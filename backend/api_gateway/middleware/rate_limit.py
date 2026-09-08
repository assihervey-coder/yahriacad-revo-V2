"""Rate limiting — fenêtre glissante par token, quotas différenciés par coût.

Les quotas (config.py) sont exprimés « action/fenêtre » : une passe de routage
(POST /pipeline/run) et un export (POST /exports/{id}/download) ne consomment
PAS les mêmes compteurs. Implémentation : deque de timestamps par
(token, action), thread-safe — déclenche RateLimitExceeded (HTTP 429) avec le
délai de réessai.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from ..config import GatewaySettings

_WINDOW_SECONDS: dict[str, int] = {"min": 60, "hour": 3600, "day": 86400}


@dataclass
class QuotaDecision:
    """Résultat d'un check réussi — exposé en headers X-RateLimit-*."""

    action: str
    limit: int
    remaining: int
    window_s: int


class RateLimitExceeded(RuntimeError):
    """Quota dépassé — le middleware traduit en 429 + Retry-After."""

    def __init__(self, detail: str, retry_after_s: float) -> None:
        super().__init__(detail)
        self.retry_after_s = retry_after_s


class RateLimiter:
    """Fenêtre glissante par (token, action) — mémoire, thread-safe."""

    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._mutex = threading.Lock()

    # ---- API ---------------------------------------------------------------
    def check(self, token_id: str, tier: str = "free", action: str = "default",
              cost: int = 1) -> QuotaDecision:
        """Compte `cost` unités pour l'action — lève RateLimitExceeded si quota."""
        limit, window_s = self._quota_for(tier, action)
        now = time.monotonic()
        with self._mutex:
            hits = self._hits.setdefault((token_id, action), deque())
            self._evict(hits, now, window_s)
            if len(hits) + cost > limit:
                retry_after = max(0.0, window_s - (now - hits[0])) if hits else window_s
                raise RateLimitExceeded(
                    f"quota « {action} » atteint pour le palier {tier} "
                    f"({limit}/{_label(window_s)}) — réessayez dans {int(retry_after) + 1}s",
                    retry_after_s=retry_after)
            for _ in range(max(1, cost)):
                hits.append(now)
        return QuotaDecision(action=action, limit=limit, remaining=max(0, limit - len(hits)),
                             window_s=window_s)

    def peek(self, token_id: str, tier: str = "free", action: str = "default") -> int:
        """Restant sans consommer — pour le dashboard de crédits."""
        limit, window_s = self._quota_for(tier, action)
        now = time.monotonic()
        with self._mutex:
            hits = self._hits.get((token_id, action), deque())
            self._evict(hits, now, window_s)
            return max(0, limit - len(hits))

    # ---- interne ---------------------------------------------------------------
    def _quota_for(self, tier: str, action: str) -> tuple[int, int]:
        """Lit les quotas « action/fenêtre » — repli : default/day."""
        quotas = self.settings.quotas.get(tier) or self.settings.quotas.get("free", {})
        raw = quotas.get(action) or quotas.get("default/day", 100)
        default_limit = quotas.get("default/day", 100)
        limit, window_s = _parse_quota_key(action, raw, default_limit)
        return limit, window_s

    @staticmethod
    def _evict(hits: deque[float], now: float, window_s: int) -> None:
        while hits and now - hits[0] > window_s:
            hits.popleft()


def _label(window_s: int) -> str:
    return {60: "minute", 3600: "heure", 86400: "jour"}.get(window_s, f"{window_s}s")


def _parse_quota_key(action: str, raw: int | str | None,
                     default_limit: int = 100) -> tuple[int, int]:
    """Parse « action/fenêtre » — accepte aussi une valeur int brute."""
    del action  # la clé porte la fenêtre : "routing_pass/day"
    if isinstance(raw, int):
        return raw, _WINDOW_SECONDS["day"]
    text = str(raw)
    if "/" in text:
        value, window = text.rsplit("/", 1)
        return int(value), _WINDOW_SECONDS.get(window, _WINDOW_SECONDS["day"])
    try:
        return int(float(text)), _WINDOW_SECONDS["day"]
    except ValueError:
        return default_limit, _WINDOW_SECONDS["day"]

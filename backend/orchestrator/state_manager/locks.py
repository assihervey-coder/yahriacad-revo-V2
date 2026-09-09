"""Verrous de concurrence par zone (bounding boxes).

Le rl_agent et un surgical_edit humain ne peuvent JAMAIS écrire simultanément
la même zone : règle de priorité « l'humain gagne » (owner `human:surgical`
préempte `rl_agent`, qui attend), les autres collisions humaines lèvent une
ZoneConflictError immédiate. TTL intégré : un verrou oublié expire seul.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from common.log import get_logger

logger = get_logger("state_manager.locks")

RL_AGENT_OWNER = "rl_agent"
HUMAN_OWNER = "human:surgical"          # préfixe des propriétaires humains


@dataclass(frozen=True)
class BBox:
    """Rectangle en mm — (x_min, y_min, x_max, y_max)."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def intersects(self, other: "BBox") -> bool:
        return not (self.x_max <= other.x_min or other.x_max <= self.x_min
                    or self.y_max <= other.y_min or other.y_max <= self.y_min)


@dataclass
class ZoneLock:
    """Verrou actif sur une zone — identifié par un ticket."""

    ticket_id: str
    project_id: str
    bbox: BBox
    owner: str
    ttl_s: float
    acquired_at: float = field(default_factory=time.monotonic)

    @property
    def expires_at(self) -> float:
        return self.acquired_at + self.ttl_s

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) > self.expires_at


class ZoneConflictError(RuntimeError):
    """Collision de zone entre deux propriétaires non arbitrables (429 côté API)."""


def _is_human(owner: str) -> bool:
    """Un propriétaire humain (human:surgical…) prime toujours sur rl_agent."""
    return owner == HUMAN_OWNER or owner.startswith("human:")


class ZoneLockManager:
    """Registre de verrous par projet — thread-safe, TTL automatique."""

    def __init__(self) -> None:
        self._locks: dict[str, list[ZoneLock]] = {}
        self._mutex = threading.Lock()

    def acquire(self, project_id: str, bbox: BBox, owner: str,
                ttl_s: float = 60.0) -> Optional[ZoneLock]:
        """Acquiert un verrou ; retourne None si le demandeur doit ATTENDRE.

        Sémantique :
        - zone libre → verrou accordé ;
        - même owner → réentrant (TTL prolongé) ;
        - rl_agent vs human:surgical → l'HUMAIN gagne : si le demandeur est
          humain, le verrou RL est révoqué (préemption) ; si le demandeur est
          rl_agent → None (il attend, sans lever d'erreur) ;
        - toute autre collision → ZoneConflictError.
        """
        with self._mutex:
            self._sweep(project_id)
            active = self._locks.setdefault(project_id, [])
            for lock in list(active):
                if not bbox.intersects(lock.bbox) or lock.owner == owner:
                    continue
                human_involved = _is_human(lock.owner) or _is_human(owner)
                rl_involved = RL_AGENT_OWNER in {lock.owner, owner}
                if human_involved and rl_involved:
                    if _is_human(owner):
                        active.remove(lock)   # préemption : l'humain gagne
                        logger.info("verrou RL préempté par un humain",
                                    extra={"project_id": project_id, "owner": lock.owner})
                        continue
                    logger.info("rl_agent attend un verrou humain",
                                extra={"project_id": project_id})
                    return None              # rl_agent attend
                raise ZoneConflictError(
                    f"zone {bbox} déjà verrouillée par {lock.owner!r} (demandeur {owner!r})")
            lock = ZoneLock(ticket_id=uuid.uuid4().hex[:12], project_id=project_id,
                            bbox=bbox, owner=owner, ttl_s=ttl_s)
            active.append(lock)
            return lock

    def release(self, project_id: str, ticket_id: str) -> bool:
        with self._mutex:
            active = self._locks.get(project_id, [])
            for lock in list(active):
                if lock.ticket_id == ticket_id:
                    active.remove(lock)
                    return True
            return False

    def active(self, project_id: str) -> list[ZoneLock]:
        with self._mutex:
            self._sweep(project_id)
            return list(self._locks.get(project_id, []))

    def _sweep(self, project_id: str) -> None:
        now = time.monotonic()
        active = self._locks.get(project_id, [])
        self._locks[project_id] = [l for l in active if not l.is_expired(now)]

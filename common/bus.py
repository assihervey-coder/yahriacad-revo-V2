"""Bus de contraintes (pub/sub) — colonne vertébrale transversale du cerveau IA.

Diffuse les contraintes techniques vers tous les sous-systèmes avec une latence
cible < 50 ms et une rétention de la dernière valeur par clé :
    thermal_zone_update · impedance_target · keepout_zone · length_match_rule

Le pattern publié/abonné découple la détection d'un problème (simulateur) de sa
résolution (agents). L'implémentation par défaut est en mémoire (mono-processus,
tests, plugin KiCad) ; l'adaptateur Redis (multi-pods Kubernetes) est activé
automatiquement si `redis` est installé et `REDIS_URL` joignable.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from common.log import get_logger

logger = get_logger("common.bus")


class ConstraintKind(str, Enum):
    """Familles de contraintes publiées sur le bus (nomenclature section 11)."""
    THERMAL_ZONE_UPDATE = "thermal_zone_update"
    IMPEDANCE_TARGET = "impedance_target"
    KEEPOUT_ZONE = "keepout_zone"
    LENGTH_MATCH_RULE = "length_match_rule"
    CURRENT_BUDGET = "current_budget"
    CLEARANCE = "clearance"


@dataclass
class ConstraintMessage:
    kind: ConstraintKind
    key: str                      # ex. "thermal/U12", "impedance/USB3_TX"
    value: dict = field(default_factory=dict)   # charge utile libre mais JSON-sérialisable
    project_id: str = ""
    source: str = "system"        # émetteur : multi_physics_loop, constraint_extractor...
    ts: float = field(default_factory=time.time)
    revision: int = 0

    def to_json(self) -> dict:
        return {
            "kind": self.kind.value,
            "key": self.key,
            "value": self.value,
            "project_id": self.project_id,
            "source": self.source,
            "ts": self.ts,
            "revision": self.revision,
        }


Subscriber = Callable[[ConstraintMessage], None]


class ConstraintBus:
    """Interface du bus — dernière valeur retenue par clé + notifications push."""

    def publish(self, message: ConstraintMessage) -> float:  # pragma: no cover - interface
        raise NotImplementedError

    def subscribe(self, kinds: Optional[List[ConstraintKind]], callback: Subscriber) -> str:  # pragma: no cover
        raise NotImplementedError

    def unsubscribe(self, token: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def latest(self, kind: Optional[ConstraintKind] = None) -> Dict[str, ConstraintMessage]:  # pragma: no cover
        raise NotImplementedError


class InMemoryConstraintBus(ConstraintBus):
    """Implémentation en mémoire — utilisée en mono-processus et en tests.

    `publish()` mesure sa propre latence et journalise un avertissement si la
    cible des 50 ms est dépassée : la diffusion doit rester temps réel même sur
    des designs denses.
    """

    def __init__(self, target_latency_ms: float = 50.0) -> None:
        self._subs: Dict[str, tuple[List[ConstraintKind] | None, Subscriber]] = {}
        self._latest: Dict[str, ConstraintMessage] = {}
        self._target_ms = target_latency_ms
        self._revisions: Dict[str, int] = {}
        self._counter = 0

    def publish(self, message: ConstraintMessage) -> float:
        start = time.perf_counter()
        message.revision = self._revisions.get(message.key, 0) + 1
        self._revisions[message.key] = message.revision
        self._latest[message.key] = message
        self._counter += 1
        for kinds, callback in list(self._subs.values()):
            if kinds is None or message.kind in kinds:
                try:
                    callback(message)
                except Exception:  # un abonné défaillant ne bloque jamais le bus
                    logger.exception("abonné constraint_bus en erreur", extra={"key": message.key})
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if elapsed_ms > self._target_ms:
            logger.warning(
                "latence constraint_bus au-dessus de la cible",
                extra={"elapsed_ms": round(elapsed_ms, 2), "target_ms": self._target_ms, "key": message.key},
            )
        return elapsed_ms

    def subscribe(self, kinds: Optional[List[ConstraintKind]], callback: Subscriber) -> str:
        self._counter += 1
        token = f"sub-{self._counter}"
        self._subs[token] = (kinds, callback)
        return token

    def unsubscribe(self, token: str) -> None:
        self._subs.pop(token, None)

    def latest(self, kind: Optional[ConstraintKind] = None) -> Dict[str, ConstraintMessage]:
        if kind is None:
            return dict(self._latest)
        return {k: v for k, v in self._latest.items() if v.kind == kind}

    @property
    def published_count(self) -> int:
        return self._counter


class RedisConstraintBus(InMemoryConstraintBus):
    """Adaptateur multi-processus via Redis pub/sub (déploiement Kubernetes).

    Réutilise la sémantique en mémoire comme cache de dernière valeur, et
    propage chaque publication sur le canal Redis `constraint_bus`. L'import
    est gardé : si `redis` est absent, `create_bus()` retombe sur la version
    en mémoire — le service démarre toujours.
    """

    CHANNEL = "constraint_bus"

    def __init__(self, redis_url: str, target_latency_ms: float = 50.0) -> None:
        super().__init__(target_latency_ms)
        import redis  # import gardé — voir docstring de module

        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._pubsub = self._redis.pubsub()
        self._pubsub.subscribe(self.CHANNEL)

    def publish(self, message: ConstraintMessage) -> float:
        elapsed_ms = super().publish(message)
        try:
            self._redis.publish(self.CHANNEL, json.dumps(message.to_json()))
        except Exception:
            logger.exception("publication Redis échouée — cache local conservé", extra={"key": message.key})
        return elapsed_ms


def create_bus(redis_url: str | None = None, target_latency_ms: float = 50.0) -> ConstraintBus:
    """Fabrique le bus adapté à l'environnement (Redis si disponible, sinon mémoire)."""
    if redis_url:
        try:
            return RedisConstraintBus(redis_url, target_latency_ms)  # type: ignore[return-value]
        except Exception:
            logger.warning("Redis indisponible — bus en mémoire utilisé", extra={"redis_url": redis_url})
    return InMemoryConstraintBus(target_latency_ms)

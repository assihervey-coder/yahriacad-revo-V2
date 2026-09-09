"""Rollback manager — annulation propre d'une action invalidée [Siemens Fuse].

Fonctionnement : AVANT chaque action, ``push_state`` empile une copie complète
du Board + une entrée de journal ; si la vérification échoue après application,
``rollback_last`` restaure l'état précédent, émet un événement
``ROLLBACK_PERFORMED``, RÉINJECTE la contrainte violée sur le constraint_bus
(les autres agents l'apprennent instantanément) et enregistre une expérience
négative destinée à l'entraînement de la policy. La pile est bornée à
200 snapshots (FIFO — le plus ancien est abandonné, LRU mémoire maîtrisée).
"""

from __future__ import annotations

import copy
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from common.bus import ConstraintMessage
from common.events import EventType, make_event
from common.log import get_logger

logger = get_logger("ai_engine.rollback")

MAX_SNAPSHOTS = 200
MAX_NEGATIVE_EXPERIENCES = 512


class RollbackManager:
    """Pile d'états de design + journal d'annulation + expérience négative."""

    def __init__(self, project_id: str, bus: Any = None,
                 emitter: str = "self_verifier", max_snapshots: int = MAX_SNAPSHOTS) -> None:
        self.project_id = project_id
        self.bus = bus
        self.emitter = emitter
        self._stack: Deque[Dict[str, Any]] = deque(maxlen=max_snapshots)
        self._negative: Deque[Dict[str, Any]] = deque(maxlen=MAX_NEGATIVE_EXPERIENCES)
        self.n_rollbacks = 0
        self.n_pushes = 0
        self._events: List[Any] = []

    # ---- pile d'états --------------------------------------------------------------
    def push_state(self, board: Any, journal_entry: Optional[Dict[str, Any]] = None) -> int:
        """Empile une copie profonde du board avant une action ; retourne la profondeur."""
        entry = dict(journal_entry or {})
        entry.setdefault("ts", time.time())
        entry.setdefault("drc_score", board.drc_score())
        self._stack.append({"board": copy.deepcopy(board), "entry": entry})
        self.n_pushes += 1
        return len(self._stack)

    def rollback_last(self, board: Any = None, reason: str = "",
                      violated: Optional[List[str]] = None,
                      constraint: Optional[ConstraintMessage] = None,
                      action: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Restaure le dernier état empilé, journalise et réinjecte la contrainte.

        ``board`` (optionnel) est l'objet design courant du chiamant : il est
        restauré EN PLACE depuis le snapshot (identité préservée pour tous les
        détenteurs de la référence). Retourne le résumé d'annulation, ou None
        si la pile est vide (l'état courant est alors l'état initial).
        """
        if not self._stack:
            logger.warning("rollback demandé sur pile vide — état initial conservé")
            return None
        snapshot = self._stack.pop()
        if board is not None:
            restored = snapshot["board"]
            board.components = restored.components
            board.placements = restored.placements
            board.nets = restored.nets
            board.zones = restored.zones
            board.width_mm = restored.width_mm
            board.height_mm = restored.height_mm
        self.n_rollbacks += 1
        violated = list(violated or [])
        summary = {
            "reason": reason,
            "violated_constraints": violated,
            "restored_entry": snapshot["entry"],
            "remaining_snapshots": len(self._stack),
        }
        # 1) événement d'audit
        event = make_event(
            EventType.ROLLBACK_PERFORMED, self.project_id, emitter=self.emitter,
            reason=reason, violated_constraints=violated,
            action=action or {}, restored_drc=snapshot["entry"].get("drc_score"),
        )
        self._events.append(event)
        # 2) réinjection de la contrainte violée sur le bus (les agents réagissent)
        if constraint is not None and self.bus is not None:
            self.bus.publish(constraint)
        # 3) expérience négative pour la policy
        self._negative.append({
            "ts": time.time(), "reason": reason, "violated": violated,
            "action": action or {}, "reward": 0.0, "kept": False,
        })
        logger.info("rollback effectué", extra=summary)
        return summary

    def record_negative_experience(self, experience: Dict[str, Any]) -> None:
        """Ajout direct d'une expérience négative (utilisé après un verdict invalide)."""
        experience = dict(experience, kept=False, reward=experience.get("reward", 0.0))
        self._negative.append(experience)

    # ---- accès --------------------------------------------------------------------
    @property
    def negative_experiences(self) -> List[Dict[str, Any]]:
        """Expériences négatives — consommées par policy.learn_from."""
        return list(self._negative)

    @property
    def depth(self) -> int:
        return len(self._stack)

    @property
    def events(self) -> List[Any]:
        return list(self._events)

    def stats(self) -> Dict[str, int]:
        return {"pushes": self.n_pushes, "rollbacks": self.n_rollbacks,
                "snapshots": self.depth, "negative_experiences": len(self._negative)}

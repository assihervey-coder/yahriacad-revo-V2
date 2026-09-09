"""Service constraint_bus par projet — enveloppe du bus commun [Cadence AuraStack].

Wrappe :class:`common.bus.InMemoryConstraintBus` (l'adaptateur Redis est
choisi côté socle si joignable) en y ajoutant : un project_id injecté dans
chaque message, la conversion automatique du modèle de design en contraintes
(``broadcast_board_constraints``), et des compteurs de diffusion + latence
moyenne — la cible contractuelle est < 50 ms par publication.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from common.bus import ConstraintKind, ConstraintMessage, InMemoryConstraintBus

Subscriber = Callable[[ConstraintMessage], None]


class ProjectConstraintBus:
    """Bus de contraintes d'un projet — pub/sub dernière-valeur par clé."""

    def __init__(self, project_id: str, target_latency_ms: float = 50.0) -> None:
        self.project_id = project_id
        self._bus = InMemoryConstraintBus(target_latency_ms)
        self._latencies_ms: List[float] = []
        self._broadcasts = 0

    # ---- API de bas niveau ----------------------------------------------------
    def publish(self, message: ConstraintMessage) -> float:
        """Publie un message (project_id forcé) ; retourne la latence en ms."""
        message.project_id = self.project_id
        elapsed_ms = self._bus.publish(message)
        self._latencies_ms.append(elapsed_ms)
        return elapsed_ms

    def make_message(self, kind: ConstraintKind, key: str, value: Dict[str, Any],
                     source: str = "ai_engine") -> ConstraintMessage:
        """Fabrique un message prêt à publier pour ce projet."""
        return ConstraintMessage(
            kind=kind, key=key, value=value, project_id=self.project_id,
            source=source, ts=time.time(),
        )

    def subscribe_for(self, kinds: Optional[Sequence[ConstraintKind]],
                      callback: Subscriber) -> str:
        """Abonne un callback à une liste de familles (None = toutes)."""
        return self._bus.subscribe(list(kinds) if kinds is not None else None, callback)

    def unsubscribe(self, token: str) -> None:
        self._bus.unsubscribe(token)

    def latest(self, kind: Optional[ConstraintKind] = None) -> Dict[str, ConstraintMessage]:
        """Dernière valeur retenue, par clé (filtrable par famille)."""
        return self._bus.latest(kind)

    # ---- conversion design -> contraintes --------------------------------------
    def broadcast_board_constraints(self, board: Any, source: str = "ai_engine") -> int:
        """Diffuse les contraintes dérivées du Board ; retourne le nb de messages.

        Mapping déterministe : zones keepout → KEEPOUT_ZONE, zones thermiques →
        THERMAL_ZONE_UPDATE, nets à impédance cible → IMPEDANCE_TARGET, groupes
        d'appariement de longueur → LENGTH_MATCH_RULE, nets d'alim →
        CURRENT_BUDGET, plus une clearance globale par défaut (0.2 mm).
        """
        count = 0
        for zone in board.zones:
            rect = {
                "x_min_mm": zone.x_min_mm, "y_min_mm": zone.y_min_mm,
                "x_max_mm": zone.x_max_mm, "y_max_mm": zone.y_max_mm,
            }
            if zone.kind == "keepout":
                self.publish(self.make_message(ConstraintKind.KEEPOUT_ZONE, f"keepout/{zone.name}", rect, source))
                count += 1
            elif zone.kind == "thermal":
                payload = dict(rect, max_temp_c=zone.max_temp_c)
                self.publish(self.make_message(ConstraintKind.THERMAL_ZONE_UPDATE, f"thermal/{zone.name}", payload, source))
                count += 1
        for net in board.nets.values():
            if net.impedance_target_ohm is not None:
                self.publish(self.make_message(
                    ConstraintKind.IMPEDANCE_TARGET, f"impedance/{net.name}",
                    {"target_ohm": net.impedance_target_ohm, "net_class": net.net_class}, source))
                count += 1
            if net.length_match_group:
                self.publish(self.make_message(
                    ConstraintKind.LENGTH_MATCH_RULE, f"length/{net.name}",
                    {"group": net.length_match_group}, source))
                count += 1
            if net.net_class == "power":
                self.publish(self.make_message(
                    ConstraintKind.CURRENT_BUDGET, f"current/{net.name}",
                    {"budget_a": 1.0, "net_class": "power"}, source))
                count += 1
        self.publish(self.make_message(
            ConstraintKind.CLEARANCE, "clearance/global",
            {"min_clearance_mm": 0.2}, source))
        count += 1
        self._broadcasts += 1
        return count

    # ---- métriques ---------------------------------------------------------------
    @property
    def published_count(self) -> int:
        """Nombre total de publications depuis la création du service."""
        return self._bus.published_count

    @property
    def broadcasts(self) -> int:
        return self._broadcasts

    @property
    def avg_latency_ms(self) -> float:
        """Latence moyenne de diffusion — doit rester sous la cible de 50 ms."""
        if not self._latencies_ms:
            return 0.0
        return sum(self._latencies_ms) / len(self._latencies_ms)

    @property
    def max_latency_ms(self) -> float:
        return max(self._latencies_ms, default=0.0)

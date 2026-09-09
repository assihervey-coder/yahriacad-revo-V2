"""Arbitre de conflits de contraintes du super_agent.

Exemple canonique : thermique vs empilement de couches. Règle de priorité
écrite dans la spécification (section 04) :
    safety > signal_integrity > thermal > cost
La décision (contrainte gagnante + justification) est enregistrée dans le
graphe d'intention [Cadence AuraStack — modèle mental partagé] via l'adaptateur
`intent_graph_record`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.log import get_logger

from ..adapters import call_adapter

logger = get_logger("super_agent.conflict_arbiter")

# Ordre de priorité : index faible = gagne (safety toujours d'abord)
PRIORITY_ORDER: tuple[str, ...] = ("safety", "signal_integrity", "thermal", "cost")

_RATIONALES: dict[str, str] = {
    "safety": "la sécurité (isolation, clearance/creepage) n'est jamais négociable",
    "signal_integrity": "l'intégrité du signal conditionne la conformité du produit",
    "thermal": "la thermique est résoluble par via-array/couche cuivre sans surcoût fort",
    "cost": "le coût est optimisé en dernier — jamais au détriment des trois autres",
}


@dataclass
class ConflictResolution:
    """Décision d'arbitrage — version Python du message ConflictReport."""

    constraint_a: dict[str, Any]
    constraint_b: dict[str, Any]
    winner: str
    loser: str
    resolution: str
    rationale: str
    project_id: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "constraint_a": self.constraint_a, "constraint_b": self.constraint_b,
            "winner": self.winner, "loser": self.loser,
            "resolution": self.resolution, "rationale": self.rationale,
            "project_id": self.project_id,
        }


class ConflictArbiter:
    """Arbitre les conflits et écrit les justifications dans le graphe d'intention."""

    def __init__(self, project_id: str = "") -> None:
        self.project_id = project_id
        self.history: list[ConflictResolution] = []

    def arbitrate(self, constraint_a: dict[str, Any],
                  constraint_b: dict[str, Any]) -> ConflictResolution:
        """Compare deux contraintes {"family", "name", ...} et tranche.

        Égalité de famille (imprévu) : la première contrainte (a) gagne —
        convention documentée, la décision reste auditable.
        """
        family_a = str(constraint_a.get("family", "cost")).lower()
        family_b = str(constraint_b.get("family", "cost")).lower()
        rank_a = self._rank(family_a)
        rank_b = self._rank(family_b)
        if rank_a <= rank_b:
            winner, loser = family_a, family_b
            win, lose = constraint_a, constraint_b
        else:
            winner, loser = family_b, family_a
            win, lose = constraint_b, constraint_a
        resolution = ConflictResolution(
            constraint_a=constraint_a, constraint_b=constraint_b,
            winner=winner, loser=loser,
            resolution=(f"priorité à « {win.get('name', winner)} » sur "
                        f"« {lose.get('name', loser)} »"),
            rationale=_RATIONALES[winner],
            project_id=self.project_id,
        )
        self.history.append(resolution)
        self._record_in_intent_graph(resolution)
        return resolution

    def _record_in_intent_graph(self, resolution: ConflictResolution) -> None:
        """Chaque décision est auditable dans le graphe d'intention."""
        result = call_adapter("intent_graph_record", self.project_id, {
            "type": "conflict_arbitration",
            "winner": resolution.winner,
            "loser": resolution.loser,
            "resolution": resolution.resolution,
            "rationale": resolution.rationale,
        })
        if result.source == "fallback":
            logger.info("arbitrage enregistré localement (graphe d'intention absent)",
                        extra=resolution.to_json())

    @staticmethod
    def _rank(family: str) -> int:
        try:
            return PRIORITY_ORDER.index(family)
        except ValueError:
            return len(PRIORITY_ORDER)   # famille inconnue = priorité la plus basse

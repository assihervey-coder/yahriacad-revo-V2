"""Catalogue des règles de design paramétrables (design_rules, section 6.4).

Chaque règle est une donnée, pas du code : le constraint_bus peut la réviser à
la volée (révision issue du manufacturing_feedback — la fabrication enseigne à
la conception), et le deterministic_checker du self_verifier les consomme pour
valider chaque action du RL agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class DesignRule:
    """Une règle de design : identifiant, sévérité, paramètres géométriques."""

    rule_id: str                    # ex. "DR-clearance-trace"
    name: str
    severity: str = "error"         # "error" | "warning"
    params: Dict[str, float] = field(default_factory=dict)

    def describe(self) -> str:
        params = ", ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.rule_id} ({self.name}) : {params}"


def default_rules() -> Dict[str, DesignRule]:
    """Jeu de règles par défaut — valeurs des capacités PCBWay 2 couches."""
    return {
        "clearance": DesignRule(
            rule_id="DR-clearance-trace",
            name="Clearance piste-piste / piste-pastille",
            severity="error",
            params={"min_clearance_mm": 0.2},
        ),
        "min_width": DesignRule(
            rule_id="DR-min-width",
            name="Largeur de piste minimale",
            severity="error",
            params={"min_width_mm": 0.2},
        ),
        "annular_ring": DesignRule(
            rule_id="DR-annular-ring",
            name="Annular ring minimal des vias",
            severity="error",
            params={"min_annular_mm": 0.15},
        ),
        "via_diameter": DesignRule(
            rule_id="DR-via-diameter",
            name="Diamètre de via minimal",
            severity="error",
            params={"min_via_dia_mm": 0.3},
        ),
        "edge_clearance": DesignRule(
            rule_id="DR-edge-clearance",
            name="Clearance au bord de carte",
            severity="error",
            params={"min_edge_mm": 0.5},
        ),
        "placement_overlap": DesignRule(
            rule_id="DR-placement-overlap",
            name="Chevauchement d'empreintes",
            severity="error",
            params={"margin_mm": 0.1},
        ),
        "keepout": DesignRule(
            rule_id="DR-keepout",
            name="Violation de zone interdite",
            severity="error",
            params={},
        ),
        "unrouted": DesignRule(
            rule_id="DR-unrouted-net",
            name="Net non routé",
            severity="warning",
            params={},
        ),
        "silk_over_pad": DesignRule(
            rule_id="DR-silk-over-pad",
            name="Silk sur pastille",
            severity="warning",
            params={},
        ),
    }

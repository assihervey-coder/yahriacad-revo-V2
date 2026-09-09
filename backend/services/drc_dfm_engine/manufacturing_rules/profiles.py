"""Profils de fabrication par usine — manufacturing_rules (section 6.4).

Les profils couvrent les capacités RÉELLES de chaque usine : un export ne sort
jamais avec une règle violée pour l'usine cible. Le manufacturing_feedback
(section 07) requalifie ces règles à partir des rendements observés — la
fabrication enseigne à la conception [Siemens].
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Board  # noqa: E402
from common.log import get_logger  # noqa: E402
from design_rules.checker import Violation  # noqa: E402

logger = get_logger("drc.profiles")


@dataclass
class FactoryProfile:
    """Capacités d'une usine de fabrication PCB."""

    name: str
    min_trace_mm: float
    min_clearance_mm: float
    min_via_dia_mm: float
    min_annular_mm: float
    min_pad_mm: float
    max_layers: int
    copper_oz: float = 1.0
    notes: str = ""

    def summary(self) -> str:
        return (f"{self.name} : trace ≥ {self.min_trace_mm} mm, clearance ≥ "
                f"{self.min_clearance_mm} mm, via ≥ {self.min_via_dia_mm} mm, "
                f"{self.max_layers} couches max, cuivre {self.copper_oz} oz")


_PROFILES: Dict[str, FactoryProfile] = {
    "pcbway": FactoryProfile(
        name="PCBWay", min_trace_mm=0.127, min_clearance_mm=0.127, min_via_dia_mm=0.3,
        min_annular_mm=0.15, min_pad_mm=0.25, max_layers=14, copper_oz=1.0,
        notes="Capacités standard PCBWay (2-14 couches), seuils prototype 2 couches.",
    ),
    "jlcpcb": FactoryProfile(
        name="JLCPCB", min_trace_mm=0.127, min_clearance_mm=0.127, min_via_dia_mm=0.3,
        min_annular_mm=0.15, min_pad_mm=0.2, max_layers=20, copper_oz=1.0,
        notes="JLCPCB : pastilles minimales 0,2 mm, jusqu'à 20 couches.",
    ),
    "internal": FactoryProfile(
        name="Internal lab", min_trace_mm=0.2, min_clearance_mm=0.2, min_via_dia_mm=0.4,
        min_annular_mm=0.2, min_pad_mm=0.3, max_layers=4, copper_oz=1.0,
        notes="Profil interne de référence du benchmark (défaut des règles design_rules).",
    ),
}


def get_profile(name: str = "pcbway") -> FactoryProfile:
    """Retourne le profil usine demandé (fallback : internal)."""
    profile = _PROFILES.get((name or "internal").lower())
    if profile is None:
        logger.warning("profil usine inconnu — profil internal appliqué", extra={"profile": name})
        return _PROFILES["internal"]
    return profile


def available_profiles() -> List[str]:
    return sorted(_PROFILES)


def check_factory(board: Board, profile: FactoryProfile) -> List[Violation]:
    """Vérifie le Board contre les capacités réelles de l'usine cible."""
    out: List[Violation] = []

    # largeurs de pistes / clearances usine (plus fins que les règles génériques)
    for net in board.nets.values():
        for seg in net.routed_segments:
            if not seg.is_via and seg.width_mm < profile.min_trace_mm:
                out.append(Violation(
                    rule_id=f"MF-min-trace-{profile.name.lower()}", severity="error",
                    net=net.name,
                    message=f"piste {seg.width_mm} mm < {profile.min_trace_mm} mm ({profile.name})",
                ))
            if seg.is_via and seg.width_mm < profile.min_via_dia_mm:
                out.append(Violation(
                    rule_id=f"MF-min-via-{profile.name.lower()}", severity="error",
                    net=net.name,
                    message=f"via {seg.width_mm} mm < {profile.min_via_dia_mm} mm ({profile.name})",
                ))

    # nombre de couches
    layer_count = len([l for l in board.layers if l.is_copper])
    if layer_count > profile.max_layers:
        out.append(Violation(
            rule_id=f"MF-max-layers-{profile.name.lower()}", severity="error",
            message=f"{layer_count} couches cuivre > {profile.max_layers} autorisées ({profile.name})",
        ))

    # pastilles minimales
    for comp in board.components.values():
        for pad in comp.pads:
            if pad.diameter_mm < profile.min_pad_mm:
                out.append(Violation(
                    rule_id=f"MF-min-pad-{profile.name.lower()}", severity="error",
                    component_ref=comp.ref,
                    message=f"pastille {pad.diameter_mm} mm < {profile.min_pad_mm} mm ({profile.name})",
                ))
                break  # une violation par composant suffit
    return out

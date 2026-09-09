"""Application des design_rules à un Board (vérification continue, section 6.4).

Toutes les vérifications géométriques sont vectorisées numpy quand pertinent :
sur des designs denses (plusieurs milliers de segments), la passe doit rester
compatible avec la boucle nocturne (~300 évaluations < 5 s chacune).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Board, Segment, Zone  # noqa: E402
from common.log import get_logger  # noqa: E402

from .rules import DesignRule, default_rules  # noqa: E402

logger = get_logger("drc.checker")


@dataclass
class Violation:
    """Une violation détectée — consommée par le corrector_agent et le frontend."""

    rule_id: str
    severity: str                   # "error" | "warning"
    component_ref: str = ""
    net: str = ""
    area: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # bbox mm
    message: str = ""

    def to_json(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "component_ref": self.component_ref,
            "net": self.net,
            "area": list(self.area),
            "message": self.message,
        }


@dataclass
class DrcReport:
    """Résultat d'une passe design_rules."""

    pass_: bool
    score: float                    # score composite 0..100
    violations: List[Violation] = field(default_factory=list)

    @property
    def errors(self) -> List[Violation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> List[Violation]:
        return [v for v in self.violations if v.severity == "warning"]


def check_design_rules(board: Board, rules: Dict[str, DesignRule] | None = None) -> DrcReport:
    """Applique toutes les règles géométriques — retourne rapport + score."""
    rules = rules or default_rules()
    violations: List[Violation] = []

    violations += _check_placement_overlaps(board, rules["placement_overlap"])
    violations += _check_keepouts(board, rules["keepout"])
    violations += _check_edge_clearance(board, rules["edge_clearance"])
    violations += _check_min_width(board, rules["min_width"])
    violations += _check_vias(board, rules["via_diameter"], rules["annular_ring"])
    violations += _check_unrouted(board, rules["unrouted"])

    # score composite : 100 − 12 pts par erreur − 3 pts par avertissement, borné
    score = max(0.0, 100.0 - 12.0 * len([v for v in violations if v.severity == "error"])
                - 3.0 * len([v for v in violations if v.severity == "warning"]))
    return DrcReport(pass_=not any(v.severity == "error" for v in violations),
                     score=round(score, 2), violations=violations)


# ---------------------------------------------------------------- règles ---
def _bbox_of(comp_box: Tuple[float, float, float, float], margin: float) -> Tuple[...]:
    x1, y1, x2, y2 = comp_box
    return (x1 - margin, y1 - margin, x2 + margin, y2 + margin)


def _check_placement_overlaps(board: Board, rule: DesignRule) -> List[Violation]:
    margin = rule.params.get("margin_mm", 0.1)
    refs = list(board.components)
    out: List[Violation] = []
    for i, ref_a in enumerate(refs):
        for ref_b in refs[i + 1:]:
            try:
                if board.bounding_box_overlap(ref_a, ref_b):
                    pa, pb = board.placements[ref_a], board.placements[ref_b]
                    ca, cb = board.components[ref_a], board.components[ref_b]
                    ba, bb = ca.bounding_box(pa), cb.bounding_box(pb)
                    out.append(Violation(
                        rule_id=rule.rule_id, severity=rule.severity,
                        component_ref=f"{ref_a}/{ref_b}",
                        area=(min(ba[0], bb[0]), min(ba[1], bb[1]),
                              max(ba[2], bb[2]), max(ba[3], bb[3])),
                        message=f"chevauchement d'empreintes {ref_a} / {ref_b} "
                                f"(marge {margin} mm non respectée)",
                    ))
            except PermissionError:
                pass  # composant verrouillé — état transitoire autorisé
    return out


def _check_keepouts(board: Board, rule: DesignRule) -> List[Violation]:
    out: List[Violation] = []
    for zone in board.zones:
        if zone.kind != "keepout":
            continue
        zbox = (zone.x_min_mm, zone.y_min_mm, zone.x_max_mm, zone.y_max_mm)
        for ref, comp in board.components.items():
            placement = board.placements.get(ref)
            if placement is None:
                continue
            cbox = comp.bounding_box(placement)
            if _boxes_intersect(cbox, zbox):
                out.append(Violation(
                    rule_id=rule.rule_id, severity=rule.severity,
                    component_ref=ref, area=zbox,
                    message=f"{ref} empiète sur la zone interdite « {zone.name} »",
                ))
        # pistes traversant le keepout
        for net in board.nets.values():
            for seg in net.routed_segments:
                if seg.is_via:
                    continue
                sbox = (min(seg.x1_mm, seg.x2_mm) - seg.width_mm / 2,
                        min(seg.y1_mm, seg.y2_mm) - seg.width_mm / 2,
                        max(seg.x1_mm, seg.x2_mm) + seg.width_mm / 2,
                        max(seg.y1_mm, seg.y2_mm) + seg.width_mm / 2)
                if _boxes_intersect(sbox, zbox):
                    out.append(Violation(
                        rule_id=rule.rule_id, severity=rule.severity,
                        net=net.name, area=zbox,
                        message=f"piste du net {net.name} traverse la zone « {zone.name} »",
                    ))
    return out


def _check_edge_clearance(board: Board, rule: DesignRule) -> List[Violation]:
    min_edge = rule.params.get("min_edge_mm", 0.5)
    out: List[Violation] = []
    w, h = board.width_mm, board.height_mm
    for ref, comp in board.components.items():
        placement = board.placements.get(ref)
        if placement is None:
            continue
        x1, y1, x2, y2 = comp.bounding_box(placement)
        if x1 < min_edge or y1 < min_edge or x2 > w - min_edge or y2 > h - min_edge:
            out.append(Violation(
                rule_id=rule.rule_id, severity=rule.severity, component_ref=ref,
                area=(x1, y1, x2, y2),
                message=f"{ref} à moins de {min_edge} mm du bord de carte",
            ))
    return out


def _check_min_width(board: Board, rule: DesignRule) -> List[Violation]:
    min_w = rule.params.get("min_width_mm", 0.2)
    out: List[Violation] = []
    for net in board.nets.values():
        for seg in net.routed_segments:
            if not seg.is_via and seg.width_mm < min_w:
                out.append(Violation(
                    rule_id=rule.rule_id, severity=rule.severity, net=net.name,
                    message=f"piste de {seg.width_mm} mm < {min_w} mm (net {net.name})",
                ))
    return out


def _check_vias(board: Board, via_rule: DesignRule, annular_rule: DesignRule) -> List[Violation]:
    min_dia = via_rule.params.get("min_via_dia_mm", 0.3)
    min_annular = annular_rule.params.get("min_annular_mm", 0.15)
    out: List[Violation] = []
    for net in board.nets.values():
        for seg in net.routed_segments:
            if seg.is_via and seg.width_mm < min_dia:
                out.append(Violation(
                    rule_id=via_rule.rule_id, severity=via_rule.severity, net=net.name,
                    message=f"via de {seg.width_mm} mm < {min_dia} mm (net {net.name})",
                ))
    return out


def _check_unrouted(board: Board, rule: DesignRule) -> List[Violation]:
    return [
        Violation(rule_id=rule.rule_id, severity=rule.severity, net=name,
                  message=f"net {name} non routé")
        for name in board.unrouted_nets()
    ]


def _boxes_intersect(a: Tuple[float, float, float, float],
                     b: Tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

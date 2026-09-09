"""Modèle de design interne — langage commun de tous les services.

Le parser normalise toute entrée (netlists SPICE, schémas KiCad, requêtes NL)
vers ces structures ; le state_manager les versionne ; le viewer_3d les rend ;
l'exporter les sérialise en Gerber/ODB++. Un seul vocabulaire = zéro perte de
sémantique entre les couches (leçon tirée de l'altium_bridge).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Side(str, Enum):
    TOP = "top"
    BOTTOM = "bottom"


@dataclass
class Layer:
    """Couche empilée de la carte (cuivre ou diélectrique)."""

    index: int
    name: str
    thickness_um: float = 35.0
    is_copper: bool = True


@dataclass
class Pad:
    """Pastille d'un composant (empreinte décomposée)."""

    name: str
    x_mm: float
    y_mm: float
    diameter_mm: float = 0.6
    net: Optional[str] = None
    side: Side = Side.TOP


@dataclass
class Component:
    """Composant électronique — unité du BOM et du placement."""

    ref: str                       # ex. "U1", "R12"
    mpn: str = ""                  # référence fabricant (DigiKey/Mouser)
    value: str = ""
    footprint: str = ""            # ex. "Package_QFP:LQFP-64_10x10mm_P0.5mm"
    pins: int = 0
    width_mm: float = 5.0
    height_mm: float = 5.0
    power_w: float = 0.0           # dissipation thermique — consommé par thermal_sim
    price_usd: float = 0.0
    stock: int = 0
    pads: List[Pad] = field(default_factory=list)
    functional_block: str = ""     # ex. "power", "mcu", "rf" — peuplé par planner_agent

    def center(self) -> Tuple[float, float]:
        if self.pads:
            xs = [p.x_mm for p in self.pads]
            ys = [p.y_mm for p in self.pads]
            return (sum(xs) / len(xs), sum(ys) / len(ys))
        return (0.0, 0.0)

    def bounding_box(self, placement: "Placement") -> Tuple[float, float, float, float]:
        """(x_min, y_min, x_max, y_max) en mm une fois le placement appliqué."""
        cx, cy = placement.x_mm, placement.y_mm
        hw, hh = self.width_mm / 2.0, self.height_mm / 2.0
        if abs(math.sin(math.radians(placement.rotation_deg))) > 0.5:
            hw, hh = hh, hw
        return (cx - hw, cy - hh, cx + hw, cy + hh)


@dataclass
class Placement:
    """Position (x, y, rotation, couche) — l'espace d'action du rl_agent."""

    ref: str
    x_mm: float = 0.0
    y_mm: float = 0.0
    rotation_deg: float = 0.0
    layer: int = 0                 # 0 = top, 1 = bottom (composants)
    locked: bool = False           # verrou posé par un surgical_edit humain


@dataclass
class Segment:
    """Segment de piste routée (ou via via un point unique)."""

    net: str
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float
    layer: int
    width_mm: float = 0.2
    is_via: bool = False

    @property
    def length_mm(self) -> float:
        return math.hypot(self.x2_mm - self.x1_mm, self.y2_mm - self.y1_mm)


@dataclass
class Net:
    """Net électrique : liste de connexions (ref, pad) + état de routage."""

    name: str
    connections: List[Tuple[str, str]] = field(default_factory=list)  # (ref, pad)
    net_class: str = "default"     # DDR, USB, MIPI, power... — posé par constraint_extractor
    impedance_target_ohm: Optional[float] = None
    length_match_group: Optional[str] = None
    routed_segments: List[Segment] = field(default_factory=list)
    layer_allowlist: List[int] = field(default_factory=lambda: list(range(8)))

    @property
    def is_routed(self) -> bool:
        return len(self.routed_segments) > 0

    @property
    def routed_length_mm(self) -> float:
        return sum(s.length_mm for s in self.routed_segments)

    @property
    def via_count(self) -> int:
        return sum(1 for s in self.routed_segments if s.is_via)


@dataclass
class Zone:
    """Zone rectangulaire — keepout, zone thermique ou bloc fonctionnel."""

    name: str
    x_min_mm: float
    y_min_mm: float
    x_max_mm: float
    y_max_mm: float
    kind: str = "keepout"          # keepout | thermal | functional
    max_temp_c: Optional[float] = None


@dataclass
class Board:
    """Carte complète — agrégat versionné par le state_manager."""

    width_mm: float = 100.0
    height_mm: float = 80.0
    layers: List[Layer] = field(default_factory=lambda: [
        Layer(i, n) for i, n in enumerate(["F.Cu", "GND", "PWR", "B.Cu"])
    ])
    components: Dict[str, Component] = field(default_factory=dict)
    placements: Dict[str, Placement] = field(default_factory=dict)
    nets: Dict[str, Net] = field(default_factory=dict)
    zones: List[Zone] = field(default_factory=list)

    # ---- helpers d'agrégat -------------------------------------------------
    def add_component(self, comp: Component, placement: Optional[Placement] = None) -> None:
        self.components[comp.ref] = comp
        self.placements[comp.ref] = placement or Placement(ref=comp.ref)

    def move(self, ref: str, x_mm: float, y_mm: float, rotation_deg: Optional[float] = None) -> Placement:
        """Déplacement atomique — rejeté si le composant est verrouillé."""
        if ref not in self.placements:
            raise KeyError(f"composant inconnu : {ref}")
        current = self.placements[ref]
        if current.locked:
            raise PermissionError(f"{ref} est verrouillé (surgical_edit humain)")
        new_rot = current.rotation_deg if rotation_deg is None else rotation_deg
        new = replace(current, x_mm=x_mm, y_mm=y_mm, rotation_deg=new_rot)
        self.placements[ref] = new
        return new

    def bounding_box_overlap(self, ref_a: str, ref_b: str) -> bool:
        pa, pb = self.placements[ref_a], self.placements[ref_b]
        ca, cb = self.components[ref_a], self.components[ref_b]
        ax1, ay1, ax2, ay2 = ca.bounding_box(pa)
        bx1, by1, bx2, by2 = cb.bounding_box(pb)
        return not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1)

    # ---- métriques utilisées par le benchmark vs Quilter --------------------
    def via_count(self) -> int:
        return sum(n.via_count for n in self.nets.values())

    def routed_length_mm(self) -> float:
        return sum(n.routed_length_mm for n in self.nets.values())

    def unrouted_nets(self) -> List[str]:
        return [name for name, net in self.nets.items() if not net.is_routed]

    def drc_score(self) -> float:
        """Score composite 0..100 provisoire : complétude + pénalités de vias.

        Le score définitif est calculé par le drc_dfm_engine ; celui-ci sert
        d'objectif interne au rl_agent et au keeper_logic (AutoPCB).
        """
        total = len(self.nets) or 1
        routed_ratio = (total - len(self.unrouted_nets())) / total
        via_penalty = min(0.2, self.via_count() / 2000.0)
        return round(100.0 * routed_ratio * (1.0 - via_penalty), 2)


@dataclass
class DesignState:
    """État complet d'un projet : carte + version + journal des transitions.

    Persisté par le state_manager à chaque transition majeure du pipeline —
    c'est ce journal que lit le rollback_manager (self_verifier) pour annuler
    proprement une action invalide, et que rejoue le session_restorer.
    """

    project_id: str
    board: Board
    version: int = 1
    pipeline_step: int = 0         # WorkflowStep courant
    journal: List[Dict] = field(default_factory=list)

    def snapshot(self, author: str, note: str) -> Dict:
        entry = {
            "version": self.version,
            "author": author,
            "note": note,
            "drc_score": self.board.drc_score(),
            "via_count": self.board.via_count(),
        }
        self.journal.append(entry)
        return entry

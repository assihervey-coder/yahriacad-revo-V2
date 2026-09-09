"""Normalisation d'import — fusion vers le Board commun (section 6.1).

Crée les pads (disposition DIP par défaut, coordonnées absolues), déduplique
les références, valide nets flottants / empreintes manquantes, dimensionne la
carte et positionne les composants en grille fonctionnelle.
"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Board, Component, Net, Pad, Placement  # noqa: E402
from common.log import get_logger  # noqa: E402

logger = get_logger("parser.normalizer")

# Priorité de placement : l'alimentation au centre, les périphériques autour
_BLOCK_ORDER = {"power": 0, "mcu": 1, "rf": 2, "logic": 3, "sensor": 4, "connector": 5, "passive": 6}


@dataclass
class ImportReport:
    """Rapport d'import — aligne le message proto ImportReport du service parser."""

    ok: bool
    components_imported: int
    nets_imported: int
    warnings: List[str] = field(default_factory=list)
    board: Board = field(default_factory=Board)


def detect_format(filename: str, content_bytes: bytes, format_hint: str = "auto") -> str:
    """Détermine le format d'entrée : « spice » ou « kicad_sch »."""
    hint = (format_hint or "auto").strip().lower()
    if hint in {"spice", "kicad_sch"}:
        return hint
    name = (filename or "").lower()
    head = content_bytes[:8192].decode("utf-8", errors="ignore")
    lowered = head.lower()
    if "kicad_sch" in lowered or name.endswith(".kicad_sch"):
        return "kicad_sch"
    if name.endswith((".net", ".cir", ".spice", ".sp", ".cki")) or ".subckt" in lowered:
        return "spice"
    if re.search(r"\(\s*kicad_sch", head):
        return "kicad_sch"
    if re.search(r"^[A-Za-z]{1,2}\S*\s+\S+\s+\S+", head, re.M):
        return "spice"
    return "spice"  # repli : format le plus permissif


def _fallback_footprint(pins: int) -> str:
    """Empreinte générique quand le composant importé n'en définit aucune."""
    if pins <= 2:
        return "Resistor_SMD:R_0603_1608Metric"
    if pins <= 8:
        return "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
    return f"Package_QFP:LQFP-{max(pins, 32)}_custom"


def _default_pad_layout(n_pins: int, width_mm: float, height_mm: float) -> List[Pad]:
    """Disposition DIP en coordonnées locales : moitié gauche, moitié droite."""
    pads: List[Pad] = []
    left_count = (n_pins + 1) // 2
    right_count = n_pins - left_count
    offset = width_mm / 2.0 + 0.95  # pastilles juste à l'extérieur du boîtier
    for index in range(n_pins):
        on_left = index < left_count
        local_index = index if on_left else index - left_count
        rows = max(left_count, right_count, 1)
        y = -height_mm / 2.0 + (local_index + 1) * height_mm / (rows + 1)
        pads.append(Pad(name=str(index + 1), x_mm=-offset if on_left else offset, y_mm=y,
                        diameter_mm=0.6))
    return pads


def _place_on_grid(components: List[Component]) -> Dict[str, Placement]:
    """Placement initial en grille 4 colonnes, trié par bloc fonctionnel."""
    ordered = sorted(components, key=lambda c: (_BLOCK_ORDER.get(c.functional_block, 9), c.ref))
    placements: Dict[str, Placement] = {}
    for index, comp in enumerate(ordered):
        col, row = index % 4, index // 4
        placements[comp.ref] = Placement(ref=comp.ref, x_mm=14.0 + col * 22.0, y_mm=12.0 + row * 18.0)
    return placements


def normalize_import(components: Sequence[Component],
                     net_connections: Dict[str, List[Tuple[str, str]]],
                     wire_hints: Optional[Sequence] = None,
                     warnings: Optional[List[str]] = None) -> ImportReport:
    """Fusionne les sorties des parseurs en un Board normalisé + avertissements."""
    warns: List[str] = list(warnings or [])
    if wire_hints:
        logger.debug("wire hints conservés pour le router", extra={"count": len(list(wire_hints))})

    # 1) validation des composants : refs uniques + empreintes présentes
    clean: List[Component] = []
    for comp in components:
        if any(comp.ref == other.ref for other in clean):
            comp.ref = f"{comp.ref}_dup{len(clean)}"
            warns.append(f"référence dupliquée renommée en « {comp.ref} »")
        if not comp.footprint:
            comp.footprint = _fallback_footprint(max(comp.pins, 2))
            warns.append(f"empreinte manquante pour {comp.ref} — boîtier générique appliqué")
        if comp.pins <= 0:
            comp.pins = 2
        clean.append(comp)

    # 2) validation des nets : détecter les nets flottants (< 2 connexions)
    for net_name, conns in sorted(net_connections.items()):
        if len(conns) < 2:
            refs = ", ".join(f"{r}.{p}" for r, p in conns) or "?"
            warns.append(f"net flottant « {net_name} » ({len(conns)} connexion(s) : {refs})")

    # 3) placement initial + carte dimensionnée
    board = Board()
    placements = _place_on_grid(clean) if clean else {}
    if placements:
        max_x = max(placements[c.ref].x_mm + c.width_mm / 2.0 for c in clean)
        max_y = max(placements[c.ref].y_mm + c.height_mm / 2.0 for c in clean)
        board.width_mm = max(40.0, math.ceil(max_x + 8.0))
        board.height_mm = max(30.0, math.ceil(max_y + 8.0))

    # 4) pads : disposition locale → coordonnées absolues, net attaché
    conn_by_key: Dict[Tuple[str, str], str] = {}
    for net_name, conns in net_connections.items():
        for ref, pad in conns:
            conn_by_key[(ref, str(pad))] = net_name
    for comp in clean:
        place = placements[comp.ref]
        comp.pads = []
        for local in _default_pad_layout(comp.pins, comp.width_mm, comp.height_mm):
            local.x_mm = place.x_mm + local.x_mm  # convention : pads en coordonnées absolues
            local.y_mm = place.y_mm + local.y_mm
            local.net = conn_by_key.get((comp.ref, local.name))
            comp.pads.append(local)
        board.add_component(comp, place)

    # 5) nets du Board
    for net_name in sorted(net_connections):
        conns = [(ref, str(pad)) for ref, pad in net_connections[net_name]]
        board.nets[net_name] = Net(name=net_name, connections=conns)

    ok = bool(clean)
    logger.info("import normalisé", extra={"ok": ok, "components": len(clean),
                                           "nets": len(board.nets), "warnings": len(warns)})
    return ImportReport(ok=ok, components_imported=len(clean),
                        nets_imported=len(board.nets), warnings=warns, board=board)

"""Export Specctra DSN simplifié — placement + nets d'un Board interne.

Format (simplifié mais conforme à la grammaire DSN acceptée par FreeRouting) :

    (placement (place U1 12.0 8.0 90.0 top))
    (net N_VCC (pins U1-1 U3-2))

C'est le premier maillon de la chaîne d'évaluation complète décrite dans
:mod:`freerouting_bridge` — en production : DSN → freerouting (routeur Java)
→ session DRC ; ici : DSN (traçabilité/export) + évaluateur géométrique.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_LAYER_NAMES = {0: "top", 1: "bottom"}


def _fmt(value: float) -> str:
    """Format numérique stable (2 décimales, pas de -0.00)."""
    out = f"{value:.2f}"
    return "0.00" if out == "-0.00" else out


def export_dsn(board: Any, design_name: str = "pcb_ai_design") -> str:
    """Sérialise le design en texte Specctra DSN simplifié."""
    lines: list[str] = []
    lines.append(f"(dsn \"{design_name}\"")
    lines.append(f"  (resolution um 1000)")
    lines.append(f"  (boundary (rect pcb 0 0 {_fmt(board.width_mm)} {_fmt(board.height_mm)}))")
    # placement — un (place ...) par composant avec rotation et couche
    lines.append("  (placement")
    for ref, placement in sorted(board.placements.items()):
        layer = _LAYER_NAMES.get(placement.layer, "top")
        lines.append(f"    (place {ref} {_fmt(placement.x_mm)} {_fmt(placement.y_mm)} "
                     f"{_fmt(placement.rotation_deg)} {layer})")
    lines.append("  )")
    # netlist — pins sous la forme REF-PAD
    lines.append("  (net")
    for name, net in sorted(board.nets.items()):
        pins = " ".join(f"{ref}-{pad}" for ref, pad in net.connections)
        lines.append(f"    (net {name} (pins {pins}))")
    lines.append("  )")
    lines.append(")")
    return "\n".join(lines)


def write_dsn(board: Any, path: Path, design_name: str = "pcb_ai_design") -> Path:
    """Écrit le DSN sur disque (traçabilité d'itération nocturne) ; retourne path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(export_dsn(board, design_name), encoding="utf-8")
    return path

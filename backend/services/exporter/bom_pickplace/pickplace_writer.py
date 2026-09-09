"""Pick & place CSV — position, rotation, side (section 6.6)."""

from __future__ import annotations

import csv
import io

from common.design_model import Board, Side


def export_pick_place(board: Board) -> str:
    """Fichier pick & place pour l'assemblage : centres réels des composants."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ref", "mid_x_mm", "mid_y_mm", "rotation_deg", "layer", "side"])
    for ref, comp in board.components.items():
        placement = board.placements.get(ref)
        if placement is None:
            continue
        cx, cy = comp.center()
        x = placement.x_mm + cx
        y = placement.y_mm + cy
        layer = placement.layer
        side = Side.TOP if layer == 0 else Side.BOTTOM
        writer.writerow([ref, f"{x:.3f}", f"{y:.3f}", placement.rotation_deg, layer, side.value])
    return buffer.getvalue()

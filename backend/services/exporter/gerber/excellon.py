"""Fichier de perçage Excellon — positions des vias (section 6.6).

Format minimal : header M48 avec outils T (diamètres), unités métriques
METRIC/000.000, puis positions X/Y par outil. Les diamètres de via rencontrés
dans le design déterminent la table d'outils.
"""

from __future__ import annotations

from typing import Dict, List

from common.design_model import Board


def _tool_id(diameter: float, table: Dict[float, str]) -> str:
    if diameter not in table:
        table[diameter] = f"T{len(table) + 1:02d}"
    return table[diameter]


def export_drill(board: Board) -> str:
    """Génère le fichier Excellon .drl à partir des vias du design."""
    lines: List[str] = ["; Excellon drill — pcb_ai_designer_v2/exporter", "M48", "METRIC", "000.000"]
    tool_table: Dict[float, str] = {}
    vias_by_tool: Dict[str, List[tuple]] = {}

    for net in board.nets.values():
        for seg in net.routed_segments:
            if not seg.is_via:
                continue
            tool = _tool_id(seg.width_mm, tool_table)
            vias_by_tool.setdefault(tool, []).append((seg.x1_mm, seg.y1_mm))

    if not tool_table:
        tool_table[0.3] = "T01"
        vias_by_tool.setdefault("T01", [])
    for diameter, tool in sorted(tool_table.items()):
        lines.append(f"; via {diameter:.3f} mm")
        lines.append(f"{tool},0.000")
    lines.append("%")
    lines.append("G90")
    lines.append("G05")

    for tool, positions in vias_by_tool.items():
        lines.append(tool)
        for x, y in positions:
            lines.append(f"X{x:.3f}Y{y:.3f}")
    lines.append("T00")
    lines.append("M30")
    return "\n".join(lines)

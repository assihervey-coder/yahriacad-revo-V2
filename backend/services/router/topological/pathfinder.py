"""Routeur A* multi-couches sur grille avec négociation de congestion (section 6.4).

Implémentation type PathFinder : chaque paire de pads (MST du net) est routée
par A* sur une grille discrète (pas 0,25 mm), où le coût combine longueur,
changement de couche (via — biais stay-on-layer, levier 3 du via_minimizer),
congestion résiduelle et contraintes (keepouts, clearances). Déterministe :
même entrée → même sortie, condition du benchmark honnête.
"""

from __future__ import annotations

import heapq
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Board, Net, Segment  # noqa: E402
from common.log import get_logger  # noqa: E402
from topological.connectivity_graph import ConnectivityGraph, PadNode  # noqa: E402

logger = get_logger("router.pathfinder")

GRID_STEP_MM = 0.25
VIA_COST = 12.0          # coût d'un changement de couche — biais stay-on-layer
CONGESTION_GROWTH = 1.6  # facteur de pénalité par usage d'une cellule
EDGE_CLEARANCE_MM = 0.5  # clearance de bord de carte


@dataclass
class RouteResult:
    """Résultat du routage d'un net : segments tracés + statistiques."""

    net: str
    segments: List[Segment]
    cells_used: int = 0
    vias: int = 0


class Pathfinder:
    """A* multi-couches sur grille avec usage de congestion cumulée."""

    def __init__(self, board: Board, grid_step_mm: float = GRID_STEP_MM,
                 layer_allowlist: Optional[List[int]] = None) -> None:
        self.board = board
        self.step = grid_step_mm
        self.layers = layer_allowlist or [l.index for l in board.layers if l.is_copper]
        self.nx = int(board.width_mm / grid_step_mm) + 1
        self.ny = int(board.height_mm / grid_step_mm) + 1
        # Usage de congestion par cellule (x, y, layer) — négociation PathFinder
        self._usage: Dict[Tuple[int, int, int], int] = {}
        # Cellules interdites : keepouts, périphérie, emprises de pads
        self._blocked: set = set()
        self._build_blocked()

    # ------------------------------------------------------------------ grid
    def _to_cell(self, x_mm: float, y_mm: float) -> Tuple[int, int]:
        return (max(0, min(self.nx - 1, int(round(x_mm / self.step)))),
                max(0, min(self.ny - 1, int(round(y_mm / self.step)))))

    def _to_mm(self, cell: Tuple[int, int]) -> Tuple[float, float]:
        return (cell[0] * self.step, cell[1] * self.step)

    def _build_blocked(self) -> None:
        """Marque les keepouts et la périphérie comme inroutables."""
        for zone in self.board.zones:
            if zone.kind == "keepout":
                cx_min, cy_min = self._to_cell(zone.x_min_mm, zone.y_min_mm)
                cx_max, cy_max = self._to_cell(zone.x_max_mm, zone.y_max_mm)
                for cx in range(cx_min, cx_max + 1):
                    for cy in range(cy_min, cy_max + 1):
                        for layer in self.layers:
                            self._blocked.add((cx, cy, layer))
        edge = max(1, int(EDGE_CLEARANCE_MM / self.step))
        for cx in range(self.nx):
            for cy in range(self.ny):
                if cx < edge or cy < edge or cx >= self.nx - edge or cy >= self.ny - edge:
                    for layer in self.layers:
                        self._blocked.add((cx, cy, layer))

    def block_pads(self, radius_cells: int = 1) -> None:
        """Bloque les cellules autour des pastilles (clearance de base).

        Les cellules de départ/arrivée restent accessibles : A* exempte
        toujours la cellule but (cf. `_astar`).
        """
        for ref, comp in self.board.components.items():
            placement = self.board.placements.get(ref)
            layer = placement.layer if placement else 0
            for pad in comp.pads:
                cx, cy = self._to_cell(pad.x_mm, pad.y_mm)
                for ddx in range(-radius_cells, radius_cells + 1):
                    for ddy in range(-radius_cells, radius_cells + 1):
                        self._blocked.add((cx + ddx, cy + ddy, layer))

    def block_cells(self, cells: List[Tuple[int, int, int]]) -> None:
        """Bloque explicitement des cellules (contraintes du bus, re-route local)."""
        self._blocked.update(cells)

    # ------------------------------------------------------------------- A*
    def route_net(self, graph: ConnectivityGraph, net: Net) -> Optional[RouteResult]:
        """Route un net : paires MST (Prim) reliées successivement par A*."""
        pairs = graph.mst_pairs(net.name)
        if not pairs:
            logger.warning("net sans paire de pads — ignoré", extra={"net": net.name})
            return None

        segments: List[Segment] = []
        cells_used = 0
        vias = 0
        for a, b in pairs:
            path = self._astar(a, b, net)
            if path is None:
                logger.warning("net inroutable — escalade super_agent", extra={"net": net.name})
                if segments:
                    return RouteResult(net=net.name, segments=segments,
                                       cells_used=cells_used, vias=vias)
                return None
            segments.extend(self._path_to_segments(net, path))
            cells_used += len(path)
            vias += sum(1 for c1, c2 in zip(path, path[1:]) if c1[2] != c2[2])

        # enregistre l'usage (pour la négociation des nets suivants)
        for seg in segments:
            for cell in self._segment_cells(seg):
                self._usage[cell] = self._usage.get(cell, 0) + 1
        return RouteResult(net=net.name, segments=segments, cells_used=cells_used, vias=vias)

    def _astar(self, a: PadNode, b: PadNode, net: Net) -> Optional[List[Tuple[int, int, int]]]:
        allow = set(self.layers) if not net.layer_allowlist \
            else set(self.layers) & set(net.layer_allowlist) or set(self.layers)
        start_layer = min(a.layer, max(self.layers))
        goal_layer = min(b.layer, max(self.layers))
        start = (*self._to_cell(a.x_mm, a.y_mm), start_layer)
        goal = (*self._to_cell(b.x_mm, b.y_mm), goal_layer)

        open_heap: List[Tuple[float, float, tuple]] = [(0.0, 0.0, start)]
        g_score: Dict[tuple, float] = {start: 0.0}
        came: Dict[tuple, tuple] = {}
        closed: set = set()

        def h(c: tuple) -> float:
            dx = (c[0] - goal[0]) * self.step
            dy = (c[1] - goal[1]) * self.step
            dl = abs(c[2] - goal[2]) * VIA_COST
            return math.hypot(dx, dy) + dl

        while open_heap:
            _, g, current = heapq.heappop(open_heap)
            if current == goal:
                return self._reconstruct(came, current)
            if current in closed:
                continue
            closed.add(current)
            for nxt in self._neighbors(current, allow):
                if nxt in self._blocked and nxt != goal:
                    continue
                move_cost = self.step
                if nxt[2] != current[2]:
                    move_cost += VIA_COST
                usage = self._usage.get(nxt, 0)
                move_cost *= (1.0 + usage * (CONGESTION_GROWTH - 1.0))
                tentative = g + move_cost
                if tentative < g_score.get(nxt, math.inf):
                    g_score[nxt] = tentative
                    came[nxt] = current
                    heapq.heappush(open_heap, (tentative + h(nxt), tentative, nxt))
        return None

    def _neighbors(self, cell: tuple, allow: set) -> List[tuple]:
        x, y, layer = cell
        out: List[tuple] = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx_, ny_ = x + dx, y + dy
            if 0 <= nx_ < self.nx and 0 <= ny_ < self.ny:
                out.append((nx_, ny_, layer))
        if layer + 1 in allow:
            out.append((x, y, layer + 1))
        if layer - 1 in allow:
            out.append((x, y, layer - 1))
        return out

    def _reconstruct(self, came: Dict[tuple, tuple], node: tuple) -> List[tuple]:
        path = [node]
        while node in came:
            node = came[node]
            path.append(node)
        path.reverse()
        return path

    # ------------------------------------------------------- segments fusion
    def _path_to_segments(self, net: Net, path: List[tuple]) -> List[Segment]:
        """Fusionne les cellules alignées en segments, insère les vias.

        Un run = suite de cellules colinéaires sur la même couche. Un changement
        de couche produit un via au point de transition.
        """
        segments: List[Segment] = []
        if len(path) < 2:
            return segments
        run_start = path[0]
        prev_dir: Optional[Tuple[int, int]] = None
        for i in range(1, len(path)):
            cell = path[i]
            prev = path[i - 1]
            dx, dy = cell[0] - prev[0], cell[1] - prev[1]
            layer_change = cell[2] != prev[2]
            cur_dir = None if layer_change else (dx, dy)
            if layer_change or (prev_dir is not None and cur_dir != prev_dir):
                segments.append(self._make_segment(net, run_start, prev))
                if layer_change:
                    segments.append(self._make_via(net, prev))
                    run_start = cell          # le nouveau run part de l'autre couche
                else:
                    run_start = prev          # coude : nouveau run au même point
                prev_dir = cur_dir
            elif prev_dir is None:
                prev_dir = cur_dir
        segments.append(self._make_segment(net, run_start, path[-1]))
        return [s for s in segments if s.is_via or s.length_mm > 0]

    def _make_segment(self, net: Net, c1: tuple, c2: tuple) -> Segment:
        x1, y1 = self._to_mm((c1[0], c1[1]))
        x2, y2 = self._to_mm((c2[0], c2[1]))
        width = 0.4 if (net.net_class or "").lower().startswith(("pwr", "power")) else 0.2
        return Segment(net=net.name, x1_mm=x1, y1_mm=y1, x2_mm=x2, y2_mm=y2,
                       layer=c1[2], width_mm=width, is_via=False)

    def _make_via(self, net: Net, cell: tuple) -> Segment:
        x, y = self._to_mm((cell[0], cell[1]))
        return Segment(net=net.name, x1_mm=x, y1_mm=y, x2_mm=x, y2_mm=y,
                       layer=cell[2], width_mm=0.3, is_via=True)

    def _segment_cells(self, seg: Segment) -> List[tuple]:
        c1 = self._to_cell(seg.x1_mm, seg.y1_mm)
        c2 = self._to_cell(seg.x2_mm, seg.y2_mm)
        cells = []
        steps = max(abs(c2[0] - c1[0]), abs(c2[1] - c1[1]), 1)
        for i in range(steps + 1):
            cx = c1[0] + round((c2[0] - c1[0]) * i / steps)
            cy = c1[1] + round((c2[1] - c1[1]) * i / steps)
            cells.append((cx, cy, seg.layer))
        return cells

    @property
    def congestion_max(self) -> int:
        return max(self._usage.values(), default=0)

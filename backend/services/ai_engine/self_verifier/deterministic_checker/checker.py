"""Checker déterministe — géométrie, électrique, routabilité, contraintes du bus.

Dernier rempart avant engagement [Siemens Fuse] : aucune heuristique
apprisent, uniquement de la physique reproductible. Trois familles de tests :
  1. GÉOMÉTRIE  — bounding boxes (collision, clearance 0.2 mm par défaut,
     surchargée par la contrainte CLEARANCE du bus), zones keepout (du board
     ET du bus), limites de carte ;
  2. ÉLECTRIQUE — chute de tension sur les nets d'alim : R ≈ ρ·L/A
     (cuivre 1.724e-5 Ω·mm, section = largeur × 35 µm), seuil 3 % ;
  3. ROUTABILITÉ — A* 8-directions sur grille clairsemée (cellule 2 mm) :
     l'action ne doit murer aucun composant.
Le verdict cite les clés du constraint_bus violées (ex. ``keepout/antenna``).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from common.bus import ConstraintKind
from common.design_model import Placement

_RHO_CU_OHM_MM = 1.724e-5        # résistivité cuivre, Ω·mm
_TRACE_THICKNESS_MM = 0.035      # cuivre 1 oz
_DEFAULT_V_RAIL = 3.3


@dataclass
class DesignRules:
    """Règles physiques par défaut — surchargées par le constraint_bus."""

    min_clearance_mm: float = 0.2
    max_voltage_drop_pct: float = 3.0
    default_current_a: float = 0.5
    trace_width_mm: float = 0.2
    cell_mm: float = 2.0            # pas de la grille de routabilité


@dataclass
class Verdict:
    """Réponse du checker — mappable sur VerifyActionReply du proto gRPC."""

    valid: bool
    violated_constraints: List[str] = field(default_factory=list)
    reason: str = ""
    family: str = ""                # geometrie | electrique | routabilite

    def to_json(self) -> Dict[str, Any]:
        return {"valid": self.valid, "violated_constraints": self.violated_constraints,
                "reason": self.reason, "family": self.family}


class DeterministicChecker:
    """Vérifie qu'une action de placement reste physiquement acceptable."""

    def __init__(self, bus: Any = None, rules: Optional[DesignRules] = None) -> None:
        self.bus = bus                     # ProjectConstraintBus optionnel
        self.rules = rules or DesignRules()

    # ---- lecture des contraintes du bus ----------------------------------------------
    def _clearance(self) -> float:
        """Clearance effective = max(défaut, dernière contrainte CLEARANCE)."""
        clearance = self.rules.min_clearance_mm
        if self.bus is not None:
            for message in self.bus.latest(ConstraintKind.CLEARANCE).values():
                clearance = max(clearance, float(message.value.get("min_clearance_mm", 0.0)))
        return clearance

    def _keepout_rects(self, board: Any) -> List[Tuple[str, Tuple[float, float, float, float]]]:
        """Rects keepout du board + ceux publiés sur le bus (source externe)."""
        rects = [(f"keepout/{z.name}", (z.x_min_mm, z.y_min_mm, z.x_max_mm, z.y_max_mm))
                 for z in board.zones if z.kind == "keepout"]
        if self.bus is not None:
            for key, message in self.bus.latest(ConstraintKind.KEEPOUT_ZONE).items():
                v = message.value
                rects.append((key, (float(v.get("x_min_mm", 0.0)), float(v.get("y_min_mm", 0.0)),
                                    float(v.get("x_max_mm", 0.0)), float(v.get("y_max_mm", 0.0)))))
        return rects

    # ---- API principale -----------------------------------------------------------------
    def check(self, board: Any, action: Any) -> Verdict:
        """Vérifie une action (ref, x_mm, y_mm, rotation_deg, layer) sur le board."""
        comp = board.components.get(action.ref)
        if comp is None:
            return Verdict(False, [f"component/{action.ref}"],
                           f"composant inconnu : {action.ref}", "geometrie")
        x1, y1, x2, y2 = comp.bounding_box(
            Placement(ref=action.ref, x_mm=action.x_mm, y_mm=action.y_mm,
                      rotation_deg=getattr(action, "rotation_deg", 0.0),
                      layer=getattr(action, "layer", 0)))
        violated: List[str] = []
        # ---- 1) géométrie -------------------------------------------------------------
        clearance = self._clearance()
        if x1 < -0.01 or y1 < -0.01 or x2 > board.width_mm + 0.01 or y2 > board.height_mm + 0.01:
            violated.append(f"board_bounds/{action.ref}")
        for key, rect in self._keepout_rects(board):
            kx1, ky1, kx2, ky2 = rect
            if not (x2 <= kx1 + clearance or kx2 - clearance <= x1
                    or y2 <= ky1 + clearance or ky2 - clearance <= y1):
                if key not in violated:
                    violated.append(key)
        for other_ref, other_p in board.placements.items():
            if other_ref == action.ref or other_ref not in board.components:
                continue
            ox1, oy1, ox2, oy2 = board.components[other_ref].bounding_box(other_p)
            gap = max(ox1 - x2, x1 - ox2, oy1 - y2, y1 - oy2)
            if gap < clearance:
                key = f"collision/{other_ref}"
                if key not in violated:
                    violated.append(key)
        if violated:
            return Verdict(False, violated, "chevauchement géométrique ou keepout", "geometrie")
        # ---- 2) électrique ----------------------------------------------------------------
        drop = self._worst_voltage_drop_pct(board, action)
        if drop is not None and drop > self.rules.max_voltage_drop_pct:
            return Verdict(False, [f"voltage_drop/{drop:.1f}pct"],
                           f"chute de tension estimée {drop:.1f} % > "
                           f"{self.rules.max_voltage_drop_pct} %", "electrique")
        # ---- 3) routabilité -------------------------------------------------------------------
        if not self._routable(board, action, (x1, y1, x2, y2)):
            return Verdict(False, ["routability_blocked"],
                           "l'action mure une connexion (A* sans chemin)", "routabilite")
        return Verdict(True, [], "action physiquement valide", "")

    # ---- électrique -----------------------------------------------------------------------
    def _worst_voltage_drop_pct(self, board: Any, action: Any) -> Optional[float]:
        """Chute de tension max (%) sur les nets d'alim touchés par l'action.

        R = ρ·L/A avec L = distance Manhattan estimée entre les deux charges
        les plus éloignées du net, A = largeur × épaisseur (0.2 × 0.035 mm²).
        """
        worst: Optional[float] = None
        for net in board.nets.values():
            if net.net_class != "power" or len(net.connections) < 2:
                continue
            refs = {ref for ref, _pad in net.connections if ref in board.placements}
            if action.ref not in refs:
                continue
            if self.bus is not None:
                current = 0.0
                for message in self.bus.latest(ConstraintKind.CURRENT_BUDGET).values():
                    current = max(current, float(message.value.get("budget_a", 0.0)))
                current = current or self.rules.default_current_a
            else:
                current = self.rules.default_current_a
            points = []
            for ref in refs:
                p = board.placements[ref]
                points.append((p.x_mm, p.y_mm))
            span = max(math.hypot(px - qx, py - qy)
                       for i, (px, py) in enumerate(points)
                       for (qx, qy) in points[i + 1:]) if len(points) > 1 else 0.0
            area = self.rules.trace_width_mm * _TRACE_THICKNESS_MM
            resistance = _RHO_CU_OHM_MM * span / max(area, 1e-9)
            drop_pct = 100.0 * current * resistance / _DEFAULT_V_RAIL
            worst = drop_pct if worst is None else max(worst, drop_pct)
        return worst

    # ---- routabilité (A* 8 directions) --------------------------------------------------------
    def _routable(self, board: Any, action: Any, bbox: Tuple[float, float, float, float]) -> bool:
        """Vérifie qu'un chemin libre relie le composant déplacé à son partenaire
        de net le plus éloigné — grille clairsemée, obstacles = keepouts + autres
        composants. Sans partenaire : la cellule centre de carte fait office de but."""
        cell = self.rules.cell_mm
        cols = max(2, int(math.ceil(board.width_mm / cell)))
        rows = max(2, int(math.ceil(board.height_mm / cell)))
        blocked = [[False] * cols for _ in range(rows)]

        def block_rect(x1: float, y1: float, x2: float, y2: float, margin: float) -> None:
            c1, r1 = max(0, int((x1 - margin) / cell)), max(0, int((y1 - margin) / cell))
            c2 = min(cols - 1, int((x2 + margin) / cell))
            r2 = min(rows - 1, int((y2 + margin) / cell))
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    blocked[r][c] = True

        for key, (kx1, ky1, kx2, ky2) in self._keepout_rects(board):
            block_rect(kx1, ky1, kx2, ky2, 0.0)
        for other_ref, other_p in board.placements.items():
            if other_ref == action.ref or other_ref not in board.components:
                continue
            ox1, oy1, ox2, oy2 = board.components[other_ref].bounding_box(other_p)
            block_rect(ox1, oy1, ox2, oy2, 0.0)
        # le composant déplacé reste praticable autour de sa nouvelle bbox
        bx1, by1, bx2, by2 = bbox
        for r in range(max(0, int(by1 / cell)), min(rows, int(by2 / cell) + 1)):
            for c in range(max(0, int(bx1 / cell)), min(cols, int(bx2 / cell) + 1)):
                blocked[r][c] = False

        start = (min(rows - 1, int((by1 + by2) / 2.0 / cell)),
                 min(cols - 1, int((bx1 + bx2) / 2.0 / cell)))
        goal = self._goal_cell(board, action, rows, cols, cell)
        # Le but (centre du partenaire) peut tomber DANS sa propre bbox bloquée :
        # on vise la cellule libre la plus proche (périphérie du composant).
        goal = self._nearest_free(blocked, rows, cols, goal)
        if goal is None:
            return False
        return self._astar(blocked, rows, cols, start, goal) is not None

    def _goal_cell(self, board: Any, action: Any, rows: int, cols: int,
                   cell: int) -> Tuple[int, int]:
        """Cellule but : partenaire de net le plus éloigné, sinon centre de carte."""
        best_xy = (board.width_mm / 2.0, board.height_mm / 2.0)
        best_dist = -1.0
        for net in board.nets.values():
            refs = {ref for ref, _pad in net.connections if ref in board.placements}
            if action.ref not in refs:
                continue
            p = board.placements[action.ref]
            for ref in refs - {action.ref}:
                q = board.placements[ref]
                dist = math.hypot(p.x_mm - q.x_mm, p.y_mm - q.y_mm)
                if dist > best_dist:
                    best_dist, best_xy = dist, (q.x_mm, q.y_mm)
        return (min(rows - 1, int(best_xy[1] / cell)), min(cols - 1, int(best_xy[0] / cell)))

    @staticmethod
    def _nearest_free(blocked: List[List[bool]], rows: int, cols: int,
                      cell: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        """Cellule libre la plus proche (recherche en anneaux BFS) — le but du
        A* doit être atteignable sans traverser l'obstacle qu'est le composant
        partenaire lui-même."""
        if not blocked[cell[0]][cell[1]]:
            return cell
        seen = {cell}
        frontier = [cell]
        while frontier:
            next_ring: List[Tuple[int, int]] = []
            for (r, c) in frontier:
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in seen:
                            if not blocked[nr][nc]:
                                return (nr, nc)
                            seen.add((nr, nc))
                            next_ring.append((nr, nc))
            frontier = next_ring
        return None

    @staticmethod
    def _astar(blocked: List[List[bool]], rows: int, cols: int,
               start: Tuple[int, int], goal: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        """A* 8-directions classique (coûts 1 / √2, heuristique octile)."""

        def heuristic(r: int, c: int) -> float:
            dr, dc = abs(r - goal[0]), abs(c - goal[1])
            return (dr + dc) + (math.sqrt(2.0) - 2.0) * min(dr, dc)

        frontier = [(0.0, start)]
        cost = {start: 0.0}
        while frontier:
            _f, (r, c) = heapq.heappop(frontier)
            if (r, c) == goal:
                return goal
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols and not blocked[nr][nc]:
                        new_cost = cost[(r, c)] + math.hypot(dr, dc)
                        if new_cost < cost.get((nr, nc), float("inf")):
                            cost[(nr, nc)] = new_cost
                            heapq.heappush(frontier, (new_cost + heuristic(nr, nc), (nr, nc)))
        return None

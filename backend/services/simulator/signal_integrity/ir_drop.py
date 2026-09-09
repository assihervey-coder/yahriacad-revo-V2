"""Chute de tension IR du réseau d'alimentation — analyse nodale numpy.

Modèle (« chute = I·R cumulée depuis la source ») :
  - nœuds = extrémités des segments routés du rail (arrondis à 10 µm) ;
  - résistance d'un segment : R = R_sheet · (L / W) avec R_sheet = ρ_cu / ép_cuv
    (≈ 4.9e-4 Ω/□ pour 35 µm) ; chaque via ajoute R_VIA fixe ;
  - charges = composants connectés au rail, I = power_w / rail_v, injectées au
    nœud « ancre » (relié par une piste) le plus proche ;
  - sources = composants fonctionnels `power`/`regulator`/`pmic` (Dirichlet à
    rail_v) ; à défaut, la première connexion du net sert de source ;
  - rail non routé → chaîne linéaire synthétique source→charges (largeur par
    défaut), afin de ne jamais retourner un résultat vide.

Système G·V = b résolu en dense (numpy.linalg.solve) — le nombre de nœuds d'un
rail reste modeste. Nets au-delà de 3 % de chute : flag + composants fautifs.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from common.log import get_logger

logger = get_logger("simulator.ir_drop")

RHO_CU = 1.72e-8              # Ω·m — résistivité du cuivre
CU_THICKNESS_MM = 0.035        # 35 µm standard
R_SHEET = RHO_CU / (CU_THICKNESS_MM * 1e-3)  # ≈ 4.914e-4 Ω/□
R_VIA_OHM = 1.0e-3             # résistance d'un via d'alimentation
DEFAULT_TRACE_WIDTH_MM = 1.0
LIMIT_PCT = 3.0                # consigne : chute ≤ 3 % du rail
SOURCE_BLOCKS = {"power", "regulator", "pmic", "vrm"}
NODE_QUANTUM_MM = 0.01


@dataclass
class IRDropResult:
    """Résultat IR drop (alimente le champ ir_drop_mv de SignalIntegrityResult)."""

    net: str = ""
    rail_v: float = 0.0
    source_ref: str = ""
    source_v: float = 0.0
    min_v: float = 0.0
    ir_drop_mv: float = 0.0
    ir_drop_pct: float = 0.0
    failing_components: List[str] = field(default_factory=list)
    component_drops_mv: Dict[str, float] = field(default_factory=dict)
    node_count: int = 0
    iterations: int = 0
    converged: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "net": self.net, "rail_v": self.rail_v, "source_ref": self.source_ref,
            "source_v": self.source_v, "min_v": round(self.min_v, 4),
            "ir_drop_mv": round(self.ir_drop_mv, 2),
            "ir_drop_pct": round(self.ir_drop_pct, 3),
            "failing_components": list(self.failing_components),
            "component_drops_mv": {k: round(v, 2) for k, v in self.component_drops_mv.items()},
            "node_count": self.node_count,
            "converged": self.converged,
            "note": self.note,
        }


def _flag_over_budget(ir_drop_pct: float, limit_pct: float = LIMIT_PCT) -> bool:
    """Prédicat de drapeau (testé unitairement) — chute au-delà de la consigne."""
    return ir_drop_pct > limit_pct


def _pad_point(board, ref: str, pad_name: str) -> Tuple[float, float]:
    """Position absolue d'une pastille — pads absolus si dans la carte, sinon centrés.

    Le modèle commun ne fixe pas la convention pad local/absolu : on détecte
    (pad dans l'emprise carte → absolu ; sinon → offset du placement).
    """
    comp = board.components[ref]
    placement = board.placements.get(ref)
    for pad in comp.pads:
        if pad.name == pad_name:
            inside = 0.0 <= pad.x_mm <= board.width_mm and 0.0 <= pad.y_mm <= board.height_mm
            if inside or placement is None:
                return (pad.x_mm, pad.y_mm)
            return (placement.x_mm + pad.x_mm, placement.y_mm + pad.y_mm)
    if placement is not None:
        return (placement.x_mm, placement.y_mm)
    return (board.width_mm / 2.0, board.height_mm / 2.0)


def _pick_power_net(board, net_name: Optional[str]) -> Optional[str]:
    """Sélection du rail analysé : nom explicite, sinon classe/net d'alimentation."""
    if net_name is not None:
        return net_name if net_name in board.nets else None
    for name, net in board.nets.items():
        if (net.net_class or "").lower() in ("power", "pwr", "alim"):
            return name
    upper_tokens = ("VCC", "VDD", "VIN", "+", "3V3", "5V", "1V8", "12V")
    for name in board.nets:
        if any(tok in name.upper() for tok in upper_tokens):
            return name
    return None


def _default_trace_width(net) -> float:
    widths = [s.width_mm for s in net.routed_segments if not s.is_via and s.width_mm > 0]
    return max(widths) if widths else DEFAULT_TRACE_WIDTH_MM


def _snap(point: Tuple[float, float], anchors: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Point d'ancrage le plus proche (nœud réellement relié par une piste)."""
    return min(anchors, key=lambda k: math.hypot(k[0] - point[0], k[1] - point[1]))


def analyze_ir_drop(
    board,
    net_name: Optional[str] = None,
    rail_v: float = 3.3,
    r_sheet_ohm_per_sq: float = R_SHEET,
    r_via_ohm: float = R_VIA_OHM,
    limit_pct: float = LIMIT_PCT,
) -> IRDropResult:
    """Résolution nodale du rail d'alimentation → IRDropResult.

    Retourne la chute max, le pourcentage et les composants au-delà de
    `limit_pct` (3 % par défaut). Jamais d'exception : rail absent/non routé →
    résultat annoté (note) pour rester consommable par la boucle multiphysique.
    """
    t0 = time.perf_counter()
    selected = _pick_power_net(board, net_name)
    if selected is None:
        return IRDropResult(note="aucun rail d'alimentation détecté")
    net = board.nets[selected]

    nodes: Dict[Tuple[float, float], int] = {}

    def node_of(x: float, y: float) -> int:
        key = (round(x / NODE_QUANTUM_MM) * NODE_QUANTUM_MM,
               round(y / NODE_QUANTUM_MM) * NODE_QUANTUM_MM)
        if key not in nodes:
            nodes[key] = len(nodes)
        return nodes[key]

    edges: List[Tuple[int, int, float]] = []  # (i, j, R ohm)

    if net.routed_segments:
        for seg in net.routed_segments:
            if seg.is_via:
                if abs(seg.x1_mm - seg.x2_mm) > 1e-9 or abs(seg.y1_mm - seg.y2_mm) > 1e-9:
                    edges.append((node_of(seg.x1_mm, seg.y1_mm),
                                  node_of(seg.x2_mm, seg.y2_mm), r_via_ohm))
                else:
                    node_of(seg.x1_mm, seg.y1_mm)  # via ponctuel → nœud de raccord
                continue
            length = seg.length_mm
            if length < NODE_QUANTUM_MM:
                node_of(seg.x1_mm, seg.y1_mm)
                continue
            width = seg.width_mm if seg.width_mm > 0 else DEFAULT_TRACE_WIDTH_MM
            resistance = r_sheet_ohm_per_sq * (length / width)
            edges.append((node_of(seg.x1_mm, seg.y1_mm),
                          node_of(seg.x2_mm, seg.y2_mm), resistance))

    # ---- classification des connexions --------------------------------------
    source_refs = [
        ref for (ref, _pad) in net.connections
        if (board.components[ref].functional_block or "").lower() in SOURCE_BLOCKS
    ] or ([net.connections[0][0]] if net.connections else [])

    loads: List[Tuple[str, float, Tuple[float, float]]] = []
    source_points: List[Tuple[float, float]] = []
    for ref, pad_name in net.connections:
        point = _pad_point(board, ref, pad_name)
        if ref in source_refs:
            source_points.append(point)
        elif board.components[ref].power_w > 0:
            loads.append((ref, board.components[ref].power_w / rail_v, point))

    if not source_refs:
        return IRDropResult(net=selected, rail_v=rail_v,
                            note="aucune source d'alimentation identifiée")

    # rail non routé : chaîne synthétique source → charges triées par distance
    if not net.routed_segments and loads:
        logger.info("rail non routé — estimation en chaîne linéaire", extra={"net": selected})
        ordered = sorted(loads, key=lambda l: math.hypot(
            l[2][0] - source_points[0][0], l[2][1] - source_points[0][1]))
        chain: List[Tuple[float, float]] = [source_points[0]] + [l[2] for l in ordered]
        width = _default_trace_width(net)
        for a, b in zip(chain, chain[1:]):
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length > NODE_QUANTUM_MM:
                edges.append((node_of(*a), node_of(*b),
                              r_sheet_ohm_per_sq * (length / width)))

    if not nodes:
        return IRDropResult(net=selected, rail_v=rail_v,
                            note="rail sans géométrie exploitable")

    # nœuds ancres = nœuds participant à au moins une piste (snapping des pads)
    anchors = [k for k, i in nodes.items() if any(i in (a, b) for a, b, _r in edges)]
    if not anchors:
        return IRDropResult(net=selected, rail_v=rail_v,
                            note="rail sans segment de cuivre exploitable")

    # ---- assemblage du système G·V = b ---------------------------------------
    n = len(nodes)
    G = np.zeros((n, n), dtype=np.float64)
    b = np.zeros(n, dtype=np.float64)
    for i, j, r in edges:
        g = 1.0 / max(r, 1e-9)
        G[i, i] += g; G[j, j] += g
        G[i, j] -= g; G[j, i] -= g
    for _ref, current, point in loads:
        idx = nodes[_snap(point, anchors)]
        b[idx] -= current

    # Dirichlet : nœuds de source fixés à rail_v
    source_nodes = {nodes[_snap(p, anchors)] for p in source_points}
    for s in source_nodes:
        G[s, :] = 0.0
        G[s, s] = 1.0
        b[s] = rail_v
    # nœuds isolés (aucune piste, aucune charge) — régularisation numérique
    diag_idx = np.arange(n)
    isolated = np.diag(G) < 1e-12
    if isolated.any():
        G[diag_idx[isolated], diag_idx[isolated]] += 1.0

    try:
        V = np.linalg.solve(G, b)
        converged = bool(np.all(np.isfinite(V)))
    except np.linalg.LinAlgError:
        V, *_ = np.linalg.lstsq(G, b, rcond=None)
        converged = False

    drops_mv: Dict[str, float] = {}
    failing: List[str] = []
    min_v = rail_v
    for ref, _current, point in loads:
        idx = nodes[_snap(point, anchors)]
        v = float(V[idx])
        min_v = min(min_v, v)
        drop_mv = (rail_v - v) * 1000.0
        drops_mv[ref] = round(drop_mv, 2)
        if _flag_over_budget((rail_v - v) / rail_v * 100.0, limit_pct):
            failing.append(ref)

    ir_drop_mv = (rail_v - min_v) * 1000.0
    ir_pct = (rail_v - min_v) / rail_v * 100.0 if rail_v > 0 else 0.0
    if failing:
        logger.warning("chute IR au-delà de la consigne",
                       extra={"net": selected, "pct": round(ir_pct, 2), "components": failing})

    return IRDropResult(
        net=selected, rail_v=rail_v,
        source_ref=", ".join(sorted(set(source_refs))),
        source_v=rail_v, min_v=round(min_v, 4),
        ir_drop_mv=round(ir_drop_mv, 2), ir_drop_pct=round(ir_pct, 3),
        failing_components=failing, component_drops_mv=drops_mv,
        node_count=n, iterations=n, converged=converged,
        note="",
    )

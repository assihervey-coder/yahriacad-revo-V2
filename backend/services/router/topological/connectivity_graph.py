"""Graphe de connectivité — préparation topologique du routage (section 6.4).

Construit la vue nœuds (pads) / arêtes (nets) d'un Board, fournit les points
d'ancrage par net, l'arbre couvrant minimal (paires à relier) et la liste des
couches autorisées. Le pathfinder consomme ces primitives.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Board  # noqa: E402
from common.log import get_logger  # noqa: E402

logger = get_logger("router.connectivity")


@dataclass(frozen=True)
class PadNode:
    """Nœud du graphe : une pastille positionnée (coordonnées absolues)."""

    ref: str
    pad: str
    x_mm: float
    y_mm: float
    layer: int = 0


class ConnectivityGraph:
    """Vue topologique du Board : nets → nœuds de pads, avec MST par net."""

    def __init__(self, board: Board) -> None:
        self.board = board
        self._pads: Dict[str, List[PadNode]] = {}
        for ref, comp in board.components.items():
            placement = board.placements.get(ref)
            layer = placement.layer if placement else 0
            for pad in comp.pads:
                net = self._resolve_net(pad.net, ref, pad.name)
                if net is None:
                    continue
                self._pads.setdefault(net, []).append(
                    PadNode(ref, pad.name, pad.x_mm, pad.y_mm, layer))

    def _resolve_net(self, declared: Optional[str], ref: str, pad: str) -> Optional[str]:
        """Net d'une pastille : champ direct, sinon résolution via net.connections."""
        if declared:
            return declared
        for name, net in self.board.nets.items():
            if (ref, pad) in net.connections:
                return name
        return None

    def net_endpoints(self, net_name: str) -> List[Tuple[str, str, Tuple[float, float]]]:
        """Points d'ancrage d'un net : [(ref, pad, (x_mm, y_mm)), ...]."""
        return [(node.ref, node.pad, (node.x_mm, node.y_mm))
                for node in self._pads.get(net_name, [])]

    def nodes(self, net_name: str) -> List[PadNode]:
        """Nœuds complets (avec couche) attachés à un net."""
        return list(self._pads.get(net_name, []))

    def mst_pairs(self, net_name: str) -> List[Tuple[PadNode, PadNode]]:
        """Arbre couvrant minimal (Prim) — paires de pads à relier par le router."""
        nodes = self._pads.get(net_name, [])
        if len(nodes) < 2:
            return []
        in_tree = [nodes[0]]
        remaining = nodes[1:]
        pairs: List[Tuple[PadNode, PadNode]] = []
        while remaining:
            best = min(
                ((a, b) for a in in_tree for b in remaining),
                key=lambda pair: (pair[0].x_mm - pair[1].x_mm) ** 2
                + (pair[0].y_mm - pair[1].y_mm) ** 2,
            )
            pairs.append(best)
            in_tree.append(best[1])
            remaining.remove(best[1])
        return pairs

    def unrouted_net_names(self) -> List[str]:
        """Nets à router : non routés et possédant au moins deux pastilles."""
        return [name for name, net in self.board.nets.items()
                if not net.is_routed and len(self._pads.get(name, [])) >= 2]

    def layer_allowlist(self, net_name: str) -> List[int]:
        """Couches cuivre autorisées pour un net (intersection avec la pile)."""
        net = self.board.nets.get(net_name)
        allowed = net.layer_allowlist if net and net.layer_allowlist else list(range(len(self.board.layers)))
        valid = [layer for layer in allowed if 0 <= layer < len(self.board.layers)]
        return valid or [0]

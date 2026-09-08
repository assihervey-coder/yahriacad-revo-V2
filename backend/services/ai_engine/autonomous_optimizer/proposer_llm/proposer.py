"""Proposer de modifications candidates — 100 % heuristiques déterministes.

Familles de propositions, motivées par le score courant + graphe d'intention :
  * rotate        — rotations 90/180/270 des composants les plus encombrants ;
  * swap          — permutation de deux composants de blocs différents quand
                    elle réduit la HPWL totale ;
  * spread_hot    — écartement des zones chaudes (composants de puissance) ;
  * regroup       — rapprochement des membres d'un même bloc fonctionnel ;
  * compact_net   — rapprochement du centroïde des partenaires de ses nets.
Le hook ``llm_refine`` (callable optionnel) peut réordonner/filtrer les
propositions ; en son absence l'ordre heuristique est conservé — aucune
proposition « non structurée » n'entre jamais dans la boucle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class Proposal:
    """Modification candidate sérialisable — consommée par apply_proposal()."""

    json: Dict[str, Any]                 # {action, ref(s), x, y, rotation_deg}
    description: str
    targets: List[str] = field(default_factory=list)
    kind: str = ""

    def to_json(self) -> Dict[str, Any]:
        return dict(self.json, description=self.description,
                    targets=list(self.targets), kind=self.kind)


def apply_proposal(board: Any, proposal: Proposal) -> bool:
    """Applique une proposition au board (move/rotate/swap) — False si refusée."""
    payload = proposal.json
    action = payload.get("action")
    try:
        if action == "rotate":
            board.move(payload["ref"], board.placements[payload["ref"]].x_mm,
                       board.placements[payload["ref"]].y_mm,
                       rotation_deg=float(payload["rotation_deg"]))
        elif action == "move":
            board.move(payload["ref"], float(payload["x"]), float(payload["y"]))
        elif action == "swap":
            ref_a, ref_b = payload["ref_a"], payload["ref_b"]
            pa, pb = board.placements[ref_a], board.placements[ref_b]
            board.move(ref_b, pa.x_mm, pa.y_mm)
            board.move(ref_a, pb.x_mm, pb.y_mm)
        else:
            return False
    except (KeyError, PermissionError):
        return False
    return True


def hpwl_mm(board: Any) -> float:
    """Longueur totale estimée des nets (Half-Perimeter Wire Length)."""
    total = 0.0
    for net in board.nets.values():
        points = [(board.placements[r].x_mm, board.placements[r].y_mm)
                  for r, _p in net.connections if r in board.placements]
        if len(points) >= 2:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


class HeuristicProposer:
    """Générateur de propositions — déterministe, sémantiquement motivé."""

    def __init__(self, llm_refine: Optional[Callable[[List[Proposal]], List[Proposal]]] = None) -> None:
        self.llm_refine = llm_refine      # hook optionnel (jamais requis)

    # ---- API -----------------------------------------------------------------
    def propose(self, board: Any, intent_graph: Any = None, n: int = 6) -> List[Proposal]:
        """Jusqu'à n propositions candidates, motivées et sérialisables.

        Les familles sont entrelacées (rotate, swap, spread_hot, regroup,
        compact_net) pour qu'un lot de 3 ne soit jamais monoculture — la
        boucle nocturne explore ainsi toutes les stratégies à chaque passe.
        """
        buckets: List[List[Proposal]] = [
            self._rotate_big(board),
            self._swap_for_hpwl(board),
            self._spread_hot(board),
            self._regroup_blocks(board, intent_graph),
            self._compact_net(board),
        ]
        proposals: List[Proposal] = []
        index = 0
        while len(proposals) < max(0, n) and any(buckets):
            bucket = buckets[index % len(buckets)]
            index += 1
            if bucket:
                proposals.append(bucket.pop(0))
        if self.llm_refine is not None:
            try:
                proposals = self.llm_refine(proposals) or proposals
            except Exception:
                pass                      # le hook ne doit jamais casser la boucle
        return proposals

    # ---- générateurs -----------------------------------------------------------
    def _rotate_big(self, board: Any) -> List[Proposal]:
        """Rotation des composants non carrés à grande empreinte — peut débloquer
        le routage (une rotation d'empreinte carrée est un no-op exclu)."""
        out: List[Proposal] = []
        ranked = sorted(
            (c for c in board.components.values() if c.width_mm != c.height_mm),
            key=lambda c: -(c.width_mm * c.height_mm * (1.0 + c.power_w)))
        for comp in ranked[:2]:
            for rotation in (90.0, 180.0):
                out.append(Proposal(
                    json={"action": "rotate", "ref": comp.ref, "rotation_deg": rotation},
                    description=(f"Rotation {rotation:.0f}° de {comp.ref} "
                                 f"({comp.width_mm}x{comp.height_mm} mm) pour libérer le corridor"),
                    targets=[comp.ref], kind="rotate"))
        return out

    def _swap_for_hpwl(self, board: Any) -> List[Proposal]:
        """Permutation des deux composants qui réduit le plus la HPWL totale."""
        best: Optional[Tuple[float, str, str]] = None
        refs = [r for r in board.placements if r in board.components]
        baseline = hpwl_mm(board)
        for i, ref_a in enumerate(refs):
            for ref_b in refs[i + 1:]:
                pa, pb = board.placements[ref_a], board.placements[ref_b]
                if pa.locked or pb.locked:
                    continue
                board.placements[ref_a], board.placements[ref_b] = pb, pa   # swap éphémère
                delta = hpwl_mm(board) - baseline
                board.placements[ref_a], board.placements[ref_b] = pa, pb
                if delta < -1e-6 and (best is None or delta < best[0]):
                    best = (delta, ref_a, ref_b)
        if best is None:
            return []
        delta, ref_a, ref_b = best
        return [Proposal(
            json={"action": "swap", "ref_a": ref_a, "ref_b": ref_b},
            description=(f"Permutation {ref_a} ↔ {ref_b} : HPWL réduite de {-delta:.1f} mm"),
            targets=[ref_a, ref_b], kind="swap")]

    def _spread_hot(self, board: Any) -> List[Proposal]:
        """Écarte le composant le plus chaud de son voisin puissant le plus proche."""
        hot = [c for c in board.components.values() if c.power_w > 0.2]
        if len(hot) < 2:
            return []
        source = max(hot, key=lambda c: c.power_w)
        p0 = board.placements[source.ref]
        nearest, nearest_d = None, float("inf")
        for comp in hot:
            if comp.ref == source.ref:
                continue
            p = board.placements[comp.ref]
            d = math.hypot(p.x_mm - p0.x_mm, p.y_mm - p0.y_mm)
            if d < nearest_d:
                nearest, nearest_d = comp, d
        if nearest is None or nearest_d < 1e-3:
            return []
        q = board.placements[nearest.ref]
        dx, dy = p0.x_mm - q.x_mm, p0.y_mm - q.y_mm
        norm = math.hypot(dx, dy) or 1.0
        step = 5.0
        new_x = min(max(p0.x_mm + dx / norm * step, 0.0), board.width_mm)
        new_y = min(max(p0.y_mm + dy / norm * step, 0.0), board.height_mm)
        return [Proposal(
            json={"action": "move", "ref": source.ref, "x": round(new_x, 2), "y": round(new_y, 2)},
            description=(f"Écartement de {source.ref} ({source.power_w:.1f} W) de "
                         f"{nearest.ref} : espacement thermique +{step:.0f} mm"),
            targets=[source.ref], kind="spread_hot")]

    def _regroup_blocks(self, board: Any, intent_graph: Any) -> List[Proposal]:
        """Rapproche un composant du centroïde des membres de son bloc fonctionnel."""
        blocks: Dict[str, List[str]] = {}
        for comp in board.components.values():
            if comp.functional_block:
                blocks.setdefault(comp.functional_block, []).append(comp.ref)
        out: List[Proposal] = []
        for block, refs in blocks.items():
            if len(refs) < 2:
                continue
            cx = sum(board.placements[r].x_mm for r in refs) / len(refs)
            cy = sum(board.placements[r].y_mm for r in refs) / len(refs)
            farthest = max(refs, key=lambda r: math.hypot(
                board.placements[r].x_mm - cx, board.placements[r].y_mm - cy))
            p = board.placements[farthest]
            if math.hypot(p.x_mm - cx, p.y_mm - cy) < 3.0:
                continue
            out.append(Proposal(
                json={"action": "move", "ref": farthest,
                      "x": round(cx, 2), "y": round(cy, 2)},
                description=(f"Regroupement du bloc « {block} » : {farthest} vers le "
                             f"centroïde du bloc ({cx:.1f}, {cy:.1f})"),
                targets=[farthest], kind="regroup"))
        return out[:2]

    def _compact_net(self, board: Any) -> List[Proposal]:
        """Rapproche un composant du centroïde des partenaires de ses nets."""
        partners: Dict[str, List[str]] = {}
        for net in board.nets.values():
            refs = [r for r, _p in net.connections if r in board.placements]
            for ref in refs:
                partners.setdefault(ref, []).extend(r for r in refs if r != ref)
        out: List[Proposal] = []
        for ref, others in partners.items():
            if not others or board.placements[ref].locked:
                continue
            cx = sum(board.placements[r].x_mm for r in others) / len(others)
            cy = sum(board.placements[r].y_mm for r in others) / len(others)
            p = board.placements[ref]
            if math.hypot(p.x_mm - cx, p.y_mm - cy) < 2.0:
                continue
            out.append(Proposal(
                json={"action": "move", "ref": ref, "x": round(cx, 2), "y": round(cy, 2)},
                description=(f"Rapprochement de {ref} vers le centroïde de ses "
                             f"{len(set(others))} partenaires de nets"),
                targets=[ref], kind="compact_net"))
        return out[:2]

"""Graphe d'intention — « mémoire de long terme » partagée par tous les agents.

Stocke les nœuds (bloc fonctionnel, composant, net, zone) et les arêtes typées
(alimente, mesure, contraint, sensitive_to_noise, beside_of, member_of,
connecte) dans de simples dictionnaires — aucune base externe requise. Chaque
mutation incrémente une version (modèle event-sourcing léger) : les agents
peuvent donc détecter qu'ils travaillent sur une représentation périmée.
Peuplé dès l'étape 1 (NL → SKiDL), il répond aussi à « pourquoi » via
:meth:`IntentGraph.why` — intention posée + alternatives rejetées.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

# Nomenclature canonique des nœuds et arêtes (section 05.1 de la spécification)
NODE_KINDS = ("functional_block", "component", "net", "zone")

# Types d'arêtes canoniques + extension documentée :
#   alimente          : net d'alim → composant
#   mesure            : composant → net analogique qu'il surveille
#   contraint         : zone → composant (keepout, thermique)
#   sensitive_to_noise: net sensible → source de bruit voisine
#   beside_of         : préférence de voisinage spatial
#   member_of         : composant → bloc fonctionnel (extension)
#   connecte          : composant → net via ses pastilles (extension)
EDGE_TYPES = (
    "alimente", "mesure", "contraint", "sensitive_to_noise",
    "beside_of", "member_of", "connecte",
)

_SEQ = itertools.count()


@dataclass
class IntentNode:
    """Nœud sémantique du graphe d'intention."""

    id: str
    kind: str                              # un des NODE_KINDS
    label: str = ""
    attrs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentEdge:
    """Arête typée orientée entre deux nœuds."""

    src: str
    dst: str
    edge_type: str
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple:
        return (self.src, self.dst, self.edge_type)


class IntentGraph:
    """Graphe sémantique versionné — une seule source de vérité partagée."""

    def __init__(self, project_id: str = "") -> None:
        self.project_id = project_id
        self._nodes: Dict[str, IntentNode] = {}
        self._edges: Dict[tuple, IntentEdge] = {}
        self._intentions: Dict[str, str] = {}          # node_id -> intention posée
        self._rejected: Dict[str, List[str]] = {}      # node_id -> alternatives rejetées
        self._version = 0

    # ---- mutations ----------------------------------------------------------
    def _mutate(self) -> int:
        """Incrémente la version à chaque mutation (contrat event-sourcing)."""
        self._version += 1
        return self._version

    def add_node(self, node_id: str, kind: str, label: str = "", **attrs: Any) -> IntentNode:
        """Ajoute (ou met à jour) un nœud ; retourne le nœud stocké."""
        if kind not in NODE_KINDS:
            raise ValueError(f"kind de nœud inconnu : {kind}")
        node = self._nodes.get(node_id)
        if node is None:
            node = IntentNode(id=node_id, kind=kind, label=label or node_id, attrs=dict(attrs))
            self._nodes[node_id] = node
            self._mutate()
        elif attrs:
            node.attrs.update(attrs)
            self._mutate()
        return node

    def add_edge(self, src: str, dst: str, edge_type: str, **attrs: Any) -> IntentEdge:
        """Ajoute une arête typée (idempotent sur le triplet src/dst/type)."""
        if edge_type not in EDGE_TYPES:
            raise ValueError(f"type d'arête inconnu : {edge_type}")
        key = (src, dst, edge_type)
        edge = self._edges.get(key)
        if edge is None:
            edge = IntentEdge(src=src, dst=dst, edge_type=edge_type, attrs=dict(attrs))
            self._edges[key] = edge
            self._mutate()
        elif attrs:
            edge.attrs.update(attrs)
            self._mutate()
        return edge

    def set_intention(self, node_id: str, intention: str) -> None:
        """Enregistre l'intention (le « pourquoi ») attachée à un nœud."""
        self._intentions[node_id] = intention
        self._mutate()

    def reject_alternative(self, node_id: str, description: str) -> None:
        """Journalise une alternative rejetée — contextuellement décisif pour why()."""
        self._rejected.setdefault(node_id, []).append(description)
        self._mutate()

    # ---- consultation --------------------------------------------------------
    def neighbors(self, node_id: str, edge_type: Optional[str] = None) -> List[str]:
        """Voisins d'un nœud (sortants + entrants), filtrables par type d'arête."""
        found: List[str] = []
        for (src, dst, etype) in self._edges:
            if node_id not in (src, dst):
                continue
            if edge_type is not None and etype != edge_type:
                continue
            found.append(dst if src == node_id else src)
        return sorted(set(found))

    def why(self, node_id: str) -> Dict[str, Any]:
        """Contexte décisionnel d'un nœud : intention, alternatives rejetées, voisinage."""
        node = self._nodes[node_id]
        return {
            "node": {"id": node.id, "kind": node.kind, "label": node.label, "attrs": node.attrs},
            "intention": self._intentions.get(node_id, ""),
            "alternatives_rejetees": list(self._rejected.get(node_id, [])),
            "voisinage": {
                etype: self.neighbors(node_id, etype)
                for etype in EDGE_TYPES
                if self.neighbors(node_id, etype)
            },
            "version_graphe": self._version,
        }

    def node(self, node_id: str) -> Optional[IntentNode]:
        return self._nodes.get(node_id)

    @property
    def version(self) -> int:
        return self._version

    @property
    def stats(self) -> Dict[str, int]:
        by_kind: Dict[str, int] = {}
        for node in self._nodes.values():
            by_kind[node.kind] = by_kind.get(node.kind, 0) + 1
        return {"nodes": len(self._nodes), "edges": len(self._edges), "version": self._version, **by_kind}

    # ---- synchronisation avec le modèle de design ----------------------------
    def update_from_board(self, board: Any) -> int:
        """Synchronise le graphe depuis un Board du modèle commun.

        Règles déterministes : nœud composant/net/zone par entité, nœud bloc
        fonctionnel regroupant ses composants (member_of), arêtes connecte
        depuis net.connections, alimente pour les nets d'alim, mesure pour les
        nets analogiques, contraint pour les zones recouvrant un composant.
        Retourne le nombre de mutations appliquées.
        """
        before = self._version
        for comp in board.components.values():
            self.add_node(f"component/{comp.ref}", "component", comp.ref,
                          mpn=comp.mpn, power_w=comp.power_w, pins=comp.pins)
            if comp.functional_block:
                block_id = f"block/{comp.functional_block}"
                self.add_node(block_id, "functional_block", comp.functional_block)
                self.add_edge(f"component/{comp.ref}", block_id, "member_of")
        for net in board.nets.values():
            self.add_node(f"net/{net.name}", "net", net.name,
                          net_class=net.net_class, impedance=net.impedance_target_ohm)
            for (ref, _pad) in net.connections:
                self.add_edge(f"component/{ref}", f"net/{net.name}", "connecte")
            if net.net_class == "power":
                for (ref, _pad) in net.connections:
                    self.add_edge(f"net/{net.name}", f"component/{ref}", "alimente")
            if net.net_class == "analog":
                for (ref, _pad) in net.connections:
                    self.add_edge(f"component/{ref}", f"net/{net.name}", "mesure")
        for zone in board.zones:
            zid = f"zone/{zone.name}"
            self.add_node(zid, "zone", zone.name, zone_kind=zone.kind, max_temp_c=zone.max_temp_c)
            for ref, placement in board.placements.items():
                comp = board.components.get(ref)
                if comp is None:
                    continue
                x1, y1, x2, y2 = comp.bounding_box(placement)
                if x1 >= zone.x_min_mm and x2 <= zone.x_max_mm and y1 >= zone.y_min_mm and y2 <= zone.y_max_mm:
                    self.add_edge(zid, f"component/{ref}", "contraint")
        return self._version - before

    # ---- persistance ----------------------------------------------------------
    def export_json(self) -> Dict[str, Any]:
        """Sérialisation complète (nœuds, arêtes, intentions, rejets, version)."""
        return {
            "project_id": self.project_id,
            "version": self._version,
            "nodes": [{"id": n.id, "kind": n.kind, "label": n.label, "attrs": n.attrs}
                      for n in self._nodes.values()],
            "edges": [{"src": e.src, "dst": e.dst, "edge_type": e.edge_type, "attrs": e.attrs}
                      for e in self._edges.values()],
            "intentions": dict(self._intentions),
            "rejected": {k: list(v) for k, v in self._rejected.items()},
        }

    def import_json(self, data: Dict[str, Any]) -> None:
        """Restaure un état exporté — remplace intégralement le graphe courant."""
        self.__init__(project_id=data.get("project_id", ""))
        for raw in data.get("nodes", []):
            self.add_node(raw["id"], raw["kind"], raw.get("label", ""), **raw.get("attrs", {}))
        for raw in data.get("edges", []):
            self.add_edge(raw["src"], raw["dst"], raw["edge_type"], **raw.get("attrs", {}))
        self._intentions.update(data.get("intentions", {}))
        for node_id, items in data.get("rejected", {}).items():
            for item in items:
                self.reject_alternative(node_id, item)
        self._version = int(data.get("version", self._version))

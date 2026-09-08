"""Parseur de schéma KiCad (.kicad_sch, s-expression) — section 6.1.

Extrait les instances (symbol (lib_id ...) (property "Reference" "R1") ...) en
composants et les (wire (pts (xy ...) (xy ...))) comme indices de routage.
Message de repli explicite si le format n'est pas reconnu.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Component  # noqa: E402
from common.log import get_logger  # noqa: E402

from netlist_parser.spice_parser import default_package_for, guess_functional_block  # noqa: E402

logger = get_logger("parser.kicad_sch")

_TOKEN_RE = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


def _tokenize(text: str) -> List[str]:
    """Découpe le fichier en parenthèses / chaînes quotées / atomes."""
    return _TOKEN_RE.findall(text)


def _parse_sexprs(tokens: List[str]) -> List[Any]:
    """Parse les s-expressions en listes imbriquées (atomes = chaînes)."""
    position = 0

    def parse_one() -> Any:
        nonlocal position
        token = tokens[position]
        if token == "(":
            position += 1
            node: List[Any] = []
            while position < len(tokens) and tokens[position] != ")":
                node.append(parse_one())
            position += 1  # saute ')' (tolérant aux déséquilibres)
            return node
        position += 1
        if token.startswith('"'):
            return token[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return token

    trees: List[Any] = []
    while position < len(tokens):
        if tokens[position] == ")":  # parenthèse fermante orpheline — ignorée
            position += 1
            continue
        trees.append(parse_one())
    return trees


def _head(node: Any) -> str:
    """Nom d'un nœud s-expression (« symbol », « wire », ...) ou chaîne vide."""
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else ""


def _children(node: List[Any], name: str) -> List[List[Any]]:
    return [child for child in node[1:] if isinstance(child, list) and _head(child) == name]


def _atoms(node: List[Any]) -> List[str]:
    return [child for child in node[1:] if isinstance(child, str)]


def _walk(node: Any) -> Iterable[Any]:
    if isinstance(node, list):
        yield node
        for child in node[1:]:
            yield from _walk(child)


@dataclass
class SymbolInstance:
    """Instance de symbole : composant + position schéma (mm, Y inversé)."""

    component: Component
    x_mm: float
    y_mm: float
    rotation_deg: float


@dataclass
class KiCadSchematic:
    """Résultat intermédiaire consommé par netlist_parser.normalizer."""

    components: List[SymbolInstance] = field(default_factory=list)
    wire_hints: List[Tuple[Tuple[float, float], Tuple[float, float]]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    net_connections: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)


def _estimate_pins(lib_id: str, pin_nodes: List[List[Any]]) -> int:
    """Nombre d'épingles : comptées si présentes, sinon heuristique par préfixe."""
    if pin_nodes:
        return len(pin_nodes)
    base = lib_id.split(":", 1)[-1].upper()
    if base[:1] in {"R", "C", "L", "D"}:
        return 2
    return 8


def _symbol_to_instance(node: List[Any], seen_refs: Dict[str, int],
                        warnings: List[str]) -> Optional[SymbolInstance]:
    """Convertit un nœud (symbol (lib_id ...)) en SymbolInstance."""
    lib_id_nodes = _children(node, "lib_id")
    if not lib_id_nodes:
        return None  # définition de lib_symbols (sans lib_id) — ignorée
    lib_id = str(lib_id_nodes[0][1]) if len(lib_id_nodes[0]) > 1 else ""
    at_nodes = _children(node, "at")
    atoms = _atoms(at_nodes[0]) if at_nodes else []
    x = float(atoms[0]) if len(atoms) > 0 else 0.0
    y = -float(atoms[1]) if len(atoms) > 1 else 0.0  # KiCad : Y vers le bas → inversion
    rotation = float(atoms[2]) if len(atoms) > 2 else 0.0

    props: Dict[str, str] = {}
    for prop in _children(node, "property"):
        values = _atoms(prop)
        if len(values) >= 2:
            props[values[0]] = values[1]

    ref = props.get("Reference", "")
    if not ref:
        ref = f"X?{len(warnings) + 1}"
        warnings.append(f"symbole sans référence ({lib_id}) — ref générée « {ref} »")
    if ref in seen_refs:
        seen_refs[ref] += 1
        warnings.append(f"référence dupliquée « {ref} » renommée en « {ref}_{seen_refs[ref]} »")
        ref = f"{ref}_{seen_refs[ref]}"
    else:
        seen_refs[ref] = 0

    prefix = ref[:1].upper()
    footprint, width, height, price = default_package_for(prefix)
    if props.get("Footprint"):
        footprint = props["Footprint"]
    pins = _estimate_pins(lib_id, _children(node, "pin"))
    component = Component(
        ref=ref,
        mpn=props.get("Value", "") if prefix in {"U", "X", "Q", "M"} else "",
        value=props.get("Value", ""),
        footprint=footprint,
        pins=pins,
        width_mm=width,
        height_mm=height,
        price_usd=price,
        functional_block=guess_functional_block(prefix, props.get("Value", "")),
    )
    return SymbolInstance(component=component, x_mm=x, y_mm=y, rotation_deg=rotation)


def parse_kicad_sch(text: str) -> KiCadSchematic:
    """Parse un .kicad_sch minimal : symboles → composants, wires → indices."""
    result = KiCadSchematic()
    tokens = _tokenize(text)
    if "(" not in tokens:
        raise ValueError("contenu illisible : aucune s-expression trouvée (format .kicad_sch attendu)")
    trees = _parse_sexprs(tokens)
    if not trees or _head(trees[0]) != "kicad_sch":
        result.warnings.append("en-tête (kicad_sch ...) absent — lecture opportuniste tentée")

    seen_refs: Dict[str, int] = {}
    for tree in trees:
        for node in _walk(tree):
            if isinstance(node, list) and _head(node) == "symbol":
                instance = _symbol_to_instance(node, seen_refs, result.warnings)
                if instance is not None:
                    result.components.append(instance)
    if not result.components:
        raise ValueError(
            "format non reconnu : aucun (symbol (lib_id ...)) trouvé — "
            "s'agit-il bien d'un fichier .kicad_sch ?"
        )

    for tree in trees:
        for node in _walk(tree):
            if not (isinstance(node, list) and _head(node) == "wire"):
                continue
            pts = _children(node, "pts")
            if not pts:
                continue
            coords = [_children(pts[0], "xy")]
            endpoints: List[Tuple[float, float]] = []
            for xy in coords[0]:
                values = _atoms(xy)
                if len(values) >= 2:
                    endpoints.append((float(values[0]), -float(values[1])))
            if len(endpoints) == 2:
                result.wire_hints.append((endpoints[0], endpoints[1]))

    result.warnings.append(
        "schéma sans netlist : connectivité non résolue "
        f"({len(result.wire_hints)} wires conservés comme indices de routage)"
    )
    logger.info("schéma KiCad analysé", extra={"components": len(result.components),
                                               "wire_hints": len(result.wire_hints)})
    return result

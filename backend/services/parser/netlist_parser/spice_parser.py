"""Parseur de netlist SPICE — première brique de l'import (section 6.1).

Lit les lignes composants (R1 1 2 10k, C1 2 0 100n, U1 3 4 IC), les
sous-circuits .subckt/.ends (composants internes préfixés), ignore
.include/.model/.end ; gère les commentaires (* et ;) et les continuations
(+). Sortie : composants + carte de connexions {net: [(ref, pad), ...]}.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Component  # noqa: E402
from common.log import get_logger  # noqa: E402

logger = get_logger("parser.spice_parser")

# Empreintes/boîtiers par défaut par préfixe SPICE (heuristique déterministe)
_DEFAULT_PACKAGES: Dict[str, Tuple[str, float, float, float]] = {
    "R": ("Resistor_SMD:R_0603_1608Metric", 1.6, 0.8, 0.003),
    "C": ("Capacitor_SMD:C_0603_1608Metric", 1.6, 0.8, 0.010),
    "L": ("Inductor_SMD:L_0603_1608Metric", 1.6, 0.8, 0.020),
    "D": ("Diode_SMD:D_SOD-123", 3.7, 1.6, 0.050),
    "Q": ("Package_TO_SOT_SMD:SOT-23", 2.9, 2.5, 0.100),
    "M": ("Package_TO_SOT_SMD:SOT-23", 2.9, 2.5, 0.150),
    "J": ("Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", 5.1, 2.5, 0.050),
    "X": ("Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", 10.2, 2.5, 0.100),
    "U": ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", 3.9, 4.9, 0.350),
}
_TWO_PIN_PREFIXES = {"R", "C", "L", "D"}

# Valeurs SPICE : 10k, 100n, 1meg, 4k7, 2R2, 3.3 (m = milli, meg = méga)
_VALUE_RE = re.compile(r"^(?P<mant>[0-9]*\.?[0-9]+)\s*(?P<suf>meg|[TGKMUNPFRtgkmunpfr])?(?P<dec>[0-9]+)?$")
_MULT = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
         "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15, "r": 1.0}


def parse_spice_value(token: str) -> float:
    """Convertit une valeur SPICE en ohms/farads/henrys (4k7 → 4700.0)."""
    match = _VALUE_RE.match(token.strip())
    if not match:
        raise ValueError(f"valeur SPICE illisible : {token!r}")
    mant, suffix, dec = match.group("mant"), (match.group("suf") or "").lower(), match.group("dec")
    value = float(mant)
    if dec:  # notation « 4k7 » : décimales après le suffixe
        value = float(f"{mant}.{dec}")
    return value * _MULT.get(suffix, 1.0)


def default_package_for(prefix: str) -> Tuple[str, float, float, float]:
    """Empreinte, largeur, hauteur et prix par défaut pour un préfixe donné."""
    return _DEFAULT_PACKAGES.get(prefix.upper(), _DEFAULT_PACKAGES["U"])


def guess_functional_block(prefix: str, value: str) -> str:
    """Bloc fonctionnel heuristique — consommé par le BOM et le placement RL."""
    upper = (value or "").upper()
    if any(k in upper for k in ("RFM", "SX1", "SX6", "LORA", "NRF", "CC11")):
        return "rf"
    if prefix == "U" and any(k in upper for k in ("STM", "ESP", "PIC", "ATMEGA", "GD32", "MK6")):
        return "mcu"
    if prefix in {"X", "J"}:
        return "connector"
    if prefix in {"Q", "M"}:
        return "power"
    if prefix in {"R", "C", "L"}:
        return "passive"
    return "logic"


@dataclass
class SpiceNetlist:
    """Résultat intermédiaire consommé par netlist_parser.normalizer."""

    components: List[Component] = field(default_factory=list)
    net_connections: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _canonical_net(node: str) -> str:
    """Normalise un nœud SPICE : la masse « 0 »/« gnd » devient GND, reste en majuscules."""
    name = node.strip().upper()
    return "GND" if name in {"0", "GND"} else (name or "N?")


def _make_component(ref: str, prefix: str, value: str, node_count: int) -> Component:
    footprint, width, height, price = default_package_for(prefix)
    pins = max(node_count, 2)
    if prefix == "X" and node_count > 4:  # connecteur : une rangée d'épingles
        footprint = f"Connector_PinHeader_2.54mm:PinHeader_1x{pins}_P2.54mm_Vertical"
        width = 2.54 * pins + 1.0
    elif prefix in {"U"} and node_count > 8:  # CI à nombreuses épingles
        width = max(width, 1.3 * (node_count / 2 + 1))
        height = max(height, 4.0)
        footprint = f"Package_QFP:LQFP-{pins}_custom"
    return Component(
        ref=ref,
        mpn=value if prefix in {"U", "X", "Q", "M"} else "",
        value=value,
        footprint=footprint,
        pins=pins,
        width_mm=width,
        height_mm=height,
        price_usd=price,
        functional_block=guess_functional_block(prefix, value),
    )


def _handle_directive(line: str, subckt_stack: List[str], result: SpiceNetlist) -> None:
    """Traite les directives pointées (.subckt/.ends/.include/.model/.end)."""
    tokens = line.split()
    keyword = tokens[0].lower()
    if keyword == ".subckt":
        name = tokens[1] if len(tokens) > 1 else "?"
        subckt_stack.append(name)
        result.warnings.append(f"sous-circuit '{name}' intégré (composants internes préfixés)")
    elif keyword == ".ends":
        if subckt_stack:
            subckt_stack.pop()
        else:
            result.warnings.append("directive .ends orpheline ignorée")
    elif keyword == ".include":
        target = tokens[1] if len(tokens) > 1 else "?"
        result.warnings.append(f"directive .include ignorée ({target})")
    elif keyword in {".model", ".end", ".param", ".option", ".global"}:
        logger.debug("directive SPICE ignorée", extra={"directive": keyword})
    else:
        result.warnings.append(f"directive SPICE inconnue ignorée : {keyword}")


def parse_spice(text: str) -> SpiceNetlist:
    """Parse une netliste SPICE en composants + connexions (ref, pad) par net."""
    result = SpiceNetlist()
    comps: Dict[str, Component] = {}

    # 1) fusion des continuations « + » et retrait des commentaires (* et ;)
    logical: List[str] = []
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("*"):
            continue
        if line.startswith("+") and logical:
            logical[-1] += " " + line[1:].strip()
            continue
        logical.append(line.strip())

    subckt_stack: List[str] = []
    for line in logical:
        if line.lower().startswith("."):
            _handle_directive(line, subckt_stack, result)
            continue
        tokens = line.split()
        ref_raw = tokens[0]
        prefix = ref_raw[0].upper()
        # Convention de référence (alignée sur le benchmark) : la lettre SPICE
        # "X" est un marqueur de sous-circuit — si elle est suivie du préfixe
        # métier (XU1, XR1, XC1...), on la retire : XU1 → U1, XR1 → R1.
        if (prefix == "X" and len(ref_raw) >= 3 and ref_raw[1].isalpha()
                and ref_raw[2:].isdigit()):
            ref_raw = ref_raw[1:]
            prefix = ref_raw[0].upper()
        scope = subckt_stack[-1] if subckt_stack else ""
        ref = f"{scope}_{ref_raw}" if scope else ref_raw
        if len(tokens) >= 4:  # dernier token = valeur/modèle, le reste = nœuds
            nodes, value = tokens[1:-1], tokens[-1]
        else:
            nodes, value = tokens[1:], ""
        if prefix == "X" and "=" in value:  # instance X : nom du sous-circuit seul
            value = value.split("=", 1)[0]
        if ref in comps:
            result.warnings.append(f"référence dupliquée dans la netlist : {ref} (ignorée)")
            continue
        comps[ref] = _make_component(ref, prefix, value, len(nodes))
        for index, node in enumerate(nodes):
            net = _canonical_net(node)
            result.net_connections.setdefault(net, []).append((ref, str(index + 1)))

    result.components = list(comps.values())
    if not result.components:
        result.warnings.append("aucun composant détecté dans la netlist SPICE")
    logger.info("netlist SPICE analysée",
                extra={"components": len(result.components), "nets": len(result.net_connections)})
    return result

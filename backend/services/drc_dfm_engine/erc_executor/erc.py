"""erc_executor — exécution des vérifications ERC électriques [Circuitron, section 6.4].

Les erreurs ERC sont émises comme événements ERC_ERROR et consommées par le
corrector_agent (boucle de correction runtime jusqu'à convergence). L'analyse
combine le modèle interne (Board) et une lecture statique du script SKiDL
(regex sur les Net/Part) — les deux sources se complètent.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Board, Net  # noqa: E402
from common.events import EventType, make_event  # noqa: E402
from common.log import get_logger  # noqa: E402
from design_rules.checker import Violation  # noqa: E402

logger = get_logger("drc.erc")

# motifs SKiDL : Part('Device:R', value='10k'), Net('GND'), + / connectés
_RE_PART = re.compile(r"Part\s*\(\s*['\"]([^'\"]+)['\"]")
_RE_NET = re.compile(r"Net\s*\(\s*['\"]([^'\"]+)['\"]")
_RE_CONNECT = re.compile(r"(\w[\w\[\]'\"]*)\s*\+=\s*(\w[\w\[\]'\"]*)")


def run_erc(board: Board, project_id: str = "demo", skidl_source: Optional[str] = None,
            design_version: int = 1) -> List[Violation]:
    """Exécute l'ERC complet : règles électriques sur Board + analyse SKiDL.

    Chaque erreur est journalisée et émise comme événement ERC_ERROR — le
    corrector_agent en dépend pour sa boucle de correction.
    """
    errors: List[Violation] = []
    errors += _check_floating_nets(board)
    errors += _check_multiple_drivers(board)
    errors += _check_i2c_pullups(board)
    errors += _check_supply_decoupling(board)
    if skidl_source:
        errors += _check_skidl_static(skidl_source)

    for err in errors:
        logger.info("erreur ERC détectée", extra={"rule": err.rule_id, "net": err.net})
        make_event(EventType.ERC_ERROR, project_id, "erc_executor", design_version,
                   rule_id=err.rule_id, net=err.net, message=err.message)
    return errors


def _check_floating_nets(board: Board) -> List[Violation]:
    """Nets avec moins de 2 connexions — fils suspendus."""
    out: List[Violation] = []
    for name, net in board.nets.items():
        if len(net.connections) < 2:
            out.append(Violation(
                rule_id="ERC-floating-net", severity="error", net=name,
                message=f"net {name} flottant ({len(net.connections)} connexion(s))",
            ))
    return out


def _check_multiple_drivers(board: Board) -> List[Violation]:
    """Deux sorties actives sur un même net — court-circuit potentiel.

    Heuristique : un composant dont la référence commence par U et un pad
    nommé OUT/TX/SW est considéré comme driver ; deux drivers sur un net =
    erreur (hors bus explicitement classés CAN/LIN).
    """
    out: List[Violation] = []
    for name, net in board.nets.items():
        if any(k in (net.net_class or "").upper() for k in ("BUS", "CAN", "LIN", "RS485")):
            continue
        drivers = [f"{ref}/{pad}" for ref, pad in net.connections
                   if ref.startswith("U") and any(
                       tag in pad.upper() for tag in ("OUT", "TX", "SW", "DRV"))]
        if len(drivers) > 1:
            out.append(Violation(
                rule_id="ERC-multi-driver", severity="error", net=name,
                message=f"{len(drivers)} sorties actives sur le net {name} : {', '.join(drivers)}",
            ))
    return out


def _check_i2c_pullups(board: Board) -> List[Violation]:
    """Bus I2C sans résistance de pull-up sur SDA/SCL."""
    out: List[Violation] = []
    for name, net in board.nets.items():
        if (net.net_class or "").upper() != "I2C" and not _looks_like_i2c(name):
            continue
        has_pullup = any(ref.startswith("R") for ref, _ in net.connections)
        if not has_pullup:
            out.append(Violation(
                rule_id="ERC-i2c-pullup", severity="error", net=name,
                message=f"bus I2C {name} sans résistance de pull-up (motif connu du graphe Neo4j)",
            ))
    return out


def _looks_like_i2c(net_name: str) -> bool:
    upper = net_name.upper()
    return any(tag in upper for tag in ("SDA", "SCL", "I2C"))


def _check_supply_decoupling(board: Board) -> List[Violation]:
    """Net d'alimentation sans condensateur de découplage associé."""
    out: List[Violation] = []
    for name, net in board.nets.items():
        if not _is_power_net(net):
            continue
        has_cap = any(ref.startswith(("C",)) for ref, _ in net.connections)
        if not has_cap:
            out.append(Violation(
                rule_id="ERC-decoupling", severity="error", net=name,
                message=f"rail {name} sans condensateur de découplage",
            ))
    return out


def _is_power_net(net: Net) -> bool:
    upper = net.name.upper()
    return (upper in {"VCC", "VDD", "VBAT", "3V3", "5V", "VIN"} or
            upper.startswith(("VDD", "VCC", "+3V3", "+5V", "PWR")) or
            (net.net_class or "").upper() == "POWER")


# ------------------------------------------------------------- SKiDL statique
def _check_skidl_static(source: str) -> List[Violation]:
    """Analyse statique du script SKiDL généré (regex — sans exécution)."""
    out: List[Violation] = []
    nets = set(_RE_NET.findall(source))
    parts = _RE_PART.findall(source)

    if not parts:
        out.append(Violation(
            rule_id="ERC-skidl-empty", severity="error",
            message="script SKiDL sans aucun Part() — génération défaillante",
        ))
    if "from skidl" not in source:
        out.append(Violation(
            rule_id="ERC-skidl-import", severity="error",
            message="script SKiDL sans import skidl — non exécutable",
        ))

    # pull-up I2C : présence de SDA/SCL sans résistance déclarée proche
    i2c_nets = [n for n in nets if any(t in n.upper() for t in ("SDA", "SCL", "I2C"))]
    resistors = [p for p in parts if "resistor" in p.lower() or "R" in p]
    if i2c_nets and not resistors:
        out.append(Violation(
            rule_id="ERC-skidl-i2c-pullup", severity="error",
            message=f"nets I2C {i2c_nets} sans Part résistance dans le script SKiDL",
        ))
    return out


def erc_summary(errors: List[Violation]) -> Tuple[int, int, Dict[str, int]]:
    """(erreurs, avertissements, comptage par règle) — pour les rapports."""
    counts: Dict[str, int] = {}
    for e in errors:
        counts[e.rule_id] = counts.get(e.rule_id, 0) + 1
    err = sum(1 for e in errors if e.severity == "error")
    warn = sum(1 for e in errors if e.severity == "warning")
    return err, warn, counts

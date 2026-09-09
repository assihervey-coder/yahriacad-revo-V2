"""Validateur de motifs SKiDL — détecte les écarts avant tout engagement coûteux.

Parse le script SKiDL généré par l'étape 1 (Part / Net / ``+=``) par regex
tolérantes, puis le confronte à deux sources : les règles électroniques
canoniques (découplage régulateur, pull-up I2C, découplage alim, masse
présente) et les motifs appris du graphe de connaissances (accumulés design
après design via :meth:`PatternValidator.learn_validated`). Chaque écart est
remonté au corrector_agent avec le code du motif et une piste de correction.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .neo4j_client import Neo4jClient

# Regex d'extraction SKiDL (tolérantes : ref='X', var = Part(...), var = Net(...))
_RE_PART_REF = re.compile(r"ref\s*=\s*['\"]([A-Za-z]\w*)['\"]")
_RE_PART_VAR = re.compile(r"(\w+)\s*=\s*Part\s*\(")
_RE_NET_NAME = re.compile(r"Net\s*\(\s*['\"]([\w\-/+\.]+)['\"]")
_RE_NET_VAR = re.compile(r"(\w+)\s*=\s*Net\s*\(")
_RE_CONNECT = re.compile(r"(\w+)(?:\[[^\]]+\])?\s*\+=\s*(\w+)(?:\[[^\]]+\])?")

_POWER_NET_RE = re.compile(r"(?i)(vcc|vdd|3v3|5v|1v8|2v5|vbat|vin|vout|vrail)")
_REGULATOR_RE = re.compile(r"(?i)(ldo|regulator|ams1117|lm317|ap2112|xc6206|lp38)")
_PULLUP_NET_RE = re.compile(r"(?i)(^|_)(sda|scl)($|_)")
_GND_RE = re.compile(r"(?i)^(gnd|vss|agnd|dgnd)")


@dataclass
class PatternRule:
    """Règle canonique : déclencheur (part/net) + exigence + piste de correction."""

    name: str
    code: str                      # ex. missing_decoupling, missing_pullup
    description: str
    hint: str
    trigger_part: Optional[re.Pattern] = None
    trigger_net: Optional[re.Pattern] = None
    require_part: Optional[re.Pattern] = None
    require_net: Optional[re.Pattern] = None

    def is_triggered(self, parts_lower: set, nets_lower: set) -> bool:
        """Règle toujours active si aucun déclencheur n'est défini."""
        if self.trigger_part is None and self.trigger_net is None:
            return True
        hit_part = self.trigger_part is not None and any(self.trigger_part.search(p) for p in parts_lower)
        hit_net = self.trigger_net is not None and any(self.trigger_net.search(n) for n in nets_lower)
        return hit_part or hit_net


KNOWN_RULES: Tuple[PatternRule, ...] = (
    PatternRule(
        "regulator_decoupling", "missing_decoupling",
        "Un régulateur exige un condensateur de découplage",
        "Ajouter C déc. (100 nF + 10 µF) sur la sortie du régulateur",
        trigger_part=_REGULATOR_RE, require_part=re.compile(r"(?i)^c\d")),
    PatternRule(
        "i2c_pullup", "missing_pullup",
        "Un bus I2C exige des pull-up sur SDA/SCL",
        "Ajouter R pull-up 4.7 kΩ sur SDA et SCL",
        trigger_net=_PULLUP_NET_RE, require_part=re.compile(r"(?i)^r\d")),
    PatternRule(
        "power_decoupling", "missing_decoupling",
        "Chaque rail d'alim exige un condensateur de découplage",
        "Ajouter un C de découplage près de chaque charge du rail",
        trigger_net=_POWER_NET_RE, require_part=re.compile(r"(?i)^c\d")),
    PatternRule(
        "ground_net", "missing_gnd",
        "Le design doit définir une masse",
        "Créer le net GND et y rattacher les broches de masse",
        require_net=_GND_RE),
)


@dataclass
class Violation:
    """Écart détecté — consommé par le corrector_agent (code + motif + piste)."""

    code: str
    detail: str
    pattern: str            # nom de la règle ou du motif appris
    hint: str

    def to_json(self) -> Dict[str, str]:
        return {"code": self.code, "detail": self.detail,
                "pattern": self.pattern, "hint": self.hint}


@dataclass
class SkidlSummary:
    """Vue plate du script : refs de parts, noms de nets, liens part↔net."""

    parts: List[str] = field(default_factory=list)
    nets: List[str] = field(default_factory=list)
    links: List[Tuple[str, str]] = field(default_factory=list)   # (net, part)


def parse_skidl(script: str) -> SkidlSummary:
    """Extraction robuste des Part/Net/connexions d'un script SKiDL."""
    summary = SkidlSummary()
    summary.parts = sorted(set(_RE_PART_REF.findall(script))
                           | {v for v in _RE_PART_VAR.findall(script)
                              if v.lower() not in {"net", "part"}})
    summary.nets = sorted(set(_RE_NET_NAME.findall(script))
                          | {v for v in _RE_NET_VAR.findall(script)})
    known_nets = {n.lower() for n in summary.nets}
    for left, right in _RE_CONNECT.findall(script):
        pair = (left, right)
        lo, ro = left.lower(), right.lower()
        if ro in known_nets and lo not in known_nets:
            pair = (right, left)          # normalise (net, part)
        elif lo not in known_nets and ro not in known_nets:
            continue                      # ligne non résolue — ignorée
        if pair[0].lower() in known_nets and pair[1].lower() not in known_nets:
            summary.links.append(pair)
    return summary


class PatternValidator:
    """Confronte un script SKiDL aux règles canoniques et aux motifs appris."""

    def __init__(self, kg_client: Optional[Neo4jClient] = None) -> None:
        self.kg = kg_client or Neo4jClient()

    def validate(self, script: str) -> List[Violation]:
        """Liste des écarts (vide = script conforme aux règles canoniques)."""
        summary = parse_skidl(script)
        parts_lower = {p.lower() for p in summary.parts}
        nets_lower = {n.lower() for n in summary.nets}
        violations: List[Violation] = []
        for rule in KNOWN_RULES:
            if not rule.is_triggered(parts_lower, nets_lower):
                continue
            missing_part = rule.require_part is not None and not any(
                rule.require_part.search(p) for p in parts_lower)
            missing_net = rule.require_net is not None and not any(
                rule.require_net.search(n) for n in nets_lower)
            if missing_part or missing_net:
                what = "partie requise absente" if missing_part else "net requis absent"
                violations.append(Violation(
                    code=rule.code, detail=f"{rule.description} — {what}",
                    pattern=rule.name, hint=rule.hint))
        return violations

    def learn_validated(self, script: str) -> bool:
        """Si le script est conforme, stocke son motif dans le graphe ( apprentissage)."""
        if self.validate(script):
            return False
        summary = parse_skidl(script)
        pattern = {
            "tags": ["skidl", "validated", "design_pattern"],
            "parts": summary.parts, "nets": summary.nets, "links": summary.links,
            "learned_at": time.time(),
        }
        self.kg.store_pattern(pattern)
        return True

    def validate_against_learned(self, script: str) -> List[Violation]:
        """Compare aux motifs appris : signale un design qui s'écarte du corpus."""
        summary = parse_skidl(script)
        nets_lower = {n.lower() for n in summary.nets}
        patterns = self.kg.find_patterns(["validated", "skidl"])
        if not patterns:
            return []
        best, best_ratio = None, -1.0
        for pattern in patterns:
            known = {str(n).lower() for n in pattern.get("nets", [])}
            if not known:
                continue
            ratio = len(known & nets_lower) / len(known)
            if ratio > best_ratio:
                best, best_ratio = pattern, ratio
        if best is not None and best_ratio < 0.4:
            return [Violation(
                code="deviates_from_learned",
                detail=(f"Écart avec le motif validé « {best.get('id', '?')} » : "
                        f"{best_ratio:.0%} de nets communs seulement"),
                pattern=str(best.get("id", "learned")),
                hint="Revoir le plan multi-agents ou justifier l'écart d'architecture")]
        return []

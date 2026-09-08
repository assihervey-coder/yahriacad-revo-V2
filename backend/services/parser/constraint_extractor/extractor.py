"""Extraction des contraintes techniques — textes et netlists (section 6.1).

Détecte les classes de signaux (catalogue signal_classes), les zones
interdites (« keepout près de l'antenne »), le budget thermique (« max 80°C »),
les longueurs appariées (« DDR appariées ±5mm ») et les budgets de courant,
puis produit des ConstraintMessage prêtes à publier sur le constraint_bus.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from typing import List

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.bus import ConstraintBus, ConstraintKind, ConstraintMessage  # noqa: E402
from common.log import get_logger  # noqa: E402

from constraint_extractor.signal_classes import SignalClassSpec, match_signal_classes  # noqa: E402

logger = get_logger("parser.constraint_extractor")

_SOURCE = "constraint_extractor"

# Fenêtres de recherche pour rattacher une règle de longueur à une classe de nets
_CLASS_HINTS = ("ddr", "usb", "mipi", "dsi", "csi", "rmii", "eth", "can", "spi", "i2c", "data", "clock")


def _deaccent(text: str) -> str:
    """Minuscules sans accents + espaces normalisés pour les regex de détection."""
    flat = "".join(char for char in unicodedata.normalize("NFD", (text or "").lower())
                   if unicodedata.category(char) != "Mn")
    return " ".join(flat.split())


def _msg(kind: ConstraintKind, key: str, value: dict, project_id: str = "") -> ConstraintMessage:
    return ConstraintMessage(kind=kind, key=key, value=value, project_id=project_id, source=_SOURCE)


def _infer_group(lowered: str, match_start: int) -> str:
    """Rattache une règle de longueur appariée à une classe (fenêtre avant le match)."""
    window = lowered[max(0, match_start - 80):match_start]
    for hint in _CLASS_HINTS:
        if hint in window:
            return hint.upper()
    return "default"


def extract_constraints(spec_text: str, project_id: str = "") -> List[ConstraintMessage]:
    """Extrait les contraintes d'un cahier des charges NL ou d'une netlist."""
    lowered = _deaccent(spec_text)
    messages: List[ConstraintMessage] = []

    # 1) classes de signaux → impédance cible + skew + largeur minimale
    specs: List[SignalClassSpec] = match_signal_classes(lowered)
    for spec in specs:
        messages.append(_msg(
            ConstraintKind.IMPEDANCE_TARGET, f"impedance/{spec.net_class}",
            {
                "impedance_target_ohm": spec.impedance_target_ohm,
                "differential": spec.differential,
                "skew_tolerance_mm": spec.skew_tolerance_mm,
                "min_trace_width_mm": spec.min_trace_width_mm,
                "class_name": spec.name,
            },
            project_id,
        ))

    # 2) longueurs appariées : « appariées ±5mm » / « matched within 5 mm »
    for match in re.finditer(
            r"(?:appari\w*|matched(?:\s+within)?|length\s+match\w*)[^.0-9]{0,30}?±?\s*"
            r"(\d+(?:[.,]\d+)?)\s*mm", lowered):
        tolerance = float(match.group(1).replace(",", "."))
        group = _infer_group(lowered, match.start())
        messages.append(_msg(ConstraintKind.LENGTH_MATCH_RULE, f"length_match/{group}",
                             {"tolerance_mm": tolerance, "group": group}, project_id))

    # 3) zones interdites : « keepout de 3 mm près de l'antenne »
    for match in re.finditer(r"keepout[^.;]{0,90}", lowered):
        span = match.group(0)
        reason = next((word for word in ("antenne", "antenna", "rf", "cara", "thermique") if word in span),
                      "zone")
        radius = re.search(r"(\d+(?:[.,]\d+)?)\s*mm", span)
        clearance = float(radius.group(1).replace(",", ".")) if radius else 5.0
        messages.append(_msg(ConstraintKind.KEEPOUT_ZONE, f"keepout/{reason}",
                             {"reason": reason, "clearance_mm": clearance, "hint": span.strip()},
                             project_id))

    # 4) budget thermique : « température max 80°C » / « max 85 »
    for match in re.finditer(
            r"(?:max(?:imum)?|limite)[^.0-9]{0,25}?(\d+(?:[.,]\d+)?)\s*(?:degres\s*)?(?:°\s*)?c\b"
            r"|temperature[^.0-9]{0,20}?(\d+(?:[.,]\d+)?)", lowered):
        raw = match.group(1) or match.group(2)
        if raw:
            messages.append(_msg(ConstraintKind.THERMAL_ZONE_UPDATE, "thermal/global",
                                 {"max_temp_c": float(raw.replace(",", "."))}, project_id))

    # 5) budgets de courant : « jusqu'à 3A », « 2 A max »
    for match in re.finditer(r"(\d+(?:[.,]\d+)?)\s*a\b(?![a-z])", lowered):
        amps = float(match.group(1).replace(",", "."))
        if 0.05 <= amps <= 50.0:
            key = f"current/rail_{len(messages)}"
            messages.append(_msg(ConstraintKind.CURRENT_BUDGET, key,
                                 {"max_current_a": amps}, project_id))

    # 6) clearances explicites : « isolement de 0,3 mm »
    for match in re.finditer(r"(?:clearance|isolement|distance)[^.0-9]{0,25}?(\d+(?:[.,]\d+)?)\s*mm",
                             lowered):
        value = float(match.group(1).replace(",", "."))
        if 0.05 <= value <= 5.0:
            messages.append(_msg(ConstraintKind.CLEARANCE, "clearance/explicit",
                                 {"min_clearance_mm": value}, project_id))

    logger.info("contraintes extraites", extra={"count": len(messages),
                                                "classes": [s.net_class for s in specs]})
    return messages


def publish(bus: ConstraintBus, messages: List[ConstraintMessage]) -> int:
    """Publie les contraintes sur le bus — retourne le nombre de messages publiés."""
    published = 0
    for message in messages:
        bus.publish(message)
        published += 1
    return published

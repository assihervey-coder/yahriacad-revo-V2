"""Générateur d'en-tête C style Arduino — #define simples + enum des fonctions."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

_ROOT = Path(__file__).resolve().parents[4]           # racine du dépôt (common/)
_SERVICE_DIR = Path(__file__).resolve().parents[1]    # firmware_bridge/ (paquets frères)
for _path in (str(_ROOT), str(_SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from pin_exporter.exporter import PinAssignment


def generate_arduino_header(assignments: List[PinAssignment]) -> Tuple[str, str]:
    """pin_map_arduino.h : #define PIN_* + enum des fonctions logicielles."""
    guard = "PCB_AI_DESIGNER_PIN_MAP_ARDUINO_H"
    lines: List[str] = [
        "/*",
        " * pin_map_arduino.h — généré par pcb_ai_designer_v2/firmware_bridge",
        " * Synchronisé automatiquement avec le design (HW/SW — brique Flux.ai).",
        " */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
    ]

    # enum des fonctions rencontrées (hors alimentation)
    functions = sorted({a.function for a in assignments
                        if a.function not in {"GND", "POWER_IN"}})
    lines.append("typedef enum {")
    for i, function in enumerate(functions):
        comma = "," if i < len(functions) - 1 else ""
        lines.append(f"    PINFUNC_{function}{comma}")
    lines.append("} PinFunction;")
    lines.append("")

    for a in assignments:
        if a.function in {"GND", "POWER_IN"}:
            continue
        symbol = _symbol(a)
        pin_number = _arduino_pin(a.pin)
        lines.append(f"#define PIN_{symbol:<26s} {pin_number:<3d}"
                     f"  // net: {a.net or 'n/c'} — {a.function}")

    lines += [
        "",
        "// Table récapitulative pour les initialisations",
        "struct PinEntry { const char* net; uint8_t pin; PinFunction func; };",
        "static const PinEntry kPinMap[] PROGMEM = {",
    ]
    for a in assignments:
        if a.function in {"GND", "POWER_IN"}:
            continue
        lines.append(f'    {{"{a.net}", {_arduino_pin(a.pin)}, PINFUNC_{a.function}}},')
    lines += ["};", "", f"#endif /* {guard} */", ""]
    return "pin_map_arduino.h", "\n".join(lines)


def _symbol(a: PinAssignment) -> str:
    base = (a.net or f"PIN_{a.pin}").upper()
    cleaned = "".join(c if c.isalnum() else "_" for c in base)
    return "_".join(part for part in cleaned.split("_") if part)


def _arduino_pin(pin: str) -> int:
    """Numéro de broche Arduino déduit de l'étiquette — D2..D13 sinon 0."""
    digits = "".join(c for c in pin if c.isdigit())
    index = int(digits) if digits else 0
    return max(2, min(13, index % 14))

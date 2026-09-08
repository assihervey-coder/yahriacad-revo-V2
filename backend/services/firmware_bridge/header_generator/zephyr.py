"""Générateur d'en-tête C style Zephyr — pin mapping avec devicetree commenté."""

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


def generate_zephyr_header(assignments: List[PinAssignment]) -> Tuple[str, str]:
    """pin_map_zephyr.h : #define PIN_MAP_* + bloc devicetree commenté.

    Le contenu suit les conventions Zephyr (DT_NAMED_GPIO, devicetree overlay)
    tout en restant un en-tête C simple compilable partout.
    """
    guard = "PCB_AI_DESIGNER_PIN_MAP_ZEPHYR_H"
    lines: List[str] = [
        "/*",
        " * pin_map_zephyr.h — généré par pcb_ai_designer_v2/firmware_bridge",
        " * Ne pas éditer à la main : toute modification de brochage dans le design",
        " * déclenche une régénération automatique (événement FIRMWARE_REGENERATED).",
        " */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <zephyr/kernel.h>",
        "#include <zephyr/drivers/gpio.h>",
        "",
    ]

    seen: set = set()
    for a in assignments:
        symbol = _symbol(a)
        if symbol in seen:
            continue
        seen.add(symbol)
        lines.append(f"#define PIN_MAP_{symbol:<28s} {a.pin:<8s}"
                     f" /* net: {a.net or 'n/c'} */")

    # bloc devicetree overlay commenté — collage direct dans une board overlay
    lines += [
        "",
        "/*",
        " * Overlay devicetree correspondant (à coller dans app.overlay) :",
        " *",
        " * / {",
    ]
    seen.clear()
    for a in assignments:
        symbol = _symbol(a)
        if symbol in seen or a.function in {"GND", "POWER_IN"}:
            continue
        seen.add(symbol)
        lines.append(f" *   zephyr_user: {symbol.lower()} {{")
        lines.append(f" *     gpios = <&gpio0 {_gpio_index(a.pin)} GPIO_ACTIVE_HIGH>;"
                     f" /* {a.net or 'n/c'} */")
        lines.append(" *   }")
    lines += [" * };", " */", "", f"#endif /* {guard} */", ""]
    return "pin_map_zephyr.h", "\n".join(lines)


def _symbol(a: PinAssignment) -> str:
    """Symbole C : NET normalisé — UART_TX_1, SPI_MOSI, GPIO_PA5..."""
    base = (a.net or f"PIN_{a.pin}").upper()
    cleaned = "".join(c if c.isalnum() else "_" for c in base)
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned


def _gpio_index(pin: str) -> int:
    """Index GPIO déduit de l'étiquette PA{N} — 0 si non déductible."""
    digits = "".join(c for c in pin if c.isdigit())
    return int(digits) % 32 if digits else 0

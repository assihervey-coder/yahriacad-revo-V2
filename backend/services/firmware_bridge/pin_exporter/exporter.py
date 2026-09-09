"""Extraction des assignations de broches réelles du design (section 6.5).

Pour chaque composant, chaque pastille devient une PinAssignment : (pin,
fonction déduite du nom de net / classe de signaux, net). La déduction de
fonction suit les conventions d'attributions STM32/ESP32 : PA9 → UART1_TX quand
le net s'appelle UART_TX, GPIO pur sinon.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Board  # noqa: E402
from common.log import get_logger  # noqa: E402

logger = get_logger("firmware.pin_exporter")


@dataclass
class PinAssignment:
    """Une broche réelle : composant, pin, fonction logicielle, net."""

    component_ref: str
    pin: str                     # ex. "PA9" — broche physique
    function: str                # "GPIO_OUT", "UART1_TX", "I2C1_SDA"...
    net: str

    def to_dict(self) -> dict:
        return {"component_ref": self.component_ref, "pin": self.pin,
                "function": self.function, "net": self.net}


# conventions de détection de fonction par nom de net / classe
_FUNCTIONS = [
    (re.compile(r"UART\d*_?TX|USART\d*_?TX", re.I), "UART_TX"),
    (re.compile(r"UART\d*_?RX|USART\d*_?RX", re.I), "UART_RX"),
    (re.compile(r"SDA", re.I), "I2C_SDA"),
    (re.compile(r"SCL", re.I), "I2C_SCL"),
    (re.compile(r"SPI\d*_?SCK|SCLK", re.I), "SPI_SCK"),
    (re.compile(r"SPI\d*_?MOSI|MOSI", re.I), "SPI_MOSI"),
    (re.compile(r"SPI\d*_?MISO|MISO", re.I), "SPI_MISO"),
    (re.compile(r"SPI\d*_?CS|NSS|CS_N", re.I), "SPI_CS"),
    (re.compile(r"USB\D*DP|USB\D*D\+|DP", re.I), "USB_DP"),
    (re.compile(r"USB\D*DM|USB\D*D-|DM", re.I), "USB_DM"),
    (re.compile(r"CAN_?TX|CANTX", re.I), "CAN_TX"),
    (re.compile(r"CAN_?RX|CANRX", re.I), "CAN_RX"),
    (re.compile(r"SWDIO", re.I), "SWDIO"),
    (re.compile(r"SWCLK", re.I), "SWCLK"),
    (re.compile(r"LED", re.I), "GPIO_OUT"),
    (re.compile(r"BTN|BUTTON|SW[0-9]", re.I), "GPIO_IN"),
    (re.compile(r"MOTOR|PWM", re.I), "PWM_OUT"),
    (re.compile(r"ANT|RF", re.I), "RF_ANTENNA"),
    (re.compile(r"GND", re.I), "GND"),
    (re.compile(r"VCC|VDD|VBAT|3V3|5V", re.I), "POWER_IN"),
]


def _deduce_function(net_name: str, net_class: str, index: int) -> str:
    """Fonction logicielle déduite : nom de net > classe de signaux > GPIO."""
    for pattern, function in _FUNCTIONS:
        if pattern.search(net_name or ""):
            return f"{function}" if index < 1 else f"{function}_{index + 1}"
    if (net_class or "").upper() in {"USB", "DDR", "MIPI", "ETHERNET"}:
        return f"{net_class.upper()}_{index + 1}"
    return "GPIO"


def export_pins(board: Board, target: str = "raw") -> List[PinAssignment]:
    """Exporte toutes les assignations de broches du design.

    target : "raw" (liste brute), "zephyr" ou "arduino" (filtres futurs — la
    génération des en-têtes est dans header_generator).
    """
    assignments: List[PinAssignment] = []
    for ref in sorted(board.components):
        comp = board.components[ref]
        if not comp.pads:
            continue
        net_of_pad = {p.name: (p.net or "") for p in comp.pads}
        # complète avec les connexions déclarées dans les nets
        for net_name, net in board.nets.items():
            for conn_ref, pad_name in net.connections:
                if conn_ref == ref and not net_of_pad.get(pad_name):
                    net_of_pad[pad_name] = net_name
        for index, pad in enumerate(comp.pads):
            net_name = pad.net or net_of_pad.get(pad.name, "")
            function = _deduce_function(net_name, _class_of(board, net_name), index)
            pin_label = _physical_pin(comp.ref, pad.name, index)
            assignments.append(PinAssignment(component_ref=ref, pin=pin_label,
                                             function=function, net=net_name))
    logger.info("broches exportées", extra={"components": len(board.components),
                                            "pins": len(assignments), "target": target})
    return assignments


def _class_of(board: Board, net_name: str) -> str:
    net = board.nets.get(net_name)
    return net.net_class if net else ""


def _physical_pin(ref: str, pad_name: str, index: int) -> str:
    """Étiquette physique stylisée MCU : U1/PA{index+1}, R1/1."""
    if ref.startswith(("U", "IC")):
        return f"PA{index + 1}"
    return pad_name

"""Catalogue des classes de signaux — référentiel d'intégrité du signal (section 6.1).

Impédances différentielles cibles, tolérances de skew et largeurs minimales
pour les bus courants (DDR3, USB2/3, MIPI DSI/CSI, Ethernet RMII, SPI, I2C,
CAN). Le constraint_extractor s'appuie sur ce catalogue pour publier les
contraintes ; le router consomme largeurs et couches, le drc_dfm_engine les
clearances. Une impédance de 0,0 ohm signifie « non contrôlé ».
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class SignalClassSpec:
    """Spécification d'une classe de signaux (impédance, skew, largeur min)."""

    name: str
    net_class: str
    differential: bool
    impedance_target_ohm: float   # mode différentiel si differential, sinon single-ended
    skew_tolerance_mm: float
    min_trace_width_mm: float
    keywords: Tuple[str, ...]


SIGNAL_CLASSES: Dict[str, SignalClassSpec] = {
    spec.net_class: spec for spec in (
        SignalClassSpec(
            "DDR3 données/adresses (fly-by, 40 Ω single-ended)",
            "DDR", False, 40.0, 2.5, 0.15,
            ("ddr", "ddr3", "ddr4", "sdram", "dqs", "memoire"),
        ),
        SignalClassSpec(
            "USB 2.0 différentiel (90 Ω)",
            "USB2", True, 90.0, 1.5, 0.20,
            ("usb2", "usb 2", "usb_dp", "usb_dm", "d+", "usb"),
        ),
        SignalClassSpec(
            "USB 3.x SuperSpeed différentiel (85 Ω)",
            "USB3", True, 85.0, 0.5, 0.15,
            ("usb3", "usb 3", "superspeed", "ssrx", "sstx"),
        ),
        SignalClassSpec(
            "MIPI DSI display (100 Ω diff)",
            "MIPI_DSI", True, 100.0, 0.5, 0.13,
            ("mipi", "dsi", "dphy", "display_serial"),
        ),
        SignalClassSpec(
            "MIPI CSI camera (100 Ω diff)",
            "MIPI_CSI", True, 100.0, 0.5, 0.13,
            ("csi", "camera", "cam_"),
        ),
        SignalClassSpec(
            "Ethernet RMII (50 Ω, groupe apparié)",
            "ETH_RMII", False, 50.0, 5.0, 0.20,
            ("rmii", "rgmii", "ethernet", "eth", "mdio", "phy"),
        ),
        SignalClassSpec(
            "SPI (non contrôlé, largeur standard)",
            "SPI", False, 0.0, 10.0, 0.20,
            ("spi", "mosi", "miso", "sclk", "qspi"),
        ),
        SignalClassSpec(
            "I2C (non contrôlé, pull-up requis)",
            "I2C", False, 0.0, 20.0, 0.20,
            ("i2c", "sda", "scl", "twi"),
        ),
        SignalClassSpec(
            "CAN bus différentiel (120 Ω)",
            "CAN", True, 120.0, 5.0, 0.20,
            ("can_h", "can_l", "canh", "canl", "twai"),
        ),
    )
}

# Ordre de spécificité : les classes spécifiques priment sur les génériques
_SPECIFICITY = ["USB3", "USB2", "MIPI_CSI", "MIPI_DSI", "DDR", "ETH_RMII", "CAN", "SPI", "I2C"]


def get_signal_class(net_class: str) -> SignalClassSpec:
    """Accès typé au catalogue — KeyError explicite si la classe est inconnue."""
    if net_class not in SIGNAL_CLASSES:
        raise KeyError(f"classe de signaux inconnue : {net_class} (connues : {sorted(SIGNAL_CLASSES)})")
    return SIGNAL_CLASSES[net_class]


def match_signal_classes(text: str) -> List[SignalClassSpec]:
    """Détecte les classes de signaux évoquées dans un texte (accents ignorés).

    Les mots-clés sont matchés en préfixe de mot (regex) pour éviter les faux
    positifs ; les classes génériques masquées par une classe plus spécifique
    (USB2 derrière USB3) sont retirées.
    """
    import re

    lowered = " ".join((text or "").lower().split())
    matched: Dict[str, SignalClassSpec] = {}
    for spec in SIGNAL_CLASSES.values():
        for keyword in spec.keywords:
            pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])" \
                if len(keyword) <= 3 else re.escape(keyword)
            if re.search(pattern, lowered):
                matched[spec.net_class] = spec
                break
    # USB2 masqué par USB3 si le texte ne parle explicitement que d'USB3
    if "USB3" in matched and "USB2" in matched and "usb2" not in lowered:
        matched.pop("USB2")
    return [matched[key] for key in _SPECIFICITY if key in matched]

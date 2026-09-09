"""Ouverture d'œil des buses rapides — heuristique calibrée sur la perte d'insertion.

Modèle (par net rapide) :
  - perte d'insertion diélectrique : IL ≈ 0.5 dB/in/GHz × longueur(in) × f(GHz) ;
  - pénalité par via : 0.6·√f dB (discontinuité d'impédance) ;
  - fermeture d'œil verticale ≈ amplitude résiduelle 10^(−IL_tot/20), amputée
    d'un facteur ISI dépendant de la longueur, puis de la diaphonie fournie ;
  - dépassement (overshoot) : réflexions ∝ nombre de vias + longueur.

Nets considérés « rapides » : classe connue du tableau ci-dessous ou cible
d'impédance déclarée. Les nets à risque sont listés (risk = ok | watch | fail).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from common.log import get_logger

logger = get_logger("simulator.si")

MM_PER_INCH = 25.4
LOSS_DB_PER_IN_GHZ = 0.5     # perte diélectrique typique FR4
VIA_LOSS_DB = 0.6            # pénalité de réflexion par via, à 1 GHz (× √f)
FAIL_EYE_PCT = 35.0          # en dessous : bus en échec
WATCH_EYE_PCT = 45.0         # en dessous : à surveiller
OVERSHOOT_LIMIT_MV = 400.0   # consigne typique d'overshoot (mV)

# fréquence de travail (GHz) par classe de net — heuristique
CLASS_FREQ_GHZ: Dict[str, float] = {
    "usb3": 5.0, "pcie": 8.0, "ddr4": 1.6, "mipi": 1.5,
    "usb2": 0.48, "ethernet": 0.125, "lvds": 1.25, "diff": 2.5,
    "high_speed": 2.0, "spi": 0.05, "uart": 0.001,
}
DEFAULT_FREQ_GHZ = 1.0


@dataclass
class NetMargin:
    """Marge SI d'un net rapide (une ligne du rapport)."""

    net: str
    net_class: str
    length_mm: float
    f_ghz: float
    vias: int
    insertion_loss_db: float
    eye_opening_pct: float
    overshoot_mv: float
    risk: str  # ok | watch | fail

    def to_dict(self) -> dict:
        return {
            "net": self.net, "net_class": self.net_class,
            "length_mm": round(self.length_mm, 1),
            "f_ghz": self.f_ghz, "vias": self.vias,
            "insertion_loss_db": round(self.insertion_loss_db, 2),
            "eye_opening_pct": round(self.eye_opening_pct, 1),
            "overshoot_mv": round(self.overshoot_mv, 1),
            "risk": self.risk,
        }


@dataclass
class SIResult:
    """Résultat intégrité du signal (contrat proto SignalIntegrityResult)."""

    eye_opening_min_pct: float = 100.0
    overshoot_max_mv: float = 0.0
    failing_nets: List[str] = field(default_factory=list)
    margins: List[NetMargin] = field(default_factory=list)
    analyzed_nets: int = 0
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "eye_opening_min_pct": round(self.eye_opening_min_pct, 1),
            "overshoot_max_mv": round(self.overshoot_max_mv, 1),
            "failing_nets": list(self.failing_nets),
            "margins": [m.to_dict() for m in self.margins],
            "analyzed_nets": self.analyzed_nets,
            "elapsed_s": round(self.elapsed_s, 4),
        }


def _net_frequency(net) -> Optional[float]:
    """Fréquence de travail du net (GHz) — None si le net n'est pas « rapide »."""
    cls = (net.net_class or "default").lower()
    if net.impedance_target_ohm is not None and cls not in CLASS_FREQ_GHZ:
        return DEFAULT_FREQ_GHZ
    return CLASS_FREQ_GHZ.get(cls)


def analyze_net(net, crosstalk_db: float = -40.0) -> Optional[NetMargin]:
    """Heuristique d'ouverture d'œil pour un net — None si le net n'est pas analysé."""
    f_ghz = _net_frequency(net)
    if f_ghz is None or not net.routed_segments:
        return None

    length_mm = net.routed_length_mm
    length_in = length_mm / MM_PER_INCH
    vias = net.via_count

    il_diell_db = LOSS_DB_PER_IN_GHZ * length_in * f_ghz
    il_via_db = VIA_LOSS_DB * math.sqrt(max(f_ghz, 1e-3)) * vias
    il_total = il_diell_db + il_via_db

    amplitude = 10.0 ** (-il_total / 20.0)                    # 0..1
    isi_factor = min(0.5, 0.02 * f_ghz * length_in)           # interférence inter-symboles
    xt_factor = min(0.25, 0.5 * (10.0 ** (crosstalk_db / 20.0)))
    eye_pct = max(0.0, 100.0 * amplitude * (1.0 - isi_factor) * (1.0 - xt_factor))

    # overshoot : réflexion des discontinuités (vias) + ringing proportionnel à la longueur
    overshoot_mv = min(OVERSHOOT_LIMIT_MV, 60.0 + 40.0 * vias + 8.0 * length_in * f_ghz)

    if eye_pct < FAIL_EYE_PCT:
        risk = "fail"
    elif eye_pct < WATCH_EYE_PCT:
        risk = "watch"
    else:
        risk = "ok"

    return NetMargin(
        net=net.name, net_class=net.net_class,
        length_mm=round(length_mm, 1), f_ghz=f_ghz, vias=vias,
        insertion_loss_db=round(il_total, 2),
        eye_opening_pct=round(eye_pct, 1),
        overshoot_mv=round(overshoot_mv, 1), risk=risk,
    )


def analyze(board, crosstalk_db: float = -40.0) -> SIResult:
    """Analyse SI complète de la carte → SIResult (nets à risque listés)."""
    t0 = time.perf_counter()
    margins: List[NetMargin] = []
    for net in board.nets.values():
        margin = analyze_net(net, crosstalk_db=crosstalk_db)
        if margin is not None:
            margins.append(margin)

    failing = [m.net for m in margins if m.risk == "fail"]
    eye_min = min((m.eye_opening_pct for m in margins), default=100.0)
    overshoot_max = max((m.overshoot_mv for m in margins), default=0.0)
    if failing:
        logger.warning(
            "nets SI en échec d'ouverture d'œil", extra={"nets": failing},
        )
    return SIResult(
        eye_opening_min_pct=round(eye_min, 1),
        overshoot_max_mv=round(overshoot_max, 1),
        failing_nets=failing,
        margins=sorted(margins, key=lambda m: m.eye_opening_pct),
        analyzed_nets=len(margins),
        elapsed_s=round(time.perf_counter() - t0, 4),
    )

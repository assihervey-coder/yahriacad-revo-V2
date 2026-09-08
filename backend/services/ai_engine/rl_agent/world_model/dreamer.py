"""World model « DreamerV3-like » en numpy pur — le simulateur interne du cerveau.

POURQUOI un world model : exécuter puis évaluer une action réelle (routage,
DRC, thermique) coûte des secondes ; le prédire coûte des microsecondes. En
apprivoisant une représentation prédictive de l'environnement, l'agent explore
~2 ordres de grandeur d'hypothèses en plus à budget constant — c'est le cœur
de l'approche Dreamer : « apprendre le monde, rêver dedans, agir au réveil ».

Implémentation : réseau minimal 2 couches fully-connected (activation tanh,
forward manuel SANS torch). La première couche est une projection aléatoire
fixe (random features — approxime un noyau non linéaire), la seconde est
ajustée par régression aux moindres carrés (ridge, forme fermée numpy) sur les
expériences observées. AVANT tout entraînement, ``predict_consequences``
retombe sur des heuristiques déterministes (densité, distances, puissance) —
le code fonctionne donc intégralement hors ligne.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from common.design_model import Placement

FEATURE_DIM = 12
_RIDGE_LAMBDA = 1e-3
_BUFFER_MAX = 2048


class TinyNet:
    """2 couches FC : tanh(X·W1 + b1)·W2 + b2 — W1 fixe, W2 appris par LS."""

    def __init__(self, feature_dim: int, hidden: int = 32, output_dim: int = 3,
                 seed: int = 42) -> None:
        rng = np.random.default_rng(seed)
        self.W1 = rng.normal(0.0, 0.5, size=(feature_dim, hidden))
        self.b1 = np.zeros(hidden)
        self.W2 = np.zeros((hidden, output_dim))
        self.b2 = np.zeros(output_dim)

    def hidden(self, X: np.ndarray) -> np.ndarray:
        """Activation de la couche cachée — tanh, forward manuel."""
        return np.tanh(X @ self.W1 + self.b1)

    def forward(self, X: np.ndarray) -> np.ndarray:
        """Sortie (n, 3) : [congestion, thermal_rise, si_risk] estimés."""
        return self.hidden(X) @ self.W2 + self.b2

    def fit_ridge(self, X: np.ndarray, Y: np.ndarray, lam: float = _RIDGE_LAMBDA) -> None:
        """Résout min ||H·W2 + b2 − Y||² + λ||W2||² en forme fermée."""
        H = self.hidden(X)
        Hb = np.hstack([H, np.ones((H.shape[0], 1))])
        reg = lam * np.eye(Hb.shape[1])
        solution, *_ = np.linalg.lstsq(Hb.T @ Hb + reg, Hb.T @ Y, rcond=None)
        self.W2 = solution[:-1, :]
        self.b2 = solution[-1, :]


class DreamerWorldModel:
    """Prédit {congestion_score, thermal_rise_estimate, si_risk} d'une action."""

    def __init__(self, feature_dim: int = FEATURE_DIM, hidden: int = 32) -> None:
        self.net = TinyNet(feature_dim, hidden)
        self.trained = False
        self._features: List[np.ndarray] = []
        self._targets: List[np.ndarray] = []

    # ---- features -------------------------------------------------------------
    def extract_features(self, board: Any, ref: str, x_mm: float, y_mm: float,
                         rotation_deg: float = 0.0, layer: int = 0) -> np.ndarray:
        """Vecteur de contexte : position, distances, densité, puissance locale."""
        comp = board.components[ref]
        x1, y1, x2, y2 = comp.bounding_box(
            Placement(ref=ref, x_mm=x_mm, y_mm=y_mm,
                      rotation_deg=rotation_deg, layer=layer))
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        min_dist, density, power_nearby = 1e9, 0.0, 0.0
        for other_ref, other_p in board.placements.items():
            if other_ref == ref or other_ref not in board.components:
                continue
            other = board.components[other_ref]
            ox1, oy1, ox2, oy2 = other.bounding_box(other_p)
            ocx, ocy = (ox1 + ox2) / 2.0, (oy1 + oy2) / 2.0
            gap = max(0.0, max(ox1 - x2, x1 - ox2, oy1 - y2, y1 - oy2))
            min_dist = min(min_dist, gap)
            if math.hypot(cx - ocx, cy - ocy) <= 10.0:
                density += 1.0
            power_nearby += other.power_w
        edge = min(cx, cy, board.width_mm - cx, board.height_mm - cy)
        return np.array([
            cx / board.width_mm, cy / board.height_mm, rotation_deg / 360.0,
            float(layer), min(min_dist / 20.0, 1.0), density / 8.0,
            min(power_nearby / 5.0, 1.0), min(comp.power_w / 2.0, 1.0),
            (x2 - x1) * (y2 - y1) / 100.0, edge / max(1.0, min(board.width_mm, board.height_mm) / 2.0),
            float(len(board.nets)) / 50.0,
            1.0 if any(not (x2 <= z.x_min_mm or z.x_max_mm <= x1 or y2 <= z.y_min_mm
                            or z.y_max_mm <= y1) for z in board.zones if z.kind == "keepout")
            else 0.0,
        ], dtype=np.float64)

    # ---- prédiction ------------------------------------------------------------
    def predict_consequences(self, board: Any, action: Any) -> Dict[str, float]:
        """Conséquences prédites d'une action — réseau si entraîné, sinon heuristique."""
        features = self.extract_features(board, action.ref, action.x_mm, action.y_mm,
                                         getattr(action, "rotation_deg", 0.0),
                                         getattr(action, "layer", 0))
        if self.trained:
            congestion, thermal, si = np.clip(self.net.forward(features[None, :])[0], 0.0, 1.0)
            return {"congestion_score": float(congestion),
                    "thermal_rise_estimate": float(thermal),
                    "si_risk": float(si)}
        return _heuristic_consequences(features)

    # ---- apprentissage ------------------------------------------------------------
    def observe(self, board: Any, action: Any, metrics: Dict[str, float],
                weight: float = 1.0) -> None:
        """Stocke une expérience (features, conséquences réelles) — buffer LRU."""
        features = self.extract_features(board, action.ref, action.x_mm, action.y_mm,
                                         getattr(action, "rotation_deg", 0.0),
                                         getattr(action, "layer", 0))
        target = np.array([metrics.get("congestion_score", 0.0),
                           metrics.get("thermal_rise_estimate", 0.0),
                           metrics.get("si_risk", 0.0)]) * weight
        self._features.append(features)
        self._targets.append(np.clip(target, 0.0, 2.0))
        if len(self._features) > _BUFFER_MAX:      # LRU : oublie les plus anciennes
            self._features.pop(0)
            self._targets.pop(0)

    def train(self, min_samples: int = 4) -> int:
        """Régression ridge sur le buffer ; retourne le nb d'échantillons utilisés."""
        if len(self._features) < max(4, min_samples):
            return 0
        X = np.vstack(self._features)
        Y = np.vstack(self._targets)
        self.net.fit_ridge(X, Y)
        self.trained = True
        return X.shape[0]

    def fit_arrays(self, X: np.ndarray, Y: np.ndarray) -> bool:
        """Entraînement direct (utilisé par la policy) — X (n,d), Y (n,3)."""
        if X.shape[0] < 4:
            return False
        self.net.fit_ridge(X, Y)
        self.trained = True
        return True

    # ---- persistance ---------------------------------------------------------------
    def save(self, path: Path) -> bool:
        """Poids vers npz (checkpoint policy) ; False si non écrivable."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(path, W1=self.net.W1, b1=self.net.b1, W2=self.net.W2, b2=self.net.b2,
                     trained=np.array(self.trained))
            return True
        except OSError:
            return False

    def load(self, path: Path) -> bool:
        try:
            with np.load(path, allow_pickle=False) as data:
                self.net.W1, self.net.b1 = data["W1"], data["b1"]
                self.net.W2, self.net.b2 = data["W2"], data["b2"]
                self.trained = bool(data["trained"])
            return True
        except (OSError, KeyError, ValueError):
            return False


def _heuristic_consequences(features: np.ndarray) -> Dict[str, float]:
    """Fallback déterministe — les mêmes signaux, calculés à la main.

    indices : 4=distance min, 5=densité locale, 6=puissance voisine,
    7=puissance propre, 8=aire, 9=proximité du bord, 11=keepout touché.
    """
    min_dist, density = features[4], features[5]
    power_nearby, own_power = features[6], features[7]
    edge, keepout_hit = features[9], features[11]
    congestion = float(np.clip(0.35 * density + 0.25 * (1.0 - min_dist)
                               + 0.2 * keepout_hit + 0.2 * (1.0 - edge), 0.0, 1.0))
    thermal = float(np.clip(0.45 * power_nearby + 0.35 * own_power + 0.2 * density, 0.0, 1.0))
    si_risk = float(np.clip(0.5 * (1.0 - min_dist) + 0.3 * density + 0.2 * keepout_hit, 0.0, 1.0))
    return {"congestion_score": congestion, "thermal_rise_estimate": thermal, "si_risk": si_risk}

"""Policy ε-greedy — score = récompense prédite − λ·risque, sur les prédictions
du world model.

La policy n'exécute jamais une action réelle pour l'évaluer : elle interroge
``DreamerWorldModel.predict_consequences`` (le « rêve »), convertit les trois
risques en récompense, pénalise le pire scénario (λ·risque) et explore avec
une probabilité ε. L'apprentissage est supervisé par les verdicts du
self_verifier : expériences positives = actions « keep » acceptées, négatives
= rollbacks — la policy apprend à rêver juste, pas à avoir de la chance.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# pondération de la récompense sur les trois conséquences prédites
_W_CONGESTION, _W_THERMAL, _W_SI = 0.4, 0.3, 0.3
_EPSILON_MIN = 0.01
_NEGATIVE_FACTOR = 1.15        # cible majorée pour les rollbacks
_POSITIVE_FACTOR = 0.85        # cible minorée pour les keepers


@dataclass
class Experience:
    """Feedback d'une action exécutée — positif (keeper) ou négatif (rollback)."""

    ref: str
    reward: float                 # 0..1, 1 = aucun risque observé
    kept: bool
    metrics: Dict[str, float] = field(default_factory=dict)


class EpsilonGreedyPolicy:
    """Sélection d'action guidée par le world model + apprentissage par verdicts."""

    def __init__(self, world_model: Any, epsilon: float = 0.15,
                 lambda_risk: float = 0.5, seed: int = 42) -> None:
        self.world_model = world_model
        self.epsilon = epsilon
        self.lambda_risk = lambda_risk
        self._rng = random.Random(seed)
        self._last_features: List[np.ndarray] = []
        self.n_positive = 0
        self.n_negative = 0

    # ---- évaluation --------------------------------------------------------------
    def score_action(self, board: Any, action: Any) -> Tuple[float, Dict[str, float]]:
        """(score, détails) : score = reward − λ·risk sur les prédictions."""
        prediction = self.world_model.predict_consequences(board, action)
        reward = 1.0 - (_W_CONGESTION * prediction["congestion_score"]
                        + _W_THERMAL * prediction["thermal_rise_estimate"]
                        + _W_SI * prediction["si_risk"])
        risk = max(prediction["congestion_score"], prediction["thermal_rise_estimate"],
                   prediction["si_risk"])
        return reward - self.lambda_risk * risk, prediction

    # ---- sélection -----------------------------------------------------------------
    def select_action(self, board: Any, candidates: Sequence[Any],
                      legal_mask: Optional[np.ndarray] = None) -> Tuple[Optional[Any], Dict[str, Any]]:
        """Meilleure candidate (ou exploration ε) — None si tout est illégal."""
        legal = [(i, a) for i, a in enumerate(candidates)
                 if legal_mask is None or i < len(legal_mask) and bool(legal_mask[i])]
        if not legal:
            return None, {"reason": "aucune action légale", "explored": False}
        if self._rng.random() < self.epsilon:            # exploration
            idx, action = self._rng.choice(legal)
            score, prediction = self.score_action(board, action)
            action.predicted_reward = score
            return action, {"explored": True, "score": score, "prediction": prediction}
        best, best_score, best_pred = None, -float("inf"), {}
        for _i, action in legal:
            score, prediction = self.score_action(board, action)
            if score > best_score:
                best, best_score, best_pred = action, score, prediction
        best.predicted_reward = best_score
        return best, {"explored": False, "score": best_score, "prediction": best_pred}

    def rank_candidates(self, board: Any, candidates: Sequence[Any]) -> List[Any]:
        """Candidates triées par score décroissant (pour propose_placement)."""
        scored = [(self.score_action(board, a)[0], a) for a in candidates]
        scored.sort(key=lambda pair: -pair[0])
        for score, action in scored:
            action.predicted_reward = score
        return [a for _s, a in scored]

    # ---- apprentissage -----------------------------------------------------------------
    def learn_from(self, experiences: Sequence[Experience]) -> int:
        """Alimente le world model : keepers → cibles améliorées, rollbacks → dégradées."""
        if not experiences:
            return 0
        rows_X: List[np.ndarray] = []
        rows_Y: List[np.ndarray] = []
        for exp in experiences:
            if not self._last_features:
                break                      # pas de features mémorisées → rien à apprendre
            features = self._last_features[-1]
            base = 1.0 - exp.reward        # niveau de risque observé
            factor = _POSITIVE_FACTOR if exp.kept else _NEGATIVE_FACTOR
            target = np.full(3, min(1.0, base * factor), dtype=np.float64)
            rows_X.append(features)
            rows_Y.append(target)
            self.n_positive += 1 if exp.kept else 0
            self.n_negative += 0 if exp.kept else 1
        if not rows_X:
            return 0
        self.world_model.fit_arrays(np.vstack(rows_X), np.vstack(rows_Y))
        return len(rows_X)

    def remember_features(self, board: Any, action: Any) -> None:
        """Mémorise le vecteur de features d'une action proposée (buffer court)."""
        try:
            self._last_features.append(self.world_model.extract_features(
                board, action.ref, action.x_mm, action.y_mm,
                getattr(action, "rotation_deg", 0.0), getattr(action, "layer", 0)))
            if len(self._last_features) > 256:
                self._last_features.pop(0)
        except KeyError:
            pass                      # composant inconnu — rien à mémoriser

    def anneal(self, factor: float = 0.97) -> None:
        """Réduit ε après chaque lot de succès — exploitation croissante."""
        self.epsilon = max(_EPSILON_MIN, self.epsilon * factor)

    # ---- persistance -----------------------------------------------------------------
    def save(self, path: Path) -> bool:
        """Checkpoint complet (poids world model + état ε) ; False si non écrivable."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(path,
                     W1=self.world_model.net.W1, b1=self.world_model.net.b1,
                     W2=self.world_model.net.W2, b2=self.world_model.net.b2,
                     epsilon=np.array(self.epsilon),
                     trained=np.array(self.world_model.trained))
            return True
        except OSError:
            return False

    def load(self, path: Path) -> bool:
        """Recharge un checkpoint policy/world model."""
        try:
            with np.load(path, allow_pickle=False) as data:
                self.world_model.net.W1, self.world_model.net.b1 = data["W1"], data["b1"]
                self.world_model.net.W2, self.world_model.net.b2 = data["W2"], data["b2"]
                self.world_model.trained = bool(data["trained"])
                self.epsilon = max(_EPSILON_MIN, float(data["epsilon"]))
            return True
        except (OSError, KeyError, ValueError):
            return False

    @property
    def stats(self) -> Dict[str, float]:
        return {"epsilon": round(self.epsilon, 4),
                "positive": self.n_positive, "negative": self.n_negative,
                "world_model_trained": self.world_model.trained}

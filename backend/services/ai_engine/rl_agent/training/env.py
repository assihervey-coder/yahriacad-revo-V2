"""Environnement de placement type gym — le terrain de jeu de la passe RL.

Chaque épisode place séquentiellement les composants non verrouillés d'une
carte. À l'étape *t*, l'agent reçoit l'état s_t : le vecteur de 12 features du
world model (``DreamerWorldModel.extract_features``) calculé à l'ancre du
composant courant (centroïde de ses partenaires de nets). Il répond par un
vecteur continu-discret ``(dx_norm, dy_norm, rot_idx, layer)`` :

  * ``(dx, dy)``   — déplacement relatif à l'ancre, en unités normalisées
    (× ``spread_mm`` = 12 mm par défaut) ;
  * ``rot_idx``    — index de rotation ∈ {0, 90, 180, 270}° ;
  * ``layer``      — couche ∈ {0 = top, 1 = bottom}.

Récompense (déterministe, donc reproductible — exigence Circuitron) :

    r = 0.6 · (1 − risque) + 0.4 · gain_HPWL_normalisé − 0.5 · [action illégale]

où ``risque = 0.4·congestion + 0.3·thermique + 0.3·si`` (heuristique du world
model, cf. ``dreamer._heuristic_consequences``). Une action illégale (hors
carte, keepout, collision) n'est PAS appliquée : le composant garde son
emplacement courant et l'agent encaisse la pénalité — c'est le mur bas.

L'environnement travaille sur les placements d'une copie restaurée à chaque
``reset()`` : le board appelant n'est jamais muté.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from backend.services.ai_engine.rl_agent.action_space.space import ROTATIONS, Action, ActionSpace
from backend.services.ai_engine.rl_agent.world_model.dreamer import (
    DreamerWorldModel,
    _heuristic_consequences,
)
from backend.services.ai_engine.autonomous_optimizer.proposer_llm.proposer import hpwl_mm
from common.design_model import Placement

_RISK_WEIGHTS = (0.4, 0.3, 0.3)       # congestion, thermique, SI — miroir policy.py
_ILLEGAL_PENALTY = -0.5


class PlacementEnv:
    """Environnement gym-like : place les composants d'une carte, pas à pas."""

    def __init__(self, board: Any, seed: int = 42, spread_mm: float = 12.0) -> None:
        self.board = board
        self.seed = seed
        self.spread_mm = spread_mm
        self.action_space = ActionSpace()
        self.world = DreamerWorldModel()          # extract_features uniquement
        self._initial = {ref: replace(p) for ref, p in board.placements.items()}
        self._queue: List[str] = []
        self._anchors: Dict[str, Tuple[float, float]] = {}
        self._rng = random.Random(seed)
        self.steps_taken = 0

    # ---- cycle gym ------------------------------------------------------------------------
    def reset(self) -> np.ndarray:
        """Restaure les placements initiaux et retourne l'état du 1er composant."""
        for ref, placement in self._initial.items():
            self.board.placements[ref] = replace(placement)
        self._queue = [ref for ref, p in self.board.placements.items() if not p.locked]
        self._anchors = {ref: self._anchor_for(ref) for ref in self._queue}
        self.steps_taken = 0
        return self._state(self._queue[0]) if self._queue else np.zeros(12)

    def step(self, action_vec: Sequence[float]) -> Tuple[np.ndarray, float, bool, Dict[str, Any]]:
        """Applique (dx, dy, rot, layer) sur le composant courant.

        Retour : ``(état_suivant, récompense, terminé, info)`` — convention gym.
        """
        if not self._queue:
            return np.zeros(12), 0.0, True, {"reason": "épisode déjà terminé"}
        dx_norm, dy_norm = float(action_vec[0]), float(action_vec[1])
        rot_idx = int(np.clip(round(float(action_vec[2])), 0, len(ROTATIONS) - 1))
        layer = int(np.clip(round(float(action_vec[3])), 0, 1))
        ref = self._queue[0]
        anchor_x, anchor_y = self._anchors[ref]
        action = Action(
            ref=ref,
            x_mm=self.action_space.snap(anchor_x + dx_norm * self.spread_mm),
            y_mm=self.action_space.snap(anchor_y + dy_norm * self.spread_mm),
            rotation_deg=float(ROTATIONS[rot_idx]), layer=layer)

        legal = bool(self.action_space.legal_mask(self.board, [action])[0])
        hpwl_before = hpwl_mm(self.board)
        if legal:
            self.board.move(ref, action.x_mm, action.y_mm,
                            rotation_deg=action.rotation_deg)
            self.board.placements[ref] = replace(self.board.placements[ref],
                                                 layer=action.layer)
        features = self.world.extract_features(self.board, ref, action.x_mm, action.y_mm,
                                               action.rotation_deg, action.layer)
        consequences = _heuristic_consequences(features)
        risk = (_RISK_WEIGHTS[0] * consequences["congestion_score"]
                + _RISK_WEIGHTS[1] * consequences["thermal_rise_estimate"]
                + _RISK_WEIGHTS[2] * consequences["si_risk"])
        hpwl_after = hpwl_mm(self.board)
        hpwl_gain = 0.0
        if hpwl_before > 1e-9:
            hpwl_gain = float(np.clip((hpwl_before - hpwl_after) / hpwl_before, -1.0, 1.0))
        reward = 0.6 * (1.0 - risk) + 0.4 * hpwl_gain
        if not legal:
            reward += _ILLEGAL_PENALTY

        self._queue.pop(0)
        self.steps_taken += 1
        done = not self._queue
        next_state = self._state(self._queue[0]) if self._queue else np.zeros(12)
        info: Dict[str, Any] = {"ref": ref, "legal": legal,
                                "consequences": consequences,
                                "hpwl_mm": round(hpwl_after, 3),
                                "step": self.steps_taken}
        return next_state, float(reward), done, info

    # ---- helpers ------------------------------------------------------------------------------
    def _anchor_for(self, ref: str) -> Tuple[float, float]:
        """Ancre = centroïde des partenaires de nets placés, sinon position courante."""
        xs: List[float] = []
        ys: List[float] = []
        for net in self.board.nets.values():
            partners = [r for r, _pad in net.connections if r != ref and r in self.board.placements]
            if not partners:
                continue
            xs.extend(self.board.placements[r].x_mm for r in partners)
            ys.extend(self.board.placements[r].y_mm for r in partners)
        if xs and ys:
            return (float(np.clip(sum(xs) / len(xs), 0.0, self.board.width_mm)),
                    float(np.clip(sum(ys) / len(ys), 0.0, self.board.height_mm)))
        current = self.board.placements.get(ref)
        if current is not None and (current.x_mm or current.y_mm):
            return (current.x_mm, current.y_mm)
        return (self.board.width_mm / 2.0, self.board.height_mm / 2.0)

    def _state(self, ref: str) -> np.ndarray:
        """Features du composant à placer, calculées à son ancre (rot 0, top)."""
        anchor_x, anchor_y = self._anchors[ref]
        return self.world.extract_features(self.board, ref, anchor_x, anchor_y, 0.0, 0)

    # ---- utilitaires ---------------------------------------------------------------------------
    def random_action(self) -> np.ndarray:
        """Action uniforme légale-dans-les-bornes — pour la collecte de dataset."""
        return np.array([self._rng.uniform(-1.0, 1.0), self._rng.uniform(-1.0, 1.0),
                         self._rng.randint(0, len(ROTATIONS) - 1),
                         self._rng.randint(0, 1)], dtype=np.float64)

    @property
    def pending(self) -> List[str]:
        """Composants restant à placer dans l'épisode courant."""
        return list(self._queue)

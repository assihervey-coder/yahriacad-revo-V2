"""Espace d'action (x, y, rotation, couche) — le vocabulaire du rl_agent.

Discrétisation : pas de 0.5 mm en x/y, rotations limitées à {0, 90, 180, 270}°,
couche ∈ {0=top, 1=bottom}. Fournit l'échantillonnage (autour d'une ancre ou
uniforme), l'encodage numpy pour le world model, et le masque des actions
illégales (hors carte, zones keepout, superpositions grossières) — le masque
évite de gaspiller les essais du world model sur des actions trivialement
invalides.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np

from common.design_model import Placement

GRID_STEP_MM = 0.5
ROTATIONS: Tuple[float, ...] = (0.0, 90.0, 180.0, 270.0)
LAYERS: Tuple[int, ...] = (0, 1)
DEFAULT_CLEARANCE_MM = 0.2


@dataclass
class Action:
    """Une action de placement — mappable 1:1 vers Placement et le proto gRPC."""

    ref: str
    x_mm: float
    y_mm: float
    rotation_deg: float = 0.0
    layer: int = 0
    predicted_reward: float = 0.0     # estimation du world_model (DreamerV3)

    def to_placement(self, locked: bool = False) -> Placement:
        return Placement(ref=self.ref, x_mm=self.x_mm, y_mm=self.y_mm,
                         rotation_deg=self.rotation_deg, layer=self.layer, locked=locked)

    def to_json(self) -> dict:
        return {"component_ref": self.ref, "x_mm": self.x_mm, "y_mm": self.y_mm,
                "rotation_deg": self.rotation_deg, "layer": self.layer,
                "predicted_reward": round(self.predicted_reward, 4)}


class ActionSpace:
    """Fabrique et filtre les actions de placement pour un composant donné."""

    def __init__(self, step_mm: float = GRID_STEP_MM) -> None:
        self.step_mm = step_mm

    # ---- utilitaires ------------------------------------------------------------
    def snap(self, value_mm: float) -> float:
        """Aligne une coordonnée sur la grille de 0.5 mm."""
        return round(round(value_mm / self.step_mm) * self.step_mm, 3)

    def encode(self, action: Action) -> np.ndarray:
        """Encode une action en vecteur numpy (x, y, rotation/90, couche)."""
        return np.array([action.x_mm, action.y_mm,
                         action.rotation_deg / 90.0, float(action.layer)], dtype=np.float64)

    def decode(self, vector: Sequence[float], ref: str) -> Action:
        """Reconstruit une action depuis un vecteur encodé."""
        x, y, rot_idx, layer = vector
        rotation = ROTATIONS[int(round(rot_idx)) % len(ROTATIONS)]
        return Action(ref=ref, x_mm=self.snap(float(x)), y_mm=self.snap(float(y)),
                      rotation_deg=float(rotation), layer=int(layer) % len(LAYERS))

    # ---- échantillonnage -------------------------------------------------------------
    def sample(self, bounds: Tuple[float, float], n: int, ref: str,
               anchor: Optional[Tuple[float, float]] = None,
               spread_mm: float = 12.0,
               rng: Optional[random.Random] = None) -> List[Action]:
        """Échantillonne n actions : autour de l'ancre (jitter ±spread) si fournie,
        sinon uniformément sur la carte. Toutes les coordonnées sont snappées."""
        rng = rng or random.Random(42)
        width, height = bounds
        actions: List[Action] = []
        for _ in range(max(0, n)):
            if anchor is not None:
                x = anchor[0] + rng.uniform(-spread_mm, spread_mm)
                y = anchor[1] + rng.uniform(-spread_mm, spread_mm)
            else:
                x, y = rng.uniform(0.0, width), rng.uniform(0.0, height)
            actions.append(Action(
                ref=ref, x_mm=self.snap(min(max(x, 0.0), width)),
                y_mm=self.snap(min(max(y, 0.0), height)),
                rotation_deg=rng.choice(ROTATIONS), layer=rng.choice(LAYERS)))
        return actions

    # ---- légalité --------------------------------------------------------------------------
    def legal_mask(self, board: Any, actions: Sequence[Action]) -> np.ndarray:
        """Masque booléen : True = action légalement plaçable sur ce board."""
        mask = np.zeros(len(actions), dtype=bool)
        comp = board.components.get(actions[0].ref) if actions else None
        for i, action in enumerate(actions):
            if comp is None:
                continue
            placement = action.to_placement()
            x1, y1, x2, y2 = comp.bounding_box(placement)
            # 1) intégralement sur la carte
            if x1 < 0.0 or y1 < 0.0 or x2 > board.width_mm or y2 > board.height_mm:
                continue
            # 2) hors des zones keepout (marge de clearance incluse)
            margin = DEFAULT_CLEARANCE_MM
            hit_keepout = any(
                not (x2 <= z.x_min_mm - margin or z.x_max_mm + margin <= x1
                     or y2 <= z.y_min_mm - margin or z.y_max_mm + margin <= y1)
                for z in board.zones if z.kind == "keepout")
            if hit_keepout:
                continue
            # 3) pas de superposition grossière avec les autres composants
            collision = False
            for other_ref, other_placement in board.placements.items():
                if other_ref == action.ref or other_ref not in board.components:
                    continue
                other = board.components[other_ref]
                ox1, oy1, ox2, oy2 = other.bounding_box(other_placement)
                if not (x2 <= ox1 + margin or ox2 + margin <= x1
                        or y2 <= oy1 + margin or oy2 + margin <= y1):
                    collision = True
                    break
            mask[i] = not collision
        return mask

    def anchored_candidates(self, board: Any, ref: str, n: int,
                            anchor: Tuple[float, float],
                            rng: Optional[random.Random] = None) -> List[Action]:
        """Échantillonne puis filtre : ne garde que les actions légales."""
        candidates = self.sample((board.width_mm, board.height_mm), n, ref,
                                 anchor=anchor, rng=rng)
        mask = self.legal_mask(board, candidates)
        return [action for action, ok in zip(candidates, mask) if ok]

    def rotated(self, action: Action, rotation_deg: float) -> Action:
        """Copie de l'action avec une nouvelle rotation (utilisé par le proposer)."""
        return replace(action, rotation_deg=float(rotation_deg))

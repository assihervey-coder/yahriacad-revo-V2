"""Collecte du dataset d'entraînement — transitions réelles de l'environnement.

Le dataset sert au pré-entraînement supervisé du world model torch :
chaque ligne relie les 12 features d'une décision de placement aux trois
conséquences RÉELLEMENT calculées par l'environnement (heuristiques
déterministes du simulateur interne). C'est le principe « apprendre le
monde avant d'agir dedans » de l'approche Dreamer : le réseau de runtime
n'est jamais alimenté par des données fictives.

Les archives JSONL du keeper (``data/trained_models/keeper_archive.jsonl``)
ne contiennent pas les features brutes (seulement propositions + scores) :
``summarize_archive`` les résume pour le rapport d'entraînement, sans jamais
les présenter comme un dataset de régression — honnêteté des métriques.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from backend.services.ai_engine.rl_agent.training.env import PlacementEnv


def collect_dataset(env: PlacementEnv, episodes: int = 30,
                    seed: int = 42) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Dict[str, Any]]:
    """Rollouts aléatoires → (features X, cibles Y, actions A, rewards R, stats).

    * ``X`` — (n, 12) features du composant courant à son ancre ;
    * ``Y`` — (n, 3)  conséquences réelles [congestion, thermique, si] ;
    * ``A`` — (n, 4)  vecteur d'action appliqué (dx, dy, rot_idx, layer) ;
    * ``R`` — (n,)    récompense de chaque transition.
    """
    rows_X: List[np.ndarray] = []
    rows_Y: List[np.ndarray] = []
    rows_A: List[np.ndarray] = []
    rows_R: List[float] = []
    n_legal, n_steps = 0, 0
    for _episode in range(max(1, episodes)):
        state = env.reset()
        done = False
        while not done:
            action = env.random_action()
            next_state, reward, done, info = env.step(action)
            rows_X.append(np.asarray(state, dtype=np.float64))
            cons = info["consequences"]
            rows_Y.append(np.array([cons["congestion_score"],
                                    cons["thermal_rise_estimate"],
                                    cons["si_risk"]], dtype=np.float64))
            rows_A.append(np.asarray(action, dtype=np.float64))
            rows_R.append(float(reward))
            n_legal += 1 if info.get("legal") else 0
            n_steps += 1
            state = next_state
    stats = {"episodes": episodes, "transitions": n_steps, "legal_ratio":
             round(n_legal / n_steps, 3) if n_steps else 0.0,
             "mean_reward": round(float(np.mean(rows_R)), 4) if rows_R else 0.0}
    return (np.vstack(rows_X) if rows_X else np.zeros((0, 12)),
            np.vstack(rows_Y) if rows_Y else np.zeros((0, 3)),
            np.vstack(rows_A) if rows_A else np.zeros((0, 4)),
            np.array(rows_R, dtype=np.float64), stats)


def summarize_archive(path: Optional[Path]) -> Dict[str, Any]:
    """Résumé d'une archive keeper JSONL — contexte du rapport, pas un dataset."""
    if path is None or not Path(path).exists():
        return {"available": False}
    iterations, kept, scores = 0, 0, []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                iterations += 1
                kept += 1 if record.get("kept") else 0
                if isinstance(record.get("eval_score"), (int, float)):
                    scores.append(float(record["eval_score"]))
    except OSError:
        return {"available": False}
    return {"available": True, "iterations": iterations, "kept": kept,
            "keep_ratio": round(kept / iterations, 3) if iterations else 0.0,
            "mean_eval_score": round(sum(scores) / len(scores), 4) if scores else None}

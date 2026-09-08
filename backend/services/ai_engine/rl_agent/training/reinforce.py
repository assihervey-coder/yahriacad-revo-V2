"""REINFORCE avec baseline — la passe de policy-gradient de l'agent [AutoPCB].

Algorithme : pour chaque épisode, on accumule les (état, action, récompense)
de l'environnement de placement ; à l'issue, on calcule les retours actualisés
(return-to-go), on les centre par une baseline EMA (moyenne mobile des retours
d'épisode) et on minimise :

    L = −moyenne(log π(a_t|s_t) · (G_t − b)) − β · moyenne(H[π])

Le bonus d'entropie (β) maintient l'exploration ; la baseline EMA réduit la
variance sans introduction de critic (les épisodes sont courts : ≤ 12 pas).
C'est le choix assumé REINFORCE plutôt que PPO : 3 ordres de grandeur moins
de calcul par mise à jour, suffisant pour un espace d'action de dimension 4.

torch est importé de façon gardée — sans torch, le module reste importable
mais les classes ne sont pas définies (cf. torch_modules.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

try:                                   # import gardé — torch optionnel
    import torch
except ImportError:
    torch = None                       # type: ignore[assignment]


def torch_available() -> bool:
    return torch is not None


if torch is not None:                  # bloc gardé

    @dataclass
    class ReinforceConfig:
        """Hyperparamètres de la passe REINFORCE — valeurs par défaut sobres."""

        episodes: int = 120            # épisodes d'entraînement
        lr: float = 3e-3               # Adam — petit réseau, LR volontairement haut
        gamma: float = 0.95            # actualisation (épisodes courts)
        entropy_beta: float = 1e-2     # bonus d'entropie
        baseline_beta: float = 0.9     # lissage EMA de la baseline
        seed: int = 42
        device: str = "cpu"
        deterministic_eval: bool = False

    class ReinforceTrainer:
        """Boucle d'entraînement policy-gradient sur ``PlacementEnv``."""

        def __init__(self, policy_net: Any, env: Any,
                     config: Optional[ReinforceConfig] = None) -> None:
            self.policy = policy_net
            self.env = env
            self.cfg = config or ReinforceConfig()
            torch.manual_seed(self.cfg.seed)
            self.optimizer = torch.optim.Adam(self.policy.parameters(), lr=self.cfg.lr)
            self.baseline = 0.0                  # EMA des retours d'épisode
            self.best_mean_reward = -float("inf")
            self.history: List[Dict[str, float]] = []

        # ---- un épisode ------------------------------------------------------------------------
        def _rollout(self) -> Dict[str, Any]:
            """Un épisode complet : états, log-probs, entropies, rewards."""
            state = torch.tensor(np.asarray(self.env.reset(), dtype=np.float32),
                                 device=self.cfg.device)
            log_probs, entropies, rewards = [], [], []
            done, info_last = False, {}
            while not done:
                action_vec, aux = self.policy.act(state.unsqueeze(0),
                                                  deterministic=self.cfg.deterministic_eval)
                next_state, reward, done, info = self.env.step(action_vec.detach().cpu().numpy()[0])
                log_probs.append(aux["log_prob"].squeeze(0))
                entropies.append(aux["entropy"].squeeze(0))
                rewards.append(float(reward))
                info_last = info
                state = torch.tensor(np.asarray(next_state, dtype=np.float32),
                                     device=self.cfg.device)
            return {"log_probs": log_probs, "entropies": entropies,
                    "rewards": rewards, "final_info": info_last}

        @staticmethod
        def _returns(rewards: List[float], gamma: float) -> "torch.Tensor":
            """Return-to-go actualisé : G_t = r_t + γ·G_{t+1} (Monte-Carlo)."""
            returns, accumulator = [], 0.0
            for reward in reversed(rewards):
                accumulator = reward + gamma * accumulator
                returns.insert(0, accumulator)
            return torch.tensor(returns, dtype=torch.float32)

        def train_episode(self) -> Dict[str, float]:
            """Un épisode = rollout + mise à jour policy-gradient. Retour : métriques."""
            rollout = self._rollout()
            returns = self._returns(rollout["rewards"], self.cfg.gamma)
            episode_return = float(returns[0].item())
            self.baseline = (self.cfg.baseline_beta * self.baseline
                             + (1.0 - self.cfg.baseline_beta) * episode_return)
            advantages = returns - self.baseline            # baseline EMA — variance réduite
            if advantages.numel() > 1:
                std = advantages.std().item()
                if std > 1e-6:                              # normalisation légère
                    advantages = (advantages - advantages.mean()) / (std + 1e-8)

            log_probs = torch.stack(rollout["log_probs"])
            entropies = torch.stack(rollout["entropies"])
            policy_loss = -(log_probs * advantages).mean()
            entropy_bonus = entropies.mean()
            loss = policy_loss - self.cfg.entropy_beta * entropy_bonus

            self.optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.policy.parameters(), max_norm=5.0)
            self.optimizer.step()

            metrics = {"episode_return": round(episode_return, 4),
                       "baseline": round(self.baseline, 4),
                       "loss": round(float(loss.item()), 6),
                       "entropy": round(float(entropy_bonus.item()), 4),
                       "steps": len(rollout["rewards"]),
                       "hpwl_mm": rollout["final_info"].get("hpwl_mm", 0.0)}
            self.history.append(metrics)
            return metrics

        # ---- boucle complète ------------------------------------------------------------------------
        def train(self, episodes: Optional[int] = None,
                  checkpoint_path: Optional[Path] = None) -> Dict[str, Any]:
            """Entraîne N épisodes, sauvegarde le meilleur checkpoint (retour moyen max)."""
            total = int(episodes or self.cfg.episodes)
            window: List[float] = []
            for episode in range(1, total + 1):
                metrics = self.train_episode()
                window.append(metrics["episode_return"])
                if len(window) > 10:
                    window.pop(0)
                mean_recent = sum(window) / len(window)
                if mean_recent > self.best_mean_reward + 1e-6 and checkpoint_path is not None:
                    self.best_mean_reward = mean_recent
                    self.save_checkpoint(checkpoint_path, episode, mean_recent)
            mean_reward = (sum(m["episode_return"] for m in self.history) / len(self.history)
                           if self.history else 0.0)
            return {"episodes": len(self.history),
                    "mean_reward": round(mean_reward, 4),
                    "best_mean_reward_10": round(self.best_mean_reward, 4),
                    "final_baseline": round(self.baseline, 4),
                    "history_tail": self.history[-5:]}

        # ---- persistance ------------------------------------------------------------------------
        def save_checkpoint(self, path: Path, episode: int, mean_reward: float) -> bool:
            """Checkpoint complet (poids policy + hyperparamètres + métrique)."""
            try:
                path = Path(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                torch.save({"policy_state_dict": self.policy.state_dict(),
                            "episode": episode,
                            "mean_reward_10": mean_reward,
                            "config": {"lr": self.cfg.lr, "gamma": self.cfg.gamma,
                                       "entropy_beta": self.cfg.entropy_beta,
                                       "baseline_beta": self.cfg.baseline_beta,
                                       "seed": self.cfg.seed}}, path)
                return True
            except OSError:
                return False

        def load_checkpoint(self, path: Path) -> bool:
            """Recharge un checkpoint policy — False si fichier absent/corrompu."""
            try:
                payload = torch.load(Path(path), map_location=self.cfg.device)
                self.policy.load_state_dict(payload["policy_state_dict"])
                return True
            except (OSError, KeyError, RuntimeError, FileNotFoundError):
                return False

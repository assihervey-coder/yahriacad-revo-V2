"""Modules torch de la passe RL — world model + policy de placement.

``TorchWorldModel`` reflète EXACTEMENT l'architecture du ``TinyNet`` numpy du
runtime (``dreamer.TinyNet``) : première couche = projection aléatoire FIXE
(random features), seconde couche = tête linéaire entraînée. Cette contrainte
de symétrie est délibérée : les poids torch entraînés par Adam + early
stopping sont exportables en ``.npz`` au format du runtime
(``DreamerWorldModel.load``) — la passe d'entraînement améliore donc
directement le cerveau embarqué, sans distillation ni traduction de graphe.

``PlacementPolicyNet`` est la tête de décision entraînée par policy-gradient
(REINFORCE, cf. ``reinforce.py``) : tronc MLP sur les 12 features du world
model, tête gaussienne pour (dx, dy) continus, têtes catégorielles pour la
rotation (4 classes) et la couche (2 classes).

torch est importé de façon gardée : sans torch, ce module reste importable
(les classes ne sont simplement pas définies) — cohérent avec la convention
du dépôt « le runtime ne dépend jamais d'une lib lourde ».
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:                                   # import gardé — torch optionnel
    import torch
    from torch import nn
except ImportError:                    # torch absent — pas de définition de classes
    torch = None                       # type: ignore[assignment]
    nn = None                          # type: ignore[assignment]


def torch_available() -> bool:
    """True si torch est importable — utilisé par le CLI et les tests."""
    return torch is not None


if torch is not None:                  # bloc gardé — voir docstring de module

    class TorchWorldModel(nn.Module):
        """World model torch — miroir exact du TinyNet runtime (W1 fixe, W2 appris)."""

        def __init__(self, feature_dim: int = 12, hidden: int = 32,
                     output_dim: int = 3, seed: int = 42) -> None:
            super().__init__()
            # Même initialisation que dreamer.TinyNet : projection aléatoire partagée
            rng = np.random.default_rng(seed)
            W1 = rng.normal(0.0, 0.5, size=(feature_dim, hidden)).astype(np.float32)
            b1 = np.zeros(hidden, dtype=np.float32)
            self.register_buffer("W1", torch.from_numpy(W1))
            self.register_buffer("b1", torch.from_numpy(b1))
            self.head = nn.Linear(hidden, output_dim)      # W2, b2 entraînables
            nn.init.zeros_(self.head.weight)
            nn.init.zeros_(self.head.bias)

        def forward(self, X: "torch.Tensor") -> "torch.Tensor":
            """tanh(X·W1 + b1)·W2 + b2 — mêmes maths que le runtime numpy."""
            hidden = torch.tanh(X @ self.W1 + self.b1)
            return self.head(hidden)

        @torch.no_grad()
        def export_npz(self, path: Path) -> bool:
            """Export au format des checkpoints du runtime — DreamerWorldModel.load."""
            try:
                path = Path(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                W2 = self.head.weight.detach().cpu().numpy().T      # (hidden, out)
                b2 = self.head.bias.detach().cpu().numpy()
                np.savez(path,
                         W1=self.W1.cpu().numpy().astype(np.float64),
                         b1=self.b1.cpu().numpy().astype(np.float64),
                         W2=W2.astype(np.float64), b2=b2.astype(np.float64),
                         trained=np.array(True))
                return True
            except OSError:
                return False

    def train_supervised_world_model(
            model: "TorchWorldModel", X: np.ndarray, Y: np.ndarray,
            epochs: int = 60, lr: float = 1e-2, batch_size: int = 32,
            weight_decay: float = 1e-4, val_ratio: float = 0.2,
            patience: int = 8, seed: int = 42,
            device: str = "cpu") -> Dict[str, Any]:
        """Régression supervisée (MSE + Adam) avec split train/val et early stopping.

        Retour : ``{"epochs_run", "best_val_mse", "final_train_mse", "history"}``.
        Le meilleur état (val minimale) est restauré avant la sortie — jamais
        d'overfit silencieux vers les derniers epochs.
        """
        assert model is not None and X.shape[0] >= 8, "dataset trop petit (< 8 lignes)"
        rng = np.random.default_rng(seed)
        indices = rng.permutation(X.shape[0])
        n_val = max(2, int(X.shape[0] * val_ratio))
        val_idx, train_idx = indices[:n_val], indices[n_val:]
        X_train = torch.tensor(X[train_idx], dtype=torch.float32, device=device)
        Y_train = torch.tensor(Y[train_idx], dtype=torch.float32, device=device)
        X_val = torch.tensor(X[val_idx], dtype=torch.float32, device=device)
        Y_val = torch.tensor(Y[val_idx], dtype=torch.float32, device=device)

        optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, epochs))
        history: List[Dict[str, float]] = []
        best_val, best_state, bad_epochs = float("inf"), None, 0

        for epoch in range(1, max(1, epochs) + 1):
            model.train()
            perm = torch.randperm(X_train.shape[0])
            epoch_loss, n_batches = 0.0, 0
            for start in range(0, X_train.shape[0], batch_size):
                batch = perm[start:start + batch_size]
                optimizer.zero_grad()
                prediction = model(X_train[batch])
                loss = torch.nn.functional.mse_loss(prediction, Y_train[batch])
                loss.backward()
                optimizer.step()
                epoch_loss += float(loss.item())
                n_batches += 1
            scheduler.step()
            model.eval()
            with torch.no_grad():
                val_loss = float(torch.nn.functional.mse_loss(model(X_val), Y_val).item())
            history.append({"epoch": epoch,
                            "train_mse": round(epoch_loss / max(1, n_batches), 6),
                            "val_mse": round(val_loss, 6)})
            if val_loss < best_val - 1e-6:                # early stopping
                best_val, bad_epochs = val_loss, 0
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
            else:
                bad_epochs += 1
                if bad_epochs >= patience:
                    break
        if best_state is not None:
            model.load_state_dict(best_state)
        return {"epochs_run": len(history), "best_val_mse": round(best_val, 6),
                "final_train_mse": history[-1]["train_mse"] if history else None,
                "history": history}

    class PlacementPolicyNet(nn.Module):
        """Policy π_θ(a|s) : tronc MLP → gaussienne (dx, dy) + catégorielles (rot, couche).

        Les déplacements sont produits en unités normalisées (−1..1 ≈ ±spread_mm
        de l'environnement) : la policy reste invariante à la taille de carte.
        """

        def __init__(self, feature_dim: int = 12, hidden: int = 64,
                     n_rotations: int = 4, n_layers: int = 2) -> None:
            super().__init__()
            self.trunk = nn.Sequential(
                nn.Linear(feature_dim, hidden), nn.Tanh(),
                nn.Linear(hidden, hidden), nn.Tanh())
            self.mu_head = nn.Linear(hidden, 2)               # (dx, dy) normalisés
            self.log_std = nn.Parameter(torch.zeros(2))       # std appris, état-indépendant
            self.rot_head = nn.Linear(hidden, n_rotations)
            self.layer_head = nn.Linear(hidden, n_layers)

        def forward(self, state: "torch.Tensor") -> Dict[str, Any]:
            """Distributions d'action — appelé par ``act`` et par le trainer."""
            features = self.trunk(state)
            mu = self.mu_head(features)
            std = self.log_std.exp().expand_as(mu)
            continuous = torch.distributions.Normal(mu, std)
            rotations = torch.distributions.Categorical(logits=self.rot_head(features))
            layers = torch.distributions.Categorical(logits=self.layer_head(features))
            return {"continuous": continuous, "rotations": rotations, "layers": layers}

        def act(self, state: "torch.Tensor",
                deterministic: bool = False) -> Tuple["torch.Tensor", Dict[str, "torch.Tensor"]]:
            """Échantillonne une action — retourne (vecteur_action, {log_prob, entropy})."""
            dists = self(state)
            if deterministic:
                dx_dy = dists["continuous"].mean
                rot_idx = dists["rotations"].logits.argmax(dim=-1)
                layer_idx = dists["layers"].logits.argmax(dim=-1)
            else:
                dx_dy = dists["continuous"].rsample()
                rot_idx = dists["rotations"].sample()
                layer_idx = dists["layers"].sample()
            dx_dy = torch.clamp(dx_dy, -1.0, 1.0)
            log_prob = (dists["continuous"].log_prob(dx_dy).sum(dim=-1)
                        + dists["rotations"].log_prob(rot_idx)
                        + dists["layers"].log_prob(layer_idx))
            entropy = (dists["continuous"].entropy().sum(dim=-1)
                       + dists["rotations"].entropy()
                       + dists["layers"].entropy())
            action_vec = torch.stack([dx_dy[:, 0], dx_dy[:, 1],
                                      rot_idx.to(dx_dy.dtype),
                                      layer_idx.to(dx_dy.dtype)], dim=-1)
            return action_vec, {"log_prob": log_prob, "entropy": entropy}

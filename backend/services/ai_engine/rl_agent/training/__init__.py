"""Passe d'entraînement RL du cerveau IA [AutoPCB].

Chaîne complète offline :
  1. ``env.PlacementEnv``        — environnement de placement type gym,
    récompense déterministe (risques heuristiques + gain HPWL) ;
  2. ``dataset.collect_dataset`` — transitions (features → conséquences réelles)
    récoltées par rollouts aléatoires dans l'environnement ;
  3. ``torch_modules.TorchWorldModel`` — world model torch (architecture miroir
    exacte du TinyNet numpy du runtime) pré-entraîné par régression supervisée
    puis exporté en ``.npz`` directement chargeable par ``DreamerWorldModel`` ;
  4. ``torch_modules.PlacementPolicyNet`` + ``reinforce.ReinforceTrainer`` —
    policy-gradient (REINFORCE avec baseline EMA + bonus d'entropie) sur
    l'espace d'action continu (dx, dy) et discret (rotation, couche) ;
  5. ``train.main``              — CLI orchestrant le tout, artefacts dans
    ``data/trained_models/rl_checkpoints/``.

torch est une dépendance OPTIONNELLE : ``env`` et ``dataset`` fonctionnent en
numpy pur, les modules torch sont gardés par import conditionnel.
"""

from backend.services.ai_engine.rl_agent.training.dataset import collect_dataset
from backend.services.ai_engine.rl_agent.training.env import PlacementEnv

try:                                   # import gardé — torch optionnel
    from backend.services.ai_engine.rl_agent.training.torch_modules import (
        PlacementPolicyNet,
        TorchWorldModel,
        train_supervised_world_model,
    )
    from backend.services.ai_engine.rl_agent.training.reinforce import (
        ReinforceConfig,
        ReinforceTrainer,
    )
    TORCH_AVAILABLE = True
except ImportError:                    # torch absent — sous-ensemble numpy seul
    PlacementPolicyNet = None          # type: ignore[assignment]
    TorchWorldModel = None             # type: ignore[assignment]
    train_supervised_world_model = None  # type: ignore[assignment]
    ReinforceConfig = None             # type: ignore[assignment]
    ReinforceTrainer = None            # type: ignore[assignment]
    TORCH_AVAILABLE = False

__all__ = ["PlacementEnv", "collect_dataset", "TORCH_AVAILABLE",
           "TorchWorldModel", "train_supervised_world_model",
           "PlacementPolicyNet", "ReinforceConfig", "ReinforceTrainer"]

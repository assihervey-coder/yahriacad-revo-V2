"""Tests de la passe RL d'entraînement (torch).

Sans torch : seuls les tests numpy (environnement, dataset, archives) tournent.
Avec torch : régression supervisée du world model (perte qui baisse), export
npz chargé par le runtime ``DreamerWorldModel``, et boucle REINFORCE complète.
"""

from __future__ import annotations

import numpy as np
import pytest
from dataclasses import replace

from backend.services.ai_engine.rl_agent.training.dataset import (
    collect_dataset, summarize_archive)
from backend.services.ai_engine.rl_agent.training.env import PlacementEnv
from backend.services.ai_engine.rl_agent.training.train import build_demo_board


@pytest.fixture()
def env():
    return PlacementEnv(build_demo_board(), seed=42)


# ---- environnement (numpy pur — toujours exécutés) ------------------------------------
def test_env_reset_state_features(env):
    state = env.reset()
    assert state.shape == (12,)
    assert np.all(np.isfinite(state))
    assert len(env.pending) == 3                 # U1, U2, J1


def test_env_episode_completes(env):
    env.reset()
    steps, total_reward = 0, 0.0
    done = False
    while not done:
        _state, reward, done, info = env.step(env.random_action())
        assert isinstance(reward, float) and np.isfinite(reward)
        assert "consequences" in info
        total_reward += reward
        steps += 1
    assert done and steps == 3                   # un pas par composant
    assert total_reward > -3.0                   # jamais d'effondrement catastrophique


def test_env_illegal_action_penalized(env):
    env.reset()
    _state, _r0, _d0, _i0 = env.step(env.random_action())
    # action hors carte : dx énorme vers l'extérieur + composant poussé hors bornes
    _state, reward, _done, info = env.step(np.array([+40.0, 0.0, 0, 0]))
    if not info["legal"]:
        assert reward <= 0.0                     # pénalité appliquée


def test_env_restores_initial_placements(env):
    initial = replace(env.board.placements["U2"])
    env.reset()
    for _ in range(3):
        env.step(env.random_action())
    env.reset()
    assert env.board.placements["U2"] == initial


# ---- dataset ----------------------------------------------------------------------------
def test_collect_dataset_shapes(env):
    X, Y, A, R, stats = collect_dataset(env, episodes=4, seed=7)
    assert X.shape[1] == 12 and Y.shape[1] == 3 and A.shape[1] == 4
    assert R.shape[0] == X.shape[0] == Y.shape[0] == A.shape[0]
    assert X.shape[0] >= 12                       # 4 épisodes × 3 pas
    assert stats["transitions"] == X.shape[0]
    assert np.all(np.isfinite(X)) and np.all(np.isfinite(Y))


def test_summarize_archive_missing(tmp_path):
    assert summarize_archive(tmp_path / "absent.jsonl") == {"available": False}


def test_summarize_archive_parses(tmp_path):
    archive = tmp_path / "keeper_archive.jsonl"
    lines = ['{"iteration": 1, "kept": true, "eval_score": 0.55}',
             '{"iteration": 2, "kept": false, "eval_score": 0.53}',
             '{"iteration": 3, "kept": true, "eval_score": 0.61}']
    archive.write_text("\n".join(lines), encoding="utf-8")
    summary = summarize_archive(archive)
    assert summary["available"] and summary["iterations"] == 3
    assert summary["kept"] == 2 and summary["keep_ratio"] == 0.667


# ---- torch (skippés proprement si absent) -------------------------------------------------
def test_world_model_supervised_training(tmp_path):
    torch = pytest.importorskip("torch", reason="torch absent — passe RL non testée")
    from backend.services.ai_engine.rl_agent.training.torch_modules import (
        TorchWorldModel, train_supervised_world_model)

    env = PlacementEnv(build_demo_board(), seed=42)
    X, Y, _A, _R, _stats = collect_dataset(env, episodes=12, seed=42)
    world = TorchWorldModel(seed=42)
    result = train_supervised_world_model(world, X, Y, epochs=30, lr=5e-3,
                                          batch_size=16, seed=42)
    assert result["epochs_run"] >= 1
    assert result["best_val_mse"] < 0.5           # converge vers les heuristiques
    first, last = result["history"][0]["train_mse"], result["history"][-1]["train_mse"]
    assert last <= first                          # la perte descend (ou stationne bas)


def test_world_model_npz_export_loads_in_runtime(tmp_path):
    pytest.importorskip("torch", reason="torch absent — passe RL non testée")
    from backend.services.ai_engine.rl_agent.world_model.dreamer import DreamerWorldModel
    from backend.services.ai_engine.rl_agent.training.torch_modules import TorchWorldModel

    torch_world = TorchWorldModel(seed=42)
    npz_path = tmp_path / "world_model_torch.npz"
    assert torch_world.export_npz(npz_path) is True
    runtime = DreamerWorldModel()
    assert runtime.load(npz_path) is True         # format identique au runtime
    assert runtime.trained is True
    assert runtime.net.W1.shape == torch_world.W1.numpy().shape
    np.testing.assert_allclose(runtime.net.W1, torch_world.W1.numpy(), rtol=1e-6)


def test_reinforce_training_loop(tmp_path):
    pytest.importorskip("torch", reason="torch absent — passe RL non testée")
    from backend.services.ai_engine.rl_agent.training.reinforce import (
        ReinforceConfig, ReinforceTrainer)
    from backend.services.ai_engine.rl_agent.training.torch_modules import PlacementPolicyNet

    env = PlacementEnv(build_demo_board(), seed=42)
    policy = PlacementPolicyNet()
    trainer = ReinforceTrainer(policy, env, config=ReinforceConfig(episodes=6, seed=42))
    ckpt = tmp_path / "policy_reinforce.pt"
    result = trainer.train(episodes=6, checkpoint_path=ckpt)
    assert result["episodes"] == 6
    assert all(np.isfinite(m["loss"]) for m in trainer.history)
    assert ckpt.exists()                          # meilleur checkpoint sauvegardé
    assert trainer.load_checkpoint(ckpt) is True


def test_cli_train_full_pass(tmp_path):
    pytest.importorskip("torch", reason="torch absent — passe RL non testée")
    from backend.services.ai_engine.rl_agent.training import train as train_cli

    out_dir = tmp_path / "rl_checkpoints"
    code = train_cli.main(["--episodes-dataset", "8", "--episodes-rl", "8",
                           "--epochs-world", "12", "--out", str(out_dir),
                           "--seed", "42"])
    assert code == 0
    assert (out_dir / "world_model_torch.npz").exists()
    assert (out_dir / "policy_reinforce.pt").exists()
    assert (out_dir / "training_report.json").exists()

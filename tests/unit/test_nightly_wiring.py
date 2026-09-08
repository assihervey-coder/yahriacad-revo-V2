"""Branchement world_model_torch.npz ↔ boucle nocturne (run_night_optimization).

Sans torch : warm start depuis un checkpoint numpy (DreamerWorldModel.save),
chargé à l'ouverture de la nuit, réécrit en fin de nuit — y compris quand le
générateur est refermé avant épuisement (try/finally).

Avec torch : l'export TorchWorldModel.export_npz est chargé tel quel par le
runtime DreamerWorldModel (format miroir garanti) — c'est le contrat npz qui
relie la passe RL au slot runtime PCB_WORLD_MODEL_NPZ.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.services.ai_engine.main import AiEngine, build_demo_board
from backend.services.ai_engine.rl_agent.world_model.dreamer import DreamerWorldModel


@pytest.fixture()
def engine() -> AiEngine:
    return AiEngine(project_id="test-nightly")


# ---- warm start numpy (toujours exécutés — aucun torch requis) ------------------------
def test_warm_start_loads_and_night_saves(tmp_path):
    """Un npz entraîné est chargé à l'ouverture de la nuit, réécrit à la fermeture."""
    slot = tmp_path / "world_model_torch.npz"
    donor = AiEngine(project_id="donor")
    rng = np.random.default_rng(0)
    assert donor.world_model.fit_arrays(rng.normal(size=(8, 12)),
                                        rng.normal(size=(8, 3))) is True
    assert donor.world_model.save(slot) is True

    engine = AiEngine(project_id="night")
    engine.load_board(build_demo_board())
    iters = list(engine.run_night_optimization(15, warm_start_path=slot, save_path=slot))
    assert len(iters) >= 1
    assert engine.world_model.trained is True                 # poids du donor chargés
    summary = engine.last_night_summary
    assert summary["warm_start"] == {"loaded": True, "path": str(slot)}
    assert summary["world_model_saved"]["saved"] is True
    assert summary["world_model_saved"]["path"] == str(slot)
    # ratchet : le score final ne peut pas être sous le score initial
    assert summary["best_score"] >= summary["initial_score"] - 1e-9
    assert slot.exists()                                      # slot réécrit


def test_warm_start_absent_is_tolerated_and_slot_created(tmp_path, engine):
    """Sans npz présent : la nuit démarre quand même et crée le slot runtime."""
    engine.settings.world_model_npz = tmp_path / "slot" / "world_model_torch.npz"
    engine.load_board(build_demo_board())
    iters = list(engine.run_night_optimization(15))            # chemins par défaut
    assert len(iters) >= 1
    summary = engine.last_night_summary
    assert summary["warm_start"]["loaded"] is False
    assert summary["warm_start"]["path"] == str(tmp_path / "slot" / "world_model_torch.npz")
    assert summary["world_model_saved"]["saved"] is True        # poids de la nuit persistés
    assert engine.settings.world_model_npz.exists()


def test_save_on_early_generator_close(tmp_path):
    """GeneratorExit : le finally sauvegarde le world model même sans épuisement."""
    slot = tmp_path / "wm.npz"
    engine = AiEngine(project_id="early-close")
    engine.load_board(build_demo_board())
    gen = engine.run_night_optimization(300, warm_start_path=tmp_path / "absent.npz",
                                        save_path=slot)
    next(gen)                                                  # première itération gardée
    gen.close()                                                # GeneratorExit → finally
    assert slot.exists()
    assert engine.last_night_summary["world_model_saved"]["saved"] is True
    assert engine.last_night_summary["kept"] >= 1


# ---- contrat npz torch (skippés sans torch) --------------------------------------------
def test_torch_export_npz_loadable_by_runtime(tmp_path):
    """export_npz (passe RL torch) → DreamerWorldModel.load : même format npz."""
    pytest.importorskip("torch")
    from backend.services.ai_engine.rl_agent.training.torch_modules import TorchWorldModel

    npz = tmp_path / "world_model_torch.npz"
    assert TorchWorldModel(seed=42).export_npz(npz) is True
    model = DreamerWorldModel()
    assert model.load(npz) is True
    assert model.trained is True
    # le réseau chargé prédit des conséquences finies (congestion, thermique, SI)
    from backend.services.ai_engine.rl_agent.action_space.space import Action
    board = build_demo_board()
    out = model.predict_consequences(board, Action(ref="U1", x_mm=10.0, y_mm=10.0))
    assert set(out) == {"congestion_score", "thermal_rise_estimate", "si_risk"}
    assert all(np.isfinite(v) and 0.0 <= v <= 1.0 for v in out.values())

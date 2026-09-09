"""Boucle nocturne — pont entre la passe RL torch et le runtime AutoPCB.

S'exécute chaque nuit (``make nightly``) :

  1. **passe RL** — ``training.train`` ré-entraîne le world model torch et
     installe l'export au slot runtime ``PCB_WORLD_MODEL_NPZ``
     (défaut ``data/trained_models/world_model_torch.npz``) ; sans torch, cette
     étape est sautée avec avertissement et la nuit démarre des poids courants ;
  2. **boucle ratchet** — ``AiEngine.run_night_optimization`` charge le npz en
     warm start puis déroule proposer → vérifier → évaluer → keeper sur la
     carte de référence du self-test (placements initiaux volontairement
     médiocres : la nuit a de la marge d'amélioration) ;
  3. **persistance** — en fin de nuit (ou fermeture anticipée), les poids mis à
     jour par les itérations gardées sont réécrits au même slot : le modèle de
     la veille sert de point de départ à la nuit suivante (amélioration
     continue, le design ne régresse jamais — ratchet).

Exemples :

    make nightly                                # passe RL complète + 300 itérations
    python -m backend.services.ai_engine.nightly --train-quick --iters 60
    python -m backend.services.ai_engine.nightly --skip-train   # boucle seule
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from backend.services.ai_engine.main import AiEngine, build_demo_board
from common.config import get_settings
from common.log import get_logger

logger = get_logger("ai_engine.nightly")

_TRAIN_MODULE = "backend.services.ai_engine.rl_agent.training.train"


def _run_rl_pass(train_args: List[str]) -> bool:
    """Lance la passe RL dans un sous-processus ; False si elle échoue (torch absent)."""
    cmd = [sys.executable, "-m", _TRAIN_MODULE, *train_args]
    try:
        proc = subprocess.run(cmd, check=False)
    except OSError as exc:                       # interpréteur indisponible, etc.
        print(f"passe RL non lancée ({exc})", file=sys.stderr)
        return False
    if proc.returncode != 0:
        print("passe RL échouée (torch absent ?) — nuit lancée sans warm start torch",
              file=sys.stderr)
        return False
    return True


def main(argv: List[str] | None = None) -> int:
    """Point d'entrée CLI — 0 si la nuit s'est déroulée (ratchet respecté)."""
    parser = argparse.ArgumentParser(
        prog="ai_engine.nightly",
        description="Boucle nocturne : passe RL torch (world_model_torch.npz) "
                    "puis optimisation ratchet warm-startée.")
    parser.add_argument("--iters", type=int, default=300,
                        help="itérations max de la boucle nocturne (défaut 300)")
    parser.add_argument("--skip-train", action="store_true",
                        help="saute la passe RL (boucle seule, poids courants)")
    parser.add_argument("--train-quick", action="store_true",
                        help="passe RL courte (smoke, ~2 s) au lieu de la passe complète")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    settings = get_settings("ai_engine")
    summary: Dict[str, Any] = {"nightly": {"iters": args.iters,
                                           "world_model_npz": str(settings.world_model_npz)}}

    # ---- 1) passe RL (torch) → slot runtime ------------------------------------------------
    if not args.skip_train:
        train_args: List[str] = []
        if args.train_quick:
            train_args = ["--episodes-dataset", "10", "--epochs-world", "15",
                          "--episodes-rl", "25",
                          "--out", "data/trained_models/rl_checkpoints/nightly_quick"]
        summary["nightly"]["rl_pass"] = "ok" if _run_rl_pass(train_args) else "skipped"
    else:
        summary["nightly"]["rl_pass"] = "skipped"

    # ---- 2+3) boucle nocturne warm-startée + persistance ------------------------------------
    npz = Path(settings.world_model_npz)
    summary["nightly"]["warm_start_available"] = npz.exists()
    engine = AiEngine(project_id="nightly-loop")
    engine.load_board(build_demo_board())
    kept = list(engine.run_night_optimization(args.iters))
    stats = engine.keeper.stats()
    assert stats["best_score"] >= stats["initial_score"] - 1e-9, "RATCHET VIOLÉ"
    summary["nightly"]["iterations_yielded"] = len(kept)
    summary["nightly"].update(engine.last_night_summary)
    summary["elapsed_s"] = round(time.perf_counter() - started, 2)

    print("=== RAPPORT BOUCLE NOCTURNE ===")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

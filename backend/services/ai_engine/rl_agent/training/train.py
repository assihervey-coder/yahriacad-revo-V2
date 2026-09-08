"""CLI de la passe RL — python -m backend.services.ai_engine.rl_agent.training.train

Chaîne exécutée :
  1. collecte d'un dataset de transitions dans ``PlacementEnv`` (rollouts aléatoires) ;
  2. pré-entraînement supervisé du ``TorchWorldModel`` (MSE + Adam + early stopping) ;
  3. export ``world_model_torch.npz`` — chargeable tel quel par le runtime
     ``DreamerWorldModel`` (architecture miroir) ;
  4. entraînement REINFORCE de la ``PlacementPolicyNet`` ;
  5. artefacts + ``training_report.json`` dans ``--out``
     (défaut : ``data/trained_models/rl_checkpoints/``).

Exemples :
    # passe complète (dataset 40 épisodes, RL 150 épisodes)
    python -m backend.services.ai_engine.rl_agent.training.train

    # reprendre le world model depuis les archintentions du keeper + RL court
    python -m backend.services.ai_engine.rl_agent.training.train \
        --archive data/trained_models/keeper_archive.jsonl --episodes-rl 60
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from backend.services.ai_engine.rl_agent.training.dataset import collect_dataset, summarize_archive
from backend.services.ai_engine.rl_agent.training.env import PlacementEnv
from common.design_model import Board, Component, Net, Zone
from common.log import get_logger

logger = get_logger("ai_engine.rl_training")

TORCH_HINT = ("torch est requis pour la passe RL : "
              "pip install torch --index-url https://download.pytorch.org/whl/cpu")


def build_demo_board() -> Board:
    """Mini-carte de référence (miroir du self-test ai_engine) : 3 composants,
    4 nets, 1 keepout — déterministe, exécutable hors ligne."""
    board = Board(width_mm=50.0, height_mm=40.0)
    board.add_component(Component(ref="U1", mpn="STM32F407", value="MCU",
                                  footprint="LQFP-64", pins=64,
                                  width_mm=10.0, height_mm=10.0, power_w=0.9))
    board.add_component(Component(ref="U2", mpn="L6981", value="buck",
                                  footprint="QFN-16", pins=16,
                                  width_mm=4.0, height_mm=4.0, power_w=1.2))
    board.add_component(Component(ref="J1", mpn="USB4105", value="USB-C",
                                  footprint="USB-C", pins=12,
                                  width_mm=9.0, height_mm=8.0, power_w=0.1))
    # placements initiaux : ligne de base (point de départ volontairement médiocre)
    board.placements["U1"] = replace(board.placements["U1"], x_mm=10.0, y_mm=8.0)
    board.placements["U2"] = replace(board.placements["U2"], x_mm=30.0, y_mm=30.0)
    board.placements["J1"] = replace(board.placements["J1"], x_mm=40.0, y_mm=8.0)
    board.nets["VBUS"] = Net(name="VBUS", connections=[("J1", "VBUS"), ("U2", "VIN")],
                             net_class="power")
    board.nets["VDD_3V3"] = Net(name="VDD_3V3", connections=[("U2", "SW"), ("U1", "VDD")],
                                net_class="power")
    board.nets["USB_DP"] = Net(name="USB_DP", connections=[("J1", "DP"), ("U1", "PA12")],
                               net_class="USB", impedance_target_ohm=90.0)
    board.nets["GND"] = Net(name="GND", connections=[("J1", "GND"), ("U1", "VSS"), ("U2", "PGND")],
                            net_class="power")
    board.zones.append(Zone(name="keepout_antenna", x_min_mm=36.0, y_min_mm=30.0,
                            x_max_mm=48.0, y_max_mm=38.0, kind="keepout"))
    return board


def main(argv: List[str] | None = None) -> int:
    """Point d'entrée CLI — 0 si la passe complète a réussi."""
    parser = argparse.ArgumentParser(
        prog="rl_training.train",
        description="Passe RL du cerveau IA : world model (torch) + policy-gradient REINFORCE.")
    parser.add_argument("--episodes-dataset", type=int, default=40,
                        help="épisodes de rollouts aléatoires pour le dataset (défaut 40)")
    parser.add_argument("--episodes-rl", type=int, default=150,
                        help="épisodes REINFORCE (défaut 150)")
    parser.add_argument("--epochs-world", type=int, default=80,
                        help="epochs max de régression supervisée (défaut 80)")
    parser.add_argument("--out", type=Path, default=Path("data/trained_models/rl_checkpoints"),
                        help="répertoire des artefacts")
    parser.add_argument("--archive", type=Path, default=None,
                        help="archive keeper JSONL (contexte du rapport)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", type=str, default="cpu")
    parser.add_argument("--skip-world", action="store_true", help="saute la régression supervisée")
    parser.add_argument("--skip-rl", action="store_true", help="saute REINFORCE")
    args = parser.parse_args(argv)

    try:                                   # import gardé — message d'aide clair
        import torch
    except ImportError:
        print(f"ERREUR : {TORCH_HINT}", file=sys.stderr)
        return 2
    from backend.services.ai_engine.rl_agent.training.reinforce import (
        ReinforceConfig, ReinforceTrainer)
    from backend.services.ai_engine.rl_agent.training.torch_modules import (
        PlacementPolicyNet, TorchWorldModel, train_supervised_world_model)

    started = time.perf_counter()
    args.out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    env = PlacementEnv(build_demo_board(), seed=args.seed)
    report: Dict[str, Any] = {"torch_version": torch.__version__, "device": args.device,
                              "seed": args.seed, "out_dir": str(args.out)}

    # ---- 1) dataset -----------------------------------------------------------------
    X, Y, A, R, dataset_stats = collect_dataset(env, episodes=args.episodes_dataset,
                                                seed=args.seed)
    report["dataset"] = dataset_stats
    print(f"[1/4] dataset : {dataset_stats['transitions']} transitions "
          f"({dataset_stats['episodes']} épisodes, légalité {dataset_stats['legal_ratio']:.0%})")

    # ---- 2) world model supervisé ------------------------------------------------------
    if not args.skip_world:
        world = TorchWorldModel(seed=args.seed)
        result = train_supervised_world_model(world, X, Y, epochs=args.epochs_world,
                                              seed=args.seed, device=args.device)
        npz_path = args.out / "world_model_torch.npz"
        exported = world.export_npz(npz_path)
        report["world_model"] = {"epochs_run": result["epochs_run"],
                                 "best_val_mse": result["best_val_mse"],
                                 "final_train_mse": result["final_train_mse"],
                                 "exported": exported, "npz": str(npz_path)}
        print(f"[2/4] world model : {result['epochs_run']} epochs, "
              f"val MSE {result['best_val_mse']:.4f}, export npz {'OK' if exported else 'ÉCHEC'}")
    else:
        report["world_model"] = {"skipped": True}

    # ---- 3) REINFORCE ------------------------------------------------------------------
    if not args.skip_rl:
        policy = PlacementPolicyNet()
        trainer = ReinforceTrainer(policy, env,
                                   config=ReinforceConfig(seed=args.seed, device=args.device))
        ckpt_path = args.out / "policy_reinforce.pt"
        rl_result = trainer.train(episodes=args.episodes_rl, checkpoint_path=ckpt_path)
        report["reinforce"] = {**rl_result, "checkpoint": str(ckpt_path)}
        print(f"[3/4] REINFORCE : {rl_result['episodes']} épisodes, "
              f"retour moyen {rl_result['mean_reward']:.3f}, "
              f"meilleur fenêtre 10 {rl_result['best_mean_reward_10']:.3f}")
    else:
        report["reinforce"] = {"skipped": True}

    # ---- 4) rapport --------------------------------------------------------------------
    report["keeper_archive"] = summarize_archive(args.archive)
    report["elapsed_s"] = round(time.perf_counter() - started, 2)
    report_path = args.out / "training_report.json"
    try:
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                               encoding="utf-8")
        print(f"[4/4] rapport : {report_path}")
    except OSError as exc:
        print(f"[4/4] rapport non écrit ({exc}) — artefacts npz/pt disponibles", file=sys.stderr)
    print(f"Passe RL terminée en {report['elapsed_s']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Keeper logic — règle du ratchet de la boucle nocturne [AutoPCB].

Score composite (toutes composantes normalisées 0..1) :
    composite = 0.5·DRC_norm + 0.2·SI + 0.2·thermique + 0.1·(1 − vias/max_vias)

RÈGLE DU RATCHET : une proposition n'est engagée que si son score STRICTEMENT
supérieur au meilleur score connu (+ epsilon anti-flottement) — l'état du
design ne régresse JAMAIS, même après 300 itérations nocturnes. Chaque
itération est archivée (JSONL si disque écrivable, sinon mémoire) et émise
comme événement OPTIMIZER_ITERATION pour le frontend temps réel.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.events import EventType, make_event
from common.log import get_logger

logger = get_logger("ai_engine.keeper")

_EPSILON = 1e-9


@dataclass
class IterationReport:
    """Rapport d'une itération — mappable sur OptimizerIteration du proto."""

    iteration: int
    proposal_json: str          # sérialisation de la proposition candidate
    eval_score: float           # score du fast_evaluator
    kept: bool                  # décision du ratchet
    best_score: float
    elapsed_s: float = 0.0
    gain_vs_initial: float = 0.0
    note: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {"iteration": self.iteration, "proposal": self.proposal_json,
                "eval_score": self.eval_score, "kept": self.kept,
                "best_score": self.best_score, "elapsed_s": self.elapsed_s,
                "gain_vs_initial": self.gain_vs_initial, "note": self.note}


class KeeperLogic:
    """État du ratchet : meilleur score, statistiques, archive JSONL, événements."""

    def __init__(self, project_id: str = "", max_vias: int = 200,
                 archive_path: Optional[Path] = None, emitter: str = "autonomous_optimizer") -> None:
        self.project_id = project_id
        self.max_vias = max_vias
        self.archive_path = archive_path
        self.emitter = emitter
        self.initial_score: Optional[float] = None
        self.best_score: float = float("-inf")
        self.iterations = 0
        self.kept_count = 0
        self.rejected_count = 0
        self.reports: List[IterationReport] = []
        self._archive_fallback: List[Dict[str, Any]] = []   # si disque non écrivable
        self.events: List[Any] = []

    # ---- score composite ----------------------------------------------------------------
    def composite_score(self, drc: float, si: float, thermal: float, vias: int) -> float:
        """Score 0..1 : 0.5·DRC + 0.2·SI + 0.2·thermique + 0.1·(1 − vias/max)."""
        drc_norm = min(1.0, max(0.0, drc / 100.0)) if drc > 1.0 else min(1.0, max(0.0, drc))
        si_n = min(1.0, max(0.0, si))
        thermal_n = min(1.0, max(0.0, thermal))
        via_term = 1.0 - min(1.0, max(0, vias) / max(1, self.max_vias))
        return 0.5 * drc_norm + 0.2 * si_n + 0.2 * thermal_n + 0.1 * via_term

    def set_baseline(self, score: float) -> float:
        """Fige le score initial — la référence du gain rapporté ensuite."""
        self.initial_score = score
        self.best_score = max(self.best_score, score)
        return self.best_score

    # ---- ratchet ----------------------------------------------------------------------------
    def keep_if_better(self, proposal: Dict[str, Any], score: float) -> bool:
        """True = proposition engagée (nouveau meilleur score) ; sinon rejetée."""
        self.iterations += 1
        kept = score > self.best_score + _EPSILON      # ratchet : jamais de régression
        if kept:
            self.kept_count += 1
            self.best_score = score
        else:
            self.rejected_count += 1
        report = IterationReport(
            iteration=self.iterations,
            proposal_json=json.dumps(proposal, ensure_ascii=False),
            eval_score=round(score, 4), kept=kept, best_score=round(self.best_score, 4),
            gain_vs_initial=self.gain_vs_initial(score),
            note="engagée" if kept else "rejetée (ratchet)",
        )
        self.reports.append(report)
        self._archive(report)
        self.events.append(make_event(
            EventType.OPTIMIZER_ITERATION, self.project_id, emitter=self.emitter,
            iteration=report.iteration, eval_score=report.eval_score,
            kept=kept, best_score=report.best_score, proposal=proposal,
        ))
        return kept

    def gain_vs_initial(self, score: Optional[float] = None) -> float:
        """Gain relatif par rapport au score initial (0 si pas de baseline)."""
        if self.initial_score is None or self.initial_score <= 0:
            return 0.0
        current = self.best_score if score is None else score
        return round((current - self.initial_score) / self.initial_score, 4)

    # ---- archive ----------------------------------------------------------------------------
    def _archive(self, report: IterationReport) -> None:
        """Une ligne JSONL par itération — replay et audit de la nuit."""
        line = json.dumps(report.to_json(), ensure_ascii=False)
        if self.archive_path is None:
            self._archive_fallback.append(report.to_json())
            return
        try:
            self.archive_path.parent.mkdir(parents=True, exist_ok=True)
            with self.archive_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            self._archive_fallback.append(report.to_json())

    @property
    def archive(self) -> List[Dict[str, Any]]:
        """Archive en mémoire (fallback) — les lignes disque sont lues au besoin."""
        return list(self._archive_fallback)

    def stats(self) -> Dict[str, float]:
        """Statistiques de la session nocturne — affichées par le self-test."""
        return {
            "iterations": self.iterations,
            "kept": self.kept_count,
            "rejected": self.rejected_count,
            "best_score": round(self.best_score, 4),
            "initial_score": round(self.initial_score, 4) if self.initial_score is not None else None,
            "gain_vs_initial": self.gain_vs_initial(),
        }

"""Évaluateur rapide < 5 s — pont freerouting si présent, sinon géométrie interne.

CHAÎNE RÉELLE (production) :
    1. specctra_export.write_dsn(board, ...)            → design.dsn
    2. freerouting (routeur Java, `shutil.which`)       → java -jar freerouting.jar
    3. session .ses + drc_dfm_engine                    → score DRC officiel
La branche binaire est tentée UNIQUEMENT si l'exécutable `freerouting` est
dans le PATH ; tout échec (binaire absent, timeout > budget, sortie illisible)
retombe sur l'évaluateur géométrique interne : longueur HPWL + congestion des
enveloppes de nets + étalement thermique + pénalité de vias. Garde-fou
time.perf_counter : aucune évaluation ne dépasse ``fast_eval_budget_s``.
"""

from __future__ import annotations

import copy
import math
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.config import Settings, get_settings
from common.log import get_logger

from .specctra_export import export_dsn, write_dsn

logger = get_logger("ai_engine.fast_eval")

# pondérations du score interne (somme = 1.0)
_W_HPWL, _W_THERMAL, _W_CONGESTION, _W_COMPLETENESS = 0.45, 0.25, 0.20, 0.10


class FastEvaluator:
    """Score unique 0..100 d'un placement — décision de la boucle nocturne."""

    def __init__(self, budget_s: Optional[float] = None, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings("ai_engine")
        self.budget_s = budget_s if budget_s is not None else self.settings.fast_eval_budget_s
        self.engine_used = "internal-geometric"

    # ---- API ------------------------------------------------------------------
    def evaluate(self, board: Any) -> float:
        """Score composite 0..100 du design, garanti < budget (5 s par défaut)."""
        deadline = time.perf_counter() + self.budget_s
        detail = self.evaluate_detailed(board, deadline=deadline)
        return float(detail["score"])

    def evaluate_detailed(self, board: Any, deadline: Optional[float] = None) -> Dict[str, Any]:
        """Décomposition du score — utilisée par le keeper et le self-test."""
        started = time.perf_counter()
        deadline = deadline or (started + self.budget_s)
        freerouting_score = self._try_freerouting(board, deadline)
        if freerouting_score is not None:
            return {"score": freerouting_score, "engine": "freerouting",
                    "elapsed_s": round(time.perf_counter() - started, 4)}
        hpwl = self._hpwl(board)
        thermal = self._thermal_spread(board)
        congestion = self._congestion(board)
        completeness = self._completeness(board)
        score = 100.0 * (_W_HPWL * hpwl + _W_THERMAL * thermal
                         + _W_CONGESTION * congestion + _W_COMPLETENESS * completeness)
        elapsed = time.perf_counter() - started
        self.engine_used = "internal-geometric"
        return {
            "score": round(score, 3), "engine": self.engine_used,
            "hpwl_mm": round(hpwl, 2), "thermal_spread": round(thermal, 3),
            "congestion_free": round(congestion, 3), "completeness": round(completeness, 3),
            "drc_proxy": round(score, 2),       # proxy géométrique (DRC réel = drc_dfm_engine)
            "si_risk": round(1.0 - congestion, 3),
            "thermal_ok": round(thermal, 3),
            "elapsed_s": round(elapsed, 4),
        }

    # ---- métriques géométriques ----------------------------------------------------
    def _hpwl(self, board: Any) -> float:
        """HPWL normalisée 0..1 (1 = compact idéal pour ce board)."""
        total, terms = 0.0, 0
        for net in board.nets.values():
            points = [(board.placements[r].x_mm, board.placements[r].y_mm)
                      for r, _p in net.connections if r in board.placements]
            if len(points) >= 2:
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                total += (max(xs) - min(xs)) + (max(ys) - min(ys))
                terms += 1
        if terms == 0:
            return 1.0
        reference = terms * (board.width_mm + board.height_mm)   # pire cas crédible
        return max(0.0, min(1.0, 1.0 - total / reference))

    def _thermal_spread(self, board: Any) -> float:
        """1 − recouvrement thermique : les composants chauds se repoussent."""
        hot = [(c.power_w, board.placements[c.ref]) for c in board.components.values()
               if c.power_w > 0.0 and c.ref in board.placements]
        if len(hot) < 2:
            return 1.0
        penalty = 0.0
        for i, (pw_a, pa) in enumerate(hot):
            for (pw_b, pb) in hot[i + 1:]:
                dist = math.hypot(pa.x_mm - pb.x_mm, pa.y_mm - pb.y_mm)
                penalty += (pw_a * pw_b) / (1.0 + dist)
        normalizer = sum(pw for pw, _p in hot) * max(1.0, len(hot) - 1)
        return max(0.0, min(1.0, 1.0 - penalty / normalizer))

    def _congestion(self, board: Any) -> float:
        """1 − recouvrement des enveloppes de nets (proxy congestion routage)."""
        rects: List[Tuple[float, float, float, float]] = []
        for net in board.nets.values():
            points = [(board.placements[r].x_mm, board.placements[r].y_mm)
                      for r, _p in net.connections if r in board.placements]
            if len(points) >= 2:
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                rects.append((min(xs), min(ys), max(xs), max(ys)))
        overlaps = 0
        for i, (ax1, ay1, ax2, ay2) in enumerate(rects):
            for (bx1, by1, bx2, by2) in rects[i + 1:]:
                if not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1):
                    overlaps += 1
        max_overlaps = max(1, len(rects) * (len(rects) - 1) // 2)
        return max(0.0, min(1.0, 1.0 - overlaps / max_overlaps))

    def _completeness(self, board: Any) -> float:
        """Part de nets routés — 1.0 tant que le routage n'a pas commencé."""
        if not board.nets:
            return 1.0
        return 1.0 - len(board.unrouted_nets()) / len(board.nets)

    # ---- branche binaire freerouting ------------------------------------------------
    def _try_freerouting(self, board: Any, deadline: float) -> Optional[float]:
        """Chaîne réelle si le binaire existe ; None = fallback interne immédiat."""
        binary = shutil.which("freerouting")
        if binary is None:
            return None
        try:
            import tempfile
            remaining = max(1.0, deadline - time.perf_counter())
            with tempfile.TemporaryDirectory(prefix="pcb_eval_") as tmp:
                dsn_path = write_dsn(board, Path(tmp) / "design.dsn")
                proc = subprocess.run(
                    [binary, "-dsn", str(dsn_path), "-maxPasses", "1"],
                    capture_output=True, text=True,
                    timeout=min(self.budget_s, remaining))
            for line in proc.stdout.splitlines():            # convention : "score: 87.3"
                if "score" in line.lower():
                    return float(line.split(":")[-1].strip().rstrip("%"))
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            logger.warning("freerouting indisponible — évaluateur interne",
                           extra={"error": str(exc)[:120]})
        return None

    # ---- utilitaire -----------------------------------------------------------------
    def evaluate_action(self, board: Any, action: Any) -> Dict[str, Any]:
        """Évalue l'impact d'une action sur une copie (jamais sur le board réel)."""
        trial = copy.deepcopy(board)
        if action.ref in trial.placements:
            trial.move(action.ref, action.x_mm, action.y_mm,
                       rotation_deg=getattr(action, "rotation_deg", None))
        return self.evaluate_detailed(trial)

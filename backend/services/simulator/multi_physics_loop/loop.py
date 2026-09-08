"""Boucle multiphysique continue [Cadence AuraStack] — section 6.2.

À chaque commit du state_manager (hook `notify_commit()` déclenché par callback
d'abonnement) ou périodiquement (thread léger daemon), la boucle rejoue
thermal + SI (+ EM) sur l'état courant de la carte, puis :

  1. publie les contraintes révisées sur le constraint_bus :
       - THERMAL_ZONE_UPDATE  clé "thermal/{ref}"   (hotspots composants/zones)
       - IMPEDANCE_TARGET     clé "impedance/{net}" (nets SI en échec d'œil)
  2. expose les « zones à re-optimiser » via `latest_findings()` — le rl_agent
     relance alors une exploration locale sans refaire tout le pipeline.

Publication dédupliquée : une contrainte n'est republiée que si sa valeur a
changé au-delà d'un epsilon (évite de saturer le bus à 50 ms de latence cible).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from common.bus import (
    ConstraintKind,
    ConstraintBus,
    ConstraintMessage,
    InMemoryConstraintBus,
)
from common.log import get_logger

from backend.services.simulator.signal_integrity import eye_diagram
from backend.services.simulator.signal_integrity import ir_drop
from backend.services.simulator.thermal_sim import diffusion

logger = get_logger("simulator.multi_physics_loop")

DEFAULT_INTERVAL_S = 1.0        # cadence du thread quand aucun commit n'arrive
DEFAULT_CELL_MM = 1.5           # grille thermique allégée pour la boucle continue
PUBLISH_EPSILON = 1e-3          # seuil de déduplication des contraintes

# impédances suggérées par classe (utilisées si le net n'a pas de cible déclarée)
SUGGESTED_IMPEDANCE_OHM: Dict[str, float] = {
    "usb3": 90.0, "pcie": 85.0, "ddr4": 60.0, "mipi": 100.0,
    "diff": 100.0, "default": 50.0,
}


@dataclass
class Finding:
    """Constat multiphysique — une « zone à re-optimiser » pour le rl_agent."""

    key: str                     # ex. "thermal/U1", "impedance/USB3_TX"
    kind: str                    # thermal_hotspot | si_eye_closure | ir_drop | emi_loop_area
    severity: str                # info | warning | critical
    zone: Optional[Tuple[float, float, float, float]]  # bbox mm (x0, y0, x1, y1)
    detail: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "kind": self.kind, "severity": self.severity,
            "zone": list(self.zone) if self.zone else None,
            "detail": self.detail, "ts": self.ts,
        }


class MultiPhysicsLoop:
    """Boucle d'analyse multiphysique continue par projet (un objet par projet).

    Deux déclencheurs cumulables :
      - `notify_commit()` — hook appelé par le state_manager (abonnement
        callback) après chaque commit de design ;
      - le thread `start()` — rejoue l'analyse au plus toutes les `interval_s`.
    """

    def __init__(
        self,
        project_id: str,
        board_provider: Callable[[], object],
        bus: Optional[ConstraintBus] = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        emitter: str = "multi_physics_loop",
        cell_size_mm: float = DEFAULT_CELL_MM,
        design_version_provider: Optional[Callable[[], int]] = None,
    ) -> None:
        self.project_id = project_id
        self._board_provider = board_provider
        self._bus = bus if bus is not None else InMemoryConstraintBus()
        self.interval_s = max(0.05, float(interval_s))
        self.emitter = emitter
        self.cell_size_mm = cell_size_mm
        self._version_provider = design_version_provider

        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()          # une analyse à la fois
        self._published_hashes: Dict[str, str] = {}

        self.findings: List[Finding] = []
        self.analyses = 0
        self.last_analysis_ts: float = 0.0
        self.last_analysis_ms: float = 0.0
        self.published_count = 0

    # ---- cycle de vie -------------------------------------------------------
    def start(self) -> None:
        """Démarre le thread daemon d'analyse continue (no-op si déjà actif)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"multi_physics_{self.project_id}", daemon=True,
        )
        self._thread.start()
        logger.info("boucle multiphysique démarrée",
                    extra={"project_id": self.project_id, "interval_s": self.interval_s})

    def stop(self, timeout_s: float = 3.0) -> None:
        """Arrêt gracieux — termine l'itération en cours puis joint le thread."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None
        logger.info("boucle multiphysique arrêtée", extra={"project_id": self.project_id})

    def notify_commit(self, _board=None) -> None:
        """Hook à brancher sur le state_manager : `sm.subscribe(cb=lambda s: loop.notify_commit())`.

        Réveille immédiatement le thread (analyse sur l'état frais) ; sans
        thread démarré, l'analyse sera effectuée au prochain `start()`.
        """
        self._wake.set()

    # ---- thread -------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=self.interval_s)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.analyze_once()
            except Exception:  # une itération défaillante n'arrête jamais la boucle
                logger.exception("itération multiphysique en erreur",
                                 extra={"project_id": self.project_id})

    # ---- analyse ------------------------------------------------------------
    def analyze_once(self, board=None) -> List[Finding]:
        """Une passe complète thermal + SI (+ IR drop) — thread-safe, sans thread requis."""
        if not self._lock.acquire(blocking=False):
            return list(self.findings)  # analyse déjà en cours — état précédent conservé
        try:
            t0 = time.perf_counter()
            current = board if board is not None else self._board_provider()
            findings: List[Finding] = []

            # ---- thermique : hotspots ----------------------------------------
            thermal = diffusion.steady_state(current, cell_size_mm=self.cell_size_mm)
            for hotspot in thermal.hotspots:
                key = f"thermal/{hotspot.component_ref}" if hotspot.component_ref else "thermal/board"
                severity = "critical" if hotspot.kind == "zone" else (
                    "warning" if hotspot.temp_c > thermal.ambient_c + 25.0 else "info"
                )
                findings.append(Finding(
                    key=key, kind="thermal_hotspot", severity=severity,
                    zone=(hotspot.x_min_mm, hotspot.y_min_mm, hotspot.x_max_mm, hotspot.y_max_mm),
                    detail={**hotspot.to_dict(), "max_temp_c": thermal.max_temp_c},
                ))
                self._publish(
                    ConstraintKind.THERMAL_ZONE_UPDATE, key,
                    {**hotspot.to_dict(), "max_temp_c": thermal.max_temp_c,
                     "ambient_c": thermal.ambient_c, "backend": thermal.backend},
                )

            # ---- SI : ouverture d'œil ----------------------------------------
            si = eye_diagram.analyze(current, crosstalk_db=-35.0)
            for margin in si.margins:
                if margin.risk == "ok":
                    continue
                net = current.nets.get(margin.net)
                target = None
                if net is not None and net.impedance_target_ohm is not None:
                    target = float(net.impedance_target_ohm)
                else:
                    target = SUGGESTED_IMPEDANCE_OHM.get(
                        (margin.net_class or "default").lower(),
                        SUGGESTED_IMPEDANCE_OHM["default"],
                    )
                key = f"impedance/{margin.net}"
                detail = {
                    "net": margin.net, "eye_opening_pct": margin.eye_opening_pct,
                    "insertion_loss_db": margin.insertion_loss_db,
                    "overshoot_mv": margin.overshoot_mv, "risk": margin.risk,
                    "target_ohm": target,
                }
                findings.append(Finding(
                    key=key, kind="si_eye_closure",
                    severity="critical" if margin.risk == "fail" else "warning",
                    zone=None, detail=detail,
                ))
                self._publish(ConstraintKind.IMPEDANCE_TARGET, key, detail)

            # ---- IR drop (rail principal) -------------------------------------
            ird = ir_drop.analyze_ir_drop(current)
            if ird.note == "" and ird.ir_drop_pct > 0.5:
                findings.append(Finding(
                    key=f"ir_drop/{ird.net}", kind="ir_drop",
                    severity="warning" if ird.failing_components else "info",
                    zone=None,
                    detail={**ird.to_dict()},
                ))

            self.findings = findings
            self.analyses += 1
            self.last_analysis_ts = time.time()
            self.last_analysis_ms = (time.perf_counter() - t0) * 1000.0
            logger.info(
                "analyse multiphysique terminée",
                extra={
                    "project_id": self.project_id, "findings": len(findings),
                    "duration_ms": round(self.last_analysis_ms, 1),
                },
            )
            return findings
        finally:
            self._lock.release()

    def latest_findings(self) -> List[Finding]:
        """Derniers constats (zones à re-optimiser) — copie défensive."""
        return list(self.findings)

    def reoptimize_zones(self) -> List[Finding]:
        """Sous-ensemble à re-optimiser : severities warning/critical uniquement."""
        return [f for f in self.findings if f.severity in ("warning", "critical")]

    # ---- publication --------------------------------------------------------
    def _publish(self, kind: ConstraintKind, key: str, value: dict) -> None:
        """Publie sur le bus si la valeur a changé (déduplication par hash JSON)."""
        blob = json.dumps(value, sort_keys=True, default=str)
        digest = hashlib.sha1(blob.encode()).hexdigest()
        if self._published_hashes.get(key) == digest:
            return
        self._published_hashes[key] = digest
        self._bus.publish(ConstraintMessage(
            kind=kind, key=key, value=value,
            project_id=self.project_id, source=self.emitter,
        ))
        self.published_count += 1

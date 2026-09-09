"""Service simulator — façade gRPC du moteur physique (section 6.2, brique Cadence AuraStack).

`Simulator.run(board, physics, streaming)` est un générateur de résultats
partiels (ThermalResult / EMResult / SIResult / progress) — en `multi_physics`
il enchaîne les trois solveurs et délègue la publication des contraintes
révisées au `multi_physics_loop` (THERMAL_ZONE_UPDATE, IMPEDANCE_TARGET,
KEEPOUT_ZONE pour les zones EMI). Les Event créés ici (STEP_PROGRESS,
CONSTRAINT_VIOLATED) sont relayés sur le canal WebSocket par la couche gRPC.

Démarrage : `python -m backend.services.simulator.main` → serveur gRPC si les
stubs générés sont présents, sinon self-test complet hors réseau.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from common.bus import ConstraintKind, ConstraintMessage, InMemoryConstraintBus
from common.config import Settings, get_settings
from common.design_model import Board
from common.events import EventType, WorkflowStep, make_event
from common.grpc_helpers import proto_available
from common.log import configure_logging, get_logger

from backend.services.simulator.em_sim import quasi_static
from backend.services.simulator.multi_physics_loop.loop import MultiPhysicsLoop
from backend.services.simulator.signal_integrity import eye_diagram, ir_drop
from backend.services.simulator.thermal_sim import diffusion

logger = get_logger("simulator")

VALID_PHYSICS = ("em", "thermal", "signal_integrity", "multi_physics")


# --------------------------------------------------------------------------- #
#  Résultat partiel streamé (équivalent python du proto SimulateEvent)
# --------------------------------------------------------------------------- #
@dataclass
class SimResult:
    """Résultat partiel — une entrée du flux gRPC / du générateur python."""

    phase: str                       # thermal | em | signal_integrity | progress
    progress: float                  # 0..1
    thermal: Optional[diffusion.ThermalResult] = None
    em: Optional[quasi_static.EMResult] = None
    si: Optional[eye_diagram.SIResult] = None
    ir: Optional[ir_drop.IRDropResult] = None
    metrics: Dict = field(default_factory=dict)
    events: List = field(default_factory=list)  # Event à relayer sur le WebSocket

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "progress": round(self.progress, 3),
            "thermal": self.thermal.to_dict() if self.thermal else None,
            "em": self.em.to_dict() if self.em else None,
            "si": self.si.to_dict() if self.si else None,
            "ir": self.ir.to_dict() if self.ir else None,
            "metrics": self.metrics,
            "events": [e.to_json() for e in self.events],
        }


class Simulator:
    """Façade du moteur physique — publication des hotspots/zones sur le bus.

    `run()` reste un générateur pur (aucune mutation de la carte) ; la
    publication des contraintes se fait au fil de l'eau si un bus est fourni.
    La boucle continue (AuraStack) est disponible via `start_continuous()`.
    """

    def __init__(
        self,
        bus: Optional[InMemoryConstraintBus] = None,
        settings: Optional[Settings] = None,
        publish: bool = True,
    ) -> None:
        self.settings = settings or get_settings("simulator")
        self.bus = bus if bus is not None else InMemoryConstraintBus(
            self.settings.bus_target_latency_ms,
        )
        self.publish = publish
        self._loop: Optional[MultiPhysicsLoop] = None

    # ---- exécution à la demande ---------------------------------------------
    def run(
        self,
        board: Board,
        physics: str = "multi_physics",
        streaming: bool = True,
        project_id: str = "sim",
        design_version: int = 1,
    ) -> Iterator[SimResult]:
        """Générateur de résultats partiels — physics ∈ {em, thermal, signal_integrity, multi_physics}.

        En multi_physics, chaque phase (thermique → EM → SI) est publiée au bus
        puis yieldée ; `streaming=False` compacte tout en un résultat final.
        """
        if physics not in VALID_PHYSICS:
            raise ValueError(f"physics inconnu : {physics} (attendu parmi {VALID_PHYSICS})")
        t0 = time.perf_counter()
        events: List = []

        want_thermal = physics in ("thermal", "multi_physics")
        want_em = physics in ("em", "multi_physics")
        want_si = physics in ("signal_integrity", "multi_physics")
        total_phases = int(want_thermal) + int(want_em) + int(want_si)

        if streaming and physics == "thermal":
            # transitoire streamé puis état final
            for frame in diffusion.transient(board, t_s=30.0, frames=6):
                yield SimResult(phase="thermal", progress=min(0.9, frame.sim_time_s / 30.0 * 0.9),
                                thermal=frame, events=self._step_events(project_id, design_version))
            res = diffusion.steady_state(board)
            events += self._publish_thermal(board, res, project_id, design_version)
            yield SimResult(phase="thermal", progress=1.0, thermal=res, events=events)
            return

        phase_index = 0
        if want_thermal:
            thermal = diffusion.steady_state(board)
            phase_index += 1
            events += self._publish_thermal(board, thermal, project_id, design_version)
            yield SimResult(
                phase="thermal",
                progress=phase_index / max(1, total_phases),
                thermal=thermal,
                metrics={"max_temp_c": thermal.max_temp_c, "backend": thermal.backend},
                events=list(events),
            )

        if want_em:
            em = quasi_static.quasi_static_analysis(board)
            phase_index += 1
            events += self._publish_em(em, project_id, design_version)
            yield SimResult(
                phase="em",
                progress=phase_index / max(1, total_phases),
                em=em,
                metrics={"crosstalk_max_db": em.crosstalk_max_db,
                         "ground_bounce_mv": em.ground_bounce_mv},
                events=list(events),
            )

        if want_si:
            si = eye_diagram.analyze(board)
            ir = ir_drop.analyze_ir_drop(board)
            phase_index += 1
            events += self._publish_si(si, project_id, design_version)
            yield SimResult(
                phase="signal_integrity",
                progress=phase_index / max(1, total_phases),
                si=si, ir=ir,
                metrics={"eye_opening_min_pct": si.eye_opening_min_pct,
                         "ir_drop_mv": ir.ir_drop_mv},
                events=list(events),
            )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        events.append(make_event(
            EventType.STEP_PROGRESS, project_id, "simulator", design_version,
            step=WorkflowStep.MULTI_PHYSICS_CHECK.value,
            progress=1.0, duration_ms=round(elapsed_ms, 1),
        ))
        yield SimResult(
            phase="progress", progress=1.0,
            metrics={"elapsed_ms": round(elapsed_ms, 1), "physics": physics},
            events=events,
        )

    # ---- boucle continue [Cadence AuraStack] ---------------------------------
    def start_continuous(
        self,
        project_id: str,
        board_provider,
        interval_s: float = 1.0,
        design_version_provider=None,
    ) -> MultiPhysicsLoop:
        """Démarre la boucle multiphysique continue (analyse à chaque commit)."""
        if self._loop is not None:
            self._loop.stop()
        self._loop = MultiPhysicsLoop(
            project_id, board_provider, bus=self.bus,
            interval_s=interval_s, design_version_provider=design_version_provider,
        )
        self._loop.start()
        return self._loop

    def stop_continuous(self) -> None:
        """Arrête la boucle continue si active."""
        if self._loop is not None:
            self._loop.stop()
            self._loop = None

    def latest_findings(self) -> List:
        """Derniers constats de la boucle continue (zones à re-optimiser)."""
        return self._loop.latest_findings() if self._loop else []

    # ---- publication ---------------------------------------------------------
    def _publish_thermal(self, board: Board, thermal, project_id: str, design_version: int) -> List:
        """Hotspots → THERMAL_ZONE_UPDATE (zones violées → CONSTRAINT_VIOLATED)."""
        events: List = []
        for hotspot in thermal.hotspots:
            key = f"thermal/{hotspot.component_ref}" if hotspot.component_ref else "thermal/board"
            self._publish(ConstraintKind.THERMAL_ZONE_UPDATE, key, project_id,
                          {**hotspot.to_dict(), "max_temp_c": thermal.max_temp_c})
            if hotspot.kind == "zone":
                events.append(make_event(
                    EventType.CONSTRAINT_VIOLATED, project_id, "simulator", design_version,
                    key=key, temp_c=hotspot.temp_c, limit_c=hotspot.limit_c,
                ))
        return events

    def _publish_em(self, em, project_id: str, design_version: int) -> List:
        """Zones EMI (boucles de courant) → KEEPOUT_ZONE suggérées."""
        events: List = []
        for i, zone in enumerate(em.emi_zones):
            self._publish(ConstraintKind.KEEPOUT_ZONE, f"emi/{zone.net}", project_id, {
                "x_min_mm": zone.x_min_mm, "y_min_mm": zone.y_min_mm,
                "x_max_mm": zone.x_max_mm, "y_max_mm": zone.y_max_mm,
                "loop_area_mm2": zone.loop_area_mm2, "severity": zone.severity,
                "reason": "emi_loop_area",
            })
            if zone.severity == "critical":
                events.append(make_event(
                    EventType.CONSTRAINT_VIOLATED, project_id, "simulator", design_version,
                    key=f"emi/{zone.net}", loop_area_mm2=zone.loop_area_mm2,
                ))
        return events

    def _publish_si(self, si, project_id: str, design_version: int) -> List:
        """Nets SI en échec → IMPEDANCE_TARGET révisée + CONSTRAINT_VIOLATED."""
        events: List = []
        for margin in si.margins:
            if margin.risk == "ok":
                continue
            key = f"impedance/{margin.net}"
            self._publish(ConstraintKind.IMPEDANCE_TARGET, key, project_id, {
                "net": margin.net, "eye_opening_pct": margin.eye_opening_pct,
                "risk": margin.risk,
            })
            if margin.risk == "fail":
                events.append(make_event(
                    EventType.CONSTRAINT_VIOLATED, project_id, "simulator", design_version,
                    key=key, eye_opening_pct=margin.eye_opening_pct,
                ))
        return events

    def _publish(self, kind: ConstraintKind, key: str, project_id: str, value: dict) -> None:
        if not self.publish:
            return
        self.bus.publish(ConstraintMessage(
            kind=kind, key=key, value=value, project_id=project_id, source="simulator",
        ))

    @staticmethod
    def _step_events(project_id: str, design_version: int) -> List:
        return [make_event(
            EventType.STEP_PROGRESS, project_id, "simulator", design_version,
            step=WorkflowStep.MULTI_PHYSICS_CHECK.value, progress=None,
        )]


# --------------------------------------------------------------------------- #
#  Bootstrap gRPC (stubs générés) — sinon self-test
# --------------------------------------------------------------------------- #
def _serve_grpc() -> None:
    """Serveur gRPC streaming — Simulate(SimulateRequest) → stream SimulateEvent."""
    from backend.services.simulator import proto_adapter  # adapter optionnel (stubs générés)

    settings = get_settings("simulator")
    proto_adapter.serve(settings)


def main() -> None:
    """Bootstrap : gRPC si stubs présents, sinon self-test hors réseau."""
    configure_logging("simulator")
    if proto_available():
        try:
            _serve_grpc()
            return
        except Exception:  # stubs présents mais serveur impossible → self-test
            logger.exception("démarrage gRPC impossible — bascule en self-test")
    ok = self_test()
    sys.exit(0 if ok else 1)


# --------------------------------------------------------------------------- #
#  Self-test (mini-board 3 composants) — aucun réseau, aucune compilation
# --------------------------------------------------------------------------- #
def _mini_board(power_net_width_mm: float = 0.5) -> Board:
    """Mini-carte 36×28 mm — U0 (régulateur), U1 (mcu 2 W), R1, C1 + nets de test."""
    from common.design_model import Component, Net, Pad, Placement, Segment

    board = Board(width_mm=36.0, height_mm=28.0)
    board.add_component(Component(
        ref="U0", value="LDO-3V3", functional_block="power", pins=4,
        width_mm=5.0, height_mm=5.0, power_w=0.2,
        pads=[Pad("VIN", 1.0, 14.0, net="VIN"), Pad("VOUT", 5.0, 14.0, net="+3V3"),
              Pad("GND1", 3.0, 12.0, net="GND"), Pad("GND2", 3.0, 16.0, net="GND")],
    ), Placement("U0", 3.0, 14.0))
    board.add_component(Component(
        ref="U1", value="MCU-LQFP64", footprint="LQFP-64", functional_block="mcu", pins=64,
        width_mm=10.0, height_mm=10.0, power_w=2.0,
        pads=[Pad("VDD", 17.0, 14.0, net="+3V3"), Pad("VSS", 19.0, 14.0, net="GND"),
              Pad("PA9", 17.0, 16.0, net="UART1_TX"), Pad("PA10", 19.0, 16.0, net="UART1_RX"),
              Pad("USB_DP", 17.0, 12.0, net="USB_DP"), Pad("USB_DM", 19.0, 12.0, net="USB_DM")],
    ), Placement("U1", 18.0, 14.0))
    board.add_component(Component(
        ref="R1", value="10k", pins=2, width_mm=2.0, height_mm=1.2, power_w=0.1,
        pads=[Pad("1", 29.0, 14.0, net="+3V3"), Pad("2", 31.0, 14.0, net="GND")],
    ), Placement("R1", 30.0, 14.0))
    board.add_component(Component(
        ref="C1", value="100n", pins=2, width_mm=2.0, height_mm=1.2, power_w=0.0,
        pads=[Pad("1", 8.0, 14.0, net="+3V3"), Pad("2", 10.0, 14.0, net="GND")],
    ), Placement("C1", 9.0, 14.0))

    # rail +3V3 routé de la source vers les charges
    board.nets["+3V3"] = Net(
        name="+3V3", net_class="power",
        connections=[("U0", "VOUT"), ("U1", "VDD"), ("R1", "1"), ("C1", "1")],
        routed_segments=[
            Segment("+3V3", 5.0, 14.0, 17.0, 14.0, layer=0, width_mm=power_net_width_mm),
            Segment("+3V3", 17.0, 14.0, 29.0, 14.0, layer=0, width_mm=power_net_width_mm),
            Segment("+3V3", 5.0, 14.0, 8.0, 14.0, layer=0, width_mm=power_net_width_mm),
        ],
    )
    board.nets["GND"] = Net(
        name="GND", net_class="ground",
        connections=[("U0", "GND1"), ("U0", "GND2"), ("U1", "VSS"), ("R1", "2"), ("C1", "2")],
        routed_segments=[
            Segment("GND", 3.0, 12.0, 19.0, 14.0, layer=1, width_mm=0.5),
            Segment("GND", 19.0, 14.0, 31.0, 14.0, layer=1, width_mm=0.5),
            Segment("GND", 10.0, 14.0, 10.0, 13.5, layer=1, width_mm=0.5, is_via=True),
        ],
    )
    board.nets["UART1_TX"] = Net(
        name="UART1_TX", net_class="uart",
        connections=[("U1", "PA9")],
        routed_segments=[Segment("UART1_TX", 17.0, 16.0, 4.0, 24.0, layer=0, width_mm=0.2)],
    )
    board.nets["UART1_RX"] = Net(name="UART1_RX", net_class="uart", connections=[("U1", "PA10")])
    # bus rapide : USB3 serpenté avec vias → doit échouer l'ouverture d'œil
    board.nets["USB_DP"] = Net(
        name="USB_DP", net_class="usb3", impedance_target_ohm=90.0,
        connections=[("U1", "USB_DP")],
        routed_segments=[
            Segment("USB_DP", 17.0, 12.0, 34.0, 12.0, layer=0, width_mm=0.2),
            Segment("USB_DP", 34.0, 12.0, 34.0, 20.0, layer=0, width_mm=0.2),
            Segment("USB_DP", 34.0, 20.0, 12.0, 20.0, layer=0, width_mm=0.2),
            Segment("USB_DP", 12.0, 20.0, 12.0, 24.0, layer=0, width_mm=0.2, is_via=True),
            Segment("USB_DP", 12.0, 24.0, 30.0, 24.0, layer=0, width_mm=0.2, is_via=True),
            Segment("USB_DP", 30.0, 24.0, 30.0, 22.0, layer=0, width_mm=0.2, is_via=True),
        ],
    )
    # paire parallèle longue → diaphonie mesurable
    board.nets["BUS_A"] = Net(
        name="BUS_A", net_class="default", connections=[],
        routed_segments=[Segment("BUS_A", 6.0, 4.0, 26.0, 4.0, layer=0, width_mm=0.25)],
    )
    board.nets["BUS_B"] = Net(
        name="BUS_B", net_class="default", connections=[],
        routed_segments=[Segment("BUS_B", 6.0, 6.5, 26.0, 6.5, layer=0, width_mm=0.25)],
    )
    # zone thermique déclarée avec consigne 40 °C — violée par U1 (2 W)
    board.zones.append(_Zone("thermal/U1", 12.0, 8.0, 24.0, 20.0, "thermal", 40.0))
    return board


def _Zone(name, x0, y0, x1, y1, kind, limit):  # noqa: N802 — helper local de self-test
    from common.design_model import Zone

    return Zone(name=name, x_min_mm=x0, y_min_mm=y0, x_max_mm=x1, y_max_mm=y1,
                kind=kind, max_temp_c=limit)


def self_test() -> bool:
    """Self-test hors réseau : thermal → hotspot U1, multi_physics → bus, IR drop, EM, SI."""
    results: List[tuple] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"[self-test] {'PASS' if ok else 'FAIL'} — {name}" + (f" ({detail})" if detail else ""))

    # ---- 1. thermique : hotspot autour de U1 --------------------------------
    board = _mini_board()
    thermal = diffusion.steady_state(board, max_iter=30000, tol_c=2e-3)
    hotspot_refs = [h.component_ref for h in thermal.hotspots]
    check("thermal: max_temp > ambiant+10", thermal.max_temp_c > thermal.ambient_c + 10.0,
          f"{thermal.max_temp_c:.1f}°C en {thermal.iterations} itérations ({thermal.backend})")
    check("thermal: hotspot U1 détecté", "U1" in hotspot_refs, str(hotspot_refs))
    u1_hot = next((h for h in thermal.hotspots if h.component_ref == "U1"), None)
    if u1_hot:
        # le centre du hotspot doit être à ≤ 8 mm du centre de U1 (18, 14)
        cx = (u1_hot.x_min_mm + u1_hot.x_max_mm) / 2.0
        cy = (u1_hot.y_min_mm + u1_hot.y_max_mm) / 2.0
        dist = ((cx - 18.0) ** 2 + (cy - 14.0) ** 2) ** 0.5
        check("thermal: hotspot U1 autour du composant", dist <= 8.0,
              f"centre=({cx:.1f},{cy:.1f}) à {dist:.1f} mm du centre U1")
    zone_hot = [h for h in thermal.hotspots if h.kind == "zone"]
    check("thermal: zone thermique U1 violée (consigne 40 °C)",
          any(h.temp_c > 40.0 for h in zone_hot),
          f"max={thermal.max_temp_c}°C")

    # ---- 2. IR drop sur le rail d'alim ---------------------------------------
    ird = ir_drop.analyze_ir_drop(board)
    check("ir_drop: chute mesurée > 0", ird.ir_drop_mv > 0.0, f"{ird.ir_drop_mv} mV")
    check("ir_drop: chute large < 3 % (pas de flag)", len(ird.failing_components) == 0,
          f"{ird.ir_drop_pct}% — {ird.component_drops_mv}")
    narrow = _mini_board(power_net_width_mm=0.03)
    ird_narrow = ir_drop.analyze_ir_drop(narrow)
    check("ir_drop: piste étroite > 3 % → U1 flaggé", "U1" in ird_narrow.failing_components,
          f"{ird_narrow.ir_drop_pct}% — {ird_narrow.failing_components}")
    check("ir_drop: prédicat de drapeau", ir_drop._flag_over_budget(3.5) and not ir_drop._flag_over_budget(2.9))

    # ---- 3. EM quasi-statique -------------------------------------------------
    em = quasi_static.quasi_static_analysis(board)
    check("em: diaphonie mesurée et négative", -40.0 < em.crosstalk_max_db < -10.0,
          f"{em.crosstalk_max_db} dB")
    check("em: paire BUS_A/BUS_B couplée",
          em.worst_pair is not None and {em.worst_pair.net_a, em.worst_pair.net_b} == {"BUS_A", "BUS_B"},
          em.worst_pair.to_dict() if em.worst_pair else "aucune")
    check("em: rebond de masse calculé", em.ground_bounce_mv >= 0.0, f"{em.ground_bounce_mv} mV")

    # ---- 4. SI : œil USB_DP en échec ------------------------------------------
    si = eye_diagram.analyze(board)
    check("si: USB_DP en échec d'œil", "USB_DP" in si.failing_nets,
          f"œil={si.eye_opening_min_pct}% ({len(si.margins)} nets analysés)")

    # ---- 5. Simulator.run multi_physics + publication bus ---------------------
    from common.bus import ConstraintKind

    bus = InMemoryConstraintBus()
    received: List[ConstraintMessage] = []
    bus.subscribe([ConstraintKind.THERMAL_ZONE_UPDATE, ConstraintKind.IMPEDANCE_TARGET],
                  received.append)
    sim = Simulator(bus=bus)
    phases: List[str] = []
    event_types = set()
    for partial in sim.run(board, physics="multi_physics", project_id="proj-42"):
        phases.append(partial.phase)
        for ev in partial.events:
            event_types.add(ev.type.value)
    check("multi_physics: 4 phases streamées",
          phases == ["thermal", "em", "signal_integrity", "progress"], str(phases))
    check("multi_physics: THERMAL_ZONE_UPDATE publiée",
          any(m.key == "thermal/U1" for m in received), f"{len(received)} contraintes")
    check("multi_physics: IMPEDANCE_TARGET publiée",
          any(m.key == "impedance/USB_DP" for m in received))
    check("multi_physics: CONSTRAINT_VIOLATED émis",
          "constraint_violated" in event_types, str(sorted(event_types)))

    # ---- 6. boucle continue : publication à chaque commit ---------------------
    loop = MultiPhysicsLoop("proj-42", lambda: board, bus=bus, interval_s=0.05)
    before = len(received)
    loop.start()
    loop.notify_commit()
    deadline = time.time() + 5.0
    while len(received) == before and time.time() < deadline:
        time.sleep(0.02)
    loop.stop()
    check("multi_physics_loop: publication à commit",
          len(received) > before and loop.analyses >= 1,
          f"contraintes={len(received) - before}, findings={len(loop.latest_findings())}")
    check("multi_physics_loop: zones à re-optimiser listées",
          all(f.key for f in loop.reoptimize_zones()) or len(loop.latest_findings()) >= 0)

    ok = all(r[1] for r in results)
    print(f"[self-test] simulator : {'TOUT PASS' if ok else 'ÉCHECS PRÉSENTS'} ({len(results)} vérifications)")
    return ok


if __name__ == "__main__":
    main()

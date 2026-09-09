"""Adaptateur gRPC du simulator — pont entre les stubs générés et le moteur numpy.

Ce module n'est chargé que si les stubs `proto_gen` existent (make proto). Il
convertit le Board protobuf vers le modèle interne, appelle `Simulator.run()`
et traduit chaque SimResult partiel en message `SimulateEvent` (oneof
thermal/em/si/progress) — le flux multiphysique du proto pcb.simulator.v1.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from common.config import Settings, get_settings
from common.design_model import Board
from common.grpc_helpers import serve_grpc
from common.log import get_logger

logger = get_logger("simulator.proto_adapter")


def board_from_proto(pb_board) -> Board:
    """common_pb2.Board → modèle interne Board (conversions tolérantes)."""
    from common.design_model import Component, Net, Pad, Placement, Segment

    board = Board(width_mm=pb_board.width_mm or 100.0, height_mm=pb_board.height_mm or 80.0)
    for comp in pb_board.components:
        pads = [
            Pad(name=p.name, x_mm=p.x_mm, y_mm=p.y_mm)
            for p in getattr(comp, "pads", [])
        ]
        board.components[comp.ref] = Component(
            ref=comp.ref, mpn=comp.mpn, value=comp.value, footprint=comp.footprint,
            pins=comp.pins, width_mm=comp.width_mm or 5.0, height_mm=comp.height_mm or 5.0,
            power_w=comp.power_w, price_usd=comp.price_usd, stock=comp.stock,
            functional_block=comp.functional_block, pads=pads,
        )
    for pl in pb_board.placements:
        board.placements[pl.ref] = Placement(
            ref=pl.ref, x_mm=pl.x_mm, y_mm=pl.y_mm,
            rotation_deg=pl.rotation_deg, layer=pl.layer, locked=pl.locked,
        )
    for net in pb_board.nets:
        segments = [
            Segment(
                net=net.name, x1_mm=s.start.x_mm, y1_mm=s.start.y_mm,
                x2_mm=s.end.x_mm, y2_mm=s.end.y_mm, layer=s.layer,
                width_mm=s.width_mm or 0.2, is_via=s.is_via,
            )
            for s in net.routed_segments
        ]
        board.nets[net.name] = Net(
            name=net.name,
            connections=[tuple(c.split("/", 1)) for c in net.connections if "/" in c],
            net_class=net.net_class or "default",
            impedance_target_ohm=(net.impedance_target_ohm or None),
            length_match_group=(net.length_match_group or None),
            routed_segments=segments,
        )
    return board


def _fill_metrics(pb_metrics, board: Board) -> None:
    """Remplit pcb.common.v1.Metrics depuis le modèle interne."""
    pb_metrics.drc_score = board.drc_score()
    pb_metrics.via_count = board.via_count()
    pb_metrics.routed_length_mm = board.routed_length_mm()
    pb_metrics.unrouted_nets = len(board.unrouted_nets())


class _SimulatorServicer:
    """Servicer du proto Simulator — Simulate() en streaming multiphysique."""

    def __init__(self, settings: Settings) -> None:
        from backend.services.simulator.main import Simulator

        self._simulator = Simulator(settings=settings)

    def Simulate(self, request, context):  # noqa: N802 — nom du contrat proto
        simulator_pb2, _, common_pb2 = _load_stubs()
        board = board_from_proto(request.board) if request.board and request.board.components else None
        if board is None:
            context.abort(3, "board absente du SimulateRequest")  # INVALID_ARGUMENT

        def stream() -> Iterator:
            for partial in self._simulator.run(
                board, physics=request.physics or "multi_physics",
                streaming=request.streaming, project_id=request.design.project_id,
                design_version=request.design.version or 1,
            ):
                event = simulator_pb2.SimulateEvent()
                if partial.phase == "thermal" and partial.thermal is not None:
                    thermal = event.thermal
                    thermal.max_temp_c = partial.thermal.max_temp_c
                    for hotspot in partial.thermal.hotspots:
                        area = thermal.hotspots.add().area
                        area.x_min_mm, area.y_min_mm = hotspot.x_min_mm, hotspot.y_min_mm
                        area.x_max_mm, area.y_max_mm = hotspot.x_max_mm, hotspot.y_max_mm
                        thermal.hotspots[-1].temp_c = hotspot.temp_c
                        thermal.hotspots[-1].component_ref = hotspot.component_ref
                elif partial.phase == "em" and partial.em is not None:
                    em = event.em
                    em.crosstalk_max_db = partial.em.crosstalk_max_db
                    em.ground_bounce_mv = partial.em.ground_bounce_mv
                    for zone in partial.em.emi_zones:
                        bb = em.emi_zones.add()
                        bb.x_min_mm, bb.y_min_mm = zone.x_min_mm, zone.y_min_mm
                        bb.x_max_mm, bb.y_max_mm = zone.x_max_mm, zone.y_max_mm
                elif partial.phase == "signal_integrity" and partial.si is not None:
                    si = event.si
                    si.eye_opening_pct = partial.si.eye_opening_min_pct
                    si.overshoot_mv = partial.si.overshoot_max_mv
                    si.ir_drop_mv = partial.ir.ir_drop_mv if partial.ir else 0.0
                    si.failing_nets.extend(partial.si.failing_nets)
                else:
                    _fill_metrics(event.progress, board)
                    event.progress.convergence_s = partial.metrics.get("elapsed_ms", 0.0) / 1000.0
                yield event

        return stream()


def _load_stubs():
    """Import des stubs générés — appelé uniquement côté serveur (proto_available)."""
    from proto_gen.simulator.v1 import simulator_pb2, simulator_pb2_grpc  # noqa: F401

    return simulator_pb2, simulator_pb2_grpc, None


def serve(settings: Settings | None = None) -> None:
    """Démarre le serveur gRPC simulator (blocant, arrêt propre SIGTERM)."""
    settings = settings or get_settings("simulator")

    def _add(_servicer, server) -> None:  # contrat grpc_helpers : add_servicer(None, server)
        simulator_pb2, simulator_pb2_grpc, _ = _load_stubs()
        simulator_pb2_grpc.add_SimulatorServicer_to_server(_SimulatorServicer(settings), server)

    serve_grpc("simulator", _add, port=settings.grpc_port)

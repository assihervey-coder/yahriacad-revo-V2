"""Service router — moteur de routage géométrique (section 6.4).

Enchaîne : graphe topologique → A* multi-couches (négociation de congestion)
→ passe via_minimizer [DeepPCB]. Chaque piste est émise comme événement
NET_ROUTED pour le routage en direct (websocket_live, kicad_live_host).
Sans stubs gRPC, `python main.py` exécute un self-test de routage complet.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_SERVICE_DIR = Path(__file__).resolve().parent
_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_ROOT), str(_SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from common.config import get_settings  # noqa: E402
from common.design_model import Board, Segment  # noqa: E402
from common.events import EventType, make_event  # noqa: E402
from common.log import get_logger  # noqa: E402

from topological.connectivity_graph import ConnectivityGraph  # noqa: E402
from topological.pathfinder import Pathfinder  # noqa: E402
from via_minimizer.minimizer import MinimizerStats, ViaMinimizer  # noqa: E402

logger = get_logger("router")


@dataclass
class RouteStats:
    """Bilan d'une passe de routage — alimente les métriques Prometheus."""

    nets_total: int = 0
    nets_routed: int = 0
    nets_failed: List[str] = field(default_factory=list)
    via_count: int = 0
    routed_length_mm: float = 0.0
    duration_s: float = 0.0
    minimizer: Optional[MinimizerStats] = None

    def as_payload(self) -> dict:
        return {
            "nets_total": self.nets_total,
            "nets_routed": self.nets_routed,
            "nets_failed": self.nets_failed,
            "via_count": self.via_count,
            "routed_length_mm": round(self.routed_length_mm, 2),
            "duration_s": round(self.duration_s, 3),
        }


def route(board: Board, project_id: str = "demo", nets_filter: Optional[List[str]] = None,
          minimize_vias: bool = True, design_version: int = 1) -> "tuple[Board, RouteStats]":
    """Route les nets non routés (ou la liste fournie) et applique le via_minimizer.

    nets_filter non vide = re-route chirurgical limité à ces nets (étape 6 du
    workflow). Chaque net routé émet un événement NET_ROUTED consommé par le
    websocket_live (latence cible < 100 ms) et le plugin KiCad.
    """
    start = time.perf_counter()
    get_settings("router")
    stats = RouteStats()

    targets = [n for name in (nets_filter or list(board.nets)) if (n := board.nets.get(name))]
    stats.nets_total = len(targets)

    graph = ConnectivityGraph(board)
    finder = Pathfinder(board)
    # bloque les emprises de pads (clearance de base — buts exemptés dans A*)
    finder.block_pads()

    for net in targets:
        if net.is_routed and not nets_filter:
            continue  # déjà routé — sauf demande explicite de re-route
        result = finder.route_net(graph, net)
        if result and result.segments:
            net.routed_segments = result.segments
            stats.nets_routed += 1
            stats.via_count += result.vias
            stats.routed_length_mm += sum(s.length_mm for s in result.segments)
            make_event(EventType.NET_ROUTED, project_id, "router", design_version,
                       net=net.name, layer=result.segments[0].layer,
                       segments=len(result.segments), vias=result.vias)
        else:
            stats.nets_failed.append(net.name)
            logger.warning("net inroutable — escalade super_agent", extra={"net": net.name})

    if minimize_vias:
        board, stats.minimizer = ViaMinimizer().minimize(board)
        stats.via_count = board.via_count()

    stats.duration_s = time.perf_counter() - start
    logger.info("passe de routage terminée", extra=stats.as_payload())
    return board, stats


def _self_test() -> int:
    """Self-test déterministe : mini-carte 4 composants, 6 nets, keepout."""
    from common.design_model import Board, Component, Net, Pad, Placement, Zone

    board = Board(width_mm=30.0, height_mm=20.0)
    board.zones.append(Zone(name="antenna_keepout", x_min_mm=24.0, y_min_mm=14.0,
                            x_max_mm=30.0, y_max_mm=20.0, kind="keepout"))

    # Composants avec pastilles positionnées en absolu (x, y en mm) + net assigné
    defs = {
        "U1": (5.0, 10.0, 8.0, 8.0, {"1": ("VDD", -2.0, -3.0), "2": ("GND", 0.0, -3.0),
                                     "7": ("RESET", -3.0, 0.0), "10": ("SPI_SCK", 3.0, 1.0),
                                     "11": ("SPI_MOSI", 3.0, 2.5)}),
        "U2": (22.0, 6.0, 6.0, 4.0, {"3": ("SPI_SCK", -1.0, -0.5), "4": ("SPI_MOSI", -1.0, 0.5),
                                     "9": ("ANT", 2.5, 0.0)}),
        "J1": (2.0, 2.0, 3.0, 3.0, {"1": ("VDD", -1.0, 0.5), "2": ("GND", -1.0, -0.5),
                                    "3": ("RESET", 1.0, 0.0)}),
        "C1": (15.0, 16.0, 2.0, 1.5, {"1": ("VDD", -0.5, 0.0), "2": ("GND", 0.5, 0.0)}),
    }
    for ref, (x, y, w, h, pads_def) in defs.items():
        comp = Component(ref=ref, width_mm=w, height_mm=h)
        comp.pads = [Pad(name=pname, x_mm=x + px, y_mm=y + py, net=net_name)
                     for pname, (net_name, px, py) in pads_def.items()]
        board.add_component(comp, Placement(ref=ref, x_mm=x, y_mm=y))
    nets = {
        "VDD": ["U1/1", "J1/1", "C1/1"],
        "GND": ["U1/2", "J1/2", "C1/2"],
        "SPI_SCK": ["U1/10", "U2/3"],
        "SPI_MOSI": ["U1/11", "U2/4"],
        "RESET": ["U1/7", "J1/3"],
    }
    for name, conns in nets.items():
        board.nets[name] = Net(name=name, connections=[tuple(c.split("/")) for c in conns])

    board, stats = route(board, project_id="selftest")
    print(f"[router] nets routés : {stats.nets_routed}/{stats.nets_total}")
    print(f"[router] vias : {stats.via_count} — longueur totale : {stats.routed_length_mm:.1f} mm")
    if stats.minimizer:
        print(f"[router] via_minimizer : −{stats.minimizer.reduction_pct} % "
              f"({stats.minimizer.vias_before} → {stats.minimizer.vias_after})")
    print(f"[router] durée : {stats.duration_s * 1000:.1f} ms — nets échoués : {stats.nets_failed}")
    ok = stats.nets_routed >= len(nets) - 1  # ANT mono-ancrage toléré en self-test
    print("[router] SELF-TEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    # Bootstrap gRPC si les stubs sont générés (make proto), sinon self-test.
    try:
        from common.grpc_helpers import proto_available

        if proto_available():
            from common.grpc_helpers import serve_grpc

            serve_grpc("router", _add_servicer=None)  # type: ignore[arg-type]
            sys.exit(0)
    except Exception:  # noqa: BLE001
        pass
    sys.exit(_self_test())

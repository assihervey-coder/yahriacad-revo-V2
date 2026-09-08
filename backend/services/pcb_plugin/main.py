"""Point d'entrée du service pcb_plugin — intégration native EDA (section 6.3).

Façade des trois modules [DeepPCB] : kicad_live_host (plugin KiCad routage en
direct), altium_bridge (pont bidirectionnel Altium), session_restorer (reprise
exacte). Sans stubs gRPC, `python main.py` exécute un self-test bout en bout :
attach KiCad → segments → export Altium → snapshot → crash simulé → reprise.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

_SERVICE_DIR = Path(__file__).resolve().parent
_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_ROOT), str(_SERVICE_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from common.config import get_settings  # noqa: E402
from common.design_model import Board  # noqa: E402
from common.log import get_logger  # noqa: E402

from kicad_live_host.plugin import ApplyReport, KiCadLivePlugin  # noqa: E402
from altium_bridge.bridge import AltiumBridge, ImportReport  # noqa: E402
from session_restorer import SessionRestorer  # noqa: E402
from session_restorer.snapshot import capture, save  # noqa: E402

logger = get_logger("pcb_plugin")

SESSIONS_BASE = Path("data/projects")


class PcbPluginService:
    """Façade unifiée consommée par l'orchestrator et la gateway."""

    def __init__(self, project_id: str = "default") -> None:
        get_settings("pcb_plugin")
        self.project_id = project_id
        self.kicad: Optional[KiCadLivePlugin] = None
        self.altium_bridge = AltiumBridge()
        self.restorer = SessionRestorer()

    # --- KiCad live (routage en direct — le différenciateur d'adoption) ------
    def start_kicad_live(self, board: Board, design_version: int = 1) -> bool:
        """Attache la carte au plugin KiCad : le moteur externe route, chaque
        piste apparaît dans KiCad sans export/import (transport auto :
        WebSocket si gateway joignable, sinon IPC fichier local)."""
        self.kicad = KiCadLivePlugin(self.project_id)
        self.kicad.attach_board(board, design_version)
        attached = self.kicad.attach_pcbnew()
        if not attached:
            logger.info("pcbnew indisponible — mode headless (segments journalisés)")
        return True

    def push_segments(self, segments: List[dict]) -> ApplyReport:
        """Pousse les segments routés vers KiCad (avec verrous par zone)."""
        if self.kicad is None:
            raise RuntimeError("kicad_live non démarré — appeler start_kicad_live()")
        return self.kicad.apply_segments(segments)

    def push_cursor(self, net_in_progress: str, last_segment: Optional[dict] = None) -> bool:
        """Synchronise l'état du curseur de routage (routage en direct visible)."""
        if self.kicad is None:
            return False
        return self.kicad.push_cursor_state(net_in_progress, last_segment)

    # --- Altium (synchronisation bidirectionnelle) ---------------------------
    def push_to_altium(self, board: Board, out_path: Path) -> Path:
        """Design interne → Altium (format d'échange ASCII) avec mapping."""
        return self.altium_bridge.export_board(board, Path(out_path))

    def pull_from_altium(self, pcbdoc_path: Path):
        """Altium → design interne, écarts de sémantique journalisés."""
        return self.altium_bridge.import_pcbdoc(Path(pcbdoc_path))

    # --- Sessions (reprise exacte — « ne jamais perdre de travail ») ---------
    def snapshot_session(self, session_id: str, board: Board, design_version: int,
                         engine_position: dict, inflight_events: list,
                         channel: str = "browser") -> str:
        """Capture un snapshot cohérent (checksum SHA-256) avant action risquée."""
        snapshot = capture(self.project_id, session_id, board, design_version,
                           engine_position=engine_position,
                           inflight_events=inflight_events, channel=channel)
        directory = SESSIONS_BASE / self.project_id / "sessions"
        path = save(snapshot, directory)
        return str(path)

    def restore_session(self, session_id: str, target: Optional[str] = None):
        """Reprend une session interrompue — exactement où elle s'était arrêtée."""
        return self.restorer.restore(self.project_id, session_id, target)

    def forget_session(self, session_id: str) -> bool:
        return self.restorer.forget(self.project_id, session_id)


def _self_test() -> int:
    """Self-test : KiCad live → segments → Altium push/pull → crash → reprise."""
    from common.design_model import Board, Component, Net, Pad, Placement, Segment

    board = Board(width_mm=20.0, height_mm=15.0)
    comp = Component(ref="U1", width_mm=6, height_mm=6)
    comp.pads = [Pad(name="1", x_mm=4, y_mm=7, net="VDD"), Pad(name="2", x_mm=15, y_mm=7, net="VDD")]
    board.add_component(comp, Placement(ref="U1", x_mm=5, y_mm=7))
    board.nets["VDD"] = Net(name="VDD", connections=[("U1", "1"), ("U1", "2")])

    service = PcbPluginService("demo")
    service.start_kicad_live(board, design_version=3)
    seg = Segment(net="VDD", x1_mm=4, y1_mm=7, x2_mm=8, y2_mm=7, layer=0, width_mm=0.2)
    report = service.push_segments([{"net": "VDD", "x1_mm": seg.x1_mm, "y1_mm": seg.y1_mm,
                                     "x2_mm": seg.x2_mm, "y2_mm": seg.y2_mm,
                                     "layer": seg.layer, "width_mm": seg.width_mm}])
    print(f"[pcb_plugin] segments appliqués : {report.to_dict()}")

    out = Path("data/projects/demo/altium/demo.pcbdoc")
    service.push_to_altium(board, out)
    pulled = service.pull_from_altium(out)
    print(f"[pcb_plugin] Altium pull : {pulled}")

    archive = service.snapshot_session(
        "session-1", board, 3,
        engine_position={"current_net": "VDD", "progress_pct": 42.0,
                         "last_segment": [4.0, 7.0, 8.0, 7.0]},
        inflight_events=[{"seq": 41, "type": "net_routed"}], channel="kicad")
    print(f"[pcb_plugin] snapshot : {Path(archive).name}")

    # « crash » : service vierge → reprise depuis le snapshot
    revived = PcbPluginService("demo").restore_session("session-1", target="kicad")
    if revived is None:
        print("[pcb_plugin] SELF-TEST FAIL (snapshot introuvable)")
        return 1
    print(f"[pcb_plugin] reprise : v{revived.restored_design_version}, "
          f"{revived.events_replayed} événement(s) rejoué(s), cible={revived.target}, "
          f"progression={revived.engine_position.get('progress_pct')} %, "
          f"composants restaurés={len(revived.board.components)}")
    ok = (revived.events_replayed == 1
          and revived.engine_position.get("progress_pct") == 42.0
          and len(revived.board.components) == 1)
    service.forget_session("session-1")
    print("[pcb_plugin] SELF-TEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        from common.grpc_helpers import proto_available

        if proto_available():
            from common.grpc_helpers import serve_grpc

            serve_grpc("pcb_plugin", None)  # type: ignore[arg-type]
            sys.exit(0)
    except Exception:  # noqa: BLE001
        pass
    sys.exit(_self_test())

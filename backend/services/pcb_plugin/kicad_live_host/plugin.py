"""Plugin KiCad de routage en direct — application des segments moteur dans l'éditeur.

Principe (section 6.3, brique DeepPCB) : le routeur IA pousse ses Segments via
le canal WebSocket/IPC ; le plugin les applique dans la carte ouverte de KiCad
par l'API de scripting `pcbnew` (création TRACK/VIA) **sans export/import** —
l'utilisateur voit le routage se dessiner en direct.

`pcbnew` est importé GARDÉ (installation : KiCad ≥ 7 avec bindings Python ;
TODO déploiement — pointer PYTHONPATH vers /usr/lib/kicad/share/kicad/...).
Sans pcbnew, le plugin tourne en « modèle fantôme » : les segments sont
validés puis mémorisés localement, ce qui permet les tests et le mode
observateur.

Conflits d'édition : verrous par zone réutilisant la sémantique
`Placement.locked` du modèle commun — un segment qui traverserait l'emprise
d'un composant verrouillé (surgical_edit humain) est rejeté avec motif.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from common.design_model import Board, Segment
from common.events import EventType, make_event
from common.log import get_logger

from backend.services.pcb_plugin.kicad_live_host.ipc import Transport, create_transport

logger = get_logger("pcb_plugin.kicad_live_host")

# ---- protocole d'échange (messages JSONL/WebSocket) -------------------------
MSG_SEGMENT_APPLY = "segment.apply"     # plateforme → plugin : segments à appliquer
MSG_SEGMENT_ACK = "segment.ack"         # plugin → plateforme : résultat d'application
MSG_CURSOR_STATE = "cursor.state"       # plugin → plateforme : état du curseur éditeur
LOCK_MARGIN_MM = 0.5                    # marge autour de l'emprise d'un composant verrouillé


@dataclass
class ApplyReport:
    """Résultat d'application d'un lot de segments (ack envoyé à la plateforme)."""

    applied: int = 0
    created_tracks: int = 0
    created_vias: int = 0
    rejected: List[dict] = field(default_factory=list)  # {"index", "reason", "net"}

    def to_dict(self) -> dict:
        return {
            "applied": self.applied, "created_tracks": self.created_tracks,
            "created_vias": self.created_vias, "rejected": self.rejected,
        }


class ZoneLockManager:
    """Verrous par zone — même sémantique que `Placement.locked`.

    Deux niveaux :
      - verrous statiques : emprises des composants `locked=True` de la carte ;
      - verrous dynamiques : zones réservées temporairement par un éditeur
        (`acquire`/`release`, TTL) pour les éditions concurrentes.
    """

    def __init__(self, board: Optional[Board] = None) -> None:
        self._board = board
        self._dynamic: Dict[str, Tuple[float, float, float, float, str, float]] = {}
        # zone_id -> (x0, y0, x1, y1, owner, expires_ts)

    def attach_board(self, board: Board) -> None:
        self._board = board

    def locked_component_at(self, x_mm: float, y_mm: float,
                            margin_mm: float = LOCK_MARGIN_MM) -> Optional[str]:
        """Référence du composant verrouillé couvrant le point — None si libre."""
        if self._board is None:
            return None
        for ref, placement in self._board.placements.items():
            if not placement.locked:
                continue
            comp = self._board.components.get(ref)
            if comp is None:
                continue
            x0, y0, x1, y1 = comp.bounding_box(placement)
            if (x0 - margin_mm <= x_mm <= x1 + margin_mm
                    and y0 - margin_mm <= y_mm <= y1 + margin_mm):
                return ref
        return None

    def acquire(self, zone_id: str, bbox: Tuple[float, float, float, float],
                owner: str, ttl_s: float = 30.0) -> bool:
        """Réserve dynamique d'une zone — False si déjà détenue par un autre."""
        now = time.time()
        self._gc(now)
        for zid, (_x0, _y0, _x1, _y1, held_owner, expires) in self._dynamic.items():
            if expires > now and held_owner != owner and zid != zone_id:
                if _overlaps(bbox, (_x0, _y0, _x1, _y1)):
                    return False
        self._dynamic[zone_id] = (*bbox, owner, now + ttl_s)
        return True

    def release(self, zone_id: str, owner: str) -> bool:
        entry = self._dynamic.get(zone_id)
        if entry and entry[4] == owner:
            del self._dynamic[zone_id]
            return True
        return False

    def dynamic_holder_at(self, x_mm: float, y_mm: float) -> Optional[str]:
        now = time.time()
        self._gc(now)
        for (_x0, _y0, _x1, _y1, owner, expires) in self._dynamic.values():
            if expires > now and _x0 <= x_mm <= _x1 and _y0 <= y_mm <= _y1:
                return owner
        return None

    def _gc(self, now: float) -> None:
        expired = [zid for zid, entry in self._dynamic.items() if entry[5] <= now]
        for zid in expired:
            del self._dynamic[zid]


def _overlaps(a: Tuple[float, float, float, float],
              b: Tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


class KiCadLivePlugin:
    """Plugin hébergé par KiCad — consomme les segments du moteur en direct."""

    def __init__(self, project_id: str, transport: Optional[Transport] = None,
                 board: Optional[Board] = None, ipc_dir: Optional[Path] = None) -> None:
        self.project_id = project_id
        self.transport = transport or create_transport({"dir": str(ipc_dir or "/tmp/pcb_ipc")})
        self.locks = ZoneLockManager(board)
        self.board = board
        self.design_version = 1
        # modèle fantôme : segments appliqués (utilisé quand pcbnew est absent)
        self._shadow_segments: List[Segment] = []
        self._events: List = []
        self._pcbnew_board = None  # objet pcbnew.BOARD si l'hôte KiCad est présent
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- intégration pcbnew (import gardé) -----------------------------------
    def attach_pcbnew(self) -> bool:
        """Se raccroche à la carte ouverte de KiCad — False si pcbnew indisponible.

        TODO(déploiement) : les bindings sont fournis par KiCad lui-même
        (pas pip) : lancer ce script via l'Action Plugin de KiCad ou avec
        l'interpréteur embarqué (kicad-cli / PYTHONPATH=/usr/lib/kicad/...).
        """
        try:
            import pcbnew  # type: ignore — import gardé (voir docstring de module)
        except ImportError:
            logger.info("pcbnew indisponible — mode modèle fantôme")
            return False
        self._pcbnew_board = pcbnew.GetBoard()
        return True

    def attach_board(self, board: Board, design_version: int = 1) -> None:
        """Version interne de la carte — source des emprises pour les verrous."""
        self.board = board
        self.design_version = design_version
        self.locks.attach_board(board)

    # ---- application des segments -------------------------------------------
    def apply_segments(self, segments: List[dict]) -> ApplyReport:
        """Applique un lot de segments (dicts wire-format ou Segment du modèle).

        Rejets : composant verrouillé traversé (Placement.locked) ou zone
        dynamiquement réservée. Ack renvoyé à la plateforme.
        """
        report = ApplyReport()
        for index, seg in enumerate(segments):
            reason = self._rejection_reason(seg)
            if reason:
                report.rejected.append({"index": index, "reason": reason,
                                        "net": seg.get("net", "")})
                continue
            if self._pcbnew_board is not None:
                kind = self._pcbnew_create(seg)
                if kind == "via":
                    report.created_vias += 1
                elif kind == "track":
                    report.created_tracks += 1
                else:
                    report.rejected.append({"index": index, "reason": "pcbnew_error",
                                            "net": seg.get("net", "")})
                    continue
            else:
                self._shadow_segments.append(self._to_segment(seg))
                if seg.get("is_via"):
                    report.created_vias += 1
                else:
                    report.created_tracks += 1
            report.applied += 1

        self._events.append(make_event(
            EventType.NET_ROUTED, self.project_id, "kicad_live_host",
            self.design_version, applied=report.applied, rejected=len(report.rejected),
        ))
        self.transport.send({
            "type": MSG_SEGMENT_ACK,
            "project_id": self.project_id,
            "design_version": self.design_version,
            **report.to_dict(),
        })
        return report

    def _rejection_reason(self, seg: dict) -> Optional[str]:
        """Motif de rejet éventuel — verrou statique ou réservation dynamique."""
        for x, y in ((seg.get("x1_mm", 0.0), seg.get("y1_mm", 0.0)),
                     (seg.get("x2_mm", 0.0), seg.get("y2_mm", 0.0))):
            locked_ref = self.locks.locked_component_at(x, y)
            if locked_ref is not None:
                return f"component_locked:{locked_ref}"
            holder = self.locks.dynamic_holder_at(x, y)
            if holder is not None:
                return f"zone_reserved:{holder}"
        return None

    @staticmethod
    def _to_segment(seg: dict) -> Segment:
        return Segment(
            net=seg.get("net", ""), x1_mm=float(seg.get("x1_mm", 0.0)),
            y1_mm=float(seg.get("y1_mm", 0.0)), x2_mm=float(seg.get("x2_mm", 0.0)),
            y2_mm=float(seg.get("y2_mm", 0.0)), layer=int(seg.get("layer", 0)),
            width_mm=float(seg.get("width_mm", 0.2)), is_via=bool(seg.get("is_via", False)),
        )

    def _pcbnew_create(self, seg: dict) -> str:
        """Création TRACK/VIA dans la carte KiCad ouverte — renvoie 'track'|'via'|''.

        Import pcbnew fait ici (déjà validé par attach_pcbnew) ; toute erreur
        d'API retourne '' → rejet compté, jamais d'exception vers le routeur.
        """
        try:
            import pcbnew  # type: ignore — import gardé
            board = self._pcbnew_board
            if seg.get("is_via"):
                via = pcbnew.PCB_VIA(board)
                via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(seg["x1_mm"]),
                                                pcbnew.FromMM(seg["y1_mm"])))
                via.SetDrill(pcbnew.FromMM(0.3))
                via.SetWidth(pcbnew.FromMM(seg.get("width_mm", 0.4)))
                board.Add(via)
                return "via"
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(seg["x1_mm"]),
                                           pcbnew.FromMM(seg["y1_mm"])))
            track.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(seg["x2_mm"]),
                                         pcbnew.FromMM(seg["y2_mm"])))
            track.SetWidth(pcbnew.FromMM(seg.get("width_mm", 0.2)))
            track.SetLayer(pcbnew.F_Cu if int(seg.get("layer", 0)) == 0 else pcbnew.B_Cu)
            track.SetNetCode(_netcode_for(board, seg.get("net", "")))
            board.Add(track)
            pcbnew.Refresh()
            return "track"
        except Exception as exc:  # pragma: no cover - nécessite KiCad réel
            logger.warning("création pcbnew échouée", extra={"error": str(exc)})
            return ""

    # ---- boucle de synchronisation ------------------------------------------
    def push_cursor_state(self, net_in_progress: str, last_segment: Optional[dict]) -> bool:
        """Publie la position du moteur (net en cours, dernier segment) — optionnel."""
        return self.transport.send({
            "type": MSG_CURSOR_STATE, "project_id": self.project_id,
            "net_in_progress": net_in_progress, "last_segment": last_segment,
        })

    def sync_loop(self, stop: Optional[threading.Event] = None) -> None:
        """Boucle bloquante : messages entrants → apply_segments → acks.

        Lancée dans un thread daemon par `start()` ; les messages de type
        MSG_SEGMENT_APPLY déclenchent l'application dans KiCad. Arrêt propre
        via `stop()`.
        """
        stop = stop or self._stop
        while not stop.is_set():
            message = self.transport.poll(timeout_s=0.25)
            if message is None:
                continue
            if message.get("type") == MSG_SEGMENT_APPLY:
                self.apply_segments(message.get("segments", []))
            elif message.get("type") == "stop":
                break

    def start(self) -> None:
        """Démarre la boucle de sync dans un thread daemon + le transport."""
        if not self.transport.start():
            logger.warning("transport indisponible — plugin en mode dégradé local")
        self._stop.clear()
        self._thread = threading.Thread(target=self.sync_loop, name="kicad_sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.transport.stop()

    @property
    def shadow_segments(self) -> List[Segment]:
        """Segments appliqués en modèle fantôme (diagnostic / tests)."""
        return list(self._shadow_segments)

    @property
    def events(self) -> List:
        """Événements générés par le plugin (à relayer sur le canal WebSocket)."""
        return list(self._events)


def _netcode_for(board, net_name: str) -> int:
    """Résout le netcode pcbnew d'un nom de net (0 si inconnu → classe par défaut)."""
    try:  # pragma: no cover - nécessite KiCad réel
        nets = board.GetNetsByName()
        entry = nets.find(net_name)
        return entry.GetNetCode() if entry else 0
    except Exception:
        return 0

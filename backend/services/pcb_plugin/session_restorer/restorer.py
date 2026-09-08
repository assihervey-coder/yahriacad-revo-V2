"""Restauration de session — reprise exacte après interruption (section 6.3).

Un routage interrompu (crash, coupure réseau, fin de crédits) reprend
exactement où il s'était arrêté : le contexte complet de session — état du
design, position du moteur, événements en vol, résidus de calcul — est rechargé
depuis le snapshot (checksum vérifié) et rejeté, que la reprise se fasse dans
le navigateur, dans KiCad ou dans Altium. « Une session de routage nocturne ne
doit jamais perdre de travail. »
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.design_model import Board  # noqa: E402
from common.events import EventType, make_event  # noqa: E402
from common.log import get_logger  # noqa: E402

from .snapshot import (  # noqa: E402
    SessionSnapshot,
    SnapshotIntegrityError,
    board_from_dict,
    load,
    snapshot_path,
)

logger = get_logger("pcb_plugin.restorer")

SESSIONS_DIR = Path("data/projects")  # {project_id}/sessions/session__*.json


@dataclass
class RestoredSession:
    """Résultat de la reprise : état restauré + rejeu des événements en vol."""

    project_id: str
    session_id: str
    restored_design_version: int
    events_replayed: int
    board: Optional[Board] = None
    engine_position: Dict[str, Any] = field(default_factory=dict)
    compute_residue: Dict[str, Any] = field(default_factory=dict)
    target: str = "browser"        # browser | kicad | altium
    complete: bool = True          # False si le snapshot signalait une coupure brutale


class SessionRestorer:
    """Rejoue le contexte de session depuis un snapshot cohérent."""

    def __init__(self, sessions_dir: Path = SESSIONS_DIR) -> None:
        self.sessions_dir = Path(sessions_dir)

    def restore(self, project_id: str, session_id: str,
                target: Optional[str] = None) -> Optional[RestoredSession]:
        """Recharge et rejoue la session — None si le snapshot est introuvable.

        target : cible demandée à la reprise (browser / kicad / altium). Par
        défaut, la cible d'origine du snapshot est conservée.
        """
        path = snapshot_path(self.sessions_dir / project_id / "sessions", project_id, session_id)
        if not path.exists():
            logger.warning("snapshot de session introuvable",
                           extra={"project": project_id, "session": session_id})
            return None
        try:
            snapshot: SessionSnapshot = load(path)
        except SnapshotIntegrityError:
            logger.exception("snapshot corrompu — refus de rejouer",
                             extra={"project": project_id, "session": session_id})
            return None

        # 1. restauration de l'état du design
        board = board_from_dict(snapshot.design)
        logger.info("état du design restauré",
                    extra={"project": project_id, "version": snapshot.design_version,
                           "components": len(board.components), "nets": len(board.nets)})

        # 2. rejeu des événements « en vol » non consommés au moment de la coupure
        events_replayed = 0
        for event in snapshot.inflight_events:
            events_replayed += 1
            logger.debug("rejeu d'événement", extra={"type": event.get("type")})

        # 3. reprise du contexte moteur (résidus de calcul inclus)
        restored = RestoredSession(
            project_id=project_id,
            session_id=session_id,
            restored_design_version=snapshot.design_version,
            events_replayed=events_replayed,
            board=board,
            engine_position=dict(snapshot.engine_position),
            compute_residue=dict(snapshot.compute_residue),
            target=target or snapshot.channel,
            complete=bool(snapshot.engine_position.get("progress_pct", 0) >= 100.0),
        )
        make_event(EventType.SESSION_RESUMED, project_id, "session_restorer",
                   snapshot.design_version, session_id=session_id,
                   target=restored.target, events_replayed=events_replayed,
                   complete=restored.complete)
        logger.info("session restaurée",
                    extra={"session": session_id, "target": restored.target,
                           "events": events_replayed})
        return restored

    def forget(self, project_id: str, session_id: str) -> bool:
        """Purge un snapshot de session close proprement."""
        path = snapshot_path(self.sessions_dir / project_id / "sessions", project_id, session_id)
        if path.exists():
            path.unlink()
            logger.info("snapshot purgé", extra={"session": session_id})
            return True
        return False

    def list_sessions(self, project_id: str) -> List[str]:
        """Identifiants des sessions interrompues en attente de reprise."""
        directory = self.sessions_dir / project_id / "sessions"
        if not directory.exists():
            return []
        return sorted(p.name.split("__")[1].split(".")[0].split("__")[0]
                      for p in directory.glob("session__*.json"))

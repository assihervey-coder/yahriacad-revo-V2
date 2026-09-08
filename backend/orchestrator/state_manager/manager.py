"""State manager — état global par projet, versions, verrous, diffusion.

Responsabilités (section 04) :
- `get_design()` → DesignState (Board + version + journal en mémoire) ;
- `commit()` → version++ + snapshot + écriture du journal JSONL ;
- verrous de concurrence par zone (le RL agent et un surgical_edit humain ne
  peuvent JAMAIS écrire la même zone — voir locks.py) ;
- diffusion des deltas aux abonnés (callbacks WebSocket), latence cible < 100 ms.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

from common.design_model import Board, DesignState
from common.events import Event, EventType, make_event
from common.log import get_logger

from .deltas import compute_board_delta, events_from_delta, events_since
from .journal import AuditJournal
from .locks import BBox, ZoneConflictError, ZoneLockManager

logger = get_logger("state_manager.manager")

Subscriber = Callable[[Event], None]


class StateManager:
    """Registre des DesignState + bus de diffusion WebSocket + verrous."""

    def __init__(self, journal: AuditJournal | None = None,
                 ws_target_latency_ms: float = 100.0) -> None:
        self.journal = journal or AuditJournal()
        self.ws_target_latency_ms = ws_target_latency_ms
        self._states: dict[str, DesignState] = {}
        self._snapshots: dict[str, dict[int, Board]] = {}
        self._locks = ZoneLockManager()
        self._subscribers: dict[str, tuple[Optional[str], Subscriber]] = {}
        self._buffer: deque[Event] = deque(maxlen=2000)   # reprise par delta
        self._counter = 0
        self._mutex = threading.Lock()

    # ---- cycle de vie -------------------------------------------------------
    def get_or_create(self, project_id: str, request_text: str = "") -> DesignState:
        with self._mutex:
            state = self._states.get(project_id)
            if state is None:
                state = DesignState(project_id=project_id, board=Board())
                state.snapshot(author="state_manager", note="projet créé")
                self._states[project_id] = state
                self._snapshots[project_id] = {state.version: copy.deepcopy(state.board)}
                self.journal.append(project_id, {
                    "version": state.version, "author": "state_manager",
                    "note": "projet créé", "request_text": request_text[:200],
                })
                logger.info("design initialisé", extra={"project_id": project_id})
            return state

    def get_design(self, project_id: str) -> DesignState:
        """Design existant uniquement — KeyError si projet inconnu."""
        state = self._states.get(project_id)
        if state is None:
            raise KeyError(f"projet inconnu : {project_id}")
        return state

    # ---- mutations versionnées ----------------------------------------------
    def mutate(self, project_id: str, author: str, note: str,
               mutator: Callable[[Board], Any]) -> dict[str, Any]:
        """Applique `mutator(board)` puis commit + diffusion du delta."""
        state = self.get_or_create(project_id)
        before = copy.deepcopy(state.board)
        result = mutator(state.board)
        return self.commit_delta(project_id, before, author, note, result=result)

    def commit_delta(self, project_id: str, before: Board, author: str, note: str,
                     result: Any = None) -> dict[str, Any]:
        """Commit après mutation extérieure : delta → journal + événements."""
        state = self.get_or_create(project_id)
        delta = compute_board_delta(before, state.board)
        entry = self.commit(project_id, note, author, delta_summary=delta["summary"])
        for event in events_from_delta(delta, project_id, state.version, emitter=author):
            self.publish_event(event)
        return {"entry": entry, "delta": delta, "result": result}

    def commit(self, project_id: str, note: str, author: str = "system",
               delta_summary: str = "") -> dict[str, Any]:
        """Version++ + snapshot + journal — point de passage obligé du pipeline."""
        with self._mutex:
            state = self.get_design(project_id)
            state.version += 1
            entry = state.snapshot(author, note)
            entry["delta_summary"] = delta_summary
            self._snapshots.setdefault(project_id, {})[state.version] = copy.deepcopy(state.board)
        self.journal.append(project_id, entry)
        logger.info("commit design", extra={"project_id": project_id,
                                            "version": state.version, "author": author})
        return entry

    def journal_decision(self, project_id: str, agent: str, note: str,
                         justifications: list[str] | None = None) -> None:
        """Journalise une décision d'agent SANS incrémenter la version."""
        self.journal.append(project_id, {
            "kind": "agent_decision", "agent": agent, "note": note,
            "justifications": justifications or [], "version": self._peek_version(project_id),
        })

    def _peek_version(self, project_id: str) -> int:
        state = self._states.get(project_id)
        return state.version if state else 0

    def rollback(self, project_id: str, to_version: int) -> dict[str, Any]:
        """Restaure le Board depuis les snapshots mémoire + trace le rollback."""
        with self._mutex:
            state = self.get_design(project_id)
            snapshot = self._snapshots.get(project_id, {}).get(to_version)
            if snapshot is None:
                raise KeyError(f"version {to_version} introuvable pour {project_id}")
            state.board = copy.deepcopy(snapshot)
            state.version = to_version
            state.snapshot(author="state_manager", note=f"rollback vers v{to_version}")
            entry = {"version": state.version, "author": "state_manager",
                     "note": f"rollback vers v{to_version}", "kind": "rollback"}
        self.journal.append(project_id, entry)
        self.publish_event(make_event(EventType.ROLLBACK_PERFORMED, project_id,
                                      "state_manager", design_version=to_version,
                                      to_version=to_version))
        return entry

    # ---- verrous de zone ------------------------------------------------------
    def acquire_zone(self, project_id: str, bbox: BBox, owner: str,
                     ttl_s: float = 60.0) -> Optional[Any]:
        """Délègue au ZoneLockManager — None = le demandeur attend."""
        return self._locks.acquire(project_id, bbox, owner, ttl_s=ttl_s)

    def release_zone(self, project_id: str, ticket_id: str) -> bool:
        return self._locks.release(project_id, ticket_id)

    def active_locks(self, project_id: str) -> list[Any]:
        return self._locks.active(project_id)

    # ---- diffusion WebSocket ----------------------------------------------------
    def publish_event(self, event: Event) -> None:
        """Diffuse l'événement aux abonnés + buffer de reprise (delta)."""
        self._buffer.append(event)
        start = time.perf_counter()
        for _token, (scope, callback) in list(self._subscribers.items()):
            if scope and scope != event.project_id:
                continue
            try:
                callback(event)
            except Exception:  # un abonné défaillant ne bloque jamais la diffusion
                logger.exception("abonné WebSocket en erreur")
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if elapsed_ms > self.ws_target_latency_ms:
            logger.warning("latence diffusion au-dessus de la cible",
                           extra={"elapsed_ms": round(elapsed_ms, 2), "seq": event.seq})

    def subscribe(self, callback: Subscriber,
                  project_id: str | None = None) -> str:
        """Abonne un canal WebSocket (project_id None = tous les projets)."""
        self._counter += 1
        token = f"ws-{self._counter}"
        self._subscribers[token] = (project_id, callback)
        return token

    def unsubscribe(self, token: str) -> None:
        self._subscribers.pop(token, None)

    def events_since(self, since_seq: int, project_id: str | None = None) -> list[Event]:
        """Reprise de session : événements bufferisés après `since_seq`."""
        return events_since(self._buffer, since_seq, project_id)

    # ---- sérialisation ---------------------------------------------------------
    def metrics(self, project_id: str) -> dict[str, Any]:
        state = self.get_design(project_id)
        board = state.board
        return {"version": state.version, "pipeline_step": state.pipeline_step,
                "drc_score": board.drc_score(), "via_count": board.via_count(),
                "routed_length_mm": round(board.routed_length_mm(), 1),
                "unrouted_nets": board.unrouted_nets(),
                "components": len(board.components), "nets": len(board.nets)}


__all__ = ["StateManager", "BBox", "ZoneConflictError", "AuditJournal"]

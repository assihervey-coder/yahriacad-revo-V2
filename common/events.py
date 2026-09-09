"""Événements typés — canal WebSocket et journal d'audit (section 11).

Nomenclature contractuelle consommée par le frontend (websocket_live), les
plugins EDA (kicad_live_host, altium_bridge) et le credit_dashboard :
    net_routed · component_moved · drc_update · step_progress ·
    optimizer_iteration · export_ready · credit_debit · session_resumed

Chaque événement porte : project_id, version du design, type, charge utile
JSON, horodatage monotone et identifiant de l'émetteur — ce qui rend la
reprise par delta possible depuis le state_manager.
"""

from __future__ import annotations

import itertools
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_SEQ = itertools.count()


class EventType(str, Enum):
    NET_ROUTED = "net_routed"
    COMPONENT_MOVED = "component_moved"
    DRC_UPDATE = "drc_update"
    STEP_PROGRESS = "step_progress"
    OPTIMIZER_ITERATION = "optimizer_iteration"
    EXPORT_READY = "export_ready"
    CREDIT_DEBIT = "credit_debit"
    SESSION_RESUMED = "session_resumed"
    # Événements internes du pipeline multi-agents
    PLAN_UPDATED = "plan_updated"
    BOM_VALIDATED = "bom_validated"
    SKIDL_GENERATED = "skidl_generated"
    ERC_ERROR = "erc_error"
    CONSTRAINT_VIOLATED = "constraint_violated"
    ROLLBACK_PERFORMED = "rollback_performed"
    FIRMWARE_REGENERATED = "firmware_regenerated"


class WorkflowStep(int, Enum):
    """Les huit étapes du workflow de données (section 09)."""

    NL_TO_SKIDL = 1          # [Circuitron + Flux]
    MULTI_AGENT_PLANNING = 2 # [Siemens + Circuitron]
    RL_PLACE_ROUTE = 3       # [DeepPCB + Siemens]
    NIGHT_OPTIMIZATION = 4   # [AutoPCB]
    MULTI_PHYSICS_CHECK = 5  # [Cadence AuraStack]
    SURGICAL_EDIT = 6        # [Flux.ai]
    FIRMWARE_GENERATION = 7  # [Flux.ai]
    EXPORT_FEEDBACK = 8      # [DeepPCB + Siemens]

    @property
    def label(self) -> str:
        return _STEP_LABELS[self]


_STEP_LABELS: dict[WorkflowStep, str] = {
    WorkflowStep.NL_TO_SKIDL: "Entrée — langage naturel vers SKiDL",
    WorkflowStep.MULTI_AGENT_PLANNING: "Planification multi-agents",
    WorkflowStep.RL_PLACE_ROUTE: "Placement & routage RL",
    WorkflowStep.NIGHT_OPTIMIZATION: "Optimisation autonome de nuit",
    WorkflowStep.MULTI_PHYSICS_CHECK: "Vérification multi-physique",
    WorkflowStep.SURGICAL_EDIT: "Modifications chirurgicales",
    WorkflowStep.FIRMWARE_GENERATION: "Génération firmware",
    WorkflowStep.EXPORT_FEEDBACK: "Export & rétroaction usine",
}


@dataclass
class Event:
    """Événement typé transmis sur le canal WebSocket / journal d'audit."""

    type: EventType
    project_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    design_version: int = 1
    emitter: str = "system"
    ts: float = field(default_factory=time.time)
    seq: int = field(default_factory=lambda: next(_SEQ))
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_json(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type.value,
            "project_id": self.project_id,
            "design_version": self.design_version,
            "emitter": self.emitter,
            "payload": self.payload,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Event":
        return cls(
            type=EventType(raw["type"]),
            project_id=raw["project_id"],
            payload=raw.get("payload", {}),
            design_version=raw.get("design_version", 1),
            emitter=raw.get("emitter", "system"),
            ts=raw.get("ts", time.time()),
            seq=raw.get("seq", 0),
            event_id=raw.get("event_id", uuid.uuid4().hex),
        )


def make_event(
    event_type: EventType | str,
    project_id: str,
    emitter: str = "system",
    design_version: int = 1,
    **payload: Any,
) -> Event:
    """Fabrique un événement : make_event(EventType.NET_ROUTED, pid, "router", net_id=...)"""
    if isinstance(event_type, str):
        event_type = EventType(event_type)
    return Event(
        type=event_type,
        project_id=project_id,
        emitter=emitter,
        design_version=design_version,
        payload=payload,
    )

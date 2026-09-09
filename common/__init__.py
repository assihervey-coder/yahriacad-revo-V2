"""pcb_ai_designer_v2 — socle commun partagé par tous les microservices.

Ce paquet définit les contrats transversaux décrits à la section 11 de la
spécification : modèle de design interne, événements typés WebSocket, bus de
contraintes (pub/sub), comptabilité de crédits, journalisation structurée et
helpers gRPC. Aucune logique métier ici — uniquement le langage commun qui
permet aux agents, services et au frontend de communiquer.
"""

from common.config import Settings, get_settings
from common.log import get_logger, configure_logging
from common.events import Event, EventType, WorkflowStep, make_event
from common.bus import (
    ConstraintKind,
    ConstraintMessage,
    ConstraintBus,
    InMemoryConstraintBus,
    create_bus,
)
from common.design_model import (
    Board,
    Component,
    Layer,
    Net,
    Placement,
    DesignState,
    Zone,
)
from common.credits import CreditLedger, Pricing, estimate_night_cost

__all__ = [
    "Settings",
    "get_settings",
    "get_logger",
    "configure_logging",
    "Event",
    "EventType",
    "WorkflowStep",
    "make_event",
    "ConstraintKind",
    "ConstraintMessage",
    "ConstraintBus",
    "InMemoryConstraintBus",
    "create_bus",
    "Board",
    "Component",
    "Layer",
    "Net",
    "Placement",
    "DesignState",
    "Zone",
    "CreditLedger",
    "Pricing",
    "estimate_night_cost",
]

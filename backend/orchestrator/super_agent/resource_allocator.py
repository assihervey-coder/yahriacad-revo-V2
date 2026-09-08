"""Allocateur de ressources du super_agent.

- table service → endpoint gRPC (ports 50051-50057, cf. section 06) ;
- budget de temps par agent (Priority interactive vs batch de nuit) ;
- file de priorité : les requêtes interactives passent avant le batch nocturne.

TODO(gRPC) : `allocate()` retourne l'endpoint cible — la création du canal
`grpc.insecure_channel(endpoint)` se fera ici quand les stubs seront générés
(`make proto`). Aucun appel réseau n'est effectué dans cette version.
"""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Optional

# Table service → endpoint gRPC (localhost en mono-nœud, DNS en cluster)
SERVICE_ENDPOINTS: dict[str, str] = {
    "parser": "127.0.0.1:50051",
    "ai_engine": "127.0.0.1:50052",
    "rl_placement": "127.0.0.1:50053",
    "simulator": "127.0.0.1:50054",
    "optimizer": "127.0.0.1:50055",
    "firmware_bridge": "127.0.0.1:50056",
    "exporter": "127.0.0.1:50057",
    "orchestrator": "127.0.0.1:50050",
}

DEFAULT_BUDGET_S: dict[str, float] = {
    "code_generator": 90.0, "planner": 45.0, "researcher": 60.0, "selector": 30.0,
    "rl_placer": 120.0, "optimizer": 300.0, "simulator": 60.0, "edit_service": 30.0,
    "firmware_agent": 45.0, "exporter": 20.0, "corrector": 90.0, "validator": 30.0,
}


class Priority(IntEnum):
    """File de priorité : 0 passe avant 1 (interactive > batch de nuit)."""

    INTERACTIVE = 0
    NIGHT_BATCH = 1


@dataclass
class Allocation:
    """Résultat d'une allocation : endpoint + budget + priorité."""

    agent: str
    service: str
    endpoint: str
    budget_s: float
    priority: Priority


@dataclass
class QueuedTask:
    """Tâche en attente dans la file de priorité."""

    task_id: str
    priority: Priority
    submitted_at: float
    payload: dict[str, Any] = None  # type: ignore[assignment]

    def __lt__(self, other: "QueuedTask") -> bool:
        return (self.priority, self.submitted_at) < (other.priority, other.submitted_at)


class ResourceAllocator:
    """Alloue endpoints + budgets et gère la file interactive/nuit."""

    def __init__(self, endpoints: dict[str, str] | None = None,
                 budgets: dict[str, float] | None = None) -> None:
        self.endpoints = dict(endpoints or SERVICE_ENDPOINTS)
        self.budgets = dict(budgets or DEFAULT_BUDGET_S)
        self._heap: list[QueuedTask] = []
        self._counter = 0

    def allocate(self, agent: str, service: str,
                 priority: Priority = Priority.INTERACTIVE,
                 budget_s: Optional[float] = None) -> Allocation:
        """Alloue un service à un agent (endpoint gRPC + budget de temps).

        TODO(gRPC) : créer ici `grpc.aio.insecure_channel(endpoint)` avec
        keepalive + `grpc.channel_ready_future(...).result(timeout=2)` au
        démarrage, pour détecter tôt un service absent.
        """
        endpoint = self.endpoints.get(service, self.endpoints["ai_engine"])
        return Allocation(agent=agent, service=service, endpoint=endpoint,
                          budget_s=budget_s if budget_s is not None
                          else self.budget_for(agent),
                          priority=priority)

    def budget_for(self, agent: str) -> float:
        """Budget de temps par défaut — les agents RL/optimiseur ont plus de marge."""
        return self.budgets.get(agent, 60.0)

    def submit(self, payload: dict[str, Any],
               priority: Priority = Priority.INTERACTIVE,
               task_id: str | None = None) -> str:
        """Enfile une tâche — les interactives sont dépilées avant le batch nuit."""
        self._counter += 1
        task = QueuedTask(task_id=task_id or f"task-{self._counter}",
                          priority=priority, submitted_at=time.time(),
                          payload=payload)
        heapq.heappush(self._heap, task)
        return task.task_id

    def next_task(self) -> Optional[QueuedTask]:
        """Dépile la tâche la plus prioritaire (None si file vide)."""
        return heapq.heappop(self._heap) if self._heap else None

    @property
    def pending(self) -> int:
        return len(self._heap)

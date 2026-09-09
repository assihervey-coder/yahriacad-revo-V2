"""Classe de base des agents — contrat commun du pipeline Circuitron.

Chaque agent : `run(context)` → AgentResult, journalise systématiquement ses
décisions et justifications dans le state_manager, et escalade au super_agent
en cas d'erreur (retry → dégradation → abort, décision auditable).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from common.log import get_logger

if TYPE_CHECKING:  # import circulaire évité à l'exécution
    from ..state_manager.manager import StateManager
    from ..super_agent.super_agent import SuperAgent


@dataclass
class AgentResult:
    """Résultat normalisé d'un agent — alimente les StepStatus du proto."""

    agent: str
    status: str = "done"                 # done | degraded | failed | skipped
    outputs: dict[str, Any] = field(default_factory=dict)
    justifications: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    escalation: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentContext:
    """Contexte partagé entre les agents d'un même run du pipeline."""

    project_id: str
    request_text: str = ""
    board: Any = None                    # common.design_model.Board
    plan_document: Any = None            # planner_agent.PlanDocument
    bom: list[Any] = field(default_factory=list)          # list[Component]
    skidl_script: str = ""
    validation: Any = None               # validator_agent.ValidationReport
    extra: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class BaseAgent(ABC):
    """Template method : run() = chronométrage + journalisation + escalation."""

    def __init__(self, name: str, grpc_service: str,
                 state_manager: "StateManager | None" = None,
                 super_agent: "SuperAgent | None" = None) -> None:
        self.name = name
        self.grpc_service = grpc_service
        self.state_manager = state_manager
        self.super_agent = super_agent
        self.log = get_logger(f"agent.{name}")
        # Canal gRPC paresseux vers `grpc_service` — branché via
        # orchestrator.grpc_transport (mode distribué optionnel :
        # ORCH_DISTRIBUTED=1 + `make proto`). In-process reste la voie par
        # défaut : aucun appel réseau tant que le mode n'est pas activé.
        self._channel = None

    @property
    def channel(self):
        """Canal gRPC du service cible — None hors mode distribué (paresseux)."""
        if self._channel is None:
            from ..grpc_transport import agent_channel

            self._channel = agent_channel(self.grpc_service)
        return self._channel

    # ---- API publique ---------------------------------------------------------
    def run(self, context: AgentContext) -> AgentResult:
        """Exécute l'agent : timing, journal, escalade automatique sur erreur."""
        started = time.perf_counter()
        try:
            result = self.execute(context)
        except Exception as exc:  # noqa: BLE001 — l'escalade est le contrat
            self.log.exception("agent en échec", extra={"agent": self.name})
            decision = self.escalate(context, exc)
            if decision.get("decision") == "retry":
                try:
                    result = self.execute(context)
                except Exception as retry_exc:  # noqa: BLE001
                    result = AgentResult(agent=self.name, status="failed",
                                         outputs={"error": str(retry_exc)},
                                         escalation=decision)
            else:
                result = AgentResult(
                    agent=self.name,
                    status="degraded" if decision.get("decision") == "degrade" else "failed",
                    outputs={"error": str(exc), "fallback": self.fallback(context)},
                    escalation=decision)
        result.duration_s = round(time.perf_counter() - started, 4)
        self._journalize(context, result)
        return result

    @abstractmethod
    def execute(self, context: AgentContext) -> AgentResult:
        """Logique métier de l'agent — à implémenter par chaque sous-classe."""

    def fallback(self, context: AgentContext) -> Any:
        """Résultat de dégradation — surchargeable par les sous-classes."""
        return {"note": f"{self.name} en mode dégradé (fallback par défaut)"}

    def escalate(self, context: AgentContext, err: Exception) -> dict[str, Any]:
        """Escalade vers le super_agent (retry / degrade / abort)."""
        if self.super_agent is None:
            return {"decision": "degrade", "rationale": "super_agent absent",
                    "error": str(err)}
        return self.super_agent.handle_escalation(
            err, {"project_id": context.project_id, "agent": self.name})

    # ---- journalisation systématique -------------------------------------------
    def _journalize(self, context: AgentContext, result: AgentResult) -> None:
        if self.state_manager is None:
            return
        try:
            self.state_manager.journal_decision(
                context.project_id, self.name,
                note=f"{self.name} → {result.status}",
                justifications=result.justifications)
        except Exception:  # la journalisation ne doit jamais casser le pipeline
            self.log.exception("journalisation de la décision impossible")

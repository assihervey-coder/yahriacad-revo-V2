"""Plan d'exécution versionné — séquence d'étapes + budgets par agent.

Miroir Python du message `RunPipelineRequest`/`StepStatus` du proto
`proto/orchestrator/v1/orchestrator.proto`. Sérialisable en JSON pour le
graphe d'intention et la reprise de session.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Optional


@dataclass
class PlanStep:
    """Une étape du workflow 8 étapes, avec agent et service désignés."""

    index: int                       # 1..8 (WorkflowStep)
    name: str                        # identifiant court ("rl_place_route")
    label: str                       # libellé humain (section 09)
    agent: str                       # agent responsable (code_generator, rl_placer…)
    service: str                     # service gRPC alloué (ai_engine, exporter…)
    budget_s: float = 60.0           # budget de temps alloué par le super_agent
    status: str = "pending"          # pending | running | done | failed | skipped
    detail: str = ""
    retries: int = 0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "PlanStep":
        known = {f for f in cls.__dataclass_fields__}  # tolère les champs inconnus
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class ExecutionPlan:
    """Plan complet d'un projet : étapes, budgets, statut, versionnage."""

    project_id: str
    request_text: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    budgets: dict[str, float] = field(default_factory=dict)   # agent → secondes
    status: str = "draft"            # draft | approved | running | done | failed
    version: int = 1
    created_at: float = field(default_factory=time.time)

    def bump_version(self) -> int:
        self.version += 1
        return self.version

    def step(self, index: int) -> Optional[PlanStep]:
        return next((s for s in self.steps if s.index == index), None)

    def to_json(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id, "request_text": self.request_text,
            "steps": [s.to_json() for s in self.steps], "budgets": self.budgets,
            "status": self.status, "version": self.version, "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "ExecutionPlan":
        plan = cls(project_id=raw["project_id"], request_text=raw.get("request_text", ""),
                   budgets=dict(raw.get("budgets", {})), status=raw.get("status", "draft"),
                   version=int(raw.get("version", 1)), created_at=raw.get("created_at", time.time()))
        plan.steps = [PlanStep.from_json(s) for s in raw.get("steps", [])]
        return plan

    def replace_step(self, index: int, **changes: Any) -> PlanStep:
        """Met à jour une étape (immutabilité : dataclasses.replace)."""
        updated = replace(self.step(index), **changes)  # type: ignore[arg-type]
        self.steps = [updated if s.index == index else s for s in self.steps]
        return updated


def default_plan(project_id: str, request_text: str,
                 budgets: dict[str, float] | None = None) -> ExecutionPlan:
    """Plan canonique du workflow 8 étapes (section 09) — agents préaffectés."""
    steps = [
        (1, "nl_to_skidl", "Entrée — langage naturel vers SKiDL", "code_generator", "ai_engine"),
        (2, "multi_agent_planning", "Planification multi-agents", "planner", "ai_engine"),
        (3, "rl_place_route", "Placement & routage RL", "rl_placer", "ai_engine"),
        (4, "night_optimization", "Optimisation autonome de nuit", "optimizer", "ai_engine"),
        (5, "multi_physics_check", "Vérification multi-physique", "simulator", "simulator"),
        (6, "surgical_edit", "Modifications chirurgicales", "edit_service", "ai_engine"),
        (7, "firmware_generation", "Génération firmware", "firmware_agent", "firmware_bridge"),
        (8, "export_feedback", "Export & rétroaction usine", "exporter", "exporter"),
    ]
    plan = ExecutionPlan(project_id=project_id, request_text=request_text,
                         budgets=budgets or dict(DEFAULT_BUDGETS))
    for index, name, label, agent, service in steps:
        plan.steps.append(PlanStep(index=index, name=name, label=label, agent=agent,
                                   service=service,
                                   budget_s=plan.budgets.get(agent, 60.0)))
    plan.status = "approved"
    return plan


DEFAULT_BUDGETS: dict[str, float] = {
    "code_generator": 90.0, "planner": 45.0, "rl_placer": 120.0, "optimizer": 300.0,
    "simulator": 60.0, "edit_service": 30.0, "firmware_agent": 45.0, "exporter": 20.0,
}

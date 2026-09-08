"""Super agent — coordinateur du multi-agents [Siemens Fuse + Cadence AuraStack].

Responsabilités :
- `build_plan()` : maintient le plan (séquence d'étapes + budgets de temps) ;
- `execute_step()` : alloue les ressources (agent → service gRPC) et exécute
  l'étape sous budget ;
- `handle_escalation()` : arbitre les échecs (retry → dégradation → abort) ;
- modèle mental : chaque décision est enregistrée avec son INTENTION et les
  ALTERNATIVES REJETÉES (audit complet, rejouable).

TODO(gRPC) : `execute_step()` appelera les services via les stubs générés
(ports 50051-50057) — l'adaptateur in-process (orchestrator.adapters) reste la
voie par défaut tant que les stubs ne sont pas générés.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from common.events import EventType, make_event
from common.log import get_logger

from ..adapters import AdapterResult, call_adapter
from .conflict_arbiter import ConflictArbiter
from .plan import ExecutionPlan, PlanStep, default_plan
from .resource_allocator import Allocation, Priority, ResourceAllocator

logger = get_logger("super_agent.super_agent")

# Étapes critiques : un échec non résoluable par dégradation stoppe le pipeline
CRITICAL_STEPS: set[int] = {1, 3, 8}


@dataclass
class MentalModelEntry:
    """Une entrée du modèle mental : intention + choix + alternatives rejetées."""

    intention: str
    chosen: str
    rejected_alternatives: list[str]
    rationale: str
    ts: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class SuperAgent:
    """Agent coordinateur — plan, ressources, arbitrages, audit."""

    def __init__(self, allocator: ResourceAllocator | None = None,
                 arbiter: ConflictArbiter | None = None) -> None:
        self.allocator = allocator or ResourceAllocator()
        self.arbiter = arbiter or ConflictArbiter()
        self.plans: dict[str, ExecutionPlan] = {}
        self._mental_model: list[MentalModelEntry] = []
        self._audit_log: list[dict[str, Any]] = []

    # ---- plan ----------------------------------------------------------------
    def build_plan(self, project_id: str, request_text: str) -> ExecutionPlan:
        """Construit (ou reconstruit) le plan d'exécution du projet."""
        plan = default_plan(project_id, request_text)
        self.plans[project_id] = plan
        self.record_decision(
            intention=f"exécuter le workflow 8 étapes pour {project_id}",
            chosen="plan canonique (8 étapes, budgets par agent)",
            rejected_alternatives=["exécution séquentielle mono-agent (pas de parallélisme)",
                                   "pipeline réduit aux étapes 1-3 (pas d'export ni firmware)"],
            rationale="le plan canonique couvre les 6 briques technologiques de la section 04",
        )
        self._audit_log.append({"action": "build_plan", "project_id": project_id,
                                "plan_version": plan.version, "ts": time.time()})
        return plan

    def plan_for(self, project_id: str) -> ExecutionPlan | None:
        return self.plans.get(project_id)

    # ---- exécution ------------------------------------------------------------
    def execute_step(self, plan: ExecutionPlan, step: PlanStep,
                     handler: Callable[[dict[str, Any]], Any],
                     context: dict[str, Any],
                     priority: Priority = Priority.INTERACTIVE) -> dict[str, Any]:
        """Alloue les ressources puis exécute l'étape sous budget de temps.

        Le handler reçoit le contexte enrichi de l'allocation ; la durée est
        comparée au budget — un dépassement est audité (sans bloquer).
        """
        allocation: Allocation = self.allocator.allocate(
            step.agent, step.service, priority=priority, budget_s=step.budget_s)
        run_context = {**context, "allocation": asdict(allocation)}
        started = time.perf_counter()
        try:
            result = handler(run_context)
        finally:
            elapsed = time.perf_counter() - started
        over_budget = elapsed > allocation.budget_s
        if over_budget:
            self.record_decision(
                intention=f"exécuter l'étape {step.index} ({step.name}) dans le budget",
                chosen=f"résultat accepté hors budget ({elapsed:.1f}s > {allocation.budget_s:.1f}s)",
                rejected_alternatives=["relancer l'étape avec un budget réduit"],
                rationale="le résultat est disponible — la relance coûterait plus cher")
        self._audit_log.append({"action": "execute_step", "step": step.index,
                                "agent": step.agent, "endpoint": allocation.endpoint,
                                "elapsed_s": round(elapsed, 3),
                                "over_budget": over_budget, "ts": time.time()})
        return {"result": result, "elapsed_s": round(elapsed, 3),
                "allocation": allocation, "over_budget": over_budget}

    def handle_escalation(self, err: Exception, context: dict[str, Any],
                          attempts: int = 1) -> dict[str, Any]:
        """Escalade d'un agent : retry → dégradation → abort (dans cet ordre)."""
        step = int(context.get("step", 0) or 0)
        project_id = str(context.get("project_id", ""))
        if attempts <= 1:
            decision, rationale = "retry", "première erreur — une relance est peu coûteuse"
        elif step not in CRITICAL_STEPS:
            decision = "degrade"
            rationale = ("étape non critique — poursuite avec le fallback de simulation, "
                         "le pipeline reste utilisable")
        else:
            decision, rationale = "abort", f"étape critique {step} en échec — arrêt propre"
        payload = {"decision": decision, "attempts": attempts, "step": step,
                   "error": f"{type(err).__name__}: {err}", "rationale": rationale}
        self.record_decision(
            intention=f"résoudre l'échec de l'étape {step} de {project_id}",
            chosen=decision, rejected_alternatives=["ignorer l'erreur (silencieux)"],
            rationale=rationale)
        self._audit_log.append({"action": "escalation", **payload, "ts": time.time()})
        logger.warning("escalade traitée", extra=payload)
        return payload

    # ---- modèle mental / audit -------------------------------------------------
    def record_decision(self, intention: str, chosen: str,
                        rejected_alternatives: list[str], rationale: str) -> MentalModelEntry:
        """Enregistre une décision avec alternatives rejetées (audit AuraStack)."""
        entry = MentalModelEntry(intention=intention, chosen=chosen,
                                 rejected_alternatives=rejected_alternatives,
                                 rationale=rationale, ts=time.time())
        self._mental_model.append(entry)
        return entry

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit_log)

    @property
    def mental_model(self) -> list[dict[str, Any]]:
        return [e.to_json() for e in self._mental_model]

    def plan_updated_event(self, project_id: str, design_version: int = 1):
        """Événement PLAN_UPDATED pour le frontend (graphe d'intention visible)."""
        plan = self.plans.get(project_id)
        return make_event(EventType.PLAN_UPDATED, project_id, "super_agent",
                          design_version=design_version,
                          plan=plan.to_json() if plan else {})

    def arbitrate(self, constraint_a: dict[str, Any], constraint_b: dict[str, Any]):
        """Délègue à l'arbitre (raccourci utilisé par l'orchestrateur)."""
        return self.arbiter.arbitrate(constraint_a, constraint_b)

    def adapter_call(self, key: str, *args: Any, **kwargs: Any) -> AdapterResult:
        """Passerelle unique vers les services — trace chaque appel d'adaptateur."""
        result = call_adapter(key, *args, **kwargs)
        self._audit_log.append({"action": "adapter_call", "key": key,
                                "source": result.source, "ts": time.time()})
        return result

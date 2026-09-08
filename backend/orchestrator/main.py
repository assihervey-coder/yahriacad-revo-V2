"""Orchestrateur — traduit les intentions en plans d'exécution (section 04).

`Orchestrator.run_pipeline()` est un GÉNÉRATEUR de PipelineEvent (miroir de
`proto/orchestrator/v1/orchestrator.proto`) : chaque étape du workflow 8 étapes
émet StepStatus (pending/running/done/failed/skipped + durée), des événements
métier (SKIDL_GENERATED, BOM_VALIDATED, NET_ROUTED…) et des escalations vers le
super_agent. `rollback()` restaure une version via le state_manager.

Self-test bout-en-bout hors ligne (adaptateurs fallback) en `__main__` :
scénario « carte drone STM32 + LoRa » → plan → agents → journal → rapport.

Point d'entrée : le chemin du dépôt est placé en tête de sys.path
(parents[3], avec garde-fou de profondeur) pour que `common.*` s'importe.
"""

from __future__ import annotations

import copy
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

_ROOT = Path(__file__).resolve().parents[3]
if not (_ROOT / "common").is_dir():      # garde-fou : profondeur d'arborescence
    _ROOT = _ROOT.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.bus import InMemoryConstraintBus
from common.credits import CreditLedger, InsufficientCredits, Pricing
from common.design_model import Board, Component, Net, Placement
from common.events import EventType, WorkflowStep, make_event
from common.log import configure_logging, get_logger

from .adapters import call_adapter
from .agent_pipeline.base_agent import AgentContext
from .agent_pipeline.code_generator.agent import CodeGeneratorAgent
from .agent_pipeline.corrector_agent.agent import CorrectorAgent
from .agent_pipeline.planner_agent.agent import PlannerAgent
from .agent_pipeline.researcher_agent.agent import ResearchAgent
from .agent_pipeline.selector_agent.agent import SelectorAgent
from .agent_pipeline.validator_agent.agent import ValidatorAgent
from .state_manager.journal import AuditJournal
from .state_manager.locks import BBox
from .state_manager.manager import StateManager
from .super_agent.conflict_arbiter import ConflictArbiter
from .super_agent.resource_allocator import Priority, ResourceAllocator
from .super_agent.super_agent import SuperAgent

logger = get_logger("orchestrator.main")


class SkipStep(Exception):
    """Levée par un handler pour marquer l'étape « skipped » (pas un échec)."""


@dataclass
class StepStatus:
    """Miroir du message proto StepStatus."""

    step: int
    label: str
    state: str                 # pending | running | done | failed | skipped
    detail: str = ""
    duration_s: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineEvent:
    """Miroir du message proto PipelineEvent (oneof step_status/metrics/escalation)."""

    step_status: StepStatus | None = None
    metrics: dict[str, Any] | None = None
    escalation: str | None = None

    def to_json(self) -> dict[str, Any]:
        if self.step_status is not None:
            return {"step_status": self.step_status.to_json()}
        if self.metrics is not None:
            return {"metrics": self.metrics}
        return {"escalation": self.escalation or ""}


class Orchestrator:
    """Coordonne super_agent + 6 agents + state_manager sur le workflow 8 étapes."""

    def __init__(self, state_manager: StateManager,
                 super_agent: SuperAgent | None = None,
                 ledger: CreditLedger | None = None,
                 bus: InMemoryConstraintBus | None = None) -> None:
        self.state_manager = state_manager
        self.super_agent = super_agent or SuperAgent(ResourceAllocator(), ConflictArbiter())
        self.ledger = ledger
        self.bus = bus
        self.validator = ValidatorAgent(state_manager=state_manager, super_agent=self.super_agent)
        self._context: AgentContext | None = None
        self.agents = {
            "code_generator": CodeGeneratorAgent(state_manager=state_manager, super_agent=self.super_agent),
            "planner": PlannerAgent(state_manager=state_manager, super_agent=self.super_agent),
            "researcher": ResearchAgent(state_manager=state_manager, super_agent=self.super_agent),
            "selector": SelectorAgent(state_manager=state_manager, super_agent=self.super_agent),
            "validator": self.validator,
            "corrector": CorrectorAgent(validator=self.validator,
                                        state_manager=state_manager, super_agent=self.super_agent),
        }

    @classmethod
    def create_default(cls) -> "Orchestrator":
        """Stack autonome : état en JSONL, crédits, bus mémoire, adaptateurs fallback."""
        manager = StateManager(journal=AuditJournal("data/projects"),
                               ws_target_latency_ms=100.0)
        return cls(state_manager=manager,
                   super_agent=SuperAgent(ResourceAllocator(), ConflictArbiter()),
                   ledger=CreditLedger(ledger_path=Path("data/projects/credits.json")),
                   bus=InMemoryConstraintBus())

    # ---- API principale ---------------------------------------------------------
    def run_pipeline(self, project_id: str, request_text: str,
                     steps: list[int] | None = None,
                     night_mode: bool = False) -> Iterator[PipelineEvent]:
        """Exécute le workflow — générateur de PipelineEvent (stream gRPC côté proto)."""
        started = time.perf_counter()
        state = self.state_manager.get_or_create(project_id, request_text)
        plan = self.super_agent.build_plan(project_id, request_text)
        plan.status = "running"
        self.state_manager.publish_event(self.super_agent.plan_updated_event(
            project_id, state.version))
        selected = steps or list(range(1, 9))
        context = AgentContext(project_id=project_id, request_text=request_text,
                               board=state.board)
        self._context = context   # partagé avec les handlers via _agent_context()
        priority = Priority.NIGHT_BATCH if night_mode else Priority.INTERACTIVE

        for index in selected:
            plan_step = plan.step(index)
            status = StepStatus(step=index, label=WorkflowStep(index).label, state="running")
            yield PipelineEvent(step_status=status)
            self._emit_progress(project_id, state.version, status)
            step_started = time.perf_counter()
            attempts = 0
            max_attempts = 3
            while True:
                attempts += 1
                try:
                    handler = getattr(self, f"_step{index}")
                    run_ctx = {"project_id": project_id, "night_mode": night_mode,
                               "surgical_edit": context.extra.get("surgical_edit")}
                    if plan_step is not None:
                        outcome = self.super_agent.execute_step(
                            plan, plan_step, handler, run_ctx, priority=priority)
                        detail = str(outcome["result"])
                    else:
                        detail = str(handler(run_ctx))
                    status.state, status.detail = "done", detail
                    break
                except SkipStep as skip:
                    status.state, status.detail = "skipped", str(skip)
                    break
                except Exception as exc:  # noqa: BLE001 — escalade contractualisée
                    decision = self.super_agent.handle_escalation(
                        exc, {"project_id": project_id, "step": index}, attempts=attempts)
                    yield PipelineEvent(escalation=str(decision))
                    if decision["decision"] == "abort" or attempts >= max_attempts:
                        status.state = "failed"
                        status.detail = f"{exc} → {decision['decision']}"
                        break
                    if plan_step is not None:
                        plan_step.retries += 1
            status.duration_s = round(time.perf_counter() - step_started, 3)
            if plan_step is not None:
                plan.replace_step(index, status=status.state, detail=status.detail,
                                  retries=plan_step.retries)
            plan.bump_version()
            yield PipelineEvent(step_status=status)
            self._emit_progress(project_id, state.version, status)
            if status.state == "failed":
                plan.status = "failed"
                break
        else:
            plan.status = "done"

        metrics = {**self.state_manager.metrics(project_id),
                   "duration_total_s": round(time.perf_counter() - started, 3),
                   "plan_version": plan.version, "night_mode": night_mode}
        yield PipelineEvent(metrics=metrics)

    def rollback(self, project_id: str, to_version: int) -> dict[str, Any]:
        """Restaure une version antérieure via le journal du state_manager."""
        entry = self.state_manager.rollback(project_id, to_version)
        logger.info("rollback exécuté", extra={"project_id": project_id,
                                               "to_version": to_version})
        return entry

    # ---- handlers d'étapes (workflow 8 étapes, section 09) -----------------------
    def _step1(self, ctx: dict[str, Any]) -> str:
        """nl_to_skidl (+constraint_extractor, init graphe d'intention)."""
        context = self._agent_context(ctx)
        generated = self.agents["code_generator"].run(context)
        validated = self.agents["validator"].run(context)
        corrected = None
        if not validated.outputs.get("report", {}).get("passed", False):
            corrected = self.agents["corrector"].run(context)
        call_adapter("constraint_extractor", context.request_text)
        call_adapter("intent_graph_init", context.project_id)
        components = context.extra.get("skidl_components") or []
        nets = context.extra.get("skidl_nets") or []
        self._load_netlist(context.project_id, components, nets)
        self._mark_step(context.project_id, 1)
        parts = [f"SKiDL ({generated.outputs.get('source')})",
                 f"{len(components)} composant(s)", f"{len(nets)} net(s)"]
        if corrected is not None:
            parts.append(f"corrector: {corrected.outputs.get('iterations')} itération(s)")
        return " · ".join(parts)

    def _step2(self, ctx: dict[str, Any]) -> str:
        """planner → researcher → selector : BOM initial + contraintes."""
        context = self._agent_context(ctx)
        plan_result = self.agents["planner"].run(context)
        research = self.agents["researcher"].run(context)
        context.extra["research"] = research.outputs
        selection = self.agents["selector"].run(context)
        bom = context.bom
        self._merge_bom(context.project_id, bom)
        self._mark_step(context.project_id, 2)
        return (f"plan v{plan_result.outputs['plan']['version']} · "
                f"BOM {len(bom)} composant(s) · "
                f"rejets documentés : {len(selection.outputs.get('rejected', []))}")

    def _step3(self, ctx: dict[str, Any]) -> str:
        """Placement & routage RL — SOUS verrou de zone (l'humain prime)."""
        project_id = str(ctx["project_id"])
        state = self.state_manager.get_design(project_id)
        bbox = BBox(0.0, 0.0, state.board.width_mm, state.board.height_mm)
        lock = self.state_manager.acquire_zone(project_id, bbox, owner="rl_agent", ttl_s=300.0)
        if lock is None:
            raise SkipStep("verrou humain actif — rl_agent attend (l'humain gagne)")
        try:
            before = copy.deepcopy(state.board)
            placed = call_adapter("rl_place_route", state.board)
            verified = call_adapter("self_verify", state.board)
            self.state_manager.commit_delta(project_id, before, "rl_agent",
                                            "Étape 3 — placement & routage RL",
                                            result=placed.value)
        finally:
            self.state_manager.release_zone(project_id, lock.ticket_id)
        self._mark_step(project_id, 3)
        self._debit(project_id, Pricing.ROUTING_PASS)
        return (f"score DRC {placed.value.get('score')} · "
                f"{len(placed.value.get('moved', []))} déplacement(s) · "
                f"self_verifier ok={verified.value.get('ok')}")

    def _step4(self, ctx: dict[str, Any]) -> str:
        """Optimisation autonome de nuit (AutoPCB) — events par itérations."""
        project_id = str(ctx["project_id"])
        state = self.state_manager.get_design(project_id)
        optimized = call_adapter("night_optimizer", state.board, iterations=300)
        for it in range(1, 4):
            self.state_manager.publish_event(make_event(
                EventType.OPTIMIZER_ITERATION, project_id, "optimizer",
                design_version=state.version, iteration=it,
                score=round(optimized.value.get("score_after", 0.0) * it / 3.0, 2)))
        debit = self._debit(project_id, Pricing.NIGHT_OPTIMIZATION)
        self._mark_step(project_id, 4)
        return (f"{optimized.value.get('iterations')} itérations · "
                f"score {optimized.value.get('score_before')} → "
                f"{optimized.value.get('score_after')} · {debit}")

    def _step5(self, ctx: dict[str, Any]) -> str:
        """Vérification multi-physique — conflit thermique arbitrée si besoin."""
        project_id = str(ctx["project_id"])
        state = self.state_manager.get_design(project_id)
        checked = call_adapter("multi_physics", state.board)
        self.state_manager.publish_event(make_event(
            EventType.DRC_UPDATE, project_id, "simulator", design_version=state.version,
            max_temp_c=checked.value.get("max_temp_c"), thermal_ok=checked.value.get("thermal_ok")))
        if not checked.value.get("thermal_ok", True):
            self.state_manager.publish_event(make_event(
                EventType.CONSTRAINT_VIOLATED, project_id, "simulator",
                design_version=state.version, constraint="thermal"))
            resolution = self.super_agent.arbitrate(
                {"family": "thermal", "name": "max_temp_c"},
                {"family": "cost", "name": "layer_count"})
            note = f" · arbitrage: {resolution.resolution}"
        else:
            note = ""
        self._mark_step(project_id, 5)
        return (f"thermique ok={checked.value.get('thermal_ok')} "
                f"(max {checked.value.get('max_temp_c')}°C) · "
                f"SI ok={checked.value.get('si_ok')}{note}")

    def _step6(self, ctx: dict[str, Any]) -> str:
        """Modifications chirurgicales — verrou humain + bounding box d'impact."""
        edit = ctx.get("surgical_edit") or {}
        if not edit.get("targets"):
            raise SkipStep("aucune modification chirurgicale demandée")
        project_id = str(ctx["project_id"])
        state = self.state_manager.get_design(project_id)
        bbox = BBox(0.0, 0.0, state.board.width_mm, state.board.height_mm)
        lock = self.state_manager.acquire_zone(project_id, bbox,
                                               owner="human:surgical", ttl_s=120.0)
        if lock is None:
            raise SkipStep("zone verrouillée par rl_agent — attendez sa libération")
        try:
            before = copy.deepcopy(state.board)
            applied = call_adapter("scoped_edit", state.board,
                                   list(edit["targets"]), edit.get("transformation", {}))
            self.state_manager.commit_delta(project_id, before, "human:surgical",
                                            "Étape 6 — modification chirurgicale",
                                            result=applied.value)
        finally:
            self.state_manager.release_zone(project_id, lock.ticket_id)
        self._mark_step(project_id, 6)
        return f"edit appliqué à {applied.value.get('changed_refs')} (revue accept/reject)"

    def _step7(self, ctx: dict[str, Any]) -> str:
        """Génération firmware (Flux.ai) — émet FIRMWARE_REGENERATED."""
        project_id = str(ctx["project_id"])
        state = self.state_manager.get_design(project_id)
        built = call_adapter("firmware", [c.__dict__ for c in state.board.components.values()],
                             state.board)
        self.state_manager.publish_event(make_event(
            EventType.FIRMWARE_REGENERATED, project_id, "firmware_bridge",
            design_version=state.version, files=built.value.get("files"),
            hash=built.value.get("hash")))
        self._mark_step(project_id, 7)
        return f"firmware {built.value.get('framework')} · {len(built.value.get('files', []))} fichier(s)"

    def _step8(self, ctx: dict[str, Any]) -> str:
        """Export Gerber/ODB++ + rétroaction usine — émet EXPORT_READY."""
        project_id = str(ctx["project_id"])
        state = self.state_manager.get_design(project_id)
        exported = call_adapter("exporter", state.board, project_id)
        debit = self._debit(project_id, Pricing.GERBER_EXPORT)
        self.state_manager.publish_event(make_event(
            EventType.EXPORT_READY, project_id, "exporter", design_version=state.version,
            files=exported.value.get("files"),
            archive_key=exported.value.get("archive_key")))
        self._mark_step(project_id, 8)
        return f"{len(exported.value.get('files', []))} gerbers · {exported.value.get('archive_key')} · {debit}"

    # ---- utilitaires internes ------------------------------------------------------
    def _agent_context(self, ctx: dict[str, Any]) -> AgentContext:
        """Reconstitue l'AgentContext (état partagé du run courant)."""
        project_id = str(ctx["project_id"])
        state = self.state_manager.get_design(project_id)
        if self._context is None or self._context.project_id != project_id:
            self._context = AgentContext(project_id=project_id, board=state.board)
        self._context.board = state.board
        return self._context

    def _load_netlist(self, project_id: str, components: list[dict[str, Any]],
                      nets: list[dict[str, Any]]) -> None:
        """Charge composants + nets du script SKiDL dans le Board (versionné)."""
        if not components and not nets:
            return

        def mutator(board: Board) -> None:
            for raw in components:
                comp = Component(ref=str(raw.get("ref")), mpn=str(raw.get("mpn", "")),
                                 value=str(raw.get("value", "")),
                                 footprint=str(raw.get("footprint", "")),
                                 pins=int(raw.get("pins", 0)),
                                 power_w=float(raw.get("power_w", 0.0)),
                                 price_usd=float(raw.get("price_usd", 0.0)),
                                 stock=int(raw.get("stock", 0)))
                board.add_component(comp, Placement(ref=comp.ref))
            for raw in nets:
                name = str(raw.get("name"))
                board.nets[name] = Net(name=name,
                                       connections=[(str(r), str(p))
                                                    for r, p in raw.get("connections", [])])

        self.state_manager.mutate(project_id, "code_generator",
                                  "Étape 1 — netlist SKiDL chargée", mutator)

    def _merge_bom(self, project_id: str, bom: list[Component]) -> None:
        """Réconcilie le BOM validé avec le Board (empreintes, coûts, stocks)."""
        if not bom:
            return

        def mutator(board: Board) -> None:
            for comp in bom:
                if comp.ref in board.components:
                    board.components[comp.ref] = comp
                else:
                    board.add_component(comp, Placement(ref=comp.ref))

        self.state_manager.mutate(project_id, "selector",
                                  "Étape 2 — BOM initial validé", mutator)

    def _mark_step(self, project_id: str, step: int) -> None:
        state = self.state_manager.get_or_create(project_id)
        state.pipeline_step = step

    def _emit_progress(self, project_id: str, design_version: int, status: StepStatus) -> None:
        self.state_manager.publish_event(make_event(
            EventType.STEP_PROGRESS, project_id, "orchestrator",
            design_version=design_version, **status.to_json()))

    def _debit(self, project_id: str, item: Pricing) -> str:
        """Débite le grand livre + émet credit_debit — jamais bloquant."""
        if self.ledger is None:
            return ""
        try:
            tx = self.ledger.debit(project_id, item, user="orchestrator")
            self.state_manager.publish_event(self.ledger.to_event(tx))
            return f"débit {tx.amount_usd} USD"
        except InsufficientCredits as exc:
            return f"crédits insuffisants ({exc})"


# ---- Self-test bout-en-bout hors ligne ------------------------------------------
if __name__ == "__main__":  # pragma: no cover — scénario de démonstration
    configure_logging("orchestrator", "WARNING")   # logs discrets pour le rapport
    orchestrator = Orchestrator.create_default()
    PROJECT = "demo-drone-stm32"
    REQUEST = ("Carte drone : MCU STM32F405, radio LoRa 868 MHz, IMU MPU-6050, "
               "baro BMP280, GPS NEO-M8N, régulation buck-boost 2S, 4 ESC, USB-C.")

    print("=" * 72)
    print("SELF-TEST orchestrator — scénario « carte drone STM32 + LoRa » (hors ligne)")
    print("=" * 72)
    events = list(orchestrator.run_pipeline(PROJECT, REQUEST, night_mode=False))
    print(f"\n{'étape':>5}  {'état':<8} {'durée s':>8}  détail")
    escalations = 0
    for event in events:
        if event.step_status is not None:
            s = event.step_status
            print(f"{s.step:>5}  {s.state:<8} {s.duration_s:>8.3f}  {s.detail[:70]}")
        elif event.metrics is not None:
            m = event.metrics
            print(f"\nMétriques finales : drc={m.get('drc_score')} vias={m.get('via_count')} "
                  f"composants={m.get('components')} nets={m.get('nets')} "
                  f"non-routés={len(m.get('unrouted_nets', []))} "
                  f"plan v{m.get('plan_version')} en {m.get('duration_total_s')}s")
        else:
            escalations += 1
            print(f"ESCALATION : {str(event.escalation)[:90]}")
    state = orchestrator.state_manager.get_design(PROJECT)
    print(f"\nJournal : {len(orchestrator.state_manager.journal.read_all(PROJECT))} entrées "
          f"(data/projects/{PROJECT}/journal.jsonl) · version courante v{state.version}")
    plan = orchestrator.super_agent.plan_for(PROJECT)
    print(f"Plan : {plan.status} v{plan.version} · décisions mentales : "
          f"{len(orchestrator.super_agent.mental_model)} · audit : "
          f"{len(orchestrator.super_agent.audit_log)} entrées · escalations : {escalations}")
    print("\nRollback de démonstration vers v2 :",
          orchestrator.rollback(PROJECT, 2))
    print(f"Version après rollback : v{orchestrator.state_manager.get_design(PROJECT).version}")
    print("=" * 72)

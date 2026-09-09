"""Couche de services de la gateway — agrège orchestrator + state_manager.

Les routes REST, le serveur MCP et les resolvers GraphQL appellent tous CETTE
couche (jamais l'orchestrateur directement) : un seul point de composition.
Les dépendances optionnelles du dépôt racine (common.*) sont importées après
un garde-fou sys.path — ce module est aussi consommé hors entry point.
"""

from __future__ import annotations

import base64
import sys
import time
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if not (_ROOT / "common").is_dir():
    _ROOT = _ROOT.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.bus import InMemoryConstraintBus
from common.credits import CreditLedger, Pricing
from common.design_model import Board
from common.events import Event, EventType, make_event
from common.log import get_logger

from backend.orchestrator.main import Orchestrator
from backend.orchestrator.state_manager.journal import AuditJournal
from backend.orchestrator.state_manager.locks import BBox, ZoneConflictError
from backend.orchestrator.state_manager.manager import StateManager
from backend.orchestrator.super_agent.conflict_arbiter import ConflictArbiter
from backend.orchestrator.super_agent.resource_allocator import ResourceAllocator
from backend.orchestrator.super_agent.super_agent import SuperAgent

from .config import GatewaySettings, get_gateway_settings
from .middleware.auth import AuthService
from .middleware.rate_limit import RateLimiter

logger = get_logger("api_gateway.runtime")


class NotFoundError(LookupError):
    """Ressource inconnue — les routes traduisent en HTTP 404."""


# ---- sérialisation du design ----------------------------------------------------

def serialize_board(board: Board) -> dict[str, Any]:
    """Board → dict JSON-sûr (enums et dataclasses aplaties)."""
    return {
        "width_mm": board.width_mm, "height_mm": board.height_mm,
        "layers": [{"index": l.index, "name": l.name, "thickness_um": l.thickness_um}
                   for l in board.layers],
        "components": [
            {"ref": c.ref, "mpn": c.mpn, "value": c.value, "footprint": c.footprint,
             "pins": c.pins, "power_w": c.power_w, "price_usd": c.price_usd,
             "stock": c.stock, "functional_block": c.functional_block}
            for c in board.components.values()],
        "placements": [
            {"ref": p.ref, "x_mm": p.x_mm, "y_mm": p.y_mm,
             "rotation_deg": p.rotation_deg, "layer": p.layer, "locked": p.locked}
            for p in board.placements.values()],
        "nets": [
            {"name": n.name, "net_class": n.net_class, "is_routed": n.is_routed,
             "routed_length_mm": round(n.routed_length_mm, 1), "via_count": n.via_count,
             "connections": [list(c) for c in n.connections]}
            for n in board.nets.values()],
        "zones": [
            {"name": z.name, "kind": z.kind, "bbox": [z.x_min_mm, z.y_min_mm,
                                                      z.x_max_mm, z.y_max_mm]}
            for z in board.zones],
    }


def serialize_state(state: Any) -> dict[str, Any]:
    """DesignState complet — cible du GET /design/{id}/state et du type GraphQL Design."""
    return {
        "project_id": state.project_id, "version": state.version,
        "pipeline_step": state.pipeline_step,
        "board": serialize_board(state.board),
        "journal": state.journal[-20:],
    }


class GatewayRuntime:
    """Composition root : auth, quotas, crédits, state_manager, orchestrateur."""

    def __init__(self, settings: GatewaySettings | None = None) -> None:
        self.settings = settings or get_gateway_settings()
        self.bus = InMemoryConstraintBus()
        self.journal = AuditJournal(self.settings.data_dir)
        self.state_manager = StateManager(journal=self.journal,
                                          ws_target_latency_ms=self.settings.ws_target_latency_ms)
        self.ledger = CreditLedger(ledger_path=self.settings.credits_ledger_path)
        self.auth = AuthService(self.settings)
        self.rate_limiter = RateLimiter(self.settings)
        self.super_agent = SuperAgent(ResourceAllocator(), ConflictArbiter())
        self.orchestrator = Orchestrator(state_manager=self.state_manager,
                                         super_agent=self.super_agent,
                                         ledger=self.ledger, bus=self.bus)
        # registres mémoire (mono-nœud ; PostgreSQL/S3 en cluster — section 07)
        self.projects: dict[str, dict[str, Any]] = {}
        self.pipeline_runs: dict[str, dict[str, Any]] = {}
        self.exports: dict[str, dict[str, Any]] = {}
        self.files: dict[str, dict[str, Any]] = {}
        self.started_at = time.time()
        # clé API de démonstration (agents externes) — imprimable via self-test
        self.demo_api_key = self.auth.register_api_key("external-agents", tier="pro")

    # ---- projets -----------------------------------------------------------------
    def create_project(self, name: str, request_text: str = "",
                       tier: str = "free") -> dict[str, Any]:
        """Commande create_project — venue de la conversation chat_interface."""
        project_id = f"prj-{uuid.uuid4().hex[:10]}"
        state = self.state_manager.get_or_create(project_id, request_text)
        project = {"id": project_id, "name": name, "tier": tier,
                   "created_at": time.time(), "version": state.version,
                   "pipeline_step": state.pipeline_step}
        self.projects[project_id] = project
        self.state_manager.publish_event(make_event(
            EventType.PLAN_UPDATED, project_id, "gateway",
            design_version=state.version, action="project_created", name=name))
        return project

    def list_projects(self) -> list[dict[str, Any]]:
        return list(self.projects.values())

    def get_project(self, project_id: str) -> dict[str, Any]:
        project = self.projects.get(project_id)
        if project is None:
            raise NotFoundError(f"projet inconnu : {project_id}")
        project = dict(project)
        state = self.state_manager.get_or_create(project_id)
        project.update(version=state.version, pipeline_step=state.pipeline_step)
        return project

    # ---- pipeline -----------------------------------------------------------------
    def run_pipeline(self, project_id: str, request_text: str = "",
                     steps: list[int] | None = None,
                     night_mode: bool = False) -> dict[str, Any]:
        """Consomme le générateur de l'orchestrateur → statut persisté."""
        pipeline_id = f"pipe-{uuid.uuid4().hex[:10]}"
        statuses: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {}
        escalations: list[str] = []
        for event in self.orchestrator.run_pipeline(project_id, request_text,
                                                    steps=steps, night_mode=night_mode):
            if event.step_status is not None:
                statuses.append(event.step_status.to_json())
            elif event.metrics is not None:
                metrics = event.metrics
            else:
                escalations.append(event.escalation or "")
        run = {"pipeline_id": pipeline_id, "project_id": project_id,
               "statuses": statuses, "metrics": metrics, "escalations": escalations,
               "state": statuses[-1]["state"] if statuses else "pending",
               "created_at": time.time()}
        self.pipeline_runs[pipeline_id] = run
        return run

    def get_run(self, pipeline_id: str) -> dict[str, Any]:
        run = self.pipeline_runs.get(pipeline_id)
        if run is None:
            raise NotFoundError(f"pipeline inconnu : {pipeline_id}")
        return run

    # ---- design -----------------------------------------------------------------
    def get_design_state(self, project_id: str) -> dict[str, Any]:
        return serialize_state(self.state_manager.get_or_create(project_id))

    def rollback(self, project_id: str, to_version: int) -> dict[str, Any]:
        try:
            return self.orchestrator.rollback(project_id, to_version)
        except KeyError as exc:
            raise NotFoundError(str(exc)) from exc

    # ---- édition chirurgicale ---------------------------------------------------------
    def apply_surgical_edit(self, project_id: str, targets: list[str],
                            transformation: dict[str, Any],
                            author: str = "human:surgical",
                            dry_run: bool = False) -> dict[str, Any]:
        """Bounding box d'impact → verrou humain → edit → revue accept/reject."""
        state = self.state_manager.get_or_create(project_id)
        bbox = self._impact_bbox(state.board, targets)
        lock = self.state_manager.acquire_zone(project_id, bbox, owner=author, ttl_s=120.0)
        if lock is None:
            raise ZoneConflictError("zone verrouillée par rl_agent — l'agent attendra")
        try:
            import copy
            before = copy.deepcopy(state.board)
            applied = self.super_agent.adapter_call(
                "scoped_edit", state.board, targets, transformation)
            result = {"changed_refs": applied.value.get("changed_refs", []),
                      "source": applied.source, "impact_bbox": list(bbox.__dict__.values())}
            if not dry_run:
                entry = self.state_manager.commit_delta(
                    project_id, before, author,
                    f"Modification chirurgicale de {result['changed_refs']}",
                    result=result)
                result["entry"] = entry
        finally:
            self.state_manager.release_zone(project_id, lock.ticket_id)
        result["review"] = {"status": "accepted" if not dry_run else "pending_accept",
                            "targets": targets, "transformation": transformation}
        return result

    @staticmethod
    def _impact_bbox(board: Board, targets: list[str], margin_mm: float = 2.0) -> BBox:
        """Bounding box d'impact = union des empreintes des cibles + marge."""
        xs: list[float] = []
        ys: list[float] = []
        for ref in targets:
            comp = board.components.get(ref)
            place = board.placements.get(ref)
            if comp is None or place is None:
                continue
            x0, y0, x1, y1 = comp.bounding_box(place)
            xs += [x0, x1]
            ys += [y0, y1]
        if not xs:
            return BBox(0.0, 0.0, board.width_mm, board.height_mm)
        return BBox(max(0.0, min(xs) - margin_mm), max(0.0, min(ys) - margin_mm),
                    min(board.width_mm, max(xs) + margin_mm),
                    min(board.height_mm, max(ys) + margin_mm))

    # ---- exports / fichiers -----------------------------------------------------------
    def export_gerbers(self, project_id: str) -> dict[str, Any]:
        """Export via adaptateur + enregistrement du job + événement."""
        state = self.state_manager.get_or_create(project_id)
        exported = self.super_agent.adapter_call("exporter", state.board, project_id)
        job_id = f"exp-{uuid.uuid4().hex[:10]}"
        job = {"job_id": job_id, "project_id": project_id,
               "files": exported.value.get("files", []),
               "archive_key": exported.value.get("archive_key", ""),
               "status": "ready", "created_at": time.time()}
        self.exports[job_id] = job
        self.state_manager.publish_event(make_event(
            EventType.EXPORT_READY, project_id, "gateway",
            design_version=state.version, job_id=job_id,
            files=job["files"], archive_key=job["archive_key"]))
        return job

    def presigned_download_url(self, job_id: str, ttl_s: int = 900) -> str:
        """Presigned URL SIMULÉE (S3 en cluster — section 07) ; aucun appel réseau."""
        job = self.exports.get(job_id)
        if job is None:
            raise NotFoundError(f"export inconnu : {job_id}")
        expires = int(time.time()) + ttl_s
        signature = uuid.uuid5(uuid.NAMESPACE_URL, f"{job_id}:{expires}").hex
        return (f"https://{self.settings.service_name}.local/"
                f"{job['archive_key']}?X-PCB-Expires={expires}&X-PCB-Signature={signature}")

    def store_upload(self, filename: str, content_b64: str) -> dict[str, Any]:
        """Upload « multipart simulé » → adaptateur parser → résumé stocké."""
        try:
            content = base64.b64decode(content_b64 or "", validate=False)
        except Exception as exc:  # noqa: BLE001 — base64 invalide
            raise ValueError(f"content_b64 invalide : {exc}") from exc
        parsed = self.super_agent.adapter_call("parser", filename, content_b64)
        key = f"file-{uuid.uuid4().hex[:10]}"
        record = {"key": key, "filename": filename, "size_bytes": len(content),
                  "summary": parsed.value.get("summary", ""),
                  "components": parsed.value.get("components", []),
                  "nets": parsed.value.get("nets", []),
                  "adapter_source": parsed.source, "created_at": time.time()}
        self.files[key] = record
        return record

    # ---- santé ---------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Readiness/liveness — consommé par /health et Kubernetes."""
        try:
            project_count = len(self.projects)
            state_ok = True
        except Exception:  # noqa: BLE001 — sonde défensive
            project_count, state_ok = -1, False
        return {
            "status": "ok" if state_ok else "degraded",
            "readiness": state_ok,
            "liveness": True,
            "uptime_s": round(time.time() - self.started_at, 1),
            "checks": {"state_manager": "ok" if state_ok else "ko",
                       "ledger": "ok", "bus": "ok"},
            "projects": project_count,
        }


_RUNTIME: GatewayRuntime | None = None


def get_runtime() -> GatewayRuntime:
    """Singleton runtime — réinitialisable en tests via `set_runtime(None)`."""
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = GatewayRuntime()
    return _RUNTIME


def set_runtime(runtime: GatewayRuntime | None) -> None:
    global _RUNTIME
    _RUNTIME = runtime

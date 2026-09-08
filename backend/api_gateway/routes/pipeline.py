"""Router /pipeline — déclenchement et suivi du workflow 8 étapes.

POST /pipeline/run → orchestrator.run_pipeline (générateur consommé, les
StepStatus alimentent aussi l'event WebSocket step_progress) ; renvoie un
pipeline_id. GET /pipeline/{pipeline_id}/status → étapes 1..8 avec état.
"""

from __future__ import annotations

from typing import Any

from ..compat import APIRouter, BaseModel
from ..runtime import get_runtime
from . import raise_http_for

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class PipelineRunRequest(BaseModel):
    """Corps de POST /pipeline/run — miroir de RunPipelineRequest (proto)."""

    project_id: str
    request_text: str = ""       # vide si la netlist est déjà importée
    steps: list[int] = []        # [1..8] — vide = workflow complet
    night_mode: bool = False     # étape 4 en arrière-plan (batch)


class PipelineRunResponse(BaseModel):
    """Réponse : pipeline_id + statuts initiaux des étapes."""

    pipeline_id: str
    project_id: str
    state: str
    statuses: list[dict[str, Any]]


@router.post("/run")
def run_pipeline(body: PipelineRunRequest) -> PipelineRunResponse:
    """Déclenche le pipeline — coûteux (quota routing_pass côté middleware)."""
    try:
        run = get_runtime().run_pipeline(
            body.project_id, request_text=body.request_text,
            steps=body.steps or None, night_mode=body.night_mode)
        return PipelineRunResponse(pipeline_id=str(run["pipeline_id"]),
                                   project_id=str(run["project_id"]),
                                   state=str(run["state"]),
                                   statuses=list(run["statuses"]))
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)


@router.get("/{pipeline_id}/status")
def get_status(pipeline_id: str) -> dict[str, Any]:
    """État des étapes 1..8 (done/failed/skipped + durées + escalations)."""
    try:
        run = get_runtime().get_run(pipeline_id)
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)
    return {"pipeline_id": run["pipeline_id"], "project_id": run["project_id"],
            "state": run["state"], "steps": run["statuses"],
            "metrics": run.get("metrics", {}),
            "escalations": run.get("escalations", [])}

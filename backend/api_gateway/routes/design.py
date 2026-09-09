"""Router /design — état complet (cible GraphQL) et rollback versionné.

GET  /design/{id}/state → DesignState sérialisé (board + version + journal).
POST /design/{id}/rollback {to_version} → restauration via le journal.
"""

from __future__ import annotations

from typing import Any

from ..compat import APIRouter, BaseModel
from ..runtime import get_runtime, serialize_state
from . import raise_http_for

router = APIRouter(prefix="/design", tags=["design"])


class RollbackRequest(BaseModel):
    """Corps de POST /design/{id}/rollback — version cible (miroir proto)."""

    to_version: int


@router.get("/{project_id}/state")
def get_state(project_id: str) -> dict[str, Any]:
    """État complet — alimente le viewer 3D et le type GraphQL `Design`."""
    try:
        return get_runtime().get_design_state(project_id)
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)


@router.get("/{project_id}/metrics")
def get_metrics(project_id: str) -> dict[str, Any]:
    """Métriques compactes (drc_score, vias, nets non routés) — dashboards."""
    try:
        runtime = get_runtime()
        return runtime.state_manager.metrics(project_id)
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)


@router.post("/{project_id}/rollback")
def rollback(project_id: str, body: RollbackRequest) -> dict[str, Any]:
    """Restaure une version antérieure (event rollback_performed émis)."""
    try:
        entry = get_runtime().rollback(project_id, body.to_version)
        state = get_runtime().state_manager.get_or_create(project_id)
        return {"rollback": entry, "current_version": state.version,
                "state": serialize_state(state)}
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)

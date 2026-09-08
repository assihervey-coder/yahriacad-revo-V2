"""Router /projects — création depuis la conversation chat_interface.

POST /projects → commande create_project (runtime → state_manager + event),
GET /projects → liste, GET /projects/{id} → détail versionné.
"""

from __future__ import annotations

from typing import Any

from ..compat import APIRouter, BaseModel
from ..runtime import get_runtime
from . import raise_http_for

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    """Corps de POST /projects — demandé par le chat_interface."""

    name: str
    request_text: str = ""       # cahier des charges en langage naturel
    tier: str = "free"


class ProjectOut(BaseModel):
    """Réponse projet — version + étape courante du pipeline."""

    id: str
    name: str
    tier: str
    version: int
    pipeline_step: int
    created_at: float


def _to_out(project: dict[str, Any]) -> ProjectOut:
    return ProjectOut(id=str(project["id"]), name=str(project.get("name", "")),
                      tier=str(project.get("tier", "free")),
                      version=int(project.get("version", 1)),
                      pipeline_step=int(project.get("pipeline_step", 0)),
                      created_at=float(project.get("created_at", 0.0)))


@router.post("")
def create_project(body: ProjectCreate) -> ProjectOut:
    """Crée le projet + son DesignState v1 (event plan_updated émis)."""
    try:
        return _to_out(get_runtime().create_project(
            body.name, request_text=body.request_text, tier=body.tier))
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)


@router.get("")
def list_projects() -> dict[str, Any]:
    """Liste des projets connus de la gateway (mono-nœud)."""
    runtime = get_runtime()
    return {"projects": [_to_out(p).model_dump() for p in runtime.list_projects()],
            "count": len(runtime.projects)}


@router.get("/{project_id}")
def get_project(project_id: str) -> ProjectOut:
    """Détail d'un projet — 404 si inconnu."""
    try:
        return _to_out(get_runtime().get_project(project_id))
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)

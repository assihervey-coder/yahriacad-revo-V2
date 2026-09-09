"""Router /edits — modifications chirurgicales avec revue accept/reject.

POST /edits/scoped {project_id, target, transformation} :
1. bounding box d'impact calculée (union des empreintes cibles + marge) ;
2. verrou de zone humain (l'humain gagne sur rl_agent — state_manager.locks) ;
3. edit via adaptateur `scoped_edit` + re-route local (fallback simulé) ;
4. revue accept/reject retournée au client (dry_run = prévisualisation).
"""

from __future__ import annotations

from typing import Any

from ..compat import APIRouter, BaseModel
from ..runtime import get_runtime
from . import raise_http_for

router = APIRouter(prefix="/edits", tags=["edits"])


class ScopedEditRequest(BaseModel):
    """Corps de POST /edits/scoped — cible(s) + transformation bornée."""

    project_id: str
    target: str | list[str]                # ref unique ("U1") ou liste
    transformation: dict[str, Any] = {}    # {"move": {"dx_mm", "dy_mm"}, "rotate_deg"}
    dry_run: bool = False                  # prévisualisation sans commit


class ScopedEditResponse(BaseModel):
    """Réponse : refs modifiées + bbox d'impact + statut de revue."""

    project_id: str
    changed_refs: list[str]
    impact_bbox: list[float]
    review: dict[str, Any]
    source: str


@router.post("/scoped")
def scoped_edit(body: ScopedEditRequest) -> ScopedEditResponse:
    """Édition chirurgicale — jamais concurrente du RL sur la même zone."""
    targets = [body.target] if isinstance(body.target, str) else list(body.target)
    try:
        result = get_runtime().apply_surgical_edit(
            body.project_id, targets, body.transformation, dry_run=body.dry_run)
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)
    return ScopedEditResponse(
        project_id=body.project_id,
        changed_refs=list(result.get("changed_refs", [])),
        impact_bbox=[float(v) for v in result.get("impact_bbox", [])],
        review=dict(result.get("review", {})),
        source=str(result.get("source", "service")))

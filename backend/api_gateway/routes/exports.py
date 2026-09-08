"""Router /exports — suivi des exports et téléchargement (URL signée simulée).

GET  /exports/{job_id}           → statut du job (fichiers, archive_key).
POST /exports/{job_id}/download  → presigned URL SIMULÉE (S3 en cluster) ;
                                   aucun appel réseau n'est effectué.
"""

from __future__ import annotations

from typing import Any

from ..compat import APIRouter, BaseModel
from ..runtime import get_runtime
from . import raise_http_for

router = APIRouter(prefix="/exports", tags=["exports"])


class DownloadResponse(BaseModel):
    """Réponse de téléchargement — URL pré-signée + expiration."""

    job_id: str
    download_url: str
    expires_in_s: int


@router.get("/{job_id}")
def get_export(job_id: str) -> dict[str, Any]:
    """Statut du job d'export (créé via /exports (POST) ou l'étape 8)."""
    try:
        return dict(get_runtime().exports[job_id])
    except KeyError as exc:
        raise_http_for(KeyError(f"export inconnu : {job_id}"))
        raise  # never reached — aide les analyseurs statiques
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)


@router.post("")
def create_export(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lance un export Gerber immédiat (débit GERBER_EXPORT côté runtime)."""
    project_id = str((payload or {}).get("project_id", ""))
    if not project_id:
        raise_http_for(ValueError("project_id requis"))
    try:
        return dict(get_runtime().export_gerbers(project_id))
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)


@router.post("/{job_id}/download")
def download(job_id: str) -> DownloadResponse:
    """Retourne une URL signée simulée — signature uuid5 déterministe."""
    try:
        url = get_runtime().presigned_download_url(job_id)
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)
    return DownloadResponse(job_id=job_id, download_url=url, expires_in_s=900)

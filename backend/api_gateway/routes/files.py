"""Router /files — upload « multipart simulé » et consultation.

POST /files/upload {filename, content_b64} → adaptateur parser (import du
service écrit en parallèle, fallback simulation) → résumé stocké.
GET  /files/{key} → métadonnées du fichier importé.
"""

from __future__ import annotations

from typing import Any

from ..compat import APIRouter, BaseModel
from ..runtime import get_runtime
from . import raise_http_for

router = APIRouter(prefix="/files", tags=["files"])


class FileUploadRequest(BaseModel):
    """Corps de POST /files/upload — multipart simulé (bytes encodés base64)."""

    filename: str
    content_b64: str = ""        # netlist SPICE / schéma KiCad / CSV BOM


class FileUploadResponse(BaseModel):
    """Réponse d'upload — clé + résumé du parsing."""

    key: str
    filename: str
    size_bytes: int
    summary: str
    adapter_source: str          # "service" | "fallback"


@router.post("/upload")
def upload(body: FileUploadRequest) -> FileUploadResponse:
    """Import d'un fichier → parser (les composants/nets enjambent l'étape 1)."""
    try:
        record = get_runtime().store_upload(body.filename, body.content_b64)
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)
    return FileUploadResponse(key=str(record["key"]),
                              filename=str(record["filename"]),
                              size_bytes=int(record["size_bytes"]),
                              summary=str(record["summary"]),
                              adapter_source=str(record["adapter_source"]))


@router.get("/{key}")
def get_file(key: str) -> dict[str, Any]:
    """Métadonnées + résumé du parsing — 404 si clé inconnue."""
    try:
        return dict(get_runtime().files[key])
    except KeyError as exc:
        raise_http_for(KeyError(f"fichier inconnu : {key}"))
        raise  # never reached — aide les analyseurs statiques
    except Exception as exc:  # noqa: BLE001
        raise_http_for(exc)

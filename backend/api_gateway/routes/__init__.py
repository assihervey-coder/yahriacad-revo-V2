"""Routers REST de la gateway — un module par ressource (section 04).

Chaque route : modèles pydantic Request/Response (fallback dataclass-like via
compat.py), logique DÉLÉGUÉE à la couche runtime (adapters orchestrator),
événements émis par le state_manager. Erreurs normalisées : 400 validation,
402 crédits, 404 inconnu, 409 conflit de verrou.
"""

from __future__ import annotations

from typing import NoReturn

from common.credits import InsufficientCredits

from ..compat import HTTPException
from ..runtime import NotFoundError
from backend.orchestrator.state_manager.locks import ZoneConflictError


def raise_http_for(exc: Exception) -> NoReturn:
    """Traduit les exceptions applicatives en HTTPException normalisées."""
    if isinstance(exc, NotFoundError):
        raise HTTPException(404, str(exc))
    if isinstance(exc, InsufficientCredits):
        raise HTTPException(402, str(exc))
    if isinstance(exc, ZoneConflictError):
        raise HTTPException(409, str(exc))
    if isinstance(exc, (ValueError, KeyError)):
        raise HTTPException(400, str(exc))
    raise HTTPException(500, f"{type(exc).__name__}: {exc}")

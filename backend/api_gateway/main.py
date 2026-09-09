"""Point d'entrée de la api_gateway — FastAPI (ou stub) : REST + GraphQL + WS + MCP.

`uvicorn backend.api_gateway.main:app` monte le point d'entrée unique du
système (section 04) : routers REST (upload / status / download), middleware
auth + rate limiting, WebSocket /ws pour le routage en direct (latence cible
< 100 ms, reprise par delta), /health pour les probes Kubernetes, /metrics
Prometheus. Sans fastapi installé, `FastAPIStub` offre le même dispatch en
synchrone — le self-test `python main.py` exerce alors toute la gateway.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.api_gateway.compat import (FASTAPI_AVAILABLE, APIRouter, FastAPIStub,
                                         HTTPException)
from backend.api_gateway.config import get_gateway_settings
from backend.api_gateway.middleware.auth import AuthError, AuthService
from backend.api_gateway.middleware.rate_limit import RateLimitExceeded, RateLimiter
from backend.api_gateway.runtime import get_runtime
from backend.api_gateway.routes import projects, pipeline, design, edits, credits, exports, files

try:  # import gardé : serveur de production optionnel
    import uvicorn  # noqa: F401
except ImportError:  # pragma: no cover
    uvicorn = None

try:  # import gardé : métriques Prometheus optionnelles
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
except ImportError:  # pragma: no cover
    CONTENT_TYPE_LATEST = "text/plain; charset=utf-8"
    generate_latest = None


def _health_payload() -> dict[str, Any]:
    """Readiness/liveness Kubernetes — état du runtime + latences cibles."""
    from common.config import get_settings

    runtime = get_runtime()
    settings = get_gateway_settings()
    common = get_settings()
    return {
        "status": "serving",
        "service": "api_gateway",
        "env": settings.env,
        "projects": len(runtime.list_projects()),
        "targets": {"websocket_latency_ms": common.websocket_target_latency_ms,
                    "bus_latency_ms": common.bus_target_latency_ms},
    }


def _build_app(force_stub: bool = False) -> Any:
    """Assemble l'application : routers, guards auth/rate-limit, health/metrics.

    force_stub=True retourne toujours un FastAPIStub (self-test synchrone,
    tests hors serveur) même si fastapi est installé.
    """
    if FASTAPI_AVAILABLE and not force_stub:
        from fastapi import FastAPI

        app = FastAPI(title="pcb_ai_designer_v2 gateway", version="2.0.0",
                      description="Point d'entrée unique : REST + GraphQL + WebSocket + MCP")
        app.include_router(projects.router)
        app.include_router(pipeline.router)
        app.include_router(design.router)
        app.include_router(edits.router)
        app.include_router(credits.router)
        app.include_router(exports.router)
        app.include_router(files.router)

        @app.get("/health")
        def health() -> dict[str, Any]:
            return _health_payload()

        @app.get("/metrics")
        def metrics() -> Any:
            from fastapi import Response

            if generate_latest is None:
                return Response(content="prometheus_client absent (dev)\n",
                                media_type=CONTENT_TYPE_LATEST)
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

        return app

    # ---- mode stub : même surface, dispatch synchrone (tests hors fastapi) ----
    app = FastAPIStub()
    for router in (projects.router, pipeline.router, design.router, edits.router,
                   credits.router, exports.router, files.router):
        app.include_router(router)

    health_router = APIRouter(prefix="/health", tags=["ops"])

    @health_router.get("")
    def health_stub() -> dict[str, Any]:
        return _health_payload()

    app.include_router(health_router)

    auth_service = AuthService(get_gateway_settings())
    rate_limiter = RateLimiter(get_gateway_settings())

    # Routes publiques : probes Kubernetes (liveness/readiness) sans authentification
    PUBLIC_PATHS = {"/health", "/metrics"}

    def guard(context: dict[str, Any]) -> None:
        """Applique auth + rate limit avant chaque dispatch (ordre : 401 puis 429)."""
        if context.get("path", "") in PUBLIC_PATHS:
            return
        headers = context.get("headers") or {}
        try:
            identity = auth_service.authenticate(headers)
            rate_limiter.check(identity.sub, tier=identity.tier,
                               action=context.get("path", "/"))
        except AuthError as exc:
            raise HTTPException(exc.status_code, exc.detail)
        except RateLimitExceeded as exc:
            raise HTTPException(429, str(exc))
        context["identity"] = identity

    app.add_guard(guard)
    app._auth_service = auth_service   # exposé pour le self-test (clé de dev)
    return app


app = _build_app()
_stub_app = _build_app(force_stub=True)


def main() -> int:
    """Point d'entrée CLI : serveur uvicorn si dispo, sinon self-test du stub."""
    settings = get_gateway_settings()
    if FASTAPI_AVAILABLE and uvicorn is not None and "--serve" in sys.argv:
        uvicorn.run("backend.api_gateway.main:app", host="0.0.0.0",
                    port=settings.gateway_port, log_level=settings.log_level.lower())
        return 0
    # ---- self-test : dispatch synchrone via le stub (fastapi ou non) --------
    app_mode = "FastAPI" if FASTAPI_AVAILABLE else "stub"
    print(f"[gateway] mode {app_mode} — self-test synchrone (ajouter --serve pour uvicorn) :")
    # clé API de test : le middleware auth l'exige pour les routes protégées
    dev_key = _stub_app._auth_service.register_api_key("selftest", scopes=["read", "write"])
    auth_headers = {"X-API-Key": dev_key}
    for method, path, payload in [
        ("GET", "/health", None),
        ("POST", "/projects", {"name": "Carte drone", "request_text": "carte drone STM32 + LoRa"}),
        ("GET", "/credits", None),
        ("GET", "/routes/inconnue", None),
    ]:
        status, body = _stub_app.handle(method, path, payload, headers=auth_headers)
        label = str(body)[:80]
        print(f"  {method:<4s} {path:<22s} → {status} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

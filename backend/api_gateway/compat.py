"""Compatibilité optionnelle — stubs minimalistes si fastapi/pydantic absents.

Objectif : que `compileall` ET les imports fonctionnent sur toute machine,
même sans les dépendances listées dans requirements.txt. Chaque stub implémente
le sous-ensemble réellement utilisé par la gateway :
- `BaseModel` (pydantic) : modèle request/response avec `model_dump()` ;
- `HTTPException` : erreur HTTP transportable ;
- `APIRouter` : enregistrement (method, path) → handler ;
- `FastAPIStub` : table de routes + `handle(method, path, payload, headers)`
  pour tester la gateway entière sans serveur (dispatch synchrone).
Si fastapi/pydantic sont installés, ce module n'expose QUE des alias —
aucun comportement n'est dévié.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable

try:  # ---- pydantic ---------------------------------------------------------
    from pydantic import BaseModel            # type: ignore
    PYDANTIC_AVAILABLE = True
except ImportError:                           # stub dataclass-like minimal
    PYDANTIC_AVAILABLE = False

    class BaseModel:  # type: ignore[no-redef]
        """Squelette pydantic-compatible : annotations + valeurs par défaut."""

        def __init__(self, **data: Any) -> None:
            hints = getattr(self, "__annotations__", {}) or {}
            for name in hints:
                if name in data:
                    setattr(self, name, data[name])
                elif hasattr(type(self), name):
                    setattr(self, name, getattr(type(self), name))
                else:
                    setattr(self, name, None)

        def model_dump(self) -> dict[str, Any]:
            return {name: getattr(self, name)
                    for name in getattr(self, "__annotations__", {})}

        def dict(self) -> dict[str, Any]:     # alias pydantic v1
            return self.model_dump()


try:  # ---- fastapi ----------------------------------------------------------
    from fastapi import APIRouter, HTTPException   # type: ignore
    FASTAPI_AVAILABLE = True
except ImportError:                             # stubs documentés
    FASTAPI_AVAILABLE = False

    class HTTPException(Exception):  # type: ignore[no-redef]
        """Erreur HTTP transportable — identique à fastapi.HTTPException."""

        def __init__(self, status_code: int, detail: Any = "") -> None:
            super().__init__(str(detail))
            self.status_code = status_code
            self.detail = detail

    class APIRouter:  # type: ignore[no-redef]
        """Enregistre (method, path, handler) — surface fastapi minimale."""

        def __init__(self, prefix: str = "", tags: list[str] | None = None) -> None:
            self.prefix = prefix
            self.tags = tags or []
            self.routes: list[tuple[str, str, Callable[..., Any]]] = []

        def _register(self, method: str, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                self.routes.append((method, self.prefix + path, fn))
                return fn
            return decorator

        def get(self, path: str, **_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            return self._register("GET", path)

        def post(self, path: str, **_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            return self._register("POST", path)

        def put(self, path: str, **_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            return self._register("PUT", path)

        def websocket(self, path: str, **_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            return self._register("WS", path)


class FastAPIStub:
    """Gateway sans serveur : `handle(method, path, payload)` → (status, body).

    Toujours disponible (même si fastapi est installé) : sert au self-test
    synchrone et aux tests hors serveur. Les middlewares (auth, rate limit)
    sont exécutés avant le dispatch via `add_guard(fn)`.
    """

    def __init__(self, **_kwargs: Any) -> None:
        self.route_table: dict[tuple[str, str], Callable[..., Any]] = {}
        self._guards: list[Callable[[dict[str, Any]], None]] = []

    def include_router(self, router: APIRouter, **_kwargs: Any) -> None:
        """Enregistre les routes d'un router — tuples du stub OU APIRoute fastapi."""
        for route in router.routes:
            if isinstance(route, tuple):
                method, path, fn = route
                self.route_table[(method, path)] = fn
                continue
            methods = getattr(route, "methods", None) or {"GET"}
            for method in methods:
                self.route_table[(method.upper(), route.path)] = route.endpoint

    def add_guard(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self._guards.append(fn)

    def middleware(self, _kind: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Décorateur accepté pour compatibilité — sans effet en mode stub
        (les gardes auth/rate-limit sont branchés via `add_guard`)."""
        return lambda fn: fn

    def handle(self, method: str, path: str, payload: dict[str, Any] | None = None,
               headers: dict[str, str] | None = None) -> tuple[int, Any]:
        """Dispatch synchrone : → (status_code, body) — pour les tests."""
        context = {"method": method.upper(), "path": path,
                   "payload": payload or {}, "headers": headers or {}}
        try:
            for guard in self._guards:
                guard(context)
            method_up = method.upper()
            fn = self.route_table.get((method_up, path))
            params: dict[str, str] = {}
            if fn is None:
                for (m, pattern), handler in self.route_table.items():
                    if m != method_up:
                        continue
                    found = _match(pattern, path)
                    if found is not None:
                        fn, params = handler, found
                        break
            if fn is None:
                return 404, {"detail": f"route inconnue : {method} {path}"}
            return 200, _invoke(fn, params, context["payload"], context["headers"])
        except HTTPException as exc:
            return exc.status_code, {"detail": exc.detail}
        except Exception as exc:  # noqa: BLE001 — erreurs applicatives
            return 500, {"detail": f"{type(exc).__name__}: {exc}"}


def _match(pattern: str, path: str) -> dict[str, str] | None:
    """Match `/{param}/...` — retourne les params extraits ou None."""
    regex = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$"
    found = re.match(regex, path)
    return found.groupdict() if found else None


def _invoke(fn: Callable[..., Any], path_params: dict[str, str],
            payload: dict[str, Any], headers: dict[str, str]) -> Any:
    """Appelle un handler avec ses paramètres (path / body pydantic / query).

    `eval_str=True` résout les annotations paresseuses (`from __future__ import
    annotations`) — sans cela les annotations arrivent en str et le body
    pydantic ne serait jamais injecté.
    """
    try:
        signature = inspect.signature(fn, eval_str=True)
    except (ValueError, NameError):
        signature = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        annotation = parameter.annotation
        if name in path_params:
            kwargs[name] = _coerce(annotation, path_params[name])
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            kwargs[name] = annotation(**(payload or {}))
        elif name in (payload or {}):
            kwargs[name] = _coerce(annotation, payload[name])
        elif name in headers:
            kwargs[name] = headers[name]
    return fn(**kwargs)


def _coerce(annotation: Any, raw: Any) -> Any:
    """Coercition minimale str → int/float pour les paramètres de chemin."""
    if annotation in (int, float) and isinstance(raw, str):
        try:
            return annotation(raw)
        except ValueError:
            return raw
    return raw

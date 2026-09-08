"""Configuration de la gateway — env-only (JWT_SECRET, GATEWAY_PORT, quotas).

Aucune valeur n'est lue à l'import d'autres modules : `get_gateway_settings()`
est injecté explicitement (même discipline que common.config.Settings).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Quotas différenciés par palier — clé « action/fenêtre », valeur = nombre max.
# Une passe de routage (5/jour en free) ne coûte pas pareil qu'un export
# (10/jour) : le rate limiter pondère chaque action par son coût unitaire.
DEFAULT_QUOTAS: dict[str, dict[str, int]] = {
    "free": {
        "default/day": 200,          # requêtes génériques
        "routing_pass/day": 5,       # POST /pipeline/run
        "export/day": 10,            # POST /exports/{id}/download
        "surgical_edit/day": 20,     # POST /edits/scoped
    },
    "pro": {
        "default/day": 5000,
        "routing_pass/day": 200,
        "export/day": 500,
        "surgical_edit/day": 1000,
    },
}


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


@dataclass
class GatewaySettings:
    """Paramètres d'exécution de la gateway (section 04)."""

    service_name: str = "api_gateway"
    env: str = field(default_factory=lambda: _env("APP_ENV", "development"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    # Réseau
    gateway_port: int = field(default_factory=lambda: _env_int("GATEWAY_PORT", 8000))

    # JWT — secret via env uniquement (jamais de valeur en dur en production)
    jwt_secret: str = field(
        default_factory=lambda: _env("JWT_SECRET", "dev-insecure-secret-change-me"))
    jwt_issuer: str = "pcb-ai-designer-gateway"
    jwt_expires_min: int = field(default_factory=lambda: _env_int("JWT_EXPIRES_MIN", 60))

    # Rate limiting (fenêtre glissante par token)
    quotas: dict[str, dict[str, int]] = field(
        default_factory=lambda: {tier: dict(q) for tier, q in DEFAULT_QUOTAS.items()})

    # WebSocket — latence cible de diffusion (section 03)
    ws_target_latency_ms: int = field(default_factory=lambda: _env_int("WS_TARGET_LATENCY_MS", 100))

    # Persistance (réutilise la convention data/ du dépôt)
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data/projects")))
    credits_ledger_path: Path = field(
        default_factory=lambda: Path(_env("CREDITS_LEDGER_PATH", "data/projects/credits.json")))

    def as_dict(self) -> dict:
        """Sérialisation sûre pour les logs de démarrage (secrets masqués)."""
        payload = {k: ("***" if "secret" in k else v) for k, v in self.__dict__.items()}
        payload["data_dir"] = str(self.data_dir)
        payload["credits_ledger_path"] = str(self.credits_ledger_path)
        return payload


_SETTINGS: GatewaySettings | None = None


def get_gateway_settings() -> GatewaySettings:
    """Singleton de configuration — surcharge test via `os.environ` avant appel."""
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = GatewaySettings()
    return _SETTINGS

"""Configuration transversale — lecture d'environnement sans dépendance externe.

Chaque microservice instancie un `Settings` dérivé de variables d'environnement
(voir .env.example). Aucune valeur n'est lue au moment de l'import d'autres
modules : la configuration est explicitement injectée, ce qui facilite les tests
et la reproductibilité exigée par la brique Circuitron (.infra/docker_toolchain).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(key: str, default: str) -> str:
    value = os.environ.get(key, default)
    return value.strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Paramètres d'exécution d'un microservice."""

    # Identité du service (utilisé par les logs, Prometheus et Kubernetes)
    service_name: str = field(default_factory=lambda: _env("SERVICE_NAME", "pcb-service"))
    env: str = field(default_factory=lambda: _env("APP_ENV", "development"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    # Réseau gRPC / HTTP
    grpc_host: str = field(default_factory=lambda: _env("GRPC_HOST", "0.0.0.0"))
    grpc_port: int = field(default_factory=lambda: _env_int("GRPC_PORT", 50051))
    gateway_port: int = field(default_factory=lambda: _env_int("GATEWAY_PORT", 8000))

    # Persistance (section 07 de la spécification)
    s3_endpoint: str = field(default_factory=lambda: _env("S3_ENDPOINT", "http://localhost:9000"))
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET", "pcb-projects"))
    neo4j_uri: str = field(default_factory=lambda: _env("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_user: str = field(default_factory=lambda: _env("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD", "pcb-dev"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))

    # LLM / RAG (briques Siemens Fuse + Circuitron)
    # llm_provider : "local" (extractif déterministe) | "ollama" (LLM local natif)
    #              | "openai-compatible" (toute API /v1/chat/completions)
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "local"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "nemotron-70b"))
    llm_api_base: str = field(default_factory=lambda: _env("LLM_API_BASE", "http://localhost:11434/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "bge-m3"))
    vector_store_dir: Path = field(
        default_factory=lambda: Path(_env("VECTOR_STORE_DIR", "data/trained_models/vector_store"))
    )

    # LLM local Ollama — API native (distincte du point d'entrée OpenAI-compatible)
    ollama_base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_timeout_s: float = field(default_factory=lambda: _env_float("OLLAMA_TIMEOUT_S", 8.0))
    ollama_num_ctx: int = field(default_factory=lambda: _env_int("OLLAMA_NUM_CTX", 4096))

    # GPU (brique C++/CUDA du simulateur)
    enable_cuda: bool = field(default_factory=lambda: _env_bool("ENABLE_CUDA", False))

    # Latences cibles de la spécification
    websocket_target_latency_ms: int = field(
        default_factory=lambda: _env_int("WS_TARGET_LATENCY_MS", 100)
    )
    bus_target_latency_ms: int = field(default_factory=lambda: _env_int("BUS_TARGET_LATENCY_MS", 50))
    fast_eval_budget_s: float = field(default_factory=lambda: _env_float("FAST_EVAL_BUDGET_S", 5.0))

    # Crédits (brique DeepPCB)
    credits_ledger_path: Path = field(
        default_factory=lambda: Path(_env("CREDITS_LEDGER_PATH", "data/projects/credits.json"))
    )

    def as_dict(self) -> dict:
        """Sérialisation sûre pour les logs de démarrage (secrets masqués)."""
        payload = {
            k: (v if "password" not in k and "api_key" not in k else "***")
            for k, v in self.__dict__.items()
        }
        payload["vector_store_dir"] = str(self.vector_store_dir)
        payload["credits_ledger_path"] = str(self.credits_ledger_path)
        return payload


_SETTINGS: Settings | None = None


def get_settings(service_name: str | None = None, **overrides) -> Settings:
    """Retourne la configuration courante (singleton par service).

    `service_name` est réinjecté si fourni — chaque binaire appelle
    `get_settings("parser")` au démarrage de son `main()`.
    """
    global _SETTINGS
    if _SETTINGS is None or service_name is not None or overrides:
        _SETTINGS = Settings()
        if service_name:
            _SETTINGS.service_name = service_name
        for key, value in overrides.items():
            setattr(_SETTINGS, key, value)
    return _SETTINGS

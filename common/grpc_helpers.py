"""Helpers gRPC — démarrage homogène des sept microservices internes.

Chaque service suit le même contrat (section 06) : API ProtoBuf versionnée,
journalisation structurée, métriques Prometheus, probes readiness/liveness.
Les stubs générés (`proto_gen/`) sont optionnels : sans génération préalable
(`make proto`), le serveur démarre en mode « logique seule » — le cœur
algorithmique reste testable sans dépendance gRPC.
"""

from __future__ import annotations

import signal
import sys
from concurrent import futures
from typing import Callable, Optional

from common.config import Settings, get_settings
from common.log import configure_logging, get_logger


def proto_available() -> bool:
    """True si les stubs ont été générés via `make proto`."""
    try:
        import grpc  # noqa: F401
        from grpc_tools import protoc  # noqa: F401
        import proto_gen  # noqa: F401
        return True
    except ImportError:
        return False


def serve_grpc(
    service_name: str,
    add_servicer: Callable[[object, "grpc.Server"], None],  # noqa: F821
    port: Optional[int] = None,
    max_workers: int = 8,
) -> None:
    """Démarre un serveur gRPC standardisé pour un microservice.

    `add_servicer(servicer, server)` doit appeler
    `pb2_grpc.add_XServicer_to_server(servicer, server)`.
    Intercepte SIGINT/SIGTERM pour un arrêt propre (arrêt gracieux Kubernetes).
    """
    settings: Settings = get_settings(service_name)
    configure_logging(service_name, settings.log_level)
    logger = get_logger(service_name)

    if not proto_available():
        logger.error(
            "stubs gRPC absents — exécuter « make proto » avant de lancer le service",
            extra={"service": service_name},
        )
        sys.exit(2)

    import grpc
    from grpc_health.v1 import health, health_pb2, health_pb2_grpc

    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=[
            ("grpc.keepalive_time_ms", 30_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.max_message_length", 64 * 1024 * 1024),  # designs denses
        ],
    )
    add_servicer(None, server)  # placeholder réel fourni par le main() du service

    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)

    bound_port = server.add_insecure_port(f"{settings.grpc_host}:{port or settings.grpc_port}")
    server.start()
    logger.info("service gRPC démarré", extra={"port": bound_port, "service": service_name})

    stop = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        signal.signal(signal.SIGTERM, lambda *_: server.stop(grace=5.0))
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("arrêt demandé (SIGINT)", extra={"service": service_name})
        server.stop(grace=5.0)


def channel_for(service_address: str) -> "grpc.Channel":  # noqa: F821
    """Canal gRPC réutilisable entre orchestrator et services internes."""
    import grpc

    return grpc.insecure_channel(
        service_address,
        options=[("grpc.enable_retries", 1), ("grpc.keepalive_time_ms", 30_000)],
    )

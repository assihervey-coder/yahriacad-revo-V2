"""Tests du transport gRPC optionnel de l'orchestrateur (audit P3 — branchement).

Sans ORCH_DISTRIBUTED (défaut), aucun appel réseau : la voie in-process et le
fallback restent la référence. Avec ORCH_DISTRIBUTED=1 + stubs générés
(`make proto`), un serveur Parser minimal éphémère valide le chemin complet :
mapping clé d'adaptateur -> RPC proto v1, canal réutilisé, MessageToDict.
En CI sans grpcio/stubs, les tests réseau sont sautés proprement (skipif).
"""

from __future__ import annotations

import base64
from concurrent import futures
from pathlib import Path

import pytest

from backend.orchestrator import adapters, grpc_transport

REPO_ROOT = Path(__file__).resolve().parents[2]
_PROTO_GEN = REPO_ROOT / "backend" / "proto_gen"
_GRPC_STUBS = _PROTO_GEN.is_dir() and grpc_transport.grpc_available()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Aucun test ne doit dépendre d'un ORCH_DISTRIBUTED hérité de l'extérieur."""
    monkeypatch.delenv("ORCH_DISTRIBUTED", raising=False)
    grpc_transport.reset_channels()
    yield
    grpc_transport.reset_channels()


def test_disabled_by_default_keeps_offline_paths():
    """Sans ORCH_DISTRIBUTED : zéro réseau, source « service » ou « fallback »."""
    assert grpc_transport.distributed_enabled() is False
    result = adapters.call_adapter("constraint_extractor", "carte drone STM32")
    assert result.source in {"service", "fallback"}
    assert result.source != "grpc"


def test_adapter_map_matches_proto_contracts():
    """Chaque entrée du mapping vise un RPC réellement déclaré en proto v1."""
    declared = {entry.rpc for entry in grpc_transport.ADAPTER_RPC.values()}
    assert {"ImportFile", "NaturalLanguageToSkidl", "ExtractConstraints",
            "ProposePlacement", "VerifyAction", "RunNightOptimization",
            "Simulate", "Check", "Export", "GenerateHeaders"} <= declared
    # Les clés sans contrat proto restent volontairement hors mapping
    assert not {"intent_graph_init", "scoped_edit"} & set(grpc_transport.ADAPTER_RPC)


def test_call_grpc_is_none_without_flag():
    """grpcio + stubs présents mais drapeau absent -> None (voie in-process)."""
    if not _GRPC_STUBS:
        pytest.skip("grpcio + stubs (make proto) requis")
    assert grpc_transport.call_grpc("parser", "a.net", "") is None


@pytest.mark.skipif(not _GRPC_STUBS, reason="grpcio + stubs (make proto) requis")
def test_roundtrip_parser_over_real_grpc(monkeypatch):
    """Chemin complet : call_adapter -> stub généré -> serveur -> MessageToDict."""
    monkeypatch.setenv("ORCH_DISTRIBUTED", "1")
    stubs = grpc_transport._stubs()
    assert stubs is not None and "parser" in stubs

    import grpc

    parser_pb2 = stubs["parser"]["pb2"]
    parser_pb2_grpc = stubs["parser"]["pb2_grpc"]

    class _FakeParser(parser_pb2_grpc.ParserServicer):
        """Émulation minimale du service Parser (contrat pcb.parser.v1)."""

        def ImportFile(self, request, context):
            assert request.filename == "drone.net"
            assert request.content == b"V R1 1 2"
            return parser_pb2.ImportReport(ok=True, components_imported=2,
                                           nets_imported=3,
                                           warnings=["aller-retour de test"])

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    parser_pb2_grpc.add_ParserServicer_to_server(_FakeParser(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        from backend.orchestrator.super_agent.resource_allocator import SERVICE_ENDPOINTS

        monkeypatch.setitem(SERVICE_ENDPOINTS, "parser", f"127.0.0.1:{port}")
        result = adapters.call_adapter(
            "parser", "drone.net", base64.b64encode(b"V R1 1 2").decode())
        assert result.source == "grpc"
        assert result.value["ok"] is True
        assert result.value["components_imported"] == 2
        assert result.value["warnings"] == ["aller-retour de test"]
    finally:
        server.stop(grace=0.1)


@pytest.mark.skipif(not _GRPC_STUBS, reason="grpcio + stubs (make proto) requis")
def test_allocator_probe_reports_reachability(monkeypatch):
    """probe=True en mode distribué : sonde réelle ; sinon reachable reste None."""
    from backend.orchestrator.super_agent.resource_allocator import (
        Priority,
        ResourceAllocator,
    )

    allocator = ResourceAllocator()
    monkeypatch.setenv("ORCH_DISTRIBUTED", "1")
    probed = allocator.allocate("rl_placer", "ai_engine",
                                Priority.INTERACTIVE, probe=True)
    assert probed.reachable is False   # aucun service réel sur 50052 dans le sandbox

    monkeypatch.delenv("ORCH_DISTRIBUTED", raising=False)
    silent = allocator.allocate("rl_placer", "ai_engine",
                                Priority.INTERACTIVE, probe=True)
    assert silent.reachable is None    # hors mode distribué : aucun réseau

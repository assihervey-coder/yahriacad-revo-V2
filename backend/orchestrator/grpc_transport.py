"""Transport gRPC de l'orchestrateur — mode distribué optionnel (section 06).

Branche les 4 points d'extension documentés par l'audit de complétude
(adapters, resource_allocator, super_agent, base_agent) sur les stubs générés
depuis `proto/*/v1/*.proto` (cible `make proto` -> backend/proto_gen).

Trois conditions pour qu'un appel passe par le réseau :

1. ``ORCH_DISTRIBUTED=1`` — désactivé par défaut (voie in-process) ;
2. ``grpcio`` installé ;
3. stubs générés (``make proto``).

Sinon ``grpc_ready()`` renvoie False et les appelants restent sur la voie
in-process puis sur le fallback de simulation : ZÉRO appel réseau par défaut,
la suite de tests reste 100 % hors ligne (contrat d'exécutabilité du dépôt).

Import des stubs sans collision : les fichiers générés s'importent en absolu
(``from common.v1 import common_pb2``) alors que ``common`` désigne déjà le
socle partagé du dépôt (common/config.py…). Les modules générés sont donc
chargés par emplacement fichier (importlib) puis enregistrés sous leur nom
littéral dans ``sys.modules`` — sans toucher à ``sys.path``, donc sans
écraser aucun package du dépôt.

En mode distribué, la valeur retournée suit le contrat proto v1 (dictionnaire
issu de MessageToDict, snake_case préservé) — les appelants qui consomment des
champs précis tolèrent les deux formes (voir adapters.call_adapter).
"""

from __future__ import annotations

import base64
import importlib
import importlib.util
import os
import sys
import threading
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from common.log import get_logger

logger = get_logger("orchestrator.grpc_transport")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROTO_GEN = _REPO_ROOT / "backend" / "proto_gen"

# Ordre de chargement : `common` d'abord — toutes les autres protos l'importent.
_PROTO_PKGS = ("common", "parser", "ai_engine", "simulator", "router",
               "drc_dfm", "exporter", "firmware_bridge", "orchestrator")

_DEFAULT_TIMEOUT_S = 5.0

_STUBS: dict[str, dict[str, Any]] | None = None
_STUBS_LOCK = threading.Lock()
_CHANNELS: dict[str, Any] = {}


# ---- activation -----------------------------------------------------------------

def distributed_enabled() -> bool:
    """True si ORCH_DISTRIBUTED active explicitement le mode distribué."""
    return os.environ.get("ORCH_DISTRIBUTED", "").strip().lower() in {"1", "true", "yes", "on"}


def grpc_available() -> bool:
    """grpcio + stubs générés présents (indépendant du drapeau d'activation)."""
    try:
        importlib.import_module("grpc")
    except ImportError:
        return False
    return _stubs() is not None


def grpc_ready() -> bool:
    """Les trois conditions du mode distribué sont réunies."""
    return distributed_enabled() and grpc_available()


# ---- chargement des stubs (sans collision de noms) ------------------------------

def _register_shell(name: str, path: Path) -> None:
    """Enregistre une coquille de package générée (ex. « parser.v1 ») si absente."""
    if name in sys.modules:
        return
    shell = types.ModuleType(name)
    shell.__path__ = [str(path)]
    sys.modules[name] = shell


def _stubs() -> dict[str, dict[str, Any]] | None:
    """Charge (une fois) les stubs proto_gen — None s'ils sont absents/cassés."""
    global _STUBS
    if _STUBS is not None:
        return _STUBS
    with _STUBS_LOCK:
        if _STUBS is not None:
            return _STUBS
        if not _PROTO_GEN.is_dir():
            return None
        loaded: dict[str, dict[str, Any]] = {}
        try:
            for pkg in _PROTO_PKGS:
                pkg_dir = _PROTO_GEN / pkg / "v1"
                if not pkg_dir.is_dir():
                    continue
                # « common » est déjà le package du socle partagé (importé ci-dessus) :
                # on n'enregistre jamais de coquille concurrente pour lui.
                if pkg != "common":
                    _register_shell(pkg, _PROTO_GEN / pkg)
                v1_name = f"{pkg}.v1"
                _register_shell(v1_name, pkg_dir)
                mods: dict[str, Any] = {}
                for suffix in ("pb2", "pb2_grpc"):
                    mod_name = f"{v1_name}.{pkg}_{suffix}"
                    if mod_name in sys.modules:
                        mods[suffix] = sys.modules[mod_name]
                        continue
                    file = pkg_dir / f"{pkg}_{suffix}.py"
                    if not file.exists():
                        continue
                    spec = importlib.util.spec_from_file_location(mod_name, file)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[mod_name] = module
                    spec.loader.exec_module(module)  # type: ignore[union-attr]
                    setattr(sys.modules[v1_name], f"{pkg}_{suffix}", module)
                    mods[suffix] = module
                if "pb2" in mods and "pb2_grpc" in mods:
                    loaded[pkg] = mods
        except Exception as exc:  # stubs partiels — voie in-process conservée
            logger.warning("stubs proto_gen indisponibles (%s) — voie in-process", exc)
            return None
        _STUBS = loaded or None
        return _STUBS


# ---- canaux ----------------------------------------------------------------------

def _channel(endpoint_key: str) -> Any:
    """Canal réutilisable par endpoint (caché ; options keepalive de grpc_helpers)."""
    if endpoint_key in _CHANNELS:
        return _CHANNELS[endpoint_key]
    from backend.orchestrator.super_agent.resource_allocator import SERVICE_ENDPOINTS

    endpoint = SERVICE_ENDPOINTS.get(endpoint_key)
    if not endpoint:
        raise KeyError(f"aucun endpoint déclaré pour « {endpoint_key} »")
    from common.grpc_helpers import channel_for

    channel = channel_for(endpoint)
    _CHANNELS[endpoint_key] = channel
    return channel


def probe_channel(endpoint: str, timeout_s: float = 2.0) -> bool:
    """Sonde ``channel_ready_future`` — détection précoce d'un service absent."""
    import grpc

    try:
        grpc.channel_ready_future(grpc.insecure_channel(endpoint)).result(timeout=timeout_s)
        return True
    except Exception:
        return False


def agent_channel(service: str) -> Any:
    """Canal gRPC du service cible pour un agent — None hors mode distribué."""
    if not grpc_ready():
        return None
    try:
        return _channel(service)
    except Exception as exc:
        logger.warning("canal %s indisponible (%s)", service, exc)
        return None


def reset_channels() -> None:
    """Ferme les canaux en cache (tests / arrêt propre)."""
    for channel in _CHANNELS.values():
        try:
            channel.close()
        except Exception:
            pass
    _CHANNELS.clear()


# ---- conversion Board <-> proto (contrat common.v1) ------------------------------

def _arg(args: tuple, kwargs: dict, index: int, key: str, default: Any = "") -> Any:
    if len(args) > index:
        return args[index]
    return kwargs.get(key, default)


def _board(args: tuple, kwargs: dict) -> Any:
    """Extrait le Board (duck-typing : components + placements) puis le convertit."""
    candidate = _arg(args, kwargs, 0, "board", None)
    if candidate is None or not hasattr(candidate, "placements"):
        for obj in tuple(args) + tuple(kwargs.values()):
            if hasattr(obj, "placements") and hasattr(obj, "components"):
                candidate = obj
                break
    if candidate is None:
        raise LookupError("Board absent — conversion proto impossible")
    return _board_to_proto(candidate)


def _board_to_proto(board: Any) -> Any:
    """Convertit common.design_model.Board en pcb.common.v1.Board (sans perte)."""
    common_pb2 = sys.modules["common.v1.common_pb2"]
    out = common_pb2.Board(width_mm=float(board.width_mm),
                           height_mm=float(board.height_mm),
                           layer_count=len(getattr(board, "layers", []) or [0, 0, 0, 0]))
    for comp in board.components.values():
        out.components.append(common_pb2.Component(
            ref=comp.ref,
            mpn=getattr(comp, "mpn", ""), value=getattr(comp, "value", ""),
            footprint=getattr(comp, "footprint", ""),
            pins=int(getattr(comp, "pins", 0) or 0),
            width_mm=float(getattr(comp, "width_mm", 0.0)),
            height_mm=float(getattr(comp, "height_mm", 0.0)),
            power_w=float(getattr(comp, "power_w", 0.0)),
            price_usd=float(getattr(comp, "price_usd", 0.0)),
            stock=int(getattr(comp, "stock", 0) or 0),
            functional_block=getattr(comp, "functional_block", "")))
    for place in board.placements.values():
        out.placements.append(common_pb2.Placement(
            ref=place.ref, x_mm=float(place.x_mm), y_mm=float(place.y_mm),
            rotation_deg=float(getattr(place, "rotation_deg", 0.0)),
            layer=int(getattr(place, "layer", 0) or 0),
            locked=bool(getattr(place, "locked", False))))
    for net in board.nets.values():
        proto_net = out.nets.add()
        proto_net.name = net.name
        proto_net.connections.extend(f"{ref}/{pad}" for ref, pad in net.connections)
        proto_net.net_class = getattr(net, "net_class", "default")
        if getattr(net, "impedance_target_ohm", None):
            proto_net.impedance_target_ohm = float(net.impedance_target_ohm)
        if getattr(net, "length_match_group", None):
            proto_net.length_match_group = net.length_match_group
        for seg in net.routed_segments:
            proto_seg = proto_net.routed_segments.add()
            proto_seg.net = seg.net
            proto_seg.start.x_mm = float(seg.x1_mm)
            proto_seg.start.y_mm = float(seg.y1_mm)
            proto_seg.end.x_mm = float(seg.x2_mm)
            proto_seg.end.y_mm = float(seg.y2_mm)
            proto_seg.layer = int(seg.layer)
            proto_seg.width_mm = float(getattr(seg, "width_mm", 0.2))
            proto_seg.is_via = bool(getattr(seg, "is_via", False))
    return out


# ---- mapping clé d'adaptateur -> RPC proto v1 ------------------------------------

@dataclass(frozen=True)
class _Rpc:
    """Une entrée du mapping : stub + RPC + constructeur de requête."""

    pkg: str                     # paquet proto_gen (service hôte du RPC)
    endpoint_key: str            # clé SERVICE_ENDPOINTS (resource_allocator)
    stub: str                    # classe du stub généré
    rpc: str                     # méthode RPC
    request: str                 # message de requête
    build: Callable[..., dict]   # (args, kwargs) -> champs du message
    streaming: bool = False      # réponse en stream (collectée en liste)


ADAPTER_RPC: dict[str, _Rpc] = {
    "parser": _Rpc("parser", "parser", "ParserStub", "ImportFile", "ImportFileRequest",
                   build=lambda a, k: {
                       "filename": str(_arg(a, k, 0, "filename", "")),
                       "content": base64.b64decode(_arg(a, k, 1, "content_b64", "") or "")}),
    "nl_to_skidl": _Rpc("parser", "parser", "ParserStub", "NaturalLanguageToSkidl",
                        "NaturalLanguageRequest",
                        build=lambda a, k: {
                            "request_text": str(_arg(a, k, 0, "request_text", "")),
                            "target_format": "skidl"}),
    "constraint_extractor": _Rpc("parser", "parser", "ParserStub", "ExtractConstraints",
                                 "ExtractConstraintsRequest",
                                 build=lambda a, k: {
                                     "netlist_or_spec": str(_arg(a, k, 0, "request_text", ""))}),
    "rl_place_route": _Rpc("ai_engine", "ai_engine", "AiEngineStub", "ProposePlacement",
                           "ProposeActionRequest",
                           build=lambda a, k: {
                               "board": _board(a, k),
                               "candidates": int(k.get("candidates", 8) or 8)}),
    "self_verify": _Rpc("ai_engine", "ai_engine", "AiEngineStub", "VerifyAction",
                        "VerifyActionRequest",
                        build=lambda a, k: {"board": _board(a, k)}),
    "night_optimizer": _Rpc("ai_engine", "ai_engine", "AiEngineStub", "RunNightOptimization",
                            "RunNightOptimizationRequest",
                            build=lambda a, k: {
                                "board": _board(a, k),
                                "max_iterations": int(_arg(a, k, 1, "iterations", 300) or 300)},
                            streaming=True),
    "multi_physics": _Rpc("simulator", "simulator", "SimulatorStub", "Simulate",
                          "SimulateRequest",
                          build=lambda a, k: {"board": _board(a, k),
                                              "physics": "multi_physics"},
                          streaming=True),
    "drc_dfm": _Rpc("drc_dfm", "drc_dfm", "DrcDfmStub", "Check", "CheckRequest",
                    build=lambda a, k: {"board": _board(a, k)}),
    "exporter": _Rpc("exporter", "exporter", "ExporterStub", "Export", "ExportRequest",
                     build=lambda a, k: {"board": _board(a, k), "outputs": ["gerber"]}),
    "firmware": _Rpc("firmware_bridge", "firmware_bridge", "FirmwareBridgeStub",
                     "GenerateHeaders", "ExportPinsRequest",
                     build=lambda a, k: {"board": _board(a, k),
                                         "target": str(k.get("target", "zephyr"))}),
}

# Clés SANS équivalent proto v1 (intent_graph_init, intent_graph_record,
# scoped_edit, erc_executor) : elles restent sur la voie in-process — le
# graphe d'intention et les éditions bornées sont des API Python internes.


# ---- appel -----------------------------------------------------------------------

def call_grpc(key: str, *args: Any, timeout_s: float | None = None, **kwargs: Any) -> Any:
    """Appel RPC réel pour la clé d'adaptateur — None si la voie est indisponible."""
    entry = ADAPTER_RPC.get(key)
    if entry is None or not grpc_ready():
        return None
    try:
        mods = (_stubs() or {}).get(entry.pkg)
        if mods is None:
            return None
        from google.protobuf.json_format import MessageToDict

        stub_cls = getattr(mods["pb2_grpc"], entry.stub)
        request = getattr(mods["pb2"], entry.request)(**entry.build(args, kwargs))
        rpc = getattr(stub_cls(_channel(entry.endpoint_key)), entry.rpc)
        limit = timeout_s or _DEFAULT_TIMEOUT_S
        if entry.streaming:
            return [MessageToDict(event, preserving_proto_field_name=True)
                    for event in rpc(request, timeout=limit)]
        return MessageToDict(rpc(request, timeout=limit), preserving_proto_field_name=True)
    except Exception as exc:
        logger.warning("RPC %s en échec (%s) — bascule in-process", key, exc)
        _CHANNELS.pop(entry.endpoint_key, None)  # canal douteux : recréé au prochain appel
        return None


def try_grpc(key: str, args: tuple, kwargs: dict) -> Any:
    """Point d'entrée adapters.call_adapter — None = rester sur la voie in-process."""
    if not grpc_ready():
        return None
    return call_grpc(key, *args, **kwargs)

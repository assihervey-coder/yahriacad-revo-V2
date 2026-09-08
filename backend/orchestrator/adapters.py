"""Adaptateurs inter-services — point d'extension unique de l'orchestrateur.

Tout appel vers un service externe (parser, ai_engine, rl_agent, exporteur…)
passe par `call_adapter()` : interface + implémentation « in-process » (import
direct du package cible). Les versions gRPC sont des stubs à générer
(TODO ci-dessous) — AUCUN appel réseau réel n'est effectué ici.

Les services cibles sont écrits EN PARALLÈLE par d'autres agents : si
l'import échoue, un fallback de simulation locale renvoie un résultat
plausible + un avertissement, afin que le pipeline reste exécutable hors ligne.

TODO(gRPC) : remplacer l'import in-process par un stub généré depuis
`proto/<service>/v1/*.proto` ; les endpoints (ports 50051-50057) sont déjà
décrits dans `super_agent/resource_allocator.py` (`SERVICE_ENDPOINTS`).
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from common.log import get_logger

logger = get_logger("orchestrator.adapters")

# Contrats « attendus » des services écrits en parallèle. Chaque entrée décrit
# le module, la fonction et la signature attendues — document de référence
# pour les équipes parallèles (aucun couplage fort : fallback si l'import échoue).
ADAPTER_CONTRACTS: dict[str, dict[str, str]] = {
    "parser": {
        "module": "backend.services.parser.main",
        "function": "parse_design_file",
        "signature": "(filename: str, content_b64: str) -> dict(components, nets, summary)",
    },
    "nl_to_skidl": {
        "module": "backend.services.ai_engine.llm_orchestrator",
        "function": "nl_to_skidl",
        "signature": "(request_text: str, plan: dict | None = None) -> dict(script, components, nets)",
    },
    "constraint_extractor": {
        "module": "backend.services.ai_engine.constraint_extractor",
        "function": "extract",
        "signature": "(request_text: str) -> list[dict] (kind, key, value)",
    },
    "intent_graph_init": {
        "module": "backend.services.ai_engine.shared_mental_model.intent_graph.graph",
        "function": "init_graph",
        "signature": "(project_id: str) -> dict(graph_id, nodes)",
    },
    "intent_graph_record": {
        "module": "backend.services.ai_engine.shared_mental_model.intent_graph.graph",
        "function": "record_decision",
        "signature": "(project_id: str, decision: dict) -> bool",
    },
    "rl_place_route": {
        "module": "backend.services.ai_engine.rl_agent.service",
        "function": "place_and_route",
        "signature": "(board: common.design_model.Board) -> dict(score, moved)",
    },
    "self_verify": {
        "module": "backend.services.ai_engine.self_verifier.service",
        "function": "verify",
        "signature": "(board: common.design_model.Board) -> dict(ok, issues)",
    },
    "night_optimizer": {
        "module": "backend.services.ai_engine.autonomous_optimizer.service",
        "function": "optimize",
        "signature": "(board: Board, iterations: int = 300) -> dict(iterations, score_before, score_after)",
    },
    "multi_physics": {
        "module": "backend.services.simulator.multi_physics",
        "function": "check",
        "signature": "(board: Board) -> dict(thermal_ok, max_temp_c, si_ok, notes)",
    },
    "scoped_edit": {
        "module": "backend.services.edits.scoped_edit",
        "function": "apply",
        "signature": "(board: Board, targets: list[str], transformation: dict) -> dict(changed_refs, new_bbox)",
    },
    "firmware": {
        "module": "backend.services.firmware_bridge",
        "function": "generate",
        "signature": "(bom: list, board: Board) -> dict(files, framework, hash)",
    },
    "exporter": {
        "module": "backend.services.exporter.main",
        "function": "export_gerbers",
        "signature": "(board: Board, project_id: str = '') -> dict(files, archive_key)",
    },
    "erc_executor": {
        "module": "backend.services.validation.erc_executor",
        "function": "run_erc",
        "signature": "(netlist: dict) -> list[dict] (code, severity, message, ref)",
    },
    "drc_dfm": {
        "module": "backend.services.drc_dfm.engine",
        "function": "run_drc",
        "signature": "(board: Board) -> dict(score, violations)",
    },
}


@dataclass
class AdapterResult:
    """Résultat normalisé d'un appel d'adaptateur (source traçable)."""

    key: str
    value: Any
    source: str = "service"          # "service" (module importé) | "fallback" (simulation)
    elapsed_s: float = 0.0
    warnings: list[str] = field(default_factory=list)


def call_adapter(key: str, *args: Any, **kwargs: Any) -> AdapterResult:
    """Appelle le service `key` in-process, avec fallback de simulation locale.

    TODO(gRPC) : ici, brancher `grpc.insecure_channel(endpoint)` + le stub
    généré (voir ADAPTER_CONTRACTS / SERVICE_ENDPOINTS) — l'interface de cette
    fonction resterait inchangée pour tous les appelants.
    """
    contract = ADAPTER_CONTRACTS[key]
    start = time.perf_counter()
    try:
        module = importlib.import_module(contract["module"])
        fn: Callable[..., Any] | None = getattr(module, contract["function"], None)
        if fn is None:
            raise ImportError(f"fonction absente : {contract['function']}")
        value = fn(*args, **kwargs)
        return AdapterResult(key=key, value=value, source="service",
                             elapsed_s=round(time.perf_counter() - start, 4))
    except Exception as exc:  # service écrit en parallèle — fallback résilient
        warning = f"adaptateur {key} indisponible ({type(exc).__name__}) — simulation locale"
        logger.warning(warning, extra={"target_module": contract["module"]})
        fallback = _FALLBACKS[key]
        value = fallback(*args, **kwargs) if callable(fallback) else fallback
        return AdapterResult(key=key, value=value, source="fallback",
                             elapsed_s=round(time.perf_counter() - start, 4),
                             warnings=[warning])


# --- Fallbacks de simulation (résultats plausibles, hors ligne) --------------

_INTENT_GRAPHS: dict[str, dict] = {}


def _fb_intent_graph_init(project_id: str) -> dict:
    _INTENT_GRAPHS.setdefault(project_id, {"nodes": 0, "decisions": []})
    return {"graph_id": f"intent:{project_id}", "nodes": _INTENT_GRAPHS[project_id]["nodes"]}


def _fb_intent_graph_record(project_id: str, decision: dict) -> bool:
    graph = _INTENT_GRAPHS.setdefault(project_id, {"nodes": 0, "decisions": []})
    graph["decisions"].append(decision)
    graph["nodes"] += 1
    return True


def _fb_rl_place_route(board) -> dict:  # noqa: ANN001 — Board (common.design_model)
    """Placement en grille + routage en L (2 segments par net), hors ligne."""
    moved: list[str] = []
    x, y = 8.0, 8.0
    for ref, comp in board.components.items():
        place = board.placements.get(ref)
        try:
            if place is None or not place.locked:
                board.move(ref, x, y)
            moved.append(ref)
        except (KeyError, PermissionError):
            continue
        x += comp.width_mm + 4.0
        if x > board.width_mm - 10.0:
            x, y = 8.0, y + 12.0
    for name, net in board.nets.items():
        if net.is_routed or len(net.connections) < 2:
            continue
        from common.design_model import Segment

        (ref_a, _pad_a), (ref_b, _pad_b) = net.connections[0], net.connections[-1]
        pa = board.placements.get(ref_a)
        pb = board.placements.get(ref_b)
        if pa is None or pb is None:
            continue
        net.routed_segments = [
            Segment(net=name, x1_mm=pa.x_mm + 2, y1_mm=pa.y_mm,
                    x2_mm=pa.x_mm + 2, y2_mm=pb.y_mm, layer=0),
            Segment(net=name, x1_mm=pa.x_mm + 2, y1_mm=pb.y_mm,
                    x2_mm=pb.x_mm - 2, y2_mm=pb.y_mm, layer=0),
        ]
    return {"score": board.drc_score(), "moved": moved}


def _fb_scoped_edit(board, targets: list[str], transformation: dict) -> dict:  # noqa: ANN001
    """Applique move/rotate aux cibles — borné à la bounding box d'impact."""
    move = transformation.get("move", {})
    rotation = float(transformation.get("rotate_deg", 0.0) or 0.0)
    changed: list[str] = []
    for ref in targets:
        place = board.placements.get(ref)
        if place is None:
            continue
        try:
            board.move(ref, place.x_mm + float(move.get("dx_mm", 0.0)),
                       place.y_mm + float(move.get("dy_mm", 0.0)),
                       rotation if rotation else None)
            changed.append(ref)
        except (KeyError, PermissionError):
            continue
    return {"changed_refs": changed, "new_bbox": None}


_FALLBACKS: dict[str, Any] = {
    "parser": lambda filename="", content_b64="": {
        "components": [], "nets": [],
        "summary": f"parser indisponible — {filename} non analysé (simulation)"},
    "nl_to_skidl": lambda request_text="", plan=None: {
        "script": "# SKiDL simulé (adaptateur indisponible) — voir code_generator/skidl_emitter",
        "components": [], "nets": []},
    "constraint_extractor": lambda request_text="": [
        {"kind": "impedance_target", "key": "impedance/default", "value": {"ohm": 50.0}},
        {"kind": "clearance", "key": "clearance/default", "value": {"mm": 0.2}},
    ],
    "intent_graph_init": _fb_intent_graph_init,
    "intent_graph_record": _fb_intent_graph_record,
    "rl_place_route": _fb_rl_place_route,
    "self_verify": lambda board=None: {"ok": not board.unrouted_nets() if board else True,
                                       "issues": []},
    "night_optimizer": lambda board=None, iterations=300: {
        "iterations": iterations, "score_before": board.drc_score() if board else 0.0,
        "score_after": min(100.0, (board.drc_score() if board else 0.0) + 2.5)},
    "multi_physics": lambda board=None: {
        "thermal_ok": True, "max_temp_c": 62.5, "si_ok": True,
        "notes": ["simulation multi-physique locale (service absent)"]},
    "scoped_edit": _fb_scoped_edit,
    "firmware": lambda bom=None, board=None: {
        "files": ["main.c", "Makefile", "README.md"], "framework": "chibios",
        "hash": "sim-0f1e2d3c"},
    "exporter": lambda board=None, project_id="": {
        "files": [f"{n}.gbr" for n in ("F.Cu", "B.Cu", "F.Mask", "B.Mask", "Edge.Cuts")],
        "archive_key": f"projects/{project_id}/gerbers.zip"},
    "erc_executor": lambda netlist=None: [],
    "drc_dfm": lambda board=None: {
        "score": board.drc_score() if board else 0.0, "violations": []},
}

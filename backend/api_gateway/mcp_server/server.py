"""Serveur MCP — JSON-RPC 2.0 sur stdio, dispatch vers la couche runtime.

TRANSPORT : stdio (une requête JSON-RPC par ligne). La boucle serveur réelle
serait :
    while True:
        line = sys.stdin.readline()
        if not line: break
        sys.stdout.write(handle_line(line) + "\n")
Elle N'EST PAS démarrée ici (aucun serveur long dans ce dépôt) : `handle_line`
et `handle_request` sont appelables unitairement pour tests et intégration.

Méthodes : `initialize`, `tools/list`, `tools/call` — les 5 outils
(mcp_server/tools.py) sont dispatchés vers la couche runtime (adapters
orchestrator). Agents consommateurs visés : Claude, Cursor, Devin.

Self-test en __main__ : initialize + tools/list + tools/call get_design_state.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if not (_ROOT / "common").is_dir():
    _ROOT = _ROOT.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.log import get_logger

from ..runtime import GatewayRuntime, get_runtime
from .tools import TOOLS, get_tool

logger = get_logger("api_gateway.mcp_server")

PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    """Point d'entrée JSON-RPC 2.0 des agents externes — zéro réseau."""

    def __init__(self, runtime: GatewayRuntime | None = None) -> None:
        self.runtime = runtime or get_runtime()
        self.server_info = {"name": "pcb-ai-designer-gateway", "version": "2.0.0"}

    # ---- transport stdio (unitaire — pas de boucle infinie) ------------------------
    def handle_line(self, line: str) -> str | None:
        """Traite UNE ligne JSON-RPC → réponse JSON (None si notification)."""
        line = line.strip()
        if not line:
            return None
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            return json.dumps(self._error(None, -32700, f"JSON illisible : {exc}"),
                              ensure_ascii=False)
        return json.dumps(self.handle_request(request), ensure_ascii=False, default=str)

    # ---- JSON-RPC -----------------------------------------------------------------
    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Dispatch JSON-RPC 2.0 — id conservé, erreurs -32601/-32602."""
        request_id = request.get("id")
        method = str(request.get("method", ""))
        params = request.get("params") or {}
        try:
            result = self._dispatch(method, params)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except KeyError as exc:
            return self._error(request_id, -32602, f"argument inconnu : {exc}")
        except LookupError as exc:
            return self._error(request_id, -32602, str(exc))
        except NotImplementedError:
            return self._error(request_id, -32601, f"méthode inconnue : {method}")
        except Exception as exc:  # noqa: BLE001 — erreurs outil → is_error
            logger.exception("outil MCP en échec", extra={"method": method})
            return self._error(request_id, -32000, f"{type(exc).__name__}: {exc}")

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return {"protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": self.server_info}
        if method == "tools/list":
            return {"tools": [t.to_dict() for t in TOOLS]}
        if method == "tools/call":
            return self._call_tool(str(params.get("name", "")),
                                   dict(params.get("arguments") or {}))
        raise NotImplementedError(method)

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Exécute un outil → contenu texte (contrat MCP tools/call)."""
        try:
            tool = get_tool(name)
        except KeyError as exc:
            raise LookupError(f"outil inconnu : {name}") from exc
        handlers = {
            "create_project": self._tool_create_project,
            "run_pipeline": self._tool_run_pipeline,
            "get_design_state": self._tool_get_design_state,
            "apply_surgical_edit": self._tool_apply_surgical_edit,
            "export_gerbers": self._tool_export_gerbers,
        }
        payload = handlers[name](arguments)
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False,
                                                               default=str)}],
                "isError": False, "_credit_cost_usd": tool.credit_cost_usd}

    # ---- outils (délégation runtime) ------------------------------------------------
    def _tool_create_project(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.create_project(
            str(args["name"]), request_text=str(args.get("request_text", "")),
            tier=str(args.get("tier", "free")))

    def _tool_run_pipeline(self, args: dict[str, Any]) -> dict[str, Any]:
        run = self.runtime.run_pipeline(
            str(args["project_id"]), request_text=str(args.get("request_text", "")),
            steps=list(args.get("steps") or []) or None,
            night_mode=bool(args.get("night_mode", False)))
        return {"pipeline_id": run["pipeline_id"], "state": run["state"],
                "steps": run["statuses"], "escalations": run["escalations"]}

    def _tool_get_design_state(self, args: dict[str, Any]) -> dict[str, Any]:
        state = self.runtime.get_design_state(str(args["project_id"]))
        return {"project_id": state["project_id"], "version": state["version"],
                "pipeline_step": state["pipeline_step"],
                "metrics": self.runtime.state_manager.metrics(state["project_id"]),
                "board_summary": {
                    "components": len(state["board"]["components"]),
                    "nets": len(state["board"]["nets"]),
                    "placements": len(state["board"]["placements"])}}

    def _tool_apply_surgical_edit(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.apply_surgical_edit(
            str(args["project_id"]), [str(t) for t in args["targets"]],
            dict(args.get("transformation") or {}),
            dry_run=bool(args.get("dry_run", False)))

    def _tool_export_gerbers(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.export_gerbers(str(args["project_id"]))

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": code, "message": message}}


# ---- Self-test : initialize + tools/list + tools/call get_design_state -----------
if __name__ == "__main__":  # pragma: no cover — démonstration hors ligne
    runtime = GatewayRuntime()
    server = MCPServer(runtime)
    print("— MCP self-test (stdio unitaire, aucun réseau) —")

    init = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1,
                                          "method": "initialize", "params": {}}))
    print("initialize →", init)

    listing = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 2,
                                             "method": "tools/list"}))
    tools = json.loads(listing or "{}").get("result", {}).get("tools", [])
    print("tools/list →", [t["name"] for t in tools])

    project = json.loads(server.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "create_project",
                   "arguments": {"name": "drone-mcp", "request_text": "carte drone STM32 + LoRa"}}
    }) or "{}"))
    project_id = json.loads(project["result"]["content"][0]["text"])["id"]
    print("create_project →", project_id)

    state = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 4,
                                           "method": "tools/call",
                                           "params": {"name": "get_design_state",
                                                      "arguments": {"project_id": project_id}}}))
    print("get_design_state →", (state or "")[:180], "…")

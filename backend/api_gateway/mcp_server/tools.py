"""Catalogue des 5 outils MCP — typés, avec effets de bord et coût crédité.

Chaque outil = McpTool(name, description, input_schema (JSON Schema),
side_effects, credit_cost_usd). Les coûts réutilisent la grille
common.credits.Pricing ; les effets de bord sont documentés pour l'audit
des agents externes (Claude / Cursor / Devin).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from common.credits import Pricing


@dataclass
class McpTool:
    """Un outil MCP exposé par la gateway (spec Model Context Protocol)."""

    name: str
    description: str
    input_schema: dict[str, Any]           # JSON Schema (type: object)
    side_effects: list[str] = field(default_factory=list)
    credit_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Format tools/list du protocole MCP."""
        payload = asdict(self)
        payload["inputSchema"] = payload.pop("input_schema")
        return payload


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


TOOLS: list[McpTool] = [
    McpTool(
        name="create_project",
        description="Crée un projet PCB à partir d'un cahier des charges en "
                    "langage naturel (commande create_project du chat_interface).",
        input_schema=_schema({
            "name": {"type": "string", "description": "Nom du projet"},
            "request_text": {"type": "string", "description": "Cahier des charges NL"},
            "tier": {"type": "string", "enum": ["free", "pro"]},
        }, ["name"]),
        side_effects=["crée data/projects/{id}/journal.jsonl",
                      "émet l'événement plan_updated"],
        credit_cost_usd=0.0,
    ),
    McpTool(
        name="run_pipeline",
        description="Déclenche le workflow 8 étapes (nl_to_skidl, planning, RL "
                    "place&route, optimisation nocturne, multi-physique, edits, "
                    "firmware, export) et retourne les StepStatus.",
        input_schema=_schema({
            "project_id": {"type": "string"},
            "request_text": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "integer",
                                                 "minimum": 1, "maximum": 8}},
            "night_mode": {"type": "boolean"},
        }, ["project_id"]),
        side_effects=["écrit le journal d'audit (JSONL)",
                      "commits versionnés du design",
                      "débite des crédits (routing_pass, nuit d'optimisation, export)"],
        credit_cost_usd=float(Pricing.ROUTING_PASS.value),
    ),
    McpTool(
        name="get_design_state",
        description="Retourne l'état complet du design : version, board sérialisé "
                    "(composants, placements, nets, zones) et métriques.",
        input_schema=_schema({
            "project_id": {"type": "string"},
        }, ["project_id"]),
        side_effects=["aucun (lecture seule)"],
        credit_cost_usd=0.0,
    ),
    McpTool(
        name="apply_surgical_edit",
        description="Applique une modification chirurgicale bornée (move/rotate) "
                    "aux cibles : bounding box d'impact calculée, verrou humain, "
                    "re-route local, revue accept/reject.",
        input_schema=_schema({
            "project_id": {"type": "string"},
            "targets": {"type": "array", "items": {"type": "string"},
                        "description": "refs ciblées (ex. [\"U1\"])"},
            "transformation": {"type": "object",
                               "description": '{"move": {"dx_mm", "dy_mm"}, "rotate_deg"}'},
            "dry_run": {"type": "boolean"},
        }, ["project_id", "targets"]),
        side_effects=["verrou de zone humain (prioritaire sur rl_agent)",
                      "commit + delta WebSocket si dry_run=false"],
        credit_cost_usd=0.0,
    ),
    McpTool(
        name="export_gerbers",
        description="Exporte les Gerbers du projet (via l'exporter) et retourne "
                    "les fichiers + la clé d'archive (URL signée simulée).",
        input_schema=_schema({
            "project_id": {"type": "string"},
        }, ["project_id"]),
        side_effects=["émet l'événement export_ready",
                      "débite GERBER_EXPORT"],
        credit_cost_usd=float(Pricing.GERBER_EXPORT.value),
    ),
]

_TOOLS_BY_NAME: dict[str, McpTool] = {t.name: t for t in TOOLS}


def get_tool(name: str) -> McpTool:
    """Lève KeyError si l'outil n'existe pas — traduit en -32602 côté serveur."""
    return _TOOLS_BY_NAME[name]

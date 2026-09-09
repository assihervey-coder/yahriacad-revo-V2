"""Agent planner — décomposition du cahier des charges en sous-objectifs [Fuse].

Lit la requête NL (ou la netlist importée), identifie les blocs fonctionnels,
alloue les zones de placement, fixe le budget de couches et extrait les
contraintes thermiques/SI via le constraint_extractor (adaptateur). Chaque
sous-objectif est VÉRIFIABLE : le validator_agent le rejouera en fin de run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..base_agent import AgentContext, AgentResult, BaseAgent
from ...adapters import call_adapter

# Mots-clés → bloc fonctionnel (heuristique locale, le LLM reste l'avenant)
_BLOCK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "mcu": ("stm32", "mcu", "microcontrôleur", "microcontroller", "esp32", "cpu"),
    "rf": ("lora", "rf", "antenne", "antenna", "wifi", "ble", "radio", "gnss", "gps"),
    "power": ("buck", "ldo", "batterie", "battery", "alim", "power", "régulateur"),
    "sensor": ("imu", "capteur", "sensor", "baro", "altimètre", "magnétomètre"),
    "actuator": ("esc", "moteur", "motor", "servo", "driver"),
}


@dataclass
class PlanDocument:
    """Document de planification — consommé par researcher/selector/rl_placer."""

    project_id: str
    functional_blocks: list[dict[str, Any]] = field(default_factory=list)
    placement_zones: list[dict[str, Any]] = field(default_factory=list)
    layer_budget: int = 4
    constraints: list[dict[str, Any]] = field(default_factory=list)
    sub_objectives: list[dict[str, Any]] = field(default_factory=list)
    version: int = 1

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _detect_blocks(request_text: str) -> list[str]:
    """Détecte les blocs fonctionnels par mots-clés (mcu toujours présent)."""
    text = (request_text or "").lower()
    detected = [block for block, keywords in _BLOCK_KEYWORDS.items()
                if any(k in text for k in keywords)]
    return detected or ["mcu"]


def _layer_budget(blocks: list[str]) -> int:
    """RF + power + mcu ⇒ 4 couches minimum (empilement signal/GND/PWR)."""
    if {"rf", "actuator"} & set(blocks):
        return 4
    return 2 if len(blocks) <= 2 else 4


class PlannerAgent(BaseAgent):
    """Décompose la requête en sous-objectifs vérifiables (PlanDocument)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="planner", grpc_service="ai_engine", **kwargs)

    def execute(self, context: AgentContext) -> AgentResult:
        blocks = _detect_blocks(context.request_text)
        layer_budget = _layer_budget(blocks)
        # Contraintes via adaptateur (kind/key/value — cf. common.bus)
        extracted = call_adapter("constraint_extractor", context.request_text)
        constraints = list(extracted.value or [])
        justifications = [
            f"blocs détectés : {', '.join(blocks)} (mots-clés de la requête)",
            f"budget de couches = {layer_budget} "
            f"({'RF/actionneurs ⇒ empilement 4 couches' if layer_budget >= 4 else 'design simple'})",
        ]
        if extracted.source == "fallback":
            justifications.append("contraintes par défaut (constraint_extractor indisponible)")

        plan = PlanDocument(
            project_id=context.project_id,
            functional_blocks=[{"name": b, "refs": [], "priority": i}
                               for i, b in enumerate(blocks)],
            placement_zones=self._zones(blocks),
            layer_budget=layer_budget,
            constraints=constraints,
            sub_objectives=([{"objective": f"placer le bloc {name}", "block": name,
                              "verify": "zones sans chevauchement (validator)"}
                             for name in blocks] + [
                {"objective": "router 100 % des nets",
                 "verify": "board.unrouted_nets() vide"},
                {"objective": "respecter le budget de couches",
                 "verify": f"layers <= {layer_budget}"},
            ]),
        )
        context.plan_document = plan
        self.log.info("plan produit", extra={"blocks": blocks, "layers": layer_budget})
        return AgentResult(agent=self.name, status="done",
                           outputs={"plan": plan.to_json()},
                           justifications=justifications)

    def _zones(self, blocks: list[str]) -> list[dict[str, Any]]:
        """Zones de placement — mcu au centre, RF en bord (antenne), power au bord."""
        zones = []
        for i, block in enumerate(blocks):
            y = 8.0 + i * 20.0
            zones.append({
                "block": block,
                "bbox": [8.0, y, 60.0, y + 18.0],
                "note": "RF en bord de carte (antenne dégagée)" if block == "rf" else "",
            })
        return zones

"""Agent code_generator — génère le script SKiDL depuis BOM + plan [Circuitron].

Chemin principal : adaptateur `nl_to_skidl` (service ai_engine écrit en
parallèle). Sinon, génération template MAISON via `skidl_emitter.emit_skidl`
(BOM du selector si disponible, sinon seed du catalogue researcher).
Émet l'événement SKIDL_GENERATED — la netlist est exécutable et versionnable.
"""

from __future__ import annotations

from typing import Any

from common.events import EventType, make_event

from ..base_agent import AgentContext, AgentResult, BaseAgent
from ...adapters import call_adapter
from ..researcher_agent.providers import load_catalogue
from .skidl_emitter import emit_skidl

# Seed minimal (drone STM32 + LoRa) si ni BOM ni nl_to_skidl disponibles
_SEED_BOM: list[dict[str, Any]] = [
    {"ref": "U1", "mpn": "STM32F405RGT6", "value": "STM32F405",
     "footprint": "LQFP-64_10x10mm", "pins": 64},
    {"ref": "U2", "mpn": "RFM95W-868S2", "value": "LoRa module",
     "footprint": "SMD-16_SMA", "pins": 16},
    {"ref": "U3", "mpn": "MPU-6050", "value": "IMU 6 axes",
     "footprint": "QFN-24_4x4mm", "pins": 24},
    {"ref": "C1", "mpn": "GRM188R71H104KA93D", "value": "100nF",
     "footprint": "C_0603_1608Metric", "pins": 2},
]
_SEED_NETS: list[dict[str, Any]] = [
    {"name": "3V3", "connections": [["U1", "1"], ["U2", "1"], ["U3", "1"], ["C1", "1"]]},
    {"name": "GND", "connections": [["U1", "12"], ["U2", "2"], ["U3", "2"], ["C1", "2"]]},
    {"name": "I2C_SDA", "connections": [["U1", "58"], ["U3", "24"]]},
    {"name": "I2C_SCL", "connections": [["U1", "59"], ["U3", "23"]]},
    {"name": "LORA_NSS", "connections": [["U1", "20"], ["U2", "5"]]},
]


class CodeGeneratorAgent(BaseAgent):
    """Produit la netlist SKiDL de l'étape 1 (nl_to_skidl)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="code_generator", grpc_service="ai_engine", **kwargs)

    def execute(self, context: AgentContext) -> AgentResult:
        # 1) voie principale : service nl_to_skidl (écrit en parallèle)
        adapter = call_adapter("nl_to_skidl", context.request_text,
                               context.plan_document.to_json()
                               if context.plan_document is not None else None)
        components = list(adapter.value.get("components") or [])
        nets = list(adapter.value.get("nets") or [])
        script = str(adapter.value.get("script") or "")
        justifications: list[str] = []
        source = adapter.source

        if not components or not nets:
            # 2) voie de repli : template maison depuis BOM ou seed
            bom_dicts = [c.__dict__ for c in context.bom] if context.bom else []
            if bom_dicts:
                components = bom_dicts
                justifications.append("SKiDL généré localement depuis le BOM (selector)")
            else:
                components = list(_SEED_BOM)
                justifications.append("SKiDL généré localement depuis le seed STM32+LoRa")
            nets = nets or [dict(n) for n in _SEED_NETS]
            script = emit_skidl(components, nets, title=context.project_id)
            source = "local_emitter"

        context.skidl_script = script
        context.extra["skidl_components"] = components
        context.extra["skidl_nets"] = nets
        justifications.append(
            "netlist exécutable, lisible en revue, versionnable (skidl_emitter déterministe)")

        if self.state_manager is not None:
            self.state_manager.publish_event(make_event(
                EventType.SKIDL_GENERATED, context.project_id, self.name,
                source=source, lines=script.count("\n") + 1,
                components=len(components), nets=len(nets)))
        return AgentResult(agent=self.name, status="done",
                           outputs={"skidl_script": script, "source": source,
                                    "components": components, "nets": nets},
                           justifications=justifications)

    def fallback(self, context: AgentContext) -> Any:
        script = emit_skidl(_SEED_BOM, _SEED_NETS, title=context.project_id)
        return {"skidl_script": script, "components": _SEED_BOM, "nets": _SEED_NETS}

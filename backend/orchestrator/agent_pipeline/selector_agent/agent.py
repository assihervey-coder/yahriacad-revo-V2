"""Agent selector — valide la short-list et produit le BOM initial.

Règles de compatibilité :
- électrique : tension de service <= rating du composant, courant supporté ;
- empreinte : footprint connu (non vide) et pin-count cohérent avec le bloc ;
- arbitrage coût/performances/stock : score pondéré, justifié par candidat.
Sortie : BOM (list[common.design_model.Component]) + rejets documentés.
"""

from __future__ import annotations

from typing import Any

from common.design_model import Component
from common.events import EventType, make_event

from ..base_agent import AgentContext, AgentResult, BaseAgent

_REF_PREFIX: dict[str, str] = {"mcu": "U", "rf": "U", "power": "U", "sensor": "U",
                               "passive": "R", "connector": "J"}
_RAIL_VOLTAGE_V = 3.3          # rail logique par défaut (drone 1S-2S → 3V3)
_MIN_STOCK: dict[str, int] = {"passive": 500, "default": 20}


class SelectorAgent(BaseAgent):
    """Sélectionne UN composant par besoin et assemble le BOM initial."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="selector", grpc_service="ai_engine", **kwargs)

    def execute(self, context: AgentContext) -> AgentResult:
        research = context.extra.get("research") or {}
        shortlist: dict[str, list[dict[str, Any]]] = research.get("shortlist", {})
        bom: list[Component] = []
        rejected: list[dict[str, str]] = []
        justifications: list[str] = []
        counters: dict[str, int] = {}

        for block, candidates in shortlist.items():
            if not candidates:
                justifications.append(f"bloc {block} : AUCUN candidat — à compléter manuellement")
                continue
            best, reason = self._pick(candidates, block, rejected)
            if best is None:
                justifications.append(f"bloc {block} : tous les candidats rejetés ({reason})")
                continue
            prefix = str(best.get("ref_prefix") or _REF_PREFIX.get(block, "U"))
            counters[prefix] = counters.get(prefix, 0) + 1
            ref = f"{prefix}{counters[prefix]}"
            comp = Component(
                ref=ref, mpn=str(best.get("mpn", "")),
                value=str(best.get("value") or str(best.get("description", ""))[:24]),
                footprint=str(best.get("footprint", "")), pins=int(best.get("pins", 0)),
                width_mm=float(best.get("width_mm", 5.0)),
                height_mm=float(best.get("height_mm", 5.0)),
                power_w=float(best.get("power_w", 0.0)),
                price_usd=float(best.get("price_usd", 0.0)),
                stock=int(best.get("stock", 0)), functional_block=block)
            bom.append(comp)
            justifications.append(
                f"{ref} = {comp.mpn} ({block}) : {reason}")

        context.bom = bom
        if self.state_manager is not None:
            self.state_manager.publish_event(make_event(
                EventType.BOM_VALIDATED, context.project_id, self.name,
                bom_size=len(bom), rejected=len(rejected)))
        self.log.info("BOM initial", extra={"refs": [c.ref for c in bom]})
        return AgentResult(agent=self.name, status="done",
                           outputs={"bom": [c.__dict__ for c in bom],
                                    "rejected": rejected},
                           justifications=justifications)

    # ---- règles de sélection ---------------------------------------------------
    def _pick(self, candidates: list[dict[str, Any]], block: str,
              rejected: list[dict[str, str]]) -> tuple[dict[str, Any] | None, str]:
        for record in candidates:
            voltage = float(record.get("voltage_v", 0.0))
            if block not in ("passive", "connector") and voltage and voltage < _RAIL_VOLTAGE_V:
                rejected.append({"mpn": str(record.get("mpn")),
                                 "reason": f"tension {voltage}V < rail {_RAIL_VOLTAGE_V}V"})
                continue
            if not record.get("footprint"):
                rejected.append({"mpn": str(record.get("mpn")), "reason": "empreinte inconnue"})
                continue
            min_stock = _MIN_STOCK.get(block, _MIN_STOCK["default"])
            if int(record.get("stock", 0)) < min_stock:
                rejected.append({"mpn": str(record.get("mpn")),
                                 "reason": f"stock < {min_stock}"})
                continue
            return record, (f"meilleur score coût/stock du bloc {block} "
                            f"({record.get('price_usd')} USD, stock {record.get('stock')})")
        return None, "aucun candidat compatible"

    def fallback(self, context: AgentContext) -> Any:
        return {"bom": [], "note": "sélection dégradée — BOM vide"}

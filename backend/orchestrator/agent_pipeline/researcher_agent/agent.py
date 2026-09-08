"""Agent researcher — recherche de composants candidats [Circuitron].

Interroge d'abord data/component_library (seed_components.json s'il existe,
sinon mini-catalogue embarqué ~20 composants via providers.py), puis les API
DigiKey/Mouser EN ADAPTATEUR optionnel (aucun appel réel — TODO REST). Sortie :
short-list par bloc fonctionnel + datasheets indexées RAG (simulation locale).
"""

from __future__ import annotations

from typing import Any

from ..base_agent import AgentContext, AgentResult, BaseAgent
from .providers import DigiKeyClient, MouserClient, SupplierClient

_DATASHEET_ROOT = "data/component_library/datasheets"   # index RAG simulé


class ResearchAgent(BaseAgent):
    """Constitue la short-list de candidats par bloc fonctionnel."""

    def __init__(self, providers: list[SupplierClient] | None = None, **kwargs: Any) -> None:
        super().__init__(name="researcher", grpc_service="ai_engine", **kwargs)
        self.providers = providers or [DigiKeyClient(), MouserClient()]

    def execute(self, context: AgentContext) -> AgentResult:
        plan = context.plan_document
        blocks = [b["name"] for b in getattr(plan, "functional_blocks", [])] or ["mcu"]
        shortlist: dict[str, list[dict[str, Any]]] = {}
        datasheet_index: dict[str, str] = {}
        justifications: list[str] = []

        for block in blocks:
            candidates: list[dict[str, Any]] = []
            for provider in self.providers:
                found = provider.search(query=block, category=block)
                if found:
                    candidates.extend(found)
                    justifications.append(
                        f"{len(found)} candidat(s) « {block} » via {provider.name}")
                    break  # premier fournisseur qui répond — les suivants en secours
            # short-list : top 3 par stock/price (proxy de disponibilité)
            ranked = sorted(candidates, key=lambda c: (-c.get("stock", 0),
                                                       c.get("price_usd", 9e9)))[:3]
            shortlist[block] = ranked
            for record in ranked:
                datasheet_index[record["mpn"]] = f"{_DATASHEET_ROOT}/{record['mpn']}.pdf"

        self.log.info("short-list constituée",
                      extra={"blocks": blocks,
                             "candidates": sum(len(v) for v in shortlist.values())})
        return AgentResult(
            agent=self.name, status="done",
            outputs={"shortlist": shortlist, "datasheet_index": datasheet_index,
                     "rag_indexed": True},
            justifications=justifications or ["catalogue local — aucune API distante appelée"])

    def fallback(self, context: AgentContext) -> Any:
        return {"shortlist": {"mcu": []}, "note": "recherche dégradée — catalogue vide"}

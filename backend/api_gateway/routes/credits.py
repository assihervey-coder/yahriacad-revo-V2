"""Router /credits — solde, historique, estimation pré-action, topup.

GET  /credits                 → solde + historique (credit_dashboard).
GET  /credits/estimate?item=  → estimation pré-action du dashboard (ledger).
POST /credits/topup           → rechargement (transaction tracée + persistée).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from common.credits import CreditTransaction, Pricing

from ..compat import APIRouter, BaseModel
from ..runtime import get_runtime
from . import raise_http_for

router = APIRouter(prefix="/credits", tags=["credits"])

# item (query) → entrée de la grille tarifaire common.credits.Pricing
_ITEM_ALIASES: dict[str, Pricing] = {
    "routing_pass": Pricing.ROUTING_PASS,
    "night_optimization": Pricing.NIGHT_OPTIMIZATION,
    "fast_eval_iteration": Pricing.FAST_EVAL_ITERATION,
    "simulation_multi_physics": Pricing.SIMULATION_MULTI_PHYSICS,
    "gerber_export": Pricing.GERBER_EXPORT,
    "skidl_generation": Pricing.SKIDL_GENERATION,
}


class TopupRequest(BaseModel):
    """Corps de POST /credits/topup — montant en USD."""

    amount_usd: float
    project_id: str = "topup"


@router.get("")
def get_credits() -> dict[str, Any]:
    """Solde courant + historique des transactions (grand livre JSON)."""
    ledger = get_runtime().ledger
    return {
        "balance_usd": ledger.balance_usd,
        "transactions": [t.__dict__ for t in ledger.history()],
        "pricing": [{"item": p.value, "label": p.label} for p in Pricing],
    }


@router.get("/estimate")
def estimate(item: str = "routing_pass", quantity: int = 1) -> dict[str, Any]:
    """Estimation pré-action — bloque côté dashboard si non abordable."""
    pricing = _ITEM_ALIASES.get(item)
    if pricing is None:
        raise_http_for(ValueError(
            f"item inconnu : {item} (attendus : {', '.join(sorted(_ITEM_ALIASES))})"))
    return dict(get_runtime().ledger.estimate(pricing, quantity=quantity))


@router.post("/topup")
def topup(body: TopupRequest) -> dict[str, Any]:
    """Recharge le solde — transaction appendée puis persistée (best-effort)."""
    if body.amount_usd <= 0:
        raise_http_for(ValueError("amount_usd doit être positif"))
    ledger = get_runtime().ledger
    ledger.balance_usd = round(ledger.balance_usd + body.amount_usd, 4)
    tx = CreditTransaction(
        tx_id=uuid.uuid4().hex[:12], project_id=body.project_id, item=0.0,
        amount_usd=body.amount_usd, balance_after_usd=ledger.balance_usd,
        ts=time.time(), user="topup")
    ledger.transactions.append(tx)
    ledger._persist()   # noqa: SLF001 — persistance best-effort du grand livre
    return {"tx_id": tx.tx_id, "amount_usd": tx.amount_usd,
            "balance_usd": ledger.balance_usd}

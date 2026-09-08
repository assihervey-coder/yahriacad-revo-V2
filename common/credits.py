"""Comptabilité de crédits « pay-as-you-go » — brique DeepPCB.

Alimente le credit_dashboard (endpoint /credits) et l'événement credit_debit.
Grille tarifaire calibrée sur le coût d'API réel : une session d'optimisation
nocturne de 300 itérations correspond à environ 1,20 USD.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List

from common.events import Event, EventType


class Pricing(float, Enum):
    """Unités en USD — coûts unitaires d'API (spécification section 03)."""

    ROUTING_PASS = 0.40          # passe de routage complète
    NIGHT_OPTIMIZATION = 1.20    # ~300 itérations du autonomous_optimizer
    FAST_EVAL_ITERATION = 0.004  # 1,20 USD / 300 itérations
    SIMULATION_MULTI_PHYSICS = 0.15
    GERBER_EXPORT = 0.05
    SKIDL_GENERATION = 0.10

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    Pricing.ROUTING_PASS: "Passe de routage",
    Pricing.NIGHT_OPTIMIZATION: "Nuit d'optimisation (300 itérations)",
    Pricing.FAST_EVAL_ITERATION: "Itération d'évaluation rapide",
    Pricing.SIMULATION_MULTI_PHYSICS: "Simulation multiphysique",
    Pricing.GERBER_EXPORT: "Export Gerber",
    Pricing.SKIDL_GENERATION: "Génération SKiDL",
}


def estimate_night_cost(iterations: int = 300) -> float:
    """Coût estimé d'une nuit d'optimisation — ~1,20 USD pour 300 itérations."""
    return round(iterations * Pricing.FAST_EVAL_ITERATION.value, 2)


@dataclass
class CreditTransaction:
    tx_id: str
    project_id: str
    item: float                 # clé de Pricing (valeur USD)
    amount_usd: float
    balance_after_usd: float
    ts: float
    user: str = "system"


@dataclass
class CreditLedger:
    """Grand livre de crédits — persisté en JSON, thread-safe.

    En production, ce registre est adossé à PostgreSQL (data/projects) ; la
    sérialisation JSON reste la référence de secours pour la reprise de session
    (session_restorer) et les déploiements mono-nœud.
    """

    balance_usd: float = 50.0
    ledger_path: Path = Path("data/projects/credits.json")
    transactions: List[CreditTransaction] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def debit(self, project_id: str, item: Pricing, user: str = "system",
              quantity: int = 1) -> CreditTransaction:
        cost = round(item.value * quantity, 4)
        with self._lock:
            if cost > self.balance_usd:
                raise InsufficientCredits(
                    f"solde insuffisant : {self.balance_usd:.2f} USD requis {cost:.2f} USD ({item.label})"
                )
            self.balance_usd = round(self.balance_usd - cost, 4)
            tx = CreditTransaction(
                tx_id=uuid.uuid4().hex[:12],
                project_id=project_id,
                item=item.value,
                amount_usd=cost,
                balance_after_usd=self.balance_usd,
                ts=time.time(),
                user=user,
            )
            self.transactions.append(tx)
            self._persist()
            return tx

    def to_event(self, tx: CreditTransaction, design_version: int = 1) -> Event:
        """Convertit une transaction en événement credit_debit pour le frontend."""
        return Event(
            type=EventType.CREDIT_DEBIT,
            project_id=tx.project_id,
            design_version=design_version,
            payload={
                "tx_id": tx.tx_id,
                "item": tx.item,
                "amount_usd": tx.amount_usd,
                "balance_usd": tx.balance_after_usd,
                "label": _LABELS.get(Pricing(tx.item), str(tx.item)),
            },
        )

    def estimate(self, item: Pricing, quantity: int = 1) -> Dict[str, float]:
        """Estimation affichée par le credit_dashboard avant action coûteuse."""
        cost = round(item.value * quantity, 4)
        return {
            "item": item.value,
            "label": item.label,
            "quantity": quantity,
            "estimated_usd": cost,
            "affordable": cost <= self.balance_usd,
            "balance_usd": self.balance_usd,
        }

    def history(self, project_id: str | None = None) -> List[CreditTransaction]:
        if project_id is None:
            return list(self.transactions)
        return [t for t in self.transactions if t.project_id == project_id]

    def _persist(self) -> None:
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "balance_usd": self.balance_usd,
                "transactions": [t.__dict__ for t in self.transactions[-500:]],
            }
            self.ledger_path.write_text(json.dumps(payload, indent=2))
        except OSError:
            pass  # persistance best-effort — le grand livre en mémoire reste la source


class InsufficientCredits(RuntimeError):
    """Levée quand une action coûteuse dépasse le solde — le dashboard bloque proprement."""

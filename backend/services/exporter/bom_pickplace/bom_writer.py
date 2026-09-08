"""BOM CSV — aligné sur le BOM validé par le selector_agent (section 6.6)."""

from __future__ import annotations

import csv
import io
from typing import Dict, List, Tuple

from common.design_model import Board

_HEADER = ["ref", "mpn", "value", "footprint", "quantity", "unit_price_usd",
           "subtotal_usd", "functional_block"]


def export_bom(board: Board) -> Tuple[str, Dict[str, float]]:
    """Génère le BOM CSV groupé par (mpn, value) + sous-totaux par bloc.

    Retourne (csv_text, {functional_block: subtotal_usd}) — les sous-totaux par
    bloc fonctionnel alimentent le credit_dashboard et les revues de coût.
    """
    groups: Dict[Tuple[str, str], List] = {}
    for comp in board.components.values():
        groups.setdefault((comp.mpn, comp.value), []).append(comp)

    block_subtotals: Dict[str, float] = {}
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_HEADER)
    for (mpn, value), comps in sorted(groups.items()):
        first = comps[0]
        qty = len(comps)
        subtotal = round(qty * first.price_usd, 4)
        block = first.functional_block or "uncategorized"
        block_subtotals[block] = round(block_subtotals.get(block, 0.0) + subtotal, 4)
        writer.writerow([", ".join(c.ref for c in comps), mpn, value, first.footprint,
                         qty, first.price_usd, subtotal, block])
    return buffer.getvalue(), block_subtotals

"""Chargement du corpus de designs de référence (JSON versionnés).

Chaque design du corpus est un point de repère de la revue d'architecture :
même netliste rejouée sur les deux moteurs à chaque release — toute dérive de
métrique doit être explicite (baseline.json + gate CI).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ReferenceDesign:
    """Design de référence du corpus — structure contractuelle du JSON.

    - components / nets : netliste normalisée (MPN, dimensions, connexions) ;
    - constraints : carte, keepouts, cibles d'impédance, zones, budget
      thermique — consommés par le moteur interne et le stub Quilter ;
    - expected_metrics : métriques « cible de revue » (documentaires) — le
      gate officiel compare au baseline.json, pas à ce champ.
    """

    id: str
    name: str
    description: str
    components: list[dict[str, Any]] = field(default_factory=list)
    nets: list[dict[str, Any]] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    expected_metrics: dict[str, float] = field(default_factory=dict)

    @property
    def board_config(self) -> dict[str, Any]:
        """Config carte (width_mm, height_mm, layers) issue des contraintes."""
        return self.constraints.get("board", {"width_mm": 100.0, "height_mm": 80.0, "layers": 4})


def load_design(path: Path) -> ReferenceDesign:
    """Charge un design JSON du corpus et valide les champs obligatoires."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in ("id", "name", "description", "components", "nets"):
        if key not in raw:
            raise ValueError(f"{path}: champ obligatoire manquant '{key}'")
    if not raw["components"] or not raw["nets"]:
        raise ValueError(f"{path}: un design de référence doit avoir composants ET nets")
    return ReferenceDesign(
        id=str(raw["id"]),
        name=str(raw["name"]),
        description=str(raw["description"]),
        components=list(raw["components"]),
        nets=list(raw["nets"]),
        constraints=dict(raw.get("constraints", {})),
        expected_metrics=dict(raw.get("expected_metrics", {})),
    )


def load_corpus(corpus_dir: Path) -> list[ReferenceDesign]:
    """Charge tous les designs du corpus (tri par id — ordre déterministe)."""
    if not corpus_dir.is_dir():
        raise FileNotFoundError(f"corpus introuvable : {corpus_dir}")
    designs = [load_design(p) for p in sorted(corpus_dir.glob("*.json"))]
    if not designs:
        raise ValueError(f"corpus vide : {corpus_dir}")
    ids = [d.id for d in designs]
    if len(set(ids)) != len(ids):
        raise ValueError(f"ids dupliqués dans le corpus : {ids}")
    return designs

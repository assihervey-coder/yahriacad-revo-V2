"""Métriques comparées du benchmark vs Quilter + logique de gain.

Six métriques par design et par moteur :
  drc_score        (0-100, supérieur = mieux) — complétude + pénalités
  via_count        (entier, inférieur = mieux) — coût usine et bruit SI
  routed_length_mm (mm, inférieur = mieux) — longueur totale routée
  si_compliance    (0-100, supérieur = mieux) — respect impédance / length match
  convergence_s    (s, inférieur = mieux) — temps jusqu'au résultat routé
  cost_usd         (USD, inférieur = mieux) — crédits (nous) / abonnement amorti (Quilter)

compare(ours, quilter) renvoie un tableau de lignes + le gain moyen en % :
positif = notre moteur gagne sur la métrique.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Métriques dont la plus petite valeur est la meilleure.
LOWER_IS_BETTER = frozenset({"via_count", "routed_length_mm", "convergence_s", "cost_usd"})

# Ordre d'affichage contractuel des lignes de comparaison.
METRIC_KEYS = ("drc_score", "via_count", "routed_length_mm", "si_compliance",
               "convergence_s", "cost_usd")

METRIC_LABELS_FR = {
    "drc_score": "Score DRC (0-100)",
    "via_count": "Vias",
    "routed_length_mm": "Longueur routée (mm)",
    "si_compliance": "Conformité SI (0-100)",
    "convergence_s": "Convergence (s)",
    "cost_usd": "Coût (USD)",
}


@dataclass
class BenchmarkMetrics:
    """Métriques d'un moteur sur un design — l'unité d'échange du harnais."""

    drc_score: float
    via_count: int
    routed_length_mm: float
    si_compliance: float
    convergence_s: float
    cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "BenchmarkMetrics":
        missing = [k for k in METRIC_KEYS if k not in raw]
        if missing:
            raise ValueError(f"métriques manquantes : {missing}")
        return cls(
            drc_score=float(raw["drc_score"]),
            via_count=int(raw["via_count"]),
            routed_length_mm=float(raw["routed_length_mm"]),
            si_compliance=float(raw["si_compliance"]),
            convergence_s=float(raw["convergence_s"]),
            cost_usd=float(raw["cost_usd"]),
        )


@dataclass
class MetricRow:
    """Une ligne du tableau comparatif pour une métrique."""

    metric: str
    label: str
    ours: float
    quilter: float
    gain_pct: float          # > 0 : notre moteur gagne (dans l'unité de la métrique)
    better: str              # "ours" | "quilter" | "tie"


@dataclass
class ComparisonResult:
    """Tableau de comparaison pour un design + gain moyen."""

    design_id: str
    rows: list[MetricRow] = field(default_factory=list)
    mean_gain_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "design_id": self.design_id,
            "rows": [asdict(r) for r in self.rows],
            "mean_gain_pct": self.mean_gain_pct,
        }

    def gain_for(self, metric: str) -> MetricRow | None:
        return next((r for r in self.rows if r.metric == metric), None)


def _gain_pct(ours: float, quilter: float, metric: str) -> tuple[float, str]:
    """Gain en % (base : valeur Quilter) + désignation du meilleur.

    Convention : pour une métrique « plus petit = mieux », gain =
    (quilter - ours) / quilter x 100 — positif si nous consommons moins.
    Pour « plus grand = mieux », gain = (ours - quilter) / quilter x 100.
    """
    base = abs(quilter) or 1.0
    if metric in LOWER_IS_BETTER:
        gain = (quilter - ours) / base * 100.0
    else:
        gain = (ours - quilter) / base * 100.0
    if abs(gain) < 0.05:  # sous 0,05 % : ex æquo
        return round(gain, 2), "tie"
    return round(gain, 2), ("ours" if gain > 0 else "quilter")


def compare(ours: BenchmarkMetrics, quilter: BenchmarkMetrics,
            design_id: str = "") -> ComparisonResult:
    """Compare les deux moteurs métrique par métrique -> tableau + gain moyen."""
    rows: list[MetricRow] = []
    ours_d, quilter_d = ours.to_dict(), quilter.to_dict()
    for key in METRIC_KEYS:
        gain, better = _gain_pct(float(ours_d[key]), float(quilter_d[key]), key)
        rows.append(MetricRow(
            metric=key,
            label=METRIC_LABELS_FR.get(key, key),
            ours=float(ours_d[key]),
            quilter=float(quilter_d[key]),
            gain_pct=gain,
            better=better,
        ))
    mean_gain = round(sum(r.gain_pct for r in rows) / len(rows), 2)
    return ComparisonResult(design_id=design_id, rows=rows, mean_gain_pct=mean_gain)

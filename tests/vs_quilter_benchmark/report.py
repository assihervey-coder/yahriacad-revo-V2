"""Rendu du rapport markdown du benchmark — prêt pour la revue d'architecture.

Un tableau comparatif par design (métrique / notre moteur / Quilter / gain),
puis la synthèse globale (gain moyen, réduction de vias, verdict de la gate).
"""

from __future__ import annotations

from typing import Any

from tests.vs_quilter_benchmark.metrics import ComparisonResult
from tests.vs_quilter_benchmark.run_benchmark import DesignRun  # noqa: F401 — contrat documentaire


def _fmt(value: float) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value)}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _fmt_gain(gain_pct: float, better: str) -> str:
    """Gain lisible : +X % (nous) / -X % (Quilter) / =."""
    if better == "tie":
        return "="
    sign = "+" if gain_pct > 0 else ""
    arrow = "notre moteur" if better == "ours" else "Quilter"
    return f"{sign}{gain_pct:.2f} % ({arrow})"


def render_design_table(run: DesignRun) -> str:
    """Tableau comparatif d'un design — section du rapport."""
    comp: ComparisonResult = run.comparison
    lines = [
        f"### {run.design.id} — {run.design.name}",
        "",
        run.design.description,
        "",
        f"Replays déterministes : {'oui' if run.replays_identical else 'NON (échec Circuitron)'}"
        f" — vias flashés à l'export : {run.gerber_summary.get('vias_flashed', '?')}",
        "",
        "| Métrique | Notre moteur | Quilter (stub) | Gain |",
        "|---|---|---|---|",
    ]
    for row in comp.rows:
        lines.append(f"| {row.label} | {_fmt(row.ours)} | {_fmt(row.quilter)} "
                     f"| {_fmt_gain(row.gain_pct, row.better)} |")
    lines += [
        f"| **Gain moyen** | | | **{comp.mean_gain_pct:+.2f} %** |",
        "",
    ]
    if run.gate_violations:
        lines.append(f"**Gate : ÉCHEC** — régression vs baseline :")
        for v in run.gate_violations:
            lines.append(f"- `{v.metric}` : {v.detail}")
    else:
        lines.append("**Gate : OK** — métriques conformes à la baseline certifiée.")
    lines.append("")
    # Métriques « cible de revue » du corpus (documentaires).
    if run.design.expected_metrics:
        lines.append("Cibles de revue du corpus : "
                     + ", ".join(f"{k}={v}" for k, v in sorted(run.design.expected_metrics.items()))
                     + ".")
        lines.append("")
    return "\n".join(lines)


def render_report(runs: list[DesignRun], summary: dict[str, Any],
                  gate_violations: list[str]) -> str:
    """Rapport complet : en-tête, un tableau par design, synthèse + verdict."""
    lines = [
        "# Benchmark vs Quilter — rapport de release",
        "",
        f"> Généré par le harnais `tests/vs_quilter_benchmark` — moteur interne "
        f"(chaîne déterministe hors ligne) vs `QuilterClient` (stub déterministe, "
        f"endpoint réel à brancher en production).",
        "",
        "| Indicateur | Valeur |",
        "|---|---|",
        f"| Designs du corpus | {summary.get('designs_total', 0)} |",
        f"| Designs conformes à la baseline | {summary.get('designs_gate_ok', 0)} |",
        f"| Gain moyen toutes métriques | {summary.get('mean_gain_pct', 0):+.2f} % |",
        f"| Réduction moyenne de vias vs Quilter | {summary.get('via_reduction_mean_pct', 0):+.2f} % |",
        "",
        "---",
        "",
    ]
    for run in runs:
        lines.append(render_design_table(run))
        lines.append("---")
        lines.append("")
    lines += ["## Synthèse", ""]
    if gate_violations:
        lines += [
            f"**VERDICT : RELEASE BLOQUÉE** — {len(gate_violations)} violation(s) de baseline :",
            "",
        ]
        lines += [f"- `{v}`" for v in gate_violations]
        lines += ["",
                  "Action : investiguer la couche responsable (modèle RL, routage, "
                  "règles DRC), corriger, puis re-certifier la baseline par revue "
                  "d'architecture — jamais de baseline réécrite pour « verdir » la CI."]
    else:
        lines += [
            "**VERDICT : GATE OK** — aucune régression vs baseline certifiée.",
            "",
            "La promesse portée par les métriques (moins de vias, coût crédit "
            "maîtrisé, convergence rapide) reste tenue sur le corpus de référence. "
            "Ce rapport peut être joint à la release.",
        ]
    lines.append("")
    return "\n".join(lines)

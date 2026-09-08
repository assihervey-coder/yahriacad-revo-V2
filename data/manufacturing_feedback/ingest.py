#!/usr/bin/env python3
"""Pipeline d'ingestion des retours usine — boucle Siemens Fuse (étape 8).

Lit les exports CSV de la fab (runs de fabrication + défauts AOI), les charge
dans le schéma de schema.sql, calcule les taux de défauts PAR MOTIF DE
PLACEMENT, crée les signaux de requalification DRC/DFM et exporte le jeu
d'entraînement supervisé du rl_agent (JSONL : placement_features -> penalty).

Stockage :
  - PostgreSQL si --pg-dsn est fourni et que psycopg2 est installé (prod) ;
  - sinon fallback SQLite (--db, défaut data/manufacturing_feedback/feedback.sqlite3)
    — aucun serveur requis : dev, CI, démonstrations hors ligne.

Usage :
    python3 data/manufacturing_feedback/ingest.py \\
        --factory-csv data/manufacturing_feedback/sample_factory_runs.csv \\
        --defects-csv data/manufacturing_feedback/sample_defects_aoi.csv \\
        --export-rl data/manufacturing_feedback/rl_training_set.jsonl

Sortie console : résumé (runs, défauts, taux par motif, requalifications, RL).
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constantes métier
# ---------------------------------------------------------------------------

# Mapping type de défaut AOI -> règle DRC/DFM à requalifier (drc_dfm_engine).
DEFECT_TO_RULE: Dict[str, str] = {
    "tombstone": "dfm.tombstone",
    "solder_bridge": "dfm.solder_bridge_clearance",
    "component_shift": "drc.component_spacing",
    "insufficient_solder": "dfm.stencil_aperture",
    "excess_solder": "dfm.solder_volume",
    "missing": "dfm.pick_place_feasibility",
    "polarity": "drc.polarity_marking",
}

# Seuils par défaut (surchargeables en CLI) : un motif est requalifié quand son
# taux de défauts par unité produite dépasse REQUAL_THRESHOLD sur au moins
# MIN_SAMPLE_RUNS runs ; assoupli quand il descend sous THRESHOLD/5.
REQUAL_THRESHOLD = 0.005   # 0,5 % d'unités en défaut
MIN_SAMPLE_RUNS = 3

# Fichier de features composants (jointure MPN -> dimensions/bloc fonctionnel).
DEFAULT_COMPONENT_SEED = Path("data/component_library/seed_components.json")

# DDL SQLite — miroir fidèle (dialecte) de data/manufacturing_feedback/schema.sql.
SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS factory_runs (
    run_id          TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    design_version  INTEGER NOT NULL,
    fab             TEXT NOT NULL,
    fab_date        TEXT NOT NULL,
    panels          INTEGER NOT NULL DEFAULT 1,
    units_produced  INTEGER NOT NULL DEFAULT 0,
    units_failed    INTEGER NOT NULL DEFAULT 0,
    process         TEXT NOT NULL DEFAULT 'reflow',
    ingested_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS defects (
    defect_id         TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES factory_runs(run_id),
    ref               TEXT NOT NULL,
    mpn               TEXT NOT NULL,
    defect_type       TEXT NOT NULL,
    x_mm              REAL NOT NULL DEFAULT 0,
    y_mm              REAL NOT NULL DEFAULT 0,
    placement_pattern TEXT NOT NULL DEFAULT 'unclassified',
    disposition       TEXT NOT NULL DEFAULT 'repair',
    detected_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS rule_requalification (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id           TEXT NOT NULL,
    placement_pattern TEXT NOT NULL,
    action            TEXT NOT NULL CHECK (action IN ('tighten', 'loosen')),
    min_clearance_mm  REAL,
    defect_rate       REAL NOT NULL,
    sample_runs       INTEGER NOT NULL,
    triggered_by      TEXT NOT NULL DEFAULT 'ingest.py',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ---------------------------------------------------------------------------
# Connexions (PostgreSQL si dispo, sinon SQLite)
# ---------------------------------------------------------------------------

def connect_postgres(dsn: str):
    """Connexion PostgreSQL — import gardé : psycopg2 peut être absent en dev."""
    try:
        import psycopg2  # type: ignore  # import gardé — driver optionnel
    except ImportError:
        return None
    try:
        conn = psycopg2.connect(dsn)
    except Exception as exc:  # noqa: BLE001 — toute erreur -> fallback SQLite
        print(f"AVERTISSEMENT : connexion PostgreSQL impossible ({exc}) — fallback SQLite", file=sys.stderr)
        return None
    return conn


def open_store(pg_dsn: Optional[str], sqlite_path: Path) -> Tuple[object, str]:
    """Retourne (connexion, backend) — backend = 'postgres' ou 'sqlite'."""
    if pg_dsn:
        conn = connect_postgres(pg_dsn)
        if conn is not None:
            return conn, "postgres"
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(sqlite_path), "sqlite"


def ensure_schema(conn, backend: str) -> None:
    """Crée les tables si absentes (idempotent)."""
    cur = conn.cursor()
    if backend == "sqlite":
        cur.executescript(SQLITE_DDL)
    else:
        schema = Path(__file__).resolve().parent / "schema.sql"
        cur.execute(schema.read_text(encoding="utf-8"))
    conn.commit()


# ---------------------------------------------------------------------------
# Ingestion CSV
# ---------------------------------------------------------------------------

def load_factory_runs(conn, backend: str, csv_path: Path) -> int:
    """Charge factory_runs.csv (run_id, project_id, design_version, fab,
    fab_date, panels, units_produced, units_failed, process)."""
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            rows.append((
                raw["run_id"].strip(),
                raw["project_id"].strip(),
                int(raw["design_version"]),
                raw["fab"].strip(),
                raw["fab_date"].strip(),
                int(raw.get("panels") or 1),
                int(raw.get("units_produced") or 0),
                int(raw.get("units_failed") or 0),
                (raw.get("process") or "reflow").strip(),
            ))
    cur = conn.cursor()
    if backend == "sqlite":
        cur.executemany(
            "INSERT OR REPLACE INTO factory_runs (run_id, project_id, design_version, fab, fab_date,"
            " panels, units_produced, units_failed, process) VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
    else:
        cur.executemany(
            "INSERT INTO factory_runs (run_id, project_id, design_version, fab, fab_date,"
            " panels, units_produced, units_failed, process) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (run_id) DO UPDATE SET units_produced = EXCLUDED.units_produced,"
            " units_failed = EXCLUDED.units_failed",
            rows,
        )
    conn.commit()
    return len(rows)


def load_defects(conn, backend: str, csv_path: Path) -> int:
    """Charge defects_aoi.csv (defect_id, run_id, ref, mpn, defect_type, x_mm,
    y_mm, placement_pattern, disposition)."""
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            rows.append((
                raw["defect_id"].strip(),
                raw["run_id"].strip(),
                raw["ref"].strip(),
                raw["mpn"].strip(),
                raw["defect_type"].strip(),
                float(raw.get("x_mm") or 0.0),
                float(raw.get("y_mm") or 0.0),
                (raw.get("placement_pattern") or "unclassified").strip(),
                (raw.get("disposition") or "repair").strip(),
            ))
    cur = conn.cursor()
    if backend == "sqlite":
        cur.executemany(
            "INSERT OR REPLACE INTO defects (defect_id, run_id, ref, mpn, defect_type,"
            " x_mm, y_mm, placement_pattern, disposition) VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
    else:
        cur.executemany(
            "INSERT INTO defects (defect_id, run_id, ref, mpn, defect_type,"
            " x_mm, y_mm, placement_pattern, disposition) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (defect_id) DO NOTHING",
            rows,
        )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Analyse : taux de défauts par motif de placement + requalification
# ---------------------------------------------------------------------------

def defect_rates_by_pattern(conn, backend: str) -> Dict[str, Dict[str, float]]:
    """Taux de défauts par motif de placement, pondéré par les unités produites.

    Retourne {pattern: {"defects": n, "rate_per_unit": r, "runs": k}} où
    rate_per_unit = somme(defauts du motif) / somme(unités produites des runs
    concernés). C'est le signal brut qui pilote la requalification.
    """
    cur = conn.cursor()
    # Défauts par motif, tous types confondus.
    cur.execute(
        "SELECT d.placement_pattern, d.run_id, COUNT(*) FROM defects d GROUP BY d.placement_pattern, d.run_id"
    )
    defect_rows = cur.fetchall()

    # Unités produites par run (pour pondérer).
    cur.execute("SELECT run_id, units_produced FROM factory_runs")
    units_by_run: Dict[str, int] = {
        r[0]: int(r[1]) for r in cur.fetchall()
    }

    per_pattern: Dict[str, Dict[str, float]] = defaultdict(lambda: {"defects": 0.0, "units": 0.0, "runs": 0.0})
    for pattern, run_id, count in sorted(defect_rows):
        stats = per_pattern[str(pattern)]
        stats["defects"] += float(count)
        stats["units"] += units_by_run.get(str(run_id), 0)
        stats["runs"] += 1.0

    result: Dict[str, Dict[str, float]] = {}
    for pattern in sorted(per_pattern):
        stats = per_pattern[pattern]
        units = max(stats["units"], 1.0)
        result[pattern] = {
            "defects": int(stats["defects"]),
            "rate_per_unit": round(stats["defects"] / units, 6),
            "runs": int(stats["runs"]),
        }
    return result


def write_requalifications(conn, backend: str, rates: Dict[str, Dict[str, float]],
                           threshold: float, min_runs: int) -> List[Tuple[str, str, str, float]]:
    """Crée les signaux de requalification DRC/DFM à partir des taux par motif.

    - tighten : taux > threshold sur >= min_runs runs (règle trop laxiste) ;
    - loosen  : taux < threshold/5 sur >= min_runs runs (contrainte inutilement
      coûteuse — on la relâche pour libérer de la surface de routage).
    """
    cur = conn.cursor()
    emitted: List[Tuple[str, str, str, float]] = []
    for pattern in sorted(rates):
        stats = rates[pattern]
        rate = stats["rate_per_unit"]
        if stats["runs"] < min_runs:
            continue  # pas assez d'échantillons usine pour décider
        if rate > threshold:
            action = "tighten"
        elif rate < threshold / 5.0:
            action = "loosen"
        else:
            continue  # zone grise : on garde les règles courantes
        # Une requalification par règle impactée par le motif (défauts dominants).
        rule_id = DEFECT_TO_RULE.get(_dominant_defect_type(conn, backend, pattern), "drc.component_spacing")
        min_clearance = round(0.25 if action == "tighten" else 0.15, 3)
        if backend == "sqlite":
            cur.execute(
                "INSERT INTO rule_requalification (rule_id, placement_pattern, action, min_clearance_mm,"
                " defect_rate, sample_runs) VALUES (?,?,?,?,?,?)",
                (rule_id, pattern, action, min_clearance, rate, stats["runs"]),
            )
        else:
            cur.execute(
                "INSERT INTO rule_requalification (rule_id, placement_pattern, action, min_clearance_mm,"
                " defect_rate, sample_runs) VALUES (%s,%s,%s,%s,%s,%s)",
                (rule_id, pattern, action, min_clearance, rate, stats["runs"]),
            )
        emitted.append((rule_id, pattern, action, rate))
    conn.commit()
    return emitted


def _dominant_defect_type(conn, backend: str, pattern: str) -> str:
    """Type de défaut le plus fréquent pour un motif (décide la règle visée)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT defect_type, COUNT(*) AS n FROM defects WHERE placement_pattern = ? GROUP BY defect_type"
        " ORDER BY n DESC, defect_type ASC LIMIT 1",
        (pattern,),
    )
    row = cur.fetchone()
    return str(row[0]) if row else "component_shift"


# ---------------------------------------------------------------------------
# Export du jeu d'entraînement RL (JSONL : placement_features -> penalty)
# ---------------------------------------------------------------------------

def export_rl_training_set(conn, backend: str, out_path: Path,
                           components_seed: Optional[Path]) -> int:
    """Exporte un échantillon RL par (mpn, motif de placement).

    features = métadonnées composant (catalogue) + contexte de placement ;
    penalty  = taux de défauts normalisé 0..1 (1 = pire motif observé) —
    c'est la récompense négative que le rl_agent minimisera au placement.
    """
    # Catalogue : MPN -> features géométriques/électriques.
    catalogue: Dict[str, dict] = {}
    if components_seed is not None and components_seed.exists():
        seed = json.loads(components_seed.read_text(encoding="utf-8"))
        catalogue = {c["mpn"]: c for c in seed.get("components", [])}

    cur = conn.cursor()
    # Taux de défauts par (mpn, motif) puis normalisation par le pire taux.
    cur.execute(
        "SELECT mpn, placement_pattern, COUNT(*) FROM defects GROUP BY mpn, placement_pattern ORDER BY mpn, placement_pattern"
    )
    rows = cur.fetchall()
    counts: Dict[Tuple[str, str], int] = {(str(r[0]), str(r[1])): int(r[2]) for r in rows}

    cur.execute("SELECT run_id, units_produced FROM factory_runs")
    units_by_run = {str(r[0]): int(r[1]) for r in cur.fetchall()}
    cur.execute("SELECT mpn, placement_pattern, run_id FROM defects ORDER BY mpn, placement_pattern, run_id")
    per_pair_runs: Dict[Tuple[str, str], set] = defaultdict(set)
    for mpn, pattern, run_id in cur.fetchall():
        per_pair_runs[(str(mpn), str(pattern))].add(str(run_id))

    rates: Dict[Tuple[str, str], float] = {}
    for (mpn, pattern), count in counts.items():
        units = sum(units_by_run.get(r, 0) for r in per_pair_runs[(mpn, pattern)])
        rates[(mpn, pattern)] = count / max(units, 1)
    worst = max(rates.values(), default=0.0) or 1.0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for (mpn, pattern), rate in sorted(rates.items()):
            comp = catalogue.get(mpn, {})
            features = {
                "mpn": mpn,
                "functional_block": comp.get("functional_block", "unknown"),
                "pins": comp.get("pins", 0),
                "width_mm": comp.get("width_mm", 0.0),
                "height_mm": comp.get("height_mm", 0.0),
                "power_w": comp.get("power_w", 0.0),
                "placement_pattern": pattern,
            }
            penalty = round(min(1.0, rate / worst), 4)
            fh.write(json.dumps({"placement_features": features, "penalty": penalty},
                                ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingestion retours usine (boucle Siemens Fuse) + export jeu RL")
    parser.add_argument("--factory-csv", type=Path, required=True,
                        help="CSV des runs de fabrication")
    parser.add_argument("--defects-csv", type=Path, required=True,
                        help="CSV des défauts AOI")
    parser.add_argument("--db", type=Path, default=Path("data/manufacturing_feedback/feedback.sqlite3"),
                        help="SQLite de fallback (défaut : data/manufacturing_feedback/feedback.sqlite3)")
    parser.add_argument("--pg-dsn", default=None,
                        help="DSN PostgreSQL — si fourni et joignable, remplace SQLite")
    parser.add_argument("--export-rl", type=Path,
                        default=Path("data/manufacturing_feedback/rl_training_set.jsonl"),
                        help="Jeu d'entraînement RL JSONL en sortie")
    parser.add_argument("--components", type=Path, default=DEFAULT_COMPONENT_SEED,
                        help="Catalogue composants (jointure features MPN)")
    parser.add_argument("--requal-threshold", type=float, default=REQUAL_THRESHOLD,
                        help="Seuil de taux de défauts déclenchant une requalification")
    parser.add_argument("--min-sample-runs", type=int, default=MIN_SAMPLE_RUNS,
                        help="Nombre minimal de runs avant décision de requalification")
    args = parser.parse_args(argv)

    for required in (args.factory_csv, args.defects_csv):
        if not required.exists():
            print(f"ERREUR : fichier introuvable {required}", file=sys.stderr)
            return 2

    conn, backend = open_store(args.pg_dsn, args.db)
    try:
        ensure_schema(conn, backend)
        n_runs = load_factory_runs(conn, backend, args.factory_csv)
        n_defects = load_defects(conn, backend, args.defects_csv)

        rates = defect_rates_by_pattern(conn, backend)
        requals = write_requalifications(conn, backend, rates,
                                         args.requal_threshold, args.min_sample_runs)
        n_rl = export_rl_training_set(conn, backend, args.export_rl, args.components)

        print(f"backend         : {backend}" + (f" ({args.db})" if backend == "sqlite" else ""))
        print(f"runs ingérés    : {n_runs}")
        print(f"défauts AOI     : {n_defects}")
        print("taux par motif  :")
        for pattern in sorted(rates):
            stats = rates[pattern]
            print(f"  {pattern:<16} {stats['defects']:>4} défauts  {stats['rate_per_unit']*100:6.3f} %/unité  ({stats['runs']} runs)")
        print(f"requalifications: {len(requals)}")
        for rule_id, pattern, action, rate in requals:
            print(f"  {action:<7} {rule_id} (motif {pattern}, taux {rate*100:.3f} %)")
        print(f"jeu RL exporté  : {args.export_rl} ({n_rl} échantillons)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

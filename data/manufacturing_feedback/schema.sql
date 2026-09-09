-- ============================================================================
-- data/manufacturing_feedback/schema.sql — boucle usine (PostgreSQL 16)
-- Base : pcb_designer (docker-compose.yml, service postgres).
-- Appliquer : docker compose exec -T postgres psql -U pcb -d pcb_designer < data/manufacturing_feedback/schema.sql
-- Mirroir sqlite (fallback dev/CI) : constantes SQLITE_DDL de ingest.py.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Un run de fabrication : un lot de panneaux produits par une fab pour une
-- version de design donnée. Source des taux de rendement.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS factory_runs (
    run_id          TEXT PRIMARY KEY,          -- identifiant fab (W12345, LOT-2024-061...)
    project_id      TEXT NOT NULL,             -- projet pcb_ai_designer
    design_version  INTEGER NOT NULL,          -- version immuable concernée (data/projects)
    fab             TEXT NOT NULL,             -- usine (JLCPCB, PCBWay, interne...)
    fab_date        DATE    NOT NULL,
    panels          INTEGER NOT NULL DEFAULT 1 CHECK (panels > 0),
    units_produced  INTEGER NOT NULL DEFAULT 0 CHECK (units_produced >= 0),
    units_failed    INTEGER NOT NULL DEFAULT 0 CHECK (units_failed >= 0),
    process         TEXT    NOT NULL DEFAULT 'reflow',   -- reflow, wave, hand...
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (units_failed <= units_produced)
);

CREATE INDEX IF NOT EXISTS idx_factory_runs_project ON factory_runs (project_id, design_version);
CREATE INDEX IF NOT EXISTS idx_factory_runs_fab_date ON factory_runs (fab_date);

-- ---------------------------------------------------------------------------
-- Défauts AOI (Automated Optical Inspection) : un défaut par ligne, position
-- absolue sur le panneau en mm, classé par type. Source des taux de défauts
-- par motif de placement (signal principal de requalification).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS defects (
    defect_id         TEXT PRIMARY KEY,
    run_id            TEXT    NOT NULL REFERENCES factory_runs (run_id) ON DELETE CASCADE,
    ref               TEXT    NOT NULL,        -- référence composant sur le design (U12, R3...)
    mpn               TEXT    NOT NULL,        -- référence fabricant (joint data/component_library)
    defect_type       TEXT    NOT NULL,        -- tombstone, solder_bridge, component_shift,
                                               -- insufficient_solder, excess_solder, missing, polarity
    x_mm              NUMERIC(8,2) NOT NULL,   -- position panneau (mm)
    y_mm              NUMERIC(8,2) NOT NULL,
    placement_pattern TEXT    NOT NULL DEFAULT 'unclassified',  -- edge_connector, bga_center, dense_group...
    disposition       TEXT    NOT NULL DEFAULT 'repair',        -- scrap, repair, rework, ok
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_defects_run ON defects (run_id);
CREATE INDEX IF NOT EXISTS idx_defects_mpn ON defects (mpn);
CREATE INDEX IF NOT EXISTS idx_defects_pattern ON defects (placement_pattern, defect_type);

-- ---------------------------------------------------------------------------
-- Requalification des règles DRC/DFM : un signal par (règle, motif, horizon).
-- Le drc_dfm_engine versionne ses règles ; cette table décide du sens de
-- requalification (tighten = resserrer, loosen = assouplir) et du delta.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rule_requalification (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_id           TEXT    NOT NULL,        -- ex. drc.component_spacing, dfm.solder_bridge
    placement_pattern TEXT    NOT NULL,        -- motif concerné (agrégation des défauts)
    action            TEXT    NOT NULL CHECK (action IN ('tighten', 'loosen')),
    min_clearance_mm  NUMERIC(6,3),            -- nouvelle clearance proposée (tighten)
    defect_rate       NUMERIC(6,4) NOT NULL,   -- taux de défauts observé (0..1)
    sample_runs       INTEGER  NOT NULL,       -- nb de runs derrière la décision
    triggered_by      TEXT    NOT NULL DEFAULT 'ingest.py',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rule_requal_rule ON rule_requalification (rule_id, placement_pattern, created_at DESC);

COMMENT ON TABLE  factory_runs IS 'Runs de fabrication — rendement par projet/version (boucle Siemens Fuse)';
COMMENT ON TABLE  defects IS 'Défauts AOI positionnés en mm — clé d agrégation : placement_pattern';
COMMENT ON TABLE  rule_requalification IS 'Décisions de requalification DRC/DFM consommées par drc_dfm_engine';

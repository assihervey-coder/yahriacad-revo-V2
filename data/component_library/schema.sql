-- ============================================================================
-- data/component_library/schema.sql — catalogue de composants (PostgreSQL 16)
-- Base : pcb_designer (docker-compose.yml, service postgres).
-- Appliquer : docker compose exec -T postgres psql -U pcb -d pcb_designer < data/component_library/schema.sql
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Table principale : un composant du catalogue (clé métier = MPN).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS components (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mpn               TEXT        NOT NULL,                 -- référence fabricant (DigiKey/Mouser)
    ref_prefix        TEXT        NOT NULL,                 -- U, R, C, L, Y, J, D, ANT...
    description       TEXT        NOT NULL DEFAULT '',
    footprint         TEXT        NOT NULL DEFAULT '',      -- empreinte KiCad 8
    pins              INTEGER     NOT NULL DEFAULT 0 CHECK (pins >= 0),
    width_mm          NUMERIC(6,2) NOT NULL DEFAULT 0,      -- empreinte au sol (placement RL)
    height_mm         NUMERIC(6,2) NOT NULL DEFAULT 0,
    power_w           NUMERIC(8,4) NOT NULL DEFAULT 0,      -- dissipation max (simulation thermique)
    price_usd         NUMERIC(10,4) NOT NULL DEFAULT 0,     -- source DigiKey, sync quotidienne
    stock             INTEGER     NOT NULL DEFAULT 0,       -- stock agrégé DigiKey + Mouser
    functional_block  TEXT        NOT NULL DEFAULT 'passive', -- mcu, power, rf, sensor...
    keywords          TEXT[]      NOT NULL DEFAULT '{}',    -- RAG datasheets + LLM planner
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mpn)
);

-- Index de recherche du planner (MPN partiel, empreinte, bloc fonctionnel).
CREATE INDEX IF NOT EXISTS idx_components_mpn_trgm ON components (mpn text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_components_footprint ON components (footprint);
CREATE INDEX IF NOT EXISTS idx_components_block ON components (functional_block);
CREATE INDEX IF NOT EXISTS idx_components_keywords ON components USING gin (keywords);

-- ---------------------------------------------------------------------------
-- Équivalences 2nd source : un MPN donné peut être remplacé par un équivalent
-- validé (même empreinte + brochage compatible). Utilisé par le planner quand
-- le stock d'un MPN s'épuise — jamais automatiquement sans validation ERC.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mpn_alternates (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mpn            TEXT        NOT NULL,   -- référence d'origine
    alternate_mpn  TEXT        NOT NULL,   -- équivalent 2nd source
    verified       BOOLEAN     NOT NULL DEFAULT FALSE,  -- brochage vérifié manuellement
    verified_by    TEXT        NOT NULL DEFAULT 'system',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mpn, alternate_mpn),
    -- interdire une auto-équivalence triviale
    CHECK (mpn <> alternate_mpn)
);

CREATE INDEX IF NOT EXISTS idx_mpn_alternates_mpn ON mpn_alternates (mpn);

-- Commentaires de colonnes (documentation dans la base elle-même).
COMMENT ON TABLE  components IS 'Catalogue de composants — source : DigiKey/Mouser (sync quotidienne), empreintes KiCad 8 gelées';
COMMENT ON COLUMN components.functional_block IS 'Bloc fonctionnel pour le regroupement placement : mcu, power, rf, sensor, connector, memory, timing, passive, protection, usb, motor';
COMMENT ON TABLE  mpn_alternates IS 'Équivalences 2nd source validées (remplacement uniquement si verified = true)';

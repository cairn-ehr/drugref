-- db/009_schema_local_tier.sql
-- drugref LOCAL tier, slice 8a: Australian PBS products and their bridge to the
-- global moiety spine.
--
-- LICENCE (spec section 1) -- read before extending this file:
-- PBS data is NOT bundled or redistributed by drugref. A node operator ingests it
-- into their own database under whatever terms bind them. Critically, ATC codes
-- (WHO, NonCommercial + NoDerivatives) and AMT/SNOMED CT-AU concept IDs (NCTS
-- affiliate licence) may NEVER enter drugref. That is why there is no atc_code or
-- amt_code column below and why there must never be one: the schema is the last
-- line of a defence whose first line is simply not reading those files.
--
-- WHY NO APPEND-ONLY FLOOR HERE (contrast db/001):
-- slice 1's floor guards substance IDENTITY, which is immortal. These tables are a
-- REBUILDABLE PROJECTION of a monthly upstream release: re-ingesting DELETEs this
-- source's rows and re-inserts, so an item DE-LISTED by PBS disappears here too. A
-- no-DELETE trigger would make that impossible. Stability comes from determinism
-- instead -- local_product_uuid is a pure function of (jurisdiction, source, code),
-- so every surviving product returns with the UUID it had before (src/drugref/ids.py).

-- The ingest_run.source CHECK (db/005) is the key every per-source rebuild joins
-- through, so a new authority must be admitted explicitly rather than by accident.
-- NOTE: the constraint db/005 actually created is named `ingest_run_source` (NOT
-- `ingest_run_source_check` -- that suffix is only what Postgres auto-generates for
-- an UNNAMED check constraint; db/005 named this one explicitly). Verified against
-- the live schema with \d drugref.ingest_run before writing this DROP, because
-- dropping the wrong name with IF EXISTS would silently no-op and leave the old
-- constraint rejecting 'PBS' with no error to say why.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS'));

-- One row per PBS item INSTANCE. Keyed on li_item_id rather than the PBS Item
-- Code because a PBS code is a PRESCRIBING RULE (drug x form x max-quantity x
-- repeats x restriction x program) that covers MANY BRANDS -- measured on the
-- 2026-07 release: 14,840 item rows across only 6,945 codes. Keying on the code
-- would collapse every brand of a molecule into one row.
CREATE TABLE IF NOT EXISTS drugref.local_product (
    local_product_uuid uuid   PRIMARY KEY,   -- uuid5(LOCAL_PRODUCT_NAMESPACE,'AU:PBS:'||source_code)
    jurisdiction        text   NOT NULL,
    source              text   NOT NULL,
    source_code         text   NOT NULL,      -- PBS li_item_id (unique per row upstream)
    pbs_code            text,                 -- the recognisable Item Code, an ATTRIBUTE not the key
    brand_name          text,
    drug_name           text,                 -- li_drug_name (or drug_name): the licence-clean name
    form_strength       text,
    program_code        text,
    benefit_type_code   text,                 -- U/R/S/A: the restriction LEVEL only, never its text
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    CONSTRAINT local_product_jurisdiction CHECK (jurisdiction IN ('AU')),
    CONSTRAINT local_product_source       CHECK (source IN ('PBS')),
    CONSTRAINT local_product_benefit_type
        CHECK (benefit_type_code IS NULL OR benefit_type_code IN ('U', 'R', 'S', 'A')),
    CONSTRAINT local_product_natural_key UNIQUE (jurisdiction, source, source_code)
);

-- The name-resolved bridge to the global spine. An EDGE TABLE, not a column on
-- local_product, for two reasons: a combination product resolves to SEVERAL
-- moieties, and slices 3/4 (salt, clinical drug) do not exist yet -- so when they
-- land, the attachment point can be refined WITHOUT re-keying any product.
CREATE TABLE IF NOT EXISTS drugref.local_product_moiety (
    local_product_uuid uuid   NOT NULL REFERENCES drugref.local_product(local_product_uuid),
    moiety_uuid         uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    component_name      text   NOT NULL,      -- the ingredient name that resolved
    match_method        text   NOT NULL,      -- how it resolved; see the CHECK
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (local_product_uuid, moiety_uuid, component_name),
    -- 'salt_stripped' marks a row that matched only after a trailing salt/hydrate
    -- token was removed -- a HEURISTIC standing in for slice 3's GSRS active-moiety
    -- relationships. Recording it per row is what lets a consumer ignore the
    -- heuristic entirely instead of having to trust it (spec 5.1).
    CONSTRAINT local_product_moiety_match_method
        CHECK (match_method IN ('exact', 'salt_stripped'))
);

-- Coverage made QUERYABLE. An ingredient PBS lists that no moiety carries is not
-- an error and not a silent drop: many are foods, dressings and extemporaneous
-- chemicals that slice 1's gate excludes BY DESIGN. Persisting them is what turns
-- "how much do we not know" into a number (spec 7), mirroring
-- ingest_unmatched_ingredient.
CREATE TABLE IF NOT EXISTS drugref.local_unmatched_ingredient (
    ingest_run     bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    jurisdiction   text   NOT NULL,
    source         text   NOT NULL,
    source_code    text   NOT NULL,          -- which PBS item raised it
    component_name text   NOT NULL           -- the name that matched no moiety
);

-- The rebuild-delete path joins ingest_run by source, so index what it filters on.
CREATE INDEX IF NOT EXISTS local_product_by_run
    ON drugref.local_product (ingest_run);
CREATE INDEX IF NOT EXISTS local_product_moiety_by_run
    ON drugref.local_product_moiety (ingest_run);
CREATE INDEX IF NOT EXISTS local_product_moiety_by_moiety
    ON drugref.local_product_moiety (moiety_uuid);
CREATE INDEX IF NOT EXISTS local_unmatched_by_run
    ON drugref.local_unmatched_ingredient (ingest_run);

COMMENT ON TABLE drugref.local_product IS
    'PBS item instances (AU local tier). Rebuildable projection, NOT bundled data: '
    'drugref ships the ingest code; a node operator supplies the PBS release.';
COMMENT ON TABLE drugref.local_product_moiety IS
    'Name-resolved bridge from a local product to global moieties. match_method '
    'separates exact matches from the salt-strip heuristic (a slice-3 stand-in).';
COMMENT ON TABLE drugref.local_unmatched_ingredient IS
    'Ingredient names PBS lists that no moiety carries. Expected, not failure: '
    'foods/dressings/excipients are outside the slice-1 moiety gate by design.';

-- db/049_drugcentral_ddi.sql
-- =============================================================================
-- DrugCentral's `ddi` table as drugref's third interaction candidate source.
-- Design: docs/superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-design.md
-- Measurement it rests on:
--   docs/superpowers/specs/2026-08-23-drugref-drugcentral-ddi-remeasurement-results.md
--
-- RULE 6 IN ONE LINE: only `ddi_ref_id = 2` (VHA NDF-RT, a US federal work) is
-- ingested. `1` is Stockley's Drug Interactions (a copyrighted book) and `3` is
-- Lexicomp Online (a commercial compendium); both are permanently out, and
-- DrugCentral's own CC BY-SA 4.0 on the compilation is not evidence of a right to
-- relicense a third-party compendium inside it. The orchestrator additionally
-- verifies the dump's `reference` row identity before admitting a row, because `2`
-- is a surrogate key and a re-publication is free to renumber it.
-- =============================================================================

-- ============================================================================
-- 1. The source vocabulary -- two CHECKs and one Python table, in one commit
-- ============================================================================
-- COPIED VERBATIM from the live catalog and then extended by one value, never
-- retyped from a document. db/039's comment records the reason: a plan's stale
-- list still said ('MED-RT','MeSH') and would have DROPPED 'DRUGREF'.
--
-- src/drugref/ids.py gains "DRUGCENTRAL": "DRUGCENTRAL" in the same commit, and
-- src/drugref/provenance.py gains 'drugcentral_run'. The three are a TRIO: the
-- failure mode when one lands without the others is silent -- ids.canonical_source
-- would fold the source to a spelling this CHECK does not admit, and a per-source
-- rebuild would delete nothing and report success.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF', 'GSRS',
                      'ONCHIGH', 'FDA-CYP', 'DRUGCENTRAL'));

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run',
                      'onchigh_run', 'fda_cyp_run', 'drugcentral_run'));

-- NOTE what is deliberately NOT widened, because a state file said otherwise:
-- class_contraindication_source stays ('MED-RT','ONCHIGH') and
-- moiety_contraindication_source stays ('MED-RT'). DrugCentral writes no class
-- rule and no row into either table -- its assertions are unordered moiety pairs
-- with a severity, which is neither shape.

-- ============================================================================
-- 2. ddi_source_severity -- an upstream band mapped to a drugref grade, AS DATA
-- ============================================================================
-- WHY A TABLE AND NOT FOUR LINES OF PYTHON. db/006's finding, one tier up: a
-- vocabulary written in code and in a CHECK is two lists to widen and one way to
-- disagree. And this mapping is additionally a CLINICAL JUDGEMENT that drugref
-- makes on a consumer's behalf -- a node operator must be able to SELECT it,
-- disagree with it, and see exactly what it did. Revising it is then a migration
-- over two rows rather than a re-ingest of 7,571.
--
-- THE MAPPING. VA/NDF-RT's own wording is "Critical = avoid the combination" and
-- "Significant = may have clinical consequences; monitor or adjust", so each band
-- maps to the drugref grade that says the same thing.
--
-- `major` CARRIES NO DRUGCENTRAL ROW, and that is a signal rather than an
-- omission: a two-band authority has two bands. The cost is stated rather than
-- hidden -- some `Significant` pairs (fluvoxamine + tapentadol, apixaban +
-- heparin) are arguably major and are graded a notch low. That is what the
-- curated overlay exists to correct, one pair at a time, and it is why this
-- mapping's revisability is load-bearing.
CREATE TABLE IF NOT EXISTS drugref.ddi_source_severity (
    source       text NOT NULL,
    source_label text NOT NULL,
    -- db/035's four grades and THEIR CLINICAL ORDER. A foreign key, not a CHECK:
    -- severity_rank is what decides which of two disagreeing grades a consumer
    -- sees, and a level with no agreed rank would make that non-deterministic.
    severity     text NOT NULL REFERENCES drugref.severity_kind(severity),
    -- Keyed PER SOURCE, because two authorities may both use the word
    -- 'Significant' and mean different things by it.
    PRIMARY KEY (source, source_label)
);

INSERT INTO drugref.ddi_source_severity (source, source_label, severity) VALUES
    ('DRUGCENTRAL', 'Critical',    'contraindicated'),
    ('DRUGCENTRAL', 'Significant', 'moderate')
ON CONFLICT (source, source_label) DO NOTHING;

COMMENT ON TABLE drugref.ddi_source_severity IS
    'How one upstream authority''s severity vocabulary maps onto drugref''s four '
    'grades. SEEDED, NOT CURATED: a new mapping is a migration, deliberately, '
    'because the mapping is a clinical judgement drugref makes on a consumer''s '
    'behalf and it must be inspectable by anyone who can run a query. The '
    'candidate tier stores the upstream label VERBATIM (drugcentral_ddi_assertion.'
    'severity_label) and derives the grade through this table, so the authority''s '
    'own words survive and drugref''s reading of them is separately visible.';
COMMENT ON COLUMN drugref.ddi_source_severity.source_label IS
    'The upstream string EXACTLY as published -- ''Critical'', not ''critical''. '
    'Folding it here would put a case rule in a second place.';

-- ============================================================================
-- 3. drugcentral_ddi_assertion -- every bundleable row, exactly as published
-- ============================================================================
-- A rebuildable projection keyed by ingest_run.source: delete-and-rebuild, like
-- every other ingested feed. The shape is db/039's fda_cyp_assertion, whose
-- comment states the principle -- "it holds every tuple the parser produced,
-- members and withheld alike, because the withheld ones are the point". Here the
-- withheld ones are the 37 rows whose endpoint drugref cannot key, and holding
-- them is what lets section 5's gap view exist without a table of its own.
CREATE TABLE IF NOT EXISTS drugref.drugcentral_ddi_assertion (
    ingest_run      bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- Symmetric with class_contraindication.source and every other projection's:
    -- widened per source as authorities land, not left open.
    source          text   NOT NULL
        CONSTRAINT drugcentral_ddi_assertion_source CHECK (source = 'DRUGCENTRAL'),
    -- DrugCentral's `ddi.source_id` -- the VA's OWN key for the interaction record
    -- ('C56^4966^'), NOT `ddi.id`. Measured 2026-08-23: all 7,571 bundleable rows
    -- carry a distinct source_id, so it is a valid key -- and it is the upstream
    -- AUTHORITY's identifier rather than an artifact of one dump's row numbering,
    -- which is what a key anything downstream might cite has to be.
    upstream_key    text   NOT NULL,
    -- The two endpoints AS THE DUMP GIVES THEM (`drug_class1`, `drug_class2`).
    -- Verbatim, never folded: fold_name is the resolver's rule and belongs in one
    -- place. The names are stored even when they resolved, because a name that
    -- STOPS resolving in a later release is only diagnosable against the name the
    -- earlier run actually read.
    endpoint_1_name text   NOT NULL,
    endpoint_2_name text   NOT NULL,
    -- `ddi.description`. MEASURED 2026-08-23: all 7,571 match
    -- 'NAME1/NAME2 [VA Drug Interaction]' -- 35 to 75 characters, no clinical
    -- content whatsoever, so issue 101's "every row carries a description" is true
    -- and empty. It is stored anyway for one reason: it names the endpoints at
    -- PRODUCT/SALT grain ('PIOGLITAZONE HCL', 'INDINAVIR SULFATE') while the
    -- endpoint columns carry the base, and that is the only visible explanation
    -- for why 33 pairs appear twice (see the view in section 4).
    upstream_label  text   NOT NULL,
    -- `ddi_risk`, VERBATIM -- 'Critical' or 'Significant' in this subset. drugref's
    -- own grade is DERIVED through ddi_source_severity, never stored here, so the
    -- authority's own words and drugref's reading of them stay separately visible.
    severity_label  text   NOT NULL,
    -- NULLABLE, and that is the whole design of this table. An endpoint drugref
    -- cannot key leaves the row here with a NULL uuid and a route saying why.
    moiety_1_uuid   uuid   REFERENCES drugref.substance_moiety(moiety_uuid),
    moiety_2_uuid   uuid   REFERENCES drugref.substance_moiety(moiety_uuid),
    -- HOW each endpoint resolved, or why it did not. The vocabulary is
    -- drugcentral_resolve.ROUTES and this CHECK is its SECOND home -- admitted
    -- deliberately and pinned by test_the_route_checks_match_the_python_vocabulary,
    -- on the same terms ids._SOURCE_CANONICAL and ingest_run_source already live
    -- under. `missing_keys_row` is in the list on purpose: it means a struct_id was
    -- found by name and is absent from the key index, which cannot happen on a
    -- well-formed extract -- counted apart so a corrupt extract does not pass for a
    -- difficult one.
    route_1         text   NOT NULL,
    route_2         text   NOT NULL,
    PRIMARY KEY (ingest_run, source, upstream_key),
    -- THE LOAD-BEARING CONSTRAINT. A release inventing a third band is refused at
    -- INSERT, loudly, rather than stored and silently mapped to nothing by the
    -- view's join. db/006's lesson applied to a vocabulary that crosses a source
    -- boundary.
    CONSTRAINT drugcentral_ddi_assertion_severity
        FOREIGN KEY (source, severity_label)
        REFERENCES drugref.ddi_source_severity(source, source_label),
    CONSTRAINT drugcentral_ddi_assertion_route_1 CHECK (route_1 IN
        ('display_name', 'inchikey', 'cas',
         'not_a_substance', 'no_structural_key', 'missing_keys_row', 'unresolved')),
    CONSTRAINT drugcentral_ddi_assertion_route_2 CHECK (route_2 IN
        ('display_name', 'inchikey', 'cas',
         'not_a_substance', 'no_structural_key', 'missing_keys_row', 'unresolved')),
    -- ONE CHECK PER ENDPOINT, not two nullable columns nobody cross-checks --
    -- curated_interaction_ruling_is_complete's shape. "Resolved but no uuid" and
    -- "a uuid on an unresolved route" are both UNREPRESENTABLE rather than merely
    -- discouraged.
    CONSTRAINT drugcentral_ddi_assertion_endpoint_1_complete
        CHECK ((route_1 IN ('display_name', 'inchikey', 'cas'))
               = (moiety_1_uuid IS NOT NULL)),
    CONSTRAINT drugcentral_ddi_assertion_endpoint_2_complete
        CHECK ((route_2 IN ('display_name', 'inchikey', 'cas'))
               = (moiety_2_uuid IS NOT NULL))
    -- NO SELF-PAIR CHECK, and the asymmetry with db/014's
    -- moiety_contraindication_not_self is deliberate. There a self-pair is a
    -- malformed assertion; here it is a CONSEQUENCE OF RESOLUTION -- two endpoint
    -- names legitimately folding onto one moiety -- so refusing it would abort an
    -- ingest over a correct reading of the source. The view in section 4 excludes
    -- it and the orchestrator's summary counts it as its own bucket, so it cannot
    -- become nonzero unnoticed. Measured 2026-08-23: 0 of 7,571.
);

CREATE INDEX IF NOT EXISTS drugcentral_ddi_assertion_by_moiety_1
    ON drugref.drugcentral_ddi_assertion (moiety_1_uuid);
CREATE INDEX IF NOT EXISTS drugcentral_ddi_assertion_by_moiety_2
    ON drugref.drugcentral_ddi_assertion (moiety_2_uuid);

COMMENT ON TABLE drugref.drugcentral_ddi_assertion IS
    'DrugCentral''s `ddi` table, `ddi_ref_id = 2` only (VHA NDF-RT), one row per '
    'published assertion. A REBUILDABLE PROJECTION, CANDIDATE TIER -- the 2023 '
    'release does not refresh, so rows feed review and must not auto-alert. '
    'UNORDERED: endpoint_1 and endpoint_2 are NOT subject and object. Measured '
    '2026-08-23, no ordered endpoint pair repeats and 33 appear in BOTH orders, '
    'so this source asserts no direction -- read drugcentral_ddi_pair, which '
    'canonicalises, rather than either endpoint column alone.';
COMMENT ON COLUMN drugref.drugcentral_ddi_assertion.severity_label IS
    'The upstream band VERBATIM. drugref''s grade is ddi_source_severity''s job.';

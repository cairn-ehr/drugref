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

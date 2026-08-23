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

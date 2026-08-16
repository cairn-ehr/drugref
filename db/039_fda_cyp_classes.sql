-- db/039_fda_cyp_classes.sql
-- Slice 5c.2g: admit FDA's CYP/transporter examples table as a CLASSIFICATION
-- source -- the potency vocabulary MED-RT cannot express and SPL mining needs.
--
-- Spec: docs/superpowers/specs/2026-08-16-drugref-slice-5c2g-fda-cyp-classes-design.md
--
-- SCHEMA ONLY. No parser, no orchestrator, no data lands here. This slice adds
-- classification MEMBERSHIP and nothing else: it touches no curated_* table, no
-- class_contraindication, no read path, and creates NO DDI pair. FDA describes
-- its table as an optional, non-exhaustive interpretive guide; joining its
-- inhibitor and substrate columns would manufacture ~800 pairs no source asserts
-- (spec section 9).

-- ============================================================================
-- 1. The three-place source vocabulary
-- ============================================================================
-- Copied VERBATIM from the live catalog before adding one value each -- db/031's
-- discipline, because retyping either list from memory is how a value goes
-- silently missing, and both CHECKs gate every ingest in the project.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF', 'GSRS',
                      'ONCHIGH', 'FDA-CYP'));

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run',
                      'onchigh_run', 'fda_cyp_run'));

-- db/003 created this CHECK with a comment ending "Extend it and
-- _SOURCE_CANONICAL together when a source lands." db/020 (Plan C) already
-- extended it once, to ('MED-RT', 'MeSH', 'DRUGREF') -- read live rather than
-- taken from this slice's own plan, which still said only ('MED-RT', 'MeSH')
-- and would have DROPPED 'DRUGREF' had its list been retyped instead of copied.
-- FDA-CYP is the first class-defining source to land since db/020, so this is
-- the same instruction being followed again, on top of the current list rather
-- than a stale one. src/drugref/ids.py gains its entry in the same commit; the
-- two are a pair, and a test asserts both.
ALTER TABLE drugref.substance_class DROP CONSTRAINT IF EXISTS substance_class_source;
ALTER TABLE drugref.substance_class ADD CONSTRAINT substance_class_source
    CHECK (source IN ('MED-RT', 'MeSH', 'DRUGREF', 'FDA-CYP'));

-- NOTE what is deliberately NOT widened: substance_class_concept_type already
-- admits 'PK' and class_membership_relationship already admits 'has_PK', both
-- since db/003. This slice reuses those vocabularies rather than adding to them,
-- which is the whole argument for projecting FDA's roles as PK classes instead
-- of inventing a mechanism.

-- ============================================================================
-- 2. fda_cyp_assertion -- every parsed tuple, including the ones NOT promoted
-- ============================================================================
-- A rebuildable projection keyed by ingest_run.source, in the shape
-- ingest_unresolved_onc_endpoint (db/031) established: a row here is a WORKLIST
-- ENTRY, not an error and not a drop.
--
-- It holds every tuple the parser produced -- members and withheld alike --
-- because the withheld ones are the point. 29 of 337 cells carry a footnote, and
-- two of those footnotes NEGATE the row they sit on: bupropion's row asserts
-- '2B6 sensitive substrate' while footnote 2 says "Bupropion itself is not a
-- sensitive substrate", and rolapitant's asserts P-gp/BCRP inhibition while
-- footnote 17 denies it for the IV route. Promoting those to membership would
-- make drugref assert the opposite of its cited source; deciding they are
-- negated is a clinical reading of prose. Storing the row with its footnote and
-- withholding the membership is the only option that neither asserts nor
-- discards (spec sections 3 and 5).
CREATE TABLE IF NOT EXISTS drugref.fda_cyp_assertion (
    ingest_run       bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source           text   NOT NULL
        CONSTRAINT fda_cyp_assertion_source CHECK (source = 'FDA-CYP'),
    -- The row's 1-based position in FDA's table. FDA publishes no row id, and the
    -- substance name is NOT unique (aprepitant occupies two rows), so this is the
    -- only stable within-release handle back to the exact upstream line.
    row_ordinal      integer NOT NULL,
    -- The substance name AS FDA PRINTS IT, footnote markers and all. The raw fact,
    -- never a guess at what it should have been -- ingest_unresolved_onc_endpoint's
    -- identifier_value has the same contract.
    raw_substance    text   NOT NULL,
    resolved_moiety_uuid uuid REFERENCES drugref.substance_moiety(moiety_uuid),
    -- FDA's own column heading ('CYP Mod INH'), carried so a curator can find the
    -- exact cell, and because it is half of the role cross-check (spec section 8).
    column_heading   text   NOT NULL,
    raw_cell         text   NOT NULL,
    system           text   NOT NULL
        CONSTRAINT fda_cyp_assertion_system CHECK (system IN ('CYP', 'transporter')),
    pathway          text   NOT NULL,
    role             text   NOT NULL
        CONSTRAINT fda_cyp_assertion_role
        CHECK (role IN ('inhibitor', 'inducer', 'substrate')),
    -- NULL for transporters, which FDA gives no potency vocabulary at all. A
    -- nullable column here is the honest representation of "this axis has no
    -- band", not a missing value.
    potency          text
        CONSTRAINT fda_cyp_assertion_potency
        CHECK (potency IS NULL OR potency IN ('strong', 'moderate', 'weak',
                                              'sensitive', 'moderate sensitive')),
    class_uuid       uuid REFERENCES drugref.substance_class(class_uuid),
    footnote_markers text,
    footnote_text    text,
    -- CURATOR EVIDENCE, NEVER COVERAGE. What a stated prefix rule found in the
    -- registry near an unresolved name, so a curator need not redo the search.
    -- A row carrying one is EXACTLY as unresolved as one carrying none, and NO
    -- COUNT MAY EVER BE QUOTED AGAINST IT. The DrugCentral evaluation already
    -- paid for this lesson: its prefix heuristic "matched" glycerol to
    -- glycerol 1,3-dimethacrylate, a different substance, and its own note says
    -- "treat it as the shape of the problem, not a count to quote."
    registry_near_name text,
    -- FIVE VALUES, NOT NINE -- spec section 7.1, and the reason is the standing
    -- rule (PROJECT-NOTES): a disposition records what was OBSERVED, never what
    -- the round suspects it MEANS. The resolution residue splits into six
    -- recognisable categories, and only these two name one, because only these
    -- two are asserted by FDA: combination_regimen from the regimen string FDA
    -- wrote, non_drug_entity from FDA's own pinned five-substance sentence.
    -- Calling R-venlafaxine an "enantiomer of a held racemate" would be a
    -- chemical relationship inferred from a string prefix -- issue 122's
    -- manufactured-cause defect. Those four collapse to unresolved_substance.
    disposition      text   NOT NULL
        CONSTRAINT fda_cyp_assertion_disposition
        CHECK (disposition IN ('member', 'withheld_qualified', 'unresolved_substance',
                               'combination_regimen', 'non_drug_entity')),
    PRIMARY KEY (ingest_run, row_ordinal, column_heading, pathway)
);

COMMENT ON TABLE drugref.fda_cyp_assertion IS
    'Every (substance x pathway x role x potency) tuple parsed from FDA''s '
    'CYP/transporter examples table, INCLUDING the ones deliberately not promoted '
    'to class_membership. A rebuildable projection keyed by ingest_run.source. '
    'Rows with disposition <> ''member'' are a WORKLIST, not errors and not drops: '
    'ingest preserves evidence, curation creates clinical judgement.';

COMMENT ON COLUMN drugref.fda_cyp_assertion.registry_near_name IS
    'Curator evidence, NEVER coverage. A row carrying a near name is exactly as '
    'unresolved as one without, and no coverage figure may be computed from this '
    'column. See db/039''s header and the DrugCentral glycerol precedent.';

CREATE INDEX IF NOT EXISTS fda_cyp_assertion_by_disposition
    ON drugref.fda_cyp_assertion (disposition);

-- ============================================================================
-- 3. The gap view
-- ============================================================================
-- Grouped on (source, raw_substance, column_heading, pathway) -- dropping ONLY
-- ingest_run, exactly as db/016 and db/031 dropped it -- so one view row is one
-- independently-answerable fact and the view's grain matches the grain the
-- gap_key built from it uses. db/017's lesson, restated because it has bitten
-- twice: grouping coarser folds two independent facts onto one immortal
-- question_uuid; grouping finer mints two questions for one fact.
--
-- 'member' rows are excluded: a membership drugref already wrote asks nobody
-- anything.
CREATE OR REPLACE VIEW drugref.gap_fda_cyp_unadjudicated AS
SELECT a.source,
       a.raw_substance,
       a.column_heading,
       a.pathway,
       max(a.disposition)        AS disposition,
       max(a.raw_cell)           AS raw_cell,
       max(a.footnote_text)      AS footnote_text,
       max(a.registry_near_name) AS registry_near_name,
       max(r.upstream_release)   AS upstream_release
FROM   drugref.fda_cyp_assertion a
JOIN   drugref.ingest_run r ON r.ingest_run_id = a.ingest_run
WHERE  a.disposition <> 'member'
GROUP  BY a.source, a.raw_substance, a.column_heading, a.pathway;

COMMENT ON VIEW drugref.gap_fda_cyp_unadjudicated IS
    'FDA-CYP tuples awaiting a human: a footnote nobody has adjudicated, a name '
    'drugref did not resolve, a regimen, or a non-drug entity. One row per '
    '(source, raw_substance, column_heading, pathway) -- the grain a gap_key built '
    'from this view must also use. ABSENCE OF A ROW IS NOT COVERAGE.';

-- ============================================================================
-- 4. The seventeenth question kind
-- ============================================================================
-- Guarded on the constraint's TEXT rather than its name, so a replay against an
-- already-widened database skips the drop/add entirely instead of rescanning --
-- the idiom db/016, db/019, db/022, db/028, db/029 and db/031 all reuse.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%fda_cyp_unadjudicated%') THEN
        ALTER TABLE drugref.open_question DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question ADD CONSTRAINT open_question_gap_kind
            CHECK (gap_kind IN (
                -- COPIED VERBATIM from the live catalog, then extended by one.
                -- Retyping this list from memory would silently drop a kind and
                -- orphan every question already minted under it. The live catalog
                -- read SIXTEEN values, not the fifteen this slice's own plan
                -- expected -- db/035 (slice "class grain") added
                -- 'uncurated_class_interaction_rule' after this plan was written,
                -- and reading live rather than retyping is exactly what caught it.
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root',
                'unresolved_ci_object', 'dead_by_expansion_policy',
                'condition_without_indication', 'uncurated_additive_effect',
                'uncurated_threshold', 'ineffective_contribution',
                'ungraded_contribution', 'unruled_composition_activity',
                'uncurated_condition_contradiction', 'uncurated_interaction_rule',
                'unresolved_onc_endpoint', 'uncurated_class_interaction_rule',
                'fda_cyp_unadjudicated'));
    END IF;
END $$;

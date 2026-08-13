-- db/031_onc_high_priority.sql
-- Slice 5c.2: admit the ONC high-priority DDI list as a SECOND candidate source
-- beside MED-RT.
--
-- Spec: docs/superpowers/specs/2026-08-11-drugref-slice-5c2-onc-ddi-floor-design.md
--
-- SCHEMA ONLY. No parser, no orchestrator, no data lands in this migration -- it
-- only widens the shapes a later slice-5c.2 writer needs so that its own tests
-- are not also blocked on schema. class_contraindication and ingest_run are
-- CANDIDATE-TIER, rebuildable projections; nothing here touches
-- curated_interaction's append-only floor.
--
-- ============================================================================
-- 1. class_contraindication admits ONCHIGH
-- ============================================================================
-- 5c.1 (db/006) put `source` INTO class_contraindication's primary key
-- (subject, object, relationship, source) precisely so a second authority's row
-- would never collide with MED-RT's on the same rule -- and curated_interaction's
-- own key deliberately OMITS source (db/029, section 1) because that tier holds
-- drugref's OWN judgement, not a record of who said it. Both decisions were made
-- for a candidate tier designed to carry MORE THAN ONE upstream authority; ONCHIGH
-- is the first one to actually arrive, so this is the first time either decision
-- pays for itself rather than merely being future-proofing.
-- IF EXISTS on the DROP, matching the house style every other drop-and-add
-- widening in this file (and db/009, db/020, db/028) already uses: apply_migrations
-- runs this file exactly once per database, but a hand-run `psql -f` replay (the
-- documented exposure db/029 names) should be a no-op, not an error.
ALTER TABLE drugref.class_contraindication
    DROP CONSTRAINT IF EXISTS class_contraindication_source;
ALTER TABLE drugref.class_contraindication
    ADD CONSTRAINT class_contraindication_source
    CHECK (source IN ('MED-RT', 'ONCHIGH'));

-- ============================================================================
-- 2. ingest_run admits the ONCHIGH source and its onchigh_run writer
-- ============================================================================
-- Every projection row below carries an ingest_run, so missing this step stops
-- everything -- db/028's note on the same trap. The two lists are copied
-- VERBATIM from the live catalog (`\d drugref.ingest_run` against drugref_test,
-- 2026-08-11) before adding the one new value each: retyping either from memory
-- is how a value goes silently missing, and both CHECKs gate every ingest in the
-- project.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF', 'GSRS',
                      'ONCHIGH'));

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run',
                      'onchigh_run'));

-- ============================================================================
-- 3. A third expansion axis: CI_EPC over has_EPC
-- ============================================================================
-- ci_axis (db/006) maps each contraindication predicate to the class_membership
-- axis its object class expands over -- CI_MoA over has_MoA, CI_PE over has_PE.
-- The ONC list names its drug classes by EPC (Established Pharmacologic Class,
-- e.g. "Nonsteroidal Anti-inflammatory Drug [EPC]"), which is NOT the same
-- vocabulary as MoA and does not reduce to it: measured against the current
-- registry, Cyclooxygenase Inhibitors [MoA] carries 56 members against
-- Nonsteroidal Anti-inflammatory Drug [EPC]'s 21, while Potassium-sparing
-- Diuretic [EPC] (2 members) has no usable MoA twin at all. Neither vocabulary
-- subsumes the other, so mapping ONC's EPC classes onto MoA would both invent
-- members MoA never asserted and drop classes MoA has no equivalent for -- a
-- third axis is the only faithful representation. has_EPC memberships already
-- exist (1,525 of them, from MED-RT's own EPC-to-drug 'Parent Of' edges;
-- db/002's note on concept_type EPC), so this inserts only the axis mapping, not
-- new membership data. expands_descendants = true for the same reason it is true
-- for CI_MoA/CI_PE (db/010): 65 of the 811 [EPC] classes have children, so
-- direct-only expansion would lose real pairs the same way it would on MoA/PE.
INSERT INTO drugref.ci_axis (relationship, membership_relationship, expands_descendants)
VALUES ('CI_EPC', 'has_EPC', true)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 4. The ONC endpoint worklist: record what resolved to nothing, then derive a
--    gap over it
-- ============================================================================
-- db/016's precedent for CI_ChemClass objects, one level earlier in the pipeline.
-- There, an OBJECT resolved to no class. Here, an ENDPOINT (either side of an ONC
-- pair -- the list names drugs and drug classes by free-text/RxNorm-ish
-- identifiers, not MED-RT NUIs) resolves to no moiety or class at all, so the
-- whole entry cannot be turned into a class_contraindication row. Losing it
-- SILENTLY would mean an ONC high-priority pair drugref claims to cover in fact
-- is not covered; recording it here is what lets a curator (or a later, better
-- matcher) close the gap instead of it going unnoticed.
--
-- KEYED PER ROLE, deliberately. A single ONC entry has a subject and an object,
-- and they can each fail to resolve INDEPENDENTLY of one another -- warfarin
-- might resolve while "NSAID" does not, or vice versa. Folding both roles into
-- one row would make the row's meaning depend on which side failed, and a
-- re-ingest that fixes only one side would have nothing stable to update. Two
-- independently-failing facts get two rows, exactly as db/016's object-kind
-- split keeps two different reasons-for-absence apart rather than merging them.
CREATE TABLE IF NOT EXISTS drugref.ingest_unresolved_onc_endpoint (
    ingest_run       bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- Symmetric with class_contraindication.source and
    -- ingest_unresolved_ci_object.source: widened per source as authorities land,
    -- not left open, so a future source cannot land data into this table without
    -- a migration noticing.
    source           text   NOT NULL
        CONSTRAINT ingest_unresolved_onc_endpoint_source
        CHECK (source = 'ONCHIGH'),
    -- The ONC list's own row identifier for the pair (e.g. a slug like
    -- 'warfarin-nsaid'), carried through so a curator can find the exact upstream
    -- entry a gap traces back to.
    entry_id         text   NOT NULL,
    -- Which side of the pair this row is about -- see the header on why this is
    -- in the key rather than folded away.
    endpoint_role    text   NOT NULL
        CONSTRAINT ingest_unresolved_onc_endpoint_role
        CHECK (endpoint_role IN ('subject', 'object')),
    -- The identifier scheme and value AS THE ONC LIST GIVES THEM (e.g. 'UNII',
    -- 'ZZZZZZZZZZ') -- the raw fact that failed to bridge, not a guess at what it
    -- should have been.
    identifier_scheme text  NOT NULL,
    identifier_value  text  NOT NULL,
    -- The upstream label for the endpoint, where the list provides one. Nullable:
    -- some ONC rows name only an identifier.
    endpoint_name     text,
    PRIMARY KEY (ingest_run, source, entry_id, endpoint_role)
);

COMMENT ON TABLE drugref.ingest_unresolved_onc_endpoint IS
    'ONC high-priority DDI entries whose subject or object endpoint drugref could '
    'not bridge to a moiety or class: one row per (entry, role), because the two '
    'sides of a pair can fail independently. Not an error and not a drop -- it is '
    'the worklist behind gap_unresolved_onc_endpoint. Symmetric in spirit with '
    'db/016''s ingest_unresolved_ci_object, one pipeline stage earlier.';

-- The gap view. Grouped on (source, entry_id, endpoint_role) -- dropping only
-- ingest_run, exactly as db/016 dropped it from gap_unresolved_ci_object -- so
-- one view row is one independently-resolvable fact and, per db/017's lesson,
-- the view's grain matches the grain any future gap_key built from it would use.
-- Grouping any coarser (e.g. by entry_id alone) would re-fold the two
-- independently-failing roles db/016's header warns against; grouping any finer
-- (e.g. including ingest_run) would let a re-ingest of the SAME unresolved
-- endpoint mint a second row that folds to the same question_uuid once a later
-- slice wires this into open_question -- the collision db/017 was filed to fix.
CREATE OR REPLACE VIEW drugref.gap_unresolved_onc_endpoint AS
SELECT u.source,
       u.entry_id,
       u.endpoint_role,
       max(u.identifier_scheme) AS identifier_scheme,
       max(u.identifier_value)  AS identifier_value,
       max(u.endpoint_name)     AS endpoint_name,
       max(r.upstream_release)  AS upstream_release
FROM   drugref.ingest_unresolved_onc_endpoint u
JOIN   drugref.ingest_run r ON r.ingest_run_id = u.ingest_run
GROUP  BY u.source, u.entry_id, u.endpoint_role;

COMMENT ON VIEW drugref.gap_unresolved_onc_endpoint IS
    'ONC high-priority pair endpoints drugref did not resolve, one row per '
    '(source, entry_id, endpoint_role) -- the grain a gap_key built from this view '
    'must also use (db/017''s lesson: a coarser grouping folds two '
    'independently-failing endpoints into one question, a finer one mints two '
    'questions for the same fact). ABSENCE OF A ROW IS NOT COVERAGE -- an endpoint '
    'no release ever failed to resolve appears nowhere here.';

-- Admit the fifteenth question kind. Guarded on the constraint's TEXT rather than
-- its name, so a replay against an already-widened database skips the drop/add
-- entirely instead of rescanning -- the idiom db/016, db/019, db/022, db/028 and
-- db/029 all reuse rather than re-deriving.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%unresolved_onc_endpoint%') THEN
        ALTER TABLE drugref.open_question
            DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question
            ADD CONSTRAINT open_question_gap_kind CHECK (gap_kind IN (
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root',
                'unresolved_ci_object', 'dead_by_expansion_policy',
                'condition_without_indication',
                -- Plan C
                'uncurated_additive_effect', 'uncurated_threshold',
                'ineffective_contribution', 'ungraded_contribution',
                -- Slice 3
                'unruled_composition_activity',
                -- Slice 5c.1: the two kinds a curated row answers, not a lookup
                'uncurated_condition_contradiction', 'uncurated_interaction_rule',
                -- Slice 5c.2: an ONC pair endpoint drugref could not resolve
                'unresolved_onc_endpoint'));
    END IF;
END $$;

-- db/028_composition_tree.sql
-- Slice 3: the composition tree -- which registered moieties a specific substance
-- (a salt, a hydrate) is composed of, and which of them the release marks active.
--
-- Spec: docs/superpowers/specs/2026-08-05-drugref-slice-3-composition-tree-design.md
--
-- SHAPE: composition EDGES over ONE registry. There is no second registry and no
-- second immortal identity. 4,425 of 7,377 composites are not drugref moieties at
-- all, and 3,195 GSRS salts ALREADY ARE moieties (admitted by the gate, immortal,
-- and undemotable) -- so the composite side is a KEY FROM THE SOURCE and only the
-- component side is a drugref identity. A row may be a moiety AND have components;
-- those are statements about different things.
--
-- REBUILDABLE PROJECTION, outside slice 1's append-only floor: a substance whose
-- composition upstream corrects must be able to change, exactly as class_membership
-- and class_contraindication can.

-- ============================================================================
-- 1. The source and writer trio (db/020's note, and db/025's)
-- ============================================================================
-- Three places pin an authority's spelling: ingest_run's source CHECK, the writer
-- CHECK, and ids._SOURCE_CANONICAL (Python, edited in Task 6). Missing the
-- ingest_run source CHECK stops everything, because every projection row below
-- carries an ingest_run.
--
-- NOTE the constraint names are `ingest_run_source` and `ingest_run_writer`, NOT
-- the `..._check` suffix Postgres auto-generates for unnamed CHECKs -- db/005 and
-- db/025 named them explicitly, and db/009 and db/020 both record the trap.
--
-- substance_class_source is DELIBERATELY NOT WIDENED: GSRS defines no classes, and
-- admitting a source to a CHECK it will never write to invites a future writer to
-- believe it may.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF', 'GSRS'));

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run'));

-- ============================================================================
-- 2. The relation vocabulary -- a TABLE, not a CHECK
-- ============================================================================
-- db/006's precedent, and the standing rule it produced: a vocabulary written down
-- twice is two things that can disagree. The column below is a FOREIGN KEY into
-- this table, so the values have exactly one home and an error message can quote it.
CREATE TABLE IF NOT EXISTS drugref.composition_relation (
    relation    text PRIMARY KEY,
    description text NOT NULL
);

INSERT INTO drugref.composition_relation (relation, description) VALUES
    ('SALT_SOLVATE',
     'The substance is a salt or solvate of the component. Normalised from GSRS''s '
     'mirror-encoded SALT/SOLVATE->PARENT and PARENT->SALT/SOLVATE relationships.'),
    ('SOLVATE_ANHYDROUS',
     'The substance is a solvate (typically a hydrate) of the component''s anhydrous '
     'form. Normalised from ANHYDROUS->SOLVATE and SOLVATE->ANHYDROUS. Every solvate '
     'in the 2026-02-26 release has exactly ONE anhydrous parent.')
ON CONFLICT (relation) DO NOTHING;

-- ============================================================================
-- 3. The projection
-- ============================================================================
CREATE TABLE IF NOT EXISTS drugref.substance_composition (
    -- The COMPOSITE. Deliberately TEXT and NOT a foreign key: 4,425 of 7,377
    -- composites are not drugref moieties, and this slice mints no identity for
    -- them. Adding an FK "for safety" deletes two-thirds of the table.
    substance_unii      text   NOT NULL,
    component_moiety    uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    relation            text   NOT NULL REFERENCES drugref.composition_relation(relation),
    -- NULL means UNRULED -- the release says nothing about which component is
    -- active -- and NOT inactive. NO DEFAULT, for the reason `allow` is not the
    -- same as absent in class_expansion_policy and `withdrawn` is not `allow`:
    -- 2,668 rows land here, and defaulting them to false silently retires a
    -- question nobody answered, while defaulting to true propagates through
    -- counterions.
    is_active_component boolean,
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (substance_unii, component_moiety, relation)
);

-- The read view's join column. The PK already serves lookups BY COMPOSITE.
CREATE INDEX IF NOT EXISTS substance_composition_by_component
    ON drugref.substance_composition (component_moiety)
    WHERE is_active_component;

COMMENT ON TABLE drugref.substance_composition IS
    'Which registered moieties a specific substance is composed of (slice 3, GSRS). '
    'A rebuildable projection keyed by ingest_run.source = ''GSRS''. The composite '
    'side is a UNII from the source and is NOT a drugref identity: 4,425 of 7,377 '
    'composites are not moieties. Measured on 2026-02-26: 8,671 rows (7,962 '
    'SALT_SOLVATE + 709 SOLVATE_ANHYDROUS) over 7,377 composites and 4,433 component '
    'moieties; 4,433 moieties (22.8% of the registry) gain at least one child, of '
    'which 4,092 (21.1%) do so through a SALT_SOLVATE edge.';

COMMENT ON COLUMN drugref.substance_composition.is_active_component IS
    'Whether the release marks this component as what makes the composite '
    'pharmacologically active. TRUE 5,011 / FALSE 992 / NULL 2,668 on 2026-02-26. '
    'NULL means UNRULED, never inactive -- only 6,696 of 14,090 salts declare an '
    'active moiety at all. Derived from GSRS''s ACTIVE MOIETY relationship, which is '
    'used ONLY here: as an EDGE it is the ion level and would assert that '
    'levomefolate magnesium is interchangeable with magnesium sulfate.';

-- ============================================================================
-- 4. The read path -- only the ACTIVE component propagates
-- ============================================================================
-- A contraindication or interaction asserted on moiety M reaches composite S only
-- where the release says M is what makes S active. Maleic acid's 124 salts stay
-- unlinked: expanding them would be alert-fatigue by construction, and the same
-- discredited inference the withheld chemical-class contraindications refuse.
--
-- `IS TRUE`, never `= true`: the predicate must never let a NULL be coerced into a
-- match by a later rewrite.
CREATE OR REPLACE VIEW drugref.moiety_active_in_composite AS
SELECT component_moiety AS moiety_uuid,
       substance_unii,
       relation
FROM drugref.substance_composition
WHERE is_active_component IS TRUE;

COMMENT ON VIEW drugref.moiety_active_in_composite IS
    'For a moiety, the specific substances it is the ACTIVE component of -- the only '
    'composition inference slice 3 licenses. Deliberately NOT wired into '
    'ddi_candidate_pair, a measured 3.6 ms hot path; that is its own round.';

-- ============================================================================
-- 5. Gap kind 12 -- the shortfall is published, not hidden
-- ============================================================================
-- The read path deliberately chooses FEWER rows for unruled composites, and for a
-- contraindication fewer rows is the harm direction. That trade is only defensible
-- because the shortfall is on a worklist -- the same posture as the 103 unresolved
-- CI objects: withheld, counted, and put in front of a curator.
--
-- GRAIN = the gap_key's grain (#41): one row per COMPOSITE, keyed on the composite.
-- bool_and is what makes "no ruling at all" different from "some ruling": a
-- composite with any TRUE or FALSE has been reviewed and leaves the queue.
CREATE OR REPLACE VIEW drugref.gap_unruled_composition_activity AS
SELECT substance_unii,
       count(*)::int AS component_count
FROM drugref.substance_composition
GROUP BY substance_unii
HAVING bool_and(is_active_component IS NULL);

COMMENT ON VIEW drugref.gap_unruled_composition_activity IS
    'Composites carrying components but NO activity ruling, so no contraindication '
    'on a component can reach them. 2,245 rows on 2026-02-26. Unlike '
    'gap_dead_by_expansion_policy this one is populated from day one.';

-- ============================================================================
-- 6. Widen open_question.gap_kind -- twelve in all
-- ============================================================================
-- Widened deliberately, in a migration, exactly as db/007 asks: an unconstrained
-- gap_kind would let a typo mint a whole parallel question namespace that nothing
-- ever reconciles. The guard reads the CURRENT definition rather than assuming
-- db/022's, so re-running is safe and a future kind extends this list rather than
-- replacing it.
--
-- Edited into db/028 rather than added as db/029: this branch is unmerged, so
-- db/028 is not yet an APPLIED migration anywhere outside it, and editing it in
-- place is the documented exception to "migrations are immutable once applied"
-- while the branch stands. Add db/029 for the next gap kind after merge.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%unruled_composition_activity%') THEN
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
                'unruled_composition_activity'));
    END IF;
END $$;

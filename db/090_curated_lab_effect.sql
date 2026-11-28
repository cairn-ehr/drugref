-- db/090 -- drug effects on laboratory values, part 2: the curator's rulings (#294).
--
-- WHAT THIS IS FOR. Label statements are MACHINE-READ and unconfirmed (db/091, db/092).
-- This table is where a human judgement enters: a curator confirms or refutes a reading,
-- corrects its role, adds what no label gives (biotin's interference, a usual time to
-- onset, a co-medication such as warfarin), or carves one drug out of a class
-- statement -- the owner's "rule exceptions and special rulings that cannot be derived"
-- (design spec 2026-11-27-drugref-lab-effects-design.md section 6.5).
--
-- A SIBLING OF curated_property_ruling (db/058), copied on purpose: the same AFTER floor
-- (AFTER because Postgres computes STORED generated columns after BEFORE triggers run;
-- db/058's floor-trigger comment has the measurement), the same deferred single-live
-- guard, the same live-key and by-question indexes, the same signature registration.
-- Both trigger functions are generic over the natural key, so this file adds no
-- PL/pgSQL.
--
-- THE NATURAL KEY has seven columns: subject (kind + uuid), analyte, direction, role,
-- co-medication (kind + uuid). Role and direction are IN the key because one drug can
-- hold several true facts about one analyte (clozapine CAUSES low neutrophils AND has a
-- MONITORING protocol for them). Which machine readings a ruling answers is db/092's
-- rule 1 (spec 6.5 as amended by sections 12 and 13): the same drug, analyte,
-- direction and co-medication, and a `holds` in any role -- so a curator can correct a
-- misread role without first refuting the machine's guess -- or a does_not_hold /
-- cannot_establish in the reading's own role.
--
-- WHY GENERATED COLUMNS: forbid_multiple_live_assertions raises on a NULL key column
-- (NULL = NULL is never true, so the guard would guard nothing). The optional
-- co-medication is therefore coalesced into co_medication_kind ('none' / 'moiety' /
-- 'class') and co_medication_uuid (the nil UUID for 'none').

-- ============================================================================
-- 1. curated_lab_effect
-- ============================================================================
CREATE TABLE IF NOT EXISTS drugref.curated_lab_effect (
    curated_lab_effect_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_class_uuid  uuid REFERENCES drugref.substance_class(class_uuid),
    subject_moiety_uuid uuid REFERENCES drugref.substance_moiety(moiety_uuid),
    analyte_code text NOT NULL REFERENCES drugref.lab_analyte(analyte_code),
    direction    text NOT NULL,
    -- Required: a curator always states the role (a machine reading may say not_read;
    -- a ruling may not).
    role         text NOT NULL,
    co_medication_moiety_uuid uuid REFERENCES drugref.substance_moiety(moiety_uuid),
    co_medication_class_uuid  uuid REFERENCES drugref.substance_class(class_uuid),
    subject_kind text GENERATED ALWAYS AS (
        CASE WHEN subject_class_uuid IS NOT NULL THEN 'class' ELSE 'moiety' END) STORED,
    subject_uuid uuid GENERATED ALWAYS AS (
        COALESCE(subject_class_uuid, subject_moiety_uuid)) STORED,
    co_medication_kind text GENERATED ALWAYS AS (
        CASE WHEN co_medication_moiety_uuid IS NOT NULL THEN 'moiety'
             WHEN co_medication_class_uuid  IS NOT NULL THEN 'class'
             ELSE 'none' END) STORED,
    co_medication_uuid uuid GENERATED ALWAYS AS (
        COALESCE(co_medication_moiety_uuid, co_medication_class_uuid,
                 '00000000-0000-0000-0000-000000000000'::uuid)) STORED,
    -- NO DEFAULT: a ruling is stated, never guessed. does_not_hold is the ONLY way
    -- drugref ever says "no effect" (spec section 8).
    verdict        text NOT NULL,
    -- The usual time from starting the drug to the effect, in days, where known.
    onset_min_days integer,
    onset_max_days integer,
    -- The assay method, for an interference ruling only. Free text: no open method
    -- vocabulary exists (source evaluation 4).
    method         text,
    -- NULL for a special ruling no worklist asked for.
    question_uuid  uuid REFERENCES drugref.open_question(question_uuid) ON DELETE CASCADE,
    source         text NOT NULL,
    reviewed_by    text NOT NULL,
    -- The citation, for every ruling (structured evidence references, db/045, need a
    -- question_uuid and so reach only question-backed rulings).
    reviewed_against text NOT NULL,
    reviewed_at    timestamptz NOT NULL DEFAULT now(),
    superseded_by  bigint REFERENCES drugref.curated_lab_effect(curated_lab_effect_id),
    CONSTRAINT curated_lab_effect_one_subject
        CHECK (num_nonnulls(subject_class_uuid, subject_moiety_uuid) = 1),
    CONSTRAINT curated_lab_effect_one_co_medication
        CHECK (num_nonnulls(co_medication_moiety_uuid, co_medication_class_uuid) <= 1),
    CONSTRAINT curated_lab_effect_direction
        CHECK (direction IN ('high', 'low', 'unspecified')),
    CONSTRAINT curated_lab_effect_role
        CHECK (role IN ('causes', 'more_dangerous', 'monitoring', 'interferes')),
    CONSTRAINT curated_lab_effect_verdict
        CHECK (verdict IN ('holds', 'does_not_hold', 'cannot_establish')),
    -- db/058's rule: a fact about a class either holds for it or it does not.
    CONSTRAINT curated_lab_effect_class_subject_verdict
        CHECK (subject_class_uuid IS NULL OR verdict IN ('holds', 'does_not_hold')),
    CONSTRAINT curated_lab_effect_method_needs_interference
        CHECK (method IS NULL OR role = 'interferes'),
    CONSTRAINT curated_lab_effect_onset
        CHECK ((onset_min_days IS NULL OR onset_min_days >= 0)
               AND (onset_max_days IS NULL OR onset_max_days >= 0)
               AND (onset_min_days IS NULL OR onset_max_days IS NULL
                    OR onset_min_days <= onset_max_days)),
    CONSTRAINT curated_lab_effect_source CHECK (source IN ('DRUGREF'))
);

DROP TRIGGER IF EXISTS curated_lab_effect_append_only ON drugref.curated_lab_effect;
CREATE TRIGGER curated_lab_effect_append_only
    AFTER UPDATE OR DELETE ON drugref.curated_lab_effect
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'curated_lab_effect_id', 'subject_kind', 'subject_uuid', 'analyte_code',
        'direction', 'role', 'co_medication_kind', 'co_medication_uuid');

-- DEFERRED: a correction is briefly two live rows (INSERT new, then UPDATE old).
DROP TRIGGER IF EXISTS curated_lab_effect_single_live ON drugref.curated_lab_effect;
CREATE CONSTRAINT TRIGGER curated_lab_effect_single_live
    AFTER INSERT OR UPDATE ON drugref.curated_lab_effect
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'subject_kind', 'subject_uuid', 'analyte_code', 'direction', 'role',
        'co_medication_kind', 'co_medication_uuid');

CREATE INDEX IF NOT EXISTS curated_lab_effect_live_key
    ON drugref.curated_lab_effect
       (subject_kind, subject_uuid, analyte_code, direction, role,
        co_medication_kind, co_medication_uuid)
    WHERE superseded_by IS NULL;

CREATE INDEX IF NOT EXISTS curated_lab_effect_by_question
    ON drugref.curated_lab_effect (question_uuid);

COMMENT ON TABLE drugref.curated_lab_effect IS
    'CURATED, APPEND-ONLY, SIGNED: drugref''s own ruling on one lab effect -- does '
    '(subject class or moiety, analyte, direction, role, optional co-medication) HOLD, '
    'NOT HOLD, or is it beyond what the sources can establish. Confirms, refutes or '
    'corrects machine-read label statements, and carries what no label gives (onset, '
    'method, co-medication, special rulings). does_not_hold is the only "no effect" '
    'drugref states. Sibling of curated_property_ruling (db/058); #294.';
COMMENT ON COLUMN drugref.curated_lab_effect.superseded_by IS
    'One-way, set once, always a LATER row on the SAME natural key. A superseded row is '
    'history and is never deleted.';

INSERT INTO drugref.signature_target_kind
    (target_kind, target_table, pk_column, payload_context)
VALUES
    ('curated_lab_effect', 'curated_lab_effect', 'curated_lab_effect_id',
     'curated_lab_effect/v1')
ON CONFLICT (target_kind) DO NOTHING;

-- ============================================================================
-- 2. lab_effect_class_member -- who a class lab-effect ruling reaches
-- ============================================================================
-- The class profiles' LEAF rule (db/059 class_profile_member: DIRECT members, MED-RT
-- PK classes excluded), then expanded down the class DAG by class_subtree, so a
-- ruling on a parent class reaches a drug tagged only to a subclass. Unlike the
-- profiles' own member count, which is direct-only (a subclass-only drug goes to
-- db/060's class_subclass_only_member instead).
CREATE OR REPLACE VIEW drugref.lab_effect_class_member AS
SELECT DISTINCT st.root_uuid AS class_uuid, m.moiety_uuid
FROM   drugref.class_subtree st
JOIN   drugref.class_profile_member m ON m.class_uuid = st.class_uuid;

COMMENT ON VIEW drugref.lab_effect_class_member IS
    'Every drug a class lab-effect ruling reaches: class_profile_member (direct members, '
    'MED-RT PK classes excluded) below the class in class_subtree, the class itself '
    'included. NOT class_rule_member (db/079), which is the interaction rules'' expansion.';

-- ============================================================================
-- 3. curated_lab_effect_reach -- what a class ruling reached when it was made (#258)
-- ============================================================================
-- Class membership is delete-and-rebuild per ingest, so lab_effect_class_member only
-- answers "who does it reach NOW". The decision audit needs "who did it reach WHEN THE
-- CURATOR RULED". One row per class ruling, written by
-- curation_lab_effect.snapshot_reach beside the ruling. members is an ARRAY so that a
-- class that reached no one is an empty array, never a missing row (the ambiguity
-- db/082 had to add a second table to resolve). A correction snapshots again under
-- its own id; the superseded ruling keeps its row. Moiety rulings have none.
CREATE TABLE IF NOT EXISTS drugref.curated_lab_effect_reach (
    curated_lab_effect_id bigint PRIMARY KEY
        REFERENCES drugref.curated_lab_effect (curated_lab_effect_id),
    -- Sorted, distinct moiety uuids. Not FK-checked element by element (an array
    -- cannot be); moiety_uuid is immortal by slice 1's floor.
    members     uuid[] NOT NULL,
    captured    text NOT NULL CHECK (captured IN ('at_ruling')),
    recorded_at timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS curated_lab_effect_reach_insert_only
    ON drugref.curated_lab_effect_reach;
CREATE TRIGGER curated_lab_effect_reach_insert_only
    BEFORE UPDATE OR DELETE ON drugref.curated_lab_effect_reach
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.curated_lab_effect_reach IS
    'The drugs a CLASS lab-effect ruling reached when it was written (members, sorted; '
    'empty = reached no one). Insert-only; a correction snapshots under its own id. '
    'Audit data no upstream file can rebuild, so it travels in the curated export (#258).';

-- ============================================================================
-- 4. current_release -- a new lab-effect ruling is drift too
-- ============================================================================
-- COPIED VERBATIM FROM db/081 (the only migration that defines this view), with one
-- more probe in the re-curated arm.
CREATE OR REPLACE VIEW drugref.current_release AS
SELECT m.release_tag, m.published_at, rb.git_commit, rb.ledger_head,
       array_remove(ARRAY[
           CASE WHEN rb.release_tag IS NULL
                THEN 'no build record (published before db/081)' END,
           CASE WHEN EXISTS (
                    SELECT 1 FROM drugref.ingest_run r
                    WHERE r.finished_at > rb.recorded_at
                      AND r.source IN (SELECT c.source FROM drugref.ingest_run c
                                       WHERE c.ingest_run_id = ANY (rb.run_ids)))
                THEN 're-ingested' END,
           CASE WHEN EXISTS (SELECT 1 FROM drugref.curated_interaction
                             WHERE reviewed_at > rb.recorded_at)
                  OR EXISTS (SELECT 1 FROM drugref.curated_condition
                             WHERE reviewed_at > rb.recorded_at)
                  OR EXISTS (SELECT 1 FROM drugref.curated_class_interaction
                             WHERE reviewed_at > rb.recorded_at)
                  OR EXISTS (SELECT 1 FROM drugref.curated_property_ruling
                             WHERE reviewed_at > rb.recorded_at)
                  OR EXISTS (SELECT 1 FROM drugref.curated_lab_effect
                             WHERE reviewed_at > rb.recorded_at)
                THEN 're-curated' END,
           CASE WHEN EXISTS (SELECT 1 FROM drugref.class_expansion_policy
                             WHERE reviewed_at > rb.recorded_at)
                THEN 'expansion policy changed' END,
           CASE WHEN rb.release_tag IS NOT NULL
                 AND (SELECT max(filename) FROM drugref.schema_migration)
                     IS DISTINCT FROM rb.ledger_head
                THEN 'newer migration' END
       ], NULL) AS drift_reasons
FROM (SELECT * FROM drugref.release_manifest ORDER BY recorded_at DESC LIMIT 1) m
LEFT JOIN drugref.release_build rb ON rb.release_tag = m.release_tag;

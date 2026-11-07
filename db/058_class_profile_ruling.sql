-- db/058_class_profile_ruling.sql -- class profiles, task 1: the curated ruling table.
--
-- A class profile says which properties a drug class shares and which belong to one
-- member alone (design spec 2026-11-07-drugref-class-profiles-design.md). Almost all of
-- it is DERIVED from tables that already exist. This is the ONE place a human judgement
-- enters: "for this class (or member), this property HOLDS / DOES NOT HOLD / cannot be
-- established from the sources". Spec section 5 says why that cannot be inferred.
--
-- A SIBLING OF curated_class_interaction (db/032), copied on purpose: same append-only
-- floor, same deferred single-live guard, same live-key and by-question indexes, same
-- signature registration (db/035 section 8). Both trigger functions are generic over the
-- natural key, so this file adds no PL/pgSQL.
--
-- WHY TWO / THREE NULLABLE FKs INSTEAD OF A POLYMORPHIC uuid: a ruling's subject is a
-- class OR a moiety, its object a condition OR a moiety OR a class, and a bare uuid
-- column cannot carry a foreign key. One nullable FK per kind keeps referential
-- integrity; exactly-one-set CHECKs keep it unambiguous.
--
-- WHY THE GENERATED COLUMNS: forbid_multiple_live_assertions raises on a NULL natural-key
-- column (NULL = NULL is never true, so the guard would silently guard nothing). The
-- natural key therefore needs NOT NULL columns, and `subject_uuid` / `object_uuid`
-- coalesce the nullable FKs into them. STORED, so the trigger's to_jsonb(NEW) sees them.
CREATE TABLE IF NOT EXISTS drugref.curated_property_ruling (
    curated_property_ruling_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_class_uuid  uuid REFERENCES drugref.substance_class(class_uuid),
    subject_moiety_uuid uuid REFERENCES drugref.substance_moiety(moiety_uuid),
    object_condition_uuid uuid REFERENCES drugref.condition(condition_uuid),
    object_moiety_uuid    uuid REFERENCES drugref.substance_moiety(moiety_uuid),
    object_class_uuid     uuid REFERENCES drugref.substance_class(class_uuid),
    subject_kind text GENERATED ALWAYS AS (
        CASE WHEN subject_class_uuid IS NOT NULL THEN 'class' ELSE 'moiety' END) STORED,
    subject_uuid uuid GENERATED ALWAYS AS (
        COALESCE(subject_class_uuid, subject_moiety_uuid)) STORED,
    object_kind  text GENERATED ALWAYS AS (
        CASE WHEN object_condition_uuid IS NOT NULL THEN 'condition'
             WHEN object_moiety_uuid    IS NOT NULL THEN 'moiety'
             ELSE 'class' END) STORED,
    object_uuid  uuid GENERATED ALWAYS AS (
        COALESCE(object_condition_uuid, object_moiety_uuid, object_class_uuid)) STORED,
    relationship text NOT NULL,
    -- NO DEFAULT: a ruling must be stated, never guessed. cannot_establish is a real
    -- answer ("looked; the sources do not settle it") distinct from nobody having looked.
    verdict      text NOT NULL,
    question_uuid uuid REFERENCES drugref.open_question(question_uuid) ON DELETE CASCADE,
    source       text NOT NULL,
    reviewed_by  text NOT NULL,
    reviewed_against text NOT NULL,
    reviewed_at  timestamptz NOT NULL DEFAULT now(),
    superseded_by bigint REFERENCES drugref.curated_property_ruling(curated_property_ruling_id),
    CONSTRAINT curated_property_ruling_one_subject
        CHECK (num_nonnulls(subject_class_uuid, subject_moiety_uuid) = 1),
    CONSTRAINT curated_property_ruling_one_object
        CHECK (num_nonnulls(object_condition_uuid, object_moiety_uuid, object_class_uuid) = 1),
    CONSTRAINT curated_property_ruling_relationship CHECK (relationship IN (
        'may_treat', 'may_prevent', 'may_diagnose', 'CI_with', 'induces', 'interacts_with')),
    -- A condition relationship needs a condition object; an interaction a drug or class.
    CONSTRAINT curated_property_ruling_relationship_matches_object CHECK (
        (relationship = 'interacts_with') = (object_condition_uuid IS NULL)),
    CONSTRAINT curated_property_ruling_verdict
        CHECK (verdict IN ('holds', 'does_not_hold', 'cannot_establish')),
    -- Class x class interactions already live in curated_class_interaction, and a class
    -- cannot "fail to establish" a fact about itself: it either holds for the class
    -- (a confirmed shared property) or it does not.
    CONSTRAINT curated_property_ruling_class_subject_scope CHECK (
        subject_class_uuid IS NULL
        OR (relationship <> 'interacts_with' AND verdict IN ('holds', 'does_not_hold'))),
    CONSTRAINT curated_property_ruling_source CHECK (source IN ('DRUGREF'))
);

-- The floor, REUSED: pk column first, then the natural key a correction must preserve.
-- AFTER, NOT BEFORE (unlike db/032), and measured: Postgres computes STORED generated
-- columns AFTER the BEFORE triggers run, so in a BEFORE trigger NEW.subject_kind etc. are
-- NULL while OLD's are populated, and forbid_overlay_rewrite's "only superseded_by may
-- change" comparison refuses the very supersession it exists to allow. An AFTER trigger
-- sees the finished row; a RAISE there still aborts the statement, so the floor is as hard.
DROP TRIGGER IF EXISTS curated_property_ruling_append_only ON drugref.curated_property_ruling;
CREATE TRIGGER curated_property_ruling_append_only
    AFTER UPDATE OR DELETE ON drugref.curated_property_ruling
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'curated_property_ruling_id', 'subject_kind', 'subject_uuid', 'relationship',
        'object_kind', 'object_uuid');

-- DEFERRED: a correction is briefly two live rows (INSERT new, then UPDATE old).
DROP TRIGGER IF EXISTS curated_property_ruling_single_live ON drugref.curated_property_ruling;
CREATE CONSTRAINT TRIGGER curated_property_ruling_single_live
    AFTER INSERT OR UPDATE ON drugref.curated_property_ruling
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'subject_kind', 'subject_uuid', 'relationship', 'object_kind', 'object_uuid');

-- PARTIAL and NOT UNIQUE, matching the trigger's predicate; nothing but the trigger reads
-- it (db/023 measured the quadratic cost of its absence).
CREATE INDEX IF NOT EXISTS curated_property_ruling_live_key
    ON drugref.curated_property_ruling
       (subject_kind, subject_uuid, relationship, object_kind, object_uuid)
    WHERE superseded_by IS NULL;

-- The referencing side of question_uuid's FK is never indexed by Postgres; the ingest
-- guard probes it and the ON DELETE CASCADE must find these rows.
CREATE INDEX IF NOT EXISTS curated_property_ruling_by_question
    ON drugref.curated_property_ruling (question_uuid);

COMMENT ON TABLE drugref.curated_property_ruling IS
    'CURATED, APPEND-ONLY: drugref''s own ruling on one class-profile property -- does '
    '(subject class or moiety, relationship, object condition/moiety/class) HOLD, NOT '
    'HOLD, or is it beyond what the sources can establish. The only human judgement the '
    'class profiles read; everything else is derived. Sibling of '
    'curated_class_interaction (db/032); see this file''s preamble for the nullable-FK '
    'and generated-column design.';
COMMENT ON COLUMN drugref.curated_property_ruling.superseded_by IS
    'One-way, set once, always a LATER row on the SAME natural key. A superseded row is '
    'history and is never deleted.';

INSERT INTO drugref.signature_target_kind
    (target_kind, target_table, pk_column, payload_context)
VALUES
    ('curated_property_ruling', 'curated_property_ruling',
     'curated_property_ruling_id', 'curated_property_ruling/v1')
ON CONFLICT (target_kind) DO NOTHING;

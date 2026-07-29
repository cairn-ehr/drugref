-- db/016_unresolved_ci_object_gap.sql
-- Publish the CI_ChemClass objects slice 5b did not ingest.
--
-- WHY THIS EXISTS. 405 assertions over 103 MeSH objects are real upstream safety
-- content that drugref does not ingest. Not ingesting it is the right call in both
-- of the cases below; doing so SILENTLY is not, so each object becomes a question --
-- exactly as Plan B made a pharmacist rule on 14 expansion roots.
--
-- THE TWO CASES ARE ANSWERED DIFFERENTLY, which is why db/014 gives the worklist an
-- object_kind and why this view carries it through:
--   * CHEMICAL_CLASS -- expanding it over MeSH's STRUCTURAL chemical tree makes a
--     rule on Sulfonamides reach bendroflumethiazide and bosentan, the discredited
--     sulfa cross-reactivity inference. drugref answers this ITSELF by recording a
--     decision, like unreviewed_expansion_root: the second such gap kind.
--   * UNREGISTERED_SUBSTANCE -- the object names a substance drugref's registry does
--     not carry. That is a COVERAGE question, answered by registering the moiety,
--     and it is emphatically NOT answered by ruling on tree expansion. One gap_kind
--     covers both because both are objects that did not make it in; questions.py
--     phrases each according to its own remedy.

-- BOTH object kinds are published here, not just the withheld class arm. An object
-- that names a substance drugref does not register is equally a real upstream rule
-- drugref did not ingest, and dropping it from this view would trade a wrong
-- question for a silent one. What object_kind buys is that questions.py can ask the
-- RIGHT question of each -- which it could not do while the two were indistinguishable.
CREATE OR REPLACE VIEW drugref.gap_unresolved_ci_object AS
SELECT u.object_code,
       max(u.object_name)       AS object_name,
       max(u.relationship)      AS relationship,
       max(u.object_kind)       AS object_kind,
       sum(u.assertion_count)   AS ci_rule_count,
       max(r.upstream_release)  AS upstream_release
FROM   drugref.ingest_unresolved_ci_object u
JOIN   drugref.ingest_run r ON r.ingest_run_id = u.ingest_run
GROUP  BY u.object_code;

COMMENT ON VIEW drugref.gap_unresolved_ci_object IS
    'Contraindication objects drugref did not ingest, with how many upstream rules '
    'ride on each and WHY each was not ingested (object_kind, which decides whether '
    'the curator is asked a structural-tree-expansion question or a '
    'register-this-moiety one). One row per object, because the decision is per '
    'object. ABSENCE OF A ROW IS NOT COVERAGE -- an object no release ever asserted '
    'appears nowhere here. object_kind is folded by max() alongside relationship: '
    'issue #41 tracks this view collapsing a composite key onto object_code alone.';

-- Admit the fifth question kind. Guarded on the constraint's TEXT rather than its
-- name, so a replay against an already-widened database skips the drop/add entirely
-- instead of rescanning -- the same idiom as db/010 and db/003.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%unresolved_ci_object%') THEN
        ALTER TABLE drugref.open_question
            DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question
            ADD CONSTRAINT open_question_gap_kind CHECK (gap_kind IN (
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root',
                'unresolved_ci_object'));
    END IF;
END $$;

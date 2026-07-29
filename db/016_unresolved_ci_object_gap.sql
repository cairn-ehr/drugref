-- db/016_unresolved_ci_object_gap.sql
-- Publish the contraindication objects slice 5b deliberately withheld.
--
-- WHY THIS EXISTS. CI_ChemClass's class arm (405 assertions over 108 MeSH chemical
-- classes) is real upstream safety content that drugref does not ingest, because
-- expanding it over MeSH's STRUCTURAL chemical tree makes a rule on Sulfonamides
-- reach bendroflumethiazide and bosentan -- the discredited sulfa cross-reactivity
-- inference. Withholding it is the right call; withholding it SILENTLY is not.
--
-- So it becomes a question, exactly as Plan B made a pharmacist rule on 14 expansion
-- roots. This is drugref's second gap kind that drugref can answer ITSELF, by
-- recording a decision, rather than by consulting an external source.

CREATE OR REPLACE VIEW drugref.gap_unresolved_ci_object AS
SELECT u.object_code,
       max(u.object_name)       AS object_name,
       max(u.relationship)      AS relationship,
       sum(u.assertion_count)   AS ci_rule_count,
       max(r.upstream_release)  AS upstream_release
FROM   drugref.ingest_unresolved_ci_object u
JOIN   drugref.ingest_run r ON r.ingest_run_id = u.ingest_run
GROUP  BY u.object_code;

COMMENT ON VIEW drugref.gap_unresolved_ci_object IS
    'Contraindication objects drugref did not ingest, with how many upstream rules '
    'ride on each. One row per object, because the decision is per object: "should a '
    'contraindication naming this class expand over MeSH''s structural tree?" '
    'ABSENCE OF A ROW IS NOT COVERAGE -- an object no release ever asserted appears '
    'nowhere here.';

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

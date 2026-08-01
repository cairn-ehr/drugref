-- db/023_accumulation_review_round.sql
-- The review round on Plan C (db/020-db/022). Four findings, each measured or probed
-- against a real schema rather than argued from the code:
--
--   1. The generic single-live trigger was QUADRATIC -- its predicate could not use an
--      index, so the deferred check at COMMIT was a sequential scan PER ROW.
--   2. gap_uncurated_threshold cleared on curation that reviewed nothing: it counted
--      effect_contribution ROWS, including rows that regrade nobody.
--   3. interaction_group_assertion had no retirement column, so a group could not be
--      withdrawn as a whole -- the same defect db/020 fixed on the other two tables.
--   4. interaction_group_member_moiety is deliberately NOT unique on (group, role,
--      moiety) and said so nowhere, while its sibling view promises uniqueness.
--
-- db/020-db/022 are applied and therefore immutable; this is the new file that carries
-- the corrections, which is the whole point of the ledger (db/013's route).

-- ============================================================================
-- 1. THE SINGLE-LIVE CHECK, MADE INDEXABLE
-- ============================================================================
-- MEASURED, on a schema built from these migrations, loading N promotions into one
-- transaction and timing the deferred check that SET CONSTRAINTS ALL IMMEDIATE forces:
--
--                  before      after
--     100 rows      17 ms       4 ms
--     400 rows     236 ms       8 ms
--    1000 rows   1,428 ms      21 ms
--    2000 rows   5,773 ms      42 ms
--
-- Before: 4x the time for 2x the rows -- quadratic. After: 2x for 2x -- linear, and
-- 137x faster at the 2,000-row mark. Same measurement, same machine, same script.
--
-- THE CAUSE. db/020 compared a jsonb projection of the natural key -- `to_jsonb(t) @> $1`
-- -- which is generic and readable and which NO INDEX CAN SERVE. `EXPLAIN` confirms a
-- Seq Scan. Since this is a FOR EACH ROW constraint trigger, a transaction inserting n
-- rows performs n full scans of a table that is n rows longer by the end.
--
-- db/020's comment says this is "the same shape as db/007's forbid_multiple_live_states".
-- The reasoning is the same; the SHAPE is not, in the one respect that costs. db/007
-- asks `WHERE question_uuid = NEW.question_uuid` -- an equality predicate a btree index
-- answers. Generalising over the natural key is what traded that away, and it did not
-- have to: the natural-key columns are already known, in TG_ARGV.
--
-- So the check is rebuilt as EQUALITY PREDICATES composed from those same arguments.
-- Still one function for all four tables; still no per-table copy to drift. The values
-- go through format's %L (quote_literal), so a curator-entered `role` cannot inject SQL.
--
-- WHY EQUALITY AND NOT `IS NOT DISTINCT FROM`, which would also handle a NULL key: that
-- operator is not index-usable either, so it would undo the entire fix. Every natural-key
-- column on all four tables is NOT NULL today, and a future nullable one now RAISES here
-- rather than silently counting zero live rows and passing a check it never performed.
CREATE OR REPLACE FUNCTION drugref.forbid_multiple_live_assertions() RETURNS trigger AS $$
DECLARE
    new_j jsonb  := to_jsonb(NEW);
    key_j jsonb  := '{}'::jsonb;
    preds text[] := '{}';
    col   text;
    live  int;
    i     int;
BEGIN
    FOR i IN 0 .. TG_NARGS - 1 LOOP
        col := TG_ARGV[i];
        -- SQL NULL (column absent) or JSON null (column present and NULL). Either way
        -- `t.col = NULL` is never true, so the count would come back 0 and the check
        -- would pass without having checked anything.
        IF (new_j -> col) IS NULL OR jsonb_typeof(new_j -> col) = 'null' THEN
            RAISE EXCEPTION
                'drugref.%: natural-key column % is NULL, but the single-live check '
                'compares it with = and no NULL satisfies that. Make the column NOT '
                'NULL, or this table cannot use this trigger.', TG_TABLE_NAME, col;
        END IF;
        key_j := key_j || jsonb_build_object(col, new_j -> col);
        preds := preds || format('t.%I = %L', col, new_j ->> col);
    END LOOP;

    EXECUTE format(
        'SELECT count(*) FROM drugref.%I t WHERE t.superseded_by IS NULL AND %s',
        TG_TABLE_NAME, array_to_string(preds, ' AND ')) INTO live;

    IF live > 1 THEN
        RAISE EXCEPTION
            'drugref.%: % live rows for natural key %; at most one row per key may '
            'have superseded_by IS NULL', TG_TABLE_NAME, live, key_j;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION drugref.forbid_multiple_live_assertions() IS
    'At most one LIVE row per natural key, checked at COMMIT. Deferred rather than a '
    'partial unique index (which spec 5.0 asks for) because a correction to these '
    'tables preserves the natural key, so both rows are briefly live and an immediate '
    'check would reject the only sequence that can express a correction. Same reasoning '
    'as db/007''s forbid_multiple_live_states, and since db/023 the same SHAPE too: an '
    'EQUALITY predicate per natural-key column, which the <table>_live_key partial '
    'indexes serve. db/020''s jsonb-containment version was unindexable and therefore '
    'quadratic in the size of a bulk curation load -- 5.8 s for 2,000 rows. Do not '
    'revert it to `to_jsonb(t) @> ...` for readability without re-measuring that.';

-- The indexes that make the rewrite pay. PARTIAL, matching the trigger's predicate
-- exactly, and NOT UNIQUE -- uniqueness over live rows is precisely what this design
-- cannot use (decisions/correcting-a-curated-assertion.md), because a correction leaves
-- two live rows for the instant between the INSERT and the UPDATE that supersedes.
--
-- Nothing but the trigger reads these, so they look unused to a catalog sweep. A test
-- asserts each one by name for that reason.
CREATE INDEX IF NOT EXISTS additive_effect_live_key
    ON drugref.additive_effect (effect_class_uuid)
    WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS effect_contribution_live_key
    ON drugref.effect_contribution (effect_class_uuid, contributor_class_uuid)
    WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS interaction_group_assertion_live_key
    ON drugref.interaction_group_assertion (group_uuid)
    WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS interaction_group_member_live_key
    ON drugref.interaction_group_member (group_uuid, role, class_uuid)
    WHERE superseded_by IS NULL;

-- ============================================================================
-- 2. RETIRING A GROUP -- the third table that needed a ruling column
-- ============================================================================
-- db/020 found that supersession alone can never WITHDRAW anything: a correction must
-- point at a later row carrying the SAME natural key, so every correction leaves another
-- live row standing. It gave additive_effect an `accumulates` boolean and
-- interaction_group_member a `satisfies_role` boolean for exactly that reason -- and
-- stopped one table short.
--
-- interaction_group_assertion has the same shape and therefore the same hole: a group
-- always keeps exactly one live assertion once it has any, so there was no way to say
-- "this group no longer applies". The only route was retiring every member one at a
-- time, which works (an empty required-role set never fires, see accumulation.group_fires)
-- but leaves a live assertion still claiming a severity for a group that cannot fire,
-- and takes one INSERT per member to express one decision.
--
-- NO DEFAULT, for the reason db/014 gives expands_descendants and db/020 gives the other
-- two: a ruling must be stated, never inherited. The tables ship empty, so this ADD
-- COLUMN is free now and would need a backfill decision later -- which is itself the
-- argument for doing it in this round rather than after curation lands.
ALTER TABLE drugref.interaction_group_assertion
    ADD COLUMN IF NOT EXISTS applies boolean NOT NULL;

COMMENT ON COLUMN drugref.interaction_group_assertion.applies IS
    'Whether drugref still asserts this group. RETIRING A GROUP IS AN INSERT OF FALSE '
    'that supersedes the true row -- the same move satisfies_role makes one table over, '
    'and the only one an append-only overlay whose supersession preserves the natural '
    'key can make. A CONSUMER MUST CHECK THIS: interaction_group_member_moiety '
    'deliberately does NOT filter on it (a group under curation has members before it '
    'has an assertion at all), so the roles of a retired group are still published. '
    'Reading the assertion is unavoidable anyway -- it is where the name and severity '
    'live, and nothing can be rendered without them.';

COMMENT ON TABLE drugref.interaction_group_assertion IS
    'CURATED, APPEND-ONLY: what drugref claims about an interaction group -- its name, '
    'severity, clinical note, and since db/023 whether it still `applies`. Separate from '
    'interaction_group so a correction never touches the identity that members and '
    'external citations point at. CANDIDATE TIER: a covered group is an input to review, '
    'not an auto-rendered warning.';

-- ============================================================================
-- 3. gap_uncurated_threshold -- count the UNREVIEWED, not the rows
-- ============================================================================
-- WHAT WAS WRONG. The view asks "would this effect fire on members nobody reviewed?"
-- and gated on `count(effect_contribution rows) < threshold_total`. That counts the
-- wrong population, and two probes against a real schema showed it clearing the gap
-- without reviewing anything:
--
--   * Grading two classes that hold NO MEMBER OF THE EFFECT cleared a (0,2) effect
--     whose four members were all still unreviewed. Both UUIDs are valid classes, so
--     the rows insert cleanly and regrade nobody -- gap_ineffective_contribution
--     reports them, but this gate had already gone quiet.
--   * Two explicit `minor` rows on classes reaching only 2 of 4 members cleared it
--     while the other 2 -- enough to trip (0,2) between them -- stayed unreviewed.
--
-- THE GATE IS NOW THE QUESTION ITSELF. With threshold_major = 0 the effect fires on any
-- `threshold_total` contributors regardless of grade, so it fires on unreviewed drugs
-- exactly when at least that many contributors are unreviewed. `ungraded_member_count
-- >= threshold_total` is that sentence, and it moves only when a promotion actually
-- reaches a member.
--
-- AN EXPLICIT `minor` STILL CLEARS THE MEMBERS IT REACHES, and must: spec 5.2's whole
-- distinction is that a curator LOOKED. What no longer counts is looking at a class
-- that holds none of the effect's drugs.
--
-- graded_contributor_count keeps its name and its place, but now counts only promotions
-- that BITE -- it is quoted back to a curator in the question text, so a no-op inflating
-- it reported review that never happened. ungraded_member_count is appended (CREATE OR
-- REPLACE VIEW admits new columns only at the end) and is the number the gate reads.
CREATE OR REPLACE VIEW drugref.gap_uncurated_threshold AS
WITH touched AS (
    -- Every (promotion, member) pair where the promotion REACHES a contributor of the
    -- effect. Joining through additive_effect_contributor rather than re-deriving the
    -- membership keeps this on the published contract -- one definition of "who
    -- contributes", not a second one that can drift from it.
    SELECT DISTINCT
           ac.effect_class_uuid,
           ec.effect_contribution_id,
           ac.moiety_uuid
    FROM   drugref.effect_contribution ec
    JOIN   drugref.class_subtree    s ON s.root_uuid  = ec.contributor_class_uuid
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
    JOIN   drugref.additive_effect_contributor ac
           ON  ac.effect_class_uuid = ec.effect_class_uuid
           AND ac.moiety_uuid       = m.moiety_uuid
    WHERE  ec.superseded_by IS NULL
),
reviewed AS (
    SELECT effect_class_uuid,
           count(DISTINCT effect_contribution_id) AS effective_promotion_count,
           count(DISTINCT moiety_uuid)            AS graded_member_count
    FROM   touched
    GROUP  BY effect_class_uuid
),
contributor_total AS (
    -- count(*) is the member count because the contract view is UNIQUE on
    -- (effect_class_uuid, moiety_uuid) -- spec 8, asserted by its own test.
    SELECT effect_class_uuid, count(*) AS member_count
    FROM   drugref.additive_effect_contributor
    GROUP  BY effect_class_uuid
)
SELECT ae.effect_class_uuid,
       sc.class_name,
       ae.threshold_major,
       ae.threshold_total,
       COALESCE(rv.effective_promotion_count, 0)                            AS graded_contributor_count,
       r.upstream_release,
       COALESCE(ct.member_count, 0) - COALESCE(rv.graded_member_count, 0)   AS ungraded_member_count
FROM   drugref.additive_effect ae
JOIN   drugref.substance_class sc ON sc.class_uuid   = ae.effect_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = ae.ingest_run
LEFT   JOIN reviewed          rv ON rv.effect_class_uuid = ae.effect_class_uuid
LEFT   JOIN contributor_total ct ON ct.effect_class_uuid = ae.effect_class_uuid
WHERE  ae.superseded_by IS NULL
AND    ae.accumulates
AND    ae.threshold_major = 0
AND    COALESCE(ct.member_count, 0) - COALESCE(rv.graded_member_count, 0)
       >= ae.threshold_total;

COMMENT ON VIEW drugref.gap_uncurated_threshold IS
    'Curated effects that would fire on UNREVIEWED members: threshold_major = 0 with at '
    'least threshold_total contributors that no live promotion reaches. Not an error -- '
    '(0, 2) is the right encoding for a fully curated effect -- which is exactly why it '
    'is surfaced rather than forbidden. SINCE db/023 THE GATE COUNTS UNREVIEWED MEMBERS, '
    'not effect_contribution rows: the row count let two promotions that regrade NOBODY '
    'clear the gap while every member the effect fires on stayed unreviewed. An explicit '
    '`minor` still clears the members it reaches, because that is a curator looking '
    '(spec 5.2); a promotion reaching none of them now clears nothing. Clears itself '
    'once curation catches up with the threshold. Prefer threshold_major >= 1 when '
    'curating a new effect.';
COMMENT ON COLUMN drugref.gap_uncurated_threshold.graded_contributor_count IS
    'Live promotions that actually REACH a contributor of this effect. Quoted back to a '
    'curator in the question text, so it counts only promotions that bite -- a no-op '
    'inflating it would report review that never happened.';
COMMENT ON COLUMN drugref.gap_uncurated_threshold.ungraded_member_count IS
    'Contributors of this effect that no live promotion reaches -- the drugs it would '
    'fire on with nobody having looked at them. THE NUMBER THE GATE READS: the row is '
    'reported while this is >= threshold_total, because with threshold_major = 0 that is '
    'precisely when the unreviewed population can trip the threshold by itself.';

-- ============================================================================
-- 4. The role view's non-uniqueness, said out loud
-- ============================================================================
-- Its sibling additive_effect_contributor PROMISES uniqueness on (effect, moiety), in a
-- COMMENT ON, because its consumer COUNTS and one drug emitted twice is the difference
-- between firing and not firing. This view made no such promise and could not keep one:
-- a moiety filed under a role through both a class and that class's descendant appears
-- once per route, because `via_class` -- the row a curator would have to correct -- is
-- part of what it publishes.
--
-- Silence there reads as the same guarantee left unstated. Saying it is what stops a
-- later reader either "fixing" the duplication away or joining through this view
-- expecting one row.
COMMENT ON VIEW drugref.interaction_group_member_moiety IS
    'Which moieties satisfy which ROLE in an interaction group -- live members only. A '
    'group fires when the regimen covers EVERY DISTINCT role here for that group; two '
    'drugs satisfying the SAME role do not cover a second one, which is the whole reason '
    'groups exist beside accumulation. A role with no live true member is simply ABSENT, '
    'so a consumer is never handed a role nothing can satisfy. Roles expand over DAG '
    'descendants (spec is silent; db/021 decides it) for curation economy and because '
    'missing a member is the harm direction for advisory data. '
    'DELIBERATELY NOT UNIQUE on (group_uuid, role, moiety_uuid), unlike '
    'additive_effect_contributor: a moiety reached through both a class and its '
    'descendant appears once per route, because via_class is part of what this publishes '
    'and is what a curator needs to correct a member. Safe because the consumer takes a '
    'SET of roles -- a duplicate changes nothing -- but do not join through this view '
    'expecting one row per drug. '
    'IT ALSO DOES NOT FILTER ON interaction_group_assertion.applies: a group under '
    'curation has members before it has an assertion. A retired group''s roles are still '
    'published here, and the consumer checks `applies` on the assertion it must read '
    'anyway for the name and severity. '
    'CANDIDATE TIER: covering every role is an input to review, not a warning.';

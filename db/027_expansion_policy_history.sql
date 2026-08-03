-- db/027 -- expansion-policy history (#35)
--
-- drugref.class_expansion_policy is the one curated table still edited in place, and
-- it GATES RECALL: a single `UPDATE ... SET decision = 'deny'` removes thousands of
-- candidate pairs with no audit row and nothing reporting it. db/010 reasoned its tier
-- out explicitly and was right AT THE TIME --
--
--   "NOT the append-only signed overlay either (that tier arrives with Plan C). This
--    is small, low-cardinality policy data in the same class as ci_axis and
--    source_tier: edited in place, reviewed by diff."
--
-- -- and Plan C has since landed. That parenthesis is now the argument FOR this
-- change. db/010 is applied and immutable, so the standing correction to its prose
-- lives in docs-site/docs/decisions/expansion-policy-is-append-only.md.
--
-- WHAT THIS MIGRATION DOES NOT DO: it does not touch ci_class_subtree. The deny-list
-- filters the class a rule NAMES, never the walk -- `Decreased Coagulation Activity`
-- is a descendant of a denied root and must still expand, which is how a rule reaches
-- warfarin, apixaban and aspirin (test_a_descendant_of_a_denied_root_still_expands).

-- ---- 1. the surrogate key -----------------------------------------------------
--
-- THE NATURAL KEY HAS TO STOP BEING UNIQUE, and this is the part most likely to be
-- "simplified" back. Correction-by-overlay means INSERTING the new judgement and THEN
-- pointing the old row at it, so both rows carry the same (source, source_code) --
-- a primary key on those columns rejects the only sequence that can express a
-- correction, and in-place mutation becomes the only possible implementation.
-- db/001 shipped exactly that defect on identity_claim and db/005 had to repair it;
-- db/020 records the same reasoning for additive_effect.
--
-- "At most one LIVE row per class" is not lost -- it moves to the deferred trigger in
-- section 3, which is the only shape that can express it (see
-- docs-site/docs/decisions/correcting-a-curated-assertion.md).
ALTER TABLE drugref.class_expansion_policy
    DROP CONSTRAINT IF EXISTS class_expansion_policy_pkey;

ALTER TABLE drugref.class_expansion_policy
    ADD COLUMN IF NOT EXISTS policy_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY;

-- One-way, set once, always a LATER row on the SAME class. The seeded rows acquire
-- NULL and are therefore live: nobody has revised or withdrawn anything.
ALTER TABLE drugref.class_expansion_policy
    ADD COLUMN IF NOT EXISTS superseded_by bigint
        REFERENCES drugref.class_expansion_policy(policy_id);

COMMENT ON COLUMN drugref.class_expansion_policy.policy_id IS
    'Surrogate key. The natural key (source, source_code) is deliberately NOT unique: '
    'a correction preserves it, so history rows share it. Do not "restore" a UNIQUE '
    'constraint there -- it would forbid every correction.';
COMMENT ON COLUMN drugref.class_expansion_policy.superseded_by IS
    'One-way, set once, always a LATER row on the SAME class. A superseded judgement '
    'is history and is never deleted: what drugref believed, and when, stays '
    'answerable -- which matters most for exactly the decisions that withheld pairs.';

-- ---- 2. the floor, REUSED rather than copied ----------------------------------
--
-- Both functions are db/020's, generic over the natural key (db/023 rewrote the
-- second one as equality predicates so an index can serve it). This table attaches to
-- them with no new PL/pgSQL, which is the point: one rule in five places is one rule
-- that will drift, and this project has spent four rounds proving it (#31, #40, #43,
-- db/018's two CTEs).
DROP TRIGGER IF EXISTS class_expansion_policy_append_only
    ON drugref.class_expansion_policy;
CREATE TRIGGER class_expansion_policy_append_only
    BEFORE UPDATE OR DELETE ON drugref.class_expansion_policy
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'policy_id', 'source', 'source_code');

-- DEFERRED, because a correction is momentarily TWO live rows -- between the INSERT
-- and the UPDATE that supersedes -- and an immediate check would reject the only
-- sequence that can express one.
DROP TRIGGER IF EXISTS class_expansion_policy_single_live
    ON drugref.class_expansion_policy;
CREATE CONSTRAINT TRIGGER class_expansion_policy_single_live
    AFTER INSERT OR UPDATE ON drugref.class_expansion_policy
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'source', 'source_code');

-- PARTIAL and NOT UNIQUE, matching the trigger's predicate exactly -- uniqueness over
-- live rows is precisely what this design cannot use. db/023 measured that without
-- this index the trigger is a sequential scan per row and therefore quadratic.
-- Nothing but the trigger reads it, so a test asserts it by name.
CREATE INDEX IF NOT EXISTS class_expansion_policy_live_key
    ON drugref.class_expansion_policy (source, source_code)
    WHERE superseded_by IS NULL;

-- db/024_ineffective_contribution_cost.sql
-- gap_ineffective_contribution, rebuilt to walk the class DAG a FIXED number of times
-- instead of once per curated row. Identical rows, 127x cheaper on the real release.
--
-- WHY THIS IS A SEPARATE FILE FROM db/023. db/023 is applied, and an applied migration
-- is immutable -- that is the ledger's whole point. This finding also arrived after it:
-- db/023's own review measured the trigger, and only measuring the GAP VIEWS on the real
-- release afterwards showed this one. Cheap to state as a new file, so it is one.

-- ============================================================================
-- THE MEASUREMENT, which is the entire argument
-- ============================================================================
-- On the 2026.07.06 release (4,202 classes / 22,754 class_subtree rows / 40,818
-- memberships), ten curated effects, N live promotions, view read to completion:
--
--     promotions      db/022      db/024     rows
--             25    2,544 ms       58 ms       17
--            100   12,100 ms      109 ms       78
--            400   59,117 ms      465 ms      311
--
-- Byte-identical row sets at every N (asserted in the probe, not assumed). ~150 ms PER
-- CURATED ROW became ~1 ms, and the curve went from "cost x rows" to linear.
--
-- AN EARLIER SYNTHETIC PROBE MISSED THIS ENTIRELY -- it reported 582 ms for 2,000
-- promotions and looked fine. Its fixture had 2,000 classes and NO EDGES, so
-- class_subtree was trivial and the repeated walk cost nothing. The real DAG is what
-- makes the walk expensive. Measure recursion against a real DAG or do not measure it.
--
-- WHY IT WAS SLOW. db/022 asked the question as a CORRELATED `NOT EXISTS` that named
-- class_subtree TWICE and referenced the outer row in both:
--
--     AND NOT EXISTS (SELECT 1 FROM class_subtree cs JOIN class_membership cm ...
--                     JOIN class_subtree es ON es.root_uuid = ec.effect_class_uuid ...
--                     WHERE cs.root_uuid = ec.contributor_class_uuid)
--
-- class_subtree is a view over a WITH RECURSIVE with no parameters, so it cannot be
-- pushed into: the planner re-runs the whole 22,754-row closure per outer row, twice.
-- The predicate is readable and states the spec-5.2 intersection exactly, which is
-- precisely why it survived review -- correctness reads fine, cost does not read at all.
--
-- THE FIX IS SHAPE, NOT CLEVERNESS. Compute the two membership sets ONCE for all live
-- promotions, intersect them, and ask which promotions came out empty. Same intersection,
-- same rows, two walks total instead of two per row.
--
-- WHAT THE REWRITE MUST NOT LOSE, and what a test pins: the verdict is per
-- (effect, contributor) PAIR. The same class is a sound promotion for an effect whose
-- drugs it shares and a no-op for one it does not, so `biting` is keyed on
-- effect_contribution_id -- the row -- and never on the contributor class. Deciding
-- "does this class bite?" once per class is the obvious way to make this cheaper and it
-- reports the wrong rows; it is also the same mistake the compound gap_key exists to
-- prevent one layer up.
CREATE OR REPLACE VIEW drugref.gap_ineffective_contribution AS
WITH live AS (
    SELECT effect_contribution_id, effect_class_uuid, contributor_class_uuid,
           magnitude, ingest_run
    FROM   drugref.effect_contribution
    WHERE  superseded_by IS NULL
),
contributor_member AS (
    -- every moiety at or below the PROMOTED class, per promotion
    SELECT l.effect_contribution_id, m.moiety_uuid
    FROM   live l
    JOIN   drugref.class_subtree    s ON s.root_uuid  = l.contributor_class_uuid
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
),
effect_member AS (
    -- every moiety at or below the EFFECT class, per promotion
    SELECT l.effect_contribution_id, m.moiety_uuid
    FROM   live l
    JOIN   drugref.class_subtree    s ON s.root_uuid  = l.effect_class_uuid
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
),
biting AS (
    -- spec 5.2's intersection, computed once for every promotion at the same time.
    -- Keyed on the PROMOTION, never on the contributor class -- see above.
    SELECT DISTINCT cm.effect_contribution_id
    FROM   contributor_member cm
    JOIN   effect_member em USING (effect_contribution_id, moiety_uuid)
)
SELECT l.effect_class_uuid,
       ef.class_name AS effect_class_name,
       l.contributor_class_uuid,
       co.class_name AS contributor_class_name,
       l.magnitude,
       r.upstream_release
FROM   live l
JOIN   drugref.substance_class ef ON ef.class_uuid   = l.effect_class_uuid
JOIN   drugref.substance_class co ON co.class_uuid   = l.contributor_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = l.ingest_run
WHERE  NOT EXISTS (SELECT 1 FROM biting b
                   WHERE  b.effect_contribution_id = l.effect_contribution_id);

COMMENT ON VIEW drugref.gap_ineffective_contribution IS
    'Live effect_contribution rows whose promoted class shares ZERO moieties with the '
    'effect''s contributor set -- a silent no-op. The schema cannot catch these (both '
    'UUIDs are valid classes), so they are published instead. Expect this view to fire '
    'after an upstream reshuffle moves a class out from under an effect: a curation '
    'that used to work and quietly stopped. ONE ROW PER (effect, contributor), matching '
    'its gap_key -- and the VERDICT is per pair too: one class can bite for one effect '
    'and be a no-op for another, so nothing here may be decided once per contributor '
    'class. SINCE db/024 the DAG walk is hoisted out of the row loop: db/022 asked this '
    'as a correlated NOT EXISTS naming class_subtree twice, which re-ran the whole '
    '22,754-row closure PER ROW -- 59 s for 400 promotions on the real release, against '
    '465 ms now for identical rows. Do not fold it back into a correlated subquery '
    'because it reads better; measure it against a real DAG first.';

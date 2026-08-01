-- db/022_accumulation_gap_views.sql
-- Plan C's REVIEW GATE: the four gap views that depend on curation existing, plus the
-- four gap kinds they register as open questions.
--
-- A GAP IS A QUERY, NEVER A REPORT (spec 7). These are views over ingested + curated
-- data, so they are always current, shrink visibly as curation lands, and make "how
-- much do we not know" a number that can be watched per release.
--
-- Each answers a question a curator would otherwise have to REMEMBER to ask, and each
-- corresponds to a way this model can be wrong without anything erroring:
--
--   gap_uncurated_additive_effect  an effect nobody has RULED on
--   gap_uncurated_threshold        an effect firing on DEFAULTS nobody reviewed
--   gap_ineffective_contribution   a promotion that is a silent NO-OP
--   gap_ungraded_contribution      a contributor class nobody has GRADED
--
-- THE STANDING GRAIN RULE (#41), restated because it applies to every new gap kind:
-- THE VIEW'S GRAIN MUST BE THE gap_key'S GRAIN. A view that groups more coarsely than
-- its key folds two gaps onto one immortal question_uuid that curator rows then attach
-- to; more finely, and it mints two questions for one gap. The two pair-keyed views
-- below therefore return exactly one row per (effect, contributor).

-- ============================================================================
-- 1. gap_uncurated_additive_effect -- a pending DECISION, not a coverage gap
-- ============================================================================
-- Returns EVERYTHING when additive_effect is empty, which is the correct initial
-- answer: on a fresh database every candidate effect is undecided.
--
-- THE FILTER IS DELIBERATELY CRUDE -- ">= 1 CI rule OR >= 10 subtree members" -- and
-- was chosen to make the first worklist finite and reviewable rather than to be
-- clinically precise. It is a view definition and so is cheap to retune once a curator
-- has actually seen its output. Without any filter every one of the release's 1,873 PE
-- classes becomes an externally-citable, immortal question.
--
-- SUBTREE members, not direct ones: a class whose drugs all sit one level down is
-- exactly as much of a pending decision as one holding them directly, and Plan B
-- measured that the direct-only reading hides most of the population.
--
-- PE ONLY. Accumulation is a claim about a physiologic EFFECT; an EPC or MoA class is
-- not a thing that adds up, and asking a curator whether it does is the same category
-- error db/014's object_kind was introduced to stop one slice over.
CREATE OR REPLACE VIEW drugref.gap_uncurated_additive_effect AS
WITH subtree_reach AS (
    SELECT s.root_uuid,
           count(DISTINCT m.moiety_uuid) AS subtree_member_count
    FROM   drugref.class_subtree s
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
    GROUP  BY s.root_uuid
),
ci_rules AS (
    SELECT object_class_uuid,
           count(*) AS ci_rule_count
    FROM   drugref.class_contraindication
    GROUP  BY object_class_uuid
)
SELECT sc.class_uuid,
       sc.class_name,
       sc.concept_type,
       COALESCE(cr.ci_rule_count, 0)        AS ci_rule_count,
       COALESCE(sr.subtree_member_count, 0) AS subtree_member_count,
       r.upstream_release
FROM   drugref.substance_class sc
JOIN   drugref.ingest_run r ON r.ingest_run_id = sc.first_seen_ingest
LEFT   JOIN ci_rules     cr ON cr.object_class_uuid = sc.class_uuid
LEFT   JOIN subtree_reach sr ON sr.root_uuid       = sc.class_uuid
WHERE  sc.concept_type = 'PE'
       -- A LIVE ROW OF EITHER RULING CLOSES THIS GAP. `accumulates = false` is an
       -- answer, and a worklist that went on asking after a curator said no would
       -- re-earn the same attention every release forever -- the nagging failure mode
       -- spec 7.2.1 diagnoses for questions and 5.2 for grades.
AND    NOT EXISTS (SELECT 1 FROM drugref.additive_effect ae
                   WHERE  ae.effect_class_uuid = sc.class_uuid
                   AND    ae.superseded_by IS NULL)
AND    (COALESCE(cr.ci_rule_count, 0) >= 1
        OR COALESCE(sr.subtree_member_count, 0) >= 10);

COMMENT ON VIEW drugref.gap_uncurated_additive_effect IS
    'PE classes nobody has ruled on: does this effect ACCUMULATE, and on what '
    'threshold? A pending DECISION rather than a coverage gap -- drugref can answer it '
    'itself, like gap_unreviewed_expansion_root, and unlike gap_unmatched_ingredient '
    'which needs a source. Returns everything while additive_effect is empty, which is '
    'the correct initial answer. The ">= 1 CI rule OR >= 10 subtree members" filter is '
    'a deliberately crude first cut, chosen to make the worklist finite and reviewable '
    'rather than to be clinically precise, and cheap to retune. A live additive_effect '
    'row of EITHER ruling closes the gap.';

-- ============================================================================
-- 2. gap_uncurated_threshold -- the effects firing on defaults alone
-- ============================================================================
-- Tension A made visible instead of prohibited. threshold_major = 0 is LEGAL and is
-- the correct encoding for a genuinely curated effect where every member counts -- but
-- combined with default-minor it means an effect fires on any N members of a subtree
-- most of which no curator has looked at. The schema cannot tell those two apart, so
-- this view surfaces the combination and lets a human do it.
CREATE OR REPLACE VIEW drugref.gap_uncurated_threshold AS
SELECT ae.effect_class_uuid,
       sc.class_name,
       ae.threshold_major,
       ae.threshold_total,
       count(ec.effect_contribution_id) AS graded_contributor_count,
       r.upstream_release
FROM   drugref.additive_effect ae
JOIN   drugref.substance_class sc ON sc.class_uuid   = ae.effect_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = ae.ingest_run
LEFT   JOIN drugref.effect_contribution ec
       ON  ec.effect_class_uuid = ae.effect_class_uuid
       AND ec.superseded_by IS NULL
WHERE  ae.superseded_by IS NULL
AND    ae.accumulates
AND    ae.threshold_major = 0
GROUP  BY ae.effect_class_uuid, sc.class_name, ae.threshold_major,
          ae.threshold_total, r.upstream_release
HAVING count(ec.effect_contribution_id) < ae.threshold_total;

COMMENT ON VIEW drugref.gap_uncurated_threshold IS
    'Curated effects that would fire on UNREVIEWED members: threshold_major = 0 with '
    'fewer graded contributors than threshold_total. Not an error -- (0, 2) is the '
    'right encoding for a fully curated effect -- which is exactly why it is surfaced '
    'rather than forbidden. Clears itself once the curation catches up with the '
    'threshold. Prefer threshold_major >= 1 when curating a new effect.';

-- ============================================================================
-- 3. gap_ineffective_contribution -- promotions that do nothing
-- ============================================================================
-- A curation mistake the schema CANNOT catch: both UUIDs are valid substance_class
-- references, so promoting a class that shares no member with the effect inserts
-- cleanly, changes nothing, and reports nothing. This is also the view most likely to
-- fire immediately after a MED-RT reshuffle moves a class out from under an effect --
-- i.e. a curation that USED to work and silently stopped.
CREATE OR REPLACE VIEW drugref.gap_ineffective_contribution AS
SELECT ec.effect_class_uuid,
       ef.class_name AS effect_class_name,
       ec.contributor_class_uuid,
       co.class_name AS contributor_class_name,
       ec.magnitude,
       r.upstream_release
FROM   drugref.effect_contribution ec
JOIN   drugref.substance_class ef ON ef.class_uuid   = ec.effect_class_uuid
JOIN   drugref.substance_class co ON co.class_uuid   = ec.contributor_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = ec.ingest_run
WHERE  ec.superseded_by IS NULL
       -- The intersection spec 5.2 defines: moieties below the contributor class that
       -- are ALSO below the effect class. Zero of them means the promotion regrades
       -- nobody.
AND    NOT EXISTS (
           SELECT 1
           FROM   drugref.class_subtree     cs
           JOIN   drugref.class_membership  cm ON cm.class_uuid = cs.class_uuid
           JOIN   drugref.class_subtree     es ON es.root_uuid  = ec.effect_class_uuid
           JOIN   drugref.class_membership  em ON em.class_uuid  = es.class_uuid
                                              AND em.moiety_uuid = cm.moiety_uuid
           WHERE  cs.root_uuid = ec.contributor_class_uuid);

COMMENT ON VIEW drugref.gap_ineffective_contribution IS
    'Live effect_contribution rows whose promoted class shares ZERO moieties with the '
    'effect''s contributor set -- a silent no-op. The schema cannot catch these (both '
    'UUIDs are valid classes), so they are published instead. Expect this view to fire '
    'after an upstream reshuffle moves a class out from under an effect: a curation '
    'that used to work and quietly stopped. ONE ROW PER (effect, contributor), '
    'matching its gap_key.';

-- ============================================================================
-- 4. gap_ungraded_contribution -- the review queue
-- ============================================================================
-- MEMBERS WITH NO effect_contribution ROW AT ALL -- emphatically NOT "members whose
-- grade is minor". An explicit `minor` row records that a curator LOOKED and it really
-- is minor, which is a different fact from "nobody has looked" even though both grade
-- to minor in additive_effect_contributor. Reading this queue the other way would
-- leave every reviewed-and-confirmed-minor class in it permanently, re-earning the
-- same curator attention forever. That absence is the ONLY observable difference
-- between the two states, which is why a test asserts it directly.
--
-- KEYED ON CLASS, not moiety, because the curation unit is a class: "grade class C for
-- effect E" is one decision covering every member. A moiety-grained queue would ask a
-- curator 109 times for something they answer once, contradicting the curate-once-
-- apply-widely economy that motivates keying effect_contribution on a class at all.
--
-- CLASSES THAT HOLD NO MEMBER ARE EXCLUDED: grading one would create exactly the
-- empty-intersection no-op that gap_ineffective_contribution exists to report, so the
-- queue must not ask for it.
CREATE OR REPLACE VIEW drugref.gap_ungraded_contribution AS
SELECT ae.effect_class_uuid,
       ef.class_name AS effect_class_name,
       s.class_uuid  AS contributor_class_uuid,
       co.class_name AS contributor_class_name,
       count(DISTINCT m.moiety_uuid) AS member_count,
       r.upstream_release
FROM   drugref.additive_effect ae
JOIN   drugref.substance_class ef ON ef.class_uuid   = ae.effect_class_uuid
JOIN   drugref.class_subtree    s ON s.root_uuid     = ae.effect_class_uuid
JOIN   drugref.substance_class co ON co.class_uuid   = s.class_uuid
JOIN   drugref.class_membership m ON m.class_uuid    = s.class_uuid
JOIN   drugref.ingest_run       r ON r.ingest_run_id = ae.ingest_run
WHERE  ae.superseded_by IS NULL
AND    ae.accumulates
AND    NOT EXISTS (SELECT 1 FROM drugref.effect_contribution ec
                   WHERE  ec.effect_class_uuid      = ae.effect_class_uuid
                   AND    ec.contributor_class_uuid = s.class_uuid
                   AND    ec.superseded_by IS NULL)
GROUP  BY ae.effect_class_uuid, ef.class_name, s.class_uuid, co.class_name,
          r.upstream_release;

COMMENT ON VIEW drugref.gap_ungraded_contribution IS
    'THE REVIEW QUEUE: classes at or below a curated effect that hold members and have '
    'NO effect_contribution row at all. NOT "classes graded minor" -- an explicit '
    'minor means REVIEWED and leaves the queue, and since it grades identically to an '
    'ungraded class, this absence is the only place the difference is observable. '
    'Keyed on CLASS because that is the curation unit: one grading decision covers '
    'every member, and a moiety-grained queue would ask a curator a hundred times for '
    'one answer. Classes holding no member are excluded -- grading one would create '
    'the very no-op gap_ineffective_contribution reports.';

-- ============================================================================
-- 5. FOUR more gap kinds -- eleven in all
-- ============================================================================
-- Widened deliberately, in a migration, exactly as db/007 asks: an unconstrained
-- gap_kind would let a typo mint a whole parallel question namespace that nothing ever
-- reconciles. The guard reads the CURRENT definition rather than assuming db/019's, so
-- re-running is safe and a future kind extends this list rather than replacing it.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%ungraded_contribution%') THEN
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
                'ineffective_contribution', 'ungraded_contribution'));
    END IF;
END $$;

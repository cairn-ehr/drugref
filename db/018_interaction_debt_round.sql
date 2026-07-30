-- db/018_interaction_debt_round.sql
-- drugref global tier: the interaction-model debt round (#39, #31, #45).
--
-- Three follow-ups filed by earlier slices, cleared before slice 5b.2 reuses the same
-- code paths. Each was measured against the real releases (UNII 26Feb2026, MED-RT
-- 2026.07.06, MeSH desc/supp 2026) before it was touched, and TWO of the three issue
-- texts turned out to be stale -- both noted below, because a number in an issue is a
-- claim about a release, not a fact about the code.
--
--   1. #39 -- ingest_unmatched_ingredient is rebuilt per SOURCE while TWO
--             orchestrators write it under 'MED-RT'. A `reason` discriminator gives
--             each writer its own bucket, and both documented caveats disappear.
--   2. #31 -- a contraindication on a DENIED expansion root with no direct member
--             yields no pair, and no gap view says so. Plus the same silence from a
--             second cause, found while measuring this one.
--   3. #45 -- condition_contraindication_expanded recomputes the whole condition walk
--             per query. A function that walks UP from the patient's condition answers
--             the same question in O(ancestors).

-- ============================================================================
-- 1. #39: WHO OWNS WHICH ROWS OF THE UNMATCHED-INGREDIENT WORKLIST
-- ============================================================================
--
-- db/008 made ingest_unmatched_ingredient a rebuildable projection cleared per
-- ingest_run.source. That was true while medrt_run was its only writer. Slice 5b
-- added mesh_ci_run, which opens its run under the SAME source ('MED-RT' states the
-- rule; MeSH only defines its object), and the two lists are built from different
-- upstream assertions:
--
--   * medrt_run    -- ingredients MED-RT CLASSIFIES that no moiety carries;
--   * mesh_ci_run  -- SUBJECTS of a contraindication that no moiety carries.
--
-- NEITHER SET CONTAINS THE OTHER. Measured on the real release through the real moiety
-- gate: 6,012 classified ingredients, 3,757 CI subjects, 2,271 classified that are not
-- CI subjects, and 16 CI subjects MED-RT never classifies -- three of which
-- (221083 sulfur colloidal, 5924 inulin, 89767 colloid sulfur) are outside the
-- registry and so are real losses, one CI_with rule each.
--
-- So slice 5b wrote its rows and deliberately did NOT clear, leaving two honest
-- caveats: a medrt_run destroyed the CI-only rows and could not re-add them
-- (ORDER-DEPENDENT), and consecutive mesh_ci_runs accumulated (nothing collected them).
-- One column removes both: each writer clears exactly what it re-derives.
ALTER TABLE drugref.ingest_unmatched_ingredient
    ADD COLUMN IF NOT EXISTS reason text;

-- Existing rows predate the discriminator and cannot be attributed by inspection --
-- both writers stored the same three columns. 'classification' is the right default
-- for exactly one reason: it is the bucket medrt_run rebuilds, so the value survives
-- only until the next ingest and then every row is written by a writer that declares
-- its own reason. A rebuildable projection is allowed to heal this way; the
-- append-only spine would not be.
UPDATE drugref.ingest_unmatched_ingredient SET reason = 'classification'
WHERE  reason IS NULL;

ALTER TABLE drugref.ingest_unmatched_ingredient
    ALTER COLUMN reason SET NOT NULL;

-- NOT NULL WITH NO DEFAULT, which is the point rather than an omission. db/012 found
-- that a NOT NULL column with a DEFAULT does not force a declaration, it supplies one
-- silently; db/014 then gave condition_ci_axis.expands_descendants no default for that
-- reason. The same argument is stronger here, because the value SCOPES A DELETE: a
-- default would let a third writer land its rows in an existing writer's bucket, which
-- is #39 restored with nothing to notice it.
ALTER TABLE drugref.ingest_unmatched_ingredient
    DROP CONSTRAINT IF EXISTS ingest_unmatched_ingredient_reason;
ALTER TABLE drugref.ingest_unmatched_ingredient
    ADD CONSTRAINT ingest_unmatched_ingredient_reason
    CHECK (reason IN ('classification', 'contraindication'));

-- The grain of the table is now (run, reason, rxcui): one RxCUI can be BOTH classified
-- and contraindicated without a moiety, and those are two writers' rows. Under the old
-- key the second writer's insert would have been swallowed by ON CONFLICT DO NOTHING
-- and its bucket would then be permanently short of a row it is supposed to own.
ALTER TABLE drugref.ingest_unmatched_ingredient
    DROP CONSTRAINT ingest_unmatched_ingredient_pkey;
ALTER TABLE drugref.ingest_unmatched_ingredient
    ADD CONSTRAINT ingest_unmatched_ingredient_pkey
    PRIMARY KEY (ingest_run, reason, rxcui);

COMMENT ON COLUMN drugref.ingest_unmatched_ingredient.reason IS
    'WHY this RxCUI is on the worklist, and -- because the per-source clear is scoped '
    'on it -- WHICH writer owns the row. `classification`: an ingredient the release '
    'classifies that no moiety carries (medrt_run). `contraindication`: the subject of '
    'a contraindication that no moiety carries (mesh_ci_run). Both orchestrators run '
    'under source MED-RT and neither set contains the other, so without this column '
    'whichever ran last deleted the other''s rows and could not re-add them (#39). '
    'NO DEFAULT, DELIBERATELY: a writer that does not declare its reason must fail, '
    'not inherit somebody else''s bucket. EXACTLY ONE WRITER PER (source, reason) is '
    'the invariant a new writer must preserve -- add a value here rather than sharing '
    'one, or the clears collide again.';

COMMENT ON TABLE drugref.ingest_unmatched_ingredient IS
    'RxCUIs an upstream release named that no moiety in the registry carries. A '
    'REBUILDABLE PROJECTION, replaced per (source, reason) -- see the `reason` column '
    'for why that pair and not source alone. Exists so gap_unmatched_ingredient can be '
    'a query: before it, only the COUNT of these survived an ingest and the identities '
    'were discarded.';

-- ============================================================================
-- 2. #31: THE CONTRAINDICATIONS THAT YIELD NOTHING, AND NOTHING SAID SO
-- ============================================================================
--
-- drugref's posture is that a coverage gap is a QUERY, never a silence. Two dead-rule
-- shapes were silent, and measuring the first is what turned up the second.

-- ---- 2a. a class whose only member is the rule's own SUBJECT ------------------
--
-- ddi_candidate_pair excludes the subject from its own partner list
-- (`m.moiety_uuid <> ci.subject_moiety_uuid`) -- a drug is not co-administered with
-- itself, and slice 5b's db/014 makes the same statement structurally for the MeSH
-- pair table. gap_unpopulated_contraindication's population test did not know that, so
-- a class whose ONLY member is the subject counted as populated and the rule vanished.
--
-- MEASURED, one case in the real release: acetohydroxamic acid carries a CI_MoA
-- against `Urease Inhibitors [MoA]`, and the only urease inhibitor drugref's gated
-- registry holds is acetohydroxamic acid. The rule yields no pair, and until this
-- re-issue nothing reported it.
--
-- THE FIX IS TO ASK THE READ PATH'S OWN QUESTION: not "is any drug filed below" but
-- "is any drug filed below that could BE a partner". Two views answering slightly
-- different questions about one row set is the two-lists-in-two-places failure db/006
-- exists to remove; here it had already cost a silent gap.
--
-- STILL NON-CORRELATED, which is why the subject test is expressed as a count plus one
-- member rather than the obvious `AND m.moiety_uuid <> ci.subject_moiety_uuid`. That
-- clause would correlate the subquery with the outer row and re-run the RECURSIVE
-- ci_class_subtree walk once per contraindication (635 of them). member_count > 1
-- settles every class with two or more members without looking at the subject at all;
-- only_member is read precisely when it does not.
--
-- (array_agg(DISTINCT ...))[1] rather than min(): Postgres has ordering operators for
-- uuid but no min/max AGGREGATE, and casting to text to borrow one would assert an
-- ordering this does not need. The element is read only where member_count = 1, and
-- there the array holds exactly one member -- so which element [1] is cannot matter.
CREATE OR REPLACE VIEW drugref.gap_unpopulated_contraindication AS
WITH populated AS (
    SELECT s.root_uuid,
           m.relationship                     AS membership_relationship,
           count(DISTINCT m.moiety_uuid)      AS member_count,
           (array_agg(DISTINCT m.moiety_uuid))[1] AS only_member
    FROM   drugref.ci_class_subtree s
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
    GROUP  BY s.root_uuid, m.relationship
)
SELECT ci.object_class_uuid          AS class_uuid,
       sc.class_name,
       sc.concept_type,
       count(*)                      AS ci_rule_count,
       max(r.upstream_release)       AS upstream_release
FROM   drugref.class_contraindication ci
       -- A predicate with no ci_axis row cannot be in the table at all (db/006's
       -- foreign key), so this join drops nothing it should have kept.
JOIN   drugref.ci_axis         a  ON a.relationship  = ci.relationship
JOIN   drugref.substance_class sc ON sc.class_uuid   = ci.object_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = ci.ingest_run
WHERE  NOT EXISTS (SELECT 1 FROM populated p
                   WHERE p.root_uuid               = ci.object_class_uuid
                   AND   p.membership_relationship = a.membership_relationship
                   AND   (p.member_count > 1
                          OR p.only_member <> ci.subject_moiety_uuid))
GROUP  BY ci.object_class_uuid, sc.class_name, sc.concept_type;

COMMENT ON VIEW drugref.gap_unpopulated_contraindication IS
    'Contraindications whose object class has no drug filed under it ON THE AXIS THE '
    'RULE EXPANDS OVER (ci_axis), anywhere in the class subtree -- upstream asserts '
    'the concern and never populates it. THE RULE''S OWN SUBJECT DOES NOT COUNT AS A '
    'MEMBER (db/018): ddi_candidate_pair excludes it, so a class whose only member is '
    'the subject yields no pair -- acetohydroxamic acid against Urease Inhibitors, the '
    'one such case in the 2026.07.06 release. ci_rule_count counts only the DEAD rules '
    'on that class and is the priority signal for this view; question_worklist does '
    'not order by it. TWO CAVEATS. (1) Population is tested over the whole SUBTREE, '
    'while a DENIED class expands over direct members only -- that combination is '
    'reported by gap_dead_by_expansion_policy instead, so one dead rule raises one '
    'question. (2) ABSENCE OF A ROW IS NOT COVERAGE: a hazard MED-RT never modelled at '
    'all appears nowhere here.';

-- ---- 2b. a rule killed by the deny-list ---------------------------------------
--
-- Plan B's residue. A contraindication whose object class is DENIED in
-- class_expansion_policy expands to DIRECT members only; if the class has none on the
-- rule's axis, the rule yields no pair at all -- and nothing surfaced it:
--
--   * gap_unpopulated_contraindication tests the whole SUBTREE, so it calls the class
--     populated and stays silent -- a drug IS filed below, just not reachably;
--   * gap_unreviewed_expansion_root is silent too: the class HAS been reviewed. The
--     deny is the point.
--
-- NOT A REGRESSION, A RESIDUE. These rules returned nothing before Plan B as well,
-- when the view expanded over direct membership only. Plan B closed the hole
-- everywhere except under a denied root, shrinking the affected set rather than
-- creating it.
--
-- MEASURED at db/017 against the real release: ONE class -- `Endocrine Activity
-- Alteration [PE]`, 1 rule, 0 direct has_PE members, 300 distinct drugs in its subtree, 0
-- pairs. #31 records TWO; `Cardiovascular Activity Alteration [PE]` was the other and
-- is no longer dead, because the #34 moiety-gate fix gave it 7 direct members. The
-- issue text predates that fix.
--
-- WHY THIS IS WORTH ASKING, and why it is a good question rather than noise: upstream
-- has vouched that the concern matters, and a curator has vouched that expansion is
-- not the answer. "This concern is stated, the class it names is too abstract to pair
-- on, and no drug is filed directly under it" is exactly the kind of thing a register
-- of open questions exists to hold. The available answers are to record `allow` (for
-- Endocrine Activity Alteration, 300 partners is fan-out, so probably not), to file a
-- drug directly under the class, or to accept the rule as unactionable -- and the
-- question records which.
CREATE OR REPLACE VIEW drugref.gap_dead_by_expansion_policy AS
WITH reachable AS (
    -- What the deny is holding back: distinct drugs anywhere below, per (root, axis).
    SELECT s.root_uuid,
           m.relationship                AS membership_relationship,
           count(DISTINCT m.moiety_uuid) AS member_count
    FROM   drugref.ci_class_subtree s
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
    GROUP  BY s.root_uuid, m.relationship
)
SELECT ci.object_class_uuid    AS class_uuid,
       sc.class_name,
       sc.concept_type,
       count(*)                AS ci_rule_count,
       -- Per (root, axis) while the row is per class. In the real release no class is
       -- named on two axes (a CI_MoA rule names a MoA class, CI_PE a PE class -- 389
       -- and 246 rules, zero overlap), so max() picks the only value there is; if a
       -- release ever crosses them it reports the largest cost among them, which is
       -- what a priority signal should do. This is an aggregate over a MEASURE, not
       -- over a key: #41's defect was folding a KEY component with max(), and grouping
       -- per class is precisely what keeps this view's grain equal to its gap_key's.
       max(x.member_count)     AS subtree_member_count,
       max(r.upstream_release) AS upstream_release
FROM   drugref.class_contraindication ci
JOIN   drugref.ci_axis         a  ON a.relationship  = ci.relationship
JOIN   drugref.substance_class sc ON sc.class_uuid   = ci.object_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = ci.ingest_run
       -- DENIED, not merely reviewed. `allow` and absent both expand (the view
       -- COALESCEs a missing policy row to 'allow'), so their rules are not dead --
       -- and an unreviewed sprawling root is gap_unreviewed_expansion_root's question.
JOIN   drugref.class_expansion_policy p
       ON p.source = sc.source AND p.source_code = sc.source_code
      AND p.decision = 'deny'
       -- The subtree DOES hold drugs on this axis. Without this the view would
       -- re-report the classes gap_unpopulated_contraindication already owns, minting
       -- a SECOND immortal question for one dead rule -- and one whose answer changes
       -- nothing, since allowing expansion over an empty subtree reaches nobody. Plan
       -- A tolerates two questions on one class only when they are independently
       -- answerable (unpopulated + unreviewed_expansion_root); these are not.
JOIN   reachable x ON x.root_uuid               = ci.object_class_uuid
                  AND x.membership_relationship = a.membership_relationship
       -- A predicate that cannot expand cannot be rescued by allowing expansion, so
       -- no available decision would retire the question. db/012's rule -- the review
       -- gate must only ask what an answer could change -- in a fourth place.
WHERE  a.expands_descendants
       -- ...and nothing is filed DIRECTLY on the class, which is all a denied rule
       -- can reach. Per axis, exactly as the population test above is: a direct member
       -- on has_MoA does not save a CI_PE rule.
AND    NOT EXISTS (SELECT 1 FROM drugref.class_membership m
                   WHERE  m.class_uuid   = ci.object_class_uuid
                   AND    m.relationship = a.membership_relationship)
GROUP  BY ci.object_class_uuid, sc.class_name, sc.concept_type;

COMMENT ON VIEW drugref.gap_dead_by_expansion_policy IS
    'Contraindications that yield NO pair because their object class is denied '
    'expansion (class_expansion_policy) and carries no member directly on the rule''s '
    'axis -- while its subtree does hold drugs, which is what makes the question '
    'answerable. Upstream vouches the concern matters and a curator vouched that '
    'expansion is not the answer, so the rule reaches nobody and, before db/018, '
    'silently. subtree_member_count is what the deny holds back and is the priority '
    'signal: 300 for Endocrine Activity Alteration, the ONE case in the 2026.07.06 '
    'release (#31 lists two; the other gained direct members in the #34 gate fix). '
    'DISJOINT FROM gap_unpopulated_contraindication BY CONSTRUCTION -- that view owns '
    'the empty-subtree case -- so one dead rule raises one question. NOT A REGRESSION '
    'OF PLAN B: these rules returned nothing before descendant expansion too. ABSENCE '
    'OF A ROW IS NOT COVERAGE, and one shape is deliberately not reported: a predicate '
    'with expands_descendants false and no direct member is equally dead, but allowing '
    'expansion could not revive it, so it is a ci_axis question rather than a policy '
    'one. No MED-RT predicate is non-expanding today; when one lands it needs its own '
    'view rather than a widened one, because the remedy differs -- tracked as #48.';

-- Admit the sixth question kind. Guarded on the constraint's TEXT rather than its
-- name, so a replay against an already-widened database skips the drop/add entirely
-- instead of rescanning -- the same idiom as db/016, db/010 and db/003.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%dead_by_expansion_policy%') THEN
        ALTER TABLE drugref.open_question
            DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question
            ADD CONSTRAINT open_question_gap_kind CHECK (gap_kind IN (
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root',
                'unresolved_ci_object', 'dead_by_expansion_policy'));
    END IF;
END $$;

-- ============================================================================
-- 3. #45: THE SAME ANSWER, REACHED FROM THE PATIENT'S END
-- ============================================================================
--
-- condition_subtree's anchor is EVERY condition named by a contraindication, and
-- Postgres cannot push an outer predicate into a recursive CTE. So
--
--     SELECT * FROM condition_contraindication_expanded WHERE member_condition = $1
--
-- walks the DAG from all 641 roots and materialises the whole subtree before filtering
-- to the handful of rows the caller wanted: the cost is a function of the graph, not
-- of the query.
--
-- MEASURED on the real release (5,203 conditions, 7,157 edges, 9,471 rules), the
-- Epilepsy lookup: 9-10 ms, of which the recursion is 11,512 rows built to return 15.
-- The function below: 0.7-0.9 ms, ~13x. Neither is slow TODAY -- the issue was filed
-- slice 5b.2 (~18k more assertions) reuses this DAG and the registry grows with every
-- MeSH release, and #45 says in terms not to change this without measuring first.
--
-- WHY A FUNCTION AND NOT A MATERIALIZED VIEW, the other option #45 lists: a
-- materialised subtree needs a REFRESH at the end of every orchestrator that touches
-- condition_parent or the rules, which is new coupling in each writer and a new way
-- for a read to be silently stale. This walks UP from the patient's condition instead
-- -- O(ancestors) rather than O(graph), correct by construction at any moment, and
-- needing no maintenance. It also needs no new index: condition_parent's PRIMARY KEY
-- already leads on child_condition_uuid, which is the direction this joins.
--
-- THE VIEW STAYS, and is still the right thing for whole-set access, for `WHERE
-- is_direct`, and for anything not keyed on one patient condition. Two read paths over
-- one expansion rule is a real risk -- it is the two-lists-in-two-places footgun in
-- another costume -- so test_condition_pairs asserts the two agree row for row rather
-- than asserting what each should return.
CREATE OR REPLACE FUNCTION drugref.contraindications_for_condition(
    patient_condition uuid)
RETURNS TABLE (subject_moiety   uuid,
               object_condition uuid,
               member_condition uuid,
               is_direct        boolean,
               relationship     text,
               source           text)
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    WITH RECURSIVE ancestor(condition_uuid) AS (
        SELECT patient_condition
      UNION
        SELECT cp.parent_condition_uuid
        FROM   ancestor an
        JOIN   drugref.condition_parent cp
               ON cp.child_condition_uuid = an.condition_uuid
    )
    SELECT ci.subject_moiety_uuid,
           ci.object_condition_uuid,
           -- Always the condition asked about: this walk climbs from it, so every row
           -- it returns is a rule that reaches THAT condition. It is returned anyway
           -- so the shape matches the view's column for column.
           patient_condition,
           ci.object_condition_uuid = patient_condition,
           ci.relationship,
           ci.source
    FROM   drugref.moiety_condition_contraindication ci
    JOIN   drugref.condition_ci_axis a ON a.relationship = ci.relationship
    JOIN   ancestor an ON an.condition_uuid = ci.object_condition_uuid
    -- The same gate the view applies, and it must stay the same: when a predicate does
    -- not expand, only a rule naming the patient's OWN condition fires.
    WHERE  a.expands_descendants
       OR  ci.object_condition_uuid = patient_condition;
$$;

COMMENT ON FUNCTION drugref.contraindications_for_condition(uuid) IS
    'Every contraindication that fires for a patient coded with this condition, found '
    'by walking UP the condition DAG from it -- O(ancestors) instead of the O(graph) '
    'walk condition_contraindication_expanded does before its filter can apply '
    '(measured on the 2026 release: 0.7-0.9 ms against 9-10 ms for the same answer). '
    'RETURNS EXACTLY `condition_contraindication_expanded WHERE member_condition = $1`, '
    'column for column, and a test pins that equality: it is a second implementation '
    'of one expansion rule, which is only safe while something fails when they '
    'disagree. Use the view for whole-set access or `WHERE is_direct`; use this for a '
    'patient lookup. DIRECTIONAL and CANDIDATE TIER, exactly as the view is -- rows '
    'feed review and must not auto-alert. UNION over the node, not the path, so it '
    'terminates under a cycle (db/013 forbids only self-parenting).';

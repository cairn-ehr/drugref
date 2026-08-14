-- db/035_class_grain_detectors.sql -- the class grain's DETECTORS.
--
-- Closes issues #90, #96, #97, #98 and #99, which are ONE DEFECT reported five times.
--
-- THE FINDING THE PR #95 REVIEW ENDED ON. db/032-db/034 gave the class x class grain
-- slice 5c.1's WRITE path -- a candidate tier (`class_pair_contraindication`), an
-- append-only overlay (`curated_class_interaction`) and a two-grain read view
-- (`curated_ddi_pair`) -- and NONE of the moiety grain's DETECTORS. The moiety grain
-- has a view for every way a rule can fail: ungraded (gap_uncurated_interaction_rule),
-- unpopulated (gap_unpopulated_contraindication), orphaned (curated_target_unresolved),
-- sprawling-and-unreviewed (gap_unreviewed_expansion_root), unsigned
-- (curated_signature_status + the release manifest). Individually each omission reads
-- as a reasonable follow-up. Together they mean a class-grain contraindication can be
-- INGESTED, GRADED, COMMITTED AND REPORTED SUCCESSFUL WHILE REACHING ZERO PATIENTS,
-- with `drugref status` printing health. That is why the five issues are one migration.
--
-- WHAT IS DELIBERATELY NOT HERE. Issue #92 (a mixed-kind [MoA] x [EPC] rule expands to
-- zero pairs, because one axis selects one membership relationship) keeps its own
-- issue: section 2 below is its SUBSTRATE -- such a rule now shows an
-- `object_effective_member_count` of 0 where before it showed nothing anywhere -- but
-- the real fix is schema-level (a rule that can name two axes), not a detector.
-- Issues #93 and #94 are clinical-content questions no view answers.

-- ============================================================================
-- 1. severity_kind -- the grading vocabulary AND its order, in one place
-- ============================================================================
-- WHY THIS TABLE EXISTS AT ALL, since a fifth CHECK would have been fewer lines:
-- issue #97 asks which of two disagreeing grades a consumer should take, and the only
-- answer that survives contact with a real client is one they can WRITE IN SQL.
-- `severity` is text, so `ORDER BY severity` sorts 'contraindicated' < 'major' <
-- 'minor' < 'moderate' -- putting `minor` ABOVE `moderate`, which is not merely
-- useless but inverted. An ordinal is needed, and an ordinal has to live somewhere.
--
-- db/006's finding 1, for the fifth time in this schema: the four levels were written
-- as five identical CHECK constraints (db/020 twice, db/029 twice, db/032 once), which
-- is five things to widen and four ways to widen them inconsistently -- a value one
-- table admits and the others refuse. A CASE expression in `curated_ddi_pair` would
-- have made it six. One table, five foreign keys into it, one home.
--
-- RANK 1 IS THE MOST SEVERE, so `ORDER BY severity_rank` is most-severe-first with no
-- DESC for a caller to forget. UNIQUE, so two levels cannot share a rank and make the
-- order non-deterministic exactly where determinism is the point.
CREATE TABLE IF NOT EXISTS drugref.severity_kind (
    severity      text     PRIMARY KEY,
    severity_rank smallint NOT NULL UNIQUE
);

INSERT INTO drugref.severity_kind (severity, severity_rank)
VALUES ('contraindicated', 1), ('major', 2), ('moderate', 3), ('minor', 4)
ON CONFLICT (severity) DO NOTHING;

COMMENT ON TABLE drugref.severity_kind IS
    'The four grades every curated drugref judgement uses, and THEIR CLINICAL ORDER. '
    'Seeded, not curated: a fifth level is a migration, deliberately, because '
    'severity_rank is what decides which of two disagreeing grades a consumer sees '
    '(curated_ddi_pair, db/035) and a level with no agreed rank would make that '
    'non-deterministic. RANK 1 IS THE MOST SEVERE -- `ORDER BY severity_rank` is '
    'most-severe-first with no DESC to forget. Replaced five identical CHECK '
    'constraints (db/020 x2, db/029 x2, db/032) on db/006''s precedent: a vocabulary '
    'written in five places is five things to widen and four ways to disagree.';
COMMENT ON COLUMN drugref.severity_kind.severity_rank IS
    'ASCENDING BY SEVERITY: 1 = contraindicated ... 4 = minor. The direction is the '
    'whole design -- it makes the safe read (`ORDER BY severity_rank LIMIT 1`) the '
    'one a caller writes by default, rather than the one they have to remember to '
    'reverse.';

-- ---- the five CHECKs become five foreign keys -------------------------------------
-- ADDITIVE AND NON-NARROWING: every value the CHECKs admitted is a row in the table
-- above, so no existing row can fail. NULL still passes (a foreign key does not
-- constrain NULL), which is required -- `applies = false` / `ruling = 'spurious'`
-- rows carry NULL severity by their tables' own completeness CHECKs, and those are
-- untouched.
--
-- DROP IF EXISTS, then ADD: re-running this file is a no-op, and a database that
-- somehow lacks the CHECK still gains the key.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['curated_interaction', 'curated_condition',
                             'curated_class_interaction', 'additive_effect',
                             'interaction_group_assertion']
    LOOP
        EXECUTE format(
            'ALTER TABLE drugref.%I DROP CONSTRAINT IF EXISTS %I', t, t || '_severity');
        EXECUTE format(
            'ALTER TABLE drugref.%I ADD CONSTRAINT %I FOREIGN KEY (severity) '
            'REFERENCES drugref.severity_kind(severity)', t, t || '_severity');
    END LOOP;
END $$;

-- ============================================================================
-- 2. class_pair_rule_reach -- the class grain states its OWN reach (#96, #99)
-- ============================================================================
-- `ci_rule_partner_reach` (db/018) one grain over, and the structural difference is
-- the point: the moiety grain's subject is already a single drug, so ONE side needed
-- counting. A class x class rule expands on BOTH sides (db/034's
-- `ci_class_pair_subtree` is seeded from both columns for exactly that reason), so its
-- reach is a PRODUCT and both factors have to be visible -- a rule reaching 4 x 0 and a
-- rule reaching 0 x 4 are both dead, and an operator fixes them in different places.
--
-- THE EFFECTIVE COUNTS APPLY THE EXPANSION POLICY, and the raw ones do not, because
-- the two answer different questions. db/018 reports subtree and direct separately so
-- `gap_unpopulated_contraindication` and `gap_dead_by_expansion_policy` can partition
-- the dead rules between them; the same two numbers are here for the same reason. The
-- `*_effective_member_count` columns then state what the rule reaches UNDER TODAY'S
-- POLICY, using db/034's own predicate verbatim (direct membership always pairs;
-- beyond that the axis must expand AND the class the rule names must not be denied) --
-- without them the worklist in section 3 would queue a rule whose root is DENIED and
-- which therefore reaches nobody, which is #36's measured mistake one grain over.
--
-- READS ci_class_pair_subtree, NOT ci_class_subtree. db/034 separated the two walks
-- after a merged one was measured to tax every moiety-grain query ~3.6x for class-grain
-- content most callers do not have; a detector re-merging them would reinstate exactly
-- that, and issue #100 is already open about a replay doing it by accident.
CREATE OR REPLACE VIEW drugref.class_pair_rule_reach AS
WITH subtree_member AS (
    -- Everything filed anywhere at or below a class the class grain names, per
    -- (root, axis). One aggregate for both sides: the subject side and the object
    -- side walk the SAME view, so counting them separately would pay for the
    -- recursive walk twice.
    SELECT s.root_uuid,
           m.relationship                AS membership_relationship,
           count(DISTINCT m.moiety_uuid) AS member_count
    FROM   drugref.ci_class_pair_subtree s
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
    GROUP  BY s.root_uuid, m.relationship
),
direct_member AS (
    -- ...and the subset filed ON the class itself, which is all a DENIED rule
    -- reaches. No recursion: a plain aggregate over class_membership.
    SELECT m.class_uuid,
           m.relationship                AS membership_relationship,
           count(DISTINCT m.moiety_uuid) AS member_count
    FROM   drugref.class_membership m
    GROUP  BY m.class_uuid, m.relationship
),
sided AS (
    SELECT cpc.subject_class_uuid,
           cpc.object_class_uuid,
           cpc.relationship,
           cpc.source,
           cpc.ingest_run,
           a.membership_relationship,
           a.expands_descendants,
           COALESCE(ss.member_count, 0) AS subject_subtree_member_count,
           COALESCE(sd.member_count, 0) AS subject_direct_member_count,
           COALESCE(os.member_count, 0) AS object_subtree_member_count,
           COALESCE(od.member_count, 0) AS object_direct_member_count,
           -- A denied class expands to its DIRECT members only; an unreviewed or
           -- allowed one expands over the subtree. COALESCE makes "no policy row"
           -- expand, matching ddi_candidate_pair and db/034 exactly -- unreviewed is
           -- the safe default and section 4 below is what reports it.
           (a.expands_descendants
            AND COALESCE(sp.decision, 'allow') <> 'deny') AS subject_expands,
           (a.expands_descendants
            AND COALESCE(op.decision, 'allow') <> 'deny') AS object_expands
    FROM   drugref.class_pair_contraindication cpc
    JOIN   drugref.ci_axis a ON a.relationship = cpc.relationship
    -- INNER: both columns are foreign keys into substance_class, so these drop
    -- nothing. They exist to reach (source, source_code), the key policy is stated on.
    JOIN   drugref.substance_class ssc ON ssc.class_uuid = cpc.subject_class_uuid
    JOIN   drugref.substance_class osc ON osc.class_uuid = cpc.object_class_uuid
    LEFT   JOIN drugref.class_expansion_policy_current sp
           ON sp.source = ssc.source AND sp.source_code = ssc.source_code
    LEFT   JOIN drugref.class_expansion_policy_current op
           ON op.source = osc.source AND op.source_code = osc.source_code
    LEFT   JOIN subtree_member ss
           ON ss.root_uuid = cpc.subject_class_uuid
          AND ss.membership_relationship = a.membership_relationship
    LEFT   JOIN direct_member sd
           ON sd.class_uuid = cpc.subject_class_uuid
          AND sd.membership_relationship = a.membership_relationship
    LEFT   JOIN subtree_member os
           ON os.root_uuid = cpc.object_class_uuid
          AND os.membership_relationship = a.membership_relationship
    LEFT   JOIN direct_member od
           ON od.class_uuid = cpc.object_class_uuid
          AND od.membership_relationship = a.membership_relationship
)
SELECT subject_class_uuid,
       object_class_uuid,
       relationship,
       source,
       ingest_run,
       membership_relationship,
       expands_descendants,
       subject_subtree_member_count,
       subject_direct_member_count,
       object_subtree_member_count,
       object_direct_member_count,
       CASE WHEN subject_expands THEN subject_subtree_member_count
            ELSE subject_direct_member_count END AS subject_effective_member_count,
       CASE WHEN object_expands THEN object_subtree_member_count
            ELSE object_direct_member_count END  AS object_effective_member_count,
       -- AN UPPER BOUND, AND NAMED AS ONE. The read path excludes a drug pairing with
       -- ITSELF (db/034's `sm.subject_moiety <> pm.partner_moiety`, db/014's rule at
       -- the pair grain), so a rule whose two classes share members reaches fewer
       -- pairs than this product. Counting exactly would mean cross-joining both
       -- membership sets per rule -- the expansion `curated_ddi_pair` already does for
       -- GRADED rules, paid here for UNGRADED ones, on a worklist. The bound is what
       -- the ranking needs and the exclusion cannot change 0 into non-zero, which is
       -- the only threshold anything downstream tests.
       (CASE WHEN subject_expands THEN subject_subtree_member_count
             ELSE subject_direct_member_count END)
       * (CASE WHEN object_expands THEN object_subtree_member_count
               ELSE object_direct_member_count END) AS max_pair_count
FROM   sided;

COMMENT ON VIEW drugref.class_pair_rule_reach IS
    'Per CLASS x CLASS rule: how many drugs each side could pair with, counted over '
    'the class subtree, over direct members only, and EFFECTIVELY (subtree or direct '
    'according to today''s class_expansion_policy, using db/034''s own predicate). '
    'ci_rule_partner_reach''s class-grain sibling -- and a PRODUCT rather than a '
    'single count, because a class x class rule expands on BOTH sides. THE ONE PLACE '
    'the class grain STATES A RULE''S REACH: gap_uncurated_class_interaction_rule is '
    'a filter over it, so the two agree by construction. `max_pair_count` is an UPPER '
    'BOUND -- the read path excludes a drug pairing with itself, which this product '
    'cannot see -- but it is exact about ZERO, which is the threshold that matters. '
    'A rule reading 0 on either side reaches nobody however it is graded: that is '
    'issue #92''s mixed-kind shape ([MoA] x [EPC], where one axis cannot select both '
    'memberships) made visible, and an unpopulated class besides. Walks '
    'ci_class_pair_subtree, NEVER ci_class_subtree -- db/034 separated them after a '
    'merged walk was measured to tax every moiety-grain query ~3.6x.';
COMMENT ON COLUMN drugref.class_pair_rule_reach.max_pair_count IS
    'subject_effective_member_count * object_effective_member_count -- an UPPER BOUND '
    'on the drug pairs this rule reaches, exact about zero. See the view comment.';

-- ============================================================================
-- 3. gap_uncurated_class_interaction_rule -- the grain's PRIMARY question (#96)
-- ============================================================================
-- THE FAILURE THIS CLOSES, in the issue's own words: `drugref ingest chain` reports
-- `class_rules_written=9`; the operator does not run `drugref curate onchigh` (a
-- DELIBERATELY separate command, so a routine chain re-run can never write to the one
-- tier where a mistake is permanent); nine ONC high-priority class rules sit
-- permanently ungraded; `question_worklist` shows nothing to do; and the moiety grain
-- would have raised nine questions in the same situation.
--
-- GROUPED ON THE THREE NATURAL-KEY COLUMNS, WITHOUT `source`, and that is load-bearing
-- rather than tidy. `class_pair_contraindication`'s primary key INCLUDES source (db/006's
-- reason: a second authority's row must not be swallowed by the first's), so one
-- clinical rule asserted by two authorities is TWO candidate rows -- while
-- `curated_class_interaction`'s key omits source, because drugref's judgement is about
-- the clinical fact and not about who asserted it. Ungrouped, one rule would raise TWO
-- questions on one gap_key, and `register_from_gaps` would insert them under ONE
-- immortal question_uuid, the second silently overwriting the first's text. The
-- aggregates are max() over values that do not vary with source (both counts depend
-- only on the two classes and the axis), so max() is an identity here -- the same
-- reasoning db/031's own view records, and the same reasoning that makes it SAFE,
-- not the #41 defect of folding a KEY component.
--
-- A RULE REACHING NO PAIR IS OMITTED, exactly as gap_uncurated_interaction_rule omits
-- one (its INNER JOIN to ddi_candidate_pair). Grading it would change nothing, and #36
-- measured what asking such a question costs a curator. It is not thereby hidden:
-- section 2's counts are what `drugref status` prints for the operator, whose problem
-- it is -- a rule reaching nobody is a data or schema fault (issue #92), not a
-- clinical question.
CREATE OR REPLACE VIEW drugref.gap_uncurated_class_interaction_rule AS
SELECT r.subject_class_uuid AS subject_class,
       r.object_class_uuid  AS object_class,
       r.relationship,
       ssc.class_name       AS subject_class_name,
       osc.class_name       AS object_class_name,
       max(r.subject_effective_member_count) AS subject_member_count,
       max(r.object_effective_member_count)  AS object_member_count,
       max(r.max_pair_count)                 AS max_pair_count,
       max(ir.upstream_release)              AS upstream_release
FROM   drugref.class_pair_rule_reach r
JOIN   drugref.substance_class ssc ON ssc.class_uuid   = r.subject_class_uuid
JOIN   drugref.substance_class osc ON osc.class_uuid   = r.object_class_uuid
JOIN   drugref.ingest_run      ir  ON ir.ingest_run_id = r.ingest_run
       -- LIVE ROW, not live ASSERTING row -- db/029 section 4's distinction, unchanged
       -- one grain over. Every ruling means a curator LOOKED, including `applies =
       -- false` ("reviewed, and this class x class rule is not a real interaction"),
       -- so every ruling retires the question. A retired ruling that stayed on the
       -- worklist would be asked about every release forever.
WHERE  NOT EXISTS (SELECT 1 FROM drugref.curated_class_interaction c
                    WHERE c.subject_class_uuid = r.subject_class_uuid
                      AND c.object_class_uuid  = r.object_class_uuid
                      AND c.relationship       = r.relationship
                      AND c.superseded_by IS NULL)
GROUP  BY r.subject_class_uuid, r.object_class_uuid, r.relationship,
          ssc.class_name, osc.class_name
HAVING max(r.max_pair_count) > 0;

COMMENT ON VIEW drugref.gap_uncurated_class_interaction_rule IS
    'CLASS x CLASS contraindication rules carrying no live drugref grade, ranked by '
    'max_pair_count -- the drug pairs at stake in the answer. The class grain''s '
    'PRIMARY question, and it had none until db/035: db/031 added a gap kind for the '
    'lesser one (an endpoint that resolved to nothing) while the grain''s own "these '
    'rules are ungraded" reached nobody, so nine ingested rules could sit permanently '
    'uncurated with question_worklist showing nothing to do. GROUPED WITHOUT `source` '
    'so one rule asserted by two authorities raises ONE question -- its gap_key is '
    'CLASS:{subject}/CLASS:{object}/AXIS:{relationship} and question_uuid is a pure '
    'function of it, so a per-source grain would mint one immortal question and '
    'overwrite its own text. A rule reaching NO pair is omitted (#36: a review gate '
    'must only ask what an answer could change) and is reported to the OPERATOR '
    'through class_pair_rule_reach instead, since a rule reaching nobody is a data '
    'fault rather than a clinical question.';

-- ============================================================================
-- 4. gap_unreviewed_expansion_root -- WIDENED, not duplicated (#99)
-- ============================================================================
-- db/034 gave the class grain its own subtree walk, which left it outside the review
-- gate that makes `COALESCE(policy, 'allow')` SAFE. The policy is consulted on every
-- class-grain read (db/034 sections 1-2 gate both sides on it) and never ASKED ABOUT:
-- a class-grain rule naming a sprawling abstract root expands over its entire subtree
-- by default, permanently, invisible to the gate that exists to catch precisely that.
-- Measured on a scratch copy during the PR #95 review: 0 rows here named a class-grain
-- root. Not a regression db/034 introduced -- db/033 did not cover it either.
--
-- WIDENED RATHER THAN COPIED, AND THAT IS THE DESIGN DECISION IN THIS SECTION. The
-- question is "may this class expand?"; the answer is ONE `class_expansion_policy` row
-- keyed on (source, source_code); and `question_uuid = uuid5(gap_kind, 'CLASS:' ||
-- class_uuid)` is IMMORTAL and externally cited. A second gap kind over the same class
-- would mint a SECOND permanent question that ONE policy decision answers, and a
-- curator answering it would retire one and not the other -- forever. So the arm is
-- added here, under the SAME gap_kind and the SAME gap_key, and not one existing
-- question_uuid moves. `ci_rule_count` now counts the expanding rules of EITHER grain
-- that ride on the decision, which is what "ride on the answer" always meant.
--
-- RE-ISSUED, NOT REWRITTEN: db/027 already re-issued this view once (correcting
-- db/012's "either decision retires the question", which `withdrawn` made false), and
-- its whole comment is restated below with only the grain sentences changed, on that
-- same precedent. db/012 and db/027 are applied and immutable.
CREATE OR REPLACE VIEW drugref.gap_unreviewed_expansion_root AS
WITH sized AS (
    -- BOTH WALKS, UNIONed and then counted. `UNION` (not UNION ALL) dedupes on
    -- (root, class), so a class named by both grains is counted ONCE -- the same
    -- property db/012 relied on for cycle safety, doing a second job here. Minus one
    -- for the root itself, which each walk contributes exactly once per root.
    SELECT root_uuid, count(*) - 1 AS descendant_class_count
    FROM   (SELECT root_uuid, class_uuid FROM drugref.ci_class_subtree
            UNION
            SELECT root_uuid, class_uuid FROM drugref.ci_class_pair_subtree) both_walks
    GROUP  BY root_uuid
),
naming_rule AS (
    -- One row per (class a rule NAMES, rule). The moiety grain names its object
    -- class; the class grain names BOTH of its classes, because db/034 expands both
    -- sides and a sprawling SUBJECT root fans out exactly as a sprawling object one
    -- does.
    SELECT ci.object_class_uuid AS class_uuid, ci.relationship, ci.ingest_run
    FROM   drugref.class_contraindication ci
  UNION ALL
    SELECT named.class_uuid, cpc.relationship, cpc.ingest_run
    FROM   drugref.class_pair_contraindication cpc
           -- DISTINCT INSIDE THE LATERAL, and it is not decoration: db/032 DECISION 2
           -- deliberately permits a class SELF-PAIR (QT-prolonging x QT-prolonging is
           -- a real ONC entry), and without the dedup one such rule would contribute
           -- TWO rows for one class and count as two rules riding on the decision.
    CROSS  JOIN LATERAL (
        SELECT DISTINCT u.class_uuid
        FROM   (VALUES (cpc.subject_class_uuid), (cpc.object_class_uuid))
                   AS u(class_uuid)
    ) named
)
SELECT sc.class_uuid,
       sc.class_name,
       sc.concept_type,
       z.descendant_class_count,
       count(*)                AS ci_rule_count,
       max(r.upstream_release) AS upstream_release
FROM   naming_rule n
       -- The axis join (db/012's re-issue): a predicate that does not expand cannot
       -- fan out, so no decision about its class matters.
JOIN   drugref.ci_axis         a  ON a.relationship  = n.relationship
JOIN   drugref.substance_class sc ON sc.class_uuid   = n.class_uuid
JOIN   sized                   z  ON z.root_uuid     = n.class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = n.ingest_run
WHERE  z.descendant_class_count > 20
AND    a.expands_descendants
       -- Either decision counts as reviewed; `withdrawn` (db/027) deliberately
       -- re-raises, because it means no current judgement rather than a permissive one.
AND    NOT EXISTS (SELECT 1 FROM drugref.class_expansion_policy_current p
                   WHERE  p.source      = sc.source
                   AND    p.source_code = sc.source_code)
GROUP  BY sc.class_uuid, sc.class_name, sc.concept_type, z.descendant_class_count;

COMMENT ON VIEW drugref.gap_unreviewed_expansion_root IS
    'Contraindicated classes with more than 20 descendant classes that nobody has '
    'ruled on in class_expansion_policy_current -- so they expand over their whole '
    'subtree by default, which for an abstract organ-system bucket is fan-out rather '
    'than recall. SINCE db/035 IT COVERS BOTH RULE GRAINS: the moiety grain''s object '
    'class (class_contraindication) and the class grain''s subject AND object '
    '(class_pair_contraindication, both sides, since db/034 expands both). WIDENED '
    'RATHER THAN COPIED ON PURPOSE -- one class, one policy row, one immortal '
    'question_uuid: a second gap kind over the same class would mint a second '
    'permanent question that one decision answers, and answering it would retire only '
    'one of them. ci_rule_count therefore counts the expanding rules of EITHER grain. '
    'SCOPED TO PREDICATES THAT ACTUALLY EXPAND (ci_axis.expands_descendants): a class '
    'named only by non-expanding rules is not asked about, because no decision could '
    'change a row. The threshold is a DISCOVERY HEURISTIC for the worklist, never the '
    'criterion for denying expansion: that judgement is qualitative and belongs in '
    'the policy table. A `deny` or `allow` retires the question; `withdrawn` (db/027) '
    'RE-RAISES it, because it means no current judgement rather than a permissive '
    'one. ABSENCE OF A ROW IS NOT A GUARANTEE OF SENSIBLE EXPANSION: a badly-shaped '
    'root with 20 descendants is invisible here.';

-- ============================================================================
-- 5. curated_target_unresolved -- the third arm (#90)
-- ============================================================================
-- A curated row names its candidate by NATURAL KEY and carries no foreign key into it,
-- because candidates are rebuildable projections and an FK would either block the
-- per-source rebuild or cascade curator judgement away with it. The cost is that a
-- rebuild CAN leave a judgement pointing at a candidate that no longer exists, and
-- nothing would say so. db/029 built this view to say so for two curated tables;
-- db/032 added a third and the view was not widened, so a live `curated_class_interaction`
-- whose candidate vanished was reported to NOBODY while the equivalent moiety row was.
--
-- ONE TRAILING COLUMN, NOT A RENAME. `subject_moiety` cannot carry a CLASS uuid under
-- a name that says moiety -- two disjoint namespaces in one field is what
-- UnresolvedTarget's own docstring already warns about -- and `CREATE OR REPLACE VIEW`
-- cannot rename or reorder anyway. `subject_class` is appended (db/030's precedent,
-- which appended `signature_status` to two read views for the same reason), so every
-- operator's existing SQL keeps working: `target_table` was always the discriminator
-- and still is. On the two moiety arms `subject_class` is NULL; on the class arm
-- `subject_moiety` is NULL. NOTE the NULL-comparison hazard db/032 records and #97
-- re-reports: `WHERE subject_class = x` silently drops the moiety arms, so filter on
-- `target_table`, never on a nullable discriminating column.
CREATE OR REPLACE VIEW drugref.curated_target_unresolved AS
SELECT 'curated_interaction'::text AS target_table,
       c.subject_moiety_uuid       AS subject_moiety,
       c.object_class_uuid         AS object_uuid,
       c.relationship,
       c.reviewed_by,
       c.reviewed_against,
       NULL::uuid                  AS subject_class
FROM   drugref.curated_interaction c
WHERE  c.superseded_by IS NULL
AND    NOT EXISTS (SELECT 1 FROM drugref.class_contraindication cc
                    WHERE cc.subject_moiety_uuid = c.subject_moiety_uuid
                      AND cc.object_class_uuid   = c.object_class_uuid
                      AND cc.relationship        = c.relationship)
UNION ALL
SELECT 'curated_condition',
       c.subject_moiety_uuid,
       c.object_condition_uuid,
       NULL,
       c.reviewed_by,
       c.reviewed_against,
       NULL::uuid
FROM   drugref.curated_condition c
WHERE  c.superseded_by IS NULL
AND    NOT EXISTS (SELECT 1 FROM drugref.moiety_condition_contraindication x
                    WHERE x.subject_moiety_uuid   = c.subject_moiety_uuid
                      AND x.object_condition_uuid = c.object_condition_uuid)
AND    NOT EXISTS (SELECT 1 FROM drugref.moiety_condition_indication x
                    WHERE x.subject_moiety_uuid   = c.subject_moiety_uuid
                      AND x.object_condition_uuid = c.object_condition_uuid)
UNION ALL
-- THE THIRD ARM (db/035). Same shape, same predicate, one grain over: the class
-- grain's candidate tier is `class_pair_contraindication` and its natural key is the
-- same three columns the overlay carries.
SELECT 'curated_class_interaction',
       NULL::uuid,
       c.object_class_uuid,
       c.relationship,
       c.reviewed_by,
       c.reviewed_against,
       c.subject_class_uuid
FROM   drugref.curated_class_interaction c
WHERE  c.superseded_by IS NULL
AND    NOT EXISTS (SELECT 1 FROM drugref.class_pair_contraindication x
                    WHERE x.subject_class_uuid = c.subject_class_uuid
                      AND x.object_class_uuid  = c.object_class_uuid
                      AND x.relationship       = c.relationship);

COMMENT ON VIEW drugref.curated_target_unresolved IS
    'Live curated rows whose candidate is no longer projected -- a judgement pointing '
    'at nothing after a rebuild. EXPECTED EMPTY. ALL THREE curated tables since '
    'db/035 (curated_interaction, curated_condition, curated_class_interaction): the '
    'class grain had the identical failure mode and none of the protection. The price '
    'of referencing candidates by natural key instead of by foreign key, which is what '
    'stops a per-source rebuild cascading curator judgement away. An OPERATOR signal, '
    'deliberately not a gap kind: it reports an upstream change, not a clinical '
    'question. `target_table` DISCRIMINATES the subject columns -- `subject_moiety` is '
    'NULL on the class-grain arm and `subject_class` is NULL on the other two, so '
    'filtering on either silently drops arms; filter on target_table.';

-- ============================================================================
-- 6. curated_ddi_pair -- severity_rank, and a STATED precedence (#97)
-- ============================================================================
-- THE DEFECT. This view is a UNION ALL of two grains, so a pair reachable by BOTH a
-- moiety rule and a class rule appears TWICE, with independently-curated severity.
-- Reproduced against real registry data in the PR #95 review: one pair, `moderate`
-- from the moiety grain and `contraindicated` from the class grain. Both single-live
-- guards are satisfied -- the rows live in different tables, each with exactly one
-- live row per its OWN natural key -- so nothing in the floor catches it. A client
-- doing `SELECT severity ... LIMIT 1` got an arbitrary answer, AND WHICHEVER IT TOOK
-- MIGHT BE THE LOWER ONE, which inverts the "fewer rows is the harm direction"
-- reasoning UNION ALL was chosen for in the first place.
--
-- THE PRECEDENCE, decided by the human partner and stated here because a rule nobody
-- can find is not a rule:
--
--     ORDER BY severity_rank, (rule_grain = 'moiety_rule') DESC
--
-- MOST SEVERE FIRST; on a tie, the MOIETY grain, because the rule naming an actual
-- drug carries better mechanism/management text than the rule naming its whole class.
-- Severity-first is the direction every other choice on this read path already takes:
-- a signature never gates a read, a missing expansion policy COALESCEs to 'allow', and
-- the view is a UNION ALL rather than a merge. Under-warning is the harm direction.
--
-- WHAT MAKES THAT SAFE RATHER THAN MERELY LOUD, and it is the half a severity rule
-- usually gets wrong: over-warning has a real cost -- click-through fatigue is a
-- leading reason prescribers stop reading alerts at all, and drugref exists partly to
-- stop flooding them with clinically irrelevant ones. Most-severe-wins is defensible
-- only because section 7 turns every disagreement into a FINITE reconciliation
-- worklist rather than permanent noise: a class rule over-warning where a curator
-- specifically graded one drug milder is a row somebody answers, once.
--
-- PRECEDENCE IS AN ORDER, NEVER A FILTER. Dropping the losing row would make the view
-- state less than it knows. Both rows still appear; `severity_rank` merely makes the
-- safe one reachable in one clause.
--
-- FIFTH CREATE OR REPLACE (db/029 defined it, db/030 appended signature_status, db/033
-- added the class grain, db/034 moved one line to ci_class_pair_subtree). EVERY COLUMN,
-- PREDICATE AND JOIN below is db/034's verbatim; the only additions are the LEFT JOIN
-- to severity_kind in each half and the trailing column it supplies.
CREATE OR REPLACE VIEW drugref.curated_ddi_pair AS
WITH class_dag AS (
    SELECT * FROM drugref.ci_class_pair_subtree
),
class_rule_subject_member AS (
    SELECT DISTINCT cci.curated_class_interaction_id,
           sub_m.moiety_uuid AS subject_moiety
    FROM   drugref.curated_class_interaction cci
    JOIN   drugref.ci_axis a ON a.relationship = cci.relationship
    JOIN   class_dag s ON s.root_uuid = cci.subject_class_uuid
    JOIN   drugref.class_membership sub_m
           ON sub_m.class_uuid   = s.class_uuid
          AND sub_m.relationship = a.membership_relationship
    JOIN   drugref.substance_class sc ON sc.class_uuid = cci.subject_class_uuid
    LEFT   JOIN drugref.class_expansion_policy_current p
           ON p.source = sc.source AND p.source_code = sc.source_code
    WHERE  cci.superseded_by IS NULL
    AND    cci.applies
    AND    (s.class_uuid = cci.subject_class_uuid
            OR (a.expands_descendants AND COALESCE(p.decision, 'allow') <> 'deny'))
),
class_rule_partner_member AS (
    SELECT DISTINCT ON (cci.curated_class_interaction_id, obj_m.moiety_uuid)
           cci.curated_class_interaction_id,
           obj_m.moiety_uuid AS partner_moiety,
           obj_m.class_uuid  AS member_class,
           (obj_m.class_uuid = cci.object_class_uuid) AS is_direct
    FROM   drugref.curated_class_interaction cci
    JOIN   drugref.ci_axis a ON a.relationship = cci.relationship
    JOIN   class_dag s ON s.root_uuid = cci.object_class_uuid
    JOIN   drugref.class_membership obj_m
           ON obj_m.class_uuid   = s.class_uuid
          AND obj_m.relationship = a.membership_relationship
    JOIN   drugref.substance_class sc ON sc.class_uuid = cci.object_class_uuid
    LEFT   JOIN drugref.class_expansion_policy_current p
           ON p.source = sc.source AND p.source_code = sc.source_code
    WHERE  cci.superseded_by IS NULL
    AND    cci.applies
    AND    (s.class_uuid = cci.object_class_uuid
            OR (a.expands_descendants AND COALESCE(p.decision, 'allow') <> 'deny'))
    ORDER  BY cci.curated_class_interaction_id, obj_m.moiety_uuid,
              (s.class_uuid = cci.object_class_uuid) DESC, s.class_uuid
)
-- ---- half 1: the moiety grain --------------------------------------------------
SELECT p.subject_moiety,
       p.partner_moiety,
       p.relationship,
       p.via_class,
       p.member_class,
       p.is_direct,
       c.severity,
       c.mechanism,
       c.management,
       c.evidence_grade,
       c.question_uuid,
       c.source           AS curated_source,
       c.reviewed_by,
       c.reviewed_against,
       c.reviewed_at,
       p.upstream_release,
       p.source           AS candidate_source,
       COALESCE(ss.signature_status, 'unsigned') AS signature_status,
       'moiety_rule'::text AS rule_grain,
       NULL::uuid          AS via_subject_class,
       sk.severity_rank
FROM   drugref.ddi_candidate_pair p
JOIN   drugref.curated_interaction c
       ON  c.subject_moiety_uuid = p.subject_moiety
       AND c.object_class_uuid   = p.via_class
       AND c.relationship        = p.relationship
LEFT   JOIN drugref.curated_signature_status ss
       ON  ss.target_kind = 'curated_interaction'
       AND ss.target_id   = c.curated_interaction_id
       -- LEFT, not INNER, and the four-row table makes that look pedantic. It is not:
       -- INNER here would let a severity this view cannot rank DELETE a row of
       -- clinical advice, and fewer rows is the harm direction. The foreign key in
       -- section 1 makes a miss unreachable; the LEFT makes it harmless if it ever
       -- became reachable again.
LEFT   JOIN drugref.severity_kind sk ON sk.severity = c.severity
WHERE  c.superseded_by IS NULL
AND    c.applies

UNION ALL

-- ---- half 2: the class grain ---------------------------------------------------
SELECT sm.subject_moiety,
       pm.partner_moiety,
       cci.relationship,
       cci.object_class_uuid AS via_class,
       pm.member_class,
       pm.is_direct,
       cci.severity,
       cci.mechanism,
       cci.management,
       cci.evidence_grade,
       cci.question_uuid,
       cci.source            AS curated_source,
       cci.reviewed_by,
       cci.reviewed_against,
       cci.reviewed_at,
       r.upstream_release,
       cpc.source            AS candidate_source,
       COALESCE(ss.signature_status, 'unsigned') AS signature_status,
       'class_rule'::text     AS rule_grain,
       cci.subject_class_uuid AS via_subject_class,
       sk.severity_rank
FROM   drugref.curated_class_interaction cci
JOIN   class_rule_subject_member  sm ON sm.curated_class_interaction_id = cci.curated_class_interaction_id
JOIN   class_rule_partner_member  pm ON pm.curated_class_interaction_id = cci.curated_class_interaction_id
JOIN   drugref.class_pair_contraindication cpc
       ON  cpc.subject_class_uuid = cci.subject_class_uuid
       AND cpc.object_class_uuid  = cci.object_class_uuid
       AND cpc.relationship       = cci.relationship
JOIN   drugref.ingest_run r ON r.ingest_run_id = cpc.ingest_run
       -- SINCE db/035 THIS JOIN CAN MATCH: `curated_class_interaction` is now a
       -- signature_target_kind (section 8), so a signed class-grain row reads its real
       -- status here instead of 'unsigned' unconditionally. db/033's comment saying
       -- otherwise described the state db/035 ends.
LEFT   JOIN drugref.curated_signature_status ss
       ON  ss.target_kind = 'curated_class_interaction'
       AND ss.target_id   = cci.curated_class_interaction_id
LEFT   JOIN drugref.severity_kind sk ON sk.severity = cci.severity
WHERE  cci.superseded_by IS NULL
AND    cci.applies
AND    sm.subject_moiety <> pm.partner_moiety;

COMMENT ON VIEW drugref.curated_ddi_pair IS
    'Drug pairs carrying a live drugref grade, from EITHER of two rule grains -- '
    '`rule_grain` says which (''moiety_rule'' | ''class_rule''), and '
    '`via_subject_class` names the class-grain rule''s subject (NULL for a moiety '
    'rule). ONE PAIR CAN APPEAR TWICE, once per grain, with different grades: both '
    'rulings are live, they sit in different tables, and each satisfies its own '
    'single-live guard, so no constraint can prevent it. THE PRECEDENCE IS '
    '`ORDER BY severity_rank, (rule_grain = ''moiety_rule'') DESC` -- MOST SEVERE '
    'FIRST, moiety grain breaking ties (the rule naming an actual drug carries better '
    'mechanism/management text than one naming its whole class). Severity-first '
    'because under-warning is the harm direction on this path, the same reason a '
    'signature never gates a read and a missing expansion policy expands. IT IS AN '
    'ORDER, NOT A FILTER: both rows still appear, because dropping one would make the '
    'view state less than it knows. A disagreement is not left standing -- '
    'curated_grain_disagreement (db/035) lists them for reconciliation, which is what '
    'keeps most-severe-wins from becoming permanent over-warning. '
    'A moiety-grain row is expanded from the class-level rule the grade was written '
    'against, so ONE curated row reaches every pair its rule expands to; a class-grain '
    'row is expanded on BOTH sides, so one row can reach thousands of pairs (SSRIs x '
    'MAOIs alone is ~2,263). INNER JOIN to the overlay throughout: an ungraded '
    'candidate does not appear here at all, because a NULL severity beside a real pair '
    'reads as "reviewed and harmless". ddi_candidate_pair remains the place to ask '
    'what a moiety-grain release said; class_pair_contraindication is the class '
    'grain''s own candidate tier. Each grain walks the class DAG through its OWN view '
    '(ci_class_subtree / ci_class_pair_subtree, db/034) -- SEPARATED, after a merged '
    'walk was measured to tax every moiety-grain query for class-grain content most '
    'callers do not have. signature_status is REGISTRY-LEVEL ONLY and its join is '
    'LEFT for both halves: an unsigned row still appears, labelled ''unsigned'', and a '
    'key revocation relabels a row rather than removing it.';

-- ============================================================================
-- 7. curated_grain_disagreement -- the reconciliation worklist (#97)
-- ============================================================================
-- db/032's own preamble argues that avoiding "two rows stating one fact to disagree"
-- is why the class grain exists at all, so leaving CROSS-grain disagreement
-- unreconciled is that same defect one tier up. Section 6 makes the READ
-- deterministic; only a detector makes the disagreement finite work.
--
-- THE GRAIN IS THE RULE PAIR, NOT THE DRUG PAIR, and this is the design decision here.
-- Two rules can overlap on thousands of drug pairs -- SSRIs x MAOIs alone is ~2,263 --
-- and a per-pair view would report ONE curator decision thousands of times. The
-- curator's answer is about the RULES (supersede one, or record why both stand), so
-- that is the grain, with `overlapping_pair_count` carrying the size.
--
-- AN OPERATOR VIEW, DELIBERATELY NOT A GAP KIND -- YET. It is a question drugref can
-- answer itself, which is Plan A's own criterion for a gap kind, and it is a
-- reasonable candidate for one. It is not made one today because a gap_key is FROZEN
-- FOREVER (question_uuid = uuid5(gap_kind, gap_key), immortal and externally cited),
-- this project has now broken a frozen key twice and caught it in review both times,
-- and ZERO class-grain rows currently ship -- so the right grain for the key would be
-- chosen against no real instance of the problem. The detector lands now; promotion
-- waits for content -- filed as issue #105 so the choice is a decision rather than an
-- omission.
--
-- ONE SHAPE IT DELIBERATELY DOES NOT COVER, filed as issue #106: two MOIETY-grain
-- rules on DIFFERENT axes can also reach one pair with different grades. That is not
-- this issue -- two axes are two statements about two different mechanisms, which is
-- why the join below matches on `relationship` -- and section 6's precedence orders it
-- deterministically anyway, so the gap there is visibility rather than correctness.
--
-- COUNTS DISTINCT PARTNERS, NOT JOIN ROWS. `ddi_candidate_pair`'s DISTINCT ON includes
-- `source`, so one moiety-grain rule asserted by two authorities yields two rows per
-- pair; count(*) would report 2n. The subject is fixed per group, so distinct partners
-- ARE distinct drug pairs -- gap_uncurated_interaction_rule's own lesson, re-applied.
CREATE OR REPLACE VIEW drugref.curated_grain_disagreement AS
SELECT m.subject_moiety      AS moiety_rule_subject,
       m.via_class           AS moiety_rule_object_class,
       c.via_subject_class   AS class_rule_subject_class,
       c.via_class           AS class_rule_object_class,
       m.relationship,
       m.severity            AS moiety_severity,
       c.severity            AS class_severity,
       m.evidence_grade      AS moiety_evidence_grade,
       c.evidence_grade      AS class_evidence_grade,
       count(DISTINCT m.partner_moiety) AS overlapping_pair_count
FROM   drugref.curated_ddi_pair m
       -- SAME AXIS, not merely the same pair. Comparing across axes would call a
       -- CI_MoA and a CI_PE grade on one pair a disagreement when they are two
       -- statements about two different mechanisms.
JOIN   drugref.curated_ddi_pair c
       ON  c.subject_moiety = m.subject_moiety
       AND c.partner_moiety = m.partner_moiety
       AND c.relationship   = m.relationship
WHERE  m.rule_grain = 'moiety_rule'
AND    c.rule_grain = 'class_rule'
       -- IS DISTINCT FROM, not <>: a NULL mechanism-or-management is common and
       -- irrelevant here, but a NULL on ONE side of severity/evidence_grade would
       -- make `<>` return NULL and silently drop the row -- the NULL-comparison
       -- hazard db/032 records, in its third incarnation.
AND    (m.severity       IS DISTINCT FROM c.severity
        OR m.evidence_grade IS DISTINCT FROM c.evidence_grade)
GROUP  BY m.subject_moiety, m.via_class, c.via_subject_class, c.via_class,
          m.relationship, m.severity, c.severity, m.evidence_grade, c.evidence_grade;

COMMENT ON VIEW drugref.curated_grain_disagreement IS
    'Rule PAIRS -- one moiety-grain rule and one class-grain rule -- that grade at '
    'least one drug pair on the same axis with a different severity or evidence '
    'grade. EXPECTED EMPTY, and it is what makes curated_ddi_pair''s '
    'most-severe-wins precedence safe rather than merely loud: without it, a class '
    'rule over-warning where a curator specifically graded one drug milder would '
    'stand forever, and permanent irrelevant warnings are how prescribers learn to '
    'click through them. THE GRAIN IS THE RULE PAIR, not the drug pair: two rules can '
    'overlap on thousands of pairs (SSRIs x MAOIs alone is ~2,263) and one curator '
    'decision must not be reported thousands of times -- overlapping_pair_count '
    'carries the size instead. An OPERATOR view rather than a gap kind FOR NOW: it is '
    'a question drugref answers itself and so a fair candidate, but a gap_key is '
    'frozen forever and no class-grain content ships yet, so the key''s grain would '
    'be chosen against no real instance. Answer a row by superseding one of the two '
    'rulings, or by recording why both stand.';

-- ============================================================================
-- 8. the class grain enters a signed release (#98)
-- ============================================================================
-- `signature_target_kind` held exactly curated_interaction, curated_condition and
-- release_manifest, so `curated_class_interaction` could not be signed AND could not
-- enter a release manifest at all. The consequence db/033 noted was cosmetic (every
-- class-grain row reads 'unsigned'); the one it did not is not: a node builds and signs
-- a release, the manifest enumerates the two registered kinds, `release_manifest.row_count`
-- matches its own entries, and `verify_release` PASSES -- with the entire class grain,
-- potentially thousands of pairs, absent from the signed set.
--
-- A SILENTLY INCOMPLETE SIGNED RELEASE IS WORSE THAN A FAILED ONE: the signature
-- attests to a set that does not contain what the operator believes it does.
--
-- ONE INSERT IS ONLY HALF THE FIX, and the half a migration can do. `releases.enumerate_live`
-- iterates `_CURATED_KINDS` in Python, and a kind absent from THAT list is absent from
-- the manifest AND from the live side of the comparison -- so it would never even be
-- reported as `added`. The Python half lands in the same commit; the existing test
-- `test_every_curated_catalog_kind_is_covered_by_a_release` derives its expectation
-- from THIS TABLE, so this INSERT is what makes that alarm fire until the Python
-- catches up. That is the alarm working as designed.
INSERT INTO drugref.signature_target_kind
    (target_kind, target_table, pk_column, payload_context)
VALUES
    ('curated_class_interaction', 'curated_class_interaction',
     'curated_class_interaction_id', 'curated_class_interaction/v1')
ON CONFLICT (target_kind) DO NOTHING;

-- ============================================================================
-- 9. open_question.gap_kind -- SIXTEEN in all
-- ============================================================================
-- Widened in a migration exactly as db/007 asks: an unconstrained gap_kind would let a
-- typo mint a whole parallel question namespace that nothing ever reconciles.
--
-- The guard reads the CURRENT constraint definition rather than assuming db/031's, so
-- re-running this file is a no-op once it has landed. It does NOT merge -- the ADD
-- below states all sixteen kinds literally, so replaying this file over a database a
-- LATER migration had widened would narrow it back. `apply_migrations` makes that
-- unreachable (each file runs once, and a file whose content changed after being
-- applied RAISEs), so the exposure is a hand-run `psql -f`. The instruction to the next
-- author is therefore unchanged: add your kind by ADDING A MIGRATION that restates the
-- full list, never by replaying one.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid)
                          LIKE '%uncurated_class_interaction_rule%') THEN
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
                'unruled_composition_activity',
                -- Slice 5c.1
                'uncurated_condition_contradiction', 'uncurated_interaction_rule',
                -- Slice 5c.2
                'unresolved_onc_endpoint',
                -- db/035: the class grain's own primary question
                'uncurated_class_interaction_rule'));
    END IF;
END $$;

-- db/012_expansion_policy_review_round.sql
-- drugref global tier: the review round on Plan B (db/010, PR #32).
--
-- Five fixes, none of them changing what Plan B decided -- every one closing a gap
-- between what db/010's own comments legislate and what its DDL does. Migrations are
-- immutable once applied, so each is a re-issue here rather than an edit there.
--
--   1. THE WALK BECOMES ONE OBJECT. db/010 claimed "one recursion pattern in the
--      codebase, not two" while shipping the identical WITH RECURSIVE block three
--      times: twice in db/010 (ddi_candidate_pair, gap_unreviewed_expansion_root) and
--      once in db/008 (gap_unpopulated_contraindication). Three copies of one frozen
--      rule is the two-lists-in-two-places footgun db/006 was written to remove.
--   2. THE REVIEW GATE BECOMES AXIS-AWARE. gap_unreviewed_expansion_root asked
--      "should this class expand?" of classes whose predicates cannot expand at all.
--   3. THE POLICY'S `source` GETS A CHECK, as every other source column in the schema
--      has.
--   4. ci_axis.expands_descendants's COMMENT stops claiming to force a decision that
--      its DEFAULT supplies.
--   5. ddi_candidate_pair's COMMENT regains the source-blindness caveat db/004 wrote
--      and db/006 dropped -- which descendant expansion made materially larger.

-- ---- 1. the class-DAG descent, as one named object ----------------------------
--
-- Every caller wants the same thing: for each class a CONTRAINDICATION NAMES, that
-- class and everything below it. Hoisted verbatim so the three views cannot drift,
-- and so the next change to how drugref walks the DAG -- a depth column, a
-- materialised view, an index-only rewrite -- lands in one place.
--
-- The recursion is UNION over (root, class), NOT over paths, and that is the load-
-- bearing choice rather than a stylistic one:
--
--   * CYCLE-SAFE. db/002 forbids only self-parenting, so A-is-a-B-is-a-A is
--     representable and one bad release could introduce it. Deduping on the NODE
--     makes the walk terminate; deduping on the path would not. A view that never
--     returns is worse than a wrong answer, because nothing reports it.
--   * LINEAR IN A MULTI-PARENT DAG. 440 classes in the 2026.07.06 release have more
--     than one parent, so the number of PATHS is exponential while the number of
--     NODES is not.
--
-- SCOPED TO CONTRAINDICATED CLASSES, as all three callers already were: this answers
-- "what does this rule reach", so seeding it from every class in the 3,634-class DAG
-- would compute thousands of subtrees nothing asks about.
CREATE OR REPLACE VIEW drugref.ci_class_subtree AS
WITH RECURSIVE subtree(root_uuid, class_uuid) AS (
    SELECT DISTINCT ci.object_class_uuid, ci.object_class_uuid
    FROM   drugref.class_contraindication ci
  UNION
    SELECT s.root_uuid, cp.child_class_uuid
    FROM   subtree s
    JOIN   drugref.class_parent cp ON cp.parent_class_uuid = s.class_uuid
)
SELECT root_uuid, class_uuid FROM subtree;

COMMENT ON VIEW drugref.ci_class_subtree IS
    'For every class a contraindication NAMES: that class and every class below it in '
    'the parent DAG. THE ROOT IS INCLUDED IN ITS OWN SUBTREE -- ddi_candidate_pair''s '
    '`is_direct` and gap_unreviewed_expansion_root''s `count(*) - 1` both depend on '
    'it. Deduped on (root, class) rather than on paths, so it terminates under a '
    'cycle (db/002 forbids only self-parenting) and stays linear in a multi-parent '
    'DAG. Scoped to contraindicated classes: a class no rule names is ABSENT, not '
    'present with only itself. THE ONE PLACE drugref WALKS THE CLASS DAG -- '
    'ddi_candidate_pair, gap_unreviewed_expansion_root and '
    'gap_unpopulated_contraindication all read it, and each carried its own copy '
    'before db/012.';
COMMENT ON COLUMN drugref.ci_class_subtree.root_uuid IS
    'The contraindicated class the walk started from -- what a rule NAMES.';
COMMENT ON COLUMN drugref.ci_class_subtree.class_uuid IS
    'A class at or below root_uuid. Equal to root_uuid for exactly one row per root.';

-- ---- 2. the three callers, now reading it ------------------------------------
--
-- CREATE OR REPLACE rather than DROP: no column list changes in any of the three, so
-- replacing in place keeps grants and dependent objects intact.

-- 2a. The read path. Byte-identical to db/010 apart from the subtree join, and the
--     COMMENT re-issued below for reason 5.
CREATE OR REPLACE VIEW drugref.ddi_candidate_pair AS
SELECT DISTINCT ON (ci.subject_moiety_uuid, ci.object_class_uuid, ci.relationship,
                    ci.source, m.moiety_uuid)
       ci.subject_moiety_uuid AS subject_moiety,   -- the drug the CI is ABOUT
       m.moiety_uuid          AS partner_moiety,   -- the co-administered drug
       ci.relationship,
       ci.object_class_uuid   AS via_class,        -- the class the RULE names
       m.class_uuid           AS member_class,     -- where the PARTNER is filed
       (m.class_uuid = ci.object_class_uuid) AS is_direct,
       ci.source,
       ci.ingest_run,
       r.upstream_release,                         -- WHICH release said so
       r.finished_at          AS ingested_at       -- and when drugref took it in
FROM   drugref.class_contraindication ci
       -- The axis mapping, a join rather than a CASE since db/006: a predicate with
       -- no ci_axis row cannot be in the table at all (foreign key), so there is no
       -- way for a stored contraindication to expand to nothing.
JOIN   drugref.ci_axis a
       ON a.relationship = ci.relationship
JOIN   drugref.ci_class_subtree s
       ON s.root_uuid = ci.object_class_uuid
JOIN   drugref.class_membership m
       ON m.class_uuid   = s.class_uuid
      AND m.relationship = a.membership_relationship
JOIN   drugref.ingest_run r
       ON r.ingest_run_id = ci.ingest_run
       -- Inner join: object_class_uuid is a foreign key into substance_class, so
       -- this drops nothing. It exists to reach (source, source_code), the key the
       -- policy is stated on.
JOIN   drugref.substance_class oc
       ON oc.class_uuid = ci.object_class_uuid
LEFT   JOIN drugref.class_expansion_policy p
       ON p.source = oc.source AND p.source_code = oc.source_code
WHERE  m.moiety_uuid <> ci.subject_moiety_uuid
       -- Direct membership always pairs. Beyond that the predicate must expand AND
       -- the class the RULE NAMES must not be denied. COALESCE makes "no policy row"
       -- expand: unreviewed is the safe default, and the review gate reports it.
AND    (m.class_uuid = ci.object_class_uuid
        OR (a.expands_descendants AND COALESCE(p.decision, 'allow') <> 'deny'))
       -- DISTINCT ON keeps the FIRST row per group, so a partner filed both directly
       -- and under a descendant is reported as the direct hit it is -- which is also
       -- what makes `WHERE is_direct` reproduce the pre-expansion row set exactly.
       -- m.class_uuid last: a deterministic tiebreak among equally-indirect classes.
ORDER  BY ci.subject_moiety_uuid, ci.object_class_uuid, ci.relationship, ci.source,
          m.moiety_uuid, (m.class_uuid = ci.object_class_uuid) DESC, m.class_uuid;

-- 2b. The population test. Identical to db/008 apart from the subtree join.
CREATE OR REPLACE VIEW drugref.gap_unpopulated_contraindication AS
WITH populated AS (
    SELECT DISTINCT s.root_uuid, m.relationship AS membership_relationship
    FROM   drugref.ci_class_subtree s
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
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
                   AND   p.membership_relationship = a.membership_relationship)
GROUP  BY ci.object_class_uuid, sc.class_name, sc.concept_type;

-- ---- 3. the review gate, now axis-aware --------------------------------------
--
-- THE QUESTION THIS VIEW ASKS IS "SHOULD THIS CLASS EXPAND?", so it must only be
-- asked where the answer can change a row. db/010 shipped it blind to
-- ci_axis.expands_descendants, so a class named only by NON-expanding predicates was
-- put on a pharmacist's worklist to decide something already decided -- and worse,
-- register_from_gaps mints it an immortal, externally-citable question_uuid that no
-- available decision can retire, since neither `deny` nor `allow` would alter one
-- pair.
--
-- Latent in db/010 (both MED-RT predicates expand) and live at slice 5b, which is
-- precisely the slice expands_descendants was added for. db/008's
-- gap_unpopulated_contraindication joins ci_axis for the identical reason --
-- "populated is per axis, not per class" -- so this is that rule applied in the
-- third place it was always needed.
--
-- ci_rule_count therefore counts the EXPANDING rules on the class, which is what
-- makes it a priority signal: it is how much reach the decision actually governs. A
-- class named by one expanding and one non-expanding predicate stays on the worklist,
-- weighted by the expanding rule alone.
--
-- THE >20 THRESHOLD IS STILL A DISCOVERY HEURISTIC FOR THE WORKLIST and nothing else.
-- It is emphatically NOT the criterion for denying expansion: that judgement is
-- qualitative ("does this class name an effect a prescriber can act on, or only the
-- organ system?") and lives in class_expansion_policy. Retuning it -- including the
-- open question of whether to count descendant MEMBERS on the rule's axis rather than
-- descendant CLASSES (#36) -- wants a reason from a curator, not from this file.
CREATE OR REPLACE VIEW drugref.gap_unreviewed_expansion_root AS
WITH sized AS (
    -- Minus one for the root itself, which ci_class_subtree contributes exactly once
    -- per root -- the UNION dedupes on (root, class), so this stays right under a
    -- cycle too.
    SELECT root_uuid, count(*) - 1 AS descendant_class_count
    FROM   drugref.ci_class_subtree
    GROUP  BY root_uuid
)
SELECT sc.class_uuid,
       sc.class_name,
       sc.concept_type,
       z.descendant_class_count,
       -- How many EXPANDING contraindications ride on the decision: the priority
       -- signal for a reviewer, not an ordering this view imposes.
       count(*)                AS ci_rule_count,
       max(r.upstream_release) AS upstream_release
FROM   drugref.class_contraindication ci
       -- The axis join, and the whole point of this re-issue: a predicate that does
       -- not expand cannot fan out, so no decision about its object class matters.
JOIN   drugref.ci_axis         a  ON a.relationship  = ci.relationship
JOIN   drugref.substance_class sc ON sc.class_uuid   = ci.object_class_uuid
JOIN   sized                   z  ON z.root_uuid    = ci.object_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = ci.ingest_run
WHERE  z.descendant_class_count > 20
AND    a.expands_descendants
       -- Either decision counts as reviewed. `allow` and `deny` differ for the pair
       -- set and agree here, because this view asks only whether a human has looked.
AND    NOT EXISTS (SELECT 1 FROM drugref.class_expansion_policy p
                   WHERE  p.source      = sc.source
                   AND    p.source_code = sc.source_code)
GROUP  BY sc.class_uuid, sc.class_name, sc.concept_type, z.descendant_class_count;

COMMENT ON VIEW drugref.gap_unreviewed_expansion_root IS
    'Contraindicated classes with more than 20 descendant classes that nobody has '
    'ruled on in class_expansion_policy -- so they expand over their whole subtree by '
    'default, which for an abstract organ-system bucket is fan-out rather than '
    'recall. SCOPED TO PREDICATES THAT ACTUALLY EXPAND (ci_axis.expands_descendants): '
    'a class named only by non-expanding rules is not asked about, because no '
    'decision could change a row -- and ci_rule_count counts the expanding rules for '
    'the same reason. The threshold is a DISCOVERY HEURISTIC for the worklist, never '
    'the criterion for denying expansion: that judgement is qualitative and belongs '
    'in the policy table. EITHER decision retires the question. ABSENCE OF A ROW IS '
    'NOT A GUARANTEE OF SENSIBLE EXPANSION: a badly-shaped root with 20 descendants '
    'is invisible here.';

-- ---- 4. the policy table's own source vocabulary -----------------------------
--
-- Every other `source` column in the schema is CHECK-constrained to the authority
-- spellings drugref knows -- substance_class in db/003, class_contraindication in
-- db/004 -- because the whole registry keys on the string matching exactly. db/010
-- left this one free text, and it is joined on (source, source_code): a row spelled
-- 'MEDRT' inserts cleanly and then matches no class ever again, which is a deny that
-- reads as working and denies nothing. expansion_policy_unresolved does list it, but
-- a constraint refuses it where the typo is made instead of reporting it afterwards.
--
-- Drop-then-add rather than db/003's shape guard: the ledger runs this file exactly
-- once, and the DROP ... IF EXISTS only makes that safe against a database where an
-- operator added the constraint by hand.
ALTER TABLE drugref.class_expansion_policy
    DROP CONSTRAINT IF EXISTS class_expansion_policy_source;
ALTER TABLE drugref.class_expansion_policy
    ADD CONSTRAINT class_expansion_policy_source
    CHECK (source IN ('MED-RT', 'MeSH'));

-- ---- 5. two contracts stated more honestly than db/010 stated them ----------
--
-- 5a. db/010 justified this column with db/006's discipline -- "a predicate cannot be
--     admitted without declaring what it expands over" -- which db/006 enforced with
--     a FOREIGN KEY. A NOT NULL column with a DEFAULT does not force a declaration;
--     it supplies one silently. The default is kept, because true is the recall-safe
--     direction and for a contraindication FEWER ROWS IS THE HARM DIRECTION -- so a
--     slice-5b author who forgets errs the safe way. But the COMMENT should say that
--     is what it does, rather than claim a gate the DDL does not implement.
COMMENT ON COLUMN drugref.ci_axis.expands_descendants IS
    'Does a rule with this predicate reach members of the object class''s DESCENDANTS, '
    'or only its direct members? True for both MED-RT predicates: MED-RT files '
    'membership at the specific node and writes rules against the parent, so '
    'direct-only loses 65% of the pairs. A predicate over a differently shaped object '
    'vocabulary (slice 5b, MeSH) may want false. NOTE THE DEFAULT IS true AND DOES NOT '
    'FORCE A DECISION: unlike the membership_relationship beside it (which has no '
    'default, so db/006''s foreign key makes declaring it unavoidable), a new '
    'predicate inserted without this column EXPANDS. That is deliberate -- expansion '
    'is the recall-safe direction, and an unreviewed sprawling root is caught by '
    'gap_unreviewed_expansion_root -- but it is a default, not a gate.';

-- 5b. db/004 recorded, in a `--` comment, that the membership join is deliberately
--     NOT filtered by source, and that a second membership authority means
--     "revisit whether a MED-RT CI rule should fan out over another authority's
--     members". db/006 moved the contract into the catalog and did not carry that
--     clause with it; db/010 rewrote the COMMENT again without it. Descendant
--     expansion makes it materially larger, because class_parent is source-blind too
--     (its primary key is the pair, with no source column), so the walk is now a
--     TRANSITIVE closure that can cross vocabularies wherever a cross-source parent
--     edge exists.
--
--     Latent today: MeSH classes and edges exist (slice 2b) but MeSH populates
--     has_PA, and ci_axis maps the two MED-RT predicates to has_MoA/has_PE, so no
--     MeSH membership can be reached. Slice 5b is where that stops holding, which is
--     why the caveat goes back in the catalog rather than into a comment Postgres
--     discards.
COMMENT ON VIEW drugref.ddi_candidate_pair IS
    'DIRECTIONAL, not symmetric: one row means "subject_moiety is contraindicated '
    'with partner_moiety", derived from the subject''s own contraindication against '
    'a class the partner belongs to. The mirror row (partner, subject) appears ONLY '
    'if the partner independently carries its own contraindication -- a distinct '
    'assertion. A consumer asking "do X and Y interact" MUST query both directions; '
    'querying one and finding nothing is not evidence of no interaction. '
    'EXPANSION DESCENDS THE CLASS DAG (Plan B, #15) via ci_class_subtree: the partner '
    'may be filed under a DESCENDANT of the class the rule names -- member_class says '
    'which, is_direct says whether it was the class itself. Filter WHERE is_direct '
    'for the direct-membership-only semantics this view had before. Two things bound '
    'the walk: ci_axis.expands_descendants per predicate, and class_expansion_policy, '
    'which denies expansion for abstract organ-system roots -- so this view still has '
    'recall gaps under a DENIED class. '
    'NEITHER THE WALK NOR THE MEMBERSHIP JOIN IS FILTERED BY SOURCE (db/004): '
    'class_parent and class_membership carry no source column, so a rule from one '
    'authority expands over every authority''s edges and members. Harmless while the '
    'reachable axes belong to one source; slice 5b (MeSH-keyed predicates) is where '
    'that must be revisited. '
    'CANDIDATE TIER -- see class_contraindication; nothing here is an alert.';

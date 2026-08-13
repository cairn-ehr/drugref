-- db/034_class_grain_own_subtree.sql -- slice 5c.2, Task 11B: recover the moiety-
-- grain hot path db/033 taxed.
--
-- Spec: docs/superpowers/specs/2026-08-11-drugref-slice-5c2-onc-ddi-floor-design.md
-- section 14.3, read alongside Task 11's own report
-- (.superpowers/sdd/2026-08-11-slice-5c2-onc-ddi-floor/task-11-report.md), which
-- measured the regression this file answers and localised its cause.
--
-- THE FINDING TASK 11 REPORTED. Widening `ci_class_subtree`'s seed -- so a
-- class-grain SUBJECT could expand through it, alongside the object side db/012
-- already served -- inflated Postgres's row-count ESTIMATE for that recursive CTE
-- roughly 5x (37,414 -> 181,334 against an ACTUAL 1,233-1,238), even after Task
-- 11's own flattening mitigation. The inflated estimate tips the recursive term's
-- join strategy from a cheap Hash Join to a Merge Join re-scanning `class_parent`
-- by index once per level, and it does this FOR EVERY READER OF THE VIEW -- not
-- only the class-grain rows that motivated the widening. Measured: even an EMPTY
-- class-grain overlay cost ~3.6x baseline (1.4 ms -> ~5.1 ms), and the moiety-
-- grain-only control query (cyclosporine, touching zero class-grain data) paid the
-- SAME tax as the class-grain hot path itself. That is a structural cost paid by
-- every existing consumer, on every query, for content most of them do not have --
-- unacceptable before clinical content lands on this path for real, per the human
-- partner's ruling recorded in task-11b-brief.md.
--
-- THE HYPOTHESIS THIS FILE TESTS. `ci_class_subtree` never needed widening: it was
-- widened because section 14.3 asks the class-grain subject to expand "through
-- ci_class_subtree ... exactly as the object side already is" -- read, at the
-- time, as "reuse THIS view". But the object side's own use of ci_class_subtree
-- (inside `ddi_candidate_pair`) and the class grain's use of it are two
-- STRUCTURALLY DIFFERENT walks scoped by two DIFFERENT root sets
-- (class_contraindication vs class_pair_contraindication) that merely happened to
-- share one view. Giving the class grain its OWN subtree expansion -- seeded ONLY
-- from class_pair_contraindication's own classes -- lets `ci_class_subtree` return
-- to db/012's original seed and plan, while the class grain pays only for a walk
-- sized to its own (currently tiny) root set. Measured below: it held.
--
-- WHY THIS IS A NEW FILE, NOT AN EDIT TO db/033. db/033 is applied for real on
-- `drugref_5c4` (issue 91's own subject) and `db.apply_migrations` RAISEs on a
-- checksum mismatch the moment a migration file's content changes after being
-- applied anywhere. db/033's own file-level comment already explains this
-- discipline; nothing about it changes here.

-- ============================================================================
-- 1. ci_class_subtree -- RESTORED to db/012's original seed
-- ============================================================================
-- Byte-identical to db/012's body (and to db/033's body BEFORE the widening this
-- file reverts): roots are `class_contraindication.object_class_uuid` alone.
-- class_pair_contraindication no longer contributes a root here -- section 2 below
-- gives it a walk of its own instead.
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

-- Re-issued for db/027's precedent (an applied migration's view BODY may be
-- widened, or in this case un-widened, but its FILE is never rewritten): the
-- comment must stop claiming class_pair_contraindication as a root source, and
-- must record why a SEPARATE view exists for it rather than a second widening.
COMMENT ON VIEW drugref.ci_class_subtree IS
    'For every class a CONTRAINDICATION NAMES -- class_contraindication''s '
    'object_class_uuid alone -- that class and every class below it in the parent '
    'DAG. THE ROOT IS INCLUDED IN ITS OWN SUBTREE -- ddi_candidate_pair''s '
    '`is_direct` and gap_unreviewed_expansion_root''s `count(*) - 1` both depend on '
    'it. Deduped on (root, class) rather than on paths, so it terminates under a '
    'cycle (db/002 forbids only self-parenting) and stays linear in a multi-parent '
    'DAG. Scoped to contraindicated classes: a class no rule names is ABSENT, not '
    'present with only itself. db/033 BRIEFLY widened this view''s roots to also '
    'cover class_pair_contraindication (the class x class grain, db/032), so a '
    'class-grain SUBJECT could expand through it too -- Task 11B (this file) '
    'reverted that: the wider root set inflated Postgres''s row-ESTIMATE for the '
    'whole recursive CTE roughly 5x (37,414 -> 181,334 against an ACTUAL '
    '1,233-1,238), which tipped the recursive join from a Hash Join to a slower '
    'Merge Join FOR EVERY READER, not only class-grain ones -- a tax the empty-'
    'overlay case alone measured at ~3.6x. ci_class_pair_subtree (section 2 below) '
    'now serves the class grain from its OWN, separately-estimated walk. ONE OF TWO '
    'class-DAG walks scoped to a contraindication''s own roots since db/021 (which '
    'added the THIRD, unscoped, for Plan C''s discovery views) -- this one serves '
    'ddi_candidate_pair, gap_unreviewed_expansion_root and '
    'gap_unpopulated_contraindication. THEY ARE NOT MERGED ON PURPOSE: this view''s '
    'narrow root set is what makes the hot pair lookup ~1.4 ms instead of several '
    'times that, for an identical row set.';

-- ============================================================================
-- 2. ci_class_pair_subtree -- the class grain's OWN walk, seeded from its OWN
--    candidate tier
-- ============================================================================
-- SAME CONSTRUCTION AS ci_class_subtree -- (root, class) dedup, not path dedup, for
-- db/012's exact reasons (cycle safety under db/002's self-parenting-only rule;
-- linear rather than exponential under a multi-parent DAG) -- over a DIFFERENT root
-- source: class_pair_contraindication's own subject_class_uuid AND
-- object_class_uuid, because Task 11's file-level comment already established both
-- columns are classes a class x class rule NAMES, and db/012's own scoping
-- principle ("for every class a contraindication names") applies to both without
-- reading db/012 as though it anticipated a second table.
--
-- ONE FLAT DISTINCT OVER A UNION ALL, not two DISTINCTs combined by UNION -- Task
-- 11's own measured lesson (see this file's header, and db/033's now-corrected
-- comment on ci_class_subtree): the shape of the base term, not merely its
-- content, is what the planner's row-estimate heuristic keys off. Written the safe
-- way from the start rather than re-discovering the same regression one view later.
CREATE OR REPLACE VIEW drugref.ci_class_pair_subtree AS
WITH RECURSIVE subtree(root_uuid, class_uuid) AS (
    (
        SELECT DISTINCT class_uuid, class_uuid
        FROM (
            SELECT subject_class_uuid AS class_uuid FROM drugref.class_pair_contraindication
          UNION ALL
            SELECT object_class_uuid FROM drugref.class_pair_contraindication
        ) roots
    )
  UNION
    SELECT s.root_uuid, cp.child_class_uuid
    FROM   subtree s
    JOIN   drugref.class_parent cp ON cp.parent_class_uuid = s.class_uuid
)
SELECT root_uuid, class_uuid FROM subtree;

COMMENT ON VIEW drugref.ci_class_pair_subtree IS
    'For every class a CLASS x CLASS contraindication rule NAMES -- '
    'class_pair_contraindication''s subject_class_uuid AND object_class_uuid, both '
    'sides, since db/032''s class-subject rule expands on both -- that class and '
    'every class below it in the parent DAG. THE ROOT IS INCLUDED IN ITS OWN '
    'SUBTREE, matching ci_class_subtree''s own convention. Deduped on (root, class), '
    'not on paths, for db/012''s cycle-safety and linearity reasons. A SEPARATE '
    'view from ci_class_subtree ON PURPOSE (Task 11B, db/034): db/033 briefly '
    'widened ci_class_subtree''s roots to cover this table too, and Postgres''s '
    'row-estimate for that shared recursive CTE inflated ~5x as a result, taxing '
    'ci_class_subtree''s OTHER readers (ddi_candidate_pair and both gap views) for '
    'content only this walk needs. Kept small and separately estimated instead: '
    'empty while class_pair_contraindication is empty, and sized to only the '
    'classes a class x class rule actually names as this grain grows. THIRD scoped '
    'class-DAG walk in the codebase (after ci_class_subtree and class_subtree, '
    'db/021''s own comment on why those two are not merged) -- a fourth would be one '
    'too many; if a future grain needs its own roots, reconsider a shared, '
    'parameterised walk rather than a fourth copy.';

-- ============================================================================
-- 3. curated_ddi_pair -- unchanged shape, ONE line of plumbing moved
-- ============================================================================
-- CREATE OR REPLACE'd a fourth time (db/029 defined it, db/030 appended
-- signature_status, db/033 added the class grain and its two trailing columns).
-- EVERY COLUMN, EVERY PREDICATE AND EVERY JOIN in both halves is unchanged from
-- db/033 -- the eight tests in tests/test_class_subject_read_path.py are the
-- contract, and this migration's whole point is to satisfy them from a cheaper
-- plan, not a different result. The ONLY edit is `class_dag`'s source: it now
-- reads `drugref.ci_class_pair_subtree` (section 2 above, the class grain's own
-- walk) instead of `drugref.ci_class_subtree` (section 1, restored to db/012's
-- original scope and therefore no longer wide enough to carry a class-grain
-- SUBJECT's expansion -- exactly the widening this file reverts).
CREATE OR REPLACE VIEW drugref.curated_ddi_pair AS
WITH class_dag AS (
    -- ci_class_pair_subtree's recursive walk, READ ONCE for the whole class-grain
    -- half -- db/033's own reasoning for naming this CTE once and having both
    -- expansion CTEs below reference it, preserved verbatim: a CTE referenced more
    -- than once is MATERIALIZED by default (PG12+), so this is a plain name, not a
    -- hint, and it is what keeps the class grain's own walk to ONE materialisation
    -- rather than two (subject side and object side each inlining the view would
    -- pay for it twice, the same defect db/033 already fixed once for this view).
    SELECT * FROM drugref.ci_class_pair_subtree
),
class_rule_subject_member AS (
    -- One row per (rule, subject-side moiety), DEDUPED -- db/033's own reasoning,
    -- unchanged: a moiety filed under two classes within the same subtree must
    -- contribute exactly one row, or the cross join below would double-count it.
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
       -- IDENTICAL shape to ddi_candidate_pair's own gate, over the SUBJECT class
       -- instead of the object: direct membership always pairs; beyond that the
       -- predicate must expand AND the class the rule names must not be denied.
    AND    (s.class_uuid = cci.subject_class_uuid
            OR (a.expands_descendants AND COALESCE(p.decision, 'allow') <> 'deny'))
),
class_rule_partner_member AS (
    -- The object side -- the SAME expansion ddi_candidate_pair already runs (over
    -- class_contraindication's object, via ci_class_subtree), here run again over
    -- class_pair_contraindication's object, via ci_class_pair_subtree instead.
    -- DISTINCT ON, not plain DISTINCT, because is_direct/member_class ARE exposed
    -- downstream -- db/033's own reasoning, unchanged.
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
-- ---- half 1: the moiety grain (db/029/db/030/db/033's SELECT, verbatim) --------
-- Reads ddi_candidate_pair, which itself reads ci_class_subtree -- now back to
-- db/012's original, narrow scope (section 1 above), so this half's plan is the
-- one db/030 shipped: no change of any kind flows into it from the class grain.
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
       p.upstream_release,          -- which release raised the candidate
       p.source           AS candidate_source,
       COALESCE(ss.signature_status, 'unsigned') AS signature_status,
       'moiety_rule'::text AS rule_grain,
       NULL::uuid          AS via_subject_class
FROM   drugref.ddi_candidate_pair p
       -- INNER: an ungraded rule reaches this view NEVER, not with NULL columns.
JOIN   drugref.curated_interaction c
       ON  c.subject_moiety_uuid = p.subject_moiety
       AND c.object_class_uuid   = p.via_class
       AND c.relationship        = p.relationship
       -- LEFT: see db/030's own block comment (section 7 there) -- an unsigned row
       -- must still appear, labelled 'unsigned', and a key revocation must relabel
       -- rather than remove it.
LEFT   JOIN drugref.curated_signature_status ss
       ON  ss.target_kind = 'curated_interaction'
       AND ss.target_id   = c.curated_interaction_id
WHERE  c.superseded_by IS NULL
AND    c.applies

UNION ALL

-- ---- half 2: the class grain (db/032/db/033), now over ci_class_pair_subtree --
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
       cci.subject_class_uuid AS via_subject_class
FROM   drugref.curated_class_interaction cci
JOIN   class_rule_subject_member  sm ON sm.curated_class_interaction_id = cci.curated_class_interaction_id
JOIN   class_rule_partner_member  pm ON pm.curated_class_interaction_id = cci.curated_class_interaction_id
       -- INNER, mirroring the moiety grain's own INNER join to its candidate tier:
       -- the candidate row is where `source`/`ingest_run` (and so `upstream_release`)
       -- come from. If a rebuild ever removes the matching class_pair_contraindication
       -- row, this judgement stops producing pairs here -- the same "candidate
       -- vanished" behaviour the moiety grain already has (db/029's
       -- curated_target_unresolved is the operator-facing side of that, and stays
       -- scoped to curated_interaction/curated_condition; widening it to this
       -- table too is a follow-up, not this task's column list).
JOIN   drugref.class_pair_contraindication cpc
       ON  cpc.subject_class_uuid = cci.subject_class_uuid
       AND cpc.object_class_uuid  = cci.object_class_uuid
       AND cpc.relationship       = cci.relationship
JOIN   drugref.ingest_run r ON r.ingest_run_id = cpc.ingest_run
       -- See the file-level comment above: 'curated_class_interaction' names no row
       -- in signature_target_kind yet, so this LEFT JOIN can never match today and
       -- every class-grain row reads 'unsigned' -- written now so signing this
       -- grain later needs no further change here.
LEFT   JOIN drugref.curated_signature_status ss
       ON  ss.target_kind = 'curated_class_interaction'
       AND ss.target_id   = cci.curated_class_interaction_id
WHERE  cci.superseded_by IS NULL
AND    cci.applies
       -- db/032 DECISION 2: a class legitimately contraindicates its own members
       -- (QT-prolonging x QT-prolonging), but a MOIETY is never its own partner --
       -- db/014's exact rule, enforced here because only the expanded pair can see
       -- which one this is.
AND    sm.subject_moiety <> pm.partner_moiety;

COMMENT ON VIEW drugref.curated_ddi_pair IS
    'Drug pairs carrying a live drugref grade, from EITHER of two rule grains --  '
    '`rule_grain` says which (''moiety_rule'' | ''class_rule''), and '
    '`via_subject_class` names the class-grain rule''s subject (NULL for a moiety '
    'rule). A moiety-grain row is expanded from the class-level rule the grade was '
    'written against, so ONE curated row reaches every pair its rule expands to; a '
    'class-grain row is expanded on BOTH sides (spec 2026-08-11-drugref-slice-5c2-'
    'onc-ddi-floor-design.md section 14.3), so one row can reach thousands of pairs '
    '(SSRIs x MAOIs alone is ~2,263). INNER JOIN throughout: an ungraded candidate '
    'does not appear here at all, because a NULL severity beside a real pair reads '
    'as "reviewed and harmless". The moiety grain''s rows are UNCHANGED by this '
    'widening and remain a STRICT SUBSET of this view -- widening only ever adds, '
    'because fewer rows is the harm direction for a contraindication. '
    'ddi_candidate_pair remains the place to ask what a moiety-grain release said; '
    'class_pair_contraindication is the class-grain''s own candidate tier. Each '
    'grain walks the class DAG through its OWN view (ci_class_subtree for the '
    'moiety grain, ci_class_pair_subtree for the class grain, both db/034/Task '
    '11B) -- SEPARATED, after a merged walk was measured to tax every moiety-grain '
    'query for class-grain content most callers do not have. '
    'signature_status is REGISTRY-LEVEL ONLY (see curated_signature_status) and its '
    'join is LEFT for both halves: an unsigned row still appears, labelled '
    '''unsigned'', and a key revocation relabels a row rather than removing it -- '
    'class-grain rows read ''unsigned'' unconditionally today, since signing that '
    'grain has not shipped yet.';

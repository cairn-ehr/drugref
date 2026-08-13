-- db/033_two_grain_read_path.sql -- slice 5c.2, Task 11: the two-grain read path.
--
-- Spec: docs/superpowers/specs/2026-08-11-drugref-slice-5c2-onc-ddi-floor-design.md
-- section 14.3 ("One consumer view, not two") and section 8.
--
-- A NEW FILE, NOT AN EDIT TO db/032. db/032 shipped and merged in this branch's own
-- earlier commits (7962a0b, "feat(db): db/032 adds the class-subject interaction
-- rule"), and db.py's migration ledger stores a SHA-256 per file and RAISEs
-- "Migrations are immutable once applied: add a new db/*.sql file instead of
-- editing..." the moment a checksum changes. The test suite's `_migrated` fixture
-- drops and rebuilds the schema every session, so it would not catch an edit to
-- db/032 -- but `drugref_5c4`, the measurement database this file's own comments
-- report numbers against, has db/032 applied for real, and `drugref migrate` on it
-- (or on any deployed node) would hard-stop on the checksum mismatch. So: new file.
--
-- WHAT THIS FILE DOES. Tasks 1-8 built the moiety x class grain
-- (class_contraindication / curated_interaction) and Tasks 9-10 (db/032) added a
-- second, class x class grain (class_pair_contraindication / curated_class_interaction)
-- -- but neither Task 9 nor Task 10 touched the READ path, so a class-grain judgement
-- was unreachable from any view. This file widens two EXISTING views (CREATE OR
-- REPLACE, the mutable-view-over-immutable-migration pattern db/027 and db/030 both
-- already used) so the class grain becomes readable:
--
--   1. `ci_class_subtree` -- widened to also walk the class DAG from
--      class_pair_contraindication's own subject/object classes, not only
--      class_contraindication's object classes. Needed because the class-grain's
--      SUBJECT now also expands (Tasks 1-8's subject was always a single moiety and
--      never needed this), and section 14.3 asks for that expansion to go "through
--      ci_class_subtree + class_membership exactly as the object side already is" --
--      which only works if the walk actually contains the roots a class-grain rule
--      names. See section 1 below for why this is safe and additive.
--
--   2. `curated_ddi_pair` -- CREATE OR REPLACE'd a third time (db/029 defined it,
--      db/030 appended signature_status) to UNION ALL a second half over the
--      class-grain tables, tagged by two new trailing columns: `rule_grain`
--      ('moiety_rule' | 'class_rule') and `via_subject_class` (NULL for a moiety
--      rule). The existing moiety-grain SELECT is copied VERBATIM from db/030 --
--      same predicate, same joins, same column list up to signature_status -- so its
--      rows keep their exact meaning and remain a STRICT SUBSET of the widened view,
--      per section 14.3's "fewer rows is the harm direction" argument one level up
--      from db/029's own INNER JOIN choice.

-- ============================================================================
-- 1. ci_class_subtree -- widen the walk's roots, not its shape
-- ============================================================================
-- db/012 scoped this view to "the classes a CONTRAINDICATION NAMES" -- originally
-- meaning class_contraindication.object_class_uuid alone, because that table was the
-- only kind of contraindication candidate that existed. class_pair_contraindication
-- (db/032) is also a contraindication candidate, and BOTH its columns are classes a
-- rule NAMES -- so the honest reading of db/012's own scoping principle is that this
-- view's roots should include them too, not that db/012 anticipated a second table.
--
-- WHY THIS MUST HAPPEN HERE RATHER THAN BE RE-DERIVED PER CALLER: db/012's whole
-- point was hoisting ONE recursive walk out of three call sites that each carried a
-- copy of it ("Three copies of one frozen rule is the two-lists-in-two-places
-- footgun db/006 exists to remove" -- db/012's own preamble). Section 14.3 tells
-- Task 11 to expand the class-grain subject "through ci_class_subtree ... exactly as
-- the object side already is" -- reusing the ONE walk, not adding a second recursive
-- CTE beside it (which class_subtree, db/021's UNSCOPED sibling, already shows costs
-- 5x more for an identical row set: 18.8 ms against 3.6 ms on the real release,
-- exactly the wrong direction for a hot path this task is about to measure).
--
-- SAFE AND ADDITIVE, MEASURABLY. Every existing reader of this view --
-- ddi_candidate_pair, gap_unreviewed_expansion_root, gap_dead_by_expansion_policy
-- (via ci_rule_partner_reach), gap_unpopulated_contraindication -- joins back to
-- class_contraindication.object_class_uuid to use it (`JOIN drugref.ci_class_subtree
-- s ON s.root_uuid = ci.object_class_uuid`, one join column). A root added here that
-- is NOT also a class_contraindication.object_class_uuid value simply never matches
-- any of those joins, so their output is byte-identical to before. A root that
-- HAPPENS to already be shared between both tables (measured on drugref_5c4: SSRIs,
-- MAOIs and CYP3A4 Inhibitors [MoA] are ALREADY class_contraindication object
-- classes from MED-RT's own moiety x class rules) contributes no new rows either --
-- UNION already deduped it. tests/test_ddi_pairs.py's
-- test_the_subtree_view_is_the_one_place_the_class_dag_is_walked pins the untouched
-- behaviour: it never populates class_pair_contraindication, so its `unnamed` class
-- (walked through as a descendant but named by no rule) still returns zero rows
-- under its own root query.
CREATE OR REPLACE VIEW drugref.ci_class_subtree AS
WITH RECURSIVE subtree(root_uuid, class_uuid) AS (
    (
        -- ONE flat DISTINCT over a UNION ALL of all three root sources, not three
        -- separate DISTINCTs combined by UNION. MEASURED, not merely tidier: an
        -- earlier draft wrote this as three `SELECT DISTINCT ... UNION SELECT
        -- DISTINCT ...` arms (one per source), reasoning that "UNION dedupes
        -- either way" -- true for the ROWS, false for the PLAN. Each extra
        -- UNION-of-DISTINCT arm roughly doubled Postgres's row-count ESTIMATE for
        -- the whole recursive CTE (measured: 37,414 -> 181,334 rows estimated for
        -- an ACTUAL 1,233-1,238), which tipped the recursive term's join strategy
        -- from a single Hash Join build (cheap) to a Merge Join re-scanning
        -- class_parent by index once per recursion level (measured cost: the
        -- walk's own execution time roughly 4x, ~1.0 ms -> ~4.0 ms, on this table's
        -- shape -- BEFORE counting that this view is now read from three places in
        -- one query, see the class-grain half below). This flat form measured
        -- back down to the single-DISTINCT baseline's estimate.
        SELECT DISTINCT class_uuid, class_uuid
        FROM (
            SELECT object_class_uuid AS class_uuid FROM drugref.class_contraindication
          UNION ALL
            -- Both endpoints of a class-subject rule are roots -- the SUBJECT wall
            -- Tasks 1-8 never needed (their subject was always a single moiety)
            -- and the OBJECT wall class_contraindication's own rows already seed.
            SELECT subject_class_uuid FROM drugref.class_pair_contraindication
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

-- db/012's original COMMENT is now inaccurate about scope ("a contraindication" was
-- singular in meaning as well as in text); db/027 already re-issued this exact
-- COMMENT once for a different correction, on the precedent that an applied
-- migration's view body may be widened but its FILE never rewritten. Re-issued again
-- here with the scope corrected, everything else preserved verbatim.
COMMENT ON VIEW drugref.ci_class_subtree IS
    'For every class a contraindication NAMES -- from class_contraindication''s '
    'object_class_uuid (moiety x class rules) OR class_pair_contraindication''s '
    'subject_class_uuid/object_class_uuid (class x class rules, db/032/db/033) -- '
    'that class and every class below it in the parent DAG. THE ROOT IS INCLUDED IN '
    'ITS OWN SUBTREE -- ddi_candidate_pair''s `is_direct` and '
    'gap_unreviewed_expansion_root''s `count(*) - 1` both depend on it. Deduped on '
    '(root, class) rather than on paths, so it terminates under a cycle (db/002 '
    'forbids only self-parenting) and stays linear in a multi-parent DAG. Scoped to '
    'contraindicated classes: a class no rule names -- from EITHER table -- is '
    'ABSENT, not present with only itself. ONE OF TWO class-DAG walks since db/021 -- '
    'this one serves the CONTRAINDICATION read path and class_subtree serves Plan C. '
    'THEY ARE NOT MERGED ON PURPOSE: this view''s narrow root set is what makes the '
    'hot pair lookup 3.6 ms instead of 18.8 ms on the real release, for an identical '
    'row set.';

-- ============================================================================
-- 2. curated_ddi_pair -- both grains, one view
-- ============================================================================
-- `CREATE OR REPLACE VIEW` can only ADD a trailing column; it cannot reorder or
-- rename an existing one (db/030's own note, still true). The moiety-grain half
-- below is db/030's SELECT list REPEATED VERBATIM up to signature_status, with
-- `'moiety_rule'::text AS rule_grain, NULL::uuid AS via_subject_class` appended --
-- nothing about its predicate, its joins or its column values changes, which is
-- what makes "existing rows keep their exact meaning and remain a strict subset"
-- true by construction rather than by inspection.
--
-- THE CLASS-GRAIN HALF EXPANDS BOTH SIDES, unlike the moiety grain (whose subject
-- is already one moiety, so only the object/partner side needs class_membership).
-- It is built from TWO independent CTEs -- one per side -- each mirroring
-- ddi_candidate_pair's own object-side expansion exactly: the SAME `ci_axis`
-- join, the SAME `ci_class_subtree` + `class_membership` walk, the SAME
-- `class_expansion_policy_current` gate on `expands_descendants`. A cross join of
-- the two then produces every (subject-side member, object-side member) pair,
-- which is exactly "SSRIs (73) x MAOIs (31) is ~2,263 pairs from one rule" (spec
-- section 14.3) -- a plain product of two DISTINCT member sets, which is why each
-- CTE dedupes ITS OWN side before the cross join runs, rather than after: a moiety
-- filed under two classes within the same subtree (a real possibility in a
-- multi-parent DAG, same as ddi_candidate_pair's own DISTINCT ON reason) must
-- contribute exactly one row per side, not multiply the cross product by however
-- many paths reach it.
--
-- WHY A CTE PER SIDE RATHER THAN A SINGLE DISTINCT ON OVER THE WHOLE QUERY (as
-- ddi_candidate_pair itself uses): ddi_candidate_pair's DISTINCT ON dedupes ONE
-- expanding dimension (the object side) against a FIXED subject. Here BOTH sides
-- expand, so a single DISTINCT ON over the joined rows cannot separate "this
-- subject-side membership is redundant" from "this object-side membership is
-- redundant" -- they are independent questions, so they get independent answers,
-- each exactly as small as ddi_candidate_pair's own dedupe.
--
-- THE SELF-PAIR EXCLUSION (db/032 DECISION 2, `subject_moiety <> partner_moiety`)
-- is the class-grain's answer to the SAME requirement db/014 states as a CHECK for
-- the moiety grain (`moiety_contraindication_not_self`) and ddi_candidate_pair
-- states as a WHERE clause (`m.moiety_uuid <> ci.subject_moiety_uuid`) for its own
-- fixed-subject case: a drug is never its own co-administration partner. It has to
-- live here, at the pair grain, because class_pair_contraindication legitimately
-- permits a class to equal itself as a RULE subject (QT-prolonging x QT-prolonging
-- is a real ONC entry) -- the thing db/014 forbids is two IDENTICAL MOIETIES
-- pairing, not a class naming itself, and only the expanded pair knows which one
-- it is looking at.
--
-- THE MULTI-SOURCE DUPLICATION THIS INHERITS IS INTENDED, NOT A BUG: exactly the
-- same shape spec section 13's risk table already accepts for the moiety grain
-- ("A MED-RT release later asserts the same rule | Intended and harmless: source is
-- in the candidate PK so both coexist, and curated_interaction's key omits source
-- so one judgement still covers both"). class_pair_contraindication's PK also
-- includes `source` and curated_class_interaction's natural key also omits it, so a
-- rule two authorities both assert produces one row PER candidate_source here too --
-- the same visible-provenance duplication the moiety-grain read path already
-- ships, not a new defect.
--
-- signature_status: `curated_class_interaction` rows can never actually be signed
-- yet (db/030's `signature_target_kind` admits only 'curated_interaction',
-- 'curated_condition' and 'release_manifest' -- extending it to a fourth kind is a
-- signing-slice decision this task does not make). The LEFT JOIN below is written
-- anyway, naming the target_kind a future migration would register: every
-- class-grain row therefore reads 'unsigned' today via the same COALESCE db/030
-- already uses, and starts reflecting real signatures automatically the day that
-- registration lands, with no further change to this view.
CREATE OR REPLACE VIEW drugref.curated_ddi_pair AS
WITH class_dag AS (
    -- ci_class_subtree's recursive walk, READ ONCE for the whole class-grain half.
    -- Both expansion CTEs below need it (subject side AND object side), and a bare
    -- `JOIN drugref.ci_class_subtree` in each would inline the view TWICE -- two
    -- independent WITH RECURSIVE materialisations of the SAME result set, since
    -- Postgres does not share computation across separate references to a view
    -- that itself wraps a recursive CTE. Naming it ONCE here and having both
    -- downstream CTEs reference THIS name instead keeps it to one materialisation:
    -- a CTE referenced more than once is MATERIALIZED by default (PG12+), so this
    -- is a plain name, not a hint. (`ddi_candidate_pair`'s own reference, in the
    -- moiety-grain half below, is a SEPARATE view invocation and still pays for
    -- its own walk -- sharing across two different views' query text is not
    -- expressible in SQL. Measured cost of even that unavoidable second walk is in
    -- this task's report.)
    SELECT * FROM drugref.ci_class_subtree
),
class_rule_subject_member AS (
    -- One row per (rule, subject-side moiety), DEDUPED: a moiety filed under two
    -- classes within the same subtree must contribute exactly one row, or the
    -- cross join below would double-count it. No is_direct/member_class carried
    -- here (nothing downstream needs the subject side's own filing detail), so a
    -- plain DISTINCT suffices -- no ORDER BY tie-break to make, unlike the object
    -- side below.
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
    -- The object side -- the SAME expansion ddi_candidate_pair already runs, over
    -- class_pair_contraindication's object class rather than
    -- class_contraindication's. DISTINCT ON, not plain DISTINCT, because
    -- `is_direct`/`member_class` ARE exposed downstream and a moiety filed both
    -- directly on the named class and under one of its descendants must report as
    -- the direct hit it is -- ddi_candidate_pair's own tie-break, copied exactly.
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
-- ---- half 1: the moiety grain (db/029/db/030's SELECT, verbatim) --------------
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

-- ---- half 2: the class grain (db/032/db/033) -----------------------------------
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
    'class_pair_contraindication is the class-grain''s own candidate tier. '
    'signature_status is REGISTRY-LEVEL ONLY (see curated_signature_status) and its '
    'join is LEFT for both halves: an unsigned row still appears, labelled '
    '''unsigned'', and a key revocation relabels a row rather than removing it -- '
    'class-grain rows read ''unsigned'' unconditionally today, since signing that '
    'grain has not shipped yet.';

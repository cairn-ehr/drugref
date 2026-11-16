-- db/079 -- the class grain's member expansion becomes a view of its own (#255).
--
-- THE DEFECT. `drugref interactions` banners a curated grade that has fallen out of
-- its list (#121). A CLASS-grain grade falls out when a re-ingest drops its
-- `class_pair_contraindication` row: `curated_ddi_pair`'s class half INNER JOINs that
-- table (db/038). Which drugs such a grade concerns is the members of its two classes,
-- and that expansion lived only as two CTEs inside `curated_ddi_pair`. The reader
-- could not ask it, so `curation.orphaned_interaction_grades` bannered every class
-- orphan for EVERY drug asked: safe, and noisy enough to teach people to ignore it.
--
-- TWO CHANGES, BOTH NEEDED.
--
--   §1 `ci_class_pair_subtree` is also seeded from `curated_class_interaction`'s two
--      classes. Lifting the CTEs alone would NOT have fixed this: the walk's roots
--      came only from `class_pair_contraindication`, so an orphan -- by definition a
--      rule whose row there is gone -- would have expanded to nobody.
--   §2 `class_rule_member` holds the expansion, and `curated_ddi_pair` reads it.
--
-- WHAT A READER OF `curated_ddi_pair` SEES IS UNCHANGED. Every column, predicate and
-- join of both halves is db/038's; only where the member rows come from moved. The
-- class half still INNER JOINs `class_pair_contraindication`, so the extra roots §1
-- adds (an orphan's classes) produce member rows that view then discards.
-- tests/test_class_subject_read_path.py is the contract.


-- ============================================================================
-- 1. ci_class_pair_subtree -- seeded from BOTH class-grain tables
-- ============================================================================
-- STILL ONE WALK, NOT A FOURTH. db/034's comment on this view says a fourth scoped
-- class-DAG walk "would be one too many", and the overlay names the same kind of
-- thing the candidate tier does: classes a class x class rule names. Live overlay
-- rows' classes are, on a healthy database, a SUBSET of the candidate tier's, so the
-- root set grows only by the classes of orphaned rules -- expected empty.
--
-- EVERY OVERLAY ROW, NOT ONLY LIVE ONES: a superseded row's classes are its
-- successor's (the natural key is the same three columns), so filtering adds a
-- predicate and removes nothing.
--
-- STILL ONE FLAT DISTINCT OVER A UNION ALL, db/034's measured shape for the planner's
-- row estimate; two more branches, not a second DISTINCT.
CREATE OR REPLACE VIEW drugref.ci_class_pair_subtree AS
WITH RECURSIVE subtree(root_uuid, class_uuid) AS (
    (
        SELECT DISTINCT class_uuid, class_uuid
        FROM (
            SELECT subject_class_uuid AS class_uuid FROM drugref.class_pair_contraindication
          UNION ALL
            SELECT object_class_uuid FROM drugref.class_pair_contraindication
          UNION ALL
            SELECT subject_class_uuid FROM drugref.curated_class_interaction
          UNION ALL
            SELECT object_class_uuid FROM drugref.curated_class_interaction
        ) roots
    )
  UNION
    SELECT s.root_uuid, cp.child_class_uuid
    FROM   subtree s
    JOIN   drugref.class_parent cp ON cp.parent_class_uuid = s.class_uuid
)
SELECT root_uuid, class_uuid FROM subtree;

COMMENT ON VIEW drugref.ci_class_pair_subtree IS
    'For every class a CLASS x CLASS rule NAMES -- in the candidate tier '
    '(class_pair_contraindication) OR the curated overlay (curated_class_interaction, '
    'since db/079), both sides of each -- that class and every class below it in the '
    'parent DAG. THE ROOT IS INCLUDED IN ITS OWN SUBTREE, matching ci_class_subtree''s '
    'convention. Deduped on (root, class), not on paths, for db/012''s cycle-safety and '
    'linearity reasons. THE OVERLAY SEED (db/079, #255) is what lets a curated rule '
    'whose candidate a re-ingest dropped still expand, so drugref interactions can tell '
    'which drugs its missing grade concerns; on a healthy database it adds no root. A '
    'SEPARATE view from ci_class_subtree ON PURPOSE (db/034): a merged walk inflated '
    'Postgres''s row estimate ~5x and taxed every moiety-grain reader for content only '
    'this walk needs. THIRD scoped class-DAG walk in the codebase (after ci_class_subtree '
    'and class_subtree) -- a fourth would be one too many; if a future grain needs its '
    'own roots, reconsider a shared, parameterised walk rather than a fourth copy.';


-- ============================================================================
-- 2. class_rule_member -- who a live class rule reaches, on each side
-- ============================================================================
-- db/038's two CTEs, verbatim in their joins and predicates, as the two arms of one
-- view. ONE VIEW WITH A `side` COLUMN, NOT TWO VIEWS, for db/034's "read once"
-- reason: two views would each inline the recursive walk, and `curated_ddi_pair`
-- reads both sides; one view read through one CTE walks once.
--
-- THE ONE PREDICATE DROPPED IS `cci.applies`. `curated_ddi_pair` still filters it in
-- its own WHERE, so its rows are unchanged; the orphan banner needs it gone, because
-- an `applies = false` ruling that fell out is also a ruling the list cannot show
-- (cli_interactions._report_orphans says "ruling", not "grade", for that reason).
--
-- SUBJECT-SIDE `member_class` / `is_direct` ARE NULL: db/038's subject CTE never
-- carried them (the read reports the partner's route into the class, not the
-- subject's), and inventing them here would change what DISTINCT collapses.
CREATE OR REPLACE VIEW drugref.class_rule_member AS
WITH class_dag AS (
    SELECT * FROM drugref.ci_class_pair_subtree
),
subject_side AS (
    SELECT DISTINCT cci.curated_class_interaction_id,
           sub_m.moiety_uuid
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
    AND    (s.class_uuid = cci.subject_class_uuid
            OR (a.expands_descendants AND COALESCE(p.decision, 'allow') <> 'deny'))
),
partner_side AS (
    SELECT DISTINCT ON (cci.curated_class_interaction_id, obj_m.moiety_uuid)
           cci.curated_class_interaction_id,
           obj_m.moiety_uuid,
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
    AND    (s.class_uuid = cci.object_class_uuid
            OR (a.expands_descendants AND COALESCE(p.decision, 'allow') <> 'deny'))
    ORDER  BY cci.curated_class_interaction_id, obj_m.moiety_uuid,
              (s.class_uuid = cci.object_class_uuid) DESC, s.class_uuid
)
SELECT curated_class_interaction_id, 'subject'::text AS side, moiety_uuid,
       NULL::uuid AS member_class, NULL::boolean AS is_direct
FROM   subject_side
UNION ALL
SELECT curated_class_interaction_id, 'partner', moiety_uuid, member_class, is_direct
FROM   partner_side;

COMMENT ON VIEW drugref.class_rule_member IS
    'Every drug a LIVE curated_class_interaction row reaches, one row per (rule, side, '
    'moiety): side = ''subject'' for its subject class''s members, ''partner'' for its '
    'object class''s. Membership is on the rule axis''s membership relationship; '
    'descendant classes count only when the axis expands AND the named class''s '
    'expansion policy is not deny (direct members always count). member_class / '
    'is_direct describe the partner''s route into the class (direct first) and are NULL '
    'on the subject side. INCLUDES applies = false rulings: curated_ddi_pair filters '
    'those itself, and drugref interactions'' orphan banner needs them (db/079, #255). '
    'DOES NOT require the rule''s candidate to exist, which is the point: a rule whose '
    'class_pair_contraindication row a re-ingest dropped still says whom it concerned. '
    'Lifted from curated_ddi_pair''s db/038 CTEs, which now read this view.';


-- ============================================================================
-- 3. curated_ddi_pair -- the class half reads class_rule_member
-- ============================================================================
-- db/038's body with the three CTEs replaced by ONE (`member`, referenced twice, so
-- Postgres materialises it and the walk runs once, as `class_dag` did). The
-- moiety-grain half, every output column, and the class half's WHERE -- including
-- `cci.applies` -- are byte-for-byte db/038's. Column list identical, so
-- curated_ddi_pair_effective needs no rebuild and CREATE OR REPLACE keeps every
-- COMMENT, which is why none is re-issued here.
CREATE OR REPLACE VIEW drugref.curated_ddi_pair AS
WITH member AS (
    SELECT * FROM drugref.class_rule_member
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
       sk.severity_rank,
       COALESCE(sk.severity_rank, 0)::smallint AS effective_rank
FROM   drugref.ddi_candidate_pair p
JOIN   drugref.curated_interaction c
       ON  c.subject_moiety_uuid = p.subject_moiety
       AND c.object_class_uuid   = p.via_class
       AND c.relationship        = p.relationship
LEFT   JOIN drugref.curated_signature_status ss
       ON  ss.target_kind = 'curated_interaction'
       AND ss.target_id   = c.curated_interaction_id
LEFT   JOIN drugref.severity_kind sk ON sk.severity = c.severity
WHERE  c.superseded_by IS NULL
AND    c.applies

UNION ALL

-- ---- half 2: the class grain ---------------------------------------------------
SELECT sm.moiety_uuid         AS subject_moiety,
       pm.moiety_uuid         AS partner_moiety,
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
       sk.severity_rank,
       COALESCE(sk.severity_rank, 0)::smallint AS effective_rank
FROM   drugref.curated_class_interaction cci
JOIN   member sm ON sm.curated_class_interaction_id = cci.curated_class_interaction_id
                AND sm.side = 'subject'
JOIN   member pm ON pm.curated_class_interaction_id = cci.curated_class_interaction_id
                AND pm.side = 'partner'
JOIN   drugref.class_pair_contraindication cpc
       ON  cpc.subject_class_uuid = cci.subject_class_uuid
       AND cpc.object_class_uuid  = cci.object_class_uuid
       AND cpc.relationship       = cci.relationship
JOIN   drugref.ingest_run r ON r.ingest_run_id = cpc.ingest_run
LEFT   JOIN drugref.curated_signature_status ss
       ON  ss.target_kind = 'curated_class_interaction'
       AND ss.target_id   = cci.curated_class_interaction_id
LEFT   JOIN drugref.severity_kind sk ON sk.severity = cci.severity
WHERE  cci.superseded_by IS NULL
AND    cci.applies
AND    sm.moiety_uuid <> pm.moiety_uuid;

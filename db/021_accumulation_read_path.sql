-- db/021_accumulation_read_path.sql
-- Plan C's OUTPUT CONTRACT (spec 8): what drugref publishes, and what a consumer may
-- rely on.
--
-- drugref publishes FACTS AND THRESHOLDS, never verdicts. These two views flatten the
-- curated model into effect -> moiety -> grade and group -> role -> moiety; the
-- CONSUMER intersects them with a patient's regimen and applies additive_effect's
-- thresholds. That keeps the global tier stateless and free of patient data, matches
-- the read-time-expansion pattern ddi_candidate_pair already set, and lets a consumer
-- cache the facts. The evaluation itself is a set intersection and a count.

-- ============================================================================
-- 1. class_subtree -- the SECOND walk down the class DAG, and why it is second
-- ============================================================================
-- db/012's ci_class_subtree calls itself "THE ONE PLACE drugref WALKS THE CLASS DAG".
-- That stops being true here, and the honest thing is to say so and give the reason
-- rather than let a catalog comment quietly go false -- which is the defect the Plan
-- B review round found five instances of.
--
-- THE REASON IS MEASURED, not aesthetic. ci_class_subtree is scoped to the 104 classes
-- a contraindication actually NAMES, and that scoping is what makes it cheap. Against
-- the real release (UNII 26Feb2026 + MED-RT 2026.07.06 + MeSH 2026, 4,202 classes /
-- 4,510 edges):
--
--     ci_class_subtree, roots = the 104 CI object classes    1,233 rows   3.6 ms
--     full closure, roots = all 4,202 classes               22,754 rows  18.8 ms
--                                       (both give a byte-identical ddi_candidate_pair
--                                        of 21,664 rows -- purely a cost difference)
--
-- The 3.6/18.8 ms figures are a FILTERED pair lookup, the realistic hot path. So
-- re-expressing ci_class_subtree as a filter over a general closure would cost the
-- contraindication read path 5x for no change in what it returns, and would deepen
-- issue #37 rather than pay it down. It keeps its roots; this is a second view.
--
-- WHY THIS ONE IS NOT ROOT-SCOPED TOO. A curated-root walk would be cheaper here, but
-- gap_uncurated_additive_effect (db/022) has to measure subtree size for the 1,873 PE
-- classes NOBODY HAS CURATED YET -- a discovery view's roots are by definition the
-- classes absent from the curated tables -- so a curated-root walk cannot serve it and
-- the alternative is a THIRD recursion. 18.5 ms is affordable for a view read once per
-- ingest, and acceptable for a read path shipping with an empty curation set. If that
-- read path ever gets hot, root-scoping it is the known fix and the numbers above are
-- the argument for it.
CREATE OR REPLACE VIEW drugref.class_subtree AS
WITH RECURSIVE subtree(root_uuid, class_uuid) AS (
    SELECT class_uuid, class_uuid FROM drugref.substance_class
  UNION
    SELECT s.root_uuid, cp.child_class_uuid
    FROM   subtree s
    JOIN   drugref.class_parent cp ON cp.parent_class_uuid = s.class_uuid
)
SELECT root_uuid, class_uuid FROM subtree;

COMMENT ON VIEW drugref.class_subtree IS
    'Every class, and every class at or below it in the parent DAG. THE ROOT IS '
    'INCLUDED IN ITS OWN SUBTREE. Deduped on (root, class) rather than on paths, so it '
    'terminates under a cycle (db/002 forbids only self-parenting) and stays linear in '
    'a multi-parent DAG -- the same construction as db/012''s ci_class_subtree, which '
    'this deliberately does NOT replace: that view is scoped to the classes a '
    'contraindication names, and the scoping is what makes the hot pair lookup 3.6 ms '
    'instead of 18.8 ms. This one is unscoped because Plan C''s discovery views must '
    'see classes nobody has curated yet. 22,754 rows on the 2026.07.06 release.';
COMMENT ON COLUMN drugref.class_subtree.root_uuid IS
    'The class the walk started from. Every class in substance_class is a root here.';
COMMENT ON COLUMN drugref.class_subtree.class_uuid IS
    'A class at or below root_uuid. Equal to root_uuid for exactly one row per root.';

-- Re-issue db/012's comment, which this migration makes false. A merged migration is
-- immutable, so a correction to what it SAYS is a new COMMENT ON here (the same route
-- db/017 took for db/016's view).
COMMENT ON VIEW drugref.ci_class_subtree IS
    'For every class a contraindication NAMES: that class and every class below it in '
    'the parent DAG. THE ROOT IS INCLUDED IN ITS OWN SUBTREE -- ddi_candidate_pair''s '
    '`is_direct` and gap_unreviewed_expansion_root''s `count(*) - 1` both depend on '
    'it. Deduped on (root, class) rather than on paths, so it terminates under a '
    'cycle (db/002 forbids only self-parenting) and stays linear in a multi-parent '
    'DAG. Scoped to contraindicated classes: a class no rule names is ABSENT, not '
    'present with only itself. ONE OF TWO class-DAG walks since db/021 -- this one '
    'serves the CONTRAINDICATION read path (ddi_candidate_pair, '
    'gap_unreviewed_expansion_root, gap_unpopulated_contraindication) and class_subtree '
    'serves Plan C. THEY ARE NOT MERGED ON PURPOSE: this view''s narrow root set is '
    'what makes a filtered pair lookup 3.6 ms, against 18.8 ms for the identical row '
    'set computed as a filter over the unscoped closure. Do not "simplify" one into '
    'the other without re-measuring that.';

-- ============================================================================
-- 2. additive_effect_contributor -- the flattened fact table (spec 8)
-- ============================================================================
-- WHAT THIS VIEW IS BUILT TO MAKE IMPOSSIBLE. effect_contribution does not LIST
-- contributors, it REGRADES them (spec 5.2), and the opposite reading is the one an
-- implementer reaches for. So the contributor set is computed FROM MEMBERSHIP FIRST
-- and promotions are LEFT JOINed onto it: a promoted class sharing no member with the
-- effect can change a grade it already reaches and can never add a moiety. The rule
-- is structural here rather than a WHERE clause somebody can drop.
--
-- THE CONFLICT RULE IS PART OF THE CONTRACT (spec 8), not an implementation detail.
-- One moiety can reach one effect through several promoted classes -- aspirin is a
-- member of Decreased Platelet Aggregation and may also sit in a promoted EPC class.
-- Since the whole downstream evaluation is COUNT THE CONTRIBUTORS, a moiety emitted
-- twice is the difference between firing and not firing at threshold_total = 2: one
-- drug counted as two. So the view is UNIQUE on (effect_class_uuid, moiety_uuid) and
-- takes max(magnitude) with major > minor. `major` winning is the safety-preserving
-- direction and matches what a curator means -- promoting a class asserts that THESE
-- members matter more, never that anything already promoted matters less.
CREATE OR REPLACE VIEW drugref.additive_effect_contributor AS
WITH effect_member AS (
    -- Every moiety at or below the effect class. Membership relationship is NOT
    -- filtered: an axis is a property of the CLASS (a PE class is reached by has_PE),
    -- so the class already carries that information and filtering here would only be
    -- a second, driftable statement of it.
    SELECT DISTINCT
           e.additive_effect_id,
           e.effect_class_uuid,
           e.asserted_at,
           m.moiety_uuid,
           m.ingest_run AS membership_ingest_run
    FROM   drugref.additive_effect e
    JOIN   drugref.class_subtree s ON s.root_uuid = e.effect_class_uuid
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
    WHERE  e.superseded_by IS NULL
    AND    e.accumulates
),
promotion AS (
    SELECT DISTINCT
           c.effect_class_uuid,
           m.moiety_uuid,
           c.magnitude
    FROM   drugref.effect_contribution c
    JOIN   drugref.class_subtree s ON s.root_uuid = c.contributor_class_uuid
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
    WHERE  c.superseded_by IS NULL
)
SELECT em.effect_class_uuid,
       em.moiety_uuid,
       -- max(magnitude), spelled as a boolean fold because there are exactly two
       -- grades and 'major' < 'minor' alphabetically -- a plain max() would be
       -- silently backwards.
       CASE WHEN bool_or(p.magnitude = 'major') THEN 'major' ELSE 'minor' END AS magnitude,
       em.additive_effect_id,          -- join back for the thresholds and the note
       max(r.upstream_release)  AS upstream_release,   -- WHICH release the membership came from
       em.asserted_at                                  -- and when drugref asserted the effect
FROM   effect_member em
LEFT   JOIN promotion p
       ON  p.effect_class_uuid = em.effect_class_uuid
       AND p.moiety_uuid       = em.moiety_uuid
JOIN   drugref.ingest_run r ON r.ingest_run_id = em.membership_ingest_run
GROUP  BY em.effect_class_uuid, em.moiety_uuid, em.additive_effect_id, em.asserted_at;

COMMENT ON VIEW drugref.additive_effect_contributor IS
    'THE OUTPUT CONTRACT (spec 8): every moiety that contributes to a curated additive '
    'effect, and at what grade. UNIQUE ON (effect_class_uuid, moiety_uuid) and a '
    'consumer may rely on that -- the evaluation is a COUNT, so one drug emitted twice '
    'is the difference between firing and not firing. Where a moiety is promoted '
    'through several classes, max(magnitude) wins with major > minor. drugref does NOT '
    'evaluate: read additive_effect for thresholds and clinical note, intersect this '
    'with the patient''s regimen, count. CANDIDATE TIER -- a threshold being met is an '
    'input to review, never a rendered warning -- and ABSENCE CARRIES NO INFORMATION: '
    'an effect nobody has curated has no rows here, which is not evidence that the '
    'drugs are safe together.';
COMMENT ON COLUMN drugref.additive_effect_contributor.magnitude IS
    'DEFAULT MINOR. Every member of the effect class (including DAG descendants) is a '
    'contributor; a live effect_contribution row promotes some of them to major. A '
    '`minor` here therefore means EITHER "reviewed and genuinely minor" OR "nobody has '
    'looked" -- the two are indistinguishable in this column BY DESIGN, and '
    'gap_ungraded_contribution is where the difference is answerable.';
COMMENT ON COLUMN drugref.additive_effect_contributor.additive_effect_id IS
    'The LIVE assertion this row was computed from -- join back for thresholds, '
    'severity and clinical note. Superseded assertions contribute nothing here.';

-- ============================================================================
-- 3. interaction_group_member_moiety -- the role side of the contract
-- ============================================================================
-- A group fires when the regimen covers EVERY DISTINCT role among its live members.
-- That last step is the consumer's, for the same reason accumulation's is: drugref
-- publishes the facts and stays free of patient data.
--
-- ROLES EXPAND OVER DESCENDANTS, and the spec is silent so db/021 states it: a role
-- inherits down the DAG for the same reason a grade does (curate once, apply widely --
-- filing 'NSAID' should not depend on where MED-RT happens to file celecoxib), and
-- because db/010 settled the safety direction for advisory data: a group that fires is
-- an input to review, so missing a member is the harm direction.
CREATE OR REPLACE VIEW drugref.interaction_group_member_moiety AS
SELECT DISTINCT
       gm.group_uuid,
       gm.role,
       m.moiety_uuid,
       gm.class_uuid AS via_class,     -- the class the CURATOR named
       r.upstream_release,
       gm.asserted_at
FROM   drugref.interaction_group_member gm
JOIN   drugref.class_subtree s ON s.root_uuid = gm.class_uuid
JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
JOIN   drugref.ingest_run r ON r.ingest_run_id = m.ingest_run
WHERE  gm.superseded_by IS NULL
AND    gm.satisfies_role;

COMMENT ON VIEW drugref.interaction_group_member_moiety IS
    'Which moieties satisfy which ROLE in an interaction group -- live members only. A '
    'group fires when the regimen covers EVERY DISTINCT role here for that group; two '
    'drugs satisfying the SAME role do not cover a second one, which is the whole '
    'reason groups exist beside accumulation. A role with no live true member is '
    'simply ABSENT, so a consumer is never handed a role nothing can satisfy. Roles '
    'expand over DAG descendants (spec is silent; db/021 decides it) for curation '
    'economy and because missing a member is the harm direction for advisory data. '
    'CANDIDATE TIER: covering every role is an input to review, not a warning.';
COMMENT ON COLUMN drugref.interaction_group_member_moiety.via_class IS
    'The class the CURATOR filed under this role. The moiety may be a member of a '
    'descendant of it rather than of it directly.';

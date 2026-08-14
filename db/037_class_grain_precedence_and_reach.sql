-- db/037_class_grain_precedence_and_reach.sql -- the three follow-ups db/035's own
-- review round filed against it: #108, #109, #110.
--
-- ONE MIGRATION, THREE ISSUES, for db/035's own reason: they are three views in one
-- subsystem, all reported by the same review, and splitting them would make three
-- migrations that must be applied together anyway. db/036 corrected what db/035 SAID;
-- this one corrects what it DOES.
--
-- WHY NOW, AND WHY THIS IS THE CHEAP MOMENT. `class_pair_contraindication` is EMPTY on
-- every database in existence -- issue #94 withheld the seven class x class ONC entries
-- pending literature research, and nothing else writes the grain -- so all three
-- changes are provably content-neutral today and every count stays byte-identical.
-- The first slice that POPULATES the class grain (DrugCentral or SPL/DailyMed, whichever
-- lands) is the moment these stop being free, and #112 is already open to measure the
-- self-join before that happens. Fixing arithmetic before there is arithmetic to break
-- is the whole argument for doing it in a debt round rather than in the slice.
--
-- NO NEW TABLE, NO NEW COLUMN ON ANY TABLE, NO PL/pgSQL. Three CREATE OR REPLACE VIEWs
-- and the COMMENTs that go with them. `class_pair_rule_reach` gains one trailing column,
-- which CREATE OR REPLACE permits (appended last, existing positions untouched).

-- ============================================================================
-- 1. class_pair_rule_reach -- max_pair_count becomes exact (#108)
-- ============================================================================
-- THE DEFECT, as db/036's corrected COMMENT already states it: `max_pair_count` was
-- `subject_effective_member_count * object_effective_member_count`, while
-- `curated_ddi_pair` additionally requires `sm.subject_moiety <> pm.partner_moiety`.
-- db/032 DECISION 2 deliberately permits a class to pair with ITSELF (QT-prolonging x
-- QT-prolonging is a real ONC entry), so for a self-pair rule over a class with N
-- effective members the true reach is N*(N-1). AT N = 1 THE PRODUCT READS 1 AND THE READ
-- PATH YIELDS 0.
--
-- BOTH DETECTORS INVERT ON THAT ROW, which is what makes it worth a migration rather
-- than a caveat. `gap_uncurated_class_interaction_rule`'s `HAVING max(max_pair_count) >
-- 0` ADMITS the rule to the curator worklist and mints it an immortal question_uuid
-- asking about "up to 1 drug pair(s)" -- #36's measured mistake, a review gate asking
-- what no answer could change. `drugref status`'s `WHERE max_pair_count = 0` OMITS it,
-- so the rule stays "ingested, graded, committed and reported successful while reaching
-- zero patients" -- the exact failure db/035 is named for, reproduced by the detector
-- built to catch it. Neither reader changes here: both are filters over this column, so
-- correcting the column corrects both at once, which is why db/035 made this "THE ONE
-- PLACE the class grain states a rule's reach".
--
-- THE ARITHMETIC. |S x O| minus the pairs the read path excludes, and the excluded
-- pairs are exactly those where the two sides name the SAME moiety -- one per member of
-- the intersection. So `|S| * |O| - |S INTERSECT O|`. The self-pair rule is the
-- reachable special case (S = O gives N*N - N), not a case handled separately: a rule
-- over two DIFFERENT classes that happen to share members overstates by the overlap in
-- exactly the same way, and MED-RT files one drug under many classes, so shared
-- membership between two related classes is ordinary rather than exotic.
--
-- WHY THIS NEEDS MEMBER SETS AND NOT JUST COUNTS. An intersection size cannot be
-- recovered from two cardinalities; the CTEs therefore carry `array_agg(DISTINCT
-- moiety_uuid)` beside their counts. That is not a new technique here --
-- `ci_rule_partner_reach` (db/018) already aggregates the same array for the same
-- family of reason, and its `subject_moiety_uuid = ANY (st.members)` is the moiety
-- grain's version of this exclusion, one side instead of two. THE COUNTS ARE STILL
-- COUNTED, not derived from `cardinality(members)`: `count(DISTINCT ...)` and
-- `array_agg(DISTINCT ...)` are the same population by construction, and deriving one
-- from the other would put one quantity in two shapes.
--
-- COST: unmeasured, and honestly so. The arrays and the LATERAL are read once per
-- candidate rule, over a table holding ZERO rows today and ~9 when #94's entries land.
-- There is nothing to measure yet; #112 owns the measurement of this grain's queries
-- against real content, and this column's consumers (`status`, one gap view) are not on
-- any hot path.
CREATE OR REPLACE VIEW drugref.class_pair_rule_reach AS
WITH subtree_member AS (
    -- Everything filed anywhere at or below a class the class grain names, per
    -- (root, axis) -- db/035's own CTE, plus the member array section 1's header
    -- explains. One aggregate for both sides: the subject side and the object side
    -- walk the SAME view, so counting them separately would pay for the recursive
    -- walk twice.
    SELECT s.root_uuid,
           m.relationship                AS membership_relationship,
           count(DISTINCT m.moiety_uuid) AS member_count,
           array_agg(DISTINCT m.moiety_uuid) AS members
    FROM   drugref.ci_class_pair_subtree s
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
    GROUP  BY s.root_uuid, m.relationship
),
direct_member AS (
    -- ...and the subset filed ON the class itself, which is all a DENIED rule
    -- reaches. No recursion: a plain aggregate over class_membership.
    SELECT m.class_uuid,
           m.relationship                AS membership_relationship,
           count(DISTINCT m.moiety_uuid) AS member_count,
           array_agg(DISTINCT m.moiety_uuid) AS members
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
           -- EMPTY ARRAY, NEVER NULL, for the same reason the counts COALESCE to 0: an
           -- unpopulated class must intersect to zero rather than propagate a NULL into
           -- max_pair_count, which would make an unreachable rule read as UNKNOWN reach
           -- and drop out of both detectors' filters at once.
           COALESCE(ss.members, '{}'::uuid[]) AS subject_subtree_members,
           COALESCE(sd.members, '{}'::uuid[]) AS subject_direct_members,
           COALESCE(os.members, '{}'::uuid[]) AS object_subtree_members,
           COALESCE(od.members, '{}'::uuid[]) AS object_direct_members,
           -- A denied class expands to its DIRECT members only; an unreviewed or
           -- allowed one expands over the subtree. COALESCE makes "no policy row"
           -- expand, matching ddi_candidate_pair and db/034 exactly -- unreviewed is
           -- the safe default and db/035 section 4 is what reports it.
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
),
effective AS (
    -- The two CASEs db/035 wrote INLINE, three times each, named once instead. Not
    -- tidiness: section 1's arithmetic needs the effective member SET as well as its
    -- size, and a fourth and fifth copy of the same CASE is how the set and the count
    -- would come to disagree about which side is expanding.
    SELECT sided.*,
           CASE WHEN subject_expands THEN subject_subtree_member_count
                ELSE subject_direct_member_count END AS subject_effective_member_count,
           CASE WHEN object_expands  THEN object_subtree_member_count
                ELSE object_direct_member_count  END AS object_effective_member_count,
           CASE WHEN subject_expands THEN subject_subtree_members
                ELSE subject_direct_members END      AS subject_effective_members,
           CASE WHEN object_expands  THEN object_subtree_members
                ELSE object_direct_members  END      AS object_effective_members
    FROM   sided
)
SELECT e.subject_class_uuid,
       e.object_class_uuid,
       e.relationship,
       e.source,
       e.ingest_run,
       e.membership_relationship,
       e.expands_descendants,
       e.subject_subtree_member_count,
       e.subject_direct_member_count,
       e.object_subtree_member_count,
       e.object_direct_member_count,
       e.subject_effective_member_count,
       e.object_effective_member_count,
       -- THE COLUMN ORDER ABOVE IS db/035's, POSITION FOR POSITION, and it has to be:
       -- CREATE OR REPLACE VIEW may append columns at the end and may not move one.
       e.subject_effective_member_count * e.object_effective_member_count
           - shared.member_count AS max_pair_count,
       shared.member_count AS shared_effective_member_count
FROM   effective e
CROSS  JOIN LATERAL (
    -- |S INTERSECT O|, the pairs the read path's `subject_moiety <> partner_moiety`
    -- removes -- one per moiety on both sides. CROSS, not LEFT: `count(*)` over an
    -- empty scan is 0, never no row, so this cannot drop a rule.
    SELECT count(*)::bigint AS member_count
    FROM   unnest(e.subject_effective_members) AS s(moiety_uuid)
    WHERE  s.moiety_uuid = ANY (e.object_effective_members)
) shared;

-- Re-issued for db/027's precedent (an applied migration's view BODY may be corrected,
-- its FILE never rewritten): db/036's wording exists only to warn that the bound was
-- inexact, and repeating that warning now would be the false claim in the other
-- direction.
COMMENT ON VIEW drugref.class_pair_rule_reach IS
    'Per CLASS x CLASS rule: how many drugs each side could pair with, counted over '
    'the class subtree, over direct members only, and EFFECTIVELY (subtree or direct '
    'according to today''s class_expansion_policy, using db/034''s own predicate). '
    'ci_rule_partner_reach''s class-grain sibling -- and a PRODUCT rather than a '
    'single count, because a class x class rule expands on BOTH sides. THE ONE PLACE '
    'the class grain STATES A RULE''S REACH: gap_uncurated_class_interaction_rule and '
    'drugref status are complementary filters over max_pair_count, so the two agree by '
    'construction and correcting the column corrects both. `max_pair_count` is now '
    'EXACT for the expansion (db/037, issue #108): it subtracts '
    'shared_effective_member_count, the two sides'' shared membership, because the read '
    'path excludes a drug pairing with ITSELF. db/035 shipped the bare product and '
    'db/036 corrected the comment to say so; for a SELF-PAIR rule (db/032 DECISION 2 '
    'permits one) over a class with N members the reach is N*(N-1), which at N=1 is '
    'ZERO where the product read 1 -- so that rule was BOTH queued as a pointless '
    'curator question AND hidden from the operator''s dead-rule line. A 0 on either '
    'side is issue #92''s mixed-kind shape ([MoA] x [EPC], where one axis cannot select '
    'both memberships) made visible, and an unpopulated class besides. Walks '
    'ci_class_pair_subtree, NEVER ci_class_subtree -- db/034 separated them after a '
    'merged walk was measured to tax every moiety-grain query ~3.6x, and issue #100 is '
    'open about a stray migration replay re-merging them by accident.';
COMMENT ON COLUMN drugref.class_pair_rule_reach.max_pair_count IS
    'subject_effective_member_count * object_effective_member_count MINUS '
    'shared_effective_member_count -- the distinct drug pairs this rule''s expansion '
    'reaches. EXACT since db/037 (issue #108); db/035 shipped the bare product, which '
    'overstated wherever the two sides share membership and read 1 for a self-pair rule '
    'over a one-member class that reaches nobody. Still an upper bound on ROWS in '
    'curated_ddi_pair, for a different reason that is not arithmetic: only a rule '
    'carrying a live, applies-true curated grade emits any row at all.';
COMMENT ON COLUMN drugref.class_pair_rule_reach.shared_effective_member_count IS
    'How many moieties are effective members of BOTH sides of this rule -- exactly the '
    'self-pairs curated_ddi_pair excludes, and therefore what max_pair_count subtracts. '
    'Equals the effective member count on a SELF-PAIR rule (db/032 DECISION 2), and is '
    'ordinarily non-zero for two related classes as well, since MED-RT files one drug '
    'under many classes. Published rather than folded in silently, so an operator '
    'reading a lower-than-expected max_pair_count can see WHY without re-deriving it.';

-- ============================================================================
-- 2. curated_grain_disagreement -- orientation-normalised join (#109)
-- ============================================================================
-- THE DEFECT. The view self-joins `curated_ddi_pair` on (subject_moiety,
-- partner_moiety, relationship), and db/006's own COMMENT on `ddi_candidate_pair`
-- states the convention these rows follow: 'DIRECTIONAL, not symmetric... A consumer
-- asking "do X and Y interact" MUST query both directions.' So a moiety rule emitting
-- (a, b) and a class rule stated the other way round emitting (b, a) are ONE clinical
-- pair on ONE axis carrying TWO grades, and the join returned nothing for them.
--
-- Nothing normalises orientation between `class_contraindication` and
-- `class_pair_contraindication`, and the two tiers come from DIFFERENT upstreams
-- (MED-RT vs ONCHigh), so orientation agreement was coincidence rather than invariant --
-- which is what made the omission a silent under-report rather than a known limit.
--
-- WHAT WAS AND WAS NOT AT RISK. The READ path was never affected: a consumer unioning
-- both directions still gets `ORDER BY severity_rank` and the more severe grade. What
-- under-reported is the reconciliation worklist -- and db/035's own comment calls that
-- worklist 'what keeps most-severe-wins from becoming permanent over-warning', so a
-- disagreement nobody is ever asked to reconcile is the failure the view exists to
-- prevent, arriving through the view itself.
--
-- LEAST/GREATEST, NOT AN `OR` OF TWO ARM PAIRS. Both express the same match; only one
-- is an equi-join. `ON (c.subj = m.subj AND c.part = m.part) OR (c.subj = m.part AND
-- c.part = m.subj)` cannot be hash-joined and degrades to a nested loop over the full
-- two-grain expansion -- on a view `drugref status` reads unfiltered on EVERY
-- invocation, and whose cost against real class-grain content is issue #112's open
-- question. Normalising each side to (lesser, greater) keeps it an equality join on two
-- expressions and covers both orientations in ONE arm rather than two.
--
-- SAFE ON uuid: uuid is totally ordered in Postgres, so LEAST/GREATEST are well-defined
-- and the normalisation is a bijection on unordered pairs. Self-pairs cannot reach here
-- at all -- curated_ddi_pair requires subject_moiety <> partner_moiety -- so
-- LEAST = GREATEST never happens and no row matches itself through the normalisation.
--
-- EVERY COLUMN AND THE GROUP BY ARE db/035's, unchanged: the grain is still the RULE
-- PAIR, not the drug pair, because two rules can overlap on thousands of pairs (SSRIs x
-- MAOIs alone is ~2,263) and one curator decision must not be reported thousands of
-- times. `class_rule_subject_class`/`class_rule_object_class` already name both of the
-- class rule's ends, so a curator can read which orientation it was stated in without a
-- new column saying so.
CREATE OR REPLACE VIEW drugref.curated_grain_disagreement AS
SELECT m.subject_moiety     AS moiety_rule_subject,
       m.via_class          AS moiety_rule_object_class,
       c.via_subject_class  AS class_rule_subject_class,
       c.via_class          AS class_rule_object_class,
       m.relationship,
       m.severity           AS moiety_severity,
       c.severity           AS class_severity,
       m.evidence_grade     AS moiety_evidence_grade,
       c.evidence_grade     AS class_evidence_grade,
       count(DISTINCT m.partner_moiety) AS overlapping_pair_count
FROM   drugref.curated_ddi_pair m
JOIN   drugref.curated_ddi_pair c
       ON  LEAST(c.subject_moiety, c.partner_moiety)
         = LEAST(m.subject_moiety, m.partner_moiety)
       AND GREATEST(c.subject_moiety, c.partner_moiety)
         = GREATEST(m.subject_moiety, m.partner_moiety)
       AND c.relationship = m.relationship
WHERE  m.rule_grain = 'moiety_rule'
AND    c.rule_grain = 'class_rule'
AND    (m.severity IS DISTINCT FROM c.severity
        OR m.evidence_grade IS DISTINCT FROM c.evidence_grade)
GROUP  BY m.subject_moiety, m.via_class, c.via_subject_class, c.via_class,
          m.relationship, m.severity, c.severity,
          m.evidence_grade, c.evidence_grade;

-- Re-issued: db/036 listed orientation as the SECOND of two deliberate omissions, and
-- it is no longer one. One omission remains and it is still named, because a comment
-- that enumerates what it does not cover is read as exhaustive.
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
    'carries the size instead. ORIENTATION-BLIND since db/037 (issue #109): these rows '
    'are DIRECTIONAL per db/006, the two candidate tiers come from different upstreams, '
    'and a moiety rule on (a,b) with a class rule on (b,a) is ONE clinical pair with '
    'two grades -- so the join normalises each side to LEAST/GREATEST rather than '
    'matching subject to subject. ONE SHAPE IT STILL DOES NOT COVER: two MOIETY-grain '
    'rules on different axes (issue #106 -- two statements about two mechanisms, which '
    'is why the join matches on relationship; measured at 46 of 21,370 candidate pairs '
    'reachable on two axes and NONE of them graded, so it is not yet live). An '
    'OPERATOR view rather than a gap kind FOR NOW: it is a question drugref answers '
    'itself and so a fair candidate, but a gap_key is frozen forever and no class-grain '
    'content ships yet, so the key''s grain would be chosen against no real instance. '
    'Answer a row by superseding one of the two rulings, or by recording why both '
    'stand.';

-- ============================================================================
-- 3. curated_ddi_pair_effective -- the precedence becomes a view (#110)
-- ============================================================================
-- THE DEFECT db/035 LEFT BEHIND. db/035 described what it was fixing as: 'A client
-- doing `SELECT severity ... LIMIT 1` got an arbitrary answer, AND WHICHEVER IT TOOK
-- MIGHT BE THE LOWER ONE.' What shipped was a severity_rank COLUMN. `curated_ddi_pair`
-- still had no ORDER BY, there was no wrapper view, and nothing in src/ read
-- severity_rank at all -- so drugref never applied its own precedence and no test could
-- regress it. A client that did not change its query was affected exactly as before.
-- The migration consolidated the ORDINAL and left the ORDERING RULE in prose, which is
-- the same anti-pattern severity_kind was created to fix, one level up.
--
-- THE SAFE READ IS NOW THE DEFAULT READ. Every client had to retype
-- `ORDER BY severity_rank, (rule_grain = 'moiety_rule') DESC` correctly, DESC included,
-- from a comment. Now they select from this view instead. `curated_ddi_pair` is
-- unchanged and remains the honest one: it STATES BOTH GRADES, because dropping one
-- would make it say less than it knows, and curated_grain_disagreement (section 2) is
-- what stops the losing grade being forgotten rather than merely outranked.
--
-- NULLS FIRST, AND THIS IS THE HALF THAT WAS BACKWARDS. Postgres sorts `ORDER BY x ASC`
-- with NULLs LAST, and severity_rank 1 is MOST severe -- so an unrankable severity
-- sorted BELOW `minor` and a LIMIT 1 client would never see it. db/035 argued its
-- defensive LEFT JOIN to severity_kind 'makes it harmless if it ever became reachable
-- again'; it keeps the row but out-ranks it by everything, which is UNDER-warning, and
-- under-warning is the harm direction on this path -- the same reason a signature never
-- gates a read and a missing expansion policy expands. NULLS FIRST makes the claim
-- true. Unreachable today (both halves of curated_ddi_pair filter `AND applies`, the
-- completeness CHECKs force applies => severity IS NOT NULL, and severity is a FK into
-- severity_kind), so it is pinned by dropping that FK inside a rolled-back transaction --
-- this project's rule that a branch the release cannot exercise is pinned on controlled
-- input and verified by mutation.
--
-- THE TIE-BREAK AFTER THE PRECEDENCE IS DETERMINISM, NOT A CLINICAL PREFERENCE, and the
-- distinction matters: `severity_rank` then `moiety-grain-first` is the published rule
-- and everything after it exists only so DISTINCT ON picks the same row twice running.
-- Without it two rows agreeing on both precedence keys -- one rule asserted by two
-- authorities is the ordinary way that happens, since curated_ddi_pair carries
-- candidate_source -- would resolve arbitrarily, which is the flake curation.
-- unresolved_targets and keys.all_live each had to fix once already.
CREATE OR REPLACE VIEW drugref.curated_ddi_pair_effective AS
SELECT DISTINCT ON (subject_moiety, partner_moiety, relationship) *
FROM   drugref.curated_ddi_pair
ORDER  BY subject_moiety, partner_moiety, relationship,
          severity_rank NULLS FIRST,
          (rule_grain = 'moiety_rule') DESC,
          candidate_source, via_class, member_class, reviewed_at, reviewed_by;

COMMENT ON VIEW drugref.curated_ddi_pair_effective IS
    'ONE ROW PER (subject_moiety, partner_moiety, relationship) -- curated_ddi_pair '
    'with db/035''s precedence APPLIED rather than described. THE SAFE READ, and the '
    'one a prescribing client should use: curated_ddi_pair states both grades when the '
    'two grains disagree, so a client doing `SELECT severity ... LIMIT 1` over it got '
    'an arbitrary answer and it might be the LOWER one. THE RULE IS ORDER BY '
    'severity_rank NULLS FIRST, (rule_grain = ''moiety_rule'') DESC -- most severe '
    'first, the moiety grain breaking ties because a rule naming an actual drug carries '
    'better mechanism/management text than one naming its whole class. NULLS FIRST '
    'rather than Postgres''s default NULLS LAST (issue #110): rank 1 is most severe, so '
    'an unrankable severity must sort ABOVE contraindicated, not below minor where a '
    'LIMIT 1 client would never see it -- under-warning is the harm direction here. '
    'Anything after those two keys is a determinism tie-break, NOT a clinical '
    'preference: one rule asserted by two authorities is two rows agreeing on both '
    'precedence keys, and DISTINCT ON must pick the same one every time. STILL '
    'DIRECTIONAL (db/006): a consumer asking "do X and Y interact" queries BOTH '
    'directions here too. Read curated_ddi_pair itself to see what was outranked, and '
    'curated_grain_disagreement for the rule pairs a curator should reconcile -- '
    'most-severe-wins is safe only while that worklist is worked.';

-- Re-issued to name the view above. db/035's own text already stated the precedence
-- and called it "an ORDER, NOT A FILTER"; what it could not say is where to get it
-- applied, because there was nowhere.
COMMENT ON VIEW drugref.curated_ddi_pair IS
    'Drug pairs carrying a live drugref grade, from EITHER of two rule grains -- '
    '`rule_grain` says which (''moiety_rule'' | ''class_rule''), and `via_subject_class` '
    'names the class-grain rule''s subject (NULL for a moiety rule). ONE PAIR CAN APPEAR '
    'TWICE, once per grain, with different grades: both rulings are live, they sit in '
    'different tables, and each satisfies its own single-live guard, so no constraint '
    'can prevent it. THE PRECEDENCE IS `ORDER BY severity_rank NULLS FIRST, (rule_grain '
    '= ''moiety_rule'') DESC` -- MOST SEVERE FIRST, moiety grain breaking ties (the rule '
    'naming an actual drug carries better mechanism/management text than one naming its '
    'whole class). Severity-first because under-warning is the harm direction on this '
    'path, the same reason a signature never gates a read and a missing expansion policy '
    'expands. IT IS AN ORDER, NOT A FILTER: both rows still appear here, because '
    'dropping one would make this view state less than it knows. SINCE db/037 THE ORDER '
    'IS ALSO A VIEW -- curated_ddi_pair_effective (issue #110) applies it, so a client '
    'no longer has to retype it correctly from a comment; this view remains the place '
    'to see what was outranked. A disagreement is not left standing -- '
    'curated_grain_disagreement (db/035, orientation-blind since db/037) lists them for '
    'reconciliation, which is what keeps most-severe-wins from becoming permanent '
    'over-warning. A moiety-grain row is expanded from the class-level rule the grade '
    'was written against, so ONE curated row reaches every pair its rule expands to; a '
    'class-grain row is expanded on BOTH sides, so one row can reach thousands of pairs '
    '(SSRIs x MAOIs alone is ~2,263). INNER JOIN to the overlay throughout: an ungraded '
    'candidate does not appear here at all, because a NULL severity beside a real pair '
    'reads as "reviewed and harmless". ddi_candidate_pair remains the place to ask what '
    'a moiety-grain release said; class_pair_contraindication is the class grain''s own '
    'candidate tier. Each grain walks the class DAG through its OWN view '
    '(ci_class_subtree / ci_class_pair_subtree, db/034) -- SEPARATED, after a merged '
    'walk was measured to tax every moiety-grain query for class-grain content most '
    'callers do not have. signature_status is REGISTRY-LEVEL ONLY and its join is LEFT '
    'for both halves: an unsigned row still appears, labelled ''unsigned'', and a key '
    'revocation relabels a row rather than removing it.';

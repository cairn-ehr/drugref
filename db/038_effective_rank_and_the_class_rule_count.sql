-- db/038 -- the PR #113 follow-up round: issues 116 and 117.
--
-- WHY A NEW FILE RATHER THAN AN EDIT TO db/037. db/037 merged with PR #113 on
-- 2026-08-15, and immutability starts at MERGE (PROJECT-NOTES § repo facts): the
-- ledger records each file's checksum and `apply_migrations` raises if an applied file
-- changed. The licence db/037 itself exercised -- an unmerged migration may be edited,
-- because the ledger binds a DATABASE and not the repo -- expired when it merged.
--
-- THREE SECTIONS, ONE THEME -- text and arithmetic this project already published,
-- corrected. §1: db/037 fixed the ORDER an unrankable severity sorts in and left what
-- the client actually RECEIVES untouched. §2: that state reaches an OPERATOR, because a
-- mitigation nothing reports is a mitigation that hides its own trigger. §3: db/035
-- stated a figure in a COMMENT that the source data contradicts.
--
-- §1 AND §2 BOTH CHANGE WHAT A CONSUMER READS -- §1 adds a column every client should
-- threshold on, and §2 adds a view `drugref status` reads on every run. Only §3 is
-- purely catalog prose. (An earlier draft of this header said TWO SECTIONS and numbered
-- the figure correction §2, which is §3; the paragraph counting the file's own sections
-- disagreed with the file, in a round whose §3 exists because a comment disagreed with
-- the data. Corrected in review of PR #119.)


-- ============================================================================
-- 1. effective_rank -- an unrankable severity must be LOUD, not ABSENT (#116)
-- ============================================================================
-- WHAT db/037 GOT RIGHT AND MUST KEEP. Postgres sorts `ORDER BY x ASC` NULLS LAST, and
-- severity_rank 1 is MOST severe, so an unrankable severity sorted BELOW `minor` and a
-- LIMIT 1 client never saw it. `NULLS FIRST` fixed that, and the argument behind it --
-- under-warning is the harm direction on this path, the same reason a signature never
-- gates a read and a missing expansion policy expands -- is unchanged here.
--
-- WHAT IT LEFT OPEN, AND IT IS THE CONSEQUENTIAL HALF. Inside a `DISTINCT ON` the sort
-- key does not merely SHOW the unrankable row: it makes that row WIN, and the rankable
-- competitor is DISCARDED from the view. There is no second row to fall back to. The
-- client then receives severity_rank = NULL, and curated_read.GradedPair's own
-- docstring says what a client does with that column -- "a client that wants to
-- threshold ('warn at major or worse') needs the number". EVERY form of that threshold
-- drops a NULL: SQL `WHERE severity_rank <= 2` is UNKNOWN and filters the row out,
-- Python `g.severity_rank <= 2` raises TypeError, and the defensive
-- `g.severity_rank and g.severity_rank <= 2` is silently False.
--
-- SO db/037 TRADED ONE UNDER-WARNING FOR A WORSE ONE, which is visible only in the case
-- its test did not drive. Against a `minor` competitor (rank 4) the client at least
-- still received a severity WORD, so an "any grade at all" consumer fired. Against a
-- `contraindicated` competitor (rank 1) it receives a rank of NULL, and a numeric
-- consumer sees NOTHING. tests/test_effective_pair_precedence.py § 2 grades the
-- competitor `minor`; that is exactly why the suite could not see this.
--
--   competitor       | NULLS LAST (pre-db/037)  | NULLS FIRST (db/037)
--   -----------------+--------------------------+---------------------------------
--   minor      (r 4) | client sees `minor`      | client sees the unrankable word
--   contraindicated  | client sees rank 1       | threshold drops the pair ENTIRELY
--
-- THE FIX IS A SECOND COLUMN, NOT A CHANGED ONE, and the distinction is the whole
-- design. COALESCEing `severity_rank` itself would satisfy every threshold and DESTROY
-- the only evidence the schema is broken -- an unrankable severity would become
-- indistinguishable from a genuine rank 0, which is worse than the defect. So:
--
--   * `severity_rank` stays NULLABLE and unchanged. It is the honest report of a
--     severity absent from `severity_kind`, and a client that wants to see the FAULT
--     reads it.
--   * `effective_rank` = COALESCE(severity_rank, 0) is what the ordering and every
--     threshold use. 0 sits ABOVE contraindicated = 1, so the harm-direction argument
--     is preserved exactly, and 0 satisfies every `<= n` a client writes.
--
-- WHY 0 RATHER THAN -1 OR A SENTINEL LIKE 99. It has to sort above rank 1 (the
-- harm direction) AND satisfy `<= n` for every n a client thresholds on, and only a
-- value below the smallest real rank does both. 0 is the largest such value, so it
-- leaves room for nothing to be squeezed underneath it by accident; `severity_kind`'s
-- ranks start at 1 (db/035) and this migration does not change that.
--
-- REACHABILITY IS UNCHANGED AND STILL ZERO on a healthy database: both halves of
-- curated_ddi_pair filter `AND applies`, the completeness CHECKs force
-- applies => severity IS NOT NULL, and severity is a FOREIGN KEY into severity_kind.
-- This is a mitigation for the state § 2 below reports, pinned by mutation on
-- controlled input -- this project's rule for a branch the release cannot exercise.
--
-- APPENDED AT THE END OF BOTH HALVES, which is not a style choice: CREATE OR REPLACE
-- VIEW admits new columns only at the END, and curated_ddi_pair_effective depends on
-- this view. Adding it anywhere else would need a DROP CASCADE and a rebuild of every
-- dependant.

-- ---- the sentinel becomes a CONSTRAINT, not a promise -----------------------------
-- THE PARAGRAPH ABOVE ARGUED 0 IS SAFE BECAUSE "severity_kind's ranks start at 1", and
-- until this statement that was a COMMENT rather than a rule -- db/035 declared
-- severity_rank `smallint NOT NULL UNIQUE` and never bounded it below. The review of
-- PR #119 asked the question that matters: what stops a later migration adding a level
-- ABOVE contraindicated at rank 0? Nothing did.
--
-- AND THE COLLISION WOULD BE SILENT, which is why it is worth a constraint rather than
-- a sentence. A genuine rank 0 and an unrankable severity would both read
-- effective_rank = 0 -- destroying exactly the distinction §1 exists to preserve --
-- while curated_unrankable_severity stayed EMPTY, because that row IS in severity_kind.
-- `drugref status` would print `unrankable severities: none` over a live ambiguity.
--
-- ADDITIVE AND NON-NARROWING: the four seeded ranks are 1..4, so no existing row can
-- fail, and a fifth level added below `minor` is unaffected. The headroom this closes
-- is the one nobody wants: the space UNDERNEATH the sentinel.
ALTER TABLE drugref.severity_kind
    DROP CONSTRAINT IF EXISTS severity_kind_rank_is_positive;
ALTER TABLE drugref.severity_kind
    ADD CONSTRAINT severity_kind_rank_is_positive CHECK (severity_rank >= 1);

COMMENT ON CONSTRAINT severity_kind_rank_is_positive ON drugref.severity_kind IS
    'RANK 0 IS RESERVED as curated_ddi_pair.effective_rank''s sentinel for a severity '
    'this schema cannot rank (db/038, issue 116). A real severity at rank 0 would be '
    'indistinguishable from that fault on effective_rank, and curated_unrankable_'
    'severity would report NOTHING, since such a row is present in severity_kind. '
    'Added in review of PR #119: db/038 first argued the sentinel was safe because '
    '"severity_kind''s ranks start at 1", which was true and was not enforced.';

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
       -- LEFT, not INNER, and the four-row table makes that look pedantic. It is not:
       -- INNER here would let a severity this view cannot rank DELETE a row of
       -- clinical advice, and fewer rows is the harm direction. The foreign key in
       -- db/035 section 1 makes a miss unreachable; the LEFT makes it harmless if it
       -- ever became reachable again -- and `effective_rank` above is what makes that
       -- claim true all the way to the client, not merely to the sort.
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
       sk.severity_rank,
       COALESCE(sk.severity_rank, 0)::smallint AS effective_rank
FROM   drugref.curated_class_interaction cci
JOIN   class_rule_subject_member  sm ON sm.curated_class_interaction_id = cci.curated_class_interaction_id
JOIN   class_rule_partner_member  pm ON pm.curated_class_interaction_id = cci.curated_class_interaction_id
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
AND    sm.subject_moiety <> pm.partner_moiety;

COMMENT ON COLUMN drugref.curated_ddi_pair.severity_rank IS
    'The ORDINAL from severity_kind, 1 = most severe. NULLABLE, and a NULL is a SCHEMA '
    'FAULT rather than a grade: it means this row''s severity is absent from '
    'severity_kind, which the foreign key on both curated tables makes unreachable. '
    'READ THIS COLUMN TO SEE THE FAULT; threshold on effective_rank beside it, which '
    'never goes NULL. Kept nullable on purpose (db/038): COALESCEing it here would make '
    'a broken row indistinguishable from a real grade, destroying the only evidence the '
    'schema is wrong.';

COMMENT ON COLUMN drugref.curated_ddi_pair.effective_rank IS
    'COALESCE(severity_rank, 0) -- THE COLUMN TO THRESHOLD ON, and NOT NULL by '
    'construction (db/038, issue 116). An unrankable severity ranks 0, which sorts '
    'ABOVE contraindicated = 1 because under-warning is the harm direction here, and '
    'which satisfies every `<= n` a client writes -- `WHERE severity_rank <= 2` is '
    'UNKNOWN on a NULL and silently drops the very pair drugref judged more concerning. '
    'db/037 made an unrankable severity WIN inside curated_ddi_pair_effective''s '
    'DISTINCT ON, discarding the rankable competitor outright, so the NULL it published '
    'had no second row to fall back to.';

-- THE VIEW'S OWN COMMENT IS RE-ISSUED TOO, and forgetting it was the near-miss in this
-- migration's first draft. `CREATE OR REPLACE VIEW` PRESERVES comments, so db/037's text
-- survived above untouched -- and it says `THE PRECEDENCE IS ORDER BY severity_rank
-- NULLS FIRST`, naming the column this whole section exists to stop clients thresholding
-- on. That sentence is what `\d+ drugref.curated_ddi_pair` prints FIRST, so the two
-- column comments below were corrected while the most prominent statement of the rule
-- still pointed at the wrong column: a THIRD spelling, and the drift §1 is about.
-- Only the precedence sentence changes; every other sentence is db/037's, verbatim.
COMMENT ON VIEW drugref.curated_ddi_pair IS
    'Drug pairs carrying a live drugref grade, from EITHER of two rule grains -- '
    '`rule_grain` says which (''moiety_rule'' | ''class_rule''), and `via_subject_class` '
    'names the class-grain rule''s subject (NULL for a moiety rule). ONE PAIR CAN APPEAR '
    'TWICE, once per grain, with different grades: both rulings are live, they sit in '
    'different tables, and each satisfies its own single-live guard, so no constraint '
    'can prevent it. THE PRECEDENCE IS `ORDER BY effective_rank, (rule_grain '
    '= ''moiety_rule'') DESC` -- MOST SEVERE FIRST, moiety grain breaking ties (the rule '
    'naming an actual drug carries better mechanism/management text than one naming its '
    'whole class). THRESHOLD ON effective_rank, NEVER ON severity_rank (db/038, issue '
    '116): db/037 stated this rule with `severity_rank NULLS FIRST`, which orders '
    'correctly and then hands the client a NULL that every `<= n` test silently drops. '
    'Severity-first because under-warning is the harm direction on this '
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

-- THE ORDER BY NOW READS `effective_rank`, and it is the SAME ORDER as db/037's
-- `severity_rank NULLS FIRST` -- 0 precedes 1 exactly as NULLS FIRST placed the NULL.
-- Written this way because the ordering rule and the published threshold column must be
-- ONE thing: with two spellings of one rule, a later edit can fix the sort and leave the
-- payload behind, which is precisely how issue 116 arose out of db/037.
--
-- EVERY OTHER KEY IS db/037's, UNCHANGED, including the determinism tail that PR #113's
-- review closed across both grains -- `via_subject_class` is load-bearing there and its
-- absence let two class rules over one pair resolve by heap order.
CREATE OR REPLACE VIEW drugref.curated_ddi_pair_effective AS
SELECT DISTINCT ON (subject_moiety, partner_moiety, relationship) *
FROM   drugref.curated_ddi_pair
ORDER  BY subject_moiety, partner_moiety, relationship,
          effective_rank,
          (rule_grain = 'moiety_rule') DESC,
          candidate_source, via_subject_class, via_class, member_class,
          reviewed_at, reviewed_by;

COMMENT ON VIEW drugref.curated_ddi_pair_effective IS
    'ONE ROW PER (subject_moiety, partner_moiety, relationship) -- curated_ddi_pair '
    'with db/035''s precedence APPLIED rather than described. THE SAFE READ, and the '
    'one a prescribing client should use: curated_ddi_pair states both grades when the '
    'two grains disagree, so a client doing `SELECT severity ... LIMIT 1` over it got '
    'an arbitrary answer and it might be the LOWER one. THE RULE IS ORDER BY '
    'effective_rank, (rule_grain = ''moiety_rule'') DESC -- most severe first, the '
    'moiety grain breaking ties because a rule naming an actual drug carries better '
    'mechanism/management text than one naming its whole class. THRESHOLD ON '
    'effective_rank, NEVER ON severity_rank (db/038, issue 116): rank 1 is most severe, '
    'so an unrankable severity must sort ABOVE contraindicated -- and because this is a '
    'DISTINCT ON, sorting it first makes it WIN and DISCARDS the rankable competitor, '
    'leaving the client a severity_rank of NULL that every `<= n` threshold silently '
    'drops. effective_rank is 0 there and satisfies them all; severity_rank stays '
    'NULLABLE beside it so the schema fault is still visible, and drugref status counts '
    'those rows. Anything after the two precedence keys is a determinism tie-break, NOT '
    'a clinical preference: one rule asserted by two authorities is two rows agreeing '
    'on both precedence keys, and DISTINCT ON must pick the same one every time. THE '
    'TAIL CLOSES BOTH GRAINS -- candidate_source, then via_subject_class AND via_class, '
    'because a class-grain row is identified by both of its ends and by its source; '
    'with via_subject_class missing, two class rules over one pair (two subject classes '
    'the same drug is filed under, one object class) tied on every key and DISTINCT ON '
    'followed heap order, so a rebuild or a dump/restore could silently change which '
    'mechanism and management text a client read. STILL DIRECTIONAL (db/006): a '
    'consumer asking "do X and Y interact" queries BOTH directions here too. Read '
    'curated_ddi_pair itself to see what was outranked, and curated_grain_disagreement '
    'for the rule pairs a curator should reconcile -- most-severe-wins is safe only '
    'while that worklist is worked.';


-- ============================================================================
-- 2. curated_unrankable_severity -- the fault reaches an OPERATOR (#116)
-- ============================================================================
-- WHY A MITIGATION IS NOT ENOUGH ON ITS OWN. Section 1 makes an unrankable severity
-- harmless to a thresholding client, which is the urgent half. It also makes it QUIET:
-- the client now gets a usable number and nothing anywhere says the database is
-- mis-shaped. A severity absent from severity_kind is a SCHEMA fault -- a dropped
-- foreign key, a severity_kind row deleted, a restore that lost the vocabulary table --
-- and the standing lesson of issues 74, 76 and review I7 is that a detector nothing
-- reads is not a detector.
--
-- A VIEW RATHER THAN A QUERY IN PYTHON, for the rule curation.py's own docstring
-- states: the sweep that finds readers of the curated tables works through pg_rewrite
-- and cannot see SQL living in a Python string. Every other detector in this schema
-- ships this way and this one has no reason to be the exception.
--
-- IT COUNTS RULES, NOT PAIRS, and the choice is deliberate twice over. An operator
-- fixing this fixes a CURATED ROW, so the rule grain is the actionable one -- reporting
-- 2,263 expanded pairs for one bad class rule would be a number nobody can act on
-- (issue 115 is the same confusion, one tier up). And reading curated_ddi_pair instead
-- would drag in ddi_candidate_pair's ~2.7 s scan (issue 75) on every `drugref status`.
--
-- BOTH CURATED TABLES, because both carry a severity and both have the same FK.
-- `superseded_by IS NULL AND applies` on each: a superseded ruling is history and a
-- withdrawn one asserts nothing, so neither is a live fault an operator should chase.
--
-- THE PREDICATE IS `sk.severity_rank IS NULL`, NOT `sk.severity IS NULL`, and the review
-- of PR #119 is why. Those two differ, and the difference is the whole point of the
-- detector: what does the harm in §1 is a NULL RANK -- that is what COALESCE swallows,
-- what wins the DISTINCT ON, and what discards the competing grade. A missing JOIN is
-- merely the cause this migration first imagined. Testing the cause instead of the
-- condition meant that dropping severity_kind.severity_rank's NOT NULL -- squarely
-- inside the fault family the COMMENT below claims to cover, "a dropped constraint" --
-- produced FULL harm with ZERO detection, and `drugref status` printing an affirmative
-- `none` over it. Keying on the rank STRICTLY WIDENS the population (a join miss makes
-- every sk column NULL, rank included), so nothing that was reported stops being
-- reported. §1 wrote the ordering rule in one place for exactly this reason and §2
-- then spelled the same rule a second way; this is that second spelling removed.
CREATE OR REPLACE VIEW drugref.curated_unrankable_severity AS
SELECT 'curated_interaction'::text AS target_table,
       c.curated_interaction_id    AS target_id,
       c.severity,
       c.reviewed_by,
       c.reviewed_at
FROM   drugref.curated_interaction c
LEFT   JOIN drugref.severity_kind sk ON sk.severity = c.severity
WHERE  c.superseded_by IS NULL
AND    c.applies
AND    sk.severity_rank IS NULL

UNION ALL

SELECT 'curated_class_interaction'::text     AS target_table,
       cci.curated_class_interaction_id      AS target_id,
       cci.severity,
       cci.reviewed_by,
       cci.reviewed_at
FROM   drugref.curated_class_interaction cci
LEFT   JOIN drugref.severity_kind sk ON sk.severity = cci.severity
WHERE  cci.superseded_by IS NULL
AND    cci.applies
AND    sk.severity_rank IS NULL;

COMMENT ON VIEW drugref.curated_unrankable_severity IS
    'LIVE curated rulings whose severity is absent from severity_kind -- EMPTY on any '
    'healthy database, because a foreign key on both curated tables makes it '
    'unreachable. Non-empty means the schema is broken, not that a curator erred: a '
    'dropped constraint, a deleted severity_kind row, or a restore that lost the '
    'vocabulary. Reported by `drugref status` (db/038, issue 116). WHY IT MATTERS EVEN '
    'THOUGH effective_rank now handles the read: such a row still WINS '
    'curated_ddi_pair_effective''s DISTINCT ON and discards the competing grade, so '
    'clients are served a severity word drugref cannot rank while every real grade for '
    'that pair is suppressed. COUNTS RULES, NOT EXPANDED PAIRS -- an operator fixes a '
    'curated row, and one bad class rule can expand to thousands of pairs.';


-- ============================================================================
-- 3. db/035's class-rule count says NINE; the data says SEVEN (#117)
-- ============================================================================
-- A CORRECTION TO A MERGED MIGRATION'S PROSE, re-issued on db/027's precedent: db/035
-- is applied and immutable, so the only way to change what the SCHEMA says is a fresh
-- COMMENT ON in a later file.
--
-- THE BASE IS db/036, NOT db/035, AND THE FIRST DRAFT OF THIS SECTION GOT THAT WRONG.
-- `COMMENT ON` OVERWRITES; it does not merge. There are THREE statements in db/ over
-- this one view -- db/035 § 6, db/036 § 1, and this -- so the text being replaced is
-- whichever ran LAST, and that is db/036's. Rebuilding from db/035 silently reverted
-- db/036 § 1, which had corrected the gap_key spelling from `AXIS:` to `CI_AXIS:`, and
-- deleted the parenthetical explaining the correction. Caught in review of PR #119.
--
-- WHY THAT REVERT WAS THE SERIOUS HALF OF THIS SECTION, worse than the figure it came
-- in to fix: `question_uuid = uuid5(gap_kind, gap_key)`, and the key is FROZEN and
-- externally citable. A reader reconstructing it from `\d+` on a running node computes
-- a DIFFERENT uuid and gets NO ERROR -- just a uuid matching nothing, which db/036
-- rightly calls the hardest kind of wrong answer to notice.
--
-- AND THE ROUND'S OWN VERIFICATION COULD NOT SEE IT, which is the transferable lesson.
-- It grepped the catalog for `%nine ingested%` and `%seven ingested%` -- scoped to the
-- word being changed, so it was structurally blind to what else moved in the same
-- overwrite. A re-issued COMMENT must be diffed WHOLE against the live text, and
-- tests/test_class_grain_comment.py now pins both halves so a db/039 cannot do this
-- again.
--
-- WHERE THE 9 CAME FROM. Issue 96's failure-scenario prose said `class_rules_written=9`
-- and db/035 quoted it faithfully into this view's COMMENT. That figure was never
-- reconciled against issue 94, which WITHHELD the class x class entries pending
-- literature research -- and there are SEVEN of them, confirmed four independent ways:
-- issue 94's own title; src/drugref/data/onc_high_priority.toml ("Task 12A drafted
-- seven class x class entries... ruled that NONE of the seven ship", an eleven-entry
-- draft = 4 moiety + 7 class); docs/PROJECT-NOTES.md; docs/HANDOVER.md.
--
-- THE 9 PROPAGATES, WHICH IS WHY IT IS WORTH A MIGRATION. db/037's first draft carried
-- both numbers in ONE file -- "seven" on line 10 and "~9" on line 63 -- because its
-- author read db/035 for one and the issue for the other. Anyone reading db/035 in the
-- database will keep re-importing it exactly the same way.
--
-- ONLY THE FIGURE CHANGES, MEASURED AGAINST db/036. Every other sentence below is
-- db/036's, verbatim -- including the `CI_AXIS:` spelling and the parenthetical
-- recording that correction, both of which this section previously dropped. A whole
-- diff of this string against the live comment shows `nine` -> `seven` and one added
-- parenthetical, and nothing else.
COMMENT ON VIEW drugref.gap_uncurated_class_interaction_rule IS
    'CLASS x CLASS contraindication rules carrying no live drugref grade, ranked by '
    'max_pair_count -- the drug pairs at stake in the answer. The class grain''s '
    'PRIMARY question, and it had none until db/035: db/031 added a gap kind for the '
    'lesser one (an endpoint that resolved to nothing) while the grain''s own "these '
    'rules are ungraded" reached nobody, so seven ingested rules could sit permanently '
    'uncurated with question_worklist showing nothing to do. (db/035 said NINE here, '
    'quoting issue 96''s prose; issue 94 withheld SEVEN class x class ONC entries and '
    'the seed file agrees -- corrected by db/038, issue 117.) GROUPED WITHOUT `source` '
    'so one rule asserted by two authorities raises ONE question -- its gap_key is '
    'CLASS:{subject}/CLASS:{object}/CI_AXIS:{relationship} and question_uuid is a pure '
    'function of it, so a per-source grain would mint one immortal question and '
    'overwrite its own text. (db/035''s comment spelled that key `AXIS:`; the value was '
    'always `CI_AXIS:`, matching uncurated_interaction_rule one grain over, and db/036 '
    'corrected the sentence rather than the key -- the key is frozen.) A rule reaching '
    'NO pair is omitted (#36: a review gate must only ask what an answer could change) '
    'and is reported to the OPERATOR through class_pair_rule_reach instead, since a '
    'rule reaching nobody is a data fault rather than a clinical question.';

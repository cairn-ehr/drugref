-- db/027 -- expansion-policy history (#35)
--
-- drugref.class_expansion_policy is the one curated table still edited in place, and
-- it GATES RECALL: a single `UPDATE ... SET decision = 'deny'` removes thousands of
-- candidate pairs with no audit row and nothing reporting it. db/010 reasoned its tier
-- out explicitly and was right AT THE TIME --
--
--   "NOT the append-only signed overlay either (that tier arrives with Plan C). This
--    is small, low-cardinality policy data in the same class as ci_axis and
--    source_tier: edited in place, reviewed by diff."
--
-- -- and Plan C has since landed. That parenthesis is now the argument FOR this
-- change. db/010 is applied and immutable, so the standing correction to its prose
-- lives in docs-site/docs/decisions/expansion-policy-is-append-only.md.
--
-- WHAT THIS MIGRATION DOES NOT DO: it does not touch ci_class_subtree. The deny-list
-- filters the class a rule NAMES, never the walk -- `Decreased Coagulation Activity`
-- is a descendant of a denied root and must still expand, which is how a rule reaches
-- warfarin, apixaban and aspirin (test_a_descendant_of_a_denied_root_still_expands).

-- ---- 1. the surrogate key -----------------------------------------------------
--
-- THE NATURAL KEY HAS TO STOP BEING UNIQUE, and this is the part most likely to be
-- "simplified" back. Correction-by-overlay means INSERTING the new judgement and THEN
-- pointing the old row at it, so both rows carry the same (source, source_code) --
-- a primary key on those columns rejects the only sequence that can express a
-- correction, and in-place mutation becomes the only possible implementation.
-- db/001 shipped exactly that defect on identity_claim and db/005 had to repair it;
-- db/020 records the same reasoning for additive_effect.
--
-- "At most one LIVE row per class" is not lost -- it moves to the deferred trigger in
-- section 3, which is the only shape that can express it (see
-- docs-site/docs/decisions/correcting-a-curated-assertion.md).
--
-- THE `IF EXISTS` HERE IS NOT REPLAY SAFETY, and reads as though it were. The
-- surrogate PK added by the very next statement is ALSO auto-named
-- class_expansion_policy_pkey, so on a second run this DROP would remove the
-- SURROGATE key rather than db/010's natural one. What makes it safe is the ledger,
-- which runs each file exactly once -- and a replay would fail loudly in any case,
-- because superseded_by references policy_id. As in db/012 §4, the IF EXISTS only
-- covers a database where an operator did this by hand.
ALTER TABLE drugref.class_expansion_policy
    DROP CONSTRAINT IF EXISTS class_expansion_policy_pkey;

ALTER TABLE drugref.class_expansion_policy
    ADD COLUMN IF NOT EXISTS policy_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY;

-- One-way, set once, always a LATER row on the SAME class. The seeded rows acquire
-- NULL and are therefore live: nobody has revised or withdrawn anything.
ALTER TABLE drugref.class_expansion_policy
    ADD COLUMN IF NOT EXISTS superseded_by bigint
        REFERENCES drugref.class_expansion_policy(policy_id);

COMMENT ON COLUMN drugref.class_expansion_policy.policy_id IS
    'Surrogate key. The natural key (source, source_code) is deliberately NOT unique: '
    'a correction preserves it, so history rows share it. Do not "restore" a UNIQUE '
    'constraint there -- it would forbid every correction.';
COMMENT ON COLUMN drugref.class_expansion_policy.superseded_by IS
    'One-way, set once, always a LATER row on the SAME class. A superseded judgement '
    'is history and is never deleted: what drugref believed, and when, stays '
    'answerable -- which matters most for exactly the decisions that withheld pairs.';

-- ---- 2. the floor, REUSED rather than copied ----------------------------------
--
-- Both functions are db/020's, generic over the natural key (db/023 rewrote the
-- second one as equality predicates so an index can serve it). This table attaches to
-- them with no new PL/pgSQL, which is the point: one rule in five places is one rule
-- that will drift, and this project has spent four rounds proving it (#31, #40, #43,
-- db/018's two CTEs).
DROP TRIGGER IF EXISTS class_expansion_policy_append_only
    ON drugref.class_expansion_policy;
CREATE TRIGGER class_expansion_policy_append_only
    BEFORE UPDATE OR DELETE ON drugref.class_expansion_policy
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'policy_id', 'source', 'source_code');

-- DEFERRED, because a correction is momentarily TWO live rows -- between the INSERT
-- and the UPDATE that supersedes -- and an immediate check would reject the only
-- sequence that can express one.
DROP TRIGGER IF EXISTS class_expansion_policy_single_live
    ON drugref.class_expansion_policy;
CREATE CONSTRAINT TRIGGER class_expansion_policy_single_live
    AFTER INSERT OR UPDATE ON drugref.class_expansion_policy
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'source', 'source_code');

-- PARTIAL and NOT UNIQUE, matching the trigger's predicate exactly -- uniqueness over
-- live rows is precisely what this design cannot use. db/023 measured that without
-- this index the trigger is a sequential scan per row and therefore quadratic.
-- Nothing but the trigger reads it, so a test asserts it by name.
CREATE INDEX IF NOT EXISTS class_expansion_policy_live_key
    ON drugref.class_expansion_policy (source, source_code)
    WHERE superseded_by IS NULL;

-- ---- 3. the third decision value ----------------------------------------------
--
-- SUPERSESSION ALONE CAN NEVER WITHDRAW ANYTHING. A correction must point at a later
-- row carrying the SAME natural key, so every correction leaves another live row
-- standing -- the finding that gave additive_effect its `accumulates`,
-- interaction_group_member its `satisfies_role`, and (db/023) interaction_group_
-- assertion its `applies`. This table has the same hole, and here it BITES: absent
-- means UNREVIEWED, which expands AND raises a question, so a class that has ever
-- been ruled on could never go back on the worklist.
--
-- medrt_run already logs "Re-key or WITHDRAW them in drugref.class_expansion_policy"
-- when a release stops defining a ruled-on class. Before this round, withdraw meant
-- DELETE. Since section 2, DELETE raises -- so without this value the schema would
-- ship a warning advising an impossible action.
--
-- A THIRD VALUE RATHER THAN A BOOLEAN, deliberately. `decision` is already the ruling
-- vocabulary and all four readers branch on it; a boolean beside it would admit two
-- encodings of one state ((deny, false) and (allow, false)) and let a consumer read
-- `decision` alone and be confidently wrong -- the footgun slice 5b.2 split a table to
-- avoid. A reader that has never heard of `withdrawn` reads it as NOT-deny and
-- expands, which for a contraindication is the safe direction.
--
-- `withdrawn` IS NOT `allow`. It means NO CURRENT JUDGEMENT.
ALTER TABLE drugref.class_expansion_policy
    DROP CONSTRAINT IF EXISTS class_expansion_policy_decision;
ALTER TABLE drugref.class_expansion_policy
    ADD CONSTRAINT class_expansion_policy_decision
    CHECK (decision IN ('deny', 'allow', 'withdrawn'));

COMMENT ON COLUMN drugref.class_expansion_policy.decision IS
    '`deny` expands to DIRECT members only; `allow` expands over the full subtree; '
    '`withdrawn` (db/027) means the judgement no longer stands, which returns the '
    'class to gap_unreviewed_expansion_root. WITHDRAWN IS NOT ALLOW -- it is the '
    'absence of a judgement, recorded rather than deleted so its rationale survives.';

-- ---- 4. one view, four readers -------------------------------------------------
--
-- There are FOUR readers of this table and every one asks the same question: WHAT
-- BINDS NOW. Writing `superseded_by IS NULL AND decision <> 'withdrawn'` four times is
-- the bet this project has lost four times already (#31's reach measure stated twice
-- where only one copy learned a correction; #40's two MeSH readers; #43's two
-- checksums; db/018's two near-identical CTEs). So it is stated once.
--
-- NAMED `_current`, NOT `_live`, BECAUSE LIVE AND BINDING ARE DIFFERENT QUESTIONS. A
-- withdrawn row is live (nothing superseded it) and does not bind. The writer in
-- interactions.py needs the LIVE row including a withdrawn one, to supersede it --
-- that is a different question asked in exactly one place, not a fifth copy of this.
--
-- It also keeps ddi_candidate_pair's LEFT JOIN one-to-one: the single-live trigger
-- allows one live row per class, and this view drops the withdrawn ones, so a history
-- row can never multiply a pair.
CREATE OR REPLACE VIEW drugref.class_expansion_policy_current AS
SELECT policy_id, source, source_code, decision, class_name, rationale,
       reviewed_by, reviewed_against, reviewed_at
FROM   drugref.class_expansion_policy
WHERE  superseded_by IS NULL
AND    decision <> 'withdrawn';

COMMENT ON VIEW drugref.class_expansion_policy_current IS
    'The expansion decisions that currently BIND: not superseded, and not withdrawn. '
    'EVERY READER OF class_expansion_policy MUST GO THROUGH THIS VIEW -- the base '
    'table holds history since db/027, and history read as policy is a deny that '
    'stopped being true. A withdrawn decision is deliberately indistinguishable from '
    'no decision here, which is what returns its class to gap_unreviewed_expansion_root.';

-- 4a. expansion_policy_unresolved (db/010) -- a withdrawn decision binds nothing, so
--     there is nothing left to re-key and reporting it would be noise.
CREATE OR REPLACE VIEW drugref.expansion_policy_unresolved AS
SELECT p.source, p.source_code, p.decision, p.class_name, p.reviewed_against
FROM   drugref.class_expansion_policy_current p
WHERE  NOT EXISTS (SELECT 1 FROM drugref.substance_class sc
                   WHERE  sc.source      = p.source
                   AND    sc.source_code = p.source_code);

-- db/010's text said "expansion-policy rows", which now overstates the scope: this
-- lists only the decisions that BIND. db/010 is applied and immutable, so the
-- correction is re-issued here rather than edited there.
COMMENT ON VIEW drugref.expansion_policy_unresolved IS
    'BINDING expansion decisions naming a class the registry does not hold -- upstream '
    're-keyed or withdrew it, so the decision silently stops applying. Since db/027 it '
    'reads class_expansion_policy_current, so superseded and `withdrawn` rows are '
    'absent by construction: a withdrawn decision binds nothing, so there is nothing '
    'left to re-key and listing it would be noise a curator could never clear. '
    'Expected to be EMPTY after a full ingest; on a partial test fixture every '
    'unmatched row is listed, which is correct rather than alarming.';

-- 4b. gap_unreviewed_expansion_root (db/012) -- and THIS is where withdrawal pays:
--     the class becomes invisible here again, so the question re-raises.
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
       -- The axis join, added by db/012's re-issue and unchanged by this one: a
       -- predicate that does not expand cannot fan out, so no decision about its
       -- object class matters.
JOIN   drugref.ci_axis         a  ON a.relationship  = ci.relationship
JOIN   drugref.substance_class sc ON sc.class_uuid   = ci.object_class_uuid
JOIN   sized                   z  ON z.root_uuid    = ci.object_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = ci.ingest_run
WHERE  z.descendant_class_count > 20
AND    a.expands_descendants
       -- Either decision counts as reviewed. `allow` and `deny` differ for the pair
       -- set and agree here, because this view asks only whether a human has looked
       -- -- and since db/027, whether they still stand by it.
AND    NOT EXISTS (SELECT 1 FROM drugref.class_expansion_policy_current p
                   WHERE  p.source      = sc.source
                   AND    p.source_code = sc.source_code)
GROUP  BY sc.class_uuid, sc.class_name, sc.concept_type, z.descendant_class_count;

-- db/012's comment says "EITHER decision retires the question", which this migration
-- makes false: there are THREE decisions now, and `withdrawn` deliberately re-raises
-- the question rather than retiring it -- the entire reason the value exists. db/012
-- is applied and immutable, so the whole comment is re-issued here with that one
-- sentence corrected and nothing else changed but the relation it names.
COMMENT ON VIEW drugref.gap_unreviewed_expansion_root IS
    'Contraindicated classes with more than 20 descendant classes that nobody has '
    'ruled on in class_expansion_policy_current -- so they expand over their whole '
    'subtree by default, which for an abstract organ-system bucket is fan-out rather '
    'than recall. SCOPED TO PREDICATES THAT ACTUALLY EXPAND (ci_axis.expands_'
    'descendants): a class named only by non-expanding rules is not asked about, '
    'because no decision could change a row -- and ci_rule_count counts the expanding '
    'rules for the same reason. The threshold is a DISCOVERY HEURISTIC for the '
    'worklist, never the criterion for denying expansion: that judgement is '
    'qualitative and belongs in the policy table. A `deny` or `allow` retires the '
    'question; `withdrawn` (db/027) RE-RAISES it, because it means no current '
    'judgement rather than a permissive one. ABSENCE OF A ROW IS NOT A GUARANTEE OF '
    'SENSIBLE EXPANSION: a badly-shaped root with 20 descendants is invisible here.';

-- 4c. ddi_candidate_pair (db/012) -- a withdrawn deny stops denying, and the class
--     expands again. COALESCE already treats a missing row as 'allow', so a withdrawn
--     row disappearing from the view is exactly the right behaviour with no change to
--     the predicate.
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
LEFT   JOIN drugref.class_expansion_policy_current p
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

-- 4d. gap_dead_by_expansion_policy (db/018) -- the FOURTH reader, which the issue text
--     does not mention. A withdrawn deny stops killing its rules, so the question
--     retires, which is correct: the rule now reaches its subtree.
CREATE OR REPLACE VIEW drugref.gap_dead_by_expansion_policy AS
SELECT rr.object_class_uuid    AS class_uuid,
       sc.class_name,
       sc.concept_type,
       count(*)                AS ci_rule_count,
       -- Per RULE while the row is per class, because the subject exclusion is per
       -- rule -- so max() picks the largest cost among the class's dead rules, which
       -- is what a priority signal should do. This is an aggregate over a MEASURE,
       -- not over a key: #41's defect was folding a KEY component with max(), and
       -- grouping per class is what keeps this view's grain equal to its gap_key's.
       max(rr.subtree_partner_count) AS subtree_partner_count,
       max(r.upstream_release) AS upstream_release
FROM   drugref.ci_rule_partner_reach rr
JOIN   drugref.substance_class sc ON sc.class_uuid   = rr.object_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = rr.ingest_run
       -- DENIED, not merely reviewed. `allow` and absent both expand
       -- (ddi_candidate_pair COALESCEs a missing policy row to 'allow'), so their
       -- rules are not dead -- and an unreviewed sprawling root is
       -- gap_unreviewed_expansion_root's question.
JOIN   drugref.class_expansion_policy_current p
       ON p.source = sc.source AND p.source_code = sc.source_code
      AND p.decision = 'deny'
       -- A predicate that cannot expand cannot be rescued by allowing expansion, so
       -- no available decision would retire the question. db/012's rule -- the review
       -- gate must only ask what an answer could change -- in a fourth place. (That
       -- rule IS dead, and deliberately unreported here: #48.)
WHERE  rr.expands_descendants
       -- Nothing the rule could pair with is filed DIRECTLY on the class, which is
       -- all a denied rule reaches. A direct member that IS the subject does not save
       -- it -- the shape that made this view silent in review.
AND    rr.direct_partner_count = 0
       -- ...but the subtree does hold one, which is what makes the question
       -- answerable AND what keeps this view disjoint from the one above. Without it
       -- a class both views can see would mint a SECOND immortal question for one
       -- dead rule, and one whose answer changes nothing: allowing expansion over a
       -- subtree with no partner in it reaches nobody. Plan A tolerates two questions
       -- on one class only when they are independently answerable.
AND    rr.subtree_partner_count > 0
GROUP  BY rr.object_class_uuid, sc.class_name, sc.concept_type;

-- ---- 5. the table's own comment ------------------------------------------------
--
-- `\d+ drugref.class_expansion_policy` is the first thing the next person runs, and
-- db/010's text still enumerates two decision values, describes flat policy, and says
-- nothing about history or the view. That migration is applied and immutable, so the
-- standing text is re-issued here. It is last in the file so it can name everything
-- above it.
COMMENT ON TABLE drugref.class_expansion_policy IS
    'Per-class descendant-expansion policy for contraindications: `deny` means a CI '
    'rule naming this class expands to its DIRECT members only, `allow` means it '
    'expands over the full subtree, and `withdrawn` (db/027) means the judgement no '
    'longer stands. NO ROW MEANS UNREVIEWED, which expands (the safe default) and is '
    'reported by gap_unreviewed_expansion_root -- so `allow` and absent differ for the '
    'worklist and not for the pair set, and `withdrawn` is deliberately '
    'indistinguishable from absent. WITHDRAWN IS NOT ALLOW. '
    'SINCE db/027 THIS TABLE HOLDS HISTORY: it is append-only, (source, source_code) '
    'is NOT unique, a revision INSERTs the new judgement and then sets superseded_by '
    'on the old one, and DELETE raises. So EVERY READER MUST GO THROUGH '
    'drugref.class_expansion_policy_current -- querying this table directly reads '
    'superseded judgements as policy, which is a deny that stopped being true. Revise '
    'it through interactions.record_expansion_decision / withdraw_expansion_decision, '
    'which own that ordering. CURATOR POLICY, not a projection: no ingest clears it. '
    'THE DECISION APPLIES TO THE CLASS THE RULE NAMES, never to classes met while '
    'walking down -- a denied root does not stop a rule stated against one of its '
    'descendants.';

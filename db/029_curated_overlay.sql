-- db/029_curated_overlay.sql -- slice 5c.1: the curated overlay's assertion shape.
--
-- WHAT THIS TIER IS. Ingested feeds are REBUILDABLE PROJECTIONS, dropped and rebuilt
-- per release. Curated knowledge is an APPEND-ONLY OVERLAY: nothing is edited in
-- place and nothing is deleted, because "what did we last say about this, against
-- which release, and why did we change our mind" has to be answerable from the
-- database. db/020 built that floor; db/027 put a fifth table on it; this file adds
-- the sixth and seventh with NO NEW PL/pgSQL.
--
-- SHIPS EMPTY. No seed, no curation content. The shape is this slice; curation is
-- step 8.

-- ============================================================================
-- 1. curated_interaction -- drugref's judgement on a class-level DDI RULE
-- ============================================================================
-- KEYED ON THE RULE, NOT THE PAIR, and that is the lever the whole slice rests on.
-- class_contraindication holds 635 CI_MoA/CI_PE rules (measured 2026-08-06 against
-- drugref_5c1; not the ~739 this section used to quote, which was the raw pre-gate
-- MED-RT terminology count and never this table's own row count -- see
-- PROJECT-NOTES.md "Slice 5c.1"); ddi_candidate_pair expands them to 21,664 concrete
-- pairs AT READ TIME. So a pair has no stable row identity to
-- reference, and 21,664 is not a population anyone hand-curates. One graded rule
-- inherits to every pair it expands to -- Plan C's "keyed on class so a grade
-- inherits to every member ... a few rows, not a hundred", one table over.
--
-- `source` IS DELIBERATELY NOT IN THE KEY, and that breaks with db/014, which puts it
-- in the key of every projection table for db/006 finding 2's reason. That argument is
-- about UPSTREAM assertions: without source in the key, a second authority's
-- independent row is swallowed by ON CONFLICT DO NOTHING and then deleted by the next
-- rebuild. This tier holds DRUGREF'S JUDGEMENT about a clinical fact, not a record of
-- who said it. Keying on the upstream source would let two authorities asserting the
-- same interaction produce two competing drugref rulings that the single-live trigger
-- cannot reconcile and a consumer would have to choose between. One fact, one live
-- judgement. `source` stays as a COLUMN because it records who AUTHORED the judgement,
-- which is the licence-led layering slices 5c.2/5c.3 need.
--
-- NO FOREIGN KEY INTO class_contraindication. It is a rebuildable projection: an FK
-- would either block the per-source rebuild or cascade curator judgement away with it.
-- The candidate is named by NATURAL KEY -- stable, because moiety_uuid is immortal and
-- class_uuid is minted from (source, source_code) -- and curated_target_unresolved
-- (section 5) reports any curated row whose candidate is no longer projected. Both
-- foreign keys below point at IDENTITY (substance_moiety, substance_class), which a
-- rebuild does not touch.
--
-- WHY THE NATURAL KEY IS NOT THE PRIMARY KEY: correction-by-overlay means INSERTing
-- the new row and THEN pointing the old one at it, so both rows briefly carry the same
-- natural key. A primary key on it rejects the only sequence that can express a
-- correction, and in-place mutation becomes the only possible implementation --
-- exactly the defect db/001 shipped on identity_claim and db/005 had to repair.
CREATE TABLE IF NOT EXISTS drugref.curated_interaction (
    curated_interaction_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_moiety_uuid    uuid        NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_class_uuid      uuid        NOT NULL REFERENCES drugref.substance_class(class_uuid),
    relationship           text        NOT NULL,
    -- THE RULING. `false` means "a curator looked and this rule is not a real
    -- interaction" -- a real answer, and the only thing that lets a reviewed rule
    -- leave gap_uncurated_interaction_rule instead of being asked about every release
    -- forever. It exists because SUPERSESSION ALONE CAN NEVER WITHDRAW ANYTHING: a
    -- correction must point at a later row with the SAME natural key, so every
    -- correction leaves another live row standing. additive_effect.accumulates,
    -- interaction_group_member.satisfies_role, interaction_group_assertion.applies and
    -- class_expansion_policy.decision = 'withdrawn' are the same column, four rounds
    -- running. NO DEFAULT: a ruling must be stated, never guessed.
    applies                boolean     NOT NULL,
    severity               text,
    mechanism              text,
    management             text,
    evidence_grade         text,
    -- NULLABLE. Where a curated row answers a gap question, its citations are already
    -- reachable through question_evidence -- which has supersession, a reference
    -- scheme, and its own warning that reference_value is untrusted input. Nullable
    -- because a curator may assert something no gap view asked about, and because
    -- CURATED IS NOT VERIFIED: a NULL here is what makes "this grade rests on nothing
    -- recorded" visible instead of implied.
    --
    -- ON DELETE CASCADE matches the question registry's other three curated tables,
    -- and the cascade is a SAFETY NET rather than a deletion path: it lands on the
    -- append-only trigger below, which RAISEs and aborts the whole ingest.
    -- questions.register_from_gaps must therefore RETAIN a question this table cites
    -- rather than delete it (see task 5) -- the guard, not the cascade, is what keeps
    -- curator work.
    question_uuid          uuid        REFERENCES drugref.open_question(question_uuid)
                                       ON DELETE CASCADE,
    source                 text        NOT NULL,
    -- db/027's provenance triple, NOT Plan C's ingest_run foreign key. A human
    -- curator's assertion has no ingest run at all, and a NOT NULL FK would force
    -- every curated row to invent one. `reviewed_against` names the release the
    -- judgement was formed against, which is what makes "is this ruling stale?"
    -- answerable.
    reviewed_by            text        NOT NULL,
    reviewed_against       text        NOT NULL,
    reviewed_at            timestamptz NOT NULL DEFAULT now(),
    superseded_by          bigint      REFERENCES drugref.curated_interaction(curated_interaction_id),
    -- db/006's finding 1, not a CHECK: db/004 originally admitted CI predicates with a
    -- CHECK here AND a matching CASE inside ddi_candidate_pair, two lists kept in step
    -- by a comment -- and widening only the CHECK inserted rows that expanded to ZERO
    -- pairs with no error, because an unmapped CASE arm yields NULL and joins nothing.
    -- db/006 replaced class_contraindication's own CHECK with an FK into ci_axis for
    -- exactly that reason: one vocabulary, one home, so adding a predicate is ONE
    -- INSERT there and the read path cannot silently go quiet. This table grades
    -- class_contraindication's rows, so it must be refused the same new value that
    -- table itself would be -- a CHECK here would drift the moment ci_axis grew a
    -- third axis, admitting a relationship this table could grade but the projection
    -- could not produce a candidate for.
    CONSTRAINT curated_interaction_relationship
        FOREIGN KEY (relationship) REFERENCES drugref.ci_axis(relationship),
    -- PLAN C'S EXACT VOCABULARY, reused rather than re-minted. Two ladders for one
    -- concept is a second list to disagree with the first (db/006), and a consumer
    -- would have to reconcile them at render time.
    CONSTRAINT curated_interaction_severity
        CHECK (severity IN ('contraindicated', 'major', 'moderate', 'minor')),
    -- The DOCUMENTATION ladder the interaction literature uses -- "how well attested
    -- is this?" -- and deliberately not GRADE, which grades confidence in a
    -- recommendation derived from trials and asks a question no DDI row answers.
    -- `theoretical` is the honest label for a mechanism with no reports behind it, and
    -- having it here is what stops a curator rounding such a row up to `suspected` for
    -- want of anywhere to put it. THERE IS NO `unknown`: a curator who cannot say how
    -- well attested a claim is is describing a question, not an assertion, and the
    -- question registry is where that belongs.
    CONSTRAINT curated_interaction_evidence_grade
        CHECK (evidence_grade IN ('established', 'probable', 'suspected', 'theoretical')),
    CONSTRAINT curated_interaction_source CHECK (source IN ('DRUGREF')),
    -- ONE CHECK, not several nullable columns nobody cross-checks. An asserting row
    -- states both judgements; a non-asserting row states neither. So "real, but with
    -- no severity to render" and "not real, but graded major" are both
    -- UNREPRESENTABLE rather than merely discouraged.
    CONSTRAINT curated_interaction_ruling_is_complete CHECK (
        (applies AND severity IS NOT NULL AND evidence_grade IS NOT NULL)
        OR
        (NOT applies AND severity IS NULL AND evidence_grade IS NULL))
);

-- ---- the floor, REUSED rather than copied -----------------------------------
-- Both functions are db/020's, generic over the natural key (db/023 rewrote the second
-- as equality predicates so an index can serve it). One rule in seven places is one
-- rule that will drift, and this project has spent four rounds proving it.
DROP TRIGGER IF EXISTS curated_interaction_append_only ON drugref.curated_interaction;
CREATE TRIGGER curated_interaction_append_only
    BEFORE UPDATE OR DELETE ON drugref.curated_interaction
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'curated_interaction_id', 'subject_moiety_uuid', 'object_class_uuid',
        'relationship');

-- DEFERRED, because a correction is momentarily TWO live rows -- between the INSERT
-- and the UPDATE that supersedes -- and an immediate check would reject the only
-- sequence that can express one.
DROP TRIGGER IF EXISTS curated_interaction_single_live ON drugref.curated_interaction;
CREATE CONSTRAINT TRIGGER curated_interaction_single_live
    AFTER INSERT OR UPDATE ON drugref.curated_interaction
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'subject_moiety_uuid', 'object_class_uuid', 'relationship');

-- PARTIAL and NOT UNIQUE, matching the trigger's predicate exactly -- uniqueness over
-- live rows is precisely what this design cannot use, since a correction needs two.
-- db/023 measured that without this index the trigger is a sequential scan per row and
-- therefore quadratic: 2,000 rows cost 5,773 ms, and 42 ms with it. NOTHING BUT THE
-- TRIGGER READS IT, so a test asserts it by name.
CREATE INDEX IF NOT EXISTS curated_interaction_live_key
    ON drugref.curated_interaction
       (subject_moiety_uuid, object_class_uuid, relationship)
    WHERE superseded_by IS NULL;

-- THE OTHER UNREAD INDEX. Postgres indexes the REFERENCED side of a foreign key
-- automatically and the REFERENCING side never, so `question_uuid` is bare by default
-- -- and it has two per-ingest readers. questions.register_from_gaps probes this table
-- with NOT EXISTS once per gap kind, fourteen times a run; and the ON DELETE CASCADE
-- must find this table's rows before the append-only trigger can refuse the delete.
-- db/007 added question_source_check_by_question for exactly this, and db/023 measured
-- what an unindexed per-row predicate costs once such a table stops being empty
-- (5,773 ms against 42 ms at 2,000 rows). Read only by the planner, so a test asserts
-- it by name -- as with the live-key indexes above.
CREATE INDEX IF NOT EXISTS curated_interaction_by_question
    ON drugref.curated_interaction (question_uuid);

COMMENT ON TABLE drugref.curated_interaction IS
    'CURATED, APPEND-ONLY: drugref''s own judgement -- severity, mechanism, management '
    'and evidence grade -- on a class-level CI_MoA/CI_PE rule, inheriting to every '
    'pair the rule expands to. Keyed on the RULE, not the pair: ddi_candidate_pair is '
    'a view, so a pair has no stable identity, and 21,664 pairs is not a curatable '
    'population while 635 rules is (of which 595 reach the worklist -- see '
    'gap_uncurated_interaction_rule; the other 40 pair with nobody and are already '
    'covered by gap_unpopulated_contraindication and gap_dead_by_expansion_policy). '
    '`source` is NOT in the key -- one clinical fact, '
    'one live drugref judgement, however many upstream authorities asserted it. '
    'CURATED IS NOT VERIFIED: a grade with no question_uuid rests on nothing recorded, '
    'and that is deliberately visible rather than implied.';
COMMENT ON COLUMN drugref.curated_interaction.applies IS
    'The curator''s RULING. False is a real answer -- "reviewed, and this rule is not '
    'a real interaction" -- and is what lets a reviewed rule leave the worklist '
    'instead of being asked about every release forever. Supersession alone can never '
    'withdraw anything, which is why this column exists. No DEFAULT: absence of a row '
    'means NOBODY HAS LOOKED, and that is a third state neither value can express.';
COMMENT ON COLUMN drugref.curated_interaction.evidence_grade IS
    'How well ATTESTED the claim is, strongest first: established, probable, '
    'suspected, theoretical. Not GRADE -- that grades confidence in a trial-derived '
    'recommendation, which is not what a DDI row asserts. No `unknown` level: a '
    'curator who cannot grade the evidence is describing a question, not an assertion.';
COMMENT ON COLUMN drugref.curated_interaction.superseded_by IS
    'One-way, set once, always a LATER row on the SAME natural key. A superseded row '
    'is history and is never deleted.';

-- ============================================================================
-- 2. curated_condition -- drugref's judgement on a (drug, condition) PAIR
-- ============================================================================
-- THE KEY OMITS `relationship`, AND THE ASYMMETRY WITH curated_interaction IS THE
-- POINT OF THIS SLICE. On the interaction side the object class fixes the axis (an MoA
-- class takes CI_MoA), so mirroring the candidate key costs nothing. Here it is not
-- fixed: the SAME (drug, condition) genuinely carries both an indication and a
-- contraindication. That is 168 distinct pairs in MED-RT 2026.07.06 -- 154 moieties
-- over 40 conditions -- and the flagship is nine beta-blockers asserted both may_treat
-- and CI_with against MeSH D006333 "Heart Failure", where BOTH ARE TRUE: first-line in
-- stable chronic HFrEF, contraindicated in acute decompensation, and MeSH has one
-- descriptor for both states.
--
-- Key on `relationship` and that single judgement must be written TWICE, once per
-- predicate, with nothing preventing the two copies from disagreeing. Key on the pair
-- and there is one row, one ruling, one thing to correct. The projection tier cannot
-- express this case; a key that re-split it would reproduce the defect one layer up.
--
-- THE COST, STATED: a curator cannot grade the indication and the contraindication of
-- one pair separately. That is the intended trade -- the ruling is ABOUT THE PAIR, and
-- `severity` grades its contraindication aspect. If a real case ever needs
-- per-relationship grades it is an additive migration on a table that ships empty.
CREATE TABLE IF NOT EXISTS drugref.curated_condition (
    curated_condition_id  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_moiety_uuid   uuid        NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_condition_uuid uuid        NOT NULL REFERENCES drugref.condition(condition_uuid),
    -- THE RULING, in four values:
    --   contraindicated   the contraindication stands; any indication is outweighed
    --   indicated         the indication stands; the CI is not clinically operative
    --   context_dependent BOTH are correct, in different clinical states
    --   spurious          reviewed; the upstream assertion is wrong
    -- All four RETIRE the pair from the worklist, because all four mean a curator
    -- looked. `context_dependent` is an honest answer rather than a hedge: it is the
    -- only true statement about metoprolol and D006333 at this grain, and mechanism /
    -- management carry the states in prose while the enum is what a consumer branches
    -- on. NO DEFAULT, for the reason curated_interaction.applies has none.
    ruling                text        NOT NULL,
    severity              text,
    mechanism             text,
    management            text,
    evidence_grade        text,
    question_uuid         uuid        REFERENCES drugref.open_question(question_uuid)
                                      ON DELETE CASCADE,
    source                text        NOT NULL,
    reviewed_by           text        NOT NULL,
    reviewed_against      text        NOT NULL,
    reviewed_at           timestamptz NOT NULL DEFAULT now(),
    superseded_by         bigint      REFERENCES drugref.curated_condition(curated_condition_id),
    CONSTRAINT curated_condition_ruling CHECK (
        ruling IN ('contraindicated', 'indicated', 'context_dependent', 'spurious')),
    CONSTRAINT curated_condition_severity
        CHECK (severity IN ('contraindicated', 'major', 'moderate', 'minor')),
    CONSTRAINT curated_condition_evidence_grade
        CHECK (evidence_grade IN ('established', 'probable', 'suspected', 'theoretical')),
    CONSTRAINT curated_condition_source CHECK (source IN ('DRUGREF')),
    -- Same shape as curated_interaction's, with `ruling <> 'spurious'` where that
    -- table has `applies`.
    CONSTRAINT curated_condition_ruling_is_complete CHECK (
        (ruling <> 'spurious' AND severity IS NOT NULL AND evidence_grade IS NOT NULL)
        OR
        (ruling = 'spurious' AND severity IS NULL AND evidence_grade IS NULL))
);

DROP TRIGGER IF EXISTS curated_condition_append_only ON drugref.curated_condition;
CREATE TRIGGER curated_condition_append_only
    BEFORE UPDATE OR DELETE ON drugref.curated_condition
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'curated_condition_id', 'subject_moiety_uuid', 'object_condition_uuid');

DROP TRIGGER IF EXISTS curated_condition_single_live ON drugref.curated_condition;
CREATE CONSTRAINT TRIGGER curated_condition_single_live
    AFTER INSERT OR UPDATE ON drugref.curated_condition
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'subject_moiety_uuid', 'object_condition_uuid');

CREATE INDEX IF NOT EXISTS curated_condition_live_key
    ON drugref.curated_condition (subject_moiety_uuid, object_condition_uuid)
    WHERE superseded_by IS NULL;

-- Same two readers, same reasoning as curated_interaction_by_question above.
CREATE INDEX IF NOT EXISTS curated_condition_by_question
    ON drugref.curated_condition (question_uuid);

COMMENT ON TABLE drugref.curated_condition IS
    'CURATED, APPEND-ONLY: drugref''s ruling on a (drug, condition) pair, including '
    'the 168 pairs MED-RT asserts as BOTH an indication and a contraindication with no '
    'qualifier distinguishing them. Keyed on the PAIR, deliberately without '
    '`relationship`: one pair, one judgement, so the beta-blocker/heart-failure ruling '
    'cannot be written twice and disagree with itself. A `spurious` ruling records a '
    'disagreement WITHOUT acting on it -- the candidate stays in its projection and no '
    'view renders either as advice.';
COMMENT ON COLUMN drugref.curated_condition.ruling IS
    'contraindicated | indicated | context_dependent | spurious. All four retire the '
    'pair from the worklist, because all four mean a curator looked. ABSENCE of a row '
    'is the third state -- nobody has looked -- and no value can express it.';

-- ============================================================================
-- 3. The read path -- INNER JOINS, and candidates left exactly as they were
-- ============================================================================
-- db/019 split `induces` into its own table rather than adding a WHERE clause,
-- arguing that a consumer who forgets a filter on a shared table reads a therapeutic
-- claim off the wrong row. The same forgetfulness here -- a LEFT JOIN returning every
-- candidate with a NULL severity beside it -- renders an UNREVIEWED candidate as though
-- a curator had passed it. So these views return ONLY live, asserting curated rows: a
-- consumer must ASK for graded advice, and receives only graded advice.
--
-- THE CANDIDATE VIEWS DO NOT CHANGE, AND THEIR ROW COUNTS MUST NOT MOVE.
-- ddi_candidate_pair stays at 21,664. A `spurious` ruling does NOT delete its
-- candidate: db/027's precedent of letting curation gate a projection (a `deny` policy
-- withholds 233 pairs) governs drugref's own reading of the DAG, which is a different
-- act from contradicting an upstream assertion. Keeping them apart is what keeps "what
-- did the release say" answerable next to "what does drugref say", and keeps the
-- projection reproducible from its source alone.
--
-- Each view is named for WHAT IT MEANS, per db/027's trap: a `spurious` or
-- non-applying row is LIVE (unsuperseded) without BINDING, and the two predicates are
-- not interchangeable.

CREATE OR REPLACE VIEW drugref.curated_ddi_pair AS
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
       p.source           AS candidate_source
FROM   drugref.ddi_candidate_pair p
       -- INNER: an ungraded rule reaches this view NEVER, not with NULL columns.
JOIN   drugref.curated_interaction c
       ON  c.subject_moiety_uuid = p.subject_moiety
       AND c.object_class_uuid   = p.via_class
       AND c.relationship        = p.relationship
WHERE  c.superseded_by IS NULL
AND    c.applies;

COMMENT ON VIEW drugref.curated_ddi_pair IS
    'Drug pairs carrying a live drugref grade, expanded from the class-level rule the '
    'grade was written against -- so ONE curated row reaches every pair its rule '
    'expands to. INNER JOIN by design: an ungraded candidate does not appear here at '
    'all, because a NULL severity beside a real pair reads as "reviewed and harmless". '
    'ddi_candidate_pair remains the place to ask what the release said.';

CREATE OR REPLACE VIEW drugref.curated_condition_ruling AS
SELECT c.subject_moiety_uuid  AS subject_moiety,
       c.object_condition_uuid AS object_condition,
       c.ruling,
       c.severity,
       c.mechanism,
       c.management,
       c.evidence_grade,
       c.question_uuid,
       c.source               AS curated_source,
       c.reviewed_by,
       c.reviewed_against,
       c.reviewed_at,
       cand.candidate_kind,
       cand.relationship,
       cand.source            AS candidate_source
FROM   drugref.curated_condition c
       -- ONE ROW PER (ruling, candidate assertion), NOT one per ruling. The
       -- beta-blocker case returns two rows carrying the same `context_dependent`
       -- ruling, one naming may_treat and one naming CI_with -- which is exactly what
       -- a consumer needs in order to render "both, in different states". Aggregating
       -- the candidates into an array would hide which relationships the ruling
       -- reconciles, and #41's finding was that folding a key component under an
       -- aggregate breaks a view's grain.
JOIN   (SELECT subject_moiety_uuid, object_condition_uuid, relationship, source,
               'contraindication'::text AS candidate_kind
          FROM drugref.moiety_condition_contraindication
        UNION ALL
        SELECT subject_moiety_uuid, object_condition_uuid, relationship, source,
               'indication'
          FROM drugref.moiety_condition_indication) cand
       ON  cand.subject_moiety_uuid   = c.subject_moiety_uuid
       AND cand.object_condition_uuid = c.object_condition_uuid
WHERE  c.superseded_by IS NULL
       -- `spurious` is live and binds nothing: it records a disagreement without
       -- acting on it. Nothing renders it as advice.
AND    c.ruling <> 'spurious';

COMMENT ON VIEW drugref.curated_condition_ruling IS
    'Live drugref rulings on (drug, condition) pairs, joined to the upstream '
    'assertions they rule on -- ONE ROW PER CANDIDATE, so a `context_dependent` ruling '
    'over a pair asserted as both may_treat and CI_with returns both, and a consumer '
    'can see exactly which claims the ruling reconciles. A `spurious` ruling appears '
    'here never; the candidate it disagrees with stays in its projection.';

-- ============================================================================
-- 4. The worklist -- two gap views
-- ============================================================================
-- THESE TEST FOR A LIVE ROW, NOT FOR A LIVE ASSERTING ROW, and the difference from
-- section 3 is deliberate. Every ruling means a curator LOOKED -- including `spurious`
-- and `applies = false` -- so every ruling retires the question. A retired ruling that
-- stayed on the worklist would be asked about every release forever, which is the
-- nagging failure db/027's `withdrawn` and additive_effect's `accumulates` both exist
-- to stop.

CREATE OR REPLACE VIEW drugref.gap_uncurated_condition_contradiction AS
SELECT ci.subject_moiety_uuid    AS subject_moiety,
       ci.object_condition_uuid  AS object_condition,
       sm.display_name,
       cond.name                 AS condition_name,
       count(DISTINCT ind.relationship) AS indication_predicate_count
FROM   drugref.moiety_condition_contraindication ci
       -- The CONTRADICTION is the queue, not uncurated contraindications at large:
       -- 13,463 of those exist and a queue nobody can finish is precisely the stale
       -- generated document these views were built to replace. These 168 are the rows
       -- where the projection tier provably cannot carry the clinical distinction.
JOIN   drugref.moiety_condition_indication ind
       ON  ind.subject_moiety_uuid   = ci.subject_moiety_uuid
       AND ind.object_condition_uuid = ci.object_condition_uuid
JOIN   drugref.substance_moiety sm   ON sm.moiety_uuid    = ci.subject_moiety_uuid
JOIN   drugref.condition cond        ON cond.condition_uuid = ci.object_condition_uuid
WHERE  NOT EXISTS (SELECT 1 FROM drugref.curated_condition c
                    WHERE c.subject_moiety_uuid   = ci.subject_moiety_uuid
                      AND c.object_condition_uuid = ci.object_condition_uuid
                      AND c.superseded_by IS NULL)
GROUP  BY ci.subject_moiety_uuid, ci.object_condition_uuid, sm.display_name, cond.name;

COMMENT ON VIEW drugref.gap_uncurated_condition_contradiction IS
    'The (drug, condition) pairs an upstream release asserts as BOTH an indication and '
    'a contraindication, with no live drugref ruling -- 168 in MED-RT 2026.07.06. The '
    'highest-value curation queue drugref has: every row is a real clinical '
    'distinction MeSH''s descriptor grain cannot carry, not noise. Its grain matches '
    'curated_condition''s natural key exactly, so one question maps to one curatable '
    'row.';

CREATE OR REPLACE VIEW drugref.gap_uncurated_interaction_rule AS
SELECT cc.subject_moiety_uuid AS subject_moiety,
       cc.object_class_uuid   AS object_class,
       cc.relationship,
       sm.display_name,
       sc.class_name,
       -- DISTINCT PARTNERS, NOT JOIN ROWS, and the two coincide only while MED-RT is
       -- the sole permitted source. class_contraindication's primary key includes
       -- `source` (db/006 widened it there deliberately, so a second authority's row is
       -- not swallowed by the first); the join below deliberately OMITS source, because
       -- drugref's judgement is about the clinical fact and not about who asserted it.
       -- Both are right, and together they mean a rule asserted by two authorities
       -- joins every candidate row once per source: count(*) would report 2n, silently
       -- inflating this rule's rank against a single-source neighbour, and breaking the
       -- measured `sum(pair_count) = 21,664` partition. The subject is fixed per group,
       -- so distinct partners ARE distinct drug pairs.
       count(DISTINCT p.partner_moiety) AS pair_count
FROM   drugref.class_contraindication cc
JOIN   drugref.substance_moiety sm ON sm.moiety_uuid = cc.subject_moiety_uuid
JOIN   drugref.substance_class sc  ON sc.class_uuid  = cc.object_class_uuid
       -- RANKED BY THE PAIRS ACTUALLY AT STAKE, not by descendant_class_count. Issue
       -- #36 measured what the other metric costs: gap_unreviewed_expansion_root spent
       -- a curator's explicit `allow` on a root whose expansion was a provable no-op,
       -- because tree bushiness is not the same quantity as fan-out.
       --
       -- INNER, so a rule that pairs with NOBODY drops out of this queue entirely.
       -- Grading it would change nothing, and gap_unpopulated_contraindication already
       -- owns the different question of why its class has no members.
JOIN   drugref.ddi_candidate_pair p
       ON  p.subject_moiety = cc.subject_moiety_uuid
       AND p.via_class      = cc.object_class_uuid
       AND p.relationship   = cc.relationship
WHERE  NOT EXISTS (SELECT 1 FROM drugref.curated_interaction c
                    WHERE c.subject_moiety_uuid = cc.subject_moiety_uuid
                      AND c.object_class_uuid   = cc.object_class_uuid
                      AND c.relationship        = cc.relationship
                      AND c.superseded_by IS NULL)
GROUP  BY cc.subject_moiety_uuid, cc.object_class_uuid, cc.relationship,
          sm.display_name, sc.class_name;

COMMENT ON VIEW drugref.gap_uncurated_interaction_rule IS
    'Class-level CI_MoA/CI_PE rules carrying no live drugref grade, ranked by '
    'pair_count -- the drug pairs the rule actually reaches, which is the fan-out at '
    'stake in the answer. A rule reaching no pair is omitted: grading it is a '
    'provable no-op, and #36 measured what asking such questions costs a curator.';

-- ============================================================================
-- 5. curated_target_unresolved -- an OPERATOR check, not a question
-- ============================================================================
-- A curated row names its candidate by NATURAL KEY and carries no foreign key into it,
-- because candidates are rebuildable projections and an FK would either block the
-- per-source rebuild or cascade curator judgement away with it. The cost of that
-- choice is that a rebuild CAN leave a judgement pointing at a candidate that no longer
-- exists, and nothing would say so. This view says so.
--
-- NOT a gap kind, for expansion_policy_unresolved's reason: a vanished candidate is an
-- upstream-change signal for whoever ran the ingest, not a clinical question for a
-- curator. Expected to be EMPTY.
CREATE OR REPLACE VIEW drugref.curated_target_unresolved AS
SELECT 'curated_interaction'::text AS target_table,
       c.subject_moiety_uuid       AS subject_moiety,
       c.object_class_uuid         AS object_uuid,
       c.relationship,
       c.reviewed_by,
       c.reviewed_against
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
       c.reviewed_against
FROM   drugref.curated_condition c
WHERE  c.superseded_by IS NULL
AND    NOT EXISTS (SELECT 1 FROM drugref.moiety_condition_contraindication x
                    WHERE x.subject_moiety_uuid   = c.subject_moiety_uuid
                      AND x.object_condition_uuid = c.object_condition_uuid)
AND    NOT EXISTS (SELECT 1 FROM drugref.moiety_condition_indication x
                    WHERE x.subject_moiety_uuid   = c.subject_moiety_uuid
                      AND x.object_condition_uuid = c.object_condition_uuid);

COMMENT ON VIEW drugref.curated_target_unresolved IS
    'Live curated rows whose candidate is no longer projected -- a judgement pointing '
    'at nothing after a rebuild. EXPECTED EMPTY. The price of referencing candidates '
    'by natural key instead of by foreign key, which is what stops a per-source '
    'rebuild cascading curator judgement away. An OPERATOR signal, deliberately not a '
    'gap kind: it reports an upstream change, not a clinical question.';

-- ============================================================================
-- 6. Widen open_question.gap_kind -- fourteen in all
-- ============================================================================
-- Widened deliberately, in a migration, exactly as db/007 asks: an unconstrained
-- gap_kind would let a typo mint a whole parallel question namespace that nothing
-- ever reconciles.
--
-- WHAT THE GUARD DOES AND DOES NOT DO. It reads the CURRENT constraint definition
-- rather than assuming db/028's, so re-running THIS FILE is a no-op once it has landed.
-- It does NOT merge: the ADD CONSTRAINT below states all fourteen kinds literally, so
-- replaying this file over a database whose list a LATER migration had widened would
-- narrow it back. apply_migrations makes that unreachable -- it runs each file once and
-- refuses one whose content changed after it was applied -- so the exposure is a
-- hand-run `psql -f`. The instruction to the next author is therefore: add your kind by
-- ADDING A MIGRATION that restates the full list, never by replaying this one.
--
-- Edited into db/029 rather than added as db/030, on db/028's exact precedent: this
-- branch is unmerged, so db/029 is not yet an APPLIED migration anywhere outside it,
-- and editing it in place is the documented exception to "migrations are immutable
-- once applied" while the branch stands. This is section 4's own gap_kinds, so the
-- widening belongs in the same file that introduces them rather than a trailing one
-- with no other content. Add a new migration for the next gap kind after merge.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%uncurated_interaction_rule%') THEN
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
                -- Slice 5c.1: the two kinds a curated row answers, not a lookup
                'uncurated_condition_contradiction', 'uncurated_interaction_rule'));
    END IF;
END $$;

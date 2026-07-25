-- db/008_gap_views.sql
-- drugref global tier, Plan A: the DERIVED GAP VIEWS.
--
-- Design rule: A GAP IS A QUERY, NEVER A REPORT. A generated document is stale the
-- moment it is written and nobody trusts it; as views these are always current,
-- shrink visibly as curation lands, and turn "how much do we not know" into a
-- number that can be watched per release.
--
-- Each view feeds one gap_kind in open_question (db/007), and the mapping is
-- deliberate: view name `gap_X` derives questions of kind `X`.
--
-- ABSENCE CARRIES NO INFORMATION, and these views are the proof. 41 CI rules point
-- at classes no drug is filed under, and MED-RT has no nephrotoxicity concept at
-- all -- so an empty result here means "nothing is MODELLED", never "nothing is
-- wrong". Every COMMENT ON below says so, because db/006 established that `--`
-- comments do not survive to the catalog and the contract has to.

-- ---- 1. the one piece of storage a gap view needed --------------------------
--
-- This table exists solely so the third gap view can be a view at all. The design
-- originally claimed all three were free queries over data already present; that
-- was true of two. medrt_run built the unmatched RxCUI set locally and reported
-- `unmatched_rxcuis=len(unmatched)` -- the integer survived, the identities were
-- discarded when the function returned, and there was nothing in the database to
-- query. A count answers "how many drugs can we not speak about"; only the
-- identities answer "which ones", which is the question worth publishing.
--
-- A rebuildable projection like class_membership, replaced per run: an ingredient
-- that starts matching must LEAVE the list, which an insert-only merge could not
-- express.
CREATE TABLE IF NOT EXISTS drugref.ingest_unmatched_ingredient (
    ingest_run bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- MED-RT's RxNorm ingredient identifier, as it appears in the release.
    rxcui      text   NOT NULL,
    -- Kept so the worklist is human-readable without a second lookup: the whole
    -- point of the registry is that a person or a tool can act on a row.
    name       text,
    PRIMARY KEY (ingest_run, rxcui)
);

CREATE INDEX IF NOT EXISTS ingest_unmatched_ingredient_by_rxcui
    ON drugref.ingest_unmatched_ingredient (rxcui);

COMMENT ON TABLE drugref.ingest_unmatched_ingredient IS
    'RxCUIs a release classified that no moiety in the registry carries. A '
    'REBUILDABLE PROJECTION, replaced per ingest run. Exists so gap_unmatched_'
    'ingredient can be a query: before it, only the COUNT of these survived an '
    'ingest and the identities were discarded.';

-- ---- 2. contraindications naming a class nothing is filed under -------------
--
-- MED-RT asserts the concern and never files a drug under it: 41 of 739 CI rules
-- (5.5%) across 13 distinct classes in the 2026.07.06 release, of which
-- Genitourinary Arterial Vasoconstriction [PE] (7 rules) and Renal Arterial
-- Vasoconstriction [PE] (6) are the largest. These rules can never produce a pair
-- under ANY expansion policy, which is exactly what makes them the highest-value
-- worklist available -- upstream authority already vouching that the answer matters.
--
-- "Nothing filed under it" means nowhere in the class's SUBTREE, not merely
-- directly on it. A parent whose own membership is empty but whose child is
-- populated is not a gap; the concern is answerable one level down. Reading it as
-- direct membership only would report every abstract class in the hierarchy as an
-- open question and bury the 13 real ones.
--
-- The recursive descent is NOT the DAG-descendant expansion of #15 and needs none
-- of its deny-list: this asks only "does ANY drug sit anywhere below", a yes/no
-- that no fan-out concern applies to.
--
-- POPULATED IS PER AXIS, NOT PER CLASS, and the ci_axis join is what makes it so.
-- class_membership admits six axes (has_MoA, has_PE, has_TC, has_PK, has_EPC and
-- MeSH's has_PA); ddi_candidate_pair expands a rule along exactly ONE of them, the
-- one db/006's ci_axis maps its predicate to. Asking merely "does this class have
-- any member at all" therefore answers a different question than the read path
-- does: a CI_PE rule on a class populated only by has_TC members yields no pair,
-- yet a relationship-blind test calls the class populated and HIDES the gap. That
-- is the two-lists-in-two-places failure db/006 exists to prevent, and reasoning
-- about the axis here without consulting ci_axis would have re-created it in a
-- third place. Nothing ties class_membership.relationship to
-- substance_class.concept_type, so the axes coinciding in MED-RT's own data is a
-- property of that release, not a guarantee -- and slice 5b (MeSH-keyed CI, over a
-- vocabulary MeSH populates with has_PA) is where it stops holding.
CREATE OR REPLACE VIEW drugref.gap_unpopulated_contraindication AS
WITH RECURSIVE subtree(root_uuid, class_uuid) AS (
    SELECT DISTINCT ci.object_class_uuid, ci.object_class_uuid
    FROM   drugref.class_contraindication ci
  UNION
    SELECT s.root_uuid, cp.child_class_uuid
    FROM   subtree s
    JOIN   drugref.class_parent cp ON cp.parent_class_uuid = s.class_uuid
),
-- (root, axis) pairs: which membership axes actually have a drug somewhere below
-- this contraindicated class. A rule is dead unless ITS OWN axis appears here.
populated AS (
    SELECT DISTINCT s.root_uuid, m.relationship AS membership_relationship
    FROM   subtree s
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

COMMENT ON VIEW drugref.gap_unpopulated_contraindication IS
    'Contraindications whose object class has no drug filed under it ON THE AXIS THE '
    'RULE EXPANDS OVER (ci_axis), anywhere in the class subtree -- upstream asserts '
    'the concern and never populates it. ci_rule_count counts only the DEAD rules on '
    'that class and is the priority signal for this view; question_worklist does not '
    'order by it. TWO CAVEATS. (1) Population is tested over the whole SUBTREE, while '
    'ddi_candidate_pair expands over DIRECT membership only until descendant '
    'expansion (#15) lands: a rule whose class is populated only via a descendant '
    'yields no pair today yet is deliberately absent here, so this view UNDERSTATES '
    'what currently returns nothing. (2) ABSENCE OF A ROW IS NOT COVERAGE: a hazard '
    'MED-RT never modelled at all appears nowhere here.';

-- ---- 3. moieties no effect class contains -----------------------------------
--
-- PE is the convergence axis an effect-accumulation model needs, so a moiety with
-- no has_PE membership is STRUCTURALLY unable to participate: nothing can ever
-- accumulate for a drug no effect class contains. A drug classified on mechanism
-- (has_MoA) alone is still in this list -- deliberately, because MoA membership
-- does not make an effect add up.
CREATE OR REPLACE VIEW drugref.gap_unclassified_moiety AS
SELECT sm.moiety_uuid,
       sm.display_name,
       sm.first_seen_ingest
FROM   drugref.substance_moiety sm
WHERE  NOT EXISTS (SELECT 1 FROM drugref.class_membership m
                   WHERE  m.moiety_uuid = sm.moiety_uuid
                   AND    m.relationship = 'has_PE');

COMMENT ON VIEW drugref.gap_unclassified_moiety IS
    'Moieties with no has_PE membership: structurally unable to participate in an '
    'effect-accumulation model, because no physiologic effect contains them. A drug '
    'with has_MoA but no has_PE IS listed -- mechanism does not accumulate.';

-- ---- 4. ingredients the registry does not carry -----------------------------
--
-- The join, not the stored row, is what makes this current: once a moiety claims
-- the RxCUI the gap closes with nobody rewriting the ingest table.
--
-- ONE ROW PER RxCUI, from the most recent run that reported it. The stored table is
-- keyed (ingest_run, rxcui) and clear_source_unmatched_ingredients only clears ONE
-- source, so the moment a second source reports unmatched ingredients the same
-- RxCUI is stored twice. Un-deduplicated that is two identical rows here -- and
-- because gap_key is an input to question_uuid, both collapse onto ONE question,
-- so register_from_gaps would silently over-report its own live count. DISTINCT ON
-- makes the view's grain match the question's.
CREATE OR REPLACE VIEW drugref.gap_unmatched_ingredient AS
SELECT DISTINCT ON (u.rxcui)
       u.rxcui,
       u.name,
       r.upstream_release
FROM   drugref.ingest_unmatched_ingredient u
JOIN   drugref.ingest_run r ON r.ingest_run_id = u.ingest_run
WHERE  NOT EXISTS (SELECT 1 FROM drugref.identity_claim ic
                   WHERE  ic.scheme = 'RXNORM_IN'
                   AND    ic.value  = u.rxcui
                   AND    ic.superseded_by IS NULL)
ORDER  BY u.rxcui, u.ingest_run DESC;

COMMENT ON VIEW drugref.gap_unmatched_ingredient IS
    'Ingredients an upstream release classifies that no moiety in the registry '
    'carries -- every one is a drug drugref can say nothing about. Closes by itself '
    'when a moiety claims the RxCUI. Superseded identity claims do not count as '
    'carrying it.';

-- ---- 5. the cost ladder, and the worklist that enforces it ------------------
--
-- The tier order is DATA, not logic buried in a view: which source is cheapest is a
-- fact about the sources, and it changes when a new one lands. Rank ascending =
-- consult first. The ordering exists because §12-I of the design got it backwards
-- twice -- proposing literature mining and hand curation for gaps whose answers were
-- sitting in an openFDA label MED-RT is DERIVED FROM. A question with no
-- openFDA-SPL check has not yet earned literature-mining effort.
--
-- FAERS is deliberately absent: it prioritises the worklist, it does not populate
-- the answer path.
CREATE TABLE IF NOT EXISTS drugref.source_tier (
    source text    PRIMARY KEY,
    rank   integer NOT NULL UNIQUE,
    note   text
);

INSERT INTO drugref.source_tier (source, rank, note) VALUES
    ('MED-RT',      1, 'free, already on disk -- all files, all predicates'),
    ('openFDA-SPL', 2, 'free bulk download; the source MED-RT is derived FROM'),
    ('MeDIC',       3, 'CC0 drug-disease indications/contraindications seed'),
    ('Wikidata',    4, 'CC0 supplement only -- cross-identifiers, candidate leads'),
    ('literature',  5, 'costly, high value; for what none of the above answers')
ON CONFLICT (source) DO NOTHING;

COMMENT ON TABLE drugref.source_tier IS
    'Consultation order for answering an open question, cheapest first. Data rather '
    'than logic, because "which source is cheapest" is a fact about the sources. '
    'FAERS is absent by design: it prioritises the worklist, never populates it.';

-- The worklist: open questions, cheapest-unchecked-tier first.
--
-- `state` comes from question_state's LIVE row, defaulting to 'open' when there is
-- none -- which is what lets the register hold thousands of questions without a
-- state row for any of them. Only `withdrawn` leaves the list; an `answered`
-- question stays, because it keeps accepting evidence and medicine revises.
--
-- `is_current` is the other exclusion, and it is why a question carrying curator
-- work can be RETAINED after its gap closes without haunting the worklist forever.
--
-- The ORDER BY is a convenience for a human reading the view directly. Postgres
-- does not guarantee it survives an outer query that wraps this one, so a consumer
-- that depends on the ordering must restate it -- which is exactly what the tests
-- do rather than leaning on this clause.
CREATE OR REPLACE VIEW drugref.question_worklist AS
SELECT q.question_uuid,
       q.gap_kind,
       q.gap_key,
       q.question_text,
       COALESCE(s.state, 'open')                    AS state,
       -- The rank of the cheapest tier NOT yet checked. NULL (every tier checked)
       -- sorts last: there is nothing cheap left to try.
       (SELECT min(t.rank) FROM drugref.source_tier t
        WHERE NOT EXISTS (SELECT 1 FROM drugref.question_source_check c
                          WHERE c.question_uuid = q.question_uuid
                          AND   c.source = t.source)) AS cheapest_unchecked_rank
FROM   drugref.open_question q
LEFT   JOIN drugref.question_state s
       ON s.question_uuid = q.question_uuid AND s.superseded_by IS NULL
WHERE  COALESCE(s.state, 'open') <> 'withdrawn'
AND    q.is_current
ORDER  BY cheapest_unchecked_rank NULLS LAST, q.gap_kind, q.question_uuid;

COMMENT ON VIEW drugref.question_worklist IS
    'Open questions in the order effort should be spent: cheapest unchecked source '
    'tier first, so the free structured sources are exhausted before literature '
    'mining or hand curation. Withdrawn questions are excluded, as are questions '
    'whose gap has closed (is_current false); ANSWERED ones are NOT, because they '
    'keep accepting evidence. A question with no state row is open. The ORDER BY is '
    'a convenience and is not guaranteed through a wrapping query -- restate it.';

-- The two source vocabularies must agree. question_source_check.source (db/007) is
-- a CHECK, this table is the ladder, and the worklist JOINs them on the literal: a
-- tier spelled one way here and another way there makes every question look
-- never-checked at that tier and re-earns expensive effort forever. There is no
-- parent table to hang a foreign key on -- FAERS is deliberately admissible as a
-- CHECK value while being absent from the ladder -- so the agreement is asserted by
-- test_source_tier_spellings_are_admissible_checks rather than left to a comment.
COMMENT ON COLUMN drugref.source_tier.source IS
    'Must be spelled exactly as question_source_check.source''s CHECK admits it; the '
    'worklist joins the two on this literal. FAERS is intentionally absent: it is a '
    'valid check source but never a rung on the ladder.';

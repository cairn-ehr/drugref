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
CREATE OR REPLACE VIEW drugref.gap_unpopulated_contraindication AS
WITH RECURSIVE subtree(root_uuid, class_uuid) AS (
    SELECT DISTINCT ci.object_class_uuid, ci.object_class_uuid
    FROM   drugref.class_contraindication ci
  UNION
    SELECT s.root_uuid, cp.child_class_uuid
    FROM   subtree s
    JOIN   drugref.class_parent cp ON cp.parent_class_uuid = s.class_uuid
),
populated AS (
    SELECT DISTINCT s.root_uuid
    FROM   subtree s
    JOIN   drugref.class_membership m ON m.class_uuid = s.class_uuid
)
SELECT ci.object_class_uuid          AS class_uuid,
       sc.class_name,
       sc.concept_type,
       count(*)                      AS ci_rule_count,
       max(r.upstream_release)       AS upstream_release
FROM   drugref.class_contraindication ci
JOIN   drugref.substance_class sc ON sc.class_uuid = ci.object_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = ci.ingest_run
WHERE  ci.object_class_uuid NOT IN (SELECT root_uuid FROM populated)
GROUP  BY ci.object_class_uuid, sc.class_name, sc.concept_type;

COMMENT ON VIEW drugref.gap_unpopulated_contraindication IS
    'Contraindications naming a class no drug is filed under, ANYWHERE in its '
    'subtree -- upstream asserts the concern and never populates it, so the rule can '
    'never yield a pair. ci_rule_count is the priority signal. ABSENCE OF A ROW IS '
    'NOT COVERAGE: a hazard MED-RT never modelled at all appears nowhere here.';

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
CREATE OR REPLACE VIEW drugref.gap_unmatched_ingredient AS
SELECT u.rxcui,
       u.name,
       r.upstream_release
FROM   drugref.ingest_unmatched_ingredient u
JOIN   drugref.ingest_run r ON r.ingest_run_id = u.ingest_run
WHERE  NOT EXISTS (SELECT 1 FROM drugref.identity_claim ic
                   WHERE  ic.scheme = 'RXNORM_IN'
                   AND    ic.value  = u.rxcui
                   AND    ic.superseded_by IS NULL);

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
ORDER  BY cheapest_unchecked_rank NULLS LAST, q.gap_kind, q.question_uuid;

COMMENT ON VIEW drugref.question_worklist IS
    'Open questions in the order effort should be spent: cheapest unchecked source '
    'tier first, so the free structured sources are exhausted before literature '
    'mining or hand curation. Withdrawn questions are excluded; ANSWERED ones are '
    'NOT, because they keep accepting evidence. A question with no state row is open.';

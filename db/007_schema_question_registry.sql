-- db/007_schema_question_registry.sql
-- drugref global tier, Plan A: the OPEN-QUESTION REGISTRY.
--
-- The gaps in drugref's coverage -- contraindications naming a class no drug is
-- filed under, moieties nothing classifies, ingredients no moiety carries -- are
-- not defects to be hidden. They are a precisely-stated, externally-addressable
-- worklist, and publishing it is a contribution in its own right. This migration
-- gives each gap a durable identity so external tooling can reference one, and a
-- place to record what has been consulted and what was found.
--
-- THE STORAGE SPLIT IS THE WHOLE DESIGN. This is a hybrid store and each column
-- belongs to exactly one half; the test is "would a rebuild destroy this?":
--
--   * open_question is a REBUILDABLE PROJECTION. It is re-derived from the gap
--     views on every ingest and keyed on a deterministic question_uuid, so the
--     rebuild is an upsert that yields the same UUIDs every time. No append-only
--     floor: a gap that closes must be able to leave.
--
--   * question_state / question_source_check / question_evidence are what a
--     CURATOR or an external notifier contributes. Nothing in the gap views can
--     re-derive them, so they are append-only and keyed off that same immortal
--     UUID. Putting `state` on open_question -- the first design -- would have let
--     each rebuild erase every `withdrawn`, and would have passed every test on a
--     fresh database while failing on the second ingest of a long-lived one.
--
-- THE CASCADE IS THE SEAM BETWEEN THE TWO HALVES, and the two halves contradict
-- each other across it unless the rebuild is careful. Every curated table below is
-- ON DELETE CASCADE from open_question AND append-only with a trigger that refuses
-- DELETE outright. Those are not merely in tension -- they are incompatible: a
-- rebuild that deletes a closed question whose curator rows exist does not lose
-- them quietly, it trips forbid_question_state_rewrite and ABORTS THE INGEST. The
-- first design did exactly that, and stayed green only because no test closed a gap
-- that anyone had curated.
--
-- The registry resolves it in the writer: register_from_gaps deletes only questions
-- with no curated row at all, and RETAINS the rest with is_current false. The
-- cascades stay as a backstop for the untouched-question case, never as the
-- mechanism -- nothing should ever reach them with rows to remove.
--
-- WHY SURROGATE PRIMARY KEYS ON THE CURATED TABLES. Correction-by-overlay means
-- inserting a new row and pointing the old one at it, so both rows carry the same
-- natural key. A primary key on that natural key rejects the correction outright
-- and leaves in-place mutation as the only possibility -- precisely what the
-- overlay exists to prevent. db/001 shipped that bug on identity_claim and db/005
-- repaired it: a surrogate `bigint GENERATED ALWAYS AS IDENTITY`, with uniqueness
-- enforced over LIVE rows only. The surrogate is also the only thing that gives an
-- ordering, without which db/005's strictly-forward rule (superseded_by > id, the
-- property that makes a cycle unrepresentable) could not be checked at all.

-- ---- 1. the derived half ----------------------------------------------------

CREATE TABLE IF NOT EXISTS drugref.open_question (
    -- Deterministic: uuid5(QUESTION_NAMESPACE, gap_kind || ':' || gap_key), minted
    -- by ids.mint_question_uuid. Immortal, because the subjects it derives from are
    -- immortal (class_uuid from (source, code); moiety_uuid outright, per db/005).
    -- This is the key an external tool holds, so the derivation is frozen and
    -- pinned by a literal in tests/test_question_ids.py.
    question_uuid        uuid   PRIMARY KEY,
    gap_kind             text   NOT NULL,
    -- The natural key of the thing the question is ABOUT, in the frozen
    -- SCHEME:value form (CLASS:<uuid>, MOIETY:<uuid>, RXNORM_IN:<rxcui>). An INPUT
    -- to question_uuid, so its format is frozen too.
    gap_key              text   NOT NULL,
    question_text        text   NOT NULL,
    -- NO search_expression COLUMN, deliberately. The design table listed one ("what
    -- was asked, so re-asking is reproducible"), but Plan A asks nothing: it derives
    -- questions, it does not run searches. Shipping a column no writer populates
    -- freezes a guess about a format the plan that actually mines literature has not
    -- made yet -- and migrations are immutable once applied, so the guess would be
    -- permanent. The plan that runs searches adds it, with the shape its searches
    -- need. question_text already carries the searchable statement.
    first_derived_ingest bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- Refreshed by every rebuild that still finds the gap.
    last_derived_ingest  bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- Is the gap STILL derived? Derived data, so it belongs on the derived table.
    --
    -- The rebuild deletes questions whose gap has closed, and deleting is only safe
    -- while the question carries no curator work -- every curated table cascades
    -- from this one. A question that HAS accumulated a state, a source check or a
    -- piece of evidence is retained instead and marked false here, because deleting
    -- it would silently destroy append-only rows whose own contract promises "the
    -- record of what was believed before must survive". Retained questions leave
    -- question_worklist via this flag, so retention costs no noise; if the gap
    -- reopens the rebuild sets it true again under the very same UUID.
    is_current           boolean NOT NULL DEFAULT true,
    -- Plan A ships exactly three kinds. Widen deliberately, in a new migration, as
    -- the curated gap views land -- an unconstrained gap_kind would let a typo mint
    -- a whole parallel question namespace that nothing ever reconciles.
    CONSTRAINT open_question_gap_kind CHECK (gap_kind IN (
        'unpopulated_contraindication', 'unclassified_moiety', 'unmatched_ingredient'))
);

CREATE INDEX IF NOT EXISTS open_question_by_gap_kind
    ON drugref.open_question (gap_kind);

COMMENT ON TABLE drugref.open_question IS
    'The register of what drugref does not yet know: one row per derived gap, keyed '
    'on a deterministic question_uuid an external tool may cite. A REBUILDABLE '
    'PROJECTION of the gap_* views -- re-derived every ingest, rows may come and go. '
    'Curator intent lives in question_state, NOT here, precisely so a rebuild cannot '
    'destroy it. ABSENCE OF A QUESTION IS NOT EVIDENCE OF COVERAGE: a gap nothing '
    'models produces no row at all.';
COMMENT ON COLUMN drugref.open_question.question_uuid IS
    'Immortal and deterministic (uuid5 over gap_kind:gap_key). External references '
    'depend on it: changing the derivation re-mints every question and silently '
    'breaks every citation.';
COMMENT ON COLUMN drugref.open_question.question_text IS
    'The literature-searchable statement, naming its subject rather than referring '
    'to it by UUID so it is usable as a search expression on its own. BUILT BY '
    'CONCATENATING UPSTREAM TEXT (class_name, display_name), so like '
    'question_evidence.reference_value it is untrusted input a consumer must escape '
    'when rendering -- drugref does not sanitise what a release calls a concept.';
COMMENT ON COLUMN drugref.open_question.gap_key IS
    'SCHEME:value -- CLASS:<uuid>, MOIETY:<uuid>, RXNORM_IN:<rxcui>. An input to '
    'question_uuid, therefore frozen.';
COMMENT ON COLUMN drugref.open_question.is_current IS
    'Is the gap still derived by its view? False marks a CLOSED gap whose question '
    'was retained rather than deleted because curator work (state, source check or '
    'evidence) hangs off it and would have cascaded away. Retained questions are '
    'excluded from question_worklist. Pair with last_derived_ingest for "when was '
    'this last seen".';

-- ---- 2. curator state -------------------------------------------------------

CREATE TABLE IF NOT EXISTS drugref.question_state (
    question_state_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question_uuid     uuid        NOT NULL REFERENCES drugref.open_question(question_uuid)
                                  ON DELETE CASCADE,
    state             text        NOT NULL,
    rationale         text,
    -- WHO asserted this, and deliberately unconstrained -- see the same column on
    -- question_evidence. An open set (drugref, a named curator, an external
    -- notifier), unlike question_source_check.source, which is a closed ladder.
    source            text        NOT NULL,
    ingest_run        bigint      NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    asserted_at       timestamptz NOT NULL DEFAULT now(),
    superseded_by     bigint      REFERENCES drugref.question_state(question_state_id),
    -- WITHDRAWN is the only terminal state. "No evidence found" is `open` with recent
    -- question_source_check rows, never a closure: medicine moves, and a question
    -- unanswerable this month may be answerable next.
    CONSTRAINT question_state_state CHECK (state IN (
        'open', 'evidence_under_review', 'answered', 'withdrawn')),
    CONSTRAINT question_state_not_self
        CHECK (superseded_by IS NULL OR superseded_by <> question_state_id)
);

CREATE INDEX IF NOT EXISTS question_state_live
    ON drugref.question_state (question_uuid) WHERE superseded_by IS NULL;

-- Single-live is a DEFERRED constraint, not a unique index, and the asymmetry with
-- question_evidence below is deliberate. superseded_by must reference a row that
-- already exists, so a correction is necessarily INSERT-then-UPDATE and both rows
-- are live for the instant between them. An immediate unique index would reject the
-- only sequence that can express a correction -- re-creating, by a different route,
-- exactly the trap the surrogate key was introduced to escape. question_evidence
-- needs no deferral because its natural key includes the reference, so a correction
-- lands on a different key and never collides.
CREATE OR REPLACE FUNCTION drugref.forbid_multiple_live_states() RETURNS trigger AS $$
DECLARE
    live_count int;
BEGIN
    SELECT count(*) INTO live_count FROM drugref.question_state
        WHERE question_uuid = NEW.question_uuid AND superseded_by IS NULL;
    IF live_count > 1 THEN
        RAISE EXCEPTION
            'drugref.question_state: question % has % live states; exactly one row '
            'per question may have superseded_by IS NULL',
            NEW.question_uuid, live_count;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS question_state_single_live ON drugref.question_state;
CREATE CONSTRAINT TRIGGER question_state_single_live
    AFTER INSERT OR UPDATE ON drugref.question_state
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_states();

-- The append-only floor, modelled on db/005's forbid_claim_rewrite.
CREATE OR REPLACE FUNCTION drugref.forbid_question_state_rewrite() RETURNS trigger AS $$
DECLARE
    target_question uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drugref.question_state is append-only: DELETE forbidden';
    END IF;
    IF NEW.question_uuid <> OLD.question_uuid
       OR NEW.state       <> OLD.state
       OR NEW.ingest_run  <> OLD.ingest_run
       OR NEW.asserted_at <> OLD.asserted_at THEN
        RAISE EXCEPTION
            'drugref.question_state is append-only: only superseded_by may change';
    END IF;
    IF OLD.superseded_by IS NOT NULL
       AND NEW.superseded_by IS DISTINCT FROM OLD.superseded_by THEN
        RAISE EXCEPTION
            'drugref.question_state.superseded_by is one-way: state % is already superseded by %',
            OLD.question_state_id, OLD.superseded_by;
    END IF;
    IF NEW.superseded_by IS NOT NULL THEN
        -- A correction replaces a statement about THIS question. Pointing across is
        -- a merge, and the registry has no merge semantics: question_uuid is immortal.
        SELECT question_uuid INTO target_question FROM drugref.question_state
            WHERE question_state_id = NEW.superseded_by;
        IF target_question <> NEW.question_uuid THEN
            RAISE EXCEPTION
                'drugref.question_state: a state may only be superseded by another state '
                'on the SAME question (% vs %)', NEW.question_uuid, target_question;
        END IF;
        IF NEW.superseded_by <= NEW.question_state_id THEN
            RAISE EXCEPTION
                'drugref.question_state: superseded_by must reference a LATER row (% <= %)',
                NEW.superseded_by, NEW.question_state_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS question_state_append_only ON drugref.question_state;
CREATE TRIGGER question_state_append_only
    BEFORE UPDATE OR DELETE ON drugref.question_state
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_question_state_rewrite();

COMMENT ON TABLE drugref.question_state IS
    'Curator intent about a question, append-only and corrected by overlay. Lives '
    'apart from open_question because that table is rebuilt every ingest and would '
    'erase a `withdrawn`. A question with NO row here is `open` by default, so '
    'thousands may be registered without writing any state at all.';

-- ---- 3. the per-source-tier watermark ---------------------------------------

CREATE TABLE IF NOT EXISTS drugref.question_source_check (
    question_source_check_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question_uuid            uuid        NOT NULL
                                         REFERENCES drugref.open_question(question_uuid)
                                         ON DELETE CASCADE,
    source                   text        NOT NULL,
    -- NOT NULL for every tier, because a NULL would also silently permit unlimited
    -- duplicate checks (NULLs do not conflict in a unique constraint). Each tier
    -- defines its own meaning: a release string for MED-RT, an export date for
    -- openFDA, and for `literature` the ISO date the search ran -- which is the
    -- right answer anyway, since re-asking the literature IS a new search over a
    -- later corpus. The first design made this part of the primary key and could
    -- therefore not record a literature check at all.
    source_version           text        NOT NULL,
    checked_at               timestamptz NOT NULL DEFAULT now(),
    outcome                  text        NOT NULL,
    note                     text,
    -- CHECK-constrained for the same reason severity is: the cheapest-unchecked-tier
    -- ordering JOINs on these literals, so a row spelled 'openfda-spl' does not merely
    -- look untidy -- it makes the question appear never-checked and re-earns expensive
    -- literature effort forever.
    --
    -- This is drugref's SOURCE-TIER vocabulary and it is NOT ids._SOURCE_CANONICAL,
    -- which answers a different question (which authority spelling a class UUID is
    -- minted under, and holds only MED-RT and MeSH). Nothing reconciles the two and
    -- nothing should. The list that must agree with this one is db/008's source_tier
    -- ladder, and because FAERS belongs here while being deliberately absent there,
    -- a foreign key cannot express the relationship -- so it is asserted in
    -- test_source_tier_spellings_are_admissible_checks instead of trusted to a
    -- comment. Adding a tier means editing both lists and that test will say so.
    CONSTRAINT question_source_check_source CHECK (source IN (
        'MED-RT', 'openFDA-SPL', 'MeDIC', 'Wikidata', 'FAERS', 'literature')),
    CONSTRAINT question_source_check_outcome CHECK (outcome IN (
        'covered', 'not_covered', 'partial', 'error')),
    -- Not superseded, ever: this table records OBSERVATIONS, and a re-check against a
    -- newer version is a new observation rather than a correction of the old one.
    CONSTRAINT question_source_check_unique UNIQUE (question_uuid, source, source_version)
);

CREATE INDEX IF NOT EXISTS question_source_check_by_question
    ON drugref.question_source_check (question_uuid, source);

COMMENT ON TABLE drugref.question_source_check IS
    'Which source tier has been consulted for a question, at what version, with what '
    'outcome. WATERMARK, NOT CLOSURE: "no evidence found" leaves the question open '
    'with a recent row here. This is what makes the cost ladder enforceable -- a '
    'question with no openFDA-SPL row has not yet earned literature-mining effort.';

-- ---- 4. evidence ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS drugref.question_evidence (
    question_evidence_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question_uuid        uuid        NOT NULL
                                     REFERENCES drugref.open_question(question_uuid)
                                     ON DELETE CASCADE,
    -- Split from a single free-text `reference` so citations dedupe (the same paper
    -- as a bare DOI, a DOI URL and a PubMed link is otherwise three rows for one
    -- fact) and resolve without guessing. URL stays in the vocabulary because some
    -- evidence has no better identifier -- but as one scheme among several rather
    -- than the implicit default, which keeps an unvalidated link a deliberate choice.
    reference_scheme     text        NOT NULL,
    reference_value      text        NOT NULL,
    verdict              text        NOT NULL,
    -- Constrained like every other vocabulary here, and for the ordinary reason: a
    -- consumer filtering "high-confidence evidence only" against free text silently
    -- drops rows spelled 'High' or 'strong'. NULL stays admissible -- a finding whose
    -- confidence nobody assessed is honest; a finding with an invented level is not.
    confidence           text,
    -- `source` is deliberately UNCONSTRAINED, unlike source above in
    -- question_source_check, and the asymmetry is not an oversight. That column names
    -- one of a CLOSED ladder of source tiers the worklist joins on. This one names
    -- WHO asserted the finding -- drugref itself, a named curator, an external
    -- notifier that may not exist yet -- which is an open set by design. A CHECK here
    -- would mean a new contributor cannot record evidence without a migration.
    source               text        NOT NULL,
    ingest_run           bigint      NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    asserted_at          timestamptz NOT NULL DEFAULT now(),
    superseded_by        bigint      REFERENCES drugref.question_evidence(question_evidence_id),
    CONSTRAINT question_evidence_scheme CHECK (reference_scheme IN (
        'DOI', 'PMID', 'PMCID', 'NCT', 'SPL', 'URL')),
    CONSTRAINT question_evidence_verdict CHECK (verdict IN (
        'supports', 'refutes', 'inconclusive')),
    CONSTRAINT question_evidence_confidence CHECK (
        confidence IS NULL OR confidence IN ('high', 'moderate', 'low')),
    CONSTRAINT question_evidence_not_self
        CHECK (superseded_by IS NULL OR superseded_by <> question_evidence_id)
);

-- Immediate (not deferred, unlike question_state): the natural key includes the
-- reference, so a correction cites something DIFFERENT and never collides with the
-- row it supersedes.
CREATE UNIQUE INDEX IF NOT EXISTS question_evidence_live_unique
    ON drugref.question_evidence (question_uuid, reference_scheme, reference_value)
    WHERE superseded_by IS NULL;

CREATE OR REPLACE FUNCTION drugref.forbid_question_evidence_rewrite() RETURNS trigger AS $$
DECLARE
    target_question uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drugref.question_evidence is append-only: DELETE forbidden';
    END IF;
    IF NEW.question_uuid    <> OLD.question_uuid
       OR NEW.reference_scheme <> OLD.reference_scheme
       OR NEW.reference_value  <> OLD.reference_value
       OR NEW.verdict          <> OLD.verdict
       OR NEW.ingest_run       <> OLD.ingest_run
       OR NEW.asserted_at      <> OLD.asserted_at THEN
        RAISE EXCEPTION
            'drugref.question_evidence is append-only: only superseded_by may change';
    END IF;
    IF OLD.superseded_by IS NOT NULL
       AND NEW.superseded_by IS DISTINCT FROM OLD.superseded_by THEN
        RAISE EXCEPTION
            'drugref.question_evidence.superseded_by is one-way: evidence % is already '
            'superseded by %', OLD.question_evidence_id, OLD.superseded_by;
    END IF;
    IF NEW.superseded_by IS NOT NULL THEN
        SELECT question_uuid INTO target_question FROM drugref.question_evidence
            WHERE question_evidence_id = NEW.superseded_by;
        IF target_question <> NEW.question_uuid THEN
            RAISE EXCEPTION
                'drugref.question_evidence: evidence may only be superseded by evidence '
                'on the SAME question (% vs %)', NEW.question_uuid, target_question;
        END IF;
        IF NEW.superseded_by <= NEW.question_evidence_id THEN
            RAISE EXCEPTION
                'drugref.question_evidence: superseded_by must reference a LATER row (% <= %)',
                NEW.superseded_by, NEW.question_evidence_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS question_evidence_append_only ON drugref.question_evidence;
CREATE TRIGGER question_evidence_append_only
    BEFORE UPDATE OR DELETE ON drugref.question_evidence
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_question_evidence_rewrite();

COMMENT ON TABLE drugref.question_evidence IS
    'Findings against a question, append-only and supersedable: medicine revises, and '
    'the record of what was believed before must survive the revision. CURATED IS NOT '
    'VERIFIED -- a verdict here is an assertion by whoever `source` names, and '
    'reference_value (especially scheme URL) is author-supplied text a consumer must '
    'treat as untrusted when rendering it.';

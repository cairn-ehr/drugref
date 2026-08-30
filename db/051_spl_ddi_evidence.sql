-- db/051_spl_ddi_evidence.sql
-- =============================================================================
-- SPL section 34073-7 DRUG INTERACTIONS as drugref's fourth interaction
-- candidate source: for each label whose SUBJECT drug resolves, the known
-- moieties its interactions section NAMES -- as evidence, with offsets, a
-- citation and a bounded quoted window.
--
-- Design: docs/superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md
-- Measurements it rests on, and where this file and one of them disagree the
-- MEASUREMENT wins:
--   .../2026-08-24-drugref-slice-5c3-spl-mining-measurement.md
--   .../2026-08-24-drugref-slice-5c3-subject-recovery-measurement.md
--
-- WHAT THIS SLICE DELIBERATELY DOES NOT DO, each settled by measurement rather
-- than preference:
--
--  * NO RELATION EXTRACTION. Deciding that a sentence means `contraindicated`
--    rather than `monitor` is a clinical reading. The standing rule is *ingest
--    preserves evidence; curation creates clinical judgement*, and this slice
--    holds that line: it records that two drugs are named together in an
--    interactions section, never what the label says about them.
--  * NO CLASS GRAIN (owner's call, 2026-08-24). 32.3% of class occurrences name
--    an EMPTY class, MED-RT's PK axis is 97.2% empty and is not a drug-class
--    vocabulary (issue 155), and `Diuretics` (MeSH) and `Diuretic [APC]`
--    (MED-RT) fold to one string with no cross-source class identity. Its own
--    slice, with its own measurement.
--  * NO POTENCY BAND. The band is PAIR-scoped, not class-scoped -- FDA's
--    footnote 20 bands ciprofloxacin *moderate* and names tizanidine as the
--    substrate against which it behaves *strong* -- and reading a band off prose
--    is relation extraction by another name. It belongs on a curated assertion;
--    this slice's contribution to issue 102 is the measurement that retired its
--    options 1 and 2, not a column.
--
-- RULE 6 IN ONE LINE: the two publishers of this corpus take OPPOSITE positions
-- -- NLM disclaims ("cannot guarantee the copyright status for any item") over
-- labeling "submitted to the FDA by companies", while openFDA dedicates the same
-- bytes CC0 1.0 -- so the unit of clearance is the COLUMN, as it was for DIRIL.
-- Occurrences and offsets are facts and are clear under either reading; a
-- set_id/version citation is not a copy; a BOUNDED QUOTED WINDOW is admitted
-- under the owner's determination on issue 154 and is bounded by section 5's
-- budget; THE SECTION TEXT IN FULL IS NOT STORED, under either reading.
--
-- WHAT JUSTIFIES THE SLICE: at least 29,258 distinct candidate pairs, 25,960
-- (88.7%) novel. DrugCentral's entire slice was justified on 7,501 at 91% new.
-- =============================================================================

-- ============================================================================
-- 1. Source admission -- three edits, one commit
-- ============================================================================
-- db/049's comment names this failure mode and it is copied here deliberately,
-- because the failure is SILENT: `ids.canonical_source` folds the source to a
-- spelling the CHECK does not admit, and a per-source rebuild then deletes
-- nothing and reports success.
--
--   * drugref.ingest_run `source` CHECK gains 'SPL';
--   * drugref.ingest_run `writer` CHECK gains 'spl_run';
--   * src/drugref/ids.py gains "SPL": "SPL" and
--     src/drugref/provenance.py gains 'spl_run', in the SAME commit.
--
-- COPIED FROM THE LIVE CATALOG AND THEN EXTENDED BY ONE VALUE, never retyped
-- from a document. db/039's comment records a stale plan list that still said
-- ('MED-RT','MeSH') and would have DROPPED 'DRUGREF'.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF', 'GSRS',
                      'ONCHIGH', 'FDA-CYP', 'DRUGCENTRAL', 'SPL'));

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run',
                      'onchigh_run', 'fda_cyp_run', 'drugcentral_run', 'spl_run'));

-- NOTE what is deliberately NOT widened, on db/049's precedent:
-- class_contraindication_source stays ('MED-RT','ONCHIGH'),
-- moiety_contraindication_source stays ('MED-RT'), and
-- ddi_source_severity_source stays ('DRUGCENTRAL'). SPL writes no class rule, no
-- moiety contraindication and NO SEVERITY AT ALL -- reading a grade off prose is
-- the relation extraction this slice refuses.

-- ============================================================================
-- 2. drugref.spl_wording -- the statement, IDENTIFIED but not stored
-- ============================================================================
-- WHY A WORDING TABLE AT ALL. The corpus is 68,550 section-carrying labels
-- carrying 27,406 DISTINCT wordings -- 2.50 labels to one, because it is
-- dominated by generic labels reprinting one manufacturer's words. Storing
-- occurrences per LABEL would multiply every downstream count by that factor,
-- which is the exact error the 2026-08-13 source evaluation made and the mining
-- measurement was written to prevent.
--
-- THERE IS NO PROSE COLUMN HERE, IN ANY FORM. The wording is IDENTIFIED by the
-- digest of its normalised text and quoted only through section 5, under that
-- section's budget. A `text` column added here would make the budget
-- unenforceable in one edit, silently, which is why its absence is stated in the
-- catalog comment as well as in this one.
CREATE TABLE IF NOT EXISTS drugref.spl_wording (
    ingest_run  bigint  NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- Symmetric with every other projection's: widened per source as authorities
    -- land, not left open.
    source      text    NOT NULL
        CONSTRAINT spl_wording_source CHECK (source = 'SPL'),
    -- SHA-256 of the WHITESPACE-NORMALISED section text. Reformatting is not a
    -- new statement: two labels carrying identical wording under different
    -- line-wrapping would otherwise be two wordings and would overstate the
    -- corpus by the difference.
    text_key    text    NOT NULL,
    -- The denominator the quote budget is spent against, in characters of the
    -- NORMALISED text -- which is also the string every offset in this schema
    -- indexes. Measured 2026-08-27: mean 3,808.8, minimum 17.
    char_length integer NOT NULL,
    -- How many labels carry this wording. The de-duplication factor, stored
    -- rather than derived, so a reader cannot quote a label count as a wording
    -- count without passing this column on the way.
    label_count integer NOT NULL,
    PRIMARY KEY (ingest_run, source, text_key),
    -- The digest's SHAPE, so a producer that changed hash is refused at the table
    -- rather than quietly filling it with keys nothing joins to.
    CONSTRAINT spl_wording_key_shape CHECK (text_key ~ '^[0-9a-f]{64}$'),
    -- db/050's `upstream_key <> ''` lesson at the other end: a zero-length
    -- wording has a zero budget and can carry no evidence, so it is a malformed
    -- row rather than a difficult one. The parser already folds blank-but-present
    -- sections into absent; this refuses one that got through anyway.
    CONSTRAINT spl_wording_char_length_positive CHECK (char_length > 0),
    CONSTRAINT spl_wording_label_count_positive CHECK (label_count > 0)
);

COMMENT ON TABLE drugref.spl_wording IS
    'One row per DISTINCT section-34073-7 wording, IDENTIFIED BY DIGEST AND NOT '
    'STORED -- there is deliberately no prose column here, and adding one would '
    'make db/051 section 5''s quote budget unenforceable in a single edit. '
    'Measured 2026-08-27: 68,550 section-carrying labels carry 27,406 wordings, '
    '2.50 to one, so every rate in this slice is quoted PER WORDING -- counting '
    'labels multiplies it by the de-duplication factor, which is the error the '
    '2026-08-13 source evaluation made. A REBUILDABLE PROJECTION, CANDIDATE '
    'TIER: nothing here is a drugref judgement, and nothing may auto-alert.';
COMMENT ON COLUMN drugref.spl_wording.char_length IS
    'Characters of the NORMALISED text -- the same string spl_entity_occurrence '
    'and spl_wording_quote index. The quote budget is ceil(0.25 * this).';

-- ============================================================================
-- 3. drugref.spl_label -- one row per label, no prose
-- ============================================================================
-- Identity and provenance for every section-carrying label, INCLUDING the ones
-- whose subject did not resolve, because that is the recovery register (section
-- 8) and 19,862 labels absent from today's DailyMed release may be in
-- tomorrow's. The standing rule: *absence is a population, not a bug*.
CREATE TABLE IF NOT EXISTS drugref.spl_label (
    ingest_run     bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source         text   NOT NULL
        CONSTRAINT spl_label_source CHECK (source = 'SPL'),
    -- The citation, and the join key to BOTH corpora: openFDA publishes the
    -- section under this set_id and DailyMed publishes the XML the subject is
    -- recovered from under the same one.
    set_id         text   NOT NULL,
    version        text   NOT NULL,
    effective_time text,
    -- NULLABLE, and ABSENCE IS A POPULATION: openFDA populates it on only 86,574
    -- of 262,032 records. A NOT NULL here would have refused most of the corpus
    -- to record a field nothing in this slice reads.
    product_type   text,
    text_key       text   NOT NULL,
    -- A LABEL IS KEYED ON (set_id, version), NOT set_id ALONE. A revised label is
    -- a new document making its own statement; collapsing versions would
    -- silently prefer whichever the reader happened to ingest last. Measured
    -- 2026-08-27 over all 68,550: set_id, version and effective_time are
    -- populated on 100%, and (set_id, version) never repeats -- openFDA ships the
    -- CURRENT version only. DailyMed does NOT, which is why the DailyMed reader
    -- carries a de-duplication policy and this table needs none.
    PRIMARY KEY (ingest_run, source, set_id, version),
    FOREIGN KEY (ingest_run, source, text_key)
        REFERENCES drugref.spl_wording (ingest_run, source, text_key),
    -- db/050 section 1's lesson, transplanted. A blank key is a valid row as far
    -- as a count is concerned, it sorts before every real key, and the first one
    -- is already a defect -- so the FIRST one aborts rather than the second
    -- colliding. Measured 2026-08-27: 0 of 68,550.
    CONSTRAINT spl_label_set_id_present CHECK (set_id <> ''),
    CONSTRAINT spl_label_version_present CHECK (version <> '')
);

CREATE INDEX IF NOT EXISTS spl_label_by_wording
    ON drugref.spl_label (ingest_run, source, text_key);

COMMENT ON TABLE drugref.spl_label IS
    'One row per section-34073-7-carrying label, INCLUDING every label whose '
    'subject did not resolve -- that population is the recovery register '
    '(gap_unresolved_spl_subject), not a set of failures to drop. Keyed on '
    '(set_id, version) because a revised label is a new document making its own '
    'statement. NO PROSE: the wording is spl_wording''s digest and is quoted only '
    'through spl_wording_quote. Measured 2026-08-27: 68,550 labels, 27,406 '
    'wordings.';

-- ============================================================================
-- 4. drugref.spl_label_subject -- the route column, on db/049's terms
-- ============================================================================
-- The subject drug, and HOW it resolved or WHY it did not. The design is
-- drugcentral_ddi_assertion's, because the problem is the same one.
--
-- THE MEASURED POPULATION OF EACH ROUTE (2026-08-25, and see the note on the
-- 14,455 below, which is why they are floors):
--
--   openfda_unii              resolves   27,494  openFDA's own openfda.unii
--   dailymed_active_moiety    resolves    6,498  <activeMoiety> under an ACTIVE
--                                                ingredient in DailyMed's XML
--   dailymed_active_substance resolves       16  the SALT only -- issue 67,
--                                                counted apart so it cannot hide
--   absent_from_dailymed      no          19,862  not in the current Human Rx
--                                                release
--   unresolved                no          14,680  present, read, still unkeyable
--                                                -- incl. 200 labels carrying a
--                                                UNII drugref does not hold
--
-- THE 14,680 INCLUDES 14,455 LABELS THE MEASUREMENT NEVER SCANNED: unkeyed labels
-- sharing a wording with a keyed one, skipped as a probe optimisation because
-- they cannot rescue a WORDING. THE INGEST SCANS THEM -- a label's subject is its
-- own, and one sharing another's wording may be a different drug. Their pairs
-- are uncounted, which is why every pair figure in this slice is a FLOOR and the
-- orchestrator's check asserts >=, never ==.
--
-- A LABEL MAY CARRY MORE THAN ONE SUBJECT -- combination products are ordinary --
-- so this is a separate table rather than columns on spl_label.
--
-- ⇒ ONE SUBJECT PER LABEL PER ROUTE, AND THE SALT IS NOT A SECOND SUBJECT. The
-- route table above is EXCLUSIVE by construction, and the ingest keys subjects
-- the way spl_dailymed.subject_uniis does: the moiety where a moiety UNII
-- resolves, the salt ONLY where none does. Blending the two published 31,618
-- pairs where the exclusive rule gives 29,258, because drugref registers a salt
-- as its own moiety with its own live UNII claim, so a salt product paired twice
-- -- on 56.7% of resolvable DailyMed labels.
CREATE TABLE IF NOT EXISTS drugref.spl_label_subject (
    ingest_run      bigint   NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source          text     NOT NULL
        CONSTRAINT spl_label_subject_source CHECK (source = 'SPL'),
    set_id          text     NOT NULL,
    version         text     NOT NULL,
    -- A surrogate, and it exists for one reason: moiety_uuid is NULLABLE and a
    -- nullable column cannot be part of a primary key. Ordering is the writer's
    -- (sorted UNII order), so two runs over one release agree.
    subject_ordinal smallint NOT NULL,
    -- NULLABLE, and that is the whole design of this table. An unresolved label
    -- STAYS, with a route saying why -- db/049's drugcentral_ddi_assertion, for
    -- the same reason.
    moiety_uuid     uuid     REFERENCES drugref.substance_moiety(moiety_uuid),
    route           text     NOT NULL,
    PRIMARY KEY (ingest_run, source, set_id, version, subject_ordinal),
    FOREIGN KEY (ingest_run, source, set_id, version)
        REFERENCES drugref.spl_label (ingest_run, source, set_id, version),
    -- THE ROUTE CHECK IS THE VOCABULARY'S SECOND HOME, admitted deliberately on
    -- exactly the terms drugcentral_ddi_assertion_route_1 lives under, and pinned
    -- by a test that reads the Python vocabulary and this CHECK and compares them.
    CONSTRAINT spl_label_subject_route CHECK (route IN (
        'openfda_unii', 'dailymed_active_moiety', 'dailymed_active_substance',
        'absent_from_dailymed', 'unresolved')),
    -- ONE CHECK, not two columns nobody cross-checks -- db/049's
    -- ..._endpoint_1_complete shape. "Resolved but no uuid" and "a uuid on an
    -- unresolved route" are both UNREPRESENTABLE rather than merely discouraged.
    CONSTRAINT spl_label_subject_complete CHECK (
        (route IN ('openfda_unii', 'dailymed_active_moiety',
                   'dailymed_active_substance')) = (moiety_uuid IS NOT NULL)),
    CONSTRAINT spl_label_subject_ordinal_nonnegative CHECK (subject_ordinal >= 0)
);

-- One label may not name one moiety twice. A combination product carries several
-- subjects; a repeat is a writer defect, and it would double that pair's evidence
-- rows in the read path with nothing to say so.
CREATE UNIQUE INDEX IF NOT EXISTS spl_label_subject_one_row_per_moiety
    ON drugref.spl_label_subject (ingest_run, source, set_id, version, moiety_uuid);

-- An UNRESOLVED label has exactly ONE subject row. "No subject" is a single
-- statement about a label, and two of them would double-count it in the recovery
-- register -- which is the population every future recovery route is sized
-- against.
CREATE UNIQUE INDEX IF NOT EXISTS spl_label_subject_one_unresolved_row
    ON drugref.spl_label_subject (ingest_run, source, set_id, version)
    WHERE moiety_uuid IS NULL;

CREATE INDEX IF NOT EXISTS spl_label_subject_by_moiety
    ON drugref.spl_label_subject (moiety_uuid);

COMMENT ON TABLE drugref.spl_label_subject IS
    'WHICH drug a label''s interactions section is ABOUT, and how it resolved or '
    'why it did not -- drugcentral_ddi_assertion''s nullable-uuid-plus-route '
    'design, because the problem is the same one. A label may carry SEVERAL '
    'subjects (combination products are ordinary), but ONE ROUTE: the routes are '
    'exclusive by construction, and the subject is the MOIETY where a moiety UNII '
    'resolves and the SALT only where none does. Blending those two published '
    '31,618 pairs where the exclusive rule gives 29,258, because drugref '
    'registers a salt as its own moiety. Measured 2026-08-25: 27,494 on '
    'openfda_unii, 6,498 + 16 through DailyMed, 19,862 absent from that release '
    'and 14,680 unresolved.';
COMMENT ON COLUMN drugref.spl_label_subject.route IS
    'HOW the subject resolved, or why it did not. The SECOND HOME of '
    'drugref.ingest.spl_run.SUBJECT_ROUTES, pinned by a test comparing this '
    'CHECK against the Python tuple. `dailymed_active_substance` is the SALT '
    'grain and is counted apart precisely so it cannot be credited as recovery: '
    'it needs the salt-to-base step of issue 67, which does not exist.';

-- ============================================================================
-- 5. drugref.spl_wording_quote -- the bounded window, and the budget as a RULE
-- ============================================================================
-- THE ONLY PROSE drugref stores from this corpus, admitted under the owner's
-- issue-154 determination and bounded by the trigger below.
--
-- THE RULE (measured; owner's call, 2026-08-24): +/-60 characters around the
-- FIRST occurrence of each distinct moiety, kept in DOCUMENT order, until 25% of
-- char_length is spent. Windows are merged, and one that would exceed the budget
-- is SKIPPED rather than truncated -- truncating cuts a quote mid-word.
--
-- DOCUMENT ORDER, NOT "PAIR PRIORITY": priority order would make the stored bytes
-- depend on which pairs the registry happens to resolve, and a licensing
-- constraint whose result moves with the vocabulary is not a constraint.
--
-- MEASURED over all 26,721 wordings naming a moiety: 20.4% of a section stored on
-- average, median 22.7%, 5.1 merged windows per wording, covering 71.6% of the
-- distinct moieties named. THE ALTERNATIVES AND WHY THEY LOSE: a per-occurrence
-- window is not a quote, it is the section reassembled -- the containing sentence
-- stores 82.7% and +/-120 characters 89.0%.
--
-- THE 28.4% OF MOIETIES WITH NO WINDOW LOSE ONLY THE WINDOW. Their occurrence,
-- offsets and citation are stored regardless, because section 1's rule-6 line
-- clears those under either reading.
CREATE TABLE IF NOT EXISTS drugref.spl_wording_quote (
    ingest_run bigint   NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source     text     NOT NULL
        CONSTRAINT spl_wording_quote_source CHECK (source = 'SPL'),
    text_key   text     NOT NULL,
    -- Window order within the wording -- document order, which is also the order
    -- the budget was spent in, so a reader reassembling the stored windows gets
    -- them in the order the label wrote them.
    ordinal    smallint NOT NULL,
    char_start integer  NOT NULL,
    char_end   integer  NOT NULL,
    quote_text text     NOT NULL,
    PRIMARY KEY (ingest_run, source, text_key, ordinal),
    FOREIGN KEY (ingest_run, source, text_key)
        REFERENCES drugref.spl_wording (ingest_run, source, text_key),
    CONSTRAINT spl_wording_quote_span
        CHECK (char_start >= 0 AND char_end > char_start),
    -- THE STORED TEXT MUST BE EXACTLY AS LONG AS ITS OFFSETS CLAIM. A writer that
    -- cut the RAW text while offsetting the NORMALISED one -- the one mistake
    -- this schema's offsets are most exposed to, since normalisation changes
    -- length by a variable amount -- lands here rather than in a reader's hands.
    CONSTRAINT spl_wording_quote_length
        CHECK (length(quote_text) = char_end - char_start)
);

COMMENT ON TABLE drugref.spl_wording_quote IS
    'THE ONLY PROSE drugref stores from SPL: +/-60 characters around the FIRST '
    'occurrence of each distinct moiety, in DOCUMENT order, to a hard budget of '
    '25% of the wording''s characters. Admitted under the owner''s determination '
    'on issue 154 (2026-08-24) -- bundle a quoted window only, neither '
    'reference-only nor the full prose. THE BUDGET IS ENFORCED BY A DEFERRED '
    'CONSTRAINT TRIGGER, not by convention: measured, an unbounded '
    'per-occurrence window stores 82.7% of a section (containing sentence) or '
    '89.0% (+/-120 chars), so a rule that were merely intended would make "a '
    'quoted window" and "the prose" the same act. The shipped rule stores 20.4% '
    'on average and covers 71.6% of the moieties named; the other 28.4% lose '
    'ONLY the window -- their occurrence, offsets and citation are stored '
    'regardless.';

-- ---------------------------------------------------------------------------
-- The budget, as a DEFERRED CONSTRAINT TRIGGER
-- ---------------------------------------------------------------------------
-- WHY A TRIGGER AND NOT A CHECK: the budget is a property of a WORDING and the
-- rows are WINDOWS, so no row-local CHECK can see it. Deferred, because the
-- writer inserts windows one at a time and an immediate check would refuse a
-- legal final state on the way to it.
--
-- THIS IS db/050'S LESSON TAKEN BEFORE THE REVIEW ROUND INSTEAD OF DURING IT.
-- That round's finding was that every guard in a slice passed VACUOUSLY: the
-- failure mode here is silent, additive and visible only in aggregate -- exactly
-- the shape that survives a suite. So the test for this trigger constructs a
-- wording whose windows exceed the budget and asserts it is REFUSED; a budget
-- nobody demonstrated rejecting anything is that finding waiting to recur.
--
-- IT CHECKS THREE THINGS, and the second and third are what make the first mean
-- what it says:
--   (a) the summed window length does not exceed ceil(0.25 * char_length);
--   (b) no two windows of one wording OVERLAP -- so (a)'s sum IS the count of
--       distinct characters stored, which is the only unit a licensing
--       determination can be argued from;
--   (c) no window names a character the wording does not have -- a stored quote
--       nobody can cut back out of the source is not a citation.
CREATE OR REPLACE FUNCTION drugref.spl_wording_quote_budget()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    wording_length integer;
    allowed        integer;
    spent          integer;
    -- NOT named `overlaps`: that is a RESERVED SQL keyword in PostgreSQL (the
    -- period-overlap operator), and a plpgsql variable using it parses as the
    -- operator -- 'syntax error at or near ">"', pointing at a line that is
    -- correct.
    overlap_pairs  integer;
    over_end       integer;
BEGIN
    SELECT w.char_length INTO wording_length
      FROM drugref.spl_wording w
     WHERE w.ingest_run = NEW.ingest_run
       AND w.source     = NEW.source
       AND w.text_key   = NEW.text_key;

    -- The foreign key already guarantees this, and it is checked anyway: a
    -- deferred trigger runs at COMMIT, and a NULL here would make every
    -- comparison below NULL and let the whole budget pass silently. A guard that
    -- cannot fire is the defect this trigger exists to avoid, so it may not
    -- contain one.
    IF wording_length IS NULL THEN
        RAISE EXCEPTION
            'spl_wording_quote: no spl_wording row for text_key % in run %',
            NEW.text_key, NEW.ingest_run;
    END IF;

    -- ceil, not floor -- the identical expression drugref.ingest.spl_quote
    -- .quote_budget computes in Python, and a test compares the two so the
    -- budget's two homes cannot disagree.
    allowed := ceil(0.25 * wording_length);

    SELECT coalesce(sum(q.char_end - q.char_start), 0),
           count(*) FILTER (WHERE q.char_end > wording_length)
      INTO spent, over_end
      FROM drugref.spl_wording_quote q
     WHERE q.ingest_run = NEW.ingest_run
       AND q.source     = NEW.source
       AND q.text_key   = NEW.text_key;

    IF over_end > 0 THEN
        RAISE EXCEPTION
            'spl_wording_quote: % window(s) for text_key % end past the '
            'wording''s % characters; a quote nobody can cut back out of the '
            'source is not a citation',
            over_end, NEW.text_key, wording_length;
    END IF;

    SELECT count(*) INTO overlap_pairs
      FROM drugref.spl_wording_quote a
      JOIN drugref.spl_wording_quote b
        ON  b.ingest_run = a.ingest_run
       AND  b.source     = a.source
       AND  b.text_key   = a.text_key
       AND  b.ordinal    > a.ordinal
       AND  b.char_start < a.char_end
       AND  a.char_start < b.char_end
     WHERE a.ingest_run = NEW.ingest_run
       AND a.source     = NEW.source
       AND a.text_key   = NEW.text_key;

    IF overlap_pairs > 0 THEN
        RAISE EXCEPTION
            'spl_wording_quote: % overlapping window pair(s) for text_key %; '
            'windows must be merged before storage so the summed length IS the '
            'count of distinct characters stored',
            overlap_pairs, NEW.text_key;
    END IF;

    IF spent > allowed THEN
        RAISE EXCEPTION
            'spl_wording_quote: % characters stored for text_key %, over the '
            'budget of % (25%% of %). Issue 154''s determination is a quoted '
            'window, and an unbounded one reassembles the section',
            spent, NEW.text_key, allowed, wording_length;
    END IF;

    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION drugref.spl_wording_quote_budget() IS
    'Enforces the issue-154 quoted-window determination: per wording, the '
    'summed window length may not exceed ceil(0.25 * char_length), windows may '
    'not overlap (so that sum IS the distinct characters stored), and no window '
    'may name a character the wording does not have. DEFERRED, because the '
    'writer inserts windows one at a time and the budget is a property of the '
    'final state.';

DROP TRIGGER IF EXISTS spl_wording_quote_within_budget ON drugref.spl_wording_quote;
CREATE CONSTRAINT TRIGGER spl_wording_quote_within_budget
    AFTER INSERT OR UPDATE ON drugref.spl_wording_quote
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.spl_wording_quote_budget();

-- ============================================================================
-- 6. drugref.spl_entity_occurrence -- the derived facts
-- ============================================================================
-- One row per recognised moiety span. Facts and offsets, no prose: clear under
-- either publisher's reading of rule 6, which is why they are stored for every
-- match whether or not the budget could afford a window over it.
--
-- CLASS OCCURRENCES ARE NOT STORED (section 1). Storing them would require
-- answering issue 155 first.
CREATE TABLE IF NOT EXISTS drugref.spl_entity_occurrence (
    ingest_run      bigint  NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source          text    NOT NULL
        CONSTRAINT spl_entity_occurrence_source CHECK (source = 'SPL'),
    text_key        text    NOT NULL,
    char_start      integer NOT NULL,
    char_end        integer NOT NULL,
    moiety_uuid     uuid    NOT NULL
        REFERENCES drugref.substance_moiety(moiety_uuid),
    -- TRUE when the span folded onto MORE THAN ONE registry entry.
    --
    -- IT EXISTS BECAUSE AMBIGUITY IS UNRESOLVED, NEVER "PICK THE FIRST" --
    -- FDA-CYP's rule. Measured: 24 folded keys carry more than one registry name,
    -- covering 55 of 19,438 (0.28%), mostly stereoisomers whose punctuation
    -- suffix the fold strips ('carvone, (+)-'). THE DIRECTION MATTERS FOR DDI
    -- SPECIFICALLY: S- and R-warfarin take different CYP pathways. Every
    -- colliding entry gets a row and the flag is set; nothing downstream may
    -- silently choose.
    match_ambiguous boolean NOT NULL,
    -- One span may legitimately carry several rows -- one per colliding entry --
    -- so the moiety is part of the key. char_end is not: a span is identified by
    -- where it starts and what it resolved to, and two entries folding onto one
    -- span share both ends by construction.
    PRIMARY KEY (ingest_run, source, text_key, char_start, moiety_uuid),
    FOREIGN KEY (ingest_run, source, text_key)
        REFERENCES drugref.spl_wording (ingest_run, source, text_key),
    CONSTRAINT spl_entity_occurrence_span
        CHECK (char_start >= 0 AND char_end > char_start)
);

CREATE INDEX IF NOT EXISTS spl_entity_occurrence_by_moiety
    ON drugref.spl_entity_occurrence (moiety_uuid);
CREATE INDEX IF NOT EXISTS spl_entity_occurrence_by_wording
    ON drugref.spl_entity_occurrence (ingest_run, source, text_key);

COMMENT ON TABLE drugref.spl_entity_occurrence IS
    'Every known moiety a section-34073-7 wording NAMES, with the exact span. '
    'DERIVED FACTS AND OFFSETS ONLY -- no prose, so clear under either '
    'publisher''s reading of rule 6, and stored for every match whether or not '
    'the quote budget could afford a window over it. The match rule is the '
    'SHIPPED resolver''s: exact, case-insensitive, contiguous, whole-token, '
    'longest-match-wins over substance_moiety.display_name. It asserts NO '
    'RELATION: that two drugs are named in one interactions section is what this '
    'table says, and what the label MEANS by it is curation''s job. Class '
    'occurrences are deliberately absent -- storing them needs issue 155 '
    'answered first.';
COMMENT ON COLUMN drugref.spl_entity_occurrence.match_ambiguous IS
    'The span folded onto more than one registry entry, so EVERY one of them has '
    'a row here and none was chosen. Measured: 24 folded keys over 55 of 19,438 '
    'moieties (0.28%), mostly stereoisomers -- and S- and R-warfarin take '
    'different CYP pathways, so the direction is not cosmetic.';

-- ============================================================================
-- 7. The read path -- TWO views, at two grains, each named for its own
-- ============================================================================
-- THE GRAIN IS IN THE NAME ON PURPOSE. This project has published a figure in
-- the wrong unit in three consecutive rounds -- labels quoted as wordings, rows
-- quoted as labels, occurrences quoted as pairs -- so the view whose count IS
-- the pair count is called ..._pair, and the one carrying a row per citation is
-- called ..._evidence. A consumer who counts the wrong one gets a name that
-- contradicts them.

-- ---------------------------------------------------------------------------
-- 7a. drugref.spl_ddi_evidence -- one row per (pair, citing label)
-- ---------------------------------------------------------------------------
-- What the slice actually publishes: for each label whose subject resolved, the
-- known moieties its section names, with offsets, a citation, and the quoted
-- window covering that occurrence where the budget bought one.
--
-- SELF-PAIRS ARE EXCLUDED IN THE VIEW, NOT REFUSED AT INSERT -- db/049's
-- asymmetry with moiety_contraindication_not_self, for the same reason. A label
-- routinely names its own drug ("the effect of WARFARIN is increased by..."),
-- and that is a CORRECT reading of the source rather than a malformed row. The
-- orchestrator counts them in a bucket of their own so the number cannot become
-- nonzero unnoticed.
--
-- UNRESOLVED SUBJECTS ARE ABSENT HERE AND PRESENT IN THE GAP VIEW: filtered on a
-- NULL uuid, NEVER on the route vocabulary, which is db/050 section 4's
-- correction -- filtering on routes would put that list in a second place and
-- would need widening every time a route is added.
CREATE OR REPLACE VIEW drugref.spl_ddi_evidence AS
SELECT least(s.moiety_uuid, o.moiety_uuid)    AS moiety_lo,
       greatest(s.moiety_uuid, o.moiety_uuid) AS moiety_hi,
       s.moiety_uuid       AS subject_moiety,
       o.moiety_uuid       AS object_moiety,
       s.route             AS subject_route,
       l.source            AS candidate_source,
       l.set_id,                      -- the citation, and the join key to both
       l.version,                     -- corpora
       l.effective_time,
       l.text_key,                    -- WHICH wording said it
       o.char_start,
       o.char_end,
       o.match_ambiguous,
       q.ordinal           AS quote_ordinal,
       q.quote_text,                  -- NULL where the budget bought no window
       l.ingest_run,
       r.upstream_release,
       r.finished_at       AS ingested_at
FROM       drugref.spl_label_subject   s
JOIN       drugref.spl_label           l
        ON l.ingest_run = s.ingest_run AND l.source = s.source
       AND l.set_id     = s.set_id     AND l.version = s.version
JOIN       drugref.spl_entity_occurrence o
        ON o.ingest_run = l.ingest_run AND o.source = l.source
       AND o.text_key   = l.text_key
-- LEFT, because 28.4% of named moieties lose the window and none of them loses
-- the evidence. An inner join here would silently publish only the quotable
-- three-quarters and would look identical to a smaller corpus.
LEFT JOIN  drugref.spl_wording_quote   q
        ON q.ingest_run  = o.ingest_run AND q.source = o.source
       AND q.text_key    = o.text_key
       AND q.char_start <= o.char_start AND q.char_end >= o.char_end
JOIN       drugref.ingest_run          r ON r.ingest_run_id = l.ingest_run
WHERE      s.moiety_uuid IS NOT NULL
       -- A section naming its own subject asserts nothing about an interaction
       -- between two drugs.
       AND s.moiety_uuid <> o.moiety_uuid;

COMMENT ON VIEW drugref.spl_ddi_evidence IS
    'EVIDENCE GRAIN -- one row per (pair, citing label), which is NOT a pair '
    'count: read spl_ddi_pair for that. For every label whose subject resolved, '
    'the known moieties its interactions section names, with offsets, the '
    'set_id/version citation and the quoted window covering the occurrence where '
    'the 25% budget bought one (NULL for the 28.4% that lose the window and keep '
    'everything else). ORIENTATION-NORMALISED into moiety_lo/moiety_hi, with the '
    'direction kept in subject_moiety/object_moiety because SPL does state which '
    'drug the label is about. IT ASSERTS NO RELATION AND NO SEVERITY: that these '
    'two drugs are named together is the whole claim. Self-pairs are excluded '
    'HERE rather than refused at insert -- a label naming its own drug is a '
    'correct reading of the source.';

-- ---------------------------------------------------------------------------
-- 7b. drugref.spl_ddi_pair -- one row per unordered pair
-- ---------------------------------------------------------------------------
-- PAIR GRAIN: count(*) here IS the candidate-pair count, and it is directly
-- comparable with drugcentral_ddi_pair's. Measured floor on the 2026-08-22 /
-- 2026-08-21 releases: >= 29,258 distinct pairs, >= 25,960 (88.7%) novel.
--
-- NO SEVERITY COLUMN, and its absence is the design rather than an omission.
-- DrugCentral's arm carries a grade because VA published one; SPL publishes
-- prose, and grading it would be relation extraction.
CREATE OR REPLACE VIEW drugref.spl_ddi_pair AS
SELECT e.moiety_lo,
       e.moiety_hi,
       e.candidate_source,
       count(*)                        AS evidence_count,
       count(DISTINCT e.text_key)      AS wording_count,
       count(DISTINCT e.set_id)        AS label_count,
       bool_or(e.match_ambiguous)      AS any_match_ambiguous,
       bool_or(e.quote_text IS NOT NULL) AS has_quote,
       max(e.effective_time)           AS latest_effective_time,
       max(e.upstream_release)         AS upstream_release,
       max(e.ingested_at)              AS ingested_at
FROM   drugref.spl_ddi_evidence e
GROUP BY e.moiety_lo, e.moiety_hi, e.candidate_source;

COMMENT ON VIEW drugref.spl_ddi_pair IS
    'PAIR GRAIN -- count(*) IS the distinct candidate-pair count, directly '
    'comparable with drugcentral_ddi_pair. Measured floor on the 2026-08-22 '
    'openFDA / 2026-08-21 DailyMed releases: at least 29,258 pairs, at least '
    '25,960 (88.7%) novel against everything drugref holds. A FLOOR, not a '
    'target: the measurement scanned only orphan-wording labels, so the 14,455 '
    'redundant unkeyed labels contributed no subject and no pairs, and an ingest '
    'reproducing MORE is not failing its check. NO SEVERITY COLUMN, deliberately '
    '-- SPL publishes prose and grading it would be the relation extraction this '
    'slice refuses. THE WEAKER CLAIM: this means "a label''s interactions '
    'section names both drugs", not "an authority asserts they interact", which '
    'is why it is NOT an arm of exact_ddi_pair.';

-- ---------------------------------------------------------------------------
-- 7c. WHY spl_ddi_pair IS **NOT** MERGED INTO exact_ddi_pair
-- ---------------------------------------------------------------------------
-- Stated as its own section because the temptation is real and the reason is the
-- whole design. `exact_ddi_pair` means *an authority asserted these two drugs
-- interact*. SPL evidence, read without relation extraction, means *a label's
-- interactions section names both*. The second is WEAKER, and a read path that
-- cannot tell them apart makes the first unfalsifiable.
--
-- Consumers wanting both take the union explicitly, and see which source said
-- what. exact_ddi_pair is therefore UNCHANGED by this migration -- no existing
-- query moves.

-- ============================================================================
-- 8. drugref.gap_unresolved_spl_subject -- the recovery register
-- ============================================================================
-- Every spl_label whose subject did not resolve, with its route, its citation
-- and its wording key. 34,542 rows on today's releases, of which 19,862 carry
-- `absent_from_dailymed`.
--
-- WHAT IT IS FOR: it lets a FUTURE recovery route run against a stored list
-- rather than a re-read of 1.73 GB of openFDA plus 17.6 GB of DailyMed.
--
-- ⇒ IT IS DELIBERATELY **NOT** AN open_question KIND, AND THIS IS THE ONE PLACE
-- THAT SAYS SO. Every other gap_* view in this schema feeds
-- questions._GAP_SOURCES, so a reader is entitled to assume this one does too.
-- It does not, for db/012's test -- *could an answer change something?* A curator
-- cannot answer "this label is not in the current DailyMed release": the answer
-- is a property of an upstream release and arrives, or does not, when the next
-- one ships. Minting 34,542 immortal, externally-citable question_uuids for a
-- population that retires by itself would bury the 18 kinds a curator CAN answer
-- under twenty times their number.
--
-- FILTERED ON A NULL uuid, NEVER ON THE ROUTE VOCABULARY -- db/050 section 4's
-- correction, for db/006's reason: filtering on routes would put that list in a
-- second place and would need widening every time a route is added. The route is
-- PUBLISHED beside each row instead, because `absent_from_dailymed` and
-- `unresolved` are different findings and a consumer must be able to tell them
-- apart.
CREATE OR REPLACE VIEW drugref.gap_unresolved_spl_subject AS
SELECT l.source,
       l.set_id,
       l.version,
       s.route,
       l.text_key,
       w.char_length,
       w.label_count,
       l.ingest_run,
       r.upstream_release
FROM   drugref.spl_label_subject s
JOIN   drugref.spl_label         l
    ON l.ingest_run = s.ingest_run AND l.source = s.source
   AND l.set_id     = s.set_id     AND l.version = s.version
JOIN   drugref.spl_wording       w
    ON w.ingest_run = l.ingest_run AND w.source = l.source
   AND w.text_key   = l.text_key
JOIN   drugref.ingest_run        r ON r.ingest_run_id = l.ingest_run
WHERE  s.moiety_uuid IS NULL;

COMMENT ON VIEW drugref.gap_unresolved_spl_subject IS
    'Every SPL label carrying an interactions section whose SUBJECT drug drugref '
    'could not key, with the route saying why. A RECOVERY REGISTER, so a future '
    'route runs against a stored list rather than a re-read of 19.3 GB. '
    'Measured 2026-08-25: 34,542 rows, of which 19,862 are absent_from_dailymed '
    'and 14,680 were read and are still unkeyable -- including 200 carrying a '
    'UNII no live identity_claim holds, which is REGISTRY-COVERAGE work. '
    'DELIBERATELY NOT AN open_question KIND, unlike every other gap_* view here: '
    'a curator cannot answer "not in the current DailyMed release", which is '
    'db/012''s test for whether the review gate may ask at all, and 34,542 '
    'immortal question_uuids would bury the eighteen kinds a curator CAN answer. '
    'Filtered on a NULL uuid and never on the route vocabulary, so a route added '
    'later needs no edit here.';
COMMENT ON COLUMN drugref.gap_unresolved_spl_subject.label_count IS
    'How many labels share this wording. A label whose wording another, KEYED '
    'label also carries adds no statement drugref cannot already reach -- but '
    'its own SUBJECT is still its own, so its pairs are uncounted until it '
    'resolves. That distinction is why every pair figure in this slice is a '
    'floor.';

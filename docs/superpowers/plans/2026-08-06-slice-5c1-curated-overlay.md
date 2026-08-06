# Slice 5c.1 — Curated Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tables in which drugref may state severity, mechanism, management and evidence grade over the
5a/5b/5b.2 candidate rows — plus their read path, worklist and orphan check — shipping with an empty curation set.

**Architecture:** Two curated tables on Plan C's existing append-only overlay floor (`db/020`'s
`forbid_overlay_rewrite`, `db/023`'s `forbid_multiple_live_assertions`, a partial live-key index, and
`overlay.supersede`), with **no new PL/pgSQL**. `curated_interaction` keys the class-level DDI rule;
`curated_condition` keys the drug–condition pair *without* relationship. Neither foreign-keys into a rebuildable
projection. Two inner-joined read views leave the candidate views untouched; two gap views feed the question
registry; one check view detects orphans.

**Tech Stack:** Python 3 + psycopg 3, PostgreSQL 18, pytest, `uv`. Spec:
[2026-08-06-drugref-slice-5c1-curated-overlay-design.md](../specs/2026-08-06-drugref-slice-5c1-curated-overlay-design.md).

## Global Constraints

- **All code is AGPL-3.0.** This slice adds **no new dependency and no new data source**, so rule 6 raises nothing.
  Do not add either.
- **TDD, always:** write the failing test, run it, watch it fail for the right reason, then implement.
- **All tests must pass before every commit.** `DRUGREF_TEST_DSN=host=localhost port=5532 dbname=drugref_test user=postgres uv run pytest`
- **Lint before every commit:** `ruff check .` and `ruff format .`
- **Keep files under ~500 lines.** `curation.py` is new and must stay well inside that.
- **Inline documentation is mandatory** and must be understandable by a junior contributor. This codebase documents
  *why*, not *what*; match the density of `db/027_expansion_policy_history.sql` and `src/drugref/overlay.py`.
- **Nothing in `src/drugref` commits its own transaction** except `provenance.open_run`. The caller owns the
  transaction; the single-live trigger is DEFERRED, so mistakes surface at the caller's COMMIT.
- **One migration file, `db/029_curated_overlay.sql`, built up across Tasks 1, 2, 4 and 5.** This is safe *only
  while the branch is unmerged*: `db.apply_migrations` raises if an already-applied file changes, but the test
  suite's `_migrated` fixture does `DROP SCHEMA IF EXISTS drugref CASCADE` once per session, taking the ledger with
  it. **Never point the suite at a long-lived verification database mid-branch**, and once this merges the file is
  immutable like every other.
- **Vocabularies live in the CHECK, never restated in Python** (db/006's lesson). A bad value must raise
  `psycopg.errors.CheckViolation` from the database.
- **Ships empty.** No seed rows, no curation content. Every pre-existing count must be unchanged at Task 6.

---

### Task 1: `curated_interaction` — the table and its floor

**Files:**
- Create: `db/029_curated_overlay.sql`
- Create: `tests/test_curated_overlay.py`

**Interfaces:**
- Consumes: `db/020`'s `drugref.forbid_overlay_rewrite`, `db/023`'s `drugref.forbid_multiple_live_assertions`
  (both already exist, both generic over the natural key).
- Produces: table `drugref.curated_interaction`, natural key `(subject_moiety_uuid, object_class_uuid,
  relationship)`, surrogate PK `curated_interaction_id`; index `curated_interaction_live_key`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_curated_overlay.py`:

```python
# tests/test_curated_overlay.py
"""The curated overlay's floor and its completeness rules (db/029, slice 5c.1).

WHY THESE TESTS EXIST AT ALL. The overlay's whole purpose is to keep three states
apart: an assertion, an explicit "reviewed, this is not real", and NOBODY HAS LOOKED.
Slice 3 shipped a green suite in which deleting the guard that produced the third
state -- collapsing every unruled edge to `false` -- passed all 895 tests, because the
two ends were tested and the DECISION BETWEEN THEM was not. Every test below exists to
kill one specific mutation.

The vocabularies (severity, evidence_grade, relationship, ruling) are NOT restated in
Python. They live in db/029's CHECKs, which is the one place they can live without a
second list to disagree with the first (db/006). A bad value therefore raises
CheckViolation from the database, and a test asserts exactly that.
"""
import uuid

import psycopg
import pytest


def _a_class(conn, ingest_run_id, code="N0000000001", name="Test MoA [MoA]"):
    """One MED-RT class, for tests needing a live FK target on the object side."""
    from drugref import ids
    class_uuid = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', %s, %s, 'MoA', %s) ON CONFLICT DO NOTHING",
        (class_uuid, code, name, ingest_run_id))
    return class_uuid


def _assert_interaction(conn, moiety, klass, **over):
    """INSERT one curated_interaction row, returning its id. Defaults assert."""
    cols = dict(applies=True, severity="major", mechanism="additive bleeding risk",
                management="monitor INR", evidence_grade="established",
                source="DRUGREF", reviewed_by="test", reviewed_against="2026.07.06")
    cols.update(over)
    names = ", ".join(cols)
    holes = ", ".join(["%s"] * len(cols))
    return conn.execute(
        f"INSERT INTO drugref.curated_interaction "
        f"(subject_moiety_uuid, object_class_uuid, relationship, {names}) "
        f"VALUES (%s, %s, 'CI_MoA', {holes}) RETURNING curated_interaction_id",
        (moiety, klass, *cols.values())).fetchone()[0]


def test_an_asserting_row_must_state_severity_and_evidence(conn, a_moiety, ingest_run_id):
    """The completeness CHECK, in the direction that matters most: a row that says
    "this interaction is real" with no severity would fire an alert carrying no
    grading, and a consumer would have to guess what to render."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.CheckViolation,
                       match="curated_interaction_ruling_is_complete"):
        _assert_interaction(conn, a_moiety, klass, severity=None)


def test_a_non_applying_row_must_state_neither(conn, a_moiety, ingest_run_id):
    """The other direction. A row ruling the rule NOT real has nothing to grade, and
    filler values would put a meaningless severity in a clinical table."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.CheckViolation,
                       match="curated_interaction_ruling_is_complete"):
        _assert_interaction(conn, a_moiety, klass, applies=False)


def test_a_non_applying_row_with_no_grading_is_accepted(conn, a_moiety, ingest_run_id):
    """`applies = false` is a REAL ANSWER -- "a curator looked and this rule is not a
    real interaction" -- and is what lets a reviewed rule leave the worklist instead of
    being asked about every release forever."""
    klass = _a_class(conn, ingest_run_id)
    row_id = _assert_interaction(conn, a_moiety, klass, applies=False,
                                 severity=None, evidence_grade=None)
    assert row_id is not None


def test_applies_has_no_default(conn, a_moiety, ingest_run_id):
    """THE MUTATION SLICE 3 PROVED A GREEN SUITE MISSES. If `applies` gains a DEFAULT,
    an unstated ruling silently becomes a stated one. NOT NULL with no default is what
    forces a curator to say which."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            "INSERT INTO drugref.curated_interaction "
            "(subject_moiety_uuid, object_class_uuid, relationship, source, "
            " reviewed_by, reviewed_against) "
            "VALUES (%s, %s, 'CI_MoA', 'DRUGREF', 'test', '2026.07.06')",
            (a_moiety, klass))


def test_the_vocabulary_lives_in_the_database(conn, a_moiety, ingest_run_id):
    """A severity outside Plan C's four levels must be refused by the CHECK, not by a
    Python list nobody keeps in step with it."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.CheckViolation, match="curated_interaction_severity"):
        _assert_interaction(conn, a_moiety, klass, severity="catastrophic")


def test_the_row_cannot_be_updated_or_deleted(conn, a_moiety, ingest_run_id):
    """The append-only floor. What drugref believed, and when, stays answerable --
    which matters most for exactly the rows that fired an alert."""
    klass = _a_class(conn, ingest_run_id)
    row_id = _assert_interaction(conn, a_moiety, klass)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.curated_interaction SET severity = 'minor' "
                     "WHERE curated_interaction_id = %s", (row_id,))
    conn.rollback()
    klass = _a_class(conn, ingest_run_id)
    row_id = _assert_interaction(conn, a_moiety, klass)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.curated_interaction "
                     "WHERE curated_interaction_id = %s", (row_id,))


def test_two_live_rows_on_one_natural_key_abort_at_commit(conn, a_moiety, ingest_run_id):
    """A TEST THAT NEVER COMMITS PROVES NOTHING about a DEFERRED constraint, so this
    forces the check with SET CONSTRAINTS ALL IMMEDIATE -- which switches the mode for
    the rest of the transaction."""
    klass = _a_class(conn, ingest_run_id)
    _assert_interaction(conn, a_moiety, klass)
    _assert_interaction(conn, a_moiety, klass, severity="minor")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_live_key_index_exists_by_name(conn):
    """Nothing but the trigger reads this index, so nothing but a test protects it --
    and db/023 measured the cost of its absence: the single-live trigger becomes a
    sequential scan per row, so a 2,000-row load went from 42 ms to 5,773 ms."""
    assert conn.execute(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'drugref' "
        "AND indexname = 'curated_interaction_live_key'").fetchone() is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_curated_overlay.py -v
```

Expected: every test FAILS with `UndefinedTable: relation "drugref.curated_interaction" does not exist`.

- [ ] **Step 3: Write `db/029_curated_overlay.sql`**

```sql
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
-- class_contraindication holds ~739 CI_MoA/CI_PE rules; ddi_candidate_pair expands
-- them to 21,664 concrete pairs AT READ TIME. So a pair has no stable row identity to
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
    -- Mirrors class_contraindication's own CHECK. Widen the two together, or a rule
    -- this table can grade becomes one no candidate exists for.
    CONSTRAINT curated_interaction_relationship
        CHECK (relationship IN ('CI_MoA', 'CI_PE')),
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

COMMENT ON TABLE drugref.curated_interaction IS
    'CURATED, APPEND-ONLY: drugref''s own judgement -- severity, mechanism, management '
    'and evidence grade -- on a class-level CI_MoA/CI_PE rule, inheriting to every '
    'pair the rule expands to. Keyed on the RULE, not the pair: ddi_candidate_pair is '
    'a view, so a pair has no stable identity, and 21,664 pairs is not a curatable '
    'population while ~739 rules is. `source` is NOT in the key -- one clinical fact, '
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_curated_overlay.py -v
```

Expected: PASS, 8 tests. If `test_the_row_cannot_be_updated_or_deleted` fails with a psycopg error class other
than `RaiseException`, check what `forbid_overlay_rewrite` actually raises in `db/020` and match it — do not
loosen the assertion to bare `psycopg.Error`.

- [ ] **Step 5: Run the whole suite and lint**

```bash
ruff check . && ruff format --check .
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
```

Expected: all pass, previous count + 8.

- [ ] **Step 6: Commit**

```bash
git add db/029_curated_overlay.sql tests/test_curated_overlay.py
git commit -m "feat(5c): curated_interaction, keyed on the rule rather than the pair"
```

---

### Task 2: `curated_condition` — the pair-keyed table and its four-value ruling

**Files:**
- Modify: `db/029_curated_overlay.sql` (append section 2)
- Modify: `tests/test_curated_overlay.py` (append)

**Interfaces:**
- Consumes: the floor functions from Task 1; `drugref.condition(condition_uuid)` from `db/013`.
- Produces: table `drugref.curated_condition`, natural key `(subject_moiety_uuid, object_condition_uuid)`,
  surrogate PK `curated_condition_id`, column `ruling` over
  `('contraindicated','indicated','context_dependent','spurious')`; index `curated_condition_live_key`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_curated_overlay.py`:

```python
def _a_condition(conn, ingest_run_id, code="D006333", name="Heart Failure"):
    """One MeSH condition -- the issue-51 flagship, by name, so the test reads as the
    case it is about."""
    from drugref import ids
    condition_uuid = ids.mint_condition_uuid("MeSH", code)
    conn.execute(
        "INSERT INTO drugref.condition "
        "(condition_uuid, source, source_code, name, record_kind, first_seen_ingest) "
        "VALUES (%s, 'MeSH', %s, %s, 'DESCRIPTOR', %s) ON CONFLICT DO NOTHING",
        (condition_uuid, code, name, ingest_run_id))
    return condition_uuid


def _rule_condition(conn, moiety, condition, **over):
    """INSERT one curated_condition row, returning its id. Defaults to the issue-51
    ruling: both upstream assertions are correct, in different clinical states."""
    cols = dict(ruling="context_dependent", severity="major",
                mechanism="negative inotropy in acute decompensation",
                management="first-line in stable chronic HFrEF; withhold in acute "
                           "decompensated failure",
                evidence_grade="established", source="DRUGREF",
                reviewed_by="test", reviewed_against="2026.07.06")
    cols.update(over)
    names = ", ".join(cols)
    holes = ", ".join(["%s"] * len(cols))
    return conn.execute(
        f"INSERT INTO drugref.curated_condition "
        f"(subject_moiety_uuid, object_condition_uuid, {names}) "
        f"VALUES (%s, %s, {holes}) RETURNING curated_condition_id",
        (moiety, condition, *cols.values())).fetchone()[0]


def test_one_row_rules_on_the_pair_not_on_a_relationship(conn, a_moiety, ingest_run_id):
    """ISSUE 51 IN ONE TEST, and the reason this table's key omits `relationship`
    while its sibling's includes it. The same (drug, condition) genuinely carries both
    may_treat and CI_with -- 168 such pairs in the release -- so a relationship in the
    key would write ONE judgement TWICE and let the two copies disagree. Inserting a
    second row for the same pair must therefore collide, not coexist."""
    condition = _a_condition(conn, ingest_run_id)
    _rule_condition(conn, a_moiety, condition)
    _rule_condition(conn, a_moiety, condition, ruling="contraindicated")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_context_dependent_is_an_accepted_ruling(conn, a_moiety, ingest_run_id):
    """The only TRUE statement about metoprolol and D006333 at MeSH's grain: both
    upstream assertions are right, in different clinical states. If the vocabulary
    cannot express it, the slice does not solve the problem it exists for."""
    condition = _a_condition(conn, ingest_run_id)
    assert _rule_condition(conn, a_moiety, condition) is not None


def test_spurious_states_no_severity_and_no_grade(conn, a_moiety, ingest_run_id):
    """`spurious` means "reviewed; the upstream assertion is wrong" -- there is nothing
    to grade, and filler values would put a meaningless severity in a clinical table."""
    condition = _a_condition(conn, ingest_run_id)
    assert _rule_condition(conn, a_moiety, condition, ruling="spurious",
                           severity=None, evidence_grade=None) is not None
    conn.rollback()
    condition = _a_condition(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.CheckViolation,
                       match="curated_condition_ruling_is_complete"):
        _rule_condition(conn, a_moiety, condition, ruling="spurious")


def test_an_asserting_ruling_must_be_graded(conn, a_moiety, ingest_run_id):
    condition = _a_condition(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.CheckViolation,
                       match="curated_condition_ruling_is_complete"):
        _rule_condition(conn, a_moiety, condition, evidence_grade=None)


def test_ruling_has_no_default(conn, a_moiety, ingest_run_id):
    """Same mutation as `applies`, one table over: a DEFAULT turns an unstated ruling
    into a stated one, and absence of a row -- NOBODY HAS LOOKED -- is a third state
    neither value can express."""
    condition = _a_condition(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            "INSERT INTO drugref.curated_condition "
            "(subject_moiety_uuid, object_condition_uuid, source, reviewed_by, "
            " reviewed_against) VALUES (%s, %s, 'DRUGREF', 'test', '2026.07.06')",
            (a_moiety, condition))


def test_the_ruling_vocabulary_lives_in_the_database(conn, a_moiety, ingest_run_id):
    condition = _a_condition(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.CheckViolation, match="curated_condition_ruling"):
        _rule_condition(conn, a_moiety, condition, ruling="probably_fine")


def test_the_condition_live_key_index_exists_by_name(conn):
    assert conn.execute(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'drugref' "
        "AND indexname = 'curated_condition_live_key'").fetchone() is not None
```

- [ ] **Step 2: Run to verify they fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_curated_overlay.py -v -k condition
```

Expected: FAIL, `relation "drugref.curated_condition" does not exist`.

- [ ] **Step 3: Append section 2 to `db/029_curated_overlay.sql`**

```sql
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
```

- [ ] **Step 4: Run to verify they pass**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_curated_overlay.py -v
```

Expected: PASS, 15 tests.

- [ ] **Step 5: Lint and full suite**

```bash
ruff check . && ruff format --check .
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
```

- [ ] **Step 6: Commit**

```bash
git add db/029_curated_overlay.sql tests/test_curated_overlay.py
git commit -m "feat(5c): curated_condition, keyed on the pair so issue 51 has one home"
```

---

### Task 3: `curation.py` — the two writers

**Files:**
- Create: `src/drugref/curation.py`
- Create: `tests/test_curation_writer.py`

**Interfaces:**
- Consumes: `drugref.overlay.supersede(conn, table, pk_column, new_id, key_columns, key_values)`; the two tables
  from Tasks 1–2.
- Produces:
  - `curation.record_interaction_judgement(conn, subject_moiety_uuid, object_class_uuid, relationship, applies, *, severity=None, mechanism=None, management=None, evidence_grade=None, question_uuid=None, source='DRUGREF', reviewed_by, reviewed_against) -> int`
  - `curation.record_condition_ruling(conn, subject_moiety_uuid, object_condition_uuid, ruling, *, severity=None, mechanism=None, management=None, evidence_grade=None, question_uuid=None, source='DRUGREF', reviewed_by, reviewed_against) -> int`
  - Both return the new surrogate id and supersede whatever was live.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_curation_writer.py
"""The writers for the curated overlay (db/029, slice 5c.1).

Correction-by-overlay is INSERT-then-point-the-old-row-at-it, and the ORDER is the part
that is easy to get wrong: `superseded_by` is a foreign key to a row that must already
exist, so pointing first cannot work -- and getting it backwards fails at COMMIT, far
from the call that caused it. That is why these are functions and not a paragraph of
documentation telling every curator to write the sequence themselves.

NO VOCABULARY IS RESTATED HERE. severity, evidence_grade and ruling live in db/029's
CHECKs; a bad value raises CheckViolation from the database, which one test asserts.
"""
import psycopg
import pytest

from drugref import curation
from tests.test_curated_overlay import _a_class, _a_condition


def test_recording_a_judgement_makes_it_live(conn, a_moiety, ingest_run_id):
    klass = _a_class(conn, ingest_run_id)
    curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", True, severity="major",
        evidence_grade="established", mechanism="additive bleeding risk",
        management="monitor INR", reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT severity FROM drugref.curated_interaction "
        "WHERE superseded_by IS NULL AND subject_moiety_uuid = %s", (a_moiety,)
    ).fetchone() == ("major",)


def test_revising_a_judgement_supersedes_rather_than_overwrites(conn, a_moiety, ingest_run_id):
    """The whole reason the tier exists: the previous grade must still be answerable
    afterwards, because it is what fired yesterday's alert."""
    klass = _a_class(conn, ingest_run_id)
    first = curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", True, severity="major",
        evidence_grade="suspected", reviewed_by="test", reviewed_against="2026.07.06")
    second = curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", True, severity="moderate",
        evidence_grade="established", reviewed_by="test", reviewed_against="2026.08.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")   # a test that never commits proves nothing
    assert conn.execute(
        "SELECT curated_interaction_id, severity FROM drugref.curated_interaction "
        "WHERE superseded_by IS NULL").fetchall() == [(second, "moderate")]
    assert conn.execute(
        "SELECT superseded_by, severity FROM drugref.curated_interaction "
        "WHERE curated_interaction_id = %s", (first,)).fetchone() == (second, "major")


def test_retiring_a_rule_leaves_nothing_live_and_asserting(conn, a_moiety, ingest_run_id):
    """`applies = false` is how a rule is WITHDRAWN, since supersession alone retires
    nothing: a correction must point at a later row with the same key, so every
    correction leaves one live."""
    klass = _a_class(conn, ingest_run_id)
    curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", True, severity="major",
        evidence_grade="established", reviewed_by="test", reviewed_against="2026.07.06")
    curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", False, reviewed_by="test",
        reviewed_against="2026.08.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_interaction "
        "WHERE superseded_by IS NULL AND applies").fetchone() == (0,)


def test_recording_a_condition_ruling_makes_it_live(conn, a_moiety, ingest_run_id):
    condition = _a_condition(conn, ingest_run_id)
    curation.record_condition_ruling(
        conn, a_moiety, condition, "context_dependent", severity="major",
        evidence_grade="established",
        mechanism="negative inotropy in acute decompensation",
        management="first-line in stable chronic HFrEF; withhold when decompensated",
        reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT ruling FROM drugref.curated_condition WHERE superseded_by IS NULL"
    ).fetchone() == ("context_dependent",)


def test_revising_a_condition_ruling_supersedes(conn, a_moiety, ingest_run_id):
    condition = _a_condition(conn, ingest_run_id)
    first = curation.record_condition_ruling(
        conn, a_moiety, condition, "contraindicated", severity="major",
        evidence_grade="probable", reviewed_by="test", reviewed_against="2026.07.06")
    second = curation.record_condition_ruling(
        conn, a_moiety, condition, "context_dependent", severity="moderate",
        evidence_grade="established", reviewed_by="test", reviewed_against="2026.08.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT superseded_by FROM drugref.curated_condition "
        "WHERE curated_condition_id = %s", (first,)).fetchone() == (second,)


def test_an_unknown_grade_is_refused_by_the_database(conn, a_moiety, ingest_run_id):
    """The vocabulary has ONE home, in db/029. A second list in Python is a second
    thing to disagree with the first (db/006)."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        curation.record_interaction_judgement(
            conn, a_moiety, klass, "CI_MoA", True, severity="major",
            evidence_grade="anecdotal", reviewed_by="test",
            reviewed_against="2026.07.06")


def test_the_writer_does_not_commit(conn, a_moiety, ingest_run_id):
    """The caller owns the transaction, as everywhere in these modules -- so a rollback
    must take the row with it."""
    klass = _a_class(conn, ingest_run_id)
    curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", True, severity="major",
        evidence_grade="established", reviewed_by="test", reviewed_against="2026.07.06")
    conn.rollback()
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_interaction").fetchone() == (0,)
```

- [ ] **Step 2: Run to verify they fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_curation_writer.py -v
```

Expected: FAIL, `ImportError: cannot import name 'curation'`.

- [ ] **Step 3: Write `src/drugref/curation.py`**

```python
# src/drugref/curation.py
"""Writers for the curated overlay: drugref's own clinical judgements (db/029).

WHAT THIS MODULE OWNS. Slices 5a/5b/5b.2 project CANDIDATE rows from upstream -- MED-RT
asserts that a drug is contraindicated with a class, or in a condition, and asserts
nothing about how severe that is, by what mechanism, what to do about it, or how well
attested it is. Those four dimensions are drugref's to state, and this module is the
only supported way to state them.

THE ONE SEQUENCE THE TIER ADMITS, per overlay.py:

    1. INSERT the new assertion, which becomes live.
    2. UPDATE whatever was live for the same natural key to point at it.

In that order, always. Both functions below do exactly that, which is the whole reason
they exist rather than a note in the documentation telling each caller to get it right.

NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these modules,
and the single-live check is DEFERRED -- so a mistake surfaces at the caller's COMMIT,
not here.

NO VOCABULARY IS RESTATED IN PYTHON. `severity`, `evidence_grade`, `relationship` and
`ruling` live in db/029's CHECK constraints, which is the one place they can live
without a second list to drift from the first (db/006's lesson, learned when a CASE in
a view and a CHECK in a table disagreed silently). An unrecognised value raises
CheckViolation from the database, and that is the intended behaviour rather than a gap.
"""
import uuid

import psycopg

from drugref import overlay


def record_interaction_judgement(
        conn: psycopg.Connection,
        subject_moiety_uuid: uuid.UUID,
        object_class_uuid: uuid.UUID,
        relationship: str,
        applies: bool,
        *,
        severity: str | None = None,
        mechanism: str | None = None,
        management: str | None = None,
        evidence_grade: str | None = None,
        question_uuid: uuid.UUID | None = None,
        source: str = "DRUGREF",
        reviewed_by: str,
        reviewed_against: str) -> int:
    """Record (or revise) drugref's judgement on one class-level CI_MoA/CI_PE rule.

    Returns the new `curated_interaction_id`. THE ONLY SUPPORTED WAY TO REVISE ONE:
    the table is append-only, so a revision INSERTs the new judgement and then points
    whatever was live at it. The previous grade survives as history, which matters most
    for exactly the rows that fired an alert.

    `applies=False` is how a rule is RETIRED, and it is not a deletion: supersession
    alone can never withdraw anything, because a correction must point at a later row
    carrying the SAME natural key and therefore always leaves one live. A retired rule
    stops reaching `curated_ddi_pair` and stops being asked about on the worklist.

    A retiring call passes no grading -- db/029's completeness CHECK refuses a
    non-applying row that carries severity or evidence_grade, and refuses an applying
    row that omits either. That is deliberately enforced in the database rather than
    here, so a caller bypassing this function cannot write an incoherent row.

    `question_uuid` is optional: it links the judgement to the gap question it answers,
    whose citations live in `question_evidence`. Omitting it is legal and MEANS
    SOMETHING -- the grade rests on nothing recorded. Curated is not verified.

    THE JUDGEMENT IS KEYED ON THE RULE, not on the drug pairs it expands to, so one
    call grades every pair the rule reaches. That is the point of curating at this
    grain: ~739 rules against 21,664 pairs.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.curated_interaction "
        "(subject_moiety_uuid, object_class_uuid, relationship, applies, severity, "
        " mechanism, management, evidence_grade, question_uuid, source, reviewed_by, "
        " reviewed_against) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING curated_interaction_id",
        (subject_moiety_uuid, object_class_uuid, relationship, applies, severity,
         mechanism, management, evidence_grade, question_uuid, source, reviewed_by,
         reviewed_against)).fetchone()[0]
    overlay.supersede(
        conn, "curated_interaction", "curated_interaction_id", new_id,
        ("subject_moiety_uuid", "object_class_uuid", "relationship"),
        (subject_moiety_uuid, object_class_uuid, relationship))
    return new_id


def record_condition_ruling(
        conn: psycopg.Connection,
        subject_moiety_uuid: uuid.UUID,
        object_condition_uuid: uuid.UUID,
        ruling: str,
        *,
        severity: str | None = None,
        mechanism: str | None = None,
        management: str | None = None,
        evidence_grade: str | None = None,
        question_uuid: uuid.UUID | None = None,
        source: str = "DRUGREF",
        reviewed_by: str,
        reviewed_against: str) -> int:
    """Record (or revise) drugref's ruling on one (drug, condition) pair.

    Returns the new `curated_condition_id`. Same append-then-point sequence as its
    sibling, and the same reason for existing.

    NOTE WHAT IS ABSENT FROM THE ARGUMENTS: `relationship`. The ruling is about the
    PAIR, not about one predicate over it, because the same pair carries both an
    indication and a contraindication in 168 cases and BOTH ARE OFTEN TRUE -- nine
    beta-blockers are both may_treat and CI_with against MeSH "Heart Failure", first
    line in stable chronic HFrEF and contraindicated in acute decompensation, with one
    MeSH descriptor covering both states. `ruling='context_dependent'` is how that is
    said, and taking a relationship here would let the same judgement be written twice
    and disagree with itself.

    `ruling='spurious'` retires the pair: reviewed, and the upstream assertion is
    wrong. It records the disagreement WITHOUT acting on it -- the candidate stays in
    its projection, because contradicting a source is not the same act as drugref
    changing how it reads its own DAG, and "what did the release say" must stay
    answerable next to "what does drugref say". A spurious row therefore reaches no
    read view. Like a retiring interaction judgement, it passes no grading.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.curated_condition "
        "(subject_moiety_uuid, object_condition_uuid, ruling, severity, mechanism, "
        " management, evidence_grade, question_uuid, source, reviewed_by, "
        " reviewed_against) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING curated_condition_id",
        (subject_moiety_uuid, object_condition_uuid, ruling, severity, mechanism,
         management, evidence_grade, question_uuid, source, reviewed_by,
         reviewed_against)).fetchone()[0]
    overlay.supersede(
        conn, "curated_condition", "curated_condition_id", new_id,
        ("subject_moiety_uuid", "object_condition_uuid"),
        (subject_moiety_uuid, object_condition_uuid))
    return new_id
```

- [ ] **Step 4: Run to verify they pass**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_curation_writer.py -v
```

Expected: PASS, 7 tests.

- [ ] **Step 5: Lint, full suite, commit**

```bash
ruff check . && ruff format --check .
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
git add src/drugref/curation.py tests/test_curation_writer.py
git commit -m "feat(5c): the two curated-overlay writers, one correction sequence each"
```

---

### Task 4: The read views

**Files:**
- Modify: `db/029_curated_overlay.sql` (append section 3)
- Create: `tests/test_curated_read_path.py`

**Interfaces:**
- Consumes: both tables; `drugref.ddi_candidate_pair` (columns `subject_moiety`, `partner_moiety`,
  `relationship`, `via_class`, `member_class`, `is_direct`, `source`, `ingest_run`, `upstream_release`,
  `ingested_at`); `moiety_condition_contraindication` and `moiety_condition_indication`.
- Produces: views `drugref.curated_ddi_pair` and `drugref.curated_condition_ruling`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_curated_read_path.py
"""The curated overlay's read path (db/029 section 3, slice 5c.1).

INNER JOINS THROUGHOUT, AND THAT IS THE STRUCTURAL POINT. db/019 split `induces` into
its own table rather than adding a WHERE clause, arguing that a consumer who forgets a
filter on a shared table reads a therapeutic claim off the wrong row. The same
forgetfulness here -- a LEFT JOIN returning every candidate with a NULL severity beside
it -- renders an UNREVIEWED candidate as though a curator had passed it. A consumer must
ASK for graded advice and receive only graded advice.
"""
import psycopg
import pytest

from drugref import curation, interactions
from tests.test_curated_overlay import _a_class, _a_condition


@pytest.fixture
def a_graded_rule(conn, a_moiety, ingest_run_id):
    """One CI_MoA rule with a member on its axis, and drugref's grade on the rule."""
    from drugref import ids
    klass = _a_class(conn, ingest_run_id)
    partner = ids.mint_moiety_uuid("TESTUNII02")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, 'partnerdrug', %s) ON CONFLICT DO NOTHING",
        (partner, ingest_run_id))
    # NOTE class_membership has NO `source` column -- unlike class_contraindication
    # below, and unlike every moiety_condition_* table. db/003 made the class registry
    # source-neutral; adding one here fails with UndefinedColumn.
    conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) "
        "VALUES (%s, %s, 'has_MoA', %s) ON CONFLICT DO NOTHING",
        (partner, klass, ingest_run_id))
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)", (a_moiety, klass, ingest_run_id))
    return {"subject": a_moiety, "partner": partner, "class": klass}


def test_a_graded_rule_reaches_every_pair_it_expands_to(conn, a_graded_rule):
    """THE PAYOFF OF RULE-LEVEL CURATION. One curated row must grade the pair the rule
    expands to -- if it does not, curating 739 rules buys nothing over curating 21,664
    pairs."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established",
        management="avoid", reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT partner_moiety, severity, management FROM drugref.curated_ddi_pair "
        "WHERE subject_moiety = %s", (a_graded_rule["subject"],)
    ).fetchall() == [(a_graded_rule["partner"], "major", "avoid")]


def test_an_ungraded_rule_reaches_the_curated_view_never(conn, a_graded_rule):
    """The forgetfulness db/019 refuses to allow. An unreviewed candidate must not
    appear here at all -- not with a NULL severity, not at all."""
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_ddi_pair").fetchone() == (0,)


def test_a_retired_rule_stops_reaching_the_view(conn, a_graded_rule):
    """`applies = false` is live and binds NOTHING. Rendering it would tell a
    prescriber about an interaction a curator explicitly ruled unreal."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", False,
        reviewed_by="test", reviewed_against="2026.08.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_ddi_pair").fetchone() == (0,)


def test_a_superseded_grade_stops_reaching_the_view(conn, a_graded_rule):
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="minor", evidence_grade="suspected", reviewed_by="test",
        reviewed_against="2026.08.06")
    assert conn.execute(
        "SELECT severity FROM drugref.curated_ddi_pair").fetchall() == [("minor",)]


def test_the_candidate_view_is_untouched_by_curation(conn, a_graded_rule):
    """ddi_candidate_pair answers "what did the release say" and must keep answering it
    after drugref disagrees. db/027 does let curation gate this view -- a `deny` policy
    withholds 233 pairs -- but that governs drugref's own reading of the DAG, which is
    a different act from contradicting an upstream assertion."""
    before = conn.execute("SELECT count(*) FROM drugref.ddi_candidate_pair").fetchone()
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", False,
        reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.ddi_candidate_pair").fetchone() == before


@pytest.fixture
def a_contradicted_pair(conn, a_moiety, ingest_run_id):
    """Issue 51's shape: one pair asserted as BOTH may_treat and CI_with."""
    condition = _a_condition(conn, ingest_run_id)
    interactions.add_condition_contraindication(
        conn, a_moiety, condition, "CI_with", "MED-RT", ingest_run_id)
    conn.execute(
        "INSERT INTO drugref.moiety_condition_indication "
        "(subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'may_treat', 'MED-RT', %s)",
        (a_moiety, condition, ingest_run_id))
    return {"moiety": a_moiety, "condition": condition}


def test_one_ruling_returns_a_row_per_candidate_it_reconciles(conn, a_contradicted_pair):
    """THE VIEW'S GRAIN, pinned. The beta-blocker case must return TWO rows carrying
    the SAME ruling -- one naming may_treat, one naming CI_with. Aggregating the
    candidates into an array would hide which relationships the ruling reconciles, and
    #41's finding was that folding a key component under an aggregate breaks a view's
    grain."""
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "context_dependent", severity="major", evidence_grade="established",
        reviewed_by="test", reviewed_against="2026.07.06")
    rows = conn.execute(
        "SELECT ruling, candidate_kind, relationship FROM "
        "drugref.curated_condition_ruling ORDER BY candidate_kind").fetchall()
    assert rows == [("context_dependent", "contraindication", "CI_with"),
                    ("context_dependent", "indication", "may_treat")]


def test_a_spurious_ruling_reaches_no_consumer(conn, a_contradicted_pair):
    """It records a disagreement WITHOUT acting on it: nothing renders it as advice,
    and the candidates stay in their projections."""
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "spurious", reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_condition_ruling").fetchone() == (0,)
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_contraindication"
    ).fetchone() == (1,)
```

- [ ] **Step 2: Run to verify they fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_curated_read_path.py -v
```

Expected: FAIL, `relation "drugref.curated_ddi_pair" does not exist`.

- [ ] **Step 3: Append section 3 to `db/029_curated_overlay.sql`**

```sql
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
```

- [ ] **Step 4: Run to verify they pass**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_curated_read_path.py -v
```

Expected: PASS, 7 tests.

- [ ] **Step 5: Lint, full suite, commit**

```bash
ruff check . && ruff format --check .
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
git add db/029_curated_overlay.sql tests/test_curated_read_path.py
git commit -m "feat(5c): the two curated read views, inner-joined so absence cannot read as approval"
```

---

### Task 5: The worklist — two gap views, their registration, and the retention fix

**Files:**
- Modify: `db/029_curated_overlay.sql` (append sections 4 and 5)
- Modify: `src/drugref/questions.py` (`_GAP_SOURCES`, and the DELETE guard in `register_from_gaps`)
- Create: `tests/test_curated_gap_views.py`

**Interfaces:**
- Consumes: both tables; `class_contraindication`; `ddi_candidate_pair`; both `moiety_condition_*` projections;
  `ids.mint_question_uuid(gap_kind, gap_key)`.
- Produces: views `gap_uncurated_condition_contradiction`, `gap_uncurated_interaction_rule`,
  `curated_target_unresolved`; two `_GAP_SOURCES` entries named `uncurated_condition_contradiction` and
  `uncurated_interaction_rule`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_curated_gap_views.py
"""The curated overlay's worklist (db/029 sections 4-5, slice 5c.1).

TWO PREDICATES THAT LOOK THE SAME AND ARE NOT. A gap view asks "is there a LIVE row?"
-- because every ruling, including `spurious` and `applies = false`, means a curator
LOOKED and the question is answered. A read view asks "is there a live ASSERTING row?"
-- because a retired ruling must reach no consumer. Collapse the two and whichever end
you collapse toward breaks: db/027 met this as its `_current`-versus-`_live`
distinction, where folding `withdrawn` into `allow` silently retired a question nobody
had answered.
"""
import pytest

from drugref import curation, ids, interactions, questions
from tests.test_curated_overlay import _a_class, _a_condition
from tests.test_curated_read_path import a_contradicted_pair, a_graded_rule  # noqa: F401


def test_a_contradicted_pair_is_queued(conn, a_contradicted_pair):
    """Issue 51's 168 pairs, in miniature: a pair asserted as BOTH an indication and a
    contraindication, with nobody having ruled on it."""
    rows = conn.execute(
        "SELECT subject_moiety, object_condition FROM "
        "drugref.gap_uncurated_condition_contradiction").fetchall()
    assert rows == [(a_contradicted_pair["moiety"], a_contradicted_pair["condition"])]


def test_a_pair_with_only_one_side_is_not_queued(conn, a_moiety, ingest_run_id):
    """The queue is the CONTRADICTION, not uncurated contraindications at large --
    13,463 of those exist and a queue nobody can finish is the stale generated document
    these views replace."""
    condition = _a_condition(conn, ingest_run_id)
    interactions.add_condition_contraindication(
        conn, a_moiety, condition, "CI_with", "MED-RT", ingest_run_id)
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_condition_contradiction"
    ).fetchone() == (0,)


@pytest.mark.parametrize("ruling", ["context_dependent", "spurious"])
def test_any_ruling_retires_the_pair_from_the_queue(conn, a_contradicted_pair, ruling):
    """EVERY ruling means a curator looked, including the one that says the upstream is
    wrong. A `spurious` row that stayed on the worklist would be asked about every
    release forever -- the exact nagging failure db/027's `withdrawn` exists to stop."""
    extra = {} if ruling == "spurious" else {"severity": "major",
                                             "evidence_grade": "established"}
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"], ruling,
        reviewed_by="test", reviewed_against="2026.07.06", **extra)
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_condition_contradiction"
    ).fetchone() == (0,)


def test_an_uncurated_rule_is_queued_with_the_pairs_at_stake(conn, a_graded_rule):
    """RANKED BY MEMBERS ACTUALLY AT STAKE, not by tree bushiness. Issue #36 measured
    the cost of the other metric: gap_unreviewed_expansion_root spent a curator's
    explicit decision on a root whose expansion was a provable no-op."""
    assert conn.execute(
        "SELECT subject_moiety, object_class, pair_count FROM "
        "drugref.gap_uncurated_interaction_rule").fetchall() == [
            (a_graded_rule["subject"], a_graded_rule["class"], 1)]


def test_a_rule_pairing_with_nobody_is_not_queued(conn, a_moiety, ingest_run_id):
    """Grading a rule that reaches no pair changes nothing, so asking about it spends a
    curator's attention for a provable no-op -- #36's finding, applied before it can be
    repeated. gap_unpopulated_contraindication already owns the "why does this class
    have no members" question."""
    klass = _a_class(conn, ingest_run_id, code="N0000000002", name="Empty MoA [MoA]")
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)", (a_moiety, klass, ingest_run_id))
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_interaction_rule "
        "WHERE object_class = %s", (klass,)).fetchone() == (0,)


def test_a_retired_rule_leaves_the_queue(conn, a_graded_rule):
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", False,
        reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_interaction_rule").fetchone() == (0,)


def test_both_kinds_mint_questions(conn, a_contradicted_pair, a_graded_rule, ingest_run_id):
    """The gap_key formats are FROZEN on first mint -- question_uuid is
    uuid5(gap_kind, gap_key), immortal and externally citable -- so they are pinned by
    a test literal rather than left to whatever the view happens to emit."""
    questions.register_from_gaps(conn, ingest_run_id)
    expected = ids.mint_question_uuid(
        "uncurated_condition_contradiction",
        f"MOIETY:{a_contradicted_pair['moiety']}/"
        f"CONDITION:{a_contradicted_pair['condition']}")
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question WHERE question_uuid = %s",
        (expected,)).fetchone() == (1,)
    expected_rule = ids.mint_question_uuid(
        "uncurated_interaction_rule",
        f"MOIETY:{a_graded_rule['subject']}/CLASS:{a_graded_rule['class']}"
        f"/CI_AXIS:CI_MoA")
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question WHERE question_uuid = %s",
        (expected_rule,)).fetchone() == (1,)


def test_a_closing_gap_does_not_abort_the_ingest_when_curated(
        conn, a_contradicted_pair, ingest_run_id):
    """THE FAILURE MODE THIS SLICE COULD EASILY HAVE SHIPPED. register_from_gaps
    DELETEs a question whose gap has closed. curated_condition cascades from
    open_question and refuses DELETE, so the cascade would RAISE and abort the whole
    ingest -- and curating a pair is exactly what CLOSES its gap. The guard, not the
    cascade, is what keeps curator work: a cited question is RETAINED and marked
    is_current = false."""
    questions.register_from_gaps(conn, ingest_run_id)
    question_uuid = ids.mint_question_uuid(
        "uncurated_condition_contradiction",
        f"MOIETY:{a_contradicted_pair['moiety']}/"
        f"CONDITION:{a_contradicted_pair['condition']}")
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "context_dependent", severity="major", evidence_grade="established",
        question_uuid=question_uuid, reviewed_by="test", reviewed_against="2026.07.06")
    questions.register_from_gaps(conn, ingest_run_id)      # must not raise
    assert conn.execute(
        "SELECT is_current FROM drugref.open_question WHERE question_uuid = %s",
        (question_uuid,)).fetchone() == (False,)


def test_a_curated_row_whose_candidate_vanished_is_reported(conn, a_graded_rule):
    """The orphan detector. A curated row references its candidate by NATURAL KEY, not
    by foreign key, precisely so a per-source rebuild cannot cascade curator judgement
    away -- which means a rebuild CAN leave a judgement pointing at nothing, and an
    operator must be told. expansion_policy_unresolved is the same shape and reports 0.

    NOT a gap kind: a vanished candidate is an upstream-change signal for an operator,
    not a clinical question for a curator."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_target_unresolved").fetchone() == (0,)
    conn.execute("DELETE FROM drugref.class_contraindication")
    assert conn.execute(
        "SELECT target_table, subject_moiety FROM drugref.curated_target_unresolved"
    ).fetchall() == [("curated_interaction", a_graded_rule["subject"])]
```

- [ ] **Step 2: Run to verify they fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_curated_gap_views.py -v
```

Expected: FAIL, `relation "drugref.gap_uncurated_condition_contradiction" does not exist`.

- [ ] **Step 3: Append sections 4 and 5 to `db/029_curated_overlay.sql`**

```sql
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
       count(*)               AS pair_count
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
```

- [ ] **Step 4: Register both kinds and fix the retention guard in `src/drugref/questions.py`**

Add to `_GAP_SOURCES` (after the existing entries, keeping the file's comment density):

```python
    # Slice 5c.1. The two kinds whose answer is a CURATED ROW rather than a lookup --
    # like unreviewed_expansion_root, drugref answers these itself.
    #
    # THE gap_key FORMATS BELOW ARE FROZEN. question_uuid is uuid5(gap_kind, gap_key),
    # immortal and externally citable, so changing either re-mints every question and
    # breaks every reference an external tool holds.
    "uncurated_condition_contradiction": {
        "view": "gap_uncurated_condition_contradiction",
        # Compound, on Plan C's CLASS:a/CLASS:b precedent: the question is about the
        # PAIR, and folding it onto either half would hand two independent questions
        # one immortal UUID.
        "key_sql": "'MOIETY:' || subject_moiety || '/CONDITION:' || object_condition",
        "text_sql": (
            "'Is ' || display_name || ' indicated or contraindicated in ' || "
            "condition_name || '? The release asserts BOTH, with no qualifier "
            "distinguishing them -- often because one MeSH descriptor covers "
            "clinical states in which the answers differ.'"),
    },
    "uncurated_interaction_rule": {
        "view": "gap_uncurated_interaction_rule",
        "key_sql": ("'MOIETY:' || subject_moiety || '/CLASS:' || object_class || "
                    "'/CI_AXIS:' || relationship"),
        "text_sql": (
            "'How severe is co-administering ' || display_name || ' with a drug of ' "
            "|| class_name || ', by what mechanism, and what should a prescriber do? "
            "The release asserts the contraindication and grades nothing. ' || "
            "pair_count || ' drug pair(s) inherit the answer.'"),
    },
```

Then extend the DELETE guard in `register_from_gaps` — the `NOT EXISTS` list gains the two curated tables:

```python
        conn.execute(
            "DELETE FROM drugref.open_question q "
            "WHERE q.gap_kind = %s AND NOT (q.gap_key = ANY(%s)) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.question_state x "
            "                WHERE x.question_uuid = q.question_uuid) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.question_source_check x "
            "                WHERE x.question_uuid = q.question_uuid) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.question_evidence x "
            "                WHERE x.question_uuid = q.question_uuid) "
            # db/029. Curating a pair is exactly what CLOSES its gap, so without these
            # two the very first curated row would make the next ingest delete its
            # question, cascade into an append-only table, RAISE, and abort the whole
            # transaction. The guard -- not the cascade -- is what keeps curator work.
            "AND NOT EXISTS (SELECT 1 FROM drugref.curated_interaction x "
            "                WHERE x.question_uuid = q.question_uuid) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.curated_condition x "
            "                WHERE x.question_uuid = q.question_uuid)",
            (gap_kind, live_keys))
```

Update that function's docstring paragraph beginning *"So a question carrying any curated row is RETAINED"* to say
the guard now covers five tables, not three.

- [ ] **Step 5: Run to verify they pass**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_curated_gap_views.py -v
```

Expected: PASS, 10 tests (the parametrised one counts twice).

- [ ] **Step 6: Lint, full suite, commit**

```bash
ruff check . && ruff format --check .
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
git add db/029_curated_overlay.sql src/drugref/questions.py tests/test_curated_gap_views.py
git commit -m "feat(5c): the curation worklist, and the retention guard curation would have broken"
```

---

### Task 6: Measure the assembled chain, and write up

**Files:**
- Modify: `docs/PROJECT-NOTES.md`, `docs/ROADMAP.md`, `docs/HANDOVER.md`
- Create: `docs-site/docs/decisions/curating-a-drug-condition-pair.md`

**Interfaces:**
- Consumes: everything above. Produces no code.

- [ ] **Step 1: Build a verification database from a real release**

```bash
createdb -h localhost -p 5532 -U postgres drugref_5c1
DRUGREF_DSN='host=localhost port=5532 dbname=drugref_5c1 user=postgres' \
  uv run drugref migrate
DRUGREF_DSN='host=localhost port=5532 dbname=drugref_5c1 user=postgres' \
  uv run drugref chain --steps unii,medrt,mesh-relations,gsrs
```

Record the wall-clock time. Follow `docs/PROJECT-NOTES.md` § "How to run / test" if the flags differ — that file
is authoritative over this line.

- [ ] **Step 2: Record the counts that must NOT have moved**

```sql
SELECT 'ddi_candidate_pair', count(*) FROM drugref.ddi_candidate_pair
UNION ALL SELECT 'substance_moiety', count(*) FROM drugref.substance_moiety
UNION ALL SELECT 'moiety_condition_contraindication', count(*) FROM drugref.moiety_condition_contraindication
UNION ALL SELECT 'moiety_condition_indication', count(*) FROM drugref.moiety_condition_indication
UNION ALL SELECT 'condition_contraindication_expanded', count(*) FROM drugref.condition_contraindication_expanded;
```

Expected, from the slice-3 measurement: `ddi_candidate_pair` **21,664**, `substance_moiety` **19,438**,
`condition_contraindication_expanded` **192,161**. **Any movement is a defect in this branch, not a new number to
write down** — this slice ships empty and touches no projection.

- [ ] **Step 3: Record the two new worklist cardinalities and the check view**

```sql
SELECT count(*) FROM drugref.gap_uncurated_condition_contradiction;   -- expect 168
SELECT count(*) FROM drugref.gap_uncurated_interaction_rule;          -- order ~739
SELECT count(*) FROM drugref.curated_target_unresolved;               -- expect 0
SELECT count(*) FROM drugref.curated_ddi_pair;                        -- expect 0
SELECT count(*) FROM drugref.curated_condition_ruling;                -- expect 0
SELECT gap_kind, count(*) FROM drugref.open_question GROUP BY 1 ORDER BY 1;
```

`open_question` must have grown by exactly the two new kinds' rows against slice 3's **21,079**. **If
`gap_uncurated_condition_contradiction` is not 168, do not adjust the expectation — find out why.** Issue 51
measured it through drugref's own ingest, and a different number means either the view or the issue is wrong, and
which one matters.

- [ ] **Step 4: Measure the read and gap views**

```sql
-- curated_ddi_pair returns nothing on an empty overlay, so measure the JOIN's shape by
-- filtering on a subject that really carries rules rather than on a literal you invent.
EXPLAIN ANALYZE
SELECT * FROM drugref.curated_ddi_pair
 WHERE subject_moiety = (SELECT subject_moiety_uuid
                           FROM drugref.class_contraindication LIMIT 1);
EXPLAIN ANALYZE SELECT * FROM drugref.gap_uncurated_interaction_rule;
EXPLAIN ANALYZE SELECT * FROM drugref.gap_uncurated_condition_contradiction;
EXPLAIN ANALYZE SELECT * FROM drugref.curated_target_unresolved;
```

`gap_uncurated_interaction_rule` joins the full `ddi_candidate_pair` expansion and groups it, so it is the one to
watch. **db/024's lesson is the standard here:** a recursive walk named twice inside a correlated `NOT EXISTS` cost
**59 s** where the hoisted form cost **465 ms**, and a synthetic fixture with no edges looked fine. If this view is
slow, hoist the expansion into a CTE and re-measure — do not reason about it.

- [ ] **Step 5: Write the decision record**

Create `docs-site/docs/decisions/curating-a-drug-condition-pair.md` covering, in the *living-record* voice the
Design decisions section uses (only decisions that currently stand):

1. Why `curated_condition` keys on the pair while `curated_interaction` keys on the rule — the 168 contradicted
   pairs, and the one-judgement-written-twice failure the asymmetry avoids.
2. Why a curated row references its candidate by natural key and never by foreign key.
3. **That the tier is signable, not signed.** No signing infrastructure exists; a signature column is an additive
   migration; rows committed before signing exists can never be signed retrospectively; shipping empty is what
   makes deferring it safe, and signing must therefore land before the first curated row.

- [ ] **Step 6: Correct the "signed overlay" wording**

`ROADMAP.md` (four places, per `grep -n -i "signed" docs/ROADMAP.md`) and `PROJECT-NOTES.md` describe the tier as
an "append-only, **signed** overlay". Nothing signs anything. Change to "append-only, **signable** overlay
(signing: slice 5c.4)" and link the decision record. **This is a correction of a false claim in the docs, not a
wording preference** — leaving it would have PROJECT-NOTES asserting a security property the schema does not have.

- [ ] **Step 7: Update the three state documents**

- **`PROJECT-NOTES.md`** — a new "Slice 5c.1" section, edited in place, no line bound: the measured numbers from
  Steps 2–4, the traps a future change can still break (the two predicates of §"gap views" versus the read views;
  the retention guard now covering five tables; the natural-key-not-FK reference and its orphan view; the
  asymmetric key), and the DDInter licence finding.
- **`ROADMAP.md`** — mark 5c.1 done, list 5c.2/5c.3/5c.4 as its successors, and **remove "DDInter *if its licence
  confirms*"**: it is CC BY-NC-SA, so rule 6 blocks bundling outright and it may only ever attach as a node-local
  plug-in.
- **`HANDOVER.md`** — regenerate, within the line bound **its own header states**, focused on what is left.

- [ ] **Step 8: Commit, push, open the PR**

```bash
ruff check . && ruff format --check .
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
git add -A
git commit -m "docs: the 5c.1 measurement, the decision record, and the signed-overlay correction"
git push -u origin feat/slice-5c1-curated-overlay
gh pr create --base main --title "Slice 5c.1: the curated overlay's assertion shape" --body "$(cat <<'BODY'
Builds the tables in which drugref may state severity, mechanism, management and
evidence grade over the 5a/5b/5b.2 candidate rows. **Ships with an empty curation set**,
as Plan C did: this slice is the shape, curation is step 8.

Spec: `docs/superpowers/specs/2026-08-06-drugref-slice-5c1-curated-overlay-design.md`.
Gives issue 51's 168 contradicted pairs a worklist and a home for the answer; it does
not answer them.

## What landed
- `db/029` — `curated_interaction` (keyed on the class RULE) and `curated_condition`
  (keyed on the PAIR, deliberately without `relationship`), both on Plan C's overlay
  floor with no new PL/pgSQL; two inner-joined read views; two gap views; one orphan
  check view.
- `curation.py` — the two writers over `overlay.supersede`.
- `questions.py` — the two new gap kinds, and the retention guard extended to five
  tables, without which the first curated row would have aborted the next ingest.

## Measured
<fill from Task 6 steps 2-4: the unchanged counts, the two new cardinalities, timings>

## Reviewer notes
- `curated_condition`'s key omits `relationship` while its sibling's includes it. That
  asymmetry is the design, not an oversight — see spec §3.
- A `spurious` ruling does NOT remove its candidate from any projection.
- ROADMAP's and PROJECT-NOTES' "signed overlay" wording is corrected to "signable":
  nothing signs anything yet, and signing must land before the first curated row.
- DDInter is removed from the source ladder: CC BY-NC-SA, so rule 6 blocks bundling.
BODY
)"
```

Replace the `<fill …>` line with the real measurements before opening the PR. The PR body must link issue 51
**without** a closing keyword — write "issue 51", not "closes #51". This slice gives
those 168 pairs a queue and a home for the answer; it does not answer them, and **sweep-closed-but-unfixed has
happened four times** (#31, #35, #40, #61). The linker binds a closing keyword to the next `#N` in the phrase, and
intervening words do not save you.

---

## Notes for the implementer

- **`ddi_candidate_pair` column names differ from the base tables'**: the view exposes `subject_moiety`,
  `partner_moiety`, `via_class`, `member_class`, while `class_contraindication` has `subject_moiety_uuid`,
  `object_class_uuid`. Joining them requires spelling both, and getting it wrong yields zero rows silently.
- **`forbid_multiple_live_assertions` is DEFERRED.** Any test asserting it must force the check with
  `SET CONSTRAINTS ALL IMMEDIATE`, which switches the mode for the rest of that transaction — put it last, or use
  a fresh transaction afterwards.
- **The `conn` fixture rolls back after each test** and does not TRUNCATE. Code under test here never commits, so
  rollback isolation holds; do not add a commit to `curation.py` to make a test easier.
- **If a test needs a second moiety**, mint it with a distinct UNII (`ids.mint_moiety_uuid("TESTUNII02")`) — the
  `a_moiety` fixture always returns the same UUID, so two calls do not give two drugs.

# DrugCentral `ddi` Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest DrugCentral's NDF-RT-sourced `ddi` table as drugref's third interaction candidate source, giving 7,501 graded drug–drug pairs a rebuildable projection and the first consumer read path for exact pairs.

**Architecture:** One assertion table (`drugcentral_ddi_assertion`) holds every bundleable published row verbatim — endpoints, the VA severity band, and nullable resolved moiety UUIDs. Two views sit over it: `drugcentral_ddi_pair` canonicalises to unordered `least/greatest` pairs, joins a seeded severity-mapping table and collapses both-order duplicates most-severe-first; `exact_ddi_pair` unions that with MED-RT's `moiety_contraindication` rows. `ddi_candidate_pair` is not touched.

**Tech Stack:** Python 3.12, `uv`, `psycopg` v3, PostgreSQL ≥ 18, pytest, ruff.

**Spec:** [`docs/superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-design.md`](../specs/2026-08-23-drugref-drugcentral-ddi-ingest-design.md)

## Global Constraints

- **Licence (CLAUDE.md rule 6).** Only `ddi_ref_id = 2` (VHA NDF-RT) may be ingested. `1` (Stockley's, a copyrighted book) and `3` (Lexicomp, commercial) are permanently out. `BUNDLEABLE_REF_IDS` has exactly one home in the code, and the orchestrator additionally verifies the dump's own `reference` row identity before admitting anything.
- **Architecture invariants.** Parsers are pure and streaming with no DB access; orchestrators own the transaction and are the only writers. `moiety_uuid` is immortal; `identity_claim` is append-only. Per-source rebuilds are delete-and-rebuild keyed by `ingest_run.source`.
- **Coding rules.** TDD — the failing test first, always. Pure functions in small reusable modules. Inline documentation a junior contributor can follow is mandatory. Files stay under ~500 lines where feasible.
- **Migrations.** `db/049_drugcentral_ddi.sql` is the ONE new migration. It is built up across Tasks 2–7 **while the branch is unmerged**, which is permitted (the ledger binds a database, not the repo) — but every task that edits it must re-run the full DB-gated suite, because `conftest._migrated` drops and re-applies the schema each session.
- **Test command.** `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`. Never with `-k` or `--deselect` when claiming green.
- **Lint command.** `uv run ruff check .` and `uv run ruff format .`.
- **Suite count.** PROJECT-NOTES § "How to run / test" is the ONE home for the number. It reads **1888** at the start of this round. Update it there and nowhere else.
- **Names fixed by this plan** (used across tasks, do not rename): source `'DRUGCENTRAL'`, writer `'drugcentral_run'`, tables `drugref.ddi_source_severity` / `drugref.drugcentral_ddi_assertion`, views `drugref.drugcentral_ddi_pair` / `drugref.exact_ddi_pair` / `drugref.gap_unresolved_ddi_endpoint`, gap kind `'unresolved_ddi_endpoint'`.

---

### Task 1: Move the dump reader and resolver into `src/`

The re-measurement's parser and cascade become production ingest code. Writing a second copy for the ingest would put "what a `ddi` row means" in two homes, which is the defect this project has lost six rounds to. This task changes **no behaviour** — it is a verbatim move plus import updates, and the existing tests are the proof.

**Files:**
- Create: `src/drugref/ingest/drugcentral_dump.py` (moved from `tools/drugcentral_dump.py`)
- Create: `src/drugref/ingest/drugcentral_resolve.py` (moved from `tools/drugcentral_resolve.py`)
- Delete: `tools/drugcentral_dump.py`, `tools/drugcentral_resolve.py`
- Modify: `tools/drugcentral_cache.py`, `tools/drugcentral_ddi_measure.py`, `tools/drugcentral_ddi_report.py`, `tools/drugcentral_ddi_spike.py` (imports)
- Modify: `tests/test_drugcentral_dump_parser.py`, `tests/test_drugcentral_resolve.py` (imports)

**Interfaces:**
- Consumes: nothing.
- Produces: `drugref.ingest.drugcentral_dump.{CopyFormatError, parse_copy_header, decode_copy_field, decode_copy_row, iter_copy_rows}` and `drugref.ingest.drugcentral_resolve.{ROUTE_DISPLAY_NAME, ROUTE_INCHIKEY, ROUTE_CAS, ROUTE_NOT_A_SUBSTANCE, ROUTE_NO_STRUCTURAL_KEY, ROUTE_MISSING_KEYS_ROW, ROUTE_UNRESOLVED, RESOLVED_ROUTES, UNRESOLVED_ROUTES, ROUTES, Resolution, fold_name, Registry, EndpointIndex, build_endpoint_index, resolve_endpoint, unordered_pair, first_wins}` — all signatures unchanged except the new public `first_wins`.

- [ ] **Step 1: Record the current green state**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q 2>&1 | tail -3`

Expected: `1888 passed`. Write the number down; Step 6 must reproduce it exactly.

- [ ] **Step 2: Move both files with `git mv`, so the diff shows a rename**

```bash
git mv tools/drugcentral_dump.py src/drugref/ingest/drugcentral_dump.py
git mv tools/drugcentral_resolve.py src/drugref/ingest/drugcentral_resolve.py
```

Do **not** edit their contents in this step. A rename git can see is what lets a reviewer confirm nothing changed.

- [ ] **Step 3: Update every importer**

The four `tools/` modules and two test modules import these by their old paths. Rewrite each import:

```bash
grep -rln "tools.drugcentral_dump\|tools.drugcentral_resolve\|from tools import" tools tests
# then, in each file:
#   from tools.drugcentral_dump   import ...  ->  from drugref.ingest.drugcentral_dump   import ...
#   from tools.drugcentral_resolve import ... ->  from drugref.ingest.drugcentral_resolve import ...
```

Verify none are left: `grep -rn "tools.drugcentral_dump\|tools.drugcentral_resolve" . --include='*.py'` must print nothing.

**No re-export shims.** A shim is a third home for the same names, and this task exists to remove the second one.

- [ ] **Step 4: Write the failing test for `first_wins`**

`tools/drugcentral_ddi_spike.py` holds `_first_wins` privately. The orchestrator (Task 11) needs the identical rule — first-wins over a deterministically ordered read, because 14 InChIKeys and 29 CAS numbers are claimed by more than one moiety. Add to `tests/test_drugcentral_resolve.py`:

```python
def test_first_wins_keeps_the_first_row_and_counts_the_collisions():
    """The rule that makes a colliding structural key resolve the same way twice.

    identity_claim is unique on (moiety_uuid, scheme, value) and deliberately NOT
    across moieties, so two moieties may legitimately carry one CAS number. The
    caller reads under a deterministic ORDER BY; this decides what happens when
    that ordered read hands over the same key twice.
    """
    lookup, duplicates = drugcentral_resolve.first_wins(
        [("aaa", "uuid-1"), ("aaa", "uuid-2"), ("bbb", "uuid-3")])
    assert lookup == {"aaa": "uuid-1", "bbb": "uuid-3"}
    assert duplicates == 1


def test_first_wins_counts_nothing_when_every_key_is_unique():
    lookup, duplicates = drugcentral_resolve.first_wins(
        [("aaa", "uuid-1"), ("bbb", "uuid-2")])
    assert lookup == {"aaa": "uuid-1", "bbb": "uuid-2"}
    assert duplicates == 0
```

- [ ] **Step 5: Run it and watch it fail**

Run: `uv run pytest tests/test_drugcentral_resolve.py -k first_wins -v`
Expected: FAIL — `AttributeError: module 'drugref.ingest.drugcentral_resolve' has no attribute 'first_wins'`.

- [ ] **Step 6: Move `_first_wins` into the resolver as a public function**

Cut the body of `_first_wins` out of `tools/drugcentral_ddi_spike.py` and paste it into `src/drugref/ingest/drugcentral_resolve.py` under its public name, keeping the docstring and extending it:

```python
def first_wins(rows: Sequence[tuple[str, str]]) -> tuple[dict[str, str], int]:
    """Fold ``(key, uuid)`` rows into a lookup, counting keys claimed more than once.

    First-wins over a deterministically ORDERED read, which is the rule
    `src/drugref/classes.py` states for the same join: `identity_claim` is unique
    on ``(moiety_uuid, scheme, value)`` and deliberately NOT across moieties, so
    two moieties may legitimately carry one CAS number. An unordered single-row
    read *"could answer differently run to run"*.

    PUBLIC, and shared by the measurement instrument and the ingest. Both build the
    same three lookups against the same table; two private copies of this rule would
    be two chances for one of them to stop being first-wins.

    The collision count is returned rather than discarded, because the previous
    docstring promised the totals would report duplicates and nothing did.
    """
    lookup: dict[str, str] = {}
    duplicates = 0
    for key, uuid in rows:
        if key in lookup:
            duplicates += 1
            continue
        lookup[key] = uuid
    return lookup, duplicates
```

In `tools/drugcentral_ddi_spike.py`, import it and replace the three `_first_wins(...)` call sites with `first_wins(...)`. Add `Sequence` to the resolver's `typing`/`collections.abc` imports if it is not already there.

- [ ] **Step 7: Run the full suite**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q 2>&1 | tail -3`
Expected: `1890 passed` — Step 1's 1888 plus the two new `first_wins` tests, and **no failures**. Any other delta means the move was not verbatim.

Run: `uv run ruff check .`
Expected: no findings.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "drugcentral: move the dump reader and cascade into src/drugref/ingest

They stop being spike code the moment an ingest depends on them. Writing a
second parser and a second cascade would put 'what a ddi row means' in two
homes. Verbatim move plus first_wins made public, which the ingest needs for
the same colliding-key rule the measurement uses.

Refs #101"
```

---

### Task 2: `db/049` — admit `DRUGCENTRAL` as a source

**Files:**
- Create: `db/049_drugcentral_ddi.sql`
- Modify: `src/drugref/ids.py:49-72` (`_SOURCE_CANONICAL`)
- Modify: `src/drugref/provenance.py:27-28` (`WRITERS`)
- Test: `tests/test_drugcentral_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the source spelling `'DRUGCENTRAL'` and writer `'drugcentral_run'` accepted by `drugref.ingest_run`; `ids.canonical_source("drugcentral") == "DRUGCENTRAL"`.

- [ ] **Step 1: Read the live CHECKs — do not retype them from any document**

```bash
psql "host=localhost port=5532 dbname=drugref_test user=postgres" -Atc \
  "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
   WHERE conname IN ('ingest_run_source','ingest_run_writer');"
```

db/039's own comment records why: a plan's stale retyped list would have dropped `'DRUGREF'`. Copy what the catalog prints.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_drugcentral_schema.py`:

```python
# tests/test_drugcentral_schema.py
"""db/049's shape: the source vocabulary, the severity map, the assertion table.

WHY A SCHEMA TEST AT ALL, when later tasks exercise the same objects: a new
source spelling is not a one-line change. It must land in the database CHECK,
in ids._SOURCE_CANONICAL and in provenance.WRITERS *in the same migration*, and
the failure mode when it does not is silent -- a per-source rebuild deletes
nothing and reports success. These tests are the guard against that silence.
"""
import psycopg
import pytest

from drugref import ids, provenance


def test_drugcentral_is_a_canonical_source_spelling():
    """Listed EXPLICITLY, though the upper-case fall-through would also produce it.

    ids.py's own docstring warns by name against leaning on that fall-through:
    'openFDA-SPL' and 'MeDIC' fold to spellings a mixed-case CHECK would never
    match. 'DRUGCENTRAL' survives by luck, exactly as 'GSRS', 'DRUGREF' and
    'FDA-CYP' do, and the entry records that the luck was CHECKED.
    """
    assert ids.canonical_source("DRUGCENTRAL") == "DRUGCENTRAL"
    assert ids.canonical_source("drugcentral") == "DRUGCENTRAL"
    assert ids.canonical_source("  DrugCentral  ") == "DRUGCENTRAL"


def test_drugcentral_run_is_a_declared_writer():
    """provenance.WRITERS and db/049's CHECK are a PAIR (db/020's source-trio lesson)."""
    assert "drugcentral_run" in provenance.WRITERS


@pytest.mark.usefixtures("conn")
def test_ingest_run_admits_the_drugcentral_source_and_writer(conn):
    conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run')")


@pytest.mark.usefixtures("conn")
def test_ingest_run_still_refuses_a_misspelled_drugcentral_source(conn):
    """'DRUG-CENTRAL' is the typo db/012 finding 3 describes: it would insert
    cleanly under an unconstrained column and then match nothing, ever."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.ingest_run "
            "(source, upstream_release, source_checksum, writer) "
            "VALUES ('DRUG-CENTRAL', '11012023', 'deadbeef', 'drugcentral_run')")


@pytest.mark.usefixtures("conn")
def test_class_contraindication_source_is_NOT_widened(conn):
    """DrugCentral writes no class rule, so its source must stay OUT of that CHECK.

    HANDOVER said this CHECK needed widening for this source. It does not, and a
    widened CHECK would admit a row no writer in this project produces -- which is
    how a vocabulary grows a value nothing means.
    """
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'class_contraindication_source'").fetchone()
    assert "DRUGCENTRAL" not in definition
```

- [ ] **Step 3: Run them and watch them fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_schema.py -v`
Expected: FAIL — `canonical_source` returns `'DRUGCENTRAL'` already (that one passes by luck, which is the point of the test), `drugcentral_run` is not in `WRITERS`, and the `ingest_run` INSERT raises `CheckViolation`.

- [ ] **Step 4: Write the migration's first section**

Create `db/049_drugcentral_ddi.sql`:

```sql
-- db/049_drugcentral_ddi.sql
-- =============================================================================
-- DrugCentral's `ddi` table as drugref's third interaction candidate source.
-- Design: docs/superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-design.md
-- Measurement it rests on:
--   docs/superpowers/specs/2026-08-23-drugref-drugcentral-ddi-remeasurement-results.md
--
-- RULE 6 IN ONE LINE: only `ddi_ref_id = 2` (VHA NDF-RT, a US federal work) is
-- ingested. `1` is Stockley's Drug Interactions (a copyrighted book) and `3` is
-- Lexicomp Online (a commercial compendium); both are permanently out, and
-- DrugCentral's own CC BY-SA 4.0 on the compilation is not evidence of a right to
-- relicense a third-party compendium inside it. The orchestrator additionally
-- verifies the dump's `reference` row identity before admitting a row, because `2`
-- is a surrogate key and a re-publication is free to renumber it.
-- =============================================================================

-- ============================================================================
-- 1. The source vocabulary -- two CHECKs and one Python table, in one commit
-- ============================================================================
-- COPIED VERBATIM from the live catalog and then extended by one value, never
-- retyped from a document. db/039's comment records the reason: a plan's stale
-- list still said ('MED-RT','MeSH') and would have DROPPED 'DRUGREF'.
--
-- src/drugref/ids.py gains "DRUGCENTRAL": "DRUGCENTRAL" in the same commit, and
-- src/drugref/provenance.py gains 'drugcentral_run'. The three are a TRIO: the
-- failure mode when one lands without the others is silent -- ids.canonical_source
-- would fold the source to a spelling this CHECK does not admit, and a per-source
-- rebuild would delete nothing and report success.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF', 'GSRS',
                      'ONCHIGH', 'FDA-CYP', 'DRUGCENTRAL'));

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run',
                      'onchigh_run', 'fda_cyp_run', 'drugcentral_run'));

-- NOTE what is deliberately NOT widened, because a state file said otherwise:
-- class_contraindication_source stays ('MED-RT','ONCHIGH') and
-- moiety_contraindication_source stays ('MED-RT'). DrugCentral writes no class
-- rule and no row into either table -- its assertions are unordered moiety pairs
-- with a severity, which is neither shape.
```

**Before writing the two CHECK lists above, replace them with what Step 1 actually printed.** The lists here are what the catalog held on 2026-08-23; treat them as a shape, not a source.

- [ ] **Step 5: Add the Python halves**

In `src/drugref/ids.py`, append inside `_SOURCE_CANONICAL` (after the `FDA-CYP` entry):

```python
    # Issue 101. 'DRUGCENTRAL' survives the upper-case fall-through unchanged, as
    # 'FDA-CYP', 'GSRS' and 'DRUGREF' do -- and is listed for the same reason: the
    # entry records that the luck was CHECKED rather than assumed. db/049 widens
    # ingest_run's source CHECK to match, and provenance.WRITERS gains
    # 'drugcentral_run'; the three are a trio.
    "DRUGCENTRAL": "DRUGCENTRAL",
```

In `src/drugref/provenance.py`, extend `WRITERS`:

```python
WRITERS = ("unii_run", "chebi", "medrt_run", "mesh_run", "mesh_rel_run", "pbs_run",
           "curation", "unattributed", "gsrs_run", "onchigh_run", "fda_cyp_run",
           "drugcentral_run")
```

- [ ] **Step 6: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_schema.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 7: Commit**

```bash
git add db/049_drugcentral_ddi.sql src/drugref/ids.py src/drugref/provenance.py tests/test_drugcentral_schema.py
git commit -m "drugcentral: admit DRUGCENTRAL as a source (db/049 section 1)

The CHECK, ids._SOURCE_CANONICAL and provenance.WRITERS in one commit -- the
trio whose failure mode is a per-source rebuild that silently deletes nothing.
CHECK lists copied off the live catalog, not retyped.

class_contraindication_source is NOT widened, correcting HANDOVER: DrugCentral
writes no class rule.

Refs #101"
```

---

### Task 3: `db/049` — the severity mapping table

**Files:**
- Modify: `db/049_drugcentral_ddi.sql` (append section 2)
- Test: `tests/test_drugcentral_schema.py`

**Interfaces:**
- Consumes: Task 2's source vocabulary.
- Produces: `drugref.ddi_source_severity(source, source_label, severity)`, seeded with `('DRUGCENTRAL','Critical','contraindicated')` and `('DRUGCENTRAL','Significant','moderate')`. Task 4's assertion table foreign-keys into it; Task 5's view joins it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_drugcentral_schema.py`:

```python
@pytest.mark.usefixtures("conn")
def test_the_two_va_bands_are_seeded_and_mapped(conn):
    """VA/NDF-RT's own semantics: Critical = avoid, Significant = monitor/adjust.

    `major` is deliberately unused by this source. A two-band authority has two
    bands, and spreading them across three grades would invent a distinction VA
    does not draw.
    """
    rows = conn.execute(
        "SELECT source_label, severity FROM drugref.ddi_source_severity "
        "WHERE source = 'DRUGCENTRAL' ORDER BY source_label").fetchall()
    assert rows == [("Critical", "contraindicated"), ("Significant", "moderate")]


@pytest.mark.usefixtures("conn")
def test_a_mapped_severity_must_be_a_real_grade(conn):
    """The FK into severity_kind is what stops a mapping naming a grade that has
    no rank -- and severity_rank is what decides which of two grades a consumer
    sees, so a rankless one would make that non-deterministic."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.ddi_source_severity "
            "(source, source_label, severity) "
            "VALUES ('DRUGCENTRAL', 'Catastrophic', 'apocalyptic')")


@pytest.mark.usefixtures("conn")
def test_the_mapping_is_keyed_per_source(conn):
    """Two authorities may both use the word 'Significant' and mean different
    things, so the label alone is not the key."""
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'ddi_source_severity_pkey'").fetchone()
    assert definition == "PRIMARY KEY (source, source_label)"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_schema.py -v -k "band or grade or per_source"`
Expected: FAIL — `relation "drugref.ddi_source_severity" does not exist`.

- [ ] **Step 3: Append section 2 to the migration**

```sql
-- ============================================================================
-- 2. ddi_source_severity -- an upstream band mapped to a drugref grade, AS DATA
-- ============================================================================
-- WHY A TABLE AND NOT FOUR LINES OF PYTHON. db/006's finding, one tier up: a
-- vocabulary written in code and in a CHECK is two lists to widen and one way to
-- disagree. And this mapping is additionally a CLINICAL JUDGEMENT that drugref
-- makes on a consumer's behalf -- a node operator must be able to SELECT it,
-- disagree with it, and see exactly what it did. Revising it is then a migration
-- over two rows rather than a re-ingest of 7,571.
--
-- THE MAPPING. VA/NDF-RT's own wording is "Critical = avoid the combination" and
-- "Significant = may have clinical consequences; monitor or adjust", so each band
-- maps to the drugref grade that says the same thing.
--
-- `major` CARRIES NO DRUGCENTRAL ROW, and that is a signal rather than an
-- omission: a two-band authority has two bands. The cost is stated rather than
-- hidden -- some `Significant` pairs (fluvoxamine + tapentadol, apixaban +
-- heparin) are arguably major and are graded a notch low. That is what the
-- curated overlay exists to correct, one pair at a time, and it is why this
-- mapping's revisability is load-bearing.
CREATE TABLE IF NOT EXISTS drugref.ddi_source_severity (
    source       text NOT NULL,
    source_label text NOT NULL,
    -- db/035's four grades and THEIR CLINICAL ORDER. A foreign key, not a CHECK:
    -- severity_rank is what decides which of two disagreeing grades a consumer
    -- sees, and a level with no agreed rank would make that non-deterministic.
    severity     text NOT NULL REFERENCES drugref.severity_kind(severity),
    -- Keyed PER SOURCE, because two authorities may both use the word
    -- 'Significant' and mean different things by it.
    PRIMARY KEY (source, source_label)
);

INSERT INTO drugref.ddi_source_severity (source, source_label, severity) VALUES
    ('DRUGCENTRAL', 'Critical',    'contraindicated'),
    ('DRUGCENTRAL', 'Significant', 'moderate')
ON CONFLICT (source, source_label) DO NOTHING;

COMMENT ON TABLE drugref.ddi_source_severity IS
    'How one upstream authority''s severity vocabulary maps onto drugref''s four '
    'grades. SEEDED, NOT CURATED: a new mapping is a migration, deliberately, '
    'because the mapping is a clinical judgement drugref makes on a consumer''s '
    'behalf and it must be inspectable by anyone who can run a query. The '
    'candidate tier stores the upstream label VERBATIM (drugcentral_ddi_assertion.'
    'severity_label) and derives the grade through this table, so the authority''s '
    'own words survive and drugref''s reading of them is separately visible.';
COMMENT ON COLUMN drugref.ddi_source_severity.source_label IS
    'The upstream string EXACTLY as published -- ''Critical'', not ''critical''. '
    'Folding it here would put a case rule in a second place.';
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_schema.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add db/049_drugcentral_ddi.sql tests/test_drugcentral_schema.py
git commit -m "drugcentral: map the VA severity bands as data (db/049 section 2)

Critical -> contraindicated, Significant -> moderate, per VA/NDF-RT's own
wording. \`major\` is deliberately unused: a two-band authority has two bands.
A table rather than four lines of Python because the mapping is a clinical
judgement a node operator must be able to SELECT and disagree with.

Refs #101"
```

---

### Task 4: `db/049` — the assertion table

**Files:**
- Modify: `db/049_drugcentral_ddi.sql` (append section 3)
- Test: `tests/test_drugcentral_schema.py`

**Interfaces:**
- Consumes: Tasks 2–3; `drugref.ingest.drugcentral_resolve.{ROUTES, RESOLVED_ROUTES}` from Task 1.
- Produces: `drugref.drugcentral_ddi_assertion(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid, route_1, route_2)`. Task 8's writer inserts into it; Tasks 5 and 7 read it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_drugcentral_schema.py` (add `from drugref.ingest import drugcentral_resolve` to the imports):

```python
def _open_run(conn):
    """A DRUGCENTRAL ingest_run to hang assertion rows off. Returns its id."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def _a_moiety(conn, name):
    """A gated-in moiety to resolve an endpoint onto. Returns its uuid."""
    return conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name) "
        "VALUES (gen_random_uuid(), %s) RETURNING moiety_uuid", (name,)).fetchone()[0]


@pytest.mark.usefixtures("conn")
def test_an_assertion_row_round_trips(conn):
    run, one, two = _open_run(conn), _a_moiety(conn, "a"), _a_moiety(conn, "b")
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid, "
        " route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', 'C56.3352', 'gemfibrozil', 'pioglitazone', "
        "        'GEMFIBROZIL/PIOGLITAZONE HCL [VA Drug Interaction]', "
        "        'Significant', %s, %s, 'display_name', 'display_name')",
        (run, one, two))


@pytest.mark.usefixtures("conn")
def test_an_unmapped_severity_band_is_refused_at_insert(conn):
    """The load-bearing constraint. A future release inventing a third band must
    be REFUSED, not stored and silently mapped to nothing."""
    run, one, two = _open_run(conn), _a_moiety(conn, "a"), _a_moiety(conn, "b")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid, "
            " route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', "
            "        'Potentially significant', %s, %s, "
            "        'display_name', 'display_name')",
            (run, one, two))


@pytest.mark.usefixtures("conn")
def test_a_resolved_route_without_a_moiety_is_unrepresentable(conn):
    run = _open_run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', 'Critical', "
            "        'display_name', 'unresolved')", (run,))


@pytest.mark.usefixtures("conn")
def test_a_moiety_on_an_unresolved_route_is_unrepresentable(conn):
    run, one = _open_run(conn), _a_moiety(conn, "a")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.drugcentral_ddi_assertion "
            "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
            " upstream_label, severity_label, moiety_1_uuid, route_1, route_2) "
            "VALUES (%s, 'DRUGCENTRAL', 'X', 'a', 'b', 'A/B [VA]', 'Critical', "
            "        %s, 'unresolved', 'unresolved')", (run, one))


@pytest.mark.usefixtures("conn")
def test_an_unresolved_row_is_stored_rather_than_dropped(conn):
    """db/039's fda_cyp_assertion states the principle: the withheld rows are the
    point. An endpoint drugref cannot key is a WORKLIST ENTRY, not a drop."""
    run = _open_run(conn)
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', 'X', 'phytomenadione', 'warfarin', "
        "        'PHYTONADIONE/WARFARIN [VA Drug Interaction]', 'Critical', "
        "        'unresolved', 'unresolved')", (run,))


@pytest.mark.usefixtures("conn")
@pytest.mark.parametrize("constraint,expected", [
    ("drugcentral_ddi_assertion_route_1", "ROUTES"),
    ("drugcentral_ddi_assertion_route_2", "ROUTES"),
    ("drugcentral_ddi_assertion_endpoint_1_complete", "RESOLVED_ROUTES"),
    ("drugcentral_ddi_assertion_endpoint_2_complete", "RESOLVED_ROUTES"),
])
def test_the_route_checks_match_the_python_vocabulary(conn, constraint, expected):
    """THE PINNING TEST FOR AN ADMITTED SECOND HOME.

    drugcentral_resolve holds the closed route vocabulary as frozensets; these
    CHECKs restate it in SQL. That is a vocabulary in two places -- the defect
    db/006 was written to remove -- and it is admitted here deliberately, on the
    same terms ids._SOURCE_CANONICAL and ingest_run_source already live under:
    the two are a pair, and a test asserts both. A route added to Python and not
    to the CHECK would abort an ingest; a route REMOVED from Python while the
    CHECK still admits it would leave the database accepting a label nothing
    produces, which is the direction no error catches.
    """
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = %s", (constraint,)).fetchone()
    wanted = getattr(drugcentral_resolve, expected)
    found = {token.strip().strip("'")
             for token in definition.split("ARRAY[")[1].split("]")[0].split(",")}
    found = {value.split("::")[0].strip("'") for value in found}
    assert found == set(wanted), (
        f"{constraint} admits {sorted(found)}, "
        f"drugcentral_resolve.{expected} is {sorted(wanted)}")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_schema.py -v`
Expected: the new tests FAIL with `relation "drugref.drugcentral_ddi_assertion" does not exist`.

- [ ] **Step 3: Append section 3 to the migration**

```sql
-- ============================================================================
-- 3. drugcentral_ddi_assertion -- every bundleable row, exactly as published
-- ============================================================================
-- A rebuildable projection keyed by ingest_run.source: delete-and-rebuild, like
-- every other ingested feed. The shape is db/039's fda_cyp_assertion, whose
-- comment states the principle -- "it holds every tuple the parser produced,
-- members and withheld alike, because the withheld ones are the point". Here the
-- withheld ones are the 37 rows whose endpoint drugref cannot key, and holding
-- them is what lets section 5's gap view exist without a table of its own.
CREATE TABLE IF NOT EXISTS drugref.drugcentral_ddi_assertion (
    ingest_run      bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- Symmetric with class_contraindication.source and every other projection's:
    -- widened per source as authorities land, not left open.
    source          text   NOT NULL
        CONSTRAINT drugcentral_ddi_assertion_source CHECK (source = 'DRUGCENTRAL'),
    -- DrugCentral's `ddi.source_id` -- the VA's OWN key for the interaction record
    -- ('C56^4966^'), NOT `ddi.id`. Measured 2026-08-23: all 7,571 bundleable rows
    -- carry a distinct source_id, so it is a valid key -- and it is the upstream
    -- AUTHORITY's identifier rather than an artifact of one dump's row numbering,
    -- which is what a key anything downstream might cite has to be.
    upstream_key    text   NOT NULL,
    -- The two endpoints AS THE DUMP GIVES THEM (`drug_class1`, `drug_class2`).
    -- Verbatim, never folded: fold_name is the resolver's rule and belongs in one
    -- place. The names are stored even when they resolved, because a name that
    -- STOPS resolving in a later release is only diagnosable against the name the
    -- earlier run actually read.
    endpoint_1_name text   NOT NULL,
    endpoint_2_name text   NOT NULL,
    -- `ddi.description`. MEASURED 2026-08-23: all 7,571 match
    -- 'NAME1/NAME2 [VA Drug Interaction]' -- 35 to 75 characters, no clinical
    -- content whatsoever, so issue 101's "every row carries a description" is true
    -- and empty. It is stored anyway for one reason: it names the endpoints at
    -- PRODUCT/SALT grain ('PIOGLITAZONE HCL', 'INDINAVIR SULFATE') while the
    -- endpoint columns carry the base, and that is the only visible explanation
    -- for why 33 pairs appear twice (see the view in section 4).
    upstream_label  text   NOT NULL,
    -- `ddi_risk`, VERBATIM -- 'Critical' or 'Significant' in this subset. drugref's
    -- own grade is DERIVED through ddi_source_severity, never stored here, so the
    -- authority's words and drugref's reading of them stay separately visible.
    severity_label  text   NOT NULL,
    -- NULLABLE, and that is the whole design of this table. An endpoint drugref
    -- cannot key leaves the row here with a NULL uuid and a route saying why.
    moiety_1_uuid   uuid   REFERENCES drugref.substance_moiety(moiety_uuid),
    moiety_2_uuid   uuid   REFERENCES drugref.substance_moiety(moiety_uuid),
    -- HOW each endpoint resolved, or why it did not. The vocabulary is
    -- drugcentral_resolve.ROUTES and this CHECK is its SECOND home -- admitted
    -- deliberately and pinned by test_the_route_checks_match_the_python_vocabulary,
    -- on the same terms ids._SOURCE_CANONICAL and ingest_run_source already live
    -- under. `missing_keys_row` is in the list on purpose: it means a struct_id was
    -- found by name and is absent from the key index, which cannot happen on a
    -- well-formed extract -- counted apart so a corrupt extract does not pass for a
    -- difficult one.
    route_1         text   NOT NULL,
    route_2         text   NOT NULL,
    PRIMARY KEY (ingest_run, source, upstream_key),
    -- THE LOAD-BEARING CONSTRAINT. A release inventing a third band is refused at
    -- INSERT, loudly, rather than stored and silently mapped to nothing by the
    -- view's join. db/006's lesson applied to a vocabulary that crosses a source
    -- boundary.
    CONSTRAINT drugcentral_ddi_assertion_severity
        FOREIGN KEY (source, severity_label)
        REFERENCES drugref.ddi_source_severity(source, source_label),
    CONSTRAINT drugcentral_ddi_assertion_route_1 CHECK (route_1 IN
        ('display_name', 'inchikey', 'cas',
         'not_a_substance', 'no_structural_key', 'missing_keys_row', 'unresolved')),
    CONSTRAINT drugcentral_ddi_assertion_route_2 CHECK (route_2 IN
        ('display_name', 'inchikey', 'cas',
         'not_a_substance', 'no_structural_key', 'missing_keys_row', 'unresolved')),
    -- ONE CHECK PER ENDPOINT, not two nullable columns nobody cross-checks --
    -- curated_interaction_ruling_is_complete's shape. "Resolved but no uuid" and
    -- "a uuid on an unresolved route" are both UNREPRESENTABLE rather than merely
    -- discouraged.
    CONSTRAINT drugcentral_ddi_assertion_endpoint_1_complete
        CHECK ((route_1 IN ('display_name', 'inchikey', 'cas'))
               = (moiety_1_uuid IS NOT NULL)),
    CONSTRAINT drugcentral_ddi_assertion_endpoint_2_complete
        CHECK ((route_2 IN ('display_name', 'inchikey', 'cas'))
               = (moiety_2_uuid IS NOT NULL))
    -- NO SELF-PAIR CHECK, and the asymmetry with db/014's
    -- moiety_contraindication_not_self is deliberate. There a self-pair is a
    -- malformed assertion; here it is a CONSEQUENCE OF RESOLUTION -- two endpoint
    -- names legitimately folding onto one moiety -- so refusing it would abort an
    -- ingest over a correct reading of the source. The view in section 4 excludes
    -- it and the orchestrator's summary counts it as its own bucket, so it cannot
    -- become nonzero unnoticed. Measured 2026-08-23: 0 of 7,571.
);

CREATE INDEX IF NOT EXISTS drugcentral_ddi_assertion_by_moiety_1
    ON drugref.drugcentral_ddi_assertion (moiety_1_uuid);
CREATE INDEX IF NOT EXISTS drugcentral_ddi_assertion_by_moiety_2
    ON drugref.drugcentral_ddi_assertion (moiety_2_uuid);

COMMENT ON TABLE drugref.drugcentral_ddi_assertion IS
    'DrugCentral''s `ddi` table, `ddi_ref_id = 2` only (VHA NDF-RT), one row per '
    'published assertion. A REBUILDABLE PROJECTION, CANDIDATE TIER -- the 2023 '
    'release does not refresh, so rows feed review and must not auto-alert. '
    'UNORDERED: endpoint_1 and endpoint_2 are NOT subject and object. Measured '
    '2026-08-23, no ordered endpoint pair repeats and 33 appear in BOTH orders, '
    'so this source asserts no direction -- read drugcentral_ddi_pair, which '
    'canonicalises, rather than either endpoint column alone.';
COMMENT ON COLUMN drugref.drugcentral_ddi_assertion.severity_label IS
    'The upstream band VERBATIM. drugref''s grade is ddi_source_severity''s job.';
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_schema.py -v`
Expected: PASS, 18 tests.

- [ ] **Step 5: Commit**

```bash
git add db/049_drugcentral_ddi.sql tests/test_drugcentral_schema.py
git commit -m "drugcentral: the assertion table (db/049 section 3)

Every bundleable row as published, including the 37 whose endpoint does not
resolve -- db/039's fda_cyp_assertion shape, where the withheld rows are the
point. The severity FK refuses an unmapped band at INSERT; two completeness
CHECKs make 'resolved with no uuid' unrepresentable; the route CHECKs are an
admitted second home for drugcentral_resolve.ROUTES and are pinned by a test.

Refs #101"
```

---

### Task 5: `db/049` — `drugcentral_ddi_pair`

**Files:**
- Modify: `db/049_drugcentral_ddi.sql` (append section 4)
- Test: `tests/test_drugcentral_read_path.py`

**Interfaces:**
- Consumes: Tasks 3–4.
- Produces: view `drugref.drugcentral_ddi_pair(moiety_lo, moiety_hi, candidate_source, severity, severity_rank, upstream_severity_label, upstream_key, upstream_label, ingest_run, upstream_release, ingested_at)`. Task 6 unions it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_drugcentral_read_path.py`:

```python
# tests/test_drugcentral_read_path.py
"""db/049's two views: the canonical pair, and the union over all exact pairs.

The pair view carries THREE rules that exist nowhere else, so each has a test:
the orientation collapse, most-severe-wins between the two orientations, and a
total order so the collapse is stable when they tie.
"""
import psycopg
import pytest


def _run(conn, source="DRUGCENTRAL", writer="drugcentral_run", release="11012023"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, %s, 'deadbeef', %s) RETURNING ingest_run_id",
        (source, release, writer)).fetchone()[0]


def _moiety(conn, name):
    return conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name) "
        "VALUES (gen_random_uuid(), %s) RETURNING moiety_uuid", (name,)).fetchone()[0]


def _assert_row(conn, run, key, one, two, label, band="Significant",
                route_1="display_name", route_2="display_name"):
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid, "
        " route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', %s, 'one', 'two', %s, %s, %s, %s, %s, %s)",
        (run, key, label, band, one, two, route_1, route_2))


@pytest.mark.usefixtures("conn")
def test_both_orientations_collapse_to_one_row(conn):
    """Measured: 33 pairs are published in both orders, as two VA entries at
    different salt grains. They are one pair, not two."""
    run, a, b = _run(conn), _moiety(conn, "gatifloxacin"), _moiety(conn, "pioglitazone")
    _assert_row(conn, run, "fwd", a, b, "A/B HCL [VA Drug Interaction]")
    _assert_row(conn, run, "rev", b, a, "A/B [VA Drug Interaction]")
    rows = conn.execute("SELECT count(*) FROM drugref.drugcentral_ddi_pair").fetchone()
    assert rows == (1,)


@pytest.mark.usefixtures("conn")
def test_the_more_severe_orientation_wins(conn):
    """4 of the 33 disagree on the band. A consumer must not get the lower one."""
    run, a, b = _run(conn), _moiety(conn, "gemfibrozil"), _moiety(conn, "pioglitazone")
    _assert_row(conn, run, "fwd", a, b, "A/B HCL [VA]", band="Significant")
    _assert_row(conn, run, "rev", b, a, "A/B [VA]", band="Critical")
    (severity, rank) = conn.execute(
        "SELECT severity, severity_rank FROM drugref.drugcentral_ddi_pair").fetchone()
    assert (severity, rank) == ("contraindicated", 1)


@pytest.mark.usefixtures("conn")
def test_the_collapse_is_stable_when_the_two_orientations_tie(conn):
    """29 of the 33 duplicates carry the SAME band, so severity_rank ties and
    DISTINCT ON would otherwise keep whichever row the plan happened to emit
    first. upstream_key is the total order that makes it reproducible -- the
    defect the re-measurement's own review found in three unordered lookups."""
    run, a, b = _run(conn), _moiety(conn, "atazanavir"), _moiety(conn, "tadalafil")
    _assert_row(conn, run, "C56^4084^", a, b, "ATAZANAVIR/TADALAFIL [VA]")
    _assert_row(conn, run, "C23304710162045", b, a, "ATAZANAVIR SO4/TADALAFIL [VA]")
    seen = {conn.execute(
        "SELECT upstream_key FROM drugref.drugcentral_ddi_pair").fetchone()[0]
        for _ in range(5)}
    assert seen == {"C23304710162045"}, (
        "the collapse must pick the same row every time; got " + repr(seen))


@pytest.mark.usefixtures("conn")
def test_an_unresolved_row_yields_no_pair(conn):
    run = _run(conn)
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', 'X', 'vitamin e', 'warfarin', 'V/W [VA]', "
        "        'Critical', 'unresolved', 'unresolved')", (run,))
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_pair").fetchone() == (0,)


@pytest.mark.usefixtures("conn")
def test_a_self_pair_yields_no_pair(conn):
    """Two endpoint names folding onto one moiety asserts nothing about an
    interaction between two drugs -- the rule ddi_candidate_pair already applies."""
    run, a = _run(conn), _moiety(conn, "azithromycin")
    _assert_row(conn, run, "X", a, a, "A/A [VA]")
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_pair").fetchone() == (0,)


@pytest.mark.usefixtures("conn")
def test_the_pair_carries_the_upstream_band_beside_the_drugref_grade(conn):
    """Both, always. The authority's word is what a reviewer checks the mapping
    against, and a grade with no visible provenance cannot be disagreed with."""
    run, a, b = _run(conn), _moiety(conn, "a"), _moiety(conn, "b")
    _assert_row(conn, run, "X", a, b, "A/B [VA]", band="Significant")
    row = conn.execute(
        "SELECT severity, upstream_severity_label, candidate_source, upstream_release "
        "FROM drugref.drugcentral_ddi_pair").fetchone()
    assert row == ("moderate", "Significant", "DRUGCENTRAL", "11012023")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_read_path.py -v`
Expected: FAIL — `relation "drugref.drugcentral_ddi_pair" does not exist`.

- [ ] **Step 3: Append section 4 to the migration**

```sql
-- ============================================================================
-- 4. drugcentral_ddi_pair -- canonical unordered pairs, graded
-- ============================================================================
-- THREE RULES LIVE HERE AND NOWHERE ELSE, which is why this is a view and not
-- something the writer decided:
--
--  (a) ORIENTATION IS COLLAPSED. least/greatest gives one row per unordered pair.
--      The source publishes 33 pairs in both orders -- two VA entries at different
--      salt grains, visible in upstream_label -- and they are one pair.
--  (b) MOST-SEVERE-WINS between two orientations that disagree (4 of the 33 do).
--      `ORDER BY severity_rank` needs no DESC because db/035 made rank 1 the most
--      severe precisely so the safe read is the one a caller writes by default.
--  (c) A TOTAL ORDER, so (b) is REPRODUCIBLE. 29 of the 33 duplicates carry the
--      same band, so severity_rank ties and DISTINCT ON would otherwise keep
--      whichever row the plan emitted first -- and the reported upstream_key and
--      upstream_label could then differ between two runs over the same bytes.
--      That is exactly the defect found in three unordered registry lookups in the
--      round whose entire justification was reproducibility. upstream_key is a
--      primary-key component, so it breaks every tie.
--
-- db/037's standing instruction: the rule that chooses between two grades is
-- stated ONCE, in SQL, so a consumer querying from any language gets it.
CREATE OR REPLACE VIEW drugref.drugcentral_ddi_pair AS
SELECT DISTINCT ON (p.moiety_lo, p.moiety_hi)
       p.moiety_lo,
       p.moiety_hi,
       p.source               AS candidate_source,
       m.severity,                              -- drugref's grade, DERIVED
       s.severity_rank,
       p.severity_label       AS upstream_severity_label,  -- the authority's word
       p.upstream_key,
       p.upstream_label,
       p.ingest_run,
       r.upstream_release,                      -- WHICH release said so
       r.finished_at          AS ingested_at
FROM  (SELECT least(a.moiety_1_uuid, a.moiety_2_uuid)    AS moiety_lo,
              greatest(a.moiety_1_uuid, a.moiety_2_uuid) AS moiety_hi,
              a.*
         FROM drugref.drugcentral_ddi_assertion a
              -- Unresolved endpoints stay in the table and out of the pairs. They
              -- are gap_unresolved_ddi_endpoint's subject, not a consumer's.
        WHERE a.moiety_1_uuid IS NOT NULL
          AND a.moiety_2_uuid IS NOT NULL
              -- A rule whose two endpoints denote one substance asserts nothing
              -- about an interaction between two drugs.
          AND a.moiety_1_uuid <> a.moiety_2_uuid) p
JOIN  drugref.ddi_source_severity m
      ON m.source = p.source AND m.source_label = p.severity_label
JOIN  drugref.severity_kind s ON s.severity = m.severity
JOIN  drugref.ingest_run    r ON r.ingest_run_id = p.ingest_run
ORDER BY p.moiety_lo, p.moiety_hi, s.severity_rank, p.upstream_key;

COMMENT ON VIEW drugref.drugcentral_ddi_pair IS
    'DrugCentral''s NDF-RT interactions as ONE row per unordered moiety pair, '
    'carrying drugref''s derived grade beside the upstream band it came from. '
    'CANDIDATE TIER: the 2023 release does not refresh and nothing here is a '
    'drugref judgement -- the grade is ddi_source_severity''s reading of VA''s '
    'own band, and a curated ruling overrides it. Rows whose endpoint did not '
    'resolve are ABSENT rather than dropped: they are still in '
    'drugcentral_ddi_assertion and are published as questions.';
COMMENT ON COLUMN drugref.drugcentral_ddi_pair.upstream_severity_label IS
    'The authority''s own word, kept beside the derived grade so the mapping can '
    'be checked and disagreed with without re-reading a 1.4 GB dump.';
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_read_path.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add db/049_drugcentral_ddi.sql tests/test_drugcentral_read_path.py
git commit -m "drugcentral: canonical unordered pairs (db/049 section 4)

Three rules that live here and nowhere else: orientation collapses via
least/greatest, most-severe-wins between two orientations that disagree, and a
total order on upstream_key so the collapse is reproducible when they tie -- 29
of the 33 duplicates do tie, and an unordered DISTINCT ON is the defect the
re-measurement's own review found one layer down.

Refs #101"
```

---

### Task 6: `db/049` — `exact_ddi_pair`

**Files:**
- Modify: `db/049_drugcentral_ddi.sql` (append section 5)
- Test: `tests/test_drugcentral_read_path.py`

**Interfaces:**
- Consumes: Task 5's view; existing `drugref.moiety_contraindication`.
- Produces: view `drugref.exact_ddi_pair(moiety_lo, moiety_hi, subject_moiety, object_moiety, candidate_source, relationship, severity, severity_rank, upstream_severity_label, upstream_release, ingested_at)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_drugcentral_read_path.py`:

```python
@pytest.mark.usefixtures("conn")
def test_medrt_exact_pairs_reach_a_consumer_at_last(conn):
    """moiety_contraindication has had NO read view since db/014 -- 
    ddi_candidate_pair expands class_contraindication only. This is the first."""
    run, a, b = _run(conn, "MED-RT", "medrt_run", "2026.07.06"), \
                _moiety(conn, "warfarin"), _moiety(conn, "aspirin")
    conn.execute(
        "INSERT INTO drugref.moiety_contraindication "
        "(subject_moiety_uuid, object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_ChemClass', 'MED-RT', %s)", (a, b, run))
    row = conn.execute(
        "SELECT candidate_source, relationship, severity, subject_moiety = %s "
        "FROM drugref.exact_ddi_pair", (a,)).fetchone()
    assert row == ("MED-RT", "CI_ChemClass", None, True)


@pytest.mark.usefixtures("conn")
def test_a_medrt_pair_is_keyed_unordered_even_though_it_is_directional(conn):
    """moiety_lo/moiety_hi is the LOOKUP key -- 'am I about to co-prescribe these
    two?' is an unordered question. The direction is not lost; it moves to
    subject_moiety/object_moiety, which stay populated."""
    run = _run(conn, "MED-RT", "medrt_run", "2026.07.06")
    a, b = _moiety(conn, "warfarin"), _moiety(conn, "aspirin")
    conn.execute(
        "INSERT INTO drugref.moiety_contraindication "
        "(subject_moiety_uuid, object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_ChemClass', 'MED-RT', %s)", (a, b, run))
    lo, hi, subject, obj = conn.execute(
        "SELECT moiety_lo, moiety_hi, subject_moiety, object_moiety "
        "FROM drugref.exact_ddi_pair").fetchone()
    assert (lo, hi) == (min(a, b), max(a, b))
    assert (subject, obj) == (a, b)


@pytest.mark.usefixtures("conn")
def test_a_drugcentral_pair_asserts_no_direction(conn):
    """NULL states a fact about the source rather than hiding a missing value:
    DrugCentral publishes an unordered pair and names no subject."""
    run, a, b = _run(conn), _moiety(conn, "a"), _moiety(conn, "b")
    _assert_row(conn, run, "X", a, b, "A/B [VA]", band="Critical")
    row = conn.execute(
        "SELECT candidate_source, subject_moiety, object_moiety, relationship, "
        "       severity, severity_rank, upstream_severity_label "
        "FROM drugref.exact_ddi_pair").fetchone()
    assert row == ("DRUGCENTRAL", None, None, None, "contraindicated", 1, "Critical")


@pytest.mark.usefixtures("conn")
def test_both_authorities_appear_for_one_pair_rather_than_one_hiding_the_other(conn):
    """Fewer rows is the harm direction for a contraindication, so this is a
    UNION ALL and a consumer sees both authorities. Which one wins is a curated
    question (issues 97 and 106), deliberately not answered here."""
    a, b = _moiety(conn, "warfarin"), _moiety(conn, "aspirin")
    medrt = _run(conn, "MED-RT", "medrt_run", "2026.07.06")
    conn.execute(
        "INSERT INTO drugref.moiety_contraindication "
        "(subject_moiety_uuid, object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_ChemClass', 'MED-RT', %s)", (a, b, medrt))
    _assert_row(conn, _run(conn), "X", a, b, "A/B [VA]", band="Critical")
    sources = conn.execute(
        "SELECT candidate_source FROM drugref.exact_ddi_pair "
        "ORDER BY candidate_source").fetchall()
    assert sources == [("DRUGCENTRAL",), ("MED-RT",)]


@pytest.mark.usefixtures("conn")
def test_ddi_candidate_pair_is_untouched_by_this_migration(conn):
    """db/034 measured an arm added to that view costing 3.6x with the new grain
    EMPTY -- a structural cost paid by every existing consumer. This slice is
    additive, and this test is what keeps it that way."""
    (definition,) = conn.execute(
        "SELECT pg_get_viewdef('drugref.ddi_candidate_pair'::regclass)").fetchone()
    assert "drugcentral" not in definition.lower()
    assert "exact_ddi_pair" not in definition
```

- [ ] **Step 2: Run them and watch them fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_read_path.py -v`
Expected: the five new tests FAIL with `relation "drugref.exact_ddi_pair" does not exist` (the last one passes already, and it is a regression guard, so that is correct).

- [ ] **Step 3: Append section 5 to the migration**

```sql
-- ============================================================================
-- 5. exact_ddi_pair -- the read path exact pairs have never had
-- ============================================================================
-- drugref has held EXACT drug-drug pairs since db/014 and no view has ever
-- returned them: ddi_candidate_pair expands class_contraindication only, and
-- nothing else reads moiety_contraindication at all. A second source of exact
-- pairs makes that hole load-bearing, so this view closes it.
--
-- WHY NOT AN ARM ON ddi_candidate_pair, which is the shape db/033 chose for the
-- two grains: db/034 then MEASURED that arm costing 3.6x with the new grain
-- EMPTY -- a structural cost paid by every existing consumer on every query, for
-- content most of them do not have. And that view's columns are
-- class-expansion-shaped (via_class, member_class, is_direct), all meaningless at
-- moiety grain, so unioning would mean 7,501 rows of NULL in three columns. This
-- view is ADDITIVE: no existing query changes.
--
-- UNION ALL, not UNION: fewer rows is the harm direction for a contraindication,
-- so a pair asserted by two authorities appears twice rather than being folded to
-- whichever one sorted first. Which authority a consumer should believe is issues
-- 97/106's question and is deliberately not answered here.
CREATE OR REPLACE VIEW drugref.exact_ddi_pair AS
-- Arm 1: MED-RT's CI_ChemClass moiety arm (db/014). DIRECTIONAL -- MED-RT states
-- which drug the assertion is ABOUT -- so subject/object stay populated, while
-- moiety_lo/moiety_hi give the unordered LOOKUP key both arms share. It asserts
-- no severity, hence the NULLs.
SELECT least(mc.subject_moiety_uuid, mc.object_moiety_uuid)    AS moiety_lo,
       greatest(mc.subject_moiety_uuid, mc.object_moiety_uuid) AS moiety_hi,
       mc.subject_moiety_uuid  AS subject_moiety,
       mc.object_moiety_uuid   AS object_moiety,
       mc.source               AS candidate_source,
       mc.relationship,
       NULL::text              AS severity,
       NULL::smallint          AS severity_rank,
       NULL::text              AS upstream_severity_label,
       r.upstream_release,
       r.finished_at           AS ingested_at
FROM   drugref.moiety_contraindication mc
JOIN   drugref.ingest_run r ON r.ingest_run_id = mc.ingest_run
UNION ALL
-- Arm 2: DrugCentral's graded unordered pairs. It names no subject, so those two
-- columns are NULL -- a fact about the source, not a missing value. It names no
-- axis either: `relationship` is MED-RT's typed predicate vocabulary and VA's
-- assertion is simply "these two interact".
SELECT p.moiety_lo,
       p.moiety_hi,
       NULL::uuid              AS subject_moiety,
       NULL::uuid              AS object_moiety,
       p.candidate_source,
       NULL::text              AS relationship,
       p.severity,
       p.severity_rank,
       p.upstream_severity_label,
       p.upstream_release,
       p.ingested_at
FROM   drugref.drugcentral_ddi_pair p;

COMMENT ON VIEW drugref.exact_ddi_pair IS
    'Every EXACT drug-drug pair some upstream authority asserts, whatever its '
    'grain -- the read path moiety_contraindication has lacked since db/014. '
    'KEYED UNORDERED (moiety_lo, moiety_hi), because "am I about to co-prescribe '
    'these two?" is an unordered question; a source that DOES assert a direction '
    'keeps it in subject_moiety/object_moiety. CANDIDATE TIER, and DELIBERATELY '
    'NOT A SUPERSET OF ddi_candidate_pair: that view expands CLASS rules and this '
    'one does not, so a consumer wanting everything reads both. severity is NULL '
    'wherever the authority states none.';
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_read_path.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add db/049_drugcentral_ddi.sql tests/test_drugcentral_read_path.py
git commit -m "drugcentral: a read path for exact pairs (db/049 section 5)

exact_ddi_pair unions DrugCentral's graded unordered pairs with MED-RT's
CI_ChemClass moiety arm, which has had no consumer view since db/014.
ddi_candidate_pair is untouched and a test keeps it that way: db/034 measured
an arm on it costing 3.6x with the new grain empty.

Refs #101"
```

---

### Task 7: `db/049` — gap kind 17

**Files:**
- Modify: `db/049_drugcentral_ddi.sql` (append section 6)
- Modify: `src/drugref/questions.py` (`_GAP_SOURCES`)
- Test: `tests/test_drugcentral_gap.py`

**Interfaces:**
- Consumes: Task 4's assertion table.
- Produces: view `drugref.gap_unresolved_ddi_endpoint(source, endpoint_name, row_count, upstream_release)`; gap kind `'unresolved_ddi_endpoint'` registered in `questions._GAP_SOURCES` with `key_sql = "'DRUGCENTRAL:ENDPOINT:' || endpoint_name"`.

- [ ] **Step 1: Read the live gap-kind CHECK — do not retype it**

```bash
psql "host=localhost port=5532 dbname=drugref_test user=postgres" -Atc \
  "SELECT pg_get_constraintdef(oid) FROM pg_constraint
   WHERE conname = 'open_question_gap_kind';"
```

db/039's own comment records why: it expected fifteen values and the catalog held sixteen, because db/035 had landed in between.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_drugcentral_gap.py`:

```python
# tests/test_drugcentral_gap.py
"""Gap kind 17: the endpoint names DrugCentral keys and drugref does not.

Measured 2026-08-23: 37 rows over 10 folded names, ALL on route 'unresolved' --
DrugCentral holds a structural key and drugref does not. That matters for
whether the gate may ask at all: db/012's rule is that the review gate must only
ask what an answer COULD change, and these are registry-coverage work
(phytomenadione is the INN for phytonadione, atracurium for the besylate).
"""
import psycopg
import pytest

from drugref import ids, questions


def _run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def _unresolved(conn, run, key, name, other="warfarin"):
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', %s, %s, %s, 'X/Y [VA]', 'Critical', "
        "        'unresolved', 'unresolved')", (run, key, name, other))


@pytest.mark.usefixtures("conn")
def test_one_question_per_folded_name_not_per_row(conn):
    """A curator resolves a NAME. 37 rows over 10 names is 10 questions."""
    run = _run(conn)
    _unresolved(conn, run, "a", "Phytomenadione")
    _unresolved(conn, run, "b", "phytomenadione ")
    _unresolved(conn, run, "c", "atracurium")
    rows = conn.execute(
        "SELECT endpoint_name, row_count FROM drugref.gap_unresolved_ddi_endpoint "
        "ORDER BY endpoint_name").fetchall()
    assert rows == [("atracurium", 1), ("phytomenadione", 2)]


@pytest.mark.usefixtures("conn")
def test_a_resolved_endpoint_raises_no_question(conn):
    run = _run(conn)
    moiety = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name) "
        "VALUES (gen_random_uuid(), 'warfarin') RETURNING moiety_uuid").fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, moiety_1_uuid, route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', 'a', 'warfarin', 'vitamin e', 'W/V [VA]', "
        "        'Critical', %s, 'display_name', 'unresolved')", (run, moiety))
    rows = conn.execute(
        "SELECT endpoint_name FROM drugref.gap_unresolved_ddi_endpoint").fetchall()
    assert rows == [("vitamin e",)]


@pytest.mark.usefixtures("conn")
def test_the_gap_kind_is_admitted_and_registered(conn):
    """The CHECK, _GAP_SOURCES and the view are a TRIO: a kind registered with no
    view raises, and a view with no registration is a detector nobody reads --
    issues 74, 66 and 76, three times over."""
    assert "unresolved_ddi_endpoint" in questions._GAP_SOURCES
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'open_question_gap_kind'").fetchone()
    assert "unresolved_ddi_endpoint" in definition


@pytest.mark.usefixtures("conn")
def test_the_question_is_minted_with_a_folded_immortal_key(conn):
    """question_uuid is immortal and externally cited, so two spellings of one
    endpoint must not mint two questions."""
    run = _run(conn)
    _unresolved(conn, run, "a", "Phytomenadione")
    questions.register_from_gaps(conn, run)
    rows = conn.execute(
        "SELECT gap_key, question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ddi_endpoint'").fetchall()
    assert len(rows) == 1
    gap_key, question_uuid = rows[0]
    assert gap_key == "DRUGCENTRAL:ENDPOINT:phytomenadione"
    assert question_uuid == ids.mint_question_uuid(
        "unresolved_ddi_endpoint", "DRUGCENTRAL:ENDPOINT:phytomenadione")
```

Before running, confirm `ids.mint_question_uuid`'s exact name and argument order:
`grep -n "def mint_question_uuid" -A 6 src/drugref/ids.py`. Use whatever it actually is.

- [ ] **Step 3: Run them and watch them fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_gap.py -v`
Expected: FAIL — `relation "drugref.gap_unresolved_ddi_endpoint" does not exist`.

- [ ] **Step 4: Append section 6 to the migration**

```sql
-- ============================================================================
-- 6. gap_unresolved_ddi_endpoint, and the seventeenth question kind
-- ============================================================================
-- NO TABLE OF ITS OWN, unlike db/031's ingest_unresolved_onc_endpoint. That table
-- was needed because _GAP_SOURCES derives every kind FROM A VIEW and an ONC
-- endpoint resolving to nothing was in no table at all. Here the assertion table
-- already holds every row, resolved or not, so a view over it is the whole job.
--
-- GRAIN: one folded endpoint NAME, not one row. A curator resolves a name; 37
-- rows over 10 names is 10 questions. The fold is lower(trim(...)), which is
-- drugcentral_resolve.fold_name's rule -- restated here because question_uuid is
-- IMMORTAL and externally cited, so two spellings of one endpoint must never mint
-- two questions that can then be answered differently.
--
-- FILTERED ON A NULL uuid, NEVER ON THE ROUTE VOCABULARY. The routes are
-- descriptive; filtering on them would put that list in a second place, which is
-- the defect db/006 exists to remove -- and this view would then need widening
-- every time a route is added.
CREATE OR REPLACE VIEW drugref.gap_unresolved_ddi_endpoint AS
SELECT e.source,
       e.endpoint_name,
       count(*)                AS row_count,
       max(r.upstream_release) AS upstream_release
FROM  (SELECT a.source, a.ingest_run,
              lower(btrim(a.endpoint_1_name)) AS endpoint_name
         FROM drugref.drugcentral_ddi_assertion a
        WHERE a.moiety_1_uuid IS NULL
        UNION ALL
       SELECT a.source, a.ingest_run,
              lower(btrim(a.endpoint_2_name)) AS endpoint_name
         FROM drugref.drugcentral_ddi_assertion a
        WHERE a.moiety_2_uuid IS NULL) e
JOIN  drugref.ingest_run r ON r.ingest_run_id = e.ingest_run
      -- A blank endpoint is not a question anyone can answer, and the resolver
      -- already refuses to look one up (an empty structural key would otherwise
      -- collapse every keyless substance onto one moiety).
WHERE e.endpoint_name <> ''
GROUP BY e.source, e.endpoint_name;

COMMENT ON VIEW drugref.gap_unresolved_ddi_endpoint IS
    'Endpoint names DrugCentral resolves to a structure and drugref cannot key. '
    'ONE ROW PER FOLDED NAME, because a curator resolves a name rather than a '
    'row. Measured 2026-08-23: 37 rows over 10 names, every one on route '
    '''unresolved'' -- DrugCentral holds an InChIKey or a CAS number that no live '
    'identity_claim carries. They are REGISTRY-COVERAGE work, not a synonym list: '
    '''phytomenadione'' is the INN for phytonadione and ''atracurium'' the base of '
    'the besylate drugref already holds, so an answer could change something, '
    'which is db/012''s test for whether the review gate may ask at all. The '
    'question retires by itself when the claim lands.';

-- The seventeenth question kind. Guarded on the constraint's TEXT rather than its
-- name, so a replay against an already-widened database skips the drop/add
-- entirely instead of rescanning -- the idiom db/016, db/019, db/022, db/028,
-- db/029, db/031 and db/039 all reuse.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%unresolved_ddi_endpoint%') THEN
        ALTER TABLE drugref.open_question DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question ADD CONSTRAINT open_question_gap_kind
            CHECK (gap_kind IN (
                -- COPIED VERBATIM from the live catalog, then extended by one.
                -- Retyping this list from memory would silently drop a kind and
                -- orphan every question already minted under it -- and db/039
                -- found the live catalog holding SIXTEEN where its own plan
                -- expected fifteen, because db/035 had landed in between.
                <<< paste the live list here, then add the line below >>>
                'unresolved_ddi_endpoint'));
    END IF;
END $$;
```

**`<<< paste the live list here >>>` is a real instruction, not a placeholder in the finished file:** run Step 1's `psql` command and paste exactly what it prints, then append `'unresolved_ddi_endpoint'`. The migration must not contain that marker when you commit.

- [ ] **Step 5: Register the gap kind in `questions.py`**

Add to `_GAP_SOURCES` in `src/drugref/questions.py`:

```python
    # Issue 101 (db/049). The endpoint names DrugCentral keys to a structure and
    # drugref does not. NOT a synonym-bridge worklist: the re-measurement showed a
    # display_name -> inchikey -> cas cascade takes resolution from 857 of 924
    # names to 914, so what is left is registry COVERAGE -- a moiety or an
    # identity_claim drugref does not yet hold. The question retires by itself
    # when the claim lands.
    "unresolved_ddi_endpoint": {
        "view": "gap_unresolved_ddi_endpoint",
        # FOLDED in the view, so the key is folded here too. question_uuid is
        # immortal, so 'Phytomenadione' and 'phytomenadione' must be one question.
        "key_sql": "'DRUGCENTRAL:ENDPOINT:' || endpoint_name",
        "text_sql": (
            "'Which moiety does the interaction endpoint ' || endpoint_name || "
            "' denote? DrugCentral resolves it to a structure with an InChIKey or "
            "a CAS number, and no live identity_claim in drugref carries either, "
            "so ' || row_count || ' interaction row(s) cannot yield a pair.'"),
    },
```

- [ ] **Step 6: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_gap.py -v`
Expected: PASS, 4 tests.

Then the whole suite, because `register_from_gaps` runs in five orchestrators and a new kind touches all of them:
Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q 2>&1 | tail -3`
Expected: no failures.

- [ ] **Step 7: Commit**

```bash
git add db/049_drugcentral_ddi.sql src/drugref/questions.py tests/test_drugcentral_gap.py
git commit -m "drugcentral: publish the unresolvable endpoints (db/049 section 6)

Gap kind 17 over the assertion table -- no table of its own, because unlike
db/031's ONC endpoints these rows are already stored. One question per FOLDED
name (37 rows, 10 names), filtered on a NULL uuid rather than on the route
vocabulary so that list keeps one home.

Refs #101"
```

---

### Task 8: The writer

**Files:**
- Modify: `src/drugref/interactions.py`
- Test: `tests/test_drugcentral_writer.py`

**Interfaces:**
- Consumes: Task 4's table.
- Produces: `interactions.DRUGCENTRAL_TABLES = ("drugcentral_ddi_assertion",)`; `interactions.clear_source_drugcentral(conn, source)`; `interactions.add_drugcentral_assertion(conn, *, ingest_run_id, source, upstream_key, endpoint_1_name, endpoint_2_name, upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid, route_1, route_2) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_drugcentral_writer.py`:

```python
# tests/test_drugcentral_writer.py
"""interactions.py's DrugCentral half: one insert, one per-source clear.

The clear is what makes "rebuildable projection" true. An assertion retracted
upstream must be able to DISAPPEAR, which an insert-only merge can never express.
"""
import pytest

from drugref import interactions


def _run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def _moiety(conn, name):
    return conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name) "
        "VALUES (gen_random_uuid(), %s) RETURNING moiety_uuid", (name,)).fetchone()[0]


def _write(conn, run, key, one=None, two=None, route="unresolved"):
    return interactions.add_drugcentral_assertion(
        conn, ingest_run_id=run, source="DRUGCENTRAL", upstream_key=key,
        endpoint_1_name="one", endpoint_2_name="two",
        upstream_label="ONE/TWO [VA Drug Interaction]", severity_label="Critical",
        moiety_1_uuid=one, moiety_2_uuid=two,
        route_1="display_name" if one else route,
        route_2="display_name" if two else route)


@pytest.mark.usefixtures("conn")
def test_a_row_is_written_once(conn):
    run, a, b = _run(conn), _moiety(conn, "a"), _moiety(conn, "b")
    assert _write(conn, run, "C56.1", a, b) is True
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (1,)


@pytest.mark.usefixtures("conn")
def test_repeating_one_upstream_key_is_harmless(conn):
    """ON CONFLICT DO NOTHING, matching every sibling writer: a dump that repeats
    an assertion must not abort an ingest halfway through."""
    run, a, b = _run(conn), _moiety(conn, "a"), _moiety(conn, "b")
    assert _write(conn, run, "C56.1", a, b) is True
    assert _write(conn, run, "C56.1", a, b) is False
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (1,)


@pytest.mark.usefixtures("conn")
def test_an_unresolved_row_is_written_with_null_uuids(conn):
    run = _run(conn)
    assert _write(conn, run, "C56.2") is True
    row = conn.execute(
        "SELECT moiety_1_uuid, moiety_2_uuid, route_1 "
        "FROM drugref.drugcentral_ddi_assertion").fetchone()
    assert row == (None, None, "unresolved")


@pytest.mark.usefixtures("conn")
def test_the_clear_is_per_source_and_covers_the_whole_projection(conn):
    run, a, b = _run(conn), _moiety(conn, "a"), _moiety(conn, "b")
    _write(conn, run, "C56.1", a, b)
    interactions.clear_source_drugcentral(conn, "DRUGCENTRAL")
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (0,)


def test_the_projection_tuple_is_restated_independently():
    """Pinned by name, as every sibling table tuple is: dropping a table from one
    of these leaves a projection that grows a little on every ingest."""
    assert interactions.DRUGCENTRAL_TABLES == ("drugcentral_ddi_assertion",)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_writer.py -v`
Expected: FAIL — `module 'drugref.interactions' has no attribute 'add_drugcentral_assertion'`.

- [ ] **Step 3: Add the writer to `interactions.py`**

Append (keeping the file under ~500 lines — it is 383 before this):

```python
# ---- issue 101: DrugCentral's unordered graded pairs (db/049) ------------------
#
# The projection this source owns, cleared per-source on every re-ingest. Named as
# a tuple to match db.clear_source_tables's signature and every sibling constant
# (MESH_CONTRAINDICATION_TABLES above, onchigh_run's UNRESOLVED_ENDPOINT_TABLES,
# fda_cyp_run's FDA_CYP_TABLES). ONE table, because unlike db/031's ONC endpoints
# the unresolvable rows live in the assertion table itself.
DRUGCENTRAL_TABLES = ("drugcentral_ddi_assertion",)


def clear_source_drugcentral(conn: psycopg.Connection, source: str) -> None:
    """Drop every DrugCentral assertion contributed by `source`.

    Same rebuildable-projection discipline as clear_source_contraindications: an
    assertion retracted upstream has to be able to DISAPPEAR, and an insert-only
    merge could never express that. It also clears the unresolved rows, for the
    reason classes.clear_source_unmatched_ingredients gives: an endpoint that
    starts resolving must LEAVE the worklist, or the worklist grows by its own
    length every ingest and never shrinks.
    """
    db.clear_source_tables(conn, DRUGCENTRAL_TABLES, source)


def add_drugcentral_assertion(conn: psycopg.Connection, *,
                              ingest_run_id: int,
                              source: str,
                              upstream_key: str,
                              endpoint_1_name: str,
                              endpoint_2_name: str,
                              upstream_label: str,
                              severity_label: str,
                              moiety_1_uuid: uuid.UUID | None,
                              moiety_2_uuid: uuid.UUID | None,
                              route_1: str,
                              route_2: str) -> bool:
    """Record one published DrugCentral interaction, resolved or not.

    KEYWORD-ONLY, and that is not style: this function takes four strings and two
    optional UUIDs in two matched pairs, and a positional call that swapped
    endpoint_1 with endpoint_2 -- or route_1 with route_2 -- would insert cleanly
    and produce a wrong resolution route beside a right moiety. The endpoints are
    UNORDERED, so nothing downstream could ever detect the swap.

    NOT DIRECTIONAL. `endpoint_1`/`endpoint_2` are the dump's own column order and
    carry no clinical meaning: measured 2026-08-23, 33 pairs are published in both
    orders and no ordered pair repeats. drugcentral_ddi_pair canonicalises.

    Returns True if a new row was inserted. ON CONFLICT DO NOTHING keeps a dump
    that repeats one assertion harmless, as every sibling writer does.
    """
    cur = conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid, "
        " route_1, route_2) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (ingest_run_id, source, upstream_key, endpoint_1_name, endpoint_2_name,
         upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid,
         route_1, route_2))
    return cur.rowcount == 1
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_drugcentral_writer.py -v`
Expected: PASS, 5 tests.

Check the file size: `wc -l src/drugref/interactions.py` — if it now exceeds ~500, stop and split on the moiety/class seam before continuing (CLAUDE.md rule 4).

- [ ] **Step 5: Commit**

```bash
git add src/drugref/interactions.py tests/test_drugcentral_writer.py
git commit -m "drugcentral: the assertion writer and its per-source clear

Keyword-only: two matched string pairs and two optional UUIDs, where a
positional swap of endpoint_1/endpoint_2 or route_1/route_2 would insert
cleanly and be undetectable, because the endpoints are unordered.

Refs #101"
```

---

### Task 9: The pure parser

**Files:**
- Create: `src/drugref/ingest/drugcentral.py`
- Test: `tests/test_drugcentral_parser.py`

**Interfaces:**
- Consumes: `drugref.ingest.drugcentral_dump.iter_copy_rows`, `drugref.ingest.drugcentral_resolve.{Registry, EndpointIndex, resolve_endpoint, Resolution}`.
- Produces:
  - `drugcentral.SOURCE = "DRUGCENTRAL"`, `drugcentral.WRITER = "drugcentral_run"`
  - `drugcentral.BUNDLEABLE_REF_IDS: frozenset[str]`
  - `drugcentral.ReferenceIdentityError(RuntimeError)`
  - `drugcentral.DumpTables` — frozen dataclass with `ddi: tuple[dict, ...]`, `reference: dict[str, dict]`, `structures: tuple[dict, ...]`, `synonyms: tuple[dict, ...]`
  - `drugcentral.read_tables(lines: Iterable[str]) -> DumpTables`
  - `drugcentral.check_reference_identity(reference: Mapping[str, Mapping[str, str | None]]) -> None`
  - `drugcentral.AssertionRecord` — frozen kw-only dataclass with the eleven writer fields minus `ingest_run_id`/`source`
  - `drugcentral.bundleable_rows(ddi: Iterable[Mapping[str, str | None]]) -> Iterator[Mapping[str, str | None]]`
  - `drugcentral.resolve_row(row, index, registry) -> AssertionRecord`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_drugcentral_parser.py`:

```python
# tests/test_drugcentral_parser.py
"""The pure half of the DrugCentral ingest: rule 6, and row -> record.

NO DATABASE. Every function here takes plain mappings, which is the architecture
invariant (parsers are pure; orchestrators own the transaction) and is also what
lets the rule-6 guard be tested by executing it rather than by reading it.
"""
import pytest

from drugref.ingest import drugcentral
from drugref.ingest.drugcentral_resolve import EndpointIndex, Registry

VHA = {
    "authors": "Veterans Health Administration",
    "title": ("Veterans Health Administration (VHA) National Drug File - "
              "Reference Terminology (NDF-RT)"),
}


def test_only_reference_2_is_bundleable():
    """CLAUDE.md rule 6. 1 is Stockley's (a copyrighted book) and 3 is Lexicomp
    (a commercial compendium); DrugCentral's own CC BY-SA on the compilation is
    not evidence of a right to relicense either."""
    rows = [{"id": "1", "ddi_ref_id": "1"}, {"id": "2", "ddi_ref_id": "2"},
            {"id": "3", "ddi_ref_id": "3"}]
    assert [row["id"] for row in drugcentral.bundleable_rows(rows)] == ["2"]


def test_the_bundleable_set_has_one_home():
    assert drugcentral.BUNDLEABLE_REF_IDS == frozenset({"2"})


def test_a_matching_reference_row_is_accepted():
    drugcentral.check_reference_identity({"2": VHA})


def test_a_renumbered_reference_aborts_rather_than_bundling_it():
    """`2` is a SURROGATE key in a dump published once. A re-publication is free
    to renumber its references, and a silent renumber would bundle Lexicomp under
    a constant that still reads 2. Licensing is a blocker, not a cleanup item."""
    lexicomp = {"authors": "Wolters Kluwer Health", "title": "Lexicomp Online"}
    with pytest.raises(drugcentral.ReferenceIdentityError) as caught:
        drugcentral.check_reference_identity({"2": lexicomp})
    assert "Lexicomp Online" in str(caught.value)
    assert "National Drug File" in str(caught.value)


def test_a_missing_reference_row_aborts():
    """Absence is not agreement. A dump whose reference table lost the row cannot
    be shown to be the one rule 6 was determined against."""
    with pytest.raises(drugcentral.ReferenceIdentityError):
        drugcentral.check_reference_identity({})


def test_a_row_resolves_both_endpoints_to_a_record():
    index = EndpointIndex(names={}, structural_keys={})
    registry = Registry(display_name={"warfarin": "u-1", "aspirin": "u-2"},
                        inchikey={}, cas={})
    record = drugcentral.resolve_row(
        {"source_id": "C56.1", "drug_class1": "Warfarin", "drug_class2": "aspirin",
         "description": "WARFARIN/ASPIRIN [VA Drug Interaction]",
         "ddi_risk": "Critical"},
        index, registry)
    assert record.upstream_key == "C56.1"
    assert record.endpoint_1_name == "Warfarin"      # VERBATIM, not folded
    assert record.moiety_1_uuid == "u-1"
    assert record.route_1 == "display_name"
    assert record.moiety_2_uuid == "u-2"
    assert record.severity_label == "Critical"


def test_an_unresolvable_endpoint_becomes_a_record_with_a_null_uuid():
    """Not a drop and not an error: a worklist entry, per db/039's precedent."""
    index = EndpointIndex(names={}, structural_keys={})
    registry = Registry(display_name={"warfarin": "u-1"}, inchikey={}, cas={})
    record = drugcentral.resolve_row(
        {"source_id": "C56.2", "drug_class1": "warfarin",
         "drug_class2": "phytomenadione",
         "description": "WARFARIN/PHYTONADIONE [VA Drug Interaction]",
         "ddi_risk": "Critical"},
        index, registry)
    assert record.moiety_2_uuid is None
    assert record.route_2 == "not_a_substance"


def test_read_tables_streams_the_four_tables_it_needs():
    dump = [
        "COPY public.reference (id, authors, title) FROM stdin;",
        "2\\tVeterans Health Administration\\tNDF-RT",
        "\\\\.",
        "COPY public.ddi (id, drug_class1, drug_class2, ddi_ref_id) FROM stdin;",
        "1\\twarfarin\\taspirin\\t2",
        "\\\\.",
        "COPY public.ignored (x) FROM stdin;",
        "9",
        "\\\\.",
    ]
    tables = drugcentral.read_tables(dump)
    assert tables.reference["2"]["authors"] == "Veterans Health Administration"
    assert len(tables.ddi) == 1
    assert tables.ddi[0]["drug_class1"] == "warfarin"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_drugcentral_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.ingest.drugcentral'`.

- [ ] **Step 3: Write `src/drugref/ingest/drugcentral.py`**

```python
# src/drugref/ingest/drugcentral.py
"""The PURE half of the DrugCentral `ddi` ingest: rule 6, and row -> record.

No database access of any kind, per the architecture invariant. Everything here
takes plain mappings, which is also what lets the rule-6 guard be tested by
EXECUTING it rather than by reading a comment that claims it exists.

WHAT THIS MODULE REFUSES TO DO:

* It admits ONE reference. `ddi_ref_id = 2` is the VHA's NDF-RT, a US federal
  work; `1` is Stockley's Drug Interactions (a copyrighted book) and `3` is
  Lexicomp Online (a commercial compendium). DrugCentral publishes the
  compilation under CC BY-SA 4.0, WHICH IS NOT EVIDENCE OF A RIGHT TO RELICENSE
  A THIRD-PARTY COMPENDIUM INSIDE IT.
* It does not trust the number `2` on its own. See check_reference_identity.
* It bridges no name. The cascade in drugcentral_resolve keys on STRUCTURE --
  display_name, then InChIKey, then CAS -- which took resolution from 857 of 924
  endpoint names to 914 with no hand-maintained synonym list at all.
"""
import dataclasses
from collections.abc import Iterable, Iterator, Mapping

from drugref.ingest.drugcentral_dump import iter_copy_rows
from drugref.ingest.drugcentral_resolve import (
    EndpointIndex, Registry, resolve_endpoint,
)

SOURCE = "DRUGCENTRAL"
#: WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
#: (db/025). Declared in provenance.WRITERS and db/049's CHECK -- a pair.
WRITER = "drugcentral_run"

#: THE ONE HOME FOR THE RULE-6 DETERMINATION. The re-measurement's own code
#: review found a SECOND hard-coded `ref_id == "2"` in its renderer, unconnected
#: to the set that filtered the rows, so this is not a hypothetical failure.
BUNDLEABLE_REF_IDS = frozenset({"2"})

#: What the dump must SAY reference 2 is, read from `reference` on 2026-08-23.
#: Compared before a single row is admitted -- see check_reference_identity.
EXPECTED_REFERENCE = {
    "2": ("Veterans Health Administration",
          "Veterans Health Administration (VHA) National Drug File - "
          "Reference Terminology (NDF-RT)"),
}

#: The four tables one pass over the dump decodes. `structures` and `synonyms`
#: are DrugCentral's own name tables and are what make the cascade possible
#: without drugref learning any spelling.
WANTED_TABLES = frozenset({"ddi", "reference", "structures", "synonyms"})


class ReferenceIdentityError(RuntimeError):
    """The dump's `reference` row does not match the one rule 6 was decided on."""


@dataclasses.dataclass(frozen=True)
class DumpTables:
    """The four tables one streaming pass collects. All four are small.

    `reference` is keyed by id because that is how it is looked up; the other
    three are read in order and stay tuples.
    """

    ddi: tuple[Mapping[str, str | None], ...]
    reference: Mapping[str, Mapping[str, str | None]]
    structures: tuple[Mapping[str, str | None], ...]
    synonyms: tuple[Mapping[str, str | None], ...]


@dataclasses.dataclass(frozen=True, kw_only=True)
class AssertionRecord:
    """One published row, with both endpoints resolved or explained.

    KEYWORD-ONLY for the reason interactions.add_drugcentral_assertion is: the
    two endpoints are UNORDERED, so a positional swap would be undetectable
    downstream.
    """

    upstream_key: str
    endpoint_1_name: str
    endpoint_2_name: str
    upstream_label: str
    severity_label: str
    moiety_1_uuid: str | None
    moiety_2_uuid: str | None
    route_1: str
    route_2: str

    @property
    def resolved(self) -> bool:
        """True when BOTH endpoints reached a moiety -- the pair-yielding case."""
        return self.moiety_1_uuid is not None and self.moiety_2_uuid is not None

    @property
    def self_pair(self) -> bool:
        """True when both endpoints resolved to ONE moiety.

        Its own bucket rather than folded into `resolved`, because it is neither
        an unresolvable row nor a pair: two endpoint names legitimately folding
        onto one moiety asserts nothing about an interaction between two drugs.
        Measured 2026-08-23: 0 of 7,571, and counting it is what would make that
        stop being true visibly.
        """
        return self.resolved and self.moiety_1_uuid == self.moiety_2_uuid


def read_tables(lines: Iterable[str]) -> DumpTables:
    """Collect the four wanted tables in ONE streaming pass over the dump.

    The dump is ~1.4 GB gzipped and ~5 GB of text; `iter_copy_rows` skips a block
    for an unwanted table without decoding a field, which is what makes one pass
    for four tables cheap. All four fit in memory comfortably -- `ddi` is 7,621
    rows, `reference` 1,195, `structures` 4,995 and `synonyms` 23,369.
    """
    ddi: list[Mapping[str, str | None]] = []
    reference: dict[str, Mapping[str, str | None]] = {}
    structures: list[Mapping[str, str | None]] = []
    synonyms: list[Mapping[str, str | None]] = []

    for table, row in iter_copy_rows(lines, WANTED_TABLES):
        if table == "ddi":
            ddi.append(row)
        elif table == "reference":
            row_id = row.get("id")
            if row_id:
                reference[row_id] = row
        elif table == "structures":
            structures.append(row)
        elif table == "synonyms":
            synonyms.append(row)

    return DumpTables(ddi=tuple(ddi), reference=reference,
                      structures=tuple(structures), synonyms=tuple(synonyms))


def check_reference_identity(
        reference: Mapping[str, Mapping[str, str | None]]) -> None:
    """Refuse the dump unless every bundleable id IS the reference rule 6 cleared.

    WHY THE CONSTANT IS NOT ENOUGH. `2` is a surrogate key in a table of 1,195
    rows, in a database that has been published exactly once. A re-publication is
    free to renumber its references, and a silent renumber would bundle Lexicomp
    under a constant that still reads `2` -- with nothing anywhere raising. This
    is the one place in the slice where being wrong is unrecoverable after
    distribution, so the check is an abort rather than a warning or a skip.

    Raises:
        ReferenceIdentityError: with BOTH strings printed, so the operator can see
            what the dump claims beside what drugref expected.
    """
    for ref_id in sorted(BUNDLEABLE_REF_IDS):
        expected_authors, expected_title = EXPECTED_REFERENCE[ref_id]
        row = reference.get(ref_id)
        if row is None:
            raise ReferenceIdentityError(
                f"the dump's `reference` table has no row {ref_id!r}, so it "
                f"cannot be shown to be the release rule 6 was determined "
                f"against (expected {expected_title!r})")
        authors, title = (row.get("authors") or ""), (row.get("title") or "")
        if authors.strip() != expected_authors or title.strip() != expected_title:
            raise ReferenceIdentityError(
                f"reference {ref_id!r} in this dump is "
                f"{authors!r} / {title!r}, but rule 6 admits only "
                f"{expected_authors!r} / {expected_title!r}. Refusing to ingest: "
                f"a renumbered reference would bundle a source drugref may not "
                f"redistribute.")


def bundleable_rows(
        ddi: Iterable[Mapping[str, str | None]],
) -> Iterator[Mapping[str, str | None]]:
    """Yield only the rows CLAUDE.md rule 6 permits drugref to bundle.

    Excluding 37 Lexicomp rows and 13 Stockley's rows costs nothing measurable:
    those same 50 rows are the ones whose endpoints are class-named and do not
    resolve anyway (648 unresolvable rows over the whole table against 598 over
    this subset -- a difference of exactly 50).
    """
    for row in ddi:
        if (row.get("ddi_ref_id") or "") in BUNDLEABLE_REF_IDS:
            yield row


def resolve_row(row: Mapping[str, str | None],
                index: EndpointIndex,
                registry: Registry) -> AssertionRecord:
    """Turn one published `ddi` row into a record, resolved or explained.

    The endpoint NAMES are carried through VERBATIM. Folding is the resolver's
    rule (`fold_name`) and belongs in one place; storing the folded form here
    would lose the spelling a later release has to be diffed against.

    `source_id` rather than `id` is the key: it is the VA's own identifier for
    the interaction record, and all 7,571 bundleable rows carry a distinct one.
    """
    endpoint_1 = row.get("drug_class1") or ""
    endpoint_2 = row.get("drug_class2") or ""
    first = resolve_endpoint(endpoint_1, index, registry)
    second = resolve_endpoint(endpoint_2, index, registry)
    return AssertionRecord(
        upstream_key=row.get("source_id") or "",
        endpoint_1_name=endpoint_1,
        endpoint_2_name=endpoint_2,
        upstream_label=row.get("description") or "",
        severity_label=row.get("ddi_risk") or "",
        moiety_1_uuid=first.moiety_uuid,
        moiety_2_uuid=second.moiety_uuid,
        route_1=first.route,
        route_2=second.route,
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_drugcentral_parser.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ingest/drugcentral.py tests/test_drugcentral_parser.py
git commit -m "drugcentral: the pure parser, and a rule-6 guard that executes

BUNDLEABLE_REF_IDS has one home, and the constant alone is not trusted: \`2\` is
a surrogate key in a dump published once, so check_reference_identity reads the
dump's own reference row and aborts unless it IS the VHA NDF-RT. A silent
renumber would otherwise bundle Lexicomp under a constant that still reads 2.

Refs #101"
```

---

### Task 10: The committed fixture

**Files:**
- Create: `tests/fixtures/make_drugcentral_subset.py`
- Create: `tests/fixtures/drugcentral_ddi_subset.sql.gz`
- Test: `tests/test_drugcentral_fixture.py`

**Interfaces:**
- Consumes: Task 9's `read_tables`.
- Produces: a gzipped mini-dump the orchestrator test (Task 11) and the CLI test (Task 12) read.

- [ ] **Step 1: Write the generator**

Create `tests/fixtures/make_drugcentral_subset.py`:

```python
#!/usr/bin/env python3
"""Build tests/fixtures/drugcentral_ddi_subset.sql.gz from the real dump.

    uv run python tests/fixtures/make_drugcentral_subset.py \
        downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz \
        tests/fixtures/drugcentral_ddi_subset.sql.gz

WHAT IT KEEPS, and every choice is load-bearing for a test:

* `reference` rows 1, 2 AND 3 -- all three, so the rule-6 filter and
  check_reference_identity are both exercised against a dump that really does
  carry the excluded references.
* A handful of `ddi` rows per reference, including ONE PAIR PUBLISHED IN BOTH
  ORDERS with disagreeing bands, so the view's collapse has something to collapse.
* One endpoint resolvable only through `synonyms`, and one resolvable through
  neither, so the cascade and the gap view both have a case.
* The `structures` and `synonyms` rows those endpoints need, and nothing else.

WHAT IT REDACTS, and this is a LICENCE requirement rather than tidiness: the
`description` of every `ddi_ref_id` 1 and 3 row is replaced with the literal
string '[redacted: cites a reference CLAUDE.md rule 6 excludes]'. Those rows cite
a copyrighted book and a commercial compendium, and committing their text into an
AGPL repository is exactly what rule 6 forbids. The MED-RT fixture's endpoint
redaction is the precedent, and a test enforces it there and here.

The `ddi_ref_id = 2` rows are VHA NDF-RT content -- a US federal work -- and are
committed in full, at the fair-dealing scale tests/fixtures/pbs_items_subset.csv
already established for a handful of upstream rows.
"""
```

Write the body to stream the real dump through `drugcentral.read_tables`, select the rows described above, and emit a valid mini-dump: for each of the four tables, a `COPY public.<table> (<columns>) FROM stdin;` header, the tab-separated rows, and a `\.` terminator. Escape tabs, newlines and backslashes the way `pg_dump` does — `drugcentral_dump.decode_copy_field` is the exact inverse and is the specification to write against.

- [ ] **Step 2: Generate the fixture and inspect it by eye**

```bash
uv run python tests/fixtures/make_drugcentral_subset.py \
    downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz \
    tests/fixtures/drugcentral_ddi_subset.sql.gz
gunzip -c tests/fixtures/drugcentral_ddi_subset.sql.gz | head -40
```

Confirm by reading: three `reference` rows, a both-order pair, and every ref-1/ref-3 description redacted.

- [ ] **Step 3: Write the tests that pin those properties**

Create `tests/test_drugcentral_fixture.py`:

```python
# tests/test_drugcentral_fixture.py
"""The committed fixture's licence and coverage properties.

The redaction is a LICENCE requirement, not tidiness: ddi_ref_id 1 and 3 cite a
copyrighted book and a commercial compendium, and their description text may not
sit in an AGPL repository. tests/fixtures/medrt_subset.xml carries the same kind
of test for the same kind of reason.
"""
import gzip
import pathlib

from drugref.ingest import drugcentral

FIXTURE = pathlib.Path("tests/fixtures/drugcentral_ddi_subset.sql.gz")

REDACTED = "[redacted: cites a reference CLAUDE.md rule 6 excludes]"


def _tables():
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as handle:
        return drugcentral.read_tables(handle)


def test_the_excluded_references_are_present_so_the_filter_is_exercised():
    """A fixture holding only ref 2 would let the rule-6 filter be deleted and
    still pass every test -- the shape of issues 74, 66 and 76."""
    assert set(_tables().reference) >= {"1", "2", "3"}


def test_no_excluded_description_text_is_committed():
    for row in _tables().ddi:
        if row["ddi_ref_id"] != "2":
            assert row["description"] == REDACTED, (
                f"row {row['id']} cites reference {row['ddi_ref_id']} and its "
                f"description must be redacted before it is committed")


def test_the_fixture_carries_a_pair_published_in_both_orders():
    """The view's collapse rule needs a case, and 33 real pairs have one."""
    rows = [r for r in _tables().ddi if r["ddi_ref_id"] == "2"]
    pairs = {(r["drug_class1"].lower(), r["drug_class2"].lower()) for r in rows}
    assert any((b, a) in pairs for a, b in pairs), (
        "no endpoint pair appears in both orders; the collapse is untested")


def test_the_fixture_carries_an_unresolvable_endpoint():
    """gap_unresolved_ddi_endpoint needs a case too."""
    rows = [r for r in _tables().ddi if r["ddi_ref_id"] == "2"]
    names = {r["drug_class1"].lower() for r in rows} | \
            {r["drug_class2"].lower() for r in rows}
    structures = {(r["name"] or "").lower() for r in _tables().structures}
    assert names - structures, "every endpoint is a structures.name; nothing is a gap"
```

- [ ] **Step 4: Run them**

Run: `uv run pytest tests/test_drugcentral_fixture.py -v`
Expected: PASS, 4 tests. If the both-order or unresolvable assertions fail, adjust the generator's selection and regenerate — the fixture must carry those cases.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/make_drugcentral_subset.py tests/fixtures/drugcentral_ddi_subset.sql.gz tests/test_drugcentral_fixture.py
git commit -m "drugcentral: a committed fixture carrying all three references

All three, so the rule-6 filter and the reference guard are exercised against a
dump that really does carry the excluded ones -- a fixture holding only ref 2
would let the filter be deleted and still pass. The ref-1 and ref-3 description
text is REDACTED, which is a licence requirement: those rows cite a copyrighted
book and a commercial compendium.

Refs #101"
```

---

### Task 11: The orchestrator

**Files:**
- Create: `src/drugref/ingest/drugcentral_run.py`
- Test: `tests/test_drugcentral_run.py`

**Interfaces:**
- Consumes: Tasks 8–10; `provenance.open_run`/`finish_run`, `ingest.checksum.checksum`, `questions.register_from_gaps`.
- Produces: `drugcentral_run.DrugCentralSummary` (frozen dataclass: `rows_read`, `rows_excluded_by_reference`, `rows_bundleable`, `rows_resolved`, `rows_self_pair`, `rows_unresolved`, `pairs`, `duplicate_keys`) and `drugcentral_run.ingest_drugcentral(conn, *, dump_path, release) -> DrugCentralSummary`. Task 12's CLI calls it.

- [ ] **Step 1: Write the failing summary test first — it is pure**

Create `tests/test_drugcentral_run.py`:

```python
# tests/test_drugcentral_run.py
"""The orchestrator: it reconciles, and it is the only writer.

Issue 71's standing rule, re-learned by curate_onchigh and again by the
re-measurement's Measurement guard: a summary whose buckets do not sum is a
number that cannot be checked, and every row must land in exactly one of them.
"""
import gzip
import pathlib

import pytest

from drugref.ingest import drugcentral_run

FIXTURE = pathlib.Path("tests/fixtures/drugcentral_ddi_subset.sql.gz")


def test_the_summary_refuses_to_exist_unless_its_buckets_sum():
    with pytest.raises(ValueError, match="do not sum"):
        drugcentral_run.DrugCentralSummary(
            rows_read=10, rows_excluded_by_reference=2, rows_bundleable=8,
            rows_resolved=5, rows_self_pair=0, rows_unresolved=1,  # 6, not 8
            pairs=5, duplicate_keys=0)


def test_the_summary_accepts_buckets_that_sum():
    summary = drugcentral_run.DrugCentralSummary(
        rows_read=10, rows_excluded_by_reference=2, rows_bundleable=8,
        rows_resolved=7, rows_self_pair=0, rows_unresolved=1,
        pairs=7, duplicate_keys=0)
    assert summary.rows_bundleable == 8


def test_the_summary_refuses_a_read_count_that_excludes_more_than_it_read():
    with pytest.raises(ValueError, match="do not sum"):
        drugcentral_run.DrugCentralSummary(
            rows_read=10, rows_excluded_by_reference=3, rows_bundleable=8,
            rows_resolved=8, rows_self_pair=0, rows_unresolved=0,
            pairs=8, duplicate_keys=0)
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_drugcentral_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.ingest.drugcentral_run'`.

- [ ] **Step 3: Write the orchestrator**

Create `src/drugref/ingest/drugcentral_run.py`:

```python
# src/drugref/ingest/drugcentral_run.py
"""Orchestrate one DrugCentral ingest: read -> guard -> resolve -> clear -> write.

The ONLY writer of drugref's DrugCentral rows, per the architecture invariant:
parsers are pure, orchestrators own the transaction.

ORDER MATTERS, as for every other feed here:
  1. read the dump, checksum it and RUN THE RULE-6 GUARD before opening the run,
     so a crash -- or a refusal -- leaves no half-written run row;
  2. load the registry under a deterministic order;
  3. clear this source's old rows, so a re-ingest REPLACES rather than accumulates;
  4. write the assertions;
  5. rebuild the question register, finish, commit.

THE DATA DEPENDENCY IS REAL: the cascade joins substance_moiety.display_name and
live INCHIKEY/CAS identity_claim rows, so `unii` and `chebi` must have run. Ingest
this against an empty registry and every endpoint resolves to nothing, quietly.
"""
import dataclasses
import gzip
import logging
import pathlib

import psycopg

from drugref import interactions, provenance, questions
from drugref.ingest import drugcentral
from drugref.ingest.checksum import checksum
from drugref.ingest.drugcentral_resolve import (
    Registry, build_endpoint_index, first_wins,
)

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class DrugCentralSummary:
    """What one ingest did, in buckets that RECONCILE.

    `rows_read` = `rows_excluded_by_reference` + `rows_bundleable`, and
    `rows_bundleable` = `rows_resolved` + `rows_self_pair` + `rows_unresolved`.
    __post_init__ refuses any other arithmetic, because a summary whose buckets do
    not sum is a number nobody can check -- curate_onchigh counted entries it had
    silently dropped, and the re-measurement's Measurement guard exists for the
    same reason.

    `rows_resolved` counts rows whose two endpoints reached TWO DIFFERENT moieties;
    `rows_self_pair` counts those that reached ONE (0 of 7,571 on the 2023 release,
    and a bucket rather than a footnote so it cannot become nonzero unnoticed).
    `pairs` is the DISTINCT UNORDERED pair count from the view, which is smaller
    than `rows_resolved` because 33 pairs are published in both orders.
    """

    rows_read: int
    rows_excluded_by_reference: int
    rows_bundleable: int
    rows_resolved: int
    rows_self_pair: int
    rows_unresolved: int
    pairs: int
    duplicate_keys: int

    def __post_init__(self) -> None:
        if self.rows_excluded_by_reference + self.rows_bundleable != self.rows_read:
            raise ValueError(
                f"excluded ({self.rows_excluded_by_reference}) + bundleable "
                f"({self.rows_bundleable}) do not sum to read ({self.rows_read})")
        landed = self.rows_resolved + self.rows_self_pair + self.rows_unresolved
        if landed != self.rows_bundleable:
            raise ValueError(
                f"resolved ({self.rows_resolved}) + self-pair "
                f"({self.rows_self_pair}) + unresolved ({self.rows_unresolved}) "
                f"do not sum to bundleable ({self.rows_bundleable})")

    def __str__(self) -> str:
        return (f"{self.rows_bundleable} bundleable of {self.rows_read} rows "
                f"({self.rows_excluded_by_reference} excluded by rule 6) -> "
                f"{self.pairs} pairs; {self.rows_unresolved} unresolved, "
                f"{self.rows_self_pair} self-pairs, "
                f"{self.duplicate_keys} colliding registry keys")


def load_registry(conn: psycopg.Connection) -> tuple[Registry, int]:
    """The drugref side of the join: three lookups onto `moiety_uuid`.

    EVERY READ IS ORDERED, and that is not cosmetic. `identity_claim` is unique on
    (moiety_uuid, scheme, value) and deliberately NOT across moieties, so two
    moieties may legitimately carry one CAS number -- measured 2026-08-23, 14
    InChIKeys and 29 CAS numbers are claimed by more than one. An unordered
    single-row read would let the same dump resolve differently on two runs.

    Live claims only (`superseded_by IS NULL`): a corrected-away identifier must
    not resurrect a resolution.

    Returns the registry and the total number of colliding keys, which the summary
    reports rather than discards.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT display_name, moiety_uuid::text "
                    "FROM drugref.substance_moiety "
                    "ORDER BY display_name, moiety_uuid")
        display_name, dup_names = first_wins(cur.fetchall())

        cur.execute("SELECT value, moiety_uuid::text FROM drugref.identity_claim "
                    "WHERE scheme = %s AND superseded_by IS NULL "
                    "ORDER BY value, moiety_uuid", ("INCHIKEY",))
        inchikey, dup_keys = first_wins(cur.fetchall())

        cur.execute("SELECT value, moiety_uuid::text FROM drugref.identity_claim "
                    "WHERE scheme = %s AND superseded_by IS NULL "
                    "ORDER BY value, moiety_uuid", ("CAS",))
        cas, dup_cas = first_wins(cur.fetchall())

    # `Registry` folds its own keys, so the SQL above does not -- the case rule
    # used to live in both places, which is the shape this repo keeps losing to.
    return (Registry(display_name=display_name, inchikey=inchikey, cas=cas),
            dup_names + dup_keys + dup_cas)


def ingest_drugcentral(conn: psycopg.Connection, *,
                       dump_path: str | pathlib.Path,
                       release: str) -> DrugCentralSummary:
    """Read one DrugCentral dump and rebuild this source's projection."""
    dump_path = pathlib.Path(dump_path)
    digest = checksum(dump_path)

    with gzip.open(dump_path, "rt", encoding="utf-8") as handle:
        tables = drugcentral.read_tables(handle)

    # RULE 6, BEFORE ANY RUN ROW EXISTS. A refusal must leave the database exactly
    # as it was, and an ingest_run with finished_at NULL is not "exactly as it was".
    drugcentral.check_reference_identity(tables.reference)

    bundleable = tuple(drugcentral.bundleable_rows(tables.ddi))
    index = build_endpoint_index(tables.structures, tables.synonyms)

    run_id = provenance.open_run(conn, source=drugcentral.SOURCE,
                                 upstream_release=release,
                                 source_checksum=digest,
                                 writer=drugcentral.WRITER)

    # The work transaction. REPEATABLE READ so the registry the cascade joins is
    # ONE snapshot: under READ COMMITTED each of the three lookups would get its
    # own, and a concurrent claim landing between them would make the resolution
    # depend on timing. Must be the first statement of the transaction.
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    registry, duplicate_keys = load_registry(conn)

    interactions.clear_source_drugcentral(conn, drugcentral.SOURCE)

    resolved = self_pair = unresolved = 0
    for row in bundleable:
        record = drugcentral.resolve_row(row, index, registry)
        interactions.add_drugcentral_assertion(
            conn,
            ingest_run_id=run_id,
            source=drugcentral.SOURCE,
            upstream_key=record.upstream_key,
            endpoint_1_name=record.endpoint_1_name,
            endpoint_2_name=record.endpoint_2_name,
            upstream_label=record.upstream_label,
            severity_label=record.severity_label,
            moiety_1_uuid=record.moiety_1_uuid,
            moiety_2_uuid=record.moiety_2_uuid,
            route_1=record.route_1,
            route_2=record.route_2)
        if record.self_pair:
            self_pair += 1
        elif record.resolved:
            resolved += 1
        else:
            unresolved += 1

    questions.register_from_gaps(conn, run_id)
    pairs = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_pair").fetchone()[0]
    provenance.finish_run(conn, run_id)
    conn.commit()

    summary = DrugCentralSummary(
        rows_read=len(tables.ddi),
        rows_excluded_by_reference=len(tables.ddi) - len(bundleable),
        rows_bundleable=len(bundleable),
        rows_resolved=resolved,
        rows_self_pair=self_pair,
        rows_unresolved=unresolved,
        pairs=int(pairs),
        duplicate_keys=duplicate_keys)
    log.info("drugcentral: %s", summary)
    return summary
```

Check `provenance.finish_run`'s exact signature and `questions.register_from_gaps`'s before wiring them: `grep -n "def finish_run" -A 3 src/drugref/provenance.py` and `grep -n "def register_from_gaps" -A 3 src/drugref/questions.py`.

- [ ] **Step 4: Run the summary tests**

Run: `uv run pytest tests/test_drugcentral_run.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Write the DB-gated end-to-end test**

Append to `tests/test_drugcentral_run.py`:

```python
@pytest.fixture
def _clean(conn):
    """ingest_drugcentral COMMITS, so the conn fixture's rollback cannot undo it.
    Same shape as tests/test_ingest_run.py's autouse truncate fixture."""
    yield
    conn.execute("TRUNCATE drugref.drugcentral_ddi_assertion, "
                 "drugref.open_question, drugref.ingest_run CASCADE")
    conn.commit()


@pytest.mark.usefixtures("_clean")
def test_the_fixture_dump_ingests_and_reconciles(conn):
    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    assert summary.rows_excluded_by_reference > 0, "the rule-6 filter did nothing"
    assert summary.rows_bundleable > 0
    stored = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone()[0]
    assert stored == summary.rows_bundleable


@pytest.mark.usefixtures("_clean")
def test_no_excluded_row_reaches_the_database(conn):
    """Rule 6 enforced by EXECUTION, not by reading the filter."""
    drugcentral_run.ingest_drugcentral(conn, dump_path=FIXTURE, release="11012023")
    leaked = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion "
        "WHERE upstream_label LIKE '%redacted%'").fetchone()[0]
    assert leaked == 0


@pytest.mark.usefixtures("_clean")
def test_a_second_ingest_replaces_rather_than_accumulates(conn):
    first = drugcentral_run.ingest_drugcentral(
        conn, dump_path=FIXTURE, release="11012023")
    drugcentral_run.ingest_drugcentral(conn, dump_path=FIXTURE, release="11012023")
    stored = conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone()[0]
    assert stored == first.rows_bundleable


@pytest.mark.usefixtures("_clean")
def test_a_renumbered_reference_writes_nothing_at_all(conn, tmp_path):
    """The refusal must leave the database exactly as it was -- including no
    ingest_run row, which is why the guard runs before open_run."""
    import gzip as _gzip
    forged = tmp_path / "forged.sql.gz"
    original = _gzip.open(FIXTURE, "rt", encoding="utf-8").read()
    with _gzip.open(forged, "wt", encoding="utf-8") as out:
        out.write(original.replace("Veterans Health Administration", "Lexicomp"))
    before = conn.execute("SELECT count(*) FROM drugref.ingest_run").fetchone()[0]
    with pytest.raises(drugcentral.ReferenceIdentityError):
        drugcentral_run.ingest_drugcentral(
            conn, dump_path=forged, release="11012023")
    after = conn.execute("SELECT count(*) FROM drugref.ingest_run").fetchone()[0]
    assert after == before
```

Add `from drugref.ingest import drugcentral` to the test module's imports.

- [ ] **Step 6: Run the full suite**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q 2>&1 | tail -3`
Expected: no failures.

- [ ] **Step 7: Commit**

```bash
git add src/drugref/ingest/drugcentral_run.py tests/test_drugcentral_run.py
git commit -m "drugcentral: the orchestrator, and a summary that reconciles

Rule 6 runs BEFORE open_run, so a refusal leaves no ingest_run row behind. The
registry is read under REPEATABLE READ and a deterministic ORDER BY, because 14
InChIKeys and 29 CAS numbers are claimed by more than one moiety. The summary's
buckets must sum or DrugCentralSummary refuses to exist.

Refs #101"
```

---

### Task 12: The CLI

**Files:**
- Create: `src/drugref/cli_drugcentral.py`
- Modify: `src/drugref/cli.py:61` (import) and `:428` (registration)
- Test: `tests/test_cli_drugcentral.py`

**Interfaces:**
- Consumes: Task 11's `ingest_drugcentral`.
- Produces: `drugref ingest drugcentral --dump <path> --release <tag>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_drugcentral.py`:

```python
# tests/test_cli_drugcentral.py
"""`drugref ingest drugcentral`: a standalone subcommand, not a chain step.

FDA-CYP set that precedent and this follows it. The dump is 1.4 GB and pinned to
one 2023 release with no successor offered; the chain is the routine
rebuild-everything path, and a source that cannot refresh does not belong in it.
"""
import pytest

from drugref import cli


def test_the_subcommand_is_registered():
    parser = cli.build_parser()
    args = parser.parse_args(
        ["ingest", "drugcentral", "--dump", "d.sql.gz", "--release", "11012023"])
    assert str(args.dump) == "d.sql.gz"
    assert args.release == "11012023"


def test_the_release_is_required():
    """Unlike fda-cyp, the dump states no release of its own that drugref reads,
    so provenance depends on the operator naming it."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["ingest", "drugcentral", "--dump", "d.sql.gz"])


def test_drugcentral_is_not_a_chain_step():
    """A chain step would resolve its inputs before ANY step runs, so a node
    without the 1.4 GB dump would see the whole chain abort -- the failure
    IngestStep.packaged_defaults was added for."""
    assert "drugcentral" not in {step.name for step in cli.STEPS}
```

- [ ] **Step 2: Run and watch fail**

Run: `uv run pytest tests/test_cli_drugcentral.py -v`
Expected: FAIL — `invalid choice: 'drugcentral'`.

- [ ] **Step 3: Write `src/drugref/cli_drugcentral.py`**

```python
# src/drugref/cli_drugcentral.py
"""The `drugref ingest drugcentral` subcommand: its parser wiring and handler.

WHY ITS OWN MODULE rather than a STEPS entry that cli.py's generic
`_handle_ingest` drives: this source is NOT a chain step. cli_fda_cyp.py is the
established shape for exactly that, and cli.py sits at CLAUDE.md rule 4's
~500-line cap.

WHY NOT A CHAIN STEP. The only published dump is
`drugcentral.dump.11012023.sql.gz` -- 1.4 GB, dated 2023-11-01, with no successor
offered as of 2026-08-23. The chain is the routine rebuild-everything path; a
step there resolves its inputs BEFORE any step runs, so a node without the dump
would watch the whole chain abort, which is the failure
IngestStep.packaged_defaults was added to fix once already.

THE DATA DEPENDENCY IS REAL and is stated in --help rather than only in a
comment: the resolution cascade joins substance_moiety.display_name and live
INCHIKEY/CAS identity_claim rows, so `unii` and `chebi` must have run first.
"""
import pathlib

from drugref.ingest import drugcentral_run


def handle_drugcentral(conn, args) -> int:
    """`drugref ingest drugcentral --dump <path> --release <tag>`."""
    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=args.dump, release=args.release)
    print(f"drugcentral: {summary}")
    return 0


def add_parser(sources) -> None:
    """Register the `drugcentral` subcommand on `drugref ingest`'s subparser set."""
    parser = sources.add_parser(
        "drugcentral",
        help="ingest DrugCentral's NDF-RT drug-drug interactions "
             "(run AFTER unii and chebi: the cascade needs the registry)")
    parser.add_argument("--dump", required=True, type=pathlib.Path,
                        help="path to drugcentral.dump.<release>.sql.gz")
    # REQUIRED, unlike fda-cyp's. The dump carries a `dbversion` but drugref does
    # not read it, so nothing here could contradict the operator -- and a
    # provenance tag drugref guessed would be worse than one it was given.
    parser.add_argument("--release", required=True,
                        help="upstream release tag, e.g. 11012023")
    parser.set_defaults(handler=handle_drugcentral)
```

- [ ] **Step 4: Wire it into `cli.py`**

Add `cli_drugcentral` to the import at `src/drugref/cli.py:61`, and beside `cli_fda_cyp.add_parser(sources)` at line 428 add:

```python
    cli_drugcentral.add_parser(sources)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_cli_drugcentral.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Prove it runs end to end against the fixture**

```bash
createdb -h localhost -p 5532 -U postgres drugref_cli_smoke
uv run drugref --dsn "host=localhost port=5532 dbname=drugref_cli_smoke user=postgres" migrate
uv run drugref --dsn "host=localhost port=5532 dbname=drugref_cli_smoke user=postgres" \
    ingest drugcentral --dump tests/fixtures/drugcentral_ddi_subset.sql.gz --release 11012023
dropdb -h localhost -p 5532 -U postgres drugref_cli_smoke
```

Expected: a `drugcentral: N bundleable of M rows (K excluded by rule 6) -> …` line and exit 0. Every endpoint will be unresolved against an empty registry, which is correct and is what the `--help` warning is about.

- [ ] **Step 7: Commit**

```bash
git add src/drugref/cli_drugcentral.py src/drugref/cli.py tests/test_cli_drugcentral.py
git commit -m "drugcentral: the ingest subcommand

Standalone, not a chain step -- following fda-cyp. The dump is 1.4 GB and
pinned to one 2023 release, and a chain step resolves its inputs before any
step runs, so a node without it would see the whole chain abort.

Refs #101"
```

---

### Task 13: Measure it on real data

Nothing in this plan is believed until it runs against the real releases. This task produces the figures every later document quotes.

**Files:**
- Create: `docs/superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-measurement.md`

- [ ] **Step 1: Build a reference database from the kept one**

```bash
psql "host=localhost port=5532 dbname=postgres user=postgres" -c \
  "CREATE DATABASE drugref_dc049 TEMPLATE drugref_dc101"
export DSN="host=localhost port=5532 dbname=drugref_dc049 user=postgres"
uv run drugref --dsn "$DSN" migrate
psql "$DSN" -Atc "SELECT max(filename) FROM drugref.schema_migration"
```

Expected: `049_drugcentral_ddi.sql`. `drugref_dc101` already holds `db/048` plus the full documented ingest chain, so this is the `TEMPLATE` + `migrate` workflow re-tested for the eighth round running.

- [ ] **Step 2: Record the before-state, which two numbers must not move**

```bash
psql "$DSN" -Atc "SELECT count(*) FROM drugref.ddi_candidate_pair"     # expect 21664
psql "$DSN" -Atc "SELECT count(*) FROM drugref.substance_moiety"       # expect 19438
psql "$DSN" -Atc "SELECT count(*) FROM drugref.moiety_contraindication"
psql "$DSN" -Atc "SELECT count(*) FROM drugref.open_question WHERE is_current"
```

- [ ] **Step 3: Run the ingest and time it**

```bash
time uv run drugref --dsn "$DSN" ingest drugcentral \
    --dump downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz --release 11012023
```

- [ ] **Step 4: Read every figure back**

```bash
psql "$DSN" -Atc "SELECT count(*) FROM drugref.drugcentral_ddi_assertion"
psql "$DSN" -Atc "SELECT route_1, count(*) FROM drugref.drugcentral_ddi_assertion GROUP BY 1 ORDER BY 2 DESC"
psql "$DSN" -Atc "SELECT count(*) FROM drugref.drugcentral_ddi_pair"
psql "$DSN" -Atc "SELECT severity, count(*) FROM drugref.drugcentral_ddi_pair GROUP BY 1"
psql "$DSN" -Atc "SELECT candidate_source, count(*) FROM drugref.exact_ddi_pair GROUP BY 1"
psql "$DSN" -Atc "SELECT count(*) FROM drugref.gap_unresolved_ddi_endpoint"
psql "$DSN" -Atc "SELECT count(*) FROM drugref.open_question WHERE gap_kind = 'unresolved_ddi_endpoint' AND is_current"
psql "$DSN" -Atc "SELECT count(*) FROM drugref.ddi_candidate_pair"     # MUST still be 21664
psql "$DSN" -Atc "SELECT count(*) FROM drugref.substance_moiety"       # MUST still be 19438
psql "$DSN" -Atc "SELECT source_checksum FROM drugref.ingest_run WHERE source = 'DRUGCENTRAL'"
```

The checksum must be `055904d152d6c8eef4ee872b25f6476019682df8b5f49bcdf7cc018204f3e04f` — the SHA-256 the re-measurement recorded. A different digest means a different dump, and every figure below it describes something else.

**Expected from the re-measurement, and a mismatch is a finding rather than a nuisance:** 7,571 bundleable rows, 7,534 pair-yielding, 37 unresolved, 0 self-pairs, 7,501 distinct pairs, 10 gap rows. `severity` should split `contraindicated` / `moderate` in the ratio the bands do (2,307 / 5,264 rows, fewer pairs after the collapse).

- [ ] **Step 5: Measure the new view's hot path**

```bash
psql "$DSN" -c "EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM drugref.exact_ddi_pair
                WHERE moiety_lo = (SELECT moiety_lo FROM drugref.exact_ddi_pair LIMIT 1)"
psql "$DSN" -c "EXPLAIN ANALYZE SELECT * FROM drugref.ddi_candidate_pair LIMIT 1"
```

The second is the regression check: `ddi_candidate_pair`'s plan must be unchanged by this migration. db/034's measurement is the reason — an arm added to that view cost 3.6× even when the new grain was empty.

- [ ] **Step 6: Write the measurement record**

Create `docs/superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-measurement.md` with every command above and its actual output, the database name and its migration head, the ingest wall-clock, and an explicit line for each figure that did NOT match the re-measurement's prediction. Do not tidy a mismatch away — the whole point of the re-measurement round was that seven such figures had decayed unnoticed.

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-measurement.md
git commit -m "drugcentral: measure the ingest on the real release

Every figure read back off drugref_dc049 (drugref_dc101 + db/049), with the
dump's SHA-256 confirming it is the same bytes the re-measurement described.
ddi_candidate_pair and substance_moiety verified unmoved.

Refs #101"
```

---

### Task 14: Documentation, NOTICE, and the PR

**Files:**
- Modify: `NOTICE`
- Modify: `docs/PROJECT-NOTES.md` (new slice section; the suite count under § "How to run / test")
- Modify: `docs/ROADMAP.md` (mark the slice done, in place)
- Modify: `docs/HANDOVER.md` (regenerate)
- Create: `docs-site/docs/decisions/<slug>.md` and its `mkdocs.yml` nav entry

- [ ] **Step 1: Add the NOTICE entry**

Every bundled reference-data source needs one; `NOTICE`'s existing entries are the shape. Append:

```
- DrugCentral `ddi` (drug-drug interactions) — DrugCentral is published by the
  Division of Translational Informatics, University of New Mexico, under CC BY-SA 4.0
  (https://creativecommons.org/licenses/by-sa/4.0/). drugref ingests ONE of its three
  interaction references: `ddi_ref_id = 2`, the U.S. Department of Veterans Affairs /
  Veterans Health Administration National Drug File – Reference Terminology (NDF-RT),
  a work of the U.S. federal government. The other two — Stockley's Drug Interactions
  (Baxter 2010, ISBN 0853699143, a copyrighted book) and Lexicomp Online (Wolters
  Kluwer Health, a commercial compendium) — are NOT ingested, and DrugCentral's own
  CC BY-SA licence on the compilation is not treated as evidence of a right to
  relicense either. The reference identity is verified against the dump's own
  `reference` table on every ingest, so a re-published dump that renumbered its
  references would abort rather than bundle an excluded one. Source release:
  drugcentral.dump.11012023.sql.gz, SHA-256 recorded on ingest_run for every load.
  The committed fixture (tests/fixtures/drugcentral_ddi_subset.sql.gz) carries rows
  from all three references so the exclusion is exercised, with the excluded
  references' description text redacted.
```

- [ ] **Step 2: Add the PROJECT-NOTES slice section**

Edited IN PLACE and under no line bound. Write a new `## The DrugCentral ddi ingest (2026-08-23) — db/049` section after § "The DrugCentral re-measurement", covering: what shipped; the two measurements this round added (the empty descriptions, the both-order disagreement) and that they changed the design; the four design decisions and what each rejected; every figure from Task 13; and the traps — the route vocabulary's admitted second home and its pinning test, the `upstream_key` tiebreak, and that `class_contraindication_source` was NOT widened despite HANDOVER saying so.

**Update the suite count** under § "How to run / test" — that comment is the ONE home for it, and the number has drifted six times. Read the real number: `DRUGREF_TEST_DSN=... uv run pytest --collect-only -q 2>&1 | tail -2`.

- [ ] **Step 3: Update ROADMAP in place**

Mark the DrugCentral slice `✅ DONE`, correct the "Still not started: the ingest itself" paragraph, and correct its `class_contraindication_source` claim. Point the figures at PROJECT-NOTES rather than restating them.

- [ ] **Step 4: Publish the design decision**

`docs-site/docs/decisions/` holds LIVING records of decisions that currently stand. This slice has one worth publishing: **the candidate tier now carries an upstream severity, and the mapping is data.** Write it, and add it to `docs-site/mkdocs.yml`'s nav.

Then: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`

- [ ] **Step 5: Regenerate HANDOVER**

Rewrite it for the next session — **within the line bound its own header states**, which you read off the file rather than from this plan. It should carry: what shipped, what the next slice is, and the follow-ups this round leaves (below).

- [ ] **Step 6: File the follow-ups rather than silently widening**

```bash
gh issue create --title "exact_ddi_pair adds a third population to the ungraded cross-source disagreement question" --body "..."
```

The spec §10 names it: 635 of the 7,501 DrugCentral pairs are already reachable through MED-RT's class expansion, and nothing compares the two. That is #97/#106's question one tier down, and this slice adds to it without answering it.

- [ ] **Step 7: Final verification before the PR**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q 2>&1 | tail -3
uv run ruff check .
uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml
```

All three must be clean. A skip is not a pass.

- [ ] **Step 8: Commit, push, open the PR**

```bash
git add -A
git commit -m "drugcentral: document the ddi ingest round

Refs #101"
git push -u origin claude/drugcentral-ddi-ingest
gh pr create --base main --title "DrugCentral ddi ingest (db/049)" --body "..."
```

The PR body must link #101, state what shipped, and name the two measurements that changed the design.

---

## Self-Review

**Spec coverage.** §1 scope → Tasks 1–12. §2 rule 6 → Tasks 9 (guard), 10 (fixture), 11 (guard placement), 14 (NOTICE). §3.1/3.2 measurements → Tasks 4 (`upstream_label` comment), 5 (collapse, tiebreak). §4 rejected shapes → recorded in the spec, cited in the migration comments. §5.1 → Task 2. §5.2 → Task 3. §5.3 → Task 4. §5.4 → Task 5. §5.5 → Task 6. §6 (`ddi_candidate_pair` untouched) → Task 6 Step 1's regression test and Task 13 Step 5's plan check. §7 gap → Task 7. §8 code → Tasks 1, 8, 9, 11, 12. §9 tests and measurement → every task's test steps plus Task 13. §10 open questions → Task 14 Step 6.

**One spec correction found and applied here:** §9 says the fixture generator lives at `tools/make_drugcentral_subset.py`. Every existing generator lives in `tests/fixtures/` (`make_medrt_subset.py`, `make_pbs_subset.py`, …), so Task 10 uses `tests/fixtures/make_drugcentral_subset.py`. Fix the spec to match.

**Type consistency.** `AssertionRecord`'s nine fields match `add_drugcentral_assertion`'s nine content parameters exactly, and both match the table's nine non-provenance columns. `Registry`, `EndpointIndex`, `resolve_endpoint` and `first_wins` are used under the signatures Task 1 fixes. `DrugCentralSummary`'s eight fields are constructed once, in `ingest_drugcentral`, with every one named.

**Known gaps in this plan, stated rather than hidden.** Task 10 Step 1 describes the fixture generator's *behaviour* and does not hand over its body — writing a `pg_dump`-compatible escaper is real work, and `drugcentral_dump.decode_copy_field` is named as the specification to invert. Task 14's document edits are described by content rather than transcribed, because HANDOVER and PROJECT-NOTES must be written against what the round actually measured.

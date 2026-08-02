# Ingest-operability round (#16, #47) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Make an ingest observable (a crashed run leaves a committed provenance row) and runnable (a CLI),
and persist the CI subjects `medrt_run` counts and discards.

**Architecture:** One `provenance.py` replaces six hand-written copies of the run-record SQL; `open_run`
commits its row in its own transaction so it outlives the rollback of the work, `finish_run` does not commit
so the stamp lands with the work. `ingest_run` gains a `writer` column because source `MED-RT` has two
writers, and two views publish what a run record says. A `drugref` console script exposes every orchestrator
plus a `chain` that runs them in dependency order.

**Tech Stack:** Python 3.12, `uv`, `psycopg` v3, PostgreSQL ≥ 18, pytest, ruff.

**Design spec:** [2026-08-02-drugref-ingest-operability-design.md](../specs/2026-08-02-drugref-ingest-operability-design.md)

**Branch:** `fix/ingest-operability-round` (already created; the spec is committed on it as 9e307bb).

## Global Constraints

- **TDD.** Failing test first, every task. A step that writes code before its test is a plan violation.
- **All tests must pass before every commit**, with the DB-gated majority actually running:
  `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`
- **Lint:** `ruff check src tests` — **NOT** `ruff check .`, which walks `downloads/` and hangs.
- **Migrations are immutable once applied.** `db/025` and `db/026` are new files. They may be edited freely
  *while this branch is unmerged* (conftest drops and recreates the schema each session), never after merge.
- **Inline documentation understandable by a junior contributor is mandatory** — this codebase's comments
  explain *why*, not *what*. Match the surrounding density.
- **Keep files under ~500 lines.**
- **No new dependency** is needed or permitted in this round (`argparse` is stdlib).
- **Never edit `CLAUDE.md`.** `HANDOVER.md` and `ROADMAP.md` are updated in Task 7 only, and stay under 500
  lines each.

## File Structure

| File | Responsibility |
|---|---|
| `db/025_ingest_observability.sql` | **Create.** `ingest_run.writer` + backfill + `loaded_release` + `ingest_run_incomplete`. |
| `db/026_contraindication_class_reason.sql` | **Create.** Fourth `reason` value; re-cut `gap_unmatched_ingredient` tie-break. |
| `src/drugref/provenance.py` | **Create.** The only writer of `drugref.ingest_run`: `open_run` / `finish_run` / `WRITERS`. |
| `src/drugref/cli.py` | **Create.** `drugref` console script: `migrate`, `status`, `ingest <source>`, `ingest chain`. |
| `src/drugref/ingest/{run,chebi,medrt_run,mesh_run,mesh_rel_run,pbs_run}.py` | **Modify.** Use `provenance`; declare `SOURCE`/`WRITER`; `chebi` gains try/rollback/logging. |
| `src/drugref/classes.py` | **Modify.** `CONTRAINDICATION_CLASS` constant, added to `REASONS`. |
| `pyproject.toml` | **Modify.** `[project.scripts] drugref = "drugref.cli:main"`. |
| `tests/test_ingest_observability.py` | **Create.** `writer` column + both views. |
| `tests/test_provenance.py` | **Create.** `open_run`/`finish_run` semantics, the crash test, the one-writer contract test. |
| `tests/test_cli.py` | **Create.** Pure parser/step/glob tests + one DB-gated end-to-end. |
| 25 existing test modules | **Modify (Task 1 only).** Add `writer` to their direct `ingest_run` inserts. |

---

### Task 1: `db/025` — `ingest_run.writer` and the two views

`writer` is `NOT NULL` with no `DEFAULT`, so it cannot land separately from everything that inserts a run
row. That is why this one task also touches 25 test modules and the six orchestrators' inline SQL: an atomic
change, not scope creep.

**Files:**
- Create: `db/025_ingest_observability.sql`
- Create: `tests/test_ingest_observability.py`
- Modify: `src/drugref/ingest/run.py:74-77`, `chebi.py:22-25`, `medrt_run.py:110-113`,
  `mesh_run.py:131`, `mesh_rel_run.py:237-241`, `pbs_run.py:133-136` (add the column to the inline INSERT;
  they still write it inline until Task 2)
- Modify: `tests/conftest.py:56-60` and the 24 other test modules listed in Step 6

**Interfaces:**
- Produces: `drugref.ingest_run.writer` (text, NOT NULL, CHECKed); views `drugref.loaded_release`
  (columns `source, writer, upstream_release, source_checksum, ingest_run_id, started_at, finished_at`) and
  `drugref.ingest_run_incomplete` (columns `ingest_run_id, source, writer, upstream_release,
  source_checksum, started_at`).
- Produces: the writer vocabulary `'unii_run' | 'chebi' | 'medrt_run' | 'mesh_run' | 'mesh_rel_run' |
  'pbs_run' | 'curation' | 'unattributed'`, consumed by Task 2's `provenance.WRITERS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingest_observability.py`:

```python
# tests/test_ingest_observability.py
"""What a run record says, and what it could not say before db/025 (#16).

Two facts this module pins, both of which the schema alone would let drift:

* `writer`, because source 'MED-RT' has TWO writers (medrt_run and mesh_rel_run) and
  a release-per-source view cannot tell them apart. That is #39 one layer up, on the
  table #39's own fix could not reach.
* `ingest_run_incomplete`, which BEFORE THIS MIGRATION COULD ONLY EVER BE EMPTY: the
  run row was written inside the work's transaction, so a crash rolled it away.
"""
import psycopg
import pytest


def _run(conn, source, writer, release="r1", finished=False):
    """One ingest_run row. `finished` decides which of the two views it lands in."""
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, %s, 'sum', %s) RETURNING ingest_run_id",
        (source, release, writer)).fetchone()[0]
    if finished:
        conn.execute("UPDATE drugref.ingest_run SET finished_at = now() "
                     "WHERE ingest_run_id = %s", (run_id,))
    return run_id


def test_writer_has_no_default(conn):
    """NO DEFAULT, deliberately -- db/018's `reason` posture, for the same reason: a
    writer that does not declare itself must fail, not inherit somebody else's
    identity. A DEFAULT would make every future orchestrator correct by accident."""
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute("INSERT INTO drugref.ingest_run "
                     "(source, upstream_release, source_checksum) "
                     "VALUES ('UNII', 'r1', 'sum')")


def test_an_unknown_writer_is_refused(conn):
    """The vocabulary is CHECKed, so a typo cannot silently create a seventh writer
    that loaded_release then reports as its own live release."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO drugref.ingest_run "
                     "(source, upstream_release, source_checksum, writer) "
                     "VALUES ('UNII', 'r1', 'sum', 'unii')")


def test_loaded_release_separates_the_two_medrt_writers(conn):
    """THE REASON THE COLUMN EXISTS. medrt_run and mesh_rel_run both open under
    'MED-RT'. Re-ingest one and not the other and a per-source view would report the
    newer as THE MED-RT release while the other half is a release behind."""
    _run(conn, "MED-RT", "medrt_run", release="2026.07.06", finished=True)
    _run(conn, "MED-RT", "mesh_rel_run", release="2026.05.04", finished=True)

    rows = dict(conn.execute(
        "SELECT writer, upstream_release FROM drugref.loaded_release "
        "WHERE source = 'MED-RT'").fetchall())
    assert rows == {"medrt_run": "2026.07.06", "mesh_rel_run": "2026.05.04"}


def test_loaded_release_keeps_only_the_newest_finished_run(conn):
    """One row per (source, writer): the release that writer last landed."""
    _run(conn, "UNII", "unii_run", release="old", finished=True)
    _run(conn, "UNII", "unii_run", release="new", finished=True)

    assert conn.execute(
        "SELECT upstream_release FROM drugref.loaded_release "
        "WHERE source = 'UNII'").fetchall() == [("new",)]


def test_loaded_release_ignores_a_run_that_never_finished(conn):
    """A crashed run is not a loaded release. The whole point of committing the row
    early is that this distinction becomes observable rather than implied."""
    _run(conn, "PBS", "pbs_run", release="landed", finished=True)
    _run(conn, "PBS", "pbs_run", release="crashed", finished=False)

    assert conn.execute(
        "SELECT upstream_release FROM drugref.loaded_release "
        "WHERE source = 'PBS'").fetchall() == [("landed",)]


def test_ingest_run_incomplete_reports_exactly_the_unfinished(conn):
    """The complementary filter on the SAME column, so the two views cannot disagree
    -- db/018's ci_rule_partner_reach shape, adopted after the interaction debt round
    found one measure stated twice with only one copy corrected."""
    _run(conn, "PBS", "pbs_run", release="landed", finished=True)
    crashed = _run(conn, "PBS", "pbs_run", release="crashed", finished=False)

    assert conn.execute(
        "SELECT ingest_run_id FROM drugref.ingest_run_incomplete").fetchall() \
        == [(crashed,)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_ingest_observability.py -v`

Expected: all six FAIL — `UndefinedColumn: column "writer" of relation "ingest_run" does not exist`.

- [ ] **Step 3: Write the migration**

Create `db/025_ingest_observability.sql`:

```sql
-- db/025_ingest_observability.sql
-- #16, part 1: make a run record say who wrote it, and publish what is loaded.
--
-- WHY A `writer` COLUMN AT ALL. `ingest_run.source` names the AUTHORITY, and one
-- authority can have two writers: medrt_run ingests MED-RT's classification and
-- class-keyed contraindications, mesh_rel_run ingests MED-RT's MeSH-keyed halves, and
-- BOTH open their runs under source 'MED-RT'. Their source_checksums legitimately
-- differ (ingest/checksum.py hashes one file for the first and three for the second),
-- so "which MED-RT release is live" has two answers. A view keyed on source alone
-- reports whichever finished last and hides that the other half is a release behind.
-- That is exactly #39 -- two writers sharing one scope -- one layer up, on the table
-- #39's own fix (a `reason` discriminator) could not reach.
--
-- NOT NULL, NO DEFAULT: db/018's posture for `reason`, and for the same reason. A
-- writer that does not declare itself must fail loudly rather than inherit somebody
-- else's identity, because the value is what a consumer reads to decide whether a
-- projection is current.
ALTER TABLE drugref.ingest_run ADD COLUMN IF NOT EXISTS writer text;

-- HISTORICAL ROWS ARE NOT GUESSED. Nothing in an existing row distinguishes the two
-- MED-RT writers, so attributing them would be inventing provenance -- the one thing
-- this table exists to prevent. `unattributed` is a real value with a stated meaning:
-- written before this migration, when two orchestrators shared a source and nothing
-- told them apart. ingest_run is HISTORY, not a rebuildable projection, so it cannot
-- heal itself the way db/018's table did; these rows age out of loaded_release
-- naturally, by being older than the next real run.
UPDATE drugref.ingest_run SET writer = 'unattributed' WHERE writer IS NULL;

ALTER TABLE drugref.ingest_run ALTER COLUMN writer SET NOT NULL;

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed'));

COMMENT ON COLUMN drugref.ingest_run.writer IS
    'WHICH orchestrator opened this run, as distinct from `source`, which names the '
    'AUTHORITY. They are not the same: source ''MED-RT'' has two writers (medrt_run '
    'and mesh_rel_run), so a release is only unambiguous per (source, writer). '
    '''curation'' covers a DRUGREF-sourced run written by a curator rather than by an '
    'orchestrator (Plan C''s overlay tier). ''unattributed'' means the row predates '
    'db/025 and cannot be attributed. Extend this CHECK together with '
    'provenance.WRITERS -- they are a pair, and a value admitted to one but not the '
    'other is either refused at write time or invisible to the contract test.';

-- ============================================================================
-- The two views: complementary filters on ONE column
-- ============================================================================
-- Stated as a partition rather than as two independent questions, because "one
-- quantity stated twice is a quantity that will disagree" (db/006, and the defect the
-- interaction debt round found in its own first draft). finished_at IS NULL and
-- finished_at IS NOT NULL exhaust the table between them.

-- BEFORE THIS ROUND THIS VIEW COULD ONLY EVER BE EMPTY. Every orchestrator wrote its
-- ingest_run row inside the transaction that did the work, so a crash rolled the
-- provenance away with it: `finished_at` is nullable, which ASSERTS that "started,
-- never finished" is an observable state, and it never was. provenance.open_run
-- commits the row in its own transaction, which is what makes this view able to hold
-- anything at all.
CREATE OR REPLACE VIEW drugref.ingest_run_incomplete AS
SELECT ingest_run_id, source, writer, upstream_release, source_checksum, started_at
FROM   drugref.ingest_run
WHERE  finished_at IS NULL
ORDER  BY started_at DESC, ingest_run_id DESC;

COMMENT ON VIEW drugref.ingest_run_incomplete IS
    'Runs that started and never finished -- a crash, a kill, or a run still in '
    'flight. EMPTY BY CONSTRUCTION BEFORE db/025: the run row used to roll back with '
    'the work it described, so a crashed ingest was indistinguishable from one that '
    'never started. A row here is not itself an error: check it against '
    'loaded_release, which reports the last run that DID finish.';

-- One row per (source, writer): the release that writer last landed.
--
-- The ingest_run_id tie-break is not decoration. finished_at is a timestamp, two runs
-- can share one, and a DISTINCT ON whose ORDER BY does not name a unique row keeps
-- whichever the plan happened to emit first -- the same latent non-determinism db/018
-- found in gap_unmatched_ingredient.
CREATE OR REPLACE VIEW drugref.loaded_release AS
SELECT DISTINCT ON (source, writer)
       source, writer, upstream_release, source_checksum,
       ingest_run_id, started_at, finished_at
FROM   drugref.ingest_run
WHERE  finished_at IS NOT NULL
ORDER  BY source, writer, finished_at DESC, ingest_run_id DESC;

COMMENT ON VIEW drugref.loaded_release IS
    'Which upstream release each writer last landed, from which bytes, and when. '
    'PER (source, writer), NOT per source: MED-RT has two writers and re-ingesting one '
    'without the other is a real and otherwise invisible staleness. WHAT IT DOES NOT '
    'MEAN: this is the release a per-source rebuild last replaced its PROJECTION from, '
    'not a claim that every row attributed to that source carries this run''s id -- '
    'substance_moiety and identity_claim ACCUMULATE and hold rows from many runs by '
    'design. A run still in flight, or one that died, is in ingest_run_incomplete '
    'instead.';
```

- [ ] **Step 4: Add `writer` to the six orchestrators' inline INSERTs**

Each orchestrator keeps writing inline SQL for now (Task 2 replaces it). Add a module constant beside the
existing `SOURCE` where there is one, and create both where there is not (`run.py` and `chebi.py` hardcode
their source in the SQL string today).

`src/drugref/ingest/run.py` — add near the top, after `log = logging.getLogger(__name__)`:

```python
SOURCE = "UNII"
# WHICH orchestrator this is, as distinct from the authority it reads (db/025). One
# source can have two writers -- MED-RT does -- so a release is only unambiguous per
# (source, writer).
WRITER = "unii_run"
```

and replace the INSERT at `run.py:74-77` with:

```python
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, %s, %s, %s) RETURNING ingest_run_id",
        (SOURCE, upstream_release, checksum(unii_path), WRITER)).fetchone()[0]
```

Apply the identical shape to the other five, with these constants (add `SOURCE` to `chebi.py`; the other
four already have one):

| module | `SOURCE` | `WRITER` |
|---|---|---|
| `ingest/run.py` | `"UNII"` | `"unii_run"` |
| `ingest/chebi.py` | `"CHEBI"` | `"chebi"` |
| `ingest/medrt_run.py` | `"MED-RT"` (exists) | `"medrt_run"` |
| `ingest/mesh_run.py` | `"MeSH"` (exists) | `"mesh_run"` |
| `ingest/mesh_rel_run.py` | `"MED-RT"` (exists) | `"mesh_rel_run"` |
| `ingest/pbs_run.py` | `"PBS"` (exists) | `"pbs_run"` |

- [ ] **Step 5: Add `writer` to `tests/conftest.py`'s fixture**

Replace the body of the `ingest_run_id` fixture (`tests/conftest.py:56-60`):

```python
@pytest.fixture
def ingest_run_id(conn):
    """A committed-in-transaction ingest_run row for provenance FKs.

    Says which writer it stands in for (db/025): `writer` is NOT NULL with no
    DEFAULT, so every insert must name one, and naming a real one keeps the fixture
    honest about what it is simulating.
    """
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('PBS', 'test', 'test', 'pbs_run') RETURNING ingest_run_id").fetchone()[0]
```

- [ ] **Step 6: Add `writer` to the remaining direct inserts in tests**

Mechanical, one value per insert, chosen by the `source` the insert already names:

| the insert's `source` | the `writer` to add |
|---|---|
| `'UNII'` | `'unii_run'` |
| `'CHEBI'` | `'chebi'` |
| `'MED-RT'` | `'medrt_run'` (use `'mesh_rel_run'` only where the test is explicitly about the MeSH-keyed run) |
| `'MeSH'` | `'mesh_run'` |
| `'PBS'` | `'pbs_run'` |
| `'DRUGREF'` | `'curation'` |

Files to edit (`grep -rln "INSERT INTO drugref.ingest_run" tests/`): `test_accumulation_gap_views.py`,
`test_accumulation_read_path.py`, `test_accumulation_writer.py`, `test_chebi.py`, `test_claims.py`,
`test_class_registry_source_neutral.py`, `test_conditions_writer.py`, `test_db.py` (2),
`test_ddi_pairs.py`, `test_expansion_policy.py`, `test_gap_views.py`, `test_indications_writer.py`,
`test_interactions.py` (2), `test_local_schema.py`, `test_medrt_run.py`, `test_mesh_run.py`,
`test_pbs_run.py` (3), `test_questions.py`, `test_schema_accumulation.py`, `test_schema_classes.py`,
`test_schema_floor.py` (2), `test_schema_interactions.py`, `test_schema_question_registry.py`,
`test_source_clear_contract.py` (3).

Verify none were missed — this must print nothing:

```bash
grep -rn "source_checksum)" tests/ | grep "INSERT INTO drugref.ingest_run" || echo "all inserts updated"
```

- [ ] **Step 7: Run the whole suite**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`

Expected: PASS, 748 + 6 = **754** tests. A `NotNullViolation` in some other module means an insert in Step 6
was missed; the grep in Step 6 finds it.

- [ ] **Step 8: Lint and commit**

```bash
ruff check src tests
git add db/025_ingest_observability.sql tests/test_ingest_observability.py src/drugref/ingest tests/
git commit -m "feat(db): say WHICH writer opened a run, and publish what is loaded (#16)

ingest_run.source names the AUTHORITY, and MED-RT has two writers: medrt_run and
mesh_rel_run. Their checksums legitimately differ, so 'which MED-RT release is
live' has two answers and a per-source view reports whichever finished last --
#39 one layer up, on the table #39's own fix could not reach.

loaded_release is therefore per (source, writer). ingest_run_incomplete is its
complementary filter, and BEFORE THIS ROUND IT COULD ONLY EVER BE EMPTY: the run
row rolled back with the work it described.

Historical rows are not guessed: nothing distinguishes the two MED-RT writers, so
they carry the literal 'unattributed' and age out by being older."
```

---

### Task 2: `provenance.py`, the six orchestrators, and the crash #16 is about

**Files:**
- Create: `src/drugref/provenance.py`
- Create: `tests/test_provenance.py`
- Modify: all six orchestrators (replace the inline INSERT and the `finished_at` UPDATE)
- Modify: `src/drugref/ingest/chebi.py` (gains the try/rollback/logging the other five have)

**Interfaces:**
- Consumes: Task 1's `writer` column and its vocabulary.
- Produces: `provenance.open_run(conn, *, source, upstream_release, source_checksum, writer) -> int`
  (COMMITS), `provenance.finish_run(conn, run_id) -> None` (does NOT commit), `provenance.WRITERS: tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_provenance.py`:

```python
# tests/test_provenance.py
"""The run record: one writer, and a crash that leaves a trace (#16).

THE ASYMMETRY IS THE DESIGN and is what these tests exist to hold:

* open_run COMMITS, because the row has to outlive the rollback of the work it
  describes. Without that commit, `finished_at IS NULL` asserts a state that can
  never be observed and a crashed run is indistinguishable from one that never ran.
* finish_run does NOT commit, because the stamp must land in the same transaction as
  the work. A separate commit would let `finished` become true about data that is not
  there -- the same failure one line further down.
"""
import pathlib

import psycopg
import pytest

from drugref import provenance
from drugref.ingest import run

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")
XW = DATA / "usan_inn_crosswalk.tsv"
AL = DATA / "legacy_allowlist.tsv"

SRC = pathlib.Path("src/drugref")


@pytest.fixture(autouse=True)
def _clean(conn):
    """These tests commit (that is the point), so the conn fixture's rollback cannot
    isolate them."""
    conn.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                 "drugref.moiety_admission, drugref.open_question, "
                 "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


def test_open_run_commits_the_row_immediately(conn, _migrated):
    """Visible from ANOTHER connection before any work happens -- the property the
    whole issue turns on, and one a same-connection SELECT could not prove."""
    run_id = provenance.open_run(conn, source="UNII", upstream_release="r1",
                                 source_checksum="sum", writer="unii_run")

    with psycopg.connect(_migrated) as other:
        assert other.execute(
            "SELECT upstream_release FROM drugref.ingest_run WHERE ingest_run_id = %s",
            (run_id,)).fetchone() == ("r1",)


def test_finish_run_does_not_commit(conn, _migrated):
    """The stamp belongs to the work's transaction. If finish_run committed, a run
    could be marked finished and then have its work rolled back underneath it."""
    run_id = provenance.open_run(conn, source="UNII", upstream_release="r1",
                                 source_checksum="sum", writer="unii_run")
    provenance.finish_run(conn, run_id)

    with psycopg.connect(_migrated) as other:
        assert other.execute(
            "SELECT finished_at FROM drugref.ingest_run WHERE ingest_run_id = %s",
            (run_id,)).fetchone() == (None,)

    conn.commit()
    assert conn.execute(
        "SELECT finished_at FROM drugref.ingest_run WHERE ingest_run_id = %s",
        (run_id,)).fetchone()[0] is not None


def test_a_crashed_ingest_leaves_its_run_row_behind(conn, _migrated, monkeypatch):
    """#16 IN ONE ASSERTION, and it cannot pass before this task.

    Reproduced the way the issue describes it: raise before the work commits, then
    look from a FRESH session. The work is gone; the provenance is not.
    """
    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr("drugref.questions.register_from_gaps", boom)

    with pytest.raises(RuntimeError, match="simulated crash"):
        run.ingest_unii(conn, unii_path=FIX, crosswalk_path=XW, allowlist_path=AL,
                        upstream_release="2026-07")

    with psycopg.connect(_migrated) as other:
        assert other.execute(
            "SELECT count(*) FROM drugref.substance_moiety").fetchone()[0] == 0
        assert other.execute(
            "SELECT source, writer, finished_at FROM drugref.ingest_run").fetchall() \
            == [("UNII", "unii_run", None)]
        assert other.execute(
            "SELECT count(*) FROM drugref.ingest_run_incomplete").fetchone()[0] == 1


def test_a_successful_ingest_leaves_a_finished_run(conn, _migrated):
    """The other half of the partition: nothing in ingest_run_incomplete, one row in
    loaded_release. Without this, "the crash test passes" could mean "no run is ever
    stamped"."""
    run.ingest_unii(conn, unii_path=FIX, crosswalk_path=XW, allowlist_path=AL,
                    upstream_release="2026-07")

    with psycopg.connect(_migrated) as other:
        assert other.execute(
            "SELECT count(*) FROM drugref.ingest_run_incomplete").fetchone()[0] == 0
        assert other.execute(
            "SELECT source, writer, upstream_release FROM drugref.loaded_release"
        ).fetchall() == [("UNII", "unii_run", "2026-07")]


# ---- the one-writer contract -------------------------------------------------


def _sources():
    return sorted(SRC.rglob("*.py"))


def test_only_provenance_writes_a_run_record():
    """One reader, one clear, one checksum -- and now ONE RUN RECORD (#40, #43).

    Six modules wrote these four lines by hand, and the fix for #16 had to be made in
    all six or in none. Restated as a grep rather than by importing anything, for the
    same reason test_source_clear_contract restates each writer's table tuple: driving
    the expectation off the code under test would pass whatever that code said.
    """
    writers = [p for p in _sources()
               if "INSERT INTO drugref.ingest_run" in p.read_text()]
    assert [p.name for p in writers] == ["provenance.py"]


def test_only_provenance_stamps_a_run_finished():
    """The other half of the record. A module that stamped finished_at itself could
    mark a run complete without the work being committed, which is the exact failure
    finish_run's no-commit contract exists to prevent."""
    stampers = [p for p in _sources() if "SET finished_at" in p.read_text()]
    assert [p.name for p in stampers] == ["provenance.py"]


def test_the_writer_vocabulary_matches_the_database(conn):
    """provenance.WRITERS and db/025's CHECK are a PAIR (db/020's source-trio lesson,
    one table over): a value admitted to one and not the other is either refused at
    write time or invisible to the contract above. Restated here independently."""
    assert provenance.WRITERS == (
        "unii_run", "chebi", "medrt_run", "mesh_run", "mesh_rel_run", "pbs_run",
        "curation", "unattributed")
    for writer in provenance.WRITERS:
        conn.execute("INSERT INTO drugref.ingest_run "
                     "(source, upstream_release, source_checksum, writer) "
                     "VALUES ('UNII', 'r1', 'sum', %s)", (writer,))
    conn.rollback()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_provenance.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.provenance'`.

- [ ] **Step 3: Write `src/drugref/provenance.py`**

```python
# src/drugref/provenance.py
"""The one place a run record is written (#16).

WHY THIS MODULE EXISTS. Six orchestrators hand-wrote the same four lines -- INSERT the
ingest_run row, do the work, UPDATE finished_at, commit -- and every one of them wrote
the row INSIDE the transaction that did the work. A crashed run therefore rolled its
own provenance away, so `finished_at IS NULL` ("started, never finished") asserted a
state that could never be observed. Fixing that in six places is six chances to fix it
in five, which is the same argument that collapsed the per-source clear, the MeSH
reader and the checksum into one place each (#40, #43).

THE ASYMMETRY BETWEEN THE TWO FUNCTIONS IS THE WHOLE DESIGN. Read them together:
open_run commits, finish_run does not, and neither is free to change.
"""
import psycopg

# The writers db/025's CHECK admits, restated in Python because a value has to be
# spelled in both places to be usable. They are a PAIR: extend this tuple and the
# CHECK together, exactly as db/020's source trio must be extended together. A value
# in one and not the other is either refused at write time (Python-only) or invisible
# to callers (database-only).
#
# `curation` is not an orchestrator: it covers a DRUGREF-sourced run opened by a
# curator writing to Plan C's overlay tier. `unattributed` is historical only -- rows
# written before db/025, when two orchestrators shared a source and nothing told them
# apart -- and no code should ever write it.
WRITERS = ("unii_run", "chebi", "medrt_run", "mesh_run", "mesh_rel_run", "pbs_run",
           "curation", "unattributed")


def open_run(conn: psycopg.Connection, *, source: str, upstream_release: str,
             source_checksum: str, writer: str) -> int:
    """Open a run record and COMMIT it in its own transaction. Returns its id.

    THE COMMIT IS THE FEATURE, not an implementation detail: the row has to outlive
    the rollback of the work it describes, or a crashed ingest leaves no trace at all.
    After this returns, the caller is in a FRESH transaction and everything it does
    from here is the work -- which rolls back on failure, leaving this row standing
    with finished_at NULL and ingest_run_incomplete able to report it.

    TRANSACTION OWNERSHIP, and the contract this tightens: an orchestrator now takes
    TWO transactions on one connection. A caller with pending work has it committed at
    this boundary. Callers were already required to commit their own work before
    calling an orchestrator, so this narrows an existing rule rather than adding one --
    but it is the sort of narrowing that is silent when broken, hence this paragraph.

    `writer` is required and keyword-only: it says WHICH orchestrator this is, as
    distinct from the authority `source` names. One source can have two writers
    (MED-RT does), so a release is only unambiguous per (source, writer).
    """
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, %s, %s, %s) RETURNING ingest_run_id",
        (source, upstream_release, source_checksum, writer)).fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn: psycopg.Connection, run_id: int) -> None:
    """Stamp the run finished. DOES NOT COMMIT -- deliberately, and read this first.

    The stamp must land in the SAME transaction as the work it describes, so the
    orchestrator's own final commit publishes both atomically. Committing here would
    let `finished_at` become true about work that is subsequently rolled back: a
    consumer reading loaded_release would be told a release had landed while the
    projection still held the previous one. That is the exact failure open_run's early
    commit exists to expose, re-created one function later.

    Symmetry with open_run would therefore be a bug, not a tidy-up.
    """
    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() "
                 "WHERE ingest_run_id = %s", (run_id,))
```

- [ ] **Step 4: Migrate the six orchestrators onto it**

In each of `run.py`, `chebi.py`, `medrt_run.py`, `mesh_run.py`, `mesh_rel_run.py`, `pbs_run.py`:

1. `from drugref import provenance` (alongside the existing `drugref` imports).
2. Replace the inline INSERT with, e.g. for `medrt_run.py`:

```python
    run_id = provenance.open_run(conn, source=SOURCE, upstream_release=upstream_release,
                                 source_checksum=checksum(medrt_path), writer=WRITER)
```

3. Replace the inline `UPDATE ... finished_at` with `provenance.finish_run(conn, run_id)`, leaving the
   `conn.commit()` that follows it exactly where it is.

4. Update the transaction-ownership paragraph in each public entry point's docstring. Use this wording (it
   is the same fact in all six, so keep it identical):

```
    TRANSACTION OWNERSHIP: TWO transactions on one connection. provenance.open_run
    commits the run record first, so a crash leaves it standing with finished_at NULL
    (ingest_run_incomplete reports it); everything after that is the work, which this
    function owns, commits on success, and rolls back before re-raising. A caller with
    pending work has it committed at the provenance boundary, so callers must commit
    their own work before calling.
```

- [ ] **Step 5: Give `chebi.py` the shape the other five have**

`enrich_from_chebi` predates the foundation review and has no try/rollback and no logging. Split it the way
the other five are split — a public entry point that is only the transaction/logging boundary, and a private
body:

```python
import logging

log = logging.getLogger(__name__)

SOURCE = "CHEBI"
WRITER = "chebi"


def enrich_from_chebi(conn: psycopg.Connection, *, chebi_path,
                      upstream_release: str) -> int:
    """Add a CHEBI claim to every moiety whose INCHIKEY matches a ChEBI row.

    Returns the number of CHEBI claims newly added (idempotent on re-run).

    TRANSACTION OWNERSHIP: TWO transactions on one connection. provenance.open_run
    commits the run record first, so a crash leaves it standing with finished_at NULL
    (ingest_run_incomplete reports it); everything after that is the work, which this
    function owns, commits on success, and rolls back before re-raising. A caller with
    pending work has it committed at the provenance boundary, so callers must commit
    their own work before calling.
    """
    log.info("ChEBI enrichment starting (release=%s)", upstream_release)
    try:
        added = _enrich_from_chebi(conn, chebi_path, upstream_release)
    except Exception:
        conn.rollback()
        log.exception("ChEBI enrichment failed (release=%s); transaction rolled back",
                      upstream_release)
        raise
    log.info("ChEBI enrichment finished (release=%s): %d claims added",
             upstream_release, added)
    return added


def _enrich_from_chebi(conn: psycopg.Connection, chebi_path,
                       upstream_release: str) -> int:
    """The body of one ChEBI enrichment (see enrich_from_chebi for the contract)."""
```

Move the existing body verbatim into `_enrich_from_chebi`, with the `open_run`/`finish_run` calls from Step 4.
Also replace its hand-rolled `hashlib.sha256(...read_bytes())` with `checksum(chebi_path)` from
`drugref.ingest.checksum` — the shared digest #43 established, which this module never adopted; the digests
are identical (see that module's docstring), so no provenance already on disk changes.

- [ ] **Step 6: Run the whole suite**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`

Expected: PASS, **761** tests (754 + 7 new). If `test_only_provenance_writes_a_run_record` fails, one
orchestrator still has its inline SQL; the assertion names the offending file.

- [ ] **Step 7: Lint and commit**

```bash
ruff check src tests
git add src/drugref/provenance.py src/drugref/ingest tests/test_provenance.py
git commit -m "fix(ingest): commit the run record before the work, in one place (#16)

Six orchestrators wrote the ingest_run row inside the transaction that did the
work, so a crash rolled the provenance away and finished_at's 'started, never
finished' state could never be observed.

provenance.open_run commits the row in its own transaction -- the commit IS the
feature -- and finish_run deliberately does NOT commit, so the stamp lands with
the work and 'finished' can never be true about data that is not there.

An orchestrator now takes two transactions on one connection; the contract is
restated in all six docstrings. chebi.py, the one orchestrator the foundation
review missed, gains the try/rollback/logging the others have and the shared
checksum() it never adopted.

Pinned by a crash test that cannot pass without the early commit, and by a
contract test: one reader, one clear, one checksum -- and now one run record."
```

---

### Task 3: `db/026` — the fourth `reason`, and the tie-break that was resting on the alphabet (#47)

**Files:**
- Create: `db/026_contraindication_class_reason.sql`
- Modify: `src/drugref/classes.py:219-233` (the reason constants)
- Modify: `src/drugref/ingest/medrt_run.py` (clear both buckets; persist the CI subjects)
- Modify: `tests/test_source_clear_contract.py` (the reason vocabulary is restated there)
- Modify: `tests/test_medrt_run.py` (the new bucket's rows)

**Interfaces:**
- Consumes: nothing from Tasks 1–2 beyond a working suite.
- Produces: `classes.CONTRAINDICATION_CLASS = "contraindication_class"`, added to `classes.REASONS`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_medrt_run.py` (it already has the fixture ingest helpers and an autouse truncate):

```python
def test_the_ci_subjects_no_moiety_carries_are_persisted(conn):
    """#47. medrt_run built this set and reported it only as a summary integer.

    That is the shape db/008 exists to prevent: a count answers "how many drugs can we
    not speak about", only the identities answer "which ones". The fixture's ibuprofen
    is the subject of a CI rule and is carried by no moiety, so it lands here.
    """
    summary = _ingest(conn)

    rows = conn.execute(
        "SELECT rxcui FROM drugref.ingest_unmatched_ingredient "
        "WHERE reason = 'contraindication_class' ORDER BY rxcui").fetchall()
    assert rows
    assert len(rows) == summary.unmatched_ci_rxcuis


def test_each_medrt_bucket_is_cleared_by_its_own_writer(conn):
    """db/018's invariant, one bucket wider: EXACTLY ONE WRITER PER (source, reason).

    A re-ingest must rebuild both of medrt_run's buckets and neither of the MeSH-keyed
    run's -- if the new bucket were not cleared, the worklist would grow by its own
    length on every ingest with nothing failing.
    """
    _ingest(conn)
    first = conn.execute(
        "SELECT reason, count(*) FROM drugref.ingest_unmatched_ingredient "
        "GROUP BY reason ORDER BY reason").fetchall()
    _ingest(conn)
    second = conn.execute(
        "SELECT reason, count(*) FROM drugref.ingest_unmatched_ingredient "
        "GROUP BY reason ORDER BY reason").fetchall()

    assert first == second
```

Create the tie-break test in `tests/test_gap_views.py`:

```python
def test_the_unmatched_gap_prefers_the_row_that_carries_a_name(conn, ingest_run_id):
    """db/026. The tie-break must state its own reason, not coincide with it.

    db/018 widened this ORDER BY to `rxcui, ingest_run DESC, reason` anticipating #47,
    and its comment justified `classification` winning "alphabetically" and "by being
    the bucket with a name". BOTH justifications were wrong by the time #47 arrived:
    `contraindication_class` was very nearly named `class_contraindication`, which
    sorts BEFORE `classification`, and measured on the real release NO row in ANY
    bucket carries a name at all.

    So the view now prefers a named row explicitly. THE RELEASE CANNOT EXERCISE THIS
    -- medrt_run passes no names -- so it is pinned on controlled input, the shape
    #42 established for the descriptor-wins tie-break.
    """
    for reason, name in (("contraindication_class", None), ("classification", "ibuprofen")):
        conn.execute(
            "INSERT INTO drugref.ingest_unmatched_ingredient "
            "(ingest_run, rxcui, name, reason) VALUES (%s, '99999', %s, %s)",
            (ingest_run_id, name, reason))

    assert conn.execute(
        "SELECT name FROM drugref.gap_unmatched_ingredient "
        "WHERE rxcui = '99999'").fetchall() == [("ibuprofen",)]
```

And update the restated vocabulary in `tests/test_source_clear_contract.py` by adding:

```python
def test_the_reason_vocabulary_is_what_it_should_be():
    """Restated independently, like every writer's table tuple above. A fourth value
    was added by #47; EXACTLY ONE WRITER PER (source, reason) is what makes them safe,
    so a value appearing here without a writer -- or a writer sharing one -- is the
    defect this pins."""
    assert classes.REASONS == ("classification", "contraindication", "indication",
                               "contraindication_class")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_medrt_run.py tests/test_gap_views.py tests/test_source_clear_contract.py -v`

Expected: FAIL — the medrt_run tests find no `contraindication_class` rows, the gap test violates the
`reason` CHECK, the vocabulary test sees a 3-tuple.

- [ ] **Step 3: Write the migration**

Create `db/026_contraindication_class_reason.sql`:

```sql
-- db/026_contraindication_class_reason.sql
-- #47: persist the CI subjects medrt_run counts and discards, and re-cut the gap
-- view's tie-break so it states its own reason instead of coinciding with it.
--
-- WHY A FOURTH VALUE AND NOT A SHARED ONE. db/018's invariant is EXACTLY ONE WRITER
-- PER (source, reason): the value scopes a DELETE, so two writers sharing a bucket
-- makes the worklist depend on which ran last -- #39, exactly, with nothing to notice
-- it. medrt_run's CI subjects are its own bucket; `contraindication` belongs to
-- mesh_rel_run's MeSH-keyed rules and stays there.
--
-- THE NAME IS NOT THE ONE THE ISSUE PROPOSED, and the difference is load-bearing.
-- #47 suggests `class_contraindication`. Measured against the live database, that
-- string sorts BEFORE `classification` (`_` precedes `i` under C collation, and the
-- punctuation-insensitive pass of en_US.UTF-8 compares `classc...` against
-- `classi...`) -- so the one value the issue proposed is the one value that inverts
-- the tie-break db/018 wrote to protect it. `contraindication_class` sorts after.
ALTER TABLE drugref.ingest_unmatched_ingredient
    DROP CONSTRAINT IF EXISTS ingest_unmatched_ingredient_reason;
ALTER TABLE drugref.ingest_unmatched_ingredient
    ADD CONSTRAINT ingest_unmatched_ingredient_reason
    CHECK (reason IN ('classification', 'contraindication', 'indication',
                      'contraindication_class'));

COMMENT ON COLUMN drugref.ingest_unmatched_ingredient.reason IS
    'WHY this RxCUI is on the worklist, and -- because the clear is scoped on it -- '
    'WHICH writer owns the row. FOUR values, TWO writers, each owning TWO buckets: '
    'medrt_run owns `classification` (an ingredient the release classifies) and, '
    'since db/026, `contraindication_class` (the subject of a CI_MoA/CI_PE rule); '
    'mesh_rel_run owns `contraindication` and `indication`. '
    'NO DEFAULT, DELIBERATELY: a writer that does '
    'not declare its reason must fail, not inherit somebody else''s bucket. EXACTLY '
    'ONE WRITER PER (source, reason) -- add a value here rather than sharing one, or '
    'the clears collide again exactly as medrt_run''s and the MeSH-keyed run''s did.';

-- ============================================================================
-- The tie-break, re-cut to say what it means
-- ============================================================================
-- db/018 widened this ORDER BY to (rxcui, ingest_run DESC, reason) EXPLICITLY
-- anticipating #47, and justified it twice. Both justifications failed on measurement:
--
--   1. "`classification` wins alphabetically" -- true only for the three values that
--      existed, and #47's own proposed name would have inverted it.
--   2. "and by being the bucket with a `name`" -- measured on the real releases,
--      0 of 4,389 rows carry a name in ANY bucket. medrt_run passes no names mapping,
--      so the intended discriminator has never once had a value.
--
-- 1,430 RxCUIs already sit in more than one bucket, so this tie-break is live on real
-- data today; it is simply unobservable, because every candidate row is identical in
-- every column the view projects. That is precisely the kind of latent choice that
-- becomes a bug the moment a source starts supplying names.
--
-- So state the intent: prefer a row that HAS a name. `(u.name IS NULL)` sorts false
-- before true, so a named row wins; `reason` remains as the final, now-decorative
-- settler so the ORDER BY still names a unique row for DISTINCT ON.
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
ORDER  BY u.rxcui, u.ingest_run DESC, (u.name IS NULL), u.reason;

COMMENT ON VIEW drugref.gap_unmatched_ingredient IS
    'Ingredients an upstream release names that no moiety in the registry carries -- '
    'every one is a drug drugref can say nothing about. Closes by itself when a moiety '
    'claims the RxCUI. Superseded identity claims do not count as carrying it. ONE ROW '
    'PER RxCUI, from the most recent run that reported it and, within that run, from a '
    'row that CARRIES A NAME (db/026, replacing db/018''s alphabetical accident): '
    'gap_key is an input to question_uuid, so two rows here would mint one question '
    'and register_from_gaps would over-report its own live count.';
```

- [ ] **Step 4: Add the constant and wire `medrt_run`**

In `src/drugref/classes.py`, extend the reason block (keep the existing comment, and add to it):

```python
CLASSIFICATION = "classification"    # medrt_run: an ingredient the release CLASSIFIES
CONTRAINDICATION = "contraindication"  # mesh_rel_run: the SUBJECT of a contraindication
INDICATION = "indication"            # mesh_rel_run: the SUBJECT of an indication
# medrt_run's OWN CI subjects (#47, db/026) -- the subject of a CI_MoA/CI_PE rule that
# no moiety carries. Its own bucket, never `contraindication`: that one is
# mesh_rel_run's, and sharing it is #39 with nothing to notice it. Named to sort AFTER
# `classification`, which the issue's own suggested `class_contraindication` did not --
# see db/026 for why that matters.
CONTRAINDICATION_CLASS = "contraindication_class"
REASONS = (CLASSIFICATION, CONTRAINDICATION, INDICATION, CONTRAINDICATION_CLASS)
```

In `src/drugref/ingest/medrt_run.py`, extend step 2's clear (after the existing
`clear_source_unmatched_ingredients(conn, SOURCE, class_writer.CLASSIFICATION)`):

```python
    #    BOTH of this run's buckets, since #47. medrt_run owns two: the ingredients
    #    the release classifies, and the subjects of its CI_MoA/CI_PE rules. Each is
    #    cleared by the writer that re-derives it, or the worklist grows by its own
    #    length on every ingest with nothing failing.
    class_writer.clear_source_unmatched_ingredients(
        conn, SOURCE, class_writer.CONTRAINDICATION_CLASS)
```

and persist the set after step 5's contraindication loop, before step 6:

```python
    # 5a. Persist WHICH CI subjects went unmatched, not merely how many (#47). The set
    #     is built above and was reported only as a summary integer -- exactly the
    #     shape db/008 exists to prevent. Every one of these is a drug MED-RT
    #     contraindicates that drugref cannot speak about. Measured on the 2026.07.06
    #     release, all 99 also reach the worklist through another writer's row, but
    #     that is a property of THIS release, not a guarantee: a release naming an
    #     ingredient it neither classifies nor mentions in a MeSH-keyed rule would drop
    #     the identity silently.
    class_writer.add_unmatched_ingredients(conn, sorted(unmatched_ci), run_id,
                                           class_writer.CONTRAINDICATION_CLASS)
```

Update `MedrtSummary.unmatched_ci_rxcuis`'s docstring line to say it is now persisted as well as counted.

- [ ] **Step 5: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_medrt_run.py tests/test_gap_views.py tests/test_source_clear_contract.py -v`

Expected: PASS.

- [ ] **Step 6: Verify the tie-break test can actually fail (mutation check)**

The release cannot exercise the branch, so prove the test is load-bearing rather than decorative — this is
the #42 discipline and is a required step, not an optional one:

```bash
# Temporarily revert the ORDER BY to db/018's form in db/026, drop the test database's
# schema so migrations re-apply, and confirm the tie-break test FAILS.
DRUGREF_TEST_DSN='...' uv run pytest tests/test_gap_views.py -k prefers_the_row -v
```

Expected: FAIL with `[(None,)] != [('ibuprofen',)]`. Then restore the `(u.name IS NULL)` clause and confirm
PASS. Record the result in the commit message.

- [ ] **Step 7: Run the whole suite, lint and commit**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check src tests
git add db/026_contraindication_class_reason.sql src/drugref tests/
git commit -m "fix(ingest): persist medrt_run's own CI subjects, and say what the tie-break means (#47)

medrt_run built the set of CI_MoA/CI_PE subjects no moiety carries and reported
it only as a summary integer -- the shape db/008 exists to prevent: a count says
how many drugs we cannot speak about, only the identities say which.

A FOURTH reason value, never a shared bucket (db/018: exactly one writer per
(source, reason)). NOT the issue's proposed 'class_contraindication': measured
against the live database, that string sorts BEFORE 'classification' and inverts
the very tie-break db/018 wrote to protect it.

And that tie-break's other justification was already false -- 0 of 4,389 rows
carry a name in any bucket, while 1,430 RxCUIs sit in more than one -- so it now
prefers a named row explicitly. The release cannot exercise that branch; pinned
on controlled input and verified by mutation."
```

---

### Task 4: The CLI — `migrate`, `status`, and one subcommand per orchestrator

**Files:**
- Create: `src/drugref/cli.py`
- Create: `tests/test_cli.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `provenance` (indirectly, through the orchestrators), `db.connect`, `db.apply_migrations`.
- Produces: `cli.main(argv: Sequence[str] | None = None) -> int`; `cli.IngestStep` (frozen dataclass with
  `name: str`, `inputs: tuple[tuple[str, str], ...]`, `runner: Callable`); `cli.STEPS: tuple[IngestStep, ...]`;
  `cli.build_parser() -> argparse.ArgumentParser`. Task 5 adds `resolve_inputs` and the chain on top of these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
# tests/test_cli.py
"""The CLI: the first supported way to run an ingest outside a test (#16).

The parser and the step table are PURE -- no database, no filesystem -- so most of
this module runs anywhere. Only the end-to-end test is DB-gated.
"""
import pathlib

import pytest

from drugref import cli

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"


def test_every_orchestrator_has_a_subcommand():
    """A step table restated independently -- the shape test_source_clear_contract
    uses. Driving this off cli.STEPS would pass whatever cli.STEPS said; the point is
    that an orchestrator added without a subcommand fails here."""
    assert tuple(s.name for s in cli.STEPS) == (
        "unii", "chebi", "medrt", "mesh", "mesh-relations", "pbs")


def test_the_step_order_is_the_dependency_order():
    """UNII first because every other feed joins to moieties it registers; MED-RT
    before mesh-relations because the MeSH-keyed run reads classes medrt_run writes.
    Order is a property of the data, so it is a constant rather than an argument a
    caller could get wrong."""
    names = [s.name for s in cli.STEPS]
    assert names.index("unii") == 0
    assert names.index("medrt") < names.index("mesh-relations")


def test_ingest_subcommand_requires_a_release():
    """Provenance is stated, never guessed: a run with no upstream_release is a run
    whose coverage numbers cannot be compared to anything."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["ingest", "unii", "--unii", str(FIX)])


def test_ingest_subcommand_parses_its_paths():
    args = cli.build_parser().parse_args(
        ["ingest", "mesh", "--release", "2026", "--pa", "a.xml",
         "--desc", "b.gz", "--supp", "c.gz"])
    assert args.pa == pathlib.Path("a.xml")
    assert args.supp == pathlib.Path("c.gz")


def test_status_and_migrate_need_no_paths():
    assert cli.build_parser().parse_args(["status"]).handler is not None
    assert cli.build_parser().parse_args(["migrate"]).handler is not None


def test_main_reports_a_missing_dsn_without_a_traceback(capsys, monkeypatch):
    """An operator running this for the first time gets an actionable line, not a
    stack trace out of psycopg."""
    monkeypatch.delenv("DRUGREF_DSN", raising=False)
    assert cli.main(["status"]) == 2
    assert "DRUGREF_DSN" in capsys.readouterr().err


def test_ingest_unii_end_to_end(_migrated, monkeypatch, capsys):
    """One real ingest through the CLI, against the committed fixture."""
    monkeypatch.setenv("DRUGREF_DSN", _migrated)
    import psycopg
    with psycopg.connect(_migrated) as c:
        c.execute("TRUNCATE drugref.identity_claim, drugref.substance_moiety, "
                  "drugref.moiety_admission, drugref.open_question, "
                  "drugref.ingest_run RESTART IDENTITY CASCADE")
        c.commit()

    assert cli.main(["ingest", "unii", "--release", "2026-07", "--unii", str(FIX)]) == 0

    with psycopg.connect(_migrated) as c:
        assert c.execute(
            "SELECT source, writer, upstream_release FROM drugref.loaded_release"
        ).fetchall() == [("UNII", "unii_run", "2026-07")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_cli.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.cli'`.

- [ ] **Step 3: Write `src/drugref/cli.py`**

```python
# src/drugref/cli.py
"""The drugref command line: the first supported way to run an ingest (#16).

WHAT THIS MODULE IS AND IS NOT. It is a thin, feed-agnostic shell: argument parsing,
one connection, one call into an orchestrator. It holds NO ingest logic and no
knowledge of a feed's format -- that all lives in drugref.ingest, which is where a
parser belongs. The step table below is the single place that knows which
orchestrators exist and in which order they must run.

Everything above `main` is pure in the sense this codebase means it: no database
access, deterministic, testable with no fixtures.
"""
import argparse
import logging
import pathlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import drugref
from drugref import db
from drugref.ingest import chebi, medrt_run, mesh_rel_run, mesh_run, pbs_run, run

# The two closed seed files ship INSIDE the package (they are drugref's own curated
# data, not a download), so they are defaults rather than required arguments.
_DATA = pathlib.Path(drugref.__file__).resolve().parent / "data"
CROSSWALK = _DATA / "usan_inn_crosswalk.tsv"
ALLOWLIST = _DATA / "legacy_allowlist.tsv"

log = logging.getLogger("drugref")


@dataclass(frozen=True)
class IngestStep:
    """One orchestrator, as the CLI sees it.

    `inputs` pairs an ARGUMENT NAME with a GLOB relative to --downloads, and both
    consumers read the same tuple: the per-source subcommand turns each name into a
    required `--name PATH` flag, and the chain (task 5) resolves the same names by
    glob. One declaration, so a step cannot grow an input the chain does not know
    about.
    """
    name: str
    inputs: tuple[tuple[str, str], ...]
    runner: Callable[[object, dict[str, pathlib.Path], str], object]


def _run_unii(conn, paths, release):
    return run.ingest_unii(conn, unii_path=paths["unii"], crosswalk_path=CROSSWALK,
                           allowlist_path=ALLOWLIST, upstream_release=release)


def _run_chebi(conn, paths, release):
    return chebi.enrich_from_chebi(conn, chebi_path=paths["chebi"],
                                   upstream_release=release)


def _run_medrt(conn, paths, release):
    return medrt_run.ingest_medrt(conn, medrt_path=paths["medrt"],
                                  upstream_release=release)


def _run_mesh(conn, paths, release):
    return mesh_run.ingest_mesh(conn, pa_path=paths["pa"], desc_path=paths["desc"],
                                supp_path=paths["supp"], upstream_release=release)


def _run_mesh_relations(conn, paths, release):
    return mesh_rel_run.ingest_mesh_relations(
        conn, medrt_path=paths["medrt"], desc_path=paths["desc"],
        supp_path=paths["supp"], upstream_release=release)


def _run_pbs(conn, paths, release):
    return pbs_run.ingest_pbs(conn, paths["items"], release)


# ORDER IS THE DEPENDENCY ORDER and is a constant, not an argument: UNII first because
# every other feed joins to the moieties it registers, MED-RT before mesh-relations
# because the MeSH-keyed run reads classes medrt_run writes. A caller who could
# reorder these could produce a chain that looks like it worked and bridged nothing.
#
# The globs describe the layout a real downloads/ tree has, not a tidy one invented
# here: UNII_Records_*.txt sits at the root (NOT UNII_Names_*.txt -- a real file
# beside it carrying none of the moiety gate's four membership signals), MED-RT under
# MEDRT/ (extracted from Core_MEDRT_XML.zip by hand -- teaching this module to open
# archives would make it feed-aware for one feed's convenience), MeSH under mesh/,
# PBS under tables_as_csv/.
STEPS = (
    IngestStep("unii", (("unii", "UNII_Records_*.txt"),), _run_unii),
    IngestStep("chebi", (("chebi", "chebi*.tsv"),), _run_chebi),
    IngestStep("medrt", (("medrt", "MEDRT/Core_MEDRT_*_XML.xml"),), _run_medrt),
    IngestStep("mesh", (("pa", "mesh/pa*.xml"), ("desc", "mesh/desc*.gz"),
                        ("supp", "mesh/supp*.gz")), _run_mesh),
    IngestStep("mesh-relations", (("medrt", "MEDRT/Core_MEDRT_*_XML.xml"),
                                  ("desc", "mesh/desc*.gz"),
                                  ("supp", "mesh/supp*.gz")), _run_mesh_relations),
    IngestStep("pbs", (("items", "tables_as_csv/items.csv"),), _run_pbs),
)


def _handle_migrate(conn, args) -> int:
    db.apply_migrations(conn)
    print("migrations applied")
    return 0


def _handle_status(conn, args) -> int:
    """What is loaded, and what died trying. Two views, one command: an operator
    asking "is this current?" needs both halves, and reading only the first would
    report a stale release as healthy."""
    print("loaded releases:")
    for row in conn.execute(
            "SELECT source, writer, upstream_release, finished_at "
            "FROM drugref.loaded_release").fetchall():
        print("  {:<8} {:<14} {:<12} {}".format(*(str(c) for c in row)))

    incomplete = conn.execute(
        "SELECT ingest_run_id, source, writer, upstream_release, started_at "
        "FROM drugref.ingest_run_incomplete").fetchall()
    print("\nunfinished runs:" if incomplete else "\nunfinished runs: none")
    for row in incomplete:
        print("  #{} {:<8} {:<14} {:<12} started {}".format(*(str(c) for c in row)))
    return 0


def _handle_ingest(conn, args) -> int:
    step = args.step
    paths = {name: getattr(args, name.replace("-", "_")) for name, _ in step.inputs}
    summary = step.runner(conn, paths, args.release)
    print(f"{step.name}: {summary}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The whole command surface, built from STEPS so a new orchestrator needs one
    tuple entry rather than an edit in three places."""
    parser = argparse.ArgumentParser(
        prog="drugref", description="drugref.org reference-data service")
    parser.add_argument("--dsn", help="PostgreSQL DSN (default: $DRUGREF_DSN)")
    parser.add_argument("--log-level", default="info",
                        choices=("debug", "info", "warning", "error"))
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "migrate", help="apply every db/*.sql not yet applied"
    ).set_defaults(handler=_handle_migrate)
    commands.add_parser(
        "status", help="which release each writer last landed, and what died trying"
    ).set_defaults(handler=_handle_status)

    ingest = commands.add_parser("ingest", help="run one feed, or a chain of them")
    sources = ingest.add_subparsers(dest="source", required=True)
    for step in STEPS:
        sub = sources.add_parser(step.name, help=f"ingest one {step.name} release")
        sub.add_argument("--release", required=True,
                         help="the upstream release tag, recorded as provenance")
        for name, glob in step.inputs:
            sub.add_argument(f"--{name}", required=True, type=pathlib.Path,
                             help=f"path to the {name} file (chain glob: {glob})")
        sub.set_defaults(handler=_handle_ingest, step=step)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, connect, dispatch. Returns a process exit code.

    Takes `argv` so tests drive it by call rather than by subprocess -- a subprocess
    test would need a built package and would hide the traceback that makes a failure
    diagnosable.
    """
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        with db.connect(args.dsn) as conn:
            return args.handler(conn, args)
    except RuntimeError as exc:
        # db.connect's "no DSN" message is written for exactly this moment; a
        # traceback would bury it.
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
```

- [ ] **Step 4: Add the entry point**

In `pyproject.toml`, after the `dependencies` line:

```toml
[project.scripts]
drugref = "drugref.cli:main"
```

- [ ] **Step 5: Run the tests**

```bash
uv sync
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_cli.py -v
```

Expected: PASS (7 tests).

- [ ] **Step 6: Verify the installed script works**

```bash
uv run drugref --help
uv run drugref --dsn 'host=localhost port=5532 dbname=drugref_test user=postgres' status
```

Expected: the help text lists `migrate`, `status`, `ingest`; `status` prints the loaded releases and
`unfinished runs: none`.

- [ ] **Step 7: Run the whole suite, lint and commit**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check src tests
git add src/drugref/cli.py tests/test_cli.py pyproject.toml
git commit -m "feat(cli): a supported way to run an ingest, and to ask what is loaded (#16)

pyproject had no [project.scripts] and the only __main__ blocks in the repo were
fixture generators, so an ingest could be run from a test or a REPL and nowhere
else -- which also left the logging the orchestrators emit with nowhere to be
configured.

One console script: migrate, status, and one ingest subcommand per orchestrator,
all built from a single step table so a new feed is one tuple entry rather than
an edit in three places. status reads BOTH new views, because an operator asking
'is this current?' who sees only loaded_release reads a stale release as healthy."
```

---

### Task 5: `ingest chain` — the re-measure ritual as one command

**Files:**
- Modify: `src/drugref/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 4's `IngestStep`, `STEPS`, `build_parser`.
- Produces: `cli.InputResolutionError(Exception)`;
  `cli.resolve_inputs(downloads: pathlib.Path, step: IngestStep) -> dict[str, pathlib.Path]`;
  `cli.selected_steps(args: argparse.Namespace) -> tuple[tuple[IngestStep, str], ...]` returning
  `(step, release)` pairs in `STEPS` order.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_resolve_inputs_finds_each_file_by_its_glob(tmp_path):
    (tmp_path / "MEDRT").mkdir()
    (tmp_path / "MEDRT" / "Core_MEDRT_2026.07.06_XML.xml").write_text("x")

    step = next(s for s in cli.STEPS if s.name == "medrt")
    assert cli.resolve_inputs(tmp_path, step) == {
        "medrt": tmp_path / "MEDRT" / "Core_MEDRT_2026.07.06_XML.xml"}


def test_resolve_inputs_refuses_a_glob_that_matches_nothing(tmp_path):
    """A convention that silently matches nothing is worse than no convention: the
    chain would report success having ingested a feed it never read."""
    step = next(s for s in cli.STEPS if s.name == "medrt")
    with pytest.raises(cli.InputResolutionError) as exc:
        cli.resolve_inputs(tmp_path, step)
    assert "MEDRT/Core_MEDRT_*_XML.xml" in str(exc.value)
    assert str(tmp_path) in str(exc.value)


def test_resolve_inputs_refuses_an_ambiguous_glob(tmp_path):
    """Two releases in one directory is the normal way this goes wrong, and picking
    one would record the wrong bytes as provenance."""
    (tmp_path / "MEDRT").mkdir()
    for release in ("2026.05.04", "2026.07.06"):
        (tmp_path / "MEDRT" / f"Core_MEDRT_{release}_XML.xml").write_text("x")

    step = next(s for s in cli.STEPS if s.name == "medrt")
    with pytest.raises(cli.InputResolutionError) as exc:
        cli.resolve_inputs(tmp_path, step)
    assert "2 files" in str(exc.value)


def test_a_source_joins_the_chain_only_if_its_release_is_given():
    """No default set and no skip-list: supplying a release IS the opt-in, so a run
    can never quietly include a feed whose release tag nobody stated."""
    args = cli.build_parser().parse_args(
        ["ingest", "chain", "--downloads", "d",
         "--unii-release", "26Feb2026", "--medrt-release", "2026.07.06"])
    assert [(s.name, r) for s, r in cli.selected_steps(args)] == [
        ("unii", "26Feb2026"), ("medrt", "2026.07.06")]


def test_the_chain_runs_selected_steps_in_dependency_order():
    """Flags are given in any order; the chain is not."""
    args = cli.build_parser().parse_args(
        ["ingest", "chain", "--downloads", "d",
         "--pbs-release", "2026-07", "--unii-release", "26Feb2026"])
    assert [s.name for s, _ in cli.selected_steps(args)] == ["unii", "pbs"]


def test_the_chain_needs_at_least_one_release():
    args = cli.build_parser().parse_args(["ingest", "chain", "--downloads", "d"])
    assert cli.selected_steps(args) == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_cli.py -v`

Expected: FAIL — `AttributeError: module 'drugref.cli' has no attribute 'resolve_inputs'`.

- [ ] **Step 3: Implement the chain in `src/drugref/cli.py`**

Add above `build_parser`:

```python
class InputResolutionError(Exception):
    """A chain glob matched no file, or more than one.

    BOTH are errors, and the second is the one that bites: two releases left in one
    directory is the ordinary way this goes wrong, and silently taking either would
    record the wrong bytes as this run's provenance.
    """


def _release_flag(step: IngestStep) -> str:
    """`mesh-relations` -> `mesh_relations_release`, the argparse destination."""
    return f"{step.name.replace('-', '_')}_release"


def resolve_inputs(downloads: pathlib.Path,
                   step: IngestStep) -> dict[str, pathlib.Path]:
    """Resolve one step's inputs under `downloads`, by the globs it declares.

    GLOBS RATHER THAN FIXED NAMES, because the real layout is irregular and a tidy
    invented convention would match nothing: releases carry their version in the
    filename (UNII_Records_26Feb2026.txt, Core_MEDRT_2026.07.06_XML.xml) and a fixed
    name would go stale on the next download.
    """
    resolved = {}
    for name, pattern in step.inputs:
        matches = sorted(downloads.glob(pattern))
        if len(matches) != 1:
            raise InputResolutionError(
                f"{step.name}: expected exactly one file matching '{pattern}' under "
                f"{downloads}, found {len(matches)} files"
                + (f": {', '.join(m.name for m in matches)}" if matches else ""))
        resolved[name] = matches[0]
    return resolved


def selected_steps(args: argparse.Namespace) -> tuple[tuple[IngestStep, str], ...]:
    """The steps this chain invocation includes, in STEPS order, with their releases.

    SUPPLYING A RELEASE IS THE OPT-IN. No default set, no skip-list: a chain that ran
    feeds nobody named would record provenance nobody stated, and this project does
    not guess provenance. Returning them in STEPS order rather than flag order is what
    makes the dependency order unbreakable from the command line.
    """
    return tuple((step, getattr(args, _release_flag(step)))
                 for step in STEPS if getattr(args, _release_flag(step), None))


def _handle_chain(conn, args) -> int:
    steps = selected_steps(args)
    if not steps:
        print("drugref: no sources selected; pass at least one --<source>-release",
              file=sys.stderr)
        return 2

    # EVERY step's inputs are resolved BEFORE any step runs, so a typo fails in a
    # second rather than sixty. The feeds are rebuildable projections, so a half-run
    # chain is recoverable -- but an operator who has to notice that at all has been
    # failed by the tool.
    plan = [(step, release, resolve_inputs(args.downloads, step))
            for step, release in steps]
    for step, release, paths in plan:
        log.info("chain: %s (release=%s)", step.name, release)
        print(f"{step.name}: {step.runner(conn, paths, release)}")
    return 0
```

and register it inside `build_parser`, after the per-source loop:

```python
    chain = sources.add_parser(
        "chain", help="run several feeds in dependency order from one directory")
    chain.add_argument("--downloads", required=True, type=pathlib.Path,
                       help="directory holding the upstream releases")
    for step in STEPS:
        chain.add_argument(
            f"--{step.name}-release",
            help=f"include {step.name}, recording this release tag")
    chain.set_defaults(handler=_handle_chain)
```

Finally, catch the new error in `main` by widening the existing handler:

```python
    except (RuntimeError, InputResolutionError) as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_cli.py -v`

Expected: PASS (13 tests).

- [ ] **Step 5: Check `cli.py`'s length**

Run: `wc -l src/drugref/cli.py`

Expected: under 300. If it is over ~350, split the chain (`InputResolutionError`, `resolve_inputs`,
`selected_steps`, `_handle_chain`) into `src/drugref/cli_chain.py` and import it — the rule-4 threshold, not
a judgement call to defer.

- [ ] **Step 6: Run the whole suite, lint and commit**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check src tests
git add src/drugref/cli.py tests/test_cli.py
git commit -m "feat(cli): run the whole ingest chain in dependency order from one directory

Every round re-measures against the real releases by hand-assembling six calls in
the right order. That ritual is now a command, which is also what makes the next
round's measurement reproducible rather than reconstructed from prose.

Supplying a --<source>-release IS the opt-in: no default set and no skip-list,
because a chain that ran feeds nobody named would record provenance nobody
stated. Inputs resolve by documented globs against the REAL downloads layout,
and a glob matching zero OR several files is an error -- two releases left in
one directory is the ordinary way this goes wrong, and taking either silently
would record the wrong bytes. Every step's inputs are resolved before any step
runs."
```

---

### Task 6: Re-measure against the real releases, through the chain

No code. This is the step every round in this project performs, and skipping it is how five spec errors
reached `main` in earlier slices. **Use the new chain — dogfooding it is why it is in scope.**

**Files:** none modified (findings are recorded in Task 7).

- [ ] **Step 1: Rebuild the verification database**

`drugref_planc` carries the pre-round schema. A verification database is disposable — rebuild rather than
patch (HANDOVER's standing rule, learned when four scratch databases became permanently unmigratable):

```bash
psql "host=localhost port=5532 dbname=postgres user=postgres" -c 'DROP DATABASE IF EXISTS drugref_ops'
psql "host=localhost port=5532 dbname=postgres user=postgres" -c 'CREATE DATABASE drugref_ops'
uv run drugref --dsn 'host=localhost port=5532 dbname=drugref_ops user=postgres' migrate
```

- [ ] **Step 2: Extract the MED-RT XML (the one manual step the chain does not do)**

```bash
mkdir -p downloads/MEDRT && unzip -o -j downloads/MEDRT/Core_MEDRT_XML.zip \
  'Core_MEDRT_*_XML.xml' -d downloads/MEDRT/
ls downloads/MEDRT/Core_MEDRT_*_XML.xml
```

- [ ] **Step 3: Run the chain and time it**

```bash
time uv run drugref --dsn 'host=localhost port=5532 dbname=drugref_ops user=postgres' \
  ingest chain --downloads downloads \
  --unii-release 26Feb2026 --medrt-release 2026.07.06 \
  --mesh-release 2026 --mesh-relations-release 2026.07.06
```

Expected: ~110 s, matching the chain time Plan C measured. Record the actual figure.

- [ ] **Step 4: Check every prediction**

```bash
DSN='host=localhost port=5532 dbname=drugref_ops user=postgres'
psql "$DSN" -c "SELECT reason, count(*) FROM drugref.ingest_unmatched_ingredient GROUP BY 1 ORDER BY 1"
psql "$DSN" -Atc "SELECT count(*) FROM drugref.gap_unmatched_ingredient"
psql "$DSN" -Atc "SELECT count(*) FROM drugref.open_question WHERE is_current"
psql "$DSN" -Atc "SELECT count(*) FROM drugref.ddi_candidate_pair"
psql "$DSN" -c "SELECT gap_kind, count(*) FROM drugref.open_question WHERE is_current GROUP BY 1 ORDER BY 1"
psql "$DSN" -c "SELECT * FROM drugref.loaded_release"
psql "$DSN" -c "SELECT * FROM drugref.ingest_run_incomplete"
```

| | expected |
|---|---:|
| `contraindication_class` bucket | **99** |
| `classification` / `contraindication` / `indication` | **2,137** / **826** / **1,426**, unchanged |
| `gap_unmatched_ingredient` | **2,150**, unchanged |
| `open_question`, 11 kinds | **18,834**, unchanged |
| `ddi_candidate_pair` | **21,664**, unchanged |
| `loaded_release` | **4 rows** — UNII/unii_run, MED-RT/medrt_run, MeSH/mesh_run, MED-RT/mesh_rel_run |
| `ingest_run_incomplete` | **0 rows** |

**If the gap count or the question total moves, that is the finding, not a regression** — it would mean
#47's premise ("all 99 already reach the worklist through another writer") stopped holding for this release.
Investigate and record which RxCUIs are newly visible; do not "fix" it.

- [ ] **Step 5: Confirm `status` reads correctly**

```bash
uv run drugref --dsn "$DSN" status
```

Expected: four loaded releases, `unfinished runs: none`. **Note the two MED-RT rows** — that is the whole
argument for the `writer` column, visible on real data.

---

### Task 7: Documentation, and the PR

**Files:**
- Modify: `docs/HANDOVER.md`, `docs/ROADMAP.md`
- Modify: `docs-site/docs/decisions/` — only if Task 6 moved a published figure

- [ ] **Step 1: Update `docs/HANDOVER.md`**

Required edits, keeping the file **under 500 lines** (compress merged rounds to their traps):

1. `⇒ NEXT` — replace the stale "IN FLIGHT — Plan C" block: Plan C merged as **PR #57**; this round is the
   one in flight. Add the round to the merged list once the PR is open.
2. Add a compressed "ingest-operability round" section carrying **only the traps**:
   `open_run` commits and `finish_run` does not, and symmetry would be a bug; `writer` is NOT NULL with no
   DEFAULT and `'unattributed'` is a historical marker, not a writer; `loaded_release` is per
   `(source, writer)` and folding it onto `source` re-hides the MED-RT staleness split; the chain's globs
   error on zero *and* several matches; `gap_unmatched_ingredient`'s tie-break now states its own reason —
   db/018's alphabetical justification and its "the bucket with a name" justification were **both** false by
   the time #47 arrived (0 of 4,389 rows carry a name; 1,430 RxCUIs sit in more than one bucket).
3. Update the schema list with `025` and `026`.
4. Update the "How to run / test" section: the test count, and `drugref ingest chain` as the documented way
   to re-measure (replacing the hand-assembled sequence).
5. Update the database line: `drugref_ops` replaces `drugref_planc` as the current verification database.
6. Remove #16 and #47 from "Open follow-ups"; record the round's own residue if Task 6 found any.

- [ ] **Step 2: Update `docs/ROADMAP.md`**

Add an **✅ DONE** entry under "Cross-cutting hardening" for the ingest-operability round: what it settled
(connection ownership, one run record, the CLI), the two findings measuring #47 produced, and the measured
figures from Task 6. Keep it to the same density as the neighbouring entries and the file under 500 lines.

- [ ] **Step 3: Verify both documents**

```bash
wc -l docs/HANDOVER.md docs/ROADMAP.md    # both must be < 500
uv run mkdocs build --strict -f docs-site/mkdocs.yml
```

- [ ] **Step 4: Final full verification**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check src tests
```

Expected: all green. Record the final test count.

- [ ] **Step 5: Commit and open the PR**

```bash
git add docs/
git commit -m "docs: record the ingest-operability round, measured end to end"
git push -u origin fix/ingest-operability-round
gh pr create --base main --title "Ingest-operability round: a crashed run leaves a trace, and an ingest is runnable (#16, #47)" --body "$(cat <<'PRBODY'
Closes #16. Closes #47.

## What

Six orchestrators wrote their `ingest_run` row inside the transaction that did the
work, so a crash rolled the provenance away and `finished_at`'s "started, never
finished" state could never be observed. And `pyproject.toml` had no
`[project.scripts]`, so an ingest could be run from a test or a REPL and nowhere else.

* **`provenance.py`** is now the only writer of a run record. `open_run` commits its
  row in its own transaction — the commit *is* the feature — and `finish_run`
  deliberately does not, so the stamp lands with the work. An orchestrator now takes
  two transactions on one connection; the contract is restated in all six docstrings.
* **`db/025`** adds `ingest_run.writer`, because source `MED-RT` has two writers and a
  per-source view reports whichever finished last while the other half is a release
  behind — #39 one layer up. Plus `loaded_release` and `ingest_run_incomplete`, the
  latter of which **could only ever have been empty before this round**.
* **`db/026`** gives `medrt_run`'s CI subjects their own `reason` bucket (#47).
* **A `drugref` console script**: `migrate`, `status`, one `ingest` subcommand per
  orchestrator, and `ingest chain`, which runs them in dependency order and is how
  this round's own measurement was taken.

## Two things measuring #47 turned up

`db/018` widened `gap_unmatched_ingredient`'s tie-break *explicitly anticipating #47*
and justified it twice. Both justifications were false by the time #47 arrived:

1. "`classification` wins alphabetically" — `class_contraindication`, the value the
   issue itself proposes, is the one string that sorts **before** it. The value
   shipped is `contraindication_class`.
2. "and by being the bucket with a `name`" — measured on the real releases, **0 of
   4,389 rows carry a name in any bucket**, while **1,430 RxCUIs sit in more than
   one**. The tie-break is live on real data and simply unobservable.

The view now prefers a named row explicitly. The release cannot exercise that branch,
so it is pinned on controlled input and verified by mutation.

## For reviewers

* The `writer` column is `NOT NULL` with no `DEFAULT` (db/018's posture), which is why
  one commit touches 25 test modules: it cannot land separately from its writers.
* Historical rows carry the literal `'unattributed'` — nothing distinguishes the two
  MED-RT writers retrospectively, and inventing provenance is what this table exists
  to prevent.
* `chebi.py` was the one orchestrator the foundation review missed; it gains the
  try/rollback/logging the other five have and the shared `checksum()` it never
  adopted.
* Measured end to end against the real releases through the new chain. Every prior
  figure reproduces.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
PRBODY
)"
```

**Note on the closing keywords:** `Closes #16. Closes #47.` is correct here because this PR genuinely fixes
both. HANDOVER records that the sweep-closed-but-unfixed pattern has happened three times — so if any part
of either issue is *filed rather than fixed* by the final state, remove that keyword and describe the residue
in prose that cannot be parsed as a closing keyword.

---

## Self-Review

**Spec coverage.** §1 (provenance module, transaction contract, chebi, contract test) → Task 2. §2
(`writer`, both views, the `'unattributed'` backfill) → Task 1. §3 (CLI, chain, globs, pre-flight, purity) →
Tasks 4–5. §4 (fourth reason, both findings, mutation-pinned tie-break) → Task 3. §5 (crash test, contract
test, pure CLI tests, end-to-end, full re-measure with predictions) → Tasks 2, 4, 5, 6. §6's traps → Task 7's
HANDOVER edits. No spec section is unimplemented.

**Type consistency.** `open_run(conn, *, source, upstream_release, source_checksum, writer) -> int` and
`finish_run(conn, run_id) -> None` are used with those exact names in Tasks 2, 4 and 5.
`IngestStep(name, inputs, runner)` is defined in Task 4 and consumed unchanged in Task 5.
`classes.CONTRAINDICATION_CLASS` / the literal `'contraindication_class'` agree across the migration, the
constant, `medrt_run`, and three test modules. The writer vocabulary is spelled identically in `db/025`'s
CHECK, `provenance.WRITERS`, Task 1's Step 6 mapping table, and Task 6's expected `loaded_release` rows.

**Task-count arithmetic.** Task 1 adds 6 tests (754), Task 2 adds 7 (761), Task 3 adds 4 (765), Tasks 4–5
add 13 (778). Treat these as expectations to check, not as assertions — if the suite lands elsewhere, find
out why before proceeding.

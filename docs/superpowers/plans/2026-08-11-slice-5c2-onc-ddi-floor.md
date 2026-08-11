# Slice 5c.2 — the ONC high-priority DDI floor: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the ONC high-priority drug–drug interaction list as drugref's first curated clinical content, entering as a **second candidate source** (`source = 'ONCHIGH'`) that drugref's own graded judgements then sit on top of.

**Architecture:** `db/031` widens two CHECK vocabularies, adds one `ci_axis` row and one small projection table. A pure parser (`ingest/onchigh.py`) reads a committed TOML file; an orchestrator (`ingest/onchigh_run.py`) resolves identifiers to UUIDs, expands salt forms through the composition tree and delete-and-rebuilds the ONCHIGH candidate rows through the **existing** `interactions` writers. A separate operator command writes the append-only judgements. **db/029 and db/030 are not touched.**

**Tech Stack:** Python 3.12+, psycopg 3, PostgreSQL 18, `tomllib` (stdlib), pytest. No new dependency.

**Spec:** [`docs/superpowers/specs/2026-08-11-drugref-slice-5c2-onc-ddi-floor-design.md`](../specs/2026-08-11-drugref-slice-5c2-onc-ddi-floor-design.md)

## Global Constraints

- **TDD, always.** Failing test first, watch it fail for the *stated* reason, then the minimal code. A test that has never been observed failing is not a test (issues 74, 66, 76 and 5c.4's five-reviewer round were all this).
- **Migrations are immutable once applied.** `db/029` and `db/030` are merged and frozen. Everything here goes in **`db/031_onc_high_priority.sql`**. Never edit an applied migration.
- **Parsers are pure**, orchestrators own the transaction and are the only writers.
- **Files under ~500 lines.** `cli.py` is at 379; the new command group goes in its own `cli_curate.py`. Issue 89 is already open on two files that crossed the line.
- **No new dependency.** `tomllib` is stdlib at the `requires-python = ">=3.12"` floor.
- **Vocabulary lives in the database**, never restated in Python: `severity`, `evidence_grade`, `relationship` and `applies` are db/029 CHECKs, and an unrecognised value must surface as `CheckViolation` from Postgres.
- **Run the suite WITH the DSN** before any commit: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`. Never with `-k` or `--deselect`. Baseline at the start of this plan: **1297 passed**.
- **Lint:** `ruff check .` must pass.
- **Issue hygiene:** near `close`/`fix`/`resolve`, write the number **without** a `#` ("issue 65"). A `#65` there auto-closes the wrong thing — it has happened four times.
- **No verbatim text from either ONC paper** in any field, ever (rule 6, spec §10).

## File Structure

| File | Responsibility |
|---|---|
| `db/031_onc_high_priority.sql` | Create: the four schema changes. Widen two CHECKs, add the `CI_EPC` axis, add `ingest_unresolved_onc_endpoint` + its gap view + gap kind fifteen |
| `src/drugref/ingest/onchigh.py` | Create: **pure** parser. TOML → frozen dataclasses; structural validation; no DB, no network |
| `src/drugref/ingest/onchigh_run.py` | Create: orchestrator. Resolution, salt expansion, candidate rebuild, unresolved recording, `open_question` rebuild |
| `src/drugref/curation.py` | Modify: add `live_interaction_judgement()` so the curate step can compare before writing |
| `src/drugref/cli_curate.py` | Create: the `drugref curate onchigh` command group |
| `src/drugref/cli.py` | Modify: register the `onchigh` ingest step in `STEPS`; register the `curate` command group |
| `src/drugref/questions.py` | Modify: one `_GAP_SOURCES` entry for `unresolved_onc_endpoint` |
| `src/drugref/data/onc_high_priority.toml` | Create: the encoded list (Task 9, after clinical review) |
| `tests/fixtures/onc_fixture.toml` | Create: a small hand-written fixture exercising every structural rule |
| `tests/test_onchigh_parser.py` | Create: pure-parser tests, no DSN needed |
| `tests/test_onchigh_run.py` | Create: orchestrator tests — resolution, salt expansion, per-source isolation, gap registration |
| `tests/test_onchigh_read_path.py` | Create: the read path and per-source count guarantees |
| `tests/test_cli_curate.py` | Create: idempotence and supersession of the curate step |
| `NOTICE`, `docs-site/docs/decisions/the-onc-high-priority-floor.md` | Create/modify: the rule-6 determination (Task 1) |

---

### Task 1: The rule-6 determination, in writing

**Do this first.** Rule 6 is a blocker, not a cleanup item, and the determination currently exists only in a session memory file the next round cannot read.

**Files:**
- Create: `docs-site/docs/decisions/the-onc-high-priority-floor.md`
- Modify: `NOTICE`
- Modify: `docs-site/mkdocs.yml` (nav entry)

- [ ] **Step 1: Read the existing decision records for house style**

Run: `ls docs-site/docs/decisions/ && sed -n '1,40p' docs-site/docs/decisions/curating-a-drug-condition-pair.md`

These are *living* records — only decisions that currently stand.

- [ ] **Step 2: Write the record**

It must state, in prose a lawyer and a junior contributor can both follow:
- The pairs are **facts**, and facts are not copyrightable. drugref re-encodes the interactions, not the papers.
- **No verbatim text** from either paper enters any field. drugref authors every `mechanism` and `management` string, which is also why they are drugref's judgement rather than a quotation.
- Each entry cites its source paper, so a claim's provenance is inspectable.
- The ONC list was produced under public funding; the determination does **not** rest on that alone, because the facts argument stands on its own.
- What would change the answer: bundling the papers' own prose, or a compilation-copyright claim over the *selection*, neither of which this slice does.

- [ ] **Step 3: Add the NOTICE entry**

`NOTICE` attributes bundled reference-data sources. Add ONCHigh naming both papers and the re-encoding posture. Read the existing MED-RT and MeSH entries first and match their shape.

- [ ] **Step 4: Verify the docs site still builds**

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`
Expected: exit 0. `--strict` fails on a nav entry pointing at a missing file.

- [ ] **Step 5: Commit**

```bash
git add NOTICE docs-site/
git commit -m "docs: record the rule-6 determination for the ONC high-priority list"
```

---

### Task 2: `db/031` — the schema

**Files:**
- Create: `db/031_onc_high_priority.sql`
- Test: `tests/test_onchigh_schema.py`

**Interfaces:**
- Produces: `class_contraindication` accepting `source = 'ONCHIGH'`; `ingest_run` accepting `source = 'ONCHIGH'` and `writer = 'onchigh_run'`; `ci_axis` row `('CI_EPC', 'has_EPC', true)`; table `drugref.ingest_unresolved_onc_endpoint`; view `drugref.gap_unresolved_onc_endpoint`; gap kind `unresolved_onc_endpoint`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_onchigh_schema.py
"""db/031's four changes, each asserted for the reason it exists.

Every test here fails against db/030 alone, which is the point: a migration
whose absence no test notices is a migration nobody can safely re-order.
"""
import uuid
import pytest
import psycopg


def test_class_contraindication_admits_onchigh(conn, medrt_class, a_moiety, an_ingest_run):
    """The whole slice rests on this CHECK being widened."""
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'ONCHIGH', %s)",
        (a_moiety, medrt_class, an_ingest_run))
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_contraindication WHERE source = 'ONCHIGH'"
    ).fetchone()[0] == 1


def test_class_contraindication_still_refuses_an_unknown_source(conn, medrt_class,
                                                               a_moiety, an_ingest_run):
    """Widening a CHECK must not turn it into no CHECK at all."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.class_contraindication "
            "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
            "VALUES (%s, %s, 'CI_MoA', 'DRUGBANK', %s)",
            (a_moiety, medrt_class, an_ingest_run))


def test_ingest_run_admits_the_onchigh_source_and_writer(conn):
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) "
        "VALUES ('ONCHIGH', 'test', 'abc', 'onchigh_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    assert run_id


def test_ci_epc_axis_exists_and_expands(conn):
    """The axis maps CI_EPC onto has_EPC memberships, and expands descendants --
    65 of the 811 EPC classes have children, so expansion is not decorative."""
    row = conn.execute(
        "SELECT membership_relationship, expands_descendants FROM drugref.ci_axis "
        "WHERE relationship = 'CI_EPC'").fetchone()
    assert row == ("has_EPC", True)


def test_unresolved_endpoint_table_is_keyed_per_run_and_role(conn, an_ingest_run):
    """Two unresolved endpoints in ONE entry are two rows, not one that flickers:
    the subject and the object can each fail independently."""
    for role in ("subject", "object"):
        conn.execute(
            "INSERT INTO drugref.ingest_unresolved_onc_endpoint "
            "(ingest_run, source, entry_id, endpoint_role, identifier_scheme, "
            " identifier_value, endpoint_name) "
            "VALUES (%s, 'ONCHIGH', 'warfarin-nsaid', %s, 'UNII', 'ZZZZZZZZZZ', 'x')",
            (an_ingest_run, role))
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_unresolved_onc_endpoint").fetchone()[0] == 2


def test_open_question_admits_the_new_gap_kind(conn):
    conn.execute(
        "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, question_text) "
        "VALUES (%s, 'unresolved_onc_endpoint', 'ONCHIGH:x:UNII:Y', 'why?')",
        (uuid.uuid4(),))


def test_open_question_still_refuses_an_invented_gap_kind(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.open_question "
            "(question_uuid, gap_kind, gap_key, question_text) "
            "VALUES (%s, 'not_a_real_kind', 'k', 'why?')", (uuid.uuid4(),))
```

The fixtures `medrt_class`, `a_moiety` and `an_ingest_run` do not exist yet — write them in this test module (a MED-RT class, a gated-in moiety and an `ingest_run` row, each inserted directly). Check `tests/test_curated_overlay.py` first; if equivalent local fixtures already exist there, copy their shape rather than inventing a third.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_onchigh_schema.py -v`
Expected: FAIL. `test_class_contraindication_admits_onchigh` with `CheckViolation`, `test_ci_epc_axis_exists_and_expands` with the row being `None`, and the two table/gap-kind tests with `UndefinedTable` / `CheckViolation`.

- [ ] **Step 3: Write `db/031_onc_high_priority.sql`**

Four sections, each carrying the *reason* in a comment — the house style, and the thing that makes a migration reviewable years later:

1. `ALTER TABLE drugref.class_contraindication DROP CONSTRAINT class_contraindication_source`, then `ADD CONSTRAINT ... CHECK (source IN ('MED-RT', 'ONCHIGH'))`. Comment: 5c.1 keyed this table on `(subject, object, relationship, SOURCE)` and `curated_interaction`'s key omits `source` — the tier was designed for multiple authorities and this is the first one to arrive.
2. The same drop-and-add for `ingest_run_source` (adding `'ONCHIGH'` to `UNII, CHEBI, MED-RT, MeSH, PBS, DRUGREF, GSRS`) and `ingest_run_writer` (adding `'onchigh_run'` to `unii_run, chebi, medrt_run, mesh_run, mesh_rel_run, pbs_run, curation, unattributed, gsrs_run`). **Copy the existing lists verbatim from the catalog before editing** — retyping a vocabulary from memory is how a value goes missing.
3. `INSERT INTO drugref.ci_axis (relationship, membership_relationship, expands_descendants) VALUES ('CI_EPC', 'has_EPC', true) ON CONFLICT DO NOTHING;` Comment: measured — `Cyclooxygenase Inhibitors [MoA]` carries 56 members against `Nonsteroidal Anti-inflammatory Drug [EPC]`'s 21, but `Potassium-sparing Diuretic [EPC]` (2 members) has no usable MoA twin, so neither vocabulary subsumes the other.
4. `ingest_unresolved_onc_endpoint` mirroring `db/016`'s `ingest_unresolved_ci_object`: columns `ingest_run bigint NOT NULL REFERENCES drugref.ingest_run`, `source text NOT NULL CHECK (source = 'ONCHIGH')`, `entry_id text NOT NULL`, `endpoint_role text NOT NULL CHECK (endpoint_role IN ('subject','object'))`, `identifier_scheme text NOT NULL`, `identifier_value text NOT NULL`, `endpoint_name text`, PK `(ingest_run, source, entry_id, endpoint_role)`. Then `CREATE OR REPLACE VIEW drugref.gap_unresolved_onc_endpoint` over it joined to `ingest_run` for `upstream_release`, grouped so the grain matches the `gap_key` grain (db/017's lesson: a view grouped more finely than its key mints two rows that fold to one `question_uuid`). Then the gap-kind CHECK, replaced by the same idempotent `DO $$ ... IF NOT EXISTS (SELECT ... LIKE '%unresolved_onc_endpoint%') ...` pattern db/029 used, listing **all fifteen** kinds.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_onchigh_schema.py -v`
Expected: 7 passed. The `_migrated` fixture drops and recreates the schema per session, so the new migration applies automatically.

- [ ] **Step 5: Run the whole suite**

Run: `DRUGREF_TEST_DSN='...' uv run pytest`
Expected: 1304 passed (1297 + 7). **A failure here means db/031 disturbed an existing invariant — investigate, never re-baseline.**

- [ ] **Step 6: Commit**

```bash
git add db/031_onc_high_priority.sql tests/test_onchigh_schema.py
git commit -m "feat(db): db/031 admits ONCHIGH as a second candidate source"
```

---

### Task 3: The pure parser

**Files:**
- Create: `src/drugref/ingest/onchigh.py`
- Create: `tests/fixtures/onc_fixture.toml`
- Test: `tests/test_onchigh_parser.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class OncCandidate: subject_unii: str; subject_name: str; object_medrt_code: str; object_name: str; axis: str; citation: str`
  - `@dataclass(frozen=True) class OncJudgement: applies: bool; severity: str | None; evidence_grade: str | None; mechanism: str | None; management: str | None`
  - `@dataclass(frozen=True) class OncEntry: entry_id: str; candidate: OncCandidate; judgement: OncJudgement`
  - `class OncFormatError(ValueError)`
  - `def parse(path: pathlib.Path) -> tuple[OncEntry, ...]`
- Consumes: nothing. **No database, no network.** Every test in this task runs without a DSN.

- [ ] **Step 1: Write the fixture**

`tests/fixtures/onc_fixture.toml` — hand-written, **not** real ONC content (the real file arrives in Task 9). Three entries: one well-formed, one whose `axis` is unknown, one missing `severity` while `applies = true`. Use identifiers that exist in a test database so the orchestrator tests can reuse it.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_onchigh_parser.py
"""The ONC file's structural rules. PURE -- no DSN, no database.

Every rule here is one a hand-authored file gets wrong, and each is a RAISE
rather than a skip: the file is curated, so a malformed entry is a bug and a
silently-dropped entry is a clinical claim going missing (issue 71's lesson).
"""
import pathlib
import pytest
from drugref.ingest import onchigh

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "onc_fixture.toml"


def test_parses_a_well_formed_entry():
    entries = onchigh.parse(FIXTURE)
    entry = next(e for e in entries if e.entry_id == "warfarin-nsaid")
    assert entry.candidate.subject_unii == "5Q7ZVV76EI"
    assert entry.candidate.axis == "CI_EPC"
    assert entry.judgement.applies is True
    assert entry.judgement.severity == "major"


def test_an_unknown_axis_raises(tmp_path):
    """ci_axis is the vocabulary's one home, but the PARSER still refuses a value
    it can see is wrong -- the alternative is an ingest that fails halfway with
    half the file already written."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with(axis="CI_INVENTED"))
    with pytest.raises(onchigh.OncFormatError, match="CI_INVENTED"):
        onchigh.parse(bad)


def test_an_asserting_entry_without_severity_raises(tmp_path):
    """db/029's completeness CHECK would refuse this row anyway. Catching it in
    the parser means the curator learns WHICH ENTRY is wrong, by entry_id, rather
    than reading a constraint name off a traceback."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with(applies=True, severity=None))
    with pytest.raises(onchigh.OncFormatError, match="warfarin-nsaid"):
        onchigh.parse(bad)


def test_a_non_asserting_entry_carrying_a_grade_raises(tmp_path):
    """The other half of the same CHECK: 'not real, but graded major' must be
    unrepresentable, not merely discouraged."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with(applies=False, severity="major"))
    with pytest.raises(onchigh.OncFormatError):
        onchigh.parse(bad)


def test_a_duplicate_entry_id_raises(tmp_path):
    """entry_id is the handle a gap_key is built from, so two entries sharing one
    would mint a single question_uuid for two different gaps."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with() + "\n" + _entry_with())
    with pytest.raises(onchigh.OncFormatError, match="duplicate"):
        onchigh.parse(bad)


def test_a_missing_citation_raises(tmp_path):
    """Rule 6: a claim with no source is exactly what this slice must not ship."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with(citation=None))
    with pytest.raises(onchigh.OncFormatError, match="citation"):
        onchigh.parse(bad)
```

Write `_entry_with(**overrides)` as a module-level helper in the test file that emits one valid `[[entry]]` block with the named fields overridden or omitted. Keep it dumb string formatting — a helper that itself needs testing is not a helper.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_onchigh_parser.py -v` (no DSN needed — that is the proof the module is pure)
Expected: FAIL with `ModuleNotFoundError: No module named 'drugref.ingest.onchigh'`.

- [ ] **Step 4: Write the parser**

`parse()` opens the file with `tomllib.load` in binary mode, iterates `data["entry"]`, and builds the dataclasses. Every rule the tests name raises `OncFormatError` **naming the `entry_id`**. Do **not** validate `severity`/`evidence_grade` *values* here — those are db/029 CHECKs, and restating them in Python is the two-lists-that-drift defect db/006 was issued to fix. The parser validates **structure** (is a grade present when `applies` is true?), the database validates **vocabulary** (is `major` a legal severity?). Say that in the module docstring.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_onchigh_parser.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/onchigh.py tests/test_onchigh_parser.py tests/fixtures/onc_fixture.toml
git commit -m "feat(ingest): pure parser for the ONC high-priority list"
```

---

### Task 4: Identifier resolution and salt-form expansion

**Files:**
- Create: `src/drugref/ingest/onchigh_run.py` (resolution half only)
- Test: `tests/test_onchigh_run.py`

**Interfaces:**
- Consumes: `onchigh.OncEntry`, `onchigh.OncCandidate` from Task 3.
- Produces:
  - `@dataclass(frozen=True) class ResolvedEndpoint: entry_id: str; subject_moiety_uuids: tuple[uuid.UUID, ...]; object_class_uuid: uuid.UUID; axis: str`
  - `@dataclass(frozen=True) class UnresolvedEndpoint: entry_id: str; endpoint_role: str; identifier_scheme: str; identifier_value: str; endpoint_name: str`
  - `def resolve_entry(conn, entry: onchigh.OncEntry) -> ResolvedEndpoint | list[UnresolvedEndpoint]`
  - `def subject_forms(conn, base_moiety_uuid: uuid.UUID) -> tuple[uuid.UUID, ...]`

- [ ] **Step 1: Write the failing tests**

```python
def test_resolves_a_subject_by_unii_and_an_object_by_medrt_code(conn, seeded):
    entry = _entry(subject_unii="5Q7ZVV76EI", object_medrt_code="N0000175722")
    resolved = onchigh_run.resolve_entry(conn, entry)
    assert resolved.object_class_uuid == seeded.nsaid_class
    assert seeded.warfarin in resolved.subject_moiety_uuids


def test_a_name_disagreeing_with_its_identifier_raises(conn, seeded):
    """The name field is a REVIEW AID: a human reads it in the diff while the
    database reads the identifier. Let them disagree and the reviewer is
    approving a different substance from the one that lands."""
    entry = _entry(subject_unii="5Q7ZVV76EI", subject_name="aspirin")
    with pytest.raises(onchigh_run.EndpointMismatchError, match="warfarin"):
        onchigh_run.resolve_entry(conn, entry)


def test_an_unknown_unii_is_returned_as_unresolved_not_raised(conn, seeded):
    """A well-formed identifier naming a substance drugref does not hold is a
    COVERAGE GAP, not a bug in the file -- so it becomes data (gap kind
    fifteen), not an exception."""
    entry = _entry(subject_unii="ZZZZZZZZZZ", subject_name="notadrug")
    result = onchigh_run.resolve_entry(conn, entry)
    assert [u.endpoint_role for u in result] == ["subject"]
    assert result[0].identifier_value == "ZZZZZZZZZZ"


def test_both_endpoints_unresolved_yields_two_records(conn, seeded):
    entry = _entry(subject_unii="ZZZZZZZZZZ", object_medrt_code="N0000000000")
    assert len(onchigh_run.resolve_entry(conn, entry)) == 2


def test_subject_expands_to_its_salt_forms(conn, seeded):
    """A judgement on warfarin must reach a consumer holding warfarin sodium --
    a real product. MED-RT itself asserts per-form (it carries rules for both
    tranylcypromine and tranylcypromine sulfate)."""
    forms = onchigh_run.subject_forms(conn, seeded.warfarin)
    assert seeded.warfarin in forms
    assert seeded.warfarin_sodium in forms


def test_salt_expansion_admits_only_gated_in_moieties(conn, seeded):
    """A composition edge to a substance the moiety gate refused is not a
    subject drugref can write a rule about: class_contraindication's FK would
    reject it, and reaching the FK is the wrong place to find out."""
    forms = onchigh_run.subject_forms(conn, seeded.warfarin)
    assert seeded.ungated_warfarin_ester not in forms
```

Build a `seeded` fixture that inserts the moieties, UNII claims, MED-RT class, memberships and composition edges the tests name, returning them as a small dataclass of UUIDs. Insert it directly; do not run a real ingest.

- [ ] **Step 2: Run to verify failure**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_onchigh_run.py -v`
Expected: FAIL — `onchigh_run` has no `resolve_entry`.

- [ ] **Step 3: Implement resolution**

- Subject: `identity_claim` where `scheme = 'UNII'` and `superseded_by IS NULL`, joined to `substance_moiety`.
- Object: `substance_class` where `source = 'MED-RT'` and `source_code = %s`.
- Name check: compare the resolved `display_name` / `class_name` against the file's `name`, case-insensitively and whitespace-normalised; raise `EndpointMismatchError` on disagreement.
- `subject_forms`: the base moiety, plus every gated-in moiety whose UNII appears as `substance_composition.substance_unii` with `component_moiety = base` and `is_active_component IS TRUE`. Read `moiety_active_in_composite` rather than the base table where it serves. Return a deterministic order (sorted) — a nondeterministic order makes a diff of the candidate rows unreadable.

Document *why* salt expansion is here and not at read time, quoting the spec §6 reasons: issue 68's suspect ~19%, rebuild re-derivation, and the ~1.4 ms hot path.

- [ ] **Step 4: Run to verify pass**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_onchigh_run.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ingest/onchigh_run.py tests/test_onchigh_run.py
git commit -m "feat(ingest): resolve ONC endpoints and expand subject salt forms"
```

---

### Task 5: The candidate ingest

**Files:**
- Modify: `src/drugref/ingest/onchigh_run.py`
- Modify: `src/drugref/questions.py`
- Test: `tests/test_onchigh_run.py` (extend)

**Interfaces:**
- Consumes: `resolve_entry`, `subject_forms` (Task 4); `interactions.clear_source_contraindications(conn, source)`, `interactions.add_contraindication(conn, subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run_id) -> bool`, `provenance.open_run(conn, *, source, upstream_release, source_checksum, writer) -> int`, `provenance.finish_run(conn, run_id)`, `questions.register_from_gaps(conn, ingest_run_id)`.
- Produces:
  - `@dataclass(frozen=True) class OncSummary: entries_read: int; rules_written: int; salt_forms_expanded: int; endpoints_unresolved: int`
  - `def ingest_onchigh(conn, *, path: pathlib.Path, upstream_release: str) -> OncSummary`
  - `SOURCE = "ONCHIGH"`, `WRITER = "onchigh_run"`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_rebuild_replaces_only_this_sources_rows(conn, seeded, medrt_rows_present):
    """THE INVARIANT THIS SLICE LEANS ON HARDEST. A per-source rebuild that
    disturbed another source's rows would break the architecture invariant that
    makes multi-source candidates safe at all."""
    before = _count(conn, "MED-RT")
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="test-1")
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="test-2")
    assert _count(conn, "MED-RT") == before
    assert _count(conn, "ONCHIGH") == _expected_onchigh_rows


def test_an_unresolved_endpoint_becomes_a_question(conn, seeded):
    """Issue 71's lesson: a dropped row counted into a transient integer is a
    number nobody can act on. This one is a queryable, citable question."""
    summary = onchigh_run.ingest_onchigh(conn, path=FIXTURE_WITH_UNKNOWN,
                                         upstream_release="test")
    assert summary.endpoints_unresolved == 1
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_onc_endpoint' AND is_current").fetchone()[0] == 1


def test_the_question_uuid_is_stable_across_reruns(conn, seeded):
    """question_uuid is uuid5(gap_kind, gap_key) and external tooling cites it,
    so a second run must re-derive the SAME uuid, not mint a new one."""
    onchigh_run.ingest_onchigh(conn, path=FIXTURE_WITH_UNKNOWN, upstream_release="a")
    first = _question_uuids(conn)
    onchigh_run.ingest_onchigh(conn, path=FIXTURE_WITH_UNKNOWN, upstream_release="b")
    assert _question_uuids(conn) == first


def test_the_ingest_run_records_source_and_writer(conn, seeded):
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="ONCHigh-2015")
    row = conn.execute(
        "SELECT source, writer, upstream_release, finished_at IS NOT NULL "
        "FROM drugref.ingest_run WHERE source = 'ONCHIGH'").fetchone()
    assert row == ("ONCHIGH", "onchigh_run", "ONCHigh-2015", True)


def test_a_resolved_rule_reaches_the_worklist(conn, seeded):
    """An ONC rule is ungraded on arrival, so it must appear on the SAME
    worklist MED-RT's rules use -- no new view. That the worklist works
    unchanged for a second authority is the evidence the candidate tier really
    was designed for one."""
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="test")
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_uncurated_interaction_rule").fetchone()[0] > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_onchigh_run.py -v`
Expected: FAIL — no `ingest_onchigh`.

- [ ] **Step 3: Implement `ingest_onchigh` and the `_GAP_SOURCES` entry**

Sequence, mirroring `pbs_run` / `medrt_run`:
1. `checksum(path)`, then `provenance.open_run(...)` — which commits, on purpose, so a crashed ingest leaves a traceable row.
2. `onchigh.parse(path)`.
3. `interactions.clear_source_contraindications(conn, SOURCE)`.
4. Per entry: `resolve_entry`; on `ResolvedEndpoint`, `subject_forms` then one `interactions.add_contraindication(...)` per form; on unresolved, insert into `ingest_unresolved_onc_endpoint`. **Clear that table for this source in step 3 too** — a stale unresolved row would keep answering a question the file no longer asks.
5. `questions.register_from_gaps(conn, run_id)`.
6. `provenance.finish_run(conn, run_id)`, then `conn.commit()`.

The `_GAP_SOURCES` entry:

```python
"unresolved_onc_endpoint": {
    "view": "gap_unresolved_onc_endpoint",
    # FROZEN. question_uuid is uuid5(gap_kind, gap_key), so a later reformat
    # orphans every piece of curator work keyed to the old uuid.
    "key_sql": "'ONCHIGH:' || entry_id || ':' || identifier_scheme || ':' || identifier_value",
    "text_sql": (
        "'Does drugref hold ' || coalesce(endpoint_name, identifier_value) || "
        "' (' || identifier_scheme || ' ' || identifier_value || ')? The ONC "
        "high-priority list names it as the ' || endpoint_role || ' of entry ' || "
        "entry_id || ', and no drugref identity resolves it, so that "
        "interaction cannot be projected at all.'"),
},
```

- [ ] **Step 4: Run to verify pass**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_onchigh_run.py -v`
Expected: 11 passed.

- [ ] **Step 5: Run the whole suite**

Run: `DRUGREF_TEST_DSN='...' uv run pytest`
Expected: all pass. **Watch `tests/test_gap_views.py` and `tests/test_source_clear_contract.py` specifically** — both assert over the full set of gap kinds / cleared tables, and both *should* need updating for a fifteenth kind. If neither notices, that is a gate that does not fire: fix the test, and say so in the commit.

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/onchigh_run.py src/drugref/questions.py tests/
git commit -m "feat(ingest): rebuildable ONCHIGH candidate projection with gap kind fifteen"
```

---

### Task 6: Wire the ingest into the CLI

**Files:**
- Modify: `src/drugref/cli.py`
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: `onchigh_run.ingest_onchigh` (Task 5), `IngestStep` from `cli_chain`.

- [ ] **Step 1: Write the failing test**

```python
def test_onchigh_is_the_last_chain_step():
    """It needs moieties (UNII), MED-RT classes, and the composition tree
    (GSRS) for salt expansion, so it cannot run before any of them."""
    names = [s.name for s in cli.STEPS]
    assert names[-1] == "onchigh"
    assert names.index("gsrs") < names.index("onchigh")


def test_onchigh_declares_its_input():
    step = next(s for s in cli.STEPS if s.name == "onchigh")
    assert step.inputs == (("onc", "onc_high_priority.toml"),)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -k onchigh -v`
Expected: FAIL — `StopIteration` / no such step.

- [ ] **Step 3: Add the step**

A `_run_onchigh(conn, paths, release)` wrapper beside the other `_run_*` functions, and `IngestStep("onchigh", (("onc", "onc_high_priority.toml"),), _run_onchigh)` appended to `STEPS`. The committed file is drugref's own data, so — like `CROSSWALK` and `ALLOWLIST` — it defaults to the packaged `_DATA` path when `--onc` is not given.

- [ ] **Step 4: Run to verify pass, then the whole suite**

Run: `DRUGREF_TEST_DSN='...' uv run pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/drugref/cli.py tests/test_cli.py
git commit -m "feat(cli): register the onchigh ingest step"
```

---

### Task 7: The curate command — idempotent, append-only

**Files:**
- Create: `src/drugref/cli_curate.py`
- Modify: `src/drugref/curation.py`
- Modify: `src/drugref/cli.py` (register the group only)
- Test: `tests/test_cli_curate.py`

**Interfaces:**
- Consumes: `curation.record_interaction_judgement(conn, subject_moiety_uuid, object_class_uuid, relationship, applies, *, severity, mechanism, management, evidence_grade, question_uuid, source, reviewed_by, reviewed_against) -> int`; `onchigh.parse`; `onchigh_run.resolve_entry`, `subject_forms`.
- Produces:
  - `curation.live_interaction_judgement(conn, subject_moiety_uuid, object_class_uuid, relationship) -> dict | None`
  - `cli_curate.curate_onchigh(conn, *, path, reviewed_by, reviewed_against) -> CurateSummary`
  - `@dataclass(frozen=True) class CurateSummary: rules_seen: int; judgements_written: int; judgements_superseded: int; unchanged: int`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_first_run_writes_one_judgement_per_resolved_form(conn, seeded, ingested):
    summary = cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr X",
                                        reviewed_against="ONCHigh-2015")
    assert summary.judgements_written == _expected_forms
    assert summary.judgements_superseded == 0


def test_a_second_run_against_an_unedited_file_writes_nothing(conn, seeded, ingested):
    """Idempotent by COMPARISON, not by luck. The table is append-only, so a
    re-run that blindly inserted would write a permanent duplicate on every
    invocation -- and the deferred single-live trigger would only catch it at
    COMMIT, after the damage is described."""
    cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr X",
                              reviewed_against="ONCHigh-2015")
    conn.commit()
    second = cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr X",
                                       reviewed_against="ONCHigh-2015")
    assert second.judgements_written == 0
    assert second.unchanged == _expected_forms


def test_an_edited_grade_supersedes_rather_than_mutates(conn, seeded, ingested):
    """The previous grade survives as history -- which matters most for exactly
    the rows that fired an alert."""
    cli_curate.curate_onchigh(conn, path=FIXTURE, reviewed_by="Dr X",
                              reviewed_against="ONCHigh-2015")
    conn.commit()
    cli_curate.curate_onchigh(conn, path=FIXTURE_REGRADED, reviewed_by="Dr X",
                              reviewed_against="ONCHigh-2015")
    conn.commit()
    rows = conn.execute(
        "SELECT severity, superseded_by IS NULL FROM drugref.curated_interaction "
        "ORDER BY curated_interaction_id").fetchall()
    assert rows[0] == ("major", False)      # history, pointed at its successor
    assert rows[-1] == ("contraindicated", True)


def test_an_illegal_severity_reaches_the_database_check(conn, seeded, ingested):
    """No Python list of legal severities. db/006's lesson: two vocabularies
    kept in step by a comment drift the moment one is widened."""
    with pytest.raises(psycopg.errors.CheckViolation):
        cli_curate.curate_onchigh(conn, path=FIXTURE_BAD_SEVERITY,
                                  reviewed_by="Dr X", reviewed_against="x")
```

- [ ] **Step 2: Run to verify failure**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_cli_curate.py -v`
Expected: FAIL — no module `drugref.cli_curate`.

- [ ] **Step 3: Implement**

`live_interaction_judgement` selects the live row's graded fields for a natural key, or `None`. `curate_onchigh` parses, resolves, expands forms, and per resolved rule: no live row → write; live row differing in any graded field → `record_interaction_judgement` (which supersedes); identical → count as unchanged and write nothing.

Compare **only the graded fields** — `applies`, `severity`, `mechanism`, `management`, `evidence_grade`. `reviewed_at` moves every run and `reviewed_by` is an operator argument; including either would supersede the whole file on every invocation, which is the opposite of append-only discipline.

Register `curate` in `cli.py` as a command group delegating to `cli_curate`, matching how `cli_policy` and `cli_signing` are wired. **Do not put the implementation in `cli.py`** — it is at 379 lines and issue 89 is open on exactly this.

- [ ] **Step 4: Run to verify pass, then the whole suite**

Run: `DRUGREF_TEST_DSN='...' uv run pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/drugref/cli_curate.py src/drugref/curation.py src/drugref/cli.py tests/test_cli_curate.py
git commit -m "feat(cli): drugref curate onchigh, idempotent by comparison"
```

---

### Task 8: The read path, per-source counts, and the signing round-trip

**Files:**
- Test: `tests/test_onchigh_read_path.py`

No production code is expected in this task. **If a test here fails, that is a finding** — the spec claims the read path needs no change, and this task is where that claim gets checked rather than assumed.

- [ ] **Step 1: Write the tests**

```python
def test_a_graded_onc_rule_reaches_curated_ddi_pair(conn, seeded, ingested, curated):
    rows = conn.execute(
        "SELECT candidate_source, severity FROM drugref.curated_ddi_pair "
        "WHERE candidate_source = 'ONCHIGH'").fetchall()
    assert rows and all(r[0] == "ONCHIGH" for r in rows)


def test_an_ungraded_onc_rule_reaches_it_never(conn, seeded, ingested):
    """INNER JOIN by design: a NULL severity beside a real pair reads as
    'reviewed and harmless', which is the one rendering this schema must not
    permit."""
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_ddi_pair").fetchone()[0] == 0


def test_both_authorities_arrive_in_one_result_set(conn, seeded, ingested,
                                                   curated, medrt_curated):
    sources = {r[0] for r in conn.execute(
        "SELECT DISTINCT candidate_source FROM drugref.curated_ddi_pair")}
    assert sources == {"MED-RT", "ONCHIGH"}


def test_one_judgement_covers_a_rule_both_authorities_assert(conn, seeded):
    """curated_interaction's key OMITS source, so a rule MED-RT and ONC both
    assert takes ONE live judgement -- and the pair appears once per candidate
    source, carrying the same grade."""
    ...


def test_the_medrt_pair_count_is_undisturbed(conn, seeded, medrt_rows_present):
    before = _pairs(conn, "MED-RT")
    onchigh_run.ingest_onchigh(conn, path=FIXTURE, upstream_release="test")
    assert _pairs(conn, "MED-RT") == before


def test_a_signed_onc_judgement_verifies_and_a_tampered_one_does_not(conn, seeded,
                                                                     curated, test_key):
    """5c.4's whole point, exercised on the first content that has provenance
    worth attesting. Follows test_cli_signing's non-committing pattern -- the
    test-isolation debt (issue 2's shape) is carried, not widened."""
    ...
```

Fill the two elided bodies before running: the first asserts one live `curated_interaction` row and two `curated_ddi_pair` rows differing only in `candidate_source`; the second signs a judgement with a throwaway key, asserts `valid`, mutates a graded field via a superseding row, and asserts the old signature no longer covers the live payload.

- [ ] **Step 2: Run them**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_onchigh_read_path.py -v`
Expected: PASS without production changes. **Any failure is a spec finding — stop and report it rather than editing the test to match the behaviour.**

- [ ] **Step 3: Commit**

```bash
git add tests/test_onchigh_read_path.py
git commit -m "test: the ONC read path, per-source counts and the signing round-trip"
```

---

### Task 9: The clinical content

**This task does not begin until Tasks 1–8 are green, and no row it produces is committed without the reviewing clinician's sign-off.**

**Files:**
- Create: `src/drugref/data/onc_high_priority.toml`

- [ ] **Step 1: Retrieve the papers**

Fetch Phansalkar 2012 (*High-priority drug–drug interactions for use in electronic health records*, JAMIA) and the Ayvaz/Boyce 2015 update through the session's PubMed tooling. Both are open-access. **If an entry cannot be sourced, omit it — never reconstruct one from memory.**

- [ ] **Step 2: Resolve every endpoint against the live database**

For each pair, find the UNII (drug endpoints) and the MED-RT `source_code` (class endpoints) by querying `identity_claim`/`substance_class` directly. Record any endpoint that does not resolve — it becomes a gap-kind-fifteen question rather than a guess.

```sql
SELECT m.display_name, ic.value FROM drugref.substance_moiety m
  JOIN drugref.identity_claim ic ON ic.moiety_uuid = m.moiety_uuid
   AND ic.scheme = 'UNII' AND ic.superseded_by IS NULL
 WHERE m.display_name ILIKE '<drug>%';

SELECT source_code, class_name FROM drugref.substance_class
 WHERE class_name ILIKE '%<class>%' AND source = 'MED-RT';
```

- [ ] **Step 3: Draft the judgements**

`severity` from `contraindicated | major | moderate | minor`; `evidence_grade` from `established | probable | suspected | theoretical`. `mechanism` and `management` are **drugref's own prose** — never the paper's wording. Choose the axis per endpoint: `CI_EPC` for an `[EPC]` object, `CI_MoA` for `[MoA]`, `CI_PE` for `[PE]`.

- [ ] **Step 4: Clinical review — a hard gate**

Hand the file to the reviewing clinician. `reviewed_by` names **them**, never the drafting agent. Do not proceed on an unreviewed file.

- [ ] **Step 5: Load it and measure**

```bash
uv run drugref ingest onchigh --onc src/drugref/data/onc_high_priority.toml
uv run drugref curate onchigh --reviewed-by '<clinician>' --reviewed-against 'ONCHigh-2015'
```

Record, on a fresh database built from the real releases: ONCHIGH candidate rows, pairs per source, salt forms expanded, unresolved endpoints, the `gap_uncurated_interaction_rule` movement, and `curated_ddi_pair`'s hot-path timing against 5c.4's ~1.4 ms. **Confirm MED-RT's 21,664 and `substance_moiety`'s 19,438 are unmoved.**

- [ ] **Step 6: Commit**

```bash
git add src/drugref/data/onc_high_priority.toml
git commit -m "feat(data): the ONC high-priority DDI floor, clinically reviewed"
```

---

### Task 10: Documentation and the ROADMAP correction

**Files:**
- Modify: `docs/PROJECT-NOTES.md`, `docs/ROADMAP.md`, `docs/HANDOVER.md`
- Modify: `docs-site/docs/roadmap/index.md`

- [ ] **Step 1: PROJECT-NOTES — a new "Slice 5c.2" section**

Every measured figure from Task 9's step 5, the traps found on the way, and the two corrections this round already produced: **the gap kind is the fifteenth, not the thirteenth** (§ *Current state* still says "TWELVE gap kinds since Slice 3", which predates 5c.1's two), and the suite's "one home for this number" line.

- [ ] **Step 2: ROADMAP — mark 5c.2 done, and move the `spurious` deferral**

5c.1 handed the `spurious`-surfacing question to 5c.2, but `spurious` is a `curated_condition` ruling and this slice curates `curated_interaction` — so it cannot discharge it. Re-attach it to the first slice that curates the 168 contradicted pairs, stating *why* it moved. Also note that `ddi_candidate_pair` is now genuinely multi-source, so issue 73's text should be re-read against the interaction views.

- [ ] **Step 3: HANDOVER — regenerate**

Within the line bound **its own header states**. Focused on what is still to be done.

- [ ] **Step 4: Public docs**

Update `docs-site/docs/roadmap/index.md`, which currently lists the ONC floor as upcoming.

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`

- [ ] **Step 5: Full verification, then commit and open the PR**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check .
git add -A && git commit -m "docs: record slice 5c.2's measurements and correct the spurious deferral"
git push -u origin feat/slice-5c2-onc-floor
gh pr create --base main --title "Slice 5c.2: the ONC high-priority DDI floor (db/031)"
```

The PR body must link the issues this slice touches (51 stays open; issue 73's text needs re-reading) and state plainly what was measured and what was not.

---

## Self-Review

**Spec coverage.** §2 → Tasks 2, 5 (the source-based shape). §3 → Task 2 (all four changes). §4 → Task 3 (format, identifier keying, name-mismatch failure). §5 → Tasks 3, 4, 5, 6, 7 (pure/orchestrator split, two commands, idempotence). §6 → Task 4 (salt expansion, gated-in only). §7 → Tasks 3, 5 (raise vs. register, frozen `gap_key`). §8 → Task 8 (read path unchanged, both authorities in one result set). §9 → Task 8 (signing round-trip). §10 → Task 1 (rule 6, first not last). §11 → Task 9 step 5 (per-source counts, hot path). §12 → Task 10 step 2 (the `spurious` deferral moves).

**Two gaps found and closed while reviewing:** the spec's §3 claimed "no new table" when `_GAP_SOURCES` derives kinds from views — corrected in the spec and now Task 2 step 3 item 4; and `interactions.add_contraindication` / `clear_source_contraindications` already take `source`, so the candidate tier needs **no new writer**, which Task 5 now consumes rather than re-implementing.

**Type consistency.** `OncEntry`/`OncCandidate`/`OncJudgement` (Task 3) are consumed under those names in Tasks 4, 5 and 7. `resolve_entry`/`subject_forms` (Task 4) are consumed in Tasks 5 and 7. `ingest_onchigh` (Task 5) is consumed in Task 6. `live_interaction_judgement` (Task 7) is new on `curation`, not a rename of anything existing. `SOURCE = "ONCHIGH"` and `WRITER = "onchigh_run"` match db/031's two widened CHECKs exactly.

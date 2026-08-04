# Policy-surface debt round — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Settle the four follow-ups #35 filed rather than fixed — one shared supersession primitive (#59), a chain that runs its own documented invocation (#60), an operator surface for expansion-policy decisions (#61), and a documentation split that stops destroying its own history (#63).

**Architecture:** No migration, no new source, no clinical claim. `accumulation._supersede` moves to a new `overlay.py` and three owners import it. `IngestStep` gains a `secondary` field naming inputs whose release the step does *not* state, so `check_release_agreement` compares primary claims only. A `drugref policy record|withdraw|show` subcommand routes entirely through `interactions.py`, keeping `cli.py` free of SQL. `HANDOVER.md` splits into a small volatile file plus a stable `PROJECT-NOTES.md`.

**Tech Stack:** Python 3.12, `uv`, `psycopg` v3, PostgreSQL ≥ 18, pytest, ruff.

**Spec:** [`docs/superpowers/specs/2026-08-05-drugref-policy-surface-debt-round-design.md`](../specs/2026-08-05-drugref-policy-surface-debt-round-design.md)

## Global Constraints

- **TDD, always:** failing test first, watch it fail for the right reason, then the minimal code.
- **No migration in this round.** `db/027` is the latest file and stays the latest.
- **No published figure may move.** `ddi_candidate_pair` **21,664** · `open_question` **18,834** · `gap_dead_by_expansion_policy` **1** · `gap_unreviewed_expansion_root` **0** · `class_expansion_policy` **14** rows all binding · `expansion_policy_unresolved` **0**.
- **Tests:** `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`. Baseline at branch start: **810 passed**.
- **Lint:** `ruff check src tests` — **never** `ruff check .`, which walks `downloads/` and hangs.
- **`cli.py` writes no SQL.** Every database access goes through a module function.
- **The decision vocabulary lives in `db/027`'s CHECK only.** Never restate it as argparse `choices`, a Python list, or a validation branch.
- **Files stay under ~500 lines.** `cli.py` is 364 and grows in Task 4; `interactions.py` is 286 and grows in Task 3. Both stay under after this round — check, don't assume.
- Every new module and public function carries documentation a junior contributor can follow. This codebase's docstrings state *why*, not *what*.

---

### Task 1: `overlay.py` — one insert-then-point primitive (#59)

**Files:**
- Create: `src/drugref/overlay.py`
- Modify: `src/drugref/accumulation.py` (delete `_supersede` at 99-118; update 4 call sites at 140, 161, 216, 237; docstring at 1-19)
- Modify: `src/drugref/questions.py:370-379` (`set_state`)
- Modify: `src/drugref/interactions.py:114-127` (`record_expansion_decision`)
- Test: `tests/test_overlay_contract.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `overlay.supersede(conn, table: str, pk_column: str, new_id: int, key_columns: tuple[str, ...], key_values: tuple) -> None`. Used by Task 3's review of `interactions.py`.

- [ ] **Step 1: Write the failing contract test**

Create `tests/test_overlay_contract.py`:

```python
# tests/test_overlay_contract.py
"""The overlay tier's correction rule lives in ONE place (#59).

INSERT the new assertion, then point whatever was live at it. That pair of statements
is the only sequence the append-only overlay admits: correcting in place is refused by
the floor (db/020), and pointing first is impossible because the target does not exist
yet. Three modules hand-wrote it -- accumulation (Plan C), questions (since db/007) and
interactions (since db/027) -- and this project has spent four rounds fixing one rule
kept in two places (#31, #40, #43, db/018's two CTEs).

Restated as a grep rather than by importing anything, for the same reason
test_source_clear_contract restates each writer's table tuple and test_provenance
greps for the run record: driving the expectation off the code under test would pass
whatever that code said.
"""
import pathlib

SRC = pathlib.Path("src/drugref")


def _sources():
    return sorted(SRC.rglob("*.py"))


def test_only_overlay_points_a_row_at_its_successor():
    """One reader, one clear, one checksum, one run record -- and now ONE SUPERSESSION.

    A module that wrote this UPDATE itself would be re-deriving an ordering whose
    failure mode is a deferred constraint violation at COMMIT, arbitrarily far from the
    call that caused it. That is precisely the class of bug a shared primitive removes.
    """
    writers = [p for p in _sources()
               if "SET superseded_by" in p.read_text()]
    assert [p.name for p in writers] == ["overlay.py"]
```

- [ ] **Step 2: Run it and watch it fail for the right reason**

Run: `uv run pytest tests/test_overlay_contract.py -v`

Expected: FAIL. The assertion shows three files — `accumulation.py`, `interactions.py`, `questions.py` — and no `overlay.py`. If it names a different set, stop and reconcile before writing code.

- [ ] **Step 3: Create `src/drugref/overlay.py`**

The body is `accumulation._supersede` moved verbatim — same `psycopg.sql` composition, same parameter order:

```python
# src/drugref/overlay.py
"""The append-only curated overlay's one correction primitive.

WHAT THE OVERLAY TIER IS. drugref stores two kinds of thing. Ingested feeds are
REBUILDABLE PROJECTIONS -- dropped and rebuilt per release, because a fact upstream
retracts has to be able to disappear. Curated knowledge is an APPEND-ONLY OVERLAY:
nothing is edited in place and nothing is deleted, because "what did we last say about
this, against which release, and why did we change our mind" has to be answerable from
the database. db/020 built the floor that enforces it; db/027 put a fifth table on it.

THE ONE SEQUENCE THAT TIER ADMITS, and why it is a function rather than a paragraph of
documentation telling every writer to get it right:

    1. INSERT the new assertion, which becomes live.
    2. UPDATE whatever was live for the same natural key to point at it.

In that order, always. `superseded_by` is a foreign key to a row that must already
exist, so pointing first cannot work -- and getting the order backwards fails at
COMMIT, arbitrarily far from the call that caused it.

BOTH ROWS ARE BRIEFLY LIVE, between the INSERT and the UPDATE, and that is exactly why
single-live is a DEFERRED CONSTRAINT TRIGGER rather than a partial unique index: an
immediate check would reject the only sequence that can express a correction. Spec
5.0 proposed the index; db/007 met the problem first on `question_state` and db/020
generalised the trigger. Published as `decisions/correcting-a-curated-assertion.md`.

NOT EVERY APPEND-ONLY WRITE IS A SUPERSESSION. `claims.add_claim` uses ON CONFLICT DO
NOTHING scoped to live rows: re-asserting the same identity claim is idempotent, not a
correction, and routing it through here would write a supersession where db/005 wants
a no-op.
"""
import psycopg
from psycopg import sql


def supersede(conn: psycopg.Connection, table: str, pk_column: str, new_id: int,
              key_columns: tuple[str, ...], key_values: tuple) -> None:
    """Point whatever was live at `new_id`. Called AFTER the new row exists.

    Kept in one place because the ordering is the part that is easy to get wrong, and
    getting it wrong fails only at COMMIT -- long after the call that caused it.

    The natural key arrives as COLUMN NAMES rather than a pre-built SQL fragment, and
    the statement is composed with psycopg.sql. Every call site passes literals, so
    there was never an injection here -- but proving that took reading all of them, and
    composition makes it visible at a glance instead. It also puts the columns in the
    same shape db/020's triggers take them, which is what they are.

    `{pk} <> %s` keeps the row just written out of its own supersession.

    NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these
    modules, and the single-live check is DEFERRED -- so a mistake surfaces at the
    caller's COMMIT, not here.
    """
    where = sql.SQL(" AND ").join(
        sql.SQL("{} = %s").format(sql.Identifier(col)) for col in key_columns)
    conn.execute(
        sql.SQL("UPDATE drugref.{table} SET superseded_by = %s "
                "WHERE {where} AND superseded_by IS NULL AND {pk} <> %s").format(
            table=sql.Identifier(table), where=where, pk=sql.Identifier(pk_column)),
        (new_id, *key_values, new_id))
```

- [ ] **Step 4: Move `accumulation.py` onto it**

Delete `_supersede` (lines 99-118) and the now-unused `from psycopg import sql` import if nothing else in the file uses `sql`. Add `from drugref import ids, overlay`. Replace the four call sites, changing `_supersede(` to `overlay.supersede(` and nothing else — arguments are already in the right order:

```python
    overlay.supersede(conn, "additive_effect", "additive_effect_id", new_id,
                      ("effect_class_uuid",), (effect_class_uuid,))
```

```python
    overlay.supersede(conn, "effect_contribution", "effect_contribution_id", new_id,
                      ("effect_class_uuid", "contributor_class_uuid"),
                      (effect_class_uuid, contributor_class_uuid))
```

…and the same mechanical change at lines 216 (`interaction_group_assertion`) and 237 (`interaction_group_member`) — keep whatever key columns those two already pass.

In the module docstring, replace the "WHY THE WRITERS LOOK REPETITIVE" paragraph with a pointer, since the explanation now lives in `overlay.py`:

```
WHY THE WRITERS LOOK REPETITIVE. Each curation function inserts the new assertion and
then calls overlay.supersede to point the previous live one at it. That pair is the
only sequence the overlay admits, and overlay.py's docstring is where the reason lives
-- stated once, because three modules used to restate it.
```

- [ ] **Step 5: Move `questions.set_state` onto it**

Add `overlay` to the `drugref` import. Replace the hand-written `UPDATE` (lines 375-378) with:

```python
    overlay.supersede(conn, "question_state", "question_state_id", new_id,
                      ("question_uuid",), (question_uuid,))
```

Trim the docstring's second paragraph to a pointer, keeping the fact that this table met the problem first:

```
    Insert-then-point, in that order, via overlay.supersede -- see overlay.py for why
    the order is forced and why single-live is a DEFERRED trigger rather than a unique
    index. db/007 met that problem here first; db/020 generalised the answer.
```

- [ ] **Step 6: Move `interactions.record_expansion_decision` onto it**

Add `overlay` to the `drugref` import. Replace the hand-written `UPDATE` (lines 123-126) and its two-line comment with:

```python
    # Point whatever was live at the new row -- including a `withdrawn` one, which is
    # live but does not bind.
    overlay.supersede(conn, "class_expansion_policy", "policy_id", new_id,
                      ("source", "source_code"), (source, source_code))
```

Note the bare `"class_expansion_policy"` here: `overlay.supersede` composes `drugref.{table}` itself, so the *qualified* string `drugref.class_expansion_policy` disappears from this function. Task 3 depends on that.

- [ ] **Step 7: Run the contract test, then the whole suite**

Run: `uv run pytest tests/test_overlay_contract.py -v`
Expected: PASS.

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q`
Expected: **810 passed** + 1 new = **811**. Behaviour is identical, so any pre-existing failure here is a real regression from the move — do not proceed past it.

Run: `ruff check src tests`
Expected: clean. (Watch for an unused `sql` import left in `accumulation.py`.)

- [ ] **Step 8: Commit**

```bash
git add src/drugref/overlay.py src/drugref/accumulation.py src/drugref/questions.py \
        src/drugref/interactions.py tests/test_overlay_contract.py
git commit -m "refactor(overlay): one insert-then-point primitive, three owners (#59)"
```

---

### Task 2: `secondary` inputs — the chain runs its documented invocation (#60)

**Files:**
- Modify: `src/drugref/cli.py:43-56` (`IngestStep`), `:118-128` (`STEPS`), `:216-246` (`check_release_agreement`)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `IngestStep.secondary: tuple[str, ...]`, defaulting to `()`. No later task depends on it.

- [ ] **Step 1: Write the four failing tests**

Append to `tests/test_cli.py`. Note `_plan()` builds the resolved shape `check_release_agreement` takes — a list of `(step, release, paths)`:

```python
def _plan(*entries):
    """The resolved shape check_release_agreement takes: (step, release, paths).

    Built by hand rather than through resolve_inputs so these stay pure -- the
    question is about tags and paths, not about what is on disk.
    """
    by_name = {s.name: s for s in cli.STEPS}
    return [(by_name[name], release, paths) for name, release, paths in entries]


MEDRT_XML = pathlib.Path("downloads/MEDRT/Core_MEDRT_2026.07.06_XML.xml")
DESC = pathlib.Path("downloads/mesh/desc2026.gz")
SUPP = pathlib.Path("downloads/mesh/supp2026.gz")
PA = pathlib.Path("downloads/mesh/pa2026.xml")
UNII = pathlib.Path("downloads/UNII_Records_26Feb2026.txt")


def test_the_documented_four_source_invocation_passes_pre_flight():
    """#60: the command HANDOVER, the ingest-operability spec and #35's own plan all
    document could not run on merged main.

    mesh-relations reads desc/supp but records MED-RT's tag, because mesh_rel_run
    writes ONE ingest_run row under source='MED-RT'. Reading that as "one file claimed
    to be two releases" was the defect: the file is dated once, by mesh, and merely
    READ by mesh-relations.
    """
    cli.check_release_agreement(_plan(
        ("unii", "26Feb2026", {"unii": UNII}),
        ("medrt", "2026.07.06", {"medrt": MEDRT_XML}),
        ("mesh", "2026", {"pa": PA, "desc": DESC, "supp": SUPP}),
        ("mesh-relations", "2026.07.06",
         {"medrt": MEDRT_XML, "desc": DESC, "supp": SUPP})))


def test_two_steps_still_cannot_date_the_same_primary_file_differently():
    """The case check_release_agreement's docstring calls uncorrectable, and the one
    the secondary exemption must NOT weaken.

    The MED-RT xml is PRIMARY for both medrt and mesh-relations -- both record a tag
    describing it -- so two tags for identical bytes is still a pre-flight error.
    db/025 added `writer` precisely so an operator could see one half of MED-RT running
    a release behind the other; letting the halves disagree on purpose makes that
    signal report staleness that does not exist.
    """
    with pytest.raises(cli.ReleaseError) as exc:
        cli.check_release_agreement(_plan(
            ("medrt", "2026.07.06", {"medrt": MEDRT_XML}),
            ("mesh-relations", "2026.05.04",
             {"medrt": MEDRT_XML, "desc": DESC, "supp": SUPP})))
    assert "2026.07.06" in str(exc.value) and "2026.05.04" in str(exc.value)


def test_a_secondary_input_may_disagree_with_the_step_that_dates_it():
    """ASSERTED AS A PASS, deliberately, not left as the absence of a failure.

    This is the behaviour change #60 buys, and a guard that quietly stops guarding is
    worse than one that never existed -- so the exemption gets a test that fails if it
    is ever narrowed back, rather than only tests that fail if it is widened.

    mesh dates desc/supp as '2026'; mesh-relations reads the same bytes while recording
    MED-RT's '2026.07.06'. Both statements are true about different authorities.
    """
    cli.check_release_agreement(_plan(
        ("mesh", "2026", {"pa": PA, "desc": DESC, "supp": SUPP}),
        ("mesh-relations", "2026.07.06",
         {"medrt": MEDRT_XML, "desc": DESC, "supp": SUPP})))


def test_secondary_must_name_an_input_the_step_declares():
    """A typo would silently exempt nothing and leave the chain refusing -- the third
    place this project's rule bites: a convention that silently matches nothing is
    worse than none (resolve_inputs' globs and selected_steps' empty tag are the other
    two). Raised at construction, where STEPS is built, so it fires at import.
    """
    with pytest.raises(ValueError) as exc:
        cli.IngestStep("broken", (("desc", "mesh/desc*.gz"),), lambda *a: None,
                       secondary=("dsc",))
    assert "dsc" in str(exc.value)


def test_mesh_relations_is_the_only_step_with_a_secondary_input():
    """Restated independently, the shape test_every_orchestrator_has_a_subcommand uses:
    driving this off cli.STEPS would pass whatever cli.STEPS said. A step that gains an
    exemption without anyone deciding to grant it fails here."""
    assert {s.name: s.secondary for s in cli.STEPS if s.secondary} == {
        "mesh-relations": ("desc", "supp")}
```

- [ ] **Step 2: Run them and watch them fail for the right reason**

Run: `uv run pytest tests/test_cli.py -k "secondary or four_source or primary_file" -v`

Expected: FAIL — `test_the_documented_four_source_invocation_passes_pre_flight` and `test_a_secondary_input_may_disagree_with_the_step_that_dates_it` raise `ReleaseError`; the other two fail with `TypeError: __init__() got an unexpected keyword argument 'secondary'` / `AttributeError`. `test_two_steps_still_cannot_date_the_same_primary_file_differently` should already PASS — it pins what must not change.

- [ ] **Step 3: Add the field and its validation**

In `cli.py`, extend `IngestStep`. `secondary` needs a default so the other five steps are untouched, and `__post_init__` validates without assigning, which a frozen dataclass permits:

```python
@dataclass(frozen=True)
class IngestStep:
    """One orchestrator, as the CLI sees it.

    `inputs` pairs an ARGUMENT NAME with a GLOB relative to --downloads, and both
    consumers read the same tuple: the per-source subcommand turns each name into a
    required `--name PATH` flag, and the chain resolves the same names by glob. One
    declaration, so a step cannot grow an input the chain does not know about.

    `secondary` names the inputs this step READS BUT DOES NOT DATE (#60). A step
    records one release tag, describing its PRIMARY authority; mesh-relations reads
    two -- MED-RT states the rule, MeSH defines its object -- and writes one
    ingest_run row under source='MED-RT'. So its desc/supp inputs are dated by the
    mesh step and merely consumed here, and check_release_agreement must not read
    that as one file claimed to be two releases.

    It names INPUTS, not paths, because the declaration belongs beside the glob it
    qualifies and has to survive a glob's filename changing between releases.
    """
    name: str
    inputs: tuple[tuple[str, str], ...]
    runner: Callable[[object, dict[str, pathlib.Path], str], object]
    secondary: tuple[str, ...] = ()

    def __post_init__(self):
        # A typo here would exempt nothing and leave the chain refusing the very
        # invocation the exemption exists to allow -- a silent failure, in the field,
        # of a check whose whole job is to be loud. Raised at import, where STEPS is
        # built, so it cannot reach an operator.
        undeclared = set(self.secondary) - {name for name, _ in self.inputs}
        if undeclared:
            raise ValueError(
                f"{self.name}: secondary names an input this step does not declare: "
                f"{', '.join(sorted(undeclared))}")
```

Then mark the exemption on the one step that needs it, in `STEPS`:

```python
    IngestStep("mesh-relations", (("medrt", "MEDRT/Core_MEDRT_*_XML.xml"),
                                  ("desc", "mesh/desc*.gz"),
                                  ("supp", "mesh/supp*.gz")), _run_mesh_relations,
               secondary=("desc", "supp")),
```

- [ ] **Step 4: Make `check_release_agreement` compare primary claims only**

The loop iterates `paths.items()` instead of `paths.values()`, so it can see which input a path came from:

```python
    stated: dict[pathlib.Path, tuple[str, str]] = {}   # path -> (release, step name)
    for step, release, paths in plan:
        for name, path in paths.items():
            if name in step.secondary:
                # READ, NOT DATED. This step states no release for this file, so it
                # makes no claim that could contradict another step's. Skipping the
                # record entirely (rather than recording and tolerating a mismatch)
                # is what keeps a file dated by NO step from silently agreeing with
                # itself.
                continue
            first_release, first_step = stated.setdefault(path, (release, step.name))
            if first_release != release:
                raise ReleaseError(...)   # message unchanged
```

Extend the docstring's "THE STEPS OVERLAP" paragraph — the `mesh`/`mesh-relations` half of it is now stale:

```
    THE STEPS OVERLAP, and that is not incidental: `medrt` and `mesh-relations`
    resolve the SAME Core_MEDRT_*_XML.xml. Their release tags are stated
    independently, so `--medrt-release 2026.07.06 --mesh-relations-release 2026.05.04`
    writes two different releases into ingest_run FROM IDENTICAL BYTES. One of them is
    false, and ingest_run is history: nothing can take it back.

    `mesh` and `mesh-relations` also share desc/supp, and that overlap is NOT a
    conflict (#60): mesh-relations declares them `secondary`, so it reads them without
    dating them. Comparing those claims refused the documented four-source invocation
    for a disagreement that was never one -- two true statements about two different
    authorities.
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, all of them, including the pre-existing `test_one_file_cannot_be_recorded_as_two_releases` and `test_steps_sharing_a_file_are_fine_when_they_agree`. If either of those now fails, the exemption is too wide — it must not touch the MED-RT xml.

Run: `ruff check src tests`

- [ ] **Step 6: Commit**

```bash
git add src/drugref/cli.py tests/test_cli.py
git commit -m "fix(cli): a step declares the inputs it reads but does not date (#60)"
```

---

### Task 3: `interactions.py` — two readers, one constant, and the naming pin (#61)

**Files:**
- Modify: `src/drugref/interactions.py` (add `WITHDRAWN`, `live_decisions`, `decision_history`; `withdraw_expansion_decision` reads the constant)
- Test: `tests/test_expansion_policy_writer.py` (append), `tests/test_overlay_contract.py` (append)

**Interfaces:**
- Consumes: `overlay.supersede` from Task 1 — specifically, that `record_expansion_decision` no longer contains the qualified string `drugref.class_expansion_policy` in its UPDATE. **Task 1 must be complete or the pin's numbers are wrong.**
- Produces, for Task 4:
  - `interactions.WITHDRAWN: str` — the module's single copy of the string `"withdrawn"`.
  - `interactions.live_decisions(conn) -> list[tuple[str, str, str, str]]` — `(source, source_code, decision, class_name)` for what currently binds, ordered.
  - `interactions.decision_history(conn, source: str, source_code: str) -> list[tuple[int, str, str, str, str, int | None]]` — `(policy_id, decision, rationale, reviewed_by, reviewed_against, superseded_by)`, oldest first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_expansion_policy_writer.py`:

```python
def test_live_decisions_reports_what_binds(conn):
    """The read `drugref policy show` prints with no arguments. Goes through
    class_expansion_policy_current, so a withdrawn row is correctly absent."""
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    rows = interactions.live_decisions(conn)
    assert ("MED-RT", CODE, "deny", "Test Bucket [PE]") in rows
    # The 14 seeded roots are binding too, so this is a superset check by design.
    assert len(rows) >= 15


def test_live_decisions_omits_a_withdrawn_class(conn):
    """WITHDRAWN IS NOT A DECISION THAT BINDS. It means "no current judgement", so the
    class returns to gap_unreviewed_expansion_root -- and an operator asking what binds
    must not be shown it."""
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "test", "2026.07.06")
    interactions.withdraw_expansion_decision(
        conn, "MED-RT", CODE, "the measurement no longer holds", "test", "2026.07.06")
    assert [r for r in interactions.live_decisions(conn) if r[1] == CODE] == []


def test_decision_history_keeps_every_ruling_in_order(conn):
    """The whole of #35 in one read: what did we last say, against which release, and
    why did we change our mind. The superseded row must still carry its ORIGINAL
    rationale -- that is what an in-place UPDATE destroyed."""
    first = interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "too abstract",
        "alice", "2026.07.06")
    second = interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "allow", "Test Bucket [PE]", "subtree is narrow",
        "bob", "2026.07.06")

    history = interactions.decision_history(conn, "MED-RT", CODE)
    assert [(h[0], h[1], h[2], h[3]) for h in history] == [
        (first, "deny", "too abstract", "alice"),
        (second, "allow", "subtree is narrow", "bob")]
    assert history[0][5] == second      # the first row points at the second
    assert history[1][5] is None        # the second is live


def test_decision_history_is_empty_for_a_class_nobody_ruled_on(conn):
    """Not an error: "nobody has looked" is a legitimate answer to `policy show`, and
    is exactly what absent means -- unreviewed, which expands AND raises a question."""
    assert interactions.decision_history(conn, "MED-RT", "N0000000404") == []


def test_the_withdrawn_vocabulary_lives_in_exactly_one_python_name(conn):
    """`withdrawn` is a member of db/027's CHECK, which is the vocabulary's one home.
    interactions.WITHDRAWN exists so the CLI can refuse it (a curation surface should
    not offer a verb that bypasses withdraw_expansion_decision's two guarantees)
    WITHOUT adding a second literal. This pins that it is the same string the writer
    itself uses -- a drift would leave the CLI refusing a value the database accepts."""
    assert interactions.WITHDRAWN == "withdrawn"
    interactions.record_expansion_decision(
        conn, "MED-RT", CODE, "deny", "Test Bucket [PE]", "r", "test", "2026.07.06")
    interactions.withdraw_expansion_decision(
        conn, "MED-RT", CODE, "stale", "test", "2026.07.06")
    assert conn.execute(
        "SELECT decision FROM drugref.class_expansion_policy "
        "WHERE source_code = %s AND superseded_by IS NULL", (CODE,)
    ).fetchone()[0] == interactions.WITHDRAWN
```

Append to `tests/test_overlay_contract.py` — the pin the #62 review said was missing:

```python
# ---- where the policy table is named ----------------------------------------

# WHAT EACH FILE IS ALLOWED TO SAY, restated independently rather than counted from
# the code, so BOTH directions fail: a new reader added by accident, and an existing
# one deleted.
#
# test_only_the_current_view_reads_the_policy_table_directly pins the SQL side from
# pg_depend, but pg_rewrite sees only views and matviews -- it CANNOT see SQL embedded
# in Python or in a PL/pgSQL body. This is the other half.
POLICY_TABLE_NAMINGS = {
    # SQL. The INSERT and withdraw's `SELECT class_name` (db/027, #35), plus
    # decision_history's read of the history the _current view exists to filter out
    # (#61). The supersession UPDATE is NOT here: it goes through overlay.supersede,
    # which composes drugref.{table} from a bare argument.
    "interactions.py": 3,
    # PROSE, NOT SQL -- the operator warning telling them the table is append-only and
    # naming the two functions that can revise it. A grep that called this a reader
    # would be counting the sentence that explains the rule.
    "medrt_run.py": 1,
}


def test_only_interactions_reads_the_policy_table_from_python():
    """The base table has ONE Python owner (#61).

    `drugref policy` could easily have written its own query -- it is a read, and the
    handler has a connection. It does not: cli.py calls interactions.py, because a
    handler with its own SELECT would be a reader no test in this repo could notice.
    """
    named = {p.name: p.read_text().count("drugref.class_expansion_policy")
             for p in _sources()
             if "drugref.class_expansion_policy" in p.read_text()}
    assert named == POLICY_TABLE_NAMINGS
```

- [ ] **Step 2: Run and watch them fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_expansion_policy_writer.py tests/test_overlay_contract.py -v`

Expected: FAIL — `AttributeError: module 'drugref.interactions' has no attribute 'live_decisions'` etc., and the naming pin reports `interactions.py: 2` (Task 1 removed one, this task has not yet added `decision_history`). **If it reports 3 before you write `decision_history`, Task 1 was not applied — stop.**

- [ ] **Step 3: Add the constant and the two readers**

In `interactions.py`, beside `NoLiveDecisionError`:

```python
# The one Python copy of a value whose home is db/027's CHECK. It exists because
# withdraw_expansion_decision has to name the value it writes, and because an operator
# surface has to be able to refuse it (see cli._handle_policy_record). Two literals
# would be two things to disagree with each other; one constant read by both is not a
# second vocabulary.
WITHDRAWN = "withdrawn"
```

Change `withdraw_expansion_decision`'s final call to use it:

```python
    return record_expansion_decision(conn, source, source_code, WITHDRAWN, row[0],
                                     rationale, reviewed_by, reviewed_against)
```

Add the two readers at the end of the expansion-policy section:

```python
def live_decisions(conn: psycopg.Connection) -> list[tuple[str, str, str, str]]:
    """Every expansion decision that currently BINDS, as (source, code, decision, name).

    Through class_expansion_policy_current, like every other reader -- the view is
    where "live" and "binding" are told apart, and a `withdrawn` row is live without
    binding. A reader that asked the base table for `superseded_by IS NULL` would
    report withdrawals as decisions, which is exactly the merge db/027 forbids.
    """
    return conn.execute(
        "SELECT source, source_code, decision, class_name "
        "FROM drugref.class_expansion_policy_current "
        "ORDER BY source, source_code").fetchall()


def decision_history(conn: psycopg.Connection, source: str,
                     source_code: str) -> list[tuple]:
    """Every ruling ever recorded for one class, oldest first.

    (policy_id, decision, rationale, reviewed_by, reviewed_against, superseded_by).

    THE ONE READER THAT MUST NAME THE BASE TABLE, and deliberately so: history is
    precisely what class_expansion_policy_current filters out, so asking the view
    would return at most one row and answer none of the question #35 exists to answer
    -- what did we last say, against which release, and why did we change our mind.

    Ordered by policy_id, which is a surrogate sequence: rows are strictly
    append-only, so insertion order IS chronology. reviewed_at would tie for two
    rulings recorded in one transaction.

    An empty list is a legitimate answer -- absent means UNREVIEWED, which expands and
    raises a question -- not an error.
    """
    return conn.execute(
        "SELECT policy_id, decision, rationale, reviewed_by, reviewed_against, "
        "superseded_by FROM drugref.class_expansion_policy "
        "WHERE source = %s AND source_code = %s ORDER BY policy_id",
        (source, source_code)).fetchall()
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_expansion_policy_writer.py tests/test_overlay_contract.py -v`
Expected: PASS, and the naming pin now sees `interactions.py: 3`.

Run: `wc -l src/drugref/interactions.py` — expected still under 500.

- [ ] **Step 5: Commit**

```bash
git add src/drugref/interactions.py tests/test_expansion_policy_writer.py \
        tests/test_overlay_contract.py
git commit -m "feat(interactions): read the policy history, and name withdrawn once (#61)"
```

---

### Task 4: `drugref policy record|withdraw|show` (#61)

**Files:**
- Modify: `src/drugref/cli.py` (three handlers, the subcommand tree, `main`'s caught family, the module docstring)
- Test: `tests/test_cli_policy.py` (create)

**Interfaces:**
- Consumes: `interactions.WITHDRAWN`, `interactions.live_decisions`, `interactions.decision_history`, `interactions.record_expansion_decision`, `interactions.withdraw_expansion_decision`, `interactions.unresolved_expansion_policy`, `interactions.NoLiveDecisionError` — all from Task 3 or already present.
- Produces: nothing later tasks consume.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_policy.py`. **Read the module docstring carefully before writing — the cleanup discipline is not optional:**

```python
# tests/test_cli_policy.py
"""`drugref policy` -- the operator surface for expansion decisions (#61).

WHY THIS EXISTS. medrt_run warns an operator when a release stops defining a class
somebody ruled on, and tells them to "re-key or withdraw". Since db/027 both verbs are
unavailable as raw SQL -- DELETE raises, and so does UPDATE ... SET source_code -- so
following the warning meant writing Python against the library.

THESE TESTS COMMIT, AND COMMITTED POLICY ROWS CANNOT BE DELETED. The append-only floor
refuses it, so there is no teardown that erases them; the only way to undo a revision
is to record a FURTHER correction. That is why every write test restores in a
`finally`, exactly as test_a_second_apply_does_not_stomp_a_locally_revised_decision
does. Nothing leaks between sessions -- conftest's session-scoped _migrated drops the
schema -- but WITHIN a session these rows are visible to every later test, and
test_the_seed_holds_the_fourteen_roots_the_measurement_found asserts the live decision
of all fourteen. A missing restore would fail it, and which test failed would depend on
collection order: the worst shape a failure can have.

WHY A SEEDED ROOT RATHER THAN AN INVENTED CODE. An invented source_code names no class,
so it would appear in expansion_policy_unresolved -- the view that reports "this deny
matches no class" -- and several tests assert that view's contents. Revising a real
seeded root keeps every one of those views answering what it answered before, provided
the restore runs.

CODE is Dermatologic Activity Alteration [PE]: seeded, denied, and referenced by
exactly one other test, as a member of a frozen set that a restore-to-`deny` keeps
satisfied.
"""
import pathlib

import psycopg
import pytest

from drugref import cli, interactions

CODE = "N0000009020"
NAME = "Dermatologic Activity Alteration [PE]"
SEED_RATIONALE = "restored by tests/test_cli_policy.py"


@pytest.fixture
def committed(_migrated, monkeypatch):
    """A DSN the CLI will pick up, plus a restore of CODE's seeded `deny`.

    The restore is a third row, not a rollback: nothing can be deleted or revised in
    place, so undoing a test's revision means recording a further correction.
    """
    monkeypatch.setenv("DRUGREF_DSN", _migrated)
    try:
        yield _migrated
    finally:
        with psycopg.connect(_migrated) as c:
            live = c.execute(
                "SELECT decision FROM drugref.class_expansion_policy_current "
                "WHERE source = 'MED-RT' AND source_code = %s", (CODE,)).fetchone()
            if live is None or live[0] != "deny":
                interactions.record_expansion_decision(
                    c, "MED-RT", CODE, "deny", NAME, SEED_RATIONALE,
                    "test", "2026.07.06")
                c.commit()


def _live(dsn, code=CODE):
    with psycopg.connect(dsn) as c:
        return c.execute(
            "SELECT decision, rationale FROM drugref.class_expansion_policy_current "
            "WHERE source = 'MED-RT' AND source_code = %s", (code,)).fetchone()


def test_policy_record_revises_a_binding_decision_and_commits(committed, capsys):
    """The handler COMMITS, unlike every library function in these modules. The CLI is
    the caller, and the caller owns the transaction -- an operator's ruling that
    vanished when the process exited would be worse than no surface at all."""
    assert cli.main([
        "policy", "record", "--source", "MED-RT", "--code", CODE,
        "--decision", "allow", "--class-name", NAME,
        "--rationale", "subtree measured narrow", "--reviewed-by", "operator",
        "--reviewed-against", "2026.07.06"]) == 0
    assert _live(committed) == ("allow", "subtree measured narrow")
    assert "allow" in capsys.readouterr().out


def test_policy_record_refuses_withdrawn_and_names_the_other_subcommand(committed, capsys):
    """record_expansion_decision accepts `withdrawn` by design -- rejecting it in
    Python would put a member of the decision vocabulary back into a second place. But
    that path bypasses BOTH guarantees withdraw_expansion_decision provides: the
    NoLiveDecisionError that catches a caller believing something false, and carrying
    class_name forward so a withdrawal cannot introduce a name nobody reviewed.

    The library keeps that door open. An operator surface should not, and refusing by
    comparison to interactions.WITHDRAWN adds no second literal.
    """
    assert cli.main([
        "policy", "record", "--source", "MED-RT", "--code", CODE,
        "--decision", "withdrawn", "--class-name", NAME, "--rationale", "r",
        "--reviewed-by", "operator", "--reviewed-against", "2026.07.06"]) == 2
    assert "policy withdraw" in capsys.readouterr().err
    assert _live(committed)[0] == "deny"        # a refused command changed nothing


def test_policy_withdraw_returns_the_class_to_unreviewed(committed):
    """WITHDRAWN IS NOT `allow`. It means no current judgement, so the class goes back
    to gap_unreviewed_expansion_root -- which is what medrt_run's warning is asking an
    operator to do when a rationale has gone stale."""
    assert cli.main([
        "policy", "withdraw", "--source", "MED-RT", "--code", CODE,
        "--rationale", "the measurement no longer holds",
        "--reviewed-by", "operator", "--reviewed-against", "2026.07.06"]) == 0
    assert _live(committed) is None


def test_policy_withdraw_without_a_live_decision_exits_two(committed, capsys):
    """NoLiveDecisionError is a LookupError, which main did not catch -- so this
    printed a psycopg-free but equally unhelpful traceback. Withdrawing a decision
    nobody made means the caller believes something false; saying so plainly is the
    whole point of the error."""
    assert cli.main([
        "policy", "withdraw", "--source", "MED-RT", "--code", "N0000000404",
        "--rationale", "r", "--reviewed-by", "operator",
        "--reviewed-against", "2026.07.06"]) == 2
    err = capsys.readouterr().err
    assert "no live expansion decision" in err
    assert "Traceback" not in err


def test_policy_show_lists_what_binds(committed, capsys):
    assert cli.main(["policy", "show"]) == 0
    out = capsys.readouterr().out
    assert CODE in out and "deny" in out


def test_policy_show_prints_one_classes_history(committed, capsys):
    """The read that makes `record` usable: an operator writing a rationale needs to
    see the one they are replacing, and the superseded row keeps its ORIGINAL text."""
    cli.main(["policy", "record", "--source", "MED-RT", "--code", CODE,
              "--decision", "allow", "--class-name", NAME,
              "--rationale", "subtree measured narrow", "--reviewed-by", "operator",
              "--reviewed-against", "2026.07.06"])
    capsys.readouterr()
    assert cli.main(["policy", "show", "--source", "MED-RT", "--code", CODE]) == 0
    out = capsys.readouterr().out
    assert "subtree measured narrow" in out
    assert "allow" in out and "deny" in out      # the live ruling AND its predecessor


def test_policy_show_says_so_when_nobody_has_ruled(committed, capsys):
    """Absent means UNREVIEWED, which expands and raises a question -- a real answer,
    not an empty result an operator should read as an error."""
    assert cli.main(["policy", "show", "--source", "MED-RT",
                     "--code", "N0000000404"]) == 0
    assert "no decision" in capsys.readouterr().out.lower()


def test_policy_show_needs_both_halves_of_the_key_or_neither():
    """--code alone cannot identify a class: `source` is half the natural key, and
    means "who defines the class" rather than "who ruled on it"."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["policy", "show", "--code", CODE])
```

- [ ] **Step 2: Run and watch them fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_cli_policy.py -v`

Expected: FAIL — argparse rejects `policy` as an unknown command (`SystemExit: 2`) on every test.

- [ ] **Step 3: Write the three handlers**

In `cli.py`, after `_handle_ingest`. Note none of them writes SQL:

```python
def _handle_policy_record(conn, args) -> int:
    """Record or revise an expansion decision. COMMITS -- the CLI is the caller, and
    in these modules the caller owns the transaction."""
    if args.decision == interactions.WITHDRAWN:
        # The library accepts this and deliberately does not guard it (a guard would
        # put a member of db/027's vocabulary back into Python). An operator surface
        # is a different matter: this path skips the NoLiveDecisionError that catches
        # a caller believing something false, and skips carrying class_name forward
        # from the row being retracted.
        print(f"drugref: --decision {interactions.WITHDRAWN} is not recorded here. "
              "Use `drugref policy withdraw`, which refuses to withdraw a decision "
              "nobody made and carries the reviewed class name forward.",
              file=sys.stderr)
        return 2
    policy_id = interactions.record_expansion_decision(
        conn, args.source, args.code, args.decision, args.class_name,
        args.rationale, args.reviewed_by, args.reviewed_against)
    conn.commit()
    print(f"recorded policy_id={policy_id}: "
          f"{args.source} {args.code} -> {args.decision}")
    return 0


def _handle_policy_withdraw(conn, args) -> int:
    """Retract the live decision, returning the class to gap_unreviewed_expansion_root.

    NoLiveDecisionError propagates to main, which reports it without a traceback.
    """
    policy_id = interactions.withdraw_expansion_decision(
        conn, args.source, args.code, args.rationale, args.reviewed_by,
        args.reviewed_against)
    conn.commit()
    print(f"withdrawn policy_id={policy_id}: {args.source} {args.code} "
          "(the class is unreviewed again, so it expands AND raises a question)")
    return 0


def _handle_policy_show(conn, args) -> int:
    """What binds, or one class's whole history. Reads only -- nothing to commit."""
    if args.code is None:
        rows = interactions.live_decisions(conn)
        print("binding decisions:" if rows else "binding decisions: none")
        for source, code, decision, class_name in rows:
            print(f"  {source:<8} {code:<12} {decision:<10} {class_name}")
        # The other half of the answer, for the same reason `status` prints two
        # blocks: a decision that binds nothing looks exactly like one that works.
        unresolved = interactions.unresolved_expansion_policy(conn, "MED-RT")
        print(f"\nbinding but matching no class: {len(unresolved)}"
              + (f" ({', '.join(unresolved)})" if unresolved else ""))
        return 0

    history = interactions.decision_history(conn, args.source, args.code)
    if not history:
        print(f"{args.source} {args.code}: no decision — unreviewed, so it expands "
              "and raises a question on gap_unreviewed_expansion_root")
        return 0
    print(f"{args.source} {args.code}, oldest first:")
    for policy_id, decision, rationale, by, against, superseded_by in history:
        mark = "  " if superseded_by else "* "      # * marks the live row
        print(f"{mark}#{policy_id} {decision:<10} [{by} vs {against}] {rationale}")
    return 0
```

- [ ] **Step 4: Register the subcommands and widen `main`'s caught family**

In `build_parser`, after the `ingest` block. `--source` and `--code` are required for `record`/`withdraw`; for `show` they are optional but must arrive together, which a mutually-inclusive pair expresses most simply as a check the parser can make:

```python
    policy = commands.add_parser(
        "policy", help="record, withdraw or inspect class-expansion decisions")
    policy_actions = policy.add_subparsers(dest="action", required=True)

    record = policy_actions.add_parser(
        "record", help="record or revise whether a class expands over its subtree")
    record.add_argument("--source", required=True,
                        help="who DEFINES the class (half the natural key), e.g. MED-RT")
    record.add_argument("--code", required=True, help="the class's source_code")
    # No `choices`: the vocabulary lives in db/027's CHECK, and a second list is a
    # second thing to disagree with the first (db/006). An unrecognised value reaches
    # the database and raises CheckViolation.
    record.add_argument("--decision", required=True,
                        help="the ruling, as db/027's CHECK defines it")
    record.add_argument("--class-name", required=True,
                        help="the class's name, as reviewed")
    record.add_argument("--rationale", required=True,
                        help="why -- this is what survives as history")
    record.add_argument("--reviewed-by", required=True)
    record.add_argument("--reviewed-against", required=True,
                        help="the release the ruling was measured against")
    record.set_defaults(handler=_handle_policy_record)

    withdraw = policy_actions.add_parser(
        "withdraw", help="retract a ruling, returning the class to unreviewed")
    withdraw.add_argument("--source", required=True)
    withdraw.add_argument("--code", required=True)
    withdraw.add_argument("--rationale", required=True)
    withdraw.add_argument("--reviewed-by", required=True)
    withdraw.add_argument("--reviewed-against", required=True)
    withdraw.set_defaults(handler=_handle_policy_withdraw)

    show = policy_actions.add_parser(
        "show", help="what binds, or one class's whole history")
    show.add_argument("--source", help="with --code, the class to show history for")
    show.add_argument("--code", help="with --source, the class to show history for")
    show.set_defaults(handler=_handle_policy_show)

    return parser
```

`--source`/`--code` must arrive together for `show`. Add the check at the top of `build_parser`'s caller — cleanest as a small guard in `main`, but simplest and testable at parse time via a `parse_args` post-check. Put it in `main`, immediately after parsing, so `build_parser` stays a pure description of the surface:

```python
    args = build_parser().parse_args(argv)
    if getattr(args, "action", None) == "show" and (args.source is None) != (args.code is None):
        # Half a natural key identifies nothing. `source` here means WHO DEFINES the
        # class, not who ruled on it, so it cannot be defaulted.
        build_parser().error("policy show: --source and --code must be given together")
```

Widen the caught family so `NoLiveDecisionError` reports plainly:

```python
    except (RuntimeError, ChainError, interactions.NoLiveDecisionError) as exc:
```

Add the import: `from drugref import db, interactions`.

Update the module docstring's first line, which now under-describes the surface:

```
"""The drugref command line: the first supported way to run an ingest (#16), and to
record a curator's expansion decision (#61).
```

…and extend the "WHAT THIS MODULE IS AND IS NOT" paragraph:

```
IT HOLDS NO SQL. Every database access goes through a module function -- the
orchestrators for ingest, interactions.py for policy. That is load-bearing for the
policy commands specifically: test_only_the_current_view_reads_the_policy_table_directly
reads pg_rewrite, which sees views and matviews and CANNOT see a query embedded in
Python, so a handler with its own SELECT would be a reader of an append-only curated
table that no test in this repository could notice.
```

- [ ] **Step 5: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_cli_policy.py -v`
Expected: PASS.

Then prove the restore discipline holds. This is the thing most likely to be subtly wrong, and an order-dependent failure is the worst shape a failure can have — so force the order that would expose it rather than hoping the default collection order does:

```bash
DSN='host=localhost port=5532 dbname=drugref_test user=postgres'
# The committing module FIRST, then the module that asserts all fourteen seeded
# decisions. If a restore is missing, this fails and the default order might not.
DRUGREF_TEST_DSN="$DSN" uv run pytest -q tests/test_cli_policy.py tests/test_expansion_policy.py
# And the whole suite, unmodified.
DRUGREF_TEST_DSN="$DSN" uv run pytest -q
```

Expected: both green, and specifically `test_the_seed_holds_the_fourteen_roots_the_measurement_found` passing in the first run. If it fails there, a `finally` restore is missing — fix that, do not reorder the tests.

Run: `ruff check src tests` and `wc -l src/drugref/cli.py` (expected under 500).

- [ ] **Step 6: Commit**

```bash
git add src/drugref/cli.py tests/test_cli_policy.py
git commit -m "feat(cli): drugref policy record/withdraw/show (#61)"
```

---

### Task 5: Split the documents by volatility (#63)

**Files:**
- Create: `docs/PROJECT-NOTES.md`
- Modify: `docs/HANDOVER.md` (reduce to the volatile part), `docs/ROADMAP.md` (header note only), `CLAUDE.md`, `.claude/skills/nextsession/SKILL.md`

**Interfaces:**
- Consumes: nothing. Produces: nothing. Pure documentation — but it changes two project rules, so it is not a wrap-up edit.

- [ ] **Step 1: Create `docs/PROJECT-NOTES.md` from HANDOVER's stable half**

Move these sections **verbatim** out of `docs/HANDOVER.md` — this is a move, not a rewrite, and re-compressing them here would destroy the content the split exists to preserve:

- `Merged rounds, compressed — the traps only`
- `Current state, by layer`
- `Slice 5b.2 — MeSH-keyed indications`
- `Plan C — the accumulation model`
- `The ingest-operability round`
- `The expansion-policy history round`
- `What the upstream documentation got wrong`
- `Architecture in one breath`
- `How to run / test` (including the schema list, migration-immutability note, code map, DSNs, and the downloads/upstream-feed notes)
- `Repo facts`

Give it this header:

```markdown
# PROJECT-NOTES — drugref

> **The stable half of the working scaffolding** (#63). Traps, current state by layer, how to run and test,
> the schema and code map, upstream errata, repo facts. **Edited IN PLACE and under no line bound** — that is
> the whole point: `docs/HANDOVER.md` is regenerated each round and its history answers nothing, so anything
> whose history is worth reading lives here instead.
>
> Still **not** a source of truth. The canonical what/why is the design specs under
> [`docs/superpowers/specs/`](superpowers/specs/); living decisions are in `docs-site/docs/decisions/`. If this
> file disagrees with either, they win.
>
> **Its git history starts 2026-08-05.** That is the honest cost of the split: it buys a readable history
> going forward, not retroactively.
```

- [ ] **Step 2: Reduce `docs/HANDOVER.md` to the volatile part**

What stays: the header note, `⇒ NEXT` (merged list, in flight, tracker hygiene, next candidates), `Open follow-ups`, and the current DSN / measurement-database lines. Target **~120 lines**.

Replace the header with one that states the split and the rule:

```markdown
# HANDOVER — drugref

> **The volatile half: where we are right now.** Regenerated at the end of every working session
> (nextsession rule 9) and kept **under ~120 lines**, so a rewrite costs nothing.
>
> **The stable half is [`PROJECT-NOTES.md`](PROJECT-NOTES.md)** — traps, state by layer, how to run and test,
> the schema and code map, upstream errata, repo facts. It is edited in place and under no bound. **Put
> anything whose history is worth reading there, not here** (#63): this file's history is deliberately
> disposable, and content that lands here gets compressed away.
>
> Slice sequencing is [`ROADMAP.md`](ROADMAP.md); the canonical what/why is the specs under
> [`superpowers/specs/`](superpowers/specs/).
```

- [ ] **Step 3: Note the change in `docs/ROADMAP.md`**

Only the header block changes — no content moves:

```markdown
> **Disposable working scaffolding, not a source of truth.** The canonical *what/why* is the design spec(s)
> under [`docs/superpowers/specs/`](superpowers/specs/) (and future ADRs). This file only orders the build.
> If it disagrees with the canonical docs, the canonical docs win.
>
> **Under no line bound since #63**, and appended to per slice rather than recompressed: a bound that forces
> a compression pass trades a readable history for a line count. Session state is
> [`HANDOVER.md`](HANDOVER.md); the stable notes are [`PROJECT-NOTES.md`](PROJECT-NOTES.md).
```

- [ ] **Step 4: Change the two rules in `CLAUDE.md`**

This is a genuine rule change, which is the only thing that licenses editing `CLAUDE.md` (nextsession rule 9).

Replace the "Keep this file short and **stable**" paragraph's second half:

```markdown
Session state lives in `docs/HANDOVER.md` (**volatile**, ~120 lines, regenerated each session); the stable
working notes — traps, state by layer, how to run/test, schema and code map — live in `docs/PROJECT-NOTES.md`
(**edited in place, no line bound**); slice sequencing in `docs/ROADMAP.md` (**no line bound**); the canonical
what/why in the design specs under `docs/superpowers/specs/` (if anything here disagrees with a spec, the
spec wins).
```

And in "Starting a session", replace the `< 500 lines` instruction:

```markdown
Read `docs/HANDOVER.md` first and follow it (the `nextsession` skill does this). Before starting work,
verify HANDOVER.md, PROJECT-NOTES.md and ROADMAP.md reflect the current state; update them if stale. When
done, update all three, then commit, push, and open a PR to `main` linking any relevant issue.

**Only HANDOVER.md is bounded** (~120 lines). PROJECT-NOTES.md and ROADMAP.md are edited in place and grow:
#63 measured that the old < 500-line bound forced a compression pass every round, which turned every edit
into an ~80% rewrite and made `git log -p` useless on the two files whose job is carrying state between
sessions.
```

- [ ] **Step 5: Teach `nextsession` the third file**

In `.claude/skills/nextsession/SKILL.md`, rules 8 and 9:

```
8. Before you start working, make sure that HANDOVER.md, PROJECT-NOTES.md and ROADMAP.md represent the
   current state of progress and are up to date. If not, update them before you start working.
9. When you are done with your work, update all three to reflect the current state. HANDOVER.md is the
   volatile one — keep it under ~120 lines, focused on what still needs to be done. PROJECT-NOTES.md and
   ROADMAP.md are edited IN PLACE and are under no line bound; do not compress them to hit a number. If you
   are not sure how to do this, ask me. Do NOT update CLAUDE.md as part of routine session wrap-up.
```

- [ ] **Step 6: Verify nothing was lost**

The move must be lossless. Confirm every stable section landed exactly once:

```bash
grep -c "^## \|^### " docs/HANDOVER.md docs/PROJECT-NOTES.md
wc -l docs/HANDOVER.md docs/PROJECT-NOTES.md docs/ROADMAP.md
# Spot-check that a distinctive trap survived the move, in exactly one file:
grep -rn "test_a_descendant_of_a_denied_root_still_expands" docs/
grep -rn "UNII_Records_\*.txt, NOT" docs/
```

Expected: `HANDOVER.md` ~120 lines; each grep hits `PROJECT-NOTES.md` and not `HANDOVER.md`.

- [ ] **Step 7: Commit**

```bash
git add docs/HANDOVER.md docs/PROJECT-NOTES.md docs/ROADMAP.md CLAUDE.md \
        .claude/skills/nextsession/SKILL.md
git commit -m "docs: split the volatile half from the stable half (#63)"
```

---

### Task 6: Re-measure through the invocation #60 says is refused

**Files:**
- Modify: `docs/HANDOVER.md`, `docs/PROJECT-NOTES.md`, `docs/ROADMAP.md` (record the round and the measurement)

**Interfaces:**
- Consumes: Tasks 1-5, all committed.
- Produces: the figures the PR body quotes.

This is not optional thoroughness. #60 exists *because* the guard landed after the measurement and the documented command was never re-run against it; a round that fixed the guard without running the command would reproduce the defect it closes.

- [ ] **Step 1: Full suite and lint, green before measuring**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q
ruff check src tests
uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml
```

Expected: all green. Record the test count for the PR body.

- [ ] **Step 2: Build a fresh measurement database**

```bash
psql "host=localhost port=5532 dbname=postgres user=postgres" \
     -c "DROP DATABASE IF EXISTS drugref_policy_cli" \
     -c "CREATE DATABASE drugref_policy_cli"
DSN='host=localhost port=5532 dbname=drugref_policy_cli user=postgres'
uv run drugref --dsn "$DSN" migrate
```

A fresh database, not a reused one: `drugref_policy`, `drugref_ops` and `drugref_planc` all carry drifted ledgers, and `apply_migrations` refuses there permanently. A verification database is disposable — rebuild rather than patch.

- [ ] **Step 3: Run the exact invocation #60 says is refused**

```bash
time uv run drugref --dsn "$DSN" ingest chain --downloads downloads \
  --unii-release 26Feb2026 --medrt-release 2026.07.06 \
  --mesh-release 2026 --mesh-relations-release 2026.07.06
```

Expected: it runs (~103 s). Before this round it failed pre-flight with *"downloads/mesh/desc2026.gz is read by both mesh and mesh-relations, which were given different release tags"*. **If it still fails, Task 2 is incomplete — stop and fix it rather than falling back to the four subcommands.**

- [ ] **Step 4: Confirm every published figure reproduces**

```bash
psql "$DSN" -tAc "
SELECT 'ddi_candidate_pair            ' || count(*) FROM drugref.ddi_candidate_pair
UNION ALL SELECT 'open_question                 ' || count(*) FROM drugref.open_question
UNION ALL SELECT 'gap_dead_by_expansion_policy  ' || count(*) FROM drugref.gap_dead_by_expansion_policy
UNION ALL SELECT 'gap_unreviewed_expansion_root ' || count(*) FROM drugref.gap_unreviewed_expansion_root
UNION ALL SELECT 'expansion_policy_unresolved   ' || count(*) FROM drugref.expansion_policy_unresolved
UNION ALL SELECT 'policy rows / binding         ' || count(*) || ' / ' ||
                 (SELECT count(*) FROM drugref.class_expansion_policy_current)
                 FROM drugref.class_expansion_policy
UNION ALL SELECT 'loaded_release                ' || count(*) FROM drugref.loaded_release
UNION ALL SELECT 'ingest_run_incomplete         ' || count(*) FROM drugref.ingest_run_incomplete"
```

Required: `ddi_candidate_pair` **21,664** · `open_question` **18,834** · `gap_dead_by_expansion_policy` **1** · `gap_unreviewed_expansion_root` **0** · `expansion_policy_unresolved` **0** · policy **14 / 14** · `loaded_release` **4** · `ingest_run_incomplete` **0**.

**Any difference is a finding, not a nuisance.** Investigate before writing it up — this round changed no SQL and no ingest logic, so nothing here has licence to move.

- [ ] **Step 5: Exercise `drugref policy` against the real data**

The CLI tests use a fixture database; this proves the surface works where an operator will use it:

```bash
uv run drugref --dsn "$DSN" policy show | head -20
uv run drugref --dsn "$DSN" policy show --source MED-RT --code N0000009908
uv run drugref --dsn "$DSN" policy withdraw --source MED-RT --code N0000009020 \
  --rationale "measured: subtree no longer matches the 2026.07.06 release" \
  --reviewed-by "$(git config user.name)" --reviewed-against 2026.07.06
uv run drugref --dsn "$DSN" policy show --source MED-RT --code N0000009020
```

Confirm the withdrawal moves `gap_unreviewed_expansion_root` from 0 to 1 and that `policy show` reports the history. This database is disposable, so the write is not restored.

- [ ] **Step 6: Write the round up**

`docs/PROJECT-NOTES.md` gains a section for this round — **the traps only**, in the established voice. At minimum:

- **The `secondary` exemption filters the CLAIM, never the read.** `mesh-relations` still *reads* desc/supp; it just does not date them. A future change that skipped resolving a secondary input would break the orchestrator.
- **`medrt_run.py` names the policy table in PROSE, not SQL** — the operator warning. The naming pin counts it deliberately; a grep that called it a reader would be counting the sentence that explains the rule.
- **`record_expansion_decision` still accepts `withdrawn`; only the CLI refuses it.** The library keeps the door open on purpose (rejecting it would put the vocabulary in a second place), so a caller reaching for it from Python still bypasses both of `withdraw_expansion_decision`'s guarantees.
- **`policy` handlers COMMIT and the library functions do not** — and committed policy rows cannot be deleted, which is why `tests/test_cli_policy.py` restores in a `finally` by recording a further correction.
- **The fourth sweep-closed-but-unfixed** (#61, after #31/#35/#40): `fixed: #61` inside "Filed rather than fixed" closed it. Keep the number away from close/fix/resolve in any inflection — the linker matches on token adjacency, not meaning.

`docs/ROADMAP.md` gains the round under "Cross-cutting hardening", with the measurement table. `docs/HANDOVER.md` is regenerated: this round merged, #59/#60/#61/#63 closed, and the next candidates unchanged (5c · slice 3 · step 8 curation · #36).

- [ ] **Step 7: Commit, push, and open the PR**

```bash
git add docs/
git commit -m "docs: record the policy-surface round, measured through the chain it fixes"
git push -u origin fix/policy-surface-debt-round
```

Open a PR to `main`. The body must state the measurement, and must reference the four issues **with closing keywords only for what is genuinely fixed** — all four are, in this round. Per §6 of the spec, any issue this round does *not* fix must be named in prose with the number kept away from close/fix/resolve.

---

## Self-Review

**Spec coverage:** §2 → Task 2. §3 → Tasks 3 and 4. §4 → Task 1. §5 → Task 5. §6 → Task 5 (the rule text) and Task 6 Step 6 (the trap). §7 → the test steps throughout; §7.1 → Task 6.

**Ordering constraint:** Task 3's naming pin asserts `interactions.py: 3`, which is only true after Task 1 removes the supersession UPDATE *and* Task 3 adds `decision_history`. Task 1 must land first; Step 2 of Task 3 says so explicitly and tells the implementer to stop if the count is wrong.

**Type consistency:** `overlay.supersede` has one signature, used identically in three modules. `live_decisions` returns 4-tuples and `_handle_policy_show` unpacks four; `decision_history` returns 6-tuples and both the test and the handler unpack six. `interactions.WITHDRAWN` is read in three places (the writer, the CLI guard, one test) and defined once.

**Placeholder scan:** clean — every code step carries the actual code, and the two documentation tasks name the exact sections to move and the exact replacement text.

**Blast radius, stated:** Task 1 edits three merged single-writer modules for no behaviour change, which is the round's one real risk. It is mitigated by the existing 810 tests being the regression net and by the move being mechanical (arguments already in the primitive's order). If the suite is not green at Task 1 Step 7, the move is wrong — do not proceed to Task 2 with a failure in hand.

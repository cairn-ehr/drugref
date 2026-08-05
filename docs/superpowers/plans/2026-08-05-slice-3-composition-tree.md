# Slice 3 — Composition Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest GSRS salt/solvate composition into a rebuildable projection that records which registered moieties a specific substance is composed of, and which of those components the release marks pharmacologically active.

**Architecture:** A pure streaming parser (`ingest/gsrs.py`) normalises GSRS's mirror-encoded, direction-inverted relationships into one edge set; a single writer (`composition.py`) owns the table; one orchestrator (`ingest/gsrs_run.py`) owns the transaction. `db/028` adds a two-row vocabulary table, the projection, one read view and one gap view. No new identity is minted and no existing table changes shape.

**Tech Stack:** Python 3.12, `uv`, `psycopg` v3, PostgreSQL ≥ 18, pytest, ruff.

## Global Constraints

- **Spec is authoritative:** `docs/superpowers/specs/2026-08-05-drugref-slice-3-composition-tree-design.md`. If this plan disagrees with it, the spec wins.
- **TDD, always:** failing test first, then the minimal code. Never write implementation before a red test.
- **Inline documentation for junior contributors is mandatory** — every module and non-obvious function gets a docstring explaining *why*, matching the density of `conditions.py` and `provenance.py`.
- **Files under ~500 lines.** `cli.py` is at 452; do not grow it beyond a step declaration and a four-line runner.
- **Run DB tests with:** `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`
- **Lint with:** `ruff check src tests` — **NOT** `ruff check .`, which walks `downloads/` and hangs.
- **Migrations are immutable once applied.** `db/028_composition_tree.sql` may be edited freely while this branch is unmerged; after merge, add `db/029`.
- **Issue-number rule:** near `close`/`fix`/`resolve` in ANY inflection, write the number WITHOUT a `#` ("issue 33"). The GitHub linker binds on token adjacency, not meaning. This has caused four wrong auto-closures.
- **This slice does NOT close issues 33 or 30.** Never write a commit or PR implying it does.
- **The dump lives at** `downloads/GSRS/dump-public-2026-02-26.gsrs` (321,487,817 bytes, gitignored).
- **Source constants:** `SOURCE = "GSRS"`, `WRITER = "gsrs_run"`.

---

### Task 1: The pure parser and its direction convention

The single most dangerous piece of the slice. Inverted, the convention yields a fully-populated, entirely wrong table that no aggregate count would flag.

**Files:**
- Create: `src/drugref/ingest/gsrs.py`
- Test: `tests/test_gsrs_parser.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `gsrs.SALT_SOLVATE: str` = `"SALT_SOLVATE"`, `gsrs.SOLVATE_ANHYDROUS: str` = `"SOLVATE_ANHYDROUS"`
  - `gsrs.CompositionEdge` — frozen dataclass with `substance_unii: str`, `component_unii: str`, `relation: str`
  - `gsrs.GsrsRecord` — frozen dataclass with `unii: str`, `display_name: str | None`, `edges: tuple[CompositionEdge, ...]`, `active_moieties: frozenset[str]`
  - `gsrs.normalise_relationship(record_unii: str, rel_type: str, target_unii: str) -> CompositionEdge | None`
  - `gsrs.iter_records(path) -> Iterator[GsrsRecord]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gsrs_parser.py`:

```python
# tests/test_gsrs_parser.py
"""The direction convention is the whole point of this module (slice-3 spec 3.2).

GSRS stores a relationship of type "A->B" on record X pointing at Y, and X plays
role B while Y plays role A -- the stored edge is the INBOUND one. Read naively,
one "salt" in the real release had 124 parents; read correctly, the busiest PARENTS
are Maleic Acid (124 salts) and Tartaric Acid (123).

These tests use SYNTHETIC identifiers on purpose: the rule under test is about
role assignment, not about any substance's real UNII. The real-release properties
are pinned separately, on extracted bytes, in tests/test_gsrs_fixture.py.
"""
import gzip
import json

import pytest

from drugref.ingest import gsrs


def test_salt_to_parent_puts_the_TARGET_on_the_salt_side():
    # "SALT/SOLVATE->PARENT" stored on X: X is the PARENT, Y is the SALT.
    edge = gsrs.normalise_relationship("PARENT0001", "SALT/SOLVATE->PARENT", "SALT000001")
    assert edge == gsrs.CompositionEdge(
        substance_unii="SALT000001",
        component_unii="PARENT0001",
        relation=gsrs.SALT_SOLVATE)


def test_parent_to_salt_puts_the_RECORD_on_the_salt_side():
    # The mirror encoding of the SAME edge, from the other end.
    edge = gsrs.normalise_relationship("SALT000001", "PARENT->SALT/SOLVATE", "PARENT0001")
    assert edge == gsrs.CompositionEdge(
        substance_unii="SALT000001",
        component_unii="PARENT0001",
        relation=gsrs.SALT_SOLVATE)


def test_the_two_salt_encodings_normalise_to_one_identical_edge():
    """The mirror check, in miniature. On the real release these agree on 15,039
    edges; if the convention were inverted they would agree on essentially none."""
    a = gsrs.normalise_relationship("PARENT0001", "SALT/SOLVATE->PARENT", "SALT000001")
    b = gsrs.normalise_relationship("SALT000001", "PARENT->SALT/SOLVATE", "PARENT0001")
    assert a == b


def test_solvate_axis_puts_the_HYDRATE_on_the_substance_side():
    # "ANHYDROUS->SOLVATE" on X: X is the SOLVATE, Y is the ANHYDROUS form.
    # The hydrate is the composite; the anhydrous form is its component.
    edge = gsrs.normalise_relationship("HYDRATE001", "ANHYDROUS->SOLVATE", "ANHYDROUS1")
    assert edge == gsrs.CompositionEdge(
        substance_unii="HYDRATE001",
        component_unii="ANHYDROUS1",
        relation=gsrs.SOLVATE_ANHYDROUS)
    mirror = gsrs.normalise_relationship("ANHYDROUS1", "SOLVATE->ANHYDROUS", "HYDRATE001")
    assert mirror == edge


def test_active_moiety_is_not_a_composition_edge():
    """ACTIVE MOIETY is the ION level and must never become an edge (spec 3.1).

    Using it as one asserts that levomefolate magnesium is interchangeable with
    magnesium sulfate -- 35 substances share MAGNESIUM CATION, 27 of them drugref
    moieties. It reaches the table only through is_active_component.
    """
    assert gsrs.normalise_relationship("SALT000001", "ACTIVE MOIETY", "ION0000001") is None


def test_unrelated_relationship_types_are_ignored():
    for rel_type in ("IMPURITY->PARENT", "METABOLITE->PARENT", "TARGET->INHIBITOR",
                     "BASIS OF STRENGTH->SUBSTANCE", "RACEMATE->ENANTIOMER"):
        assert gsrs.normalise_relationship("X000000001", rel_type, "Y000000001") is None


def test_self_edges_are_dropped():
    """A record relating to itself is not a composition; 23,944 ACTIVE MOIETY
    self-edges exist in the release and 12 salt self-edges. Without this filter
    every moiety becomes its own component."""
    assert gsrs.normalise_relationship("SAME000001", "PARENT->SALT/SOLVATE", "SAME000001") is None


def _write_dump(tmp_path, records):
    """Write records in the real dump's shape: gzip, JSON-lines, TWO TAB characters
    prefixing each line before the '{'."""
    path = tmp_path / "dump-public-test.gsrs"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for rec in records:
            fh.write("\t\t" + json.dumps(rec) + "\n")
    return path


def test_iter_records_reads_the_two_tab_prefixed_json_lines(tmp_path):
    path = _write_dump(tmp_path, [{
        "approvalID": "SALT000001",
        "names": [{"name": "Test salt", "displayName": True}],
        "relationships": [
            {"type": "PARENT->SALT/SOLVATE",
             "relatedSubstance": {"approvalID": "PARENT0001"}},
            {"type": "ACTIVE MOIETY",
             "relatedSubstance": {"approvalID": "PARENT0001"}},
        ],
    }])
    records = list(gsrs.iter_records(path))
    assert len(records) == 1
    rec = records[0]
    assert rec.unii == "SALT000001"
    assert rec.display_name == "Test salt"
    assert rec.edges == (gsrs.CompositionEdge("SALT000001", "PARENT0001", gsrs.SALT_SOLVATE),)
    assert rec.active_moieties == frozenset({"PARENT0001"})


def test_a_record_with_no_unii_is_skipped(tmp_path):
    """5,078 of the release's 173,080 records carry no approvalID; they cannot join
    to anything and are dropped here rather than half-way down the writer."""
    path = _write_dump(tmp_path, [{"names": [], "relationships": []}])
    assert list(gsrs.iter_records(path)) == []


def test_a_self_active_moiety_does_not_count_as_a_ruling(tmp_path):
    """23,944 edges are a substance asserting it IS its own active moiety. That says
    nothing about WHICH COMPONENT is active, so active_moieties stays empty and the
    writer will record is_active_component = NULL (unruled), not false."""
    path = _write_dump(tmp_path, [{
        "approvalID": "SALT000001",
        "relationships": [
            {"type": "PARENT->SALT/SOLVATE",
             "relatedSubstance": {"approvalID": "PARENT0001"}},
            {"type": "ACTIVE MOIETY",
             "relatedSubstance": {"approvalID": "SALT000001"}},
        ],
    }])
    rec = next(iter(gsrs.iter_records(path)))
    assert rec.active_moieties == frozenset()


def test_a_multi_component_salt_keeps_every_component(tmp_path):
    """ZINC GLYCINATE CITRATE has three. 1,089 salts (7.7%) have more than one
    parent, so a single parent_moiety_uuid column would silently truncate them."""
    path = _write_dump(tmp_path, [{
        "approvalID": "TRIPLE0001",
        "relationships": [
            {"type": "PARENT->SALT/SOLVATE", "relatedSubstance": {"approvalID": "COMP000001"}},
            {"type": "PARENT->SALT/SOLVATE", "relatedSubstance": {"approvalID": "COMP000002"}},
            {"type": "PARENT->SALT/SOLVATE", "relatedSubstance": {"approvalID": "COMP000003"}},
        ],
    }])
    rec = next(iter(gsrs.iter_records(path)))
    assert {e.component_unii for e in rec.edges} == {"COMP000001", "COMP000002", "COMP000003"}


def test_a_malformed_line_does_not_abort_the_stream(tmp_path):
    """2.05 GB of upstream JSON: one bad line must not lose the other 173,079."""
    path = tmp_path / "dump-public-bad.gsrs"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("\t\t{not json at all\n")
        fh.write("\t\t" + json.dumps({"approvalID": "GOOD000001"}) + "\n")
    assert [r.unii for r in gsrs.iter_records(path)] == ["GOOD000001"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_gsrs_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.ingest.gsrs'`

- [ ] **Step 3: Write the parser**

Create `src/drugref/ingest/gsrs.py`:

```python
# src/drugref/ingest/gsrs.py
"""Pure, streaming parser for the GSRS public data dump (slice 3).

Reads ~2.05 GB of JSON-lines without holding it in memory, and TOUCHES NO
DATABASE -- the architecture invariant every ingest parser in this repo obeys.
The orchestrator (ingest/gsrs_run.py) owns the transaction.

THE DIRECTION CONVENTION IS THE WHOLE MODULE, and it is inverted from the naive
reading. For a relationship of type "A->B" stored on record X and pointing at Y:

    X plays role B, and Y plays role A.

The stored relationship is the INBOUND edge. This is the same class of upstream
erratum as MED-RT's "Parent Of runs parent -> child" (PROJECT-NOTES), and like
that one it is invisible to a small fixture: read naively, one "salt" in the real
release had 124 parents; read correctly, the busiest PARENTS are Maleic Acid
(124 salts), Tartaric Acid (123) and citric acid (117) -- exactly the counterions
a base should have many salts of. Two independent checks pin it (test_gsrs_fixture):
the two mirror encodings agree on 15,039 edges, and every solvate has exactly one
anhydrous parent.

WHAT IS DELIBERATELY NOT AN EDGE. `ACTIVE MOIETY` is the ION level, not a
composition: 71% of its 33,647 edges are self-references, and every magnesium form
-- including drugref's own moiety -- points at MAGNESIUM CATION. As an equivalence
join it would assert that levomefolate magnesium is interchangeable with magnesium
sulfate (35 substances share that cation, 27 of them drugref moieties), which is
the discredited sulfonamide inference one level down. It reaches the projection
ONLY as `is_active_component`, a discriminator INSIDE a composition.

Likewise NOT an edge: the top-level `moieties` key, which holds STRUCTURAL
fragments of the chemical (two for chlortetracycline bisulfate, carrying no UNII)
and is a different concept that merely shares a word.
"""
import dataclasses
import gzip
import json
import logging
import pathlib
from collections.abc import Iterator

# Declared locally, exactly as ingest/checksum.py and ingest/mesh.py each do. There
# is no shared paths module and one type alias does not justify creating one.
StrPath = str | pathlib.Path

log = logging.getLogger(__name__)

# The two composition axes, as stored in substance_composition.relation. These
# strings are also seeded into db/028's composition_relation table, which the
# column is a foreign key into -- the vocabulary's one home is the TABLE, and
# these constants exist only so the writer can name a row it inserts.
SALT_SOLVATE = "SALT_SOLVATE"
SOLVATE_ANHYDROUS = "SOLVATE_ANHYDROUS"

# The direction convention, as data rather than as four if-branches.
#
# Each entry maps an upstream relationship type to (relation, record_is_composite).
# `record_is_composite` says which end of the stored relationship is the COMPOSITE:
# True  -- the record holding the relationship is the salt/solvate, the target is
#          the component;
# False -- the record is the component (the parent base or anhydrous form) and the
#          TARGET is the composite.
#
# Read it against the convention above: for "PARENT->SALT/SOLVATE" the record plays
# the right-hand role (SALT/SOLVATE), so it is the composite.
_AXES = {
    "PARENT->SALT/SOLVATE": (SALT_SOLVATE, True),
    "SALT/SOLVATE->PARENT": (SALT_SOLVATE, False),
    "ANHYDROUS->SOLVATE": (SOLVATE_ANHYDROUS, True),
    "SOLVATE->ANHYDROUS": (SOLVATE_ANHYDROUS, False),
}

ACTIVE_MOIETY = "ACTIVE MOIETY"


@dataclasses.dataclass(frozen=True)
class CompositionEdge:
    """One composition statement, normalised so both encodings produce one row."""
    substance_unii: str    # the COMPOSITE (a salt, or a hydrate)
    component_unii: str    # what it is composed of
    relation: str          # SALT_SOLVATE | SOLVATE_ANHYDROUS


@dataclasses.dataclass(frozen=True)
class GsrsRecord:
    """One substance, reduced to the three things slice 3 needs."""
    unii: str
    display_name: str | None
    edges: tuple[CompositionEdge, ...]
    # NON-SELF active-moiety targets. Empty means the release says nothing about
    # which component is active -- which the writer records as NULL (unruled), NOT
    # as false. A self-reference is not a ruling about a component either, so it is
    # excluded here rather than downstream.
    active_moieties: frozenset[str]


def normalise_relationship(record_unii: str, rel_type: str,
                           target_unii: str) -> CompositionEdge | None:
    """Turn one stored relationship into a normalised edge, or None.

    None means "not a composition statement" -- an unrelated type, an ACTIVE MOIETY
    (which is handled separately), or a self-edge. Pure: no I/O, no state.
    """
    axis = _AXES.get(rel_type)
    if axis is None or record_unii == target_unii:
        return None
    relation, record_is_composite = axis
    composite, component = ((record_unii, target_unii) if record_is_composite
                            else (target_unii, record_unii))
    return CompositionEdge(substance_unii=composite, component_unii=component,
                           relation=relation)


def _display_name(record: dict) -> str | None:
    for name in record.get("names") or []:
        if name.get("displayName"):
            return name.get("name")
    return None


def iter_records(path: StrPath) -> Iterator[GsrsRecord]:
    """Stream the dump, yielding one GsrsRecord per substance carrying a UNII.

    THE LINE FORMAT IS NOT PLAIN JSON-LINES: every line is prefixed by two TAB
    characters before the '{'. Slicing from the first brace rather than stripping a
    fixed prefix keeps this working if upstream changes the padding.

    A record with no `approvalID` is skipped -- 5,078 of 173,080 carry none, and
    they can join to nothing. A malformed line is logged and skipped rather than
    aborting: one bad line in 2.05 GB must not cost the other 173,079 records.
    """
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for lineno, line in enumerate(handle, start=1):
            brace = line.find("{")
            if brace < 0:
                continue
            try:
                record = json.loads(line[brace:])
            except ValueError:
                log.warning("gsrs: skipping unparseable line %d", lineno)
                continue
            unii = record.get("approvalID")
            if not unii:
                continue

            edges: list[CompositionEdge] = []
            actives: set[str] = set()
            for relationship in record.get("relationships") or []:
                target = (relationship.get("relatedSubstance") or {}).get("approvalID")
                if not target:
                    continue
                rel_type = relationship.get("type")
                if rel_type == ACTIVE_MOIETY:
                    if target != unii:
                        actives.add(target)
                    continue
                edge = normalise_relationship(unii, rel_type, target)
                if edge is not None:
                    edges.append(edge)

            yield GsrsRecord(unii=unii, display_name=_display_name(record),
                             edges=tuple(dict.fromkeys(edges)),
                             active_moieties=frozenset(actives))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_gsrs_parser.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Lint**

Run: `ruff check src tests && ruff format --check src/drugref/ingest/gsrs.py tests/test_gsrs_parser.py`

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/gsrs.py tests/test_gsrs_parser.py
git commit -m "feat(gsrs): pure streaming parser, with the inverted direction convention in one place

For a relationship of type A->B stored on record X pointing at Y, X plays role B.
Read naively one salt had 124 parents; read correctly the busiest parents are the
counterions. ACTIVE MOIETY is deliberately NOT an edge -- it is the ion level, and
as an equivalence join it merges levomefolate magnesium with magnesium sulfate."
```

---

### Task 2: The real-release fixture, and the two checks that pin the convention

Synthetic tests prove the rule; only extracted bytes prove it is the rule *this release* needs. The last hand-written fixture in this repo invented an `INN_ID`, a CAS and a UNII.

**Files:**
- Create: `tests/fixtures/make_gsrs_subset.py`
- Create: `tests/fixtures/gsrs_subset.gsrs` (generated, committed)
- Test: `tests/test_gsrs_fixture.py`

**Interfaces:**
- Consumes: `gsrs.iter_records`, `gsrs.CompositionEdge`, `gsrs.SALT_SOLVATE`, `gsrs.SOLVATE_ANHYDROUS` (Task 1).
- Produces: `tests/fixtures/gsrs_subset.gsrs`, consumed by Tasks 4, 6 and 7.

- [ ] **Step 1: Write the extractor**

Create `tests/fixtures/make_gsrs_subset.py`:

```python
#!/usr/bin/env python3
"""Cut tests/fixtures/gsrs_subset.gsrs from the real GSRS public dump.

Usage:
    python tests/fixtures/make_gsrs_subset.py \
        downloads/GSRS/dump-public-2026-02-26.gsrs tests/fixtures/gsrs_subset.gsrs

The OUTPUT IS GZIPPED, because gsrs.iter_records opens with gzip.open -- the fixture
has to be the same shape as the real release or it tests a format that never ships.

COMMITTED AND RE-RUNNABLE because every fixture in this repo is extracted from a
real release, never hand-written: slice 5b found five spec errors that only real
bytes surfaced, and the last hand-written fixture invented three identifiers that
do not exist.

WHAT IS KEPT, and why each one is load-bearing (slice-3 spec 7.3):

  1. a single-parent salt                      -- the ordinary case
  2. ZINC GLYCINATE CITRATE (H3472PJ7YA)       -- THREE components; a single-FK
                                                  schema truncates it silently
  3. a solvate/anhydrous pair                  -- the second axis
  4. an active-vs-counterion discrimination    -- so a mutation defaulting NULL is caught
  5. BOTH mirror encodings of one edge         -- the direction test on real bytes
  6. a composite with components but NO active moiety -- so the gap view has a row
  7. the magnesium family                      -- the case slice 3 does NOT resolve

The magnesium family is kept precisely BECAUSE it fails. Issue 33 predicted that
GSRS gives ML30MJ2U7I -> DE08037SAB; it does not, and DE08037SAB has ZERO inbound
references across all 173,080 records. A future change that "fixes" that by joining
on shared ACTIVE MOIETY must fail a test, not pass one.
"""
import gzip
import json
import sys

# Every UNII the fixture must carry, with the role it plays. Kept as an explicit
# allow-list rather than a sampling rule so the fixture is reproducible byte-for-byte.
WANTED = {
    # (2) multi-component salt and its three components
    "H3472PJ7YA": "ZINC GLYCINATE CITRATE -- three components",
    "13S1S8SF37": "ZINC CATION -- its ACTIVE component",
    "TE7660XO1C": "Glycine -- a counterion",
    "XF417D3PSL": "Anhydrous citric acid -- a counterion, and 117 other salts' parent",
    # (1) + (5) a single-parent salt whose edge is stored from BOTH ends
    "1D06KZ672I": "CHLORTETRACYCLINE BISULFATE -- single parent, mirror-encoded",
    "WCK1KIQ23Q": "Chlortetracycline -- its parent and ACTIVE MOIETY",
    # (6) a composite with components but NO active-moiety ruling -- the gap-kind-12
    #     case. WITHOUT THIS the gap view has no real positive example and every
    #     downstream test of it passes vacuously over an empty set (found by the
    #     Task 2 review; the first version of this dict required the case in the
    #     docstring above and then omitted it here).
    "88496G1ERL": "PHYTATE SODIUM -- one composite edge, ZERO active-moiety edges",
    "7IGF0S7R8I": "its component, so the fixture holds both ends of that edge",
    # (3) + (7) the magnesium family: the solvate axis, and the refuted case
    "SK47B8698T": "Magnesium sulfate heptahydrate -- solvate",
    "ML30MJ2U7I": "Magnesium sulfate anhydrous -- its anhydrous form",
    "DE08037SAB": "MAGNESIUM SULFATE, UNSPECIFIED FORM -- drugref's moiety, 0 inbound refs",
    "T6V3LHY838": "MAGNESIUM CATION -- the active moiety GSRS names, NOT a drugref moiety",
    "02F3473H9O": "MAGNESIUM CHLORIDE -- shares that cation; the merge to refuse",
    "1VZZ62R081": "LEVOMEFOLATE MAGNESIUM -- shares it too; the merge that is absurd",
}


def main(dump_path: str, out_path: str) -> None:
    kept = set()
    with gzip.open(dump_path, "rt", encoding="utf-8", errors="replace") as handle, \
            gzip.open(out_path, "wt", encoding="utf-8") as out:
        for line in handle:
            brace = line.find("{")
            if brace < 0:
                continue
            # Cheap pre-filter before paying for json.loads on 173,080 records:
            # the UNII appears verbatim in the line if the record is one we want.
            if not any(unii in line for unii in WANTED if unii not in kept):
                continue
            record = json.loads(line[brace:])
            unii = record.get("approvalID")
            if unii in WANTED and unii not in kept:
                kept.add(unii)
                # Copied VERBATIM, two-tab prefix and all, so the fixture is real
                # bytes rather than a re-serialisation of our own parse.
                out.write(line if line.endswith("\n") else line + "\n")
    missing = sorted(set(WANTED) - kept)
    if missing:
        raise SystemExit(f"FIXTURE INCOMPLETE -- not found in the dump: {missing}")
    print(f"wrote {len(kept)} records to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
```

- [ ] **Step 2: Generate the fixture**

```bash
python tests/fixtures/make_gsrs_subset.py \
    downloads/GSRS/dump-public-2026-02-26.gsrs tests/fixtures/gsrs_subset.gsrs
```

Expected on stderr: `wrote 14 records to tests/fixtures/gsrs_subset.gsrs`. If it raises
`FIXTURE INCOMPLETE`, do **not** hand-edit the fixture — investigate why the record is
absent, because the same absence will hit production.

- [ ] **Step 3: Write the failing test**

Create `tests/test_gsrs_fixture.py`:

```python
# tests/test_gsrs_fixture.py
"""The direction convention, pinned on REAL RELEASE BYTES (slice-3 spec 3.2, 9.1).

tests/test_gsrs_parser.py proves the rule on synthetic input. This module proves
it is the rule the 2026-02-26 release actually needs, using the two checks that
distinguish the correct reading from the inverted one:

  * THE MIRROR CHECK -- GSRS stores most edges from both ends, and under the
    correct convention the two encodings normalise to the SAME edge. Under the
    inverted one they would produce two different, both-wrong edges.
  * THE FUNCTIONAL CHECK -- every solvate has exactly ONE anhydrous parent. Under
    the inverted reading the cardinality is many-to-many and meaningless.

DO NOT DELETE EITHER. Inverted, the convention produces a fully populated,
entirely wrong table that no aggregate count would flag.
"""
import collections
import pathlib

from drugref.ingest import gsrs

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "gsrs_subset.gsrs"


def _records():
    return list(gsrs.iter_records(FIXTURE))


def test_the_fixture_carries_every_role_the_slice_depends_on():
    uniis = {r.unii for r in _records()}
    for unii in ("H3472PJ7YA", "13S1S8SF37", "1D06KZ672I", "WCK1KIQ23Q",
                 "SK47B8698T", "ML30MJ2U7I", "DE08037SAB", "T6V3LHY838",
                 "88496G1ERL"):
        assert unii in uniis, f"{unii} missing -- regenerate with make_gsrs_subset.py"


def test_the_mirror_encodings_agree_on_real_bytes():
    """Both ends of one edge normalise to one identical CompositionEdge."""
    edges = collections.Counter()
    for record in _records():
        for edge in record.edges:
            edges[edge] += 1
    # The bisulfate/chlortetracycline edge is stored from both ends in the release.
    mirrored = gsrs.CompositionEdge("1D06KZ672I", "WCK1KIQ23Q", gsrs.SALT_SOLVATE)
    assert mirrored in edges, (
        "the salt->parent edge did not normalise as expected -- the direction "
        "convention is inverted, and every row of substance_composition is wrong")


def test_every_solvate_has_exactly_one_anhydrous_parent():
    """The functional check. On the full release: {1: 1635}, MAX = 1."""
    parents = collections.defaultdict(set)
    for record in _records():
        for edge in record.edges:
            if edge.relation == gsrs.SOLVATE_ANHYDROUS:
                parents[edge.substance_unii].add(edge.component_unii)
    assert parents, "the fixture carries no solvate edge -- regenerate it"
    assert max(len(v) for v in parents.values()) == 1


def test_the_heptahydrate_is_the_composite_not_the_component():
    """The concrete direction case: magnesium sulfate HEPTAHYDRATE is composed of
    the ANHYDROUS form, never the reverse."""
    edges = {e for r in _records() for e in r.edges
             if e.relation == gsrs.SOLVATE_ANHYDROUS}
    assert gsrs.CompositionEdge("SK47B8698T", "ML30MJ2U7I", gsrs.SOLVATE_ANHYDROUS) in edges
    assert gsrs.CompositionEdge("ML30MJ2U7I", "SK47B8698T", gsrs.SOLVATE_ANHYDROUS) not in edges


def test_zinc_glycinate_citrate_keeps_all_three_components():
    """The case a single parent_moiety_uuid column truncates silently."""
    record = next(r for r in _records() if r.unii == "H3472PJ7YA")
    components = {e.component_unii for e in record.edges
                  if e.relation == gsrs.SALT_SOLVATE}
    assert components == {"13S1S8SF37", "TE7660XO1C", "XF417D3PSL"}


def test_a_composite_with_no_active_moiety_ruling_is_present():
    """Gap kind 12's positive example, on real bytes. Asserts the record IS a
    composite as well as unruled -- a test that only checked the empty active set
    would pass for every non-composite in the fixture."""
    record = next(r for r in _records() if r.unii == "88496G1ERL")
    assert record.edges, "PHYTATE SODIUM must carry a composition edge"
    assert record.active_moieties == frozenset()


def test_the_active_component_is_distinguished_from_the_counterions():
    """ZINC CATION is active; glycine and citric acid are not. This is what stops
    a rule on citric acid reaching every salt containing it."""
    record = next(r for r in _records() if r.unii == "H3472PJ7YA")
    assert "13S1S8SF37" in record.active_moieties
    assert "TE7660XO1C" not in record.active_moieties
    assert "XF417D3PSL" not in record.active_moieties


def test_nothing_points_at_drugrefs_magnesium_moiety():
    """Issue 33's own proposed fix, refuted on the bytes (spec 8).

    It predicted ML30MJ2U7I -> DE08037SAB and SK47B8698T -> DE08037SAB. Neither
    exists: DE08037SAB has ZERO inbound references across the whole release.
    """
    targets = {e.component_unii for r in _records() for e in r.edges}
    targets |= {a for r in _records() for a in r.active_moieties}
    assert "DE08037SAB" not in targets


def test_the_magnesium_family_shares_an_active_moiety_and_that_is_not_a_composition():
    """The merge this slice refuses. Magnesium sulfate, magnesium chloride and
    LEVOMEFOLATE MAGNESIUM all name MAGNESIUM CATION as their active moiety. If a
    future change turns ACTIVE MOIETY into a composition edge, this fails.
    """
    by_unii = {r.unii: r for r in _records()}
    for unii in ("DE08037SAB", "02F3473H9O", "1VZZ62R081"):
        assert "T6V3LHY838" in by_unii[unii].active_moieties
    # ...and none of them is composed of the others.
    edges = {e for r in _records() for e in r.edges}
    assert not any(e.substance_unii == "1VZZ62R081" and e.component_unii == "DE08037SAB"
                   for e in edges)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_gsrs_fixture.py -v`
Expected: PASS, 8 tests. A failure here means either the fixture is incomplete (regenerate) or the convention is wrong (fix `gsrs.py`, never the test).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/make_gsrs_subset.py tests/fixtures/gsrs_subset.gsrs tests/test_gsrs_fixture.py
git commit -m "test(gsrs): extract the fixture from the real release, pin the convention on its bytes

Keeps the magnesium family precisely BECAUSE it fails: issue 33 predicted
ML30MJ2U7I -> DE08037SAB and nothing in GSRS points at DE08037SAB at all."
```

---

### Task 3: Schema `db/028` — vocabulary, projection, read view, gap view

**Files:**
- Create: `db/028_composition_tree.sql`
- Test: `tests/test_schema_composition.py`

**Interfaces:**
- Consumes: `drugref.substance_moiety`, `drugref.ingest_run` (existing).
- Produces: tables `drugref.composition_relation`, `drugref.substance_composition`; views `drugref.moiety_active_in_composite`, `drugref.gap_unruled_composition_activity`; widened CHECKs `ingest_run_source` and `ingest_run_writer`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema_composition.py`:

```python
# tests/test_schema_composition.py
"""db/028: the composition projection, its vocabulary, and its two views."""
import uuid

import psycopg
import pytest

from drugref import ids


@pytest.fixture
def gsrs_run(conn):
    """An ingest_run under the NEW source and writer -- both CHECKs must admit them."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('GSRS', '2026-02-26', 'test', 'gsrs_run') "
        "RETURNING ingest_run_id").fetchone()[0]


@pytest.fixture
def two_moieties(conn, gsrs_run):
    out = []
    for unii in ("COMPONENT1", "COMPONENT2"):
        moiety_uuid = ids.mint_moiety_uuid(unii)
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (moiety_uuid, unii, gsrs_run))
        out.append(moiety_uuid)
    return out


def test_the_source_and_writer_checks_admit_gsrs(gsrs_run):
    """The trio: ingest_run's source CHECK, its writer CHECK, ids._SOURCE_CANONICAL.
    Missing the ingest_run source CHECK stops everything, because every projection
    row carries an ingest_run."""
    assert gsrs_run is not None


def test_the_relation_vocabulary_has_exactly_two_rows(conn):
    rows = conn.execute(
        "SELECT relation FROM drugref.composition_relation ORDER BY relation").fetchall()
    assert [r[0] for r in rows] == ["SALT_SOLVATE", "SOLVATE_ANHYDROUS"]


def test_relation_is_a_foreign_key_not_a_check(conn, gsrs_run, two_moieties):
    """db/006's precedent: the vocabulary lives in a TABLE the column references,
    so it has one home. A CHECK would be a second."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.substance_composition "
            "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
            "VALUES ('SALT000001', %s, 'INVENTED', NULL, %s)",
            (two_moieties[0], gsrs_run))


def test_is_active_component_has_no_default_and_accepts_null(conn, gsrs_run, two_moieties):
    """NULL means UNRULED, not inactive (spec 5.2). 2,641 rows land here."""
    conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES ('SALT000001', %s, 'SALT_SOLVATE', NULL, %s)",
        (two_moieties[0], gsrs_run))
    stored = conn.execute(
        "SELECT is_active_component FROM drugref.substance_composition "
        "WHERE substance_unii = 'SALT000001'").fetchone()[0]
    assert stored is None

    default = conn.execute(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = 'substance_composition' "
        "AND column_name = 'is_active_component'").fetchone()[0]
    assert default is None, "a DEFAULT would turn 'unruled' into an answer nobody gave"


def test_substance_unii_is_not_a_foreign_key(conn):
    """4,425 of 7,377 composites are not moieties (spec 5.1). An FK deletes them."""
    fks = conn.execute(
        "SELECT count(*) FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage k "
        "  ON tc.constraint_name = k.constraint_name "
        "WHERE tc.table_schema = 'drugref' "
        "  AND tc.table_name = 'substance_composition' "
        "  AND tc.constraint_type = 'FOREIGN KEY' "
        "  AND k.column_name = 'substance_unii'").fetchone()[0]
    assert fks == 0


def test_a_composite_may_carry_several_components(conn, gsrs_run, two_moieties):
    """ZINC GLYCINATE CITRATE has three. The PK must not collapse them."""
    for moiety_uuid in two_moieties:
        conn.execute(
            "INSERT INTO drugref.substance_composition "
            "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
            "VALUES ('MULTI00001', %s, 'SALT_SOLVATE', NULL, %s)",
            (moiety_uuid, gsrs_run))
    assert conn.execute(
        "SELECT count(*) FROM drugref.substance_composition "
        "WHERE substance_unii = 'MULTI00001'").fetchone()[0] == 2


def test_the_read_view_shows_only_TRUE(conn, gsrs_run, two_moieties):
    """false propagates nothing and NULL propagates nothing (spec 6.1)."""
    active, counterion = two_moieties
    conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES ('SALT000001', %s, 'SALT_SOLVATE', true, %s), "
        "       ('SALT000001', %s, 'SALT_SOLVATE', false, %s), "
        "       ('SALT000002', %s, 'SALT_SOLVATE', NULL, %s)",
        (active, gsrs_run, counterion, gsrs_run, active, gsrs_run))
    rows = conn.execute(
        "SELECT moiety_uuid, substance_unii FROM drugref.moiety_active_in_composite "
        "ORDER BY substance_unii").fetchall()
    assert rows == [(active, "SALT000001")]


def test_the_gap_view_reports_only_wholly_unruled_composites(conn, gsrs_run, two_moieties):
    """A composite with ANY ruling has been reviewed and leaves the queue --
    the same posture as gap_ungraded_contribution, where an explicit `minor`
    is a review."""
    active, other = two_moieties
    conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES ('RULED00001', %s, 'SALT_SOLVATE', true, %s), "
        "       ('UNRULED001', %s, 'SALT_SOLVATE', NULL, %s), "
        "       ('UNRULED001', %s, 'SALT_SOLVATE', NULL, %s)",
        (active, gsrs_run, active, gsrs_run, other, gsrs_run))
    rows = conn.execute(
        "SELECT substance_unii, component_count "
        "FROM drugref.gap_unruled_composition_activity").fetchall()
    assert rows == [("UNRULED001", 2)]


def test_the_gap_views_grain_is_the_gap_keys_grain(conn, gsrs_run, two_moieties):
    """Standing rule (#41): a view grouping more coarsely than its key folds two
    gaps onto one immortal question_uuid. The key is the composite; so is the grain."""
    active, other = two_moieties
    conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES ('UNRULED001', %s, 'SALT_SOLVATE', NULL, %s), "
        "       ('UNRULED002', %s, 'SALT_SOLVATE', NULL, %s)",
        (active, gsrs_run, other, gsrs_run))
    keys = conn.execute(
        "SELECT substance_unii FROM drugref.gap_unruled_composition_activity "
        "ORDER BY substance_unii").fetchall()
    assert [k[0] for k in keys] == ["UNRULED001", "UNRULED002"]
```

- [ ] **Step 2: Run to verify failure**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_schema_composition.py -v`
Expected: FAIL — `relation "drugref.substance_composition" does not exist`, and the `gsrs_run` fixture fails the `ingest_run_source` CHECK.

- [ ] **Step 3: Write the migration**

Create `db/028_composition_tree.sql`:

```sql
-- db/028_composition_tree.sql
-- Slice 3: the composition tree -- which registered moieties a specific substance
-- (a salt, a hydrate) is composed of, and which of them the release marks active.
--
-- Spec: docs/superpowers/specs/2026-08-05-drugref-slice-3-composition-tree-design.md
--
-- SHAPE: composition EDGES over ONE registry. There is no second registry and no
-- second immortal identity. 4,425 of 7,377 composites are not drugref moieties at
-- all, and 3,195 GSRS salts ALREADY ARE moieties (admitted by the gate, immortal,
-- and undemotable) -- so the composite side is a KEY FROM THE SOURCE and only the
-- component side is a drugref identity. A row may be a moiety AND have components;
-- those are statements about different things.
--
-- REBUILDABLE PROJECTION, outside slice 1's append-only floor: a substance whose
-- composition upstream corrects must be able to change, exactly as class_membership
-- and class_contraindication can.

-- ============================================================================
-- 1. The source and writer trio (db/020's note, and db/025's)
-- ============================================================================
-- Three places pin an authority's spelling: ingest_run's source CHECK, the writer
-- CHECK, and ids._SOURCE_CANONICAL (Python, edited in Task 6). Missing the
-- ingest_run source CHECK stops everything, because every projection row below
-- carries an ingest_run.
--
-- NOTE the constraint names are `ingest_run_source` and `ingest_run_writer`, NOT
-- the `..._check` suffix Postgres auto-generates for unnamed CHECKs -- db/005 and
-- db/025 named them explicitly, and db/009 and db/020 both record the trap.
--
-- substance_class_source is DELIBERATELY NOT WIDENED: GSRS defines no classes, and
-- admitting a source to a CHECK it will never write to invites a future writer to
-- believe it may.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS', 'DRUGREF', 'GSRS'));

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed', 'gsrs_run'));

-- ============================================================================
-- 2. The relation vocabulary -- a TABLE, not a CHECK
-- ============================================================================
-- db/006's precedent, and the standing rule it produced: a vocabulary written down
-- twice is two things that can disagree. The column below is a FOREIGN KEY into
-- this table, so the values have exactly one home and an error message can quote it.
CREATE TABLE IF NOT EXISTS drugref.composition_relation (
    relation    text PRIMARY KEY,
    description text NOT NULL
);

INSERT INTO drugref.composition_relation (relation, description) VALUES
    ('SALT_SOLVATE',
     'The substance is a salt or solvate of the component. Normalised from GSRS''s '
     'mirror-encoded SALT/SOLVATE->PARENT and PARENT->SALT/SOLVATE relationships.'),
    ('SOLVATE_ANHYDROUS',
     'The substance is a solvate (typically a hydrate) of the component''s anhydrous '
     'form. Normalised from ANHYDROUS->SOLVATE and SOLVATE->ANHYDROUS. Every solvate '
     'in the 2026-02-26 release has exactly ONE anhydrous parent.')
ON CONFLICT (relation) DO NOTHING;

-- ============================================================================
-- 3. The projection
-- ============================================================================
CREATE TABLE IF NOT EXISTS drugref.substance_composition (
    -- The COMPOSITE. Deliberately TEXT and NOT a foreign key: 4,425 of 7,377
    -- composites are not drugref moieties, and this slice mints no identity for
    -- them. Adding an FK "for safety" deletes two-thirds of the table.
    substance_unii      text   NOT NULL,
    component_moiety    uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    relation            text   NOT NULL REFERENCES drugref.composition_relation(relation),
    -- NULL means UNRULED -- the release says nothing about which component is
    -- active -- and NOT inactive. NO DEFAULT, for the reason `allow` is not the
    -- same as absent in class_expansion_policy and `withdrawn` is not `allow`:
    -- 2,641 rows land here, and defaulting them to false silently retires a
    -- question nobody answered, while defaulting to true propagates through
    -- counterions.
    is_active_component boolean,
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (substance_unii, component_moiety, relation)
);

-- The read view's join column. The PK already serves lookups BY COMPOSITE.
CREATE INDEX IF NOT EXISTS substance_composition_by_component
    ON drugref.substance_composition (component_moiety)
    WHERE is_active_component;

COMMENT ON TABLE drugref.substance_composition IS
    'Which registered moieties a specific substance is composed of (slice 3, GSRS). '
    'A rebuildable projection keyed by ingest_run.source = ''GSRS''. The composite '
    'side is a UNII from the source and is NOT a drugref identity: 4,425 of 7,377 '
    'composites are not moieties. Measured on 2026-02-26: 8,671 rows (7,962 '
    'SALT_SOLVATE + 709 SOLVATE_ANHYDROUS) over 7,377 composites and 4,433 component '
    'moieties; 4,092 moieties (21.1% of the registry) gain at least one child.';

COMMENT ON COLUMN drugref.substance_composition.is_active_component IS
    'Whether the release marks this component as what makes the composite '
    'pharmacologically active. TRUE 5,029 / FALSE 1,001 / NULL 2,641 on 2026-02-26. '
    'NULL means UNRULED, never inactive -- only 6,696 of 14,090 salts declare an '
    'active moiety at all. Derived from GSRS''s ACTIVE MOIETY relationship, which is '
    'used ONLY here: as an EDGE it is the ion level and would assert that '
    'levomefolate magnesium is interchangeable with magnesium sulfate.';

-- ============================================================================
-- 4. The read path -- only the ACTIVE component propagates
-- ============================================================================
-- A contraindication or interaction asserted on moiety M reaches composite S only
-- where the release says M is what makes S active. Maleic acid's 124 salts stay
-- unlinked: expanding them would be alert-fatigue by construction, and the same
-- discredited inference the withheld chemical-class contraindications refuse.
--
-- `IS TRUE`, never `= true`: the predicate must never let a NULL be coerced into a
-- match by a later rewrite.
CREATE OR REPLACE VIEW drugref.moiety_active_in_composite AS
SELECT component_moiety AS moiety_uuid,
       substance_unii,
       relation
FROM drugref.substance_composition
WHERE is_active_component IS TRUE;

COMMENT ON VIEW drugref.moiety_active_in_composite IS
    'For a moiety, the specific substances it is the ACTIVE component of -- the only '
    'composition inference slice 3 licenses. Deliberately NOT wired into '
    'ddi_candidate_pair, a measured 3.6 ms hot path; that is its own round.';

-- ============================================================================
-- 5. Gap kind 12 -- the shortfall is published, not hidden
-- ============================================================================
-- The read path deliberately chooses FEWER rows for unruled composites, and for a
-- contraindication fewer rows is the harm direction. That trade is only defensible
-- because the shortfall is on a worklist -- the same posture as the 103 unresolved
-- CI objects: withheld, counted, and put in front of a curator.
--
-- GRAIN = the gap_key's grain (#41): one row per COMPOSITE, keyed on the composite.
-- bool_and is what makes "no ruling at all" different from "some ruling": a
-- composite with any TRUE or FALSE has been reviewed and leaves the queue.
CREATE OR REPLACE VIEW drugref.gap_unruled_composition_activity AS
SELECT substance_unii,
       count(*)::int AS component_count
FROM drugref.substance_composition
GROUP BY substance_unii
HAVING bool_and(is_active_component IS NULL);

COMMENT ON VIEW drugref.gap_unruled_composition_activity IS
    'Composites carrying components but NO activity ruling, so no contraindication '
    'on a component can reach them. 2,226 rows on 2026-02-26. Unlike '
    'gap_dead_by_expansion_policy this one is populated from day one.';
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_schema_composition.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Run the whole suite — the widened CHECKs touch shared tables**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q`
Expected: 844 + 9 = 853 passed. If `test_ingest_run.py` or a migration-ledger test fails, read it before changing anything.

- [ ] **Step 6: Commit**

```bash
git add db/028_composition_tree.sql tests/test_schema_composition.py
git commit -m "feat(db): db/028 composition projection, relation vocabulary, read and gap views

substance_unii is deliberately not an FK (4,425 of 7,377 composites are not
moieties) and is_active_component has NO DEFAULT, because NULL means unruled.
Widens ingest_run's source and writer CHECKs; substance_class_source is left
alone, because GSRS defines no classes."
```

---

### Task 4: The writer — `composition.py`

**Files:**
- Create: `src/drugref/composition.py`
- Test: `tests/test_composition_writer.py`

**Interfaces:**
- Consumes: `db.clear_source_tables` (existing), `db/028` (Task 3).
- Produces:
  - `composition.COMPOSITION_TABLES: tuple[str, ...]` = `("substance_composition",)`
  - `composition.clear_source_composition(conn, source: str) -> None`
  - `composition.moiety_uuid_by_unii(conn) -> dict[str, uuid.UUID]`
  - `composition.add_composition(conn, *, substance_unii: str, component_moiety: uuid.UUID, relation: str, is_active_component: bool | None, ingest_run_id: int) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_composition_writer.py`:

```python
# tests/test_composition_writer.py
"""composition.py is the ONLY module that writes substance_composition."""
import pytest

from drugref import composition, ids


@pytest.fixture
def gsrs_run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('GSRS', '2026-02-26', 'test', 'gsrs_run') "
        "RETURNING ingest_run_id").fetchone()[0]


@pytest.fixture
def component(conn, gsrs_run):
    moiety_uuid = ids.mint_moiety_uuid("COMPONENT1")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (moiety_uuid, "Component One", gsrs_run))
    conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'UNII', 'COMPONENT1', %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, gsrs_run))
    return moiety_uuid


def test_add_composition_writes_a_row(conn, gsrs_run, component):
    assert composition.add_composition(
        conn, substance_unii="SALT000001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=True,
        ingest_run_id=gsrs_run) is True
    row = conn.execute(
        "SELECT substance_unii, is_active_component FROM drugref.substance_composition"
    ).fetchone()
    assert row == ("SALT000001", True)


def test_add_composition_is_idempotent(conn, gsrs_run, component):
    """A release stating one edge from both ends must not write it twice."""
    kwargs = dict(substance_unii="SALT000001", component_moiety=component,
                  relation="SALT_SOLVATE", is_active_component=None,
                  ingest_run_id=gsrs_run)
    assert composition.add_composition(conn, **kwargs) is True
    assert composition.add_composition(conn, **kwargs) is False
    assert conn.execute(
        "SELECT count(*) FROM drugref.substance_composition").fetchone()[0] == 1


def test_null_is_stored_as_null_not_false(conn, gsrs_run, component):
    composition.add_composition(
        conn, substance_unii="SALT000001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=None, ingest_run_id=gsrs_run)
    assert conn.execute(
        "SELECT is_active_component FROM drugref.substance_composition"
    ).fetchone()[0] is None


def test_moiety_uuid_by_unii_maps_live_claims(conn, gsrs_run, component):
    mapping = composition.moiety_uuid_by_unii(conn)
    assert mapping["COMPONENT1"] == component


def test_clear_source_composition_removes_only_this_sources_rows(conn, gsrs_run, component):
    other = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('PBS', 'x', 'x', 'pbs_run') RETURNING ingest_run_id").fetchone()[0]
    composition.add_composition(
        conn, substance_unii="FROMGSRS01", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=None, ingest_run_id=gsrs_run)
    composition.add_composition(
        conn, substance_unii="FROMPBS001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=None, ingest_run_id=other)

    composition.clear_source_composition(conn, "GSRS")

    remaining = conn.execute(
        "SELECT substance_unii FROM drugref.substance_composition").fetchall()
    assert [r[0] for r in remaining] == ["FROMPBS001"]
```

- [ ] **Step 2: Run to verify failure**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_composition_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.composition'`

- [ ] **Step 3: Write the writer**

Create `src/drugref/composition.py`:

```python
# src/drugref/composition.py
"""The ONLY module that writes drugref.substance_composition (slice 3).

Mirrors conditions.py and classes.py: a rebuildable projection, so the writer owns
a per-source clear as well as an insert. `substance_composition` is REBUILT
wholesale on re-ingest -- a salt whose composition upstream corrects has to be able
to lose a component, which an insert-only merge could never express.

WHAT THIS MODULE DOES NOT DO: mint identity. The composite side of every row is a
UNII from the source, not a drugref UUID, because 4,425 of 7,377 composites are not
moieties and slice 3 deliberately creates no second registry.
"""
import uuid

import psycopg

from drugref import db

# Restated independently in tests/test_source_clear_contract.py, so that dropping a
# table here fails loudly rather than leaving a projection that grows on every
# ingest (#43).
COMPOSITION_TABLES = ("substance_composition",)


def clear_source_composition(conn: psycopg.Connection, source: str) -> None:
    """Drop every composition row contributed by `source`.

    Called at the start of a re-ingest so a new upstream release fully REPLACES the
    previous one, scoped by source so no other feed's rows are touched.
    """
    db.clear_source_tables(conn, COMPOSITION_TABLES, source)


def moiety_uuid_by_unii(conn: psycopg.Connection) -> dict[str, uuid.UUID]:
    """Every live UNII claim, as a UNII -> moiety_uuid map.

    Loaded once per run rather than queried per edge: the registry is ~19,438 rows
    against ~15,200 candidate edges, so one scan beats 15,200 round trips. This is
    the same shape the row-at-a-time ingests filed as #7/#29 got wrong.

    Superseded claims are excluded: a corrected claim's OLD value must not resolve.
    """
    rows = conn.execute(
        "SELECT value, moiety_uuid FROM drugref.identity_claim "
        "WHERE scheme = 'UNII' AND superseded_by IS NULL").fetchall()
    return {value: moiety_uuid for value, moiety_uuid in rows}


def add_composition(conn: psycopg.Connection, *, substance_unii: str,
                    component_moiety: uuid.UUID, relation: str,
                    is_active_component: bool | None,
                    ingest_run_id: int) -> bool:
    """Record that `substance_unii` is composed of `component_moiety`.

    Returns True if a new row was written. ON CONFLICT DO NOTHING keeps a release
    that states one edge from BOTH ends harmless -- GSRS stores ~15,039 of its
    ~15,100 salt edges twice, and the parser normalises both encodings to one edge.

    `is_active_component` is passed through unchanged, INCLUDING None. None means
    the release ruled on nothing, and turning it into False here would manufacture
    an answer no authority gave.
    """
    cur = conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (substance_unii, component_moiety, relation, is_active_component,
         ingest_run_id))
    return cur.rowcount == 1
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_composition_writer.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Add the table tuple to the source-clear contract**

Modify `tests/test_source_clear_contract.py` — add `composition` to the import line and this entry to `EXPECTED_TABLES`:

```python
    "composition.COMPOSITION_TABLES": (
        composition.COMPOSITION_TABLES, ("substance_composition",)),
```

The import line becomes:
```python
from drugref import classes, composition, conditions, db, indications, interactions, local
```

Also update the module docstring's "Seven writers" / "SEVEN of the seven" counts to **eight**, and the same count in `db.clear_source_tables`'s docstring ("SIX OF THE SEVEN declared table tuples" → "**SEVEN** OF THE EIGHT", because substance_composition owns its whole table and does NOT use `match=`; "Seven wrappers, not six" → "Eight wrappers, not seven"). A count restated in prose is exactly the kind of thing this repo has been bitten by; grep for it:

Run: `grep -rn "seven\|Seven\|SEVEN" src/drugref/db.py tests/test_source_clear_contract.py`

- [ ] **Step 6: Run the contract test**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_source_clear_contract.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/drugref/composition.py tests/test_composition_writer.py tests/test_source_clear_contract.py src/drugref/db.py
git commit -m "feat(composition): the single writer for substance_composition

Passes None through unchanged -- turning an unruled row into false would
manufacture an answer no authority gave. Adds the eighth declared table tuple to
the source-clear contract."
```

---

### Task 5: Gap kind 12 in the question registry

**Files:**
- Modify: `src/drugref/questions.py` (the `_GAP_SOURCES` dict)
- Test: `tests/test_gap_views.py` (add a case) or create `tests/test_composition_gap.py`

**Interfaces:**
- Consumes: `drugref.gap_unruled_composition_activity` (Task 3).
- Produces: gap kind `"unruled_composition_activity"` in `open_question`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_composition_gap.py`:

```python
# tests/test_composition_gap.py
"""Gap kind 12: a composite whose active component nobody has ruled on."""
import pytest

from drugref import composition, ids, questions


@pytest.fixture
def gsrs_run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('GSRS', '2026-02-26', 'test', 'gsrs_run') "
        "RETURNING ingest_run_id").fetchone()[0]


@pytest.fixture
def component(conn, gsrs_run):
    moiety_uuid = ids.mint_moiety_uuid("COMPONENT1")
    conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (moiety_uuid, "Component One", gsrs_run))
    return moiety_uuid


def test_an_unruled_composite_becomes_a_question(conn, gsrs_run, component):
    composition.add_composition(
        conn, substance_unii="UNRULED001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=None, ingest_run_id=gsrs_run)

    questions.register_from_gaps(conn, gsrs_run)

    row = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'unruled_composition_activity'").fetchone()
    assert row is not None, "gap kind 12 produced no question"
    assert row[0] == "SUBSTANCE:UNRULED001"
    assert "UNRULED001" in row[1]


def test_a_ruled_composite_raises_no_question(conn, gsrs_run, component):
    composition.add_composition(
        conn, substance_unii="RULED00001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=True, ingest_run_id=gsrs_run)

    questions.register_from_gaps(conn, gsrs_run)

    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unruled_composition_activity'").fetchone()[0] == 0


def test_the_question_uuid_is_stable_across_rebuilds(conn, gsrs_run, component):
    """question_uuid is immortal and externally citable. register_from_gaps takes
    the run that re-derived the register, so a second call passes the same id."""
    composition.add_composition(
        conn, substance_unii="UNRULED001", component_moiety=component,
        relation="SALT_SOLVATE", is_active_component=None, ingest_run_id=gsrs_run)
    questions.register_from_gaps(conn, gsrs_run)
    first = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unruled_composition_activity'").fetchone()[0]
    questions.register_from_gaps(conn, gsrs_run)
    second = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unruled_composition_activity'").fetchone()[0]
    assert first == second
```

- [ ] **Step 2: Run to verify failure**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_composition_gap.py -v`
Expected: FAIL — no rows of that `gap_kind`.

- [ ] **Step 3: Register the gap kind**

First read the neighbouring entries so the new one matches their shape exactly:

Run: `sed -n '30,120p' src/drugref/questions.py`

Then add to `_GAP_SOURCES` in `src/drugref/questions.py`, after the last existing entry:

```python
    # Slice 3, gap kind 12. The read path propagates ONLY the active component, so
    # an unruled composite is reached by nothing -- and for a contraindication,
    # fewer rows is the harm direction. That trade is defensible only because the
    # shortfall is on a worklist rather than hidden, which is this entry.
    #
    # Keyed on the COMPOSITE, which is also the view's grain (#41): grouping more
    # coarsely would fold two gaps onto one immortal question_uuid.
    "unruled_composition_activity": {
        "view": "gap_unruled_composition_activity",
        "key_sql": "'SUBSTANCE:' || substance_unii",
        "text_sql": (
            "'Which component of UNII ' || substance_unii || ' makes it "
            "pharmacologically active? It has ' || component_count || ' registered "
            "component(s) and the release marks none of them active, so no "
            "contraindication or interaction on a component reaches it.'"),
    },
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_composition_gap.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the whole gap-view suite — the kind count is asserted elsewhere**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_gap_views.py tests/test_questions.py -v`

If a test asserts "eleven gap kinds", update it to twelve **and** update the same count in `docs/PROJECT-NOTES.md` § Plan A ("**ELEVEN** gap kinds since Plan C"). Grep first:

Run: `grep -rn "ELEVEN\|eleven gap\|11 gap" src tests docs`

- [ ] **Step 6: Commit**

```bash
git add src/drugref/questions.py tests/test_composition_gap.py
git commit -m "feat(questions): gap kind 12, the composite whose active component is unruled

The read path deliberately propagates nothing for these, and for a
contraindication fewer rows is the harm direction -- so the shortfall goes on the
worklist rather than staying invisible."
```

---

### Task 6: The orchestrator — `ingest/gsrs_run.py`

**Files:**
- Create: `src/drugref/ingest/gsrs_run.py`
- Modify: `src/drugref/provenance.py` (the `WRITERS` tuple)
- Modify: `src/drugref/ids.py` (the `_SOURCE_CANONICAL` dict)
- Test: `tests/test_gsrs_run.py`

**Interfaces:**
- Consumes: `gsrs.iter_records` (Task 1), `composition.*` (Task 4), `provenance.open_run/finish_run`, `checksum.checksum`, `questions.register_from_gaps(conn, ingest_run_id)`.
- Produces: `gsrs_run.SOURCE`, `gsrs_run.WRITER`, `gsrs_run.GsrsSummary`, `gsrs_run.ingest_gsrs(conn, *, dump_path, upstream_release) -> GsrsSummary`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gsrs_run.py`:

```python
# tests/test_gsrs_run.py
"""The orchestrator: one transaction, one run record, worklist numbers not drops."""
import pathlib

import pytest

from drugref import ids
from drugref.ingest import gsrs_run

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "gsrs_subset.gsrs"


@pytest.fixture(autouse=True)
def _clean(conn):
    """ingest_gsrs COMMITS, so it escapes the conn fixture's rollback. Same pattern
    as tests/test_ingest_run.py's autouse truncate."""
    yield
    conn.execute("TRUNCATE drugref.substance_composition")
    conn.execute("DELETE FROM drugref.open_question "
                 "WHERE gap_kind = 'unruled_composition_activity'")
    conn.execute("DELETE FROM drugref.ingest_run WHERE source = 'GSRS'")
    conn.commit()


@pytest.fixture
def registry(conn):
    """Register the components the fixture's composites resolve to.

    ZINC CATION and Chlortetracycline are moieties; the counterions deliberately
    are NOT, so the run has something to COUNT as unresolved rather than drop.
    """
    seed_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'test', 'test', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    for unii, name in (("13S1S8SF37", "ZINC CATION"),
                       ("WCK1KIQ23Q", "Chlortetracycline"),
                       ("ML30MJ2U7I", "Magnesium sulfate anhydrous")):
        moiety_uuid = ids.mint_moiety_uuid(unii)
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING", (moiety_uuid, name, seed_run))
        conn.execute(
            "INSERT INTO drugref.identity_claim "
            "(moiety_uuid, scheme, value, ingest_run) VALUES (%s, 'UNII', %s, %s) "
            "ON CONFLICT DO NOTHING", (moiety_uuid, unii, seed_run))
    conn.commit()


def test_ingest_writes_composition_rows(conn, registry):
    summary = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    assert summary.rows_written > 0
    rows = conn.execute(
        "SELECT count(*) FROM drugref.substance_composition").fetchone()[0]
    assert rows == summary.rows_written


def test_zinc_glycinate_citrate_attaches_only_its_REGISTERED_component(conn, registry):
    """Three components upstream; only ZINC CATION is a moiety here. The other two
    are COUNTED, never silently dropped."""
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    components = conn.execute(
        "SELECT count(*) FROM drugref.substance_composition "
        "WHERE substance_unii = 'H3472PJ7YA'").fetchone()[0]
    assert components == 1


def test_unresolved_components_are_counted_not_dropped(conn, registry):
    summary = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    assert summary.components_not_in_registry > 0


def test_the_active_component_is_marked_true(conn, registry):
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    active = conn.execute(
        "SELECT is_active_component FROM drugref.substance_composition "
        "WHERE substance_unii = 'H3472PJ7YA'").fetchone()[0]
    assert active is True


def test_the_run_is_recorded_and_finished(conn, registry):
    gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    row = conn.execute(
        "SELECT source, writer, upstream_release, finished_at IS NOT NULL "
        "FROM drugref.ingest_run WHERE source = 'GSRS'").fetchone()
    assert row[0] == "GSRS"
    assert row[1] == "gsrs_run"
    assert row[2] == "2026-02-26"
    assert row[3] is True


def test_re_ingest_replaces_rather_than_accumulates(conn, registry):
    """The projection contract: running twice must not double the rows."""
    first = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    second = gsrs_run.ingest_gsrs(conn, dump_path=FIXTURE, upstream_release="2026-02-26")
    assert first.rows_written == second.rows_written
    total = conn.execute(
        "SELECT count(*) FROM drugref.substance_composition").fetchone()[0]
    assert total == second.rows_written


def test_gsrs_is_a_declared_writer_and_source():
    from drugref import ids as ids_module
    from drugref import provenance
    assert "gsrs_run" in provenance.WRITERS
    assert ids_module.canonical_source("GSRS") == "GSRS"
```

- [ ] **Step 2: Run to verify failure**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_gsrs_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.ingest.gsrs_run'`

- [ ] **Step 3: Extend the writer tuple**

In `src/drugref/provenance.py`, change:

```python
WRITERS = ("unii_run", "chebi", "medrt_run", "mesh_run", "mesh_rel_run", "pbs_run",
           "curation", "unattributed")
```

to:

```python
WRITERS = ("unii_run", "chebi", "medrt_run", "mesh_run", "mesh_rel_run", "pbs_run",
           "curation", "unattributed", "gsrs_run")
```

- [ ] **Step 4: Extend the source canonicalisation**

In `src/drugref/ids.py`, add to `_SOURCE_CANONICAL`, after the `"DRUGREF"` entry:

```python
    # Slice 3. 'GSRS' survives the upper-case fall-through unchanged, exactly as
    # 'DRUGREF' does -- and is listed for the same reason: the entry records that
    # the luck was CHECKED rather than assumed. db/028 widens ingest_run's source
    # CHECK to match; the two are a pair.
    "GSRS": "GSRS",
```

- [ ] **Step 5: Write the orchestrator**

Create `src/drugref/ingest/gsrs_run.py`:

```python
# src/drugref/ingest/gsrs_run.py
"""Orchestrate one GSRS composition ingest: parse -> clear -> insert -> rebuild.

The ONLY writer of drugref.substance_composition's transaction, per the
architecture invariant: parsers are pure, orchestrators own the transaction.

ORDER MATTERS, as for MED-RT and MeSH:
  1. parse and checksum BEFORE opening the run, so a crash during the 2.05 GB pass
     leaves no half-written run row;
  2. clear this source's old rows, so a re-ingest REPLACES rather than accumulates;
  3. insert, then rebuild the question register, then finish and commit.

WORKLIST NUMBERS, NOT SILENT DROPS -- the slice-1/2a posture. An edge whose
component is not a gated-in moiety is COUNTED (`components_not_in_registry`), never
quietly discarded: on the real release only 4,433 of GSRS's 10,090 parent bases are
drugref moieties, and a number that vanishes is a number nobody fixes.
"""
import dataclasses
import logging

import psycopg

from drugref import composition, provenance, questions
from drugref.ingest import gsrs
from drugref.ingest.checksum import checksum
from drugref.ingest.gsrs import StrPath

SOURCE = "GSRS"
# WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
# (db/025). Declared in provenance.WRITERS and db/028's CHECK -- a pair.
WRITER = "gsrs_run"

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class GsrsSummary:
    """What one GSRS run did -- returned so a caller (or test) can assert on it.

    The two worklist numbers are reported, never swallowed:

    * components_not_in_registry -- edges naming a component no gated-in moiety
      carries. The moiety gate is the binding constraint, exactly as for the MeSH
      bridge (#26), and this counts the shortfall rather than hiding it.
    * unruled_composites -- composites written with no active-component ruling,
      which become gap kind 12. NOT a failure: it is the release declining to say.
    """
    records_in_release: int
    edges_in_release: int
    rows_written: int
    composites_written: int
    components_not_in_registry: int
    unruled_composites: int


def ingest_gsrs(conn: psycopg.Connection, *, dump_path: StrPath,
                upstream_release: str) -> GsrsSummary:
    """Ingest one GSRS public dump into drugref.substance_composition."""
    # 1. PARSE FIRST, before any run row exists. The pass is ~8 s over 2.05 GB and
    #    touches no database; a crash here must leave no trace to explain.
    edges: dict[tuple[str, str, str], bool | None] = {}
    records_in_release = 0
    edges_in_release = 0
    for record in gsrs.iter_records(dump_path):
        records_in_release += 1
        if not record.edges:
            continue
        for edge in record.edges:
            edges_in_release += 1
            # NULL when the release rules on nothing; otherwise whether THIS
            # component is the active one. Keyed by the composite's own record, so
            # the mirror encoding on the component's record cannot overwrite a
            # ruling with a NULL.
            if edge.substance_unii == record.unii:
                activity = (edge.component_unii in record.active_moieties
                            if record.active_moieties else None)
            else:
                activity = None
            key = (edge.substance_unii, edge.component_unii, edge.relation)
            # A ruling beats a None, whichever end the edge arrived from.
            if key not in edges or edges[key] is None:
                edges[key] = activity

    source_checksum = checksum(dump_path)

    # 2. Open the run. This COMMITS in its own transaction (provenance.open_run),
    #    so everything after it is the work and rolls back together on failure.
    run_id = provenance.open_run(conn, source=SOURCE,
                                 upstream_release=upstream_release,
                                 source_checksum=source_checksum, writer=WRITER)

    by_unii = composition.moiety_uuid_by_unii(conn)
    composition.clear_source_composition(conn, SOURCE)

    rows_written = 0
    composites: set[str] = set()
    unresolved = 0
    activity_by_composite: dict[str, set[bool | None]] = {}
    for (substance_unii, component_unii, relation), activity in edges.items():
        component_moiety = by_unii.get(component_unii)
        if component_moiety is None:
            unresolved += 1
            continue
        if composition.add_composition(
                conn, substance_unii=substance_unii,
                component_moiety=component_moiety, relation=relation,
                is_active_component=activity, ingest_run_id=run_id):
            rows_written += 1
        composites.add(substance_unii)
        activity_by_composite.setdefault(substance_unii, set()).add(activity)

    unruled = sum(1 for values in activity_by_composite.values() if values == {None})

    questions.register_from_gaps(conn, run_id)
    provenance.finish_run(conn, run_id)
    conn.commit()

    summary = GsrsSummary(records_in_release=records_in_release,
                          edges_in_release=edges_in_release,
                          rows_written=rows_written,
                          composites_written=len(composites),
                          components_not_in_registry=unresolved,
                          unruled_composites=unruled)
    log.info("GSRS ingest: %s", summary)
    return summary
```

- [ ] **Step 6: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_gsrs_run.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 7: Commit**

```bash
git add src/drugref/ingest/gsrs_run.py src/drugref/provenance.py src/drugref/ids.py tests/test_gsrs_run.py
git commit -m "feat(gsrs): the orchestrator, with both worklist numbers reported

Parses before opening the run, so a crash in the 2.05 GB pass leaves no row.
An edge whose component is not a gated-in moiety is COUNTED, never dropped:
only 4,433 of GSRS's 10,090 parent bases are drugref moieties."
```

---

### Task 7: CLI step and chain wiring

**Files:**
- Modify: `src/drugref/cli.py` (imports, one runner, one `STEPS` entry)
- Test: `tests/test_cli.py` (add cases)

**Interfaces:**
- Consumes: `gsrs_run.ingest_gsrs` (Task 6).
- Produces: the `gsrs` CLI step, glob `GSRS/dump-public-*.gsrs`, input name `dump`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_gsrs_is_a_declared_step():
    from drugref import cli
    step = next(s for s in cli.STEPS if s.name == "gsrs")
    assert step.inputs == (("dump", "GSRS/dump-public-*.gsrs"),)
    # No secondary inputs: this step reads and DATES exactly one file.
    assert step.secondary == ()


def test_the_gsrs_glob_matches_the_real_release_name(tmp_path):
    """The glob is pinned because #60's lesson was that a wrong one ships silently.
    The release file is dump-public-YYYY-MM-DD.gsrs."""
    from drugref import cli
    downloads = tmp_path / "downloads"
    (downloads / "GSRS").mkdir(parents=True)
    (downloads / "GSRS" / "dump-public-2026-02-26.gsrs").write_text("")
    step = next(s for s in cli.STEPS if s.name == "gsrs")
    resolved = cli.resolve_inputs(downloads, step)
    assert resolved["dump"].name == "dump-public-2026-02-26.gsrs"


def test_two_gsrs_releases_in_one_directory_are_refused(tmp_path):
    """Silently taking either would record the wrong bytes as this run's provenance."""
    from drugref import cli
    downloads = tmp_path / "downloads"
    (downloads / "GSRS").mkdir(parents=True)
    (downloads / "GSRS" / "dump-public-2026-02-26.gsrs").write_text("")
    (downloads / "GSRS" / "dump-public-2026-05-01.gsrs").write_text("")
    step = next(s for s in cli.STEPS if s.name == "gsrs")
    with pytest.raises(cli.InputResolutionError):
        cli.resolve_inputs(downloads, step)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -k gsrs -v`
Expected: FAIL — `StopIteration` (no step named `gsrs`).

- [ ] **Step 3: Wire the step**

In `src/drugref/cli.py`:

1. Add `gsrs_run` to the `from drugref.ingest import ...` line.
2. Add the runner beside the other `_run_*` functions:

```python
def _run_gsrs(conn, paths, release):
    return gsrs_run.ingest_gsrs(conn, dump_path=paths["dump"],
                                upstream_release=release)
```

3. Add the step to `STEPS`. **Order matters only for UNII-first** (PROJECT-NOTES records that the step order is NOT otherwise a dependency order), but GSRS resolves components against the moiety registry, so it must run after `unii`. Place it last:

```python
    IngestStep("gsrs", (("dump", "GSRS/dump-public-*.gsrs"),), _run_gsrs),
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS. If a test asserts the exact number of steps, update it from 6 to 7.

- [ ] **Step 5: Run the whole suite**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/drugref/cli.py tests/test_cli.py
git commit -m "feat(cli): the gsrs ingest step and its chain glob

Runs after unii, because it resolves components against the moiety registry.
The glob is pinned by test: #60 shipped a wrong one silently."
```

---

### Task 8: NOTICE, the published decision record, and the full re-measure

The slice is not done until it has run against the real releases and every existing published figure has been shown not to move.

**Files:**
- Modify: `NOTICE`
- Create: `docs-site/docs/decisions/gsrs-relationship-direction.md`
- Modify: `docs-site/mkdocs.yml` (nav entry)
- Modify: `docs/PROJECT-NOTES.md`, `docs/HANDOVER.md`, `docs/ROADMAP.md`

- [ ] **Step 1: Update NOTICE**

The existing line reads:
```
- UNII / Global Substance Registration System — U.S. FDA/NCATS (public domain).
```

Replace it with:

```
- UNII / Global Substance Registration System (GSRS) — U.S. FDA/NCATS. The UNII
  release files are public domain. The GSRS public data dump, from which slice 3
  ingests salt/solvate composition and active-moiety relationships, is released
  under a Creative Commons CC0 1.0 Universal public-domain dedication
  (https://creativecommons.org/publicdomain/zero/1.0/); the GSRS software, which
  drugref neither uses nor redistributes, is Apache-2.0. The dedication is stated
  "unless otherwise noted"; no noted exception was found on any ingested record,
  and it is re-confirmed before the first production load.
```

- [ ] **Step 2: Write the published decision record**

Create `docs-site/docs/decisions/gsrs-relationship-direction.md`:

```markdown
# GSRS relationship direction runs target → record

**Status:** current · **Applies to:** slice 3 (`substance_composition`)

GSRS stores a relationship of type `A->B` on a record `X` pointing at a record `Y`,
and **`X` plays role `B` while `Y` plays role `A`**. The stored relationship is the
*inbound* edge, not the outbound one.

This is not documented upstream, and reading the type left-to-right produces a
plausible, fully-populated, entirely wrong graph. In the 2026-02-26 public dump the
naive reading yields a single "salt" with **124 parent bases**. Under the correct
reading the same data says Maleic Acid is the parent of **124 salts** — which is
what a common counterion should look like.

Two independent checks pin the convention, and both are kept as tests:

- **Mirror agreement.** Most edges are stored from both ends. Normalised under this
  convention the two encodings agree on **15,039** edges (of 15,109 and 15,150).
  Inverted, they would agree on essentially none.
- **Functional cardinality.** Every solvate has exactly **one** anhydrous parent.
  Inverted, the relation is many-to-many and meaningless.

The 70 edges stored only as `SALT/SOLVATE->PARENT` and the 111 stored only as
`PARENT->SALT/SOLVATE` are upstream asymmetry: counted, not repaired.

This is the same class of erratum as MED-RT's `Parent Of`, which runs parent →
child rather than the reverse.

## A second finding, recorded with it

The **public GSRS API strips `relationships` entirely**. A record whose `ACTIVE
MOIETY` edge is present in the dump returns zero relationships from
`/api/v1/substances/{uuid}`. Any tool reading GSRS relationships must use the dump;
the API is not a fallback for it.
```

- [ ] **Step 3: Add it to the docs nav**

In `docs-site/mkdocs.yml`, add the new file to the **Design decisions** section beside the existing records. Check the exact nav shape first:

Run: `grep -n "decisions" -A 8 docs-site/mkdocs.yml`

- [ ] **Step 4: Build the docs**

Run: `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`
Expected: builds clean. `--strict` fails on a nav entry pointing at a missing file, and on a file absent from the nav.

- [ ] **Step 5: Re-measure against the real releases**

This is the step the slice cannot ship without. Create a fresh database and run the full chain **with GSRS in it**:

```bash
DSN='host=localhost port=5532 dbname=drugref_slice3 user=postgres'
createdb -h localhost -p 5532 -U postgres drugref_slice3
uv run drugref --dsn "$DSN" migrate
uv run drugref --dsn "$DSN" ingest chain --downloads downloads \
    --unii-release 26Feb2026 --medrt-release 2026.07.06 \
    --mesh-release 2026 --mesh-relations-release 2026.07.06 \
    --gsrs-release 2026-02-26
uv run drugref --dsn "$DSN" status
```

(If the chain's release-flag naming differs, read `build_parser` and use what it declares.)

- [ ] **Step 6: Confirm the new figures, and that NOTHING ELSE MOVED**

```bash
psql "$DSN" -c "
SELECT 'substance_composition'      AS what, count(*) FROM drugref.substance_composition
UNION ALL SELECT 'composites',        count(DISTINCT substance_unii) FROM drugref.substance_composition
UNION ALL SELECT 'component moieties',count(DISTINCT component_moiety) FROM drugref.substance_composition
UNION ALL SELECT 'active TRUE',       count(*) FROM drugref.substance_composition WHERE is_active_component
UNION ALL SELECT 'active FALSE',      count(*) FROM drugref.substance_composition WHERE NOT is_active_component
UNION ALL SELECT 'active NULL',       count(*) FROM drugref.substance_composition WHERE is_active_component IS NULL
UNION ALL SELECT 'gap kind 12',       count(*) FROM drugref.gap_unruled_composition_activity
UNION ALL SELECT 'ddi_candidate_pair',count(*) FROM drugref.ddi_candidate_pair
UNION ALL SELECT 'open_question',     count(*) FROM drugref.open_question
UNION ALL SELECT 'moieties',          count(*) FROM drugref.substance_moiety;"
```

**Expected — the slice-3 figures:**

| what | expected |
|---|---:|
| `substance_composition` | **8,671** |
| composites | **7,377** |
| component moieties | **4,433** |
| active TRUE | **5,029** |
| active FALSE | **1,001** |
| active NULL | **2,641** |
| gap kind 12 | **2,226** |

**Expected — unmoved, because this slice changes no SQL any of them depend on:**

| what | must still be |
|---|---:|
| `ddi_candidate_pair` | **21,664** |
| `substance_moiety` | **19,438** |
| `open_question` | **18,834 + 2,226 = 21,060** |

**Any movement in the second table is a defect, not a discovery.** If `ddi_candidate_pair` or the moiety count moved, stop and find out why before doing anything else.

If a slice-3 figure differs from the table above, **the code is wrong or the release changed** — investigate before editing the number. The measurement scripts that produced these are the authority, and every figure came from `dump-public-2026-02-26.gsrs`.

- [ ] **Step 7: Update the three working docs**

- `docs/PROJECT-NOTES.md` § "Slice 3": change "DESIGNED … not yet built" to record it as built, add the measured figures, and keep every trap.
- `docs/PROJECT-NOTES.md` § "How to run / test": add the `gsrs` step to the chain invocation and the per-source list, and update the test count.
- `docs/PROJECT-NOTES.md` § Plan A: eleven gap kinds → twelve.
- `docs/ROADMAP.md` § "Slice 3": mark ✅ DONE and add the measured table.
- `docs/HANDOVER.md`: regenerate, **under the bound its own header states** (verify with `wc -l`).

- [ ] **Step 8: Full verification before the PR**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check src tests
uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml
wc -l docs/HANDOVER.md
```

All must pass. Do not open the PR on a red suite.

- [ ] **Step 9: Commit and open the PR**

```bash
git add -A
git commit -m "docs: NOTICE, the direction-convention decision record, and the re-measure"
git push -u origin feat/slice-3-composition-tree
```

PR body must state: the rule-6 clearance (CC0/Apache-2.0), the four refuted roadmap claims, the measured figures, and **that the slice does not resolve issues 33 or 30**. Reference issue 67 and issue 68 **without** a closing keyword, and **without** a `#`, since neither is closed by this work.

---

## Self-Review

**Spec coverage:** §1 licence → Task 8 (NOTICE) · §2 release facts → Tasks 1–2 · §3.1 ACTIVE MOIETY not an edge → Task 1 + Task 2 tests · §3.2 direction → Task 1 (one function) + Task 2 (both checks) · §3.3 multi-parent → Tasks 1, 2, 3 · §3.4 flat tree → no recursive view anywhere · §4 exclusions → issue 67 filed, no `ddi_candidate_pair` change · §5.1 schema → Task 3 · §5.2 `is_active_component` → Tasks 3, 4, 6 · §5.3 row counts → Task 8 · §6.1 read view → Task 3 · §6.2 gap kind 12 → Tasks 3, 5 · §7.1 parser → Task 1 · §7.2 orchestrator → Task 6 · §7.3 fixture (all 7 required cases) → Task 2 · §8 non-closure → Global Constraints + Task 8 · §9 verification → Tasks 2, 3, 8 · §10 traps → tests in Tasks 1–4.

**Type consistency:** `CompositionEdge(substance_unii, component_unii, relation)` is used identically in Tasks 1, 2 and 6. `add_composition` is keyword-only in Task 4 and called keyword-only in Task 6. `SALT_SOLVATE`/`SOLVATE_ANHYDROUS` are the same strings in `gsrs.py`, `db/028`'s seed rows and the tests. `moiety_uuid_by_unii` returns `dict[str, uuid.UUID]` and is consumed as one.

**Verified against the codebase while writing this plan, so the implementer does not have to guess:**
1. `StrPath` is a module-local alias (`str | pathlib.Path`) in both `ingest/checksum.py:22` and `ingest/mesh.py:47`. There is **no** `ingest/paths.py`; `gsrs.py` declares its own, and `gsrs_run.py` imports it from `gsrs`.
2. `resolve_inputs`'s signature is **`resolve_inputs(downloads, step)`** (`cli.py:201`) — that argument order is used in Task 7's tests.
3. The fixture is **gzipped**, because `iter_records` uses `gzip.open`.
4. **No test asserts `len(STEPS)`**, so adding the step breaks nothing there.
5. The gap-kind count appears in prose at `docs/PROJECT-NOTES.md:130` ("**ELEVEN** gap kinds") and must go to twelve; `tests/test_db.py:158` says "through eleven" in a comment and should be read before editing.

**Remaining genuine unknown:** the chain's per-step release flag for GSRS (`--gsrs-release`) is inferred from the existing naming pattern; Task 8 Step 5 says to read `build_parser` and use what it declares.

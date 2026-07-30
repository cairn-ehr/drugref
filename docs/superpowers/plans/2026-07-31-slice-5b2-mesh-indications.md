# Slice 5b.2 — MeSH-keyed indications: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest MED-RT's MeSH-keyed indications — `may_treat` / `may_prevent` /
`may_diagnose` (18,144 assertions) and `induces` (170) — as two rebuildable projections
over slice 5b's condition registry, storing only what the release asserts and
generalising at read time in the one direction that is sound.

**Architecture:** Two new relation tables plus a vocabulary table (`db/019`), a single
writer module (`indications.py`) mirroring `interactions.py`, one reach view and one
ancestor-walk function as the read path, a seventh gap kind, and a refactor that makes
**one** orchestrator own the condition registry for both halves of the MeSH-keyed
content. No new source, no new mechanism: concept resolution, the descendant closure,
the DAG builder, the moiety indexes and the question register are all slice 5b's.

**Tech Stack:** Python 3.12 + `uv`, `psycopg` v3, PostgreSQL ≥ 18, pytest.

**Spec:** [2026-07-30-drugref-slice-5b2-mesh-indication-design.md](../specs/2026-07-30-drugref-slice-5b2-mesh-indication-design.md).
Read §3.2 (why expansion is unsound), §3.6 (why 5b's expanded figures move) and §5
(schema) before starting. **If this plan disagrees with the spec, the spec wins.**

## Global Constraints

- **TDD.** Failing test first, every time. A step that writes code before its test has
  been seen to fail is a plan violation.
- **Licence.** AGPL-3.0. No new dependency and no new data source in this slice. Do not
  add one to "make something easier".
- **Migrations are immutable once applied — but immutability starts at MERGE.**
  `db/019_mesh_indications.sql` is built up across Tasks 4, 6 and 7 by **editing the same
  file**. That is safe *only* while the branch is unmerged: `tests/conftest.py`'s
  `_migrated` fixture runs `DROP SCHEMA IF EXISTS drugref CASCADE` and re-applies every
  migration, so an edited unmerged file is re-applied cleanly. Never edit `db/001`–`db/018`.
- **File size.** Keep files under ~500 lines (CLAUDE.md rule 4). `ingest/mesh_ci_run.py`
  is already 458, which is why Task 7 splits it rather than extending it.
- **Inline documentation is mandatory** and this codebase's bar is high: comments say
  *why*, name the measured figure behind a decision, and name the defect a guard exists
  to prevent. Match the surrounding density — read `db/018` and `ingest/mesh_ci_run.py`
  before writing any comment.
- **No silent drops.** Every assertion that does not become a row is counted, and the
  count is reported in the run summary.
- **Test command:** `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`
  (DB-gated tests skip without the DSN — a green run without it proves nothing about the
  schema). Lint with `ruff check .`.
- **Baseline before you start: 568 tests pass, `ruff check` clean.**

---

## File Structure

**Created:**

| file | responsibility |
|---|---|
| `db/019_mesh_indications.sql` | all DDL for this slice (Tasks 4, 6, 7) |
| `src/drugref/indications.py` | the ONLY writer of the two indication relations |
| `src/drugref/ingest/mesh_ci_relations.py` | the contraindication relation pass (moved out of `mesh_ci_run.py`) |
| `src/drugref/ingest/mesh_ind_relations.py` | the indication relation pass |
| `src/drugref/ingest/mesh_rel_run.py` | the single orchestrator for MED-RT's MeSH-keyed relations |
| `tests/test_indications_writer.py` | writer + constraint tests |
| `tests/test_indication_read_path.py` | reach view + `indications_for_condition` |
| `tests/test_medrt_indication_parser.py` | parser tests for the four predicates |

**Modified:** `src/drugref/ingest/medrt.py` · `src/drugref/ingest/mesh_concepts.py` ·
`src/drugref/questions.py` · `src/drugref/conditions.py` · `tests/test_medrt_parser.py` ·
`tests/test_mesh_ci_run.py` · `tests/test_gap_views.py` ·
`tests/test_source_clear_contract.py` · `tests/fixtures/make_medrt_subset.py` ·
`docs/HANDOVER.md` · `docs/ROADMAP.md` · `docs-site/docs/decisions/`.

**Deleted:** `src/drugref/ingest/mesh_ci_run.py` (Task 7 — its content moves; its
docstring knowledge must be carried over, not lost).

---

## Task 1: Parse the four indication predicates

**Files:**
- Modify: `src/drugref/ingest/medrt.py`
- Modify: `tests/test_medrt_parser.py:315-324` (one existing assertion must change)
- Test: `tests/test_medrt_indication_parser.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `medrt.INDICATION_RELATIONSHIPS: frozenset[str]`,
  `medrt.INDUCES_RELATIONSHIP: str`, `medrt.MESH_INDICATION_RELATIONSHIPS: frozenset[str]`,
  and on `ParsedMedrt`: `mesh_indications: list[MeshObjectAssertion]` and
  `class_subject_indications: int`. Later tasks read `parsed.mesh_indications` and
  filter it by `a.relationship`.

**Context you need:** `MeshObjectAssertion` already exists (`rxcui`, `mesh_code`,
`relationship`) and is exactly the right shape — an indication's object is a raw MeSH
ConceptUI resolved later, same as `CI_with`'s. Do not invent a second record type.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_medrt_indication_parser.py`:

```python
# tests/test_medrt_indication_parser.py
"""The four indication predicates (slice 5b.2).

Mirrors test_medrt_mesh_ci_parser.py, because the parsing problem is identical: the
object is a MeSH ConceptUI this module must NOT resolve, and the endpoint pair is the
only scoping that keeps unlicensed namespaces out.
"""
import pathlib

import pytest

from drugref.ingest import medrt

FIX = pathlib.Path(__file__).parent / "fixtures" / "medrt_subset.xml"


@pytest.fixture(scope="module")
def parsed():
    return medrt.parse(FIX)


def write_medrt(tmp_path, associations: str) -> pathlib.Path:
    """One MED-RT file holding a single MoA class plus the given associations."""
    path = tmp_path / "medrt.xml"
    path.write_text(
        '<?xml version="1.0"?>\n<terminology>\n'
        "<concept><namespace>MED-RT</namespace><code>C-MOA</code>"
        "<name>Real Mechanism [MoA]</name><status>A</status>"
        "<property><name>CTY</name><value>MoA</value></property>"
        "<property><name>NUI</name><value>N-MOA-9</value></property></concept>\n"
        + associations + "\n</terminology>\n", encoding="utf-8")
    return path


def assoc(name: str, fns: str, fc: str, tns: str, tc: str) -> str:
    return (f"<association><name>{name}</name>"
            f"<from_namespace>{fns}</from_namespace><from_code>{fc}</from_code>"
            f"<to_namespace>{tns}</to_namespace><to_code>{tc}</to_code></association>")


@pytest.mark.parametrize("predicate",
                         ["may_treat", "may_prevent", "may_diagnose", "induces"])
def test_each_predicate_is_parsed_with_its_raw_mesh_code(tmp_path, predicate):
    path = write_medrt(tmp_path, assoc(predicate, "RxNorm", "161", "MeSH", "M0001"))
    result = medrt.parse(path)
    assert len(result.mesh_indications) == 1
    got = result.mesh_indications[0]
    assert (got.rxcui, got.mesh_code, got.relationship) == ("161", "M0001", predicate)


def test_a_class_subject_is_refused_and_counted(tmp_path):
    """193 assertions in the real release run MED-RT -> MeSH: the subject is a
    pharmacologic CLASS, not an ingredient, so there is no RxCUI to bridge. Refused
    and COUNTED -- the posture non_mesh_ci_objects takes -- never dropped."""
    path = write_medrt(tmp_path, assoc("may_treat", "MED-RT", "C-MOA", "MeSH", "M0001"))
    result = medrt.parse(path)
    assert result.mesh_indications == []
    assert result.class_subject_indications == 1


def test_an_object_outside_mesh_is_refused_and_counted(tmp_path):
    """The counter is named for the shape the release contains, but it increments for
    ANY endpoint pair other than RxNorm -> MeSH -- which is what keeps SNOMED out."""
    path = write_medrt(tmp_path,
                       assoc("may_treat", "RxNorm", "161", "SNOMED CT", "12345"))
    result = medrt.parse(path)
    assert result.mesh_indications == []
    assert result.class_subject_indications == 1


def test_indications_do_not_leak_into_the_other_lists(tmp_path):
    """Slice 5a's class_contraindication rows are load-bearing for ddi_candidate_pair,
    and 5b's mesh_contraindications for the CI relations. Neither may gain a row here."""
    path = write_medrt(tmp_path,
                       assoc("may_treat", "RxNorm", "161", "MeSH", "M0001")
                       + assoc("CI_with", "RxNorm", "161", "MeSH", "M0002"))
    result = medrt.parse(path)
    assert [a.relationship for a in result.mesh_indications] == ["may_treat"]
    assert [a.relationship for a in result.mesh_contraindications] == ["CI_with"]
    assert result.contraindications == []
    assert result.memberships == []


def test_indication_predicates_left_the_skipped_list(parsed):
    """skipped_predicates is the release-to-release change detector: a predicate
    drugref now INGESTS must leave it, or an upstream rename stops being visible."""
    for predicate in ("may_treat", "may_prevent", "may_diagnose", "induces"):
        assert predicate not in parsed.skipped_predicates


def test_the_committed_fixture_exercises_indications(parsed):
    """Asserted against the fixture so it cannot pass by the fixture quietly losing
    the assertions it exists to exercise (test_has_sc_into_mesh_is_dropped's idiom)."""
    assert parsed.mesh_indications, "fixture carries no indication assertions"
```

- [ ] **Step 2: Run the tests and watch them fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_medrt_indication_parser.py -v
```

Expected: every test fails with `AttributeError: 'ParsedMedrt' object has no attribute
'mesh_indications'`.

- [ ] **Step 3: Add the constants**

In `src/drugref/ingest/medrt.py`, immediately after `MESH_CI_RELATIONSHIPS`:

```python
# MeSH-keyed INDICATIONS (slice 5b.2). Same endpoint shape as MESH_CI_RELATIONSHIPS --
# RxNorm subject, MeSH ConceptUI object this parser hands on RAW -- and scoped the same
# way, which is what keeps SNOMED endpoints unreadable.
#
#   may_treat     -- 15,319 RxNorm->MeSH assertions in the 2026.07.06 release
#   may_prevent   --  2,670, and the object is often the ORGANISM rather than the
#                     infection (Influenza A virus 76): these are the vaccines.
#   may_diagnose  --    155
INDICATION_RELATIONSHIPS = frozenset({"may_treat", "may_prevent", "may_diagnose"})

# `induces` points the OTHER WAY: the drug CAUSES the state (Unconsciousness 32,
# Mydriasis 14, Diarrhea 8), which is sometimes the therapeutic point and sometimes the
# adverse effect -- MED-RT does not say which. It is neither an indication nor a
# contraindication and db/019 gives it its own table so it cannot be read as either.
INDUCES_RELATIONSHIP = "induces"

# Parsed together because the parsing problem is identical; separated downstream,
# where the MEANING differs. 170 induces assertions, all RxNorm->MeSH.
MESH_INDICATION_RELATIONSHIPS = INDICATION_RELATIONSHIPS | {INDUCES_RELATIONSHIP}
```

- [ ] **Step 4: Add the fields to `ParsedMedrt`**

After the `mesh_contraindications` field:

```python
    mesh_indications: list[MeshObjectAssertion] = field(default_factory=list)
    # Indication assertions this parse could not use. Every one in the 2026.07.06
    # release is MED-RT -> MeSH -- a pharmacologic CLASS as the subject (may_treat 100,
    # may_prevent 90, may_diagnose 3) -- which has no RxCUI to bridge to a moiety.
    # Strictly it counts ANY endpoint pair other than RxNorm -> MeSH, so the name
    # describes the only case the release contains, not the only case that increments
    # it: the same honesty non_mesh_ci_objects' comment applies to itself. Ingesting
    # these needs a class->condition relation and a second expansion question, so they
    # are counted and filed against #8 rather than guessed at.
    class_subject_indications: int = 0
```

- [ ] **Step 5: Parse them**

Add a branch in `parse()`'s association loop, immediately after the
`elif name in MESH_CI_RELATIONSHIPS:` branch:

```python
        elif name in MESH_INDICATION_RELATIONSHIPS:
            # Scoped exactly as the MeSH-keyed contraindications are, and for the same
            # reason: the object is a ConceptUI resolved later against the MeSH release
            # (ingest/mesh_concepts.py), so there is nothing to look up here, and any
            # OTHER endpoint pair is refused rather than assumed to be MeSH.
            if from_ns == RXNORM_NAMESPACE and to_ns == MESH_NAMESPACE:
                mesh_indications.append(MeshObjectAssertion(
                    rxcui=from_code, mesh_code=to_code, relationship=name))
            else:
                class_subject_indications += 1
```

Declare `mesh_indications: list[MeshObjectAssertion] = []` and
`class_subject_indications = 0` beside the existing locals, and pass both into the
`ParsedMedrt(...)` constructor at the end of `parse()`.

- [ ] **Step 6: Update the two stale docstrings and the one stale assertion**

In `medrt.py`, the module docstring says MeSH concepts "are read for exactly two
predicates". Change that sentence to name six, and update
`MEMBERSHIP_RELATIONSHIPS`'s comment, which says `may_treat / may_prevent` are
"indications, still a later slice" — they are this slice. The final `else:` branch's
comment lists `may_treat` as an example of a deliberate skip; replace it with
`site_of_metabolism`, which really is still skipped.

In `tests/test_medrt_parser.py`, `test_skipped_association_names_are_reported` asserts
`result.skipped_predicates == ("has_SC", "may_treat")`. `may_treat` is now ingested, so
change the expectation to `("has_SC",)` and add a line to that test's body proving the
predicate went somewhere rather than vanishing:

```python
    assert result.skipped_predicates == ("has_SC",)
    assert len(result.memberships) == 1      # the recognised one still lands
    assert len(result.mesh_indications) == 1  # may_treat is INGESTED now, not skipped
```

Also update `test_indication_and_contraindication_are_not_membership`'s docstring: the
point is no longer "a later slice", it is that an indication is **not a membership** and
must not reach `class_membership`.

- [ ] **Step 7: Run the tests and watch them pass**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_medrt_indication_parser.py tests/test_medrt_parser.py \
  tests/test_medrt_mesh_ci_parser.py -v
```

Expected: all pass. Then run the whole suite — nothing else should move.

- [ ] **Step 8: Commit**

```bash
git add src/drugref/ingest/medrt.py tests/test_medrt_indication_parser.py tests/test_medrt_parser.py
git commit -m "feat(medrt): parse the four MeSH-keyed indication predicates (5b.2)"
```

---

## Task 2: Read `SCRClass` off supplementary records

**Files:**
- Modify: `src/drugref/ingest/mesh_concepts.py:43-66` (the `MeshRecord` dataclass) and
  `:110-122` (`_record`)
- Test: `tests/test_mesh_concepts.py` (add to the existing module)

**Interfaces:**
- Consumes: nothing.
- Produces: `MeshRecord.scr_class: str | None` — the published digit, `None` for a
  descriptor. Task 7 stores it; Task 6's gap view filters on `'3'`.

**Context:** verified against supp2026 — the attribute is `SCRClass` on
`<SupplementalRecord>`; `<DescriptorRecord>` carries `DescriptorClass` instead and must
never populate this field. The release publishes **six** values (1: 249,245 · 4: 65,236 ·
3: 6,542 · 5: 1,763 · 2: 1,236 · 6: 23), not the four the documentation describes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mesh_concepts.py`:

```python
def test_a_supplementary_record_carries_its_scr_class(tmp_path):
    """MeSH's SCRClass is what tells a rare disease (3) from a chemical (1) among
    records that bear NO tree numbers, and therefore no DAG position at all. It is the
    only thing that lets gap_condition_without_indication publish 'Short QT Syndrome'
    while excluding 'aliskiren'."""
    supp = tmp_path / "supp.xml"
    supp.write_text(
        '<?xml version="1.0"?><SupplementalRecordSet>'
        '<SupplementalRecord SCRClass="3">'
        '<SupplementalRecordUI>C536914</SupplementalRecordUI>'
        '<SupplementalRecordName><String>Thyroid cancer, medullary</String>'
        '</SupplementalRecordName>'
        '<ConceptList><Concept PreferredConceptYN="Y">'
        '<ConceptUI>M0999001</ConceptUI></Concept></ConceptList>'
        '</SupplementalRecord></SupplementalRecordSet>', encoding="utf-8")
    empty = tmp_path / "desc.xml"
    empty.write_text('<?xml version="1.0"?><DescriptorRecordSet/>', encoding="utf-8")

    got = mesh_concepts.resolve_concepts(empty, supp, {"M0999001"})["M0999001"]
    assert got.record_kind == mesh_concepts.SCR
    assert got.scr_class == "3"


def test_a_descriptor_carries_no_scr_class(tmp_path):
    """DescriptorRecord publishes DescriptorClass, a different vocabulary. Reading it
    into this field would make descriptors indistinguishable from SCR chemicals."""
    desc = tmp_path / "desc.xml"
    desc.write_text(
        '<?xml version="1.0"?><DescriptorRecordSet>'
        '<DescriptorRecord DescriptorClass="1">'
        '<DescriptorUI>D004827</DescriptorUI>'
        '<DescriptorName><String>Epilepsy</String></DescriptorName>'
        '<ConceptList><Concept PreferredConceptYN="Y">'
        '<ConceptUI>M0007720</ConceptUI></Concept></ConceptList>'
        '</DescriptorRecord></DescriptorRecordSet>', encoding="utf-8")
    empty = tmp_path / "supp.xml"
    empty.write_text('<?xml version="1.0"?><SupplementalRecordSet/>', encoding="utf-8")

    got = mesh_concepts.resolve_concepts(desc, empty, {"M0007720"})["M0007720"]
    assert got.record_kind == mesh_concepts.DESCRIPTOR
    assert got.scr_class is None
```

- [ ] **Step 2: Run them and watch them fail**

```bash
uv run pytest tests/test_mesh_concepts.py -k scr_class -v
```

Expected: `TypeError: MeshRecord.__init__() got an unexpected keyword argument` or
`AttributeError: 'MeshRecord' object has no attribute 'scr_class'`.

- [ ] **Step 3: Add the field**

In `MeshRecord`, after `is_preferred_concept`:

```python
    # MeSH's SCRClass, AS PUBLISHED, and None for a descriptor (which carries
    # DescriptorClass, a different vocabulary). Stored rather than interpreted because
    # supp2026 publishes SIX values -- 1: 249,245 · 4: 65,236 · 3: 6,542 · 5: 1,763 ·
    # 2: 1,236 · 6: 23 -- while the documentation describes four, so drugref asserts a
    # meaning for none of them here. Exactly one consumer reads it, and it reads only
    # '3' (rare disease): db/019's gap_condition_without_indication, which needs to tell
    # 'Short QT Syndrome' from 'aliskiren' among records that bear no tree numbers and
    # so have no DAG position to reason about.
    scr_class: str | None = None
```

- [ ] **Step 4: Populate it**

In `_record()`, add the attribute read and pass it through:

```python
    return MeshRecord(concept_ui=concept_ui,
                      record_ui=el.findtext(ui_tag) or "",
                      record_kind=kind,
                      name=el.findtext(name_tag) or "",
                      tree_numbers=trees,
                      unii=frozenset(uniis), cas=frozenset(cas),
                      is_preferred_concept=preferred,
                      # .get() returns None on a DescriptorRecord, which has no such
                      # attribute -- the desired answer, arrived at structurally.
                      scr_class=el.get("SCRClass"))
```

- [ ] **Step 5: Run the tests and the module's suite**

```bash
uv run pytest tests/test_mesh_concepts.py -v
```

Expected: all pass, including the pre-existing tests (the field is defaulted, so
every existing `MeshRecord(...)` construction in tests keeps working).

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/mesh_concepts.py tests/test_mesh_concepts.py
git commit -m "feat(mesh): read SCRClass off supplementary records (5b.2)"
```

---

## Task 3: Extend the fixtures to carry indications

**Files:**
- Modify: `tests/fixtures/make_medrt_subset.py`
- Regenerate: `tests/fixtures/medrt_subset.xml`, then
  `tests/fixtures/mesh_ci_desc_subset.xml` + `tests/fixtures/mesh_ci_supp_subset.xml`
- Verify: the whole suite

**Why this task comes before the schema:** every orchestrator test in Tasks 7–8 asserts
against `medrt_subset.xml`, which today carries exactly **one** `may_treat` and **one**
`may_prevent` association — enough for the parser tests in Task 1, not enough to prove an
ingest. And the MeSH CI fixture's wanted set is **read out of** `medrt_subset.xml`, so
the order is fixed: MED-RT first, MeSH second.

**⚠ Read `tests/fixtures/make_mesh_ci_subset.py`'s module docstring before touching
anything.** The first hand-picked version of that fixture described a world disjoint from
the MED-RT fixture's — every CI object resolved to nothing while both files looked
healthy alone.

- [ ] **Step 1: Extend the MED-RT extractor**

`make_medrt_subset.py` keeps "every association that touches one of our four
ingredients" (step 1 in its `main()`). Indication assertions on those ingredients are
therefore **already selected** — confirm before changing anything:

```bash
grep -c "may_treat\|may_prevent\|may_diagnose\|induces" tests/fixtures/medrt_subset.xml
```

If the count is small (it is: 2), widen the extractor's ingredient set rather than
special-casing predicates — a fixture that carries one assertion per predicate cannot
exercise a closure. Add to its `WANTED` ingredient map two subjects that carry many
indications in the real release, and say why in the file's header comment:

```python
# 6809  metformin      -- 11 may_treat assertions, whose objects (Diabetes Mellitus,
#                         Type 2 and its tree neighbours) give the condition closure
#                         something real to expand over.
# 42463 clopidogrel    -- may_prevent assertions whose objects are CARDIOVASCULAR,
#                         i.e. a different MeSH subtree from the CI_with objects, so
#                         the union closure genuinely widens the DAG (spec 3.6).
```

Keep the endpoint redaction exactly as it is — `tests/test_medrt_parser.py`'s
`test_the_fixture_redacts_snomed_but_keeps_mesh` enforces it, and it is what makes the
committed fixture licence-clean.

- [ ] **Step 2: Regenerate, in this order**

```bash
cd /Users/hherb/src/drugref
python tests/fixtures/make_medrt_subset.py \
  <(unzip -p downloads/MEDRT/Core_MEDRT_XML.zip Core_MEDRT_2026.07.06_XML.xml) \
  > tests/fixtures/medrt_subset.xml
python tests/fixtures/make_mesh_ci_subset.py \
  downloads/mesh/desc2026.gz downloads/mesh/supp2026.gz tests/fixtures/
```

If the process-substitution form fails on your shell, extract the XML to the scratchpad
first and pass the path.

- [ ] **Step 3: Run the whole suite and fix the drift**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
```

**Expect failures, and read each one before changing it.** Tests that assert fixture
counts (`test_medrt_parser.py`'s membership counts, `test_mesh_ci_run.py`'s summary
figures) will move because the fixture now holds more ingredients and more conditions.

The rule for updating them: a count assertion may be updated to the new measured value
**only when you can say why it moved**. A moved count you cannot explain is a defect, not
a stale expectation. Record the explanation in the commit message.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/
git commit -m "test(fixtures): carry indication assertions and their MeSH objects (5b.2)"
```

---

## Task 4: `db/019` — the two relations, the vocabulary, and the writer

**Files:**
- Create: `db/019_mesh_indications.sql`
- Create: `src/drugref/indications.py`
- Create: `tests/test_indications_writer.py`
- Modify: `tests/test_source_clear_contract.py:40-56` (the `EXPECTED_TABLES` map)

**Interfaces:**
- Consumes: nothing from earlier tasks (the writer is independent of the parser).
- Produces:
  - `indications.INDICATION_TABLES: tuple[str, ...]` = `("moiety_condition_indication", "moiety_induced_condition")`
  - `indications.clear_source_indications(conn, source) -> None`
  - `indications.add_condition_indication(conn, subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run_id) -> bool`
  - `indications.add_induced_condition(conn, subject_moiety_uuid, object_condition_uuid, source, ingest_run_id) -> bool`
    — note: **no `relationship` parameter**; the table holds one predicate and the writer
    supplies it, so a caller cannot file a `may_treat` row there.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_indications_writer.py`:

```python
# tests/test_indications_writer.py
"""The indication relations and their single writer (slice 5b.2, db/019)."""
import pytest

from drugref import conditions, ids, indications
from drugref.ingest.mesh_concepts import DESCRIPTOR, MeshRecord

pytestmark = pytest.mark.usefixtures("conn")

EPILEPSY = MeshRecord(concept_ui="M0007720", record_ui="D004827",
                      record_kind=DESCRIPTOR, name="Epilepsy",
                      tree_numbers=("C10.228.140.490",), unii=frozenset(),
                      cas=frozenset(), is_preferred_concept=True)


@pytest.fixture
def a_condition(conn, ingest_run_id):
    condition_uuid, _ = conditions.upsert_condition(conn, EPILEPSY, ingest_run_id,
                                                    "MeSH")
    return condition_uuid


def test_an_indication_is_recorded(conn, a_moiety, a_condition, ingest_run_id):
    assert indications.add_condition_indication(
        conn, a_moiety, a_condition, "may_treat", "MED-RT", ingest_run_id) is True
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication").fetchone()[0] == 1


def test_the_same_assertion_twice_is_harmless(conn, a_moiety, a_condition,
                                              ingest_run_id):
    """A release that states one assertion through two MeSH concepts collapses onto
    the primary key -- 19 assertions do exactly that in the 2026.07.06 release."""
    args = (conn, a_moiety, a_condition, "may_treat", "MED-RT", ingest_run_id)
    assert indications.add_condition_indication(*args) is True
    assert indications.add_condition_indication(*args) is False


def test_two_predicates_on_one_pair_are_two_rows(conn, a_moiety, a_condition,
                                                 ingest_run_id):
    """relationship is IN the key: a drug may both treat and prevent one condition."""
    for predicate in ("may_treat", "may_prevent"):
        assert indications.add_condition_indication(
            conn, a_moiety, a_condition, predicate, "MED-RT", ingest_run_id) is True


def test_an_undeclared_predicate_is_refused(conn, a_moiety, a_condition,
                                            ingest_run_id):
    """The FK into condition_indication_axis is what stops a predicate reaching the
    table before anyone has declared whether it may generalise."""
    with pytest.raises(Exception):
        indications.add_condition_indication(
            conn, a_moiety, a_condition, "may_cure", "MED-RT", ingest_run_id)


def test_a_mistyped_source_is_refused(conn, a_moiety, a_condition, ingest_run_id):
    """db/012 finding 3: an unconstrained source once let 'MEDRT' insert cleanly and
    then match nothing, ever -- a per-source rebuild cannot find rows it cannot name."""
    with pytest.raises(Exception):
        indications.add_condition_indication(
            conn, a_moiety, a_condition, "may_treat", "MEDRT", ingest_run_id)


def test_an_induced_condition_is_recorded(conn, a_moiety, a_condition, ingest_run_id):
    assert indications.add_induced_condition(
        conn, a_moiety, a_condition, "MED-RT", ingest_run_id) is True
    row = conn.execute("SELECT relationship FROM drugref.moiety_induced_condition"
                       ).fetchone()
    assert row[0] == "induces"


def test_induces_cannot_be_filed_as_an_indication(conn, a_moiety, a_condition,
                                                  ingest_run_id):
    """The tables are separate BECAUSE the unfiltered read of each must be one true
    sentence: 'used for this condition' vs 'can CAUSE this condition'. A consumer who
    forgets a WHERE clause must not read 'treats agranulocytosis' off an induces row."""
    with pytest.raises(Exception):
        indications.add_condition_indication(
            conn, a_moiety, a_condition, "induces", "MED-RT", ingest_run_id)


def test_the_axis_forces_a_declaration(conn):
    """NO DEFAULT on generalises_to_descendants: a predicate added later must state
    its own answer (db/014's discipline, after db/012 finding 5)."""
    with pytest.raises(Exception):
        conn.execute("INSERT INTO drugref.condition_indication_axis (relationship) "
                     "VALUES ('may_palliate')")


def test_the_three_therapeutic_predicates_are_declared(conn):
    rows = dict(conn.execute(
        "SELECT relationship, generalises_to_descendants "
        "FROM drugref.condition_indication_axis").fetchall())
    assert rows == {"may_treat": True, "may_prevent": True, "may_diagnose": True}
    assert "induces" not in rows      # it licenses no walk and has no axis row


def test_the_clear_is_scoped_by_source(conn, a_moiety, a_condition):
    """Rebuildable projection: a re-ingest REPLACES this source's rows, and an
    unrelated feed's survive.

    THE RUN IS OPENED HERE, NOT TAKEN FROM THE ingest_run_id FIXTURE, and that is the
    whole point of the test: clear_source_indications scopes on ingest_run.source, NOT
    on the row's own `source` column. The fixture's run is opened under 'PBS', so a row
    written through it would be deleted by a 'PBS' clear while carrying source
    'MED-RT' -- the test would then assert the opposite of what it claims to prove.
    """
    medrt_run = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('MED-RT', 'test', 'test') RETURNING ingest_run_id").fetchone()[0]
    indications.add_condition_indication(conn, a_moiety, a_condition, "may_treat",
                                         "MED-RT", medrt_run)

    indications.clear_source_indications(conn, "PBS")     # an unrelated feed
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication").fetchone()[0] == 1

    indications.clear_source_indications(conn, "MED-RT")
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication").fetchone()[0] == 0
```

**`ingest_run.source` vs the row's `source` column — read this before writing any test
in this slice.** They are different things and the clear uses the former.
`tests/conftest.py:50-56`'s `ingest_run_id` fixture opens its run under `'PBS'`, so a row
written through it belongs to a PBS *run* while carrying `source = 'MED-RT'`. Any test
that means to prove per-source scoping must open its own MED-RT run, as above.

- [ ] **Step 2: Run them and watch them fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_indications_writer.py -v
```

Expected: `ModuleNotFoundError: No module named 'drugref.indications'`.

- [ ] **Step 3: Write the migration**

Create `db/019_mesh_indications.sql`. This task writes sections 1–3; Tasks 6 and 7 append
to the same file.

```sql
-- db/019_mesh_indications.sql
-- Slice 5b.2: MED-RT's MeSH-keyed INDICATIONS, over slice 5b's condition registry.
--
-- TWO RELATIONS, NOT ONE, AND THE TEST IS NOT "ARE THE ENDPOINTS ALIKE" (they are:
-- moiety -> condition). It is WHAT DOES A ROW SAY IF NOBODY FILTERS IT.
--   * moiety_condition_indication -- "this drug is USED FOR this condition", true of
--     may_treat, may_prevent and may_diagnose alike. 18,144 upstream assertions.
--   * moiety_induced_condition    -- "this drug can CAUSE this condition". 170.
-- A consumer who forgets a relationship filter on a shared table would read
-- "carbamazepine treats agranulocytosis" off an induces row. db/010 chose `is_direct`
-- so that a forgetful consumer errs toward RECALL, which is safe for a
-- contraindication; here the same forgetfulness asserts a therapy, so the split is
-- structural rather than a WHERE clause.
--
-- Both are REBUILDABLE PROJECTIONS and CANDIDATE TIER, exactly as slice 5b's relations
-- are: MED-RT does not track label updates, so rows feed review and must not
-- auto-alert. And an indication is not a RECOMMENDATION -- MED-RT asserts that a drug
-- may treat a condition, never that it is appropriate for a given patient, first-line,
-- or safe in combination.

-- ---- 1. the indication vocabulary --------------------------------------------
CREATE TABLE IF NOT EXISTS drugref.condition_indication_axis (
    relationship               text    PRIMARY KEY,
    -- NO DEFAULT, deliberately -- db/014's discipline after db/012 finding 5 found a
    -- comment claiming it while a DEFAULT quietly answered the question. A predicate
    -- added later MUST state its own answer.
    --
    -- DELIBERATELY NOT NAMED expands_descendants, because it licenses something
    -- WEAKER. condition_ci_axis.expands_descendants = true says the rule FIRES for the
    -- descendant: a patient coded Temporal Lobe Epilepsy IS a patient with epilepsy, so
    -- a contraindication on Epilepsy holds. Applied to an indication the same walk
    -- distributes over the OBJECT's subclasses, which the release never asserted -- one
    -- may_treat rule on Neoplasms would manufacture 702 therapeutic claims, Infections
    -- 785, and the whole set inflates 13x/41x/75x. So nothing derived is ever STORED;
    -- this column governs only whether indications_for_condition may OFFER a rule from
    -- an ancestor, LABELLED as a generalisation.
    generalises_to_descendants boolean NOT NULL
);

INSERT INTO drugref.condition_indication_axis (relationship, generalises_to_descendants)
VALUES ('may_treat', true), ('may_prevent', true), ('may_diagnose', true)
ON CONFLICT (relationship) DO NOTHING;

COMMENT ON TABLE drugref.condition_indication_axis IS
    'Admissible drug-condition INDICATION predicates, and whether a rule on one may be '
    'offered for a more specific condition as a labelled generalisation. '
    'moiety_condition_indication.relationship is a foreign key into this table. '
    'induces is deliberately ABSENT: it is not an indication, it licenses no walk, and '
    'it lives in its own table.';
COMMENT ON COLUMN drugref.condition_indication_axis.generalises_to_descendants IS
    'True for all three therapeutic predicates: a drug indicated for Epilepsy is worth '
    'OFFERING for Temporal Lobe Epilepsy, as the weaker statement "indicated for a more '
    'general form of this diagnosis". It is NOT expands_descendants -- nothing derived '
    'is stored, and a derived row is a weaker claim rather than a wider one.';

-- ---- 2. drug -> condition, therapeutic ---------------------------------------
CREATE TABLE IF NOT EXISTS drugref.moiety_condition_indication (
    subject_moiety_uuid   uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_condition_uuid uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    relationship          text   NOT NULL
        REFERENCES drugref.condition_indication_axis(relationship),
    -- SOURCE IS IN THE KEY and CHECK-constrained, for db/006 finding 2's reason as
    -- restated by db/014: without it a second authority's independent assertion is
    -- swallowed by ON CONFLICT DO NOTHING and then deleted by the next MED-RT rebuild.
    -- The CHECK is not decoration -- db/012 finding 3: an unconstrained source once let
    -- 'MEDRT' insert cleanly and match nothing, ever.
    source                text   NOT NULL
        CONSTRAINT moiety_condition_indication_source CHECK (source IN ('MED-RT')),
    ingest_run            bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (subject_moiety_uuid, object_condition_uuid, relationship, source)
);

CREATE INDEX IF NOT EXISTS moiety_condition_indication_by_condition
    ON drugref.moiety_condition_indication (object_condition_uuid);

COMMENT ON TABLE drugref.moiety_condition_indication IS
    'Drug-condition INDICATIONS: the subject moiety is used for the object condition '
    '(may_treat / may_prevent / may_diagnose). A REBUILDABLE PROJECTION, CANDIDATE TIER '
    '-- rows feed review and must not auto-alert. NOT A RECOMMENDATION: MED-RT asserts '
    'that a drug may treat a condition, never that it is appropriate for a given '
    'patient, first-line, correctly dosed, or safe in combination, and it asserts no '
    'ordering among the drugs that treat one condition. NOTHING HERE IS DERIVED -- '
    'every row is an assertion the release makes; generalisation happens at read time '
    'in indications_for_condition and is labelled there.';
COMMENT ON COLUMN drugref.moiety_condition_indication.object_condition_uuid IS
    'The condition treated, prevented or diagnosed. Usually a disease, but MED-RT also '
    'names the ORGANISM for prevention (Influenza A virus carries 76 may_prevent '
    'assertions -- these are the vaccines) and, rarely, a treatment target such as LDL '
    'Cholesterol. condition.tree_numbers is what lets a consumer tell them apart.';

-- ---- 3. drug -> condition, caused --------------------------------------------
CREATE TABLE IF NOT EXISTS drugref.moiety_induced_condition (
    subject_moiety_uuid   uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_condition_uuid uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    -- A CHECK, NOT AN FK, and the asymmetry with the table above is db/014's own
    -- argument: an FK exists to keep a predicate list in step with a SECOND list held
    -- elsewhere (a view's CASE, a walk's gate). Nothing walks this table -- induces has
    -- no axis row and licenses no generalisation -- so there is no second list, and an
    -- FK would copy the form of that fix while its cause is absent.
    relationship          text   NOT NULL
        CONSTRAINT moiety_induced_condition_relationship
        CHECK (relationship IN ('induces')),
    source                text   NOT NULL
        CONSTRAINT moiety_induced_condition_source CHECK (source IN ('MED-RT')),
    ingest_run            bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (subject_moiety_uuid, object_condition_uuid, relationship, source)
);

CREATE INDEX IF NOT EXISTS moiety_induced_condition_by_condition
    ON drugref.moiety_induced_condition (object_condition_uuid);

COMMENT ON TABLE drugref.moiety_induced_condition IS
    'States a drug CAUSES: Unconsciousness (32 rules -- the anaesthetics), Mydriasis '
    '(14), Diarrhea (8). NEITHER an indication NOR a contraindication, which is why it '
    'has its own table: sometimes the induced state is the therapeutic point and '
    'sometimes it is the adverse effect, and MED-RT does not say which. A REBUILDABLE '
    'PROJECTION, CANDIDATE TIER.';

-- ---- 4. the cached SCR class -------------------------------------------------
--
-- Stored AS PUBLISHED and with no CHECK, exactly as condition.tree_numbers is: it is
-- opaque source data. supp2026 publishes SIX values (1: 249,245 · 4: 65,236 ·
-- 3: 6,542 · 5: 1,763 · 2: 1,236 · 6: 23) while the documentation describes four, so a
-- CHECK would abort an ingest the first time NLM adds a seventh. Drift is caught by a
-- COUNT instead -- the run summary reports registered conditions per scr_class, the
-- same posture skipped_predicates takes -- so a renumbering shows up as a number that
-- moved rather than as a gap view going quiet.
ALTER TABLE drugref.condition ADD COLUMN IF NOT EXISTS scr_class text;

COMMENT ON COLUMN drugref.condition.scr_class IS
    'MeSH SCRClass as published, NULL for a descriptor (which carries DescriptorClass, '
    'a different vocabulary). Only value 3 (rare disease) is load-bearing, and only in '
    'gap_condition_without_indication: an SCR bears no tree numbers, so nothing else '
    'can tell "Short QT Syndrome" from "aliskiren". drugref asserts no meaning for 5 '
    'and 6, which are published but undocumented.';
```

- [ ] **Step 4: Write the writer module**

Create `src/drugref/indications.py`:

```python
"""The ONLY module that writes the indication tables.

Mirrors interactions.py's single-writer role and enforces the same discipline:
`moiety_condition_indication` and `moiety_induced_condition` are REBUILDABLE
PROJECTIONS of MED-RT, not the append-only signed overlay. So inserts dedupe
(ON CONFLICT DO NOTHING) and clear_source_indications() deliberately DELETEs -- an
indication withdrawn upstream has to disappear here too, which an insert-only merge
could never express.

WHY THIS IS NOT IN interactions.py. That module answers "what must not be given";
this one answers "what is this drug for". They share a shape and nothing else, and
interactions.py is already the writer of four tables. Keeping them apart is also what
makes the two-table split of db/019 legible at the call site.
"""
import uuid

import psycopg

from drugref import db

# The predicate moiety_induced_condition holds. Named here rather than spelled at the
# call site so the writer supplies it and a caller CANNOT file an induces row through
# the indication path (or the reverse) by passing a string.
INDUCES = "induces"

# Both relations one 5b.2 ingest writes. Restated independently in
# tests/test_source_clear_contract.py, so dropping one fails loudly instead of leaving
# a projection that grows a little on every ingest (#43).
INDICATION_TABLES = ("moiety_condition_indication", "moiety_induced_condition")


def clear_source_indications(conn: psycopg.Connection, source: str) -> None:
    """Drop every indication and induced-state row contributed by `source`.

    Covers BOTH tables, because one ingest writes both and a partial clear would leave
    the last release's rows beside this one's. Scoped by source so an unrelated feed's
    rows survive.

    No `reason` narrowing here, unlike classes.clear_source_unmatched_ingredients:
    these tables have exactly ONE writer, which is the state #39 restored for
    ingest_unmatched_ingredient rather than the exception it made for it.
    """
    db.clear_source_tables(conn, INDICATION_TABLES, source)


def add_condition_indication(conn: psycopg.Connection, subject_moiety_uuid: uuid.UUID,
                             object_condition_uuid: uuid.UUID, relationship: str,
                             source: str, ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` is used for `object_condition_uuid`, on
    `relationship` (may_treat / may_prevent / may_diagnose).

    Returns True if a new row was inserted. ON CONFLICT DO NOTHING keeps a release that
    states one assertion through two MeSH concepts harmless -- 19 assertions in the
    2026.07.06 release collapse exactly that way, which is why the caller's count comes
    from this return value and not from the assertion list's length.

    `relationship` is a foreign key into condition_indication_axis, so a predicate
    nobody has declared a generalisation policy for cannot reach the table.
    """
    cur = conn.execute(
        "INSERT INTO drugref.moiety_condition_indication "
        "(subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_condition_uuid, relationship, source,
         ingest_run_id))
    return cur.rowcount == 1


def add_induced_condition(conn: psycopg.Connection, subject_moiety_uuid: uuid.UUID,
                          object_condition_uuid: uuid.UUID, source: str,
                          ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` CAUSES `object_condition_uuid`.

    Takes no `relationship` argument on purpose: the table holds one predicate, and
    supplying it here means a caller cannot file a may_treat row in the induced-state
    table by passing the wrong string. The database CHECK is the second line of that
    defence, not the first.

    Returns True if a new row was inserted.
    """
    cur = conn.execute(
        "INSERT INTO drugref.moiety_induced_condition "
        "(subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_condition_uuid, INDUCES, source, ingest_run_id))
    return cur.rowcount == 1
```

- [ ] **Step 5: Add the tables to the clear contract**

In `tests/test_source_clear_contract.py`, import `indications` and add to
`EXPECTED_TABLES`:

```python
    "indications.INDICATION_TABLES": (
        indications.INDICATION_TABLES,
        ("moiety_condition_indication", "moiety_induced_condition")),
```

- [ ] **Step 6: Run the tests and watch them pass**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_indications_writer.py tests/test_source_clear_contract.py -v
```

Expected: all pass. Then run the full suite — `db/019` is new, so `_migrated` applies it
on the next session; nothing existing should break.

- [ ] **Step 7: Commit**

```bash
git add db/019_mesh_indications.sql src/drugref/indications.py \
  tests/test_indications_writer.py tests/test_source_clear_contract.py
git commit -m "feat(indications): two relations, one vocabulary, one writer (5b.2)"
```

---

## Task 5: The read path — one reach view, one ancestor walk

**Files:**
- Modify: `db/019_mesh_indications.sql` (append section 5)
- Test: `tests/test_indication_read_path.py` (create)

**Interfaces:**
- Consumes: Task 4's tables.
- Produces:
  - view `drugref.condition_indication_reach(condition_uuid, direct_indication_rules, generalised_indication_rules)` — one row per registry condition, zeroes where nothing reaches it.
  - function `drugref.indications_for_condition(patient_condition uuid)` returning
    `(subject_moiety uuid, object_condition uuid, member_condition uuid, is_direct boolean, relationship text, source text)`.

**Read first:** `db/018:444-493` (`contraindications_for_condition`) — this function is
the same walk over the same graph, and matching its column shape exactly is deliberate so
a consumer sees one shape for both halves.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_indication_read_path.py`:

```python
# tests/test_indication_read_path.py
"""Generalisation, and the one place it happens (slice 5b.2, db/019).

Nothing derived is STORED, so unlike slice 5b there is no expanded view to compare the
function against. What is pinned instead is that the function and
condition_indication_reach -- the only other statement of the same rule -- agree, which
is what makes two statements of one rule safe (db/006, and db/018's round, where a
quantity stated twice disagreed and a whole class of dead rules went unreported).
"""
import pytest

from drugref import conditions, indications
from drugref.ingest.mesh_concepts import DESCRIPTOR, MeshRecord

pytestmark = pytest.mark.usefixtures("conn")


def record(ui: str, name: str, *trees: str) -> MeshRecord:
    return MeshRecord(concept_ui=f"M{ui}", record_ui=ui, record_kind=DESCRIPTOR,
                      name=name, tree_numbers=trees, unii=frozenset(),
                      cas=frozenset(), is_preferred_concept=True)


@pytest.fixture
def dag(conn, ingest_run_id):
    """Epilepsy -> Temporal Lobe Epilepsy -> a deeper node, plus an unrelated root."""
    made = {}
    for ui, name, trees in (
            ("D004827", "Epilepsy", ("C10.228.140.490",)),
            ("D004833", "Epilepsy, Temporal Lobe", ("C10.228.140.490.360",)),
            ("D017034", "Epilepsy, Frontal Lobe", ("C10.228.140.490.360.300",)),
            ("D006973", "Hypertension", ("C14.907.489",))):
        made[ui], _ = conditions.upsert_condition(conn, record(ui, name, *trees),
                                                  ingest_run_id, "MeSH")
    for child, parent in (("D004833", "D004827"), ("D017034", "D004833")):
        conditions.add_condition_parent_edge(conn, made[child], made[parent],
                                             ingest_run_id)
    return made


def test_a_direct_indication_is_returned_as_direct(conn, a_moiety, dag, ingest_run_id):
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    rows = conn.execute(
        "SELECT is_direct, object_condition FROM "
        "drugref.indications_for_condition(%s)", (dag["D004827"],)).fetchall()
    assert rows == [(True, dag["D004827"])]


def test_an_ancestors_indication_is_offered_as_a_generalisation(conn, a_moiety, dag,
                                                                ingest_run_id):
    """The clinical case: a rule on Epilepsy reaches a patient coded Frontal Lobe
    Epilepsy TWO levels down, and the row says which condition it was written against
    so a consumer can render 'indicated for Epilepsy, a more general form'."""
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    rows = conn.execute(
        "SELECT is_direct, object_condition FROM "
        "drugref.indications_for_condition(%s)", (dag["D017034"],)).fetchall()
    assert rows == [(False, dag["D004827"])]


def test_a_sibling_branch_is_not_reached(conn, a_moiety, dag, ingest_run_id):
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    assert conn.execute("SELECT count(*) FROM drugref.indications_for_condition(%s)",
                        (dag["D006973"],)).fetchone()[0] == 0


def test_expansion_never_runs_DOWNWARD(conn, a_moiety, dag, ingest_run_id):
    """THE CENTRAL GUARANTEE OF THIS SLICE. A rule on a SPECIFIC condition must never
    reach the general one: 'treats Frontal Lobe Epilepsy' does not mean 'treats
    epilepsy', and the inverse direction is what would manufacture 702 claims from one
    rule on Neoplasms."""
    indications.add_condition_indication(conn, a_moiety, dag["D017034"], "may_treat",
                                         "MED-RT", ingest_run_id)
    assert conn.execute("SELECT count(*) FROM drugref.indications_for_condition(%s)",
                        (dag["D004827"],)).fetchone()[0] == 0


def test_a_non_generalising_predicate_returns_only_direct_rows(conn, a_moiety, dag,
                                                               ingest_run_id):
    """Switching generalisation off is ONE UPDATE and needs no view or function edit."""
    conn.execute("UPDATE drugref.condition_indication_axis "
                 "SET generalises_to_descendants = false WHERE relationship = 'may_treat'")
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    assert conn.execute("SELECT count(*) FROM drugref.indications_for_condition(%s)",
                        (dag["D004833"],)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM drugref.indications_for_condition(%s)",
                        (dag["D004827"],)).fetchone()[0] == 1


def test_induced_states_never_appear(conn, a_moiety, dag, ingest_run_id):
    """'can cause' must not be readable through the indication path, at any distance."""
    indications.add_induced_condition(conn, a_moiety, dag["D004827"], "MED-RT",
                                      ingest_run_id)
    for uuid_ in (dag["D004827"], dag["D004833"]):
        assert conn.execute(
            "SELECT count(*) FROM drugref.indications_for_condition(%s)",
            (uuid_,)).fetchone()[0] == 0


def test_the_reach_view_counts_direct_and_generalised_separately(conn, a_moiety, dag,
                                                                 ingest_run_id):
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    rows = dict(conn.execute(
        "SELECT condition_uuid, direct_indication_rules || '/' || "
        "generalised_indication_rules FROM drugref.condition_indication_reach"
    ).fetchall())
    assert rows[dag["D004827"]] == "1/0"
    assert rows[dag["D004833"]] == "0/1"
    assert rows[dag["D017034"]] == "0/1"
    assert rows[dag["D006973"]] == "0/0"     # present with zeroes, never absent


def test_the_function_and_the_reach_view_agree(conn, a_moiety, dag, ingest_run_id):
    """The pin that makes two statements of one rule safe. db/018's round found a
    quantity stated twice where only one copy learned a correction; here the equality is
    asserted rather than assumed, and the real-release run checks it over every
    condition."""
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    indications.add_condition_indication(conn, a_moiety, dag["D004833"], "may_prevent",
                                         "MED-RT", ingest_run_id)
    for condition_uuid in dag.values():
        from_view = conn.execute(
            "SELECT direct_indication_rules + generalised_indication_rules "
            "FROM drugref.condition_indication_reach WHERE condition_uuid = %s",
            (condition_uuid,)).fetchone()[0]
        from_function = conn.execute(
            "SELECT count(*) FROM drugref.indications_for_condition(%s)",
            (condition_uuid,)).fetchone()[0]
        assert from_view == from_function, f"disagreement at {condition_uuid}"


def test_the_walk_terminates_under_a_cycle(conn, a_moiety, dag, ingest_run_id):
    """db/013 forbids only SELF-parenting; a longer cycle must be survived by the walk
    itself, as db/012's ci_class_subtree explains."""
    conditions.add_condition_parent_edge(conn, dag["D004827"], dag["D017034"],
                                         ingest_run_id)
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    assert conn.execute("SELECT count(*) FROM drugref.indications_for_condition(%s)",
                        (dag["D004833"],)).fetchone()[0] == 1
```

- [ ] **Step 2: Run them and watch them fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_indication_read_path.py -v
```

Expected: failures citing `relation "drugref.condition_indication_reach" does not exist`
and `function drugref.indications_for_condition(uuid) does not exist`.

- [ ] **Step 3: Append the read path to `db/019`**

```sql
-- ============================================================================
-- 5. THE READ PATH -- one walk, in the only sound direction
-- ============================================================================
--
-- There is deliberately NO condition_indication_expanded view to mirror slice 5b's.
-- 5b needs one because its rows are stored expandable and whole-set access is a real
-- use; here nothing is stored expanded, so the base table IS whole-set access and a
-- second walk would buy nothing while creating exactly the disagreement db/006 warns
-- about. The ONE other statement of the reach rule is the view below, and a test pins
-- the two against each other.

CREATE OR REPLACE VIEW drugref.condition_indication_reach AS
WITH RECURSIVE subtree(root_uuid, condition_uuid) AS (
    SELECT DISTINCT i.object_condition_uuid, i.object_condition_uuid
    FROM   drugref.moiety_condition_indication i
  UNION
    SELECT s.root_uuid, cp.child_condition_uuid
    FROM   subtree s
    JOIN   drugref.condition_parent cp ON cp.parent_condition_uuid = s.condition_uuid
),
reached AS (
    SELECT s.condition_uuid,
           count(*) FILTER (WHERE s.condition_uuid = i.object_condition_uuid)
               AS direct_rules,
           count(*) FILTER (WHERE s.condition_uuid <> i.object_condition_uuid
                            AND   a.generalises_to_descendants) AS generalised_rules
    FROM   subtree s
    JOIN   drugref.moiety_condition_indication i
           ON i.object_condition_uuid = s.root_uuid
    JOIN   drugref.condition_indication_axis a ON a.relationship = i.relationship
    GROUP  BY s.condition_uuid
)
SELECT c.condition_uuid,
       COALESCE(r.direct_rules, 0)      AS direct_indication_rules,
       COALESCE(r.generalised_rules, 0) AS generalised_indication_rules
FROM   drugref.condition c
LEFT   JOIN reached r ON r.condition_uuid = c.condition_uuid;

COMMENT ON VIEW drugref.condition_indication_reach IS
    'For EVERY registry condition, how many indication rules reach it: directly, and '
    'by generalisation from an ancestor. One row per condition -- a condition nothing '
    'reaches is present with zeroes, never absent, which is what lets '
    'gap_condition_without_indication be a filter on this view rather than a second '
    'statement of the same walk (db/018: one quantity stated twice will disagree). '
    'induces is excluded: it holds no axis row and licenses no walk.';
COMMENT ON COLUMN drugref.condition_indication_reach.generalised_indication_rules IS
    'Rules written against an ANCESTOR of this condition. A WEAKER claim, not a wider '
    'one -- the drug is indicated for a more general form of the diagnosis, which is '
    'not the same as being indicated for the diagnosis.';

CREATE OR REPLACE FUNCTION drugref.indications_for_condition(patient_condition uuid)
RETURNS TABLE (subject_moiety   uuid,
               object_condition uuid,
               member_condition uuid,
               is_direct        boolean,
               relationship     text,
               source           text)
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    WITH RECURSIVE ancestor(condition_uuid) AS (
        SELECT patient_condition
      UNION
        SELECT cp.parent_condition_uuid
        FROM   ancestor an
        JOIN   drugref.condition_parent cp
               ON cp.child_condition_uuid = an.condition_uuid
    )
    SELECT i.subject_moiety_uuid,
           i.object_condition_uuid,
           -- Always the condition asked about: this walk climbs from it, so every row
           -- returned is a rule that reaches THAT condition. Returned anyway so the
           -- shape matches contraindications_for_condition column for column.
           patient_condition,
           i.object_condition_uuid = patient_condition,
           i.relationship,
           i.source
    FROM   drugref.moiety_condition_indication i
    JOIN   drugref.condition_indication_axis a ON a.relationship = i.relationship
    JOIN   ancestor an ON an.condition_uuid = i.object_condition_uuid
    WHERE  a.generalises_to_descendants
       OR  i.object_condition_uuid = patient_condition;
$$;

COMMENT ON FUNCTION drugref.indications_for_condition(uuid) IS
    'Every indication that reaches a patient coded with this condition, found by '
    'walking UP the condition DAG from it. THE DIRECTION IS THE POINT: walking DOWN '
    'from a rule''s object would distribute a therapeutic claim over the object''s '
    'subclasses, and one may_treat rule on Neoplasms would manufacture 702 claims the '
    'release never made. Walking up instead yields a WEAKER statement that is true. '
    'A row with is_direct = false MUST be rendered as "indicated for <object_condition>, '
    'a more general form of this diagnosis" and NEVER as an indication for the coded '
    'diagnosis -- object_condition is a column for exactly that reason. CANDIDATE TIER '
    'and not a recommendation: no severity, no line of therapy, no ordering. UNION over '
    'the node, not the path, so it terminates under a cycle (db/013 forbids only '
    'self-parenting).';
```

- [ ] **Step 4: Run the tests and watch them pass**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_indication_read_path.py -v
```

Expected: all pass. If `test_the_reach_view_counts_direct_and_generalised_separately`
fails on the `0/0` row, the `LEFT JOIN` from `condition` was dropped — the gap view in
Task 6 depends on unreached conditions being **present**, so fix the view rather than the
test.

- [ ] **Step 5: Commit**

```bash
git add db/019_mesh_indications.sql tests/test_indication_read_path.py
git commit -m "feat(indications): generalise UP the condition DAG, labelled (5b.2)"
```

---

## Task 6: The seventh gap kind

**Files:**
- Modify: `db/019_mesh_indications.sql` (append section 6)
- Modify: `src/drugref/questions.py:35-166` (`_GAP_SOURCES`)
- Modify: `tests/test_gap_views.py`

**Interfaces:**
- Consumes: Task 5's `condition_indication_reach`.
- Produces: view `drugref.gap_condition_without_indication(condition_uuid, name, source_code, record_kind)` and gap kind `'condition_without_indication'`.

**Measured target: 66 rows** — 55 conditions carrying a C or F tree number, plus 11
tree-less `SCRClass = 3` rare diseases. **789 unreached conditions are excluded on
purpose**, 669 of them surgical procedures.

**⚠ The trap in this task.** The `reason` CHECK widening (Task 7) and the `gap_kind`
CHECK widening (here) both use `db/016`'s guard idiom — `pg_get_constraintdef(oid) LIKE
'%...%'`. For `gap_kind`, `'%condition_without_indication%'` is distinctive. For
`reason`, **`'%indication%'` matches the existing constraint** because
`'contraindication'` contains `'indication'` — the guard would be satisfied, the widening
would silently not happen, and the first `reason = 'indication'` write would fail. Use
`'%''indication''::text%'` (with the quotes), which cannot match `'contraindication'::text`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gap_views.py` (follow the module's existing fixture idiom):

```python
def test_a_disease_with_no_indication_anywhere_above_it_is_published(conn,
                                                                     ingest_run_id):
    """The gap this kind exists for: drugref holds nothing that treats this disease,
    and nothing that treats anything above it either."""
    orphan = _register_condition(conn, ingest_run_id, "D000000", "Rare Disease X",
                                 trees=("C10.999",))
    rows = [r[0] for r in conn.execute(
        "SELECT condition_uuid FROM drugref.gap_condition_without_indication").fetchall()]
    assert orphan in rows


def test_a_disease_reached_by_an_ancestors_indication_is_not_a_gap(conn, a_moiety,
                                                                   ingest_run_id):
    parent = _register_condition(conn, ingest_run_id, "D004827", "Epilepsy",
                                 trees=("C10.228.140.490",))
    child = _register_condition(conn, ingest_run_id, "D004833", "Epilepsy, Temporal",
                                trees=("C10.228.140.490.360",))
    conditions.add_condition_parent_edge(conn, child, parent, ingest_run_id)
    indications.add_condition_indication(conn, a_moiety, parent, "may_treat",
                                         "MED-RT", ingest_run_id)
    rows = [r[0] for r in conn.execute(
        "SELECT condition_uuid FROM drugref.gap_condition_without_indication").fetchall()]
    assert child not in rows and parent not in rows


def test_a_surgical_procedure_is_never_a_gap(conn, ingest_run_id):
    """669 of the 855 unreached conditions are E-tree procedures. 'Nothing is indicated
    for Abdominoplasty' is a category error, not a gap, and 789 such rows would bury the
    66 real ones under externally-citable question_uuids for noise."""
    procedure = _register_condition(conn, ingest_run_id, "D015917", "Abdominoplasty",
                                    trees=("E04.680",))
    rows = [r[0] for r in conn.execute(
        "SELECT condition_uuid FROM drugref.gap_condition_without_indication").fetchall()]
    assert procedure not in rows


def test_a_rare_disease_SCR_is_a_gap_but_a_chemical_SCR_is_not(conn, ingest_run_id):
    """An SCR bears no tree numbers, so it has no DAG position and 'nothing above it'
    is vacuously true. SCRClass is the only thing that separates the 11 real rare
    diseases from records like aliskiren."""
    rare = _register_condition(conn, ingest_run_id, "C580439", "Short QT Syndrome",
                               trees=(), record_kind="SCR", scr_class="3")
    chemical = _register_condition(conn, ingest_run_id, "C446481", "aliskiren",
                                   trees=(), record_kind="SCR", scr_class="1")
    rows = [r[0] for r in conn.execute(
        "SELECT condition_uuid FROM drugref.gap_condition_without_indication").fetchall()]
    assert rare in rows
    assert chemical not in rows


def test_the_gap_reaches_the_question_register(conn, ingest_run_id):
    _register_condition(conn, ingest_run_id, "D000000", "Rare Disease X",
                        trees=("C10.999",))
    questions.register_from_gaps(conn, ingest_run_id)
    row = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'condition_without_indication'").fetchone()
    assert row[0].startswith("CONDITION:")
    assert "Rare Disease X" in row[1]


def test_the_views_grain_is_the_gap_keys_grain(conn, ingest_run_id):
    """#41's test, restated for this kind: question_uuid is a pure function of
    (gap_kind, gap_key), so two view rows folding to one key would hand two conditions
    ONE immortal question that append-only curator rows then attach to."""
    for ui in ("D000001", "D000002"):
        _register_condition(conn, ingest_run_id, ui, f"Disease {ui}",
                            trees=(f"C10.99{ui[-1]}",))
    keys = conn.execute(
        "SELECT count(*), count(DISTINCT 'CONDITION:' || condition_uuid) "
        "FROM drugref.gap_condition_without_indication").fetchone()
    assert keys[0] == keys[1]
```

You will need a `_register_condition(conn, ingest_run_id, ui, name, trees, record_kind,
scr_class)` helper in that module; write it with `conditions.upsert_condition` plus a
follow-up `UPDATE ... SET scr_class = %s` (the writer does not set it until Task 7).

- [ ] **Step 2: Run them and watch them fail**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_gap_views.py -k indication -v
```

Expected: `relation "drugref.gap_condition_without_indication" does not exist`.

- [ ] **Step 3: Append the gap view and widen the CHECK**

```sql
-- ============================================================================
-- 6. THE SEVENTH GAP KIND -- diseases drugref knows nothing to give for
-- ============================================================================
--
-- A COMPLEMENTARY FILTER ON condition_indication_reach, not a second walk: `= 0` on
-- the sum of its two columns. db/018's round is why -- the reach measure was stated
-- twice there, only one copy learned a correction, and a whole class of dead rules was
-- reported by nothing.
--
-- SCOPED, AND THE SCOPE IS A JUDGEMENT WITH NUMBERS BEHIND IT. 855 registry conditions
-- are unreached; 66 of them are gaps. The 789 excluded are:
--   669  E-tree SURGICAL PROCEDURES (Abdominoplasty, Ablation Techniques)
--    40  D-tree chemicals · 35 B-tree organisms · 32 G-tree phenomena (Beer, Cheese)
--    25  N-tree health care · 13 M-tree demographics (Adolescent, Aged) · 12 J · rest
--     7  tree-less SCRs that are NOT rare diseases (aliskiren, formaldehyde-serum
--        albumin) -- see below
-- "Nothing is indicated for Abdominoplasty" is a category error, not a gap, and
-- question_uuid is EXTERNALLY CITABLE and immortal: minting 789 of them for noise
-- would bury the 66 real rows on a worklist whose whole value is that a curator can
-- work it.
--
-- TREE-LESS RECORDS ARE EXCLUDED ON A DIFFERENT GROUND, and the distinction matters:
-- an SCR holds no DAG position at all, so "no indication above it" is VACUOUSLY true
-- and says nothing. The SCRClass = 3 carve-out recovers exactly the 11 for which the
-- vacuous answer is also the clinically right one -- a rare disease with no recorded
-- indication is the most valuable row on this list (Short QT Syndrome, succinic
-- semialdehyde dehydrogenase deficiency, Familial medullary thyroid carcinoma).
CREATE OR REPLACE VIEW drugref.gap_condition_without_indication AS
SELECT c.condition_uuid,
       c.name,
       c.source_code,
       c.record_kind
FROM   drugref.condition c
JOIN   drugref.condition_indication_reach r ON r.condition_uuid = c.condition_uuid
WHERE  r.direct_indication_rules + r.generalised_indication_rules = 0
AND    (EXISTS (SELECT 1 FROM unnest(c.tree_numbers) t
                WHERE  left(t, 1) IN ('C', 'F'))
        -- 3 = rare disease. The only SCRClass value drugref reads, and the only place
        -- it is read. See condition.scr_class on why no CHECK constrains it.
        OR (c.tree_numbers = '{}' AND c.scr_class = '3'));

COMMENT ON VIEW drugref.gap_condition_without_indication IS
    'DISEASES drugref holds no indication for -- not directly, and not from any '
    'condition above them. 66 rows against the 2026 releases: 55 carrying a C '
    '(Diseases) or F (Psychiatry) tree number, plus 11 tree-less SCRClass-3 rare '
    'diseases. Scoped DELIBERATELY: 789 further unreached conditions are excluded, 669 '
    'of them surgical procedures, because "nothing is indicated for Abdominoplasty" is '
    'a category error rather than a gap. Answerable from openFDA-SPL labels (tier 2) '
    'or MeDIC (tier 3) before literature.';

-- Admit the seventh question kind. Guarded on the constraint's TEXT, as db/016 and
-- db/018 are, and 'condition_without_indication' is distinctive enough to guard on.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%condition_without_indication%') THEN
        ALTER TABLE drugref.open_question
            DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question
            ADD CONSTRAINT open_question_gap_kind CHECK (gap_kind IN (
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root',
                'unresolved_ci_object', 'dead_by_expansion_policy',
                'condition_without_indication'));
    END IF;
END $$;
```

- [ ] **Step 4: Wire it into `questions.py`**

Add to `_GAP_SOURCES`:

```python
    # Slice 5b.2. Diseases nothing in the registry treats, prevents or diagnoses --
    # directly or from above. The gap_key is the REGISTERED-OBJECT form (MOIETY:,
    # CLASS:) rather than unresolved_ci_object's {NAMESPACE}:{code}, and the difference
    # is real: this subject IS registered and has a drugref UUID to cite, whereas that
    # one is an upstream record drugref never registered, which is exactly why it is a
    # gap. The text names the disease AND its MeSH code so the row is usable as a
    # literature search on its own.
    "condition_without_indication": {
        "view": "gap_condition_without_indication",
        "key_sql": "'CONDITION:' || condition_uuid",
        "text_sql": (
            "'Which drugs treat, prevent or diagnose ' || name || ' (MeSH ' || "
            "source_code || ')? No may_treat, may_prevent or may_diagnose assertion "
            "names it or any condition above it in the MeSH tree, so drugref can offer "
            "nothing for a patient coded with it.'"),
    },
```

`source_tier` needs **no change**: `openFDA-SPL` (rank 2) and `MeDIC` (rank 3) already
name the sources that answer this, and `question_worklist` orders generically.

- [ ] **Step 5: Run the tests and watch them pass**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' \
  uv run pytest tests/test_gap_views.py tests/test_questions.py -v
```

- [ ] **Step 6: Commit**

```bash
git add db/019_mesh_indications.sql src/drugref/questions.py tests/test_gap_views.py
git commit -m "feat(questions): publish diseases with no known indication (5b.2)"
```

---

## Task 7: One orchestrator for both halves — refactor, no behaviour change

**Files:**
- Create: `src/drugref/ingest/mesh_rel_run.py`, `src/drugref/ingest/mesh_ci_relations.py`
- Delete: `src/drugref/ingest/mesh_ci_run.py`
- Modify: `src/drugref/conditions.py` (store `scr_class`), `db/019_mesh_indications.sql`
  (append the `reason` widening), `tests/test_mesh_ci_run.py`

**This task changes NO numbers.** Every count the suite asserts must come out identical;
that is the whole point of separating it from Task 8. If a number moves here, stop and
find out why before continuing.

**Why one orchestrator (spec §6.1):** `condition` and `condition_parent` are rebuilt per
`ingest_run.source`, and both halves run under `MED-RT`. Two orchestrators would each
clear the other's DAG edges — `#39` one layer deeper and **unfixable by a
discriminator**, because a `(child, parent)` edge is derived by *both* closures and
cannot be split by a `reason` column.

**Interfaces:**
- Produces:
  - `mesh_rel_run.ingest_mesh_relations(conn, *, medrt_path, desc_path, supp_path, upstream_release) -> MeshRelSummary`
  - `MeshRelSummary(registry: RegistryTally, contraindications: CiTally, indications: IndicationTally)` — nested so the registry figures are stated **once**; reporting `conditions_registered` twice is the "one quantity stated twice" trap.
  - `mesh_ci_relations.write_contraindications(conn, assertions, records, uuid_by_code, indexes, run_id) -> CiRelations`

- [ ] **Step 1: Move the contraindication pass out, unchanged**

Create `ingest/mesh_ci_relations.py` holding `_Relations` (renamed `CiRelations`),
`_resolve_object_moiety` and `_write_relations` (renamed `write_contraindications`),
**with their docstrings carried over verbatim** — they hold measured findings (the 370-vs-405
ordering defect, the 103-vs-108 record-grain argument, the self-pair count) that must not
be lost in the move.

- [ ] **Step 2: Create the orchestrator**

`ingest/mesh_rel_run.py` holds what is now `_ingest`, extended so that:
- the closure is taken over **all** MeSH-keyed objects — CI_with **and** all four
  indication predicates — in one call (spec §6.1);
- `condition_writer.upsert_condition` is called once per closure record;
- the moiety indexes are built **once** and passed to both passes.

Carry `mesh_ci_run.py`'s module docstring across, updated: it now describes a run that
reads five predicates rather than two.

- [ ] **Step 3: Store `scr_class`**

In `conditions.py`, add the column to the INSERT and to the `ON CONFLICT DO UPDATE` SET
list (it is a cached upstream value, exactly like `name` and `tree_numbers`):

```python
        "(condition_uuid, source, source_code, name, record_kind, tree_numbers, "
        " scr_class, first_seen_ingest) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (condition_uuid) DO UPDATE SET "
        "  name = EXCLUDED.name, record_kind = EXCLUDED.record_kind, "
        "  tree_numbers = EXCLUDED.tree_numbers, scr_class = EXCLUDED.scr_class "
```

- [ ] **Step 4: Widen the `reason` CHECK — carefully**

Append to `db/019`:

```sql
-- ============================================================================
-- 7. A FOURTH unmatched-ingredient bucket
-- ============================================================================
-- db/018's invariant is EXACTLY ONE WRITER PER (source, reason). One orchestrator
-- owning two buckets is fine; two writers sharing one bucket is what #39 was.
--
-- THE GUARD BELOW MUST NOT MATCH ON '%indication%': 'contraindication' CONTAINS
-- 'indication', so that pattern is satisfied by the EXISTING constraint, the widening
-- silently does not happen, and the first reason = 'indication' write fails at ingest
-- time. Matching on the quoted literal cannot match 'contraindication'::text.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'ingest_unmatched_ingredient_reason'
                   AND    conrelid = 'drugref.ingest_unmatched_ingredient'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%''indication''::text%') THEN
        ALTER TABLE drugref.ingest_unmatched_ingredient
            DROP CONSTRAINT IF EXISTS ingest_unmatched_ingredient_reason;
        ALTER TABLE drugref.ingest_unmatched_ingredient
            ADD CONSTRAINT ingest_unmatched_ingredient_reason
            CHECK (reason IN ('classification', 'contraindication', 'indication'));
    END IF;
END $$;
```

Add `INDICATION = "indication"` to `classes.py` beside `CLASSIFICATION` and
`CONTRAINDICATION`, and to `REASONS`. **Verify the guard actually fired:**

```bash
psql 'host=localhost port=5532 dbname=drugref_test user=postgres' -c \
  "SELECT pg_get_constraintdef(oid) FROM pg_constraint
   WHERE conname = 'ingest_unmatched_ingredient_reason'"
```

Expected output contains all three values.

- [ ] **Step 5: Update the orchestrator tests**

`tests/test_mesh_ci_run.py` imports `mesh_ci_run` and asserts on `MeshCiSummary`'s flat
fields. Update the import and re-nest the assertions (`summary.contraindications.condition_rows`
and so on). **Every asserted number stays the same.**

- [ ] **Step 6: Run the whole suite**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check .
```

Expected: all pass, with the same counts as before the refactor.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(ingest): one orchestrator owns the MeSH-keyed condition registry (5b.2)"
```

---

## Task 8: Ingest the indications

**Files:**
- Create: `src/drugref/ingest/mesh_ind_relations.py`
- Modify: `src/drugref/ingest/mesh_rel_run.py`, `tests/test_mesh_ci_run.py`

**Interfaces:**
- Consumes: Task 1's `parsed.mesh_indications`, Task 4's writer, Task 7's orchestrator.
- Produces: `mesh_ind_relations.write_indications(conn, assertions, records, uuid_by_code, rxcui_index, run_id) -> IndicationRelations` with fields
  `indication_rows: int`, `induced_rows: int`, `unmatched_rxcuis: set[str]`,
  `chemical_object_assertions: int`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mesh_ci_run.py` (or a new `tests/test_mesh_ind_run.py` if that module
passes 500 lines):

```python
def test_one_run_ingests_both_halves(mesh_relations_run):
    """One ingest_run, one registry, both relations -- the shape #39 forced."""
    summary = mesh_relations_run
    assert summary.contraindications.condition_rows > 0
    assert summary.indications.indication_rows > 0


def test_indications_and_contraindications_do_not_mix(conn, mesh_relations_run):
    overlap = conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication i "
        "JOIN drugref.moiety_condition_contraindication c "
        "  ON  c.subject_moiety_uuid = i.subject_moiety_uuid "
        "  AND c.object_condition_uuid = i.object_condition_uuid "
        "  AND c.relationship = i.relationship").fetchone()[0]
    assert overlap == 0


def test_induced_states_land_in_their_own_table(conn, mesh_relations_run):
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication "
        "WHERE relationship = 'induces'").fetchone()[0] == 0


def test_a_rerun_changes_nothing(conn, mesh_relations_run, run_again):
    """Rebuildable projection: re-ingesting the same release is idempotent."""
    before = _relation_counts(conn)
    run_again()
    assert _relation_counts(conn) == before


def test_unmatched_indication_subjects_are_persisted_under_their_own_reason(
        conn, mesh_relations_run):
    """A fourth bucket, never a shared one (db/018's one-writer-per-(source, reason))."""
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_unmatched_ingredient "
        "WHERE reason = 'indication'").fetchone()[0] > 0


def test_a_later_contraindication_clear_leaves_indication_rows_standing(
        conn, mesh_relations_run):
    """The #39 defect, asserted in this slice's terms: clearing one bucket must not
    take the other's rows with it."""
    from drugref import classes
    before = conn.execute(
        "SELECT count(*) FROM drugref.ingest_unmatched_ingredient "
        "WHERE reason = 'indication'").fetchone()[0]
    classes.clear_source_unmatched_ingredients(conn, "MED-RT",
                                              classes.CONTRAINDICATION)
    assert conn.execute(
        "SELECT count(*) FROM drugref.ingest_unmatched_ingredient "
        "WHERE reason = 'indication'").fetchone()[0] == before
```

- [ ] **Step 2: Run them and watch them fail**

Expected: `AttributeError: 'MeshRelSummary' object has no attribute 'indications'`.

- [ ] **Step 3: Write the indication pass**

`mesh_ind_relations.write_indications` mirrors `write_contraindications`, and is simpler
because there is no object-side bridge — an indication's object is always a condition:

```python
    for a in assertions:
        record = records.get(a.mesh_code)
        if record is None:
            continue                                # counted by the caller
        object_uuid = uuid_by_code.get(record.record_ui)
        if object_uuid is None:
            continue                                # not a registered condition
        if any(t.startswith("D") for t in record.tree_numbers):
            # 17 assertions name a MeSH CHEMICAL rather than a patient state (LDL
            # Cholesterol, Analgesics, Prostate-Specific Antigen). Ingested anyway --
            # some are defensible treatment targets, condition.tree_numbers lets a
            # consumer scope, and 5b already registered 18 such CI_with objects -- but
            # COUNTED, because 0.09% of may_treat being a category error upstream is a
            # fact an operator should see rather than discover.
            out.chemical_object_assertions += 1
        subjects = rxcui_index.get(a.rxcui, ())
        if not subjects:
            out.unmatched_rxcuis.add(a.rxcui)       # counted, never dropped
            continue
        for subject in subjects:
            if a.relationship == medrt.INDUCES_RELATIONSHIP:
                if indications.add_induced_condition(conn, subject, object_uuid,
                                                     SOURCE, run_id):
                    out.induced_rows += 1
            elif indications.add_condition_indication(conn, subject, object_uuid,
                                                      a.relationship, SOURCE, run_id):
                out.indication_rows += 1
```

- [ ] **Step 4: Call it from the orchestrator**

In `mesh_rel_run._ingest`, after the contraindication pass:
- call `indications.clear_source_indications(conn, SOURCE)` **with the other clears** in
  step 3, not here — a clear that happens after some rows are written deletes them;
- run the pass;
- persist the unmatched subjects under `classes.INDICATION`;
- fold the tallies into `MeshRelSummary`, and add `scr_class_counts` to the registry
  tally (`Counter(r.scr_class for r in closure.values() if r.scr_class)` as a sorted
  tuple of pairs);
- log a warning naming `gap_unmatched_ingredient` when there are unmatched subjects, in
  the style of the existing warnings.

**`questions.register_from_gaps` still runs LAST**, after both passes — it now derives a
seventh kind that reads tables this run rewrote.

- [ ] **Step 5: Run the suite**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check .
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(ingest): ingest MED-RT's MeSH-keyed indications (5b.2)"
```

---

## Task 9: Verify against the real releases, then document

**This is not optional and it is not a formality.** Slice 5b's spec was wrong in five
places and only the end-to-end run found it; this slice has already had one prediction
corrected by measurement (spec §3.6).

- [ ] **Step 1: Build a scratch database and run the whole chain**

```bash
createdb -h localhost -p 5532 -U postgres drugref_5b2_verify
```

Then run, in order: UNII → MED-RT → MeSH PA → the new relations run, against
`downloads/`. Write the driver as a throwaway script in the scratchpad, not in the repo
(there is still no CLI — that is [#16](https://github.com/cairn-ehr/drugref/issues/16)).

- [ ] **Step 2: Check every figure against the spec's §10 table**

| figure | expectation |
|---|---|
| `moiety_condition_contraindication` | **9,471** — must not move |
| `moiety_contraindication` | **1,442** — must not move |
| `gap_unresolved_ci_object` | **103** rows / **405** rules — must not move |
| `ddi_candidate_pair` | **21,664** — must not move |
| `condition` | **5,963** |
| `condition_parent` | **8,507** |
| `condition_subtree` over `CI_with` roots | **12,415** |
| `condition_contraindication_expanded` | **≈192,500** (+0.39%) |
| `moiety_condition_indication` | ≤ **18,125**, measured |
| `moiety_induced_condition` | ≤ **170**, measured |
| `gap_condition_without_indication` | **66** |
| `condition.scr_class` distribution | 29 × `'3'`, 5 × `'1'` |

- [ ] **Step 3: Check the function against the view on the real data**

For **every** registry condition, assert
`count(indications_for_condition(c)) = direct + generalised` from
`condition_indication_reach`. This is `#45`'s check ("200 conditions, 4,935 rows, zero
difference in either direction") applied to this slice's pair. Report the number of
conditions checked and the number of disagreements — which must be zero.

- [ ] **Step 4: Record what was measured**

Any figure that disagrees with the spec is **the spec being wrong**, as it was five times
in 5b. Correct it in a **docs-site living decision record**, never by editing the merged
spec. Write `docs-site/docs/decisions/indications-do-not-expand.md` covering the §3.2
argument with its measured numbers — it is a decision that currently stands, which is
exactly what that section is for. Add it to `mkdocs.yml`'s nav and check
`mkdocs build --strict`.

- [ ] **Step 5: Update HANDOVER.md and ROADMAP.md**

Per nextsession rule 9: concise, under 500 lines, focused on what remains. ROADMAP's
slice 5b.2 section becomes ✅ DONE with the measured table. HANDOVER gains the traps a
future change can still break — the generalisation direction, the two-table split, the
scoped gap view, and §3.6's registry-widening effect on 5b's expanded figures.

- [ ] **Step 6: Final check and PR**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check . && mkdocs build --strict
git push -u origin feat/slice-5b2-mesh-indications
gh pr create --base main --title "Slice 5b.2: MeSH-keyed indications" --body "..."
```

The PR body must link the spec, carry the measured table, and state plainly which slice-5b
figures moved and why (§3.6) — a moved figure explained in the PR is a finding; the same
figure discovered later by a reviewer is a defect.

---

## Self-Review

**Spec coverage.** §1 licence → Task 9 (no `NOTICE` change to make). §3.1 predicates →
Task 1. §3.4 D-tree counting → Task 8 step 3. §3.5 `SCRClass` → Task 2. §3.6 registry
widening → Task 9 step 2. §5.1 two relations → Task 4. §5.2 vocabulary → Task 4. §5.3
reach view → Task 5. §5.4 function, no expanded view → Task 5. §5.5 `scr_class` column →
Tasks 4 and 7. §5.6 gap kind → Task 6. §6.1 one orchestrator → Task 7. §6.2 parser →
Task 1. §6.3 the 193 → Task 1. §7 worklist numbers → Tasks 7–8. §9 testing → every task.
§10 verification → Task 9.

**Two gaps found and closed while reviewing:** the fixture work has to precede the
orchestrator tests (now Task 3, not a step inside Task 8), and the `reason` CHECK guard
needed its trap called out (Task 6 preamble, implemented in Task 7 step 4).

**Type consistency.** `add_induced_condition` takes no `relationship` in its definition
(Task 4) and is called without one (Task 8). `write_indications` returns
`IndicationRelations` with the four fields Task 8's summary folding reads.
`condition_indication_reach`'s columns are `direct_indication_rules` /
`generalised_indication_rules` in the DDL (Task 5), the tests (Task 5) and the gap view
(Task 6).

**Deliberately left to the implementer:** the exact line-by-line split of
`mesh_rel_run.py` (Task 7), since it is a mechanical move whose only hard constraint —
carry the docstrings, change no number — is stated; and the `_register_condition` test
helper's body (Task 6).

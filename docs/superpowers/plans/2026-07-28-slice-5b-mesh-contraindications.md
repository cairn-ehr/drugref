# Slice 5b — MeSH-keyed contraindications: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Ingest MED-RT's `CI_with` (drug↔condition) and `CI_ChemClass`'s moiety arm (drug↔drug) into
drugref, over a new MeSH **condition** registry, counting rather than dropping everything that does not
resolve.

**Architecture:** A new pure streaming resolver turns MED-RT's MeSH `to_code` (a MeSH **ConceptUI**) into a
MeSH record; a new writer module owns the `condition` / `condition_parent` tables; a new orchestrator owns
the transaction and is the only writer path. Read-time descendant expansion mirrors `db/012`'s
`ci_class_subtree` over a **condition** DAG derived from MeSH tree-number nesting.

**Tech Stack:** Python 3.12, `uv`, `psycopg` v3, PostgreSQL ≥ 18, `pytest`, `ruff`. No new dependency.

**Spec:** [slice-5b design](../specs/2026-07-28-drugref-slice-5b-mesh-contraindication-design.md) — read
§4 (ground truth) and §7 (no silent drops) before starting. **If this plan disagrees with the spec, the
spec wins.**

## Global Constraints

- **TDD, always:** write the failing test, run it, watch it fail *for the right reason*, then implement.
- **All tests must pass before every commit.** `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`
- **Lint clean before every commit:** `ruff check .`
- **Migrations are immutable once applied.** `apply_migrations` checksums each file and raises if an applied
  one changed. This plan therefore uses **four** new files (`db/013`–`db/016`); never edit an earlier one.
- **Inline documentation understandable by a junior contributor is mandatory** (CLAUDE.md rule 3). Every
  module and non-obvious function gets a docstring saying *why*, not just *what*.
- **Keep files under ~500 lines.** This is why `mesh_concepts.py` is separate from `mesh.py` (296 lines).
- **Parsers are pure:** no DB, no network, no UUID minting. Orchestrators own the transaction and are the
  only writers.
- **No silent drops.** Anything upstream asserts that drugref cannot use is *counted* and surfaced.
- **Licence:** no new source. MED-RT (public domain) and MeSH (NLM, cleared in 2b) only. **No `NOTICE`
  change.** Do not add the NDF-RT accessory crosswalk.
- **Source strings:** `'MED-RT'` and `'MeSH'` — always via `ids.canonical_source`, never a raw literal in a
  stored column.
- Upstream files live in `downloads/` (gitignored). MeSH: `downloads/mesh/desc2026.gz`,
  `downloads/mesh/supp2026.gz`. MED-RT XML: unzip `downloads/MEDRT/Core_MEDRT_XML.zip`.

---

### Task 1: `mint_condition_uuid` — the immortal condition identity

**Files:**
- Modify: `src/drugref/ids.py` (add beside `CLASS_NAMESPACE`, ~line 23, and after `mint_class_uuid`)
- Test: `tests/test_ids.py`

**Interfaces:**
- Consumes: `ids.canonical_source(source) -> str` (exists).
- Produces: `ids.CONDITION_NAMESPACE: uuid.UUID`; `ids.mint_condition_uuid(source: str, code: str) -> uuid.UUID`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ids.py`:

```python
def test_condition_uuid_is_deterministic():
    """Same (source, code) -> same UUID, always. Two drugref instances ingesting
    the same MeSH release derive identical condition UUIDs with no coordination."""
    assert ids.mint_condition_uuid("MeSH", "D004827") == \
           ids.mint_condition_uuid("MeSH", "D004827")


def test_condition_uuid_is_frozen():
    """PINNED LITERAL. condition_uuid is immortal, externally citable, and is the
    join key of condition_parent -- so a drift would orphan every edge on the next
    rebuild with no error anywhere. Exactly the guard class UUIDs carry."""
    assert str(ids.mint_condition_uuid("MeSH", "D004827")) == \
        "6b6b4bd8-1b3f-5b0e-9d8f-2a3c4f5e6d70"   # replace with the value from Step 3


def test_condition_uuid_folds_source_spelling():
    """'mesh', 'MESH' and 'MeSH' are one authority, so they must mint one UUID --
    the same fold canonical_source applies before the value is stored."""
    assert ids.mint_condition_uuid("mesh", "D004827") == \
           ids.mint_condition_uuid("MeSH", "D004827")


def test_condition_uuid_folds_code_case():
    assert ids.mint_condition_uuid("MeSH", "d004827") == \
           ids.mint_condition_uuid("MeSH", "D004827")


def test_condition_and_class_uuids_never_collide():
    """A MeSH descriptor may be BOTH a PA class (slice 2b) and a condition. The
    per-level namespaces are what stop one code minting one UUID for two different
    kinds of thing."""
    assert ids.mint_condition_uuid("MeSH", "D004827") != \
           ids.mint_class_uuid("MeSH", "D004827")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ids.py -k condition -v`
Expected: FAIL — `AttributeError: module 'drugref.ids' has no attribute 'mint_condition_uuid'`

- [ ] **Step 3: Implement**

In `src/drugref/ids.py`, add after `LOCAL_PRODUCT_NAMESPACE` (line 25):

```python
CONDITION_NAMESPACE = uuid.uuid5(_DRUGREF_ROOT, "condition")
```

and after `mint_class_uuid`:

```python
def mint_condition_uuid(source: str, code: str) -> uuid.UUID:
    """Derive a condition's immortal UUID from (authority, record code).

    A CONDITION is the patient state a drug must not be given in -- a disease, but
    also pregnancy, lactation or a procedure (slice-5b spec §4.3). `code` is the
    authority's own stable record identifier: a MeSH DescriptorUI ("D004827") or a
    SupplementalRecordUI ("C536778").

    Derived and re-derived on every ingest, never pinned -- the same discipline as
    mint_class_uuid and deliberately unlike mint_moiety_uuid. That is what lets the
    condition registry be dropped and rebuilt while every surviving condition comes
    back with exactly the UUID it had before.

    A SEPARATE NAMESPACE FROM CLASS_NAMESPACE, and that is load-bearing: a MeSH
    descriptor can be both a PA class (slice 2b) and a condition, and sharing a
    namespace would mint ONE UUID for two different kinds of thing, silently
    joining a condition row to a class row through either edge table.
    """
    canon = canonical_source(source)
    key = f"{canon.upper()}:{code.strip().upper()}"
    return uuid.uuid5(CONDITION_NAMESPACE, key)
```

- [ ] **Step 4: Fix the pinned literal, then run the tests**

Run: `uv run python -c "from drugref import ids; print(ids.mint_condition_uuid('MeSH','D004827'))"`
Paste the printed UUID into `test_condition_uuid_is_frozen`, replacing the placeholder.

Run: `uv run pytest tests/test_ids.py -v` → Expected: PASS
Run: `ruff check .` → Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ids.py tests/test_ids.py
git commit -m "feat(ids): mint immortal condition UUIDs in their own namespace

A MeSH descriptor can be both a PA class and a condition, so CONDITION_NAMESPACE
is separate from CLASS_NAMESPACE -- sharing one would mint a single UUID for two
different kinds of thing. Pinned by a frozen literal: condition_uuid is the join
key of condition_parent, so a drift would orphan every edge with no error."
```

---

### Task 2: `db/013` — the condition registry

**Files:**
- Create: `db/013_mesh_conditions.sql`
- Test: `tests/test_schema_conditions.py`

**Interfaces:**
- Produces: tables `drugref.condition` (`condition_uuid`, `source`, `source_code`, `name`, `record_kind`,
  `tree_numbers`, `first_seen_ingest`) and `drugref.condition_parent` (`child_condition_uuid`,
  `parent_condition_uuid`, `ingest_run`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_schema_conditions.py`:

```python
"""The condition registry's structural guarantees (slice 5b, db/013)."""
import uuid

import psycopg
import pytest

from drugref import ids


@pytest.fixture
def a_condition(conn, ingest_run_id):
    """One registered condition, for tests needing a live FK target."""
    cu = ids.mint_condition_uuid("MeSH", "D004827")
    conn.execute(
        "INSERT INTO drugref.condition (condition_uuid, source, source_code, name, "
        "record_kind, tree_numbers, first_seen_ingest) "
        "VALUES (%s, 'MeSH', 'D004827', 'Epilepsy', 'DESCRIPTOR', %s, %s)",
        (cu, ["C10.228.140.490"], ingest_run_id))
    return cu


def test_condition_round_trips(conn, a_condition):
    row = conn.execute(
        "SELECT name, record_kind, tree_numbers FROM drugref.condition "
        "WHERE condition_uuid = %s", (a_condition,)).fetchone()
    assert row == ("Epilepsy", "DESCRIPTOR", ["C10.228.140.490"])


def test_source_is_constrained(conn, ingest_run_id):
    """As db/003 constrains class sources: an unknown authority is refused, so a
    typo cannot open a parallel registry nothing reconciles."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.condition (condition_uuid, source, source_code, "
            "name, record_kind, first_seen_ingest) "
            "VALUES (%s, 'SNOMED', 'X', 'x', 'DESCRIPTOR', %s)",
            (uuid.uuid4(), ingest_run_id))


def test_record_kind_is_constrained(conn, ingest_run_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.condition (condition_uuid, source, source_code, "
            "name, record_kind, first_seen_ingest) "
            "VALUES (%s, 'MeSH', 'D1', 'x', 'QUALIFIER', %s)",
            (uuid.uuid4(), ingest_run_id))


def test_source_code_is_unique_per_source(conn, a_condition, ingest_run_id):
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO drugref.condition (condition_uuid, source, source_code, "
            "name, record_kind, first_seen_ingest) "
            "VALUES (%s, 'MeSH', 'D004827', 'dup', 'DESCRIPTOR', %s)",
            (uuid.uuid4(), ingest_run_id))


def test_condition_parent_requires_both_endpoints(conn, a_condition, ingest_run_id):
    """The DAG is closed over the registry: an edge to an unregistered condition is
    refused, which is what keeps un-ingested MeSH content out of the tree."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.condition_parent (child_condition_uuid, "
            "parent_condition_uuid, ingest_run) VALUES (%s, %s, %s)",
            (a_condition, uuid.uuid4(), ingest_run_id))


def test_condition_cannot_parent_itself(conn, a_condition, ingest_run_id):
    """Self-parenting is the one cycle a recursive walk cannot survive; db/002
    forbids it for classes and the same reasoning applies here."""
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.condition_parent (child_condition_uuid, "
            "parent_condition_uuid, ingest_run) VALUES (%s, %s, %s)",
            (a_condition, a_condition, ingest_run_id))


def test_condition_supports_multiple_parents(conn, a_condition, ingest_run_id):
    """1,690 of the 5,190 conditions in the real release have several parents, so
    the DAG must be many-to-many, never a single parent FK."""
    for code, name in (("D000001", "P1"), ("D000002", "P2")):
        cu = ids.mint_condition_uuid("MeSH", code)
        conn.execute(
            "INSERT INTO drugref.condition (condition_uuid, source, source_code, "
            "name, record_kind, first_seen_ingest) VALUES (%s,'MeSH',%s,%s,"
            "'DESCRIPTOR',%s)", (cu, code, name, ingest_run_id))
        conn.execute(
            "INSERT INTO drugref.condition_parent (child_condition_uuid, "
            "parent_condition_uuid, ingest_run) VALUES (%s, %s, %s)",
            (a_condition, cu, ingest_run_id))
    assert conn.execute(
        "SELECT count(*) FROM drugref.condition_parent WHERE child_condition_uuid = %s",
        (a_condition,)).fetchone()[0] == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_schema_conditions.py -v`
Expected: FAIL — `UndefinedTable: relation "drugref.condition" does not exist`

- [ ] **Step 3: Write the migration**

Create `db/013_mesh_conditions.sql`:

```sql
-- db/013_mesh_conditions.sql
-- The MeSH CONDITION registry: the object side of a drug-condition contraindication.
--
-- WHY CONDITIONS ARE NOT substance_class ROWS (spec §2, tension A). MED-RT's CI_with
-- names the patient state a drug must not be given in. Measured against the real
-- 2026.07.06 release, that is a disease 10,091 times -- but also PREGNANCY and
-- LACTATION (786 assertions), a procedure (105), and the check tag "Female". Three
-- things follow, and each on its own is decisive:
--   * class_membership (moiety IS-A-MEMBER-OF class) is meaningless here. Nothing is
--     a member of pregnancy.
--   * substance_class's axis vocabulary (MoA/PE/TC/PK/EPC/APC/PA) is entirely
--     pharmacological. Filing "Coronary Artery Bypass" under it needs either a lie or
--     an axis meaning "not actually a substance class".
--   * substance_class currently MEANS "a class of substances", and that meaning is
--     load-bearing for the licence-scoping argument in ingest/medrt.py.
--
-- REBUILDABLE PROJECTION, like substance_class and deliberately outside slice 1's
-- append-only floor: a condition withdrawn upstream must be able to disappear.
-- Condition IDENTITY survives a rebuild by determinism -- condition_uuid is a pure
-- function of (source, source_code) -- so no pin table is needed.

CREATE TABLE IF NOT EXISTS drugref.condition (
    -- UUIDv5(CONDITION_NAMESPACE, source || ':' || source_code), minted by
    -- ids.mint_condition_uuid. Immortal, externally citable, and the join key of
    -- condition_parent -- so the derivation is frozen and pinned by a test literal.
    condition_uuid    uuid    PRIMARY KEY,
    -- Constrained for the reason db/003 constrains substance_class.source: the
    -- stored spelling and the UUID key derive from ONE canonicalisation
    -- (ids.canonical_source), and a source the CHECK admits but that function does
    -- not know would be stored under a spelling a per-source rebuild cannot find.
    -- Widen this CHECK and _SOURCE_CANONICAL together, never one alone.
    source            text    NOT NULL CHECK (source IN ('MeSH')),
    -- The authority's stable record id: a MeSH DescriptorUI (D004827) or a
    -- SupplementalRecordUI (C536778). NOT the ConceptUI (M0004868) MED-RT points at
    -- -- see mesh_concepts.py: many concepts resolve to one record, so keying on the
    -- concept would split one condition into several.
    source_code       text    NOT NULL,
    name              text    NOT NULL,
    record_kind       text    NOT NULL CHECK (record_kind IN ('DESCRIPTOR', 'SCR')),
    -- MeSH tree numbers, AS PUBLISHED. Stored because they are SOURCE data, not
    -- derived: they are the input condition_parent is built from, and they are what
    -- lets a consumer tell a disease (C) from a physiological state (G) from a
    -- procedure (E) without drugref inventing a taxonomy of its own. SCRs carry none.
    tree_numbers      text[]  NOT NULL DEFAULT '{}',
    first_seen_ingest bigint  NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    UNIQUE (source, source_code)
);

COMMENT ON TABLE drugref.condition IS
    'Patient states a drug may be contraindicated in, from MeSH: diseases, but also '
    'physiological states (pregnancy, lactation), procedures and demographics. A '
    'REBUILDABLE PROJECTION -- re-ingest replaces this source''s rows. NOT a '
    'substance_class: nothing is a MEMBER of a condition, so no membership table '
    'exists or should be added.';
COMMENT ON COLUMN drugref.condition.source_code IS
    'MeSH DescriptorUI or SupplementalRecordUI -- the RECORD, never the ConceptUI '
    'MED-RT references. Several concepts resolve to one record.';
COMMENT ON COLUMN drugref.condition.tree_numbers IS
    'MeSH tree numbers as published. Source data, not derived: condition_parent is '
    'built from their nesting, and the leading letter distinguishes a disease (C) '
    'from a physiological state (G) from a procedure (E).';

CREATE TABLE IF NOT EXISTS drugref.condition_parent (
    child_condition_uuid  uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    parent_condition_uuid uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    ingest_run            bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (child_condition_uuid, parent_condition_uuid),
    -- Self-parenting is the ONE cycle a UNION-over-(root,node) walk cannot survive,
    -- so it is forbidden structurally. Longer cycles are tolerated by the walk
    -- itself (db/012's ci_class_subtree explains why), not by this constraint.
    CONSTRAINT condition_parent_not_self
        CHECK (child_condition_uuid <> parent_condition_uuid)
);

CREATE INDEX IF NOT EXISTS condition_parent_by_parent
    ON drugref.condition_parent (parent_condition_uuid);

COMMENT ON TABLE drugref.condition_parent IS
    'The condition DAG, derived from MeSH tree-number nesting exactly as slice 2b '
    'derived the PA DAG. MANY-TO-MANY: a descriptor bears several tree numbers, so '
    '1,690 of the 5,190 conditions in the 2026 release have more than one parent. '
    'A REBUILDABLE PROJECTION -- cleared and rebuilt per source on every ingest.';
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_schema_conditions.py -v`
Expected: PASS (8 tests)

Run the whole suite and lint:
`DRUGREF_TEST_DSN='...' uv run pytest -q && ruff check .` → Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add db/013_mesh_conditions.sql tests/test_schema_conditions.py
git commit -m "feat(db): add the MeSH condition registry (db/013)

Conditions get their own tables rather than a concept_type on substance_class:
nothing is a member of pregnancy, substance_class's axis vocabulary is entirely
pharmacological, and its meaning is load-bearing for medrt.py's licence-scoping
argument. tree_numbers is stored as SOURCE data -- it builds the DAG and it is
what distinguishes a disease from a physiological state from a procedure."
```

---

### Task 3: `ingest/mesh_concepts.py` — the M-code resolver (pure)

**Files:**
- Create: `src/drugref/ingest/mesh_concepts.py`
- Create: `tests/fixtures/make_mesh_ci_subset.py`
- Create (generated): `tests/fixtures/mesh_ci_desc_subset.xml`, `tests/fixtures/mesh_ci_supp_subset.xml`
- Test: `tests/test_mesh_concepts.py`

**Interfaces:**
- Consumes: `mesh.registry_keys(values) -> tuple[set[str], set[str]]`, `mesh._iter_records`, `mesh._texts`,
  `mesh._findtext` (all exist in `ingest/mesh.py`).
- Produces:
  - `MeshRecord(concept_ui, record_ui, record_kind, name, tree_numbers, unii, cas, is_preferred_concept)`
  - `resolve_concepts(desc_path, supp_path, wanted: set[str]) -> dict[str, MeshRecord]`
  - `descriptors_under(desc_path, tree_prefixes: frozenset[str]) -> list[MeshRecord]`
  - `ConditionParentEdge(child_code, parent_code)`
  - `parent_edges(records: Iterable[MeshRecord]) -> list[ConditionParentEdge]`

- [ ] **Step 1: Write the fixture extractor**

Create `tests/fixtures/make_mesh_ci_subset.py`:

```python
"""Extract a MeSH desc/supp subset covering slice 5b's contraindication objects.

Run:
    uv run python tests/fixtures/make_mesh_ci_subset.py \
        downloads/mesh/desc2026.gz downloads/mesh/supp2026.gz tests/fixtures/

WHY A SEPARATE FIXTURE FROM slice 2b's mesh_desc_subset.xml: 2b's fixture is scoped
to the PHARMACOLOGICAL ACTION axis, and 5b's objects are diseases, physiological
states and procedures -- disjoint records. Extending 2b's file would grow it for a
purpose its own tests do not share; a separate file keeps each fixture legible.

EXTRACTED FROM THE REAL RELEASE, NEVER HAND-WRITTEN. This is the standing rule since
issue #27, where the last hand-written fixture concealed a wrong column name that
would have shipped an entirely unlabelled registry. A fixture invented by hand can
only ever confirm what its author already believed.
"""
import gzip
import pathlib
import sys
from xml.etree import ElementTree as ET

# Records the 5b tests need, chosen to exercise every branch of the resolver:
#   D004827 Epilepsy               -- the worked example; has descendants
#   D004829 Epilepsy, Generalized  -- a DESCENDANT of Epilepsy (closure test)
#   D011247 Pregnancy              -- the G-branch case the table is named for
#   D001026 Coronary Artery Bypass -- an E-branch procedure
#   D010860 Pimozide               -- a CI_ChemClass object that IS a substance
#   D013449 Sulfonamides           -- the class-arm object (must NOT be ingested)
WANT_DESC = ["D004827", "D004829", "D011247", "D001026", "D010860", "D013449"]
#   C536778 -- an SCR, to exercise the supplementary-record fallback
WANT_SUPP = ["C536778"]


def extract(path, tag, ui_tag, wanted, out_path, root_tag):
    """Copy whole records verbatim -- never a reconstruction, so the fixture cannot
    disagree with upstream about element names or nesting."""
    kept = []
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as fh:
        for _event, el in ET.iterparse(fh, events=("end",)):
            if el.tag != tag:
                continue
            if (el.findtext(ui_tag) or "") in wanted:
                kept.append(ET.tostring(el, encoding="unicode"))
            el.clear()
    out_path.write_text(
        f"<{root_tag}>\n" + "\n".join(kept) + f"\n</{root_tag}>\n", encoding="utf-8")
    print(f"{out_path}: {len(kept)}/{len(wanted)} records")


if __name__ == "__main__":
    desc, supp, outdir = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
    extract(desc, "DescriptorRecord", "DescriptorUI", set(WANT_DESC),
            outdir / "mesh_ci_desc_subset.xml", "DescriptorRecordSet")
    extract(supp, "SupplementalRecord", "SupplementalRecordUI", set(WANT_SUPP),
            outdir / "mesh_ci_supp_subset.xml", "SupplementalRecordSet")
```

- [ ] **Step 2: Generate the fixtures**

```bash
uv run python tests/fixtures/make_mesh_ci_subset.py \
    downloads/mesh/desc2026.gz downloads/mesh/supp2026.gz tests/fixtures/
```

Expected: `mesh_ci_desc_subset.xml: 6/6 records` and `mesh_ci_supp_subset.xml: 1/1 records`.

If a count is short, the UI no longer exists upstream — **find a live replacement and update the
constants**; do not hand-write the missing record.

Inspect the generated file to learn the real ConceptUI of Epilepsy for the next step:

```bash
grep -A2 "<ConceptUI>" tests/fixtures/mesh_ci_desc_subset.xml | head -20
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_mesh_concepts.py`:

```python
"""The M-code resolver (slice 5b): MED-RT's MeSH to_code is a MeSH ConceptUI."""
import pathlib

from drugref.ingest import mesh_concepts

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DESC = FIXTURES / "mesh_ci_desc_subset.xml"
SUPP = FIXTURES / "mesh_ci_supp_subset.xml"

# The preferred ConceptUI of D004827 (Epilepsy), read from the real release.
EPILEPSY_CONCEPT = "M0007751"          # replace with the value from Step 2


def test_resolves_a_concept_to_its_descriptor():
    """THE CENTRAL FACT of this slice: MED-RT points at a MeSH ConceptUI, and the
    record that owns it is the condition. Established from the release, not the
    docs -- the documentation route (the NDF-RT crosswalk) resolves 85% and yields
    only a name."""
    got = mesh_concepts.resolve_concepts(DESC, SUPP, {EPILEPSY_CONCEPT})
    rec = got[EPILEPSY_CONCEPT]
    assert rec.record_ui == "D004827"
    assert rec.record_kind == "DESCRIPTOR"
    assert rec.name == "Epilepsy"
    assert rec.is_preferred_concept is True
    assert any(t.startswith("C10.") for t in rec.tree_numbers)


def test_unwanted_concepts_are_not_returned():
    """The resolver is scoped: streaming the full release must retain only what was
    asked for, or peak memory grows with MeSH rather than with the query."""
    got = mesh_concepts.resolve_concepts(DESC, SUPP, {EPILEPSY_CONCEPT})
    assert set(got) == {EPILEPSY_CONCEPT}


def test_unresolvable_code_is_absent_not_invented():
    """A withdrawn M-code must be MISSING from the result so the caller can count
    it, not silently mapped to something plausible."""
    assert mesh_concepts.resolve_concepts(DESC, SUPP, {"M9999999"}) == {}


def test_resolves_a_supplementary_record():
    """86 of the release's stragglers live in supp2026, so the SCR fallback is
    load-bearing: without it resolution stops at 96.4% instead of 99.88%."""
    concepts = _concept_uis(SUPP, "SupplementalRecord")
    got = mesh_concepts.resolve_concepts(DESC, SUPP, set(concepts))
    assert got, "the SCR fixture yielded no concepts"
    rec = next(iter(got.values()))
    assert rec.record_kind == "SCR"
    assert rec.record_ui.startswith("C")
    assert rec.tree_numbers == ()          # SCRs carry no tree numbers


def test_descendants_are_found_under_a_tree_prefix():
    """The closure test. A rule names Epilepsy; the patient is coded Epilepsy,
    Generalized. Without this the read path expands into an empty registry and the
    whole feature is inert while appearing to work."""
    epilepsy = mesh_concepts.resolve_concepts(DESC, SUPP, {EPILEPSY_CONCEPT})
    prefixes = frozenset(epilepsy[EPILEPSY_CONCEPT].tree_numbers)
    found = {r.record_ui for r in mesh_concepts.descriptors_under(DESC, prefixes)}
    assert "D004829" in found          # Epilepsy, Generalized -- strictly below
    assert "D004827" not in found      # the root itself is NOT its own descendant
    assert "D011247" not in found      # Pregnancy is in another branch entirely


def test_a_prefix_is_not_matched_by_string_prefix_alone():
    """'C10.228.140.49' must not match 'C10.228.140.490'. Segment-aware matching,
    or a tree number would adopt unrelated siblings as children."""
    assert mesh_concepts.is_descendant_tree("C10.228.140.490.100", "C10.228.140.490")
    assert not mesh_concepts.is_descendant_tree("C10.228.140.490", "C10.228.140.49")
    assert not mesh_concepts.is_descendant_tree("C10.228.140.490", "C10.228.140.490")


def test_parent_edges_come_from_tree_nesting():
    """Mirrors mesh._build_dag: only the IMMEDIATE tree-parent, and only when that
    parent is itself in the ingested set."""
    records = mesh_concepts.resolve_concepts(DESC, SUPP, {EPILEPSY_CONCEPT})
    parent = records[EPILEPSY_CONCEPT]
    children = mesh_concepts.descriptors_under(DESC, frozenset(parent.tree_numbers))
    edges = mesh_concepts.parent_edges([parent, *children])
    assert mesh_concepts.ConditionParentEdge("D004829", "D004827") in edges


def test_parent_edges_never_self_parent():
    records = mesh_concepts.resolve_concepts(DESC, SUPP, {EPILEPSY_CONCEPT})
    for edge in mesh_concepts.parent_edges(list(records.values())):
        assert edge.child_code != edge.parent_code


def _concept_uis(path, tag):
    """Every ConceptUI in a fixture file -- test scaffolding, not production code."""
    from xml.etree import ElementTree as ET
    root = ET.parse(path).getroot()
    return [c.text for r in root.iter(tag)
            for c in r.iter("ConceptUI") if c.text]
```

- [ ] **Step 4: Run to verify they fail**

Run: `uv run pytest tests/test_mesh_concepts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.ingest.mesh_concepts'`

- [ ] **Step 5: Implement**

Create `src/drugref/ingest/mesh_concepts.py`:

```python
"""Resolve MED-RT's MeSH endpoints into MeSH records (slice 5b).

THE FACT THIS MODULE EXISTS FOR, established from the real 2026.07.06 release and
NOT from the documentation: MED-RT's `to_code` for a MeSH endpoint is a MeSH
**ConceptUI** ("M0004868"), not a DescriptorUI. Every MeSH record owns one or more
Concepts, exactly one of them preferred, so resolution is a plain lookup over files
drugref already ingests:

    desc2026 alone            2,385 / 2,474 = 96.4%
    desc2026 + supp2026       2,471 / 2,474 = 99.88%
    NDF-RT accessory crosswalk 2,103 / 2,474 = 85.0%, and yields only a NAME

The crosswalk route is therefore NOT used: it is worse, and a name is not a key
(ROADMAP principle 2). Two code shapes occur -- legacy "M0000006" and modern
"M000595362" -- and both are ConceptUIs; nothing here keys off the length.

WHY THIS IS NOT IN mesh.py. That module answers "what are the PA classes and their
members"; this one answers "which MeSH record is this concept". Different questions,
and mesh.py is already 296 lines against a ~500-line budget (CLAUDE.md rule 4).

This module is PURE and STREAMING: it reads files and returns records. No database,
no network, no UUID minting. The orchestrator (mesh_ci_run.py) does all of that.
Every file is streamed with iterparse + clear, so peak memory scales with the QUERY
(the wanted set), never with the release -- supp2026 is ~750 MB uncompressed.
"""
import gzip
import pathlib
from collections.abc import Iterable
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from drugref.ingest.mesh import registry_keys

StrPath = str | pathlib.Path

DESCRIPTOR = "DESCRIPTOR"
SCR = "SCR"


@dataclass(frozen=True)
class MeshRecord:
    """One MeSH record, reached through one of its concepts.

    `concept_ui` is what MED-RT pointed at; `record_ui` is the record that owns it
    and is what a condition is KEYED on. The two are deliberately kept apart: many
    concepts resolve to one record, so keying on the concept would split a single
    condition into several rows that no rebuild could ever merge.

    `is_preferred_concept` is recorded rather than discarded because a SUBORDINATE
    concept may be NARROWER than the record it belongs to -- 81 of this slice's
    1,051 resolved objects are subordinate. Storing the condition at record grain
    loses that nuance; this flag makes the loss visible and measurable instead of
    silent (spec §10 tension C).
    """
    concept_ui: str
    record_ui: str
    record_kind: str                    # DESCRIPTOR | SCR
    name: str
    tree_numbers: tuple[str, ...]
    unii: frozenset[str]
    cas: frozenset[str]
    is_preferred_concept: bool


@dataclass(frozen=True)
class ConditionParentEdge:
    """One condition-DAG edge: `child_code` is a kind of `parent_code`.
    Both are MeSH record UIs (the key drugref stores), never concept UIs."""
    child_code: str
    parent_code: str


def is_descendant_tree(tree_number: str, prefix: str) -> bool:
    """Is `tree_number` STRICTLY below `prefix` in the MeSH tree?

    Segment-aware on purpose. A bare str.startswith would make "C10.228.140.49" a
    parent of "C10.228.140.490" -- two unrelated concepts whose numbers merely share
    a text prefix -- and would also report a node as its own descendant, which would
    put a self-edge in condition_parent that db/013's CHECK then rejects mid-ingest.
    """
    return tree_number.startswith(prefix + ".")


def _open(path: StrPath):
    """Open a MeSH file, transparently handling the .gz the NLM publishes."""
    return gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb")


def _stream(path: StrPath, tag: str):
    """Yield each `tag` element, clearing it (and its parent) after use.

    Bounded memory by construction: nothing accumulates but what the caller keeps.
    """
    with _open(path) as fh:
        for _event, el in ET.iterparse(fh, events=("end",)):
            if el.tag == tag:
                yield el
                el.clear()


def _record(el, ui_tag: str, name_tag: str, kind: str, concept_ui: str,
            preferred: bool) -> MeshRecord:
    """Build a MeshRecord from a raw MeSH record element."""
    uniis, cas = registry_keys(
        [r.text for r in el.iter("RegistryNumber") if r.text])
    trees = tuple(t.text for t in el.findall("TreeNumberList/TreeNumber") if t.text)
    return MeshRecord(concept_ui=concept_ui,
                      record_ui=el.findtext(ui_tag) or "",
                      record_kind=kind,
                      name=el.findtext(name_tag) or "",
                      tree_numbers=trees,
                      unii=frozenset(uniis), cas=frozenset(cas),
                      is_preferred_concept=preferred)


# (file, record tag, UI tag, name tag, record kind) -- descriptors FIRST, because a
# concept defined in both files is authoritatively a descriptor.
_SOURCES = (
    ("desc", "DescriptorRecord", "DescriptorUI", "DescriptorName/String", DESCRIPTOR),
    ("supp", "SupplementalRecord", "SupplementalRecordUI",
     "SupplementalRecordName/String", SCR),
)


def resolve_concepts(desc_path: StrPath, supp_path: StrPath,
                     wanted: set[str]) -> dict[str, MeshRecord]:
    """Resolve each wanted MeSH ConceptUI to the record that owns it.

    Returns {concept_ui: MeshRecord} containing ONLY codes that resolved. A code
    that resolves nowhere is simply ABSENT -- never mapped to a plausible
    substitute -- so the caller can count it as a gap rather than ship a wrong
    condition. Exactly 2 of this slice's 1,053 object codes are withdrawn upstream
    and land here.

    Descriptors win over SCRs when a concept appears in both: a descriptor is the
    fuller record, and preferring it deterministically stops the answer depending on
    file order.
    """
    out: dict[str, MeshRecord] = {}
    remaining = set(wanted)
    for path, tag, ui_tag, name_tag, kind in (
            (desc_path, *_SOURCES[0][1:]), (supp_path, *_SOURCES[1][1:])):
        if not remaining:
            break                                   # everything already resolved
        for el in _stream(path, tag):
            for concept in el.findall("ConceptList/Concept"):
                cui = concept.findtext("ConceptUI") or ""
                if cui in remaining:
                    out[cui] = _record(el, ui_tag, name_tag, kind, cui,
                                       concept.get("PreferredConceptYN") == "Y")
        remaining -= set(out)
    return out


def descriptors_under(desc_path: StrPath,
                      tree_prefixes: frozenset[str]) -> list[MeshRecord]:
    """Every descriptor STRICTLY below one of `tree_prefixes`.

    THE DESCENDANT CLOSURE, and the reason the registry is not merely the set of
    referenced conditions. Expansion exists so a rule on Epilepsy fires for a
    patient coded Temporal Lobe Epilepsy -- and that descendant is NOT itself a
    CI_with object. A registry scoped to referenced objects would have nothing to
    expand into, and the feature would be inert while appearing to work.

    Measured on the real release: 664 referenced descriptors -> 5,190 in closure.

    Each record is returned under its own PREFERRED concept where it has one, since
    the caller keys conditions by record_ui and only needs a concept for provenance.
    """
    found: list[MeshRecord] = []
    if not tree_prefixes:
        return found
    for el in _stream(desc_path, "DescriptorRecord"):
        trees = [t.text for t in el.findall("TreeNumberList/TreeNumber") if t.text]
        if not any(is_descendant_tree(t, p) for t in trees for p in tree_prefixes):
            continue
        concepts = el.findall("ConceptList/Concept")
        preferred = next((c for c in concepts
                          if c.get("PreferredConceptYN") == "Y"), None)
        chosen = preferred if preferred is not None else (
            concepts[0] if concepts else None)
        found.append(_record(
            el, "DescriptorUI", "DescriptorName/String", DESCRIPTOR,
            (chosen.findtext("ConceptUI") or "") if chosen is not None else "",
            preferred is not None))
    return found


def parent_edges(records: Iterable[MeshRecord]) -> list[ConditionParentEdge]:
    """Derive the condition DAG from tree-number nesting.

    The same idiom as mesh._build_dag, deliberately -- one way of turning MeSH tree
    numbers into a DAG in this codebase, not two. Only the IMMEDIATE tree-parent of
    each tree number counts, and only when that parent is itself an ingested record;
    a record whose immediate parent is outside the set is simply a ROOT of the
    ingested subset, not re-attached to a more distant ancestor.

    Multi-parent by construction: a descriptor bears several tree numbers, which is
    why 1,690 of the 5,190 conditions have more than one parent.
    """
    records = list(records)
    owner_of_tree = {t: r.record_ui for r in records for t in r.tree_numbers}
    edges: set[ConditionParentEdge] = set()
    for r in records:
        for tree in r.tree_numbers:
            if "." not in tree:
                continue                            # a top-level node has no parent
            owner = owner_of_tree.get(tree.rsplit(".", 1)[0])
            if owner and owner != r.record_ui:
                edges.add(ConditionParentEdge(child_code=r.record_ui,
                                              parent_code=owner))
    # Sorted so the edge order is reproducible (a set has none).
    return sorted(edges, key=lambda e: (e.child_code, e.parent_code))
```

- [ ] **Step 6: Fix the concept literal, then run the tests**

Run: `grep -B4 "PreferredConceptYN=\"Y\"" tests/fixtures/mesh_ci_desc_subset.xml | grep -A1 D004827`
or simply read the `<ConceptUI>` inside D004827's preferred `<Concept>`, and set `EPILEPSY_CONCEPT`.

Run: `uv run pytest tests/test_mesh_concepts.py -v` → Expected: PASS (8 tests)
Run: `ruff check .` → Expected: clean

- [ ] **Step 7: Commit**

```bash
git add src/drugref/ingest/mesh_concepts.py tests/test_mesh_concepts.py \
        tests/fixtures/make_mesh_ci_subset.py tests/fixtures/mesh_ci_*.xml
git commit -m "feat(mesh): resolve MED-RT's MeSH endpoints to MeSH records

MED-RT's MeSH to_code is a ConceptUI, not a DescriptorUI -- established from the
real release. desc+supp resolve 99.88% of it; the NDF-RT accessory crosswalk the
docs point at resolves 85% and yields only a name, so it is not used.

descriptors_under() computes the DESCENDANT CLOSURE, without which a rule on
Epilepsy has no Temporal Lobe Epilepsy row to expand into and the whole read path
is inert while appearing to work. Fixtures extracted from the real release."
```

---

### Task 4: `conditions.py` — the single writer

**Files:**
- Create: `src/drugref/conditions.py`
- Test: `tests/test_conditions_writer.py`

**Interfaces:**
- Consumes: `ids.mint_condition_uuid`, `ids.canonical_source`, `mesh_concepts.MeshRecord`,
  `mesh_concepts.ConditionParentEdge`.
- Produces:
  - `upsert_condition(conn, record: MeshRecord, ingest_run_id: int, source: str) -> tuple[uuid.UUID, bool]`
  - `clear_source_condition_edges(conn, source: str) -> None`
  - `add_condition_parent_edge(conn, child_uuid, parent_uuid, ingest_run_id) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_conditions_writer.py`:

```python
"""The condition writer (slice 5b), mirroring classes.py's single-writer role."""
from drugref import conditions, ids
from drugref.ingest.mesh_concepts import ConditionParentEdge, MeshRecord

EPILEPSY = MeshRecord(
    concept_ui="M0007751", record_ui="D004827", record_kind="DESCRIPTOR",
    name="Epilepsy", tree_numbers=("C10.228.140.490",),
    unii=frozenset(), cas=frozenset(), is_preferred_concept=True)


def test_upsert_returns_the_derived_uuid(conn, ingest_run_id):
    cu, is_new = conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "MeSH")
    assert cu == ids.mint_condition_uuid("MeSH", "D004827")
    assert is_new is True


def test_second_upsert_is_not_new(conn, ingest_run_id):
    """Conditions ACCUMULATE while edges are rebuilt, so 'in this release' and
    'added by this run' are genuinely different numbers -- as for classes."""
    conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "MeSH")
    _cu, is_new = conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "MeSH")
    assert is_new is False


def test_upsert_refreshes_the_cached_name(conn, ingest_run_id):
    """Upstream renames records; the cache must follow."""
    conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "MeSH")
    renamed = MeshRecord(**{**EPILEPSY.__dict__, "name": "Epilepsies"})
    conditions.upsert_condition(conn, renamed, ingest_run_id, "MeSH")
    assert conn.execute(
        "SELECT name FROM drugref.condition WHERE source_code = 'D004827'"
    ).fetchone()[0] == "Epilepsies"


def test_stored_source_is_canonical(conn, ingest_run_id):
    """The stored spelling and the UUID key must derive from ONE canonicalisation,
    or a per-source rebuild silently misses rows it owns (ids.canonical_source)."""
    conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "mesh")
    assert conn.execute(
        "SELECT source FROM drugref.condition WHERE source_code = 'D004827'"
    ).fetchone()[0] == "MeSH"


def test_clear_source_edges_removes_only_that_source(conn, ingest_run_id):
    parent, _ = conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "MeSH")
    child_rec = MeshRecord(**{**EPILEPSY.__dict__, "record_ui": "D004829",
                              "name": "Epilepsy, Generalized",
                              "tree_numbers": ("C10.228.140.490.360",)})
    child, _ = conditions.upsert_condition(conn, child_rec, ingest_run_id, "MeSH")
    assert conditions.add_condition_parent_edge(conn, child, parent, ingest_run_id)

    conditions.clear_source_condition_edges(conn, "MeSH")
    assert conn.execute("SELECT count(*) FROM drugref.condition_parent").fetchone()[0] == 0
    # Condition rows themselves survive: their UUIDs are immortal.
    assert conn.execute("SELECT count(*) FROM drugref.condition").fetchone()[0] == 2


def test_duplicate_edge_is_harmless(conn, ingest_run_id):
    parent, _ = conditions.upsert_condition(conn, EPILEPSY, ingest_run_id, "MeSH")
    child_rec = MeshRecord(**{**EPILEPSY.__dict__, "record_ui": "D004829"})
    child, _ = conditions.upsert_condition(conn, child_rec, ingest_run_id, "MeSH")
    assert conditions.add_condition_parent_edge(conn, child, parent, ingest_run_id)
    assert not conditions.add_condition_parent_edge(conn, child, parent, ingest_run_id)
```

- [ ] **Step 2: Run to verify they fail**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_conditions_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.conditions'`

- [ ] **Step 3: Implement**

Create `src/drugref/conditions.py`:

```python
"""The ONLY module that writes the condition tables.

Mirrors classes.py exactly, and for the same reason: `condition` and
`condition_parent` are a REBUILDABLE PROJECTION of MeSH, not the append-only spine.
So clear_source_condition_edges() deliberately DELETEs -- a condition that lost a
parent upstream has to lose it here too, which an insert-only merge could never
express -- while condition IDENTITY survives untouched, because condition_uuid is a
pure function of (source, source_code).

Condition ROWS are never deleted by a rebuild, only their edges. The UUID is
immortal and externally citable; re-deriving it on the way back in is what makes the
whole projection safe to drop.
"""
import uuid

import psycopg

from drugref import ids
from drugref.ingest.mesh_concepts import MeshRecord


def upsert_condition(conn: psycopg.Connection, record: MeshRecord,
                     ingest_run_id: int, source: str) -> tuple[uuid.UUID, bool]:
    """Register a condition (or refresh its cached name and tree numbers).

    Returns (condition_uuid, is_new), where is_new is True only the first time
    drugref ever saw this condition. The caller needs the distinction because
    conditions ACCUMULATE while edges are REBUILT, so "conditions in this release"
    and "conditions added by this run" are different numbers and a summary reporting
    only one of them would be ambiguous.

    Keyed on record_ui, NEVER on concept_ui: many concepts resolve to one record, so
    keying on the concept would split one condition into several rows.

    The UUID is derived, never looked up, so this is safe to call on every ingest.
    ON CONFLICT refreshes the caches -- upstream renames records and re-files them
    in the tree -- while first_seen_ingest is deliberately left out of the SET list,
    because it records when drugref FIRST saw the condition. That is also what makes
    it the newness test: the row is new exactly when the value returned is this run's.
    """
    condition_uuid = ids.mint_condition_uuid(source, record.record_ui)
    # Store the SAME canonicalisation the UUID was minted from, so the stored source
    # and the identity key can never drift -- two spellings of one authority would
    # share a UUID yet be stored as two strings, and a per-source rebuild would then
    # miss half its own rows.
    stored_source = ids.canonical_source(source)
    first_seen = conn.execute(
        "INSERT INTO drugref.condition "
        "(condition_uuid, source, source_code, name, record_kind, tree_numbers, "
        " first_seen_ingest) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (condition_uuid) DO UPDATE SET "
        "  name = EXCLUDED.name, record_kind = EXCLUDED.record_kind, "
        "  tree_numbers = EXCLUDED.tree_numbers "
        "RETURNING first_seen_ingest",
        (condition_uuid, stored_source, record.record_ui, record.name,
         record.record_kind, list(record.tree_numbers), ingest_run_id)).fetchone()[0]
    return condition_uuid, first_seen == ingest_run_id


def clear_source_condition_edges(conn: psycopg.Connection, source: str) -> None:
    """Drop every condition DAG edge contributed by `source`.

    Called at the start of a re-ingest so a new upstream release fully REPLACES the
    previous one. Scoped by source so an unrelated feed's edges survive. Condition
    rows are NOT deleted -- their UUIDs are immortal and are re-derived identically
    on the way back in.
    """
    conn.execute(
        "DELETE FROM drugref.condition_parent WHERE ingest_run IN "
        "(SELECT ingest_run_id FROM drugref.ingest_run WHERE source = %s)",
        (source,))


def add_condition_parent_edge(conn: psycopg.Connection, child_uuid: uuid.UUID,
                              parent_uuid: uuid.UUID, ingest_run_id: int) -> bool:
    """Add one condition DAG edge. Returns True if a new row was inserted.

    ON CONFLICT DO NOTHING keeps a release that states an edge twice harmless.
    """
    cur = conn.execute(
        "INSERT INTO drugref.condition_parent "
        "(child_condition_uuid, parent_condition_uuid, ingest_run) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (child_uuid, parent_uuid, ingest_run_id))
    return cur.rowcount == 1
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_conditions_writer.py -v` → PASS (6 tests)
Run: `DRUGREF_TEST_DSN='...' uv run pytest -q && ruff check .` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/drugref/conditions.py tests/test_conditions_writer.py
git commit -m "feat(conditions): the single writer for the condition registry

Mirrors classes.py: edges are a rebuildable projection and are DELETEd per source,
while condition rows survive because condition_uuid is a pure function of
(source, source_code). Keyed on record_ui, never concept_ui -- many concepts
resolve to one record, so keying on the concept would split one condition."
```

---

### Task 5: `db/014` — the contraindication relations

**Files:**
- Create: `db/014_mesh_contraindications.sql`
- Test: `tests/test_schema_mesh_contraindications.py`

**Interfaces:**
- Produces: `drugref.condition_ci_axis`, `drugref.moiety_condition_contraindication`,
  `drugref.moiety_contraindication`, `drugref.ingest_unresolved_ci_object`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_schema_mesh_contraindications.py`:

```python
"""Structural guarantees of slice 5b's two contraindication relations (db/014)."""
import uuid

import psycopg
import pytest

from drugref import ids


@pytest.fixture
def a_condition(conn, ingest_run_id):
    cu = ids.mint_condition_uuid("MeSH", "D004827")
    conn.execute(
        "INSERT INTO drugref.condition (condition_uuid, source, source_code, name, "
        "record_kind, first_seen_ingest) "
        "VALUES (%s,'MeSH','D004827','Epilepsy','DESCRIPTOR',%s)", (cu, ingest_run_id))
    return cu


def test_ci_with_axis_is_seeded_and_expands(conn):
    """ROADMAP's standing instruction: decide expands_descendants per predicate
    rather than inherit a default. CI_with is declared true on Plan B's argument --
    for a contraindication, fewer rows is the harm direction."""
    assert conn.execute(
        "SELECT expands_descendants FROM drugref.condition_ci_axis "
        "WHERE relationship = 'CI_with'").fetchone() == (True,)


def test_expands_descendants_has_no_default(conn):
    """db/012 finding 5: ci_axis claimed a force-a-declaration discipline while
    supplying a DEFAULT. This table actually implements it, so a predicate added
    later cannot inherit an unexamined answer."""
    assert conn.execute(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema='drugref' AND table_name='condition_ci_axis' "
        "AND column_name='expands_descendants'").fetchone()[0] is None


def test_condition_ci_relationship_is_a_foreign_key(conn, a_moiety, a_condition,
                                                    ingest_run_id):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.moiety_condition_contraindication "
            "(subject_moiety_uuid, object_condition_uuid, relationship, source, "
            " ingest_run) VALUES (%s,%s,'CI_invented','MED-RT',%s)",
            (a_moiety, a_condition, ingest_run_id))


def test_source_is_in_the_condition_ci_primary_key(conn, a_moiety, a_condition,
                                                   ingest_run_id):
    """db/006 finding 2: without source in the key, a second authority's identical
    assertion is swallowed by ON CONFLICT and then destroyed by the FIRST source's
    next rebuild. Slice 5c plans exactly that second source."""
    for src in ("MED-RT", "DRUGREF"):
        conn.execute(
            "INSERT INTO drugref.moiety_condition_contraindication "
            "(subject_moiety_uuid, object_condition_uuid, relationship, source, "
            " ingest_run) VALUES (%s,%s,'CI_with',%s,%s)",
            (a_moiety, a_condition, src, ingest_run_id))
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_contraindication"
    ).fetchone()[0] == 2


def test_moiety_contraindication_round_trips(conn, a_moiety, ingest_run_id):
    other = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (%s,'pimozide',%s) RETURNING moiety_uuid",
        (uuid.uuid4(), ingest_run_id)).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.moiety_contraindication (subject_moiety_uuid, "
        "object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s,%s,'CI_ChemClass','MED-RT',%s)", (a_moiety, other, ingest_run_id))
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication").fetchone()[0] == 1


def test_a_moiety_is_not_contraindicated_with_itself(conn, a_moiety, ingest_run_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.moiety_contraindication (subject_moiety_uuid, "
            "object_moiety_uuid, relationship, source, ingest_run) "
            "VALUES (%s,%s,'CI_ChemClass','MED-RT',%s)",
            (a_moiety, a_moiety, ingest_run_id))


def test_moiety_contraindication_relationship_is_constrained(conn, a_moiety,
                                                             ingest_run_id):
    other = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (%s,'x',%s) RETURNING moiety_uuid",
        (uuid.uuid4(), ingest_run_id)).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.moiety_contraindication (subject_moiety_uuid, "
            "object_moiety_uuid, relationship, source, ingest_run) "
            "VALUES (%s,%s,'nonsense','MED-RT',%s)", (a_moiety, other, ingest_run_id))


def test_unresolved_ci_object_records_the_class_arm(conn, ingest_run_id):
    """The 405 withheld assertions are PRESERVED as a worklist row, not dropped."""
    conn.execute(
        "INSERT INTO drugref.ingest_unresolved_ci_object (ingest_run, source, "
        "relationship, object_source, object_code, object_name, assertion_count) "
        "VALUES (%s,'MED-RT','CI_ChemClass','MeSH','D013449','Sulfonamides',36)",
        (ingest_run_id,))
    assert conn.execute(
        "SELECT assertion_count FROM drugref.ingest_unresolved_ci_object"
    ).fetchone()[0] == 36
```

- [ ] **Step 2: Run to verify they fail**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_schema_mesh_contraindications.py -v`
Expected: FAIL — `UndefinedTable: relation "drugref.condition_ci_axis" does not exist`

- [ ] **Step 3: Write the migration**

Create `db/014_mesh_contraindications.sql`:

```sql
-- db/014_mesh_contraindications.sql
-- Slice 5b's two contraindication relations, plus the worklist for what is withheld.
--
-- TWO RELATIONS, NOT ONE, because the objects are different kinds of thing:
--   * moiety_condition_contraindication -- CI_with. "Do not give drug X to a patient
--     in state C." 9,482 rows against the real release.
--   * moiety_contraindication           -- CI_ChemClass's moiety arm. "Do not
--     co-administer drug X with drug Y." 1,443 rows, and drugref's FIRST genuinely
--     pairwise DDI content: both endpoints are moieties, so nothing expands.
--
-- Both are REBUILDABLE PROJECTIONS and CANDIDATE TIER, exactly as
-- class_contraindication is: MED-RT does not track label updates, so rows here feed
-- review and must never auto-alert.

-- ---- 1. the condition-contraindication vocabulary ---------------------------
--
-- A SEPARATE TABLE FROM ci_axis, because the two map to different things:
-- ci_axis.membership_relationship names the class_membership axis a rule expands
-- over, and a condition has no membership axis -- nothing is a member of pregnancy.
CREATE TABLE IF NOT EXISTS drugref.condition_ci_axis (
    relationship        text    PRIMARY KEY,
    -- NO DEFAULT, deliberately. db/012 finding 5 recorded that ci_axis's comment
    -- claimed db/006's force-a-declaration discipline while supplying a DEFAULT
    -- that quietly answered the question for you. This column implements the
    -- discipline: a predicate added later MUST state whether it expands, because
    -- MeSH's tree is a different shape from MED-RT's and the recall-safe answer is
    -- not automatically the correct one.
    expands_descendants boolean NOT NULL
);

INSERT INTO drugref.condition_ci_axis (relationship, expands_descendants)
VALUES ('CI_with', true)
ON CONFLICT (relationship) DO NOTHING;

COMMENT ON TABLE drugref.condition_ci_axis IS
    'Admissible drug-condition contraindication predicates, and whether each expands '
    'down the condition DAG. moiety_condition_contraindication.relationship is a '
    'foreign key into this table, so adding a predicate is ONE insert and cannot '
    'leave the read path silently returning nothing.';
COMMENT ON COLUMN drugref.condition_ci_axis.expands_descendants IS
    'Declared per predicate, with NO default. CI_with is true on Plan B''s argument: '
    'a rule on Epilepsy must reach a patient coded Temporal Lobe Epilepsy, and for a '
    'contraindication FEWER ROWS IS THE HARM DIRECTION.';

-- ---- 2. drug -> condition ----------------------------------------------------

CREATE TABLE IF NOT EXISTS drugref.moiety_condition_contraindication (
    subject_moiety_uuid   uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_condition_uuid uuid   NOT NULL REFERENCES drugref.condition(condition_uuid),
    relationship          text   NOT NULL REFERENCES drugref.condition_ci_axis(relationship),
    source                text   NOT NULL,
    ingest_run            bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- SOURCE IS IN THE KEY (db/006 finding 2). Without it, a second authority
    -- asserting what MED-RT already recorded is swallowed by ON CONFLICT DO NOTHING
    -- -- and the next routine MED-RT rebuild, which deletes by ingest_run, takes the
    -- shared row away with it, destroying the other source's independent assertion.
    -- Slice 5c plans exactly that second source.
    PRIMARY KEY (subject_moiety_uuid, object_condition_uuid, relationship, source)
);

CREATE INDEX IF NOT EXISTS moiety_condition_ci_by_condition
    ON drugref.moiety_condition_contraindication (object_condition_uuid);

COMMENT ON TABLE drugref.moiety_condition_contraindication IS
    'Drug-CONDITION contraindications: the subject moiety is contraindicated in a '
    'patient who has the object condition. A REBUILDABLE PROJECTION, CANDIDATE TIER '
    '-- MED-RT does not track label updates, so rows feed review and must not '
    'auto-alert. NOT AN ABSOLUTE CONTRAINDICATION: MED-RT asserts the association, '
    'never its severity nor whether benefit-risk may override it, so a consumer must '
    'not render "contraindicated in pregnancy" as a hard stop. The curated overlay '
    '(slice 5c) adds severity, mechanism and management.';
COMMENT ON COLUMN drugref.moiety_condition_contraindication.subject_moiety_uuid IS
    'The drug the contraindication is ABOUT. Not interchangeable with the object.';
COMMENT ON COLUMN drugref.moiety_condition_contraindication.object_condition_uuid IS
    'The patient state -- a disease, but also pregnancy, lactation or a procedure.';

-- ---- 3. drug -> drug ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS drugref.moiety_contraindication (
    subject_moiety_uuid uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    object_moiety_uuid  uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    -- A CHECK, NOT A FOREIGN KEY INTO AN AXIS TABLE -- and that asymmetry with
    -- db/006 is deliberate. db/006 replaced a CHECK with an FK because the predicate
    -- list was duplicated in a CASE *inside a view*: two lists in two places, where
    -- widening only one silently produced rows that expanded to nothing. Here both
    -- endpoints are moieties -- no DAG, no expansion, no membership axis, and
    -- therefore NO SECOND LIST to keep in step with. An FK would copy the form of
    -- db/006's fix while its cause is absent.
    relationship        text   NOT NULL
        CONSTRAINT moiety_contraindication_relationship
        CHECK (relationship IN ('CI_ChemClass')),
    source              text   NOT NULL,
    ingest_run          bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (subject_moiety_uuid, object_moiety_uuid, relationship, source),
    CONSTRAINT moiety_contraindication_not_self
        CHECK (subject_moiety_uuid <> object_moiety_uuid)
);

CREATE INDEX IF NOT EXISTS moiety_contraindication_by_object
    ON drugref.moiety_contraindication (object_moiety_uuid);

COMMENT ON TABLE drugref.moiety_contraindication IS
    'PAIRWISE drug-drug contraindications: the subject moiety must not be '
    'co-administered with the object moiety. drugref''s first EXACT pair data -- '
    'both endpoints are moieties, so nothing expands and no class DAG is involved. '
    'DIRECTIONAL: the subject is the drug the statement is ABOUT, and swapping the '
    'columns changes the meaning. A REBUILDABLE PROJECTION, CANDIDATE TIER.';

-- ---- 4. what is withheld, preserved as a worklist -----------------------------
--
-- CI_ChemClass's CLASS arm (405 assertions over 108 MeSH chemical classes) is NOT
-- ingested. Expanding it over MeSH's STRUCTURAL chemical tree makes a rule on
-- Sulfonamides (D013449, 36 rules) reach 61 moieties including bendroflumethiazide
-- and bosentan -- the discredited sulfa cross-reactivity inference, generated
-- automatically and shipped as a safety assertion. MeSH's chemical tree is a
-- structural taxonomy and does not mean what a clinical class means.
--
-- Plan B's precedent governs: it made a pharmacist rule on 14 expansion roots before
-- expanding over them. So the content is PRESERVED and published as a question, and
-- a curator decides. This table is what db/008 established for unmatched ingredients
-- -- keeping only a COUNT and discarding the identity is what made that gap
-- unqueryable, and the same mistake is not repeated here.
CREATE TABLE IF NOT EXISTS drugref.ingest_unresolved_ci_object (
    ingest_run      bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    source          text   NOT NULL,
    relationship    text   NOT NULL,
    object_source   text   NOT NULL,
    object_code     text   NOT NULL,
    object_name     text,
    -- How many assertions ride on this object. One row per OBJECT, not per
    -- assertion, because the question a curator answers is per class: "should a
    -- contraindication naming Sulfonamides expand over MeSH's structural tree?"
    assertion_count integer NOT NULL,
    PRIMARY KEY (ingest_run, source, relationship, object_source, object_code)
);

COMMENT ON TABLE drugref.ingest_unresolved_ci_object IS
    'Contraindication assertions whose OBJECT drugref deliberately did not ingest: '
    'one row per object, carrying how many rules ride on it. Not an error and not a '
    'drop -- it is the worklist behind gap_unresolved_ci_object. Populated by '
    'CI_ChemClass objects that name a CLASS rather than a substance, which are '
    'withheld pending curator review (see the sulfonamide case in this migration).';
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_schema_mesh_contraindications.py -v` → PASS (8)
Run: `DRUGREF_TEST_DSN='...' uv run pytest -q && ruff check .` → all pass

- [ ] **Step 5: Commit**

```bash
git add db/014_mesh_contraindications.sql tests/test_schema_mesh_contraindications.py
git commit -m "feat(db): slice 5b's two contraindication relations (db/014)

Two relations, not one: CI_with's object is a patient state, CI_ChemClass's is
another drug. moiety_contraindication is drugref's first exact pairwise DDI table.

condition_ci_axis.expands_descendants has NO default -- db/012 found ci_axis
claiming a force-a-declaration discipline while supplying one. CI_with declares
true on Plan B's argument. moiety_contraindication.relationship is a CHECK, not
an FK: db/006's fix addressed a list duplicated inside a view's CASE, and with two
moiety endpoints there is no second list for that failure mode to occur in.

ingest_unresolved_ci_object preserves the withheld class arm as a worklist."
```

---

### Task 6: `medrt.py` — parse the two MeSH-keyed predicates

**Files:**
- Modify: `src/drugref/ingest/medrt.py` (constants ~line 83; `ParsedMedrt` ~line 130; `parse` ~line 287)
- Modify: `tests/fixtures/make_medrt_subset.py`
- Test: `tests/test_medrt_parser.py`

**Interfaces:**
- Produces: `medrt.MeshObjectAssertion(rxcui: str, mesh_code: str, relationship: str)`;
  `ParsedMedrt.mesh_contraindications: list[MeshObjectAssertion]`;
  `ParsedMedrt.non_mesh_ci_objects: int`; `medrt.MESH_CI_RELATIONSHIPS: frozenset[str]`.

- [ ] **Step 1: Extend the MED-RT fixture extractor**

In `tests/fixtures/make_medrt_subset.py`, the extractor currently redacts out-of-scope association
endpoints. **MeSH endpoints must now be RETAINED for `CI_with` and `CI_ChemClass`** — MeSH is
licence-cleared (slice 2b), unlike SNOMED. Find the redaction predicate and add `MeSH` to the namespaces
whose endpoint codes survive, keeping SNOMED redacted.

Read the file first, then make the minimal change; the existing redaction test
(`tests/test_medrt_parser.py`, search for `redact`) must be updated to assert **SNOMED is still redacted
while MeSH is not**, and must not simply be deleted.

Regenerate:

```bash
unzip -o -q downloads/MEDRT/Core_MEDRT_XML.zip -d /tmp/medrt
uv run python tests/fixtures/make_medrt_subset.py \
    /tmp/medrt/Core_MEDRT_2026.07.06_XML.xml > tests/fixtures/medrt_subset.xml
```

Confirm the fixture now carries at least one `CI_with` and one `CI_ChemClass`:

```bash
grep -c "CI_with\|CI_ChemClass" tests/fixtures/medrt_subset.xml
```

If zero, add a subject RxCUI that carries one to the extractor's keep-list and regenerate.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_medrt_parser.py`:

```python
def test_mesh_keyed_contraindications_are_parsed():
    """CI_with and CI_ChemClass were deliberately skipped until slice 5b, because
    their object is a MeSH code this parser could not resolve. It still does not
    resolve them -- it hands the raw code on -- but it no longer discards them."""
    parsed = medrt.parse(FIXTURE)
    assert parsed.mesh_contraindications, "no MeSH-keyed contraindications parsed"
    predicates = {a.relationship for a in parsed.mesh_contraindications}
    assert predicates <= {"CI_with", "CI_ChemClass"}
    for a in parsed.mesh_contraindications:
        assert a.rxcui and a.mesh_code
        assert a.mesh_code.startswith("M")     # a MeSH ConceptUI


def test_non_mesh_ci_objects_are_counted_not_dropped():
    """Two CI_with assertions in the real release point at a MED-RT EXT concept
    ('Current Non-smoker'), not MeSH. EXT is deliberately not an ingested concept
    type, so the assertion is refused -- but counted, never silently dropped."""
    parsed = medrt.parse(FIXTURE)
    assert isinstance(parsed.non_mesh_ci_objects, int)
    for a in parsed.mesh_contraindications:
        assert a.mesh_code.startswith("M")


def test_mesh_ci_predicates_left_the_skipped_list():
    """skipped_predicates is the release-to-release change detector. A predicate we
    now INGEST must leave it, or the detector stops meaning anything."""
    parsed = medrt.parse(FIXTURE)
    assert "CI_with" not in parsed.skipped_predicates
    assert "CI_ChemClass" not in parsed.skipped_predicates


def test_class_level_ci_is_unaffected():
    """CI_MoA/CI_PE must be untouched by this change -- 5a's rows are load-bearing
    for ddi_candidate_pair and its measured 6,395 pairs."""
    parsed = medrt.parse(FIXTURE)
    assert {c.relationship for c in parsed.contraindications} <= {"CI_MoA", "CI_PE"}
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_medrt_parser.py -v`
Expected: FAIL — `AttributeError: 'ParsedMedrt' object has no attribute 'mesh_contraindications'`

- [ ] **Step 4: Implement**

In `src/drugref/ingest/medrt.py`:

(a) After `CI_RELATIONSHIPS` (line 83), add:

```python
# MeSH-keyed contraindications (slice 5b). Both run RxNorm -> MeSH, so their OBJECT
# is a MeSH ConceptUI this parser cannot resolve on its own -- ingest/mesh_concepts.py
# does that, from the MeSH release. The parser therefore hands the raw code on rather
# than resolving or dropping it.
#
#   CI_with       -- "contraindicated in a patient with <condition>". 11,524
#                    assertions; the object is a disease, but also pregnancy,
#                    lactation, a procedure or a demographic (spec §4.3).
#   CI_ChemClass  -- "do not co-administer with <this chemical>". 1,939 assertions,
#                    and mostly a SPECIFIC DRUG (Pimozide, Cisapride, Ritonavir)
#                    rather than a class, which is why 5b resolves its object
#                    against the moiety registry first.
MESH_CI_RELATIONSHIPS = frozenset({"CI_with", "CI_ChemClass"})

# The namespace a MeSH-keyed contraindication's object must live in.
MESH_NAMESPACE = "MeSH"
```

(b) Add the dataclass after `ContraindicationAssertion` (line 128):

```python
@dataclass(frozen=True)
class MeshObjectAssertion:
    """MED-RT asserts a contraindication whose OBJECT is a MeSH concept.

    `mesh_code` is a MeSH ConceptUI ("M0004868") -- NOT a DescriptorUI. Left
    unresolved here on purpose: this module is pure and reads only the MED-RT file,
    while resolving the code needs the MeSH release. The orchestrator joins the two.
    """
    rxcui: str
    mesh_code: str
    relationship: str
```

(c) Extend `ParsedMedrt` (after `contraindications`, line 142):

```python
    mesh_contraindications: list[MeshObjectAssertion] = field(default_factory=list)
    # CI_with/CI_ChemClass assertions whose object is NOT in the MeSH namespace. Two
    # exist in the 2026.07.06 release, both pointing at the MED-RT EXT concept
    # 'Current Non-smoker' -- and EXT is deliberately not an ingested concept type.
    # Counted rather than dropped, the same posture as inactive_concepts.
    non_mesh_ci_objects: int = 0
```

(d) In `parse`, initialise beside `contraindications` (line 255):

```python
    mesh_contraindications: list[MeshObjectAssertion] = []
    non_mesh_ci_objects = 0
```

(e) Add a branch after the `elif name in CI_RELATIONSHIPS:` block (after line 295):

```python
        elif name in MESH_CI_RELATIONSHIPS:
            # Slice 5b. Endpoint-scoped exactly as the other branches are, but to
            # the MeSH namespace: the object is resolved later, against the MeSH
            # release, by ingest/mesh_concepts.py. An object in any other namespace
            # is refused and COUNTED -- in the real release those two rows point at
            # a MED-RT EXT concept, which drugref does not ingest.
            if from_ns == RXNORM_NAMESPACE and to_ns == MESH_NAMESPACE:
                mesh_contraindications.append(MeshObjectAssertion(
                    rxcui=from_code, mesh_code=to_code, relationship=name))
            else:
                non_mesh_ci_objects += 1
```

(f) Add both to the returned `ParsedMedrt(...)`:

```python
                       mesh_contraindications=mesh_contraindications,
                       non_mesh_ci_objects=non_mesh_ci_objects,
```

(g) Update the module docstring's line about `CI_with`/`CI_ChemClass` being out of scope, and the
`else:` branch comment listing them as deliberate skips — they are now ingested.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_medrt_parser.py -v` → PASS
Run: `DRUGREF_TEST_DSN='...' uv run pytest -q && ruff check .` → all pass

**If `test_medrt_run.py` fails**, the fixture regeneration changed counts an existing test asserts on.
Update those assertions to the new measured values — do not revert the fixture.

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/medrt.py tests/test_medrt_parser.py \
        tests/fixtures/make_medrt_subset.py tests/fixtures/medrt_subset.xml
git commit -m "feat(medrt): parse CI_with and CI_ChemClass, endpoint-scoped to MeSH

The object is handed on as a raw MeSH ConceptUI: this module is pure and reads only
the MED-RT file, while resolving the code needs the MeSH release. An object outside
the MeSH namespace is refused and COUNTED -- two such rows exist in the release,
both naming a MED-RT EXT concept drugref does not ingest.

The fixture extractor now retains MeSH endpoints (licence-cleared in 2b) while
still redacting SNOMED."
```

---

### Task 7: `interactions.py` — the new writers

**Files:**
- Modify: `src/drugref/interactions.py`
- Test: `tests/test_interactions.py`

**Interfaces:**
- Produces:
  - `add_condition_contraindication(conn, subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run_id) -> bool`
  - `add_moiety_contraindication(conn, subject_moiety_uuid, object_moiety_uuid, relationship, source, ingest_run_id) -> bool`
  - `clear_source_mesh_contraindications(conn, source) -> None`
  - `record_unresolved_ci_objects(conn, rows: Iterable[tuple[str, str, str, str, str | None, int]], ingest_run_id) -> int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_interactions.py` (reuse its existing fixtures; add a condition fixture as in Task 5):

```python
def test_add_condition_contraindication(conn, a_moiety, a_condition, ingest_run_id):
    assert interactions.add_condition_contraindication(
        conn, a_moiety, a_condition, "CI_with", "MED-RT", ingest_run_id)


def test_repeated_condition_contraindication_is_harmless(conn, a_moiety, a_condition,
                                                         ingest_run_id):
    """A release that states one assertion twice must not fail the ingest."""
    interactions.add_condition_contraindication(
        conn, a_moiety, a_condition, "CI_with", "MED-RT", ingest_run_id)
    assert not interactions.add_condition_contraindication(
        conn, a_moiety, a_condition, "CI_with", "MED-RT", ingest_run_id)


def test_add_moiety_contraindication_is_directional(conn, a_moiety, ingest_run_id):
    """Subject and object are not interchangeable: the subject is the drug the
    statement is ABOUT. Both directions are storable and mean different things."""
    other = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (gen_random_uuid(),'pimozide',%s) "
        "RETURNING moiety_uuid", (ingest_run_id,)).fetchone()[0]
    assert interactions.add_moiety_contraindication(
        conn, a_moiety, other, "CI_ChemClass", "MED-RT", ingest_run_id)
    assert interactions.add_moiety_contraindication(
        conn, other, a_moiety, "CI_ChemClass", "MED-RT", ingest_run_id)
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication").fetchone()[0] == 2


def test_clear_source_removes_both_relations(conn, a_moiety, a_condition,
                                             ingest_run_id):
    """A re-ingest must fully REPLACE the previous release, across both tables."""
    other = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
        "first_seen_ingest) VALUES (gen_random_uuid(),'x',%s) RETURNING moiety_uuid",
        (ingest_run_id,)).fetchone()[0]
    interactions.add_condition_contraindication(
        conn, a_moiety, a_condition, "CI_with", "MED-RT", ingest_run_id)
    interactions.add_moiety_contraindication(
        conn, a_moiety, other, "CI_ChemClass", "MED-RT", ingest_run_id)

    interactions.clear_source_mesh_contraindications(conn, "PBS")   # another source
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_contraindication"
    ).fetchone()[0] == 1

    interactions.clear_source_mesh_contraindications(conn, "PBS")
    # ingest_run_id's fixture source IS 'PBS' in conftest -- clearing it empties both.
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication").fetchone()[0] == 0


def test_record_unresolved_ci_objects(conn, ingest_run_id):
    written = interactions.record_unresolved_ci_objects(
        conn, [("MED-RT", "CI_ChemClass", "MeSH", "D013449", "Sulfonamides", 36)],
        ingest_run_id)
    assert written == 1
    assert conn.execute(
        "SELECT object_name, assertion_count FROM drugref.ingest_unresolved_ci_object"
    ).fetchone() == ("Sulfonamides", 36)
```

> **Note for the implementer:** `conftest.ingest_run_id` creates a run with `source = 'PBS'`. Check that
> before writing the clear-source assertions and adjust the source strings so the test asserts what it
> claims to. If it is simpler, create a second `ingest_run` row with `source='MED-RT'` inside the test.

- [ ] **Step 2: Run to verify they fail**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_interactions.py -v`
Expected: FAIL — `AttributeError: module 'drugref.interactions' has no attribute 'add_condition_contraindication'`

- [ ] **Step 3: Implement**

Append to `src/drugref/interactions.py` (and extend the module docstring to say it now writes four tables):

```python
def clear_source_mesh_contraindications(conn: psycopg.Connection, source: str) -> None:
    """Drop every slice-5b contraindication contributed by `source`.

    Covers BOTH relations plus the unresolved-object worklist, because one ingest
    writes all three and a partial clear would leave last release's rows beside this
    one's. Same rebuildable-projection discipline as
    clear_source_contraindications: a contraindication retracted upstream has to
    disappear here too, and an insert-only merge could never express that.

    The worklist is cleared for the reason classes.clear_source_unmatched_ingredients
    gives: an object that starts resolving must LEAVE the list, or the worklist grows
    by its own length every ingest and never shrinks.
    """
    for table in ("moiety_condition_contraindication", "moiety_contraindication",
                  "ingest_unresolved_ci_object"):
        conn.execute(
            f"DELETE FROM drugref.{table} WHERE ingest_run IN "
            "(SELECT ingest_run_id FROM drugref.ingest_run WHERE source = %s)",
            (source,))


def add_condition_contraindication(conn: psycopg.Connection,
                                   subject_moiety_uuid: uuid.UUID,
                                   object_condition_uuid: uuid.UUID,
                                   relationship: str, source: str,
                                   ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` is contraindicated in a patient who has
    `object_condition_uuid`, on axis `relationship` (CI_with).

    Returns True if a new row was inserted. The clinical direction is carried
    entirely by which side is which -- a condition is not a drug, so the two are not
    even the same kind of thing, but the column names still say so explicitly.
    """
    cur = conn.execute(
        "INSERT INTO drugref.moiety_condition_contraindication "
        "(subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_condition_uuid, relationship, source,
         ingest_run_id))
    return cur.rowcount == 1


def add_moiety_contraindication(conn: psycopg.Connection,
                                subject_moiety_uuid: uuid.UUID,
                                object_moiety_uuid: uuid.UUID, relationship: str,
                                source: str, ingest_run_id: int) -> bool:
    """Record that `subject_moiety_uuid` must not be co-administered with
    `object_moiety_uuid` (CI_ChemClass's moiety arm).

    DIRECTIONAL, and both directions may legitimately be stored: MED-RT states which
    drug the assertion is ABOUT, and it does not always assert the converse. A
    consumer wanting "is this pair contraindicated at all" must query both columns
    rather than assume symmetry.

    Returns True if a new row was inserted.
    """
    cur = conn.execute(
        "INSERT INTO drugref.moiety_contraindication "
        "(subject_moiety_uuid, object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (subject_moiety_uuid, object_moiety_uuid, relationship, source,
         ingest_run_id))
    return cur.rowcount == 1


def record_unresolved_ci_objects(conn: psycopg.Connection, rows, ingest_run_id: int) -> int:
    """Persist contraindication objects drugref deliberately did not ingest.

    `rows` is an iterable of
    (source, relationship, object_source, object_code, object_name, assertion_count).

    Not an error and not a drop: these are real upstream assertions withheld pending
    a curator decision (see db/014 on the sulfonamide case). Persisting the IDENTITY
    rather than only a count is what lets gap_unresolved_ci_object be a query -- the
    exact lesson db/008 drew when the earlier ingest kept only the COUNT of unmatched
    ingredients and discarded the RxCUIs.

    Batched, like classes.add_unmatched_ingredients: nobody needs the per-row
    insert-vs-conflict answer, because the caller already holds the deduped set.
    Returns rows written.
    """
    batch = [(ingest_run_id, *row) for row in rows]
    if not batch:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO drugref.ingest_unresolved_ci_object "
            "(ingest_run, source, relationship, object_source, object_code, "
            " object_name, assertion_count) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING", batch)
    return len(batch)
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_interactions.py -v` → PASS
Run: `DRUGREF_TEST_DSN='...' uv run pytest -q && ruff check .` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/drugref/interactions.py tests/test_interactions.py
git commit -m "feat(interactions): writers for slice 5b's two contraindication relations

Plus record_unresolved_ci_objects, which persists the IDENTITY of withheld objects
rather than only a count -- db/008's lesson from unmatched ingredients, where
keeping the count and discarding the RxCUIs made the gap unqueryable."
```

---

### Task 8: `db/015` — the read path

**Files:**
- Create: `db/015_condition_read_path.sql`
- Test: `tests/test_condition_pairs.py`

**Interfaces:**
- Produces: views `drugref.condition_subtree(root_uuid, condition_uuid)` and
  `drugref.condition_contraindication_expanded(subject_moiety, object_condition, member_condition,
  is_direct, relationship, source)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_condition_pairs.py`:

```python
"""The condition read path (db/015): descendant expansion, and its opt-out."""
import pytest

from drugref import conditions, ids, interactions
from drugref.ingest.mesh_concepts import MeshRecord


def _condition(conn, run_id, code, name, trees):
    rec = MeshRecord(concept_ui="M0", record_ui=code, record_kind="DESCRIPTOR",
                     name=name, tree_numbers=trees, unii=frozenset(),
                     cas=frozenset(), is_preferred_concept=True)
    cu, _ = conditions.upsert_condition(conn, rec, run_id, "MeSH")
    return cu


@pytest.fixture
def epilepsy_tree(conn, a_moiety, ingest_run_id):
    """Epilepsy, with one descendant, and a rule naming only the PARENT."""
    parent = _condition(conn, ingest_run_id, "D004827", "Epilepsy",
                        ("C10.228.140.490",))
    child = _condition(conn, ingest_run_id, "D004829", "Epilepsy, Generalized",
                       ("C10.228.140.490.360",))
    conditions.add_condition_parent_edge(conn, child, parent, ingest_run_id)
    interactions.add_condition_contraindication(
        conn, a_moiety, parent, "CI_with", "MED-RT", ingest_run_id)
    return {"parent": parent, "child": child, "moiety": a_moiety}


def test_a_rule_reaches_a_descendant_condition(conn, epilepsy_tree):
    """THE POINT OF THE SLICE'S READ PATH. A rule written against Epilepsy must fire
    for a patient coded Epilepsy, Generalized -- for a contraindication, fewer rows
    is the harm direction (Plan B)."""
    rows = conn.execute(
        "SELECT member_condition, is_direct FROM "
        "drugref.condition_contraindication_expanded "
        "WHERE subject_moiety = %s ORDER BY is_direct DESC",
        (epilepsy_tree["moiety"],)).fetchall()
    assert (epilepsy_tree["parent"], True) in rows
    assert (epilepsy_tree["child"], False) in rows


def test_is_direct_reproduces_the_unexpanded_set(conn, epilepsy_tree):
    """WHERE is_direct must return exactly what an unexpanded view would, so a
    precision-sensitive consumer opts out explicitly -- and one who FORGETS the
    filter errs toward recall, which is the safe direction to fail in."""
    rows = conn.execute(
        "SELECT member_condition FROM drugref.condition_contraindication_expanded "
        "WHERE subject_moiety = %s AND is_direct", (epilepsy_tree["moiety"],)
    ).fetchall()
    assert rows == [(epilepsy_tree["parent"],)]


def test_object_condition_is_what_the_rule_named(conn, epilepsy_tree):
    """The provenance column: a consumer must be able to see that the match came via
    an ancestor, not that the rule named the patient's exact condition."""
    row = conn.execute(
        "SELECT object_condition FROM drugref.condition_contraindication_expanded "
        "WHERE subject_moiety = %s AND NOT is_direct", (epilepsy_tree["moiety"],)
    ).fetchone()
    assert row == (epilepsy_tree["parent"],)


def test_expansion_is_gated_on_the_axis(conn, epilepsy_tree):
    """Switching a predicate off must need no view edit -- one UPDATE, and the
    read path stops expanding it. This is what makes 5b.2's per-predicate decision
    a data change rather than a migration."""
    conn.execute("UPDATE drugref.condition_ci_axis "
                 "SET expands_descendants = false WHERE relationship = 'CI_with'")
    rows = conn.execute(
        "SELECT member_condition FROM drugref.condition_contraindication_expanded "
        "WHERE subject_moiety = %s", (epilepsy_tree["moiety"],)).fetchall()
    assert rows == [(epilepsy_tree["parent"],)]


def test_subtree_includes_its_own_root(conn, epilepsy_tree):
    """is_direct is computed from this, so the root MUST be in its own subtree."""
    rows = conn.execute(
        "SELECT condition_uuid FROM drugref.condition_subtree WHERE root_uuid = %s",
        (epilepsy_tree["parent"],)).fetchall()
    assert (epilepsy_tree["parent"],) in rows
    assert len(rows) == 2


def test_a_condition_no_rule_names_is_absent_from_the_subtree(conn, epilepsy_tree):
    """Scoped to contraindicated conditions: computing 5,190 subtrees nothing asks
    about would be pure waste."""
    assert conn.execute(
        "SELECT count(*) FROM drugref.condition_subtree WHERE root_uuid = %s",
        (epilepsy_tree["child"],)).fetchone()[0] == 0


def test_the_walk_survives_a_cycle(conn, epilepsy_tree, ingest_run_id):
    """db/013 forbids only SELF-parenting; a longer cycle must terminate rather than
    recurse forever. UNION over (root, condition) is what guarantees that."""
    conditions.add_condition_parent_edge(
        conn, epilepsy_tree["parent"], epilepsy_tree["child"], ingest_run_id)
    rows = conn.execute(
        "SELECT count(*) FROM drugref.condition_subtree WHERE root_uuid = %s",
        (epilepsy_tree["parent"],)).fetchone()[0]
    assert rows == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_condition_pairs.py -v`
Expected: FAIL — `UndefinedTable: relation "drugref.condition_contraindication_expanded" does not exist`

- [ ] **Step 3: Write the migration**

Create `db/015_condition_read_path.sql`:

```sql
-- db/015_condition_read_path.sql
-- Read-time descendant expansion for drug-condition contraindications.
--
-- The same shape as db/012's ci_class_subtree + ddi_candidate_pair, over a different
-- DAG. Deliberately NOT the same view: this walks condition_parent (MeSH conditions),
-- not class_parent (substance classes), so it is a second walk over a second graph --
-- not the duplication db/012 removed, which was three copies of ONE walk.

CREATE OR REPLACE VIEW drugref.condition_subtree AS
WITH RECURSIVE subtree(root_uuid, condition_uuid) AS (
    SELECT DISTINCT ci.object_condition_uuid, ci.object_condition_uuid
    FROM   drugref.moiety_condition_contraindication ci
  UNION
    SELECT s.root_uuid, cp.child_condition_uuid
    FROM   subtree s
    JOIN   drugref.condition_parent cp ON cp.parent_condition_uuid = s.condition_uuid
)
SELECT root_uuid, condition_uuid FROM subtree;

COMMENT ON VIEW drugref.condition_subtree IS
    'For every condition a contraindication NAMES: that condition and every one '
    'below it in the condition DAG. THE ROOT IS INCLUDED IN ITS OWN SUBTREE -- '
    'condition_contraindication_expanded''s `is_direct` depends on it. Deduped on '
    '(root, condition) rather than on paths, so it terminates under a cycle (db/013 '
    'forbids only self-parenting) and stays linear in a multi-parent DAG, where '
    '1,690 of 5,190 conditions have several parents. Scoped to CONTRAINDICATED '
    'conditions: a condition no rule names is ABSENT, not present with only itself.';

CREATE OR REPLACE VIEW drugref.condition_contraindication_expanded AS
SELECT ci.subject_moiety_uuid    AS subject_moiety,
       ci.object_condition_uuid  AS object_condition,
       s.condition_uuid          AS member_condition,
       s.condition_uuid = ci.object_condition_uuid AS is_direct,
       ci.relationship,
       ci.source
FROM   drugref.moiety_condition_contraindication ci
JOIN   drugref.condition_ci_axis a ON a.relationship = ci.relationship
JOIN   drugref.condition_subtree s ON s.root_uuid    = ci.object_condition_uuid
-- Expansion is per predicate. When a predicate does not expand, only the named
-- condition survives -- so switching it off is ONE UPDATE and needs no view edit.
WHERE  a.expands_descendants
   OR  s.condition_uuid = ci.object_condition_uuid;

COMMENT ON VIEW drugref.condition_contraindication_expanded IS
    'Drug-condition contraindications, expanded down the condition DAG: the subject '
    'moiety is contraindicated in a patient whose condition is member_condition, '
    'because a rule named object_condition at or above it. DIRECTIONAL and CANDIDATE '
    'TIER -- rows feed review and must not auto-alert, and MED-RT asserts no severity, '
    'so this is never a hard stop on its own. `WHERE is_direct` reproduces the '
    'unexpanded row set exactly, so a precision-sensitive consumer opts out '
    'EXPLICITLY and a consumer who forgets errs toward recall. EXPANSION WIDENS '
    'RECALL, NOT CERTAINTY: a row with is_direct = false was written against an '
    'ancestor of the patient''s coded condition, which is why member_condition and '
    'is_direct are columns rather than an internal detail.';
COMMENT ON COLUMN drugref.condition_contraindication_expanded.object_condition IS
    'The condition the RULE named -- provenance for a non-direct match.';
COMMENT ON COLUMN drugref.condition_contraindication_expanded.member_condition IS
    'The condition actually matched: at or below object_condition.';
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_condition_pairs.py -v` → PASS (7)
Run: `DRUGREF_TEST_DSN='...' uv run pytest -q && ruff check .` → all pass

- [ ] **Step 5: Commit**

```bash
git add db/015_condition_read_path.sql tests/test_condition_pairs.py
git commit -m "feat(db): expand drug-condition contraindications over the condition DAG

Mirrors db/012's ci_class_subtree over a different graph: a rule on Epilepsy now
reaches a patient coded Epilepsy, Generalized. WHERE is_direct reproduces the
unexpanded row set, so precision is an explicit opt-out and forgetting the filter
errs toward recall. Expansion is gated per predicate, so switching one off is one
UPDATE and no view edit."
```

---

### Task 9: `db/016` — the review gate for the withheld class arm

**Files:**
- Create: `db/016_unresolved_ci_object_gap.sql`
- Modify: `src/drugref/questions.py` (`_GAP_SOURCES`, ~line 35)
- Test: `tests/test_gap_views.py`

**Interfaces:**
- Produces: view `drugref.gap_unresolved_ci_object(object_code, object_name, relationship,
  ci_rule_count, upstream_release)`; `open_question.gap_kind` admits `'unresolved_ci_object'`;
  `questions._GAP_SOURCES` gains that kind with `gap_key = 'MESH:<code>'`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gap_views.py`:

```python
def test_unresolved_ci_object_becomes_a_question(conn, ingest_run_id):
    """The 405 withheld CI_ChemClass assertions are PUBLISHED as questions, not
    dropped -- Plan B's precedent, where a pharmacist ruled on each expansion root
    before drugref expanded over it."""
    conn.execute(
        "INSERT INTO drugref.ingest_unresolved_ci_object (ingest_run, source, "
        "relationship, object_source, object_code, object_name, assertion_count) "
        "VALUES (%s,'MED-RT','CI_ChemClass','MeSH','D013449','Sulfonamides',36)",
        (ingest_run_id,))
    counts = questions.register_from_gaps(conn, ingest_run_id)
    assert counts["unresolved_ci_object"] == 1

    row = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ci_object'").fetchone()
    assert row[0] == "MESH:D013449"
    assert "Sulfonamides" in row[1]
    assert "36" in row[1]


def test_unresolved_ci_object_question_uuid_is_stable(conn, ingest_run_id):
    """Re-running an ingest must not re-mint the question: external tools cite it."""
    conn.execute(
        "INSERT INTO drugref.ingest_unresolved_ci_object (ingest_run, source, "
        "relationship, object_source, object_code, object_name, assertion_count) "
        "VALUES (%s,'MED-RT','CI_ChemClass','MeSH','D013449','Sulfonamides',36)",
        (ingest_run_id,))
    questions.register_from_gaps(conn, ingest_run_id)
    first = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind='unresolved_ci_object'").fetchone()[0]
    questions.register_from_gaps(conn, ingest_run_id)
    second = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind='unresolved_ci_object'").fetchone()[0]
    assert first == second
    assert first == ids.mint_question_uuid("unresolved_ci_object", "MESH:D013449")


def test_gap_kind_admits_the_fifth_kind(conn):
    """register_from_gaps INSERTs at the very LAST step of an ingest, so a kind the
    CHECK does not admit aborts the whole transaction after everything was rebuilt."""
    definition = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'open_question_gap_kind'").fetchone()[0]
    assert "unresolved_ci_object" in definition
```

- [ ] **Step 2: Run to verify they fail**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_gap_views.py -k unresolved -v`
Expected: FAIL — `UndefinedTable: relation "drugref.gap_unresolved_ci_object" does not exist`

- [ ] **Step 3: Write the migration**

Create `db/016_unresolved_ci_object_gap.sql`:

```sql
-- db/016_unresolved_ci_object_gap.sql
-- Publish the contraindication objects slice 5b deliberately withheld.
--
-- WHY THIS EXISTS. CI_ChemClass's class arm (405 assertions over 108 MeSH chemical
-- classes) is real upstream safety content that drugref does not ingest, because
-- expanding it over MeSH's STRUCTURAL chemical tree makes a rule on Sulfonamides
-- reach bendroflumethiazide and bosentan -- the discredited sulfa cross-reactivity
-- inference. Withholding it is the right call; withholding it SILENTLY is not.
--
-- So it becomes a question, exactly as Plan B made a pharmacist rule on 14 expansion
-- roots. This is drugref's second gap kind that drugref can answer ITSELF, by
-- recording a decision, rather than by consulting an external source.

CREATE OR REPLACE VIEW drugref.gap_unresolved_ci_object AS
SELECT u.object_code,
       max(u.object_name)       AS object_name,
       max(u.relationship)      AS relationship,
       sum(u.assertion_count)   AS ci_rule_count,
       max(r.upstream_release)  AS upstream_release
FROM   drugref.ingest_unresolved_ci_object u
JOIN   drugref.ingest_run r ON r.ingest_run_id = u.ingest_run
GROUP  BY u.object_code;

COMMENT ON VIEW drugref.gap_unresolved_ci_object IS
    'Contraindication objects drugref did not ingest, with how many upstream rules '
    'ride on each. One row per object, because the decision is per object: "should a '
    'contraindication naming this class expand over MeSH''s structural tree?" '
    'ABSENCE OF A ROW IS NOT COVERAGE -- an object no release ever asserted appears '
    'nowhere here.';

-- Admit the fifth question kind. Guarded on the constraint's TEXT rather than its
-- name, so a replay against an already-widened database skips the drop/add entirely
-- instead of rescanning -- the same idiom as db/010 and db/003.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%unresolved_ci_object%') THEN
        ALTER TABLE drugref.open_question
            DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question
            ADD CONSTRAINT open_question_gap_kind CHECK (gap_kind IN (
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root',
                'unresolved_ci_object'));
    END IF;
END $$;
```

- [ ] **Step 4: Wire it into `questions.py`**

Add to `_GAP_SOURCES` in `src/drugref/questions.py`, after `unreviewed_expansion_root`:

```python
    # Slice 5b. Like unreviewed_expansion_root, a question drugref answers ITSELF --
    # by deciding whether the named class may expand over MeSH's structural tree --
    # rather than by consulting a source. The gap_key scheme is MESH:{code} because
    # the subject is an upstream RECORD drugref never registered: it has no
    # drugref UUID to cite, which is exactly why it is a gap.
    "unresolved_ci_object": {
        "view": "gap_unresolved_ci_object",
        "key_sql": "'MESH:' || object_code",
        "text_sql": (
            "'Should contraindications naming ' || COALESCE(object_name, object_code) "
            "|| ' be expanded to the drugs beneath it in MeSH''s structural tree? ' "
            "|| ci_rule_count || ' upstream rule(s) ride on the answer, and they are "
            "withheld until it is decided -- MeSH structural classes do not map "
            "cleanly onto clinical ones.'"),
    },
```

- [ ] **Step 5: Run the tests**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_gap_views.py -v` → PASS
Run: `DRUGREF_TEST_DSN='...' uv run pytest -q && ruff check .` → all pass

- [ ] **Step 6: Commit**

```bash
git add db/016_unresolved_ci_object_gap.sql src/drugref/questions.py tests/test_gap_views.py
git commit -m "feat(questions): publish the withheld CI objects as a fifth gap kind

Withholding CI_ChemClass's class arm is the right call; withholding it silently is
not. Each withheld object becomes a citable question carrying how many upstream
rules ride on the answer -- Plan B's precedent, where a pharmacist ruled on each
expansion root before drugref expanded over it."
```

---

### Task 10: `ingest/mesh_ci_run.py` — the orchestrator

**Files:**
- Create: `src/drugref/ingest/mesh_ci_run.py`
- Test: `tests/test_mesh_ci_run.py`

**Interfaces:**
- Consumes: everything produced by Tasks 1–9, plus `classes.moieties_by_rxcui`,
  `classes.moieties_by_scheme`, `classes.add_unmatched_ingredients`,
  `classes.clear_source_unmatched_ingredients`, `questions.register_from_gaps`.
- Produces: `MeshCiSummary`; `ingest_mesh_contraindications(conn, *, medrt_path, desc_path, supp_path, upstream_release) -> MeshCiSummary`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mesh_ci_run.py`:

```python
"""End-to-end slice-5b ingest against the committed fixtures.

Uses an autouse TRUNCATE fixture for the reason the other orchestrator tests do:
ingest_mesh_contraindications commits internally, so it escapes conftest's
rollback-based isolation.
"""
import pathlib

import pytest

from drugref.ingest import mesh_ci_run

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean(conn):
    conn.execute(
        "TRUNCATE drugref.moiety_condition_contraindication, "
        "drugref.moiety_contraindication, drugref.ingest_unresolved_ci_object, "
        "drugref.condition_parent, drugref.condition, "
        "drugref.open_question, drugref.class_contraindication, "
        "drugref.class_membership, drugref.class_parent, drugref.substance_class, "
        "drugref.identity_claim, drugref.substance_moiety, drugref.ingest_run "
        "RESTART IDENTITY CASCADE")
    conn.commit()


def _run(conn):
    return mesh_ci_run.ingest_mesh_contraindications(
        conn,
        medrt_path=FIXTURES / "medrt_subset.xml",
        desc_path=FIXTURES / "mesh_ci_desc_subset.xml",
        supp_path=FIXTURES / "mesh_ci_supp_subset.xml",
        upstream_release="test")


def test_ingest_reports_a_summary(conn, seeded_moieties):
    """seeded_moieties: a fixture registering the moieties the MED-RT subset's
    subject RxCUIs point at. Build it from the fixture, not by hand."""
    summary = _run(conn)
    assert summary.conditions_in_release >= 1
    assert summary.condition_contraindications >= 1


def test_the_class_arm_is_counted_not_ingested(conn, seeded_moieties):
    """THE GUARD AGAINST THE SULFONAMIDE HAZARD. A CI_ChemClass naming a class must
    produce a worklist row and ZERO contraindication rows. Do not delete this test."""
    _run(conn)
    withheld = conn.execute(
        "SELECT object_code FROM drugref.ingest_unresolved_ci_object").fetchall()
    assert ("D013449",) in withheld, "Sulfonamides was not recorded as withheld"
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_contraindication mc "
        "JOIN drugref.substance_moiety sm ON sm.moiety_uuid = mc.object_moiety_uuid "
        "WHERE sm.display_name ILIKE '%sulfonamide%'").fetchone()[0] == 0


def test_rerunning_replaces_rather_than_duplicates(conn, seeded_moieties):
    """Per-source rebuild: a second run must leave the same row count, not double it."""
    first = _run(conn)
    second = _run(conn)
    assert first.condition_contraindications == second.condition_contraindications
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_contraindication"
    ).fetchone()[0] == second.condition_contraindications


def test_condition_uuids_survive_a_rebuild(conn, seeded_moieties):
    """Immortal by determinism: a rebuild re-derives the same UUIDs, which is what
    lets the projection be dropped safely."""
    _run(conn)
    before = set(conn.execute(
        "SELECT condition_uuid FROM drugref.condition").fetchall())
    _run(conn)
    assert set(conn.execute(
        "SELECT condition_uuid FROM drugref.condition").fetchall()) == before


def test_unmatched_subjects_are_counted(conn, seeded_moieties):
    """22% of CI_with subjects do not join the gated registry. Counted, never
    dropped -- the slice-1/2a no-silent-exclude posture."""
    summary = _run(conn)
    assert summary.unmatched_subject_rxcuis >= 0
    assert isinstance(summary.unmatched_subject_rxcuis, int)


def test_the_question_register_is_rebuilt(conn, seeded_moieties):
    """Every orchestrator rebuilds the register as its LAST step before commit."""
    _run(conn)
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ci_object'").fetchone()[0] >= 1
```

> **Implementer note:** write the `seeded_moieties` fixture first. Read
> `tests/fixtures/medrt_subset.xml` for the `from_code` values of its `CI_with` /
> `CI_ChemClass` associations, then insert a `substance_moiety` plus an `RXNORM_IN`
> `identity_claim` for each, and a `UNII`/`CAS` claim matching the MeSH keys of the
> substance-valued objects (e.g. Pimozide D010860). Mirror
> `tests/test_medrt_run.py`'s existing seeding fixture rather than inventing a new style.

- [ ] **Step 2: Run to verify they fail**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_mesh_ci_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.ingest.mesh_ci_run'`

- [ ] **Step 3: Implement**

Create `src/drugref/ingest/mesh_ci_run.py`:

```python
"""Orchestrate one slice-5b ingest: MED-RT's MeSH-keyed contraindications.

Reads TWO authorities and joins them: MED-RT states the contraindication, MeSH
defines its object. Mirrors medrt_run/mesh_run (open an ingest_run for provenance,
do the work, stamp finished_at, commit) with two genuinely new pieces:

  1. M-CODE RESOLUTION. MED-RT's MeSH endpoint is a ConceptUI, so every object is
     resolved against the MeSH release (ingest/mesh_concepts.py). 99.81% of this
     slice's objects resolve; the rest are counted.
  2. THE TWO ARMS OF CI_ChemClass. Its object is usually a SPECIFIC DRUG (Pimozide,
     Cisapride, Ritonavir), so it is first resolved against the moiety registry via
     slice 2b's two-key UNII->CAS bridge. When it resolves, the assertion is an exact
     drug-drug pair. When it does not, the object is a genuine chemical CLASS, and
     the assertion is WITHHELD and recorded as a question -- expanding it over MeSH's
     structural tree would make a rule on Sulfonamides reach bendroflumethiazide
     (see db/014 and db/016).

Order matters:
  1. parse MED-RT (pure) -> the set of MeSH codes to resolve;
  2. resolve those codes, then walk their tree positions for the DESCENDANT CLOSURE,
     without which a rule on Epilepsy has nothing to expand into;
  3. upsert conditions, then clear this source's edges and contraindications, then
     write the DAG and the two relations;
  4. rebuild the open-question register LAST, before the commit.

WORKLIST NUMBERS, NOT SILENT DROPS -- four distinct losses, each counted separately
so they stay legible (spec §7).
"""
import hashlib
import logging
import uuid
from collections import Counter
from dataclasses import dataclass

import psycopg

from drugref import classes as class_writer
from drugref import conditions as condition_writer
from drugref import interactions, questions
from drugref.ingest import medrt, mesh_concepts

SOURCE = "MED-RT"
OBJECT_SOURCE = "MeSH"
CONDITION_PREDICATE = "CI_with"
PAIR_PREDICATE = "CI_ChemClass"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeshCiSummary:
    """What one slice-5b run did -- returned so a caller or test can assert on it.

    Conditions ACCUMULATE while edges and contraindications are REBUILT, so the two
    condition numbers are reported separately rather than as one ambiguous count.

    The four worklist numbers are reported, never swallowed:
      * unmatched_subject_rxcuis  -- the rule's subject is carried by no moiety
      * withheld_class_objects    -- CI_ChemClass objects that name a CLASS
      * unresolved_object_codes   -- M-codes MeSH no longer defines
      * non_mesh_objects          -- objects outside the MeSH namespace (MED-RT EXT)
    """
    conditions_in_release: int
    conditions_added: int
    condition_parent_edges: int
    condition_contraindications: int
    moiety_contraindications: int
    unmatched_subject_rxcuis: int
    withheld_class_objects: int
    unresolved_object_codes: int
    non_mesh_objects: int


def _checksum(*paths) -> str:
    """One checksum over every input file, in a fixed order, so the run's provenance
    changes if ANY input changes. Chunked: the MeSH files are large and slurping them
    would undo the streaming parser's bounded memory."""
    digest = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _resolve_object_moiety(record, unii_index, cas_index) -> uuid.UUID | None:
    """Resolve a MeSH record to a moiety: UNII-primary, CAS-fallback.

    The same rule mesh_run._resolve_moieties applies, reduced to a single answer
    because a contraindication names ONE partner drug. UNII is drugref's own identity
    key so it wins outright; CAS is tried only when no UNII resolved at all. Keys are
    set-valued (a record may carry several), and sorted iteration keeps the ingest
    reproducible.
    """
    for value in sorted(record.unii):
        for moiety_uuid in unii_index.get(value, ()):
            return moiety_uuid
    for value in sorted(record.cas):
        for moiety_uuid in cas_index.get(value, ()):
            return moiety_uuid
    return None


def ingest_mesh_contraindications(conn: psycopg.Connection, *, medrt_path,
                                  desc_path, supp_path,
                                  upstream_release: str) -> MeshCiSummary:
    """Ingest MED-RT's MeSH-keyed contraindications. Idempotent.

    TRANSACTION OWNERSHIP: as for medrt_run/mesh_run -- this owns `conn`'s
    transaction, commits on success, and rolls back before re-raising on failure so
    the caller never receives a connection stuck in the aborted-transaction state.
    """
    log.info("MeSH CI ingest starting (release=%s)", upstream_release)
    try:
        summary = _ingest(conn, medrt_path, desc_path, supp_path, upstream_release)
    except Exception:
        conn.rollback()
        log.exception("MeSH CI ingest failed (release=%s); rolled back",
                      upstream_release)
        raise
    log.info("MeSH CI ingest finished (release=%s): %s", upstream_release, summary)
    if summary.withheld_class_objects:
        # WARNING, not an error: withholding is the designed behaviour, but the
        # operator's next move is to look at those exact rows, so the number is put
        # where they will see it -- the same posture medrt_run takes for
        # unresolved_expansion_policy.
        log.warning("%d contraindication object(s) withheld pending review; see "
                    "drugref.gap_unresolved_ci_object", summary.withheld_class_objects)
    return summary


def _ingest(conn, medrt_path, desc_path, supp_path, upstream_release) -> MeshCiSummary:
    """The body of one slice-5b ingest (see ingest_mesh_contraindications)."""
    parsed = medrt.parse(medrt_path)
    assertions = parsed.mesh_contraindications

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, %s, %s) RETURNING ingest_run_id",
        (SOURCE, upstream_release,
         _checksum(medrt_path, desc_path, supp_path))).fetchone()[0]

    # 1. Resolve every referenced MeSH code, then take the DESCENDANT CLOSURE of the
    #    condition objects. The closure is what a rule expands INTO: the descendants
    #    are not themselves CI objects, so a registry scoped to referenced objects
    #    would leave the read path with nothing to find (spec §5.1).
    wanted = {a.mesh_code for a in assertions}
    records = mesh_concepts.resolve_concepts(desc_path, supp_path, wanted)
    unresolved_object_codes = len(wanted - set(records))

    condition_codes = {a.mesh_code for a in assertions
                       if a.relationship == CONDITION_PREDICATE}
    prefixes = frozenset(t for code in condition_codes if code in records
                         for t in records[code].tree_numbers)
    closure = {r.record_ui: r for r in mesh_concepts.descriptors_under(desc_path, prefixes)}
    for code in condition_codes:
        if code in records:
            closure[records[code].record_ui] = records[code]

    # 2. Conditions first: every edge and every contraindication references one.
    uuid_by_code: dict[str, uuid.UUID] = {}
    added = 0
    for record in closure.values():
        cu, is_new = condition_writer.upsert_condition(conn, record, run_id, OBJECT_SOURCE)
        uuid_by_code[record.record_ui] = cu
        added += is_new

    # 3. Clear this source's previous projection before writing this run's.
    condition_writer.clear_source_condition_edges(conn, OBJECT_SOURCE)
    interactions.clear_source_mesh_contraindications(conn, SOURCE)
    class_writer.clear_source_unmatched_ingredients(conn, SOURCE)

    # 4. The condition DAG.
    parent_edges = sum(
        condition_writer.add_condition_parent_edge(
            conn, uuid_by_code[e.child_code], uuid_by_code[e.parent_code], run_id)
        for e in mesh_concepts.parent_edges(closure.values())
        if e.child_code in uuid_by_code and e.parent_code in uuid_by_code)

    # 5. The two relations. Read every index ONCE -- a subject appears in many
    #    assertions, so a per-assertion lookup re-asks an answered question.
    rxcui_index = class_writer.moieties_by_rxcui(conn)
    unii_index = class_writer.moieties_by_scheme(conn, "UNII")
    cas_index = class_writer.moieties_by_scheme(conn, "CAS")

    condition_rows = pair_rows = 0
    unmatched_rxcuis: set[str] = set()
    withheld: Counter[str] = Counter()          # object code -> assertions withheld
    withheld_relationship: dict[str, str] = {}

    for a in assertions:
        subjects = rxcui_index.get(a.rxcui, ())
        if not subjects:
            unmatched_rxcuis.add(a.rxcui)       # counted, never dropped
            continue
        record = records.get(a.mesh_code)
        if record is None:
            continue                            # already counted above
        if a.relationship == CONDITION_PREDICATE:
            object_uuid = uuid_by_code.get(record.record_ui)
            if object_uuid is None:
                continue
            for subject in subjects:
                if interactions.add_condition_contraindication(
                        conn, subject, object_uuid, a.relationship, SOURCE, run_id):
                    condition_rows += 1
        else:                                    # CI_ChemClass
            object_moiety = _resolve_object_moiety(record, unii_index, cas_index)
            if object_moiety is None:
                # The CLASS arm: withheld pending curator review (db/014, db/016).
                withheld[record.record_ui] += 1
                withheld_relationship[record.record_ui] = a.relationship
                continue
            for subject in subjects:
                if subject == object_moiety:
                    continue                     # db/014 forbids a self-pair
                if interactions.add_moiety_contraindication(
                        conn, subject, object_moiety, a.relationship, SOURCE, run_id):
                    pair_rows += 1

    class_writer.add_unmatched_ingredients(conn, sorted(unmatched_rxcuis), run_id)
    interactions.record_unresolved_ci_objects(
        conn,
        [(SOURCE, withheld_relationship[code], OBJECT_SOURCE, code,
          _name_of(code, records, closure), count)
         for code, count in sorted(withheld.items())],
        run_id)

    # 6. Re-derive the open-question register LAST, for the reason every orchestrator
    #    does: this run rewrote projections the gap views read, and calling it earlier
    #    would read a half-demolished registry.
    questions.register_from_gaps(conn, run_id)

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() "
                 "WHERE ingest_run_id = %s", (run_id,))
    conn.commit()
    return MeshCiSummary(
        conditions_in_release=len(uuid_by_code), conditions_added=added,
        condition_parent_edges=parent_edges,
        condition_contraindications=condition_rows,
        moiety_contraindications=pair_rows,
        unmatched_subject_rxcuis=len(unmatched_rxcuis),
        withheld_class_objects=len(withheld),
        unresolved_object_codes=unresolved_object_codes,
        non_mesh_objects=parsed.non_mesh_ci_objects)


def _name_of(record_ui: str, records, closure) -> str | None:
    """The human-readable name of a withheld object.

    A worklist a human cannot read is a worklist nobody works -- the same reason
    classes.add_unmatched_ingredients accepts names.
    """
    if record_ui in closure:
        return closure[record_ui].name
    for record in records.values():
        if record.record_ui == record_ui:
            return record.name
    return None
```

- [ ] **Step 4: Run the tests**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_mesh_ci_run.py -v` → PASS (6)
Run: `DRUGREF_TEST_DSN='...' uv run pytest -q && ruff check .` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ingest/mesh_ci_run.py tests/test_mesh_ci_run.py
git commit -m "feat(ingest): orchestrate slice 5b's MeSH-keyed contraindications

Joins two authorities: MED-RT states the contraindication, MeSH defines its object.
CI_ChemClass is split at ingest -- an object that resolves to a moiety becomes an
exact drug-drug pair, one that does not is a chemical class and is WITHHELD as a
question rather than expanded over MeSH's structural tree.

The condition registry is built as the DESCENDANT CLOSURE of the referenced
conditions; without it a rule on Epilepsy has nothing to expand into. Four distinct
losses are counted separately so they stay legible."
```

---

### Task 11: Verify against the real releases, then document and open the PR

**Files:**
- Modify: `docs/HANDOVER.md`, `docs/ROADMAP.md`
- Create (scratch, not committed): a verification script

**Interfaces:** none — this task produces measurements and documentation.

- [ ] **Step 1: Run the full ingest against the real releases**

Against a scratch database (never the test one), run UNII → MED-RT → the 5b ingest, then record:

```sql
SELECT count(*) FROM drugref.condition;                                  -- expect ~5,190
SELECT count(*) FROM drugref.condition_parent;                           -- expect ~7,157
SELECT count(*) FROM drugref.moiety_condition_contraindication;          -- expect ~9,482
SELECT count(*) FROM drugref.moiety_contraindication;                    -- expect ~1,443
SELECT count(*) FROM drugref.gap_unresolved_ci_object;                   -- expect ~108
SELECT sum(ci_rule_count) FROM drugref.gap_unresolved_ci_object;         -- expect ~405
SELECT count(*) FROM drugref.condition_contraindication_expanded;
SELECT count(*) FROM drugref.condition_contraindication_expanded WHERE is_direct;
```

- [ ] **Step 2: Confirm the headline clinical case**

```sql
-- A drug contraindicated in Epilepsy must reach a patient coded with a descendant.
SELECT sm.display_name, c.name AS matched_condition, e.is_direct
FROM   drugref.condition_contraindication_expanded e
JOIN   drugref.substance_moiety sm ON sm.moiety_uuid = e.subject_moiety
JOIN   drugref.condition c ON c.condition_uuid = e.member_condition
WHERE  e.object_condition = (SELECT condition_uuid FROM drugref.condition
                             WHERE source_code = 'D004827')
ORDER  BY e.is_direct DESC, sm.display_name
LIMIT  20;

-- And the pregnancy case, which is why the table is not called drug_disease_*.
SELECT count(*) FROM drugref.moiety_condition_contraindication ci
JOIN   drugref.condition c ON c.condition_uuid = ci.object_condition_uuid
WHERE  c.source_code IN ('D011247', 'D007774');      -- Pregnancy, Lactation
```

- [ ] **Step 3: Record what was measured**

**Report the numbers you actually got, not the ones this plan predicts.** If they differ materially from
the spec's §4.5, say so explicitly and investigate before proceeding — a divergence means either the
implementation or the design measurement is wrong, and finding out which is the point.

Update `docs/HANDOVER.md`:
- move slice 5b from "Next candidates" to a "Current state, by layer" entry;
- record the measured numbers in a table;
- add `db/013`–`db/016` to the schema list;
- note the new modules under **Code**;
- record that slice 5b.2 (indications) is the next natural step and reuses the condition registry unchanged;
- state that the source-blind walk is still latent (the class arm was deferred).

Update `docs/ROADMAP.md`: mark **Slice 5b ✅ DONE**, with the measured yield and the deferred pieces.

Keep both under 500 lines; prune stale detail rather than appending.

- [ ] **Step 4: Full verification before the PR**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check .
```

Both must be clean. **Report the actual test count.**

- [ ] **Step 5: Commit and open the PR**

```bash
git add docs/HANDOVER.md docs/ROADMAP.md
git commit -m "docs: record slice 5b's measured outcome"
git push -u origin feat/slice-5b-mesh-contraindications
gh pr create --base main \
  --title "Slice 5b — MeSH-keyed contraindications (drug-condition and drug-drug)" \
  --body "..."
```

The PR body must state: the measured numbers; that the `CI_ChemClass` class arm is **withheld by design**
with the sulfonamide rationale; that no new source and no `NOTICE` change is involved; and that ROADMAP's
"5b ends the source-blind walk" claim was retracted. Link the spec and this plan.

---

## Self-Review

**Spec coverage:**

| spec section | task |
|---|---|
| §1 licence gate (no new source, crosswalk rejected) | Global Constraints; Task 3 docstring |
| §2 conditions are not classes | Task 2 (`db/013` comments) |
| §3 rebuildable projection, candidate tier | Tasks 2, 4, 5 (`COMMENT ON`) |
| §4.2 M-code resolution | Task 3 |
| §4.3 condition kinds (`tree_numbers`) | Task 2 |
| §4.4 `CI_ChemClass`'s two arms | Tasks 6, 10 |
| §5.1 registry = descendant closure | Tasks 3 (`descriptors_under`), 10 |
| §5.2 `condition_ci_axis`, no DEFAULT | Task 5 |
| §5.3 the two relations, `source` in PK, CHECK-not-FK | Task 5 |
| §5.4 `condition_subtree` + expanded view | Task 8 |
| §5.5 counted gap | Tasks 5, 9 |
| §6 ingest module layout | Tasks 3, 4, 6, 7, 10 |
| §7 four counted losses | Task 10 (`MeshCiSummary`) |
| §8 clinical-safety posture | Tasks 5, 8 (`COMMENT ON`) |
| §9 testing | every task; §9's eight behaviours map to Tasks 3, 8, 10 |
| §10 tensions | recorded in the migration/module comments they govern |
| §11 out of scope | not implemented, by design |

**Placeholder scan:** two literals are *intentionally* placeholders and each has an explicit step that
replaces them with a measured value — the frozen `condition_uuid` (Task 1 Step 4) and `EPILEPSY_CONCEPT`
(Task 3 Step 6). No other TBDs.

**Type consistency:** `MeshRecord` fields are used identically in Tasks 3, 4 and 10; `ConditionParentEdge`
uses `child_code`/`parent_code` throughout; `upsert_condition` returns `(uuid, bool)` in both its
definition (Task 4) and its callers (Task 10); `record_unresolved_ci_objects` takes 6-tuples in Task 7 and
is called with 6-tuples in Task 10.

**Known soft spots the implementer must resolve, flagged rather than hidden:**

1. **Task 6 Step 1** — the exact redaction predicate in `make_medrt_subset.py` was not read while writing
   this plan. Read it first; the change is minimal but its shape depends on how the redaction is expressed.
2. **Task 7** — `conftest.ingest_run_id` uses `source='PBS'`. The clear-source test as written leans on
   that; verify and adjust rather than assuming.
3. **Task 10** — the `seeded_moieties` fixture must be derived from whatever the regenerated
   `medrt_subset.xml` actually contains. It cannot be written before Task 6 completes.

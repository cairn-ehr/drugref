# Slice 2a — MED-RT Classification DAG + Membership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a MED-RT-seeded classification layer over drugref's active-moiety spine — class registry, subclass DAG, and many-to-many moiety↔class membership.

**Architecture:** Three new Postgres tables in schema `drugref` (`substance_class`, `class_parent`, `class_membership`), a pure XML parser for the MED-RT feed, a single DB-writer module, and a thin orchestrator. Class identity is immortal-by-determinism (`UUIDv5` on the MED-RT NUI); the DAG and membership edges are **rebuildable projections** (deleted and re-inserted per MED-RT ingest), deliberately outside slice 1's append-only trigger floor. Membership joins MED-RT's RxNorm-namespace ingredient concepts (code-in-source = RxCUI) to our moieties via the `RXNORM_IN` identity claims slice 1 already records.

**Tech Stack:** Python 3.12, `uv`, `psycopg` v3, PostgreSQL ≥ 18, `pytest`, `xml.etree.ElementTree` (stdlib — no new dependency).

**Spec:** [`docs/superpowers/specs/2026-07-23-drugref-slice-2a-medrt-classification-design.md`](../specs/2026-07-23-drugref-slice-2a-medrt-classification-design.md)

## Global Constraints

- **Licence:** all code AGPL-3.0. MED-RT is bundleable (US federal/VA work, UMLS restriction level 0). **Never ingest SNOMED CT- or MeSH-namespace endpoints** — parse only `MED-RT` and `RxNorm` namespace concepts.
- **TDD:** failing test first, then the minimal implementation. Never write implementation before a red test.
- **Pure functions in reusable modules**; DB-touching code is a thin imperative shell.
- **Inline documentation legible to a junior contributor is mandatory** — every module gets a docstring explaining *why*, not just *what*.
- **Files under 500 lines.**
- **All tests must pass before committing.** DB-gated tests need `DRUGREF_TEST_DSN`; run `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`.
- **No new runtime dependencies.**
- **Ingested MED-RT concept types:** exactly `MoA`, `PE`, `TC`, `PK`. **Ingested membership relationships:** exactly `has_MoA`, `has_PE`, `has_TC`, `has_PK`. There is no `has_EPC`; `EPC`/`EXT`/`HC` are out of scope.
- **No silent drops:** an ingredient whose RxCUI matches no moiety is counted and reported, never silently discarded.

## File Structure

| File | Responsibility |
|---|---|
| `db/002_schema_classes.sql` (create) | The three new tables + constraints. Applied automatically by `db.apply_migrations` (globs `db/*.sql` in filename order). |
| `src/drugref/ids.py` (modify) | Add `CLASS_NAMESPACE` + `mint_class_uuid()` beside the existing moiety minting. |
| `src/drugref/ingest/medrt.py` (create) | **Pure** MED-RT XML parser. No DB, no I/O beyond reading the given path. |
| `src/drugref/classes.py` (create) | The **only** module that writes the class tables (mirrors `claims.py`'s role for the identity tables). |
| `src/drugref/ingest/medrt_run.py` (create) | Orchestrator: open an `ingest_run`, upsert classes, rebuild edges, resolve RxCUI→moiety, return a summary. |
| `tests/fixtures/medrt_subset.xml` (create) | Small crafted MED-RT feed covering every acceptance case. |
| `tests/test_ids.py` (modify) | Class-UUID determinism + namespace separation. |
| `tests/test_schema_classes.py` (create) | Schema constraints + the rebuildable-vs-append-only distinction. |
| `tests/test_medrt_parser.py` (create) | Parser unit tests (no DB). |
| `tests/test_medrt_run.py` (create) | DB-gated acceptance matrix. |

---

### Task 1: Class UUID minting

**Files:**
- Modify: `src/drugref/ids.py`
- Test: `tests/test_ids.py`

**Interfaces:**
- Consumes: nothing (extends an existing module).
- Produces: `drugref.ids.CLASS_NAMESPACE: uuid.UUID`, `drugref.ids.mint_class_uuid(nui: str) -> uuid.UUID`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ids.py`:

```python
def test_mint_class_uuid_is_deterministic():
    """Same NUI -> same UUID, always, so independent instances agree."""
    assert ids.mint_class_uuid("N0000175722") == ids.mint_class_uuid("N0000175722")


def test_mint_class_uuid_normalises_whitespace_and_case():
    """NUIs arrive from XML text nodes; incidental whitespace/case must not fork identity."""
    assert ids.mint_class_uuid("  n0000175722  ") == ids.mint_class_uuid("N0000175722")


def test_class_and_moiety_namespaces_are_distinct():
    """Per-level namespaces guarantee a class and a moiety derived from the same
    source string can never collide (design §4)."""
    assert ids.CLASS_NAMESPACE != ids.MOIETY_NAMESPACE
    assert ids.mint_class_uuid("X") != ids.mint_moiety_uuid("X")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ids.py -v`
Expected: FAIL — `AttributeError: module 'drugref.ids' has no attribute 'mint_class_uuid'`

- [ ] **Step 3: Write minimal implementation**

In `src/drugref/ids.py`, add below the existing `MOIETY_NAMESPACE` line:

```python
CLASS_NAMESPACE = uuid.uuid5(_DRUGREF_ROOT, "class")
```

and append this function at the end of the file:

```python
def mint_class_uuid(nui: str) -> uuid.UUID:
    """Derive a classification class's immortal UUID from its MED-RT NUI.

    The NUI (an N-prefixed alphanumeric, e.g. "N0000175722") is MED-RT's own
    stable concept identifier -- its "code in source" -- so it is the natural key
    to derive from. Deterministic: same NUI -> same UUID, everywhere, with zero
    coordination between drugref instances.

    Unlike a moiety (which is pinned on first sight because a UNII correction
    would otherwise re-key it), a class UUID is a pure function of the NUI and is
    simply re-derived on every ingest. MED-RT NUIs are stable across releases, so
    this needs no pin table. Immortality across a NUI *change* is out of scope --
    the same caveat the moiety spine records for a UNII change.
    """
    key = f"MEDRT:{nui.strip().upper()}"
    return uuid.uuid5(CLASS_NAMESPACE, key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ids.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ids.py tests/test_ids.py
git commit -m "feat(ids): deterministic class UUID minting keyed on MED-RT NUI"
```

---

### Task 2: Class schema (the three new tables)

**Files:**
- Create: `db/002_schema_classes.sql`
- Create: `tests/test_schema_classes.py`

**Interfaces:**
- Consumes: `drugref.ingest_run`, `drugref.substance_moiety` (slice 1); `drugref.ids.mint_class_uuid` (Task 1).
- Produces: tables `drugref.substance_class(class_uuid, medrt_nui, medrt_code, class_name, concept_type, first_seen_ingest)`, `drugref.class_parent(child_class_uuid, parent_class_uuid, ingest_run)`, `drugref.class_membership(moiety_uuid, class_uuid, relationship, ingest_run)`.

> **Why no append-only trigger here:** slice 1's floor guards *identity*. MED-RT edges are rebuildable projections of an upstream authority — a re-ingest must be able to `DELETE` and re-insert them. Adding the floor would break reproducible rebuild (spec §3, tension A).

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema_classes.py`:

```python
# tests/test_schema_classes.py
"""Schema-level guarantees for the slice-2a classification tables.

These tests pin the two design decisions that are easy to regress: the CHECK
constraints that keep concept_type/relationship symmetric with the four ingested
MED-RT axes, and the deliberate ABSENCE of an append-only floor on the edge
tables (they are rebuildable projections and MUST stay deletable).
"""
import uuid
import pytest
import psycopg
from drugref import ids


def _run(conn, source="MED-RT"):
    """Create an ingest_run row and return its id (every row needs provenance)."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _class(conn, run_id, nui, name="Test Class [MoA]", cty="MoA"):
    """Insert a class row and return its deterministic uuid."""
    cu = ids.mint_class_uuid(nui)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, medrt_nui, medrt_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, %s, %s, %s, %s, %s)", (cu, nui, nui, name, cty, run_id))
    return cu


def test_concept_type_is_constrained_to_the_four_ingested_axes(conn):
    run_id = _run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        _class(conn, run_id, "N0000000001", cty="EPC")   # EPC is out of scope for 2a


def test_relationship_is_constrained_to_the_four_membership_associations(conn):
    run_id = _run(conn)
    cu = _class(conn, run_id, "N0000000002")
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
                 "VALUES (%s, 'testium', %s)", (m, run_id))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO drugref.class_membership "
                     "(moiety_uuid, class_uuid, relationship, ingest_run) VALUES (%s, %s, %s, %s)",
                     (m, cu, "may_treat", run_id))   # an overlay relationship, not membership


def test_a_class_may_have_many_parents(conn):
    """The classification structure is a DAG, not a tree (design §2)."""
    run_id = _run(conn)
    child = _class(conn, run_id, "N0000000003")
    p1 = _class(conn, run_id, "N0000000004")
    p2 = _class(conn, run_id, "N0000000005")
    for p in (p1, p2):
        conn.execute("INSERT INTO drugref.class_parent "
                     "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                     (child, p, run_id))
    n = conn.execute("SELECT count(*) FROM drugref.class_parent WHERE child_class_uuid = %s",
                     (child,)).fetchone()[0]
    assert n == 2


def test_a_class_may_not_be_its_own_parent(conn):
    run_id = _run(conn)
    cu = _class(conn, run_id, "N0000000006")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO drugref.class_parent "
                     "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                     (cu, cu, run_id))


def test_edge_tables_are_deletable_because_they_are_rebuildable_projections(conn):
    """The append-only floor guards identity, NOT feed projections. If a future
    change adds a no-DELETE trigger here, re-ingest of a new MED-RT release breaks."""
    run_id = _run(conn)
    child = _class(conn, run_id, "N0000000007")
    parent = _class(conn, run_id, "N0000000008")
    conn.execute("INSERT INTO drugref.class_parent "
                 "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                 (child, parent, run_id))
    conn.execute("DELETE FROM drugref.class_parent WHERE child_class_uuid = %s", (child,))
    n = conn.execute("SELECT count(*) FROM drugref.class_parent WHERE child_class_uuid = %s",
                     (child,)).fetchone()[0]
    assert n == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_schema_classes.py -v`
Expected: FAIL — `psycopg.errors.UndefinedTable: relation "drugref.substance_class" does not exist`

- [ ] **Step 3: Write minimal implementation**

Create `db/002_schema_classes.sql`:

```sql
-- db/002_schema_classes.sql
-- drugref global tier, slice 2a: the classification DAG and moiety<->class membership.
--
-- This is the SECOND of the two orthogonal structures (design §2): an
-- is-a-kind-of DAG, orthogonal to the is-made-of composition tree. Membership is
-- many-to-many -- a link table, never a parent FK on the moiety -- because a
-- moiety belongs to many classes on several axes at once.
--
-- IMPORTANT -- why there is no append-only trigger floor in this file:
-- slice 1's floor (db/001) guards substance IDENTITY, which is immortal. The
-- tables here are a REBUILDABLE PROJECTION of an upstream authority (MED-RT):
-- ingesting a newer release DELETES this source's prior edges and re-inserts
-- them. A no-DELETE trigger would make that impossible. Class *identity* is kept
-- stable a different way -- class_uuid is a pure UUIDv5 function of the MED-RT
-- NUI (src/drugref/ids.py), so a rebuild re-derives exactly the same UUIDs.

-- The class registry: one row per MED-RT pharmacologic class concept.
CREATE TABLE IF NOT EXISTS drugref.substance_class (
    class_uuid        uuid   PRIMARY KEY,          -- UUIDv5(CLASS_NAMESPACE, 'MEDRT:'||nui)
    medrt_nui         text   NOT NULL UNIQUE,      -- MED-RT's own stable id ("code in source")
    medrt_code        text,                        -- code as published (normally == the NUI)
    class_name        text   NOT NULL,             -- e.g. 'Cyclooxygenase Inhibitors [MoA]'
    concept_type      text   NOT NULL,             -- MED-RT CTY
    first_seen_ingest bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    -- Exactly the four axes that have a documented MED-RT ingredient->class
    -- association (has_MoA/has_PE/has_TC/has_PK). EPC/EXT/HC are out of scope for
    -- 2a: MED-RT exposes no ingredient->EPC association, and EPC's real linkage
    -- runs through SNOMED CT / MeSH mappings we are not licensed to bundle.
    CONSTRAINT substance_class_concept_type
        CHECK (concept_type IN ('MoA', 'PE', 'TC', 'PK'))
);

-- The subclass DAG. A class may have MANY parents, so this is an edge table and
-- not a parent column. Sourced from MED-RT 'Parent Of' relationships, followed
-- only where both endpoints are MED-RT concepts we ingest (never SNOMED/MeSH).
CREATE TABLE IF NOT EXISTS drugref.class_parent (
    child_class_uuid  uuid   NOT NULL REFERENCES drugref.substance_class(class_uuid),
    parent_class_uuid uuid   NOT NULL REFERENCES drugref.substance_class(class_uuid),
    ingest_run        bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (child_class_uuid, parent_class_uuid),
    CONSTRAINT class_parent_no_self_parent CHECK (child_class_uuid <> parent_class_uuid)
);

-- Many-to-many membership: which moieties belong to which classes, on which axis.
-- The axis (relationship) is recorded because class-level curation inherits along
-- it -- a consumer needs to ask "all MoA classes of moiety X", not just "classes".
CREATE TABLE IF NOT EXISTS drugref.class_membership (
    moiety_uuid  uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    class_uuid   uuid   NOT NULL REFERENCES drugref.substance_class(class_uuid),
    relationship text   NOT NULL,
    ingest_run   bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (moiety_uuid, class_uuid, relationship),
    -- Kept symmetric with substance_class.concept_type. Indication/contraindication
    -- relationships (may_treat, CI_with, ...) are NOT membership -- they belong to
    -- the curated interaction overlay in a later slice.
    CONSTRAINT class_membership_relationship
        CHECK (relationship IN ('has_MoA', 'has_PE', 'has_TC', 'has_PK'))
);

-- Query paths: "which moieties are in this class" and "walk the DAG upward".
CREATE INDEX IF NOT EXISTS class_membership_by_class
    ON drugref.class_membership (class_uuid);
CREATE INDEX IF NOT EXISTS class_parent_by_parent
    ON drugref.class_parent (parent_class_uuid);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_schema_classes.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the whole suite to confirm no regression**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q`
Expected: PASS, 43 tests (35 existing + 3 from Task 1 + 5 here)

- [ ] **Step 6: Commit**

```bash
git add db/002_schema_classes.sql tests/test_schema_classes.py
git commit -m "feat(db): classification DAG + membership schema"
```

---

### Task 3: MED-RT XML parser (pure)

**Files:**
- Create: `src/drugref/ingest/medrt.py`
- Create: `tests/fixtures/medrt_subset.xml`
- Create: `tests/test_medrt_parser.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces:
  - `drugref.ingest.medrt.ClassConcept(nui: str, name: str, concept_type: str)`
  - `drugref.ingest.medrt.ParentEdge(child_nui: str, parent_nui: str)`
  - `drugref.ingest.medrt.MembershipAssertion(rxcui: str, class_nui: str, relationship: str)`
  - `drugref.ingest.medrt.ParsedMedrt(classes: list[ClassConcept], parents: list[ParentEdge], memberships: list[MembershipAssertion])`
  - `drugref.ingest.medrt.parse(path) -> ParsedMedrt`

- [ ] **Step 1: Create the test fixture**

Create `tests/fixtures/medrt_subset.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- A hand-crafted MED-RT subset covering every slice-2a acceptance case.
     RxCUIs 161 (acetaminophen) and 17767 (amlodipine) match moieties in
     unii_subset.tsv; 999888 deliberately matches nothing. -->
<terminology>
  <!-- ingested class concepts (MED-RT namespace, ingested CTYs) -->
  <concept>
    <namespace>MED-RT</namespace>
    <name>Cellular or Molecular Interactions [MoA]</name>
    <code>N0000000001</code>
    <property><name>NUI</name><value>N0000000001</value></property>
    <property><name>CTY</name><value>MoA</value></property>
  </concept>
  <concept>
    <namespace>MED-RT</namespace>
    <name>Cyclooxygenase Inhibitors [MoA]</name>
    <code>N0000175722</code>
    <property><name>NUI</name><value>N0000175722</value></property>
    <property><name>CTY</name><value>MoA</value></property>
  </concept>
  <concept>
    <namespace>MED-RT</namespace>
    <name>Calcium Channel Blockers [MoA]</name>
    <code>N0000008836</code>
    <property><name>NUI</name><value>N0000008836</value></property>
    <property><name>CTY</name><value>MoA</value></property>
  </concept>
  <concept>
    <namespace>MED-RT</namespace>
    <name>Dual Action Agents [MoA]</name>
    <code>N0000029999</code>
    <property><name>NUI</name><value>N0000029999</value></property>
    <property><name>CTY</name><value>MoA</value></property>
  </concept>
  <concept>
    <namespace>MED-RT</namespace>
    <name>Decreased Prostaglandin Production [PE]</name>
    <code>N0000009999</code>
    <property><name>NUI</name><value>N0000009999</value></property>
    <property><name>CTY</name><value>PE</value></property>
  </concept>

  <!-- OUT OF SCOPE: an EPC concept must not be ingested (no has_EPC association) -->
  <concept>
    <namespace>MED-RT</namespace>
    <name>Nonsteroidal Anti-inflammatory Drug [EPC]</name>
    <code>N0000175439</code>
    <property><name>NUI</name><value>N0000175439</value></property>
    <property><name>CTY</name><value>EPC</value></property>
  </concept>

  <!-- OUT OF SCOPE: a SNOMED-namespace concept must never be ingested -->
  <concept>
    <namespace>SNOMED CT US Edition</namespace>
    <name>Substance with cyclooxygenase inhibitor mechanism of action</name>
    <code>372583002</code>
  </concept>

  <!-- hierarchy: 'Parent Of' points from a concept to its parent -->
  <association>
    <name>Parent Of</name>
    <from_namespace>MED-RT</from_namespace><from_code>N0000175722</from_code>
    <to_namespace>MED-RT</to_namespace><to_code>N0000000001</to_code>
  </association>
  <association>
    <name>Parent Of</name>
    <from_namespace>MED-RT</from_namespace><from_code>N0000029999</from_code>
    <to_namespace>MED-RT</to_namespace><to_code>N0000175722</to_code>
  </association>
  <!-- multi-parent: the same child also hangs off Calcium Channel Blockers -->
  <association>
    <name>Parent Of</name>
    <from_namespace>MED-RT</from_namespace><from_code>N0000029999</from_code>
    <to_namespace>MED-RT</to_namespace><to_code>N0000008836</to_code>
  </association>
  <!-- must be DROPPED: hierarchy mapped out into SNOMED -->
  <association>
    <name>Parent Of</name>
    <from_namespace>MED-RT</from_namespace><from_code>N0000175722</from_code>
    <to_namespace>SNOMED CT US Edition</to_namespace><to_code>372583002</to_code>
  </association>
  <!-- must be DROPPED: parent is an EPC we do not ingest -->
  <association>
    <name>Parent Of</name>
    <from_namespace>MED-RT</from_namespace><from_code>N0000008836</from_code>
    <to_namespace>MED-RT</to_namespace><to_code>N0000175439</to_code>
  </association>

  <!-- membership: ingredient (RxNorm, code == RxCUI) -> MED-RT class -->
  <association>
    <name>has_MoA</name>
    <from_namespace>RxNorm</from_namespace><from_code>161</from_code>
    <to_namespace>MED-RT</to_namespace><to_code>N0000175722</to_code>
  </association>
  <association>
    <name>has_PE</name>
    <from_namespace>RxNorm</from_namespace><from_code>161</from_code>
    <to_namespace>MED-RT</to_namespace><to_code>N0000009999</to_code>
  </association>
  <association>
    <name>has_MoA</name>
    <from_namespace>RxNorm</from_namespace><from_code>17767</from_code>
    <to_namespace>MED-RT</to_namespace><to_code>N0000008836</to_code>
  </association>
  <!-- an ingredient we do not carry: must be SKIPPED AND COUNTED, never silent -->
  <association>
    <name>has_MoA</name>
    <from_namespace>RxNorm</from_namespace><from_code>999888</from_code>
    <to_namespace>MED-RT</to_namespace><to_code>N0000175722</to_code>
  </association>
  <!-- must be DROPPED: an indication relationship is overlay data, not membership -->
  <association>
    <name>may_treat</name>
    <from_namespace>RxNorm</from_namespace><from_code>161</from_code>
    <to_namespace>MeSH</to_namespace><to_code>D010146</to_code>
  </association>
</terminology>
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_medrt_parser.py`:

```python
# tests/test_medrt_parser.py
"""Parser unit tests -- no database. These pin the licence-critical scoping rule
(only MED-RT and RxNorm namespaces are ever read) as executable behaviour."""
import pathlib
from drugref.ingest import medrt

FIX = pathlib.Path(__file__).parent / "fixtures" / "medrt_subset.xml"


def test_parses_only_the_four_ingested_concept_types():
    parsed = medrt.parse(FIX)
    by_nui = {c.nui: c for c in parsed.classes}
    assert set(by_nui) == {
        "N0000000001", "N0000175722", "N0000008836", "N0000029999", "N0000009999"}
    assert by_nui["N0000175722"].concept_type == "MoA"
    assert by_nui["N0000009999"].concept_type == "PE"
    assert by_nui["N0000175722"].name == "Cyclooxygenase Inhibitors [MoA]"


def test_excludes_epc_concepts():
    """EPC has no ingredient->class association in MED-RT, so 2a does not ingest it."""
    assert "N0000175439" not in {c.nui for c in medrt.parse(FIX).classes}


def test_excludes_snomed_namespace_concepts():
    """Licence-critical: SNOMED content must never be bundled (spec §1)."""
    assert "372583002" not in {c.nui for c in medrt.parse(FIX).classes}


def test_parent_edges_point_from_child_to_parent():
    parsed = medrt.parse(FIX)
    assert medrt.ParentEdge("N0000175722", "N0000000001") in parsed.parents


def test_a_class_can_have_two_parents():
    parents = {e.parent_nui for e in medrt.parse(FIX).parents if e.child_nui == "N0000029999"}
    assert parents == {"N0000175722", "N0000008836"}


def test_parent_edges_into_uningested_endpoints_are_dropped():
    """Both the SNOMED endpoint and the EPC endpoint must vanish."""
    parsed = medrt.parse(FIX)
    targets = {e.parent_nui for e in parsed.parents}
    assert "372583002" not in targets
    assert "N0000175439" not in targets


def test_membership_assertions_carry_rxcui_and_axis():
    parsed = medrt.parse(FIX)
    assert medrt.MembershipAssertion("161", "N0000175722", "has_MoA") in parsed.memberships
    assert medrt.MembershipAssertion("161", "N0000009999", "has_PE") in parsed.memberships
    assert medrt.MembershipAssertion("17767", "N0000008836", "has_MoA") in parsed.memberships


def test_unknown_ingredient_assertion_is_still_parsed():
    """The parser is pure: it reports the assertion; deciding it matches no moiety
    (and counting the skip) is the orchestrator's job."""
    assert medrt.MembershipAssertion("999888", "N0000175722", "has_MoA") in medrt.parse(FIX).memberships


def test_indication_relationships_are_not_membership():
    """may_treat is overlay data for a later slice, never a class membership."""
    assert all(m.relationship != "may_treat" for m in medrt.parse(FIX).memberships)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_medrt_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.ingest.medrt'`

- [ ] **Step 4: Write minimal implementation**

Create `src/drugref/ingest/medrt.py`:

```python
"""Parse the MED-RT release file into classification records.

MED-RT (Medication Reference Terminology, US Dept. of Veterans Affairs) is the
successor to NDF-RT. It publishes pharmacologic CLASS concepts and the asserted
relationships between them -- exactly the is-a-kind-of structure drugref needs.

What this module reads, and why only this:

* Class concepts live in the "MED-RT" namespace and are identified by their NUI
  (MED-RT's "code in source"). Each carries a CTY (concept type) property. We
  ingest the four types that have a documented ingredient->class association:
  MoA, PE, TC, PK. EPC/EXT/HC are deliberately skipped -- MED-RT exposes no
  has_EPC association, and EPC's real linkage runs through SNOMED CT / MeSH
  mappings (see the licence note below).

* Ingredient concepts live in the "RxNorm" namespace, where the code in source
  IS the RxCUI. That is the join key back to our moiety registry, because slice 1
  already records an RXNORM_IN identity claim for every moiety.

LICENCE-CRITICAL (design §1): MED-RT is built partly from SNOMED CT US Edition
and MeSH, and its hierarchy maps out into both. SNOMED is NOT redistributable
under our licence. This parser therefore reads ONLY the MED-RT and RxNorm
namespaces and silently drops every SNOMED/MeSH endpoint. That filtering is the
mechanism that keeps unlicensed content out of the bundle -- do not relax it.

This module is PURE: it reads a file and returns records. No database, no
network, no minting. The orchestrator (medrt_run.py) does all of that.

VERIFY-BEFORE-PRODUCTION (mirrors the same note in unii.py): the element and
property names below are taken from the MED-RT documentation (VA/VHA), not from
a parsed production release. Before running against the real feed, confirm
against an actual MED-RT XML file that: (a) concepts use <namespace>/<name>/
<code> with <property><name>CTY</name>, (b) associations use <name> plus
from_/to_ namespace and code elements, and (c) 'Parent Of' points from the child
TO its parent (if the real feed is the other way round, flip _parent_edge -- it
is deliberately a one-line change). All of these are isolated in the constants
and helpers below so a rename is a small, local edit.
"""
import pathlib
from dataclasses import dataclass
from xml.etree import ElementTree

# The only two namespaces we are licensed to read (see the module docstring).
MEDRT_NAMESPACE = "MED-RT"
RXNORM_NAMESPACE = "RxNorm"

# The MED-RT concept types (CTY) we ingest as classes -- exactly those with a
# documented ingredient->class association. Kept in lockstep with the CHECK
# constraint on drugref.substance_class.concept_type.
INGESTED_CONCEPT_TYPES = frozenset({"MoA", "PE", "TC", "PK"})

# Ingredient -> class assertions. Kept in lockstep with the CHECK constraint on
# drugref.class_membership.relationship. Indication/contraindication relations
# (may_treat, CI_with, ...) are NOT here: they are curated-overlay data.
MEMBERSHIP_RELATIONSHIPS = frozenset({"has_MoA", "has_PE", "has_TC", "has_PK"})

# The hierarchical relationship that builds the subclass DAG.
PARENT_RELATIONSHIP = "Parent Of"


@dataclass(frozen=True)
class ClassConcept:
    """One MED-RT pharmacologic class."""
    nui: str
    name: str
    concept_type: str


@dataclass(frozen=True)
class ParentEdge:
    """One DAG edge: `child_nui` is a kind of `parent_nui`."""
    child_nui: str
    parent_nui: str


@dataclass(frozen=True)
class MembershipAssertion:
    """MED-RT asserts that the ingredient with `rxcui` belongs to class
    `class_nui` on the axis named by `relationship`."""
    rxcui: str
    class_nui: str
    relationship: str


@dataclass(frozen=True)
class ParsedMedrt:
    """Everything one MED-RT file yields, already scoped to what we may ingest."""
    classes: list[ClassConcept]
    parents: list[ParentEdge]
    memberships: list[MembershipAssertion]


def _text(element, tag: str) -> str:
    """Return the stripped text of a child tag, or '' when absent.

    XML text nodes are None when a tag is empty or missing, so every read goes
    through here rather than risking None.strip() on a short record.
    """
    found = element.find(tag)
    return (found.text or "").strip() if found is not None else ""


def _properties(concept) -> dict[str, str]:
    """Collapse a concept's <property><name>/<value> pairs into a dict."""
    return {_text(p, "name"): _text(p, "value") for p in concept.findall("property")}


def _parse_concepts(root) -> list[ClassConcept]:
    """Keep only MED-RT-namespace concepts whose CTY is one we ingest."""
    classes = []
    for concept in root.findall("concept"):
        if _text(concept, "namespace") != MEDRT_NAMESPACE:
            continue                                    # SNOMED/MeSH/RxNorm: not a class
        props = _properties(concept)
        concept_type = props.get("CTY", "")
        if concept_type not in INGESTED_CONCEPT_TYPES:
            continue                                    # EPC/EXT/HC: out of scope for 2a
        # The NUI property is authoritative; <code> is the same value in practice,
        # so fall back to it if the property is absent.
        nui = props.get("NUI") or _text(concept, "code")
        classes.append(ClassConcept(nui=nui, name=_text(concept, "name"),
                                    concept_type=concept_type))
    return classes


def parse(path: str | pathlib.Path) -> ParsedMedrt:
    """Read one MED-RT XML file into the records slice 2a ingests."""
    root = ElementTree.parse(path).getroot()
    classes = _parse_concepts(root)
    known = {c.nui for c in classes}     # every edge endpoint must be an ingested class

    parents: list[ParentEdge] = []
    memberships: list[MembershipAssertion] = []
    for assoc in root.findall("association"):
        name = _text(assoc, "name")
        from_ns, from_code = _text(assoc, "from_namespace"), _text(assoc, "from_code")
        to_ns, to_code = _text(assoc, "to_namespace"), _text(assoc, "to_code")

        if name == PARENT_RELATIONSHIP:
            # Both ends must be classes we actually ingested. This single check is
            # what drops hierarchy mapped out into SNOMED/MeSH, and edges into the
            # EPC/EXT/HC types we skip.
            if (from_ns == MEDRT_NAMESPACE and to_ns == MEDRT_NAMESPACE
                    and from_code in known and to_code in known):
                parents.append(ParentEdge(child_nui=from_code, parent_nui=to_code))
        elif name in MEMBERSHIP_RELATIONSHIPS:
            # Membership always runs ingredient (RxNorm) -> class (MED-RT).
            if from_ns == RXNORM_NAMESPACE and to_ns == MEDRT_NAMESPACE and to_code in known:
                memberships.append(MembershipAssertion(
                    rxcui=from_code, class_nui=to_code, relationship=name))
        # Everything else (may_treat, CI_with, has_SC, Synonym Of, ...) is either
        # overlay data for a later slice or points at a namespace we may not read.
    return ParsedMedrt(classes=classes, parents=parents, memberships=memberships)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_medrt_parser.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/medrt.py tests/fixtures/medrt_subset.xml tests/test_medrt_parser.py
git commit -m "feat(ingest): MED-RT XML parser scoped to licensed namespaces"
```

---

### Task 4: Class table writers

**Files:**
- Create: `src/drugref/classes.py`
- Test: extend `tests/test_schema_classes.py`

**Interfaces:**
- Consumes: `drugref.ids.mint_class_uuid` (Task 1); tables from Task 2; `drugref.ingest.medrt.ClassConcept` (Task 3).
- Produces:
  - `drugref.classes.upsert_class(conn, concept: ClassConcept, ingest_run_id: int) -> uuid.UUID`
  - `drugref.classes.clear_source_edges(conn, source: str) -> None`
  - `drugref.classes.add_parent_edge(conn, child_uuid, parent_uuid, ingest_run_id) -> bool`
  - `drugref.classes.add_membership(conn, moiety_uuid, class_uuid, relationship, ingest_run_id) -> bool`
  - `drugref.classes.resolve_moiety_by_rxcui(conn, rxcui: str) -> uuid.UUID | None`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schema_classes.py`:

```python
from drugref import classes
from drugref.ingest.medrt import ClassConcept


def test_upsert_class_is_idempotent_and_refreshes_the_name(conn):
    """Re-ingest must not duplicate, and an upstream rename should land -- but
    first_seen_ingest records when we FIRST saw it and must not move."""
    r1, r2 = _run(conn), _run(conn)
    c = ClassConcept(nui="N0000123456", name="Old Name [MoA]", concept_type="MoA")
    cu = classes.upsert_class(conn, c, r1)
    again = classes.upsert_class(conn, ClassConcept("N0000123456", "New Name [MoA]", "MoA"), r2)
    assert cu == again == ids.mint_class_uuid("N0000123456")
    name, first = conn.execute(
        "SELECT class_name, first_seen_ingest FROM drugref.substance_class WHERE class_uuid = %s",
        (cu,)).fetchone()
    assert name == "New Name [MoA]"
    assert first == r1


def test_resolve_moiety_by_rxcui_uses_the_rxnorm_in_claim(conn):
    """The membership join key: MED-RT ingredients carry an RxCUI, and slice 1
    already stores one as an RXNORM_IN claim per moiety."""
    run_id = _run(conn, source="UNII")
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
                 "VALUES (%s, 'testium', %s)", (m, run_id))
    conn.execute("INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
                 "VALUES (%s, 'RXNORM_IN', '4242', %s)", (m, run_id))
    assert classes.resolve_moiety_by_rxcui(conn, "4242") == m
    assert classes.resolve_moiety_by_rxcui(conn, "no-such-rxcui") is None


def test_resolve_moiety_ignores_superseded_claims(conn):
    """A corrected-away RxCUI must not keep dragging in stale memberships."""
    run_id = _run(conn, source="UNII")
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
                 "VALUES (%s, 'testium', %s)", (m, run_id))
    old = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'RXNORM_IN', '5555', %s) RETURNING identity_claim_id", (m, run_id)).fetchone()[0]
    new = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, 'RXNORM_IN', '6666', %s) RETURNING identity_claim_id", (m, run_id)).fetchone()[0]
    conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s WHERE identity_claim_id = %s",
                 (new, old))
    assert classes.resolve_moiety_by_rxcui(conn, "5555") is None
    assert classes.resolve_moiety_by_rxcui(conn, "6666") == m


def test_clear_source_edges_removes_only_that_sources_rows(conn):
    """Rebuild semantics: a new MED-RT release replaces MED-RT edges and leaves
    any other source's edges alone."""
    medrt_run, other_run = _run(conn, source="MED-RT"), _run(conn, source="SOMETHING-ELSE")
    child = _class(conn, medrt_run, "N0000222222")
    parent = _class(conn, medrt_run, "N0000333333")
    classes.add_parent_edge(conn, child, parent, medrt_run)
    conn.execute("INSERT INTO drugref.class_parent "
                 "(child_class_uuid, parent_class_uuid, ingest_run) VALUES (%s, %s, %s)",
                 (parent, child, other_run))
    classes.clear_source_edges(conn, "MED-RT")
    remaining = conn.execute("SELECT ingest_run FROM drugref.class_parent").fetchall()
    assert remaining == [(other_run,)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_schema_classes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.classes'`

- [ ] **Step 3: Write minimal implementation**

Create `src/drugref/classes.py`:

```python
"""The ONLY module that writes the classification tables.

It mirrors claims.py's role for the identity tables -- concentrating writes in
one reviewable place -- but the discipline it enforces is DIFFERENT, and the
difference matters:

* claims.py guards an APPEND-ONLY spine. Substance identity is immortal; the
  database floor rejects UPDATE/DELETE outright.
* This module manages a REBUILDABLE PROJECTION. MED-RT is an upstream authority
  we re-ingest wholesale; its edges are meant to be dropped and rebuilt, so
  clear_source_edges() deliberately DELETEs. What stays stable across a rebuild
  is class IDENTITY -- class_uuid is a pure function of the MED-RT NUI, so the
  same class always comes back with the same UUID.
"""
import uuid

import psycopg

from drugref import ids
from drugref.ingest.medrt import ClassConcept


def upsert_class(conn: psycopg.Connection, concept: ClassConcept,
                 ingest_run_id: int) -> uuid.UUID:
    """Register a class (or refresh its cached name) and return its UUID.

    The UUID is derived, not looked up, so this is safe to call on every ingest.
    ON CONFLICT refreshes the name/type caches -- upstream may rename a class --
    while first_seen_ingest is left untouched because it records when drugref
    FIRST saw the class, not when it was last confirmed.
    """
    class_uuid = ids.mint_class_uuid(concept.nui)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, medrt_nui, medrt_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (class_uuid) DO UPDATE SET "
        "  class_name = EXCLUDED.class_name, concept_type = EXCLUDED.concept_type",
        (class_uuid, concept.nui, concept.nui, concept.name,
         concept.concept_type, ingest_run_id))
    return class_uuid


def clear_source_edges(conn: psycopg.Connection, source: str) -> None:
    """Drop every DAG and membership edge contributed by `source`.

    Called at the start of a re-ingest so a new upstream release fully REPLACES
    the previous one (a class that lost a parent upstream must lose it here too,
    which an insert-only merge could never express). Scoped by source so an
    unrelated future feed's edges survive. Class rows themselves are NOT deleted:
    their UUIDs are immortal and re-derived identically on the way back in.
    """
    for table in ("class_membership", "class_parent"):
        conn.execute(
            f"DELETE FROM drugref.{table} WHERE ingest_run IN "
            "(SELECT ingest_run_id FROM drugref.ingest_run WHERE source = %s)",
            (source,))


def add_parent_edge(conn: psycopg.Connection, child_uuid: uuid.UUID,
                    parent_uuid: uuid.UUID, ingest_run_id: int) -> bool:
    """Add one subclass edge. Returns True if a new row was inserted.

    ON CONFLICT DO NOTHING keeps a single file that repeats an edge harmless.
    """
    cur = conn.execute(
        "INSERT INTO drugref.class_parent (child_class_uuid, parent_class_uuid, ingest_run) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (child_uuid, parent_uuid, ingest_run_id))
    return cur.rowcount == 1


def add_membership(conn: psycopg.Connection, moiety_uuid: uuid.UUID,
                   class_uuid: uuid.UUID, relationship: str,
                   ingest_run_id: int) -> bool:
    """Link a moiety to a class on one axis. Returns True if newly inserted."""
    cur = conn.execute(
        "INSERT INTO drugref.class_membership "
        "(moiety_uuid, class_uuid, relationship, ingest_run) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (moiety_uuid, class_uuid, relationship, ingest_run_id))
    return cur.rowcount == 1


def resolve_moiety_by_rxcui(conn: psycopg.Connection, rxcui: str) -> uuid.UUID | None:
    """Find the moiety carrying this RxCUI, or None if we do not have it.

    This is the membership join key. MED-RT states class membership against
    RxNorm ingredient concepts whose code IS the RxCUI, and slice 1 already
    attached an RXNORM_IN claim to every moiety -- so the two meet here with no
    new bridge data.

    Superseded claims are excluded so a corrected-away RxCUI cannot resurrect a
    stale membership (the same rule chebi.py applies to InChIKey lookups).
    Returns the first match: an RxCUI identifies a single ingredient upstream, so
    a second hit would be an upstream data error rather than a case to model.
    """
    row = conn.execute(
        "SELECT moiety_uuid FROM drugref.identity_claim "
        "WHERE scheme = 'RXNORM_IN' AND value = %s AND superseded_by IS NULL "
        "LIMIT 1", (rxcui,)).fetchone()
    return row[0] if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_schema_classes.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/drugref/classes.py tests/test_schema_classes.py
git commit -m "feat(classes): class registry writers + RxCUI->moiety resolution"
```

---

### Task 5: MED-RT ingest orchestrator

**Files:**
- Create: `src/drugref/ingest/medrt_run.py`
- Create: `tests/test_medrt_run.py`

**Interfaces:**
- Consumes: `drugref.ingest.medrt.parse` (Task 3); all of `drugref.classes` (Task 4).
- Produces: `drugref.ingest.medrt_run.MedrtSummary(classes, parent_edges, memberships, unmatched_rxcuis)` and `drugref.ingest.medrt_run.ingest_medrt(conn, *, medrt_path, upstream_release: str) -> MedrtSummary`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_medrt_run.py`:

```python
# tests/test_medrt_run.py
"""DB-gated acceptance matrix for the slice-2a MED-RT ingest."""
import pathlib
import pytest
from drugref import ids
from drugref.ingest import run, medrt_run

MEDRT_FIX = pathlib.Path(__file__).parent / "fixtures" / "medrt_subset.xml"
UNII_FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")
XW = DATA / "usan_inn_crosswalk.tsv"
AL = DATA / "legacy_allowlist.tsv"


@pytest.fixture(autouse=True)
def _clean(conn):
    # Both orchestrators commit internally, so the conn fixture's rollback cannot
    # isolate these tests; truncate first so counts are order-independent.
    conn.execute("TRUNCATE drugref.class_membership, drugref.class_parent, "
                 "drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


@pytest.fixture
def seeded(conn):
    """Slice-2a membership needs the slice-1 moiety registry to join against."""
    run.ingest_unii(conn, unii_path=UNII_FIX, crosswalk_path=XW,
                    allowlist_path=AL, upstream_release="2026-07")
    return conn


def _ingest(conn, release="2026.03.02"):
    return medrt_run.ingest_medrt(conn, medrt_path=MEDRT_FIX, upstream_release=release)


def test_registers_the_ingested_classes(seeded):
    summary = _ingest(seeded)
    assert summary.classes == 5           # 4 MoA + 1 PE; the EPC and SNOMED concepts excluded
    types = dict(seeded.execute(
        "SELECT concept_type, count(*) FROM drugref.substance_class GROUP BY 1").fetchall())
    assert types == {"MoA": 4, "PE": 1}


def test_builds_the_dag_including_a_multi_parent_class(seeded):
    _ingest(seeded)
    child = ids.mint_class_uuid("N0000029999")
    parents = {r[0] for r in seeded.execute(
        "SELECT parent_class_uuid FROM drugref.class_parent WHERE child_class_uuid = %s",
        (child,)).fetchall()}
    assert parents == {ids.mint_class_uuid("N0000175722"), ids.mint_class_uuid("N0000008836")}


def test_drops_edges_into_unlicensed_or_uningested_endpoints(seeded):
    """The SNOMED endpoint and the EPC endpoint must leave no trace."""
    summary = _ingest(seeded)
    assert summary.parent_edges == 3      # 2 for the multi-parent child + 1 root edge
    nuis = {r[0] for r in seeded.execute("SELECT medrt_nui FROM drugref.substance_class").fetchall()}
    assert "372583002" not in nuis and "N0000175439" not in nuis


def test_links_moieties_to_classes_on_the_right_axis(seeded):
    _ingest(seeded)
    paracetamol = ids.mint_moiety_uuid("362O9ITL9D")
    rows = {(r[0], r[1]) for r in seeded.execute(
        "SELECT class_uuid, relationship FROM drugref.class_membership WHERE moiety_uuid = %s",
        (paracetamol,)).fetchall()}
    assert rows == {(ids.mint_class_uuid("N0000175722"), "has_MoA"),
                    (ids.mint_class_uuid("N0000009999"), "has_PE")}


def test_unmatched_ingredient_is_skipped_and_counted_not_silently_dropped(seeded):
    summary = _ingest(seeded)
    assert summary.memberships == 3       # rxcui 161 x2, 17767 x1
    assert summary.unmatched_rxcuis == 1  # rxcui 999888 -- surfaced as a worklist number


def test_reingest_rebuilds_edges_without_duplicating(seeded):
    """A second release REPLACES the previous edges; UUIDs are unchanged."""
    first = _ingest(seeded)
    second = _ingest(seeded, release="2026.04.06")
    assert (second.classes, second.parent_edges, second.memberships) == \
           (first.classes, first.parent_edges, first.memberships)
    counts = seeded.execute(
        "SELECT (SELECT count(*) FROM drugref.substance_class), "
        "       (SELECT count(*) FROM drugref.class_parent), "
        "       (SELECT count(*) FROM drugref.class_membership)").fetchone()
    assert counts == (5, 3, 3)


def test_ingest_run_provenance_is_recorded(seeded):
    _ingest(seeded)
    source, release = seeded.execute(
        "SELECT source, upstream_release FROM drugref.ingest_run "
        "WHERE source = 'MED-RT' ORDER BY ingest_run_id DESC LIMIT 1").fetchone()
    assert (source, release) == ("MED-RT", "2026.03.02")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_medrt_run.py -v`
Expected: FAIL — `ImportError: cannot import name 'medrt_run' from 'drugref.ingest'`

- [ ] **Step 3: Write minimal implementation**

Create `src/drugref/ingest/medrt_run.py`:

```python
"""Orchestrate one MED-RT ingest: parse -> upsert classes -> rebuild edges.

The shape mirrors ingest/run.py (the slice-1 UNII orchestrator): open an
ingest_run for provenance, do the work, stamp finished_at, commit. The one
structural difference is the REBUILD step -- MED-RT is a rebuildable projection,
so a new release replaces the previous release's edges wholesale rather than
merging into them (see classes.clear_source_edges for why).

Order matters here:
 1. classes first, because every edge references a class row;
 2. then clear the old edges, so a class that lost a parent upstream loses it here;
 3. then insert the new edges.
"""
import hashlib
import pathlib
from dataclasses import dataclass

import psycopg

from drugref import classes as class_writer
from drugref.ingest import medrt

SOURCE = "MED-RT"


@dataclass(frozen=True)
class MedrtSummary:
    """What one run did -- returned so a caller (or a test) can assert on it.

    `unmatched_rxcuis` is the worklist number: MED-RT asserted a class for an
    ingredient we do not carry (usually because the moiety gate excluded it).
    That is reported, never silently swallowed.
    """
    classes: int
    parent_edges: int
    memberships: int
    unmatched_rxcuis: int


def _checksum(path: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def ingest_medrt(conn: psycopg.Connection, *, medrt_path,
                 upstream_release: str) -> MedrtSummary:
    """Ingest one MED-RT release file. Idempotent: re-running rebuilds to the
    same state with the same class UUIDs."""
    parsed = medrt.parse(medrt_path)

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, %s, %s) RETURNING ingest_run_id",
        (SOURCE, upstream_release, _checksum(medrt_path))).fetchone()[0]

    # 1. Classes. Their UUIDs are derived, so this both registers new classes and
    #    gives us the lookup every edge below needs.
    uuid_by_nui = {c.nui: class_writer.upsert_class(conn, c, run_id) for c in parsed.classes}

    # 2. Drop the previous release's edges before writing this one's.
    class_writer.clear_source_edges(conn, SOURCE)

    # 3. The DAG. The parser already guaranteed both endpoints are ingested classes.
    parent_edges = sum(
        class_writer.add_parent_edge(conn, uuid_by_nui[e.child_nui],
                                     uuid_by_nui[e.parent_nui], run_id)
        for e in parsed.parents)

    # 4. Membership, joined through the RXNORM_IN claims slice 1 recorded.
    memberships = 0
    unmatched: set[str] = set()
    for assertion in parsed.memberships:
        moiety_uuid = class_writer.resolve_moiety_by_rxcui(conn, assertion.rxcui)
        if moiety_uuid is None:
            # Not an error: MED-RT classifies far more ingredients than pass our
            # moiety gate. Counted (by distinct RxCUI) so the yield is auditable.
            unmatched.add(assertion.rxcui)
            continue
        if class_writer.add_membership(conn, moiety_uuid, uuid_by_nui[assertion.class_nui],
                                       assertion.relationship, run_id):
            memberships += 1

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s",
                 (run_id,))
    conn.commit()
    return MedrtSummary(classes=len(uuid_by_nui), parent_edges=parent_edges,
                        memberships=memberships, unmatched_rxcuis=len(unmatched))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_medrt_run.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the whole suite**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q`
Expected: PASS, 63 tests (35 existing + 3 + 5 + 9 + 4 + 7)

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/medrt_run.py tests/test_medrt_run.py
git commit -m "feat(ingest): MED-RT orchestrator — classes, DAG rebuild, membership join"
```

---

### Task 6: Attribution + documentation refresh

**Files:**
- Modify: `NOTICE`
- Modify: `docs/HANDOVER.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: everything above. Produces: no code.

- [ ] **Step 1: Add MED-RT attribution to NOTICE**

Append to the "Seed data attributions:" list in `NOTICE` (after the RxNorm line):

```
- MED-RT (Medication Reference Terminology) — U.S. Department of Veterans Affairs /
  Veterans Health Administration, distributed via NCI Enterprise Vocabulary Services
  (a work of the U.S. federal government; public domain in the US, UMLS restriction
  level 0). drugref ingests only MED-RT-namespace class concepts and RxNorm-namespace
  ingredient references; no SNOMED CT content is ingested or redistributed.
```

- [ ] **Step 2: Update ROADMAP.md**

Mark "Slice 2 — Classification DAG + membership" as **2a DONE** (MED-RT), and restate the remaining slice-2b work (MeSH Pharmacological Actions, needing a UNII→MeSH bridge). Note the deferred `EPC`/`EXT`/`HC` concept types.

- [ ] **Step 3: Update HANDOVER.md**

Rewrite the `⇒ NEXT` section to point at slice 2b (or slice 3, per the user's choice), record the new test count, and add any follow-ups discovered during implementation. Keep the file under 500 lines and prune stale slice-1 detail.

- [ ] **Step 4: Run the whole suite one last time**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q`
Expected: PASS, 63 tests

- [ ] **Step 5: Commit**

```bash
git add NOTICE docs/HANDOVER.md docs/ROADMAP.md
git commit -m "docs(slice-2a): MED-RT attribution; refresh HANDOVER and ROADMAP"
```

---

## Follow-ups to file as GitHub issues (do not fix inline)

- **Verify the MED-RT XML element names against a real release** — the parser's element/property names come from the MED-RT documentation, not a parsed production file. Confirm `<concept>`/`<property>`/`<association>` shapes and, critically, the **direction of `Parent Of`** (the plan assumes from=child, to=parent). Isolated in `medrt.py` constants for a one-line fix.
- **Re-confirm the MED-RT licence deed against the live NLM source-release doc** (unreachable at design time; NLM returned HTTP 502).
- **Batch-commit large feeds** — the existing slice-1 follow-up applies here too: a real MED-RT release is far larger than the fixture and is currently ingested in one transaction.
- **`EPC`/`EXT`/`HC` concept types** — decide how (and whether) to source ingredient→EPC membership without traversing SNOMED/MeSH mappings.

# Slice 8a — PBS Localisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Attach a minimal Australian PBS product layer to drugref's global moiety spine via a name-only
bridge, as a node-local plug-in, and report a measured match rate plus a queryable residual.

**Architecture:** Three rebuildable-projection tables (`db/009`) holding PBS items, their name-resolved
links to moieties, and the ingredients that failed to resolve. A **pure** parser (`ingest/pbs.py`) streams
`items.csv` and normalises ingredient names with no DB access; a **single writer** (`local.py`) owns all
three tables; an **orchestrator** (`ingest/pbs_run.py`) owns the transaction. This mirrors the existing
MED-RT and MeSH feeds exactly.

**Tech Stack:** Python 3.12, `uv`, `psycopg` v3, PostgreSQL ≥ 18, `pytest`, `ruff`. Stdlib `csv` only — no
new dependency.

**Spec:** [`docs/superpowers/specs/2026-07-25-drugref-slice-8a-pbs-localisation-design.md`](../specs/2026-07-25-drugref-slice-8a-pbs-localisation-design.md).
Read §1 (licence gate) and §5.3 (the measurements) before starting.

## Global Constraints

- **Licence (rule 7, spec §1):** drugref ships code only. **Never commit PBS data**, never bundle or
  redistribute it, and never let an **ATC** or **AMT/SNOMED** value enter any drugref table. `downloads/` is
  gitignored. Do **not** change `NOTICE` — this slice redistributes nothing. Tracked as
  [#25](https://github.com/cairn-ehr/drugref/issues/25).
- **Read only `tables_as_csv/items.csv`.** Never open `atc-codes.csv`, `item-atc-relationships.csv` or
  `amt-items.csv` (spec §6).
- **TDD (rule 2):** write the failing test first, run it, watch it fail, then implement. Every task below is
  ordered that way; do not reorder.
- **Rule 3:** inline documentation a junior contributor can follow is mandatory. Match the surrounding
  house style — module docstrings explain *why*, not just *what* (see `classes.py`, `ids.py`).
- **Rule 4:** keep files under ~500 lines.
- **Rule 5:** no silent tech debt — fix, or file a GitHub issue.
- **Rule 6:** all tests must pass before committing.
- **Migrations are immutable once applied** (`drugref.schema_migration` ledger). `db/009` is a **new** file;
  never edit an applied one.
- **DB-gated tests** need `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres'`.
- **CSV encoding is `utf-8-sig`** (the files carry a BOM). **`'null'` is a literal empty-value sentinel.**

## File Structure

| File | Responsibility |
|---|---|
| `db/009_schema_local_tier.sql` | **Create.** Three tables + `ingest_run.source` CHECK widened for `'PBS'`. |
| `src/drugref/ids.py` | **Modify.** Add `LOCAL_PRODUCT_NAMESPACE`, `mint_local_product_uuid`, public `normalise_name`. |
| `src/drugref/ingest/gate.py` | **Modify.** `_norm` delegates to `ids.normalise_name`. |
| `src/drugref/data/salt_suffixes.tsv` | **Create.** The closed salt/hydrate list. |
| `src/drugref/ingest/pbs.py` | **Create.** Pure parser + name normalisation. No DB. |
| `src/drugref/local.py` | **Create.** The only writer for the three local-tier tables. |
| `src/drugref/ingest/pbs_run.py` | **Create.** Orchestrator; owns the transaction; returns `PbsSummary`. |
| `tests/fixtures/make_pbs_subset.py` | **Create.** Re-runnable extractor from a real release. |
| `tests/fixtures/pbs_items_subset.csv` | **Create.** The committed extract (drug names only, no ATC/AMT). |
| `tests/test_pbs_parser.py` | **Create.** Pure tests, no DB. |
| `tests/test_pbs_run.py` | **Create.** DB-gated acceptance matrix. |

**Task order:** 1 (schema) → 2 (ids) → 3 (parser: names) → 4 (parser: rows) → 5 (fixture) → 6 (writer) →
7 (orchestrator) → 8 (quarantine + rebuild acceptance) → 9 (measure + docs).

---

### Task 1: Schema — the three local-tier tables (`db/009`)

**Files:**
- Create: `db/009_schema_local_tier.sql`
- Test: `tests/test_local_schema.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: tables `drugref.local_product`, `drugref.local_product_moiety`,
  `drugref.local_unmatched_ingredient`; `ingest_run.source` accepts `'PBS'`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_local_schema.py
"""db/009: the local (jurisdiction-specific) tier's three tables.

These are REBUILDABLE PROJECTIONS, deliberately outside slice 1's append-only
floor: PBS re-lists monthly and a de-listed item must be able to disappear,
which an insert-only merge can never express (spec section 3).
"""
import pytest


def test_local_product_round_trips(conn, ingest_run_id):
    """A product row inserts and reads back under its deterministic UUID."""
    from drugref import ids
    product_uuid = ids.mint_local_product_uuid("AU", "PBS", "10001J_14023")
    conn.execute(
        "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, source, "
        "source_code, pbs_code, brand_name, drug_name, form_strength, program_code, "
        "benefit_type_code, ingest_run) "
        "VALUES (%s, 'AU', 'PBS', '10001J_14023', '10001J', 'Xifaxan', 'Rifaximin', "
        "'Tablet 550 mg', 'GE', 'A', %s)",
        (product_uuid, ingest_run_id))
    row = conn.execute(
        "SELECT drug_name, benefit_type_code FROM drugref.local_product "
        "WHERE local_product_uuid = %s", (product_uuid,)).fetchone()
    assert row == ("Rifaximin", "A")


def test_ingest_run_accepts_pbs_source(conn):
    """db/005 CHECK-constrains ingest_run.source; 009 must widen it for PBS."""
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('PBS', '2026-07-01', 'x') RETURNING ingest_run_id").fetchone()[0]
    assert run_id > 0


def test_local_product_rejects_unknown_jurisdiction(conn, ingest_run_id):
    """jurisdiction is CHECK-constrained, like every other rebuild-scoping key."""
    from drugref import ids
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, source, "
            "source_code, ingest_run) VALUES (%s, 'XX', 'PBS', 'c', %s)",
            (ids.mint_local_product_uuid("XX", "PBS", "c"), ingest_run_id))


def test_bridge_requires_a_real_moiety(conn, ingest_run_id):
    """The bridge is FK'd to substance_moiety: it can never point at a ghost."""
    import uuid
    from drugref import ids
    product_uuid = ids.mint_local_product_uuid("AU", "PBS", "solo")
    conn.execute(
        "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, source, "
        "source_code, ingest_run) VALUES (%s, 'AU', 'PBS', 'solo', %s)",
        (product_uuid, ingest_run_id))
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO drugref.local_product_moiety (local_product_uuid, moiety_uuid, "
            "component_name, match_method, ingest_run) VALUES (%s, %s, 'x', 'exact', %s)",
            (product_uuid, uuid.uuid4(), ingest_run_id))


def test_match_method_vocabulary_is_closed(conn, ingest_run_id, a_moiety):
    """match_method separates the salt-strip heuristic from exact matches, so a
    consumer can discard it (spec 5.1). A CHECK keeps the vocabulary honest."""
    from drugref import ids
    product_uuid = ids.mint_local_product_uuid("AU", "PBS", "mm")
    conn.execute(
        "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, source, "
        "source_code, ingest_run) VALUES (%s, 'AU', 'PBS', 'mm', %s)",
        (product_uuid, ingest_run_id))
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO drugref.local_product_moiety (local_product_uuid, moiety_uuid, "
            "component_name, match_method, ingest_run) VALUES (%s, %s, 'x', 'guessed', %s)",
            (product_uuid, a_moiety, ingest_run_id))
```

Add these fixtures to `tests/conftest.py` if `ingest_run_id` / `a_moiety` do not already exist — check
first and reuse the existing ones if they do:

```python
@pytest.fixture
def ingest_run_id(conn):
    """A committed-in-transaction ingest_run row for provenance FKs."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('PBS', 'test', 'test') RETURNING ingest_run_id").fetchone()[0]


@pytest.fixture
def a_moiety(conn, ingest_run_id):
    """One registered moiety, for tests that need a live FK target."""
    from drugref import ids
    moiety_uuid = ids.mint_moiety_uuid("TESTUNII01")
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, 'testdrug', %s) ON CONFLICT DO NOTHING",
        (moiety_uuid, ingest_run_id))
    return moiety_uuid
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_local_schema.py -v`
Expected: FAIL — `relation "drugref.local_product" does not exist`.

- [ ] **Step 3: Write the migration**

```sql
-- db/009_schema_local_tier.sql
-- drugref LOCAL tier, slice 8a: Australian PBS products and their bridge to the
-- global moiety spine.
--
-- LICENCE (spec section 1) -- read before extending this file:
-- PBS data is NOT bundled or redistributed by drugref. A node operator ingests it
-- into their own database under whatever terms bind them. Critically, ATC codes
-- (WHO, NonCommercial + NoDerivatives) and AMT/SNOMED CT-AU concept IDs (NCTS
-- affiliate licence) may NEVER enter drugref. That is why there is no atc_code or
-- amt_code column below and why there must never be one: the schema is the last
-- line of a defence whose first line is simply not reading those files.
--
-- WHY NO APPEND-ONLY FLOOR HERE (contrast db/001):
-- slice 1's floor guards substance IDENTITY, which is immortal. These tables are a
-- REBUILDABLE PROJECTION of a monthly upstream release: re-ingesting DELETEs this
-- source's rows and re-inserts, so an item DE-LISTED by PBS disappears here too. A
-- no-DELETE trigger would make that impossible. Stability comes from determinism
-- instead -- local_product_uuid is a pure function of (jurisdiction, source, code),
-- so every surviving product returns with the UUID it had before (src/drugref/ids.py).

-- The ingest_run.source CHECK (db/005) is the key every per-source rebuild joins
-- through, so a new authority must be admitted explicitly rather than by accident.
ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_source_check;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_source_check
    CHECK (source IN ('UNII', 'CHEBI', 'MED-RT', 'MeSH', 'PBS'));

-- One row per PBS item INSTANCE. Keyed on li_item_id rather than the PBS Item
-- Code because a PBS code is a PRESCRIBING RULE (drug x form x max-quantity x
-- repeats x restriction x program) that covers MANY BRANDS -- measured on the
-- 2026-07 release: 14,840 item rows across only 6,945 codes. Keying on the code
-- would collapse every brand of a molecule into one row.
CREATE TABLE IF NOT EXISTS drugref.local_product (
    local_product_uuid uuid   PRIMARY KEY,   -- uuid5(LOCAL_PRODUCT_NAMESPACE,'AU:PBS:'||source_code)
    jurisdiction       text   NOT NULL,
    source             text   NOT NULL,
    source_code        text   NOT NULL,      -- PBS li_item_id (unique per row upstream)
    pbs_code           text,                 -- the recognisable Item Code, an ATTRIBUTE not the key
    brand_name         text,
    drug_name          text,                 -- li_drug_name (or drug_name): the licence-clean name
    form_strength      text,
    program_code       text,
    benefit_type_code  text,                 -- U/R/S/A: the restriction LEVEL only, never its text
    ingest_run         bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    CONSTRAINT local_product_jurisdiction CHECK (jurisdiction IN ('AU')),
    CONSTRAINT local_product_source       CHECK (source IN ('PBS')),
    CONSTRAINT local_product_benefit_type
        CHECK (benefit_type_code IS NULL OR benefit_type_code IN ('U', 'R', 'S', 'A')),
    CONSTRAINT local_product_natural_key UNIQUE (jurisdiction, source, source_code)
);

-- The name-resolved bridge to the global spine. An EDGE TABLE, not a column on
-- local_product, for two reasons: a combination product resolves to SEVERAL
-- moieties, and slices 3/4 (salt, clinical drug) do not exist yet -- so when they
-- land, the attachment point can be refined WITHOUT re-keying any product.
CREATE TABLE IF NOT EXISTS drugref.local_product_moiety (
    local_product_uuid uuid   NOT NULL REFERENCES drugref.local_product(local_product_uuid),
    moiety_uuid        uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    component_name     text   NOT NULL,      -- the ingredient name that resolved
    match_method       text   NOT NULL,      -- how it resolved; see the CHECK
    ingest_run         bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (local_product_uuid, moiety_uuid, component_name),
    -- 'salt_stripped' marks a row that matched only after a trailing salt/hydrate
    -- token was removed -- a HEURISTIC standing in for slice 3's GSRS active-moiety
    -- relationships. Recording it per row is what lets a consumer ignore the
    -- heuristic entirely instead of having to trust it (spec 5.1).
    CONSTRAINT local_product_moiety_match_method
        CHECK (match_method IN ('exact', 'salt_stripped'))
);

-- Coverage made QUERYABLE. An ingredient PBS lists that no moiety carries is not
-- an error and not a silent drop: many are foods, dressings and extemporaneous
-- chemicals that slice 1's gate excludes BY DESIGN. Persisting them is what turns
-- "how much do we not know" into a number (spec 7), mirroring
-- ingest_unmatched_ingredient.
CREATE TABLE IF NOT EXISTS drugref.local_unmatched_ingredient (
    ingest_run     bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    jurisdiction   text   NOT NULL,
    source         text   NOT NULL,
    source_code    text   NOT NULL,          -- which PBS item raised it
    component_name text   NOT NULL           -- the name that matched no moiety
);

-- The rebuild-delete path joins ingest_run by source, so index what it filters on.
CREATE INDEX IF NOT EXISTS local_product_by_run
    ON drugref.local_product (ingest_run);
CREATE INDEX IF NOT EXISTS local_product_moiety_by_run
    ON drugref.local_product_moiety (ingest_run);
CREATE INDEX IF NOT EXISTS local_product_moiety_by_moiety
    ON drugref.local_product_moiety (moiety_uuid);
CREATE INDEX IF NOT EXISTS local_unmatched_by_run
    ON drugref.local_unmatched_ingredient (ingest_run);

COMMENT ON TABLE drugref.local_product IS
    'PBS item instances (AU local tier). Rebuildable projection, NOT bundled data: '
    'drugref ships the ingest code; a node operator supplies the PBS release.';
COMMENT ON TABLE drugref.local_product_moiety IS
    'Name-resolved bridge from a local product to global moieties. match_method '
    'separates exact matches from the salt-strip heuristic (a slice-3 stand-in).';
COMMENT ON TABLE drugref.local_unmatched_ingredient IS
    'Ingredient names PBS lists that no moiety carries. Expected, not failure: '
    'foods/dressings/excipients are outside the slice-1 moiety gate by design.';
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_local_schema.py -v`
Expected: PASS (5 tests). If `ingest_run_source_check` is not the real constraint name, find it with
`\d drugref.ingest_run` and correct the `DROP CONSTRAINT` line — do **not** leave the old constraint in place.

- [ ] **Step 5: Commit**

```bash
git add db/009_schema_local_tier.sql tests/test_local_schema.py tests/conftest.py
git commit -m "feat(db): add the local-tier schema (db/009) for PBS products

Three rebuildable-projection tables plus a widened ingest_run.source CHECK.
Keyed on li_item_id, not pbs_code: a PBS code is a prescribing rule covering
many brands (14,840 rows across 6,945 codes upstream), so keying on it would
collapse every brand of a molecule into one row.

No atc_code or amt_code column exists, or may ever exist: ATC (WHO, NC+ND) and
AMT/SNOMED (NCTS) may not enter drugref."
```

---

### Task 2: Deterministic product identity + a shared name fold (`ids.py`)

**Files:**
- Modify: `src/drugref/ids.py`
- Modify: `src/drugref/ingest/gate.py:20-22`
- Test: `tests/test_ids.py` (extend; create if absent)

**Interfaces:**
- Consumes: Task 1's schema.
- Produces: `ids.normalise_name(name: str) -> str`;
  `ids.mint_local_product_uuid(jurisdiction: str, source: str, code: str) -> uuid.UUID`;
  `ids.LOCAL_PRODUCT_NAMESPACE`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ids.py
def test_normalise_name_folds_case_and_whitespace():
    """PBS names are Title-case (1,085 of 1,086 upstream) while INN claims are
    stored lower-case, so this fold is what lets the two ever meet."""
    from drugref import ids
    assert ids.normalise_name("  Rifaximin  ") == "rifaximin"
    assert ids.normalise_name("Alendronic   acid") == "alendronic acid"


def test_gate_norm_delegates_to_ids():
    """gate._norm had a second consumer as of slice 8a. It now delegates rather
    than duplicating, so the bridge's fold and the INN claim's fold cannot drift."""
    from drugref import ids
    from drugref.ingest import gate
    assert gate._norm("  Foo  Bar ") == ids.normalise_name("  Foo  Bar ")


def test_local_product_uuid_is_deterministic():
    """Re-derived on every rebuild, so a surviving product keeps its UUID."""
    from drugref import ids
    first = ids.mint_local_product_uuid("AU", "PBS", "10001J_14023")
    assert first == ids.mint_local_product_uuid("au", " pbs ", "10001J_14023")


def test_local_product_uuid_separates_jurisdiction_and_source():
    """Same code in two jurisdictions must never collide."""
    from drugref import ids
    assert (ids.mint_local_product_uuid("AU", "PBS", "1")
            != ids.mint_local_product_uuid("XX", "PBS", "1"))


def test_local_product_uuid_has_its_own_namespace():
    """Per-level namespaces stop a product and a moiety derived from the same
    string from ever colliding (the rule ids.py already applies to classes)."""
    from drugref import ids
    assert ids.LOCAL_PRODUCT_NAMESPACE not in (
        ids.MOIETY_NAMESPACE, ids.CLASS_NAMESPACE, ids.QUESTION_NAMESPACE)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ids.py -v -k "normalise or local_product or gate_norm"`
Expected: FAIL — `AttributeError: module 'drugref.ids' has no attribute 'normalise_name'`.

- [ ] **Step 3: Implement**

In `src/drugref/ids.py`, add the namespace beside the existing ones:

```python
LOCAL_PRODUCT_NAMESPACE = uuid.uuid5(_DRUGREF_ROOT, "local_product")
```

and append:

```python
def normalise_name(name: str) -> str:
    """The one fold applied to any human-readable substance name.

    Strip, lower-case, collapse internal whitespace. It lives here beside
    canonical_source and canonical_claim_value because it is the same KIND of
    thing: the single spelling two independently-produced strings must agree on
    before they can be compared.

    Two consumers depend on that agreement. The INN identity claim is stored
    lower-case (it is a display label, so _UPPERCASE_SCHEMES deliberately excludes
    it), and PBS publishes Title-case drug names -- 1,085 of 1,086 distinct names
    in the 2026-07 release. If either side folded differently, the local-tier
    bridge would silently match nothing at all, which is the failure mode
    canonical_source exists to prevent for authority names.
    """
    return " ".join(name.strip().lower().split())


def mint_local_product_uuid(jurisdiction: str, source: str, code: str) -> uuid.UUID:
    """Derive a local-tier product's UUID from (jurisdiction, source, code).

    Deterministic and RE-DERIVED on every ingest, never pinned -- the same
    discipline as mint_class_uuid and deliberately unlike mint_moiety_uuid. That
    is what lets the local tier be dropped and rebuilt monthly while every
    surviving product comes back with exactly the UUID it had before.

    Jurisdiction and source are part of the key, not decoration: a second
    jurisdiction's identically-numbered item would otherwise collapse onto the
    same row. `code` is the upstream item-instance id (PBS li_item_id), which is
    unique per row upstream -- unlike the PBS Item Code, which covers many brands.
    """
    key = f"{jurisdiction.strip().upper()}:{source.strip().upper()}:{code.strip()}"
    return uuid.uuid5(LOCAL_PRODUCT_NAMESPACE, key)
```

Then in `src/drugref/ingest/gate.py` replace the body of `_norm` so there is one implementation:

```python
def _norm(name: str) -> str:
    """Case/space-fold a name for lookup and comparison.

    Delegates to ids.normalise_name, which slice 8a promoted to the shared module
    when the local-tier bridge became a second consumer. Kept as a private alias
    so this module's existing call sites read unchanged.
    """
    return ids.normalise_name(name)
```

adding `from drugref import ids` to `gate.py`'s imports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ids.py tests/test_gate.py -v`
Expected: PASS, including the pre-existing gate tests (the delegation must not change behaviour).

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ids.py src/drugref/ingest/gate.py tests/test_ids.py
git commit -m "feat(ids): add local-product identity and promote the name fold

mint_local_product_uuid re-derives on every rebuild (like a class UUID, unlike a
moiety UUID), so a monthly rebuild keeps every surviving product's identity.

_norm moves from ingest/gate.py to ids.normalise_name now that the PBS bridge is
a second consumer: reaching into another module's private name would leave the
bridge's fold and the stored INN fold free to diverge silently."
```

---

### Task 3: The pure name resolver (`ingest/pbs.py`, part 1)

The crux of the slice. Every rule below is measured against the real release (spec §5.3) — do not
"improve" them from intuition.

**Files:**
- Create: `src/drugref/ingest/pbs.py`
- Create: `src/drugref/data/salt_suffixes.tsv`
- Test: `tests/test_pbs_parser.py`

**Interfaces:**
- Consumes: `ids.normalise_name`.
- Produces: `pbs.split_components(name: str) -> list[str]`;
  `pbs.strip_salt(name: str, suffixes: frozenset[str]) -> str | None`;
  `pbs.load_salt_suffixes(path) -> frozenset[str]`; `pbs.SALT_SUFFIX_PATH`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pbs_parser.py
"""Pure tests for the PBS parser: no database, no network.

Every expectation here is drawn from the real 2026-07 release (spec 5.3), not
from the PBS data dictionary and not from intuition. Where a case looks odd, it
is odd because the upstream data is.
"""
from drugref.ingest import pbs


def test_splits_on_with():
    """' with ' is PBS's primary combination separator: 208 distinct names."""
    assert pbs.split_components("Abacavir with lamivudine") == ["abacavir", "lamivudine"]


def test_splits_on_and():
    """' and ' is the second: 88 distinct names."""
    assert pbs.split_components("Abiraterone and methylprednisolone") == [
        "abiraterone", "methylprednisolone"]


def test_does_not_split_on_plus():
    """' + ' appears in ZERO of the 1,086 distinct names upstream. A plus sign is
    therefore part of a name, never a separator, and splitting on it would shred
    real names for no gain."""
    assert pbs.split_components("Vitamin B+C complex") == ["vitamin b+c complex"]


def test_splits_multi_component_chains():
    """Real names chain commas and ' and ': 'Allantoin with sulfur, phenol, coal
    tar solution and menthol'."""
    assert pbs.split_components(
        "Allantoin with sulfur, phenol, coal tar solution and menthol") == [
        "allantoin", "sulfur", "phenol", "coal tar solution", "menthol"]


def test_strips_parenthetical_annotations():
    """'Acetic Acid (33 per cent)' must match the INN 'acetic acid'."""
    assert pbs.split_components("Acetic Acid (33 per cent)") == ["acetic acid"]
    assert pbs.split_components("Acetone (use as additive only)") == ["acetone"]


def test_folds_case():
    """PBS is Title-case; INN claims are lower-case."""
    assert pbs.split_components("Rifaximin") == ["rifaximin"]


def test_strip_salt_removes_a_trailing_salt_token():
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("alfuzosin hydrochloride", suffixes) == "alfuzosin"
    assert pbs.strip_salt("metoprolol succinate", suffixes) == "metoprolol"


def test_strip_salt_never_strips_acid():
    """THE TRAP. 'acid' is the last word of real INNs -- alendronic acid, folic
    acid, folinic acid. Stripping it destroys correct matches, so it is not on
    the list and this test pins that."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert "acid" not in suffixes
    assert pbs.strip_salt("alendronic acid", suffixes) is None
    assert pbs.strip_salt("folic acid", suffixes) is None


def test_strip_salt_returns_none_when_nothing_to_strip():
    """None means 'no fallback to try', distinct from a stripped empty string."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("rifaximin", suffixes) is None


def test_strip_salt_never_strips_the_whole_name():
    """'Docusate sodium' strips fine, but a name that IS only a salt token would
    otherwise strip to nothing and then match everything."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("sodium", suffixes) is None


def test_dimethyl_fumarate_is_a_regression_case():
    """'Dimethyl fumarate' and 'Diroximel fumarate' are INNs IN THEIR OWN RIGHT,
    even though 'fumarate' is a genuine salt token elsewhere ('Ferrous
    fumarate'). This is why the caller must try the UNSTRIPPED name FIRST and
    only fall back to the stripped one -- strip_salt itself is deliberately
    dumb, so the ordering is the safeguard (spec 5.3)."""
    suffixes = pbs.load_salt_suffixes(pbs.SALT_SUFFIX_PATH)
    assert pbs.strip_salt("dimethyl fumarate", suffixes) == "dimethyl"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_pbs_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.ingest.pbs'`.

- [ ] **Step 3: Implement the resolver and the salt list**

Create `src/drugref/data/salt_suffixes.tsv` — **closed and curated**, one token per line:

```
# Trailing salt-former and hydration tokens stripped ONLY as a fallback, when the
# unstripped name matches no moiety. A stand-in for slice 3 (GSRS active-moiety ->
# salt relationships), not a replacement for it.
#
# DELIBERATELY ABSENT: "acid". It is the last word of real INNs (alendronic acid,
# folic acid, folinic acid), so stripping it would destroy correct matches.
#
# Measured on the 2026-07 release: only ~20 distinct names carry a genuine trailing
# salt token, and two of them ("Dimethyl fumarate", "Diroximel fumarate") are INNs
# in their own right -- which is why the CALLER must try the unstripped name first.
hydrochloride
hydrobromide
sulfate
sulphate
fumarate
maleate
tartrate
succinate
mesilate
mesylate
besilate
besylate
citrate
acetate
decanoate
propionate
valerate
phosphate
nitrate
gluconate
lactate
sodium
potassium
calcium
magnesium
monohydrate
dihydrate
trihydrate
anhydrous
```

Create `src/drugref/ingest/pbs.py`:

```python
# src/drugref/ingest/pbs.py
"""PURE parsing and name normalisation for the Australian PBS feed (slice 8a).

No database access and no network: this module turns CSV rows into dataclasses
and drug names into candidate ingredient names, and nothing else. The
orchestrator (ingest/pbs_run.py) owns the transaction and is the only writer,
the same split MED-RT and MeSH already use.

LICENCE (spec section 1): this module reads ONLY tables_as_csv/items.csv. It must
never read atc-codes.csv, item-atc-relationships.csv or amt-items.csv -- ATC
(WHO, NonCommercial + NoDerivatives) and AMT/SNOMED CT-AU (NCTS affiliate
licence) may not enter drugref at all. items.csv carries neither, so the
quarantine costs nothing.

WHY THE RULES BELOW LOOK ARBITRARY: they are measured against the real 2026-07
release, not taken from the data dictionary (spec 5.3). The separator set, the
'null' sentinel and the absent "acid" suffix each encode a fact about the actual
file that intuition gets wrong.
"""
import csv
import pathlib
import re
from dataclasses import dataclass

from drugref import ids

SALT_SUFFIX_PATH = pathlib.Path(__file__).parent.parent / "data" / "salt_suffixes.tsv"

# PBS's empty-value sentinel is the LITERAL FOUR-LETTER STRING "null", used in 44
# of items.csv's 75 columns. Untreated, drugref would earnestly register a drug
# named "null" -- 159 rows carry li_drug_name = 'null'.
_NULL_SENTINEL = "null"

# Combination separators, in the forms actually present upstream. " + " is NOT
# here: it appears in zero of the 1,086 distinct names, so treating it as a
# separator could only ever shred a real name.
_SEPARATORS = re.compile(r"\s+with\s+|\s+and\s+|,\s*", re.IGNORECASE)

# A trailing " (...)" annotation: "Acetic Acid (33 per cent)", "Acetone (use as
# additive only)". The same annotation strip mesh.registry_keys() performs.
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")


def is_missing(value: str | None) -> bool:
    """True if an upstream field is absent -- blank OR the literal 'null'."""
    return value is None or value.strip() == "" or value.strip() == _NULL_SENTINEL


def load_salt_suffixes(path: str | pathlib.Path = SALT_SUFFIX_PATH) -> frozenset[str]:
    """Load the closed salt/hydrate suffix list, ignoring comments and blanks."""
    with open(path, encoding="utf-8") as fh:
        return frozenset(
            line.strip().lower() for line in fh
            if line.strip() and not line.startswith("#"))


def split_components(name: str) -> list[str]:
    """Split a PBS drug name into its normalised component ingredient names.

    Order-preserving and duplicate-free. Returns [] for a missing name.

    A combination product resolves each component INDEPENDENTLY, so a name where
    one component is a known moiety and another is not can be recorded honestly:
    the known one bridges, the unknown one is counted. Rounding such a product up
    to "matched" or down to "unmatched" would both be lies.
    """
    if is_missing(name):
        return []
    cleaned = _PARENTHETICAL.sub("", name)
    seen: list[str] = []
    for part in _SEPARATORS.split(cleaned):
        component = ids.normalise_name(part)
        if component and component not in seen:
            seen.append(component)
    return seen


def strip_salt(name: str, suffixes: frozenset[str]) -> str | None:
    """Drop ONE trailing salt/hydrate token, or None if there is nothing to drop.

    Deliberately dumb, and deliberately NOT safe to use on its own: "Dimethyl
    fumarate" is an INN in its own right, so calling this eagerly would turn a
    correct match into a miss. The SAFEGUARD IS THE CALLER'S ORDERING -- try the
    unstripped name first, come here only when it misses (see pbs_run.resolve).

    Returns None rather than the unchanged name so the caller cannot accidentally
    retry an identical lookup, and never strips a name down to nothing (a bare
    "sodium" would otherwise become "" and match indiscriminately).
    """
    words = name.split()
    if len(words) < 2 or words[-1].lower() not in suffixes:
        return None
    return " ".join(words[:-1])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pbs_parser.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ingest/pbs.py src/drugref/data/salt_suffixes.tsv tests/test_pbs_parser.py
git commit -m "feat(pbs): add the pure name resolver for the PBS bridge

Splits combinations on ' with ' / ' and ' / ',' -- the separators actually
present upstream. ' + ' is excluded: it appears in zero of the 1,086 distinct
names, so splitting on it could only shred a real name.

'acid' is deliberately absent from the salt list: it is the last word of real
INNs (alendronic, folic, folinic). And strip_salt is fallback-only by contract,
because 'Dimethyl fumarate' is itself an INN -- pinned as a regression test."
```

---

### Task 4: Streaming the CSV into `PbsItem` (`ingest/pbs.py`, part 2)

**Files:**
- Modify: `src/drugref/ingest/pbs.py`
- Test: `tests/test_pbs_parser.py` (extend)

**Interfaces:**
- Consumes: Task 3's `is_missing`.
- Produces: `PbsItem` (frozen dataclass: `source_code`, `pbs_code`, `brand_name`, `drug_name`,
  `form_strength`, `program_code`, `benefit_type_code`); `pbs.parse_items(path) -> Iterator[PbsItem]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_pbs_parser.py
import pathlib
import textwrap


def _write_csv(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    """Write a minimal items.csv. The BOM is deliberate: the real files have one."""
    path = tmp_path / "items.csv"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8-sig")
    return path


def test_parse_items_reads_the_allow_listed_columns(tmp_path):
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code
        10001J_14023,10001J,Xifaxan,Rifaximin,Rifaximin,Tablet 550 mg,GE,A
        """)
    items = list(pbs.parse_items(path))
    assert len(items) == 1
    assert items[0].source_code == "10001J_14023"
    assert items[0].drug_name == "Rifaximin"
    assert items[0].benefit_type_code == "A"


def test_parse_items_falls_back_to_drug_name_when_li_drug_name_is_null(tmp_path):
    """159 rows upstream carry li_drug_name='null' -- every one with a usable
    drug_name. Without this fallback they would all be lost."""
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code
        X_1,X,null,null,Aspirin,null,GE,U
        """)
    items = list(pbs.parse_items(path))
    assert items[0].drug_name == "Aspirin"


def test_parse_items_maps_the_null_sentinel_to_none(tmp_path):
    """The literal string 'null' must never reach the database as a value."""
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code
        X_1,X,null,Aspirin,Aspirin,null,GE,U
        """)
    items = list(pbs.parse_items(path))
    assert items[0].brand_name is None
    assert items[0].form_strength is None


def test_parse_items_ignores_encumbered_columns(tmp_path):
    """QUARANTINE (spec 6). ATC and AMT are absent from items.csv upstream; if a
    future release adds them, the fixed allow-list must still refuse to read
    them. PbsItem has nowhere to put such a value."""
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code,atc_code,amt_code
        X_1,X,B,Aspirin,Aspirin,Tab,GE,U,N02BA01,12345678
        """)
    item = next(pbs.parse_items(path))
    assert "N02BA01" not in str(item)
    assert "12345678" not in str(item)


def test_parse_items_skips_rows_with_no_identity(tmp_path):
    """A row with no li_item_id cannot be keyed, so it is refused rather than
    given a degenerate UUID -- the same discipline gate.has_identity_key applies
    to the identity spine."""
    path = _write_csv(tmp_path, """
        li_item_id,pbs_code,brand_name,li_drug_name,drug_name,li_form,program_code,benefit_type_code
        ,X,B,Aspirin,Aspirin,Tab,GE,U
        X_2,Y,B,Ibuprofen,Ibuprofen,Tab,GE,U
        """)
    items = list(pbs.parse_items(path))
    assert [i.source_code for i in items] == ["X_2"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_pbs_parser.py -v -k parse_items`
Expected: FAIL — `AttributeError: module 'drugref.ingest.pbs' has no attribute 'parse_items'`.

- [ ] **Step 3: Implement**

Append to `src/drugref/ingest/pbs.py`:

```python
@dataclass(frozen=True)
class PbsItem:
    """One PBS item instance, reduced to the licence-clean fields drugref keeps.

    This dataclass IS the quarantine boundary (spec section 6): it has no field
    for an ATC code or an AMT/SNOMED concept id, so no amount of downstream
    carelessness can put one in the database. items.csv carries neither today --
    they live in separate files the ingest never opens -- and the fixed allow-list
    in parse_items keeps that true if a future release changes its mind.
    """
    source_code: str              # li_item_id -- unique per row upstream
    pbs_code: str | None          # the Item Code: an attribute, NOT the key
    brand_name: str | None
    drug_name: str | None         # li_drug_name, falling back to drug_name
    form_strength: str | None
    program_code: str | None
    benefit_type_code: str | None  # U/R/S/A


def _clean(row: dict[str, str], column: str) -> str | None:
    """Read one column, mapping blank and the 'null' sentinel to None."""
    value = row.get(column)
    return None if is_missing(value) else value.strip()


def parse_items(path: str | pathlib.Path):
    """Stream tables_as_csv/items.csv, yielding one PbsItem per usable row.

    A GENERATOR, so the 8.3 MB file never lands in memory at once -- the same
    streaming discipline mesh.py applies, and the reason the production-ingest
    follow-up (#7) does not apply to this feed.

    Opened with utf-8-sig because the real files carry a BOM: read as plain utf-8,
    the first column name arrives as '\\ufeffli_item_id' and every lookup of it
    silently misses, yielding rows that are entirely empty.

    Rows with no li_item_id are SKIPPED, not defaulted: the product UUID derives
    from that value, so an empty one would mint a single shared UUID that every
    such row collapses onto (the failure gate.has_identity_key exists to prevent
    on the identity spine).
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            source_code = _clean(row, "li_item_id")
            if not source_code:
                continue
            # li_drug_name is the legally-determined name and the better key;
            # drug_name is the Medicinal Product Pack name and covers the 159
            # rows where the former is the 'null' sentinel.
            drug_name = _clean(row, "li_drug_name") or _clean(row, "drug_name")
            yield PbsItem(
                source_code=source_code,
                pbs_code=_clean(row, "pbs_code"),
                brand_name=_clean(row, "brand_name"),
                drug_name=drug_name,
                form_strength=_clean(row, "li_form") or _clean(row, "schedule_form"),
                program_code=_clean(row, "program_code"),
                benefit_type_code=_clean(row, "benefit_type_code"),
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pbs_parser.py -v`
Expected: PASS (16 tests).

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ingest/pbs.py tests/test_pbs_parser.py
git commit -m "feat(pbs): stream items.csv into PbsItem

PbsItem is the quarantine boundary: no field exists for an ATC or AMT value, so
carelessness downstream cannot put one in the database.

Handles two upstream traps: the literal 'null' sentinel (44 of 75 columns; 159
rows have li_drug_name='null', all with a usable drug_name fallback) and the
UTF-8 BOM (read as plain utf-8, every first-column lookup misses silently)."
```

---

### Task 5: The committed fixture and its extractor

**Files:**
- Create: `tests/fixtures/make_pbs_subset.py`
- Create: `tests/fixtures/pbs_items_subset.csv`

**Interfaces:**
- Consumes: nothing.
- Produces: `tests/fixtures/pbs_items_subset.csv` for Tasks 6–8.

- [ ] **Step 1: Write the extractor**

```python
# tests/fixtures/make_pbs_subset.py
"""Extract a small, committable items.csv from a real PBS release.

Run:
    python tests/fixtures/make_pbs_subset.py \\
        downloads/tables_as_csv/items.csv > tests/fixtures/pbs_items_subset.csv

WHY THIS EXISTS rather than a hand-written fixture: a hand-written one encodes
what we BELIEVE the upstream shape is, and slice 8a's whole lesson was that the
belief was wrong three times over (spec 5.3). Extracting from the real file means
the fixture can never re-encode an assumption -- the same discipline as
make_medrt_subset.py and make_mesh_subset.py.

LICENCE (spec section 1): PBS data is NOT redistributable by drugref, so this
extract is deliberately TINY -- a few dozen rows chosen to exercise the parser,
which is fair-dealing scale, not a dataset. Never commit the full file, and never
add columns beyond the allow-list below.

The two planted columns (atc_code, amt_code) are NOT upstream. They are added
here on purpose so the quarantine test has something to prove drugref discards:
absence upstream is exactly what the test must not depend on.
"""
import csv
import sys

# The allow-list, plus the two planted encumbrance canaries.
COLUMNS = ["li_item_id", "pbs_code", "brand_name", "li_drug_name", "drug_name",
           "li_form", "schedule_form", "program_code", "benefit_type_code",
           "atc_code", "amt_code"]

# Names chosen to cover every branch of the resolver. Keep this list SHORT.
WANTED = [
    "Rifaximin",                          # plain single ingredient
    "Abacavir with lamivudine",           # ' with ' combination
    "Abiraterone and methylprednisolone",  # ' and ' combination
    "Alfuzosin hydrochloride",            # salt-stripped match
    "Dimethyl fumarate",                  # INN that LOOKS salt-suffixed (regression)
    "Alendronic acid",                    # the 'acid' trap
    "Folic acid",                         # the 'acid' trap
    "Paracetamol",                        # high-frequency, should match
    "Amoxicillin with clavulanic acid",   # combination WHERE a part ends in 'acid'
    "Allantoin with sulfur, phenol, coal tar solution and menthol",  # multi-component
]


def main(source_path: str) -> None:
    writer = csv.DictWriter(sys.stdout, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    wanted = {name.lower() for name in WANTED}
    seen: set[str] = set()
    with open(source_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("li_drug_name") or "").strip().lower()
            if name not in wanted or name in seen:
                continue
            seen.add(name)
            out = {c: row.get(c, "") for c in COLUMNS}
            # Plant the canaries the quarantine test looks for (see docstring).
            out["atc_code"] = "ZZZ_ATC_CANARY"
            out["amt_code"] = "ZZZ_AMT_CANARY"
            writer.writerow(out)
    # One extra row exercising the 'null' sentinel fallback path.
    writer.writerow({
        "li_item_id": "NULLCASE_1", "pbs_code": "NULLC", "brand_name": "null",
        "li_drug_name": "null", "drug_name": "Aspirin", "li_form": "null",
        "schedule_form": "null", "program_code": "GE", "benefit_type_code": "U",
        "atc_code": "ZZZ_ATC_CANARY", "amt_code": "ZZZ_AMT_CANARY"})


if __name__ == "__main__":
    main(sys.argv[1])
```

- [ ] **Step 2: Generate the fixture from a real release**

```bash
curl -L -o /tmp/pbs.zip \
  "https://www.pbs.gov.au/publication/schedule/2026/07/2026-07-01-PBS-API-CSV-files.zip?variant=3"
mkdir -p downloads && unzip -o -q /tmp/pbs.zip -d downloads
python tests/fixtures/make_pbs_subset.py downloads/tables_as_csv/items.csv \
  > tests/fixtures/pbs_items_subset.csv
```

Expected: ~11 rows. **The `?variant=3` parameter is required** — without it the URL 404s. Confirm
`downloads/` is gitignored before proceeding: `git check-ignore downloads && echo IGNORED`.

- [ ] **Step 3: Verify the fixture covers every branch**

Run: `python -c "import csv;rows=list(csv.DictReader(open('tests/fixtures/pbs_items_subset.csv')));print(len(rows));[print(' ',r['li_drug_name'],'|',r['drug_name']) for r in rows]"`
Expected: rows for each `WANTED` name plus the `NULLCASE_1` row. If a name is missing upstream (PBS
re-lists monthly), substitute a comparable one **from the real file** and note the swap in the commit.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/make_pbs_subset.py tests/fixtures/pbs_items_subset.csv
git commit -m "test(pbs): add the fixture extractor and its committed extract

Extracted from the real 2026-07 release rather than hand-written, so it cannot
re-encode an assumption about upstream shape -- which slice 8a got wrong three
times before measuring (spec 5.3). Same discipline as make_medrt_subset.py.

Deliberately tiny (fair-dealing scale): PBS data is not redistributable by
drugref. The atc_code/amt_code columns are PLANTED, not upstream, so the
quarantine test proves discarding rather than relying on their absence."
```

---

### Task 6: The writer (`local.py`)

**Files:**
- Create: `src/drugref/local.py`
- Test: `tests/test_local_writer.py`

**Interfaces:**
- Consumes: Task 1's schema; `ids.mint_local_product_uuid`.
- Produces: `local.upsert_product(conn, item, ingest_run_id, jurisdiction='AU', source='PBS') -> uuid.UUID`;
  `local.clear_source_products(conn, source)`;
  `local.add_product_moiety(conn, product_uuid, moiety_uuid, component_name, match_method, ingest_run_id) -> bool`;
  `local.add_unmatched_components(conn, rows, ingest_run_id, jurisdiction='AU', source='PBS') -> int`
  where `rows` is `Iterable[tuple[str, str]]` of `(source_code, component_name)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_local_writer.py
"""The single writer for the local tier -- mirrors classes.py's role.

The discipline it enforces is the REBUILDABLE-PROJECTION one: clear_source_*
deliberately DELETEs, because a de-listed PBS item must be able to disappear.
"""
import dataclasses

import pytest

from drugref import local
from drugref.ingest.pbs import PbsItem

ITEM = PbsItem(source_code="10001J_14023", pbs_code="10001J", brand_name="Xifaxan",
               drug_name="Rifaximin", form_strength="Tablet 550 mg",
               program_code="GE", benefit_type_code="A")


def test_upsert_product_is_idempotent(conn, ingest_run_id):
    """Re-ingesting the same release must not duplicate: the UUID is derived."""
    first = local.upsert_product(conn, ITEM, ingest_run_id)
    second = local.upsert_product(conn, ITEM, ingest_run_id)
    assert first == second
    count = conn.execute("SELECT count(*) FROM drugref.local_product").fetchone()[0]
    assert count == 1


def test_upsert_product_refreshes_mutable_fields(conn, ingest_run_id):
    """Price-adjacent attributes churn monthly; identity does not."""
    product_uuid = local.upsert_product(conn, ITEM, ingest_run_id)
    renamed = dataclasses.replace(ITEM, brand_name="Xifaxan XL")
    assert local.upsert_product(conn, renamed, ingest_run_id) == product_uuid
    brand = conn.execute(
        "SELECT brand_name FROM drugref.local_product WHERE local_product_uuid = %s",
        (product_uuid,)).fetchone()[0]
    assert brand == "Xifaxan XL"


def test_add_product_moiety_reports_insert_vs_conflict(conn, ingest_run_id, a_moiety):
    product_uuid = local.upsert_product(conn, ITEM, ingest_run_id)
    assert local.add_product_moiety(
        conn, product_uuid, a_moiety, "rifaximin", "exact", ingest_run_id) is True
    assert local.add_product_moiety(
        conn, product_uuid, a_moiety, "rifaximin", "exact", ingest_run_id) is False


def test_clear_source_products_removes_bridge_and_products(conn, ingest_run_id, a_moiety):
    """A rebuild must clear the bridge FIRST or the FK blocks the product delete."""
    product_uuid = local.upsert_product(conn, ITEM, ingest_run_id)
    local.add_product_moiety(conn, product_uuid, a_moiety, "rifaximin", "exact", ingest_run_id)
    local.add_unmatched_components(conn, [("10001J_14023", "mystery")], ingest_run_id)
    local.clear_source_products(conn, "PBS")
    for table in ("local_product", "local_product_moiety", "local_unmatched_ingredient"):
        assert conn.execute(f"SELECT count(*) FROM drugref.{table}").fetchone()[0] == 0


def test_add_unmatched_components_batches(conn, ingest_run_id):
    written = local.add_unmatched_components(
        conn, [("a", "foo"), ("b", "bar")], ingest_run_id)
    assert written == 2
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_unmatched_ingredient").fetchone()[0] == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_local_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.local'`.

- [ ] **Step 3: Implement**

```python
# src/drugref/local.py
"""The ONLY module that writes the local (jurisdiction-specific) tier.

It mirrors classes.py, not claims.py, and the difference matters:

* claims.py guards an APPEND-ONLY spine -- substance identity is immortal and the
  database floor rejects UPDATE/DELETE outright.
* This module manages a REBUILDABLE PROJECTION. PBS republishes monthly, and an
  item it DE-LISTS must disappear here too -- which an insert-only merge can never
  express. So clear_source_products deliberately DELETEs.

What survives a rebuild unchanged is product IDENTITY: local_product_uuid is a
pure function of (jurisdiction, source, code), so every surviving product comes
back with exactly the UUID it had before (src/drugref/ids.py).
"""
import uuid
from collections.abc import Iterable

import psycopg

from drugref import ids
from drugref.ingest.pbs import PbsItem


def upsert_product(conn: psycopg.Connection, item: PbsItem, ingest_run_id: int,
                   jurisdiction: str = "AU", source: str = "PBS") -> uuid.UUID:
    """Register a local product, or refresh its cached attributes on re-ingest.

    The UUID is DERIVED, never looked up, so this is safe to call on every ingest.
    ON CONFLICT refreshes the descriptive columns -- brands are renamed and items
    move between programs -- while the identity columns are, by construction, the
    same values the UUID was minted from and so cannot change.
    """
    product_uuid = ids.mint_local_product_uuid(jurisdiction, source, item.source_code)
    conn.execute(
        "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, source, "
        "source_code, pbs_code, brand_name, drug_name, form_strength, program_code, "
        "benefit_type_code, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (local_product_uuid) DO UPDATE SET "
        "  pbs_code = EXCLUDED.pbs_code, brand_name = EXCLUDED.brand_name, "
        "  drug_name = EXCLUDED.drug_name, form_strength = EXCLUDED.form_strength, "
        "  program_code = EXCLUDED.program_code, "
        "  benefit_type_code = EXCLUDED.benefit_type_code, "
        "  ingest_run = EXCLUDED.ingest_run",
        (product_uuid, jurisdiction, source, item.source_code, item.pbs_code,
         item.brand_name, item.drug_name, item.form_strength, item.program_code,
         item.benefit_type_code, ingest_run_id))
    return product_uuid


def clear_source_products(conn: psycopg.Connection, source: str) -> None:
    """Drop every product, bridge row and unmatched note contributed by `source`.

    Called at the start of a re-ingest so a new monthly release fully REPLACES the
    previous one. Order matters: the bridge and the unmatched list are deleted
    BEFORE the products they reference, or the foreign key refuses the delete.

    Scoped by source (via ingest_run) so another jurisdiction's or authority's
    rows survive -- the same per-source discipline classes.clear_source_edges uses.
    """
    for table in ("local_product_moiety", "local_unmatched_ingredient", "local_product"):
        conn.execute(
            f"DELETE FROM drugref.{table} WHERE ingest_run IN "
            "(SELECT ingest_run_id FROM drugref.ingest_run WHERE source = %s)",
            (source,))


def add_product_moiety(conn: psycopg.Connection, product_uuid: uuid.UUID,
                       moiety_uuid: uuid.UUID, component_name: str,
                       match_method: str, ingest_run_id: int) -> bool:
    """Link a local product to a moiety. Returns True if newly inserted.

    `match_method` ('exact' | 'salt_stripped') is stored per row so a consumer can
    discard the salt-strip heuristic wholesale rather than having to trust it --
    the heuristic stands in for slice 3 and should not masquerade as certainty.
    """
    cur = conn.execute(
        "INSERT INTO drugref.local_product_moiety (local_product_uuid, moiety_uuid, "
        "component_name, match_method, ingest_run) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (product_uuid, moiety_uuid, component_name, match_method, ingest_run_id))
    return cur.rowcount == 1


def add_unmatched_components(conn: psycopg.Connection,
                             rows: Iterable[tuple[str, str]], ingest_run_id: int,
                             jurisdiction: str = "AU", source: str = "PBS") -> int:
    """Record ingredient names that resolved to no moiety. Returns rows written.

    Not an error and not a silent drop. PBS lists foods, dressings and
    extemporaneous chemicals that slice 1's gate excludes BY DESIGN, so a healthy
    ingest still produces these -- and persisting them is what makes coverage a
    query instead of an impression (spec section 7).

    Batched via executemany: this is thousands of rows on a real release, and
    unlike add_product_moiety no caller needs the per-row insert-vs-conflict answer.
    """
    batch = [(ingest_run_id, jurisdiction, source, source_code, component_name)
             for source_code, component_name in rows]
    if not batch:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO drugref.local_unmatched_ingredient "
            "(ingest_run, jurisdiction, source, source_code, component_name) "
            "VALUES (%s, %s, %s, %s, %s)", batch)
    return len(batch)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_local_writer.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/drugref/local.py tests/test_local_writer.py
git commit -m "feat(local): add the single writer for the local tier

Mirrors classes.py, not claims.py: this is a rebuildable projection, so
clear_source_products deliberately DELETEs -- an item PBS de-lists must be able
to disappear, which an insert-only merge cannot express. Deletion order is
bridge-then-products, or the FK refuses."
```

---

### Task 7: The orchestrator (`ingest/pbs_run.py`)

**Files:**
- Create: `src/drugref/ingest/pbs_run.py`
- Test: `tests/test_pbs_run.py`

**Interfaces:**
- Consumes: Tasks 3, 4, 6; `classes.moieties_by_scheme`.
- Produces: `pbs_run.PbsSummary` (frozen dataclass: `items_read`, `products_written`,
  `products_bridged`, `bridge_rows_exact`, `bridge_rows_salt_stripped`, `combination_products`,
  `unmatched_components`); `pbs_run.ingest_pbs(conn, items_csv_path, upstream_release, checksum) -> PbsSummary`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pbs_run.py
"""DB-gated acceptance matrix for the PBS ingest.

Seeds a small moiety registry with INN claims, then ingests the committed fixture
and asserts on the BRIDGE -- which is the only thing slice 8a is really testing.
"""
import pathlib

import pytest

from drugref import claims, ids
from drugref.ingest import pbs_run

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "pbs_items_subset.csv"


@pytest.fixture(autouse=True)
def _clean(conn):
    """ingest_pbs COMMITS internally, so conftest's rollback cannot isolate these
    tests. Truncate first, exactly as test_medrt_run.py does, so counts are
    order-independent.

    NOTE (ROADMAP floor-hardening): this fixture depends on the very TRUNCATE
    bypass that item plans to close, so this module is now the THIRD one coupled
    to it. Add it to that note.
    """
    conn.execute(
        "TRUNCATE drugref.local_product_moiety, drugref.local_unmatched_ingredient, "
        "drugref.local_product, drugref.identity_claim, drugref.substance_moiety, "
        "drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield

# INN claims are stored lower-case; PBS publishes Title-case. The fold is what
# makes these meet, so seeding them lower-case mirrors production exactly.
SEED_INNS = ["rifaximin", "abacavir", "lamivudine", "abiraterone",
             "methylprednisolone", "alfuzosin", "dimethyl fumarate",
             "alendronic acid", "folic acid", "paracetamol", "aspirin"]


@pytest.fixture
def seeded_registry(conn):
    """A moiety per SEED_INN, each carrying its INN identity claim."""
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('UNII', 'seed', 'seed') RETURNING ingest_run_id").fetchone()[0]
    out = {}
    for index, inn in enumerate(SEED_INNS):
        moiety_uuid = ids.mint_moiety_uuid(f"SEEDUNII{index:02d}")
        conn.execute(
            "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
            "first_seen_ingest) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (moiety_uuid, inn, run_id))
        claims.add_claim(conn, moiety_uuid, "INN", inn, run_id)
        out[inn] = moiety_uuid
    return out


def _bridged_names(conn, drug_name):
    return {row[0] for row in conn.execute(
        "SELECT b.component_name FROM drugref.local_product_moiety b "
        "JOIN drugref.local_product p USING (local_product_uuid) "
        "WHERE p.drug_name = %s", (drug_name,)).fetchall()}


def test_exact_match_bridges(conn, seeded_registry):
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Rifaximin") == {"rifaximin"}


def test_combination_fans_out_to_both_components(conn, seeded_registry):
    """'Abacavir with lamivudine' must produce TWO bridge rows, not one."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Abacavir with lamivudine") == {"abacavir", "lamivudine"}


def test_salt_stripped_match_is_labelled(conn, seeded_registry):
    """'Alfuzosin hydrochloride' matches only after stripping, and the row must
    say so -- otherwise a heuristic masquerades as an exact match."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    method = conn.execute(
        "SELECT b.match_method FROM drugref.local_product_moiety b "
        "JOIN drugref.local_product p USING (local_product_uuid) "
        "WHERE p.drug_name = 'Alfuzosin hydrochloride'").fetchone()[0]
    assert method == "salt_stripped"


def test_unstripped_name_wins_over_the_salt_fallback(conn, seeded_registry):
    """THE REGRESSION. 'Dimethyl fumarate' is itself an INN. Trying the stripped
    form first would match nothing (or worse, the wrong moiety) and would be
    recorded as salt_stripped."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    row = conn.execute(
        "SELECT b.component_name, b.match_method FROM drugref.local_product_moiety b "
        "JOIN drugref.local_product p USING (local_product_uuid) "
        "WHERE p.drug_name = 'Dimethyl fumarate'").fetchone()
    assert row == ("dimethyl fumarate", "exact")


def test_acid_names_match_exactly(conn, seeded_registry):
    """'Alendronic acid' and 'Folic acid' must match whole -- 'acid' is part of
    the INN, not a salt token."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Alendronic acid") == {"alendronic acid"}
    assert _bridged_names(conn, "Folic acid") == {"folic acid"}


def test_partial_combination_is_recorded_honestly(conn, seeded_registry):
    """'Amoxicillin with clavulanic acid': neither component is seeded, so both
    must land in the unmatched list rather than the product silently vanishing."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    unmatched = {row[0] for row in conn.execute(
        "SELECT component_name FROM drugref.local_unmatched_ingredient").fetchall()}
    assert "amoxicillin" in unmatched
    assert "clavulanic acid" in unmatched


def test_null_sentinel_row_uses_the_drug_name_fallback(conn, seeded_registry):
    """The NULLCASE_1 fixture row has li_drug_name='null' and drug_name='Aspirin'."""
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert _bridged_names(conn, "Aspirin") == {"aspirin"}
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_product WHERE drug_name = 'null'"
    ).fetchone()[0] == 0


def test_summary_counts_are_consistent(conn, seeded_registry):
    summary = pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert summary.items_read == summary.products_written
    assert summary.products_bridged <= summary.products_written
    assert summary.bridge_rows_salt_stripped >= 1
    assert summary.combination_products >= 2


def test_re_ingest_is_idempotent(conn, seeded_registry):
    first = pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    second = pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    assert first.products_written == second.products_written
    total = conn.execute("SELECT count(*) FROM drugref.local_product").fetchone()[0]
    assert total == second.products_written
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_pbs_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drugref.ingest.pbs_run'`.

- [ ] **Step 3: Implement**

```python
# src/drugref/ingest/pbs_run.py
"""Orchestrator for the Australian PBS ingest (slice 8a) -- the only writer path.

Owns the transaction, exactly like medrt_run.py and mesh_run.py: the parser is
pure, local.py holds the SQL, and this module decides what happens and when.

LICENCE (spec section 1): drugref ships this CODE. It never ships PBS DATA, and a
node operator supplies their own release. See issue #25 for the redistribution
gate that is still open.
"""
import hashlib
import logging
import pathlib
from dataclasses import dataclass

import psycopg

from drugref import classes, local
from drugref.ingest import pbs

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PbsSummary:
    """What one PBS ingest did -- the slice's actual deliverable (spec section 7).

    products_bridged / products_written is the MATCH RATE, and the split between
    exact and salt-stripped rows says how much of it the slice-3 stand-in is
    carrying. unmatched_components is the residual worklist.
    """
    items_read: int
    products_written: int
    products_bridged: int
    bridge_rows_exact: int
    bridge_rows_salt_stripped: int
    combination_products: int
    unmatched_components: int


def resolve(component: str, inn_index: dict[str, list], salt_suffixes: frozenset[str]):
    """Resolve one ingredient name to (moieties, match_method), or ([], None).

    ORDER IS THE SAFEGUARD, and it is the whole reason this is a function rather
    than an inline lookup: the UNSTRIPPED name is tried FIRST, and the salt strip
    is only a fallback. "Dimethyl fumarate" is an INN in its own right while
    "fumarate" is a genuine salt token elsewhere ("Ferrous fumarate"), so an
    eager strip would turn a correct exact match into a miss -- and would label
    it 'salt_stripped', hiding the damage behind a plausible-looking row.

    Returns EVERY claimant moiety, never an arbitrary first: identity_claim is
    unique per (moiety, scheme, value) but not across moieties, so picking one
    would drop a real link and could answer differently run to run
    (classes.moieties_by_scheme applies the same rule).
    """
    exact = inn_index.get(component)
    if exact:
        return exact, "exact"
    stripped = pbs.strip_salt(component, salt_suffixes)
    if stripped:
        fallback = inn_index.get(stripped)
        if fallback:
            return fallback, "salt_stripped"
    return [], None


def ingest_pbs(conn: psycopg.Connection, items_csv_path: str | pathlib.Path,
               upstream_release: str, source_checksum: str | None = None,
               jurisdiction: str = "AU", source: str = "PBS") -> PbsSummary:
    """Ingest one PBS release's items.csv. Owns the transaction end to end.

    Steps, in an order that matters: open the provenance row, CLEAR this source's
    previous projection (so a de-listed item disappears), read the INN index ONCE
    (a per-item query would re-ask an answered question thousands of times), then
    per item upsert the product and bridge or record each component.

    On failure the transaction is rolled back and the error re-raised, rather than
    left half-applied: a mid-run abort previously left the caller's connection in
    an aborted state, so the NEXT feed's first statement failed for reasons that
    had nothing to do with it.
    """
    path = pathlib.Path(items_csv_path)
    if source_checksum is None:
        source_checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        run_id = conn.execute(
            "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
            "VALUES (%s, %s, %s) RETURNING ingest_run_id",
            (source, upstream_release, source_checksum)).fetchone()[0]

        local.clear_source_products(conn, source)
        inn_index = classes.moieties_by_scheme(conn, "INN")
        salt_suffixes = pbs.load_salt_suffixes()
        log.info("PBS ingest %s: %d INN claims indexed", upstream_release, len(inn_index))

        items_read = products_bridged = exact_rows = salt_rows = combinations = 0
        unmatched: list[tuple[str, str]] = []

        for item in pbs.parse_items(path):
            items_read += 1
            product_uuid = local.upsert_product(
                conn, item, run_id, jurisdiction=jurisdiction, source=source)
            components = pbs.split_components(item.drug_name or "")
            if len(components) > 1:
                combinations += 1
            bridged_here = False
            for component in components:
                moieties, method = resolve(component, inn_index, salt_suffixes)
                if not moieties:
                    unmatched.append((item.source_code, component))
                    continue
                bridged_here = True
                for moiety_uuid in moieties:
                    if local.add_product_moiety(
                            conn, product_uuid, moiety_uuid, component, method, run_id):
                        if method == "exact":
                            exact_rows += 1
                        else:
                            salt_rows += 1
            if bridged_here:
                products_bridged += 1

        local.add_unmatched_components(
            conn, unmatched, run_id, jurisdiction=jurisdiction, source=source)
        conn.execute(
            "UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s",
            (run_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("PBS ingest failed for release %s; rolled back", upstream_release)
        raise

    summary = PbsSummary(
        items_read=items_read, products_written=items_read,
        products_bridged=products_bridged, bridge_rows_exact=exact_rows,
        bridge_rows_salt_stripped=salt_rows, combination_products=combinations,
        unmatched_components=len(unmatched))
    log.info("PBS ingest %s complete: %s", upstream_release, summary)
    return summary
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_pbs_run.py -v`
Expected: PASS (9 tests). The `_clean` autouse fixture above is **required**, not optional: `ingest_pbs`
commits internally, so conftest's rollback-based isolation does not reach it and counts would otherwise
depend on test order. Add a line to ROADMAP's floor-hardening note recording this as the **third** module
coupled to the `TRUNCATE` bypass (alongside `test_ingest_run.py` and `test_medrt_run.py`), since that item
must land together with a replacement isolation strategy.

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ingest/pbs_run.py tests/test_pbs_run.py
git commit -m "feat(pbs): add the ingest orchestrator and the name bridge

resolve() tries the UNSTRIPPED name first and falls back to the salt strip only
on a miss. That ordering is the safeguard, not a preference: 'Dimethyl fumarate'
is an INN in its own right, so an eager strip would turn a correct match into a
miss AND label it salt_stripped, hiding the damage behind a plausible row.

Returns every claimant moiety rather than an arbitrary first, matching
moieties_by_scheme, so the bridge cannot answer differently run to run."
```

---

### Task 8: Encumbrance quarantine + per-source rebuild isolation

The two acceptance tests that defend the slice's non-negotiables.

**Files:**
- Modify: `tests/test_pbs_run.py`

**Interfaces:**
- Consumes: Task 7's `ingest_pbs`.
- Produces: nothing (tests only).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_pbs_run.py

# The canary values make_pbs_subset.py plants in the fixture's atc_code/amt_code
# columns. They are NOT upstream -- the fixture adds them precisely so this test
# proves drugref DISCARDS them, instead of passing merely because they were absent.
ATC_CANARY = "ZZZ_ATC_CANARY"
AMT_CANARY = "ZZZ_AMT_CANARY"


def _all_text_columns(conn):
    """Every text-ish column in the drugref schema, for an exhaustive sweep."""
    return conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND data_type IN ('text','character varying') "
        "ORDER BY table_name, column_name").fetchall()


def test_no_encumbered_value_reaches_any_drugref_table(conn, seeded_registry):
    """THE LICENCE GUARANTEE, EXECUTABLE (spec section 6).

    ATC codes are WHO-owned (NonCommercial + NoDerivatives) and AMT/SNOMED CT-AU
    is NCTS-licensed; neither may enter drugref. The fixture carries planted
    values in both columns, so if any parser or writer ever starts reading them,
    this sweep fails loudly rather than the breach shipping silently.
    """
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    offenders = []
    for table, column in _all_text_columns(conn):
        hits = conn.execute(
            f'SELECT count(*) FROM drugref."{table}" WHERE "{column}" IN (%s, %s)',
            (ATC_CANARY, AMT_CANARY)).fetchone()[0]
        if hits:
            offenders.append(f"{table}.{column}")
    assert offenders == [], f"encumbered value leaked into: {offenders}"


def test_rebuild_is_scoped_to_pbs(conn, seeded_registry):
    """A PBS re-ingest must not touch another source's projection. The registry
    seeded by the UNII run above must survive untouched."""
    before = conn.execute(
        "SELECT count(*) FROM drugref.identity_claim WHERE scheme = 'INN'").fetchone()[0]
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "testsum")
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-08-01", "testsum2")
    after = conn.execute(
        "SELECT count(*) FROM drugref.identity_claim WHERE scheme = 'INN'").fetchone()[0]
    assert after == before
    assert conn.execute(
        "SELECT count(*) FROM drugref.substance_moiety").fetchone()[0] == len(SEED_INNS)


def test_rebuild_drops_a_delisted_item(conn, seeded_registry, tmp_path):
    """The projection must SHRINK when upstream does -- the property that makes
    delete-and-rebuild the right model and an append-only floor the wrong one."""
    import csv as _csv
    rows = list(_csv.DictReader(open(FIXTURE, newline="", encoding="utf-8-sig")))
    smaller = tmp_path / "items.csv"
    with open(smaller, "w", newline="", encoding="utf-8-sig") as fh:
        writer = _csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows[:2])
    pbs_run.ingest_pbs(conn, FIXTURE, "2026-07-01", "a")
    full = conn.execute("SELECT count(*) FROM drugref.local_product").fetchone()[0]
    pbs_run.ingest_pbs(conn, smaller, "2026-08-01", "b")
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_product").fetchone()[0] == 2 < full
```

- [ ] **Step 2: Run the tests to verify they fail (or pass for the right reason)**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_pbs_run.py -v -k "encumbered or rebuild"`
Expected: the quarantine test should **pass immediately** (the design already excludes those columns).
**Verify it can fail**: temporarily add `atc_code` to `PbsItem` and `_clean(row, "atc_code")` in
`parse_items`, re-run, confirm it FAILS, then revert. A guard that cannot fail guards nothing.

- [ ] **Step 3: No implementation needed**

These tests defend existing behaviour. If `test_rebuild_drops_a_delisted_item` fails, the bug is in
`local.clear_source_products` (likely deletion order) — fix it there, not in the test.

- [ ] **Step 4: Run the whole suite**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest`
Expected: PASS — all pre-existing tests plus the new ones. Then `ruff check . && ruff format --check .`

- [ ] **Step 5: Commit**

```bash
git add tests/test_pbs_run.py
git commit -m "test(pbs): make the licence guarantee executable

Sweeps every text column in the drugref schema for planted ATC/AMT canary values
after a full ingest. The fixture plants them deliberately, so the test proves
drugref discards them rather than passing because they happened to be absent.

Also pins that a rebuild SHRINKS when upstream does -- the property that makes
delete-and-rebuild right here and an append-only floor wrong."
```

---

### Task 9: Measure against the real release, then document

The deliverable (spec §7). **A match rate is meaningless against an empty registry**, so the UNII seed
must run first — the dev database currently holds 0 moieties.

**Files:**
- Modify: `docs/HANDOVER.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: every prior task.
- Produces: measured numbers recorded in HANDOVER.

- [ ] **Step 1: Seed the registry, then ingest the real release**

```bash
# Confirm the registry is populated FIRST -- against an empty one the bridge
# correctly matches nothing and the match rate is a meaningless 0%.
psql "$DRUGREF_TEST_DSN" -tAc \
  "SELECT count(*) FROM drugref.identity_claim WHERE scheme='INN' AND superseded_by IS NULL"
```

If that returns 0, run the slice-1 UNII ingest first (see HANDOVER "How to run / test"). Then:

```bash
python -c "
import logging; logging.basicConfig(level=logging.INFO)
from drugref import db
from drugref.ingest import pbs_run
conn = db.connect()
print(pbs_run.ingest_pbs(conn, 'downloads/tables_as_csv/items.csv', '2026-07-01'))
"
```

- [ ] **Step 2: Pull the three headline numbers**

```bash
psql "$DRUGREF_TEST_DSN" -c "
SELECT count(*) FILTER (WHERE b.local_product_uuid IS NOT NULL)::numeric
       / NULLIF(count(*), 0) AS match_rate,
       count(*) AS products
FROM drugref.local_product p
LEFT JOIN LATERAL (SELECT 1 AS local_product_uuid FROM drugref.local_product_moiety m
                   WHERE m.local_product_uuid = p.local_product_uuid LIMIT 1) b ON true;"

psql "$DRUGREF_TEST_DSN" -c "
SELECT match_method, count(*) FROM drugref.local_product_moiety GROUP BY 1;"

psql "$DRUGREF_TEST_DSN" -c "
SELECT component_name, count(*) AS items FROM drugref.local_unmatched_ingredient
GROUP BY 1 ORDER BY 2 DESC LIMIT 25;"
```

- [ ] **Step 3: Record the findings in HANDOVER**

Add a **Slice 8a** section to `docs/HANDOVER.md` covering: the measured match rate; the exact vs
salt_stripped split (i.e. how much the slice-3 stand-in carries); the top unmatched names **with a note on
how many are foods/dressings that the moiety gate excludes by design**; the licence posture (node-local
only, issue [#25](https://github.com/cairn-ehr/drugref/issues/25) open); and the download command including
`?variant=3`.

**Judge the numbers, do not just report them.** If the salt strip contributes very little, say so — the
spec predicted ~20 names and a plan that quietly leaves a useless heuristic in place is tech debt (rule 5).
If the top unmatched names are mostly real drugs rather than foods, that is evidence the AU→INN alias list
(spec §5.2) has earned its place: **file a GitHub issue rather than implementing it here.**

- [ ] **Step 4: Correct ROADMAP's refuted licence claim**

`docs/ROADMAP.md` Slice 8 still reads "**PBS + TGA ARTG** (both CC BY, redistributable)". That is refuted
(spec §1). Rewrite it to state the node-local plug-in posture, cite issue #25, and note that slice 8a
delivered the bridge. Keep both documents under 500 lines (rule 9).

- [ ] **Step 5: Commit, push, open the PR**

```bash
git add docs/HANDOVER.md docs/ROADMAP.md
git commit -m "docs: record slice 8a's measured PBS match rate and correct the licence claim"
git push -u origin feat/slice-8a-pbs-localisation
gh pr create --title "Slice 8a — PBS localisation: the local tier's first attachment" --body "$(cat <<'BODY'
drugref's first local (jurisdiction-specific) tier: a minimal Australian PBS
product layer bridged to the global moiety spine by name — the only
licence-clean join available, because PBS carries no UNII, CAS or InChIKey.

## Measured against the 2026-07 release
- Match rate: <FILL FROM STEP 2>% of <N> products bridged to >=1 moiety
- Exact vs salt_stripped: <N> / <N> (how much the slice-3 stand-in carries)
- Top unmatched: <list>, of which <N> are foods/dressings outside the moiety gate

## Licence posture — read this first
**No PBS data is bundled or redistributed.** drugref ships AGPL ingest code and
schema; a node operator supplies their own release. ATC (WHO, NC+ND) and
AMT/SNOMED (NCTS) are quarantined structurally and proven discarded by test.
Redistribution stays blocked pending written Dept-of-Health confirmation: #25.

## Notes for reviewers
- `db/009` is a rebuildable projection, deliberately outside the append-only
  floor: a de-listed PBS item must be able to disappear.
- `resolve()` tries the unstripped name FIRST; "Dimethyl fumarate" is an INN in
  its own right, so an eager salt strip would break a correct match.

Closes nothing; advances ROADMAP Slice 8.
BODY
)"
```

Replace every `<FILL …>` with the real numbers from Step 2 — a PR that reports the match rate as a
placeholder defeats the purpose of the slice.

---

## Verification checklist

- [ ] `DRUGREF_TEST_DSN=... uv run pytest` — all green, no skips (rule 6).
- [ ] `ruff check . && ruff format --check .` — clean.
- [ ] `git status` — **no PBS data staged**; `downloads/` still gitignored (rule 7).
- [ ] `NOTICE` unchanged — this slice redistributes nothing.
- [ ] The quarantine test was **proven able to fail** (Task 8, Step 2).
- [ ] HANDOVER and ROADMAP updated; ROADMAP's "both CC BY" claim corrected.
- [ ] Every file under ~500 lines (rule 4).

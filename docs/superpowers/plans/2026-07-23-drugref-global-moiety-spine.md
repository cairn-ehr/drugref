# drugref Global Tier — Slice 1: Active-Moiety Identity Spine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the new `cairn-ehr/drugref` repo and deliver a reproducibly-seeded registry of active drug moieties — each with an immortal UUID and append-only external-identifier claims — from public-domain sources.

**Architecture:** A Postgres schema `drugref` (three tables + an append-only integrity floor enforced by DB triggers) plus a Python ingest that gates the UNII substance universe down to active drug moieties (`has-INN` + a closed legacy allow-list), mints a deterministic-at-seed / immortal-thereafter `moiety_uuid` (UUIDv5 keyed on UNII), and writes external identifiers as append-only claims. Idempotent re-ingest; upstream churn attaches new claims, never re-keys.

**Tech Stack:** Python 3.12+ managed by **uv**, `psycopg` (v3), PostgreSQL ≥ 18, `pytest`. Source data: UNII/GSRS (public domain), ChEBI (CC BY 4.0). No web calls in tests — small local fixtures.

## Global Constraints

- **Licence:** repo is **AGPL-3.0**; every dependency must be **AGPL-3.0-compatible** — check before adding.
- **Python tooling:** always **`uv`** (`uv add`, `uv run`); never `pip`/`venv` directly.
- **Database:** PostgreSQL **≥ 18**.
- **TDD:** failing test first, then minimal code; **frequent commits** (one per task minimum).
- **Append-only integrity is enforced in the database**, not only in app code.
- **Identity:** drugref mints its **own** `moiety_uuid` (never key on INN); **UUIDv5 keyed on UNII at seed, pinned forever**.
- **Membership gate:** `has-INN` (UNII `INN_ID` present) **OR** on the closed legacy allow-list; else excluded.
- **Reviewer-legible code:** every non-trivial function carries comments explaining *why/how* for a junior developer (house rule 3).
- **No hard-coded cryptographic material in tests** (house rule 6) — not exercised in slice 1, but hold the rule if any signing lands.
- **Capture the full cross-reference set** (UNII, INN, RxCUI, ChEBI, CAS, PubChem) as claims — cheap at seed, and a public cross-walk asset.

---

## File Structure

```
drugref/                              # NEW repo: github.com/cairn-ehr/drugref
  LICENSE                             # AGPL-3.0 full text
  NOTICE                             # attributions: FDA/UNII, EMBL-EBI/ChEBI (CC BY 4.0), WHO/INN, NLM/RxNorm
  README.md
  .gitignore
  pyproject.toml                     # uv-managed; deps: psycopg[binary]; dev: pytest
  db/
    001_schema_drugref.sql           # schema drugref: 3 tables + append-only trigger floor
  src/drugref/
    __init__.py
    ids.py                           # UUIDv5 namespaces + mint_moiety_uuid
    db.py                            # connect() + apply_migrations()
    claims.py                        # upsert_moiety(), add_claim() — append-only writers
    ingest/
      __init__.py
      unii.py                        # parse UNII data file -> MoietyCandidate records
      gate.py                        # is_moiety(), inn_display_name(), loaders for crosswalk/allow-list
      chebi.py                       # (Task 8) join ChEBI by InChIKey -> CHEBI claim
      run.py                        # orchestrate one ingest run: gate -> mint -> claim
    data/
      usan_inn_crosswalk.tsv         # closed hand-curated USAN->INN divergences (seed subset)
      legacy_allowlist.tsv           # pre-INN drugs with no INN (magnesium sulfate, ...)
  tests/
    conftest.py                      # DB fixtures (skip if DRUGREF_TEST_DSN unset)
    fixtures/
      unii_subset.tsv
      chebi_subset.tsv
    test_ids.py
    test_gate.py
    test_unii.py
    test_claims.py                   # DB-gated
    test_schema_floor.py             # DB-gated
    test_ingest_run.py               # DB-gated — the acceptance matrix
    test_chebi.py                    # (Task 8)
```

Responsibilities: `ids.py` = identity derivation only; `claims.py` = the only module that writes moiety/claim rows (append-only discipline in one place); `ingest/*` = source-specific parsing + the gate + orchestration; `db/001` = the unbypassable floor. Files that change together (a parser + its fixture + its test) sit together.

---

### Task 1: Repo bootstrap

**Files:**
- Create: `LICENSE`, `NOTICE`, `README.md`, `.gitignore`, `pyproject.toml`, `src/drugref/__init__.py`, `src/drugref/ingest/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`

**Interfaces:**
- Produces: an installed `drugref` package importable under `uv run pytest`.

- [ ] **Step 1: Create the repo and package skeleton**

```bash
mkdir -p drugref/src/drugref/ingest/data drugref/db drugref/tests/fixtures
cd drugref
git init
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "drugref"
version = "0.0.1"
description = "drugref.org — an open drug-information service (global tier)"
requires-python = ">=3.12"
license = { text = "AGPL-3.0-or-later" }
dependencies = ["psycopg[binary]>=3.2"]

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/drugref"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Add licence, notice, gitignore, README**

`LICENSE`: the full AGPL-3.0 text (fetch from https://www.gnu.org/licenses/agpl-3.0.txt).

`NOTICE`:
```
drugref.org — global tier. Copyright (C) 2026 the drugref/Cairn contributors.
Licensed under AGPL-3.0-or-later.

Seed data attributions:
- UNII / Global Substance Registration System — U.S. FDA/NCATS (public domain).
- ChEBI — EMBL-EBI, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).
- International Nonproprietary Names (INN) — World Health Organization (public domain).
- RxNorm — U.S. National Library of Medicine (public domain, prescribable subset).
```

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
.pytest_cache/
```

`README.md`: one paragraph — what drugref is (co-equal public-good drug-information service), that this is the global tier, and a pointer to the design spec.

- [ ] **Step 4: Write the smoke test**

```python
# tests/test_smoke.py
import drugref

def test_package_imports():
    assert drugref is not None
```

- [ ] **Step 5: Install and run**

Run: `uv sync && uv run pytest tests/test_smoke.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: bootstrap drugref repo (uv, AGPL, package skeleton)"
```

---

### Task 2: Database schema + append-only floor

**Files:**
- Create: `db/001_schema_drugref.sql`, `src/drugref/db.py`, `tests/conftest.py`, `tests/test_schema_floor.py`

**Interfaces:**
- Produces: schema `drugref` with tables `ingest_run`, `substance_moiety`, `identity_claim`; `db.connect()`, `db.apply_migrations(conn)`.
- Consumes: env var `DRUGREF_TEST_DSN` (a Postgres ≥18 DSN) for DB-gated tests.

- [ ] **Step 1: Write the schema migration**

```sql
-- db/001_schema_drugref.sql
-- drugref global tier, slice 1: the active-moiety identity spine.
-- Three tables plus an append-only integrity floor enforced IN THE DATABASE, so a
-- buggy ingest -- or a raw-SQL hand -- cannot silently rewrite substance identity.

CREATE SCHEMA IF NOT EXISTS drugref;

-- Provenance: every registry/claim row traces to one ingest run, so any state is
-- reproducible and attributable to a specific upstream release.
CREATE TABLE drugref.ingest_run (
    ingest_run_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source           text        NOT NULL,   -- 'UNII' | 'CHEBI' | ...
    upstream_release text        NOT NULL,   -- the upstream file's release/version tag
    source_checksum  text        NOT NULL,   -- checksum of the ingested file
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz
);

-- The registry: one row per immortal active moiety. moiety_uuid is minted once
-- (UUIDv5 at seed, see src/drugref/ids.py) and NEVER changes.
CREATE TABLE drugref.substance_moiety (
    moiety_uuid       uuid   PRIMARY KEY,
    display_name      text   NOT NULL,       -- INN-preferred label; a cache derived from claims
    first_seen_ingest bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id)
);

-- External identifiers as append-only CLAIMS that attach to a moiety, never the key
-- (principle 2). A correction OVERLAYS: insert the corrected claim, set superseded_by
-- on the old one. Never UPDATE-in-place, never DELETE.
CREATE TABLE drugref.identity_claim (
    identity_claim_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    moiety_uuid       uuid        NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    scheme            text        NOT NULL,  -- 'UNII'|'INN'|'RXNORM_IN'|'CHEBI'|'CAS'|'PUBCHEM_CID'|'INCHIKEY'
    value             text        NOT NULL,
    ingest_run        bigint      NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    asserted_at       timestamptz NOT NULL DEFAULT now(),
    superseded_by     bigint      REFERENCES drugref.identity_claim(identity_claim_id)
);

-- Idempotent re-ingest: the same (moiety, scheme, value) is one logical claim.
CREATE UNIQUE INDEX identity_claim_unique
    ON drugref.identity_claim (moiety_uuid, scheme, value);
-- Reverse lookup (value -> moiety), the cross-walk query path.
CREATE INDEX identity_claim_by_scheme_value
    ON drugref.identity_claim (scheme, value);

-- ---- The append-only floor ------------------------------------------------

-- substance_moiety: forbid DELETE; forbid changing the immortal key. The
-- display_name cache MAY be refreshed by a later ingest.
CREATE FUNCTION drugref.forbid_moiety_rewrite() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drugref.substance_moiety is append-only: DELETE forbidden';
    END IF;
    IF NEW.moiety_uuid <> OLD.moiety_uuid THEN
        RAISE EXCEPTION 'drugref.substance_moiety.moiety_uuid is immortal: it may not change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER forbid_moiety_rewrite
    BEFORE UPDATE OR DELETE ON drugref.substance_moiety
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_moiety_rewrite();

-- identity_claim: forbid DELETE; the ONLY permitted mutation is setting superseded_by
-- (the overlay/correction path). No other column may change.
CREATE FUNCTION drugref.forbid_claim_rewrite() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drugref.identity_claim is append-only: DELETE forbidden';
    END IF;
    IF NEW.moiety_uuid  <> OLD.moiety_uuid
       OR NEW.scheme     <> OLD.scheme
       OR NEW.value      <> OLD.value
       OR NEW.ingest_run <> OLD.ingest_run
       OR NEW.asserted_at <> OLD.asserted_at THEN
        RAISE EXCEPTION 'drugref.identity_claim is append-only: only superseded_by may change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER forbid_claim_rewrite
    BEFORE UPDATE OR DELETE ON drugref.identity_claim
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_claim_rewrite();
```

- [ ] **Step 2: Write `db.py`**

```python
# src/drugref/db.py
"""Connection helper and migration applier for the drugref schema.

Kept deliberately thin: the schema (db/001_*.sql) is the source of truth for
structure and the append-only floor; this module only opens connections and
replays the SQL files in filename order (mirroring Cairn's connect-and-load
convention, so the schema is re-applied idempotently on a fresh database).
"""
import os
import pathlib
import psycopg

_DB_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "db"


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Open a connection. Falls back to the DRUGREF_DSN env var."""
    dsn = dsn or os.environ["DRUGREF_DSN"]
    return psycopg.connect(dsn)


def apply_migrations(conn: psycopg.Connection) -> None:
    """Replay every db/*.sql in filename order. Idempotent (CREATE ... IF NOT EXISTS
    where it matters); intended for a schema that has been dropped fresh in tests."""
    for path in sorted(_DB_DIR.glob("*.sql")):
        conn.execute(path.read_text())
    conn.commit()
```

- [ ] **Step 3: Write `conftest.py` (DB fixtures)**

```python
# tests/conftest.py
"""Shared DB fixtures. DB-gated tests are SKIPPED unless DRUGREF_TEST_DSN is set,
so unit tests still run anywhere. Each DB test runs inside a transaction that is
rolled back, so tests never see each other's writes."""
import os
import pytest
import psycopg
from drugref import db


@pytest.fixture(scope="session")
def _dsn():
    dsn = os.environ.get("DRUGREF_TEST_DSN")
    if not dsn:
        pytest.skip("DRUGREF_TEST_DSN not set — skipping DB-gated test")
    return dsn


@pytest.fixture(scope="session")
def _migrated(_dsn):
    """Drop and recreate the drugref schema once, then apply migrations."""
    with psycopg.connect(_dsn) as conn:
        conn.execute("DROP SCHEMA IF EXISTS drugref CASCADE")
        conn.commit()
        db.apply_migrations(conn)
    return _dsn


@pytest.fixture
def conn(_migrated):
    """A connection whose work is rolled back after each test."""
    with psycopg.connect(_migrated) as c:
        yield c
        c.rollback()
```

- [ ] **Step 4: Write the failing floor test**

```python
# tests/test_schema_floor.py
"""The append-only floor must reject rewrites even from raw SQL."""
import psycopg
import pytest


def _seed_one(conn):
    run = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('TEST','r1','x') RETURNING ingest_run_id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa','amlodipine', %s)", (run,))
    cid = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa','UNII','ABC123', %s) "
        "RETURNING identity_claim_id", (run,)).fetchone()[0]
    return run, cid


def test_moiety_delete_forbidden(conn):
    _seed_one(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.substance_moiety")


def test_moiety_uuid_immutable(conn):
    _seed_one(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.substance_moiety "
                     "SET moiety_uuid = '00000000-0000-0000-0000-0000000000bb'")


def test_claim_delete_forbidden(conn):
    _seed_one(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.identity_claim")


def test_claim_value_immutable_but_supersede_allowed(conn):
    run, cid = _seed_one(conn)
    # Changing value in place is forbidden...
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.identity_claim SET value = 'XYZ' WHERE identity_claim_id = %s", (cid,))
    conn.rollback()
    run, cid = _seed_one(conn)
    # ...but setting superseded_by is the permitted overlay path.
    conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s WHERE identity_claim_id = %s", (cid, cid))
```

- [ ] **Step 5: Run — verify tests fail without schema, pass with it**

Run: `DRUGREF_TEST_DSN=$DRUGREF_TEST_DSN uv run pytest tests/test_schema_floor.py -v`
Expected: PASS (4 passed) once `db/001_schema_drugref.sql` is in place; if the DSN is unset, SKIPPED.

- [ ] **Step 6: Commit**

```bash
git add db/001_schema_drugref.sql src/drugref/db.py tests/conftest.py tests/test_schema_floor.py
git commit -m "feat(db): drugref schema + append-only trigger floor"
```

---

### Task 3: Deterministic UUID minting

**Files:**
- Create: `src/drugref/ids.py`, `tests/test_ids.py`

**Interfaces:**
- Produces: `ids.MOIETY_NAMESPACE` (uuid.UUID); `ids.mint_moiety_uuid(unii: str) -> uuid.UUID`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ids.py
import uuid
from drugref import ids


def test_mint_is_deterministic():
    a = ids.mint_moiety_uuid("362O9ITL9D")
    b = ids.mint_moiety_uuid("362O9ITL9D")
    assert a == b
    assert isinstance(a, uuid.UUID)


def test_mint_is_case_and_space_insensitive_on_unii():
    assert ids.mint_moiety_uuid("362o9itl9d") == ids.mint_moiety_uuid("  362O9ITL9D ")


def test_distinct_uniis_distinct_uuids():
    assert ids.mint_moiety_uuid("362O9ITL9D") != ids.mint_moiety_uuid("1J444QC288")


def test_namespace_is_stable_across_runs():
    # A frozen constant: if this value ever changes, every derived UUID changes.
    assert str(ids.MOIETY_NAMESPACE) == str(uuid.uuid5(uuid.uuid5(uuid.NAMESPACE_DNS, "drugref.org"), "moiety"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_ids.py -v`
Expected: FAIL (module `drugref.ids` not found).

- [ ] **Step 3: Write minimal implementation**

```python
# src/drugref/ids.py
"""Deterministic minting of drugref's immortal substance identifiers.

drugref mints its OWN moiety UUID rather than keying on a name (principle 2:
identity is a claim, never the name). The UUID is derived deterministically
(UUIDv5) from the moiety's UNII, so two independent drugref instances ingesting
the same UNII release derive the SAME UUID with zero coordination. It is minted
at first sighting and then PINNED in the registry -- never re-derived, even if
the upstream identifier later churns.
"""
import uuid

# Namespaces are derived from the domain name (not magic literals) so they are
# self-documenting and reproducible. Per-level namespaces guarantee a moiety and
# a future salt/class derived from the same source string can never collide.
_DRUGREF_ROOT = uuid.uuid5(uuid.NAMESPACE_DNS, "drugref.org")
MOIETY_NAMESPACE = uuid.uuid5(_DRUGREF_ROOT, "moiety")


def mint_moiety_uuid(unii: str) -> uuid.UUID:
    """Derive the immortal moiety UUID from an active moiety's UNII.

    Deterministic: same UNII -> same UUID, always, everywhere. Callers use this
    only at first sighting; thereafter the registry is authoritative.
    """
    key = f"UNII:{unii.strip().upper()}"
    return uuid.uuid5(MOIETY_NAMESPACE, key)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_ids.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/drugref/ids.py tests/test_ids.py
git commit -m "feat(ids): deterministic UUIDv5 moiety minting keyed on UNII"
```

---

### Task 4: Append-only claim writers

**Files:**
- Create: `src/drugref/claims.py`, `tests/test_claims.py`

**Interfaces:**
- Consumes: `db.apply_migrations`, the `conn` fixture.
- Produces:
  - `claims.upsert_moiety(cur, moiety_uuid: uuid.UUID, display_name: str, ingest_run_id: int) -> None`
  - `claims.add_claim(cur, moiety_uuid: uuid.UUID, scheme: str, value: str, ingest_run_id: int) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_claims.py
import uuid
from drugref import claims

M = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _new_run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('TEST','r1','x') RETURNING ingest_run_id").fetchone()[0]


def test_upsert_moiety_then_add_claims(conn):
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "amlodipine", run)
    claims.add_claim(conn, M, "UNII", "1J444QC288", run)
    claims.add_claim(conn, M, "INN", "amlodipine", run)
    rows = conn.execute(
        "SELECT scheme, value FROM drugref.identity_claim WHERE moiety_uuid = %s ORDER BY scheme",
        (M,)).fetchall()
    assert rows == [("INN", "amlodipine"), ("UNII", "1J444QC288")]


def test_add_claim_is_idempotent(conn):
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "amlodipine", run)
    claims.add_claim(conn, M, "UNII", "1J444QC288", run)
    claims.add_claim(conn, M, "UNII", "1J444QC288", run)  # duplicate -> no-op
    n = conn.execute("SELECT count(*) FROM drugref.identity_claim WHERE moiety_uuid = %s", (M,)).fetchone()[0]
    assert n == 1


def test_upsert_moiety_refreshes_display_name(conn):
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "acetaminophen", run)
    claims.upsert_moiety(conn, M, "paracetamol", run)   # display cache may refresh
    name = conn.execute("SELECT display_name FROM drugref.substance_moiety WHERE moiety_uuid = %s", (M,)).fetchone()[0]
    assert name == "paracetamol"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_claims.py -v`
Expected: FAIL (module `drugref.claims` not found), or SKIPPED if no DSN.

- [ ] **Step 3: Write minimal implementation**

```python
# src/drugref/claims.py
"""The ONLY module that writes to substance_moiety / identity_claim.

Concentrating writes here keeps the append-only discipline in one reviewable
place: we INSERT new facts and overlay corrections, never UPDATE-in-place or
DELETE. The database floor (db/001) enforces the same rule against any caller.
"""
import uuid
import psycopg


def upsert_moiety(cur: psycopg.Connection, moiety_uuid: uuid.UUID,
                  display_name: str, ingest_run_id: int) -> None:
    """Register a moiety, or refresh its display-name cache on re-ingest.

    The moiety_uuid is immortal (the DB floor forbids changing it); display_name
    is a convenience cache derived from claims, so ON CONFLICT refreshes it.
    """
    cur.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (moiety_uuid) DO UPDATE SET display_name = EXCLUDED.display_name",
        (moiety_uuid, display_name, ingest_run_id))


def add_claim(cur: psycopg.Connection, moiety_uuid: uuid.UUID,
              scheme: str, value: str, ingest_run_id: int) -> None:
    """Append an external-identifier claim. Idempotent: re-asserting the same
    (moiety, scheme, value) is a no-op, so re-ingest never duplicates."""
    cur.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (moiety_uuid, scheme, value) DO NOTHING",
        (moiety_uuid, scheme, value, ingest_run_id))
```

- [ ] **Step 4: Run to verify it passes**

Run: `DRUGREF_TEST_DSN=$DRUGREF_TEST_DSN uv run pytest tests/test_claims.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/drugref/claims.py tests/test_claims.py
git commit -m "feat(claims): append-only moiety + identity-claim writers"
```

---

### Task 5: UNII data-file parser

**Files:**
- Create: `src/drugref/ingest/unii.py`, `tests/fixtures/unii_subset.tsv`, `tests/test_unii.py`

**Interfaces:**
- Produces:
  - `unii.MoietyCandidate` dataclass: `unii: str`, `preferred_name: str`, `has_inn: bool`, `cross_refs: dict[str, str]`
  - `unii.parse(path: str | pathlib.Path) -> Iterator[MoietyCandidate]`

> **VERIFY-BEFORE-PRODUCTION (design §6.1):** the real public UNII data file (`UNII_Data_*.txt`, tab-separated, header row) is expected to carry columns `UNII`, `PT`, `RN` (CAS), `RXCUI`, `PUBCHEM`, `INN_ID`, `INCHIKEY`. Confirm the exact header names against a freshly downloaded file before running against production data. The parser keys on **header names**, so a rename is a one-line map change. If `INN_ID` is absent/unpopulated in the public file, fall back to the WHO INN list for the gate signal (a later step) — this is the one upstream assumption to check first.

- [ ] **Step 1: Write the fixture** (`tests/fixtures/unii_subset.tsv`)

```
UNII	PT	RN	RXCUI	PUBCHEM	INN_ID	INCHIKEY
362O9ITL9D	ACETAMINOPHEN	103-90-2	161	1983	6689	RZVAJINKPMORJF-UHFFFAOYSA-N
1J444QC288	AMLODIPINE	88150-42-9	17767	2162	6211	HTIQEAQVCYTUBX-UHFFFAOYSA-N
DE08037SAB	MAGNESIUM SULFATE	7487-88-9	6853		
QCM	MICROCRYSTALLINE CELLULOSE	9004-34-6			
```
(Fixture values are illustrative. Row 3 = a real drug with **no INN** → later included via the legacy allow-list. Row 4 = an excipient with no INN → excluded.)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_unii.py
import pathlib
from drugref.ingest import unii

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"


def test_parse_yields_all_rows():
    cands = list(unii.parse(FIX))
    assert len(cands) == 4


def test_has_inn_flag_from_inn_id_column():
    by_name = {c.preferred_name: c for c in unii.parse(FIX)}
    assert by_name["ACETAMINOPHEN"].has_inn is True
    assert by_name["MAGNESIUM SULFATE"].has_inn is False
    assert by_name["MICROCRYSTALLINE CELLULOSE"].has_inn is False


def test_cross_refs_captured_when_present():
    by_name = {c.preferred_name: c for c in unii.parse(FIX)}
    acet = by_name["ACETAMINOPHEN"]
    assert acet.unii == "362O9ITL9D"
    assert acet.cross_refs["CAS"] == "103-90-2"
    assert acet.cross_refs["RXNORM_IN"] == "161"
    assert acet.cross_refs["PUBCHEM_CID"] == "1983"
    assert acet.cross_refs["INCHIKEY"] == "RZVAJINKPMORJF-UHFFFAOYSA-N"
    # Empty upstream cells are omitted, not stored as empty strings.
    assert "CAS" not in by_name["MICROCRYSTALLINE CELLULOSE"].cross_refs or \
        by_name["MICROCRYSTALLINE CELLULOSE"].cross_refs.get("RXNORM_IN") is None
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_unii.py -v`
Expected: FAIL (module not found).

- [ ] **Step 4: Write minimal implementation**

```python
# src/drugref/ingest/unii.py
"""Parse the FDA UNII data file into moiety-candidate records.

Each row is one substance. We extract the UNII (identity key), the preferred
term, the has-INN membership signal (presence of INN_ID -> the substance has a
WHO INN, design §6.1), and the cheap cross-references (CAS/RxCUI/PubChem/
InChIKey) that make drugref a public identifier cross-walk. The gate itself
lives in gate.py; this module only reads the file.
"""
import csv
import pathlib
from dataclasses import dataclass, field
from typing import Iterator

# Map UNII column headers -> the identity-claim scheme we store them under.
_CROSS_REF_COLUMNS = {
    "RN": "CAS",
    "RXCUI": "RXNORM_IN",
    "PUBCHEM": "PUBCHEM_CID",
    "INCHIKEY": "INCHIKEY",
}


@dataclass
class MoietyCandidate:
    unii: str
    preferred_name: str
    has_inn: bool
    cross_refs: dict[str, str] = field(default_factory=dict)


def parse(path: str | pathlib.Path) -> Iterator[MoietyCandidate]:
    """Yield one MoietyCandidate per row of the UNII data file."""
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            # `csv.DictReader` yields None for any column missing from a short row,
            # so coerce with `or ""` before stripping (None.strip() would crash).
            cross_refs = {
                scheme: (row.get(col) or "").strip()
                for col, scheme in _CROSS_REF_COLUMNS.items()
                if (row.get(col) or "").strip()      # omit empty/absent upstream cells
            }
            yield MoietyCandidate(
                unii=(row.get("UNII") or "").strip(),
                preferred_name=(row.get("PT") or "").strip(),
                has_inn=bool((row.get("INN_ID") or "").strip()),
                cross_refs=cross_refs,
            )
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_unii.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/unii.py tests/fixtures/unii_subset.tsv tests/test_unii.py
git commit -m "feat(ingest): UNII data-file parser with has-INN signal + cross-refs"
```

---

### Task 6: Membership gate + INN display-name resolution

**Files:**
- Create: `src/drugref/ingest/gate.py`, `src/drugref/data/usan_inn_crosswalk.tsv`, `src/drugref/data/legacy_allowlist.tsv`, `tests/test_gate.py`

**Interfaces:**
- Consumes: `unii.MoietyCandidate`.
- Produces:
  - `gate.load_crosswalk(path) -> dict[str, str]` (US-preferred-name-lower → INN)
  - `gate.load_allowlist(path) -> set[str]` (lowercased legacy drug names)
  - `gate.is_moiety(cand: MoietyCandidate, allowlist: set[str]) -> bool`
  - `gate.inn_display_name(cand: MoietyCandidate, crosswalk: dict[str, str]) -> str`

- [ ] **Step 1: Write the seed data files**

`src/drugref/data/usan_inn_crosswalk.tsv` (the closed legacy set — seed subset; grows only by hand as historical gaps are found):
```
us_name	inn
acetaminophen	paracetamol
albuterol	salbutamol
meperidine	pethidine
rifampin	rifampicin
glyburide	glibenclamide
```

`src/drugref/data/legacy_allowlist.tsv` (pre-INN drugs with no WHO INN; one lowercased name per line):
```
magnesium sulfate
sodium chloride
activated charcoal
sodium bicarbonate
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_gate.py
import pathlib
from drugref.ingest import gate, unii

DATA = pathlib.Path("src/drugref/data")


def _cand(name, has_inn):
    return unii.MoietyCandidate(unii="X", preferred_name=name, has_inn=has_inn)


def test_has_inn_is_a_moiety():
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(_cand("ACETAMINOPHEN", True), allow) is True


def test_legacy_allowlist_drug_is_a_moiety_despite_no_inn():
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(_cand("MAGNESIUM SULFATE", False), allow) is True


def test_excipient_without_inn_is_excluded():
    allow = gate.load_allowlist(DATA / "legacy_allowlist.tsv")
    assert gate.is_moiety(_cand("MICROCRYSTALLINE CELLULOSE", False), allow) is False


def test_inn_display_name_uses_crosswalk_for_divergent_us_name():
    xw = gate.load_crosswalk(DATA / "usan_inn_crosswalk.tsv")
    assert gate.inn_display_name(_cand("ACETAMINOPHEN", True), xw) == "paracetamol"


def test_inn_display_name_lowercases_harmonized_name():
    xw = gate.load_crosswalk(DATA / "usan_inn_crosswalk.tsv")
    assert gate.inn_display_name(_cand("AMLODIPINE", True), xw) == "amlodipine"
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_gate.py -v`
Expected: FAIL (module not found).

- [ ] **Step 4: Write minimal implementation**

```python
# src/drugref/ingest/gate.py
"""The moiety-membership gate and INN display-name resolution (design §6.1).

Gate: a substance is an active drug moiety iff it HAS a WHO INN (UNII's INN_ID
signal) OR it is on the small, closed legacy allow-list of pre-INN drugs
(magnesium sulfate, ...). Everything else (excipients, foods) is excluded.

INN display name: for harmonized drugs the UNII preferred term IS the INN once
case-folded; for the closed historical USAN<->INN divergences
(acetaminophen -> paracetamol) the hand-curated crosswalk overrides. This is
why slice 1 needs no WHO INN bulk-list: the gate signal comes from UNII, and
the display name from (UNII PT, overridden by the divergence crosswalk).
"""
import csv
import pathlib

from drugref.ingest.unii import MoietyCandidate


def _norm(name: str) -> str:
    """Case/space-fold a name for lookup and comparison."""
    return " ".join(name.strip().lower().split())


def load_crosswalk(path: str | pathlib.Path) -> dict[str, str]:
    """Load the closed USAN->INN divergence map, keyed on the normalized US name."""
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            out[_norm(row["us_name"])] = row["inn"].strip()
    return out


def load_allowlist(path: str | pathlib.Path) -> set[str]:
    """Load the closed legacy-drug allow-list (normalized names)."""
    with open(path, encoding="utf-8") as fh:
        return {_norm(line) for line in fh if line.strip()}


def is_moiety(cand: MoietyCandidate, allowlist: set[str]) -> bool:
    """True iff the candidate is an active drug moiety (design §6.1 gate)."""
    return cand.has_inn or _norm(cand.preferred_name) in allowlist


def inn_display_name(cand: MoietyCandidate, crosswalk: dict[str, str]) -> str:
    """The INN-preferred display label: crosswalk override, else the folded PT."""
    return crosswalk.get(_norm(cand.preferred_name), cand.preferred_name.strip().lower())
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_gate.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/gate.py src/drugref/data/ tests/test_gate.py
git commit -m "feat(ingest): has-INN membership gate + closed USAN->INN display crosswalk"
```

---

### Task 7: Ingest orchestrator (the acceptance matrix)

**Files:**
- Create: `src/drugref/ingest/run.py`, `tests/test_ingest_run.py`

**Interfaces:**
- Consumes: `db`, `ids`, `claims`, `unii`, `gate`.
- Produces: `run.ingest_unii(conn, *, unii_path, crosswalk_path, allowlist_path, upstream_release) -> int` (returns the count of moieties registered).

- [ ] **Step 1: Write the failing test (full acceptance matrix)**

```python
# tests/test_ingest_run.py
import pathlib
from drugref.ingest import run
from drugref import ids

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")
XW = DATA / "usan_inn_crosswalk.tsv"
AL = DATA / "legacy_allowlist.tsv"


def _ingest(conn, release="2026-07"):
    return run.ingest_unii(conn, unii_path=FIX, crosswalk_path=XW,
                           allowlist_path=AL, upstream_release=release)


def test_registers_only_gated_moieties(conn):
    n = _ingest(conn)
    # acetaminophen + amlodipine (has-INN) + magnesium sulfate (allow-list) = 3;
    # microcrystalline cellulose excluded.
    assert n == 3
    names = {r[0] for r in conn.execute("SELECT display_name FROM drugref.substance_moiety").fetchall()}
    assert names == {"paracetamol", "amlodipine", "magnesium sulfate"}
    assert "microcrystalline cellulose" not in names


def test_cross_reference_claims_present(conn):
    _ingest(conn)
    m = ids.mint_moiety_uuid("362O9ITL9D")  # acetaminophen
    claims = dict(conn.execute(
        "SELECT scheme, value FROM drugref.identity_claim WHERE moiety_uuid = %s", (m,)).fetchall())
    assert claims["UNII"] == "362O9ITL9D"
    assert claims["INN"] == "paracetamol"
    assert claims["CAS"] == "103-90-2"
    assert claims["RXNORM_IN"] == "161"
    assert claims["PUBCHEM_CID"] == "1983"


def test_reingest_is_idempotent(conn):
    _ingest(conn)
    _ingest(conn)  # run again — same UUIDs, no duplicate claims
    n_moiety = conn.execute("SELECT count(*) FROM drugref.substance_moiety").fetchone()[0]
    n_claim = conn.execute("SELECT count(*) FROM drugref.identity_claim").fetchone()[0]
    assert n_moiety == 3
    # acetaminophen: UNII+INN+CAS+RXNORM_IN+PUBCHEM_CID+INCHIKEY = 6; amlodipine = 6;
    # magnesium sulfate (no INN): UNII+CAS+RXNORM_IN = 3. Total = 15.
    assert n_claim == 15


def test_immortality_uuid_survives_upstream_rxcui_remap(conn, tmp_path):
    _ingest(conn)
    m = ids.mint_moiety_uuid("362O9ITL9D")
    # Simulate a new upstream release where acetaminophen's RxCUI changed.
    remapped = tmp_path / "unii_remap.tsv"
    remapped.write_text(
        "UNII\tPT\tRN\tRXCUI\tPUBCHEM\tINN_ID\tINCHIKEY\n"
        "362O9ITL9D\tACETAMINOPHEN\t103-90-2\t999999\t1983\t6689\tRZVAJINKPMORJF-UHFFFAOYSA-N\n")
    run.ingest_unii(conn, unii_path=remapped, crosswalk_path=XW, allowlist_path=AL,
                    upstream_release="2026-08")
    # Same UUID (unchanged); the new RxCUI is an ADDED claim, the old one retained.
    still = conn.execute("SELECT count(*) FROM drugref.substance_moiety WHERE moiety_uuid = %s", (m,)).fetchone()[0]
    assert still == 1
    rxcuis = {r[0] for r in conn.execute(
        "SELECT value FROM drugref.identity_claim WHERE moiety_uuid = %s AND scheme = 'RXNORM_IN'", (m,)).fetchall()}
    assert rxcuis == {"161", "999999"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `DRUGREF_TEST_DSN=$DRUGREF_TEST_DSN uv run pytest tests/test_ingest_run.py -v`
Expected: FAIL (module `drugref.ingest.run` not found).

- [ ] **Step 3: Write minimal implementation**

```python
# src/drugref/ingest/run.py
"""Orchestrate one slice-1 ingest run: gate -> mint -> claim.

For each UNII row that passes the moiety gate: mint (or recognise) the immortal
moiety_uuid, refresh its display-name cache, and append its identity claims
(UNII, INN, and the cheap cross-references). Idempotent and immortal: re-running
adds nothing new for unchanged data, and upstream churn attaches new claims
without ever re-keying an existing moiety.
"""
import hashlib
import pathlib

import psycopg

from drugref import claims, ids
from drugref.ingest import gate, unii


def _checksum(path: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def ingest_unii(conn: psycopg.Connection, *, unii_path, crosswalk_path,
                allowlist_path, upstream_release: str) -> int:
    """Ingest one UNII file. Returns the number of moieties registered/seen."""
    crosswalk = gate.load_crosswalk(crosswalk_path)
    allowlist = gate.load_allowlist(allowlist_path)

    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('UNII', %s, %s) RETURNING ingest_run_id",
        (upstream_release, _checksum(unii_path))).fetchone()[0]

    count = 0
    for cand in unii.parse(unii_path):
        if not gate.is_moiety(cand, allowlist):
            continue
        count += 1
        moiety_uuid = ids.mint_moiety_uuid(cand.unii)          # deterministic at seed
        claims.upsert_moiety(conn, moiety_uuid, gate.inn_display_name(cand, crosswalk), run_id)
        claims.add_claim(conn, moiety_uuid, "UNII", cand.unii, run_id)
        if cand.has_inn:
            claims.add_claim(conn, moiety_uuid, "INN", gate.inn_display_name(cand, crosswalk), run_id)
        for scheme, value in cand.cross_refs.items():
            claims.add_claim(conn, moiety_uuid, scheme, value, run_id)

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s", (run_id,))
    conn.commit()
    return count
```

- [ ] **Step 4: Run to verify it passes**

Run: `DRUGREF_TEST_DSN=$DRUGREF_TEST_DSN uv run pytest tests/test_ingest_run.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the whole suite**

Run: `DRUGREF_TEST_DSN=$DRUGREF_TEST_DSN uv run pytest -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/run.py tests/test_ingest_run.py
git commit -m "feat(ingest): UNII ingest orchestrator — gate, mint, append-only claims"
```

---

### Task 8: ChEBI enrichment join (attach ChEBI IDs)

**Files:**
- Create: `src/drugref/ingest/chebi.py`, `tests/fixtures/chebi_subset.tsv`, `tests/test_chebi.py`

**Interfaces:**
- Consumes: an existing moiety registry with `INCHIKEY` claims (from Task 7).
- Produces: `chebi.enrich_from_chebi(conn, *, chebi_path, upstream_release) -> int` (count of CHEBI claims added).

> This is the lowest-priority slice-1 task: the user asked to capture the ChEBI identifier too (cheap cross-walk value). It joins ChEBI to already-registered moieties by **InChIKey** (both UNII and ChEBI carry it), so it needs no re-gating. If real ChEBI SDF parsing proves heavy, it may be split to a follow-up; the core spine (Tasks 1-7) stands without it.

- [ ] **Step 1: Write the fixture** (`tests/fixtures/chebi_subset.tsv`)

```
CHEBI_ID	INCHIKEY
CHEBI:46195	RZVAJINKPMORJF-UHFFFAOYSA-N
CHEBI:2668	HTIQEAQVCYTUBX-UHFFFAOYSA-N
```
(ChEBI:46195 = paracetamol, CHEBI:2668 = amlodipine — matched to moieties by InChIKey.)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_chebi.py
import pathlib
from drugref.ingest import run, chebi
from drugref import ids

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"
CHEBI_FIX = pathlib.Path(__file__).parent / "fixtures" / "chebi_subset.tsv"
DATA = pathlib.Path("src/drugref/data")


def test_chebi_claim_attached_by_inchikey(conn):
    run.ingest_unii(conn, unii_path=FIX, crosswalk_path=DATA / "usan_inn_crosswalk.tsv",
                    allowlist_path=DATA / "legacy_allowlist.tsv", upstream_release="2026-07")
    added = chebi.enrich_from_chebi(conn, chebi_path=CHEBI_FIX, upstream_release="chebi-2026-07")
    assert added == 2
    m = ids.mint_moiety_uuid("362O9ITL9D")  # paracetamol
    val = conn.execute(
        "SELECT value FROM drugref.identity_claim WHERE moiety_uuid = %s AND scheme = 'CHEBI'", (m,)).fetchone()[0]
    assert val == "CHEBI:46195"
```

- [ ] **Step 3: Run to verify it fails**

Run: `DRUGREF_TEST_DSN=$DRUGREF_TEST_DSN uv run pytest tests/test_chebi.py -v`
Expected: FAIL (module `drugref.ingest.chebi` not found).

- [ ] **Step 4: Write minimal implementation**

```python
# src/drugref/ingest/chebi.py
"""Attach ChEBI identifiers to already-registered moieties.

ChEBI (CC BY 4.0) is joined to the moiety registry by InChIKey -- a structural
key both UNII and ChEBI carry -- so no re-gating is needed: if a moiety already
has an INCHIKEY claim matching a ChEBI entry, we attach that ChEBI id as another
cross-reference claim. This is the cheap public-cross-walk value the user asked
for; it does not mint or gate moieties.
"""
import hashlib
import pathlib
import csv

import psycopg

from drugref import claims


def enrich_from_chebi(conn: psycopg.Connection, *, chebi_path, upstream_release: str) -> int:
    """Add a CHEBI claim to each moiety whose INCHIKEY matches a ChEBI row.
    Returns the number of CHEBI claims added (idempotent on re-run)."""
    checksum = hashlib.sha256(pathlib.Path(chebi_path).read_bytes()).hexdigest()
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('CHEBI', %s, %s) RETURNING ingest_run_id",
        (upstream_release, checksum)).fetchone()[0]

    added = 0
    with open(chebi_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            inchikey = row["INCHIKEY"].strip()
            chebi_id = row["CHEBI_ID"].strip()
            # Find the moiety carrying this InChIKey (structural identity join).
            hit = conn.execute(
                "SELECT moiety_uuid FROM drugref.identity_claim "
                "WHERE scheme = 'INCHIKEY' AND value = %s", (inchikey,)).fetchone()
            if hit is None:
                continue
            before = conn.execute(
                "SELECT count(*) FROM drugref.identity_claim "
                "WHERE moiety_uuid = %s AND scheme = 'CHEBI' AND value = %s",
                (hit[0], chebi_id)).fetchone()[0]
            claims.add_claim(conn, hit[0], "CHEBI", chebi_id, run_id)
            if before == 0:
                added += 1

    conn.execute("UPDATE drugref.ingest_run SET finished_at = now() WHERE ingest_run_id = %s", (run_id,))
    conn.commit()
    return added
```

- [ ] **Step 5: Run to verify it passes**

Run: `DRUGREF_TEST_DSN=$DRUGREF_TEST_DSN uv run pytest tests/test_chebi.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add src/drugref/ingest/chebi.py tests/fixtures/chebi_subset.tsv tests/test_chebi.py
git commit -m "feat(ingest): ChEBI enrichment — attach ChEBI ids by InChIKey"
```

---

## Post-implementation: verify-before-production checklist

These are **not** slice-1 code tasks but must be recorded so they are not lost (design §6 + §6.1):

- [ ] Confirm the real UNII data file's exact column headers (esp. `INN_ID`) against a fresh download; adjust `unii._CROSS_REF_COLUMNS` / the `INN_ID` key if renamed.
- [ ] Confirm the ChEBI CC BY 4.0 deed and UNII/GSRS distribution terms; ensure `NOTICE` attributions are accurate.
- [ ] Expand `usan_inn_crosswalk.tsv` and `legacy_allowlist.tsv` from the seed subset toward the full closed sets (a one-time curation pass; audit yield with the RxNorm-IN / ChEBI-drug-role cross-check signals from design §6.1).

---

## Self-Review

**Spec coverage:**
- §2 hybrid store (rebuildable feed half) → Tasks 5-8 (ingest) + Task 2 (append-only identity claims) ✓
- §3 slice-1 = moiety + identity claims only; schema admits later tiers → Task 2 tables ✓
- §4 three tables → Task 2 ✓
- §5 own UUID, UUIDv5-at-seed keyed on UNII, pinned, per-level namespace → Task 3; immortality → Task 7 test ✓
- §6 seeding: UNII backbone, INN display anchor, RxCUI demoted to claim, ChEBI cross-ref, closed crosswalk → Tasks 5-8 ✓
- §6.1 membership gate = has-INN + legacy allow-list; RxNorm-IN/ChEBI as audit cross-checks (deferred to checklist) → Task 6 ✓
- §7 Python ingest + in-DB append-only floor → Tasks 2, 4-7 ✓
- §8 testing: mint, claims, idempotency, immortality, append-only floor, membership gate, crosswalk → Tasks 2-7 tests ✓
- §9 out-of-scope (salts, classes, DDI, API, local tier, ATC, Cairn wiring) → not built ✓

**Placeholder scan:** no TBD/TODO in code steps; every code step shows complete code; the "verify-before-production" items are explicitly out-of-band, not code placeholders. ✓

**Type consistency:** `MoietyCandidate(unii, preferred_name, has_inn, cross_refs)` used identically in Tasks 5/6/7; `mint_moiety_uuid(unii)` consistent Tasks 3/7/8; `add_claim(conn, moiety_uuid, scheme, value, ingest_run_id)` consistent Tasks 4/7/8; scheme strings (`UNII/INN/CAS/RXNORM_IN/PUBCHEM_CID/INCHIKEY/CHEBI`) consistent across parser, gate, orchestrator, and DB comment. ✓

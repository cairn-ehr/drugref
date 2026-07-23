# HANDOVER — drugref

> **Disposable working scaffolding, NOT a source of truth.** The canonical *what/why* for slice 1 is the
> design spec [`docs/superpowers/specs/2026-07-23-drugref-global-moiety-spine-design.md`](superpowers/specs/2026-07-23-drugref-global-moiety-spine-design.md)
> and its implementation plan alongside it. If this file disagrees with the spec, the spec wins.
> Regenerate this file at the end of every working session (nextsession rule 9).

## What drugref is

**drugref.org v2** — an open, co-equal **public-good drug-information service** (any EHR / pharmacy / app can
consume it; Cairn is its first client on the same public-API footing — the "steward uses only the public API"
posture). Two tiers: a **global tier** (jurisdiction-independent — substance identity, chemistry, classes,
interactions) built first, and a later **local tier** (country-specific packaging/pricing, e.g. Australian
PBS/TGA). It is designed to co-reside in a Cairn deployment's PostgreSQL **or** run standalone, but it is
**advisory reference data — never on Cairn's signed inter-node wire core** (a licence-encumbered source can
attach node-locally without ever contaminating interoperability).

## ⇒ NEXT

**Slice 1 (the active-moiety identity spine) is ✅ COMPLETE** on branch `feat/slice-1-moiety-spine`
(12 feature commits, **30 tests green** with the DB DSN set; final whole-branch review passed, verdict
"merge with fixes" — all fixes applied, 0 Critical). **It is not yet merged / PR'd** — the immediate next
step is to finish the branch (merge to `main` or open a PR) and then **file the deferred follow-ups as
GitHub issues** (list below). After that, the next build slice is **Slice 2 — the class DAG + membership**
(see [ROADMAP.md](ROADMAP.md)).

## Current state (what slice 1 delivered)

A Postgres schema `drugref` + a Python ingest that stands up a registry of **active drug moieties**, each with
an **immortal UUID** and **append-only external-identifier claims**, seeded international-by-construction.

- **Own immortal `moiety_uuid`**, never keyed on a name (principle 2): `UUIDv5` derived deterministically from
  the moiety's **UNII** at first sighting (so independent instances agree with no central registry), then
  **pinned forever** — upstream churn attaches new claims, never re-keys. Namespace is domain-derived
  (frozen literal `d07651ee-311d-552b-a97b-591219eb3ad3`).
- **External IDs are append-only claims** (UNII, INN, RXNORM_IN, CAS, PUBCHEM_CID, INCHIKEY, CHEBI) — drugref
  doubles as a public identifier cross-walk.
- **Membership gate = `has-INN`** (UNII `INN_ID` column) **OR** a small closed legacy allow-list
  (magnesium sulfate, …); excipients/foods excluded.
- **International-by-construction seeding**: UNII (public domain) is the identity backbone; INN the display
  anchor; ChEBI (CC BY 4.0) chemistry + cross-refs; **RxNorm demoted to a claim** (not the naming backbone);
  a closed hand-curated **USAN↔INN crosswalk** (acetaminophen→paracetamol) — a one-time, non-growing asset.
- **Append-only floor in the database** (triggers): `moiety_uuid` immortal, DELETE forbidden, `identity_claim`
  mutable only via `superseded_by`. **Scope note:** the floor covers **row-level UPDATE/DELETE only** —
  `TRUNCATE` and the table-owning role remain bypasses for slice 1 (rebuildable data; closed later via RLS +
  privilege separation — see the follow-ups).

## Architecture in one breath (holds for all future slices)

- **Hybrid store** mirroring a Cairn node: **rebuildable projections** for ingested feeds (drop-and-rebuild,
  version-pinned, provenance-tagged via `ingest_run`) + an **append-only, signed overlay** for curated
  knowledge (the DDI moat — Slice 5, not built yet).
- **Two orthogonal structures**: a **composition tree** (moiety → salt → clinical drug → product) and an
  orthogonal **classification DAG** (class ⊂ class; moiety ∈ many classes, many-to-many). The curated overlay
  attaches to either and **inherits along the edges** (down the tree, up through a moiety's classes) — the
  key curation-economy lever, esp. for class×class interactions. Slice 1 builds only the moiety top-node.
- **Substrate**: Python 3.12 + `uv`, `psycopg` v3, PostgreSQL ≥ 18. Advisory/fit-for-purpose tier (fast
  iteration on brittle feed parsing) but **integrity is enforced in the DB, not app code**.

## How to run / test

```bash
uv sync
uv run pytest                      # unit tests run anywhere; DB-gated tests SKIP without a DSN
# DB-gated tests need a PostgreSQL >= 18 database:
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
```

- Schema lives in `db/001_schema_drugref.sql` (applied via `drugref.db.apply_migrations`, idempotent).
- Code: `src/drugref/{ids,claims,db}.py` + `src/drugref/ingest/{unii,gate,run,chebi}.py`; seed data files under
  `src/drugref/data/`; fixtures under `tests/fixtures/`.
- Current dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.

## Coding rules

See the **nextsession** skill (`.claude/skills/nextsession/SKILL.md`) — TDD-first, pure reusable functions,
junior-legible inline docs, files < 500 lines, no silent tech debt (fix or file an issue), all tests green
before commit, **AGPL-3.0 + AGPL-compatible sources only (check the licence before bundling)**, keep this file
and ROADMAP current.

## Open follow-ups (file these as GitHub issues before/at next session)

- **Floor hardening** — close the `TRUNCATE` + table-owning-role bypass via **RLS + privilege separation**
  (the full floor the design §7 always envisioned; recorded as design §10 tension **G**). Add a
  `BEFORE TRUNCATE` guard + rework the two commit-internally test modules' cleanup off `TRUNCATE`.
- **ChEBI InChIKey lookup** (`ingest/chebi.py`) needs a `superseded_by IS NULL` / `ORDER BY` filter once the
  overlay/correction path exists (pre-existing codebase-wide gap — nothing filters `superseded_by` yet).
- **Batch-commit ingest** — `ingest/run.py` loads a whole file in one transaction (fine for fixtures; a real
  UNII file is hundreds of thousands of rows). Batch commits per N rows for production.
- **Verify-before-production** (from the spec §6/§6.1) — confirm the real UNII data-file header names (esp.
  `INN_ID` presence/population) against a fresh download; confirm the ChEBI CC BY 4.0 deed + UNII/GSRS
  distribution terms; expand the USAN↔INN crosswalk + legacy allow-list from seed subsets toward the full
  closed sets.
- **Cosmetic minors** — pyproject SPDX-string `license` form + `readme` field; `claims.py` module-docstring
  wording ("never UPDATE the immortal columns") + `cur:` → `conn:` rename; `gate.inn_display_name` fallback
  should use `_norm()` (collapse internal whitespace).

## Repo facts

- GitHub: `cairn-ehr/drugref` · default branch `main` · licence **AGPL-3.0** · attribution in `NOTICE`.
- No CLAUDE.md yet (the coding rules live in the nextsession skill); no ADR log yet (slice 1's *why* is the
  design spec). Consider adding both as the project grows.

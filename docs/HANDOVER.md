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

**Slice 1** (active-moiety identity spine) is ✅ merged (PR #1, `14c40ec`). **Slice 2a** (MED-RT
classification DAG + membership) is ✅ built on this branch — **91 tests green** with the DB DSN set.

The next build slice is **Slice 2b — MeSH Pharmacological Actions** (see [ROADMAP.md](ROADMAP.md)). It needs
no schema change; it is blocked on a **UNII→MeSH (or ChEBI→MeSH) bridge**, because MeSH membership has no
RxCUI to join through the way MED-RT does. MeSH's licence is already verified AGPL-compatible.

**Open follow-ups are GitHub issues [#2](https://github.com/cairn-ehr/drugref/issues/2)–
[#5](https://github.com/cairn-ehr/drugref/issues/5)** plus the slice-2a ones filed with this work.

## Current state

**Slice 1 — the identity spine.** A Postgres schema `drugref` + Python ingest standing up a registry of
**active drug moieties**, each with an **immortal UUID** and **append-only external-identifier claims**,
seeded international-by-construction.

**Slice 2a — the classification layer.** Three more tables (`substance_class`, `class_parent`,
`class_membership`) seeded from **MED-RT**, giving every moiety its pharmacologic classes on six axes.
Against the full 2026.07.06 release: **3,634 classes, 3,961 DAG edges (440 multi-parent), 27,540
memberships over 6,012 ingredients**, parsed in ~4s.

- **Class identity is immortal by determinism**: `class_uuid = UUIDv5(CLASS_NAMESPACE, "MEDRT:"+NUI)`, its
  own per-level namespace. No pin table needed — a rebuild re-derives the same UUIDs.
- **Class edges are rebuildable projections**, deliberately **outside** slice 1's append-only floor: a new
  release deletes this source's edges and re-inserts, so a parent removed upstream is removed here.
- **Membership joins via the `RXNORM_IN` claims slice 1 already recorded** — no new bridge data. MED-RT
  ingredient concepts are keyed on RxCUI. Unmatched RxCUIs are **counted, never silently dropped**.
- **Licence scoping is structural**: only MED-RT concepts are *defined* in the release (SNOMED/MeSH appear
  solely as edge endpoints), so requiring both endpoints of every edge to be an ingested class is what keeps
  unlicensed content out — not good intentions.

### Three things the MED-RT documentation got wrong (verified against the real release)

Recorded because they are invisible to a hand-written fixture and would each be a silent, plausible bug:

1. **`Parent Of` runs parent → child**, not child → parent. Verified two ways (the MoA root appears as
   `from_code` 9× and as `to_code` never; `"A [Preparations]"` is the *from* of paracetamol). The reverse
   reading inverts the whole DAG.
2. **`[HC]` concepts are the 26 alphabetical navigation bins** (`"A [Preparations]"`), not classifications —
   18,450 of 21,058 class→ingredient edges. Ingesting them files nearly every drug under a letter.
3. **EPC membership is licence-clean** and hierarchical (`Parent Of` from the EPC to the ingredient),
   *not* routed through SNOMED/MeSH mappings as first assumed. EPC is the most clinically recognisable axis,
   so it is in scope, normalised to `has_EPC`.

**The fixture is therefore extracted from the real release** by a committed, re-runnable extractor
(`tests/fixtures/make_medrt_subset.py`), so it can never re-encode a wrong assumption about upstream shape.

## What slice 1 delivered (detail)

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
  key curation-economy lever, esp. for class×class interactions. Slice 1 built the moiety top-node; slice 2a
  built the classification DAG, so class-level curation now has something to attach to.
- **Substrate**: Python 3.12 + `uv`, `psycopg` v3, PostgreSQL ≥ 18. Advisory/fit-for-purpose tier (fast
  iteration on brittle feed parsing) but **integrity is enforced in the DB, not app code**.

## How to run / test

```bash
uv sync
uv run pytest                      # unit tests run anywhere; DB-gated tests SKIP without a DSN
# DB-gated tests need a PostgreSQL >= 18 database:
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
```

- Schema: `db/001_schema_drugref.sql` (identity spine) + `db/002_schema_classes.sql` (classification),
  applied in filename order via `drugref.db.apply_migrations`, idempotent.
- Code: `src/drugref/{ids,claims,classes,db}.py` + `src/drugref/ingest/{unii,gate,run,chebi,medrt,medrt_run}.py`;
  seed data under `src/drugref/data/`; fixtures under `tests/fixtures/`.
- Current dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
- **Upstream feed files are NOT committed** (`downloads/` is gitignored — a MED-RT release is ~45 MB).
  Fetch MED-RT from [NCI EVS](https://evs.nci.nih.gov/ftp1/MED-RT/) (`Core_MEDRT_*_XML.zip`) and regenerate
  the test fixture with:

  ```bash
  python tests/fixtures/make_medrt_subset.py <Core_MEDRT_*.xml> > tests/fixtures/medrt_subset.xml
  ```

## Coding rules

See the **nextsession** skill (`.claude/skills/nextsession/SKILL.md`) — TDD-first, pure reusable functions,
junior-legible inline docs, files < 500 lines, no silent tech debt (fix or file an issue), all tests green
before commit, **AGPL-3.0 + AGPL-compatible sources only (check the licence before bundling)**, keep this file
and ROADMAP current.

## Open follow-ups

Post-review (PR #1) findings that were larger than a cleanup are now filed as GitHub issues:

- **Floor hardening** ([#2](https://github.com/cairn-ehr/drugref/issues/2)) — close the `TRUNCATE` +
  table-owning-role bypass via **RLS + privilege separation** (design §7 / §10 tension **G**); includes
  reworking the commit-internally test modules' cleanup off `TRUNCATE`.
- **UNII-change immortality** ([#3](https://github.com/cairn-ehr/drugref/issues/3)) — `moiety_uuid` survives
  every identifier's churn *except* a change to the UNII itself; structural re-key (by InChIKey) is deferred.
- **One-way supersession** ([#4](https://github.com/cairn-ehr/drugref/issues/4)) — the floor lets
  `superseded_by` be un-set / re-pointed; decide whether that's an invariant to enforce.
- **INN sourced from UNII PT, not WHO INN** ([#5](https://github.com/cairn-ehr/drugref/issues/5)) — part of
  the verify-before-production checklist (real UNII headers/`INN_ID`, ChEBI/UNII licence deeds, grow the
  closed crosswalk + allow-list).
- **Batch-commit ingest** — `ingest/run.py` loads a whole file in one transaction (fine for fixtures; a real
  UNII file is hundreds of thousands of rows). `ingest/medrt_run.py` has the same shape, and its parser
  additionally holds the entire 45 MB XML in memory via `ElementTree.parse`. Batch commits and, for MED-RT,
  `iterparse` before production.

Slice-2a follow-ups (filed with this work):

- **MED-RT licence deed** — the public-domain determination rests on federal authorship + UMLS restriction
  level 0 + open EVS distribution. NLM's formal source-release doc was HTTP 502 at design time, and the
  distribution ships **no** licence/terms file. Re-confirm against the live NLM deed before production.
- **Class-level `has_*` assertions unused** — MED-RT also asserts `MED-RT → MED-RT` `has_MoA`/`has_PE`/
  `has_TC` (an EPC declaring its own mechanism/effect, ~756 edges). These describe *classes*, so they are
  the natural substrate for letting curated knowledge inherit along the DAG (the Slice 5 economy lever).
- **Verify against the next MED-RT release** — the parser's namespace/CTY/relationship handling was
  validated against `2026.07.06`. Re-run `make_medrt_subset.py` and the suite when the release rolls.

Fixed in the post-review pass (no longer open): ChEBI InChIKey lookup now filters `superseded_by IS NULL` and
attaches to *all* matching moieties; `add_claim` reports insert-vs-conflict (no per-row probe); `db.connect`
raises a clear error on missing DSN; `gate.inn_display_name` folds via `_norm()`; `claims.py` docstring +
`conn` param naming; pyproject SPDX `license` string + `readme`/`license-files`.

## Repo facts

- GitHub: `cairn-ehr/drugref` · default branch `main` · licence **AGPL-3.0** · attribution in `NOTICE`.
- No CLAUDE.md yet (the coding rules live in the nextsession skill); no ADR log yet (slice 1's *why* is the
  design spec). Consider adding both as the project grows.

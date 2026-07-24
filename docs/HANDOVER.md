# HANDOVER — drugref

> **Disposable working scaffolding, NOT a source of truth.** The canonical *what/why* lives in the design
> specs under [`docs/superpowers/specs/`](superpowers/specs/) — slice 1
> ([moiety spine](superpowers/specs/2026-07-23-drugref-global-moiety-spine-design.md)), slice 2a
> ([MED-RT classification](superpowers/specs/2026-07-23-drugref-slice-2a-medrt-classification-design.md)) and
> slice 2b ([MeSH PA](superpowers/specs/2026-07-24-drugref-slice-2b-mesh-pa-design.md)).
> If this file disagrees with a spec, the spec wins.
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

**Slice 1** ✅ merged (PR #1). **Slice 2a** (MED-RT classification) ✅ merged (PR #9).
**Slice 2a.1 — the source-neutral class registry** ✅ merged (PR #10).
**Slice 2b — MeSH PA: measurement + design + fixture generator** ✅ done on this branch: the real MeSH 2026
release is measured, the [slice-2b design spec](superpowers/specs/2026-07-24-drugref-slice-2b-mesh-pa-design.md)
is written against those measurements, and a committed re-runnable fixture generator + fixtures are in
place. **134 tests green** — a stdlib shape-pin test (`tests/test_mesh_fixture_shape.py`, no parser/DB)
now locks the committed fixtures against drift; the slice-2b parser itself is still next.

**What remains for slice 2b = the BUILD: the MeSH parser + membership bridge + orchestrator + tests**, TDD
against the now-approved spec §6–§8. The bridge is **no longer an open question** — it is designed and
verified end-to-end against the fixture (§5.3): **two-key, UNII-primary, CAS-fallback**, both keys already
slice-1 `identity_claim` rows, **no new external source**. No schema change (2a.1 already added
`PA`/`has_PA`/`MeSH`). Key measured facts that settled it (issue #11):

- **The doc-hypothesis was wrong.** MeSH **Descriptors DO carry UNIIs** in `RegistryNumber` — aspirin
  D001241 carries UNII `R16CO5Y76E` (not "CAS only"). Both Descriptors and SCRs carry UNIIs; the real split
  is per-record key-typing, not per-record-type.
- **568 PA classes** (all Descriptors), forming a **multi-parent DAG via MeSH tree-number nesting** (build
  `class_parent` like MED-RT). **10,505 distinct member substances** (7,667 SCR + 2,838 Descriptor);
  **73% expose a UNII or CAS** (joinable), **27% expose neither** (drug combinations / novel research
  compounds — counted, never dropped). The moiety gate is the binding constraint, same as MED-RT.

Full evidence: the spec §5, and the working measurement scripts + `FINDINGS.md` under the session
scratchpad (not committed). [issue #11](https://github.com/cairn-ehr/drugref/issues/11) is answered by this
work; close it when the build lands. **Real release files are gitignored** — see "How to run / test".

**Open follow-ups are GitHub issues [#2](https://github.com/cairn-ehr/drugref/issues/2)–
[#8](https://github.com/cairn-ehr/drugref/issues/8)** (plus the slice-2b `RelatedRegistryNumber` precision
pass, spec tension B — file when the build starts).

## Current state

**Slice 1 — the identity spine.** A Postgres schema `drugref` + Python ingest standing up a registry of
**active drug moieties**, each with an **immortal UUID** and **append-only external-identifier claims**,
seeded international-by-construction.

**Slice 2a — the classification layer.** Three more tables (`substance_class`, `class_parent`,
`class_membership`) seeded from **MED-RT**, giving every moiety its pharmacologic classes on six axes.
Against the full 2026.07.06 release: **3,634 classes, 3,961 DAG edges (440 multi-parent), 27,540
memberships over 6,012 ingredients**, parsed in ~4s.

- **Class identity is immortal by determinism**: `class_uuid = UUIDv5(CLASS_NAMESPACE, "MEDRT:"+NUI)` for
  MED-RT, `UUIDv5(CLASS_NAMESPACE, SOURCE+":"+code)` in general — its own per-level namespace. No pin
  table needed — a rebuild re-derives the same UUIDs.
- **Class edges are rebuildable projections**, deliberately **outside** slice 1's append-only floor: a new
  release deletes this source's edges and re-inserts, so a parent removed upstream is removed here.
- **Membership joins via the `RXNORM_IN` claims slice 1 already recorded** — no new bridge data. MED-RT
  ingredient concepts are keyed on RxCUI. Unmatched RxCUIs are **counted, never silently dropped**.
- **Licence scoping is structural**: only MED-RT concepts are *defined* in the release (SNOMED/MeSH appear
  solely as edge endpoints), so requiring both endpoints of every edge to be an ingested class is what keeps
  unlicensed content out — not good intentions.

**Slice 2a.1 — the registry made source-neutral.** 2a named the class registry after its one authority
(`medrt_nui`/`medrt_code`, a global UNIQUE, MED-RT-only axis CHECKs, a `"MEDRT:"` prefix hard-coded into
minting), so no second authority could enter it. `db/003` renames those columns to `source_code` /
`published_code`, adds a NOT NULL `source`, moves uniqueness to **per (source, source_code)**, and widens
the CHECKs with `PA` / `has_PA`. `ids.mint_class_uuid(source, code)` replaces the NUI-only form.

- **Existing MED-RT class UUIDs are unchanged, and that is the invariant of the whole refactor.** Class
  UUIDs are the join key of `class_parent` and `class_membership`, and the projection is dropped and
  rebuilt on every ingest — so a drift in the derivation would silently re-key 3,634 classes and orphan
  every edge, with no error anywhere. `ids._SOURCE_KEY_PREFIX` therefore maps `MED-RT → "MEDRT"` (the
  prefix 2a minted with), and three **frozen UUID literals** captured before the refactor pin it.
- **The stored `source` and the UUID key derive from one canonicalisation** (`ids.canonical_source`), so a
  second spelling of one authority (`"MESH"` beside `"MeSH"`) can't share a `class_uuid` yet be stored as
  two strings and split a per-source rebuild. A `db/003` CHECK on `substance_class.source` is the floor:
  extend it **and** `ids._SOURCE_CANONICAL` together when a new authority lands.
- **db/003 is a separate migration, not an edit to 002**, because 002 uses `CREATE TABLE IF NOT EXISTS`:
  an edit there would never reach a database that already ran it. Every statement is guarded — the
  constraint steps skip the drop/add entirely once the widened shape is present, so a replay neither errors
  nor rescans — and tests replay the migrations both over an already-migrated row and over a populated
  pre-rename table (with an edge) to prove the renames survive.

**Slice 2b — MeSH PA: measured + designed (parser not yet built).** The real MeSH 2026 release was
measured (issue #11), the [design spec](superpowers/specs/2026-07-24-drugref-slice-2b-mesh-pa-design.md)
written against it, and a committed fixture generator (`tests/fixtures/make_mesh_subset.py`) + three
`mesh_*_subset.xml` fixtures + a `NOTICE` MeSH attribution landed. The membership **bridge** (MeSH PA has
no RxCUI) is settled: **two-key, UNII-primary → CAS-fallback**, resolving a member's `RegistryNumber` UNII
(else CAS) against slice-1 `identity_claim` rows — **no new external source**. PA classes form a
**multi-parent DAG via tree-number nesting** (built like MED-RT, both-endpoints-ingested scoping). The
build (parser + orchestrator + tests, TDD against the spec) is the next session's work; the fixture already
covers every acceptance case (positive UNII join, positive CAS-fallback join, key-not-in-registry, no-key,
multi-parent + root DAG).

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

- Schema: `db/001_schema_drugref.sql` (identity spine) + `db/002_schema_classes.sql` (classification) +
  `db/003_class_registry_source_neutral.sql` (registry generalised for a second authority), applied in
  filename order via `drugref.db.apply_migrations`, idempotent. **Read 003 for the class registry's actual
  shape** — 002 still shows the superseded MED-RT-specific columns.
- Code: `src/drugref/{ids,claims,classes,db}.py` + `src/drugref/ingest/{unii,gate,run,chebi,medrt,medrt_run}.py`;
  seed data under `src/drugref/data/`; fixtures under `tests/fixtures/`.
- Current dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
- **Upstream feed files are NOT committed** (`downloads/` is gitignored — a MED-RT release is ~45 MB).
  Fetch MED-RT from [NCI EVS](https://evs.nci.nih.gov/ftp1/MED-RT/) (`Core_MEDRT_*_XML.zip`) and regenerate
  the test fixture with:

  ```bash
  python tests/fixtures/make_medrt_subset.py <Core_MEDRT_*.xml> > tests/fixtures/medrt_subset.xml
  ```

- **MeSH release for slice 2b** (also NOT committed). Fetch the **compressed** files from
  [NLM](https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/) — `desc2026.gz` (16 MB),
  `supp2026.gz` (45 MB), `pa2026.xml` (5 MB) — and `gunzip` the two `.gz` (they decompress to `desc2026` /
  `supp2026`; rename to `.xml`) into `downloads/`. NLM throttles per connection hard; a segmented parallel
  fetch (byte-range `curl`) beats it ~18×. Regenerate the committed fixtures with:

  ```bash
  python tests/fixtures/make_mesh_subset.py downloads tests/fixtures/
  ```

  The committed `tests/fixtures/mesh_{desc,supp,pa}_subset.xml` are small extracts of the real release
  (all identity keys/tree numbers copied from the files, nothing invented; MeSH is attributed in `NOTICE`).

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
  `iterparse` before production ([#7](https://github.com/cairn-ehr/drugref/issues/7)).

Slice-2a follow-ups:

- **MED-RT licence deed** ([#6](https://github.com/cairn-ehr/drugref/issues/6)) — the public-domain determination rests on federal authorship + UMLS restriction
  level 0 + open EVS distribution. NLM's formal source-release doc was HTTP 502 at design time, and the
  distribution ships **no** licence/terms file. Re-confirm against the live NLM deed before production.
- **Class-level `has_*` assertions unused** ([#8](https://github.com/cairn-ehr/drugref/issues/8)) — MED-RT also asserts `MED-RT → MED-RT` `has_MoA`/`has_PE`/
  `has_TC` (an EPC declaring its own mechanism/effect, ~756 edges). These describe *classes*, so they are
  the natural substrate for letting curated knowledge inherit along the DAG (the Slice 5 economy lever).
- **Verify against the next MED-RT release** — the parser's namespace/CTY/relationship handling was
  validated against `2026.07.06`. Re-run `make_medrt_subset.py` and the suite when the release rolls.
  Regeneration must keep the endpoint redaction (a test enforces it) — see below.
- **Production ingest still writes row-at-a-time** ([#7](https://github.com/cairn-ehr/drugref/issues/7)) —
  the RxCUI index is now read once per run rather than per assertion, but classes, DAG edges and memberships
  are still individual `INSERT`s (~31k round trips on the full release). `executemany`/`COPY` belongs with
  the `iterparse` + batch-commit work.

Fixed in the slice-2a review pass (no longer open): the committed fixture no longer redistributes SNOMED CT
(or MeSH) terms and codes — `make_medrt_subset.py` redacts every endpoint outside MED-RT/RxNorm, and a test
pins it; the RxCUI membership join returns **all** claimants rather than an arbitrary first (matching
`chebi.py`, and removing a source of run-to-run non-determinism); the RxCUI index is read once per run
instead of once per assertion; `medrt_code` (since 2a.1: `published_code`) stores the published code and
edge endpoints resolve code → NUI, so a future divergence cannot silently empty the DAG; the parser
refuses (and counts) concepts
that are inactive or carry no identifier; `MedrtSummary` separates classes-in-release from classes-added.

Fixed in the slice-1 post-review pass (no longer open): ChEBI InChIKey lookup now filters `superseded_by IS NULL` and
attaches to *all* matching moieties; `add_claim` reports insert-vs-conflict (no per-row probe); `db.connect`
raises a clear error on missing DSN; `gate.inn_display_name` folds via `_norm()`; `claims.py` docstring +
`conn` param naming; pyproject SPDX `license` string + `readme`/`license-files`.

## Repo facts

- GitHub: `cairn-ehr/drugref` · default branch `main` · licence **AGPL-3.0** · attribution in `NOTICE`.
- No CLAUDE.md yet (the coding rules live in the nextsession skill); no ADR log yet (slice 1's *why* is the
  design spec). Consider adding both as the project grows.

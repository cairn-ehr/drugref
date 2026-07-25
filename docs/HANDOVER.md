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
**Slice 2b — MeSH PA: the BUILD** ✅ done on this branch (parser + bridge + orchestrator + tests, TDD
against spec §6–§8). **167 tests green.** The MeSH PA axis now ingests end-to-end: `ingest/mesh.py`
(pure streaming `iterparse` parser of pa/desc/supp → PA classes, tree-number DAG, memberships with
set-valued keys) + `ingest/mesh_run.py` (orchestrator + the two-key bridge) + the source-neutral
`ClassConcept` moved into `classes.py` with a new `moieties_by_scheme` join primitive.
[issue #11](https://github.com/cairn-ehr/drugref/issues/11) is answered — close it when this PR merges.

**Slice 5a — MED-RT CI_MoA/CI_PE contraindications** ✅ done on this branch (drugref's first drug-drug
interaction data). Design + plan:
[slice-5a spec](superpowers/specs/2026-07-25-drugref-slice-5a-medrt-contraindication-design.md) /
[plan](superpowers/plans/2026-07-25-slice-5a-medrt-contraindication.md). `db/004` adds the
`class_contraindication` rebuildable projection + the `ddi_candidate_pair` read-time expansion view;
`medrt.py` now emits `ContraindicationAssertion` for CI_MoA/CI_PE (both endpoints already ingested — **no
new source/join/UUID**); `interactions.py` is the single writer; `medrt_run.py` gained step 5 +
`contraindications`/`unmatched_ci_rxcuis` on `MedrtSummary`. **195 tests green.** Candidate tier only —
MED-RT does not track label updates (spec §4.3), so nothing here auto-alerts. `NOTICE` unchanged. Split the
ROADMAP Slice 5 into **5a** (this) / **5b** (MeSH-keyed CI_with/CI_ChemClass/may_treat/induces) / **5c**
(the curated signed overlay).

**Foundation review hardening** ✅ done on this branch. A full review of the whole codebase (not a diff),
with every finding reproduced against a live PG18 before fixing. **220 tests green.** Two new migrations and
a migration runner with a ledger; the rest is parser/writer hardening. What changed, and why it mattered:

- **`db/005`** — the correction overlay was a trapdoor in both directions. `identity_claim_unique` covered
  superseded rows, so a value upstream *reverted* could never be re-asserted (the INSERT hit the index,
  `add_claim` reported "already present", and the identifier stayed invisible to every
  `superseded_by IS NULL` join). Uniqueness is now **partial** (live claims only). The floor also now
  enforces that supersession is **one-way, same-moiety, and points at a later claim** — which makes a
  cycle unrepresentable. Plus: `first_seen_ingest` immutable, a CHECK on `ingest_run.source` (the key every
  per-source rebuild joins through, previously unconstrained), and indexes on that rebuild-delete path.
  **This closes [#4](https://github.com/cairn-ehr/drugref/issues/4).**
- **`db/006`** — the CHECK↔CASE coupling `db/004` held together with a comment is now structural: a
  **`ci_axis` table** the `relationship` column is a **foreign key into**, and the view JOINs it instead of
  a CASE. Widening the vocabulary without giving a predicate its membership axis now fails at write time
  rather than expanding to zero pairs silently (the 5b landmine). `source` joins the **primary key** (a
  second authority's identical assertion was swallowed by ON CONFLICT, then deleted by a MED-RT rebuild).
  View columns renamed `moiety_a/b` → **`subject_moiety`/`partner_moiety`**, `upstream_release` +
  `ingested_at` surfaced, and every clinical caveat moved into **`COMMENT ON`** — `--` comments are
  stripped by Postgres, so none of it was visible to anyone inspecting the database.
- **`db.apply_migrations` now keeps a ledger** (`drugref.schema_migration`, filename + checksum). Migrations
  are applied once and are **immutable afterwards** — editing an applied file raises. Before this, each file
  hand-wrote a guard inferring "has my change landed?" from the catalog, and `db/003`'s source-CHECK guard
  tested only that the constraint *existed*: editing it in place (which its own comment instructs) silently
  did nothing on an already-migrated database while a fresh one got the new constraint.
- **`ingest/unii.py` was the least defended code in the repo, at the root of identity.** A row with a blank
  UNII minted `UUIDv5(ns, "UNII:")` — one shared UUID that every such row collapsed onto, merging unrelated
  drugs into a single moiety carrying all their INNs, CAS numbers and RxCUIs; irreversible, because
  `moiety_uuid` is immortal and the floor forbids DELETE. Now refused via `gate.has_identity_key` and
  counted. Separately, `csv`'s default quoting let one stray double-quote swallow an unbounded run of
  following rows; all three TSV readers use `QUOTE_NONE`. `ingest_unii` returns a **`UniiSummary`**
  (moieties / gated_out / rows_without_unii) instead of a bare int.
- **`medrt.py`** — `nui_by_code` was last-write-wins, so two concepts publishing one `<code>` filed an edge
  against whichever came last: a `has_MoA` membership landing on a `[PE]` class, silently. Ambiguous codes
  are now refused and counted. Also reports `skipped_concept_types` / `skipped_predicates` by name, so an
  upstream *rename* of something drugref ingests no longer looks identical to a deliberate skip.
- **Claim values are canonicalised** (`ids.canonical_claim_value`): UNII/INCHIKEY/CHEBI are folded to upper
  at storage, matching the fold the moiety UUID is minted with — two cases of one UNII were inserting two
  claims and splitting the join index. `canonical_source`'s unknown-source fallback now upper-cases too.
- **Orchestrators own their transaction**: rollback-then-re-raise on failure (a mid-run error previously
  left the caller's connection aborted, so the *next* feed's first statement failed for unrelated reasons),
  plus module loggers. `classes_added` is counted by distinct key so it can no longer exceed
  `classes_in_release`.
- **CI exists** (`.github/workflows/ci.yml`, PG18 service). 123 of 220 tests are DB-gated and used to skip
  with exit 0 — `conftest` now **fails instead of skipping when `CI` is set**, and the workflow asserts the
  run contains no skips.

**Plan A — the open-question registry** ✅ done on this branch (`db/007`, `db/008`; **291 tests green**).
Detail under "Current state". First slice of the additive-effect design; Plans B (#15 descendant expansion)
and C (the accumulation model) remain.

**⇒ Next candidates: Slice 5b (MeSH-keyed CI/indications), Plan B (#15 descendant expansion) or Slice 3
(composition tree: salts/esters/hydrates).** Note for 5b: adding a CI predicate is now one `ci_axis` INSERT
plus the `source`/vocabulary CHECKs — the view needs no edit.

### Slice 5b — MeSH-keyed MED-RT contraindications & indications (the task)

The rest of MED-RT's interaction/indication content, all of it `RxNorm → MeSH` and therefore **blocked
today only because drugref has not ingested MeSH disease/chemical descriptors** (slice 2b ingested the MeSH
**PA** subset only). Measured in the 2026.07.06 release (same file slice 2a/5a parse):

- **`CI_with`** — drug–**disease** contraindication ("therapeutic or co-morbid contraindication"): **11,524
  assertions / 3,720 subjects**.
- **`CI_ChemClass`** — drug–drug by **chemical structural class** of a co-administered ingredient: **1,939 /
  565**.
- **`may_treat` / `may_prevent` / `may_diagnose`** — indications: **~18,144** (a public-domain, drugref-owned
  **MeDIC-alternative** for the drug–disease axis).
- **`induces`** — drug-induced state / adverse effect: **170**.

**What it needs, in order:**
1. **Ingest MeSH disease + chemical descriptors** as drugref's condition/chem vocabulary. **Licence already
   cleared** (NLM MeSH terms, same as slice 2b; attributed in `NOTICE`). The MED-RT endpoints are MeSH
   **`M`-codes** (concept UIs, e.g. `M0006033`), so this needs an **M-code → MeSH descriptor** resolution
   (slice 2b keyed on descriptor UI `D…`/tree numbers, not M-codes — the one genuinely new bit).
2. **Extend the parser** (`medrt.py`) to emit these predicates once their MeSH object resolves — the loop
   already sees and drops them (`medrt.py` trailing comment). Keep the both-endpoints-ingested scoping.
3. **Storage:** extend `class_contraindication`'s CHECK to admit `CI_with`/`CI_ChemClass` **only after**
   deciding object typing — `CI_with`'s object is a disease, not a `substance_class`, so it likely wants its
   own `drug_disease_*` table(s) rather than overloading `object_class_uuid`. Indications (`may_treat` etc.)
   are a **separate relation** again. **Design this in a 5b spec first** (mirror the 5a spec); do not
   overload 5a's table blindly.
4. **Same posture as 5a:** rebuildable projection, candidate tier only (MED-RT currency caveat, spec §4.3),
   subject join via `moieties_by_rxcui`, unmatched counted never dropped.

Reuse from 5a: the `interactions.py` writer pattern, the `unmatched_ci_rxcuis` counting, and the
per-source-clear rebuild discipline. The subject side is identical; **only the MeSH object side is new.**

Before production, one measured-but-
not-yet-verified-in-production concern carries over: the parser is validated against the committed fixtures
(extracts of the real 2026 release); run it against the full `pa2026`/`supp2026`/`desc2026` and re-confirm
the §5 aggregate numbers before production (real files are gitignored — see "How to run / test").

What the build delivered, and the facts that shaped it (all measured against the real release, issue #11):

- **The bridge is two-key, UNII-primary → CAS-fallback**, resolving a member's `RegistryNumber` keys
  against slice-1 `identity_claim` rows (`scheme='UNII'`, else `scheme='CAS'`). **No new external source.**
  `RelatedRegistryNumber` CAS is deliberately **not** a bridge key (spec tension B) — left to a later
  precision pass. Unmatched members are counted, split **no-key vs key-not-in-registry**, never dropped.
- **The doc-hypothesis was wrong.** MeSH **Descriptors DO carry UNIIs** in `RegistryNumber` — aspirin
  D001241 carries UNII `R16CO5Y76E` (not "CAS only"); a record may carry **several** UNIIs, so key
  extraction is set-valued. **568 PA classes** (all Descriptors), a **multi-parent DAG via tree-number
  nesting**. **10,505 member substances**; **73% joinable**, 27% neither (combinations / research
  compounds). The moiety gate is the binding constraint, same as MED-RT.

No schema change was needed (2a.1's `db/003` already admitted `PA`/`has_PA`/`MeSH`). **Real release files
are gitignored** — see "How to run / test".

**Open follow-ups are GitHub issues [#2](https://github.com/cairn-ehr/drugref/issues/2)–
[#8](https://github.com/cairn-ehr/drugref/issues/8)**, plus two slice-2b carry-overs to file: the
**`RelatedRegistryNumber` precision pass** (tension B — use a related CAS only when its parenthetical name
matches the record's own name; would lift bridge yield past the 73% RegistryNumber ceiling), and
**MED-RT `has_SC`**, now ingestible since the MeSH bridge exists (a MED-RT-side relation, spec §10).
Measured against the 2026.07.06 release: 3,632 assertions, of which 3,384 target MeSH (2,916 `RxNorm→MeSH`,
468 `MED-RT→MeSH`) and **248 target MED-RT itself** (210 `RxNorm→MED-RT`, 38 `MED-RT→MED-RT`). Those 248
need no bridge and were ingestible before it existed — so "→ MeSH structural classes" describes most of
`has_SC` but not all of it, and the MED-RT-targeted slice is not blocked on anything.

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
  ingredient concepts are keyed on RxCUI. Unmatched RxCUIs are **counted, never silently dropped** — and
  since Plan A (below) their **identities are persisted too**, in `ingest_unmatched_ingredient`.
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

**Slice 2b — MeSH PA: the classification layer's second axis.** The 568 MeSH Pharmacological-Action class
descriptors, their tree-number DAG, and moiety↔class memberships, on the **same three tables** as MED-RT
(no schema change). Built pure-function-first:

- **`ingest/mesh.py`** — a pure, streaming (`iterparse` + `clear`) parser of the three MeSH files
  (`pa`/`desc`/`supp`). Yields `PaClass` (name + tree numbers), `PaParentEdge` (child→parent, derived from
  tree-number nesting with both-endpoints-PA scoping like MED-RT), and `PaMembership` carrying `MemberKeys`
  (set-valued `unii`/`cas` from `RegistryNumber` only). `registry_keys()` is the reusable classifier
  (UNII = 10 alnum; CAS = `n-nn-n`; strips a `" (name)"` annotation). Streams by construction, so the
  batch/`iterparse` follow-up (#7) is satisfied here for MeSH.
- **`ingest/mesh_run.py`** — the orchestrator + the **two-key bridge**: read the UNII and CAS claim indexes
  once (`classes.moieties_by_scheme`), resolve each member **UNII-primary → CAS-fallback** ("else any CAS",
  not "also"), take **every** claimant. Unmatched members counted by distinct member, split
  **`members_no_key`** vs **`members_key_not_in_registry`**. `MeshSummary` mirrors `MedrtSummary`
  (classes-in-release vs classes-added, edge/membership counts, the two worklist numbers).
- **`classes.py`** — `ClassConcept` now lives here (source-neutral upsert shape, was in `medrt.py`);
  `moieties_by_scheme(conn, scheme)` is the generic join primitive (`moieties_by_rxcui` delegates to it).
- **Tests** — `test_mesh_parser.py` (parser, no DB) + `test_mesh_run.py` (DB-gated acceptance matrix: UNII
  join, CAS-fallback join, no-key/key-not-in-registry counting, multi-parent DAG orientation, idempotent
  re-ingest, per-source rebuild leaves MED-RT intact). A pinned `class_uuid` literal guards the derivation.

**Plan A — the open-question registry** (`db/007`, `db/008`). drugref's coverage gaps are published as a
queryable register rather than hidden: contraindications naming a class no drug is filed under (41 rules
across 13 classes in 2026.07.06), moieties with no `has_PE` membership, and ingredients no moiety carries.

- **A gap is a query, never a report.** Three views (`gap_unpopulated_contraindication`,
  `gap_unclassified_moiety`, `gap_unmatched_ingredient`) are always current and shrink visibly as coverage
  improves. `gap_unpopulated_contraindication` descends the class DAG — "no drug filed under E" means
  nowhere in E's subtree, not merely directly on E.
- **Populated is per AXIS, via `ci_axis`.** `ddi_candidate_pair` expands a `CI_PE` rule over `has_PE`
  members only, so a class populated solely on another of the six membership axes yields no pair — and a
  relationship-blind "has any member?" test would call it populated and **hide a real gap**. That is the
  two-lists-in-two-places failure `db/006` exists to prevent, so the view joins `ci_axis` rather than
  re-deriving the mapping. Nothing ties `class_membership.relationship` to `substance_class.concept_type`;
  the axes coinciding in MED-RT today is a property of that release, and **slice 5b is where it stops
  holding** (MeSH populates with `has_PA`). `ci_rule_count` counts the dead rules on a class, so a class
  half-populated across two axes reports only the rules that can never fire.
- **The register is rebuilt by every ingest orchestrator** — `run.py` (UNII), `medrt_run.py` and
  `mesh_run.py` each call `questions.register_from_gaps` as their **last step before commit**. Last, because
  steps 2–5 demolish and rebuild the very projections the gap views read; a rebuild earlier sees the empty
  middle and closes every question the DAG feeds. On a fresh database the UNII run is what first fills the
  register, and MED-RT's classifications empty most of it again.
- **Questions have immortal deterministic UUIDs**: `uuid5(QUESTION_NAMESPACE, gap_kind + ':' + gap_key)`,
  with `gap_key` in the frozen `SCHEME:value` form. External tooling can cite one. `gap_kind` may not
  contain `':'` — enforced in `ids.mint_question_uuid`, because it is the joiner and a colon there would let
  two distinct gaps mint the same UUID.
- **The hybrid split is the design.** `open_question` is a REBUILDABLE PROJECTION re-derived every ingest.
  Curator intent (`question_state`), tier watermarks (`question_source_check`) and findings
  (`question_evidence`) are APPEND-ONLY and keyed off that UUID. Putting `state` on `open_question` would
  have let each rebuild erase every `withdrawn` — and would have passed on a fresh database while failing on
  the second ingest of a long-lived one.
- **Curated tables use SURROGATE primary keys** with uniqueness over live rows only, per `db/005`. A
  natural-key PK rejects the correction insert outright and leaves in-place mutation as the only option.
  `question_state`'s single-live rule is a DEFERRED constraint rather than an index, because `superseded_by`
  must reference an existing row, so a correction is necessarily insert-then-point and both rows are live in
  between.
- **Watermark, not closure.** "No evidence found" is `open` with recent `question_source_check` rows; the
  only terminal state is `withdrawn`. `question_worklist` orders by cheapest-unchecked tier
  (`source_tier`), so free structured sources are exhausted before literature mining — a question with no
  `openFDA-SPL` check has not yet earned it.
- **A closed gap is retired, not always deleted** (`open_question.is_current`). The curated tables are
  `ON DELETE CASCADE` from `open_question` *and* append-only with a trigger that refuses `DELETE` — which
  is not a tension but an outright contradiction: deleting a closed question that carries curator rows
  **raises and aborts the whole ingest**. So `register_from_gaps` deletes only questions nobody has touched
  and retains the rest with `is_current` false — off the worklist, still citable, restored to current under
  the same UUID if the gap reopens. The cascades remain as a backstop nothing should reach.

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
# DB-gated tests need a PostgreSQL >= 18 database. 123 of 220 tests are DB-gated, so a
# run without this DSN passes while exercising none of the schema, floor or orchestrators:
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
```

CI (`.github/workflows/ci.yml`) runs the suite against a PostgreSQL 18 service container on every push and
PR, and `conftest` **fails rather than skips** when `CI` is set — so the DB layer can never go green by
being skipped.

- Schema: `db/001` (identity spine) + `db/002` (classification) + `db/003` (registry generalised for a
  second authority) + `db/004` (contraindication projection) + `db/005` (supersession/floor hardening) +
  `db/006` (the `ci_axis` vocabulary, contraindication PK, view contract), applied in filename order via
  `drugref.db.apply_migrations`. **Read the LATEST file that touches a table for its actual shape** — 002
  still shows the superseded MED-RT-specific columns, and 004's relationship CHECK is replaced by 006's FK.
- **Migrations are immutable once applied.** `apply_migrations` records each file's checksum in
  `drugref.schema_migration` and raises if an applied file's content changed. To alter the schema, add a new
  `db/NNN_*.sql` — editing an existing one is now an error rather than a silent no-op on migrated databases.
- Code: `src/drugref/{ids,claims,classes,db}.py` +
  `src/drugref/ingest/{unii,gate,run,chebi,medrt,medrt_run,mesh,mesh_run}.py`;
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
- ~~**One-way supersession** ([#4](https://github.com/cairn-ehr/drugref/issues/4))~~ — **done** in `db/005`:
  supersession is set once, same-moiety, and must point at a later claim (so no cycles).
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

Foundation-review follow-ups (filed, not fixed):

- **DAG-descendant expansion** ([#15](https://github.com/cairn-ehr/drugref/issues/15)) — `ddi_candidate_pair`
  matches direct membership only, so a CI naming a broad class misses drugs classified solely under a
  descendant. **Measure the real blast radius first** (how many of the ~739 rules target a class with
  children) before deciding whether to ship the recursive variant.
- **Crashed-ingest visibility + CLI** ([#16](https://github.com/cairn-ehr/drugref/issues/16)) — the
  `ingest_run` row is still written inside the run's own transaction, so a failure rolls it back and a
  crashed run is indistinguishable from one that never started. Needs a connection-ownership decision.
- **Remaining no-silent-drop gaps** ([#17](https://github.com/cairn-ehr/drugref/issues/17)) — MeSH PA
  records with no `DescriptorUI`; the legacy allow-list still keyed on a display name rather than a UNII.

## Repo facts

- GitHub: `cairn-ehr/drugref` · default branch `main` · licence **AGPL-3.0** · attribution in `NOTICE`.
- CI: `.github/workflows/ci.yml` (PG18 service; DB-gated tests fail rather than skip under `CI`).
- No CLAUDE.md yet (the coding rules live in the nextsession skill); no ADR log yet (slice 1's *why* is the
  design spec). Consider adding both as the project grows.

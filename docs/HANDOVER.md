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
every finding reproduced against a live PG18 before fixing. **220 tests green.** Highlights:

- **`db/005`** — supersession uniqueness moved from covering-all-rows to **partial** (live claims only), so
  a value upstream reverted then reasserted is no longer permanently invisible; supersession is now one-way,
  same-moiety, strictly forward (**closes [#4](https://github.com/cairn-ehr/drugref/issues/4)**); plus a
  CHECK on `ingest_run.source` and immutable `first_seen_ingest`.
- **`db/006`** — the CHECK↔CASE coupling `db/004` held together with a comment is now structural: a
  **`ci_axis`** table the `relationship` column is a foreign key into, `source` moved into the PK, and every
  clinical caveat moved into `COMMENT ON` (`--` comments are stripped by Postgres and were invisible to
  anyone inspecting the database).
- **`db.apply_migrations` gained a checksum ledger** — migrations are immutable once applied, replacing ad
  hoc guards that could silently no-op against an already-migrated database.
- **`ingest/unii.py`** — a blank UNII used to mint one shared UUID every such row collapsed onto, merging
  unrelated drugs irreversibly; now refused and counted. TSV readers use `QUOTE_NONE` (a stray quote
  previously swallowed an unbounded run of following rows).
- **`medrt.py`** — ambiguous published codes (two concepts, one code) are now refused rather than
  last-write-wins silently misfiling an edge.
- **Claim values are canonicalised** (`ids.canonical_claim_value`) so two cases of one UNII can no longer
  split the join index; **orchestrators roll back and re-raise** rather than leaving the connection aborted
  for the next feed.
- **CI added** (PG18 service); DB-gated tests now **fail rather than skip** when `CI` is set.

**Plan A — the open-question registry** ✅ done on this branch (`db/007`, `db/008`; **291 tests green**).
Detail under "Current state". First slice of the additive-effect design; Plans B (#15 descendant expansion)
and C (the accumulation model) remain.

**Slice 8a — PBS localisation: the local tier's first attachment** ✅ done on this branch. drugref's first
jurisdiction-specific (local) tier: `db/009` (three rebuildable-projection tables — no append-only floor,
because a de-listed PBS item must be able to disappear), a pure parser (`ingest/pbs.py`), the single writer
(`local.py`) and orchestrator (`ingest/pbs_run.py`) bridging PBS products to the global moiety spine **by
name alone** — the only licence-clean join, since PBS carries no UNII/CAS/InChIKey. **334 tests green.**
Measured against the real July-2026 release: a **92.4% name-bridge ceiling**, but only **84.6%** against
today's INN-gated registry — the moiety gate, not the bridge, is the binding constraint
([#26](https://github.com/cairn-ehr/drugref/issues/26)). Full write-up below.

**⇒ Next candidates: Slice 3 (composition tree: salts/esters/hydrates — now doubly motivated, since slice
8a's salt-strip heuristic carries almost nothing and GSRS salt relationships are the real fix), Slice 5b
(MeSH-keyed CI/indications), or Plan B (#15 descendant expansion).** Note for 5b: adding a CI predicate is
now one `ci_axis` INSERT plus the `source`/vocabulary CHECKs — the view needs no edit.

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

**Slice 1 — the identity spine.** See "What slice 1 delivered (detail)" below.

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
**queryable register** rather than hidden: three views (`gap_unpopulated_contraindication` — descends the
class DAG, so "no drug filed under E" means nowhere in E's subtree — `gap_unclassified_moiety`,
`gap_unmatched_ingredient`) stay current and shrink visibly as coverage improves. **Populated is per axis**
(joins `db/006`'s `ci_axis`): a class populated on a membership axis the rule doesn't expand over still
yields no pair, and reading it axis-blind would hide a real gap — MED-RT's axes coinciding today is a
property of that release, and **slice 5b is where it stops holding**. Questions carry an **immortal
deterministic UUID** (`uuid5(QUESTION_NAMESPACE, gap_kind+':'+gap_key)`) external tooling can cite. **The
hybrid split is the design:** `open_question` is a rebuildable projection re-derived every ingest; curator
intent (`question_state`), tier watermarks (`question_source_check`) and findings (`question_evidence`) are
**append-only**, keyed off that UUID — so a rebuild can never erase a `withdrawn` decision. Every
orchestrator (UNII, MED-RT, MeSH) rebuilds the register as its last step before commit. **Watermark, not
closure:** only `withdrawn` is terminal; `question_worklist` orders by cheapest-unchecked tier. **A closed
gap carrying curator work is retired, not deleted** (`is_current`) — the curated tables cascade from
`open_question` *and* refuse `DELETE`, so deleting a closed question with curator rows aborts the whole
ingest outright. Plans B (#15 descendant expansion) and C (the accumulation model) remain.

### Three things the MED-RT documentation got wrong (verified against the real release)

Recorded because each would be a silent, plausible bug invisible to a hand-written fixture: **`Parent Of`
runs parent → child**, not the reverse (the MoA root is `from_code` 9×, `to_code` never); **`[HC]` concepts
are the 26 alphabetical navigation bins** (`"A [Preparations]"`), not classifications — 18,450 of 21,058
class→ingredient edges; and **EPC membership is licence-clean and hierarchical** (`Parent Of` from the EPC
to the ingredient), not routed through SNOMED/MeSH as first assumed. The fixture is therefore extracted
from the real release by a committed, re-runnable extractor (`tests/fixtures/make_medrt_subset.py`), so it
can never re-encode a wrong assumption about upstream shape.

### Slice 8a — PBS localisation (detail)

drugref's first **local (jurisdiction-specific) tier**: a minimal Australian PBS product layer bridged to
the global moiety spine **by name** — the only licence-clean join available, because the PBS API's two
*structured* ingredient keys (ATC, AMT/SNOMED CT-AU) are exactly the two encumbered ones. Design + plan:
[slice-8a spec](superpowers/specs/2026-07-25-drugref-slice-8a-pbs-localisation-design.md) /
[plan](superpowers/plans/2026-07-25-slice-8a-pbs-localisation.md).

**What was built:** `db/009` (three tables — `local_product`, `local_product_moiety`,
`local_unmatched_ingredient` — widening `ingest_run.source`'s CHECK to admit `'PBS'`); a **rebuildable
projection**, deliberately outside slice 1's append-only floor, because PBS re-lists monthly and a
de-listed item must be able to disappear. `local_product_uuid` is a pure function of `(jurisdiction,
source, source_code)` — re-derived every ingest, never pinned — so a rebuild returns every surviving
product with the UUID it had before. `ingest/pbs.py` (pure parser: splits combination names on
`" with "`/`" and "`/`,`, strips a trailing salt/hydrate token **only as a fallback** — the unstripped name
is tried first, so "Dimethyl fumarate", an INN in its own right, isn't broken by an eager strip — and
treats the literal string `'null'`, PBS's empty-value sentinel, as absent); `local.py` (the single writer);
`ingest/pbs_run.py` (the orchestrator: clears this source's prior rows, reads the INN claim index once via
`classes.moieties_by_scheme`, resolves and bridges or records each component, one transaction, rollback-
then-re-raise on failure). **334 tests green.**

**Measured against the real July-2026 PBS release** (14,840 items) **and the real Feb-2026 UNII release**
(168,046 records), both gitignored:

| Measurement | Value |
|---|---|
| Name-bridge ceiling (vs **all** UNII substance names) | **92.4%** (13,710 / 14,840 items) |
| Against today's **INN-gated** registry | **84.6%** (12,552 items) |
| The gap — registry coverage, not name matching | **7.8 points** |
| `salt_stripped` share of bridge rows | 1.1% (gated) / 0.0% (at ceiling) |
| Combination products split | 1,647 |
| Distinct unmatched component names | 462 |

Top residual: paracetamol (105), vitamins (93), carbidopa (72), amino acid formula (66), cefalexin (60),
mesalazine (48), ciclosporin (48), minerals (45), ethinylestradiol (37), valaciclovir (37).

**Three conclusions, judged rather than merely reported:**

1. **The name bridge works** — 92.4% from name matching alone, no fuzzy matching, the honest ceiling for the
   only licence-clean join available.
2. **The binding constraint is registry coverage, not the bridge** — the same "moiety gate is the binding
   constraint" pattern already recorded for MED-RT and MeSH, now measured on a third, independent axis. The
   7.8-point gap traces to `INN_ID` being **empty** for amoxicillin, morphine, codeine, doxycycline,
   tacrolimus and dasatinib in the real UNII release. Filed as
   [#26](https://github.com/cairn-ehr/drugref/issues/26).
3. **The salt-strip heuristic is near-worthless, and is reported as such rather than left quietly implying
   it earns its place (rule 5).** 1.1% of bridge rows against the gated vocabulary, **0.0% at the ceiling**
   — spec §5.3 predicted ~20 affected names and that held. Cheap, labelled per row via `match_method`,
   harmless — but slice 3 (GSRS salt relationships) is the real answer, not this stand-in.

The residual is dominated by two explainable groups, **neither a bridge defect**: (a) **AU/INN vs US/USAN
spelling divergence** — paracetamol (UNII says ACETAMINOPHEN), cefalexin, ciclosporin, mesalazine,
valaciclovir, phenoxymethylpenicillin. Spec §5.2 deferred an AU→INN alias list pending measurement; **the
measurement is in, and it has earned its place** — the closed USAN↔INN crosswalk drugref already ships is
its natural home. (b) **Non-drugs correctly excluded by the moiety gate** — vitamins, amino acid formula,
minerals, carbohydrate, dressing-foam: foods/dressings, correct output, not failure.

**A second defect this measurement surfaced: [#27](https://github.com/cairn-ehr/drugref/issues/27).**
`ingest/unii.py` reads a `PT` column; the real UNII release has **`Display Name`** instead. Every moiety
gets an empty `display_name`, silently disabling both the legacy allow-list and the USAN↔INN crosswalk
(both keyed on that name) — and it does not raise. This is why the end-to-end DB measurement could not be
run directly and the pure-function measurement above was used instead; both #26 and #27 were invisible to
the committed 284-byte `unii_subset.tsv` fixture.

**Licence posture — read before extending this slice.** Node-local plug-in only: drugref ships AGPL-3.0
ingest code and schema, **never PBS data**. The PBS Schedule/API data mart carries no CC BY statement and
`pbs.gov.au` itself reads all-rights-reserved (only the *statistical* datasets on data.gov.au are CC BY).
ATC (WHO, NC+ND) and AMT/SNOMED CT-AU (NCTS-licensed) are quarantined **structurally**: `items.csv` has no
ATC/AMT column at all, the parser reads a fixed allow-list, no table has anywhere to put them, and a test
proves it by ingesting a fixture with **planted** `atc_code`/`amt_code` columns and asserting neither value
reaches any drugref table. Redistribution stays blocked pending written Dept-of-Health confirmation:
[#25](https://github.com/cairn-ehr/drugref/issues/25). `NOTICE` is unchanged — this slice redistributes
nothing.

**Node operator workflow.** Download the monthly ZIP into gitignored `downloads/` — **the `?variant=3`
query parameter is required, or the server 404s**:

```bash
curl -L -o downloads/pbs-2026-07.zip \
  "https://www.pbs.gov.au/publication/schedule/2026/07/2026-07-01-PBS-API-CSV-files.zip?variant=3"
```

Unpacks to `tables_as_csv/` (33 files); the ingest reads **only** `items.csv`, per the licence quarantine
above. Files are UTF-8 **with a BOM** — open with `encoding='utf-8-sig'`, or the first column name arrives
with a `﻿` prefix and every lookup of it misses.

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
  `db/006` (the `ci_axis` vocabulary, contraindication PK, view contract) + `db/007`/`008` (open-question
  registry + gap views) + `db/009` (the local tier's three PBS tables), applied in filename order via
  `drugref.db.apply_migrations`. **Read the LATEST file that touches a table for its actual shape** — 002
  still shows the superseded MED-RT-specific columns, and 004's relationship CHECK is replaced by 006's FK.
- **Migrations are immutable once applied.** `apply_migrations` records each file's checksum in
  `drugref.schema_migration` and raises if an applied file's content changed. To alter the schema, add a new
  `db/NNN_*.sql` — editing an existing one is now an error rather than a silent no-op on migrated databases.
- Code: `src/drugref/{ids,claims,classes,db,local}.py` +
  `src/drugref/ingest/{unii,gate,run,chebi,medrt,medrt_run,mesh,mesh_run,pbs,pbs_run}.py`;
  seed data under `src/drugref/data/` (incl. `salt_suffixes.tsv`); fixtures under `tests/fixtures/`.
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
- **PBS release for slice 8a** (also NOT committed — node-local only, see "Slice 8a" above for the licence
  posture). Download command and the `?variant=3`/`utf-8-sig` gotchas are in that section. Regenerate the
  fixture with:

  ```bash
  python tests/fixtures/make_pbs_subset.py downloads/tables_as_csv/items.csv \
      > tests/fixtures/pbs_items_subset.csv
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

Slice 8a follow-ups (filed, not fixed — full context in the "Slice 8a" section above):

- **PBS redistribution licence gate** ([#25](https://github.com/cairn-ehr/drugref/issues/25)) — blocks
  bundling/redistributing PBS data, not node-local ingest; needs written Dept-of-Health confirmation.
- **UNII gate excludes common drugs** ([#26](https://github.com/cairn-ehr/drugref/issues/26)) — `INN_ID` is
  empty for amoxicillin, morphine, codeine, doxycycline, tacrolimus, dasatinib; the binding constraint
  behind slice 8a's 7.8-point registry gap.
- **`ingest/unii.py` reads a non-existent `PT` column** ([#27](https://github.com/cairn-ehr/drugref/issues/27))
  — the real release uses `Display Name`; every moiety silently gets an empty `display_name` and it does not
  raise.

## Repo facts

- GitHub: `cairn-ehr/drugref` · default branch `main` · licence **AGPL-3.0** · attribution in `NOTICE`.
- CI: `.github/workflows/ci.yml` (PG18 service; DB-gated tests fail rather than skip under `CI`).
- Coding rules live in CLAUDE.md (and the nextsession skill); the published docs site's **Design decisions**
  section (`docs-site/docs/decisions/`) is the ADR-like log — living records, not immutable ADRs.
- Public docs site: `docs-site/` (MkDocs Material) → `docs.drugref.org`, deployed by `.github/workflows/docs.yml`.
  Keep decision records current (revise in place, remove reversed decisions); specs/HANDOVER/ROADMAP are **not** published.

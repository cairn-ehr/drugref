# Design — drugref global tier, slice 2b: MeSH Pharmacological Actions (classes + DAG + membership bridge)

**Date:** 2026-07-24 · **Repo:** `github.com/cairn-ehr/drugref` · **Status:** design, pending
implementation plan. **Builds on:** the
[slice-2a MED-RT design](2026-07-23-drugref-slice-2a-medrt-classification-design.md) (the classification
DAG this slice adds a second axis to) and the
[slice-1 moiety-spine design](2026-07-23-drugref-global-moiety-spine-design.md) (§5 own-immortal-UUID,
the `identity_claim` cross-walk this slice's bridge joins through).

**Scope of change:** add the **MeSH Pharmacological Action (PA)** axis to the existing classification
layer — the 568 MeSH PA **class** descriptors, their **subclass DAG** (from MeSH tree-number nesting),
and a **many-to-many moiety↔class membership** — on the **same three tables** slice 2a built
(`substance_class`, `class_parent`, `class_membership`), which slice 2a.1 (`db/003`) already made
source-neutral and pre-widened with `concept_type = 'PA'`, `relationship = 'has_PA'`, `source = 'MeSH'`.
**No schema change is needed.**

The one genuinely new problem is the **membership bridge**. MED-RT joined to moieties through the
`RXNORM_IN` claims slice 1 records; MeSH PA has no RxCUI. This spec establishes — **measured against the
real MeSH 2026 release, not the documentation** — that the bridge is **two-key (UNII-primary, CAS-fallback)
and needs no new external source**, because both keys are already slice-1 `identity_claim` rows.

**Out of scope (each a later concern):** indication/contraindication relations (`may_treat`/`CI_with` →
Slice 5 overlay), MeSH descriptors that are not PA classes, SCR→descriptor `HeadingMappedTo` mappings,
salts/formulations, and the HTTP API.

---

## 1. Licence gate (rule 7 — cleared before any bundling)

MeSH was licence-verified AGPL-bundleable in the slice-2a gate (§1): NLM Terms & Conditions require
**attribution** ("Courtesy of the U.S. National Library of Medicine"), **no endorsement**, and
**version-currency disclosure** only — **no** NonCommercial, **no** NoDerivatives; modification and
redistribution are permitted. That verdict stands. Two operational notes for this slice:

1. **Attribution lands with the data.** A `NOTICE` entry for MeSH (NLM) ships when slice 2b's ingest
   lands, in the same form as the MED-RT entry, including the currency statement (the MeSH year ingested).
2. **The fixture redistributes MeSH terms, and that is now permitted** — unlike slice 2a, where the fixture
   had to redact SNOMED/MeSH endpoints because MeSH was not yet attributed. The slice-2b fixture may carry
   MeSH descriptor UIs, names and registry numbers verbatim (they are the thing under test), provided the
   `NOTICE` attribution is in place. It still carries **only MeSH content** — no SNOMED, no other
   unlicensed namespace appears in these files at all (verified: the PA/desc/supp files are single-source).

## 2. Where this sits in the two-structure model

Identical placement to slice 2a (§2): this is the orthogonal **classification DAG** (*is-a-kind-of*), a
**second axis** beside MED-RT's six. A moiety already carrying `has_EPC`/`has_MoA` classes now also carries
`has_PA` classes. The curated overlay (Slice 5) inherits up a moiety's PA classes exactly as it does its
MED-RT classes — so, as in 2a, the PA **class DAG** (not just flat memberships) is what makes this axis
load-bearing for class-level curation, which is why §5 builds the DAG and not only the memberships.

## 3. Hybrid-store placement (unchanged from slice 2a §3)

MeSH is an **ingested feed = a rebuildable projection of an upstream authority** (NLM is the source of
truth). Applied to the three tables, exactly as MED-RT:

- **`substance_class`** — insert-only, immortal deterministic `class_uuid`; re-ingest upserts, refreshing
  the cached name. **Not** under the append-only floor (identity is immortal by construction, §4).
- **`class_parent` + `class_membership`** — **rebuildable per source**: re-ingesting a newer MeSH release
  `DELETE`s `source = 'MeSH'` edges (via `ingest_run` provenance) and re-inserts. `clear_source_edges`
  already scopes by source, so MED-RT's edges are untouched by a MeSH rebuild and vice-versa.

## 4. UUID minting (already wired in slice 2a.1)

`class_uuid = UUIDv5(class_namespace, "MESH:" + descriptor_ui)`, where `descriptor_ui` is the MeSH
descriptor UI (e.g. `D000894`). `ids.mint_class_uuid("MeSH", "D000894")` and `ids.canonical_source`
already produce this (the `MESH → MeSH` fold and the `MESH:` key prefix are in `src/drugref/ids.py`), and
`db/003`'s `substance_class_source` CHECK already admits `'MeSH'`. **No `ids.py` change is required**; a
test should pin one known MeSH `class_uuid` literal so a future refactor cannot silently re-key the axis
(as three frozen literals pin MED-RT).

## 5. Ground truth — verified against the real MeSH 2026 release, not assumed

Established by parsing the actual **`desc2026.xml`** (312,952,703 B, 31,110 descriptors),
**`supp2026.xml`** (786,380,988 B, 324,045 SCRs) and **`pa2026.xml`** (5,304,299 B) with a streaming
`iterparse` (peak RSS 32.7 MB over ~1 GB). Several facts contradict the documentation research that
[issue #11](https://github.com/cairn-ehr/drugref/issues/11) recorded, so they are pinned here.

### 5.1 The PA membership shape (`pa2026.xml`)

`pa2026.xml` is the consolidated **PharmacologicalAction** rollup (each descriptor/SCR also carries an
inline `<PharmacologicalActionList>`; `pa2026` is their clean join and is the authoritative edge source).

- **568 PA classes**, every one a **Descriptor** (D-prefixed). These are the class side of the axis.
- **35,790 membership edges** (PA class → substance): 25,194 → an SCR, 10,596 → a Descriptor.
- **10,505 distinct member substances = 7,667 SCRs (73%) + 2,838 Descriptors (27%)**. Members are a
  **mix of both record types** — the doc-research framing of "SCRs vs Descriptors" as the key split was
  incomplete; both carry members and both carry identity keys (§5.2).
- Member SCRClass: **7,666 class-1 (chemical) + 1 class-2** — members are, in effect, all chemical SCRs.

### 5.2 What the identity keys actually are (`RegistryNumber` / `RelatedRegistryNumber`)

**The issue's central hypothesis is refuted.** Issue #11 recorded (from an aspirin spot-check) that
"MeSH Descriptors appear NOT to carry a UNII … aspirin exposes only `relatedRegistryNumber 50-78-2`".
Against the real 2026 file, **aspirin `D001241` carries UNII `R16CO5Y76E`** in its `RegistryNumber`
(Calcimycin `D000001` likewise carries UNII `37H9VM9WZL`). NLM's 2013 UNII migration put the UNII in
`RegistryNumber` and displaced the CAS to `RelatedRegistryNumber` (annotated `"<cas> (<name>)"`) for
**both** record types. So the real split is **per-record key typing, not per-record-type.**

Key-type availability among PA members (a record has several concepts, so per-field counts sum > n;
a key type is credited to a record if any of its concepts carries it):

| member type | n | has UNII | has CAS | only-UNII | only-CAS | both | **NEITHER (unjoinable)** |
|---|---|---|---|---|---|---|---|
| **SCR (C)** | 7,667 | 3,949 (52%) | 4,240 (55%) | 1,043 | 1,334 | 2,906 | **2,384 (31%)** |
| **Descriptor (D)** | 2,838 | 2,012 (71%) | 2,247 (79%) | 139 | 374 | 1,873 | **452 (16%)** |

PA **class** descriptors themselves carry **no** registry number (they are abstract action classes, e.g.
`D000894` *Anti-Inflammatory Agents, Non-Steroidal*); only member substances carry identity keys.
Registry-number classification used: **UNII** = 10 upper-alphanumerics; **CAS** = `n-nn-n`;
**EC** = `EC …`; `0`/empty = none.

### 5.3 The bridge, and its yield ceiling (issue #11 items 3–4)

- **Bridge ceiling (MeSH-side key availability): 7,669 / 10,505 members (73%)** expose a UNII or CAS;
  **2,836 (27%) expose neither** and cannot join by chemical identity. The unjoinable set is, by
  inspection, **drug combinations** (`C000501` *"clonidine, chlorthalidone drug combination"*) and
  **novel research compounds** (`C000595055` *"dual orexin receptor antagonist 12"*) — substances with no
  single registered identifier. These are **counted, never silently dropped** (MED-RT's unmatched-RxCUI
  posture, §5.4).
- **The moiety gate is the binding constraint, structurally, exactly as for MED-RT (item 4).** Yield =
  (member exposes UNII/CAS: the 73% ceiling above, a MeSH-side property) × (that key sits on a
  has-INN-gated moiety: registry-dependent). The join only ever hits gated-in moieties, so once the
  registry is production-seeded the yield is bounded by the gate, not by MeSH parsing. Verified end-to-end
  against the current fixture-scale registry (3 moieties): **2 members matched by UNII, 1 by CAS** — the
  two-key mechanism resolves through both `identity_claim` schemes.
- **No new external source.** Both UNII and CAS are already slice-1 `identity_claim` rows (`scheme='UNII'`,
  `scheme='CAS'`).

### 5.4 The class DAG (from MeSH tree numbers)

All 568 PA classes carry MeSH tree numbers, and they **nest into a genuine multi-parent DAG**: of 1,042
tree numbers borne by PA classes, **794 have a parent tree number (drop the trailing `.NNN`) that is
itself a PA class** — e.g. *Reproductive Control Agents* ⊃ *Abortifacient Agents* ⊃ *{Abortifacient
Agents, Nonsteroidal / Steroidal}*. A descriptor carries several tree numbers, so a class genuinely has
several parents (a DAG, like MED-RT). Build rule, mirroring MED-RT's endpoint-scoping (2a §5): emit a
`class_parent(child, parent)` edge **only when both the child and its immediate tree-number parent are
ingested PA classes**; a parent tree number that is not a PA class drops the edge (the child attaches at
its nearest ingested ancestor, or is a root). This keeps the DAG scoped to the 568 by construction.

## 6. Ingest (`src/drugref/ingest/mesh.py` — pure-function-first, mirrors `medrt.py`)

- **Parser (pure, no DB).** Streams the three MeSH XML files with `iterparse` + `root.clear()` (the 750 MB
  `supp2026.xml` mandates streaming — `medrt.py`'s whole-file `ElementTree.parse` would not scale, and this
  parser should stream from the start; the batch-commit/`iterparse` follow-up
  [#7](https://github.com/cairn-ehr/drugref/issues/7) is thus satisfied here for MeSH by construction).
  Yields typed records:
  - `PaClass(descriptor_ui, name, tree_numbers)` — the 568 PA descriptors (from `pa2026`, enriched with
    `tree_numbers` + name from `desc2026`).
  - `PaParentEdge(child_ui, parent_ui)` — derived from tree-number nesting among ingested PA classes (§5.4).
  - `PaMembership(record_ui, descriptor_ui, keys)` where `keys` is the member's resolved identity keys
    (`unii`/`cas` sets from its `RegistryNumber`/`RelatedRegistryNumber`, §5.2). Emitted for every
    `Substance` under every `PharmacologicalAction` in `pa2026`, its keys filled from `supp2026`
    (C-records) or `desc2026` (D-records).
- **Membership bridge (the new join).** For each member, resolve `moiety_uuid` by, in order:
  **(1) RegistryNumber UNII → `identity_claim(scheme='UNII', superseded_by IS NULL)`**;
  **(2) else RegistryNumber CAS → `identity_claim(scheme='CAS', …)`**. Take **every** match (as MED-RT and
  `chebi.py` do; `identity_claim` is unique on `(moiety_uuid, scheme, value)` but not across moieties).
  Read both indexes **once per run**, not per member. **Unmatched members** (no key, or key not on a gated
  moiety) are **skipped and counted** as a worklist number — never a silent drop. `RelatedRegistryNumber`
  CAS is **not** a primary key (design tension **(B)**): it is usually the record's own displaced CAS but
  can point at a *related* substance, so it is left to a later precision pass, gated on the parenthetical
  name matching the record's own name.
- **Orchestrator (`src/drugref/ingest/mesh_run.py`, mirrors `medrt_run.py`).** Open an `ingest_run`
  (`source='MeSH'`, `upstream_release` = the MeSH year, checksum over the three files); upsert PA classes;
  `clear_source_edges('MeSH')`; insert `class_parent` then `class_membership`; return a `MeshSummary`
  separating **classes-in-release** from **classes-added**, plus parent-edge and membership row counts and
  the worklist numbers (members-unmatched split into *no-key* vs *key-not-in-registry*, so the two
  different causes stay legible).

Pure functions in reusable modules; the DB-touching orchestrator is the thin shell. Split parser across
files if `mesh.py` approaches 500 lines (the three-file read may warrant a small `mesh_keys.py`).

## 7. Testing (TDD, failing-test-first — mirrors slice-2a §7)

Unit (Python, no DB), against a fixture **extracted from the real release** by a committed re-runnable
generator (`tests/fixtures/make_mesh_subset.py`, §8) so it cannot encode a wrong assumption:

- Registry-number classification: UNII (10 alnum) vs CAS (`n-nn-n`) vs EC vs `0`; `RelatedRegistryNumber`
  parenthetical stripping (`"50-78-2 (Aspirin)"` → CAS `50-78-2`).
- Parser yields the right `PaClass` set (PA descriptors only; a non-PA descriptor in the fixture yields no
  class), correct memberships, and correct key extraction — including a record that carries **UNII in
  `RegistryNumber` and CAS in `RelatedRegistryNumber`** (the aspirin/calcimycin shape §5.2).
- Tree-number DAG orients child→parent and includes a **multi-parent** node; a tree-number parent that is
  not a PA class yields **no** edge (§5.4).

Integration (DB-gated), against the fixture + the slice-1 seed:

- PA classes minted with `concept_type='PA'`, `source='MeSH'`; `class_uuid` matches the deterministic
  derivation and a pinned literal.
- Membership links a member to its moiety **by UNII** (primary) and another **by CAS** (fallback), on
  `relationship='has_PA'`; a member exposing **neither** key yields no membership and **increments the
  no-key count**; a member whose key is not in the registry increments the **key-not-in-registry** count.
- Idempotent re-ingest: identical `class_uuid`s; `class_parent`/`class_membership` **rebuilt not
  duplicated**; a MeSH rebuild leaves MED-RT edges intact (per-source `clear_source_edges`).

## 8. The fixture generator (`tests/fixtures/make_mesh_subset.py`)

Mirrors `make_medrt_subset.py`: a committed, re-runnable extractor that selects a small subset from the
real `pa2026`/`supp2026`/`desc2026` covering every acceptance case, so the fixture is never hand-invented.
Selected members exercise: **an SCR with a UNII**, **a Descriptor with UNII-in-RegistryNumber +
CAS-in-RelatedRegistryNumber** (aspirin shape), **a member matched by CAS only**, **an unjoinable member**
(a drug-combination SCR with neither key), plus a **PA class with a tree-number parent that is also a PA
class** (a multi-parent DAG node) and one whose parent is **not** a PA class (dropped edge). Because MeSH
is attributable (§1), **no redaction is needed** — but the generator still emits only MeSH content, and a
test pins the fixture's shape (record set + key types) so a hand regeneration cannot silently drift.

## 9. Design tensions recorded (resolved)

- **(A) New schema for MeSH?** → **No.** slice 2a.1 (`db/003`) already made the three tables
  source-neutral and admits `PA` / `has_PA` / `MeSH`. Reusing them unchanged is the whole point of 2a.1.
- **(B) Is `RelatedRegistryNumber` CAS a bridge key?** → **Not in this slice.** It is usually the record's
  own displaced CAS but can name a *related* substance; the safe rule (use it only when its parenthetical
  name matches the record's own name) is a precision pass deferred to production, tracked as a follow-up.
  The primary bridge is RegistryNumber UNII → CAS, which already reaches 73% of members.
- **(C) UNII-primary or CAS-primary?** → **UNII-primary.** UNII is drugref's own identity key (the moiety
  UUID derives from it), so a UNII match is exact; CAS is the fallback for the minority of members that
  carry CAS but no UNII (only-CAS: 1,334 SCR + 374 Desc).
- **(D) Build the PA class DAG, or flat memberships only?** → **Build the DAG** (§5.4). It is measured to
  exist (794 nesting edges) and is what lets Slice-5 curation inherit up the PA axis, the same rationale
  that put MED-RT's DAG in scope in 2a.
- **(E) Silent drop of unmatched members?** → **No.** Split into *no-key* (2,836 members structurally
  unjoinable — combinations, research compounds) and *key-not-in-registry* (gated-out moiety), each a
  counted worklist number, matching the slice-1/2a no-silent-exclude posture.
- **(F) Stream or whole-file parse?** → **Stream** (`iterparse` + `root.clear`). `supp2026.xml` is 750 MB;
  a whole-file parse (as `medrt.py` still does for its 45 MB file) would not scale. Verified at 32.7 MB
  peak RSS over ~1 GB.

## 10. Explicitly out of scope for slice 2b

- **Indication/contraindication** (`may_treat`/`CI_with` etc.) — Slice-5 overlay, not classification.
- **Non-PA MeSH descriptors and SCR→descriptor `HeadingMappedTo` mappings** — not the PA axis.
- **`RelatedRegistryNumber` as a primary bridge key** — deferred precision pass (tension B).
- **MED-RT `has_SC` (→ MeSH structural classes)** — now *becomes* ingestible since the MeSH bridge exists,
  but it is a MED-RT-side relation and belongs to a MED-RT follow-up, not this slice.
- **EC-number / other-keyed members** — a small residual (EC appears on enzyme-target records); not a
  moiety identity key drugref holds. Counted in the no-key worklist, not bridged.

# Design — drugref global tier, slice 2a: the MED-RT classification DAG + membership

**Date:** 2026-07-23 · **Repo:** `github.com/cairn-ehr/drugref` · **Status:** design, pending
implementation plan. **Builds on:** the
[slice-1 moiety-spine design](2026-07-23-drugref-global-moiety-spine-design.md) (§3 two orthogonal
structures, §4 forward-compatible schema, §5 own-immortal-UUID minting, §2 hybrid store).

**Scope of change:** add the **classification layer** over the slice-1 active-moiety spine — MED-RT's
pharmacologic **class concepts** (EPC / MoA / PE / PK / TC / APC axes), their **subclass DAG** (`isa`),
and a **many-to-many moiety↔class membership** — seeded reproducibly from **MED-RT** alone. This is the
orthogonal *is-a-kind-of* structure the slice-1 design named (§3.2) and the schema was pre-shaped to admit
(§4). It is the enabling substrate for class-level interaction curation (Slice 5), whose edge-inheritance
is the biggest curation-economy lever.

**MeSH Pharmacological Actions is deferred to slice 2b** (see §8): its membership join needs a
UNII→MeSH bridge we do not yet have, whereas MED-RT joins cleanly through claims slice 1 already records.
**No** indication/interaction relationships, **no** MeSH, **no** salts/formulations, **no** API in 2a.

---

## 1. Licence gate (rule 7 — cleared before any bundling)

Slice 2's proposed sources were flagged licence-unvetted by the slice-1 design (§9) and eval 0003. Verified:

| Source | Producer | Terms | Verdict |
|---|---|---|---|
| **MED-RT** | US Dept. of Veterans Affairs (successor to NDF-RT™), distributed via NCI EVS | US federal-government work (17 U.S.C. §105 → public domain in the US); UMLS restriction **level 0** (least-restrictive "Category 0"); open EVS release files, no click-through | **AGPL-bundleable** ✓ |
| **MeSH (Pharmacological Actions)** | US NLM | NLM T&C: free, no fees/royalties, **attribution + no-endorsement + version-currency** only; no NonCommercial, no NoDerivatives, derivative incorporation permitted | **AGPL-bundleable** ✓ (2b) |
| **ATC** | WHO | NonCommercial + no-derivatives | **excluded** (node-local plug-in only) |

**Two caveats recorded** (verify-before-production checklist, like slice-1's ChEBI/UNII deeds — not blockers):

1. NLM's formal MED-RT source-release doc was unreachable live at design time (NLM HTTP 502); the
   public-domain determination rests on federal authorship + UMLS cat-0 + open EVS distribution, to be
   **re-confirmed against the live NLM deed before production**.
2. **SNOMED CT US Edition is one of MED-RT's build inputs.** Ingest must take only MED-RT's **own** class
   concepts and asserted relationships — never pass-through SNOMED-sourced content (SNOMED stays a
   node-local licensed plug-in, never bundled). An ingest-scoping rule, enforced by only reading MED-RT
   namespace concepts/associations (§5).

Attribution for MED-RT (VA/NLM) and, later, MeSH (NLM) ships in the repo `NOTICE`.

## 2. Where this sits in the two-structure model

The slice-1 design (§3) established two orthogonal structures:

1. **Composition tree** (*is-made-of*, downward) — slice 1 built its top node (the moiety); salts/clinical
   drugs are later slices.
2. **Classification DAG** (*is-a-kind-of*, orthogonal) — `class ⊂ class`; `moiety ∈ many classes` on
   multiple axes; membership is **many-to-many**, a link, never a parent FK. **This slice builds it.**

The curated overlay (Slice 5) attaches to class nodes and **inherits up** from a moiety through every class
it belongs to (curate "ACE-inhibitor + K-sparing → hyperkalaemia" once at class level, applies to all
members). That inheritance is why classes are load-bearing to the interaction mission, not mere taxonomy —
and why this slice exists before the overlay.

## 3. Hybrid-store placement (the load-bearing design call)

The slice-1 design (§2) splits the store by data nature. MED-RT is an **ingested feed = a rebuildable
projection of an upstream authority** (the VA is the source of truth; if drugref lost it, it re-ingests).
It therefore wants **versioning, provenance, reproducible rebuild** — **not** the immortality/append-only
floor that guards the identity spine. Applied per table:

- **`substance_class`** — **insert-only, immortal deterministic `class_uuid`** (class *identity* never
  re-keys — principle 2). But **not** under the slice-1 append-only trigger floor: re-ingest **upserts**
  (`ON CONFLICT (class_uuid) DO NOTHING`, preserving `first_seen_ingest`). Determinism (§4) means a
  re-ingest re-derives identical UUIDs, so immortality is achieved *by construction*, without a floor.
- **`class_parent` + `class_membership`** — **rebuildable per source**: re-ingesting a newer MED-RT release
  **deletes this source's prior edges** (`WHERE ingest_run` provenance identifies them) and re-inserts.
  These are pure projections and are **deliberately outside** the append-only floor, which guards only
  identity — a projection that could not be rebuilt would defeat the "drop-and-rebuild, version-pinned"
  discipline (slice-1 §2).

**Consequence:** the slice-1 append-only trigger floor is *not* extended to the new tables. This is correct,
not a gap — identity stays immortal, feed-derived edges stay rebuildable. Recorded as design tension **(A)**.

## 4. UUID minting (mirrors slice-1 §5, principle 2)

`class_uuid = UUIDv5(class_namespace, "MEDRT:" + nui)` where `nui` is MED-RT's stable concept identifier
(NUI, e.g. `N0000000001`). A **per-level `class_namespace` constant** (a frozen UUIDv5 literal, distinct
from the moiety namespace) guarantees derivations can never collide across levels (slice-1 §5). MED-RT NUIs
are stable across releases, so the derivation is re-runnable and coordination-free (two instances ingesting
the same release derive the same `class_uuid`). No pin-on-first-sight table is needed beyond `substance_class`
itself, because — unlike UNII, which can churn — the NUI is the natural key and the UUID is a pure function
of it (immortality-across-NUI-change is out of scope, analogous to slice-1 follow-up
[#3](https://github.com/cairn-ehr/drugref/issues/3)).

## 5. Schema (additive — no change to slice-1 tables)

Three new tables in schema `drugref`:

- **`substance_class`** — `class_uuid UUID PRIMARY KEY`, `medrt_nui TEXT NOT NULL UNIQUE` (natural key),
  `medrt_code TEXT`, `class_name TEXT NOT NULL`, `concept_type TEXT NOT NULL`
  (`CHECK concept_type IN ('EPC','MoA','PE','PK')`), `first_seen_ingest` FK → `ingest_run`.
  Thin, mirroring `substance_moiety`. **The four axes are exactly those with a verifiable MED-RT
  drug→class assertion** (`has_EPC/has_MoA/has_PE/has_PK`), so `concept_type` and `class_membership.
  relationship` stay symmetric; MED-RT's other concept families (TC therapeutic-category, APC, …) are
  deferred to a later slice rather than ingested class-less.
- **`class_parent`** — the DAG edge table: `child_class_uuid` FK, `parent_class_uuid` FK, `ingest_run` FK,
  `PRIMARY KEY (child_class_uuid, parent_class_uuid)`, `CHECK child <> parent`. A class may have **many
  parents** (DAG). Sourced from MED-RT `isa` between class concepts.
- **`class_membership`** — `moiety_uuid` FK → `substance_moiety`, `class_uuid` FK → `substance_class`,
  `relationship TEXT NOT NULL` (`CHECK relationship IN ('has_EPC','has_MoA','has_PE','has_PK')`),
  `ingest_run` FK, `PRIMARY KEY (moiety_uuid, class_uuid, relationship)`. The axis is recorded so a consumer
  can ask "all MoA classes of moiety X" (§2 inheritance needs the axis).

Rebuild scoping (§3) uses the `ingest_run` provenance column present on every edge row.

## 6. Ingest (`src/drugref/ingest/medrt.py` — pure-function-first)

- **Parser (pure, no DB).** MED-RT is distributed as XML: `<concept>` (code, name, status, a concept-type
  property) and `<association>` (`isa`, `has_EPC`, `has_MoA`, `has_PE`, `has_PK`, and out-of-scope ones we
  ignore). The parser yields typed records (`ClassConcept`, `IsaEdge`, `MembershipAssertion`) — TDD against
  a small crafted XML fixture, exactly as slice-1 parses `unii_subset.tsv`. **Reads only the MED-RT
  namespace**, satisfying the SNOMED-scoping caveat (§1).
- **Membership join.** Each MED-RT `has_*` assertion links a *drug ingredient concept* (carrying an RxNorm
  RxCUI) to a *class concept*. Resolve the ingredient's RxCUI against
  `identity_claim(scheme='RXNORM_IN', value=rxcui, superseded_by IS NULL)` → `moiety_uuid`. **Unmatched**
  ingredients (moiety not in our registry, e.g. gated out by has-INN) are **skipped and counted** — the
  run summary reports the skip count as a worklist number, never a silent drop (mirrors the slice-1 gate's
  audit posture: an active-looking substance with no match is a worklist item, not an invisible exclude).
- **Orchestrator.** Open an `ingest_run` (`source='MED-RT'`, `upstream_release`, checksum); upsert classes;
  **rebuild** (`DELETE` this source's prior rows, then insert) `class_parent` + `class_membership` for the
  run; return a summary (classes upserted, isa edges, memberships, RxCUI-unmatched skips).

Pure functions live in reusable modules (parse/derive/resolve); the DB-touching orchestrator is the thin
imperative shell. Files kept < 500 lines (split parser vs. writer if needed).

## 7. Testing (TDD, failing-test-first — mirrors slice-1 §8)

- **Unit (Python, no DB):** UUIDv5 class derivation is deterministic and stable for a fixed NUI;
  `class_namespace` is distinct from the moiety namespace (no cross-level collision); the XML parser yields
  correct `ClassConcept` / `IsaEdge` / `MembershipAssertion` records from the fixture.
- **Integration (DB-gated), against a small crafted MED-RT XML fixture** (a couple of ingredient concepts —
  one whose RxCUI matches a fixture moiety's `RXNORM_IN` claim, one that does not — plus one EPC and one MoA
  class, `isa` edges incl. a **multi-parent** class, and `has_EPC`/`has_MoA` assertions):
  - Classes minted with correct `concept_type`s; `class_uuid`s match the deterministic derivation.
  - `isa` builds the DAG, including the multi-parent node (two `class_parent` rows).
  - Membership links the matching ingredient to its moiety via the `RXNORM_IN` claim, with the correct
    `relationship` axis.
  - The RxCUI-unmatched ingredient produces **no** membership row and **increments the skip count**.
  - **Idempotent re-ingest:** re-run ⇒ identical `class_uuid`s, and `class_parent`/`class_membership`
    **rebuilt, not duplicated** (row counts stable; prior-run rows replaced).

## 8. Explicitly out of scope for slice 2a (each a later slice)

- **MeSH Pharmacological Actions (→ slice 2b)** — the second classification axis. Deferred because its
  membership join needs a UNII→MeSH (or ChEBI→MeSH) bridge drugref does not yet hold, unlike MED-RT's
  clean RxCUI join. 2b adds the bridge + a MeSH parser, reusing this slice's `substance_class` /
  `class_parent` / `class_membership` tables unchanged.
- **Indication/interaction relationships** — MED-RT `may_treat` / `may_prevent` / `CI_with` are **not**
  classification; they belong to the curated interaction/indication overlay (**Slice 5**, append-only +
  signed). Not ingested here.
- **Pass-through SNOMED content** — never (SNOMED is a build input to MED-RT but stays a node-local plug-in).
- **ATC** — licence-blocked (§1).
- **Class-level curation, edge inheritance, the HTTP API, salts/clinical drugs** — their own later slices.

## 9. Design tensions recorded (resolved)

- **(A) Extend the append-only floor to class tables?** → **No.** Class *identity* (`substance_class`) is
  immortal by deterministic UUIDv5, but MED-RT edges (`class_parent`/`class_membership`) are **rebuildable
  projections** and must stay `DELETE`-able for drop-and-rebuild (slice-1 §2). The floor guards identity,
  not feed projections. Extending it would break reproducible rebuild.
- **(B) MED-RT vs MeSH first?** → **MED-RT first.** It joins to moieties through the `RXNORM_IN` claims
  slice 1 already records; MeSH needs a bridge we lack. Ship one complete, low-risk axis (2a); add MeSH as
  2b on the same schema.
- **(C) Which MED-RT relationships are "membership"?** → the pharmacologic-class assertions
  `has_EPC/has_MoA/has_PE/has_PK` only. `may_treat`/`CI_with` are overlay data (Slice 5), excluded.
- **(D) Class UUID pinned-on-first-sight (like moiety) or pure-deterministic?** → **pure-deterministic** on
  the stable MED-RT NUI; no pin table. Immortality-across-NUI-change is out of scope (cf. slice-1
  follow-up [#3](https://github.com/cairn-ehr/drugref/issues/3)).
- **(E) Silent drop of unmatched ingredients?** → **No.** RxCUI-unmatched ingredients are **skipped and
  counted** in the run summary (worklist number), matching the slice-1 gate's no-silent-exclude posture.

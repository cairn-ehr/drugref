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

1. NLM's formal MED-RT source-release doc was unreachable live at design time (NLM HTTP 502), and the
   downloaded release itself **ships no licence, copyright or terms-of-use file** in any of its archives —
   consistent with an uncopyrightable federal work distributed without a click-through, but not positive
   confirmation. The public-domain determination therefore still rests on federal authorship + UMLS cat-0 +
   open EVS distribution, to be **re-confirmed against the live NLM deed before production**.
2. **SNOMED CT US Edition is one of MED-RT's build inputs**, and MED-RT's hierarchy genuinely maps out into
   it (761 `SNOMED CT → MED-RT` `Parent Of` edges in the current release). Confirmed from the release:
   **only MED-RT-namespace concepts are defined in the file** — SNOMED/MeSH/RxNorm appear *only* as
   association endpoints — so unlicensed content can enter solely through an edge. The parser's rule that
   both endpoints must be ingested MED-RT classes (§5) blocks exactly that path, making the scoping rule
   structural rather than a matter of care.

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
  (`CHECK concept_type IN ('MoA','PE','TC','PK','EPC','APC')`), `first_seen_ingest` FK → `ingest_run`.
  Thin, mirroring `substance_moiety`.
- **`class_parent`** — the DAG edge table: `child_class_uuid` FK, `parent_class_uuid` FK, `ingest_run` FK,
  `PRIMARY KEY (child_class_uuid, parent_class_uuid)`, `CHECK child <> parent`. A class may have **many
  parents** (DAG). Sourced from MED-RT `Parent Of`, **followed only when both endpoints are MED-RT-namespace
  concepts of an ingested `CTY`** — MED-RT's hierarchy also maps out into SNOMED CT US Edition and MeSH, and
  refusing to traverse those endpoints is what enforces the SNOMED-scoping rule (§1) structurally rather
  than by good intentions.
- **`class_membership`** — `moiety_uuid` FK → `substance_moiety`, `class_uuid` FK → `substance_class`,
  `relationship TEXT NOT NULL` (`CHECK relationship IN ('has_MoA','has_PE','has_TC','has_PK','has_EPC')`),
  `ingest_run` FK, `PRIMARY KEY (moiety_uuid, class_uuid, relationship)`. The axis is recorded so a consumer
  can ask "all MoA classes of moiety X" (§2 inheritance needs the axis).

### 5.1 Ground truth — verified against the real release, not assumed

The rules below were established by parsing the actual **`Core_MEDRT_2026.07.06_XML`** release (3,695
concepts, 96,516 associations), not inferred from documentation. Several contradict what the documentation
alone suggested, so they are recorded explicitly.

- **`Parent Of` runs parent → child**: `from` is the **parent**, `to` is the **child**. Verified twice: the
  MoA root `N0000000223` appears as `from_code` 9× and as `to_code` **0×** (a root has no parent), and
  `"A [Preparations]"` is the `from` of paracetamol. **Reading this backwards inverts the entire DAG**, and
  a hand-written fixture cannot catch it — which is why it is pinned here.
- **`CTY` inventory:** `PE` 1873, `EPC` 811, `MoA` 781, `TC` 66, `PK` 59, `APC` 44, `HC` 31, `EXT` 30.
- **Ingested types: `MoA`, `PE`, `TC`, `PK`, `EPC`, `APC`.** `APC` is included because it is the parent type
  of 835 `APC → EPC` hierarchy edges; without it the EPC hierarchy is truncated.
- **`HC` is excluded — it is navigation scaffolding, not classification.** `HC` concepts are the 26
  alphabetical bins (`"A [Preparations]"`, `"M [Preparations]"`) and account for 18,450 of the 21,058
  class→ingredient edges. Treating them as classes would fabricate meaningless memberships. **`EXT` is also
  excluded** (30 chemical-classification concepts staged for eventual addition to MeSH; no ingredient
  membership).
- **Membership arrives in two shapes**, both licence-clean:
  1. **Axis associations** `has_MoA` (7,538), `has_PE` (11,783), `has_TC` (5,532), `has_PK` (79) — all
     `RxNorm → MED-RT`, the ingredient's `from_code` being the **RxCUI**. (Each also has a small
     `MED-RT → MED-RT` variant — an EPC asserting *its own* mechanism — which is class→class, not
     membership, and is dropped.)
  2. **`Parent Of` from an `EPC` class to an RxNorm ingredient** (2,608), recorded as `relationship =
     'has_EPC'`. **There is no `has_EPC` association type in MED-RT**; EPC membership is expressed
     hierarchically. An earlier draft of this spec wrongly deferred EPC on the grounds that its linkage ran
     through SNOMED/MeSH mappings — it does not, and EPC is the most clinically recognisable axis
     (amlodipine → *Dihydropyridine Calcium Channel Blocker [EPC]*), so it is in scope.
- **Only MED-RT-namespace concepts are *defined* in the file**, all with `status = 'A'`. RxNorm, MeSH and
  SNOMED appear **only as association endpoints**, so unlicensed content can enter solely through an edge —
  exactly what the parser's endpoint filter blocks.
- **`has_SC`** targets MeSH (2,916 `RxNorm → MeSH`) and is out of scope here; it belongs to slice 2b.

Rebuild scoping (§3) uses the `ingest_run` provenance column present on every edge row.

## 6. Ingest (`src/drugref/ingest/medrt.py` — pure-function-first)

- **Parser (pure, no DB).** MED-RT is distributed as Apelon-DTS XML (schema `MED-RT_Schema_v1.xsd` ships in
  the release zip): `<concept>` (`namespace`, `name`, `code`, `status`, nested `<property><name>/<value>`
  carrying `NUI` and `CTY`) and top-level `<association>` (`name`, plus `from_namespace`/`from_name`/
  `from_code` and `to_namespace`/`to_name`/`to_code`, with `<qualifier>` entries such as `Authority`). The
  parser yields typed records (`ClassConcept`, `ParentEdge`, `MembershipAssertion`) — TDD against a fixture
  **extracted from the real release** rather than hand-invented, so it cannot encode a wrong assumption.
  **Keeps only MED-RT-namespace class concepts of an ingested `CTY` and RxNorm-namespace ingredient
  endpoints**, discarding SNOMED CT / MeSH endpoints, which satisfies the SNOMED-scoping caveat (§1) by
  construction.
- **Membership join.** Each `has_*` assertion links a *drug ingredient concept* (an RxNorm-namespace
  concept whose code-in-source **is the RxCUI**) to a MED-RT class concept; EPC membership arrives instead
  as a `Parent Of` from the EPC class to the ingredient (§5.1). Resolve that RxCUI against
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
  correct `ClassConcept` / `ParentEdge` / `MembershipAssertion` records, **discards SNOMED-/MeSH-namespace
  endpoints**, **discards `HC` and `EXT` concepts**, and **orients `Parent Of` parent→child**.
- **Integration (DB-gated), against a fixture extracted from the real release** covering paracetamol
  (RxCUI 161), amlodipine (17767, which carries two EPC parents), magnesium sulfate (6853, whose only
  parent is an `HC` bin) and an ingredient we do not carry:
  - Classes minted with correct `concept_type`s; `class_uuid`s match the deterministic derivation.
  - `Parent Of` builds the DAG **in the right direction**, including a multi-parent node.
  - Membership links each ingredient to its moiety via the `RXNORM_IN` claim on the correct axis, and
    amlodipine gets **two `has_EPC` memberships**.
  - The `HC` alphabetical bin yields **no class row and no membership** — magnesium sulfate ends up
    correctly unclassified rather than filed under "M".
  - The RxCUI-unmatched ingredient produces **no** membership row and **increments the skip count**.
  - SNOMED-/MeSH-namespace endpoints yield **no** class row and **no** edge (scoping rule §1 enforced).
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
- **The `HC` and `EXT` concept types** — `HC` is alphabetical navigation scaffolding, not classification
  (§5.1); ingesting it would fabricate meaningless memberships. `EXT` has no ingredient membership. Both
  are excluded deliberately, not deferred. (`EPC` and `APC` **are** in scope — see §5.1.)
- **MED-RT→MED-RT `has_*` assertions** — an EPC declaring its own mechanism/effect is a *class→class*
  relationship, not moiety membership. Modelling class-level attributes is a later concern.
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
- **(C) Which MED-RT relationships are "membership"?** → the four ingredient→class assertions
  `has_MoA/has_PE/has_TC/has_PK`, **plus `Parent Of` from an `EPC` class to an ingredient** (recorded as
  `has_EPC`). `may_treat`/`may_prevent`/`CI_*` are overlay data (Slice 5), excluded; `has_SC` targets MeSH
  (2b). See §5.1 — MED-RT has no `has_EPC` association type; EPC membership is hierarchical.
- **(F) `Parent Of` direction** → **`from` is the parent, `to` is the child** (§5.1), established from the
  real release, not the documentation. An earlier draft of this design assumed the reverse, which would
  have inverted the whole DAG while still passing a hand-written fixture. The fixture is therefore
  **extracted from the real release**, so it can never re-encode a wrong assumption about upstream shape.
- **(G) Should `HC` bins be ingested as classes?** → **No.** They are the 26 alphabetical navigation bins
  and would attach a spurious "class" to nearly every ingredient (18,450 edges). Excluded outright.
- **(D) Class UUID pinned-on-first-sight (like moiety) or pure-deterministic?** → **pure-deterministic** on
  the stable MED-RT NUI; no pin table. Immortality-across-NUI-change is out of scope (cf. slice-1
  follow-up [#3](https://github.com/cairn-ehr/drugref/issues/3)).
- **(E) Silent drop of unmatched ingredients?** → **No.** RxCUI-unmatched ingredients are **skipped and
  counted** in the run summary (worklist number), matching the slice-1 gate's no-silent-exclude posture.

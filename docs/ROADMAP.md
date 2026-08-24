# ROADMAP — drugref

> **Disposable working scaffolding, not a source of truth.** The canonical *what/why* is the design spec(s)
> under [`docs/superpowers/specs/`](superpowers/specs/) (and future ADRs). This file only orders the build.
> If it disagrees with the canonical docs, the canonical docs win.
>
> **Under no line bound since #63**, and appended to per slice rather than recompressed: a bound that forces
> a compression pass trades a readable history for a line count. Session state is
> [`HANDOVER.md`](HANDOVER.md); the stable notes are [`PROJECT-NOTES.md`](PROJECT-NOTES.md).

**Scope:** the **global tier** of drugref.org (jurisdiction-independent substance identity → chemistry → classes →
interactions), built bottom-up, followed by the consumer API and the local (country-specific) tier. drugref is an **advisory
reference-data service** — it never sits on Cairn's signed inter-node wire core.

## Cross-cutting (applies to every slice)

- **TDD** — failing test first, then code.
- **Licensing is non-negotiable** — all code AGPL-3.0; every dependency AND every bundled reference-data source must be
  AGPL-3.0-compatible, **checked before adding/bundling**. Encumbered sources (ATC, SNOMED/AMT, ICD-10-AM, eTG, AMH,
  commercial DrugBank…) attach only as **node-local, separately-licensed plug-ins**, never bundled.
- **Advisory tier, integrity in the DB** — ingest/normalization is fit-for-purpose Python (fast iteration on brittle feeds),
  but append-only/identity integrity is enforced **in PostgreSQL** (constraints/triggers/RLS), not app code. Postgres (≥ 18)
  is the integration substrate.
- **Hybrid store** — ingested feeds are **rebuildable projections** (drop-and-rebuild, version-pinned, `ingest_run`
  provenance); curated knowledge is an **append-only, signed overlay** (the moat) — signing shipped in `5c.4`
  (`db/030`), at two layers, with signatures **detached** rather than a column; see § Slice 5c.
- **Own immortal UUIDs, never key on a name** (principle 2); external IDs attach as **append-only claims**; cross-source
  identity is reconciled by **linking, never re-keying**.

## The data model in the large (two orthogonal structures)

1. **Composition tree** (*is-made-of*, downward): **active moiety → specific substance (salt/ester/hydrate) → clinical drug
   (moiety/salt + strength + form) → product (brand/pack)**. Product is the *local* tier.
2. **Classification DAG** (*is-a-kind-of*, orthogonal): `class ⊂ class ⊂ …`; `moiety ∈ many classes` on multiple axes
   (chemical / mechanism / therapeutic) — **many-to-many**, a link, never a parent FK.

The curated overlay attaches to nodes in **either** structure and **inherits along the edges** (down the tree, up through a
moiety's classes) — curate once, apply widely. This is the biggest curation-economy lever.

## Slices

### Slice 1 — Active-moiety identity spine ✅ DONE (gate corrected, see below)
Schema `drugref` (3 tables: `ingest_run`, `substance_moiety`, `identity_claim`) + append-only row-level floor; Python ingest;
moiety registry with immortal `UUIDv5`-on-UNII (pinned) + append-only cross-ref claims; membership gate + closed legacy
allow-list; international seeding (UNII backbone / INN display / ChEBI cross-refs / RxNorm demoted to a claim / closed
USAN↔INN crosswalk); ChEBI enrichment by InChIKey. 30 tests. Detail: the slice-1 spec + plan. **Corrected by the
identity-spine fix round** (below): the parser read a `PT` column the real release does not have, and the `has-INN` gate
turned out to rest on a false premise about `INN_ID`.

### Slice 2a — MED-RT classification DAG + membership ✅ DONE
Class registry (`substance_class`, own UUIDv5-on-NUI) + subclass DAG (`class_parent`) + many-to-many `class_membership`,
seeded from **MED-RT** (licence-verified: VA federal work, public domain, UMLS restriction level 0). Six ingested axes — MoA /
PE / TC / PK / **EPC** / APC; `HC` (alphabetical navigation bins) and `EXT` excluded. Membership joins to moieties via the
`RXNORM_IN` claims slice 1 already records; EPC membership is hierarchical (`Parent Of` from EPC to ingredient), normalised to
`has_EPC`. Class edges are **rebuildable projections**, deliberately outside slice 1's append-only floor. Against the full
2026.07.06 release: 3,634 classes, 3,961 DAG edges (440 multi-parent), 27,540 memberships over 6,012 ingredients. 102 tests.
Detail: the slice-2a design spec.

### Slice 2a.1 — Source-neutral class registry ✅ DONE
The enabling refactor for 2b, extracted because the 2a registry was built around a single authority. `db/003`:
`medrt_nui`/`medrt_code` → `source_code`/`published_code`, a NOT NULL `source` column, uniqueness moved from global to **per
(source, source_code)**, and `PA`/`has_PA` added to the axis CHECKs. `ids.mint_class_uuid(source, code)` replaces the NUI-only
form. **MED-RT class UUIDs are unchanged**, pinned by frozen literals — the derivation is the join key of both edge tables, so
a drift would orphan every edge on the next rebuild with no error anywhere. 116 tests.

### Slice 2b — MeSH Pharmacological Actions ✅ DONE
The second classification axis, on the **same three tables** as MED-RT (2a.1's `db/003` already added `PA`/`has_PA`/`MeSH`;
**no schema change**). `ingest/mesh.py` — a pure, streaming (`iterparse`) parser of the three MeSH files (pa/desc/supp) → 568
PA class descriptors, a **multi-parent DAG from tree-number nesting** (both-endpoints-PA scoping, like MED-RT), and
memberships carrying set-valued `RegistryNumber` keys. `ingest/mesh_run.py` — orchestrator + the **two-key membership
bridge**: UNII-primary → CAS-fallback, resolving a member's keys against slice-1 `identity_claim` rows (`scheme='UNII'`, else
`'CAS'`) — **no new external source**. Unmatched members counted, split no-key vs key-not-in-registry (never dropped).
`ClassConcept` moved to `classes.py` (source-neutral) + a generic `moieties_by_scheme` join primitive. 167 tests. Detail: the
slice-2b design spec.

The measurement that shaped it **refuted the doc-research**: MeSH **Descriptors DO carry UNIIs** in `RegistryNumber` (aspirin
D001241 = UNII `R16CO5Y76E`, not "CAS only"), and a record may carry several, so key extraction is set-valued. **Re-measured
post-#26: 10,506 member substances**, **72.8% carrying an identity key** — the figure this file used to call "73% joinable",
which it is not — while only **40.6% reach a gated-in moiety** (both shortfalls counted): the moiety gate is the binding
constraint, as for MED-RT. `RelatedRegistryNumber` CAS is **not** a bridge key here (tension B — deferred precision pass).
MeSH licence verified AGPL-compatible (attribution + no-endorsement + version-currency; no NC/ND), attributed in `NOTICE`;
**ATC stays excluded** (NC + no-derivatives). Follow-ups: that precision pass, and MED-RT's own `has_SC` — 3,632 assertions,
mostly (3,384) into MeSH structural classes and so unblocked by the bridge, but **248 target MED-RT itself** and never needed
it.

### Slice 3 — Composition tree: specific substances (salts/esters/hydrates) ✅ DONE
Spec: [slice-3 composition tree](superpowers/specs/2026-08-05-drugref-slice-3-composition-tree-design.md).
**Rule-6 gate cleared first: GSRS data is CC0 1.0, software Apache-2.0** — both AGPL-3.0-compatible, and CC0 imposes no
attribution or share-alike at all. `NOTICE` gains a bundled-source entry. The one caveat is the dedication's *"unless
otherwise noted"* clause, which re-confirms before production alongside #6/#25.

**The sentence this line used to hold was wrong in three ways, and the release said so.** It read: "Add the salt level
below the moiety, keyed on UNII with `parent_moiety_uuid` from **GSRS active-moiety relationships**; salt↔base
strength-equivalence data."

1. **`ACTIVE MOIETY` is the ION level, not the composition edge.** 33,647 edges, **71% self-references**; every magnesium
   form (including drugref's own moiety) points at `MAGNESIUM CATION`. As an equivalence join it merges **levomefolate
   magnesium** with magnesium sulfate — 35 substances share that cation, 27 of them drugref moieties — which is the
   discredited-sulfonamide shape already withheld once. The real edge is **`SALT/SOLVATE↔PARENT` (15,199)** +
   **`SOLVATE↔ANHYDROUS` (1,635)**; `ACTIVE MOIETY` survives only as a **discriminator inside** a composition.
2. **`parent_moiety_uuid` cannot hold it**: **1,089 salts (7.7%) have >1 parent base**, 800 within the registry.
   `ZINC GLYCINATE CITRATE` = zinc + glycine + citric acid. The relation is many-to-many by nature.
3. **Salt↔base strength equivalence is not in this source.** `BASIS OF STRENGTH` is **409 edges** of *assay* spec
   (`99–101 WEIGHT PERCENT`), not conversion factors; molecular weight covers 5.4% of records, and deriving a dose
   factor from it would have a projection compute a clinical quantity. **Deferred to 5c.**

**The direction convention is inverted from the naive reading** — for type `A->B` on record X pointing at Y, X plays B —
the same erratum shape as MED-RT's `Parent Of`. Naively, one salt had 124 parents; correctly, the busiest *parents* are
Maleic Acid (124 salts), Tartaric Acid (123), citric acid (117). Confirmed twice (mirror agreement 15,039; every solvate
has exactly 1 anhydrous).

Shape: **composition edges over ONE registry**, no second identity — `substance_composition (substance_unii TEXT,
component_moiety uuid, relation, is_active_component)`, a rebuildable `GSRS`-keyed projection (`db/028`). Code:
`ingest/gsrs.py` (pure streaming parser), `composition.py` (single writer), `ingest/gsrs_run.py` (orchestrator), and a
`gsrs` chain step. Read path propagates the **active component only**, so Maleic Acid's 124 salts stay unlinked.
Deliberately NOT wired into `ddi_candidate_pair` (a low-single-digit-millisecond hot
path, most recently measured at 2.876 ms in the policy-surface round, `db/027`).

**Measured end to end** — UNII 26Feb2026 → MED-RT 2026.07.06 → MeSH 2026 → GSRS 2026-02-26, on 2026-08-05, 137 s:

| quantity | measured |
|---|---:|
| `substance_composition` rows | **8,671** (7,962 salt + 709 solvate) |
| composites | **7,377** (4,425 are not moieties) |
| component moieties | **4,433** — 22.8% of the registry gain ≥1 child (4,092 via a salt edge) |
| `is_active_component` TRUE / FALSE / NULL | **5,011 / 992 / 2,668** |
| gap kind 12 (`gap_unruled_composition_activity`) | **2,245** composites |
| `ddi_candidate_pair` (must not move) | **21,664** ✅ unmoved |
| `substance_moiety` (must not move) | **19,438** ✅ unmoved |
| `open_question` | **21,079** = 18,834 pre-slice + 2,245 new |
| test suite | **894 passed** |

`is_active_component` has **no DEFAULT and NULL means UNRULED**. The design predicted 5,029 / 1,001 / 2,641 and 2,226
gap-12 composites; the **row set matched exactly** and only the split moved, because the prediction scripts resolved
activity through a global `unii → active moieties` map while the orchestrator only accepts a ruling from the composite's
own record. The 27 edges GSRS stores solely on the component's record are the whole difference. **The measured figures
are the published ones** — see the [decision record](https://docs.drugref.org/decisions/gsrs-relationship-direction/).

**It does not resolve issue 33 or issue 30, and the annotations below saying it does are withdrawn.** Issue 33's own
proposed fix is refuted: **nothing in GSRS points at `DE08037SAB`** (0 inbound references across 173,080 records). A
composition hop recovers **94 of 706** unmatched MeSH UNII keys and **68 of 1,977** CAS keys; the magnesium flagship is
not among them. Issue 30's yield is unmeasured here — the verification DB carries no PBS release — and is an
implementation-step measurement.

### Slice 4 — Clinical drugs (moiety/salt + strength + form)
The prescribable generic level (**RxNorm SCD** as the skeleton). Composition-tree leaf before product/local.

### Slice 5 — The interaction & contraindication layer
Two halves, per the hybrid store: **ingested rebuildable projections** (5a, 5b, 5b.2) seeded from public-domain
regulatory-derived content, then the **append-only, SIGNED curated overlay** (5c) — signing shipped in `5c.4`
(`db/030`); see the [signing the curated overlay](https://docs.drugref.org/decisions/signing-the-curated-overlay/)
decision record, and [curating a drug–condition pair](https://docs.drugref.org/decisions/curating-a-drug-condition-pair/)
§3 for why the overlay's first content-bearing slice shipped empty to make that ordering possible.
The projections give a defensible safety layer *fast*, from sources drugref already holds; the overlay is the durable
value-add built on top. Sequenced by licence-cleanliness, not by coverage.

#### Slice 5a — MED-RT mechanism/effect contraindications ✅ DONE
The smallest first cut: MED-RT **`CI_MoA`/`CI_PE`** ("contraindicated mechanism/physiological-effect of a **co-administered
ingredient**") = ~739 **class-level drug–drug** rules *in the MED-RT terminology* — **635** survive the moiety gate and
reach `class_contraindication`; see the 5c.1 section below before quoting either — mined from the **MED-RT file slice 2a
already parses** — **no new
source, no new join, no new UUID minting** (both endpoints — RxNorm subject, MoA/PE class object — are already ingested). New
table `class_contraindication` (`db/004`), a rebuildable projection like `class_membership`; concrete drug pairs **expand at
read time** over the existing class DAG (`ddi_candidate_pair` view — since Plan B that expansion descends the DAG, see below).
**Candidate tier only** — MED-RT does not track label updates, so rows carry provenance and feed review; nothing here
auto-alerts. Design: [slice-5a spec](superpowers/specs/2026-07-25-drugref-slice-5a-medrt-contraindication-design.md).

#### Slice 5b — MeSH-keyed contraindications ✅ DONE
The MeSH-endpoint MED-RT content, unlocked by ingesting **MeSH disease/chemical descriptors** (same NLM licence already
cleared in 2b — **no new source**). The 5b design round **split the work in two**: **5b** is the contraindication half —
`CI_with` (drug→condition) + `CI_ChemClass`'s moiety arm (drug↔drug) — over a new MeSH **condition** registry; **5b.2** is the
indication half (`may_treat`/`may_prevent`/`may_diagnose`/`induces`), which reuses that registry — and, as it turned out,
**widens** it, since one closure is taken over both halves' objects. Spec:
[slice-5b](superpowers/specs/2026-07-28-drugref-slice-5b-mesh-contraindication-design.md). `db/013`–`db/016`; merged as PR
#44. **Measured yield against the real releases** — **the measurement corrected the spec in five places; the measured figures
are the true ones**:

| | measured | spec had predicted |
|---|---:|---:|
| `condition` (registry) | **5,203** (5,190 descriptors + 13 SCRs) | 5,190 — descriptors only |
| `condition_parent` | **7,157** (1,690 multi-parent) | 7,157 ✓ |
| `moiety_condition_contraindication` | **9,471** over 2,900 moieties / 641 conditions | 9,482 / 667 |
| `moiety_contraindication` (exact pairs) | **1,442** | 1,443 |
| unresolved `CI_ChemClass` objects / their rules | **103** / **405** | 108 / 405 |
| `condition_contraindication_expanded` | **191,728**, of which 9,471 direct | — |

Three of the five are one cause: the spec counted MeSH **ConceptUIs** while drugref keys on the **record**, and several
concepts resolve to one record (`mesh_concepts.py` exists to keep them apart). The other two: the registry also holds 13
**supplementary records**, which carry no tree numbers and so never enter the closure; and exactly one **self-pair**
(tranylcypromine), which `db/014` forbids because a drug is not contraindicated with itself. The **103-vs-108** correction is
published as an erratum in the docs-site *Design decisions* section — the specs are immutable, so that living record is where
it lives.

Clinically confirmed: a rule on **Epilepsy** now reaches a patient coded *Temporal Lobe*, *Complex Partial*, *Frontal Lobe*…
(14 direct rows → 378 over 27 conditions, ~10 ms per lookup); and **pregnancy + lactation carry 615 rows**, which is why the
table is `moiety_condition_contraindication` and not `drug_disease_*` — `CI_with`'s object is a *patient state*, and a
disease-shaped table would have filed the release's most consequential contraindication axis as a category error.

**Deferred, deliberately — `CI_ChemClass`'s class arm** (405 assertions over 103 objects): **withheld, not dropped.**
Expanding it over MeSH's *structural* chemical tree makes a rule on Sulfonamides reach bendroflumethiazide and bosentan — the
discredited sulfa cross-reactivity inference — and only 8.3% of those objects have any `has_SC` member, so that route cannot
fill the gap either. Published instead as `gap_unresolved_ci_object` + a fifth `gap_kind`, one row per object with its rule
count, for a curator to rule on.

Those 103 are **two kinds of thing, and `object_kind` (`db/014`) keeps them apart.** Failing to bridge an object to a moiety
is not evidence that the object is a class: a MeSH record carrying **no registry key** (Alkalies, Organic Chemicals) names no
substance and is the sulfonamide case, while one carrying a real **UNII or CAS** names a substance drugref's gated registry
does not hold. Reading the first fact off the second asked a curator whether a contraindication naming *Pimozide* — a leaf
drug descriptor — should expand over the drugs below it. Both kinds stay on the worklist. **Also not done here:** MED-RT's
`has_SC` (3,632 assertions, **248 targeting MED-RT itself**) and the `RelatedRegistryNumber` precision pass.

**Two of the three things this slice was told not to forget were honoured; the third was retracted.**
`condition_ci_axis.expands_descendants` is declared per predicate **with no DEFAULT** (`db/014`), so MeSH's differently-shaped
tree cannot inherit a recall-safe guess. `mesh_run` still owes `unresolved_expansion_policy('MeSH')` — **not yet needed**,
since no MeSH-keyed row can exist in `class_expansion_policy` until the class arm lands. And **the source-blind walk: 5b does
NOT end it.** 5b registers no MeSH chemical class in `substance_class`, so the hazard stays **latent** until `has_SC` or the
class arm lands.

Follow-up filed: [#39](https://github.com/cairn-ehr/drugref/issues/39) — `ingest_unmatched_ingredient` rebuilt per `source`
while two orchestrators write under `MED-RT`. **Closed by the interaction debt round below.**

#### Post-5b debt round ✅ DONE
The five follow-ups 5b's review filed, cleared before 5b.2 reuses the same code paths, plus the two issues the tracker had
closed while the code still carried them. `db/017`; **536 tests**. [#40](https://github.com/cairn-ehr/drugref/issues/40) one
gz-aware MeSH reader (`mesh.iter_records`), which also fixed a regeneration command that found nothing against a real release
· [#17](https://github.com/cairn-ehr/drugref/issues/17) the last silent refusal counted ·
[#42](https://github.com/cairn-ehr/drugref/issues/42) the descriptor-wins tie-break pinned — **measured: 0 of MeSH's
ConceptUIs appear in both desc and supp, so the release cannot exercise that branch and the guard is against a future
partition change** · [#41](https://github.com/cairn-ehr/drugref/issues/41) the CI object's namespace taken from the data in
BOTH the view and `questions.py`, preserving every existing `question_uuid` and **canonicalised in both** ·
[#43](https://github.com/cairn-ehr/drugref/issues/43) one `checksum(*paths)`, one `db.clear_source_tables`, six declared table
tuples each restated independently by test (**seven** since indications joined). **Re-verified end-to-end**: every 5b figure
reproduced exactly, and the `object_kind` split is newly recorded as **96 CHEMICAL_CLASS (386 rules) + 7
UNREGISTERED_SUBSTANCE (19 rules)**.

#### Interaction debt round ✅ DONE
The three interaction-model follow-ups, cleared before 5b.2 reused these code paths. `db/018`; 568 tests. Each was **measured
against the real releases before it was touched, and two of the three issue texts proved stale** — a number in an issue is a
claim about a release, not about the code.

[#39](https://github.com/cairn-ehr/drugref/issues/39) — `ingest_unmatched_ingredient` gains a `reason` discriminator
(`classification` | `contraindication`, NOT NULL, **no DEFAULT**, in the PK), so each of the two orchestrators writing under
source `MED-RT` clears exactly what it re-derives. `db.clear_source_tables` grew an opt-in `match=` narrowing rather than a
seventh copy of the DELETE. Measured **2,137 + 826** rows; the `DISTINCT ON (rxcui)` gap view unchanged at 2,140. Both of
slice 5b's documented caveats are gone.

[#31](https://github.com/cairn-ehr/drugref/issues/31) — `gap_dead_by_expansion_policy`, a **sixth gap kind**: a
contraindication whose object class is *denied* expansion, holds no direct *partner* on the rule's axis, and *does* have one
below, so the rule reaches nobody. Measuring it found a **second, unreported cause**: `gap_unpopulated_contraindication`
counted the rule's own subject as a member although `ddi_candidate_pair` excludes it, so `acetohydroxamic acid` →
`Urease Inhibitors [MoA]` was dead and silent — **12 → 13 classes, 38 → 39 dead rules**. **The round's own review
then found the same defect in the NEW view** (the reach measure was stated twice, only one
copy learning the exclusion); now one view, **`ci_rule_partner_reach`**, with the two gap views as complementary filters on
one column. Re-measured ([#50](https://github.com/cairn-ehr/drugref/issues/50)): still **ONE class**, but **299 drugs held
back, not 300** (the rule's own subject *clomiphene* is filed under it). Everything else held: 2,140 unmatched, 21,664 pairs.

[#45](https://github.com/cairn-ehr/drugref/issues/45) — `contraindications_for_condition(uuid)` walks **UP** from the
patient's condition instead of down from all 641 roots: **0.7–0.9 ms against 9–10 ms**, ~13×. A materialised view was rejected
(a REFRESH in every writer, a new way to be silently stale). The view stays for whole-set access and `WHERE is_direct`;
**equivalence is pinned by test and checked on the real release** — 200 conditions, 4,935 rows, zero difference. Residue
filed: [#47](https://github.com/cairn-ehr/drugref/issues/47) (`medrt_run` counted its own unmatched CI subjects without
persisting them — **closed by the ingest-operability round below**) and [#48](https://github.com/cairn-ehr/drugref/issues/48)
(a non-expanding predicate with no direct member is equally dead, needs its own view; unreachable until a **class-side**
predicate stops expanding). [#50](https://github.com/cairn-ehr/drugref/issues/50), the post-review re-measurement, is
**closed**.

#### Slice 5b.2 — MeSH-keyed indications ✅ DONE — merged as PR #54
The other half of the MeSH-endpoint content: **`may_treat`/`may_prevent`/`may_diagnose`** plus **`induces`** — a
public-domain, drugref-owned drug–disease indication dataset (the MeDIC alternative it holds outright). **No new source.**
Spec: [slice-5b.2](superpowers/specs/2026-07-30-drugref-slice-5b2-mesh-indication-design.md). `db/019`; **622 tests**. Reuses
5b's machinery entirely — registry, ConceptUI→record resolution, closure, candidate-tier posture — with **no new mechanism**.
One orchestrator (`ingest/mesh_rel_run.py`) owns **both** halves, since `condition_parent` edges are derived by both closures
and cannot be split by a discriminator. **Measured end-to-end — the measurement corrected the spec again, in three related
places**:

| | measured | spec §10 had predicted |
|---|---:|---:|
| `moiety_condition_indication` | **14,674** over 3,632 moieties / 1,305 conditions | ≤ 18,125 |
| — by predicate | may_treat **12,662** · may_prevent **1,888** · may_diagnose **124** | — |
| `moiety_induced_condition` | **154** over 108 moieties / 49 conditions | ≤ 170 |
| `condition` (registry) / `condition_parent` | **5,963** / **8,507** | 5,963 / 8,507 ✓ |
| registered `scr_class` | **29 × `3`, 5 × `1`** | 29 / 5 ✓ |
| `gap_condition_without_indication` | **97** (80 C/F-tree + 17 rare-disease SCRs) | 66 |
| `condition_subtree` (CI roots) | **11,512 → 11,605** | 12,311 → 12,415 |
| `condition_contraindication_expanded` | **191,728 → 192,161** (+0.226%) | ≈192,500 (+0.39%) |
| **the direct 5b rows — must not move** | **9,471 · 1,442 · 103/405 · 21,664** | unchanged ✓ |

`indications_for_condition` was checked against `condition_indication_reach` for **every** registry condition — **5,963
checked, 276,343 rows, zero disagreements**, the same pin #45 established for the contraindication pair.

**The three corrections share one cause: the spec measured before the moiety gate.** 1,426 indication subject RxCUIs carry no
moiety, and `condition_subtree` walks the **641** roots that *stored* rules name, not the **677** the release references —
both right about different populations, as 5b's concept-vs-record grain was. Standing correction:
`decisions/indications-do-not-expand.md`.

**Why this is not a contraindication with the sign flipped.** A contraindication expands **down** the condition DAG (Temporal
Lobe Epilepsy is epilepsy). An indication must not: the same walk would distribute a therapeutic claim over subclasses, and
one `may_treat` on *Neoplasms* would manufacture 708 claims MED-RT never made. So **nothing derived is stored**;
`indications_for_condition` walks **UP**, labelling every derived row `is_direct = false` — a **weaker** claim, not a wider
one, and not decoration: **3,719 of 5,963 registry conditions have no direct indication but an ancestor with one**. `induces`
gets its **own table**, no axis row: a shared table plus a forgotten filter reads "carbamazepine treats agranulocytosis".

**Registry widening moves 5b's expanded figures upward — a completion.** One closure spans every MeSH-keyed object, so second
tree numbers only the indication half registers add DAG edges: 10 of 641 contraindication roots grew, none shrank, root set
byte-identical, every direct figure unchanged. **Expect this whenever the registry widens.**

**Two widenings survive the upward walk; 5b.2 COUNTS rather than resolves them.** **168 (drug, condition) pairs** are asserted
as both indication and contraindication — carvedilol/*Heart Failure*, alteplase/*Stroke* — real distinctions (chronic HFrEF vs
acute decompensation) the MeSH grain cannot carry. And **422 of 18,314 assertions** name a subordinate concept, so the row
sits on a BROADER record than MED-RT named: `may_treat` "Seizures, Focal" for eslicarbazepine lands on *Seizures*, which it
aggravates when generalised — **release-grain, above the moiety gate**, not a row count. Both are `MeshRelSummary` fields in
`COMMENT ON`, pinned by tests, each with a follow-up: [#51](https://github.com/cairn-ehr/drugref/issues/51) (how a consumer is
told), [#52](https://github.com/cairn-ehr/drugref/issues/52) (store `concept_ui`, making the row figure queryable).

**Residue filed by the merge review:** [#55](https://github.com/cairn-ehr/drugref/issues/55) — the read path offers
generalisations through an `is_direct` boolean rather than structure, the mitigation `db/019` rejected for `induces`.
**Deferred to 5c by decision**, already revisiting #51/#52. #53 is **closed by the round below**.

#### The #53 population-label round ✅ DONE — merged as PR #56
The three residuals 5b.2's final review filed rather than fixed, plus the six its own review round then found. **No migration
and no production logic change** — docstrings, one published page, and one fixture that could not tell two grains apart. **623
tests**; all three fixtures byte-reproducible from the real releases.

Each claim was **re-measured against the real releases before it was touched**, and all three held. Two were prose: `550 of
13,463` → **`550 of 13,458`**, and `this slice's 1,053` → **the 2,198 codes one run resolves** (1,053 CI + 1,528 IND, 383
named by both). Newly measured: the 81 concepts collapse onto **79** records, and **the indication half has no resolution
gap** (1,528/1,528), which is what makes 4.09% and 2.30% comparable.

**The third was a test claiming to pin a grain its fixture could not distinguish.** The collision counter reports **pairs**;
the test said a drift to **rows** would fail there, and it would not — one overlapping row and one overlapping pair cannot
tell them apart, so removing the production query's `SELECT DISTINCT` left the suite green (verified by mutation). Fixed by
**strengthening the fixture rather than weakening the claim**: **mannitol**, the only subject in the release asserting
`may_treat` *and* `may_prevent` *and* `CI_with` against one object (*Anuria*). The fixture now holds **2 pairs across 3
rows**; the extractor's cap exempts exactly these overlap assertions, scoped to the therapeutic predicates by `is_cap_exempt`
since `Synonym Of` shares their endpoint shape. That the fixture grew is *not* a spec-10 violation, because "the direct rows
must not move" is about **widening the closure** and mannitol is a new **subject**. **Also not done here:** the 193
class-subject indications (filed against #8), `has_SC`, and any read path that ranks or prefers among indications — MED-RT
asserts no line of therapy, no evidence strength and no ordering, and inventing one is slice 5c's curated work rather than a
projection's.

#### Slice 5c — The curated overlay (the moat)
Append-only, **signed** overlay (5c.4, `db/030`) adding **severity + mechanism + management + evidence grading** — the dimensions the
projections lack — **referencing** the 5a/5b candidate rows. **Plan C has already built the overlay MECHANISM** (surrogate key
+ deferred single-live + one-way supersession, generalised over five tables since `db/027`), so 5c inherits a working
correction shape rather than inventing one, and owns #51, #52, #55 and now **#67**.
The **"moat" is quality-control — who may assert — not access or leverage**: data ships paywall-free under copyleft.
Institutionally owned, never a volunteer wiki.

**"Signable, not signed" was true through 5c.1 and is no longer: `5c.4` shipped the signing subsystem** — key
registry with two kinds of revocation, a canonical payload format, per-row curator signatures, per-release
institutional manifests, one verification path over both, and the operator CLI. **The overlay is signed at two
layers, and a signature never gates a read.** Published record: [signing the curated
overlay](https://docs.drugref.org/decisions/signing-the-curated-overlay/).

**The old irreversibility argument does not survive the shape 5c.4 chose, and the correction matters for
sequencing.** 5c.1 reasoned that a signature would be a *column*, so — the floor refusing `UPDATE` — a row
committed before signing existed could never be signed. `5c.4` made signatures **detached**, in their own
insert-only table, which **dissolves that constraint**: a detached signature can be written at any time,
including years after the row. See the callout below for what remains of the sequencing rule.

**The five subsystems ROADMAP used to bundle here, now sequenced** — the design round of 2026-08-06 split them, because
one spec covering all five is one nobody can review and one branch nobody can measure:

> **⇒ EXECUTION ORDER IS NOT THE NUMBERING. 5c.1 ✅ → `5c.4` ✅ (signing) → then 5c.2 / 5c.3 in either order.**
> They are numbered by subject and listed below in that numbering, which is **not** the order they were built in.
>
> **THE REASON FOR THIS ORDER CHANGED, AND A LATER ROUND REORDERING THINGS MUST KNOW THAT.** 5c.1 recorded the
> constraint as *hard and irreversible* — a row committed before signing existed could never be signed, because
> the append-only floor refuses `UPDATE`. **That argument assumed a signature COLUMN, and `5c.4` did not build
> one.** Signatures are detached rows in `assertion_signature`, so any row can be signed at any later time, and
> the irreversibility is gone. Running `5c.4` first turned out to be **good order, not a trap**: curators do not
> accumulate a backlog of unsigned judgements waiting on a tool, and 5c.2's ONC floor is exactly the content
> whose provenance most wants attesting. **A future round with a reason to reorder the remaining slices may** —
> it is weighing convenience against convenience now, not stepping on a one-way door. **No spec exists for
> 5c.2 or 5c.3** — each opens with its own brainstorm/design round.

##### 5c.1 — the assertion shape ✅ DONE — merged as PR [#77](https://github.com/cairn-ehr/drugref/pull/77) (2026-08-06)
Spec: [slice-5c.1 curated
overlay](superpowers/specs/2026-08-06-drugref-slice-5c1-curated-overlay-design.md); plan:
[2026-08-06](plans/2026-08-06-slice-5c1-curated-overlay.md); published record: [curating a drug–condition
pair](https://docs.drugref.org/decisions/curating-a-drug-condition-pair/). `db/029`, no new PL/pgSQL: `curated_interaction`
keyed on the class **RULE** (635 curatable statements after the moiety gate — not the ~739 raw MED-RT terminology-level
count this section used to quote, which was never `class_contraindication`'s own measured row count — inheriting to
21,664 pairs, since `ddi_candidate_pair` is a view and a pair has no stable identity) and `curated_condition` keyed on the
**pair** — deliberately *without* `relationship`, because the same (drug, condition) carries both an indication and a
contraindication in **168** cases and keying on the predicate would write one judgement twice and let the copies
disagree. Two inner-joined read views, two gap views, one operator check (`curated_target_unresolved`). **Shipped
empty**, as planned; curation is 5c.2's job, not this slice's.

**Measured on a fresh `drugref_5c1`, built from the real releases** (UNII 26Feb2026 → MED-RT 2026.07.06 → MeSH 2026 →
MeSH-relations 2026.07.06 → GSRS 2026-02-26, 2026-08-06, chain wall-clock **127.5 s**): every count that must not move
held exactly — `ddi_candidate_pair` **21,664** · `substance_moiety` **19,438** ·
`condition_contraindication_expanded` **192,161**. New: `gap_uncurated_condition_contradiction` **168** (an exact
match to issue #51's own figure) · `gap_uncurated_interaction_rule` **595** (635 rules minus 40 that reach no pair,
already covered by the two pre-existing "class has no members" gap views) · `curated_target_unresolved` /
`curated_ddi_pair` / `curated_condition_ruling` all **0**, correct with nothing curated. `open_question` grew from
21,079 to **21,842** — exactly 168 + 595, nothing else moved. **936 tests at this measurement** (the two review
rounds below then added seven). `EXPLAIN ANALYZE` on all five new/touched views: four run in single-digit
milliseconds or under; `gap_uncurated_interaction_rule` costs **≈2.7 s**,
confirmed (three controls, not reasoned) to be inherited whole from `ddi_candidate_pair`'s own unfiltered-scan cost —
not db/024's duplicated-walk shape — and filed as
[#75](https://github.com/cairn-ehr/drugref/issues/75) rather than fixed here, since the fix belongs inside a prior
slice's hot-path view. Full account: PROJECT-NOTES.md § "Slice 5c.1".

**Two review rounds then edited `db/029` twice more** — the final whole-branch review (a hardcoded `relationship`
CHECK where `db/006` had already replaced that CHECK with an FK into `ci_axis`; the stale `~739`, including inside a
`COMMENT ON TABLE`; and two mutations of the natural key that all 936 tests survived) and the PR review (an untested
clause of the five-table retention guard; `pair_count` as `count(*)` over a join that omits `source`; two unindexed
`question_uuid` foreign keys). Suite **936 → 940 → 943**. Each is described in PROJECT-NOTES § "Slice 5c.1"; none
changes a count above.

**Re-measured post-merge on 2026-08-08** (`drugref_5c1m`, chain 144 s against 127.5 s — uncontrolled, filed as
[#81](https://github.com/cairn-ehr/drugref/issues/81)), because the figures above were taken before those edits and
the merged file had never been run end to end: **every count and every ingest summary reproduces exactly**, and all
four fixes above are confirmed in the live catalog — `pair_count` by `pg_get_viewdef`, the only check that can
distinguish it, since the row counts are identical either way. The `EXPLAIN ANALYZE` timings were not re-run there.
`db/029` is merged and therefore frozen — corrections need a new `db/NNN`.

##### 5c.2 — the ONC high-priority DDI floor ✅ DONE — `db/031`–`db/034`, measured 2026-08-12
Spec: [slice-5c.2](superpowers/specs/2026-08-11-drugref-slice-5c2-onc-ddi-floor-design.md); plan:
[2026-08-11](plans/2026-08-11-slice-5c2-onc-ddi-floor.md); published record: [the ONC high-priority
floor](https://docs.drugref.org/decisions/the-onc-high-priority-floor/). Suite **1297 → 1395**, then **1409** after
the PR-review round. **drugref's first clinical content.** Full account and every measurement: PROJECT-NOTES §
"Slice 5c.2".

**The ONC list enters as a SECOND CANDIDATE SOURCE (`source = 'ONCHIGH'`), not as curator-originated content, and
`db/029` was not touched at all.** 5c.1 had already keyed `class_contraindication` on `(subject, object,
relationship, SOURCE)` and written into `curated_interaction`'s own comment that its key omits `source` *"however
many upstream authorities asserted it"* — the candidate tier was designed for multiple authorities and MED-RT was
merely the only one. `db/031` widens two CHECK vocabularies, adds the `CI_EPC → has_EPC` axis, and adds gap kind
fifteen with its recording table.

**Retrieving the list then refuted the grain the slice was built on**, and `db/032` added a **class-subject**
rule (`class_pair_contraindication` + `curated_class_interaction`, expanded on both sides) — two tables rather
than a polymorphic subject column, because the single-live guard compares by equality and `NULL = NULL` is not
true. `db/033` carries both grains in one `curated_ddi_pair` with a `rule_grain` column; **`db/034` recovered a
measured 3.6× hot-path regression** by giving the class grain its own subtree walk (1.50–1.68 ms empty,
2.87–3.28 ms populated, against 5c.4's 1.4 ms baseline).

**Four of fifteen entries shipped, and that is the clinical review gate working.** Only rules whose object class
is **mechanism-defined** were committed: `Cytochrome P450 3A4 Inhibitors [MoA]` genuinely *is* the population an
irinotecan exposure interaction runs over. Seven class×class entries were drafted and **withheld** because
therapeutic classes are taxonomy, not clinical populations — `Opioid Agonist [EPC]` conflates serotonergic with
opioid-action amplification and includes loperamide; `Central Nervous System Stimulant [EPC]` includes caffeine —
deferred to [#94](https://github.com/cairn-ehr/drugref/issues/94). Four more are unencodable
([#92](https://github.com/cairn-ehr/drugref/issues/92), [#93](https://github.com/cairn-ehr/drugref/issues/93)).
Measured: **8 ONCHIGH candidates, 213 pairs, 0 unresolved endpoints**, `gap_uncurated_interaction_rule` 593 → 591
(595 → 593 from a clean baseline — same net −2; PROJECT-NOTES § "The reference-database rebuild"),
and **MED-RT's `ddi_candidate_pair` 21,664 and `substance_moiety` 19,438 both unmoved.**

**The review round's one lesson, which shapes the next migration: the class grain got the WRITE path and none of
the moiety grain's DETECTORS.** Two defects were fixed here (the `unresolved_onc_endpoint` `gap_key` omitted
`endpoint_role`, folding a class self-pair's two independently-failing endpoints onto one immortal
`question_uuid`; and `register_from_gaps`' retention guard never learned `curated_class_interaction`, whose
cascade into an append-only table turns a closed gap into a permanently aborted ingest for every source). The
rest are filed as **[#96](https://github.com/cairn-ehr/drugref/issues/96)–[#99](https://github.com/cairn-ehr/drugref/issues/99)**
— no worklist gap kind, no cross-grain precedence, no place in a signed release, no expansion-policy review —
plus [#100](https://github.com/cairn-ehr/drugref/issues/100). **They should be taken as one `db/035`, not
piecemeal:** each alone reads as a reasonable follow-up, and together they are why a class rule can be ingested,
graded and reported successful while reaching zero patients.

**⇒ THE `spurious` DEFERRAL HAS MOVED OFF THIS SLICE, and a later round must not re-attach it here.** 5c.1 handed
5c.2 the question of how "drugref believes this upstream row is wrong" reaches a consumer. But `spurious` is a
**`curated_condition.ruling`**, and 5c.2 curates **`curated_interaction`** — the interaction half only. This slice
therefore *cannot* discharge it, and did not. **It belongs to the first slice that curates the 168 contradicted
drug–condition pairs** ([#51](https://github.com/cairn-ehr/drugref/issues/51)). See [curating a drug–condition
pair](https://docs.drugref.org/decisions/curating-a-drug-condition-pair/) for the full argument.

**Also worth re-reading against this slice:** [#73](https://github.com/cairn-ehr/drugref/issues/73) says two views
read every source at once. For `ddi_candidate_pair` that is now *wanted* — it is how both authorities reach one
consumer — so the issue's text should be re-read now that the behaviour is real rather than hypothetical.

##### 5c.2a — the class grain's detectors ✅ DONE — `db/035`, measured 2026-08-14
The migration the paragraph above asked for, taken as one. Closes
[#90](https://github.com/cairn-ehr/drugref/issues/90), [#96](https://github.com/cairn-ehr/drugref/issues/96),
[#97](https://github.com/cairn-ehr/drugref/issues/97), [#98](https://github.com/cairn-ehr/drugref/issues/98) and
[#99](https://github.com/cairn-ehr/drugref/issues/99). Suite **1409 → 1451**. Full account and every measurement:
PROJECT-NOTES § "The class-grain detector round".

**No new clinical content and no behaviour change on real data** — every count on the reference database is
byte-identical before and after (`ddi_candidate_pair` MED-RT 21,664 · `substance_moiety` 19,438 ·
`curated_ddi_pair` 255 · `gap_uncurated_interaction_rule` 593 · `curated_target_unresolved` 0), and all four new
class-grain objects read **0**, because #94 withheld the seven class×class entries and nothing else writes the
grain. **That is the point: the round adds detectors, and a detector's correct reading on today's data is zero.**

**Seven pieces, one shape.** `severity_kind` makes the four grades **ordered data** (five identical CHECK
constraints became five foreign keys — db/006's finding for the fifth time), because #97's answer has to be
writable in SQL and `ORDER BY severity` sorts `minor` above `moderate`. `class_pair_rule_reach` is
`ci_rule_partner_reach` one grain over, and a **product** rather than a count since a class×class rule expands on
both sides; `gap_uncurated_class_interaction_rule` (gap kind **sixteen**) is the grain's primary question, which it
shipped without. `gap_unreviewed_expansion_root` was **widened in place, not copied** — one class, one policy row,
one immortal `question_uuid`. `curated_target_unresolved` gained a third arm and one trailing column.
`curated_ddi_pair` gained `severity_rank` and a **stated precedence**; `curated_grain_disagreement` is the
worklist that keeps it honest. `curated_class_interaction` became a `signature_target_kind`.

**⇒ THE PRECEDENCE, decided this round and now the read path's contract:
`ORDER BY severity_rank, (rule_grain = 'moiety_rule') DESC` — most severe first, moiety grain breaking ties.**
Severity-first because under-warning is the harm direction on this path, as with every other choice here (a
signature never gates a read; a missing expansion policy expands). **It is an ORDER, not a filter** — both rows
still appear. What makes it defensible rather than merely loud is `curated_grain_disagreement`: over-warning has a
real cost (click-through fatigue is why prescribers stop reading alerts), so a broad class rule out-ranking a
curator's specific milder grade must be **finite work somebody reconciles**, not permanent noise.

**Hot path unmoved, measured interleaved against a `db/034` control to control for machine noise** (issue #81's
discipline): db/034 mean **1.626 ms**, db/035 mean **1.662 ms** over 12 alternating runs each — a 2% difference
inside the control's own 0.34 ms spread — and the moiety grain's recursive union is **byte-identical**
(`cost=14.94..3886.97 rows=37414`, actual 1235). The `severity_kind` join is two hash joins against a four-row
table. **`TEMPLATE drugref_db034` → `drugref migrate` was re-tested** and works; the reference database is now
**`drugref_db035`** (ledger 35 rows) with `drugref_db034` kept as the control.

**Three findings filed rather than fixed** — [#104](https://github.com/cairn-ehr/drugref/issues/104) **`curate`
leaves the question registry stale until the next ingest** (601 stored vs 593 derived on the reference database:
the 8 rules ONCHIGH curation answered still sit on the worklist, because `curate` is deliberately not a chain step
and only ingest re-derives — **pre-existing, verified identical on db/034**) ·
[#105](https://github.com/cairn-ehr/drugref/issues/105) promote `curated_grain_disagreement` to a gap kind once
class-grain content ships (a `gap_key` is frozen forever and zero class-grain rows exist to choose its grain
against) · [#106](https://github.com/cairn-ehr/drugref/issues/106) two **moiety**-grain rules on different axes can
also grade one pair differently, which the cross-grain detector deliberately does not compare.

##### 5c.2b — the PR #107 review round ✅ DONE — `db/036`, `cli_status.py`, 2026-08-14
Five specialist reviewers over the round above. **The migration held; the Python that reads it did not.** Suite
**1451 → 1465**. Full account and every measurement: PROJECT-NOTES § "The PR #107 review round".

**⇒ THE SHIP-BLOCKER, reproduced on the `drugref_db034` control: `drugref status` crashed with a raw psycopg
traceback on every database that had not yet run `drugref migrate`.** `db/035` widened
`curated_target_unresolved` with `subject_class`, so a stale database fails with `UndefinedColumn` — a *sibling*
of `UndefinedTable`, not a subclass — and the guard written for exactly that moment did not fire. **The standing
rule this bought: a migration widening a view a guarded block reads must widen that block's exception tuple in
the same commit.** Exit 1 + traceback → exit 2 + one sentence.

**Three more of the same shape.** `drugref status` printed the literal `None` where every class-grain orphan's
subject belongs, so #90's detector said *that* a judgement was orphaned and never *which* — fixed with an
`UnresolvedTarget.subject` property, arm-label-free. The class-grain block had **no guard at all**, on reasoning
that did not follow (db/029 does not imply db/035). And **permuting a frozen signing field list passed all 249
signing tests** — the coverage test compares sets and the committed vectors never consult `FIELD_LISTS`, so a
reorder would have broken only signatures recorded *before* it, in production, as `BAD_SIGNATURE`. Now pinned,
and `curated_class_interaction/v1` has the vector case it shipped without.

**`cli.py` hit rule 4's 500-line cap and the answer was a module, not a diet** — shaving comments to fit sets
rule 4 against rule 3. Read → `curation.class_grain_counts`, voice → **`cli_status.py`** (the sixth `cli_*`
module). `cli.py` 483 → **450**.

**`db/036` is three `COMMENT ON` statements and no schema change**, because `db/035` is applied and immutable and
these ship in `pg_description` — what `\d+` prints. The frozen `gap_key` was documented as `AXIS:` where the
value is **`CI_AXIS:`**; `max_pair_count` claimed to be "exact about zero" when a one-member self-pair rule reads
1 and reaches 0; `curated_grain_disagreement` named one deliberate omission and has two. Reference database is
now **`drugref_db036`** (ledger 36), `drugref_db034` still kept as the control.

**Five findings filed rather than fixed** — [#108](https://github.com/cairn-ehr/drugref/issues/108) make
`max_pair_count` exact · [#109](https://github.com/cairn-ehr/drugref/issues/109) mirror-oriented rule pairs ·
[#110](https://github.com/cairn-ehr/drugref/issues/110) ship the #97 precedence as a view (nothing in `src/`
reads `severity_rank`) · [#111](https://github.com/cairn-ehr/drugref/issues/111) the status block's zeros need a
denominator · [#112](https://github.com/cairn-ehr/drugref/issues/112) measure the disagreement self-join before
class-grain content ships. **Four of the five are closed by the debt round below; 112 stays open by design.**

##### 5c.2c — the low-hanging-debt round ✅ DONE — `db/037`, `curated_read.py`, 2026-08-14
A sweep of all **51** open issues for work that is small, self-contained and needs no design decision. **Seven
FIXED** — 79, 87, 100, 108, 109, 110, 111 — **plus 19 and 106 answered by measurement**, so nine touched. (This
said "Eight cleared" over a list of nine numbers here, in PROJECT-NOTES, in HANDOVER and in the commit message
at once — a total beside the list it counts, disagreeing in four homes.) Suite **1465 → 1516** including the
review round below, `ruff` clean. Full account, every measurement and the list of what was deliberately NOT
taken: PROJECT-NOTES § "The low-hanging-debt round".

**`db/037` is three view corrections and one new view**, and **every published count is byte-identical between
`drugref_db036` and `drugref_db037`** — the class grain holds zero rows (#94 withheld its seven entries), which
is exactly what makes this the cheap moment to fix its arithmetic. **#108**: `max_pair_count` now subtracts a
published `shared_effective_member_count`, so reach is `|S|·|O| − |S ∩ O|` — the self-pair rule is the special
case, not the fix, and the *same wrong number* had the worklist queueing a rule no answer could change while
`status` hid it. **#109**: `curated_grain_disagreement` normalises orientation with `LEAST`/`GREATEST` (an
equi-join, where the obvious `OR` of two arm pairs is not) — these rows are directional per db/006 and the two
tiers come from different upstreams, so a moiety rule on `(a,b)` and a class rule on `(b,a)` were one clinical
pair nothing brought together. **#110**: the precedence is now a view, `curated_ddi_pair_effective`, with
**`NULLS FIRST`** — Postgres's default sorted an unrankable severity *below* `minor`, inverting the harm
direction in the one path db/035 called safe. **It is not a no-op today: 255 rows collapse to 213**, all 42
doubled pairs explained by `candidate_source` alone.

**`curated_read.py` is the seventh module split out to hold rule 4**, and it exists because a view with no
caller is half a feature — nothing in `src/` read `severity_rank`, so no test could regress the precedence.
**#79** closes the other way the same principle bites: `tests/` was blanket-exempt from E501, so it had no
column bound at all (**497** lines over 88 as of the review round — 415 when the carve-out was removed, and that
figure was stale on the day it shipped — against a ceiling that has been **119** at every measurement).
It is bounded at 120 in `tests/ruff.toml`, with `tests/test_lint_bounds.py` driving ruff at a path in each tree.
**#100** pins `ci_class_subtree`'s narrow definition from `pg_depend`, so a stray `db/033` replay fails the
suite rather than a latency graph. **#87** merges two registry queries into one `keys.for_verification`, making
`holder is None ⟺ UNKNOWN_KEY` true by construction. **#111** gives the class-grain status block a denominator.

**Reference database is now `drugref_db037`** (ledger 37); **`drugref_db036` is kept as `db/037`'s before/after
control** and `drugref_db034` stays as the pre-`db/035` one — two controls answering different questions.
**Issue 86's direction is DECIDED and deliberately not built**: add `signed_by_unknown_key` as a fourth
`signature_status` value, a published-vocabulary widening that is a round of its own.

##### 5c.2d — the PR #113 review round ✅ DONE — no new migration, 2026-08-15
Six review agents over 5c.2c's own diff. **Three silent defects, all mutation-verified**, and four findings
filed rather than fixed (114–117). Suite **1511 → 1516**, `ruff` clean. Full account: PROJECT-NOTES § "The PR
#113 review round".

**Two were wrong data reaching a consumer, and neither failed a test.** `GradedPair` was built by positional
splat from a hand-written SELECT — swapping `mechanism` and `management` left the WHOLE SUITE GREEN while
drugref labelled management advice as mechanism; one `_COLUMNS` list now generates the SELECT and binds by
keyword, making the swap **unrepresentable**. And `curated_ddi_pair_effective`'s determinism tail was missing
`via_subject_class`, so two class rules over one pair tied on every key and `DISTINCT ON` followed **heap
order** — which mechanism text a prescribing client read depended on physical row position, flippable by a
rebuild or dump/restore. **The third was the guard**: db/037 corrects `class_pair_rule_reach`'s arithmetic
without changing a name, so a db/035-or-036 database printed the OLD, OVERSTATED numbers with nothing raised.

**TWO TESTS PASSED FOR THE WRONG REASON**, which is the finding worth carrying forward: over-determined
fixtures whose docstrings claimed the opposite. Deleting `(rule_grain = 'moiety_rule') DESC` from db/037
outright left the suite green, because the fixture's sources ordered the same way the grain key did.

**`db/037` was EDITED rather than superseded** — it was *then* unmerged, and PROJECT-NOTES § repo facts records
that the ledger binds a *database*, not the repo. The reference DB was rebuilt from `TEMPLATE drugref_db036` and
every published count re-verified byte-identical; the pre-edit copy is kept as **`drugref_db037_pre_review`**.
**That window has closed: PR [#113](https://github.com/cairn-ehr/drugref/pull/113) merged on 2026-08-15
(`b449e7f`), so `db/037` is FROZEN with `db/029`–`db/036` and any correction to it needs a new `db/NNN`.**

##### 5c.2d — the db/038 round: PR #113's four filed issues, closed ✅ DONE
The four issues that review **filed rather than fixed** (114–117), taken together because three touch one read
path and the fourth needed the migration the first was already writing. **`db/038`**, one new command, one
rename. Suite **1516 → 1540**, `ruff` clean. Full account, every measurement, and the mutation evidence:
PROJECT-NOTES § "The db/038 round".

- **#116 — db/037 fixed the SORT and left the PAYLOAD.** `NULLS FIRST` is right (under-warning is the harm
  direction), but inside a `DISTINCT ON` it makes an unrankable severity **WIN** and DISCARDS the rankable
  competitor, so the client got `severity_rank = NULL` with no second row behind it — and every threshold form
  drops a NULL. Against a `minor` competitor that was an improvement; against `contraindicated` it means a
  numeric client sees **nothing**, which is why the existing test (competitor graded `minor`) could not see it.
  `db/038` § 1 publishes **`effective_rank = COALESCE(severity_rank, 0)`** and orders on it; `severity_rank`
  stays NULLABLE so the schema fault remains visible. **§ 2 gives that fault a detector and a `drugref status`
  block**, because a mitigation that hides its own trigger is how issues 74 and 76 happened.
- **#115 — `total` → `rules_total`.** It denominates `ungraded` and `dead` only; **`disagreements` counts
  PAIRS**, and `ClassGrainCounts(rules_total=7, …, disagreements=2263)` is the expected shape once class-grain
  content ships. All three invariants are now asserted, not described. **Cost: `curation.py` 500 → 523 lines**,
  measured onto [#89](https://github.com/cairn-ehr/drugref/issues/89) with the seam named rather than split
  inside a correctness diff (db/030's precedent).
- **#117 — BOTH options.** `db/038` § 3 re-issues the `COMMENT ON` with **seven** (db/027's precedent, one word
  changed); db/035's plain `--` prose cannot be corrected by anything, so that half is a standing rule in
  PROJECT-NOTES.
- **#114 — `drugref interactions <moiety> [--with <other>]`**, in a new `cli_interactions.py`. The issue said
  option 1 "would actually test the design" and it did: **one** lookup can only answer the SUBJECT direction
  (so the command says so, since an empty list otherwise reads as "these do not interact"), while the pair form
  does **two**, visibly, as `effective_grades_for`'s contract requires. Verified on the real ONC atazanavir/PPI
  entry, both ways round.

**⇒ `db/038` IS CONTENT-NEUTRAL AND THAT IS MEASURED, not assumed.** On `drugref_db038` (from `TEMPLATE
drugref_db037` + `drugref migrate`, the **fifth** round running for that workflow) every published count is
byte-identical to db037 — `ddi_candidate_pair` 21,877 · `curated_ddi_pair` 255 · `curated_ddi_pair_effective`
213 · `open_question` 21,848 · `gap_unpopulated_contraindication` 13 · `condition_contraindication_expanded`
192,161 · `class_expansion_policy` 14 · `loaded_release` 6 — **and the new column equals its source on all 255
live rows** while `curated_unrankable_severity` is empty, which is what makes the claim checkable rather than
merely stated.

**⇒ TWO THINGS THIS ROUND FOUND THAT NOBODY FILED.** Issue 114 was **already closed** when the round started —
`ed1ab5e`'s own *"Filed rather than fixed: #114"* sentence closed it, the **fifth** occurrence of that pattern
and the **second** with the identical template that closed #61, in a repo that documents the trap and its
mechanism in full. Reopened, fixed, and lodged as
[#118](https://github.com/cairn-ehr/drugref/issues/118) for a mechanical guard. And
`test_the_callers_own_order_by_puts_an_unrankable_severity_first` — added by the PR #113 review — was
**over-determined**: the unrankable partner sat on the smaller uuid, so deleting the rank key from the caller's
`ORDER BY` left it green. Swapped, and the mutation now makes it red.

##### 5c.2e — the PR #119 review round ✅ DONE — `db/038` edited in place, 2026-08-15

Five review agents plus hand verification against the live catalog. **Suite 1540 → 1564**, `ruff` clean, every
published count re-verified byte-identical after the edits. Full account: PROJECT-NOTES § "The PR #119 review
round". `db/038` was still unmerged, so the SQL fixes went into the file and `drugref_db038` was rebuilt.

- **⇒ THE HEADLINE — `db/038` § 3 SILENTLY REVERTED `db/036`.** `COMMENT ON` overwrites rather than merges, and
  **three** migrations state a comment over `gap_uncurated_class_interaction_rule`. § 3 rebuilt from db/035's
  text while the live text was **db/036's**, restoring the wrong `AXIS:` gap_key spelling db/036 existed to fix.
  `question_uuid = uuid5(gap_kind, gap_key)` and the key is frozen and externally citable, so a consumer
  reconstructing it from `\d+` computes a uuid matching nothing **with no error**. **The round's own
  verification could not see it** — it grepped only the word being changed. Two new standing rules in
  PROJECT-NOTES; `tests/test_class_grain_comment.py` pins the whole comment.
- **A SECOND STALE COMMENT:** `CREATE OR REPLACE VIEW` **preserves** comments, so db/037's
  `COMMENT ON VIEW curated_ddi_pair` still prescribed `ORDER BY severity_rank NULLS FIRST` — the first thing
  `\d+` prints, naming the column § 1 exists to retire. Re-issued.
- **SIX MUTATIONS THAT SURVIVED THE WHOLE SUITE NOW FAIL.** The **moiety arm** of the new detector was entirely
  unexercised (whole arm, `applies`, `superseded_by`) — the grain carrying all 255 curated pairs — and the
  moiety half's `COALESCE` was unpinned, so reverting it reintroduced #116 where all the data lives. The
  `COALESCE` pin is now grain-agnostic (`effective_rank IS NULL` must be 0 across the view).
- **THE DETECTOR WAS KEYED ON THE CAUSE, NOT THE CONDITION.** `sk.severity IS NULL` tests whether the join
  missed; the harm comes from a NULL **rank**. Dropping `severity_kind`'s `NOT NULL` gave full harm, zero
  detection and an affirmative `none`. Now `sk.severity_rank IS NULL`, which strictly widens.
- **`rank 0` WAS A PROMISE, NOW A CHECK.** `CHECK (severity_rank >= 1)` reserves the sentinel; a future level at
  rank 0 would otherwise be indistinguishable from the fault, and silently so.
- **`severity_rank` HAD NO PYTHON READER.** The CLI printed a schema fault as a bland `rank 0`; it now banners
  `** UNRANKABLE ... **` and names `drugref status`. `GradedPair` gained a `__post_init__` enforcing
  `effective_rank = COALESCE(severity_rank, 0)`, and `cli_interactions` joined the project-wide no-embedded-SQL
  guard it had shipped exempt from.
- **FOUR FINDINGS FILED RATHER THAN BODGED**, each needing a design call:
  [#120](https://github.com/cairn-ehr/drugref/issues/120) (unknown `moiety_uuid` reads as an ungraded drug —
  **the one with a harm direction**), [#121](https://github.com/cairn-ehr/drugref/issues/121) (orphaned grade
  reads as "no curated grade"), [#122](https://github.com/cairn-ehr/drugref/issues/122) (guards assert one cause
  and `__cause__` is dropped), [#123](https://github.com/cairn-ehr/drugref/issues/123) (detector sweeps 2 of 5
  FK tables; line now labelled `(DDI grain)`).

##### 5c.2f — the guard round ✅ DONE — issues 118, 120, 122, 2026-08-15

**No migration at all**, the first such round since 5c.1: `db/038` merged with PR
[#119](https://github.com/cairn-ehr/drugref/pull/119) and is FROZEN, and all three defects were fixable in
Python. **Suite 1564 → 1644 across the round and its own review**, `ruff` clean, **thirteen mutations run
against the fixed branches and all thirteen fail**. Three new modules (`commit_lint.py`, `registry_read.py`,
`migration_guard.py`) and one shipped git hook. Full account: PROJECT-NOTES § "The guard round" and § "The
guard round's own review".

- **⇒ THE HEADLINE — THE COMMIT GUARD FOUND A SIXTH ISSUE ON ITS FIRST RUN.** Issue 118 was filed against five
  commits that closed an issue nobody meant to close; running the finished check over the whole history
  flagged 14 and turned up **#108**, closed by `293758c`'s *"Filed rather than fixed: #108 …"* one round
  BEFORE #114, with #109–#112 in the same sentence left open. **Every document in this repo said five.** The
  failure is silent by construction, so every hand count undercounted — and the count was the evidence the
  prose rule was failing. **The split was ALSO miscounted**: 10 accidental and 4 deliberate, not 6/8, because
  four commits re-closed a known issue by QUOTING the offending sentence while documenting the rule. Six
  ISSUES, ten COMMITS.
- **⇒ AND THE ROUND'S OWN REVIEW FOUND TWO OF THE THREE GUARDS BROKEN.** The hook's `python3` fallback aborted
  **every** commit (a PEP 604 annotation evaluated at import time, under the 3.9 the OS ships), and the hook
  was blind to `git commit -m`, where git keeps `#` lines rather than stripping them. A third finding is the
  sharper one: `test_a_database_predating_db038_is_told_to_migrate` **asserted the inverse of its own name**,
  green, because the guard's own rollback restored the view the test had dropped and `match="drugref migrate"`
  is a substring of all four messages. Details: PROJECT-NOTES § "The guard round's own review".
- **THE HOOK SHIPS AS `.githooks/commit-msg` + a pure `drugref.commit_lint`**, installed with
  `git config core.hooksPath .githooks`, escape `--no-verify`. The proposed `Closes-intentionally:` trailer is
  **deliberately not shipped**: that spelling would not close anything on GitHub, so it would have been a
  second vocabulary whose name states the opposite of its effect. **Its unclosed half is now
  [#124](https://github.com/cairn-ehr/drugref/issues/124): GitHub also parses PR DESCRIPTIONS, which no commit
  hook can see, and that surface has never been measured.**
- **#120 — AN ABSENCE ABOUT THE OVERLAY WAS PRINTED AS AN ANSWER ABOUT A DRUG.** A uuid naming nothing rendered
  identically to an ungraded drug, exit 0, and the pair form asserted "no curated grade for this pair in either
  direction" about a pair that might not exist. Now a read of the IDENTITY SPINE (`registry_read`, a module of
  its own — `curated_read` is scoped to the overlay and that scope is the whole reason the view cannot answer),
  a banner naming each unknown uuid, **no grade block at all**, and **exit 2**. Existence is checked BEFORE the
  self-pair branch, pinned by a test. **The old test asserted the DEFECT as the contract** and was replaced.
- **#122 — A GUARD MAY NOT ASSERT A CAUSE IT HAS NOT CONFIRMED, AND PROBING THE RELATION IS NOT ENOUGH.** The
  worst case is self-referential: dropping `severity_kind` takes `curated_unrankable_severity` with it, so the
  detector written to REPORT that fault told the operator to run a migration the ledger says already ran — a
  no-op, forever. **The LEDGER is the only discriminator**: absent + applied means DROPPED. Four states, one
  pure wording function, `exc.diag.message_primary` carried in every branch (`cli.main` renders only the outer
  message, so `__cause__` reached nobody). `db.missing_relations` **rolls back first** or the probe raises
  `InFailedSqlTransaction` from inside the guard. **A FIFTH guard now covers the clinician path**, which alone
  had none.

##### 5c.2g — FDA-CYP potency classes ✅ DONE — `db/039`–`db/043`, measured 2026-08-17
Spec: [slice-5c.2g FDA-CYP classes](superpowers/specs/2026-08-16-drugref-slice-5c2g-fda-cyp-classes-design.md).
**A prerequisite for 5c.3, taken before it**, on the FDA spike's own instruction: *"Land `FDA-CYP` before SPL
mining; SPL already proved it needs potency-specific classes, and mining first would either drop the band or
build a temporary vocabulary."* The spike's preferred order puts DrugCentral's DDI slice first, but that is a
sequencing preference between two independent slices, **not a dependency** — FDA-CYP blocks 5c.3 and
DrugCentral does not block FDA-CYP. Source decision, rule-6 determination and reproduction manifest: [FDA
interaction and toxicity source
spike](superpowers/specs/2026-08-16-drugref-fda-interaction-and-toxicity-source-spike.md) § 3.

**What landed.** **65 PK classes** (`source = 'FDA-CYP'`, `concept_type = 'PK'`, deterministic `source_code`
such as `cyp:3a:inhibitor:strong`, `published_code` **NULL** because FDA publishes none), `has_PK` membership,
and `fda_cyp_assertion` — a rebuildable projection holding **every** parsed tuple **including the ones
deliberately not promoted**. **Five migrations, and four of them exist because something asserted a property
it had not confirmed**: `db/039` (the three-place source vocabulary, the projection, the gap view, the
seventeenth question kind), `db/040` (the gap view's grain), `db/041` (the allowlist that left a silent hole),
`db/042` (**the curator questions quoted footnote markers as part of FDA's substance names — the slice's own
headline defect, reproduced in its own output**; see below), `db/043` (**the closed pathway vocabulary existed
only in Python, and db/042's own view left its grain finer than the `gap_key` built from it, so two printed
forms of one name could silently share one immortal `question_uuid`**). Both vocabularies it uses — `PK` and `has_PK` — already existed since
`db/003`, which is the whole argument for projecting FDA's roles as classes rather than inventing a mechanism.

- **⇒ IT CREATES NO DDI PAIR, AND THAT IS A REFUSAL RATHER THAN A DEFERRAL.** FDA calls its table an optional,
  non-exhaustive interpretive guide and excludes other mechanisms. Joining its columns — 20 strong CYP3A
  inhibitors × 40 sensitive CYP3A substrates — would manufacture ~800 pairs **no source asserts**, carrying
  drugref's own provenance. A pair still needs an SPL assertion or a curated clinical source. Verified as an
  invariance, not asserted: `ddi_candidate_pair` and every `curated_*` count are read before and after on one
  database and must be equal.
- **⇒ TWO OF FDA'S FOOTNOTES NEGATE THE ROW THEY SIT ON, and that finding shaped the whole slice.** Bupropion's
  row asserts `2B6 sensitive substrate` while footnote 2 says *"Bupropion itself is not a sensitive
  substrate"*; rolapitant's asserts P-gp/BCRP inhibition while footnote 17 denies it for the IV route. So
  dropping a qualifier is not lossy simplification — for those rows it makes drugref **assert the opposite of
  its cited source**. A footnoted cell therefore writes **no membership**: it lands in the projection with the
  footnote text and raises a question. **Deciding whether a footnote negates is a clinical reading**, and the
  invariant is the spike's: *ingest preserves evidence; curation creates clinical judgement.*
- **⇒ THE FOOTNOTE TEXT IS PARSED FROM THE PAGE, NOT TRANSCRIBED.** The first implementation hardcoded FDA's
  prose as a Python dict. `checksum` and `dateModified` exist to make a source change loud, and that one
  column escaped both: a reworded footnote would re-ingest **green** while storing the old wording in the
  column whose entire purpose is carrying FDA's words.
- **⇒ THE GAP VIEW'S FIRST GRAIN WAS WRONG IN THE FINER DIRECTION (`db/040`).** It grouped every disposition on
  `(substance, column, pathway)`, so *"which drugref moiety is FDA's rifampin?"* was asked **eight times**, once
  per cell mentioning rifampin — **71 immortal `question_uuid`s where 55 belong**. Only `withheld_qualified` is
  genuinely per-cell (each footnoted cell is its own adjudication); the other three are per-substance. The view
  is now a `UNION ALL` of two grains, and the `COALESCE` in the key was chosen so **every `withheld_qualified`
  UUID stayed byte-identical** — proven by computing both key sets and diffing them, not argued. Issue 41's
  standing rule, in the direction it is usually not caught.
- **⇒ AND `db/040` THEN LEFT ITS OWN SILENT HOLE (`db/041`).** Splitting the view required a `WHERE` per half,
  and those clauses became an **allowlist** of the four known dispositions — so a future fifth or sixth value
  would be **dropped from the worklist entirely**, never reaching the question `CASE`, while `questions.py`'s
  comment claimed it "aborts the ingest loudly". A gate that does not fire, with a comment saying it does
  (issues 74/66/76, plus 122). The subject half now reads `NOT IN ('member','withheld_qualified')`, so an
  unknown disposition reaches the `CASE`, matches no `WHEN`, yields `NULL` and trips `question_text`'s
  `NOT NULL`. Pinned by a test that was verified failing first.
- **Name resolution is exact, case-insensitive, and never fuzzy: 224 of 244.** The 20-name residue is **five
  different jobs** and none is auto-bridged — 9 combination regimens, 3 non-drug entities, 3 enantiomers, 3
  apparent synonyms, 1 apparent metabolite, 1 group term. **Ambiguity is unresolved, never "pick the first"**,
  pinned by a test even though nothing is ambiguous today.
- **The stored `disposition` has FIVE values, not nine, and that is the standing rule at work.** Only
  `combination_regimen` and `non_drug_entity` name a category, because only those two are **asserted by FDA**
  (the regimen string it wrote; its own pinned five-substance sentence). Calling `R-venlafaxine` an
  "enantiomer of a held racemate" would be a chemical relationship inferred from a **string prefix** — issue
  122's manufactured-cause defect. The other four collapse to `unresolved_substance`.
- **Filed, not built:** [#128](https://github.com/cairn-ehr/drugref/issues/128) stereoisomer assertions against
  a held racemate — pharmacology with a literature behind it, scoped to every source since DrugCentral will
  meet it too; [#129](https://github.com/cairn-ehr/drugref/issues/129) `registry_near_name` ships NULL, because
  a near-name heuristic with no measured output is the pattern this slice spent five corrections catching;
  [#130](https://github.com/cairn-ehr/drugref/issues/130) `cli.py` at exactly 500/500 against its own hard cap.
- **⇒ SEVEN OF THE SPEC'S OWN FIGURES WERE WRONG AND IMPLEMENTATION FOUND EVERY ONE** — the last two
  found by the FINAL review, after the count had been written down as five.** Full account:
  PROJECT-NOTES § "Slice 5c.2g". They share one shape — **something asserted a property it had not
  confirmed** — which is the same defect the slice exists to prevent FDA's table from inflicting on drugref.

- **Release identity is the page's own `dateModified`** — `2026-05-29T14:00`, in JSON-LD and two meta tags —
  **not fetch time.** The source spike said the page carries no release identifier; it carries one. Fetch time
  records when drugref looked, `dateModified` records when FDA changed the content, and only the second
  distinguishes a re-fetch of unchanged material from a revision. A page without it **fails and names the
  field** rather than substituting fetch time. `--release` on the CLI is a CHECK, not an override: supply it
  and it must match the page or the ingest fails naming both values, **before `provenance.open_run`**, so a
  wrong `--release` leaves no history behind.

> **This entry's own six planning bullets were deleted when it went to DONE, and one of them said the page
> publishes no release identifier — the opposite of what shipped, sitting under a ✅.** They were written
> before the round and left in place beneath the account of what it did. **A planning bullet that survives
> its own slice becomes a claim**, and this file has now made that mistake once; the account above is the one
> home for what 5c.2g does.

##### 5c.3 — SPL/DailyMed mining
`ONSIDES`-*method*, MIT precedent — a full ingest slice of its own. **Still no design spec; the brainstorm and
the source measurement are DONE (2026-08-24) and the design round starts from them.** Two candidate sources
were licence-checked during 5c.2 and measured on 2026-08-13 — full account: PROJECT-NOTES § "The 5c.3 source
evaluation". **Its missing potency vocabulary was 5c.2g's job, and it landed.**

**⇒ THE SOURCE MEASUREMENT ROUND ✅ DONE (2026-08-24) — no migration, no ingest, suite 2005 → 2064.**
[Measurement record](superpowers/specs/2026-08-24-drugref-slice-5c3-spl-mining-measurement.md); full account
and every figure: PROJECT-NOTES § "The 5c.3 SPL measurement round". **Read the numbers there, not here.**
Three brainstorm decisions scope the slice: it produces **both** drug × class rules and drug × drug exemplars,
kept separate with shared provenance; extraction is **deterministic entity recognition with NO relation
extraction**; and the corpus is measured in full, never sampled. Four findings the design round inherits:

- **The corpus changed.** openFDA's bulk labels carry the same section under an explicit **CC0 1.0**
  dedication at **1.73 GB** with `drug_interactions` **pre-split** and an `openfda.unii` bridge — against
  DailyMed's 18 GB, its LOINC splitting, and NLM's explicit *"cannot guarantee the copyright status"*. Both
  were taken, DailyMed as the cross-check.
- **Rule 6 is NOT settled and is [#154](https://github.com/cairn-ehr/drugref/issues/154)** — a posture call
  for the owner. Derived facts and citations are clear under either reading; **verbatim prose is not**, and
  the recommendation is to reference rather than bundle it.
- **The potency band is PAIR-scoped, not class-scoped, and affects a quarter of all wordings** — which
  retires two of [#102](https://github.com/cairn-ehr/drugref/issues/102)'s four options. FDA's own footnote 20
  names tizanidine while banding ciprofloxacin *moderate*, so the label and the table never disagreed.
- **The pair yield justifies the slice on its own**: **17,279–21,201** distinct candidate pairs, **86.9–88.5%
  novel** — against the 7,501 at 91% that justified DrugCentral. The class half is where every unsolved
  problem lives (#155).

**The evaluation moved one source and killed the other's data:**

- **OnSIDES's DATA is not a DDI source and must stop being listed as one.** Its schema has no second-drug
  column; the unit is one label × one MedDRA term. Across all **6,928,666** rows, **one** interaction-flavoured
  MedDRA term exists (`Interaction with alcohol`, 13 rows). Warnings and Precautions *is* parsed (1.2M rows) —
  the section 5c.2 hoped for — but what the model extracts from it is adverse effects, because that is what it
  was trained to extract. **The METHOD is still the precedent this slice is named for** (MIT, reusable: label
  fetch, LOINC section split, annotate/train/threshold, RxNorm bridge).
- **The material is SPL section `34073-7` DRUG INTERACTIONS, which OnSIDES does not read** — its US pipeline
  enumerates seven section codes and 34073-7 is not among them. Verified on a live DailyMed label: tizanidine's
  section 7 states drugref's own shipped ONC entry in 690 characters, **and qualifies it by potency band**
  (*strong* CYP1A2 inhibitors contraindicated, *moderate or weak* "avoid"). MED-RT's
  `Cytochrome P450 1A2 Inhibitors [MoA]` is one undifferentiated class, so **the design round must decide what
  the schema does with a potency band** — carry it, or drop it and accept over-warning. It cannot ignore it.
- **DrugCentral is a genuine candidate second source (`source = 'DRUGCENTRAL'`, the shape 5c.2 built for a
  second authority), and its rule-6 question is ANSWERED.** Measured: **7,621** pairs, **7,000 (91.9%) with both
  endpoints keyable against drugref's registry today** (**re-measured 2026-08-23: 6,991, 91.7%** — the round
  below), 6,973 moiety × moiety — the grain the moiety rule
  already handles. **Every row cites one of three references, and they were read rather than inferred from
  DrugCentral's own CC BY-SA: 7,571 come from the VHA's NDF-RT (US federal, clean, and MED-RT's predecessor);
  13 from Stockley's Drug Interactions (a copyrighted book) and 37 from Lexicomp (commercial) — both OUT.
  Bundle `ddi_ref_id = 2` only**, which costs nothing measurable because those same 50 rows are the ones whose
  class-named endpoints do not resolve anyway.
- **And it is not a restatement of what drugref already has: of 6,941 resolvable moiety pairs, drugref holds
  604 (8.7%) via MED-RT and 6,337 are NEW** — **re-measured 2026-08-23 as 7,501 / 635 / 6,866** once endpoints
  resolve on structure rather than on spelling. Same authority, different extraction — drugref reads MED-RT's
  class-level rules, DrugCentral carries NDF-RT's drug-level assertions. **That 91%-new figure is what justifies
  a slice.** Two costs stand: the only published dump is dated **2023-11-01** (a floor that does not refresh),
  and it does **not** close the QT gap ([issue 93](https://github.com/cairn-ehr/drugref/issues/93) — number
  written without a `#` here, per the standing hygiene rule, because it sits next to "close") — it names
  high- and moderate-risk `... QT Prolonging Agents` classes in 2 rows and defines them nowhere (`pharma_class`
  contains `QT` zero times). **The exact class strings were not recorded verbatim and the two files that quoted
  them disagreed on token order; re-read them off the dump before using either — PROJECT-NOTES § "The 5c.3
  source evaluation".** **⇒ Read and transcribed 2026-08-23: `High Risk QT Prolonging Agents` and
  `Moderate Risk QT Prolonging Agents` — and all three QT rows are `ddi_ref_id = 3`, so they already sit
  outside the half rule 6 permits.**
- **⇒ THIS EVALUATION IS NOW DISCHARGED: the ingest shipped as `db/049` on 2026-08-23**, after a re-measurement
  round rebuilt the figures above from the dump. Read them off "The DrugCentral ddi ingest" below rather than
  from this bullet — the cascade that resolves endpoints on structure rather than spelling moved the pair count
  from 6,941 to **7,501** and the new-pair count from 6,337 to **6,866**, and the two figures in this bullet
  that did not reproduce are corrected where they are quoted, not here.

**2026-08-16 source follow-up:** the
[FDA interaction and toxicity source spike](superpowers/specs/2026-08-16-drugref-fda-interaction-and-toxicity-source-spike.md)
found the potency source this design was missing. FDA's public-domain CYP/transporter
table defines strong/moderate/weak roles and should land as `FDA-CYP` PK classes
**before** SPL mining; it is classification membership, never a licence to manufacture
DDI pairs. The same spike clears DICTrank, DIRIL's narrow FDA-authored projection and
DILIrank for a later **non-firing toxicity evidence projection**, and rejects
DrugCentral `omop_relationship` for bundling because its live rows cannot distinguish
pre-2012 OMOP content from later label curation. This does not change the immediate
order: DrugCentral's clean `ddi_ref_id = 2` subset was the next cheap content slice, and **it shipped on
2026-08-23 as `db/049`** — see "The DrugCentral ddi ingest" below.

##### 5c.4 — signing ✅ DONE
Spec: [slice-5c.4 signing](superpowers/specs/2026-08-09-drugref-slice-5c4-signing-design.md); published record:
[signing the curated overlay](https://docs.drugref.org/decisions/signing-the-curated-overlay/). **`db/030`**: six
tables (`signing_key`, `signing_key_status_kind`, `assertion_signature`, `signature_target_kind`,
`release_manifest`, `release_manifest_entry`), `forbid_any_rewrite`, and a trailing `signature_status` column
appended to both 5c.1 read views by `CREATE OR REPLACE`. **Two layers**: curator-held Ed25519 keys signing one
row's canonical payload, and an institutional key signing a per-release **content manifest** that enumerates
every live curated assertion — so verification is bidirectional and catches **omission** (`dropped`) as well as
`added` and `altered`. **Revocation is data, not branches**: `rotated`/`retired` are time-scoped (prior
signatures survive), `compromised` is blanket. `cli.py` was split first (508 → 347 lines) into `cli.py` +
`cli_chain.py`, then `cli_signing.py` + `cli_signing_release.py`. Suite **969 → 1260 → 1297**, the last step being
the **five-reviewer round on PR [#84](https://github.com/cairn-ehr/drugref/pull/84)** (merged 2026-08-10), which found
four defects four earlier rounds had not — two of them *measured*: deleting the release layer's Ed25519 check outright
left the suite green, and `drugref keys revoke --status active` undid a `compromised` revocation. `db/030`'s payload
format and every committed vector came through it **unchanged**. Full account: PROJECT-NOTES § "Slice 5c.4", which
leads with that round. **`db/030` is MERGED and therefore FROZEN** — corrections need a new `db/NNN`. That wave also
pushed `signing.py` to 582 lines and `release_verification.py` to 532, breaching rule 4 and lodged as
[#89](https://github.com/cairn-ehr/drugref/issues/89) rather than split inside a security-fix diff.

**Measured on a fresh `drugref_5c4`** built from the same real releases (2026-08-10, chain wall-clock **132.96 s**,
per-leg breakdown recorded for [#81](https://github.com/cairn-ehr/drugref/issues/81)): **every count that must not
move held exactly** — `ddi_candidate_pair` **21,664** · `substance_moiety` **19,438** · `open_question` **21,842** ·
`gap_uncurated_interaction_rule` **595** · `gap_uncurated_condition_contradiction` **168**. This slice adds no
projection and no gap kind, so none of them had licence to move. The filtered `curated_ddi_pair` hot path runs at
**~1.4 ms** with the new signature join executing against a populated, signed overlay (~1.3 ms empty), against
5c.1's recorded 2.5 ms — no regression. Full account: PROJECT-NOTES § "Slice 5c.4".

**What it deliberately does not do**: close [issue 2](https://github.com/cairn-ehr/drugref/issues/2) (a superuser
can still drop the append-only triggers), gate any read on a signature, define an enrolment protocol or trust root
beyond "an operator registered it", or interpret N-of-M counter-signatures.

**Separately: #52** (a projection defect — the row carries no `concept_ui`), **#55** (a read-path split on the
projection tier), **#67** (salt↔base strength equivalence: a factor per `(salt, base)` pair, a different data shape
entirely, and blocked on there being an authoritative source at all).

##### 5c.5 — pregnancy & lactation populations 🔬 SPIKED, NOT DESIGNED (2026-08-16)
Recorded here one session late: PR [#127](https://github.com/cairn-ehr/drugref/pull/127) landed a measured
source spike ([design](superpowers/specs/2026-08-16-drugref-pregnancy-and-lactation-source-spike.md) ·
[results](superpowers/specs/2026-08-16-drugref-pregnancy-and-lactation-source-spike-results.md)) that updated
none of these three documents. Full account: PROJECT-NOTES § "Two further source spikes".

- **MED-RT is the candidate floor** and drugref already holds it: 549 direct pregnancy rules (`D011247`), 66
  lactation (`D007774`), plus five smaller trimester/ectopic conditions.
- **Three new sources measured and marked "design next", all NON-FIRING**: LactMed (1,940 evidence records,
  1,702 moieties resolved, **1,679 outside the MED-RT lactation floor** — so the floor is thin, and that is
  the case for the slice), AEMPS CIMA (20,422 authorised Spanish products, segmented SmPCs, change API), ANSM
  BDPM (15,857 French specialties, server-rendered RCP with stable 4.3/4.6 anchors).
- **"Design next" does NOT approve normalization or any write to `curated_condition`**, and **a clinician
  review is a gate that has not happened** — the results doc ships a 23-row worklist asking a human to verify
  extraction boundaries, product scope, and whether normalization would be unsafe.
- **The sources have unlike grains** (active-substance review vs Spanish product section vs French product
  section) **and must not be collapsed into one moiety recommendation.** The next round designs a rebuildable
  non-firing projection plus an open-question bridge; clinician-signed promotion stays separate.

**DDInter is removed from the source ladder, not deferred.** It is **CC BY-NC-SA** — non-commercial, therefore not
AGPL-3.0-compatible and not bundleable under rule 6. The old wording ("DDInter *if its licence confirms*") predated the
check; the check has been done and the answer is no. It may only ever attach as a node-local, separately-licensed
plug-in, like every other encumbered source.

### Slice 6 — HTTP public API
The co-equal-consumer interface (any EHR/pharmacy/app; Cairn on the same footing). Deferred until there is data worth serving;
co-located Cairn reaches the schema directly meanwhile.

### Slice 6r — Human reviewer application ✅ INTERNAL GUI COMPLETE

The internal curation surface: inspect candidate assertions and gaps, attach evidence and Markdown annotations, record
append-only clinical revisions, and sign the exact row reviewed. Canonical design:
[reviewer GUI foundation](superpowers/specs/2026-08-17-drugref-reviewer-gui-foundation-design.md).

The 2026-08-17 foundation adds `reviewer-app/`: Tauri 2 + plain Svelte/TypeScript, a Rust IPC boundary, a login/profile
presentation and a searchable master-detail queue over one fixture shared by native and browser modes. The fixture uses
representative live gap-view rows and dated totals (**593** interaction rules, **168** condition contradictions, **255**
expanded curated DDI pairs); these are interface evidence, not constants. Rust rejects malformed fingerprints, incomplete
targets and duplicate stable target keys. All clinical-write, annotation and signing controls are visibly disabled. **No
migration and no database connection shipped.** Frontend audit: 0 advisories; the measured release macOS app bundle is
**8.3 MB** with an **8.0 MB** executable, before code signing or installer packaging.

The production boundary is Tauri client → authenticated Rust review service → PostgreSQL, never a shared database credential
inside the client. Private Ed25519 keys stay encrypted on the device; the service stores the public key in the existing
`signing_key` registry, independently re-derives signed payloads and writes through the existing append/supersede discipline.

The 2026-08-17 account round adds [`db/044`](superpowers/specs/2026-08-17-drugref-reviewer-user-management-design.md),
`reviewer-domain/` and `reviewer-service/`. The app blocks before workspace load when no administrator is registered,
creates the first administrator under a PostgreSQL advisory transaction lock, keeps bearer tokens in the native core,
and gives administrators a live list/create-user GUI. Accounts are stable; profile, password and key-enrolment corrections
are append-only; sessions store only token digests and insert-only revocations. The clinical queue is still explicitly
fixture-backed and read-only in that round.

The subsequent [live-queue round](superpowers/specs/2026-08-17-drugref-reviewer-live-queue-design.md) replaces the native
fixture command with authenticated `GET /v1/review-queue`: bounded pagination, literal search, database-derived kind/source/
relationship filters, stable natural-key targets and projection provenance from `ingest_run`. One materialised union of the
two gap views supplies page rows, current totals and filter vocabularies from one statement snapshot. The browser-only Vite
surface retains representative data solely for visual development and is never a native failure fallback. Clinical controls
remain disabled.

The [annotation/evidence round](superpowers/specs/2026-08-17-drugref-reviewer-annotations-design.md) adds `db/045` and an
authenticated working-record path. Immutable Markdown notes and citation-only DOI/PMID/PMCID/NCT/SPL/URL references are
attributed to stable reviewer identities and resolved through the open-question registry's canonical target key. They carry
no question state, evidence verdict, grade, clinical ruling or signature; closing a gap retains the cited question and its
research history. Administration still needs profile correction, disable/enable, password rotation and all-session
revocation.

The [curated-revision round](superpowers/specs/2026-08-18-drugref-reviewer-curated-revisions-design.md) exposes the existing
`curated_interaction` and `curated_condition` append-then-supersede model through authenticated service and Tauri boundaries.
The service owns question identity, reviewer attribution and release provenance; an expected live revision identifier plus a
transaction-scoped target lock refuses stale concurrent forms. The GUI provides kind-specific decision vocabularies, a
two-step immutable-row preview and complete revision history. New rows remain explicitly unsigned until the separate action.

The [detached-signing round](superpowers/specs/2026-08-18-drugref-reviewer-signing-design.md) keeps each Ed25519 private key in
an encrypted native Stronghold snapshot, enrols only the public key, previews the exact frozen canonical digest, and requires a
second explicit local unlock before the service independently rebuilds and verifies the signature. A database-derived pending
view resumes unsigned current revisions after the clinical queue refreshes or the app restarts. `db/046` changes no table
shape; it corrects the signing registry's catalog comment to state this authenticated enrolment trust root.

The [key-replacement follow-up](superpowers/specs/2026-08-18-drugref-reviewer-key-replacement-design.md) handles a lost
device-vault passphrase without weakening those boundaries. The service appends a time-scoped `rotated` correction and
withdrawn enrolment, then native code deletes only the fixed device files. Unused keys report zero preserved signatures;
used keys retain their earlier valid signatures. Hard deletion of the append-only registry remains forbidden.

The [account-administration round](superpowers/specs/2026-08-18-drugref-reviewer-account-administration-design.md) completes
the `db/044` account lifecycle without a migration: stale-write-guarded append-only profile corrections, disable/enable,
Argon2id password rotation and auditable all-session revocation. Administration transactions recheck authority under one
lock, refuse to disable or demote the last active administrator, and bind session creation to the credential revision that
was actually verified so a concurrent rotation cannot admit the predecessor password.

The [key-trust round](superpowers/specs/2026-08-19-drugref-reviewer-key-trust-design.md) adds administrator inspection and
append-only retirement/compromise over every public registry key. `db/047` floors only the status-rule vocabulary against
UPDATE/DELETE, closing issue #85 without freezing the deliberately mutable target-context catalog. Pending signatures now
means zero registry-unobjected signatures: compromise returns affected live revisions as explicit counter-sign tasks, one
independent unobjected signature resolves them, and clinical rows never disappear. Owned-device replacement remains the
narrow local cleanup path and preserves an administrator-recorded compromise label.

The [GUI finalization round](superpowers/specs/2026-08-20-drugref-reviewer-gui-finalization-design.md) adds `db/048` and
closes issue #86's compatibility gap. The published registry status now distinguishes unknown from revoked keys, the
shared
Rust and TypeScript contracts admit exactly four values, and the GUI renders readable objected-state warnings. `signed`
still wins when any independent signature is unobjected, and clinical rows remain served. The unused disabled Evidence
library navigation item is removed; evidence remains attached to each target's immutable working history.

The first post-key-trust documentation round replaces the public reviewer/developer placeholders with an operational reviewer
manual and component-aware developer guide, adds the native/service/PostgreSQL trust boundary to the architecture page,
updates the public roadmap and signing decision through `db/047`, and brings the repository/component READMEs and
drugref.org landing page up to the same state. The website continues to describe the general public HTTP API and release
packaging as future work; a working internal reviewer is not presented as a consumer API or downloadable release.

### The DrugCentral re-measurement ✅ DONE (2026-08-23) — no migration, no ingest
Results, regenerated by the tool rather than transcribed:
[drugcentral-ddi re-measurement](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-remeasurement-results.md).
Full account: PROJECT-NOTES § "The DrugCentral re-measurement".

**The prerequisite [#101](https://github.com/cairn-ehr/drugref/issues/101) could not be designed without.** Every
figure on that issue rested on one unrepeated 2026-08-13 run over a 1.4 GB dump that was then deleted, and this
file had quoted several of them forward. The dump was re-fetched (SHA-256 recorded), the measurement now lives in
`tools/` as six modules with **94 tests**, and it re-runs in ~25 s from a cached extract.
(Suite total: PROJECT-NOTES § "How to run / test" is its one home.)

**The rule-6 determination reproduces exactly**, re-read from the `reference` table: `ddi_ref_id = 2` is the VHA's
NDF-RT (7,571 rows, clean); `1` is Stockley's (a copyrighted book) and `3` is Lexicomp (commercial), both out.
Licence re-confirmed at source as CC BY-SA 4.0. The staleness cost stands — `drugcentral.org/download` still
offers only 11/01/2023.

**The finding that changes the slice: resolve endpoints on STRUCTURE, not on spelling.** DrugCentral resolves its
own free text to a `struct_id` for 918 of 924 NDF-RT endpoint names, and `structures` carries an InChIKey and a
CAS number drugref already holds as `identity_claim` rows. A `display_name → inchikey → cas` cascade moves
resolution from 857/924 names to **914/924**, unresolvable rows from 598 to **37**, and **new pairs from 6,337 to
6,866** — with no hand-maintained synonym list, which is what #101 had budgeted for. **Seven published figures
were wrong** (the "8 MED-RT class names" is 4 MeSH ones; 102 → 106; 7,000 → 6,991; `MAOIs or RIMAs` is not an
ENDPOINT — **and that correction, as first published, said "not in the table" and is itself WRONG**: the string
sits on 10 `ddi.source_id` rows, all of them `ddi_ref_id = 1`, and the ingest round below is what caught it; the
QT strings; the QT rows all sit in the licence-excluded subset; the provenance split is 905/13/6, not 905/17/2),
all corrected in place.

**The branch's own code review then hardened the instrument**, and the finding there is the same one a layer
down: the parser refuses a malformed row loudly, and the TSV cache underneath it quietly undid that. A crashed
extract left a cache the next run measured; nothing tied the cache to the dump; a projection the dump no longer
declared was written blank; the registry lookups had no `ORDER BY` while **14 InChIKeys and 29 CAS numbers are
claimed by more than one moiety**; and the rule-6 verdict was hard-coded a second time in the renderer. All
fixed, results regenerated, and five figures this file had called re-derivable are now actually computed.
Full account: PROJECT-NOTES § "What the code review of this branch changed".

**⇒ THE INGEST ITSELF HAS SINCE SHIPPED — `db/049`, immediately below — AND THIS PARAGRAPH NAMED THE WRONG
CONSTRAINT WHILE IT STOOD.** Admitting `source = 'DRUGCENTRAL'` does need two CHECKs widened *and* an explicit
`ids._SOURCE_CANONICAL` entry in the same migration — the silent-rebuild failure mode `ids.py` warns about by
name — but the two are **`ingest_run_source` and `ingest_run_writer`**, not `class_contraindication_source`.
DrugCentral asserts unordered moiety pairs with a severity, which is neither a class rule nor a directional
moiety rule, so it writes no row into `class_contraindication`; `db/049` leaves that CHECK at
`('MED-RT','ONCHIGH')` and a test pins that it is untouched. PROJECT-NOTES § "The DrugCentral ddi ingest" carries
the correction in full.

### The DrugCentral ddi ingest ✅ DONE (2026-08-23) — `db/049` + `db/050`, measured on the real release
Design: [drugcentral-ddi-ingest](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-design.md).
Measurement: [drugcentral-ddi-ingest-measurement](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-measurement.md).
Full account and every figure: PROJECT-NOTES § "The DrugCentral ddi ingest" — **not restated here.**
Published decision: [the candidate tier carries an upstream
severity](https://docs.drugref.org/decisions/upstream-severity-is-data/).

**drugref's THIRD interaction candidate source, and the first that grades what it asserts.** `db/049` admits
`source = 'DRUGCENTRAL'`, seeds `ddi_source_severity` (the upstream band → drugref grade mapping, **as data**),
stores every bundleable row in `drugcentral_ddi_assertion`, collapses orientation in `drugcentral_ddi_pair`, and
adds `exact_ddi_pair` — **the read path exact drug–drug pairs have never had**, since `ddi_candidate_pair`
expands class rules only and nothing else read `moiety_contraindication` at all. Plus the **eighteenth** question
kind, `unresolved_ddi_endpoint`.

**Two measurements taken for the design changed it.** The `description` column carries **no clinical content** —
all 7,571 rows match `NAME1/NAME2 [VA Drug Interaction]` — so what this source adds over a bare pair list is one
severity band and nothing else, and the whole design budget went there. And **the source asserts an UNORDERED
pair**: 33 pairs are published in both orders, 4 of them with disagreeing severities, which is what rules out
widening the directional `moiety_contraindication` (storing it there would have fabricated an orientation on
7,534 rows).

**`ddi_candidate_pair` is deliberately untouched** — db/034 measured ~3.6× with the new grain EMPTY when that
view was widened to serve a second grain, and its columns are class-expansion-shaped. (Read db/034 before
reusing that as evidence: the cost came from widening `ci_class_subtree`'s RECURSIVE SEED, and db/034 KEPT the
arm it had. db/049's comment read as though a union arm had been measured and rejected; `db/050` records the
correction, db/049 being immutable.) `exact_ddi_pair` is additive: no existing query
changes, and the plan was measured byte-identical before and after. The costs, stated: the release is pinned to
**11/01/2023** with no successor offered, and a consumer wanting everything must now read two views.

**Rule 6 is enforced twice.** `BUNDLEABLE_REF_IDS = {2}` (VHA NDF-RT, a US federal work) has one home in the
code, *and* the orchestrator reads the dump's own `reference` row for every admitted id and **aborts** unless
its authors and title still match — because `2` is a surrogate key and a re-published dump is free to renumber
it. `NOTICE` carries the determination, including why the committed fixture's `source_id` field is not redacted
on the excluded rows.

**Filed rather than silently widened:** [#148](https://github.com/cairn-ehr/drugref/issues/148) — 635 of the
7,501 pairs are already reachable through MED-RT's class expansion and nothing compares them, which is
[#97](https://github.com/cairn-ehr/drugref/issues/97)/[#106](https://github.com/cairn-ehr/drugref/issues/106)'s
question one tier down; and [#149](https://github.com/cairn-ehr/drugref/issues/149), a pre-existing gap in the
per-source-clear contract test found while registering this round's own writer.

**⇒ THEN `db/050`, the PR-150 review round.** Five specialist agents; 1 critical, 9 important, all applied.
**The critical one: a dump this code cannot read wiped the projection and exited 0** — renaming ONE column took
the fixture from 4 rows to 0 and blamed rule 6 for it in the summary line. Every guard passed vacuously,
because **every reconciliation in the slice proved the orchestrator self-consistent and none proved it
published anything** — the diagnosis that also explains the blank `upstream_key`, the blank endpoint and the
empty-registry case. `db/050` adds floor checks before `open_run`, the constraints db/049 stated in comments
and enforced nowhere (`upstream_key <> ''`, a `blank_endpoint` route, `ddi_source_severity.source`) and a
route-aware gap view, the question text having asserted one route's story about three under an immortal
`question_uuid`. In code: a disjoint `Outcome` enum, `first_wins` folding in the registry's own key space, the
REPEATABLE READ bump removed, and an autocommit refusal. Mutation testing found 17 survivors concentrated in the
orchestrator's tail; all killed. **Full account: PROJECT-NOTES § "The PR-150 review round".** Filed rather than
fixed: [#152](https://github.com/cairn-ehr/drugref/issues/152) (synthesise the fixture's excluded-reference rows
— a licensing-posture call) and [#153](https://github.com/cairn-ehr/drugref/issues/153) (concurrent pytest
sessions wipe each other's schema, pre-existing).

### Slice 7 — Cairn `inn_code` wiring (Tier-A consumer)
Fill the deliberately-nullable `inn_code` slot in Cairn's medication surface: autocomplete, coding a previously-uncoded
substance, DDI advisory — **overlay enrichment, never a wire change** on the Cairn side.

### Slice 8 — Local tier (Australia first)
Country-specific packaging/pricing. **Corrected claim (was: "PBS + TGA ARTG, both CC BY, redistributable" — refuted by a
live-source check, spec §1):** neither is confirmed open. The PBS Schedule/API data mart carries no CC BY statement and
`pbs.gov.au`'s copyright page reads all-rights-reserved (CC BY is verified only for PBS's separate *statistical* datasets on
data.gov.au); TGA ARTG's copyright page is explicitly non-commercial. ATC (WHO, NC+ND) and AMT/SNOMED CT-AU (NCTS-licensed)
were never candidates for bundling. The posture for all of them is the one **CLAUDE.md rule 6 already states**: drugref ships
**AGPL-3.0 ingest code and schema only**, never a release; a node operator supplies their own under whatever terms bind them.
Redistribution is blocked pending written confirmation — tracked for PBS as
[#25](https://github.com/cairn-ehr/drugref/issues/25).

**One stated exception, so the claim above stays literally true:** `tests/fixtures/pbs_items_subset.csv` commits ~a dozen real
PBS rows as a test input, extracted by `make_pbs_subset.py` so the suite runs against the real upstream shape instead of a
guess at it. Argued as fair-dealing scale, not a dataset; it is in scope for #25 and is the thing that has to go if #25 comes
back negative.

#### Slice 8a — PBS localisation: the local tier's first attachment ✅ DONE
A spike proving the local-tier pattern — name-only bridge, jurisdiction scoping, structural encumbrance quarantine — before
investing further. A minimal Australian PBS product layer (`local_product`, `local_product_moiety`,
`local_unmatched_ingredient` — `db/009`, a **rebuildable projection**, deliberately outside slice 1's append-only floor since
a de-listed PBS item must be able to disappear) bridged to the global moiety spine **by name alone**, the only licence-clean
join: PBS carries no UNII, CAS or InChIKey. Design: [slice-8a
spec](superpowers/specs/2026-07-25-drugref-slice-8a-pbs-localisation-design.md).

Measured against the real July-2026 release (14,840 items): the bridge sat at **85.5%** against a **92.4%** ceiling (all UNII
substance names), and slice 8a's reading — **the moiety gate, not the bridge, is the binding constraint**
([#26](https://github.com/cairn-ehr/drugref/issues/26)) — proved right: after the identity-spine fix round the bridge reaches
**13,719 = 92.4%, exactly that ceiling**, with unmatched components down 3,140 → 347. It took *two* fixes to show it, since
the gate change alone moved nothing until the bridge stopped indexing `INN` claims. The salt-strip heuristic (an admitted
slice-3 stand-in) is now **5 bridge rows, 0.03%** — reported as near-worthless rather than left to quietly imply otherwise
(rule 5); slice 3's GSRS salt relationships are the real fix. The residual is otherwise AU/INN-vs-USAN spelling divergence and
non-drugs the moiety gate correctly excludes. **347** tests after the PR-review fix round (2 findings deferred to #29 / #30).
No `NOTICE` change — the ingest path redistributes nothing; the test fixture noted above is the sole committed PBS data and is
tracked under #25.

Remaining slice-8 scope (not built): pricing (AEMP/DPMQ/premiums/fees), restriction texts/criteria, TGA ARTG, the composition
tree's salt/clinical-drug levels underneath the bridge, and the same shape applied to other jurisdictions.

## Cross-cutting hardening (not a single slice)

- **The review of PR [#78](https://github.com/cairn-ehr/drugref/pull/78) ✅ DONE** (2026-08-08, no migration) —
  twelve findings, every one in the state files themselves. **Two substantive**: the `~739`-in-`COMMENT ON TABLE`
  fix was the one 5c.1 fix no test killed (now `tests/test_curated_interaction_comment.py` plus its guard), and
  `pair_count` had been recorded "verified" on a reading that is **identical on the broken view** —
  `pg_get_viewdef`, run on both databases, is what actually settles it. The rest were prose defects with
  consequences a later session pays: the whole-branch review's findings existed only in a commit message, §5c's
  execution order was nowhere in ROADMAP, and the chain's uncontrolled 127.5 s → 144 s delta had been written off
  as a warm cache — now [#81](https://github.com/cairn-ehr/drugref/issues/81). Suite **956 → 958**, two new
  standing rules. Full account: PROJECT-NOTES § "Standing rules" and § "Slice 5c.1".
- **The gates-that-do-not-fire round ✅ DONE** (issues 74, 66, 76 — 2026-08-08, no migration). Three checks that
  existed and never fired. **74**: five of the seven single-live index tests passed a `UNIQUE` mutation that would
  forbid every correction the overlay exists to make — measured, not reasoned — and the weakest counted the index
  by name alone; all seven now go through one `assert_live_key_index` fixture, which itself gets a guard file
  mutating the real index inside the test transaction. **66**: there was no lint gate to weaken — no `[tool.ruff]`,
  `ruff` not a project dependency (a pyenv shim answered `uv run ruff`), and **no lint job in CI at all**. Now
  `line-length = 88` with `E`/`F`/`W`, `src/`'s 52 lines reflowed, ruff pinned, a CI `lint` job added, `ruff check .`
  confirmed safe at 0.18 s (because ruff honours `.gitignore` — **not** because of `extend-exclude`, which is
  belt-and-braces; the "used to hang on `downloads/`" claim does not reproduce) and `tests/`' **334** long lines
  carved out as [#79](https://github.com/cairn-ehr/drugref/issues/79). The issue's "~88 every file is written to"
  holds for `src/` only. **79 is CLOSED by the debt round (§ 5c.2c)**: the carve-out was a blanket exemption, so
  `tests/` had no bound at all and the exempted count reached **415**; it is now bounded at **120** in a nested
  `tests/ruff.toml`, which is the second of the two ways that issue offered to close. **76**: `curated_target_unresolved` shipped with no consumer — the second time (db/010's was
  the first) — now read by `curation.unresolved_targets` and printed as `drugref status`'s third block.
  **Its own review then found three gates THIS round added that also did not fire** — an orphan test whose empty
  result was over-determined, a CI step whose `tee` pipeline swallowed pytest's exit code, and a source-text grep
  that the round's own 88-column rule taught SQL to evade. All mutation-verified dead; the seven live-key tables are
  now derived from `pg_trigger.tgargs` rather than three hand-kept lists, and `conftest`'s CI hard-fail branch has a
  test for the first time. Suite **943 → 969**; orphan exit-code channel deferred to
  [#82](https://github.com/cairn-ehr/drugref/issues/82). **`cli.py` is now 508 lines, over the ~500 cap — split it
  before adding another handler.** Full account: PROJECT-NOTES § "The gates-that-do-not-fire round".
- **Identity-spine fix round ✅ DONE** (#27, #17, #26 — post-Plan-B). Made every other slice's coverage number real; every
  defect was invisible to the committed fixtures. `ingest/unii.py` read a **`PT` column the real UNII release does not have**
  (it is `Display Name`), so `row.get("PT") or ""` silently emptied all 168,046 labels — a production run would have
  completed "successfully" over an **entirely unlabelled registry** with a **dead allow-list** and a **dead USAN↔INN
  crosswalk**; `or ""` absorbing a structural mismatch, not a renamed column, is the lesson. Required columns are now
  **declared and checked**. The legacy allow-list
  moved to **UNII keys** (#17) after its flagship entry matched nothing. The membership gate (#26) became **`INN_ID | USAN_ID
  | (RXCUI & drug-like SUBSTANCE_TYPE)`** plus the allow-list — `INN_ID` is a sparse cross-reference, empty for amoxicillin,
  morphine and aspirin. **The asymmetry is the design**: uniform type-filtering would delete heparin, enoxaparin, protamine
  and 346 gene/cell therapies. **Strictly monotone**, pinned by a test; `db/011` records the admitting signal. A fourth defect
  surfaced only on measurement: the gate moved **no** downstream number until the PBS bridge stopped indexing `INN` claims
  (the new moieties have none) and indexed `display_name` instead. Measured: moieties **12,591 → 19,438**, PBS bridge **85.5%
  → 92.4%**, MED-RT classified moieties **2,066 → 3,875**, `ddi_candidate_pair` **6,402 → 21,664**. 412 tests. Residue: #33
  (**"closed by slice 3" WITHDRAWN** — the slice-3 design measured GSRS and refuted the issue's own proposed fix; see the
  slice-3 section above). Spec: [moiety gate redesign](superpowers/specs/2026-07-27-drugref-moiety-gate-redesign.md).
- **Foundation review ✅ DONE** (post-slice-5a, whole-codebase). `db/005` made the correction overlay one-way and re-assertable
  (partial unique index on LIVE claims; supersession set once, same-moiety, strictly forward — closes #4) and constrained
  `ingest_run.source`; `db/006` replaced the comment-enforced CHECK↔CASE coupling with a `ci_axis` table the vocabulary is an
  FK into, put `source` in the contraindication PK, renamed the pair view's columns to their roles and moved the clinical
  contract into `COMMENT ON`. `apply_migrations` gained a **checksum ledger** — migrations are immutable once applied.
  Parser/identity fixes: UNII rows with no identity key refused (they were merging unrelated drugs onto one immortal UUID),
  TSV read with `QUOTE_NONE`, ambiguous MED-RT published codes refused, claim values canonicalised, orchestrators roll back
  and log. **CI added** (PG18 service; the DB-gated majority now fails rather than skips). 220 tests. Remaining then: **#16
  (crashed-ingest visibility + CLI) and #17** — both since closed, #16 by the ingest-operability round below.
- **Open-question registry ✅ DONE** (Plan A of the additive-effect design). `db/007` adds `open_question` (a rebuildable
  projection keyed on a deterministic `question_uuid` external tooling can cite) plus three append-only curated tables —
  `question_state`, `question_source_check`, `question_evidence` — each with a surrogate PK and live-row-only uniqueness, per
  `db/005`. `db/008` adds the three gap views, the `ingest_unmatched_ingredient` table that makes the third possible (the
  ingest previously kept only the COUNT, discarding the RxCUIs), the `source_tier` cost ladder and `question_worklist`,
  ordering by cheapest-unchecked tier. **Four of the six orchestrators** (`run`, `medrt_run`, `mesh_run`, `mesh_rel_run`)
  rebuild the register last thing before commit; `chebi` and `pbs_run` never call it, benignly — no gap kind reads what those
  two write. Gaps are published, not hidden. **Watermark, not closure:** no-evidence-found leaves a question open; only
  `withdrawn` is terminal. **Populated is per axis:** the contraindication gap view joins `db/006`'s `ci_axis`, since a class
  populated on an axis the rule does not expand over still yields no pair. **A closed gap carrying curator work is retired,
  not deleted** (`open_question.is_current`): the curated tables cascade from `open_question` *and* refuse `DELETE`. **Seven
  gap kinds by 5b.2, eleven since Plan C** — measured against the real releases: unclassified_moiety 16,089 ·
  unmatched_ingredient 2,150 · uncurated_additive_effect 381 · unresolved_ci_object 103 · condition_without_indication 97 ·
  unpopulated_contraindication 13 · dead_by_expansion_policy 1 · the other four 0 — **18,834** in total, the figure restated
  below.
- **Descendant expansion ✅ DONE** (Plan B of the additive-effect design; the work #15 asked for). `db/010` makes
  `ddi_candidate_pair` descend the class DAG — **for a contraindication, fewer rows is the harm direction**, and direct-only
  hid 21.9% of `CI_MoA` and **85.2%** of `CI_PE` pairs because MED-RT files membership at the specific node while writing
  rules against the parent. New columns `member_class` and `is_direct`, so `WHERE is_direct` reproduces the old row set
  exactly and a consumer who forgets the filter errs toward recall. Bounded by **`class_expansion_policy`** — a deny-list held
  as data a pharmacist can read, seeded with the 14 CI object classes over the `>20 descendant classes` discovery heuristic
  (**all PE, not one MoA**): 11 denied as abstract organ-system buckets, 3 explicitly allowed. **The deny-list filters the
  rule's object class, never the walk** — `Decreased Coagulation Activity` is a descendant of a denied root and must still
  expand, which is how a rule reaches warfarin, apixaban and aspirin. Plus `ci_axis.expands_descendants` per predicate and
  `gap_unreviewed_expansion_root`, a fourth question kind, so the list cannot rot silently across releases. 384 tests. Residue
  filed as #31 — **closed by the interaction debt round** (`gap_dead_by_expansion_policy`).
- **Plan B review round ✅ DONE** (`db/012`, PR #38). The review of #32 found no defect in the expanded read path, and **five
  gaps between what `db/010`'s comments legislate and its DDL does**: the recursive walk becomes one view
  (**`ci_class_subtree`**) instead of three copies of itself; `gap_unreviewed_expansion_root` joins `ci_axis`, so it stops
  asking whether a class should expand when its predicates cannot; `expansion_policy_unresolved` gains a consumer in
  `medrt_run`, having shipped as a detector nothing read; `class_expansion_policy.source` gains the CHECK every other `source`
  column has; and two `COMMENT ON`s stop overclaiming — `expands_descendants` is a recall-safe *default*, not a gate, and the
  walk is **source-blind** (`class_parent` carries no `source`, so a transitive walk can cross vocabularies — **still latent
  after 5b**). Row set unchanged. 419 tests. Follow-ups: #36, #37 — #35 is closed by the expansion-policy history round below.
- **Plan C — the accumulation model ✅ DONE** (`db/020`–`db/024`; spec §4–§8 / §11 steps 6–7; plan:
  [plan-c](superpowers/plans/2026-08-01-plan-c-accumulation-model.md)). Gated by 5b, which had landed. The model the pairwise
  projection cannot express — **many drugs, one effect that adds up** — plus **groups**, the role-based exception where
  members play different parts and a count is meaningless. Five curated tables, two read views (spec §8's output contract),
  four gap views, **gap kinds 8–11**, `accumulation.py` — the single writer plus the two PURE rules a consumer applies.
  **Ships with an EMPTY curation set** (§11 step 7); curation is step 8. **No new source**, but drugref becomes an authority
  in its own registry (`source = 'DRUGREF'`, all three places extended together). **748 tests.**

**`db/023`–`db/024` are the review round on it**, five findings each measured rather than reasoned: the generic single-live
trigger was **unindexable and therefore quadratic** (2,000 rows 5,773 ms → **42 ms**, linear, via equality predicates +
partial `<table>_live_key` indexes); `gap_uncurated_threshold` cleared on promotions that regraded **nobody**, so it now gates
on unreviewed MEMBERS; `interaction_group_assertion` gained the `applies` ruling column `db/020` gave the other two
tables but not it, so a group can be **retired as a whole**; `interaction_group_member_moiety`'s deliberate
non-uniqueness is now stated, not merely true. Costliest:
`gap_ineffective_contribution` named `class_subtree` twice in a **correlated** subquery, re-running the 22,754-row closure
**per curated row** — **59 s** for 400 promotions, **465 ms** once `db/024` hoists the walk out. A synthetic probe missed it:
its fixture had no DAG edges. **Measure recursion against a real DAG or do not measure it.**

**Measured against the real releases**, whole chain 110 s, **every prior figure reproduced exactly** (19,438 ·
3,634/3,961/18,639 · 5,963/8,507 · 9,471 · 1,442 · 103 · 14,674/154 · 168/422 · the seven gap counts), `ddi_candidate_pair`
**unchanged at 21,664**, filtered lookup **3.1 ms** — the regression this slice most had to avoid. New: `class_subtree`
**22,754** · `gap_uncurated_additive_effect` **381** of 1,873 PE classes · the other three gap views and both read views **0**
· **18,834** questions, 11 kinds.

**Three things the design document was wrong about, each found by test or measurement.** (1) Spec §5.0's partial unique index
for single-live **cannot work** on a table whose corrections preserve the natural key, so the deferred constraint trigger
`db/007` invented for `question_state` is generalised — published as `decisions/correcting-a-curated-assertion.md`. (2)
**Nothing could be RETIRED**: supersession must point at a later row with the same key, so
`interaction_group_member.satisfies_role` and `additive_effect.accumulates` make §5.3's "superseding the last member of a role
removes the role" implementable at all. (3) Generalising `ci_class_subtree` instead of adding a second walk was measured and
**rejected**: identical rows, **5× slower**, because root-scoping is what makes it cheap. **Not done here, deliberately:** any
curation, any `DRUGREF`-minted class — §6 says mint only where the release says nothing. **#35 is closed** by the round below,
which moves `class_expansion_policy` onto this same append-only shape — the fifth table it now covers.
- **Ingest-operability round ✅ DONE** (#16, #47; `db/025`–`db/026`; spec:
  [ingest-operability](superpowers/specs/2026-08-02-drugref-ingest-operability-design.md)). A debt round: no new source, no
  clinical claim. Six orchestrators wrote `ingest_run` **inside the transaction that did the work**, so a crash rolled the
  provenance away and `finished_at`'s "started, never finished" state could never be observed; `pyproject.toml` had no
  `[project.scripts]`, so an ingest ran only from a test or a REPL. **`provenance.py`** is now the only writer of a run
  record, pinned by two contract tests that grep the tree: `open_run` commits in its own transaction — **the commit *is* the
  feature** — while `finish_run` deliberately does not, so an orchestrator takes two transactions on one connection.
  **`db/025`** adds `ingest_run.writer` (NOT NULL, no DEFAULT), since source `MED-RT` has **two** writers whose checksums
  legitimately differ (#39 one layer up), plus `loaded_release` (per `source, writer`) and `ingest_run_incomplete`, **which
  could only ever have been empty before this round**; historical rows carry `'unattributed'`. **A `drugref` console script**:
  `migrate`, `status`, one `ingest` subcommand per orchestrator, and `ingest chain`, which runs **the steps whose
  `--<source>-release` flag was given** in a fixed order, resolves inputs by documented globs (**zero matches and several
  matches are both errors**, resolved before any step runs). The order is a constant, but **only UNII-first is a data
  dependency** — every other feed joins to the `identity_claim` rows (or `display_name`) UNII registers; `medrt` before
  `mesh-relations` is convention only — the PR review round corrected both the comment claiming a read that does not
  happen and the test that had asserted the pair as a dependency. `chebi.py`, the orchestrator the foundation review
  missed, gained the try/rollback/logging the other five have. **788 tests.**

**Measuring #47 turned up two findings, both about `db/018`'s own justifications** for widening `gap_unmatched_ingredient`'s
tie-break, both false by the time #47 arrived. (1) "`classification` wins alphabetically" — but `class_contraindication`,
**the value the issue itself proposes**, sorts *before* it, so `db/026` ships **`contraindication_class`**. (2) "and by being
the bucket with a `name`" — measured on the real releases, **0 of 4,389 rows carry a name in any bucket** while **1,430 RxCUIs
sit in more than one**: live on real data and simply unobservable. The view now prefers a named row explicitly, pinned on
controlled input and verified by mutation since the release cannot exercise that branch.

**Measured end to end through the new chain** (fresh `drugref_ops`, **110.37 s**, no workarounds): the new
`contraindication_class` bucket **99** · `classification`/`contraindication`/`indication` **2,137/826/1,426** ·
`gap_unmatched_ingredient` **2,150**, `open_question` **18,834**, `ddi_candidate_pair` **21,664** — all unchanged ·
`loaded_release` **4 rows**, both MED-RT writers visible · `ingest_run_incomplete` **0**. **The plan text carried three
defects, each caught by an implementer measuring rather than reading** (the writer count, an error-message assertion
contradicting its own test, a chain glob naming `UNII_Names_*.txt`) — the final whole-branch review found the plan STILL
asserting all three, plus a wheel-installed `drugref migrate` applying nothing while printing "migrations applied", no `.sql`
shipped and `Path.glob` on a missing directory silent. **A plan is a claim about the code; correcting it only in the code
leaves it wrong.**
- **The expansion-policy history round ✅ DONE** (#35; `db/027`; spec: [expansion-policy
  history](superpowers/specs/2026-08-03-drugref-expansion-policy-history-design.md)). `class_expansion_policy` — the curator
  table that gates recall by deciding whether a class-level contraindication expands over the DAG — was the last curated table
  still edited in place: a revised deny/allow decision overwrote its own rationale, and a single `UPDATE` could remove
  thousands of candidate pairs with no audit row. It now sits on Plan C's append-only overlay floor, **the mechanism's fifth
  table**: surrogate `policy_id` identity PK (the natural key `(source, source_code)` deliberately stops being unique, since a
  correction preserves it), one-way `superseded_by`, both `db/020` trigger functions reused unchanged over a partial
  `class_expansion_policy_live_key` index. A third `decision` value, **`withdrawn`**, is new — supersession alone can never
  RETIRE a judgement, and absent means *unreviewed*, which expands **and** raises a question, so `withdrawn` cannot be folded
  into `allow`. All **four** readers (`ddi_candidate_pair`, `gap_unreviewed_expansion_root`, `gap_dead_by_expansion_policy`,
  `expansion_policy_unresolved`) now route through one view, `class_expansion_policy_current`. `interactions.py` gained
  `record_expansion_decision`, `withdraw_expansion_decision`, `NoLiveDecisionError`.

**Measured** on a fresh `drugref_policy`, built from the real releases, chain wall-clock **103.28 s**: `ddi_candidate_pair`
**21,664** — unchanged, the one figure that had to hold · `gap_dead_by_expansion_policy` **1** ·
`gap_unreviewed_expansion_root` **0** · `open_question` **18,834** · `class_expansion_policy` **14** rows, **14** binding ·
`expansion_policy_unresolved` **0**. Hot-path filtered `ddi_candidate_pair` lookup **2.876 ms** against the 3.1 ms this
project has recorded — same order, no regression. **810 tests** (788 at branch start).

Filed, deliberately not fixed in this round: [#59](https://github.com/cairn-ehr/drugref/issues/59) — the insert-then-supersede
rule lives in **three** places (`accumulation._supersede`, `questions.set_state` since `db/007`,
`interactions.record_expansion_decision`), so the "third owner" trigger has already fired; deferred because this round chose
not to widen its blast radius, not because nothing triggered it ·
[#60](https://github.com/cairn-ehr/drugref/issues/60) — `drugref ingest chain` cannot
run all four sources together, since `mesh` and `mesh-relations` share `desc*.gz`/`supp*.gz` but take different release tags
and `check_release_agreement` refuses; use the four `ingest <source>` subcommands in `STEPS` order meanwhile, as this round's
own measurement did · [#61](https://github.com/cairn-ehr/drugref/issues/61) — `DELETE` and re-keying are both refused now, so
an operator acting on `medrt_run`'s stale-decision warning has no supported surface; a `drugref policy` subcommand belongs
with 5c's curation tooling · [#63](https://github.com/cairn-ehr/drugref/issues/63) — this file and HANDOVER are rewritten
wholesale each round, so their git history answers nothing (raised by the #62 review).
- **The policy-surface debt round ✅ DONE** (#59, #60, #61, #63; spec: [policy-surface debt
  round](superpowers/specs/2026-08-05-drugref-policy-surface-debt-round-design.md)). The four follow-ups the
  expansion-policy history round filed against itself, cleared together: **no new SQL, no new ingest logic.**
  **#59** — the insert-then-supersede rule (`accumulation._supersede`, `questions.set_state`,
  `interactions.record_expansion_decision`) becomes one primitive, `overlay.supersede`, all three owners on it with
  the SQL semantically unchanged (verified natural-key-by-natural-key against `db/007`, `db/020`, `db/027`). **#60**
  — `IngestStep` gains `secondary`, the inputs a step *reads but does not date*; `check_release_agreement` skips
  them, so `mesh-relations` can read `desc*.gz`/`supp*.gz` without claiming a release for them, and the documented
  four-source `ingest chain` invocation stops refusing itself. **#61** — `drugref policy record|withdraw|show`, in a
  new `cli_policy.py` (extracted from `cli.py` to hold CLAUDE.md's ~500-line rule; `cli.py` 429, `cli_policy.py`
  137), gives an operator the surface `medrt_run`'s "re-key or withdraw" warning has named since db/027 but had
  nothing to point at; the CLI refuses `--decision withdrawn` (only `policy withdraw` may retract a decision — it
  alone carries `NoLiveDecisionError` and the retracted class's name forward), while the library function underneath
  still accepts the value on purpose. **#63** — the HANDOVER/PROJECT-NOTES split (Task 5) is this round's own
  documentation discipline, not new work here.

  **Measured on a fresh `drugref_policy_cli`, through the EXACT invocation #60 says is refused** — it ran (before
  this round it failed pre-flight with *"desc2026.gz is read by both mesh and mesh-relations, which were given
  different release tags"*): wall-clock **113.99 s** (94.78 s user + 2.57 s system, 85% CPU). Every published figure
  reproduced exactly, as it had to — this round changed no SQL and no ingest logic: `ddi_candidate_pair` **21,664** ·
  `open_question` **18,834** · `gap_dead_by_expansion_policy` **1** · `gap_unreviewed_expansion_root` **0** ·
  `expansion_policy_unresolved` **0** · `class_expansion_policy` **14** rows, **14** binding · `loaded_release` **4**
  · `ingest_run_incomplete` **0**. `drugref policy` exercised against that same database: `withdraw` on
  `N0000009020` moved `gap_unreviewed_expansion_root` from 0 to 1, and `policy show` reported the two-row history
  (the seeded `deny`, then the new `withdrawn`) with the live row marked. **844 tests** at the end of the branch
  (810 at branch start; 831 at this measurement, +4 whole-branch review, +9 PR-#64 review round),
  `ruff check src tests` and `mkdocs build --strict` clean.

  **The PR-#64 review round took back one thing this round had introduced.** `except psycopg.errors.CheckViolation`
  had been added to `cli.main`'s `try` — which wraps *every* handler, ingest included — so an ingest defect printed
  one context-free line and exited 2, this CLI's operator-error code. It moved to `cli_policy._write`, where the
  failing value demonstrably came off the command line, and got better there: the message now quotes
  `pg_get_constraintdef`, so an operator learns what the CHECK accepts by reading the CHECK rather than from a
  second copy in Python. Also fixed: `policy show` asserted flatly that an unruled class raises a question, 25 lines
  below the comment explaining why that does not always follow (and a test had pinned the false sentence); `show`
  accepted a blank half of the natural key and answered about a class that cannot exist; `medrt_run`'s remedy
  trailed off in `...` where all five flags are `required=True`; and the HANDOVER line bound was stated in three
  files, two of which disagreed while the file exceeded both. Line-length enforcement is
  [issue 66](https://github.com/cairn-ehr/drugref/issues/66) — `ruff` runs its default rule set, which omits `E501`.

  **This round also reopened [#61](https://github.com/cairn-ehr/drugref/issues/61)**, closed in error by
  `92baaea`'s own commit body — "Filed rather than fixed: #61 …" still puts the number directly after `fixed:`, and
  GitHub's linker matches on token adjacency, not on the sentence's meaning. Reopened after checking `build_parser`
  directly: nothing #61 asked for existed yet. **The fourth occurrence of the sweep-closed-but-unfixed pattern**
  (#31, #35, #40, #61), and the first where the author was deliberately writing prose to avoid it — restated as the
  standing rule in `docs/HANDOVER.md`: keep the number away from `close`/`fix`/`resolve` in any inflection.
- **Floor hardening** — close the `TRUNCATE` + table-owning-role bypass (row-level triggers don't cover them) via **RLS +
  privilege separation** — the full floor design §7 always envisioned (design §10 tension G). **Note the test-suite coupling**
  (wrong three times now — three, seven, then nine, the last of them written directly beneath this instruction — so re-run the
  grep): `grep -l TRUNCATE tests/*.py` finds **eleven**, one of them **`mesh_rel_fixtures.py`** — a shared helper rather than
  a test module, holding the one truncate both MeSH-keyed test modules use — each `TRUNCATE`-ing the drugref tables in an
  autouse fixture because their orchestrators commit internally and so escape the `conn` fixture's rollback. Those fixtures
  depend on precisely the bypass this item closes, so hardening the floor must land together with a replacement isolation
  strategy (a privileged test role, or per-test schemas) or the suite stops being able to reset itself.
- **Production ingest** — batch-commit large real feeds; the verify-before-production checklist (ChEBI/UNII/MED-RT licence
  deeds; grow the closed crosswalk + allow-list toward completeness). Note the moiety gate is the binding constraint on
  classification yield: MED-RT classifies 6,012 ingredients, so class coverage grows with the registry, not with more MED-RT
  parsing.
- **`EPC`-adjacent MED-RT content not yet used** — `EXT` concepts, and the class→class `has_*` assertions (an EPC declaring
  its own mechanism/effect) which would let class-level knowledge inherit along the DAG.

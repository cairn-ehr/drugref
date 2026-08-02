# ROADMAP — drugref

> **Disposable working scaffolding, not a source of truth.** The canonical *what/why* is the design spec(s)
> under [`docs/superpowers/specs/`](superpowers/specs/) (and future ADRs). This file only orders the build.
> If it disagrees with the canonical docs, the canonical docs win.

**Scope:** the **global tier** of drugref.org (jurisdiction-independent substance identity → chemistry →
classes → interactions), built bottom-up, followed by the consumer API and the local (country-specific) tier.
drugref is an **advisory reference-data service** — it never sits on Cairn's signed inter-node wire core.

## Cross-cutting (applies to every slice)

- **TDD** — failing test first, then code.
- **Licensing is non-negotiable** — all code AGPL-3.0; every dependency AND every bundled reference-data source must
  be AGPL-3.0-compatible, **checked before adding/bundling**. Encumbered sources (ATC, SNOMED/AMT, ICD-10-AM, eTG,
  AMH, commercial DrugBank…) attach only as **node-local, separately-licensed plug-ins**, never bundled.
- **Advisory tier, integrity in the DB** — ingest/normalization is fit-for-purpose Python (fast iteration on brittle
  feeds), but append-only/identity integrity is enforced **in PostgreSQL** (constraints/triggers/RLS), not app code.
  Postgres (≥ 18) is the integration substrate.
- **Hybrid store** — ingested feeds are **rebuildable projections** (drop-and-rebuild, version-pinned, `ingest_run`
  provenance); curated knowledge is an **append-only, signed overlay** (the moat).
- **Own immortal UUIDs, never key on a name** (principle 2); external IDs attach as **append-only claims**;
  cross-source identity is reconciled by **linking, never re-keying**.

## The data model in the large (two orthogonal structures)

1. **Composition tree** (*is-made-of*, downward): **active moiety → specific substance (salt/ester/hydrate)
   → clinical drug (moiety/salt + strength + form) → product (brand/pack)**. Product is the *local* tier.
2. **Classification DAG** (*is-a-kind-of*, orthogonal): `class ⊂ class ⊂ …`; `moiety ∈ many classes` on
   multiple axes (chemical / mechanism / therapeutic) — **many-to-many**, a link, never a parent FK.

The curated overlay attaches to nodes in **either** structure and **inherits along the edges** (down the tree, up
through a moiety's classes) — curate once, apply widely. This is the biggest curation-economy lever.

## Slices

### Slice 1 — Active-moiety identity spine ✅ DONE (gate corrected, see below)
Schema `drugref` (3 tables: `ingest_run`, `substance_moiety`, `identity_claim`) + append-only row-level floor; Python
ingest; moiety registry with immortal `UUIDv5`-on-UNII (pinned) + append-only cross-ref claims; membership gate +
closed legacy allow-list; international seeding (UNII backbone / INN display / ChEBI cross-refs / RxNorm demoted to a
claim / closed USAN↔INN crosswalk); ChEBI enrichment by InChIKey. 30 tests. Detail: the slice-1 spec + plan.
**Corrected by the identity-spine fix round** (below): the parser read a `PT` column the real release does not have,
and the `has-INN` gate turned out to rest on a false premise about `INN_ID`.

### Slice 2a — MED-RT classification DAG + membership ✅ DONE
Class registry (`substance_class`, own UUIDv5-on-NUI) + subclass DAG (`class_parent`) + many-to-many
`class_membership`, seeded from **MED-RT** (licence-verified: VA federal work, public domain, UMLS restriction level
0). Six ingested axes — MoA / PE / TC / PK / **EPC** / APC; `HC` (alphabetical navigation bins) and `EXT` excluded.
Membership joins to moieties via the `RXNORM_IN` claims slice 1 already records; EPC membership is hierarchical
(`Parent Of` from EPC to ingredient), normalised to `has_EPC`. Class edges are **rebuildable projections**,
deliberately outside slice 1's append-only floor. Against the full 2026.07.06 release: 3,634 classes, 3,961 DAG edges
(440 multi-parent), 27,540 memberships over 6,012 ingredients. 102 tests. Detail: the slice-2a design spec.

### Slice 2a.1 — Source-neutral class registry ✅ DONE
The enabling refactor for 2b, extracted because the 2a registry was built around a single authority.
`db/003`: `medrt_nui`/`medrt_code` → `source_code`/`published_code`, a NOT NULL `source` column,
uniqueness moved from global to **per (source, source_code)**, and `PA`/`has_PA` added to the axis CHECKs.
`ids.mint_class_uuid(source, code)` replaces the NUI-only form. **MED-RT class UUIDs are unchanged**,
pinned by frozen literals — the derivation is the join key of both edge tables, so a drift would orphan
every edge on the next rebuild with no error anywhere. 116 tests.

### Slice 2b — MeSH Pharmacological Actions ✅ DONE
The second classification axis, on the **same three tables** as MED-RT (2a.1's `db/003` already added
`PA`/`has_PA`/`MeSH`; **no schema change**). `ingest/mesh.py` — a pure, streaming (`iterparse`) parser of
the three MeSH files (pa/desc/supp) → 568 PA class descriptors, a **multi-parent DAG from tree-number
nesting** (both-endpoints-PA scoping, like MED-RT), and memberships carrying set-valued `RegistryNumber`
keys. `ingest/mesh_run.py` — orchestrator + the **two-key membership bridge**: UNII-primary → CAS-fallback,
resolving a member's keys against slice-1 `identity_claim` rows (`scheme='UNII'`, else `'CAS'`) — **no new
external source**. Unmatched members counted, split no-key vs key-not-in-registry (never dropped).
`ClassConcept` moved to `classes.py` (source-neutral) + a generic `moieties_by_scheme` join primitive.
167 tests. Detail: the slice-2b design spec.

The measurement that shaped it **refuted the doc-research**: MeSH **Descriptors DO carry UNIIs** in `RegistryNumber`
(aspirin D001241 = UNII `R16CO5Y76E`, not "CAS only"), and a record may carry several, so key extraction is
set-valued. **Re-measured post-#26: 10,506 member substances**, **72.8% carrying an identity key** — the figure this
file used to call "73% joinable", which it is not — while only **40.6% reach a gated-in moiety** (both shortfalls
counted): the moiety gate is the binding constraint, as for MED-RT. `RelatedRegistryNumber` CAS is **not** a bridge
key here (tension B — deferred precision pass). MeSH licence verified AGPL-compatible (attribution +
no-endorsement + version-currency; no NC/ND), attributed in `NOTICE`; **ATC stays excluded** (NC + no-derivatives).
Follow-ups: that precision pass, and MED-RT's own `has_SC` — 3,632 assertions, mostly (3,384) into MeSH structural
classes and so unblocked by the bridge, but **248 target MED-RT itself** and never needed it.

### Slice 3 — Composition tree: specific substances (salts/esters/hydrates)
Add the salt level below the moiety, keyed on **UNII** with `parent_moiety_uuid` from **GSRS active-moiety
relationships**; salt↔base strength-equivalence data. Additive to the slice-1 schema.

### Slice 4 — Clinical drugs (moiety/salt + strength + form)
The prescribable generic level (**RxNorm SCD** as the skeleton). Composition-tree leaf before product/local.

### Slice 5 — The interaction & contraindication layer
Two halves, per the hybrid store: **ingested rebuildable projections** (5a, 5b, 5b.2) seeded from public-domain
regulatory-derived content, then the **append-only signed curated overlay** (5c). The projections give a defensible
safety layer *fast*, from sources drugref already holds; the overlay is the durable value-add built on top. Sequenced
by licence-cleanliness, not by coverage.

#### Slice 5a — MED-RT mechanism/effect contraindications ✅ DONE
The smallest first cut: MED-RT **`CI_MoA`/`CI_PE`** ("contraindicated mechanism/physiological-effect of a
**co-administered ingredient**") = ~739 **class-level drug–drug** rules, mined from the **MED-RT file slice 2a
already parses** — **no new source, no new join, no new UUID minting** (both endpoints — RxNorm subject, MoA/PE class
object — are already ingested). New table `class_contraindication` (`db/004`), a rebuildable projection like
`class_membership`; concrete drug pairs **expand at read time** over the existing class DAG (`ddi_candidate_pair`
view — since Plan B that expansion descends the DAG, see below). **Candidate tier only** — MED-RT does not track
label updates, so rows carry provenance and feed review; nothing here auto-alerts. Design:
[slice-5a spec](superpowers/specs/2026-07-25-drugref-slice-5a-medrt-contraindication-design.md).

#### Slice 5b — MeSH-keyed contraindications ✅ DONE
The MeSH-endpoint MED-RT content, unlocked by ingesting **MeSH disease/chemical descriptors** (same NLM licence
already cleared in 2b — **no new source**). The 5b design round **split the work in two**: **5b** is the
contraindication half — `CI_with` (drug→condition) + `CI_ChemClass`'s moiety arm (drug↔drug) — over a new MeSH
**condition** registry; **5b.2** is the indication half (`may_treat`/`may_prevent`/`may_diagnose`/`induces`), which
reuses that registry — and, as it turned out, **widens** it, since one closure is taken over both halves' objects.
Spec: [slice-5b](superpowers/specs/2026-07-28-drugref-slice-5b-mesh-contraindication-design.md). `db/013`–`db/016`;
merged as PR #44. **Measured yield against the real releases** — **the measurement corrected the spec in five places;
the measured figures are the true ones**:

| | measured | spec had predicted |
|---|---:|---:|
| `condition` (registry) | **5,203** (5,190 descriptors + 13 SCRs) | 5,190 — descriptors only |
| `condition_parent` | **7,157** (1,690 multi-parent) | 7,157 ✓ |
| `moiety_condition_contraindication` | **9,471** over 2,900 moieties / 641 conditions | 9,482 / 667 |
| `moiety_contraindication` (exact pairs) | **1,442** | 1,443 |
| unresolved `CI_ChemClass` objects / their rules | **103** / **405** | 108 / 405 |
| `condition_contraindication_expanded` | **191,728**, of which 9,471 direct | — |

Three of the five are one cause: the spec counted MeSH **ConceptUIs** while drugref keys on the **record**, and
several concepts resolve to one record (`mesh_concepts.py` exists to keep them apart). The other two: the registry
also holds 13 **supplementary records**, which carry no tree numbers and so never enter the closure; and exactly one
**self-pair** (tranylcypromine), which `db/014` forbids because a drug is not contraindicated with itself. The
**103-vs-108** correction is published as an erratum in the docs-site *Design decisions* section — the specs are
immutable, so that living record is where it lives.

Clinically confirmed: a rule on **Epilepsy** now reaches a patient coded *Temporal Lobe*, *Complex Partial*, *Frontal
Lobe*… (14 direct rows → 378 over 27 conditions, ~10 ms per lookup); and **pregnancy + lactation carry 615 rows**,
which is why the table is `moiety_condition_contraindication` and not `drug_disease_*` — `CI_with`'s object is a
*patient state*, and a disease-shaped table would have filed the release's most consequential contraindication axis
as a category error.

**Deferred, deliberately — `CI_ChemClass`'s class arm** (405 assertions over 103 objects): **withheld, not dropped.**
Expanding it over MeSH's *structural* chemical tree makes a rule on Sulfonamides reach bendroflumethiazide and
bosentan — the discredited sulfa cross-reactivity inference — and only 8.3% of those objects have any `has_SC`
member, so that route cannot fill the gap either. Published instead as `gap_unresolved_ci_object` + a fifth
`gap_kind`, one row per object with its rule count, for a curator to rule on.

Those 103 are **two kinds of thing, and `object_kind` (`db/014`) keeps them apart.** Failing to bridge an object to a
moiety is not evidence that the object is a class: a MeSH record carrying **no registry key** (Alkalies, Organic
Chemicals) names no substance and is the sulfonamide case, while one carrying a real **UNII or CAS** names a substance
drugref's gated registry does not hold. Reading the first fact off the second asked a curator whether a
contraindication naming *Pimozide* — a leaf drug descriptor — should expand over the drugs below it. Both kinds stay
on the worklist. **Also not done here:** MED-RT's `has_SC` (3,632 assertions, **248 targeting MED-RT itself**) and
the `RelatedRegistryNumber` precision pass.

**Two of the three things this slice was told not to forget were honoured; the third was retracted.**
`condition_ci_axis.expands_descendants` is declared per predicate **with no DEFAULT** (`db/014`), so MeSH's
differently-shaped tree cannot inherit a recall-safe guess. `mesh_run` still owes
`unresolved_expansion_policy('MeSH')` — **not yet needed**, since no MeSH-keyed row can exist in
`class_expansion_policy` until the class arm lands. And **the source-blind walk: 5b does NOT end it.** 5b registers no
MeSH chemical class in `substance_class`, so the hazard stays **latent** until `has_SC` or the class arm lands.

Follow-up filed: [#39](https://github.com/cairn-ehr/drugref/issues/39) — `ingest_unmatched_ingredient` rebuilt per
`source` while two orchestrators write under `MED-RT`. **Closed by the interaction debt round below.**

#### Post-5b debt round ✅ DONE
The five follow-ups 5b's review filed, cleared before 5b.2 reuses the same code paths, plus the two issues the tracker
had closed while the code still carried them. `db/017`; **536 tests**.
[#40](https://github.com/cairn-ehr/drugref/issues/40) one gz-aware MeSH reader (`mesh.iter_records`), which also fixed
a regeneration command that found nothing against a real release · [#17](https://github.com/cairn-ehr/drugref/issues/17)
the last silent refusal counted · [#42](https://github.com/cairn-ehr/drugref/issues/42) the descriptor-wins tie-break
pinned — **measured: 0 of MeSH's ConceptUIs appear in both desc and supp, so the release cannot exercise that branch
and the guard is against a future partition change** · [#41](https://github.com/cairn-ehr/drugref/issues/41) the CI
object's namespace taken from the data in BOTH the view and `questions.py`, preserving every existing `question_uuid`
and **canonicalised in both** · [#43](https://github.com/cairn-ehr/drugref/issues/43) one `checksum(*paths)`, one
`db.clear_source_tables`, six declared table tuples each restated independently by test. **Re-verified end-to-end**:
every 5b figure reproduced exactly, and the `object_kind` split is newly recorded as **96 CHEMICAL_CLASS (386 rules)
+ 7 UNREGISTERED_SUBSTANCE (19 rules)**.

#### Interaction debt round ✅ DONE
The three interaction-model follow-ups, cleared before 5b.2 reused these code paths. `db/018`; 568 tests. Each was
**measured against the real releases before it was touched, and two of the three issue texts proved stale** — a
number in an issue is a claim about a release, not a fact about the code.

[#39](https://github.com/cairn-ehr/drugref/issues/39) — `ingest_unmatched_ingredient` gains a `reason`
discriminator (`classification` | `contraindication`, NOT NULL, **no DEFAULT**, in the PK), so each of the two
orchestrators writing it under source `MED-RT` clears exactly what it re-derives. `db.clear_source_tables` grew an
opt-in `match=` narrowing rather than a seventh copy of the DELETE. Measured **2,137 + 826** rows; the `DISTINCT ON
(rxcui)` gap view is unchanged at 2,140. Both of slice 5b's documented caveats are gone.

[#31](https://github.com/cairn-ehr/drugref/issues/31) — `gap_dead_by_expansion_policy`, a **sixth gap kind**: a
contraindication whose object class is *denied* expansion, holds no direct *partner* on the rule's axis, and *does*
have one below, so the rule reaches nobody. Measuring it turned up a **second, unreported cause**:
`gap_unpopulated_contraindication` counted the rule's own subject as a member although `ddi_candidate_pair` excludes
it, so `acetohydroxamic acid` → `Urease Inhibitors [MoA]` was dead and silent — **12 → 13 classes, 38 → 39 dead
rules**. **The review of the round then found the same defect in the NEW view**, because the reach measure was stated
twice and only one copy learned the subject exclusion; it is now one view — **`ci_rule_partner_reach`** — with the two
gap views as complementary filters on one column, so the partition holds by construction. Re-measured
([#50](https://github.com/cairn-ehr/drugref/issues/50)): still **ONE class**, but **299 drugs held back, not 300**,
because the rule's own subject *clomiphene* is filed under it. Everything else held: 2,140 unmatched, 21,664 pairs.

[#45](https://github.com/cairn-ehr/drugref/issues/45) — `contraindications_for_condition(uuid)` walks **UP** from the
patient's condition instead of down from all 641 roots: **0.7–0.9 ms against 9–10 ms**, ~13×. A materialised view was
rejected (a REFRESH in every writer, and a new way to be silently stale). The view stays for whole-set access and
`WHERE is_direct`; **equivalence with it is pinned by test and was checked on the real release** — 200 conditions,
4,935 rows, zero difference either way. Residue filed:
[#47](https://github.com/cairn-ehr/drugref/issues/47) (`medrt_run` counted its own unmatched CI subjects without
persisting them — **closed by the ingest-operability round below**) and
[#48](https://github.com/cairn-ehr/drugref/issues/48) (a non-expanding predicate with no direct member is equally
dead and needs its own view; unreachable until a **class-side** predicate stops expanding — neither 5b.2 nor Plan C
made it live). [#50](https://github.com/cairn-ehr/drugref/issues/50), the post-review re-measurement, is **closed**.

#### Slice 5b.2 — MeSH-keyed indications ✅ DONE — merged as PR #54
The other half of the MeSH-endpoint content: **`may_treat`/`may_prevent`/`may_diagnose`** plus **`induces`** — a
public-domain, drugref-owned drug–disease indication dataset (the MeDIC alternative it holds outright). **No new
source.** Spec: [slice-5b.2](superpowers/specs/2026-07-30-drugref-slice-5b2-mesh-indication-design.md). `db/019`;
**622 tests**. It reuses 5b's machinery entirely — the same registry, the same ConceptUI→record resolution, the same
closure, the same candidate-tier posture — and adds **no new mechanism**. One orchestrator (`ingest/mesh_rel_run.py`)
now owns **both** halves, because `condition_parent` edges are derived by both closures and so cannot be split by a
discriminator. **Measured end-to-end** — **the measurement corrected the spec again, in three related places**:

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

`indications_for_condition` was checked against `condition_indication_reach` for **every** registry condition —
**5,963 checked, 276,343 rows, zero disagreements**, the same pin #45 established for the contraindication pair.

**The three corrections share one cause: the spec measured before the moiety gate.** 1,426 indication subject RxCUIs
are carried by no moiety, and `condition_subtree` walks the **641** roots that *stored* rules name rather than the
**677** the release references. Re-measured pre-gate, the spec's figures reproduce exactly — both right about
different populations, as 5b's concept-vs-record grain was. Standing correction:
`decisions/indications-do-not-expand.md`.

**What the slice decides, and why it is not a contraindication with the sign flipped.** A contraindication expands
**down** the condition DAG (a patient coded *Temporal Lobe Epilepsy* is a patient with epilepsy). An indication must
not: the same walk distributes a therapeutic claim over the object's subclasses, and one `may_treat` on *Neoplasms*
would manufacture 708 claims MED-RT never made. So **nothing derived is stored**; `indications_for_condition` walks
**UP** and labels every derived row `is_direct = false`, a **weaker** claim rather than a wider one — not optional
decoration, since **3,719 of 5,963 registry conditions have no direct indication but do have an ancestor with one**.
`induces` gets its **own table** and no axis row: the drug *causes* the state, and a shared table plus a forgotten
filter reads "carbamazepine treats agranulocytosis".

**Registry widening moves 5b's expanded figures — upward, and that is a completion.** One closure is taken over every
MeSH-keyed object, so the DAG gains edges a condition's second tree number only the indication half registers. 10 of
641 contraindication roots grew, none shrank, the root set is byte-identical, every direct figure unchanged.
**Expect this every time the registry widens.**

**Two widenings survive the upward walk, and 5b.2 COUNTS rather than resolves them.** **168 (drug, condition) pairs**
are asserted as an indication *and* a contraindication — carvedilol/*Heart Failure*, alteplase/*Stroke* — real
distinctions (chronic HFrEF vs acute decompensation) the MeSH descriptor grain cannot carry, stated by MED-RT with no
qualifier. And **422 of 18,314 assertions name a subordinate concept**, so every row that follows sits on a BROADER
record than MED-RT named (MED-RT keys on a ConceptUI, a condition on the record): `may_treat` "Seizures, Focal" for
eslicarbazepine is stored on *Seizures*, which it aggravates when generalised. That 422 is **release-grain, above the
moiety gate** — it describes MED-RT, not how many rows landed. Both are `MeshRelSummary` fields, both in `COMMENT
ON`, both pinned by tests, and both have a curated follow-up: [#51](https://github.com/cairn-ehr/drugref/issues/51)
(how a consumer is told) and [#52](https://github.com/cairn-ehr/drugref/issues/52) (store `concept_ui`, which also
makes the row figure queryable).

**Residue filed by the merge review:** [#55](https://github.com/cairn-ehr/drugref/issues/55) — the read path offers
its generalisations through an `is_direct` boolean rather than through structure, the very mitigation `db/019`
rejected when it gave `induces` its own table. **Deferred to 5c by decision**, which is already revisiting how a
consumer is told about #51 and #52. #53 is **closed by the round below**.

#### The #53 population-label round ✅ DONE — merged as PR #56
The three residuals 5b.2's final review filed rather than fixed, plus the six its own review round then found. **No
migration and no production logic change** — docstrings, one published page, and one fixture that could not tell two
grains apart. **623 tests**; all three fixtures byte-reproducible from the real releases.

Each claim was **re-measured against the real releases before it was touched**, and all three held. Two were prose:
`550 of 13,463` → **`550 of 13,458`**, and `this slice's 1,053` → **the 2,198 codes one run resolves** (1,053 CI +
1,528 IND, 383 named by both). Newly measured: the 81 concepts collapse onto **79** records, and **the indication half
has no resolution gap** (1,528/1,528), which is what makes 4.09% and 2.30% comparable.

**The third was a test claiming to pin a grain its fixture could not distinguish.** The collision counter reports
**pairs**; the test said a drift to **rows** would fail there, and it would not — one overlapping row and one
overlapping pair cannot tell them apart, so removing the production query's `SELECT DISTINCT` left the suite green
(verified by mutation). Fixed by **strengthening the fixture rather than weakening the claim**: **mannitol**, the
only subject in the release asserting `may_treat` *and* `may_prevent` *and* `CI_with` against one object (*Anuria*).
The fixture now holds **2 pairs across 3 rows**; the extractor's cap exempts exactly these overlap assertions, scoped
to the therapeutic predicates by `is_cap_exempt` since `Synonym Of` shares their endpoint shape. That the fixture
grew is *not* a spec-10 violation, because "the direct rows must not move" is about **widening the closure** and
mannitol is a new **subject**. **Also not done here:** the 193 class-subject indications (filed against #8), `has_SC`,
and any read path that ranks or prefers among indications — MED-RT asserts no line of therapy, no evidence strength
and no ordering, and inventing one is slice 5c's curated work rather than a projection's.

#### Slice 5c — The curated overlay (the moat)
Append-only, **signed** overlay adding **severity + mechanism + management + evidence grading** — the dimensions the
projections lack — **referencing** the 5a/5b candidate rows. **Plan C has already built the overlay MECHANISM**
(surrogate key + deferred single-live + one-way supersession, generalised over four tables), so 5c inherits a working
correction shape rather than inventing one, and owns #51, #52, #55. Layered by licence-safety: **ONC high-priority
floor** (RAND/government-licensed) → **SPL/DailyMed-mined** (ONSIDES-*method*, MIT precedent) → DDInter *if its
licence confirms* → drugref's own hand-curation. The **"moat" is quality-control — who may assert — not access or
leverage**: data ships paywall-free under copyleft. Institutionally owned, never a volunteer wiki.

### Slice 6 — HTTP public API
The co-equal-consumer interface (any EHR/pharmacy/app; Cairn on the same footing). Deferred until there is
data worth serving; co-located Cairn reaches the schema directly meanwhile.

### Slice 7 — Cairn `inn_code` wiring (Tier-A consumer)
Fill the deliberately-nullable `inn_code` slot in Cairn's medication surface: autocomplete, coding a
previously-uncoded substance, DDI advisory — **overlay enrichment, never a wire change** on the Cairn side.

### Slice 8 — Local tier (Australia first)
Country-specific packaging/pricing. **Corrected claim (was: "PBS + TGA ARTG, both CC BY, redistributable" — refuted
by a live-source check, spec §1):** neither is confirmed open. The PBS Schedule/API data mart carries no CC BY
statement and `pbs.gov.au`'s copyright page reads all-rights-reserved (CC BY is verified only for PBS's separate
*statistical* datasets on data.gov.au); TGA ARTG's copyright page is explicitly non-commercial. ATC (WHO, NC+ND) and
AMT/SNOMED CT-AU (NCTS-licensed) were never candidates for bundling. The posture for all of them is the one
**CLAUDE.md rule 6 already states**: drugref ships **AGPL-3.0 ingest code and schema only**, never a release; a node
operator supplies their own under whatever terms bind them. Redistribution is blocked pending written confirmation —
tracked for PBS as [#25](https://github.com/cairn-ehr/drugref/issues/25).

**One stated exception, so the claim above stays literally true:** `tests/fixtures/pbs_items_subset.csv` commits ~a
dozen real PBS rows as a test input, extracted by `make_pbs_subset.py` so the suite runs against the real upstream
shape instead of a guess at it. Argued as fair-dealing scale, not a dataset; it is in scope for #25 and is the thing
that has to go if #25 comes back negative.

#### Slice 8a — PBS localisation: the local tier's first attachment ✅ DONE
A spike proving the local-tier pattern — name-only bridge, jurisdiction scoping, structural encumbrance
quarantine — before investing further. A minimal Australian PBS product layer (`local_product`,
`local_product_moiety`, `local_unmatched_ingredient` — `db/009`, a **rebuildable projection**, deliberately
outside slice 1's append-only floor since a de-listed PBS item must be able to disappear) bridged to the
global moiety spine **by name alone**, the only licence-clean join: PBS carries no UNII, CAS or InChIKey.
Design: [slice-8a spec](superpowers/specs/2026-07-25-drugref-slice-8a-pbs-localisation-design.md).

Measured against the real July-2026 release (14,840 items): the bridge sat at **85.5%** against a **92.4%** ceiling
(all UNII substance names), and slice 8a's reading — **the moiety gate, not the bridge, is the binding constraint**
([#26](https://github.com/cairn-ehr/drugref/issues/26)) — proved right: after the identity-spine fix round the bridge
reaches **13,719 = 92.4%, exactly that ceiling**, with unmatched components down 3,140 → 347. It took *two* fixes to
show it, since the gate change alone moved nothing until the bridge stopped indexing `INN` claims. The salt-strip
heuristic (an admitted slice-3 stand-in) is now **5 bridge rows, 0.03%** — reported as near-worthless rather than left
to quietly imply otherwise (rule 5); slice 3's GSRS salt relationships are the real fix. The residual is otherwise
AU/INN-vs-USAN spelling divergence and non-drugs the moiety gate correctly excludes. **347** tests after the PR-review
fix round (2 findings deferred to #29 / #30). No `NOTICE` change — the ingest path redistributes nothing; the test
fixture noted above is the sole committed PBS data and is tracked under #25.

Remaining slice-8 scope (not built): pricing (AEMP/DPMQ/premiums/fees), restriction texts/criteria, TGA
ARTG, the composition tree's salt/clinical-drug levels underneath the bridge, and the same shape applied to
other jurisdictions.

## Cross-cutting hardening (not a single slice)

- **Identity-spine fix round ✅ DONE** (#27, #17, #26 — post-Plan-B). The round that made every other slice's
  coverage number real, and every defect in it was invisible to the committed fixtures. `ingest/unii.py` read a
  **`PT` column the real UNII release does not have** (it is `Display Name`), so `row.get("PT") or ""` produced an
  empty label for all 168,046 rows — a production run would have completed "successfully" over an **entirely
  unlabelled registry** with a dead allow-list and a dead USAN↔INN crosswalk. Required columns are now **declared and
  checked**; the lesson taken was not "that column was renamed" but "`or ""` absorbed a structural mismatch". The
  legacy allow-list moved to **UNII keys** (#17) after its flagship entry was measured to match nothing. And the
  membership gate (#26) became **`INN_ID | USAN_ID | (RXCUI & drug-like SUBSTANCE_TYPE)`** plus the allow-list:
  `INN_ID` is a sparse cross-reference, not a has-INN flag, and was empty for amoxicillin, morphine and aspirin.
  **The asymmetry is the design** — a strong identifier admits outright, because uniformly type-filtering would delete
  heparin, enoxaparin, protamine and 346 gene/cell therapies from a drug-interaction service. **Strictly monotone**,
  pinned by a test. `db/011` records the admitting signal as a rebuildable projection. A fourth defect surfaced only
  on measurement: the gate change moved **no** downstream number until the PBS bridge stopped indexing `INN` claims —
  the new moieties have none — and indexed `display_name` instead. Measured: moieties **12,591 → 19,438**, PBS bridge
  **85.5% → 92.4%**, MED-RT classified moieties **2,066 → 3,875**, `ddi_candidate_pair` **6,402 → 21,664**. 412 tests.
  Residue: #33 (closed by slice 3). Spec:
  [moiety gate redesign](superpowers/specs/2026-07-27-drugref-moiety-gate-redesign.md).
- **Foundation review ✅ DONE** (post-slice-5a, whole-codebase). `db/005` made the correction overlay one-way and
  re-assertable (partial unique index on LIVE claims; supersession set once, same-moiety, strictly forward — closes
  #4) and constrained `ingest_run.source`; `db/006` replaced the comment-enforced CHECK↔CASE coupling with a `ci_axis`
  table the vocabulary is an FK into, put `source` in the contraindication PK, renamed the pair view's columns to
  their roles and moved the clinical contract into `COMMENT ON`. `apply_migrations` gained a **checksum ledger** —
  migrations are immutable once applied. Parser/identity fixes: UNII rows with no identity key are refused (they were
  merging unrelated drugs onto one immortal UUID), TSV read with `QUOTE_NONE`, ambiguous MED-RT published codes
  refused, claim values canonicalised, orchestrators roll back and log. **CI added** (PG18 service; the DB-gated
  majority now fails rather than skips). 220 tests. Remaining then: **#16 (crashed-ingest visibility + CLI) and #17**
  — both since closed, #16 by the ingest-operability round below.
- **Open-question registry ✅ DONE** (Plan A of the additive-effect design). `db/007` adds `open_question` (a
  rebuildable projection keyed on a deterministic `question_uuid` external tooling can cite) plus three append-only
  curated tables — `question_state`, `question_source_check`, `question_evidence` — each with a surrogate PK and
  live-row-only uniqueness, per `db/005`. `db/008` adds the three gap views, the `ingest_unmatched_ingredient` table
  that makes the third of them possible (the ingest previously kept only the COUNT and discarded the RxCUIs), the
  `source_tier` cost ladder and the `question_worklist` view that orders by cheapest-unchecked tier. Every
  orchestrator rebuilds the register as its last step before commit, so it reflects the database after any ingest
  rather than only the one that ran last: coverage gaps are published rather than hidden, and "how much do we not
  know" is a number watchable per release. **Watermark, not closure:** no-evidence-found leaves a question open;
  only `withdrawn` is terminal. **Populated is per axis:** the contraindication gap view joins `db/006`'s `ci_axis`,
  because a class populated on an axis the rule does not expand over still yields no pair — reading it
  relationship-blind hides real gaps. **A closed gap carrying curator work is retired, not deleted**
  (`open_question.is_current`): the curated tables cascade from `open_question` *and* refuse `DELETE`, so deleting
  one aborts the ingest outright. **Seven gap kinds by 5b.2, eleven since Plan C** — measured against the real
  releases: unclassified_moiety 16,089 · unmatched_ingredient 2,150 · unresolved_ci_object 103 ·
  condition_without_indication 97 · unpopulated_contraindication 13 · dead_by_expansion_policy 1 · the rest 0.
- **Descendant expansion ✅ DONE** (Plan B of the additive-effect design; the work #15 asked for). `db/010`
  makes `ddi_candidate_pair` descend the class DAG — **for a contraindication, fewer rows is the harm
  direction**, and direct-only hid 21.9% of `CI_MoA` and **85.2%** of `CI_PE` pairs because MED-RT files
  membership at the specific node while writing rules against the parent. New columns `member_class` and
  `is_direct`, so `WHERE is_direct` reproduces the old row set exactly and a consumer who forgets the filter errs
  toward recall. Bounded by **`class_expansion_policy`** — a deny-list held as data a pharmacist can read, seeded
  with the 14 CI object classes over the `>20 descendant classes` discovery heuristic (**all PE, not one MoA**): 11
  denied as abstract organ-system buckets, 3 explicitly allowed. **The deny-list filters the rule's object class,
  never the walk** — `Decreased Coagulation Activity` is a descendant of a denied root and must still expand, which
  is how a rule reaches warfarin, apixaban and aspirin. Plus `ci_axis.expands_descendants` per predicate and
  `gap_unreviewed_expansion_root`, a fourth question kind, so the list cannot rot silently across releases. 384
  tests. Residue filed as #31 — **closed by the interaction debt round** (`gap_dead_by_expansion_policy`).
- **Plan B review round ✅ DONE** (`db/012`, PR #38). The review of #32 found no defect in the expanded read path, and
  **five gaps between what `db/010`'s comments legislate and its DDL does**: the recursive walk becomes one view
  (**`ci_class_subtree`**) instead of three copies of itself; `gap_unreviewed_expansion_root` joins `ci_axis`, so it
  stops asking whether a class should expand when its predicates cannot; `expansion_policy_unresolved` gains a
  consumer in `medrt_run`, having shipped as a detector nothing read; `class_expansion_policy.source` gains the CHECK
  every other `source` column has; and two `COMMENT ON`s stop overclaiming — `expands_descendants` is a recall-safe
  *default*, not a gate, and the walk is **source-blind** (`class_parent` carries no `source`, so a transitive walk
  can cross vocabularies — **still latent after 5b**). Row set unchanged. 419 tests. Follow-ups: #35, #36, #37.
- **Plan C — the accumulation model ✅ DONE** (`db/020`–`db/024`; spec §4–§8 / §11 steps 6–7; plan:
  [plan-c](superpowers/plans/2026-08-01-plan-c-accumulation-model.md)). The gate was 5b, which has landed. The model
  the pairwise projection cannot express — **many drugs, one effect that adds up** — plus **groups**, the role-based
  exception where members play different parts and a count is meaningless (the triple whammy). Five curated tables,
  two read views (spec §8's output contract), four gap views, **gap kinds 8–11**, and `accumulation.py` — the single
  writer plus the two PURE rules a consumer applies. **Ships with an EMPTY curation set**, which is exactly what §11
  step 7 asks for; curation is step 8. **No new source**, but drugref becomes an authority in its own registry
  (`source = 'DRUGREF'`, all three places extended together). **748 tests.**

  **`db/023`–`db/024` are the review round on it**, five findings each measured rather than reasoned: the generic
  single-live trigger was **unindexable and therefore quadratic** (2,000 rows 5,773 ms → **42 ms**, linear, via
  equality predicates + partial `<table>_live_key` indexes); `gap_uncurated_threshold` cleared on promotions that
  regrade **nobody**, so it now gates on unreviewed MEMBERS rather than on row count; `interaction_group_assertion`
  gained the `applies` ruling column `db/020` gave the other two tables but not it, so a group can be **retired as a
  whole**; and `interaction_group_member_moiety`'s deliberate non-uniqueness is now stated instead of merely true.
  Fifth and costliest: `gap_ineffective_contribution` asked its question as a **correlated** subquery naming
  `class_subtree` twice, re-running the 22,754-row closure **per curated row** — **59 s** for 400 promotions,
  **465 ms** after `db/024` hoists the walk out of the loop. A synthetic probe missed it entirely because its fixture
  had no DAG edges: **measure recursion against a real DAG or do not measure it.**

  **Measured against the real releases**, whole chain 110 s, **every prior figure reproduced exactly** (19,438 ·
  3,634/3,961/18,639 · 5,963/8,507 · 9,471 · 1,442 · 103 · 14,674/154 · 168/422 · the seven gap counts), with
  `ddi_candidate_pair` **unchanged at 21,664** and its filtered lookup **3.1 ms** — the regression this slice most
  had to avoid. New: `class_subtree` **22,754** · `gap_uncurated_additive_effect` **381** of 1,873 PE classes · the
  other three gap views and both read views **0**, correct with nothing curated · **18,834** questions, 11 kinds.

  **Three things the design document was wrong about, each found by test or measurement.** (1) Spec §5.0 prescribes a
  partial unique index for single-live; it **cannot work** on a table whose corrections preserve the natural key, so
  the deferred constraint trigger `db/007` invented for `question_state` is adopted and generalised — published as
  the living record `decisions/correcting-a-curated-assertion.md`. (2) **Nothing could be RETIRED**: supersession must
  point at a later row with the same key, so `interaction_group_member.satisfies_role` and
  `additive_effect.accumulates` are what make §5.3's "superseding the last member of a role removes the role" and
  §5.2's reviewed-vs-unreviewed distinction implementable at all. (3) Generalising `ci_class_subtree` instead of
  adding a second walk was measured and **rejected**: identical rows, **5× slower** on the hot path, because
  root-scoping is what makes it cheap. **Not done here, deliberately:** any curation, and any `DRUGREF`-minted class
  — §6 says mint only where the release genuinely says nothing. **#35 is NOT closed**: the append-only shape now
  exists beside `class_expansion_policy`, but moving that table onto it is its own change.
- **Ingest-operability round ✅ DONE** (#16, #47; `db/025`–`db/026`; spec:
  [ingest-operability](superpowers/specs/2026-08-02-drugref-ingest-operability-design.md)). A debt round: no new
  source, no clinical claim. Six orchestrators wrote their `ingest_run` row **inside the transaction that did the
  work**, so a crash rolled the provenance away and `finished_at`'s "started, never finished" state could never be
  observed; and `pyproject.toml` had no `[project.scripts]`, so an ingest ran from a test or a REPL and nowhere else.
  **`provenance.py`** is now the only writer of a run record, pinned by two contract tests that grep the tree:
  `open_run` commits its row in its own transaction — **the commit *is* the feature** — while `finish_run`
  deliberately does not, so the stamp lands with the work, and an orchestrator takes two transactions on one
  connection. **`db/025`** adds `ingest_run.writer` (NOT NULL, no DEFAULT) because source `MED-RT` has **two** writers
  whose checksums legitimately differ — #39 one layer up, on the table #39's own fix could not reach — plus
  `loaded_release` (per `source, writer`) and `ingest_run_incomplete`, **which could only ever have been empty before
  this round**; historical rows carry `'unattributed'`, and nothing is guessed. **A `drugref` console script**:
  `migrate`, `status`, one `ingest` subcommand per orchestrator, and `ingest chain`, which runs all six in dependency
  order from one directory, resolves inputs by documented globs (**zero matches and several matches are both
  errors**, all resolved before any step runs), and is how this round's own measurement was taken. `chebi.py`, the
  orchestrator the foundation review missed, gained the try/rollback/logging the other five have. **779 tests.**

  **Measuring #47 turned up two findings, both about `db/018`'s own justifications** for widening
  `gap_unmatched_ingredient`'s tie-break *explicitly anticipating #47*. Both were false by the time #47 arrived.
  (1) "`classification` wins alphabetically" — but `class_contraindication`, **the value the issue itself proposes**,
  is the one string that sorts *before* it, so `db/026` ships **`contraindication_class`**. (2) "and by being the
  bucket with a `name`" — measured on the real releases, **0 of 4,389 rows carry a name in any bucket** while **1,430
  RxCUIs sit in more than one**: the tie-break is live on real data and simply unobservable. The view now prefers a
  named row explicitly; the release cannot exercise that branch, so it is pinned on controlled input and verified by
  mutation.

  **Measured end to end through the new chain** (fresh `drugref_ops`, **110.37 s**, no workarounds): the new
  `contraindication_class` bucket **99** · `classification`/`contraindication`/`indication` **2,137/826/1,426** ·
  `gap_unmatched_ingredient` **2,150**, `open_question` **18,834**, `ddi_candidate_pair` **21,664** — all unchanged ·
  `loaded_release` **4 rows**, both MED-RT writers visible · `ingest_run_incomplete` **0**. **The round's own plan
  text carried four defects, every one caught by an implementer measuring rather than reading**: the writer count
  (twice), an error-message assertion that contradicted the code it tested, and a chain glob naming
  `UNII_Names_*.txt` — a real file beside `UNII_Records_*.txt` carrying none of the four gate columns the parser
  requires, which made `ingest chain --unii-release` fail outright as first shipped. **A plan is a claim about the
  code too.**
- **Floor hardening** — close the `TRUNCATE` + table-owning-role bypass (row-level triggers don't cover them)
  via **RLS + privilege separation** — the full floor design §7 always envisioned (design §10 tension G).
  **Note the test-suite coupling** (this count has now been wrong twice — three, then seven — so re-run the
  grep before quoting it): `grep -l TRUNCATE tests/*.py` finds **nine** files, one of them
  **`mesh_rel_fixtures.py`** — a shared helper rather than a test module, holding the one truncate both
  MeSH-keyed test modules use — each `TRUNCATE`-ing the drugref tables in an autouse fixture because their
  orchestrators commit internally and so escape the `conn` fixture's rollback. Those fixtures depend on precisely
  the bypass this item closes, so hardening the floor must land together with a replacement isolation strategy
  (a privileged test role, or per-test schemas) or the suite stops being able to reset itself.
- **Production ingest** — batch-commit large real feeds; the verify-before-production checklist (ChEBI/UNII/MED-RT
  licence deeds; grow the closed crosswalk + allow-list toward completeness). Note the moiety gate is the binding
  constraint on classification yield: MED-RT classifies 6,012 ingredients, so class coverage grows with the registry,
  not with more MED-RT parsing.
- **`EPC`-adjacent MED-RT content not yet used** — `EXT` concepts, and the class→class `has_*` assertions
  (an EPC declaring its own mechanism/effect) which would let class-level knowledge inherit along the DAG.

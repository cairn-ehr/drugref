# ROADMAP — drugref

> **Disposable working scaffolding, not a source of truth.** The canonical *what/why* is the design spec(s)
> under [`docs/superpowers/specs/`](superpowers/specs/) (and future ADRs). This file only orders the build.
> If it disagrees with the canonical docs, the canonical docs win.

**Scope:** the **global tier** of drugref.org (jurisdiction-independent substance identity → chemistry →
classes → interactions), built bottom-up, followed by the consumer API and the local (country-specific) tier.
drugref is an **advisory reference-data service** — it never sits on Cairn's signed inter-node wire core.

## Cross-cutting (applies to every slice)

- **TDD** — failing test first, then code.
- **Licensing is non-negotiable** — all code AGPL-3.0; every dependency AND every bundled reference-data source
  must be AGPL-3.0-compatible, **checked before adding/bundling**. Encumbered sources (ATC, SNOMED/AMT,
  ICD-10-AM, eTG, AMH, commercial DrugBank…) attach only as **node-local, separately-licensed plug-ins**,
  never bundled.
- **Advisory tier, integrity in the DB** — ingest/normalization is fit-for-purpose Python (fast iteration on
  brittle feeds), but append-only/identity integrity is enforced **in PostgreSQL** (constraints/triggers/RLS),
  not app code. Postgres (≥ 18) is the integration substrate.
- **Hybrid store** — ingested feeds are **rebuildable projections** (drop-and-rebuild, version-pinned,
  `ingest_run` provenance); curated knowledge is an **append-only, signed overlay** (the moat).
- **Own immortal UUIDs, never key on a name** (principle 2); external IDs attach as **append-only claims**;
  cross-source identity is reconciled by **linking, never re-keying**.

## The data model in the large (two orthogonal structures)

1. **Composition tree** (*is-made-of*, downward): **active moiety → specific substance (salt/ester/hydrate)
   → clinical drug (moiety/salt + strength + form) → product (brand/pack)**. Product is the *local* tier.
2. **Classification DAG** (*is-a-kind-of*, orthogonal): `class ⊂ class ⊂ …`; `moiety ∈ many classes` on
   multiple axes (chemical / mechanism / therapeutic) — **many-to-many**, a link, never a parent FK.

The curated overlay attaches to nodes in **either** structure and **inherits along the edges** (down the tree,
up through a moiety's classes) — curate once, apply widely. This is the biggest curation-economy lever.

## Slices

### Slice 1 — Active-moiety identity spine ✅ DONE (gate corrected, see below)
Schema `drugref` (3 tables: `ingest_run`, `substance_moiety`, `identity_claim`) + append-only row-level floor;
Python ingest; moiety registry with immortal `UUIDv5`-on-UNII (pinned) + append-only cross-ref claims;
membership gate + closed legacy allow-list; international seeding (UNII backbone / INN display /
ChEBI cross-refs / RxNorm demoted to a claim / closed USAN↔INN crosswalk); ChEBI enrichment by InChIKey.
30 tests. Full detail: the slice-1 design spec + plan.

**Corrected by the identity-spine fix round** (below): the parser read a `PT` column the real release
does not have, and the `has-INN` gate turned out to rest on a false premise about `INN_ID`.

### Slice 2a — MED-RT classification DAG + membership ✅ DONE
Class registry (`substance_class`, own UUIDv5-on-NUI) + subclass DAG (`class_parent`) + many-to-many
`class_membership`, seeded from **MED-RT** (licence-verified: VA federal work, public domain, UMLS
restriction level 0). Six ingested axes — MoA / PE / TC / PK / **EPC** / APC; `HC` (alphabetical navigation
bins) and `EXT` excluded. Membership joins to moieties via the `RXNORM_IN` claims slice 1 already records;
EPC membership is hierarchical (`Parent Of` from EPC to ingredient), normalised to `has_EPC`. Class edges
are **rebuildable projections**, deliberately outside slice 1's append-only floor. Against the full
2026.07.06 release: 3,634 classes, 3,961 DAG edges (440 multi-parent), 27,540 memberships over 6,012
ingredients. 102 tests. Detail: the slice-2a design spec.

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

The measurement that shaped it **refuted the doc-research**: MeSH **Descriptors DO carry UNIIs** in
`RegistryNumber` (aspirin D001241 = UNII `R16CO5Y76E`, not "CAS only"), and a record may carry several, so
key extraction is set-valued. **10,505 member substances**, **73% joinable** (27% combinations / research
compounds); moiety gate is the binding constraint, as for MED-RT. `RelatedRegistryNumber` CAS is **not** a
bridge key in this slice (tension B — deferred precision pass). MeSH licence verified AGPL-compatible (NLM
terms: attribution + no-endorsement + version-currency; no NC/ND), attributed in `NOTICE`. **ATC stays
excluded** (NC + no-derivatives). Follow-ups: the RelatedRegistryNumber precision pass, and MED-RT's own
`has_SC` — 3,632 assertions, mostly (3,384) into MeSH structural classes and so unblocked by the bridge,
but **248 of them target MED-RT itself** and never needed the bridge at all (see HANDOVER).

### Slice 3 — Composition tree: specific substances (salts/esters/hydrates)
Add the salt level below the moiety, keyed on **UNII** with `parent_moiety_uuid` from **GSRS active-moiety
relationships**; salt↔base strength-equivalence data. Additive to the slice-1 schema.

### Slice 4 — Clinical drugs (moiety/salt + strength + form)
The prescribable generic level (**RxNorm SCD** as the skeleton). Composition-tree leaf before product/local.

### Slice 5 — The interaction & contraindication layer
Two halves, per the hybrid store: **ingested rebuildable projections** (5a, 5b, 5b.2) seeded from
public-domain regulatory-derived content, then the **append-only signed curated overlay** (5c). The
projections give a defensible safety layer *fast*, from sources drugref already holds; the overlay is the
durable value-add built on top. Sequenced by licence-cleanliness, not by coverage.

#### Slice 5a — MED-RT mechanism/effect contraindications ✅ DONE
The smallest first cut: MED-RT **`CI_MoA`/`CI_PE`** ("contraindicated mechanism/physiological-effect of a
**co-administered ingredient**") = ~739 **class-level drug–drug** rules, mined from the **MED-RT file slice
2a already parses** — **no new source, no new join, no new UUID minting** (both endpoints — RxNorm subject,
MoA/PE class object — are already ingested). New table `class_contraindication` (`db/004`), a rebuildable
projection like `class_membership`; concrete drug pairs **expand at read time** over the existing class DAG
(`ddi_candidate_pair` view — since Plan B that expansion descends the DAG, see below). **Candidate tier
only** — MED-RT does not track label updates, so rows carry provenance and feed review; nothing here
auto-alerts. Design:
[slice-5a spec](superpowers/specs/2026-07-25-drugref-slice-5a-medrt-contraindication-design.md) · plan:
[slice-5a plan](superpowers/plans/2026-07-25-slice-5a-medrt-contraindication.md).

#### Slice 5b — MeSH-keyed contraindications ✅ DONE
The MeSH-endpoint MED-RT content, unlocked by ingesting **MeSH disease/chemical descriptors** (same NLM
licence already cleared in 2b — **no new source**). The 5b design round **split the work in two**: **5b** is
the contraindication half — `CI_with` (drug→condition) + `CI_ChemClass`'s moiety arm (drug↔drug) — over a
new MeSH **condition** registry; **5b.2** is the indication half
(`may_treat`/`may_prevent`/`may_diagnose`/`induces`, ~18k), which **reuses that registry unchanged**. Spec:
[slice-5b](superpowers/specs/2026-07-28-drugref-slice-5b-mesh-contraindication-design.md). `db/013`–`db/016`;
494 tests.

**Measured yield against the real releases** (UNII 26Feb2026 + MED-RT 2026.07.06 + MeSH desc/supp 2026, live
PG18) — **the measurement corrected the spec in five places; the measured figures are the true ones**:

| | measured | spec had predicted |
|---|---:|---:|
| `condition` (registry) | **5,203** (5,190 descriptors + 13 SCRs) | 5,190 — descriptors only |
| `condition_parent` | **7,157** (1,690 multi-parent) | 7,157 ✓ |
| `moiety_condition_contraindication` | **9,471** over 2,900 moieties / 641 conditions | 9,482 / 667 |
| `moiety_contraindication` (exact pairs) | **1,442** | 1,443 |
| withheld class objects / their rules | **103** / **405** | 108 / 405 |
| `condition_contraindication_expanded` | **191,728**, of which 9,471 direct | — |

Three of the five are one cause: the spec counted MeSH **ConceptUIs** while drugref keys on the **record**,
and several concepts resolve to one record (`mesh_concepts.py` exists to keep them apart). The other two:
the registry also holds 13 **supplementary records**, which carry no tree numbers and so never enter the
closure; and exactly one **self-pair** (tranylcypromine), which `db/014` forbids because a drug is not
contraindicated with itself. The **103-vs-108** correction is published as an erratum in the docs-site
*Design decisions* section — the specs are immutable, so that living record is where it lives.

Clinically confirmed: a rule on **Epilepsy** now reaches a patient coded *Temporal Lobe*, *Complex Partial*,
*Frontal Lobe*… (14 direct rows → 378 over 27 conditions, ~10 ms per lookup); and **pregnancy + lactation
carry 615 rows**, which is why the table is `moiety_condition_contraindication` and not `drug_disease_*` —
`CI_with`'s object is a *patient state*, not a disease, and a disease-shaped table would have filed the
release's most consequential contraindication axis as a category error.

**Deferred, deliberately — `CI_ChemClass`'s class arm** (405 assertions over 103 objects): **withheld, not
dropped.** Expanding it over MeSH's *structural* chemical tree makes a rule on Sulfonamides reach
bendroflumethiazide and bosentan — the discredited sulfa cross-reactivity inference. Only 8.3% of those
objects have any `has_SC` member, so that route cannot fill the gap either. Published instead as
`gap_unresolved_ci_object` + a fifth `gap_kind`, one row per object with its rule count, for a curator to
rule on — Plan B's 14-expansion-roots precedent.

**Also not done here:** 5b.2 (indications), MED-RT's `has_SC` (3,632 assertions, **248 targeting MED-RT
itself**), and the `RelatedRegistryNumber` precision pass.

**Two of the three things this slice was told not to forget were honoured; the third was retracted.**
`condition_ci_axis.expands_descendants` is declared per predicate **with no DEFAULT** (`db/014`), so MeSH's
differently-shaped tree cannot inherit a recall-safe guess. `mesh_run` still owes
`unresolved_expansion_policy('MeSH')` — **not yet needed**, since no MeSH-keyed row can exist in
`class_expansion_policy` until the class arm lands. And **the source-blind walk: 5b does NOT end it**, the
claim this file used to make. 5b registers no MeSH chemical class in `substance_class` and conditions live
in their own tables with their own MeSH-only DAG, so the hazard stays **latent** until `has_SC` or the class
arm lands. Stated in `db/012`'s comments and in `ddi_candidate_pair`'s `COMMENT ON`.

Follow-up filed: [#39](https://github.com/cairn-ehr/drugref/issues/39) — `ingest_unmatched_ingredient` is
rebuilt per `source` while two orchestrators write under `MED-RT`; two caveats **documented and tested, not
solved** (order-dependence, cross-run accumulation). See HANDOVER.

#### Slice 5b.2 — MeSH-keyed indications
The other half of the MeSH-endpoint content: **`may_treat`/`may_prevent`/`may_diagnose`** (~18k) plus
**`induces`** (170) — a public-domain, drugref-owned drug–disease dataset (a MeDIC alternative it holds
outright). **The cheapest slice on this list**, because 5b built everything it needs: the same `condition` /
`condition_parent` registry **unchanged**, the same `mesh_concepts` ConceptUI→record resolution, the same
descendant closure, the same rebuildable-projection/candidate-tier posture. What it adds is a relation (or
relations — an indication is not a contraindication and should not share a table), a vocabulary row per
predicate with `expands_descendants` **declared, not defaulted**, and its own objects extended into the
registry closure. Note **`induces` points the other way**: the drug *causes* the state, so it is neither an
indication nor a contraindication and must not be filed as either.

#### Slice 5c — The curated overlay (the moat)
Append-only, **signed** overlay adding **severity + mechanism + management + evidence grading** — the
dimensions the projections lack — **referencing** the 5a/5b candidate rows. Layered by licence-safety:
**ONC high-priority floor** (re-encoded from the papers; RAND/government-licensed) → **SPL/DailyMed-mined**
(ONSIDES-*method*, MIT precedent) → DDInter *if its licence confirms* → drugref's own hand-curation as the
durable value-add. The **"moat" is quality-control — who may assert — not access or leverage**: data ships
paywall-free under copyleft (derivatives shared alike; code AGPL-3.0). Institutionally-owned, never a
volunteer wiki. Plus core pharmacology prose.

### Slice 6 — HTTP public API
The co-equal-consumer interface (any EHR/pharmacy/app; Cairn on the same footing). Deferred until there is
data worth serving; co-located Cairn reaches the schema directly meanwhile.

### Slice 7 — Cairn `inn_code` wiring (Tier-A consumer)
Fill the deliberately-nullable `inn_code` slot in Cairn's medication surface: autocomplete, coding a
previously-uncoded substance, DDI advisory — **overlay enrichment, never a wire change** on the Cairn side.

### Slice 8 — Local tier (Australia first)
Country-specific packaging/pricing. **Corrected claim (was: "PBS + TGA ARTG, both CC BY, redistributable" —
refuted by a live-source check, spec §1):** neither is confirmed open. The PBS Schedule/API data mart
carries no CC BY statement and `pbs.gov.au`'s copyright page reads all-rights-reserved (CC BY is verified
only for PBS's separate *statistical* datasets on data.gov.au); TGA ARTG's copyright page is explicitly
non-commercial. ATC (WHO, NC+ND) and AMT/SNOMED CT-AU (NCTS-licensed) were never candidates for bundling.
The posture for all of them is the one **CLAUDE.md rule 6 already states**: drugref ships **AGPL-3.0
ingest code and schema only**, never a release; a node operator supplies their own under whatever
terms bind them. Redistribution of any of it is blocked pending written confirmation — tracked for PBS as
[#25](https://github.com/cairn-ehr/drugref/issues/25).

**One stated exception, so the claim above stays literally true:** `tests/fixtures/pbs_items_subset.csv`
commits ~a dozen real PBS rows as a test input, extracted by `tests/fixtures/make_pbs_subset.py` so the
suite runs against the real upstream shape instead of a guess at it. Argued as fair-dealing scale, not a
dataset; it is in scope for #25 and is the thing that has to go if #25 comes back negative.

#### Slice 8a — PBS localisation: the local tier's first attachment ✅ DONE
A spike proving the local-tier pattern — name-only bridge, jurisdiction scoping, structural encumbrance
quarantine — before investing further. A minimal Australian PBS product layer (`local_product`,
`local_product_moiety`, `local_unmatched_ingredient` — `db/009`, a **rebuildable projection**, deliberately
outside slice 1's append-only floor since a de-listed PBS item must be able to disappear) bridged to the
global moiety spine **by name alone**, the only licence-clean join: PBS carries no UNII, CAS or InChIKey.
Design: [slice-8a spec](superpowers/specs/2026-07-25-drugref-slice-8a-pbs-localisation-design.md).

Measured against the real July-2026 release (14,840 items): the bridge sat at **85.5%** against a **92.4%**
ceiling (all UNII substance names), and slice 8a's reading — **the moiety gate, not the bridge, is the
binding constraint** ([#26](https://github.com/cairn-ehr/drugref/issues/26)) — proved right: after the
identity-spine fix round the bridge reaches **13,719 = 92.4%, exactly that ceiling**, with unmatched
components down 3,140 → 347. It took *two* fixes to show it, since the gate change alone moved nothing
until the bridge stopped indexing `INN` claims (see the fix round below). The salt-strip heuristic (an
admitted slice-3 stand-in) is now **5 bridge rows, 0.03%** — reported as near-worthless rather than left to
quietly imply otherwise (rule 5); slice 3's GSRS salt relationships are the real fix. The residual is
otherwise AU/INN-vs-USAN spelling divergence (cefalexin, ciclosporin, …) and non-drugs the moiety gate
correctly excludes (vitamins, dressings). 334 tests at the initial build; 341 after the final whole-branch
review round (all 12 findings fixed), **347** after the PR-review fix round (5 findings fixed, 2 deferred to
[#29](https://github.com/cairn-ehr/drugref/issues/29)/[#30](https://github.com/cairn-ehr/drugref/issues/30)
— see HANDOVER.md). No `NOTICE` change — the ingest path redistributes nothing; the test fixture noted
above is the sole committed PBS data and is tracked under #25.

Remaining slice-8 scope (not built): pricing (AEMP/DPMQ/premiums/fees), restriction texts/criteria, TGA
ARTG, the composition tree's salt/clinical-drug levels underneath the bridge, and the same shape applied to
other jurisdictions.

## Cross-cutting hardening (not a single slice)

- **Identity-spine fix round ✅ DONE** (#27, #17, #26 — post-Plan-B). The round that made every other
  slice's coverage number real, and every defect in it was invisible to the committed fixtures.
  `ingest/unii.py` read a **`PT` column the real UNII release does not have** (it is `Display Name`;
  `PT` is a *value* of the TYPE column in the separate names file), so `row.get("PT") or ""` produced an
  empty label for all 168,046 rows — a production run would have completed "successfully" over an
  **entirely unlabelled registry** with a dead allow-list and a dead USAN↔INN crosswalk. Required columns
  are now **declared and checked**; the lesson taken was not "that column was renamed" but "`or ""`
  absorbed a structural mismatch". The legacy allow-list moved to **UNII keys** (#17) after its flagship
  entry was measured to match nothing (`MAGNESIUM SULFATE, UNSPECIFIED FORM`). And the membership gate
  (#26) became **`INN_ID | USAN_ID | (RXCUI & drug-like SUBSTANCE_TYPE)`** plus the allow-list: `INN_ID`
  is a sparse cross-reference, not a has-INN flag, and was empty for amoxicillin, morphine, codeine,
  doxycycline, tacrolimus and aspirin. **The asymmetry is the design** — a strong identifier admits
  outright, because uniformly type-filtering would delete heparin, enoxaparin, protamine and 346 gene/cell
  therapies from a drug-interaction service. **Strictly monotone**, pinned by a test. `db/011` records the
  admitting signal as a rebuildable projection (the moiety is immortal; the evidence is per-release).
  A fourth defect surfaced only on measurement: the gate change moved **no** downstream number until the
  PBS bridge stopped indexing `INN` claims — the new moieties have none — and indexed `display_name`
  instead, which is lossless and is what a *name* bridge should match. Measured against the real releases:
  moieties **12,591 → 19,438**, PBS bridge **85.5% → 92.4%** (exactly the ceiling slice 8a identified),
  unmatched PBS components **3,140 → 347**, MED-RT classified moieties **2,066 → 3,875**, and
  `ddi_candidate_pair` **6,402 → 21,664**. 412 tests. Residue filed as
  [#33](https://github.com/cairn-ehr/drugref/issues/33) (MeSH form-specific CAS keys, closed by slice 3).
  Spec: [moiety gate redesign](superpowers/specs/2026-07-27-drugref-moiety-gate-redesign.md).

- **Foundation review ✅ DONE** (post-slice-5a, whole-codebase). `db/005` made the correction overlay
  one-way and re-assertable (partial unique index on LIVE claims; supersession set once, same-moiety,
  strictly forward — closes #4) and constrained `ingest_run.source`; `db/006` replaced the
  comment-enforced CHECK↔CASE coupling with a `ci_axis` table the vocabulary is an FK into, put `source`
  in the contraindication PK, renamed the pair view's columns to their roles and moved the clinical
  contract into `COMMENT ON`. `apply_migrations` gained a **checksum ledger** — migrations are immutable
  once applied. Parser/identity fixes: UNII rows with no identity key are refused (they were merging
  unrelated drugs onto one immortal UUID), TSV read with `QUOTE_NONE`, ambiguous MED-RT published codes
  refused, claim values canonicalised, orchestrators roll back and log. **CI added** (PG18 service; the
  DB-gated majority now fails rather than skips). 220 tests. Remaining: #16 (crashed-ingest visibility +
  CLI), #17 (last no-silent-drop gaps).
- **Open-question registry ✅ DONE** (Plan A of the additive-effect design). `db/007` adds
  `open_question` (a rebuildable projection keyed on a deterministic `question_uuid` external tooling can
  cite) plus three append-only curated tables — `question_state`, `question_source_check`,
  `question_evidence` — each with a surrogate PK and live-row-only uniqueness, per `db/005`. `db/008` adds
  the three gap views, the `ingest_unmatched_ingredient` table that makes the third of them possible (the
  ingest previously kept only the COUNT and discarded the RxCUIs), the `source_tier` cost ladder and the
  `question_worklist` view that orders by cheapest-unchecked tier. Every ingest orchestrator (UNII, MED-RT,
  MeSH) rebuilds the register as its last step before commit, so it reflects the database after any ingest
  rather than only the one that ran last. Coverage gaps are now published rather than hidden, and "how much
  do we not know" is a number watchable per release. **Watermark, not closure:** no-evidence-found leaves a
  question open; only `withdrawn` is terminal. **Populated is per axis:** the contraindication gap view
  joins `db/006`'s `ci_axis`, because a class populated on an axis the rule does not expand over still
  yields no pair — reading it relationship-blind hides real gaps. **A closed gap carrying curator work is
  retired, not deleted** (`open_question.is_current`): the curated tables cascade from `open_question` *and*
  refuse `DELETE`, so deleting one aborts the ingest outright. **Five gap kinds now** — slice 5b added
  `unresolved_ci_object`; measured against the real releases: unclassified_moiety 16,089 ·
  unmatched_ingredient 2,140 · unresolved_ci_object 103 · unpopulated_contraindication 12 ·
  unreviewed_expansion_root 0.
- **Descendant expansion ✅ DONE** (Plan B of the additive-effect design; the work #15 asked for). `db/010`
  makes `ddi_candidate_pair` descend the class DAG — **for a contraindication, fewer rows is the harm
  direction**, and direct-only hid 21.9% of `CI_MoA` and **85.2%** of `CI_PE` pairs because MED-RT files
  membership at the specific node while writing rules against the parent. New columns `member_class` and
  `is_direct`, so `WHERE is_direct` reproduces the old row set exactly and a consumer who forgets the filter
  errs toward recall. Bounded by **`class_expansion_policy`** — a deny-list held as data a pharmacist can
  read, seeded with the 14 CI object classes over the `>20 descendant classes` discovery heuristic (**all
  PE, not one MoA**): 11 denied as abstract organ-system buckets, 3 explicitly allowed. **The deny-list
  filters the rule's object class, never the walk** — `Decreased Coagulation Activity` is a descendant of a
  denied root and must still expand, which is how a rule reaches warfarin, apixaban and aspirin. Plus
  `ci_axis.expands_descendants` per predicate (slice 5b's MeSH tree has a different shape) and
  `gap_unreviewed_expansion_root`, a fourth question kind, so the list cannot rot silently across releases.
  384 tests. Residue filed as #31.
- **Plan B review round ✅ DONE** (`db/012`, PR #38). The review of #32 found no defect in the
  expanded read path, and **five gaps between what `db/010`'s comments legislate and its DDL does**: the
  recursive walk becomes one view (**`ci_class_subtree`**) instead of three copies of itself;
  `gap_unreviewed_expansion_root` joins `ci_axis`, so it stops asking whether a class should expand when its
  predicates cannot (latent today, live at 5b); `expansion_policy_unresolved` gains a consumer in
  `medrt_run`, having shipped as a detector nothing read; `class_expansion_policy.source` gains the CHECK
  every other `source` column has; and two `COMMENT ON`s stop overclaiming — `expands_descendants` is a
  recall-safe *default*, not a gate, and the walk is **source-blind** (`class_parent` carries no `source`, so
  a transitive walk can cross vocabularies — **still latent after 5b**, see above). Row set unchanged. 419
  tests. Follow-ups filed as #35, #36, #37. The axis-aware review gate, latent then, is **live at 5b**.
  **Plan C (the accumulation model) was gated on 5b, which has now landed its contraindication half.**
- **Floor hardening** — close the `TRUNCATE` + table-owning-role bypass (row-level triggers don't cover them)
  via **RLS + privilege separation** — the full floor design §7 always envisioned (design §10 tension G).
  **Note the test-suite coupling** (this count has now been wrong twice — three, then seven — so re-run the
  grep before quoting it): `grep -l TRUNCATE tests/*.py` finds **nine** modules — `test_chebi.py`,
  `test_gap_views.py`, `test_ingest_run.py`, `test_medrt_run.py`, `test_mesh_ci_run.py`,
  `test_mesh_run.py`, `test_moiety_admission.py`, `test_pbs_run.py` and `test_questions.py` — each
  `TRUNCATE`-ing the drugref tables in an autouse fixture
  because their orchestrators commit internally and so escape the `conn` fixture's rollback. Those fixtures
  depend on precisely the bypass this item closes, so hardening the floor must land together with a
  replacement isolation strategy (e.g. a privileged test role, or per-test schemas) or the suite stops being
  able to reset itself.
- **Production ingest** — batch-commit large real feeds; the verify-before-production checklist (real UNII
  headers/`INN_ID`; ChEBI/UNII/MED-RT licence deeds; grow the closed crosswalk + allow-list toward
  completeness). Note the moiety gate is the binding constraint on classification yield: MED-RT classifies
  6,012 ingredients, so class coverage grows with the registry, not with more MED-RT parsing.
- **`EPC`-adjacent MED-RT content not yet used** — `EXT` concepts, and the class→class `has_*` assertions
  (an EPC declaring its own mechanism/effect) which would let class-level knowledge inherit along the DAG.
- **Governance** — consider adding a CLAUDE.md (coding rules) and an ADR log as the design surface grows.

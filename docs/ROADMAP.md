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

### Slice 1 — Active-moiety identity spine ✅ DONE
Schema `drugref` (3 tables: `ingest_run`, `substance_moiety`, `identity_claim`) + append-only row-level floor;
Python ingest; moiety registry with immortal `UUIDv5`-on-UNII (pinned) + append-only cross-ref claims;
`has-INN` (+ closed legacy allow-list) membership gate; international seeding (UNII backbone / INN display /
ChEBI cross-refs / RxNorm demoted to a claim / closed USAN↔INN crosswalk); ChEBI enrichment by InChIKey.
30 tests. Full detail: the slice-1 design spec + plan.

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
Two halves, per the hybrid store: **ingested rebuildable projections** (5a, 5b) seeded from public-domain
regulatory-derived content, then the **append-only signed curated overlay** (5c). The projections give a
defensible safety layer *fast*, from sources drugref already holds; the overlay is the durable value-add
built on top. Sequenced by licence-cleanliness, not by coverage.

#### Slice 5a — MED-RT mechanism/effect contraindications ← next
The smallest first cut: MED-RT **`CI_MoA`/`CI_PE`** ("contraindicated mechanism/physiological-effect of a
**co-administered ingredient**") = ~739 **class-level drug–drug** rules, mined from the **MED-RT file slice
2a already parses** — **no new source, no new join, no new UUID minting** (both endpoints — RxNorm subject,
MoA/PE class object — are already ingested). New table `class_contraindication` (`db/004`), a rebuildable
projection like `class_membership`; concrete drug pairs **expand at read time** over the existing class DAG
(`ddi_candidate_pair` view). **Candidate tier only** — MED-RT does not track label updates, so rows carry
provenance and feed review; nothing here auto-alerts. Design:
[slice-5a spec](superpowers/specs/2026-07-25-drugref-slice-5a-medrt-contraindication-design.md) · plan:
[slice-5a plan](superpowers/plans/2026-07-25-slice-5a-medrt-contraindication.md).

#### Slice 5b — MeSH-keyed contraindications & indications
The MeSH-endpoint MED-RT content, unlocked once **MeSH disease/chemical descriptors** are ingested (same NLM
MeSH licence already cleared in slice 2b): **`CI_with`** (drug–disease contraindication, ~11.5k),
**`CI_ChemClass`** (drug–drug by chemical class, ~1.9k), and **`may_treat`/`may_prevent`/`may_diagnose`/
`induces`** (~18k drug–disease indications + drug-induced states) — a public-domain, drugref-owned drug–
disease dataset (a MeDIC-alternative it holds outright). Still projection tier, still candidate-only.

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
ingest code and schema only**, never the data; a node operator supplies their own release under whatever
terms bind them. Redistribution of any of it is blocked pending written confirmation — tracked for PBS as
[#25](https://github.com/cairn-ehr/drugref/issues/25).

#### Slice 8a — PBS localisation: the local tier's first attachment ✅ DONE
A spike proving the local-tier pattern — name-only bridge, jurisdiction scoping, structural encumbrance
quarantine — before investing further. A minimal Australian PBS product layer (`local_product`,
`local_product_moiety`, `local_unmatched_ingredient` — `db/009`, a **rebuildable projection**, deliberately
outside slice 1's append-only floor since a de-listed PBS item must be able to disappear) bridged to the
global moiety spine **by name alone**, the only licence-clean join: PBS carries no UNII, CAS or InChIKey.
Design: [slice-8a spec](superpowers/specs/2026-07-25-drugref-slice-8a-pbs-localisation-design.md).

Measured against the real July-2026 release (14,840 items): **92.4%** name-bridge ceiling against all UNII
substance names, but only **84.6%** against today's INN-gated registry — the **moiety gate, not the
bridge, is the binding constraint** ([#26](https://github.com/cairn-ehr/drugref/issues/26)), the same
pattern already measured for MED-RT and MeSH, now on a third independent axis. The salt-strip heuristic (an
admitted slice-3 stand-in) contributes only 1.1% of bridge rows against the gated vocabulary and **0.0% at
the ceiling** — reported as near-worthless rather than left to quietly imply otherwise (rule 5); slice 3's
GSRS salt relationships are the real fix. The residual is otherwise explained by AU/INN-vs-USAN spelling
divergence (paracetamol, cefalexin, ciclosporin, …) and non-drugs the moiety gate correctly excludes
(vitamins, dressings). 334 tests at the initial build; 341 after the final whole-branch review round (all
12 findings fixed — see HANDOVER.md). No `NOTICE` change — this slice redistributes nothing.

Remaining slice-8 scope (not built): pricing (AEMP/DPMQ/premiums/fees), restriction texts/criteria, TGA
ARTG, the composition tree's salt/clinical-drug levels underneath the bridge, and the same shape applied to
other jurisdictions.

## Cross-cutting hardening (not a single slice)

- **Foundation review ✅ DONE** (post-slice-5a, whole-codebase). `db/005` made the correction overlay
  one-way and re-assertable (partial unique index on LIVE claims; supersession set once, same-moiety,
  strictly forward — closes #4) and constrained `ingest_run.source`; `db/006` replaced the
  comment-enforced CHECK↔CASE coupling with a `ci_axis` table the vocabulary is an FK into, put `source`
  in the contraindication PK, renamed the pair view's columns to their roles and moved the clinical
  contract into `COMMENT ON`. `apply_migrations` gained a **checksum ledger** — migrations are immutable
  once applied. Parser/identity fixes: UNII rows with no identity key are refused (they were merging
  unrelated drugs onto one immortal UUID), TSV read with `QUOTE_NONE`, ambiguous MED-RT published codes
  refused, claim values canonicalised, orchestrators roll back and log. **CI added** (PG18 service; the
  DB-gated majority now fails rather than skips). 220 tests. Remaining: #15 (DAG-descendant expansion,
  measure first), #16 (crashed-ingest visibility + CLI), #17 (last no-silent-drop gaps).
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
  yields no pair — reading it relationship-blind hides real gaps, and slice 5b (MeSH `has_PA`) is where the
  axes stop coinciding. **A closed gap carrying curator work is retired, not deleted**
  (`open_question.is_current`): the curated tables cascade from `open_question` *and* refuse `DELETE`, so
  deleting one aborts the ingest outright. Plans B (DAG-descendant expansion, #15) and C (the accumulation
  model) remain.
- **Floor hardening** — close the `TRUNCATE` + table-owning-role bypass (row-level triggers don't cover them)
  via **RLS + privilege separation** — the full floor design §7 always envisioned (design §10 tension G).
  **Note the test-suite coupling** (corrected, slice-8a review round — the prior count of three was wrong
  and went unverified before being repeated): `grep -l TRUNCATE tests/*.py` finds **seven** modules —
  `test_chebi.py`, `test_gap_views.py`, `test_ingest_run.py`, `test_medrt_run.py`, `test_mesh_run.py`,
  `test_pbs_run.py` and `test_questions.py` — each `TRUNCATE`-ing the drugref tables in an autouse fixture
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

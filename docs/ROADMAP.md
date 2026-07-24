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

### Slice 2b — MeSH Pharmacological Actions (NEXT)
The second classification axis, on the **same three tables**. The **source-neutral class registry it needs
is now built** (`db/003`: `source` + `source_code`/`published_code`, per-source uniqueness, `PA`/`has_PA`
axes) — an earlier note here that 2b needed "no schema change" was wrong, since the registry was
MED-RT-shaped. What remains for 2b: the MeSH parser + the **membership bridge**.

**The bridge is the open question.** Unlike MED-RT, MeSH membership has no RxCUI to join through.
Documentation research (not yet confirmed against the real release) says:
- MeSH **SCRs** carry a **UNII** in the Registry Number field (NLM moved to UNIIs in 2013, ~8,000 records
  initially; displaced CAS/EC numbers moved to Related Registry Number) — a *direct* join to drugref's
  identity key, needing no new source.
- MeSH **Descriptors** appear NOT to carry a UNII: aspirin (D001241) exposes only
  `relatedRegistryNumber "50-78-2 (Aspirin)"` — i.e. **CAS**, which slice 1 also already records as a claim.

So the likely bridge is **two-key (UNII for SCRs, CAS for Descriptors), both already held as slice-1
claims**. This must be **measured against the real release before it is designed** — the public SPARQL
endpoint returned self-contradictory counts (62,344 "topical descriptors", more than all of MeSH), and this
project's rule since slice 2a is to verify upstream shape against the actual files, never the docs.
Requires downloading `desc2026.xml` / `supp2026.xml` / `pa2026.xml` from
[NLM](https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/).

MeSH licence verified AGPL-compatible (NLM terms: attribution "Courtesy of the U.S. National Library of
Medicine" + no-endorsement + version-currency; no NC, no ND, modification and redistribution permitted).
MED-RT's own `has_SC` relationships (targeting MeSH structural classes) become ingestible once the bridge
exists. **ATC stays excluded** (NC + no-derivatives).

### Slice 3 — Composition tree: specific substances (salts/esters/hydrates)
Add the salt level below the moiety, keyed on **UNII** with `parent_moiety_uuid` from **GSRS active-moiety
relationships**; salt↔base strength-equivalence data. Additive to the slice-1 schema.

### Slice 4 — Clinical drugs (moiety/salt + strength + form)
The prescribable generic level (**RxNorm SCD** as the skeleton). Composition-tree leaf before product/local.

### Slice 5 — The curated interaction overlay (the moat)
Append-only, **signed** DDI overlay (severity + mechanism + management), attaching at moiety **and** class
level with edge inheritance. Layered by licence-safety: **ONC high-priority floor** (re-encoded from the
papers) → **SPL/DailyMed-mined** layer (ONSIDES-style, MIT precedent) → DDInter *if its licence confirms* →
drugref's own hand-curated overlay as the durable value-add. This is the append-only/signed half of the
hybrid store; institutionally-owned, never a volunteer wiki. Plus core pharmacology prose.

### Slice 6 — HTTP public API
The co-equal-consumer interface (any EHR/pharmacy/app; Cairn on the same footing). Deferred until there is
data worth serving; co-located Cairn reaches the schema directly meanwhile.

### Slice 7 — Cairn `inn_code` wiring (Tier-A consumer)
Fill the deliberately-nullable `inn_code` slot in Cairn's medication surface: autocomplete, coding a
previously-uncoded substance, DDI advisory — **overlay enrichment, never a wire change** on the Cairn side.

### Slice 8 — Local tier (Australia first)
Country-specific packaging/pricing: **PBS + TGA ARTG** (both CC BY, redistributable) as the shippable layer;
**AMT/SNOMED CT-AU** only as a node-local NCTS-licensed plug-in, never bundled. Same shape for other
jurisdictions (open regulatory registry bundled; national SNOMED extension licensed per node).

## Cross-cutting hardening (not a single slice)

- **Floor hardening** — close the `TRUNCATE` + table-owning-role bypass (row-level triggers don't cover them)
  via **RLS + privilege separation** — the full floor design §7 always envisioned (design §10 tension G).
  **Note the test-suite coupling**: `test_ingest_run.py` and `test_medrt_run.py` each `TRUNCATE` the drugref
  tables in an autouse fixture, because both orchestrators commit internally and so escape the `conn`
  fixture's rollback. Those fixtures depend on precisely the bypass this item closes, so hardening the floor
  must land together with a replacement isolation strategy (e.g. a privileged test role, or per-test schemas)
  or the suite stops being able to reset itself.
- **Production ingest** — batch-commit large real feeds; the verify-before-production checklist (real UNII
  headers/`INN_ID`; ChEBI/UNII/MED-RT licence deeds; grow the closed crosswalk + allow-list toward
  completeness). Note the moiety gate is the binding constraint on classification yield: MED-RT classifies
  6,012 ingredients, so class coverage grows with the registry, not with more MED-RT parsing.
- **`EPC`-adjacent MED-RT content not yet used** — `EXT` concepts, and the class→class `has_*` assertions
  (an EPC declaring its own mechanism/effect) which would let class-level knowledge inherit along the DAG.
- **Governance** — consider adding a CLAUDE.md (coding rules) and an ADR log as the design surface grows.

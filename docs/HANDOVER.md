# HANDOVER — drugref

> **Disposable working scaffolding, NOT a source of truth.** The canonical *what/why* lives in the design
> specs under [`docs/superpowers/specs/`](superpowers/specs/). If this file disagrees with a spec, the spec
> wins. Regenerate it at the end of every working session (nextsession rule 9).

## What drugref is

**drugref.org v2** — an open, co-equal **public-good drug-information service** (any EHR / pharmacy / app can
consume it; Cairn is its first client on the same public-API footing). A **global tier**
(jurisdiction-independent: identity, chemistry, classes, interactions) built first, then a **local tier**
(country-specific packaging/pricing; Australia/PBS first). Co-resides in a Cairn deployment's PostgreSQL
**or** runs standalone, but is **advisory reference data — never on Cairn's signed inter-node wire core**.

## ⇒ NEXT

**Merged to `main`:** slice 1 (identity spine, PR #1) · slice 2a (MED-RT classification, #9) · slice 2a.1
(source-neutral class registry, #10) · slice 2b (MeSH PA) · slice 5a (MED-RT CI_MoA/CI_PE) · the
foundation review · Plan A (open-question registry) · slice 8a (PBS localisation, #28) · Plan B
(DAG-descendant expansion, #32) · the identity-spine fix round (#34) · the Plan B review round (#38).

**In flight: slice 5b — MeSH-keyed contraindications**, branch `feat/slice-5b-mesh-contraindications`, **494 tests
green**, `ruff check` + `mkdocs build --strict` clean, `db/013`–`db/016` applied. Verified end-to-end against the real
releases on a scratch database — measured table in "Slice 5b" below. **The measurement corrected the spec in five
places**; the final whole-branch review then corrected four stale migration figures, hoisted `mesh.tree_parent_edges`
(ONE tree-number DAG rule, not two), added the missing worklist-clear test, and filed #40–#43.

**⇒ Issue-tracker hygiene — two issues are CLOSED on GitHub but NOT fixed**, both verified against the code,
not inferred. **[#35](https://github.com/cairn-ehr/drugref/issues/35)** was swept closed by `8ce55fb`'s commit
message although that commit records it as *filed, not fixed* — the same failure mode as #31, already
reopened once. **[#17](https://github.com/cairn-ehr/drugref/issues/17)** was closed by hand with only its
allow-list half landed. Reopen or fix them, but do not leave the tracker and these docs disagreeing.

**⇒ Next candidates:**

- **Slice 5b.2 — indications** (`may_treat`/`may_prevent`/`may_diagnose`/`induces`, ~18k assertions). The
  cheapest work available: it **reuses the condition registry unchanged** — same `condition` /
  `condition_parent` tables, same `mesh_concepts` M-code resolution, same closure — so it adds a relation
  and a vocabulary row, not a mechanism. Note the registry is scoped to what 5b's objects reach, so 5b.2's
  own objects extend it.
- **Slice 3 — composition tree (salts/esters/hydrates via GSRS).** Now triply motivated: the salt-strip
  heuristic is down to **0.03%** of bridge rows, [#33](https://github.com/cairn-ehr/drugref/issues/33)
  needs form→moiety relationships to close the MeSH CAS gap, and #30 is waiting on the same thing.
- **Plan C — the accumulation model.** Gated on 5b (§12-H); 5b has landed its contraindication half, so the
  gate is down for the CI axis.
- **[#31](https://github.com/cairn-ehr/drugref/issues/31) (reopened)** — swept closed by #32's merge although
  PR #32 records it as *filed, not fixed*; verified still unfixed on `main` at 6642ebc. Plus
  **[#39](https://github.com/cairn-ehr/drugref/issues/39)** and **#40–#43**, filed by this slice; see below.

## The identity-spine fix round (merged, #34) — #27, #17, #26

Spec: [moiety gate redesign](superpowers/specs/2026-07-27-drugref-moiety-gate-redesign.md) (full measurement tables there).
Four defects, each invisible to the committed fixtures, each found by running the real releases.

1. **`ingest/unii.py` read a `PT` column the release does not have ([#27](https://github.com/cairn-ehr/drugref/issues/27)).**
   It is `Display Name`. `row.get("PT") or ""` made that an empty label for all 168,046 rows, so a
   production run would have "succeeded" over an **entirely unlabelled** registry with a dead allow-list and
   a dead crosswalk (both name-keyed). Required columns are now **declared and checked**. *The bug was never
   the wrong name — names drift — it was `or ""` absorbing a structural mismatch.*
2. **The legacy allow-list was keyed on a display name ([#17](https://github.com/cairn-ehr/drugref/issues/17)).**
   Its flagship entry matched nothing (`MAGNESIUM SULFATE, UNSPECIFIED FORM`). Now **UNII**-keyed, which
   also *tightens* the gate.
3. **The gate excluded amoxicillin and morphine ([#26](https://github.com/cairn-ehr/drugref/issues/26)).**
   `INN_ID` is a sparse cross-reference, not a has-INN flag. Now
   `INN_ID | USAN_ID | (RXCUI & drug-like SUBSTANCE_TYPE) | legacy allow-list`. **The asymmetry is the
   design:** a strong signal admits outright, whatever the type — uniform type-filtering was measured and
   rejected because it deletes heparin, enoxaparin, protamine and 346 gene/cell therapies. **Strictly
   monotone**, pinned by a test, because `moiety_uuid` is immortal. `db/011 moiety_admission` records the
   admitting evidence as a rebuildable projection; `gate.admission_signals` answers "admitted?" and "on what
   evidence?" in one call so the two cannot drift.
4. **The gate fix changed NOTHING until the bridge was fixed too.** `run.py` writes an `INN` claim only
   `if cand.has_inn` and the PBS bridge indexed those claims, so newly-admitted moieties were invisible to it.
   Indexing `display_name` instead is lossless (all 12,588 INN claims equal it, zero mismatches).

**Measured (UNII 26Feb2026, PBS 2026-07, MED-RT 2026.07.06):** moieties 12,591 → **19,438** · PBS bridged
85.5% → **92.4%** (exactly slice 8a's ceiling) · PBS unmatched components 3,140 → **347** · salt-strip rows
149 → **5** (0.03%) · MED-RT classified moieties 2,066 → **3,875** · memberships 10,562 → **18,639** ·
populated CI rules 331 → **635** · `ddi_candidate_pair` 6,402 → **21,664**. Admission evidence: `INN_ID`
12,588 · `RXCUI` 8,694 · `USAN_ID` 5,404 · `LEGACY_ALLOWLIST` 4 — **5,227 rest on `RXCUI` alone**, the weakest
evidence and the natural head of a #19 worklist. **Audit note:** the old-gate arm reproduced Plan B's recorded
6,395 pairs (6,402 here, the two extra being the allow-list re-key), so the before/after columns are the same
instrument read twice.

**Residuals, stated not hidden:** 4,453 records carry both `RXCUI` and `DAILYMED` yet are rejected (3,015
botanicals/allergens, 821 excipient polymers, 600 mixtures); genuine tail misses (**pancrelipase**, **sodium
polystyrene sulfonate**) are allow-list candidates and are **not a new loss**. And
[#33](https://github.com/cairn-ehr/drugref/issues/33): MeSH keys D008278 by the anhydrous/heptahydrate CAS while
drugref's moiety is the unspecified form — counted, not dropped; closed by slice 3. Do **not** "fix" it by
allow-listing the hydrate UNIIs.

**Every fixture is now extracted from a real release** (`make_unii_subset.py` selects **by UNII**; the name is what
drifted). The old hand-written one invented an `INN_ID` for acetaminophen, a CAS for magnesium sulfate and a UNII of
`QCM` for microcrystalline cellulose. **That was the last hand-written fixture.**

## Plan B — DAG-descendant expansion (merged, #32 + the #38 review round)

Design: §3.2 / §7.1 / §11 step 2 of the [additive-effect & open-question
spec](superpowers/specs/2026-07-25-drugref-additive-effect-and-open-question-design.md) · plan: [Plan B
plan](superpowers/plans/2026-07-27-plan-b-descendant-expansion.md) (full evidence table).

**The defect.** `ddi_candidate_pair` joined **direct** `class_membership` only, so a contraindication naming a broad
class returned nothing for a drug filed solely under a descendant. `db/004` called that "the conservative default";
for a contraindication it reads backwards — **fewer rows is the harm direction**. Over 739 rules: `CI_MoA` 21.9%
hidden, `CI_PE` **85.2%** hidden, because MED-RT files membership at the most
*specific* node while writing rules against the *parent*.

**What `db/010` + `db/012` ship.** `ddi_candidate_pair` **descends the class DAG**, gaining `member_class` and
`is_direct`; **`WHERE is_direct` reproduces the pre-Plan-B row set exactly**, so a precision-sensitive consumer opts
out explicitly and one who *forgets* errs toward recall. The walk lives in **one** view, `ci_class_subtree` (`db/012`)
— `UNION` over `(root, class)`, not paths: cycle-safe and linear in a multi-parent DAG. Bounded by
**`class_expansion_policy`**, the deny-list held as **data** a pharmacist can diff, keyed `(source, source_code)`
because a migration runs before any class exists — **curator policy, not a projection; no ingest clears it.** Plus
`ci_axis.expands_descendants` per predicate, and `gap_unreviewed_expansion_root` (a fourth `gap_kind`) so the list
cannot rot silently.

**The deny-list: 11 denied, 3 allowed.** The `>20 descendant classes` heuristic found **exactly 14** CI object
classes — **all PE, not one MoA**. Size was only how they were *discovered*; the criterion is qualitative. Ten
are `<system> Activity Alteration` buckets; three are explicitly allowed (`Vasoconstriction` 54→119,
`Decreased Immunologically Active Molecule Activity` 35→327, `Increased Sympathetic Activity` 16→16). The
fourth, **`Increased Immunologic Activity`, is denied on its SUBTREE**: `Acquired Immunity [PE]` (1,109 drugs,
in effect every vaccine) sits beneath it.

**Two traps, both load-bearing.** (a) **`allow` is not the same as absent** — absent means *unreviewed*, which expands
**and** raises a question. (b) **The deny-list filters the RULE'S OBJECT CLASS, not the walk.** `Decreased Coagulation
Activity` is a descendant of the denied `Hematologic Activity Alteration`, so a traversal-barrier reading would delete
the single most important case Plan B exists to fix. Pinned by `test_a_descendant_of_a_denied_root_still_expands` —
**do not delete that test.**

**Measured** (terminology level): direct-only 20,462 → full expansion 58,288 → **shipped 29,687**, so the deny-list
keeps ~24% of the recall gain and removes ~76% of the fan-out. End-to-end on the real release:
`expansion_policy_unresolved` and `gap_unreviewed_expansion_root` both **empty**; pairs **6,395 vs 4,363 direct
(+46.6%)**, lower than the terminology figure because the moiety gate binds; headline case confirmed, a rule on
`Decreased Coagulation Activity [PE]` reaching **warfarin** plus 54 partners; lookup ~25 ms.

**The #38 review round (`db/012`) found no defect in the read path** — the `DISTINCT ON` grain is deterministic, the
walk is cycle-safe, the deny-list distinction is correctly guarded — but **five gaps between what `db/010`'s comments
legislate and its DDL does**, each re-issued rather than edited: the recursion became one view (it had shipped three
times); `gap_unreviewed_expansion_root` **joins `ci_axis`**, so it stops asking whether a class should expand when its
predicates cannot (latent then, **live at 5b**); `expansion_policy_unresolved` gained a consumer in `medrt_run`;
`class_expansion_policy.source` gained the CHECK every other `source` column has (`'MEDRT'` had inserted cleanly and
matched nothing forever); and two `COMMENT ON`s stopped overclaiming — `expands_descendants` is a recall-safe
*default*, not a gate, and the walk is **source-blind** (`class_parent`/`class_membership` carry no `source`, so a
transitive walk can cross vocabularies). **Row set unchanged.** **Filed, not fixed:** **#31** (denied-root rules with
no direct members yield no pair and no gap view reports it — pre-existing, not a regression), **#35**, **#36**,
**#37** — each detailed under "Open follow-ups".

## Current state, by layer

**Slice 1 — the identity spine.** Schema `drugref` (`ingest_run`, `substance_moiety`, `identity_claim`) + an
append-only row-level floor. Own immortal `moiety_uuid` (`UUIDv5` on UNII at first sighting, then
**pinned forever**; namespace `d07651ee-311d-552b-a97b-591219eb3ad3`), never keyed on a name. External IDs
are **append-only claims** (UNII, INN, RXNORM_IN, CAS, PUBCHEM_CID, INCHIKEY, CHEBI), so drugref doubles as
a public cross-walk. Membership gate (since #26) = **`INN_ID | USAN_ID | (RXCUI & drug-like
SUBSTANCE_TYPE)`** **or** the closed **UNII-keyed** legacy allow-list, with the admitting signal recorded
in `moiety_admission` (`db/011`). International-by-construction seeding: UNII (public domain) backbone, INN
display anchor, ChEBI (CC BY 4.0) chemistry, **RxNorm demoted to a claim** (an RxCUI read from the FDA file
is a gate signal, not an RxNorm ingest), a closed hand-curated USAN↔INN crosswalk. **Floor scope:** row-level
UPDATE/DELETE only — `TRUNCATE` and the owning role remain bypasses
([#2](https://github.com/cairn-ehr/drugref/issues/2)).

**Slice 2a / 2a.1 — the classification DAG.** `substance_class`, `class_parent`, `class_membership` seeded from
**MED-RT**: 3,634 classes, 3,961 edges (440 multi-parent), 27,540 memberships over 6,012 ingredients at the
terminology level — **18,639 rows survive the moiety gate** (re-confirmed this session; the two grains are routinely
confused). Class identity is immortal *by determinism* — `class_uuid = UUIDv5(CLASS_NAMESPACE, SOURCE + ":" + code)` —
so a rebuild re-derives it and no pin table is needed. Edges are **rebuildable projections**, outside slice 1's floor.
Membership joins via the `RXNORM_IN` claims slice 1 already records — no new bridge data. 2a.1 (`db/003`) generalised
the registry off its one authority (`source_code`/`published_code`, per-`(source, source_code)` uniqueness);
**existing MED-RT class UUIDs are unchanged, pinned by frozen literals** — the derivation is the join key of both edge
tables, so a drift would orphan every edge with no error anywhere. The stored `source` and the UUID key derive from
one canonicalisation (`ids.canonical_source`); extend that **and** `db/003`'s CHECK together when an authority lands
(`db/013` adds the same discipline for `condition.source`).

**Licence scoping is structural**, not a matter of intent: only MED-RT concepts are *defined* in the release
(SNOMED/MeSH appear solely as edge endpoints), so requiring both endpoints of every edge to be an ingested class is
what keeps unlicensed content out.

**Slice 2b — MeSH PA.** 568 PA class descriptors, their tree-number DAG and memberships, on the **same three tables**
(no schema change). `ingest/mesh.py` is a pure streaming (`iterparse`) parser; `ingest/mesh_run.py` holds the
**two-key bridge** — UNII-primary → CAS-fallback against slice-1 `identity_claim` rows, **no new external source** (5b
reuses the same bridge for `CI_ChemClass` objects). 10,505 member substances, **73% joinable** *(measured pre-#26; not
re-run since)*; unmatched counted, split no-key vs key-not-in-registry. Part of the residual is
[#33](https://github.com/cairn-ehr/drugref/issues/33): MeSH keys chemical records by **form-specific CAS**, which
cannot reach a moiety held as UNII's *unspecified form*.

**Slice 5a — the first interaction data.** `db/004` `class_contraindication` (rebuildable projection) + read-time pair
expansion. `db/006` replaced the comment-enforced CHECK↔CASE coupling with a **`ci_axis` table the vocabulary is a
foreign key into**, put `source` in the PK, and moved the clinical contract into `COMMENT ON`. **Candidate tier only**
— MED-RT does not track label updates, so nothing here auto-alerts.

**Slice 5b — MeSH-keyed contraindications** (`db/013`–`db/016`, in flight). A **third endpoint type**: a `condition`
is neither a moiety nor a `substance_class`, because nothing is a *member of* pregnancy and `substance_class`'s axis
vocabulary is entirely pharmacological. Hence `condition` + `condition_parent` (a rebuildable projection, MeSH-only,
DAG from tree-number nesting exactly as 2b built the PA DAG) holding the
**descendant closure** of the referenced conditions — without which a rule on Epilepsy would have nothing to
expand into and the feature would be inert while appearing to work. Two relations, because the objects are
different kinds of thing: `moiety_condition_contraindication` (drug→condition) and `moiety_contraindication`
(**drugref's first exact pairwise DDI data**). `condition_ci_axis` carries `expands_descendants` with **no
DEFAULT** — `db/012` finding 5 actually implemented. Read path `condition_subtree` +
`condition_contraindication_expanded`, the same shape as `db/012` over a second graph.

**Plan A — the open-question registry** (`db/007`, `db/008`). Coverage gaps are published as a **queryable register**
rather than hidden. **The hybrid split is the design:** `open_question` is a rebuildable projection re-derived every
ingest; curator intent (`question_state`), tier watermarks (`question_source_check`) and findings
(`question_evidence`) are **append-only**, keyed off an immortal `question_uuid` external tooling can cite — so a
rebuild can never erase a `withdrawn`. **Populated is per axis** (joins `ci_axis`). **Watermark, not closure:** only
`withdrawn` is terminal. **A closed gap carrying curator work is retired, not deleted** (`is_current`) — the curated
tables cascade from `open_question` *and* refuse `DELETE`, so deleting one aborts the whole ingest. Every orchestrator
rebuilds the register as its last step before commit. Five gap kinds now; measured against the real releases:
**unclassified_moiety 16,089 · unmatched_ingredient 2,140 · unresolved_ci_object 103 ·
unpopulated_contraindication 12 · unreviewed_expansion_root 0.**

**Slice 8a — PBS localisation, the local tier's first attachment.** `db/009` (three tables, a rebuildable projection
with **no** append-only floor, because a de-listed PBS item must be able to disappear); `ingest/pbs.py` (pure parser),
`local.py` (single writer), `ingest/pbs_run.py` (orchestrator), bridging PBS products to the global spine **by name
alone** — the only licence-clean join, since PBS carries no UNII/CAS/InChIKey. `local_product_uuid` is a pure function
of `(jurisdiction, source, source_code)`.

Measured against the real July-2026 release (14,840 items): the bridge reaches **13,719 = 92.4%**, **exactly the ceiling**
originally measured against all UNII substance names — so slice 8a's call was right and **the moiety gate, not the bridge,
was the binding constraint**, though it took *both* the gate fix and the display-name index to show it. The residual is
AU/INN-vs-USAN spelling divergence (cefalexin, ciclosporin — the deferred alias list **has earned its place**) and non-drugs
the gate correctly excludes.

**Licence posture — read before extending slice 8a.** Node-local plug-in only: drugref ships AGPL-3.0 ingest code and
schema, **never a PBS release**, with one stated exception — `tests/fixtures/pbs_items_subset.csv` commits 11 real
rows and is the thing that goes if [#25](https://github.com/cairn-ehr/drugref/issues/25) lands negative. ATC (WHO,
NC+ND) and AMT/SNOMED CT-AU are quarantined **structurally**: `items.csv` has no such column, the parser reads a fixed
allow-list, no table has anywhere to put them, and a test proves it by ingesting a fixture with **planted**
`atc_code`/`amt_code` columns and asserting neither reaches any drugref table (matched by **substring**).

## Slice 5b — MeSH-keyed contraindications (in flight)

Spec: [slice-5b](superpowers/specs/2026-07-28-drugref-slice-5b-mesh-contraindication-design.md). The contraindication half of
MED-RT's MeSH-keyed content — `CI_with` (drug→condition) and `CI_ChemClass`'s moiety arm (drug↔drug) — over a
**new condition registry**. **No new source** (MeSH's licence was cleared in 2b), so `NOTICE` gains no new attribution
— but its **MED-RT and MeSH scope statements were corrected**: the old text said drugref ingested MED-RT and RxNorm
namespaces only, which stopped being true here, and `medrt_subset.xml` carries MeSH concept names and ConceptUIs.

**Measured end-to-end against the real releases** (UNII 26Feb2026 → MED-RT 2026.07.06 → MeSH desc2026 + supp2026), on
a scratch database, live PG18. Upstream denominators, so the yield reads without the spec:
**`CI_with` 11,524 assertions / 708 objects · `CI_ChemClass` 1,939 / 360**.

| | spec §4.5 predicted | **measured** | |
|---|---:|---:|---|
| `condition` | 5,190 | **5,203** | 5,190 descriptors **+ 13 SCRs**; spec counted descriptors only |
| `condition_parent` | 7,157 | **7,157** | 1,690 children multi-parent, as predicted |
| `moiety_condition_contraindication` | 9,482 | **9,471** | −11: concept→record collapse, see below |
| — distinct subjects / objects | 2,900 / 667 | **2,900 / 641** | −26 objects: same collapse |
| `moiety_contraindication` | 1,443 | **1,442** | −1: one self-pair, `db/014` forbids it |
| `gap_unresolved_ci_object` rows | 108 | **103** | spec counted ConceptUIs; the worklist keys on records |
| — `sum(ci_rule_count)` | 405 | **405** | unaffected — an assertion is an assertion |
| `condition_contraindication_expanded` | — | **191,728** | `WHERE is_direct` → **9,471**, exactly the base |

`MeshCiSummary`: `conditions_registered=5203, conditions_added=5203, condition_parent_edges=7157,
condition_contraindications=9471, moiety_contraindications=1442, unmatched_subject_rxcuis=826, withheld_class_objects=103,
unresolved_object_codes=2, non_mesh_objects=2`. Whole chain **69 s** (UNII 8.6 s, MED-RT 3.4 s, 5b **56.8 s**) — the 5b leg
is two streaming passes over `desc2026`/`supp2026` plus row-at-a-time inserts
([#7](https://github.com/cairn-ehr/drugref/issues/7)/[#29](https://github.com/cairn-ehr/drugref/issues/29)).

**Five numbers moved, and every one is the spec being wrong, not the code.** Three share a single cause:
**the spec measured at the MeSH CONCEPT grain, and drugref stores at the RECORD grain.** MED-RT's `to_code`
is a ConceptUI; a MeSH record owns one or more concepts, and `mesh_concepts.py` exists precisely to keep the
two apart — keying a condition on the concept would split it into rows no rebuild could merge. Reconciled
exactly against the release:

- **9,482 → 9,471 rows.** 706 referenced `CI_with` concepts resolve to **677 records** (664 descriptors +
  13 SCRs). After the subject join, **9** (moiety, record) pairs are asserted through more than one concept
  each — bazedoxifene × `D001943` *Breast Neoplasms* via three, sotalol × `D051437` *Renal Insufficiency*
  via two — collapsing **11** duplicate rows onto the primary key. 9,482 − 11 = 9,471.
- **667 → 641 objects.** The same 667 surviving concepts sit on 641 records; 24 records absorb 26 surplus
  concepts. 667 − 26 = 641.
- **108 → 103 withheld objects.** Five records are each named by two withheld concepts (`D000701` Analgesics
  Opioid, `D001569` Benzodiazepines, `D006993` Hypnotics and Sedatives, `D010406` "Penicillins"/"Penicillin",
  `D020902` Hypericum); one record is one curator decision. **Adjudicated twice; 103 is correct and the code
  stands** — do not "fix" it by keying the worklist on the concept. `db/013`–`db/016` were corrected in place
  *before merge* (the ledger binds a database, not the repo; immutability starts at merge). **The spec alone
  still carries 108**; its standing correction is the living record
  `docs-site/docs/decisions/withheld-chemical-class-contraindications.md`.

The other two have their own causes:
- **5,190 → 5,203 conditions.** The spec counted **descriptors only**. The registry also holds **13
  supplementary records**, which bear no tree numbers, so they never enter the closure and appear only as
  themselves. Both are right about different things — 5,190 is the *descriptor closure*, 5,203 is the
  *registry* — and `MeshCiSummary`'s docstring already says which is which.
- **1,443 → 1,442 pairs.** Exactly one self-pair — **tranylcypromine** (RxCUI 10734) against MeSH `D014191`
  *Tranylcypromine* — which `db/014`'s `moiety_contraindication_not_self` CHECK forbids and the orchestrator
  skips. MED-RT states these where a salt and its parent moiety collapse to one identity. The spec's §4.4
  count predates the constraint. A drug is not contraindicated with itself. 1,443 − 1 = 1,442.

**The two headline clinical checks, confirmed:**

- **Epilepsy (`D004827`) reaches its descendants.** 14 direct rows fan out to **378** over **27** conditions
  — bethanechol, clozapine, cycloserine, doxapram, maprotiline, mefloquine and metoclopramide now reach a
  patient coded *Epilepsy, Temporal Lobe*, *Complex Partial*, *Frontal Lobe*, *Reflex*, *Post-Traumatic*…
  A filtered lookup on a leaf condition costs **~10 ms**, against Plan B's 25 ms on the class DAG.
- **Pregnancy + lactation: 615 rows** (`D011247` **549**, `D007774` **66**) — the case that named
  `moiety_condition_contraindication` rather than `drug_disease_*`. A `drug_disease_` table would have
  filed the release's most clinically consequential contraindication axis as a category error.

**Deferred, deliberately: `CI_ChemClass`'s class arm** — 405 assertions over 103 objects, **withheld, not dropped**:
expanding a rule on Sulfonamides (36 rules) over MeSH's *structural* chemical tree reaches 61 moieties including
bendroflumethiazide and bosentan, the discredited sulfa cross-reactivity inference, and only 8.3% of these objects
have any `has_SC` member so that route cannot fill the gap either. Published instead as `gap_unresolved_ci_object` + a
fifth `gap_kind`, one row per object with its rule count — Plan B's precedent; full argument in the docs-site decision
record. Worklist head: Sulfonamides 36, Hypericum 32, Sulfites 24, Barbiturates 13, Ergotamines/Penicillins 12.

**The source-blind walk stays LATENT — 5b does NOT end it**, and ROADMAP's old claim that it would was retracted in
the design round. 5b registers **no** MeSH chemical class in `substance_class` (the class arm is deferred) and
conditions live in their own tables with their own MeSH-only DAG, so no rule yet expands over another authority's
edges. It goes live when `has_SC` or the class arm lands. (`has_SC` is itself unbuilt: 3,632 assertions, **248
targeting MED-RT itself**, needing no bridge.)

## What the upstream documentation got wrong (verified against the real releases)

Each would be a silent, plausible bug invisible to a hand-written fixture — which is why every fixture is extracted from a
real release by a committed, re-runnable extractor. **MED-RT:** `Parent Of` runs parent →
**child**, not the reverse; `[HC]` concepts are the 26 **alphabetical navigation bins** (`"A
[Preparations]"`), 18,450 of 21,058 class→ingredient edges; EPC membership is licence-clean and
**hierarchical**, not routed through SNOMED/MeSH. **MeSH:** Descriptors **DO** carry UNIIs in
`RegistryNumber` (aspirin D001241 = `R16CO5Y76E`), and a record may carry several — key extraction is
set-valued. **And the one 5b turned on:** MED-RT's MeSH `to_code` is a **ConceptUI** (`M0004868`), *not* a
DescriptorUI, in two shapes (legacy 8-char, modern 10-char — nothing keys off length). Resolving it against
`desc2026` + `supp2026` reaches **99.88%** of MeSH-keyed objects; the NDF-RT accessory crosswalk manages
85.0% and yields only a **name**, so it is rejected — a name is not a key.

## Architecture in one breath

- **Hybrid store** mirroring a Cairn node: **rebuildable projections** for ingested feeds (drop-and-rebuild,
  version-pinned, provenance-tagged via `ingest_run`) + an **append-only, signed overlay** for curated
  knowledge (the DDI moat — slice 5c, not built). `class_expansion_policy` is a third, small category:
  curator *policy*, edited in place, cleared by nothing.
- **Two orthogonal structures**: a **composition tree** (moiety → salt → clinical drug → product) and a
  **classification DAG** (class ⊂ class; moiety ∈ many classes). The curated overlay attaches to either and
  **inherits along the edges** — the key curation-economy lever. Plan B is the first read path that actually
  walks those edges; 5b adds a **third graph beside them**, the MeSH condition DAG — an *object* structure,
  not a subject one, since nothing is a member of a condition.
- **Substrate**: Python 3.12 + `uv`, `psycopg` v3, PostgreSQL ≥ 18. Advisory tier, **integrity in the DB**.

## How to run / test

```bash
uv sync
uv run pytest                      # unit tests run anywhere; DB-gated tests SKIP without a DSN
# 494 tests, of which 309 are DB-gated -- a run without this DSN passes (185 tests)
# while exercising none of the schema, floor, views or orchestrators:
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check .
```

CI (`.github/workflows/ci.yml`) runs the suite against a PostgreSQL 18 service container, and `conftest`
**fails rather than skips** when `CI` is set — so the DB layer can never go green by being skipped.

- **Schema:** `db/001` identity spine · `002` classification · `003` registry generalised · `004`
  contraindication projection · `005` supersession/floor hardening · `006` `ci_axis` + view contract · `007`
  question registry · `008` gap views · `009` local (PBS) tier · `010` descendant expansion · `011` moiety
  admission evidence · `012` expansion-policy review round (`ci_class_subtree`, axis-aware gate) · `013` MeSH
  condition registry · `014` the two 5b contraindication relations + `condition_ci_axis` +
  `ingest_unresolved_ci_object` · `015` condition read path · `016` `gap_unresolved_ci_object` + the fifth
  `gap_kind`. **Read the LATEST file that touches an object for its actual shape** — 002 still shows
  superseded MED-RT-specific columns, 004's relationship CHECK is replaced by 006's FK, and 006's
  `ddi_candidate_pair` is replaced by 010's.
- **Migrations are immutable once applied — and immutability starts at MERGE.** `apply_migrations` records
  each file's checksum in `drugref.schema_migration` and raises if an applied file changed, so altering a
  MERGED migration (*including* re-issuing a `COMMENT ON`) means a new `db/NNN_*.sql`. A migration still on an
  unmerged branch may be edited: the ledger binds a *database*, not the repo, and conftest's `_migrated`
  fixture drops and recreates the schema, so the suite re-applies it cleanly. `db/013`–`db/016` were each
  edited that way during 5b; verify with a full run after any such edit.
- **Code:** `src/drugref/{ids,claims,classes,conditions,db,interactions,local,questions}.py` +
  `src/drugref/ingest/{unii,gate,run,chebi,medrt,medrt_run,mesh,mesh_run,mesh_concepts,mesh_ci_run,pbs,pbs_run}.py`;
  seed data under `src/drugref/data/`; fixtures under `tests/fixtures/`.
  New in 5b: **`conditions.py`** (the single writer for the condition registry — upsert, per-source edge
  clear), **`ingest/mesh_concepts.py`** (pure/streaming: MeSH **ConceptUI → record** resolution, the
  descendant closure, the tree-number DAG) and **`ingest/mesh_ci_run.py`** (the orchestrator; reads two
  authorities — MED-RT states the rule, MeSH defines its object — and is the only writer).
- Current dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
- **Upstream feed files are NOT committed** (`downloads/` is gitignored):
  - **MED-RT** — [NCI EVS](https://evs.nci.nih.gov/ftp1/MED-RT/) (`Core_MEDRT_*_XML.zip`); regenerate the
    fixture with `python tests/fixtures/make_medrt_subset.py <xml> > tests/fixtures/medrt_subset.xml`
    (regeneration must keep the endpoint redaction — a test enforces it).
  - **MeSH** — [NLM](https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/): `desc2026.gz`,
    `supp2026.gz`, `pa2026.xml`. NLM throttles per connection hard; a segmented byte-range fetch beats it
    ~18×. Regenerate 2b's with `make_mesh_subset.py downloads tests/fixtures/`, and 5b's with
    `make_mesh_ci_subset.py downloads/mesh/desc2026.gz downloads/mesh/supp2026.gz tests/fixtures/`.
    **Regenerate 5b's AFTER `make_medrt_subset.py`**: its wanted set is read out of `medrt_subset.xml`,
    because the first hand-picked version described a world disjoint from the MED-RT fixture's — every CI
    object resolved to nothing while both files looked healthy alone.
  - **PBS** — the `?variant=3` query parameter is **required** or the server 404s; files are UTF-8 **with a
    BOM**, so open with `encoding='utf-8-sig'`:
    ```bash
    curl -L -o downloads/pbs-2026-07.zip \
      "https://www.pbs.gov.au/publication/schedule/2026/07/2026-07-01-PBS-API-CSV-files.zip?variant=3"
    ```
    Ingest reads **only** `items.csv`, per the licence quarantine. Regenerate with
    `python tests/fixtures/make_pbs_subset.py downloads/tables_as_csv/items.csv > tests/fixtures/pbs_items_subset.csv`.

## Open follow-ups (all filed as GitHub issues)

**Filed by the final 5b whole-branch review — not fixed, deliberately**
- [#40](https://github.com/cairn-ehr/drugref/issues/40) **MeSH `.gz` asymmetry** — `mesh_concepts._open`
  reads the gzipped files NLM publishes; `mesh._iter_records` (2b) does not, so `mesh_run` /
  `make_mesh_subset.py` need a manual gunzip the 5b pair does not, and the two regeneration commands below
  print as symmetric while 2b's names a directory the files are not in. Hoist ONE `_open`.
- [#41](https://github.com/cairn-ehr/drugref/issues/41) **`gap_unresolved_ci_object` groups on `object_code`
  alone — and `questions.py` hardcodes `'MESH:' || object_code` a SECOND time.** A future second
  `object_source` would collide in the view *and* mint a colliding `question_uuid`, which is append-only and
  externally citable. **Fix both sites or you fix half.** Needs a `db/017`.
- [#42](https://github.com/cairn-ehr/drugref/issues/42) **`resolve_concepts`' "descriptors win over SCRs"
  branch is untested** — its only uncovered branch; preferring the SCR would mint a different immortal
  `condition_uuid` and drop the condition out of the DAG. 5b.2 reuses the resolver unchanged.
- [#43](https://github.com/cairn-ehr/drugref/issues/43) **Duplicated ingest boilerplate** — `_checksum` in
  four orchestrators, six body-identical `clear_source_*` bodies. Pre-existing idiom 5b added to; it is
  exactly what made the missing worklist-clear assertion this review found so easy to introduce.

**Floor & identity**
- [#2](https://github.com/cairn-ehr/drugref/issues/2) **Floor hardening** — close the `TRUNCATE` +
  owner-role bypass via RLS + privilege separation. **Note the test-suite coupling:** `grep -l TRUNCATE
  tests/*.py` now finds **nine** modules (was seven; 5b added `test_mesh_ci_run.py`, and
  `test_moiety_admission.py` was missed in the earlier count), each truncating in an autouse fixture because
  their orchestrators commit internally and escape the `conn` fixture's rollback. Those fixtures depend on
  precisely the bypass this closes, so hardening must land with a replacement isolation strategy.
- [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality** — structural re-key by
  InChIKey, deferred.
- [#17](https://github.com/cairn-ehr/drugref/issues/17) **Remaining no-silent-drop gaps** — *half done*:
  the allow-list is now UNII-keyed (#34). Still live in the code: `ingest/mesh.py`'s `if not dui: continue`
  drops MeSH PA records with no `DescriptorUI` uncounted. **The issue is CLOSED on GitHub** (closed by hand
  2026-07-27) while the gap it names is still there — reopen it or fix it, but do not leave both.

**Ingest correctness (all found by measuring the real releases)**
- [#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms** — D008278 is
  keyed to the anhydrous/heptahydrate UNIIs while drugref's moiety is the unspecified form, so no key
  matches. Counted, not dropped. **Closed by slice 3.**
- [#5](https://github.com/cairn-ehr/drugref/issues/5) INN sourced from UNII's `Display Name`, not an
  authoritative WHO list. `UNII_Names_*.txt` carries `TYPE='of'` rows for 24,127 UNIIs and lists **both
  `ACETAMINOPHEN` and `PARACETAMOL`** — so an authoritative INN source may be derivable from a file drugref
  already downloads, possibly replacing the hand-curated crosswalk. Note `of` also covers excipients: a
  *name* source, not a membership signal.
- [#7](https://github.com/cairn-ehr/drugref/issues/7) / [#29](https://github.com/cairn-ehr/drugref/issues/29)
  **Row-at-a-time ingest** — MED-RT (~31k round trips, plus `ElementTree.parse` holding 45 MB) and PBS
  (~28k). `executemany`/`COPY` + batch commits + `iterparse`. **Slice 5b adds ~23k more** (5,203 conditions
  + 7,157 edges + 10,913 contraindication rows) and measured **56.8 s**, so it is now the slowest leg.
- [#39](https://github.com/cairn-ehr/drugref/issues/39) **`ingest_unmatched_ingredient` is rebuilt per
  `source`, but `medrt_run` and `mesh_ci_run` both write under `MED-RT`** and neither set contains the other
  (2,271 rows are `medrt_run`'s alone; 16 CI subjects are never classified, so it can never record them).
  `mesh_ci_run` therefore writes but never clears. **Two caveats are documented and TESTED, not solved:**
  order-dependence (a `medrt_run` drops the CI-only rows and cannot re-add them) and cross-run accumulation
  in the table. The `DISTINCT ON (rxcui)` gap view is unaffected. The fix is a writer discriminator.
- [#16](https://github.com/cairn-ehr/drugref/issues/16) **A crashed ingest leaves no trace** — the
  `ingest_run` row is written inside the run's own transaction, so a failure rolls it back. Needs a
  connection-ownership decision, and a CLI.
- [#30](https://github.com/cairn-ehr/drugref/issues/30) `strip_salt` drops only one trailing token —
  measure before building; slice 3 may supersede it.

**Interaction model**
- [#31](https://github.com/cairn-ehr/drugref/issues/31) **Denied-root rules with no direct members yield no
  pair, unreported** — Plan B's residue, pre-existing rather than a regression (above).
- [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is unpopulated** —
  filed as 41 of 739; `gap_unpopulated_contraindication` returns **12** against the real release (re-confirmed
  this session), Plan B's expansion having absorbed the rest. Re-measure before acting on the issue text.
  Still the highest-value curation worklist available — upstream vouching that the answer matters — and
  largely an **indexing loss, not a knowledge gap**: openFDA labels carry the statements (§3.5), which is
  why the cost ladder puts `openFDA-SPL` above `literature`.
- [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions** — decide before 5c builds on
  the pairwise shape. MED-RT does not assert the triple-whammy even pairwise.
- [#8](https://github.com/cairn-ehr/drugref/issues/8) **Class-level `has_*` assertions unused** (~756
  edges) — the other half of making the DAG carry knowledge, now that Plan B walks it.
- [#35](https://github.com/cairn-ehr/drugref/issues/35) **`class_expansion_policy` has no history** — a
  revised decision overwrites its own rationale, and unlike the `question_*` tables it has no rewrite trigger,
  on a table that gates recall. Plan C's append-only overlay is the fix. **CLOSED on GitHub but unfixed.**
- [#36](https://github.com/cairn-ehr/drugref/issues/36) **The discovery heuristic counts descendant classes,
  not reachable members** — `Increased Sympathetic Activity` spent a curator `allow` on a provable no-op
  (all 21 children empty). Changing the metric moves which roots get asked about, so it needs a curator and
  a re-measure, exactly as `db/010` says.
- [#37](https://github.com/cairn-ehr/drugref/issues/37) **The DAG is expanded unprunably on every query** —
  denied roots are walked then discarded and `WHERE is_direct` cannot push down, so the precision opt-out
  funds the expansion it throws away. The trap the issue records: restricting the *root set* is safe,
  restricting the *walk* deletes the coagulation case. **5b measured the "different size" it anticipated:**
  `condition_contraindication_expanded` is **191,728** rows over a **9,471**-row base (a 20× fan-out), yet a
  filtered lookup still costs **~10 ms** against Plan B's 25 ms. Not urgent; now measured, not guessed.

**Licence deeds (blockers before production, per rule 6)**
- [#6](https://github.com/cairn-ehr/drugref/issues/6) Re-confirm the MED-RT deed against the live NLM
  source-release doc (the distribution ships no licence file; NLM's doc was HTTP 502 at design time).
- [#25](https://github.com/cairn-ehr/drugref/issues/25) PBS redistribution — blocks bundling, not
  node-local ingest; needs written Dept-of-Health confirmation.

**Verify-before-production, generally:** re-run each parser against a full current release and re-confirm the
aggregate numbers. Fixtures from a real release are not the same thing — 5b found five spec errors that way,
each invisible to a green suite.

## Repo facts

- GitHub `cairn-ehr/drugref` · default branch `main` · **AGPL-3.0** · attribution in `NOTICE`. Slice 5b adds
  **no new source**, but corrected the MED-RT/MeSH *scope* claims already there (see "Slice 5b" above). Coding
  rules live in CLAUDE.md and the `nextsession` skill.
- Public docs site: `docs-site/` (MkDocs Material) → `docs.drugref.org`, deployed by
  `.github/workflows/docs.yml`; `mkdocs build --strict` is its test. Its **Design decisions** section holds
  *living* records (revised in place, reversed ones removed) and is **where a standing correction to an
  artefact that genuinely cannot be edited — an immutable spec — goes**; the 5b erratum now corrects the spec
  ALONE. Specs/HANDOVER/ROADMAP are **not** published.

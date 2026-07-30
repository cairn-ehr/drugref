# HANDOVER — drugref

> **Disposable working scaffolding, NOT a source of truth.** The canonical *what/why* lives in the design
> specs under [`docs/superpowers/specs/`](superpowers/specs/). If this file disagrees with a spec, the spec
> wins. Regenerate it at the end of every working session (nextsession rule 9).

## What drugref is

CLAUDE.md carries the standing summary. What it does not say: drugref is **co-equal public-good
infrastructure** (any EHR / pharmacy / app consumes it; Cairn is its first client on the same public-API
footing), the **global tier** is built before the **local tier** (country packaging/pricing; Australia/PBS
first), and it co-resides in a Cairn deployment's PostgreSQL **or** runs standalone.

## ⇒ NEXT

**Merged to `main`:** slice 1 (identity spine, PR #1) · slice 2a (MED-RT classification, #9) · slice 2a.1
(source-neutral class registry, #10) · slice 2b (MeSH PA) · slice 5a (MED-RT CI_MoA/CI_PE) · the
foundation review · Plan A (open-question registry) · slice 8a (PBS localisation, #28) · Plan B
(DAG-descendant expansion, #32) · the identity-spine fix round (#34) · the Plan B review round (#38) ·
slice 5b — MeSH-keyed contraindications (#44) · the post-5b debt round (#46).

**568 tests green** (358 DB-gated, 210 without a DSN), `ruff check` + `mkdocs build --strict` clean, `db/001`–`db/018`.
Slice 5b was verified end-to-end against the real releases on a scratch database — measured table in "Slice 5b"
below. **The measurement corrected the spec in five places**; the final whole-branch review then corrected four
stale migration figures, hoisted `mesh.tree_parent_edges` (ONE tree-number DAG rule, not two), added the missing
worklist-clear test, and filed #40–#43 + #45.

**⇒ Issue-tracker hygiene — the sweep-closed-but-unfixed pattern has now happened three times** (#31, #35,
#40), each time because a commit or PR body that says *filed, not fixed* still named the number. #31 and #35
were reopened; **#17 and #40 were closed-but-unfixed and are now genuinely fixed** (this round), so the
tracker is true again. **A number in a commit message is a claim about the code — verify it before writing
it**, and prefer prose that cannot be parsed as a closing keyword when filing rather than fixing.

**The post-5b debt round is MERGED** ([PR #46](https://github.com/cairn-ehr/drugref/pull/46)) — #40, #17,
#42, #41, #43 fixed, `db/017` added, and the whole chain **re-verified end-to-end against the real releases**
(UNII 26Feb2026 → MED-RT 2026.07.06 → MeSH desc/supp/pa 2026, gzipped, on a scratch database). Every slice-5b
headline figure reproduced exactly; the previously-unmeasured `object_kind` split and a corrected slice-2b
joinability figure are recorded below.

**In flight: the interaction debt round**, branch `fix/interaction-debt-round`, **[PR
#49](https://github.com/cairn-ehr/drugref/pull/49)** — **#39, #31 and #45 fixed**,
`db/018` added, **568 tests green**, re-verified against the real releases both before AND after its review
round (the review changed a published figure — see below). Residue filed as #47 and #48.

**⇒ Next candidates:**

- **Slice 5b.2 — indications** (`may_treat`/`may_prevent`/`may_diagnose`/`induces`, ~18k assertions). The
  cheapest work available: it **reuses the condition registry unchanged** — same `condition` /
  `condition_parent` tables, same `mesh_concepts` M-code resolution, same closure — so it adds a relation
  and a vocabulary row, not a mechanism. Note the registry is scoped to what 5b's objects reach, so 5b.2's
  own objects extend it. It also inherits `contraindications_for_condition`'s shape for its own read path,
  and is what would have made #45 bite.
- **Slice 3 — composition tree (salts/esters/hydrates via GSRS).** Now triply motivated: the salt-strip
  heuristic is down to **0.03%** of bridge rows, [#33](https://github.com/cairn-ehr/drugref/issues/33)
  needs form→moiety relationships to close the MeSH CAS gap, and #30 is waiting on the same thing.
- **Plan C — the accumulation model.** Gated on 5b (§12-H); 5b has landed its contraindication half, so the
  gate is down for the CI axis. Also the stated fix for **#35** (`class_expansion_policy` has no history).

## Two merged rounds, compressed — the traps only

Full narrative and measurement tables live in the specs; what survives here is what a future change can still
break.

**The identity-spine fix round (#34: #27, #17, #26).** Spec: [moiety gate
redesign](superpowers/specs/2026-07-27-drugref-moiety-gate-redesign.md). Four defects, every one invisible to
the committed fixtures and found only by running the real releases: a `PT` column the release does not have
(it is `Display Name`, and `or ""` absorbed the mismatch across all 168,046 rows); a name-keyed allow-list
whose flagship entry matched nothing; a gate resting on `INN_ID` as if it were a has-INN flag, which excluded
amoxicillin and morphine; and a bridge that made the gate fix move **nothing** until it stopped indexing
`INN` claims. Measured: moieties 12,591 → **19,438**, PBS bridge 85.5% → **92.4%**, MED-RT memberships
10,562 → **18,639**, `ddi_candidate_pair` 6,402 → **21,664**. Still load-bearing: the gate is `INN_ID | USAN_ID | (RXCUI & drug-like SUBSTANCE_TYPE) | UNII allow-list`,
and **the asymmetry is the design** — uniform type-filtering was measured and rejected because it deletes
heparin, enoxaparin, protamine and 346 gene/cell therapies. **Strictly monotone, pinned by a test**, because
`moiety_uuid` is immortal. **5,227 moieties rest on `RXCUI` alone** — the weakest evidence, and the natural
head of a #19 worklist. Do **not** "fix" [#33](https://github.com/cairn-ehr/drugref/issues/33) by
allow-listing the hydrate UNIIs. Every fixture is extracted from a real release; the last hand-written one
invented an `INN_ID`, a CAS and a UNII of `QCM`.

**Plan B — DAG-descendant expansion (#32 + the #38 review round, `db/010` + `db/012`).** Design: §3.2 / §7.1 /
§11 of the [additive-effect
spec](superpowers/specs/2026-07-25-drugref-additive-effect-and-open-question-design.md). `ddi_candidate_pair`
joined **direct** membership only, hiding 21.9% of `CI_MoA` and **85.2%** of `CI_PE` pairs, because MED-RT
files membership at the most *specific* node and writes rules against the *parent* — and **for a
contraindication, fewer rows is the harm direction**. It now descends the DAG through one cycle-safe view
(`ci_class_subtree`), bounded by `class_expansion_policy`: a deny-list held as **data** a pharmacist can
diff, **curator policy rather than a projection, cleared by no ingest**. 11 denied, 3 allowed, all 14 of them
PE. Measured 6,395 pairs vs 4,363 direct (+46.6%); a rule on `Decreased Coagulation Activity [PE]` reaches
warfarin plus 54 partners; ~25 ms.

**Three traps, all load-bearing.** (a) **`WHERE is_direct` reproduces the pre-Plan-B row set exactly**, so a
consumer who forgets the filter errs toward recall. (b) **`allow` is not the same as absent** — absent means
*unreviewed*, which expands **and** raises a question. (c) **The deny-list filters the RULE'S OBJECT CLASS,
never the walk**: `Decreased Coagulation Activity` is a descendant of a denied root, so a traversal-barrier
reading deletes the single most important case Plan B exists to fix. Pinned by
`test_a_descendant_of_a_denied_root_still_expands` — **do not delete that test.** The #38 round found no
defect in the read path, only five gaps between `db/010`'s comments and its DDL; row set unchanged. Residue:
#35, #36, #37 (#31 closed by the round below).

## The interaction debt round (#39, #31, #45 — `db/018`, in flight)

**Every fix was measured against the real releases first, and TWO of the three issue texts proved stale.** The
whole chain was then re-run end-to-end on a scratch database (63 s) and **every slice-5b headline figure
reproduced exactly** (19,438 moieties · 5,203 conditions · 7,157 edges · 9,471 · 1,442 · 96+7 withheld · 1
self-pair).

**#39 — one table, two writers, no owner.** `ingest_unmatched_ingredient` was cleared per `ingest_run.source`
while `medrt_run` (ingredients MED-RT *classifies*) and `mesh_ci_run` (*subjects* of a contraindication) both
run under `MED-RT`. `reason` in {`classification`, `contraindication`} now scopes each clear, the PK is
`(ingest_run, reason, rxcui)`, and `db.clear_source_tables` grew an opt-in `match=` narrowing so the DELETE
still lives in exactly one place (#43's rule). **NOT NULL with NO DEFAULT** — the value scopes a DELETE, so a
writer that does not declare its bucket must fail. Measured: **2,137 classification + 826 contraindication**
rows, and a *later* `medrt_run` now leaves all 826 standing (it used to delete them and could not re-add 16).
`gap_unmatched_ingredient` is unchanged at **2,140** — the view is `DISTINCT ON (rxcui)`, so the grain a
curator reads never moved; it is re-issued only to add `reason` to its tie-break, which the wider key had left
under-determined (unreachable until a writer owns both buckets, which is what #47 proposes). **The invariant a
third writer must preserve: ONE WRITER PER `(source, reason)`** — add a value, never share one (that is #47).

**#31 — dead rules nothing reported, and a second cause found while measuring the first.**
`gap_dead_by_expansion_policy` (sixth `gap_kind`) publishes a contraindication whose object class is **denied**
expansion, holds no direct **partner** on the rule's axis, and *does* have one below — so the rule reaches
nobody. Then the second cause: `gap_unpopulated_contraindication` counted the rule's **own subject** as a
member, although `ddi_candidate_pair` excludes it — so `acetohydroxamic acid` → `Urease Inhibitors [MoA]`,
whose only member is acetohydroxamic acid, was dead and silent. That view goes **12 → 13 classes / 38 → 39
dead rules**.

**THE REVIEW OF THIS ROUND FOUND THE SAME DEFECT IN THE NEW VIEW, and that is the lesson worth keeping.** The
first draft carried the reach measure as two near-identical CTEs, `populated` and `reachable` — and only
`populated` learned that a rule's own subject is not a partner. So a denied class whose **only direct member
was its rule's subject** was dead and reported by *nothing* (exactly what #31 was filed about), while a class
whose whole subtree held only the subject was reported by *both*. **One quantity stated twice is a quantity
that will disagree** — db/006's rule, which this migration's own comments invoke three times. The measure is
now **one view, `ci_rule_partner_reach`** (`subtree_partner_count`, `direct_partner_count`, subject excluded
from both), and the two gap views are complementary filters on one column — `= 0` and `> 0` — so the partition
is true by construction rather than by assertion. Both shapes are pinned by test.

**Re-measured after the fix, on the same three releases** (#50, closed): still **ONE class**,
`Endocrine Activity Alteration [PE]`, 1 rule — neither shape the subject exclusion changes occurs anywhere in
this release, so no class is gained and none moves to the other view — but the cost is **299, not 300**. The
rule's subject is **clomiphene**, and clomiphene is itself filed under Endocrine Activity Alteration; it was
being counted as a drug the deny holds back from a rule it can never pair with. `12 → 13 / 38 → 39`,
`unmatched_ingredient 2,140` and `ddi_candidate_pair 21,664` are all unchanged, and **no class appears in both
views on the real data**. #31 lists two classes; the other gained 7 direct members in the #34 gate fix —
**re-measure before quoting an issue.**

**#45 — the same answer from the patient's end.** `contraindications_for_condition(uuid)` walks **UP** from
the patient's condition instead of down from all 641 roots: **0.7–0.9 ms against 9–10 ms**, ~13×, because the
view's recursion builds 11,512 subtree rows to return 15 and Postgres cannot push a predicate into a recursive
CTE. A materialised view was rejected — it needs a REFRESH in every writer and a new way to be silently stale.
**The view stays** for whole-set access and `WHERE is_direct`. **Two implementations of one expansion rule is
the danger**, so equivalence is pinned by test *and* was checked on the real release: 200 conditions, 4,935
rows, **zero difference in either direction**.

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
reuses the same bridge for `CI_ChemClass` objects). **Re-measured post-#26 against the real release** (desc/supp read
gzipped, 38 s): 568 classes · 549 DAG edges · 35,793 membership assertions over **10,506** distinct member substances
→ **22,179 has_PA rows**. **The old "73% joinable" line was ambiguous and is corrected:** 72.8% (7,650) carry an
identity KEY at all, but only **4,269 = 40.6% reach a gated-in moiety** — 2,856 have no key and 3,381 have a key no
moiety carries. Both are counted, never dropped. Part of the residual is
[#33](https://github.com/cairn-ehr/drugref/issues/33): MeSH keys chemical records by **form-specific CAS**, which
cannot reach a moiety held as UNII's *unspecified form*.

**Slice 5a — the first interaction data.** `db/004` `class_contraindication` (rebuildable projection) + read-time pair
expansion. `db/006` replaced the comment-enforced CHECK↔CASE coupling with a **`ci_axis` table the vocabulary is a
foreign key into**, put `source` in the PK, and moved the clinical contract into `COMMENT ON`. **Candidate tier only**
— MED-RT does not track label updates, so nothing here auto-alerts.

**Slice 5b — MeSH-keyed contraindications** (`db/013`–`db/016`, merged #44). A **third endpoint type**: a `condition`
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
rebuilds the register as its last step before commit. **Six** gap kinds now; measured against the real releases:
**unclassified_moiety 16,089 · unmatched_ingredient 2,140 · unresolved_ci_object 103 ·
unpopulated_contraindication 13 · dead_by_expansion_policy 1 · unreviewed_expansion_root 0.**

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

## Slice 5b — MeSH-keyed contraindications (merged, #44)

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

**The `object_kind` split, MEASURED:** **`CHEMICAL_CLASS` 96 objects / 386 rules · `UNREGISTERED_SUBSTANCE`
7 objects / 19 rules** — 103 / 405, so the worklist total is unchanged and the older
`withheld_class_objects=103` is the SUM of the two fields the split created. **The 7 are the cheaper half**:
registering those moieties is ordinary coverage work, while the 96 need a curator ruling on tree expansion.

**Five numbers moved, and every one is the spec being wrong, not the code.** Three share ONE cause: **the
spec measured at the MeSH CONCEPT grain, drugref stores at the RECORD grain** (MED-RT's `to_code` is a
ConceptUI; `mesh_concepts.py` exists to keep the two apart, and keying a condition on the concept would split
it into rows no rebuild could merge). 9,482 → **9,471** rows (11 duplicates collapse onto the PK, e.g.
bazedoxifene × *Breast Neoplasms* via three concepts); 667 → **641** objects (24 records absorb 26 surplus
concepts); 108 → **103** withheld objects (five records each named by two withheld concepts). **103 was
adjudicated twice and is correct — do not "fix" it by keying the worklist on the concept.** The spec alone
still carries 108; its standing correction is the living record
`docs-site/docs/decisions/withheld-chemical-class-contraindications.md`.
The other two: 5,190 → **5,203** conditions (the spec counted descriptors only; the registry also holds 13
tree-less supplementary records, so 5,190 is the *descriptor closure* and 5,203 the *registry*), and
1,443 → **1,442** pairs (one self-pair, tranylcypromine, which `db/014` forbids — a drug is not
contraindicated with itself).

**The two headline clinical checks, confirmed:** **Epilepsy (`D004827`)** fans 14 direct rows out to **378**
over **27** conditions, so clozapine, maprotiline, mefloquine and metoclopramide now reach a patient coded
*Temporal Lobe* / *Complex Partial* / *Post-Traumatic*; and **pregnancy + lactation carry 615 rows**
(`D011247` 549, `D007774` 66) — the case that named `moiety_condition_contraindication` rather than
`drug_disease_*`, which would have filed the release's most consequential CI axis as a category error.

**Deferred, deliberately: `CI_ChemClass`'s class arm** — 405 assertions over 103 objects, **withheld, not
dropped**: expanding a rule on Sulfonamides (36 rules) over MeSH's *structural* chemical tree reaches 61 moieties
including bendroflumethiazide and bosentan, the discredited sulfa cross-reactivity inference, and only 8.3% of
these objects have any `has_SC` member so that route cannot fill the gap either. Published instead as
`gap_unresolved_ci_object`, one row per object with its rule count — full argument in the docs-site decision record.
Worklist head: Sulfonamides 36, Hypericum 32, Sulfites 24, Barbiturates 13, Ergotamines/Penicillins 12.

**The source-blind walk stays LATENT — 5b does NOT end it.** 5b registers **no** MeSH chemical class in
`substance_class` and conditions live in their own MeSH-only DAG, so no rule yet expands over another authority's
edges. It goes live when `has_SC` or the class arm lands. (`has_SC` is unbuilt: 3,632 assertions, **248 targeting
MED-RT itself**, needing no bridge.)

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
# 568 tests, of which 358 are DB-gated -- a run without this DSN passes (210 tests)
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
  `gap_kind` · `017` that view re-keyed on `(upper(object_source), object_code)` (#41) · `018` the interaction
  debt round (`ingest_unmatched_ingredient.reason` + `gap_unmatched_ingredient`'s widened tie-break;
  `ci_rule_partner_reach` and the two gap views that filter it — `gap_dead_by_expansion_policy` and a
  subject-aware `gap_unpopulated_contraindication`; `contraindications_for_condition`). **Read the LATEST file that
  touches an object for its actual shape** — 002 still shows superseded MED-RT-specific columns, 004's
  relationship CHECK is replaced by 006's FK, 006's `ddi_candidate_pair` is replaced by 010's, 016's
  `gap_unresolved_ci_object` by 017's, and 008's/012's `gap_unpopulated_contraindication` — and 008's
  `gap_unmatched_ingredient` — by 018's.
- **Migrations are immutable once applied — and immutability starts at MERGE.** `apply_migrations` records
  each file's checksum in `drugref.schema_migration` and raises if an applied file changed, so altering a
  MERGED migration (*including* re-issuing a `COMMENT ON`) means a new `db/NNN_*.sql`. A migration still on an
  unmerged branch may be edited: the ledger binds a *database*, not the repo, and conftest's `_migrated`
  fixture drops and recreates the schema, so the suite re-applies it cleanly. `db/013`–`db/016` were each
  edited that way during 5b; verify with a full run after any such edit.
- **Code:** `src/drugref/{ids,claims,classes,conditions,db,interactions,local,questions}.py` +
  `src/drugref/ingest/{unii,gate,run,checksum,chebi,medrt,medrt_run,mesh,mesh_run,mesh_concepts,mesh_ci_run,pbs,pbs_run}.py`;
  seed data under `src/drugref/data/`; fixtures under `tests/fixtures/`.
  From 5b: **`conditions.py`** (the single writer for the condition registry),
  **`ingest/mesh_concepts.py`** (pure/streaming: MeSH **ConceptUI → record** resolution, the descendant
  closure, the tree-number DAG) and **`ingest/mesh_ci_run.py`** (the orchestrator; reads two authorities —
  MED-RT states the rule, MeSH defines its object — and is the only writer).
  **Three things now live in exactly one place, and a test pins each**: `mesh.iter_records` (the gz-aware
  MeSH reader), `ingest/checksum.py` (`checksum(*paths)`, every orchestrator's provenance digest) and
  `db.clear_source_tables` (the per-source clear the rebuildable-projection model rests on).
- Current dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
- **Upstream feed files are NOT committed** (`downloads/` is gitignored):
  - **MED-RT** — [NCI EVS](https://evs.nci.nih.gov/ftp1/MED-RT/) (`Core_MEDRT_*_XML.zip`); regenerate the
    fixture with `python tests/fixtures/make_medrt_subset.py <xml> > tests/fixtures/medrt_subset.xml`
    (regeneration must keep the endpoint redaction — a test enforces it).
  - **MeSH** — [NLM](https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/): `desc2026.gz`,
    `supp2026.gz`, `pa2026.xml`. NLM throttles per connection hard; a segmented byte-range fetch beats it
    ~18×. **No gunzip step for either half since #40** — both readers take the files as served. Regenerate
    2b's with `make_mesh_subset.py downloads/mesh tests/fixtures/`, and 5b's with
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

**Filed by the interaction debt round**
- [#47](https://github.com/cairn-ehr/drugref/issues/47) **`medrt_run` counts its unmatched CI subjects but
  never persists them** — 99 on the real release, and **all 99 already reach the worklist through another
  writer's rows**, so nothing is lost *today*. That is a property of this release, not a guarantee. The fix
  needs a THIRD `reason` value, never a shared one (`db/018`'s one-writer-per-`(source, reason)` invariant).
- [#48](https://github.com/cairn-ehr/drugref/issues/48) **A non-expanding predicate with no direct member is
  equally dead and is deliberately not reported** by `gap_dead_by_expansion_policy` — allowing expansion
  could not revive it, so it is a `ci_axis` question with a different remedy and wants its own view.
  Unreachable today (both MED-RT predicates expand); **5b.2 declaring a predicate non-expanding is what makes
  it live**.

**Closed by the interaction debt round** (`db/018`, verified against the real releases): **#50** (the
post-review re-measurement: 300 → **299**, clomiphene is its own rule's subject; every other figure held),
**#39** (the
`reason` discriminator), **#31** (`gap_dead_by_expansion_policy`, plus the subject-aware population test that
found `Urease Inhibitors`), **#45** (`contraindications_for_condition`, ~13× on the patient lookup).

**Closed by the post-5b debt round** (each verified against the code before closing, since
three issues had already been swept closed while unfixed)
- **#40 MeSH `.gz` asymmetry** — `mesh.open_release_file` + `mesh.iter_records` are now the ONE reader;
  `mesh_concepts` imports them. `make_mesh_subset.py` also hardcoded `<stem>.xml` while NLM names the
  compressed files `desc2026.gz`, so the documented regeneration command found nothing against a real
  release; `_release_file` resolves the name and raises rather than writing a fixture from an unread file.
  The extractors keep their own two-line `_open` **on purpose** — all five are stdlib-only and runnable
  without drugref installed, which `make_medrt_subset.py`'s redaction test depends on.
- **#17 last silent refusal** — `MeshSummary.pa_records_without_descriptor`.
- **#42 descriptor-wins tie-break** — pinned on controlled input, because the release cannot exercise it:
  **desc2026 defines 61,794 ConceptUIs, supp2026 402,107, and 0 appear in both.** So the branch is a guard
  against a release whose partition changes, not a live case; verified by mutation (reversing the read order
  fails that test and only that test).
- **#41 namespace collision, both sites** — `db/017` groups on `(upper(object_source), object_code)`;
  `questions.py` uses `upper(object_source) || ':' || object_code`, which preserves every existing MeSH
  `question_uuid` bit-for-bit. `relationship` is **aggregated, not grouped on**: the grain is per object
  because the decision is, and grouping by it without keying on it would make two view rows mint one UUID.
  **THE VIEW'S GRAIN MUST BE THE gap_key'S GRAIN, and the first cut of this fix broke that** — it grouped
  on the *stored* spelling while the key upper()s it, so `'MeSH'` and `'MESH'` were two view rows folding to
  one `question_uuid`: the same collision one case narrower, found by the PR review and pinned by
  `test_the_views_grain_is_the_gap_keys_grain`. Case-variants now **merge** (one namespace, counts summed);
  different namespaces never do. The two `upper()`s are one *rule* stated twice, not #41's two encodings of
  one *value* — they cannot disagree, and Postgres refuses the view outright if either is dropped alone.
- **#43 duplicated boilerplate** — one `ingest/checksum.py`, one `db.clear_source_tables`; six writers keep
  their named wrapper and declare a table tuple, each **restated independently** in
  `tests/test_source_clear_contract.py` so a dropped table fails.

**Floor & identity**
- [#2](https://github.com/cairn-ehr/drugref/issues/2) **Floor hardening** — close the `TRUNCATE` +
  owner-role bypass via RLS + privilege separation. **Note the test-suite coupling:** `grep -l TRUNCATE
  tests/*.py` now finds **nine** modules (was seven; 5b added `test_mesh_ci_run.py`, and
  `test_moiety_admission.py` was missed in the earlier count), each truncating in an autouse fixture because
  their orchestrators commit internally and escape the `conn` fixture's rollback. Those fixtures depend on
  precisely the bypass this closes, so hardening must land with a replacement isolation strategy.
- [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality** — structural re-key by
  InChIKey, deferred.
- **#17 no-silent-drop gaps — CLOSED, both halves landed.** The allow-list became UNII-keyed in #34; this
  round counted the last silent refusal (`MeshSummary.pa_records_without_descriptor`). Its third part was
  never a code gap — it is the claim-canonicalisation backfill check, now carried under
  "Verify-before-production" below so closing the issue does not lose it.

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
- [#16](https://github.com/cairn-ehr/drugref/issues/16) **A crashed ingest leaves no trace** — the
  `ingest_run` row is written inside the run's own transaction, so a failure rolls it back. Needs a
  connection-ownership decision, and a CLI.
- [#30](https://github.com/cairn-ehr/drugref/issues/30) `strip_salt` drops only one trailing token —
  measure before building; slice 3 may supersede it.

**Interaction model**
- [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is unpopulated** —
  filed as 41 of 739; `gap_unpopulated_contraindication` returns **13** against the real release (12 before
  `db/018` made the population test subject-aware), Plan B's expansion having absorbed the rest. Re-measure
  before acting on the issue text.
  Still the highest-value curation worklist available — upstream vouching that the answer matters — and
  largely an **indexing loss, not a knowledge gap**: openFDA labels carry the statements (§3.5), which is
  why the cost ladder puts `openFDA-SPL` above `literature`.
- [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions** — decide before 5c builds on
  the pairwise shape. MED-RT does not assert the triple-whammy even pairwise.
- [#8](https://github.com/cairn-ehr/drugref/issues/8) **Class-level `has_*` assertions unused** (~756
  edges) — the other half of making the DAG carry knowledge, now that Plan B walks it.
- [#35](https://github.com/cairn-ehr/drugref/issues/35) **`class_expansion_policy` has no history** — a
  revised decision overwrites its own rationale, and unlike the `question_*` tables it has no rewrite trigger,
  on a table that gates recall. Plan C's append-only overlay is the fix. **Was swept closed while unfixed;
  reopened and still open.**
- [#36](https://github.com/cairn-ehr/drugref/issues/36) **The discovery heuristic counts descendant classes,
  not reachable members** — `Increased Sympathetic Activity` spent a curator `allow` on a provable no-op
  (all 21 children empty). Changing the metric moves which roots get asked about, so it needs a curator and
  a re-measure, exactly as `db/010` says.
- [#37](https://github.com/cairn-ehr/drugref/issues/37) **The DAG is expanded unprunably on every query** —
  the class-side twin of #45, still open. Denied roots are walked then discarded and `WHERE is_direct` cannot
  push down, so the precision opt-out funds the expansion it throws away. The trap: restricting the *root
  set* is safe, restricting the *walk* deletes the coagulation case. **`db/018`'s ancestor-walk function is
  the shape that fixes it** — walk UP from the partner's class — and #45 measured ~13× for it on the
  condition DAG. Not urgent: a filtered pair lookup is ~25 ms.

**Licence deeds (blockers before production, per rule 6)**
- [#6](https://github.com/cairn-ehr/drugref/issues/6) Re-confirm the MED-RT deed against the live NLM
  source-release doc (the distribution ships no licence file; NLM's doc was HTTP 502 at design time).
- [#25](https://github.com/cairn-ehr/drugref/issues/25) PBS redistribution — blocks bundling, not
  node-local ingest; needs written Dept-of-Health confirmation.

**Verify-before-production, generally:** re-run each parser against a full current release and re-confirm the
aggregate numbers. Fixtures from a real release are not the same thing — 5b found five spec errors that way,
each invisible to a green suite. **Plus one data check, inherited from #17:** `claims.add_claim`
canonicalises case-bearing claim values (UNII / INCHIKEY / CHEBI) via `ids.canonical_claim_value`, so any
database populated *before* that change could hold a spelling no lookup matches. Harmless today (no
production database exists) and cheap to confirm — but the append-only floor means such rows cannot simply
be deleted, so confirm it BEFORE the first real load, not after.

## Repo facts

- GitHub `cairn-ehr/drugref` · default branch `main` · **AGPL-3.0** · attribution in `NOTICE`. Slice 5b adds
  **no new source**, but corrected the MED-RT/MeSH *scope* claims already there (see "Slice 5b" above). Coding
  rules live in CLAUDE.md and the `nextsession` skill.
- Public docs site: `docs-site/` (MkDocs Material) → `docs.drugref.org`, deployed by
  `.github/workflows/docs.yml`; `mkdocs build --strict` is its test. Its **Design decisions** section holds
  *living* records (revised in place, reversed ones removed) and is **where a standing correction to an
  artefact that genuinely cannot be edited — an immutable spec — goes**; the 5b erratum now corrects the spec
  ALONE. Specs/HANDOVER/ROADMAP are **not** published.

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

**Merged to `main`:** slice 1 (identity spine, PR #1) · 2a (MED-RT classification, #9) · 2a.1 (source-neutral
class registry, #10) · 2b (MeSH PA) · 5a (MED-RT CI_MoA/CI_PE) · the foundation review · Plan A (open-question
registry) · 8a (PBS localisation, #28) · Plan B (DAG-descendant expansion, #32) · the identity-spine fix round
(#34) · the Plan B review round (#38) · 5b — MeSH-keyed contraindications (#44) · the post-5b debt round (#46)
· the interaction debt round (#49) · **5b.2 — MeSH-keyed indications (#54)**.

**IN FLIGHT — the #53 population-label round**, on `fix/population-label-corrections`: complete, **622 tests
green**, `ruff check` + `mkdocs build --strict` clean, **no migration and no production logic change** (two
docstrings and one published page). Details under "The #53 round" below. 5b.2's measured table is under
"Slice 5b.2"; its erratum is `docs-site/docs/decisions/indications-do-not-expand.md`.

**`db/019` is now MERGED, so it is immutable** — it was edited in place on the branch (which the ledger
permits) to carry post-gate figures; any further correction to it needs a new `db/NNN`, or the living record
for prose. Its final fix wave added the two counters in the traps below (**168** / **422**, both confirmed
end-to-end) and filed [#51](https://github.com/cairn-ehr/drugref/issues/51),
[#52](https://github.com/cairn-ehr/drugref/issues/52) and [#55](https://github.com/cairn-ehr/drugref/issues/55)
for 5c.

**⇒ Issue-tracker hygiene — the sweep-closed-but-unfixed pattern has happened three times** (#31, #35, #40),
each time because a commit or PR body saying *filed, not fixed* still named the number. The tracker is true
today. **A number in a commit message is a claim about the code — verify it**, and when filing rather than
fixing, prefer prose that cannot be parsed as a closing keyword.

**⇒ Next candidates:**

- **Slice 5c — the curated, signed overlay.** Both MeSH-keyed halves are rebuildable projections now, and a
  projection may not invent a line of therapy, a strength of evidence or an ordering among the drugs that
  treat one condition. 5c is where a human adds those, and the only thing that can.
- **Slice 3 — composition tree (salts/esters/hydrates via GSRS).** Now triply motivated: the salt-strip
  heuristic is down to **0.03%** of bridge rows, [#33](https://github.com/cairn-ehr/drugref/issues/33)
  needs form→moiety relationships to close the MeSH CAS gap, and #30 is waiting on the same thing.
- **Plan C — the accumulation model.** Gated on 5b (§12-H); 5b has landed its contraindication half, so the
  gate is down for the CI axis. Also the stated fix for **#35** (`class_expansion_policy` has no history).

## Merged rounds, compressed — the traps only

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

**The interaction debt round (#39, #31, #45, #50 — `db/018`, merged #49) — the four traps it leaves.**

1. **ONE WRITER PER `(source, reason)`** on `ingest_unmatched_ingredient` — add a value, never share one.
   `medrt_run` and the MeSH-keyed run both open under `MED-RT`, so `reason` is what tells their rows apart;
   it is **NOT NULL with NO DEFAULT** because the value scopes a DELETE. `db.clear_source_tables`'s opt-in
   `match=` keeps that DELETE in exactly one place (#43's rule). #47 would add a fourth value.
2. **One quantity stated twice is a quantity that will disagree** (db/006). The first draft of `db/018`
   carried the reach measure as two near-identical CTEs and only one learned that a rule's own subject is
   not a partner — so a whole class of dead rules was reported by *nothing*, which is exactly what #31 was
   filed about. It is now **one view, `ci_rule_partner_reach`**, with the two gap views as complementary
   filters on one column (`= 0` / `> 0`), so the partition is true by construction. **This is the reasoning
   `condition_indication_reach` and `gap_condition_without_indication` inherit** (5b.2).
3. **Two implementations of one expansion rule is the danger.** `contraindications_for_condition` walks UP
   and the expanded view walks down; equivalence is pinned by test *and* was checked on the release (200
   conditions, 4,935 rows, zero difference). ~13× faster for a patient lookup; the view stays for whole-set
   access and `WHERE is_direct`. 5b.2's function/view pair carries the same obligation.
4. **Re-measure before quoting an issue.** Two of the three issue texts proved stale, and #50 then moved a
   published figure (300 → **299**: clomiphene is its own rule's subject).

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
rebuilds the register as its last step before commit. **Seven** gap kinds now; measured against the real
releases: **unclassified_moiety 16,089 · unmatched_ingredient 2,150 · unresolved_ci_object 103 ·
condition_without_indication 97 · unpopulated_contraindication 13 · dead_by_expansion_policy 1 ·
unreviewed_expansion_root 0.**

**Slice 8a — PBS localisation, the local tier's first attachment.** `db/009` (three tables, a rebuildable projection
with **no** append-only floor, because a de-listed PBS item must be able to disappear); `ingest/pbs.py` (pure parser),
`local.py` (single writer), `ingest/pbs_run.py` (orchestrator), bridging PBS products to the global spine **by name
alone** — the only licence-clean join, since PBS carries no UNII/CAS/InChIKey. `local_product_uuid` is a pure function
of `(jurisdiction, source, source_code)`.

Measured against the real July-2026 release (14,840 items): the bridge reaches **13,719 = 92.4%**, **exactly the
ceiling** measured against all UNII substance names — so **the moiety gate, not the bridge, was the binding
constraint**, though it took *both* the gate fix and the display-name index to show it. The residual is
AU/INN-vs-USAN spelling divergence (cefalexin, ciclosporin) and non-drugs the gate correctly excludes.

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

**Measured end-to-end against the real releases** (upstream denominators: `CI_with` 11,524 assertions / 708
objects · `CI_ChemClass` 1,939 / 360). Actual: `condition`/`condition_parent` **5,203 / 7,157** (+13 SCRs over
the spec); `moiety_condition_contraindication` **9,471** over **2,900 / 641** subjects/objects;
`moiety_contraindication` **1,442** (one self-pair, `db/014` forbids it); `gap_unresolved_ci_object` **103
rows / 405 rules**; `condition_contraindication_expanded` **191,728**, of which `WHERE is_direct` is **9,471**
— exactly the base. **Do not grep for `MeshCiSummary`** — 5b's flat type became 5b.2's nested
`MeshRelSummary` (`ingest/mesh_rel_summary.py`), renaming `condition_contraindications`/
`moiety_contraindications` → `condition_rows`/`pair_rows`. Shape only; no figure moved.

**The `object_kind` split:** `CHEMICAL_CLASS` **96** objects / 386 rules · `UNREGISTERED_SUBSTANCE` **7** / 19
— 103 / 405 unchanged. The 7 are ordinary registry-coverage work; the 96 need a curator ruling on tree
expansion, and are withheld rather than expanded because MeSH's chemical tree is *structural* (a rule on
Sulfonamides would reach bendroflumethiazide — the discredited sulfa cross-reactivity inference). Full
argument: `docs-site/docs/decisions/withheld-chemical-class-contraindications.md`.

**Five numbers moved and every one was the spec, not the code** — three from one cause: **the spec measured
at the MeSH CONCEPT grain, drugref stores at the RECORD grain**. **103 was adjudicated twice and is correct —
do not "fix" it by keying the worklist on the concept**, which is the split `mesh_concepts.py` exists to
prevent. The spec alone still says 108; the living record above is its standing correction.

**The source-blind walk stays LATENT.** No MeSH chemical class is registered in `substance_class` and
conditions live in their own MeSH-only DAG, so no rule yet expands over another authority's edges. It goes
live when `has_SC` (3,632 assertions, **248 targeting MED-RT itself**) or the class arm lands.

## Slice 5b.2 — MeSH-keyed indications (`db/019`, merged #54)

Spec: [slice-5b.2](superpowers/specs/2026-07-30-drugref-slice-5b2-mesh-indication-design.md). The other half
of MED-RT's MeSH-keyed content — `may_treat` / `may_prevent` / `may_diagnose` and `induces` — over the **same**
condition registry, which one orchestrator (`ingest/mesh_rel_run.py`) now owns for both halves. **No new
source** — but `NOTICE` **was** corrected: it named MED-RT's two contraindication predicates specifically, and
six MeSH-keyed predicates are now ingested. Two relations, one vocabulary table, one cached `condition.scr_class`, one
read function, one reach view, one gap view, a seventh `gap_kind` and a third `reason` bucket.

**Measured end-to-end** (UNII 26Feb2026 → MED-RT 2026.07.06 → MeSH desc/supp/pa 2026), whole chain 108 s:

| | **measured** | |
|---|---:|---|
| `moiety_condition_indication` / `moiety_induced_condition` | **14,674 / 154** | may_treat 12,662 · may_prevent 1,888 · may_diagnose 124 |
| `condition` / `condition_parent` / `scr_class` | **5,963 / 8,507 / 29×`3`+5×`1`** | all exact to spec |
| `gap_condition_without_indication` | **97** | 80 C/F-tree + 17 tree-less `SCRClass 3` (spec said 66) |
| `condition_subtree` | **11,512 → 11,605** | +93 over 641 roots (spec said 12,311 → 12,415) |
| `condition_contraindication_expanded` | **192,161** | +433 = **+0.226%** (spec said ≈192,500) |
| indicated **and** contraindicated / broadened | **168 pairs / 422** | unmatched indication subjects **1,426** |
| **must not move** | **all held** | 9,471 · 1,442 · 103/405 · 21,664 |

`indications_for_condition` vs `condition_indication_reach` **on the release**: **5,963 conditions checked,
276,343 rows, zero disagreements** — the pin that makes two statements of one rule safe.

**Traps a future change can still break.**
- **The generalisation walks UP, never down.** Down distributes a therapeutic claim over the object's
  subclasses — one `may_treat` on *Neoplasms* manufactures 708 claims (14×/47×/77×; 14,674 rows → 276,343).
  Nothing derived is stored; ancestor rules come back `is_direct = false`, a **weaker** claim not a wider one.
  The column is `generalises_to_descendants`, deliberately **not** `expands_descendants`; do not unify them.
- **The two-table split is structural.** `induces` has its own relation, **no axis row** and no walk: the
  unfiltered read of a table must be one true sentence, and a shared table plus a forgotten `relationship`
  filter reads "carbamazepine treats agranulocytosis".
- **The gap view is deliberately scoped** to C/F-tree diseases plus tree-less `SCRClass = 3` rare diseases:
  842 further unreached conditions are excluded (670 surgical procedures) because `question_uuid` is
  externally citable and immortal. Every exclusion is counted in the view's `COMMENT ON`.
- **One registry, so widening it moves the contraindication half — upward, and that is a completion.** An
  edge is written only when **both** endpoints are registered, so registering indication objects completes
  edges the CI closure could not see. 10 of 641 CI roots grew (*Nervous System Diseases* +59, gaining *Acute
  Pain*), none shrank, the root set is byte-identical, every **direct** figure is unchanged. **Expect this
  again every time the registry widens.**
- **Two widenings survive the upward walk; both are COUNTED, not fixed.** (1) **168 pairs** are indicated
  *and* contraindicated for one condition (carvedilol/*Heart Failure* — chronic HFrEF vs acute
  decompensation), and the two read paths walk opposite ways so it multiplies below the object. (2) **422 of
  18,314** assertions **name a subordinate concept**, so their rows sit on a **broader** record than MED-RT
  named, because MED-RT keys on a ConceptUI and a condition on the record — `may_treat` "Seizures, Focal" for
  eslicarbazepine lands on *Seizures*, which it aggravates when generalised; safe on the CI half, unsafe here.
  **Mind the grain:** 422 is RELEASE-grain (counted above the moiety gate, so it matches MED-RT's own totals
  and is not a row count — the row figure is unmeasured), while 168 is ROW-grain (a query over both stored
  tables). Reading either as the other is the same pre/post-gate slip the erratum below is about; pinned by
  `test_the_widening_counters_are_release_grain_not_row_grain`. `also_contraindicated_pairs` and
  `indications.broadened_object_assertions` report them every run, tests pin both, and both tables'
  `COMMENT ON` state (1). Worked cases in the living record; remedies are
  [#51](https://github.com/cairn-ehr/drugref/issues/51) / [#52](https://github.com/cairn-ehr/drugref/issues/52) (5c).
  The read path's own exposure — a consumer who ignores `is_direct` gets the full 276,343-row generalisation
  set — is stated in the living record and open as [#55](https://github.com/cairn-ehr/drugref/issues/55).
- **The spec's 66 / 12,311 / ≈192,500 were computed BEFORE the moiety gate** and reproduce exactly when
  re-measured that way — right about different populations, as 5b's concept-vs-record grain was. `db/019`'s
  comments carry the post-gate figures (edited in place before merge); `db/015`'s cannot be and stay 5b's.
  Three further population-label slips survived the round and were **fixed by the #53 round below**.

## The #53 round — three population labels, and the fixture that could not tell two grains apart

All three claims were **re-measured against the real releases before being touched** (the standing rule) and
this time all three held. The two prose fixes: **`550 of 13,463` → `550 of 13,458`**, because 5 assertions
name the 2 codes MeSH withdrew and an unresolved code has **no `is_preferred_concept` to test**; and
**`this slice's 1,053` → the 2,198 codes ONE RUN resolves** (1,053 CI + 1,528 IND, **383 named by both**), in
a module that now serves both halves. Newly measured and now stated: the 81 CI concepts collapse onto **79**
records, and **the indication half has no resolution gap at all** (1,528/1,528), which is what makes 4.09% and
2.30% comparable. **The numerator stayed 2 while the denominator doubled only because both withdrawn codes are
CI-only** — precisely why the stale sentence looked right.

**⇒ The third was a test claiming to pin a grain its fixture could not distinguish, and the fixture now holds
2 overlapping pairs across 3 rows — do not reduce that.** The collision counter reports PAIRS; the test said a
drift to ROWS would fail there, and it would not, because the fixture held one overlapping row and one
overlapping pair. Removing the production query's `SELECT DISTINCT` left the suite **green** (verified by
mutation, both before and after). Fixed by **strengthening the fixture, not weakening the claim**: an eighth
ingredient, **mannitol (RxCUI 6628, UNII 3OWL53L36A)** — the only subject in the release asserting `may_treat`
*and* `may_prevent` *and* `CI_with` against ONE object (M0001524 → D001002 *Anuria*), clinically the same
tension as carvedilol/*Heart Failure*. The mutation now fails.

**`make_medrt_subset.py`'s cap gained one exemption, and it is the point rather than a hole**: a therapeutic
assertion whose `(subject, object)` also carries a `CI_with` from the same subject is **never trimmed**, being
the hardest data in the release (#51) rather than the noise the cap exists to remove. Side effect: the single
non-overlap `may_treat` survivor moved from charcoal→*Poisoning* (now exempt) to charcoal→*Diarrhea*.

**Fixture movement, verified by diffing identities not counts:** moieties 11 → 12, claims 53 → 58, classes
83 → **93** (+1 MoA, +6 PE, +1 EPC, +2 APC — the first ingredient to move EPC/APC), DAG edges 67 → **76** (10
new classes give 9: two are top-level and *Osmotic Diuretic [EPC]* is multi-parent), memberships 30 → **36**,
conditions 22 → **27** with **nothing displaced**, `condition_rows` 8 → **13**, `condition_parent_edges`
unchanged at 12. All three fixtures regenerate **byte-identically** from the real releases.

**⇒ One distinction worth keeping.** "The direct rows must not move" is a claim about **widening the closure**
(spec 10), not about the fixture never growing: mannitol is a new **subject**, and a subject states its own
contraindications. What would still be a defect is that number moving when only the **object** side widens —
which is what 5b.2 tested, and it held.

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
# 622 tests; the DB-gated majority SKIP without this DSN, exercising none of the
# schema, floor, views or orchestrators -- so always run WITH it before claiming green:
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
  subject-aware `gap_unpopulated_contraindication`; `contraindications_for_condition`) · `019` the two 5b.2
  indication relations + `condition_indication_axis` + `condition.scr_class` + `condition_indication_reach` +
  `indications_for_condition` + `gap_condition_without_indication` + the third `reason` value. **Read the
  LATEST file that touches an object for its actual shape** — 002 still shows superseded MED-RT-specific columns, 004's
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
  `src/drugref/indications.py` + `src/drugref/ingest/*.py`; seed data under `src/drugref/data/`; fixtures
  under `tests/fixtures/`.
  The MeSH-keyed stack: **`conditions.py`** / **`indications.py`** (single writers for the registry and the
  two indication relations), **`ingest/mesh_concepts.py`** (pure/streaming: MeSH **ConceptUI → record**
  resolution, the descendant closure, the tree-number DAG), and **`ingest/mesh_rel_run.py`** — the ONE
  orchestrator for both halves, which reads two authorities (MED-RT states the rule, MeSH defines its
  object), owns the shared registry, and runs `mesh_ci_relations.py` and `mesh_ind_relations.py` as passes.
  Its four tallies live in `ingest/mesh_rel_summary.py` and are re-exported, so `mesh_rel_run.MeshRelSummary`
  still resolves. **Two orchestrators here is not a refactor away — it is impossible**: a `condition_parent`
  edge is derived by BOTH closures, so no `reason` discriminator can split it (#39 one layer deeper).
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

**Filed by slice 5b.2 and its review (all four for 5c or a debt round)**
- [#51](https://github.com/cairn-ehr/drugref/issues/51) **168 pairs are both indicated and contraindicated**
  — counted, not resolved; 5c must decide how a consumer is told.
- [#52](https://github.com/cairn-ehr/drugref/issues/52) **422 assertions are stored against a broader MeSH
  record than MED-RT named**, and the row carries no `concept_ui`, so a consumer cannot detect which. Storing
  it is also what would make the row-grain figure (currently unmeasured) queryable.
- [#55](https://github.com/cairn-ehr/drugref/issues/55) **`indications_for_condition` offers generalisations
  through a boolean, not a structure** — `db/019` split `induces` into its own table precisely because a
  forgotten filter manufactures a therapeutic claim, then left the read path relying on one. Options are a
  second function, an explicit no-default argument, or accepting the column-parity argument and closing it;
  whichever wins revises the living record.

**Filed by the interaction debt round**
- [#47](https://github.com/cairn-ehr/drugref/issues/47) **`medrt_run` counts its unmatched CI subjects but
  never persists them** — 99 on the real release, and **all 99 already reach the worklist through another
  writer's rows**, so nothing is lost *today*. That is a property of this release, not a guarantee. The fix
  needs a **FOURTH** `reason` value, never a shared one (`db/018`'s one-writer-per-`(source, reason)`
  invariant); 5b.2 took the third (`indication`).
- [#48](https://github.com/cairn-ehr/drugref/issues/48) **A non-expanding predicate with no direct member is
  equally dead and is deliberately not reported** by `gap_dead_by_expansion_policy` — allowing expansion
  could not revive it, so it is a `ci_axis` question with a different remedy and wants its own view.
  **Still unreachable, and 5b.2 did NOT change that** — an earlier note here expected it to. `induces` holds
  no axis row rather than a false one, and an indication always reaches its own condition, so the dead-rule
  shape does not transfer to this graph. It goes live when a *class-side* predicate stops expanding.

**Closed by the two debt rounds** (#50, #39, #31, #45 · #40, #17, #42, #41, #43), each verified against the
code before closing. **Three standing rules came out of them and outlive the issues:**
- **THE VIEW'S GRAIN MUST BE THE `gap_key`'S GRAIN** (#41). A gap view that groups on the *stored* spelling
  while the key `upper()`s it folds two rows onto one immortal `question_uuid`. Pinned by
  `test_the_views_grain_is_the_gap_keys_grain`, and restated for every new gap kind.
- **One reader, one clear, one checksum** (#40, #43): `mesh.iter_records`, `db.clear_source_tables` and
  `ingest/checksum.py` each live in exactly one place, and every writer's table tuple is **restated
  independently** in `tests/test_source_clear_contract.py` so a dropped table fails.
- **A branch the release cannot exercise is pinned on controlled input and verified by mutation** (#42):
  desc2026 and supp2026 share **0** ConceptUIs, so the descriptor-wins tie-break is a guard against a future
  release, not a live case.

**Floor & identity**
- [#2](https://github.com/cairn-ehr/drugref/issues/2) **Floor hardening** — close the `TRUNCATE` +
  owner-role bypass via RLS + privilege separation. **Note the test-suite coupling:** `grep -l TRUNCATE
  tests/*.py` still finds **nine** modules, but one of them is now `tests/mesh_rel_fixtures.py` — a shared
  helper, not a test module — because the two MeSH-keyed test modules share one truncate. Each truncates in
  an autouse fixture because their orchestrators commit internally and escape the `conn` fixture's rollback. Those fixtures depend on
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
  (~28k). `executemany`/`COPY` + batch commits + `iterparse`. **The MeSH-keyed run now writes 40,211 rows**
  (5,963 conditions + 8,507 edges + 10,913 contraindication + 14,828 indication) in **55 s**, the slowest
  leg; the whole chain is 108 s.
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

- GitHub `cairn-ehr/drugref` · default branch `main` · **AGPL-3.0** · attribution in `NOTICE`. Neither 5b
  nor 5b.2 adds a source, but each corrected `NOTICE`'s MED-RT/MeSH *scope* claims — 5b.2's names all **six**
  MeSH-keyed predicates. Coding rules live in CLAUDE.md and the `nextsession` skill.
- Public docs site: `docs-site/` (MkDocs Material) → `docs.drugref.org`, deployed by
  `.github/workflows/docs.yml`; `mkdocs build --strict` is its test. Its **Design decisions** section holds
  *living* records (revised in place, reversed ones removed) and is **where a standing correction to an
  artefact that genuinely cannot be edited — an immutable spec, or a MERGED migration's `COMMENT ON` — goes**.
  Two errata live there, one per MeSH-keyed slice. Specs/HANDOVER/ROADMAP are **not** published.

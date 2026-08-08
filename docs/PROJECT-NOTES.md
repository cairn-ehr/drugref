# PROJECT-NOTES — drugref

> **The stable half of the working scaffolding** (#63). Traps, current state by layer, how to run and test,
> the schema and code map, upstream errata, repo facts. **Edited IN PLACE and under no line bound** — that is
> the whole point: `docs/HANDOVER.md` is regenerated each round and its history answers nothing, so anything
> whose history is worth reading lives here instead.
>
> Still **not** a source of truth. The canonical what/why is the design specs under
> [`docs/superpowers/specs/`](superpowers/specs/); living decisions are in `docs-site/docs/decisions/`. If this
> file disagrees with either, they win.
>
> **Its git history starts 2026-08-05.** That is the honest cost of the split: it buys a readable history
> going forward, not retroactively.

## Standing rules that outlive the issues that produced them

Each came out of a debt round, each is pinned by a test, and each states a bet this project has already lost at
least once. **Moved here from HANDOVER.md** in the #64 review round: they are durable by definition, and a rule
worth keeping does not belong in the file whose history is deliberately disposable.

- **THE VIEW'S GRAIN MUST BE THE `gap_key`'S GRAIN** (#41) — a gap view that groups more coarsely than its key
  folds two gaps onto one immortal `question_uuid`. Pinned per kind, Plan C's two compound-key views included.
- **One reader, one clear, one checksum — and one supersession** (#40, #43, #59): `mesh.iter_records`,
  `db.clear_source_tables`, `ingest/checksum.py` and `overlay.supersede` each live in one place, and every
  writer's table tuple is **restated independently** in `tests/test_source_clear_contract.py` so a dropped table
  fails. The grep contracts in `tests/test_overlay_contract.py` are the same shape for the other three.
- **A vocabulary written down twice is two things that can disagree** (db/006, #59, #64). The decision values
  live in `db/027`'s CHECK and nowhere else — `--decision` has no argparse `choices`, and the error message
  quotes `pg_get_constraintdef` rather than restating them. `interactions.WITHDRAWN` is the ONE Python name for
  the one value Python must spell, pinned by grep. **This applies to prose numbers too:** HANDOVER's line bound
  was stated in three files, two disagreed, and the file exceeded both.
- **A branch the release cannot exercise is pinned on controlled input and verified by mutation** (#42): desc2026
  and supp2026 share **0** ConceptUIs. **#53's `is_cap_exempt`, #47's named-row tie-break and ALL of #35's new
  behaviour** — no release-derived database holds a superseded or withdrawn row — are the same shape.
- **A DETECTOR NOBODY CALLS IS NOT A DETECTOR** (db/010 → #59's note → issue 76). Shipping a gap/operator view
  with no consumer has now happened **twice**: `expansion_policy_unresolved` (db/010, repaired) and
  `curated_target_unresolved` (db/029, repaired by the round below). A view is half a feature; the other half is
  the caller. **When a migration adds a detector, name its consumer in the same round or file the issue before
  the branch merges.**
- **A MEASURED FIGURE INSIDE A `COMMENT ON` IS SHIPPED DATA, AND NEEDS A TEST LIKE ANY OTHER** (the review of PR
  [#78](https://github.com/cairn-ehr/drugref/pull/78)). `db/029` carried the stale `~739` inside
  `COMMENT ON TABLE curated_interaction`; the whole-branch review corrected it and **nothing pinned the
  correction**, so the post-merge round had to read the live catalog by hand and the state files claimed a test
  coverage that did not exist. A `--` comment is stripped, but a `COMMENT ON` lands in the catalog where
  consumers read it, and it is exactly where a later rewrite restates the design spec's approximate prose. Pinned
  now by `tests/test_curated_interaction_comment.py`, asserted against the CATALOG rather than the migration
  text — the file a grep could check is not the one that shipped once a migration merges — with a guard beside it
  driving the same pure predicate with the comment that actually shipped.
- **A READING THAT IS IDENTICAL ON THE BROKEN VERSION IS NOT EVIDENCE** (the review of PR #78). The post-merge
  round "confirmed" the `pair_count` fix from `max`/`sum`/`count`, which are identical on the drifted and merged
  databases *by the finding's own argument*. Before recording a fix as verified, name the observation that
  **differs** between the two versions — here `pg_get_viewdef`, run on both — or record it as unverified.
- **A CONFIG BLOCK UNDER THE WRONG HEADER IS SILENT IN BOTH TOOLS** (issue 66). `line-length = 88` sat inside
  `[tool.pytest.ini_options]` for a whole draft and nothing failed — 88 is also ruff's default, so lint looked
  configured. The only symptom was pytest's `PytestConfigWarning: Unknown config option: line-length`. **Verify
  effective settings from the tool (`ruff check --show-settings`), never from reading the file.**

## Merged rounds, compressed — the traps only

**The identity-spine fix round (#34: #27, #17, #26).** Spec: [moiety gate
redesign](superpowers/specs/2026-07-27-drugref-moiety-gate-redesign.md). Four defects, none visible to the committed fixtures;
required columns are now **declared and checked**, because `or ""` absorbed a structural mismatch silently. The gate is
`INN_ID | USAN_ID | (RXCUI & drug-like SUBSTANCE_TYPE) | UNII allow-list`, and **the asymmetry is the design** — uniform
type-filtering was measured and rejected because it deletes heparin, enoxaparin, protamine and 346 gene/cell therapies.
**Strictly monotone, pinned by a test**, because `moiety_uuid` is immortal. **5,227 moieties rest on `RXCUI` alone**, the
natural head of a #19 worklist. Do **not** "fix" issue 33 by allow-listing the hydrate UNIIs. **Every fixture is extracted from a
real release** — the last hand-written one invented an `INN_ID`, a CAS and a UNII.

**Plan B — DAG-descendant expansion (#32 + the #38 review round, `db/010` + `db/012`).** Design: §3.2 / §7.1 / §11 of the
[additive-effect spec](superpowers/specs/2026-07-25-drugref-additive-effect-and-open-question-design.md). `ddi_candidate_pair`
joined **direct** membership only, hiding **85.2%** of `CI_PE` pairs, because MED-RT files membership at the most *specific*
node and writes rules against the *parent* — and **for a contraindication, fewer rows is the harm direction**. It now descends
the DAG through one cycle-safe view (`ci_class_subtree`), bounded by `class_expansion_policy`: a deny-list held as **data**,
cleared by no ingest (11 denied, 3 allowed, all 14 PE). **Three traps.** (a) **`WHERE is_direct` reproduces the pre-Plan-B row
set exactly**, so a consumer who forgets the filter errs toward recall. (b) **`allow` is not the same as absent** — absent
means *unreviewed*, which expands **and** raises a question. (c) **The deny-list filters the RULE'S OBJECT CLASS, never the
walk**: *Decreased Coagulation Activity* is a descendant of a denied root, so a traversal-barrier reading deletes the case
Plan B most exists to fix — pinned by `test_a_descendant_of_a_denied_root_still_expands`; **do not delete that test.**
Residue: #36, #37 (#35 is closed by the round below).

**The interaction debt round (#39, #31, #45, #50 — `db/018`, merged #49) — the four traps it leaves.**

1. **ONE WRITER PER `(source, reason)`** on `ingest_unmatched_ingredient` — add a value, never share one; `medrt_run`
   and the MeSH-keyed run both open under `MED-RT`, so `reason` tells their rows apart. **NOT NULL, NO DEFAULT**,
   because it scopes a DELETE, which `db.clear_source_tables`'s `match=` keeps in one place (#43). `db/026` added the
   fourth value — see the ingest-operability round below.
2. **One quantity stated twice is a quantity that will disagree** (db/006). Two near-identical CTEs, only one of which learned
   that a rule's own subject is not a partner, left a whole class of dead rules reported by *nothing* (#31). Now **one view,
   `ci_rule_partner_reach`**, the two gap views complementary filters on one column — and `condition_indication_reach` /
   `gap_condition_without_indication` inherit it.
3. **Two implementations of one expansion rule is the danger.** `contraindications_for_condition` walks UP and the expanded
   view walks down; equivalence is pinned by test *and* on the release. 5b.2's pair owes the same.
4. **Re-measure before quoting an issue.** Two of three issue texts proved stale; #50 moved a published figure (300 → **299**:
   clomiphene is its own rule's subject).

## Current state, by layer

**Slice 1 — the identity spine.** Schema `drugref` (`ingest_run`, `substance_moiety`, `identity_claim`) + an append-only
row-level floor. Own immortal `moiety_uuid` (`UUIDv5` on UNII at first sighting, then **pinned forever**; namespace
`d07651ee-311d-552b-a97b-591219eb3ad3`), never keyed on a name; external IDs are **append-only claims** (UNII, INN, RXNORM_IN,
CAS, PUBCHEM_CID, INCHIKEY, CHEBI), so drugref doubles as a public cross-walk. Membership gate (since #26) = **`INN_ID |
USAN_ID | (RXCUI & drug-like SUBSTANCE_TYPE)`** **or** the closed **UNII-keyed** legacy allow-list, the admitting signal in
`moiety_admission` (`db/011`). Seeded from UNII (public domain), INN, ChEBI (CC BY 4.0), **RxNorm demoted to a claim**, a
closed USAN↔INN crosswalk. **Floor scope:** row-level UPDATE/DELETE only — `TRUNCATE` and the owning role remain bypasses
(#2).

**Slice 2a / 2a.1 — the classification DAG.** `substance_class`, `class_parent`, `class_membership` seeded from **MED-RT**:
3,634 classes, 3,961 edges, 27,540 memberships over 6,012 ingredients at the terminology level — **18,639 rows survive the
moiety gate** (the two grains are routinely confused). Class identity is immortal *by determinism* (`class_uuid =
UUIDv5(CLASS_NAMESPACE, SOURCE + ":" + code)`), so a rebuild re-derives it; edges are rebuildable projections outside slice
1's floor. **Existing MED-RT class UUIDs are pinned by frozen literals** — the derivation is the join key of both edge tables,
so a drift would orphan every edge with no error anywhere. The stored `source` and the UUID key derive from one
canonicalisation (`ids.canonical_source`); **extend that AND `db/003`'s CHECK together when an authority lands**. **Licence
scoping is structural**: only MED-RT concepts are *defined* in the release, so requiring both endpoints of every edge to be an
ingested class keeps unlicensed content out.

**Slice 2b — MeSH PA.** 568 PA class descriptors, their tree-number DAG and memberships, on the **same three tables** (no
schema change). `ingest/mesh.py` is a pure streaming (`iterparse`) parser; `ingest/mesh_run.py` holds the **two-key bridge** —
UNII-primary → CAS-fallback against slice-1 `identity_claim` rows, **no new external source**. **22,179 has_PA rows** over
10,506 member substances, and **the old "73% joinable" line was ambiguous**: 72.8% carry an identity KEY, only **40.6% reach a
gated-in moiety** (both shortfalls counted; part of the residual is #33).

**Slice 5a — the first interaction data.** `db/004` `class_contraindication` (rebuildable projection) + read-time pair
expansion; `db/006` replaced the comment-enforced CHECK↔CASE coupling with a **`ci_axis` table the vocabulary is a foreign key
into**. **Candidate tier only, 5a/5b/5b.2 alike** — MED-RT does not track label updates, nothing alerts.

**Slice 5b — MeSH-keyed contraindications** (`db/013`–`db/016`, merged #44; spec:
[slice-5b](superpowers/specs/2026-07-28-drugref-slice-5b-mesh-contraindication-design.md)). A **third endpoint type**: a
`condition` is neither a moiety nor a `substance_class`, because nothing is a *member of* pregnancy and `substance_class`'s
axis vocabulary is entirely pharmacological. Hence `condition` + `condition_parent` (a rebuildable MeSH-only projection, DAG
from tree-number nesting) holding the **descendant closure** of the referenced conditions — without which a rule on Epilepsy
would have nothing to expand into and the feature would be inert while appearing to work. Two relations, because the objects
are different kinds of thing: `moiety_condition_contraindication` and `moiety_contraindication` (**drugref's first exact
pairwise DDI data**). `condition_ci_axis` carries `expands_descendants` with **no DEFAULT** (`db/012` finding 5). Read path
`condition_subtree` + `condition_contraindication_expanded`. Measured: **9,471** condition rows · **1,442** exact pairs (one
self-pair, `db/014` forbids it) · `gap_unresolved_ci_object` **103 rows / 405 rules**. **Do not grep for `MeshCiSummary`** —
it became 5b.2's nested `MeshRelSummary`. `object_kind` splits the 103 into `CHEMICAL_CLASS` **96** and
`UNREGISTERED_SUBSTANCE` **7**: the 7 are ordinary coverage work, the 96 need a curator ruling and are **withheld rather than
expanded**, because MeSH's chemical tree is *structural* (see
`docs-site/docs/decisions/withheld-chemical-class-contraindications.md`). **Five numbers moved and every one was the spec, not
the code**, three because **the spec measured at the MeSH CONCEPT grain and drugref stores at the RECORD grain**: **103 was
adjudicated twice — do not "fix" it by keying the worklist on the concept.** **The source-blind walk stays LATENT** — no MeSH
chemical class is registered in `substance_class`; it goes live when `has_SC` (**248 of its 3,632 assertions target MED-RT
itself**) or the class arm lands.

**Plan A — the open-question registry** (`db/007`, `db/008`). Coverage gaps are published as a **queryable register** rather
than hidden. **The hybrid split is the design:** `open_question` is a rebuildable projection re-derived every ingest; curator
intent (`question_state`), tier watermarks (`question_source_check`) and findings (`question_evidence`) are **append-only**,
keyed off an immortal `question_uuid` external tooling can cite — so a rebuild can never erase a `withdrawn`. **Populated is
per axis** (joins `ci_axis`). **Watermark, not closure:** only `withdrawn` is terminal. **A closed gap carrying curator work
is retired, not deleted** (`is_current`) — the curated tables cascade from `open_question` *and* refuse `DELETE`. Rebuilt
before commit by **five of the seven orchestrators**. **TWELVE** gap kinds since Slice 3 (eleven since Plan C):
unclassified_moiety **16,089** · unruled_composition_activity **2,245** · unmatched_ingredient **2,150** ·
uncurated_additive_effect **381** · unresolved_ci_object **103** · condition_without_indication **97** ·
unpopulated_contraindication **13** · dead_by_expansion_policy **1** · the other four **0** (three need curation). All
twelve are now **pipeline-measured: 21,079 questions** (2026-08-05, the Slice-3 chain end to end). The previous
**EXPECTED 2,226 / 21,060** hedging is settled and removed — the assembled registry gives **2,245**, 19 more than the
raw-extract query predicted, and the pre-Slice-3 base is unmoved at exactly **18,834**. The 19 are the composites whose
only activity ruling sits on a mirror record the orchestrator does not read a ruling from (Slice 3 erratum below).
`unruled_composition_activity` is gap kind 12 (`db/028`, Slice 3 Task 5): composites carrying components but no activity
ruling at all, populated from day one like the coverage kinds, not curation-dependent like Plan C's four.

**Slice 8a — PBS localisation, the local tier's first attachment.** `db/009` (three tables, a rebuildable projection with
**no** append-only floor, because a de-listed PBS item must be able to disappear); `ingest/pbs.py` (pure parser), `local.py`
(single writer), `ingest/pbs_run.py` (orchestrator), bridging PBS products to the global spine **by name alone** — the only
licence-clean join, since PBS carries no UNII/CAS/InChIKey. Measured (14,840 items): the bridge reaches **13,719 = 92.4%**,
**exactly the ceiling** over all UNII substance names — so **the moiety gate, not the bridge, was the binding constraint**,
though it took the gate fix *and* the display-name index to show it.

**Licence posture — read before extending slice 8a.** Node-local plug-in only: AGPL-3.0 ingest code and schema, **never a PBS
release**, with one stated exception — `tests/fixtures/pbs_items_subset.csv` commits 11 real rows and is the thing that goes
if [#25](https://github.com/cairn-ehr/drugref/issues/25) lands negative. ATC (WHO, NC+ND) and AMT/SNOMED CT-AU are quarantined
**structurally**: the parser reads a fixed allow-list, no table has anywhere to put them, and a test proves it with a fixture
carrying **planted** `atc_code`/`amt_code` columns (matched by substring).

## Slice 5b.2 — MeSH-keyed indications (`db/019`, merged #54)

Spec: [slice-5b.2](superpowers/specs/2026-07-30-drugref-slice-5b2-mesh-indication-design.md). MED-RT's other MeSH-keyed half —
`may_treat` / `may_prevent` / `may_diagnose` and `induces` — over the **same** condition registry, which one orchestrator
(`ingest/mesh_rel_run.py`) owns for both halves. **No new source**, but `NOTICE` was corrected to name all six predicates.
Measured (table in ROADMAP): **14,674 / 154** indication and induced rows · `condition`/`condition_parent` **5,963 / 8,507** ·
`condition_contraindication_expanded` **192,161** (+0.226%). **Must not move, and did not:** 9,471 · 1,442 · 103/405 · 21,664.

**Traps a future change can still break.**
- **The generalisation walks UP, never down.** Down distributes a therapeutic claim over the object's subclasses — one
  `may_treat` on *Neoplasms* manufactures 708 claims (14,674 rows → 276,343). Nothing derived is stored; ancestor rules come
  back `is_direct = false`, a **weaker** claim not a wider one. The column is `generalises_to_descendants`, deliberately
  **not** `expands_descendants`; do not unify them.
- **The two-table split is structural.** `induces` has its own relation, **no axis row** and no walk: the unfiltered read of a
  table must be one true sentence, and a shared table plus a forgotten filter reads "carbamazepine treats agranulocytosis".
  **The gap view is deliberately scoped** to C/F-tree diseases plus tree-less `SCRClass = 3` rare diseases; the 842 excluded
  conditions are counted in its `COMMENT ON`, because `question_uuid` is immortal.
- **One registry, so widening it moves the contraindication half — upward, and that is a completion.** An edge needs **both**
  endpoints registered; 10 of 641 CI roots grew, none shrank, the root set is byte-identical, every **direct** figure
  unchanged. **Expect this every time the registry widens.**
- **Two widenings survive the upward walk; both are COUNTED, not fixed.** **168 pairs** are indicated *and* contraindicated
  for one condition (carvedilol/*Heart Failure*); **422 of 18,314** assertions **name a subordinate concept**, so their rows
  sit on a **broader** record than MED-RT named. **Mind the grain:** 422 is RELEASE-grain (above the moiety gate, **not** a
  row count), 168 is ROW-grain; the slip is pinned by `test_the_widening_counters_are_release_grain_not_row_grain`. Remedies
  #51 / #52 (5c); a consumer ignoring `is_direct` gets all 276,343 rows (#55). **The spec's 66 / 12,311 / ≈192,500 predate the
  moiety gate** and reproduce exactly re-measured that way; `db/019`'s comments carry the post-gate figures, `db/015`'s stay
  5b's.
- **The #53 round (#56): the fixture holds 2 overlapping pairs across 3 rows — do not reduce that.** The collision counter
  reports PAIRS; with one overlapping row and one overlapping pair a test could not tell the grains apart (proved by
  mutation). **Mannitol** fixed it; the assertion that catches the mutation is `also_contraindicated_pairs == overlap`, kept
  able to catch anything by the `3 != 2` row assertion beside it. `make_medrt_subset.py`'s cap exempts overlap assertions,
  **scoped by `is_cap_exempt`** to the therapeutic predicates (`Synonym Of` shares their endpoint shape).

## Plan C — the accumulation model (`db/020`–`db/024`, merged #57)

Design: §4–§8 / §11 steps 6–7 of the [additive-effect
spec](superpowers/specs/2026-07-25-drugref-additive-effect-and-open-question-design.md). The model the pairwise projection
cannot express — *many drugs, one effect that adds up* — plus the role-based exception a count cannot represent. **Ships with
an EMPTY curation set.** **No new source**; drugref becomes an authority in its own registry (`source = 'DRUGREF'`, the trio
all extended). Five tables — four curated assertions on the §5.0 overlay shape, `interaction_group` the deliberate exception —
plus two read views and four gap views, **gap kinds 8–11**. New: `class_subtree` **22,754** · `gap_uncurated_additive_effect`
**381** of 1,873 PE classes · the other three gap views and both read views **0**, correct with nothing curated.

**Traps a future change can still break** (the last five are the `db/023`–`db/024` review round, measured not reasoned).
- **THERE ARE NOW TWO WALKS DOWN `class_parent`, AND THAT IS MEASURED, NOT SLOPPY.** Re-expressing `ci_class_subtree` as a
  filter over the unscoped closure gives a **byte-identical** `ddi_candidate_pair` and costs the hot path **5×** (3.6 → 18.8
  ms): scoping to the 104 classes a CI rule *names* is what makes it cheap, while a **discovery** view's roots are the classes
  absent from the curated tables. `db/021` re-issues `db/012`'s now-false "THE ONE PLACE" comment. Do not merge them
  without re-measuring.
- **Spec §5.0's partial unique index cannot work here** — a correction keeps the SAME natural key, so both rows are live
  between the INSERT and the UPDATE (`db/007` met this on `question_state` first). One **deferred** constraint trigger,
  generalised over the key, now carrying five tables (`db/027` is the fifth); published as
  `decisions/correcting-a-curated-assertion.md`. **A test that never commits proves nothing** — force it with `SET CONSTRAINTS
  ALL IMMEDIATE`, which switches the mode for the rest of the transaction.
- **FOUR ruling columns the spec does not have, all because NOTHING COULD BE RETIRED** (supersession must point at a later row
  with the same key, so every correction leaves one live): `interaction_group_member.satisfies_role`,
  `additive_effect.accumulates`, `interaction_group_assertion.applies` (`db/023`, retiring a GROUP as a whole), and
  `class_expansion_policy.decision = 'withdrawn'` (`db/027`). None has a DEFAULT. **Ask what WITHDRAWING one of a table's
  statements looks like before deciding it needs no ruling column** — three rounds have now had to.
- **Promotion REGRADES, never RECRUITS**: contributors come from membership, promotions are LEFT JOINed on.
  **`gap_ungraded_contribution` lists classes with NO row at all, not classes graded `minor`** — an explicit `minor` is
  *reviewed* and leaves the queue. Probed: *Decreased Coagulation Activity [PE]* has **83 contributors**, 3 promotions regrade
  9, the queue is **6, not 83**.
- **`group_fires` returns False on an empty required set** (`set() <= anything` is true, so the subset test would fire a
  fully-retired group on every regimen). **First COMPOUND `gap_key`** (`CLASS:a/CLASS:b`): one contributor class can be sound
  for one effect and a no-op for another, so folding onto either half hands two gaps one immortal `question_uuid`. Pinned per
  kind.
- **MEASURE RECURSION AGAINST A REAL DAG OR DO NOT MEASURE IT.** `gap_ineffective_contribution` named `class_subtree` twice
  inside a **correlated** `NOT EXISTS`, re-running the 22,754-row closure **per curated row**: 400 promotions cost **59 s**,
  **465 ms** after `db/024` hoists the walk out, identical rows. A synthetic probe looked fine because its fixture had **no
  edges**. **The verdict is per (effect, contributor) PAIR.**
- **GENERIC MUST NOT MEAN UNINDEXABLE.** `db/020`'s single-live trigger compared `to_jsonb(t) @> $1`: servable by no index,
  and FOR EACH ROW, so a bulk load went **quadratic** (2,000 rows **5,773 ms**). Rebuilt as one equality predicate per
  natural-key column over partial `<table>_live_key` indexes — **42 ms, linear**. **Nothing but the trigger reads those
  indexes**, so a test names each one (`db/027`'s included).
- **`gap_uncurated_threshold` counted the wrong population** and cleared on curation that reviewed nobody. The gate is now
  `ungraded_member_count >= threshold_total`; an explicit `minor` still clears the members it *reaches*, and **an effect with
  fewer contributors than `threshold_total` drops out** until an ingest brings members in.
- **`interaction_group_member_moiety` is deliberately NOT unique** on (group, role, moiety): a moiety reached through both a
  class and its descendant appears once per route, because `via_class` is what a curator needs to correct a member. Safe — the
  consumer takes a SET of roles — but its sibling `additive_effect_contributor` *promises* uniqueness. Now in the `COMMENT ON`
  and asserted. **And a test whose fixture does not build the case its docstring describes proves nothing**: two threshold
  tests asserted over an effect with **no members at all**.

## The ingest-operability round (#16, #47) — `db/025`–`db/026`

Spec: [ingest-operability](superpowers/specs/2026-08-02-drugref-ingest-operability-design.md). A crashed ingest now leaves a
trace, and an ingest is runnable outside a test. `provenance.py` is the ONLY file under `src/drugref` that writes a run record
— two contract tests grep for `INSERT INTO drugref.ingest_run` and `SET finished_at`. `chebi.py` gained the
try/rollback/logging the other five have. Measured on a fresh `drugref_ops` (**110.37 s**, figures in ROADMAP): every prior
count unchanged, `loaded_release` **4** rows with both MED-RT writers.

**Traps a future change can still break.**
- **`open_run` COMMITS its row; `finish_run` deliberately does NOT.** Symmetry would let `finished_at` be true about work that
  later rolls back. Two transactions on one connection. The window starts at `open_run`, and three orchestrators parse BEFORE
  it — a crash during MeSH's 750 MB parse still leaves no row.
- **`writer` is NOT NULL with no DEFAULT, and `'unattributed'` is not a writer** — it marks rows nothing can attribute
  retrospectively. A new orchestrator adds its value to `db/025`'s CHECK **and** to `provenance.WRITERS`.
- **`loaded_release` is per `(source, writer)`, not cosmetically**: folding it onto `source` re-hides the MED-RT staleness
  split (#39 one layer up). **`ingest_run_incomplete` could only ever have been EMPTY before this round.**
- **The chain's globs error on zero AND on several matches**, and every selected step's inputs resolve before any step runs.
  **The UNII glob names `UNII_Records_*.txt`, NOT `UNII_Names_*.txt`** — Names carries none of the moiety gate's four
  membership signals, and the round shipped the wrong one until the measurement ran. Steps resolving to the SAME file must
  agree on the tag, or identical bytes enter `ingest_run` as two releases — **and that guard now blocks the documented
  four-source invocation, #60**. The tag is **stated, never parsed from a filename**.
- **`drugref migrate` cannot report success having applied nothing.** From a wheel it used to: no `.sql` shipped, and
  `Path.glob` on a missing directory is silent. `db.migration_dir()` prefers the packaged copy, falls back to the checkout,
  and raises `MissingMigrationsError` when neither holds one — before touching the ledger.
- **`gap_unmatched_ingredient`'s tie-break now states its own reason.** `db/026`'s fourth `reason` is
  **`contraindication_class`, NOT the `class_contraindication` #47 proposed** — that string sorts BEFORE `classification` and
  would invert db/018's, whose *other* justification was already false (**0 of 4,389 rows carry a name**). Verified by
  mutation.
- **THREE defects in this round's own PLAN text, found by measuring** — the writer count ONCE (the *second* occurrence
  was in `db/026`, a migration), an error-message assertion contradicting its own test, and the UNII glob — each fixed
  in the code and left standing in the plan until the final review. **Plus TWO claims in `cli.py`'s own comments**: the
  docstring's DB-free boundary was drawn at `main`, the LAST function in the file, so the four `_handle_*` entry points
  and six `_run_*` wrappers fell on the wrong side of it — scoped to the argument layer, which is DB-free but **NOT
  filesystem-free**, since `resolve_inputs` globs; and **the step order is NOT a dependency order** — only UNII-first
  is, and the test asserting `medrt` before `mesh-relations` as a dependency was removed rather than left true by
  passing.

## The expansion-policy history round (#35) — `db/027`

Spec: [expansion-policy history](superpowers/specs/2026-08-03-drugref-expansion-policy-history-design.md). The last curated
table edited in place — and the one that **gates recall** — takes Plan C's overlay floor: surrogate `policy_id`, one-way
`superseded_by`, both generic trigger functions reused with no new PL/pgSQL — `forbid_overlay_rewrite` as `db/020` wrote it,
`forbid_multiple_live_assertions` as **`db/023`** rewrote it (equality predicates; `db/020`'s `jsonb` body was unindexable),
over a partial `class_expansion_policy_live_key` index that only pays off against `db/023`'s. Measured on a fresh
`drugref_policy`: `ddi_candidate_pair` **21,664** · `gap_dead_by_expansion_policy` **1** ·
`gap_unreviewed_expansion_root` **0** · `open_question` **18,834** · the **14** seeded decisions all live and binding ·
`expansion_policy_unresolved` **0**.

**Traps a future change can still break.**
- **`withdrawn` is NOT `allow`.** The third `decision` value means *no current judgement*, so the class returns to
  `gap_unreviewed_expansion_root`; folding the two together silently retires a question nobody answered. It exists because
  supersession alone retires nothing (Plan C's finding, a fourth time) and because `medrt_run`'s "re-key or withdraw" warning
  advised a `DELETE` the floor now refuses.
- **The view is `_current` (binding), NOT `_live` (unsuperseded).** A withdrawn row is live and does not bind; the writer
  deliberately asks the other question, in exactly one place. Merging them breaks withdrawal.
- **FOUR readers, one view** (`ddi_candidate_pair`, `gap_unreviewed_expansion_root`, `gap_dead_by_expansion_policy`,
  `expansion_policy_unresolved`), pinned from `pg_depend` by `test_only_the_current_view_reads_the_policy_table_directly`;
  a fifth naming the base table fails there; reverting one costs **233** ruled-out pairs. Also keeps the `LEFT JOIN` 1:1.
- **The natural key is deliberately NOT unique**, and nothing says so but a partial index and a deferred trigger. Adding
  `UNIQUE (source, source_code)` back "for safety" forbids every correction. **`db/010`'s now-false tier prose is corrected in
  `decisions/expansion-policy-is-append-only.md`** — not in the migration (applied, immutable).
- **Provenance stays `reviewed_by`/`reviewed_against`/`reviewed_at`**: Plan C's `ingest_run` triple is a decision against, not
  an oversight — the seed rows are written by a migration and have no run, and `source` here means *who defines the class* and
  is half the natural key.

## The policy-surface debt round (#59, #60, #61, #63) — `overlay.py`, `cli_policy.py`

Spec: [policy-surface debt round](superpowers/specs/2026-08-05-drugref-policy-surface-debt-round-design.md). Four follow-ups
the expansion-policy history round filed against itself, cleared together; **no SQL and no ingest logic changed**, which is
why every published figure below had no licence to move. **#59** promotes the insert-then-supersede rule — three hand-written
copies once `db/027` gave `interactions.py` its own — into one primitive, `overlay.supersede(conn, table, pk_column, new_id,
key_columns, key_values)`. Task 1 **deleted** `accumulation._supersede`; its four call sites (`curate_effect`,
`grade_contribution`, `assert_group`, `set_group_member`) now call `overlay.supersede` directly, alongside
`questions.set_state` and `record_expansion_decision`; `test_only_overlay_points_a_row_at_its_successor` greps `src/` for `"SET superseded_by"` and
asserts the only file is `overlay.py`. **#60** lets `IngestStep` declare an input `secondary` — read but not dated — so
`check_release_agreement` stops comparing `mesh`'s and `mesh-relations`' tags on the `desc*.gz`/`supp*.gz` files they share,
and the documented `drugref ingest chain --unii-release … --medrt-release … --mesh-release … --mesh-relations-release …`
stops refusing itself. **#61** gives an operator `drugref policy record|withdraw|show`, in a new `cli_policy.py` split out of
`cli.py` to hold CLAUDE.md's ~500-line rule.

**Measured on a fresh `drugref_policy_cli`, through the exact chain invocation #60 says is refused** — it ran, in
**113.99 s**: `ddi_candidate_pair` **21,664** · `open_question` **18,834** · `gap_dead_by_expansion_policy` **1** ·
`gap_unreviewed_expansion_root` **0** · `expansion_policy_unresolved` **0** · `class_expansion_policy` **14 / 14** ·
`loaded_release` **4** · `ingest_run_incomplete` **0** — all unchanged. `drugref policy withdraw` on the seeded
`N0000009020` moved `gap_unreviewed_expansion_root` 0 → 1; `policy show` on that code printed both rows, oldest first,
the live one marked `*`. **844 tests** at the end of the branch — 810 at branch start, 831 at the measurement above,
+4 from the whole-branch review, +9 from the PR-#64 review round whose traps are the last five bullets below.

**Traps a future change can still break.**
- **The `secondary` exemption filters the CLAIM, never the read.** `mesh-relations` still *reads* `desc*.gz`/`supp*.gz` in
  full — MED-RT's `to_code` resolves against them exactly as before — it just does not *date* them, so
  `check_release_agreement` stops comparing `mesh`'s and `mesh-relations`' tags on that one shared pair of files.
  A future change that mistook the exemption for permission to skip *resolving* a secondary input, rather than merely not
  dating it, would break the orchestrator on the first ConceptUI it could not look up.
- **`medrt_run.py` names `drugref.class_expansion_policy` in PROSE, not SQL** — the operator warning that tells them the
  table is append-only and names the two functions that can revise it (`withdraw_expansion_decision`,
  `record_expansion_decision`). `POLICY_TABLE_NAMINGS` in `tests/test_overlay_contract.py` counts it deliberately
  (`medrt_run.py: 1`, `interactions.py: 3`) — a grep that special-cased the sentence on the theory that a warning is not a
  "read" would silently stop catching a real fourth SQL reference landing beside it. The pin matches by **regex with a
  negative lookahead** (`r"drugref\.class_expansion_policy(?!\w)"`), not `str.count`, because `class_expansion_policy_current`
  — the one approved VIEW read — **contains the base-table name as a prefix**: a substring count would read that view read as
  a base-table read, which is exactly the substitution the pin exists to catch (a Task 3 plan defect, caught before it
  shipped, not after).
- **`record_expansion_decision` still accepts `withdrawn`; only the CLI refuses it.** Rejecting the value in the library
  would put a member of db/027's decision vocabulary back into a second place (Python, beside the CHECK constraint); its
  docstring says so instead. A caller reaching for `interactions.record_expansion_decision` directly from Python — which
  `_handle_policy_record` deliberately does not do, refusing `--decision withdrawn` before any write — still bypasses both
  of `withdraw_expansion_decision`'s guarantees: `NoLiveDecisionError` on a class with no live row, and carrying
  `class_name` forward into the audit trail. The door is left open on purpose, not by oversight.
- **`except CheckViolation` MUST NOT go on `cli.main`'s `try`, and this round put it there and had to take it back.**
  That `try` wraps EVERY handler, ingest included, and the same exception means opposite things on the two surfaces: from
  `policy` the failing value came off the command line, so one clean line is right; from an ingest it is a **defect in
  drugref** — a parser feeding a value `db/006` or `db/014` forbids — where the traceback naming the writer is the most
  useful thing the process prints, and exit 2 additionally reports a drugref bug as *operator error*. Only the caller can
  tell them apart, so the catch lives in `cli_policy._write`, which is also the only place that knows the value was typed.
  `test_main_does_not_swallow_a_check_violation_from_an_ingest` pins it by stubbing a handler that raises.
- **`cli_policy._write` ROLLS BACK before it reads the catalogue, and the order is load-bearing.** The violation aborts
  the transaction, so the `pg_get_constraintdef` lookup that makes the message actionable would itself raise
  `InFailedSqlTransaction` — turning a tidy rejection into the traceback the guard exists to prevent.
  `test_the_connection_survives_a_rejected_write` fails without the rollback. **The message quotes the CHECK rather than
  restating it** (`db.constraint_definition`): an operator learns the accepted values *by reading the constraint*, so the
  message is actionable AND `db/027` stays the vocabulary's one home. Never hand-write the values into a message.
- **"It expands" is unconditional; "it raises a question" is NOT** — and `policy show` stated the second flatly for a
  whole round, 25 lines below the comment in `_handle_policy_withdraw` explaining why it does not follow.
  `gap_unreviewed_expansion_root` **also requires a `substance_class` row** for the code, so a class no loaded release
  defines (or an operator's typo, the likelier way to reach that line) raises nothing. Both messages are now hedged
  identically. A test had pinned the false sentence, which is how it survived review.
- **`argparse`'s `required=True` checks PRESENCE, not content, on the READ path too.** `--source '' --code ''` satisfies
  `_Parser`'s both-or-neither rule — `''` is present — then matched nothing and printed the no-decision answer about a
  class that cannot exist, at exit 0. `_reject_blank` now guards `show` as well as the writers. Nothing is corrupted on a
  read; being told something false is the part worth refusing.
- **An operator warning that names a command must name every REQUIRED flag.** `medrt_run`'s remedy trailed off in `...`,
  and all five of `policy withdraw`'s flags are `required=True` — so an operator following the warning literally met an
  argparse usage error instead of the remedy. `test_the_unresolved_warning_names_every_flag_its_remedy_needs` extracts the
  backtick-quoted command **from the warning**, fills the `<placeholders>`, and parses it, so a flag added later fails the
  test rather than quietly going unmentioned.
- **The `policy` handlers COMMIT; the library functions do not.** `_handle_policy_record`/`_handle_policy_withdraw` reach
  `conn.commit()` through `_write` after `interactions.record_expansion_decision`/`withdraw_expansion_decision` return — consistent with
  every other CLI handler, but unlike the DB-gated tests of the library layer, which rely on the `conn` fixture's rollback.
  **Committed policy rows cannot be deleted** — the overlay floor refuses it, same as Plan C's other four tables — so
  `tests/test_cli_policy.py`'s `committed` fixture restores in a `finally` by **recording a further correction** (a fresh
  `deny` on the seeded root `N0000009020`, `Dermatologic Activity Alteration [PE]`), never a `DELETE` or a `ROLLBACK`.

**This round reopened [#61](https://github.com/cairn-ehr/drugref/issues/61)**, closed in error by `92baaea`: its own commit
body reads "Filed rather than fixed: #61 …", and GitHub's linker accepts `fixed:` immediately before a number as a closing
keyword — the sentence *declaring the issue unfixed* is what closed it. Reopened after checking `build_parser` directly:
nothing #61 asks for existed at that commit. **The fourth occurrence of the sweep-closed-but-unfixed pattern** (#31, #35,
#40, #61) — and the first where the author was deliberately writing prose to dodge it, which is what makes it worth
restating rather than assumed solved: keep the number away from `close`/`fix`/`resolve` **in any inflection** (closes,
closed, fixing, fixes, resolved, …), because the linker matches on **token adjacency**, not meaning. A colon in between does
not save you.

## Slice 3 — the composition tree (BUILT 2026-08-05, `db/028`, measured end to end)

Spec: [slice-3 composition tree](superpowers/specs/2026-08-05-drugref-slice-3-composition-tree-design.md). Published
record: [GSRS relationship direction](../docs-site/docs/decisions/gsrs-relationship-direction.md). The first new
external source since 2b, so **rule 6 was a gate, not a formality**: GSRS data is **CC0 1.0**, software **Apache-2.0**,
cleared BEFORE anything was downloaded (caveat above); `NOTICE` carries the entry. Shape: **composition edges over ONE
registry** — no second identity, no dual residence. `substance_composition (substance_unii TEXT, component_moiety uuid,
relation, is_active_component)`, a rebuildable `GSRS`-keyed projection. Code: `ingest/gsrs.py` (pure streaming parser),
`composition.py` (single writer), `ingest/gsrs_run.py` (orchestrator), `gsrs` chain step.

**MEASURED on the assembled chain** (UNII 26Feb2026 → MED-RT 2026.07.06 → MeSH 2026 → GSRS 2026-02-26, 2026-08-05,
137 s): **8,671 rows** (7,962 salt + 709 solvate) over **7,377 composites** and **4,433 component moieties**; **4,433
moieties (22.8%) gain ≥1 child** — 4,092 (21.1%) of them through a salt edge, which is what the earlier "4,092" figure
counted. `is_active_component` **TRUE 5,011 / FALSE 992 / NULL 2,668**; **gap kind 12 over 2,245** composites.
**Nothing pre-existing moved**: `ddi_candidate_pair` **21,664**, `substance_moiety` **19,438**, and `open_question`
grew by exactly the new gap rows, 18,834 → **21,079**.

**The predicted activity split was refuted, and the row set was not.** Design measurement predicted TRUE 5,029 / FALSE
1,001 / NULL 2,641 and 2,226 gap-12 composites. The edge set matched to the row; only the split moved. Cause,
reproduced exactly against the dump: the prediction scripts used a **global** `unii → active moieties` lookup, while
`gsrs_run.py` only lets a ruling come from the composite's **own record** (that is what the mirror-merge is keyed on).
The two disagree on exactly the **27** in-registry edges GSRS stores *only* on the component's record — 18 TRUE and 9
FALSE become NULL, leaving 19 more composites wholly unruled. The shipped reading is the conservative one (it only ever
*adds* NULLs, never downgrades a ruling), so it under-claims activity and over-reports the gap. **Left as-is
deliberately during a verification round; whether the composite's own `ACTIVE MOIETY` should rule on an edge that
arrived from the other end is filed as [issue 69](https://github.com/cairn-ehr/drugref/issues/69).**

**Filed by this round, deliberately not fixed here** (each errs toward under-claiming or over-counting, the safe
direction, and each is its own round): [issue 69](https://github.com/cairn-ehr/drugref/issues/69) above ·
[issue 70](https://github.com/cairn-ehr/drugref/issues/70) **354 all-false composites are reachable by nothing and
queued by nothing** — `moiety_active_in_composite` propagates only `TRUE`, `gap_unruled_composition_activity` queues
only a composite where *every* component is `NULL`, and a composite whose components are all `FALSE` satisfies
neither predicate · [issue 71](https://github.com/cairn-ehr/drugref/issues/71) **8,163 of 16,834 normalised edges are
dropped for an unregistered component** and counted only as the transient `components_not_in_registry` integer, never
persisted as a worklist the way gap kind 12 is.

**Traps, all measured before the first line of code.**
- **THE DIRECTION CONVENTION IS INVISIBLE WHEN WRONG** (full statement in the upstream-errata section above). Inverted, it
  yields a *fully populated, entirely wrong* table that no aggregate count would flag. It lives in ONE function, pinned by
  the mirror check AND the solvate functional check. **Do not delete either test.**
- **`ACTIVE MOIETY` IS A DISCRIMINATOR, NEVER AN EDGE, and never a substance-equivalence join.** The temptation is
  specific: it *appears* to close issue 33. It also asserts that **levomefolate magnesium** is interchangeable with magnesium
  sulfate — 35 substances share `MAGNESIUM CATION`, **27 of them drugref moieties**. Same shape as the withheld
  sulfonamide expansion. **Its 23,944 self-edges (71%) are not compositions either**; filtering them is load-bearing, or
  every moiety becomes its own component.
- **`is_active_component` NULL means UNRULED, not inactive** — no DEFAULT. `allow` ≠ absent (`class_expansion_policy`) and
  `withdrawn` ≠ `allow` are the same lesson; this is the fourth table to need it. Only **6,696 of 14,090** salts declare an
  active moiety at all. Where they do, it IS one of their own components **95.1%** of the time (6,368/6,696), selecting a
  strict subset in 589 multi-component cases — that is what separates drug from counterion. The 328 whose declared active
  moiety is *not* among its components are counted, not repaired.
- **`substance_unii` is deliberately NOT a foreign key.** Adding one deletes **4,425** composites — two-thirds of the
  table — and re-opens the second-registry question the design exists to avoid.
- **3,195 GSRS salts are ALREADY drugref moieties, and that is not a bug to fix.** `moiety_uuid` is immortal and the gate
  is strictly monotone, so they cannot be demoted; a row may be a moiety *and* have components. (42 are both salt and
  parent.) Relatedly, **3,631 drugref moieties carry an `ACTIVE MOIETY` edge to something else** — GSRS would not call
  them active moieties. That is a **moiety-gate** question (#26's lineage), not a composition one.
- **`parent_moiety_uuid` was refuted, not simplified away**: 1,089 salts (7.7%) have >1 parent, 800 in-registry.
- **The slice does NOT close issue 33 or issue 30** — ROADMAP's annotations are withdrawn. Nothing in GSRS points at
  `DE08037SAB` (**0 inbound references** across 173,080 records); a composition hop recovers **94 of 706** MeSH UNII keys
  and **68 of 1,977** CAS keys, and the magnesium flagship is not among them. Issue 30 stayed unmeasured through the
  build: the verification database carries no PBS release. **Re-measure before quoting either issue** — the fourth round
  to find an issue text stale.
- **The activity split is scope-sensitive, and the published figures are the pipeline's.** Any re-measurement done with a
  standalone script that maps `unii → active moieties` globally will read 5,029/1,001/2,641 and 2,226. That is a
  different question from the one the projection answers; see the erratum above before treating a mismatch as a
  regression.

### The PR #72 review round (2026-08-06) — what a full test suite was not testing

Five findings, all fixed on the branch; 894 → **897 tests**. The two worth carrying forward:

- **A 100%-green suite did not test the slice's central semantic.** `is_active_component` exists to keep *"the release
  ruled on nothing"* (NULL) distinct from *"this component is inactive"* (false) — the distinction the whole read path
  and gap kind 12 are built on. Deleting the `if record.active_moieties else None` guard in `gsrs_run.py`, which
  collapses every unruled edge to `false`, **passed all 895 tests**. So did replacing `unruled_composites` with a
  literal `0`. Both are now killed by two orchestrator tests. **The lesson is about WHERE the assertions were:** the
  writer had `test_null_is_stored_as_null_not_false` (passes an explicit `None`, so an orchestrator-level mutation is
  invisible to it) and the parser had its own NULL tests — the two ends were covered and the *decision between them*
  was not. A summary field returned by an orchestrator and printed by the CLI is a claim; assert it.
- **A fixture record cut for a purpose was not serving it.** `make_gsrs_subset.py` kept PHYTATE SODIUM as the genuine
  gap case and `7IGF0S7R8I` alongside it "so the gap-view edge resolves against the registry" — but `test_gsrs_run`'s
  `registry` fixture never registered `7IGF0S7R8I`, so the orchestrator dropped the edge as unresolved and the case
  reached the gap view **never**. The view was populated in that test only incidentally, by ~98 chlortetracycline salts
  nothing asserted on. **A fixture comment stating a role is not evidence the role is exercised**; the gap view had
  rows, which is exactly what made it look covered.

Also fixed: `test_gsrs_run`'s `registry` committed moieties and claims that outlived the file (nothing broke only
because `test_ingest_run.py` sorts later and TRUNCATEs those tables) — and note that a committed seed on the
append-only floor **cannot** be unpicked with DELETE at all, since `db/001`/`db/005` triggers RAISE on it; TRUNCATE is
the only tool. `GsrsRecord.display_name` was parsed for every one of 173,080 records with no consumer, and removed.
`records_in_release`/`edges_in_release` were renamed `records_with_unii`/`edge_statements_read` — the first skipped the
5,078 records with no `approvalID`, the second double-counted mirrored edges, and both names claimed the release total.
Deferred as [#73](https://github.com/cairn-ehr/drugref/issues/73): both views over `substance_composition` read every
source at once, unfixable in `db/028` because it is applied and immutable.

## Slice 5c.1 — the curated overlay's assertion shape (`db/029`, measured 2026-08-06)

Spec: [slice-5c.1 curated
overlay](superpowers/specs/2026-08-06-drugref-slice-5c1-curated-overlay-design.md). Published record:
[curating a drug–condition pair](../docs-site/docs/decisions/curating-a-drug-condition-pair.md). Plan C's overlay
mechanism (surrogate key, deferred single-live check, one-way supersession, no new PL/pgSQL) gets its sixth and
seventh tables: `curated_interaction`, keyed on the class-level `CI_MoA`/`CI_PE` **rule**
(`subject_moiety_uuid, object_class_uuid, relationship`), and `curated_condition`, keyed on the (drug, condition)
**pair** (`subject_moiety_uuid, object_condition_uuid`) — **deliberately without `relationship`**, because the
same pair can carry both an indication and a contraindication (168 cases, issue #51) and keying on the predicate
would write that one judgement twice with nothing to stop the copies disagreeing. Two inner-joined read views
(`curated_ddi_pair`, `curated_condition_ruling`), two gap views (`gap_uncurated_interaction_rule`,
`gap_uncurated_condition_contradiction`), one operator check (`curated_target_unresolved`). **Ships EMPTY** — no
seed, no curation content; curation is step 8.

**MEASURED on a fresh `drugref_5c1`**, built from the real releases (UNII 26Feb2026 → MED-RT 2026.07.06 → MeSH
2026 → MeSH-relations 2026.07.06 → GSRS 2026-02-26), chain wall-clock **127.5 s**. Every count this slice must not
move, held exactly: `ddi_candidate_pair` **21,664** · `substance_moiety` **19,438** ·
`moiety_condition_contraindication` **9,471** · `moiety_condition_indication` **14,674** ·
`condition_contraindication_expanded` **192,161**. New: `gap_uncurated_condition_contradiction` **168** (exact
match to issue #51's own figure) · `gap_uncurated_interaction_rule` **595** · `curated_target_unresolved` **0** ·
`curated_ddi_pair` **0** · `curated_condition_ruling` **0**. `open_question` **21,842** = the pre-slice **21,079**
plus exactly 168 + 595 = 763, and no other `gap_kind` moved (each of the other ten reproduces its Slice-3 figure
exactly). Test suite: **936 passed**.

**`gap_uncurated_interaction_rule`'s 595 is not the design spec's own "~739", and that is the spec's prose being
approximate, not a defect in the view.** 739 is the raw MED-RT terminology-level `CI_MoA`/`CI_PE` count *before*
the moiety gate — quoted in `curation.py`, the design spec and ROADMAP, but never pinned by a test or measured as
`class_contraindication`'s actual row count. The real, gated figure (`MedrtSummary.contraindications`) is **635**.
Of those, 40 rules pair with **nobody** in `ddi_candidate_pair` and are excluded by the view's own `INNER JOIN`
(its `COMMENT ON` says so: grading a rule with no reachable pair is a provable no-op) — 635 − 40 = 595 exactly.
All 40 are already explained by the two pre-existing "this class has no reachable members" gap views from the
interaction debt round: 39 via `gap_unpopulated_contraindication`'s 13 classes, 1 via
`gap_dead_by_expansion_policy`'s single class — the same "13 classes / 39 dead rules" finding that round already
measured, confirmed again here from a different angle. **Re-measure "~739" against `class_contraindication`
directly before quoting it as a row count** — the fifth time this project has found an issue or design-doc figure
stale on re-measurement (5b, the interaction debt round, #50, #53's round, and now this one).

**The list of where "~739" was quoted above was itself incomplete: `db/029_curated_overlay.sql` had it twice,
once in the section-1 header comment and once inside `COMMENT ON TABLE drugref.curated_interaction` — the
second one would have shipped into the catalog as a permanent, wrong figure the moment this migration applied
outside its branch.** Found by the final whole-branch review of slice 5c.1 and corrected in the same migration
(sanctioned while `db/029` is unapplied everywhere but this branch, per its own section 6 precedent) to 635
rules, of which 595 reach the worklist, with the distinction stated explicitly rather than repeating a single
approximate figure.

**THE FINAL WHOLE-BRANCH REVIEW (`1b92e99`) found six; three belong here rather than only in a commit message,
because two later paragraphs count from them. Suite 936 → 940.**

1. **BLOCKING — `curated_interaction.relationship` shipped as `CHECK (relationship IN ('CI_MoA', 'CI_PE'))`,
   under a comment claiming it mirrored `class_contraindication`'s own CHECK. That claim was false**: `db/006`
   finding 1 had *replaced* that CHECK with an FK into `ci_axis` precisely so the CI vocabulary would have one
   home, after `db/004` shipped it as a CHECK **plus** a matching `CASE` inside `ddi_candidate_pair` and
   widening only the CHECK inserted rows that expanded to zero pairs with no error (an unmapped `CASE` arm
   yields NULL and joins nothing). A hardcoded CHECK on the grading table is that same second list again, and it
   drifts in **both** directions the moment `ci_axis` grows a third axis: `class_contraindication` accepts the
   new predicate by FK, `ddi_candidate_pair` projects candidates for it and `gap_uncurated_interaction_rule`
   **queues them** — while `curated_interaction` refuses the very row a curator is being asked to write, so
   `curation.py` cannot answer its own worklist. Now the same FK, `curated_interaction_relationship`, and pinned
   by `test_an_unknown_relationship_is_refused_by_the_foreign_key`. `ci_axis` holds exactly **two** rows today
   (`CI_MoA`→`has_MoA`, `CI_PE`→`has_PE`, both `expands_descendants`), which is why the CHECK and the FK were
   indistinguishable by every test and every count in this section.
2. **Two mutations survived all 936 green tests, and both were the SAME property: `relationship` is part of
   `curated_interaction`'s natural key.** Dropping it from `curated_interaction_single_live`'s trigger arguments,
   and dropping it from `curated_interaction_live_key`'s index column list, each left the suite green — the
   first is now killed by a behavioural coexistence test
   (`test_two_live_rows_differing_only_by_relationship_may_coexist`), the second by an explicit column-list
   assertion, now inside the shared `assert_live_key_index` fixture that issue 74 built. Each was verified by
   hand-mutating `db/029`, confirming the new test fails, and reverting. **These are the "two" the paragraph
   below counts from.**

Also from that round, and recorded above rather than here: the stale `~739`, in `db/029` twice (once inside a
`COMMENT ON TABLE`) and in `curation.py`.

**The PR-review round (PR [#77](https://github.com/cairn-ehr/drugref/pull/77)) found a THIRD untested
load-bearing clause and one latent count defect. Suite 940 → 943.**

1. **`register_from_gaps`' retention guard names five tables; only the `curated_condition` clause had a test.**
   Deleting the `curated_interaction` clause passed all 940 tests. The sequence it breaks is the ordinary one,
   not a contrived one: grading a rule is exactly what retires it from `gap_uncurated_interaction_rule`, so the
   first curated interaction row citing its question makes the next ingest `DELETE` that question, cascade into
   an append-only table, and abort the whole transaction with `RaiseException` out of `forbid_overlay_rewrite`.
   Measured both directions before and after. **This is the sixth round in which the slice's own load-bearing
   property was the one thing no test killed** — and the second within slice 5c.1 alone, after the whole-branch
   review's two. The lesson has stopped being "remember to test the guard" and become **"for every clause in a
   multi-table guard, name the test that kills its removal, one per clause."**
2. **`gap_uncurated_interaction_rule.pair_count` was `count(*)` over a join that omits `source`, while
   `class_contraindication`'s primary key includes it** (`db/006` widened it there deliberately, so a second
   authority's row is not swallowed). The join omits source correctly — drugref's judgement is about the clinical
   fact, not about who asserted it — so the two are individually right and jointly wrong: a rule asserted by two
   authorities counts every candidate row once per source. Correct today **only** because
   `class_contraindication_source` admits `MED-RT` alone, which is exactly what made it invisible. Measured under
   a second source: **4 where the answer is 1**. Now `count(DISTINCT p.partner_moiety)` — a no-op against current
   data, and what keeps the measured `sum(pair_count) = 21,664` partition true when a second authority lands. The
   test drops the source `CHECK` inside the test transaction (the `conn` fixture rolls back) to reach the shape,
   rather than waiting for a future migration to discover it.
3. **`question_uuid` was an unindexed foreign key on both curated tables.** Postgres indexes the *referenced*
   side of an FK automatically and the *referencing* side never, and this column has two per-ingest readers: the
   retention guard's `NOT EXISTS` runs once per gap kind (fourteen a run), and the `ON DELETE CASCADE` must find
   the rows before the append-only trigger can refuse the delete. `question_source_check_by_question` (`db/007`)
   exists for exactly this. Added `curated_interaction_by_question` and `curated_condition_by_question`, each
   asserted by name — nothing but the planner reads them, so they look unused to a catalog sweep.

**`db/029` was therefore edited in place a SECOND time, and any database that already applied the earlier
version must be rebuilt.** `db.apply_migrations` refuses a file whose checksum changed after it was applied
(`RuntimeError: migration ... changed after it was applied`) — so `drugref_5c1` and any dev database carrying
the previous `db/029` need `DROP SCHEMA drugref CASCADE` and a re-apply. The test suite is unaffected: its
`_migrated` fixture drops the schema and re-applies every session, which is also why all three defects above
were reachable by test at all.

**POST-MERGE RE-MEASUREMENT (2026-08-08, `drugref_5c1m`) — because every figure above was measured on a schema
that the review rounds then edited twice, and the MERGED `db/029` had never been run end to end.** The next
session found `drugref_5c1`'s ledger recording the pre-merge checksum where the merged file hashes to something
else — the drift the paragraph above predicted, sitting in the database § Repo facts pointed readers at. **Both
checksums are quoted in full, once, in § Repo facts** (at the twelve hex characters `db.apply_migrations` itself
prints, so the documented value can be compared to its error text as a string rather than eyeballed). Rebuilt
from scratch on the merged file: chain wall-clock **144 s**, against the **127.5 s** above.

**That +13% is NOT explained here.** Same five releases, same order, same machine — and no control was taken: no
repeat run on `drugref_5c1m` for the run-to-run spread, no per-leg breakdown, nothing separating cache warmth
from real cost in a file that added an FK lookup and two indexes to the write path. An earlier draft of this
paragraph attributed it to "a warmer machine" and told the reader to treat both as "~2.5 min, not a regression";
that was reasoning presented as measurement, which the review of PR
[#78](https://github.com/cairn-ehr/drugref/pull/78) flagged and this round removed. Filed as
[#81](https://github.com/cairn-ehr/drugref/issues/81) with the three measurements that would settle it. This
section demands "three controls, not reasoning" of a view twenty lines below; it owes its own timings the same
standard.

**WHAT REPRODUCED, AND WHAT WAS NOT RE-RUN — the distinction matters, because "every figure above" was too
wide.** Reproduced **EXACTLY**: every count in the measurement paragraph above (listed there once, deliberately
not restated here) and all four ingest summaries (635 contraindications, 168 also-contraindicated pairs, 422
broadened, 8,163 components not in registry). **NOT re-measured on `drugref_5c1m`:** the five `EXPLAIN ANALYZE`
timings below, `gap_uncurated_interaction_rule`'s ≈2.7 s among them — and the suite figure, which is a property
of the repo rather than of any database (936 at the measurement above, **943** by the end of 5c.1's two review
rounds, higher since — the current number lives in § "How to run / test" below, and nowhere else). An operator told
this database holds *every* figure would query it for the 2.7 s or the 936 and conclude the schema had drifted
again.

**All four review fixes verified in the live catalog — three by inspection, `pair_count` by the only check that
can tell the two definitions apart.** `curated_interaction_relationship` is an **FK into `ci_axis`** (no
hardcoded CHECK) · `COMMENT ON TABLE` carries **635/595 and no `739`**, and is now pinned by
`tests/test_curated_interaction_comment.py` — the reason this round had to read it by hand is that nothing tested
it, and the next round will not have to · `curated_interaction_by_question` /
`curated_condition_by_question` both exist.

**`pair_count` needed `pg_get_viewdef`, because the row counts CANNOT distinguish the fixed view from the broken
one.** `max`/`sum`/`count` are **244 / 21,664 / 595** on *both* databases — identical, exactly as the finding
predicted, since `class_contraindication_source` admits `MED-RT` alone today. Which means an identical reading is
also precisely what an ABSENT fix produces, so on its own it is evidence of nothing: an earlier draft offered it
as the confirmation, and the review of PR #78 caught that. The discriminating check, run on both databases:
`pg_get_viewdef('drugref.gap_uncurated_interaction_rule'::regclass, true)` yields `count(DISTINCT
p.partner_moiety) AS pair_count` on `drugref_5c1m` and `count(*) AS pair_count` on `drugref_5c1`. **That is the
control, and the reason `drugref_5c1` is kept rather than dropped.** The fix is **LATENT, not cosmetic** — the
divergence arrives with the second authority. **Do not read the equality as evidence the fix was unnecessary.**

**`EXPLAIN ANALYZE` on all five new/touched views** — `curated_ddi_pair` (filtered on a subject that actually
carries a rule, per the brief's own warning against inventing a literal) **2.5 ms** · `curated_condition_ruling`
(filtered) **0.09 ms** · `gap_uncurated_condition_contradiction` **15.3 ms** · `curated_target_unresolved`
**0.10 ms** · `gap_uncurated_interaction_rule` **≈2.7 s** — three orders of magnitude above every other view here,
and the one db/024's own precedent says to check rather than reason about.

**Checked against db/024's shape, and it is NOT that shape.** `gap_uncurated_interaction_rule` names
`ddi_candidate_pair` exactly **once** (not correlated, not repeated inside a `NOT EXISTS`); the `NOT EXISTS`
against `curated_interaction` is a cheap indexed anti-join over an empty table. Three controls, not reasoning:
(1) `EXPLAIN ANALYZE SELECT count(*) FROM drugref.ddi_candidate_pair` — none of this slice's SQL involved — costs
**2.68 s**, statistically identical, so the cost is 100% inherited from the pre-existing view. (2) Raising
`work_mem` from the default to 600 MB removes the ~384 MB disk spill inside `ddi_candidate_pair`'s own `Sort` but
only drops the time to 2.47 s — genuine CPU cost (a ~3.78M-row intermediate before the DAG-scoped merge with
`subtree`), not a tunable spill artifact. (3) The recursive CTE appears once in the plan. **Why it's new:** every
previously published `ddi_candidate_pair` figure (2.876 ms, 3.1 ms — PROJECT-NOTES, #37) is for the *filtered*
lookup; every other view that reads the class DAG operates at the class grain via `ci_class_subtree`, never
through a full unfiltered scan of `ddi_candidate_pair`. This gap view is the first consumer to do that, and the
access pattern was simply never measured before. **Not fixed here**: the fix belongs inside
`ddi_candidate_pair`'s own definition (a prior slice's hot path whose row count must not move), and this project
has already measured and rejected building a second, differently-scoped implementation of the class DAG walk
(the "TWO WALKS DOWN `class_parent`" finding, Plan C section above) — doing that locally to dodge this view's cost
would repeat exactly that mistake. Filed as
[#75](https://github.com/cairn-ehr/drugref/issues/75).

**Traps a future change can still break.**
- **The gap views test for a LIVE row of any ruling; the read views test for a live ASSERTING row — and the two
  predicates are not interchangeable.** `curated_ddi_pair`/`curated_condition_ruling` require `applies` /
  `ruling <> 'spurious'`, because a NULL severity beside a real candidate reads as "reviewed and harmless."
  `gap_uncurated_interaction_rule`/`gap_uncurated_condition_contradiction` test only for the absence of *any* live
  row, because a `spurious` ruling or `applies = false` still means a curator looked and must leave the worklist.
  Unifying the two predicates breaks whichever end it is collapsed toward — the same `_current`-vs-`_live` lesson
  `db/027` learned on `class_expansion_policy`, now on a second subsystem.
- **The retention guard in `questions.register_from_gaps` now covers FIVE tables**, not three: `question_state`,
  `question_source_check`, `question_evidence` (Plan A), plus `curated_interaction` and `curated_condition`
  (this slice). Both new tables reference `open_question` with `ON DELETE CASCADE`, and `register_from_gaps`
  deletes a question whose gap has closed — but only when nothing cites it. Dropping either curated table from
  the guard's `NOT EXISTS` list means the first curated row on a gap that later closes hits the append-only
  trigger on `DELETE FROM open_question` and **aborts the whole ingest**, not just that one row.
- **Curated rows reference their candidate by natural key, never by foreign key**, because both candidate
  families are rebuildable projections and an FK would either block a per-source rebuild or cascade curator
  judgement away with it. `curated_target_unresolved` is the *only* thing that reports an orphaned reference —
  modelled on `expansion_policy_unresolved`, deliberately not a gap kind (an upstream-change signal for an
  operator, not a clinical question for a curator). It has no other reader; nothing else needs to know.
- **`curated_condition`'s key deliberately omits `relationship` while `curated_interaction`'s includes it.** Not
  an oversight, not a missed normalisation — the object class fixes the axis on the interaction side (an MoA
  class only ever takes `CI_MoA`) so mirroring the candidate key there costs nothing, while the condition side has
  no such fixed axis: the same pair can be indicated and contraindicated at once. "Make the keys match" is the
  wrong instinct here; see the decision record for the full argument.
- **`ROADMAP.md`/`PROJECT-NOTES.md`'s "signed overlay" wording is corrected to "signable"** (§ Architecture in
  one breath, below) — this round also caught and fixed one instance ef16b60 missed
  (`ROADMAP.md`'s Slice 5 intro) and one in the public docs site (`decisions/hybrid-store.md`'s title and body).
  **DDInter is CC BY-NC-SA and stays off the bundled ladder permanently** (ef16b60; unchanged by this round).

## The gates-that-do-not-fire round (issues 74, 66, 76) — 2026-08-08, no migration

Three issues with one shape: **a check that exists and never fires.** A lint rule never selected, a test that
could not fail, and a detector nothing called. No schema change, no migration — `db/029` is merged and frozen.
Suite **943 → 956**.

### Issue 74 — the live-key index tests could not fail

Seven tables carry a single-live natural-key partial index, and the tests protecting them asserted the property
in **three different strengths**: 5c.1's two checked existence + partial + non-unique + column list, the
accumulation suite's four parametrized ones checked existence + the `WHERE` substring, and
`class_expansion_policy_live_key` — the one the parametrized test never covered, because `db/027` added it four
migrations after Plan C's four — **counted the index by name and nothing else**.

**Measured, not reasoned**: with the index rebuilt as `CREATE UNIQUE INDEX … WHERE superseded_by IS NULL`, the
old accumulation-style assertion (`indexdef LIKE '%superseded_by IS NULL%'`) returns **true**, and the old
expansion-policy-style assertion (`count(*) = 1`) returns **true**. Five of the seven tests passed the mutation
that **forbids every correction the overlay exists to make** — a correction is briefly two live rows on one
natural key, and a partial index cannot be `DEFERRABLE`, so `UNIQUE` is enforced at statement time.

Fixed by moving all three properties plus the column list into **one** `assert_live_key_index` fixture in
`conftest.py` (shared via conftest, not cross-file import — the precedent commit `6621382` set), used by all
seven call sites. **Consolidating created a new single point of failure**, so the guard got a guard:
`tests/test_live_key_index_guard.py` mutates the real index inside the test transaction (Postgres DDL is
transactional; the `conn` fixture rolls back) and asserts the fixture rejects each of missing / non-partial /
UNIQUE / narrowed-columns, plus a control that it accepts the real index. **One test per property**, per the
standing rule 5c.1's PR review produced.

### Issue 66 — the lint gate ran nowhere

The issue said "ruff runs with default rules, so E501 is never checked". True, and **two things it did not say
were worse**: `ruff` was **not a project dependency at all** (`uv run ruff` fell through to a pyenv shim at
0.11.0, so the linter's version was whatever the developer happened to have), and **CI never ran ruff** — the
only workflow job was pytest. There was no lint gate to weaken.

**The measurement corrected the issue's premise.** "50+ lines exceed the ~88-column convention every file is
written to" is right for `src/` and wrong overall:

| bound | `src/` | `tests/` |
|---|---|---|
| 88 | **52** | **324** |
| 100 | 0 | 41 |
| 110 | 0 | 9 |
| 120 | 0 | 0 |

`src/` genuinely is written to 88; `tests/` is written to ~120 and never was 88. So: `[tool.ruff]` with
`line-length = 88` and `select = ["E", "F", "W"]`, `src/`'s 52 lines reflowed, ruff pinned into the dev group,
a `lint` job added to CI, and `tests/**` carved out of E501 via `per-file-ignores` with the debt filed as
**[#79](https://github.com/cairn-ehr/drugref/issues/79)** and a comment in `pyproject.toml` saying to delete the
block when 79 closes.

**`extend-exclude = ["downloads", "docs-site/site"]` retires a trap rather than working around it**: `ruff check .`
used to hang on the 2.05 GB GSRS dump, which is why every instruction in this repo said `ruff check src tests`.
The bare command now runs in **0.18 s**. (It had also been *accidentally* safe — ruff honours `.gitignore`, and
`downloads/` is gitignored — but that is not a thing to rely on.)

**TWO TRAPS THIS ROUND WALKED INTO, both worth the next reader's attention:**

1. **The `[tool.ruff]` header was missing from the first draft**, so `line-length` and `extend-exclude` sat
   inside `[tool.pytest.ini_options]`. **Nothing failed** — 88 is ruff's own default, so the lint run looked
   correct — and the only symptom was `PytestConfigWarning: Unknown config option: line-length` buried in test
   output. Now a standing rule above. Verified afterwards with `ruff check --show-settings`
   (`linter.line_length = 88`, `file_resolver.extend_exclude = ["downloads", "docs-site/site"]`) and a positive
   control: a deliberately 93-character file in `src/` **does** fail, and a 119-character line in `tests/` does
   not.
2. **An automated reflow destroyed a dataclass body.** The first script fell back to a bare-indent prefix when a
   line was not a comment, which makes a run of same-indent CODE look like a prose paragraph; it rewrapped
   `medrt.py`'s `MedrtSummary` fields into an unparseable blob. Caught by an IDE syntax diagnostic, then by
   `ast.parse` over every touched file. **Three files were reverted and redone by hand**, and the script was
   narrowed to comment blocks only.

**The reflow is provably content-preserving**: every NON-DOCSTRING string constant was compared between `HEAD`
and the working tree via `ast.parse` across all 16 touched files — Python folds implicit concatenation at parse
time, so splitting `"AAA BBB"` into `"AAA " "BBB"` is invisible while a lost or doubled space is not. **All 16
identical** (`cli.py` 119 strings, `questions.py` 117, `cli_policy.py` 92, …). Do this check after any
line-wrapping pass over SQL string literals; there were nine of them here.

### Issue 76 — `curated_target_unresolved` had no consumer

`db/029` section 5 shipped the orphan detector — live curated rows whose candidate is no longer projected after
a per-source rebuild — and **nothing read it**. The second instance of the same mistake; the first
(`expansion_policy_unresolved`, db/010) is recorded in `interactions.unresolved_expansion_policy`'s own
docstring as "precisely the failure mode it was written to catch". Now a standing rule above.

`curation.unresolved_targets(conn) -> list[UnresolvedTarget]` is the read, and `drugref status` grew a **third
block** that calls it. Two design points a later reader will otherwise re-litigate:

- **The read lives in `curation.py`, not in `cli.py`.** `cli.py`'s docstring forbids embedding SQL against
  curated append-only tables, because `test_only_the_current_view_reads_the_policy_table_directly` finds readers
  through `pg_rewrite`, which cannot see a query embedded in Python. `_handle_status`'s stated exception covers
  `loaded_release` and `ingest_run_incomplete` — **operational** views — and does not stretch to the curated
  overlay. Pinned by a parametrized grep test per curated table.
- **`drugref status`, not an ingest summary**, which is what issue 76 itself proposed. `curated_target_unresolved`
  has **no `source` column** — it compares curated rows against three projections at once — so unlike its
  expansion-policy sibling it cannot be scoped per-run, and `db/029` is merged and frozen, so adding one would
  need a new migration. That makes it a whole-database question.

`UnresolvedTarget` is built **positionally** from the SELECT, so one test asserts **all six fields** against
real SQL; the stub-driven CLI tests supply a tuple already in the assumed order and cannot see a column-order
mistake. Verified by mutation: swapping `reviewed_by`/`reviewed_against` in the SELECT fails that test and only
that test. Confirmed end to end on `drugref_5c1m` — `drugref status` prints `unresolved curated targets: none`
alongside the five loaded releases.

`cli.py` is now **479 lines**, close enough to CLAUDE.md's ~500 that the next handler added there should split
rather than append.

## Verify before the first production load

**Moved here from HANDOVER.md** in the #64 review round, for the same reason as the standing rules above: this list is
true until production happens, which is many rounds away, and it was being recompressed every session.

- **Re-run every parser against a full current release** (§ How to run / test — and #60) and re-confirm the aggregate
  numbers. Fixtures extracted from a real release are not the same thing: **5b found five spec errors that way**, each
  invisible to a green suite.
- **One data check, inherited from #17:** `claims.add_claim` canonicalises case-bearing claim values (UNII / INCHIKEY /
  CHEBI), so a database populated *before* that change could hold a spelling no lookup matches — **and such rows cannot
  be deleted.** Confirm BEFORE the first real load.
- **The two licence deeds (rule 6 blockers):** [#6](https://github.com/cairn-ehr/drugref/issues/6) re-confirm the MED-RT
  deed against the live NLM source-release doc (the distribution ships no licence file) ·
  [#25](https://github.com/cairn-ehr/drugref/issues/25) PBS redistribution — blocks bundling but not node-local ingest,
  and needs written Dept-of-Health confirmation.
- **A third, added by the slice-3 design:** GSRS's dedication reads *"**Unless otherwise noted**, the data provided by
  GSRS is public domain … CC0 1.0 Universal"*. CC0 is unconditionally AGPL-3.0-compatible and the clearance stands, but
  the clause is a **per-record exception**, so it re-confirms against the live licensing page before the first production
  load, exactly as #6 does for MED-RT. No noted exception was found on any record read.

## What the upstream documentation got wrong (verified against the real releases)

Each would be a silent, plausible bug invisible to a hand-written fixture — which is why every fixture is extracted from a
real release by a committed, re-runnable extractor. **MED-RT:** `Parent Of` runs parent → **child**, not the reverse; `[HC]`
concepts are the 26 **alphabetical navigation bins**, 18,450 of 21,058 class→ingredient edges; EPC membership is licence-clean
and **hierarchical**. **MeSH:** Descriptors **DO** carry UNIIs in `RegistryNumber` (aspirin D001241 = `R16CO5Y76E`), and a
record may carry several — key extraction is set-valued. **And the one 5b turned on:** MED-RT's MeSH `to_code` is a
**ConceptUI** (`M0004868`), *not* a DescriptorUI, in two shapes (legacy 8-char, modern 10-char — nothing keys off length);
resolving it against `desc2026` + `supp2026` reaches **99.88%** of MeSH-keyed objects, while the NDF-RT accessory crosswalk
yields only a **name** and is rejected. **UNII:** the gate columns live in `UNII_Records_*.txt`, never in `UNII_Names_*.txt`.
**GSRS (slice-3 design, 2026-08-05):** **the relationship direction is inverted from the naive reading** — for a
relationship of type `A->B` stored on record X and pointing at Y, **X plays role B and Y plays role A** (the stored edge
is the INBOUND one). Read naively, one "salt" had **124 parents**; read correctly, the busiest *parents* are Maleic Acid
(124 salts), Tartaric Acid (123), citric acid (117). Confirmed twice — the two mirror encodings agree on **15,039** edges,
and every solvate has exactly **one** anhydrous parent. **And the public API is not a substitute for the dump**: it
returns substance records with `relationships` **stripped entirely**, verified by control (`1D06KZ672I`, whose edge was
read straight out of the dump bytes, comes back from the API with zero relationships) — a first pass that trusted the API
concluded "GSRS holds no active-moiety data", which was an artifact of the transport.

## Architecture in one breath

ROADMAP states the model; what matters here is where each kind of data lives. **Hybrid store**: **rebuildable projections**
for ingested feeds (drop-and-rebuild, version-pinned, provenance-tagged via `ingest_run`) + an **append-only, SIGNABLE
overlay** for curated knowledge — **Plan C is the first content written to that tier**, and since `db/027`
`class_expansion_policy` now sits on its floor too, **the third, edited-in-place category no longer exists** (that table is
still cleared by no ingest).

**"Signed" was an overstatement, corrected 2026-08-06 by the 5c.1 design round.** No signing infrastructure exists
anywhere in the repo — no key management, no signing identity, no verification path — so the tier is **signable**, not
signed. The constraint that follows is sharp and is why 5c.1 ships empty: the floor refuses UPDATE, so **a row committed
before signing exists can never be signed retrospectively**, and signing must therefore land before the first curated
row (5c.4, ahead of 5c.2's ONC content).

**Rule-6 determination, made in the same round: DDInter is CC BY-NC-SA and is OUT of the bundled ladder permanently** —
non-commercial, so not AGPL-3.0-compatible. ROADMAP's old "DDInter *if its licence confirms*" predated the check. It may
attach only as a node-local, separately-licensed plug-in. The surviving ladder is ONC high-priority floor → SPL/DailyMed
(ONSIDES-*method*) → drugref's own curation.
Beside ROADMAP's two orthogonal structures, 5b adds a **third graph**, the MeSH condition DAG — an *object* structure, not a
subject one. **Substrate**: Python 3.12 + `uv`, `psycopg` v3, PostgreSQL ≥ 18. Advisory tier, **integrity in the DB**.

## How to run / test

```bash
uv sync
# 958 tests. The DB-gated majority SKIP without this DSN, exercising none of the
# schema, floor, views or orchestrators -- so always run WITH it before claiming green:
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest

# `ruff check .` is now the RIGHT command (issue 66). It used to walk downloads/ and
# hang, which is why this line said `ruff check src tests` for six rounds; pyproject's
# extend-exclude drops downloads/ and docs-site/site, so the bare form runs in 0.18 s.
# ruff is pinned in the dev group, so this resolves the lockfile's version rather than
# whatever is on PATH, and CI runs the same command in its own `lint` job.
uv run ruff check .

# Re-measure against the real releases, ~137 s WITH gsrs (~114 s without: the 2.05 GB
# dump adds ~23 s). TWO manual steps: unzip Core_MEDRT_XML.zip into downloads/MEDRT/, and
# put the GSRS dump at downloads/GSRS/dump-public-*.gsrs (307 MB gzipped, gitignored --
# `ruff check .` walks it and hangs, which is why the lint line above names src tests).
# The documented `ingest chain` invocation below RUNS (#60 is fixed): mesh-relations
# declares desc/supp `secondary` -- it still reads both files in full to resolve MED-RT's
# MeSH-keyed to_code, it just does not DATE them, so check_release_agreement stops
# comparing mesh's and mesh-relations' tags on a pair of files read, not claimed, by both.
# The guard still refuses two steps dating the SAME MED-RT bytes differently -- that
# disagreement was, and remains, real.
uv run drugref --dsn "$DSN" migrate
uv run drugref --dsn "$DSN" ingest chain --downloads downloads \
    --unii-release 26Feb2026 --medrt-release 2026.07.06 \
    --mesh-release 2026 --mesh-relations-release 2026.07.06 \
    --gsrs-release 2026-02-26
uv run drugref --dsn "$DSN" status      # loaded releases per (source, writer) + unfinished runs

# The per-source subcommands still exist and are still the right tool for a PARTIAL
# re-ingest (one feed, without re-running the others); only `chain` takes --downloads, so
# each of these names its own files. M=downloads/MEDRT/Core_MEDRT_2026.07.06_XML.xml,
# D='--desc downloads/mesh/desc2026.gz --supp downloads/mesh/supp2026.gz'.
uv run drugref --dsn "$DSN" ingest unii  --release 26Feb2026  --unii downloads/UNII_Records_26Feb2026.txt
uv run drugref --dsn "$DSN" ingest medrt --release 2026.07.06 --medrt "$M"
uv run drugref --dsn "$DSN" ingest mesh  --release 2026       --pa downloads/mesh/pa2026.xml $D
uv run drugref --dsn "$DSN" ingest mesh-relations --release 2026.07.06 --medrt "$M" $D
uv run drugref --dsn "$DSN" ingest gsrs  --release 2026-02-26 --dump downloads/GSRS/dump-public-2026-02-26.gsrs
```

CI runs the suite against a PostgreSQL 18 service container, and `conftest` **fails rather than skips** when `CI` is set — so
the DB layer can never go green by being skipped. **`.github/workflows/ci.yml` has TWO jobs since issue 66**:
`lint` (`uv run ruff check .`) and `pytest`. Before that round there was only `pytest`, so no lint of any kind
ran in CI and `ruff` was not even a project dependency.

- **Schema:**  `001` identity spine · `002` classification · `003` registry generalised · `004` contraindication projection ·
  `005` supersession/floor hardening · `006` `ci_axis` + view contract · `007` question registry · `008` gap views · `009`
  local (PBS) tier · `010` descendant expansion · `011` moiety admission evidence · `012` expansion-policy review round
  (`ci_class_subtree`) · `013` MeSH condition registry · `014` the two 5b contraindication relations + `condition_ci_axis` ·
  `015` condition read path · `016` `gap_unresolved_ci_object` · `017` that view re-keyed on `(upper(object_source),
  object_code)` (#41) · `018` the interaction debt round (`ingest_unmatched_ingredient.reason`, `ci_rule_partner_reach`,
  `contraindications_for_condition`) · `019` slice 5b.2 (the two indication relations, `condition_indication_axis`,
  `condition.scr_class`, the reach view, `indications_for_condition`, `gap_condition_without_indication`) · **`020`–`024` Plan
  C** (five curated accumulation tables + the generic overlay floor; `class_subtree` + two read views; four gap views + gap
  kinds 8–11; `023` the review round; `024` the hoisted DAG walk) · **`025` `ingest_run.writer` + `loaded_release` +
  `ingest_run_incomplete`** · **`026` the fourth `reason` (`contraindication_class`) + `gap_unmatched_ingredient`'s explicit
  tie-break** · **`027` the expansion policy on the overlay floor** (surrogate `policy_id`, `withdrawn`,
  `class_expansion_policy_current` and its four readers re-issued). **Read the LATEST file that touches an object for its
  actual shape** — 004's relationship CHECK is replaced by 006's FK, 006's `ddi_candidate_pair` by 010's then 012's then
  027's, 016's `gap_unresolved_ci_object` by 017's, 008's/012's `gap_unpopulated_contraindication` and 008's
  `gap_unmatched_ingredient` by 018's, and 018's by 026's.
- **Migrations are immutable once applied — and immutability starts at MERGE.** `apply_migrations` records each file's
  checksum and raises if an applied file changed, so altering a MERGED migration (*including* re-issuing a `COMMENT ON`) means
  a new `db/NNN_*.sql`. One still on an unmerged branch may be edited — the ledger binds a *database*, not the repo — as
  `db/013`–`db/016` and `db/019` were; verify with a full run after any such edit.
- **Code:** `src/drugref/{ids,claims,classes,conditions,db,interactions,local,questions}.py` +
  `src/drugref/{indications,accumulation,provenance,cli,cli_policy,overlay}.py` + `src/drugref/ingest/*.py`; seed data under
  `src/drugref/data/`; fixtures under `tests/fixtures/`. **`accumulation.py`** is Plan C's single writer plus the two PURE evaluation rules a
  consumer applies (`fires`, `group_fires`) — drugref publishes facts, never verdicts, but hands out the rules as code so
  "count the contributors" means one thing. **`interactions.py`** now carries TWO write disciplines under one docstring:
  rebuildable contraindication projections, and (since `db/027`) the append-only curator history — do not let a future writer
  inherit the wrong one. **`cli.py`** is the `drugref` console script; its pure half (`STEPS`, `build_parser`,
  `resolve_inputs` — which raises `InputResolutionError` on zero **or** several matches — and `selected_steps`) is tested
  without a database. The MeSH-keyed stack: **`conditions.py`** / **`indications.py`** (single writers),
  **`ingest/mesh_concepts.py`** (pure/streaming: **ConceptUI → record** resolution, the descendant closure, the tree-number
  DAG), and **`ingest/mesh_rel_run.py`** — the ONE orchestrator for both halves, reading two authorities (MED-RT states the
  rule, MeSH defines its object; #60's tension) and running `mesh_ci_relations.py` / `mesh_ind_relations.py` as passes. **Two
  orchestrators here is not a refactor away — it is impossible**: a `condition_parent` edge is derived by BOTH closures, so no
  `reason` discriminator can split it (#39 one layer deeper). **FIVE things live in exactly one place, and a test pins each**:
  `mesh.iter_records`, `ingest/checksum.py`, `db.clear_source_tables`, `provenance.py`, and — since the policy-surface debt
  round — `overlay.supersede`, pinned by `test_only_overlay_points_a_row_at_its_successor`. **`curation.py`** is
  slice 5c.1's curated-overlay writer, and since issue 76 also holds `unresolved_targets` — the READ that gives
  `curated_target_unresolved` a consumer, called by `drugref status`'s third block. It lives there rather than in
  `cli.py` because a Python-embedded reader of an append-only curated table is invisible to the `pg_rewrite`
  sweep that finds every other reader; a grep test per curated table pins that. **`overlay.py`** is the append-only
  tier's one correction primitive (#59): `supersede(conn, table, pk_column, new_id, key_columns, key_values)`, the
  INSERT-then-point ordering every curated writer needs, now called directly by `accumulation.py`, `questions.py` and
  `interactions.py` rather than each restating it. **`cli_policy.py`** is the `drugref policy record|withdraw|show` operator
  surface (#61), split out of `cli.py` to hold CLAUDE.md's ~500-line rule; like `cli.py` it writes no SQL of its own.
- Dev DSN: **stated once, in [`HANDOVER.md`](HANDOVER.md) § Current DSN** — it is a volatile machine detail, and CLAUDE.md
  and the `nextsession` skill both already send readers there. It used to be restated here under "update both", which is the
  same two-homes defect the standing rules above warn about. **`drugref_5c1m` holds the real releases WITH the MERGED
  `db/029`** at every COUNT and INGEST SUMMARY in § "Slice 5c.1" above — the current measurement database and the one
  to read rather than re-running the chain. It does **not** hold that section's `EXPLAIN ANALYZE` timings, which were
  never re-run on it (§ "Slice 5c.1" says which). **Read `drugref_5c1m`, NOT `drugref_5c1`:** the latter was migrated
  from a `db/029` that the two review rounds then edited twice more, so its ledger records
  `f2420c5c1196b7fa439ed4a876c6ea4a00c821d90852ebd709416a2acb19bc89` where the merged file hashes to
  `4a5efb350cd9af2a2172e9c186af713784555813ea7039a7b36bad800f011004` — **the one home for both values, quoted whole
  because `db.apply_migrations` prints the first twelve characters** (`recorded f2420c5c1196..., now
  4a5efb350cd9...`), so its error text can be compared against this line as a string. `apply_migrations` refuses
  there permanently, and it is a *pre-merge* schema (hardcoded `relationship` CHECK, the stale `~739` in
  `COMMENT ON TABLE`, `pair_count` as `count(*)`, no `question_uuid` index). **KEEP IT — it is the control**, and the
  only artefact that can still show the merged `pair_count` differs from the shipped one (§ "Slice 5c.1"); the name
  is therefore claimed, so a rebuild follows the plan's `createdb` recipe under a NEW name. Same treatment as the
  three below, for the same reason. **`drugref_policy` holds
  the real releases WITH `db/027`** at every figure above — the #35 measurement database and the one to read rather than
  re-running the ~103 s ingest. `drugref_ops` is the pre-round baseline (its ledger holds a drifted `db/025`, so
  `apply_migrations` refuses there; reads are unaffected), `drugref_planc` the pre-Plan-C one — and now `drugref_policy` too:
  this round's own `db/027` edits landed after it was migrated, so its ledger also refuses `apply_migrations` (reads
  unaffected). **A verification database is never patched — rebuild it, under a new name**: for a drifted ledger
  `apply_migrations` refuses permanently, and the drifted copy is often the only control for what the edit changed
  (`drugref_5c1` is exactly that). Expect a drift whenever a migration is edited; expect to keep both sides.
- **Upstream feed files are NOT committed** (`downloads/` is gitignored):
  - **MED-RT** — [NCI EVS](https://evs.nci.nih.gov/ftp1/MED-RT/) (`Core_MEDRT_*_XML.zip`); regenerate the fixture with
    `make_medrt_subset.py <xml> > tests/fixtures/medrt_subset.xml` (keep the endpoint redaction — a test enforces it).
  - **MeSH** — [NLM](https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/): `desc2026.gz`, `supp2026.gz`,
    `pa2026.xml`. NLM throttles per connection hard; a segmented byte-range fetch beats it ~18×. **No gunzip step since #40.**
    Regenerate 2b's with `make_mesh_subset.py downloads/mesh tests/fixtures/`, 5b's with `make_mesh_ci_subset.py
    downloads/mesh/{desc,supp}2026.gz tests/fixtures/` — **AFTER `make_medrt_subset.py`**, since its wanted set is read out of
    `medrt_subset.xml`: the first hand-picked version left every CI object resolving to nothing while both files looked
    healthy alone.
  - **PBS** — `pbs.gov.au/publication/schedule/2026/07/2026-07-01-PBS-API-CSV-files.zip?variant=3`: `?variant=3` is
    **required** or the server 404s, and files are UTF-8 **with a BOM** (`encoding='utf-8-sig'`). Ingest reads **only**
    `items.csv`; regenerate with `make_pbs_subset.py downloads/tables_as_csv/items.csv > …`.
  - **UNII** — `UNII_Records_*.txt` at the `downloads/` root is the file every parser reads; `UNII_Names_*.txt` beside it
    carries none of the gate's membership signals.
  - **GSRS** (slice 3) — `downloads/GSRS/dump-public-2026-02-26.gsrs`, **321,487,817 bytes gzip → ~2.05 GB**, JSON-lines
    with **two tab characters prefixing each line** before the `{`. Not linked from any static page: the URL
    (`https://gsrs.ncats.nih.gov/assets/downloads/dump-public-*.gsrs`) is held in the SPA's lazily-loaded JS chunk, so it
    changes without a redirect. 173,080 records / 168,002 UNIIs, and **all 19,438 drugref moieties are present (100%)** —
    the only bridge in this project that loses nothing, because GSRS is where drugref's UNII keys come from.

## Repo facts

- GitHub `cairn-ehr/drugref` · default branch `main` · **AGPL-3.0** · attribution in `NOTICE`, whose MED-RT/MeSH *scope*
  claims 5b and 5b.2 each corrected (5b.2's names all **six** MeSH-keyed predicates). **Plan C, the ingest-operability round
  and #35 add no source, so `NOTICE` is unchanged.** Rules: CLAUDE.md + `nextsession`.
- Public docs site: `docs-site/` (MkDocs Material) → `docs.drugref.org`, deployed by `.github/workflows/docs.yml`; `uv run
  --group docs mkdocs build --strict -f docs-site/mkdocs.yml` is its test. Its **Design decisions** section holds *living*
  records and is **where a standing correction to an artefact that cannot be edited — an immutable spec, or a MERGED
  migration's prose — goes**. **Four** live there; a reversed decision is removed, not tombstoned.

# HANDOVER — drugref

> **Disposable working scaffolding, NOT a source of truth.** The canonical *what/why* lives in the design specs under
> [`docs/superpowers/specs/`](superpowers/specs/). Regenerate it at the end of every working session (nextsession rule 9), keep it **under
> 500 lines** (CLAUDE.md), and get there by **compressing merged rounds to their traps** — every round adds some.
>
> **What CLAUDE.md's summary does not say:** drugref is **co-equal public-good infrastructure** (any EHR / pharmacy /
> app consumes it; Cairn is its first client on the same public-API footing), the **global tier** is built before the
> **local tier**, and it co-resides in a Cairn deployment's PostgreSQL **or** runs standalone.

## ⇒ NEXT

**Merged to `main`** (ROADMAP orders them): slices 1 (#1) · 2a (#9) · 2a.1 (#10) · 2b · 5a · 8a (#28) · the foundation
review · Plan A · Plan B (#32) · the identity-spine fix round (#34) · the Plan B review round (#38) · 5b (#44) · the
post-5b debt round (#46) · the interaction debt round (#49) · 5b.2 (#54) · #53's population-label round (#56) · Plan C
(#57) · the ingest-operability round (#58, closing #16 and #47).

**In flight: the expansion-policy history round (#35)** on `fix/expansion-policy-history` — `db/027`, implemented, measured and
pushed; **PR [#62](https://github.com/cairn-ehr/drugref/pull/62) is open and reviewed** (its findings are folded in below, and two
became #61/#63). **810 tests green**, `ruff check src tests` + `mkdocs build --strict` clean, re-measured against the real releases
on a fresh `drugref_policy` (**103.28 s**): `ddi_candidate_pair` **21,664** unchanged, filtered lookup **2.876 ms** against the
recorded 3.1 ms. Traps below. Errata live in `docs-site/docs/decisions/` — one per MeSH-keyed slice, plus Plan C's and this round's.

**⇒ Issue-tracker hygiene — the sweep-closed-but-unfixed pattern has happened three times** (#31, #35, #40), each time because
a commit or PR body saying *filed, not fixed* still named the number. The tracker is true today. **A number in a commit
message is a claim about the code — verify it**; when filing rather than fixing, use prose no closing keyword can be parsed
out of (#59–#61 and #63 are written that way).

**⇒ Next candidates:**

- **Slice 5c — the curated, signed overlay.** A projection may not invent a line of therapy, a strength of evidence or an
  ordering among the drugs that treat one condition; 5c is where a human adds those. **Plan C built the overlay MACHINERY**
  and #35 has now exercised it on a fifth table. Owns #51, #52, #55.
- **Slice 3 — composition tree (salts/esters/hydrates via GSRS).** Triply motivated: the salt-strip heuristic is down to
  **0.03%** of bridge rows, #33 needs form→moiety relationships, and #30 waits on the same thing. **The UNII release carries
  no parent-moiety column** (checked: 25 columns, none a relationship), so this needs the GSRS full export — **a new source,
  so the rule-6 licence check runs BEFORE anything is downloaded.**
- **Step 8 — curation itself**, driven by the worklist Plan C published (**381** effects awaiting a ruling), bound by §12-H:
  audit every file and predicate of a source before curating a gap it may already cover.
- **#36** — the discovery heuristic counts descendant classes rather than reachable members. Same table #35 just moved,
  deliberately left alone by it: the metric moves which roots get asked about, so it needs a curator ruling and its own
  re-measure.

## Merged rounds, compressed — the traps only

**The identity-spine fix round (#34: #27, #17, #26).** Spec: [moiety gate
redesign](superpowers/specs/2026-07-27-drugref-moiety-gate-redesign.md). Four defects, none visible to the committed fixtures;
required columns are now **declared and checked**, because `or ""` absorbed a structural mismatch silently. The gate is
`INN_ID | USAN_ID | (RXCUI & drug-like SUBSTANCE_TYPE) | UNII allow-list`, and **the asymmetry is the design** — uniform
type-filtering was measured and rejected because it deletes heparin, enoxaparin, protamine and 346 gene/cell therapies.
**Strictly monotone, pinned by a test**, because `moiety_uuid` is immortal. **5,227 moieties rest on `RXCUI` alone**, the
natural head of a #19 worklist. Do **not** "fix" #33 by allow-listing the hydrate UNIIs. **Every fixture is extracted from a
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
before commit by **four of the six orchestrators**. **ELEVEN** gap kinds since Plan C, **18,834 questions**:
unclassified_moiety **16,089** · unmatched_ingredient **2,150** · uncurated_additive_effect **381** · unresolved_ci_object
**103** · condition_without_indication **97** · unpopulated_contraindication **13** · dead_by_expansion_policy **1** · the
other four **0** (three need curation).

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

## What the upstream documentation got wrong (verified against the real releases)

Each would be a silent, plausible bug invisible to a hand-written fixture — which is why every fixture is extracted from a
real release by a committed, re-runnable extractor. **MED-RT:** `Parent Of` runs parent → **child**, not the reverse; `[HC]`
concepts are the 26 **alphabetical navigation bins**, 18,450 of 21,058 class→ingredient edges; EPC membership is licence-clean
and **hierarchical**. **MeSH:** Descriptors **DO** carry UNIIs in `RegistryNumber` (aspirin D001241 = `R16CO5Y76E`), and a
record may carry several — key extraction is set-valued. **And the one 5b turned on:** MED-RT's MeSH `to_code` is a
**ConceptUI** (`M0004868`), *not* a DescriptorUI, in two shapes (legacy 8-char, modern 10-char — nothing keys off length);
resolving it against `desc2026` + `supp2026` reaches **99.88%** of MeSH-keyed objects, while the NDF-RT accessory crosswalk
yields only a **name** and is rejected. **UNII:** the gate columns live in `UNII_Records_*.txt`, never in `UNII_Names_*.txt`.

## Architecture in one breath

ROADMAP states the model; what matters here is where each kind of data lives. **Hybrid store**: **rebuildable projections**
for ingested feeds (drop-and-rebuild, version-pinned, provenance-tagged via `ingest_run`) + an **append-only, signed overlay**
for curated knowledge — **Plan C is the first content written to that tier**, and since `db/027` `class_expansion_policy`
now sits on its floor too, **the third, edited-in-place category no longer exists** (that table is still cleared by no
ingest).
Beside ROADMAP's two orthogonal structures, 5b adds a **third graph**, the MeSH condition DAG — an *object* structure, not a
subject one. **Substrate**: Python 3.12 + `uv`, `psycopg` v3, PostgreSQL ≥ 18. Advisory tier, **integrity in the DB**.

## How to run / test

```bash
uv sync
# 810 tests. The DB-gated majority SKIP without this DSN, exercising none of the schema,
# floor, views or orchestrators -- so always run WITH it before claiming green:
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest
ruff check src tests      # NOT `ruff check .` -- that walks downloads/ and hangs

# Re-measure against the real releases, ~103 s. ONE manual step: unzip Core_MEDRT_XML.zip
# into downloads/MEDRT/. `ingest chain` CANNOT DO ALL FOUR TODAY (#60) -- mesh and
# mesh-relations share desc/supp and take different tags, so the pre-flight refuses. Run
# the four subcommands in STEPS order: same files, same tags, same work. Each names its
# files (only `chain` takes --downloads); M=downloads/MEDRT/Core_MEDRT_2026.07.06_XML.xml,
# D='--desc downloads/mesh/desc2026.gz --supp downloads/mesh/supp2026.gz'.
uv run drugref --dsn "$DSN" migrate
uv run drugref --dsn "$DSN" ingest unii  --release 26Feb2026  --unii downloads/UNII_Records_26Feb2026.txt
uv run drugref --dsn "$DSN" ingest medrt --release 2026.07.06 --medrt "$M"
uv run drugref --dsn "$DSN" ingest mesh  --release 2026       --pa downloads/mesh/pa2026.xml $D
uv run drugref --dsn "$DSN" ingest mesh-relations --release 2026.07.06 --medrt "$M" $D
uv run drugref --dsn "$DSN" status      # loaded releases per (source, writer) + unfinished runs
```

CI runs the suite against a PostgreSQL 18 service container, and `conftest` **fails rather than skips** when `CI` is set — so
the DB layer can never go green by being skipped.

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
  `src/drugref/{indications,accumulation,provenance,cli}.py` + `src/drugref/ingest/*.py`; seed data under `src/drugref/data/`;
  fixtures under `tests/fixtures/`. **`accumulation.py`** is Plan C's single writer plus the two PURE evaluation rules a
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
  `reason` discriminator can split it (#39 one layer deeper). **FOUR things live in exactly one place, and a test pins each**:
  `mesh.iter_records`, `ingest/checksum.py`, `db.clear_source_tables`, `provenance.py`.
- Current dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`. **`drugref_policy` holds
  the real releases WITH `db/027`** at every figure above — the #35 measurement database and the one to read rather than
  re-running the ~103 s ingest. `drugref_ops` is the pre-round baseline (its ledger holds a drifted `db/025`, so
  `apply_migrations` refuses there; reads are unaffected), `drugref_planc` the pre-Plan-C one — and now `drugref_policy` too:
  this round's own `db/027` edits landed after it was migrated, so its ledger also refuses `apply_migrations` (reads
  unaffected). **A verification database is disposable — rebuild rather than patch**: for a drifted ledger `apply_migrations`
  refuses permanently. Expect that whenever a migration is edited.
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

## Open follow-ups (all filed as GitHub issues)

**Filed by slice 5b.2 and its review (all three for 5c)** — [#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168
pairs both indicated and contraindicated**, counted not resolved · [#52](https://github.com/cairn-ehr/drugref/issues/52) **the
422 broadened assertions**: the row carries no `concept_ui`, so a consumer cannot detect which, and storing it is what would
make the row-grain figure queryable · [#55](https://github.com/cairn-ehr/drugref/issues/55) **`indications_for_condition`
generalises through a boolean, not a structure** — the mitigation `db/019` rejected for `induces`. Whichever option wins
revises the living record.

**Filed by the interaction debt round** — [#48](https://github.com/cairn-ehr/drugref/issues/48) **a non-expanding predicate
with no direct member is equally dead and is deliberately not reported** by `gap_dead_by_expansion_policy`; it wants its own
view. **Still unreachable — 5b.2, Plan C and #35 all left it so**: it goes live when a *class-side* predicate stops expanding.

**Filed by the expansion-policy history round, both deliberately NOT fixed in it** —
[#59](https://github.com/cairn-ehr/drugref/issues/59) the insert-then-supersede rule lives in **three** places
(`accumulation._supersede`, `questions.set_state` since `db/007`, `interactions.record_expansion_decision`), so the "promote it
when a third owner appears" trigger has already fired — and `_supersede` is already generic over table and pk, so reuse is an
import, not a refactor. Deferred anyway, for the honest reason: this round chose not to widen its blast radius into two other
modules · [#60](https://github.com/cairn-ehr/drugref/issues/60) **`drugref ingest chain` cannot run all four sources together on
merged `main`** — `mesh` and `mesh-relations` share `desc*.gz`/`supp*.gz` but take different release tags, so
`check_release_agreement` refuses the documented invocation. The guard (`98b346f`) landed AFTER the measurement (`b56d26e`)
inside #58, so the command was never re-run against it; the remedy is a design decision, because `mesh_rel_run` genuinely reads
two authorities and one tag per step cannot say so. **Use the four `ingest <source>` subcommands in `STEPS` order meanwhile —
that is what #35's measurement did.**

**Closed by the four debt rounds** (#50, #39, #31, #45 · #40, #17, #42, #41, #43 · #16, #47 · **#35**), each verified against
the code before closing. **Three standing rules came out of them and outlive the issues:**
- **THE VIEW'S GRAIN MUST BE THE `gap_key`'S GRAIN** (#41) — a gap view that groups more coarsely than its key folds two gaps
  onto one immortal `question_uuid`. Pinned per kind, Plan C's two compound-key views included.
- **One reader, one clear, one checksum** (#40, #43): `mesh.iter_records`, `db.clear_source_tables` and `ingest/checksum.py`
  each live in one place, and every writer's table tuple is **restated independently** in
  `tests/test_source_clear_contract.py` so a dropped table fails.
- **A branch the release cannot exercise is pinned on controlled input and verified by mutation** (#42): desc2026 and supp2026
  share **0** ConceptUIs. **#53's `is_cap_exempt`, #47's named-row tie-break and ALL of #35's new behaviour** — no
  release-derived database holds a superseded or withdrawn row — are the same shape.

**Floor & identity**
- [#2](https://github.com/cairn-ehr/drugref/issues/2) **Floor hardening** — close the `TRUNCATE` + owner-role bypass via RLS +
  privilege separation. **Note the test-suite coupling** (re-run the grep before quoting the count): `grep -l TRUNCATE
  tests/*.py` finds **eleven** modules, one the shared helper `tests/mesh_rel_fixtures.py`, each truncating in an autouse
  fixture because their orchestrators commit internally and escape the `conn` fixture's rollback — so hardening needs a
  replacement isolation strategy.
- [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality** — structural re-key by InChIKey, deferred.
  **#17 is CLOSED**; its third part is carried under "Verify-before-production" below.

**Ingest correctness (all found by measuring the real releases)**
- [#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms**, which cannot reach a moiety
  held as UNII's unspecified form. Counted, not dropped. **Closed by slice 3**, as is
  [#30](https://github.com/cairn-ehr/drugref/issues/30) (`strip_salt` drops only one trailing token).
- [#5](https://github.com/cairn-ehr/drugref/issues/5) INN sourced from UNII's `Display Name`, not an authoritative WHO list.
  `UNII_Names_*.txt`'s `TYPE='of'` rows (24,127 UNIIs) may yield one — but `of` also covers excipients: a *name* source, not a
  membership signal.
- [#7](https://github.com/cairn-ehr/drugref/issues/7) / [#29](https://github.com/cairn-ehr/drugref/issues/29) **Row-at-a-time
  ingest** — MED-RT (~31k round trips, plus `ElementTree.parse` holding 45 MB) and PBS (~28k). The MeSH-keyed run writes
  40,211 rows and is the slowest leg of the ~103 s ingest.

**Interaction model**
- [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is unpopulated** — filed as 41 of 739;
  `gap_unpopulated_contraindication` returns **13**, Plan B's expansion having absorbed the rest. **Re-measure before acting
  on the issue text.** Largely an **indexing loss, not a knowledge gap**: openFDA labels carry the statements, which is why
  the cost ladder puts `openFDA-SPL` above `literature`.
- [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions** — **Plan C's `interaction_group` is the shape
  that expresses it**, so this is now about the consumer contract rather than the schema ·
  [#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` assertions unused** (~756 edges), the other half
  of making the DAG carry knowledge.
- [#36](https://github.com/cairn-ehr/drugref/issues/36) **The discovery heuristic counts descendant classes, not reachable
  members**, so a curator `allow` can be spent on a provable no-op; changing the metric needs a curator and a re-measure.
  [#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** — the trap is that
  restricting the *root set* is safe while restricting the *walk* deletes the coagulation case; **`db/018`'s ancestor-walk
  function is the shape that fixes it.** Not urgent at **3.1 ms**.

**Licence deeds (blockers before production, per rule 6)** — [#6](https://github.com/cairn-ehr/drugref/issues/6) re-confirm
the MED-RT deed against the live NLM source-release doc (the distribution ships no licence file) ·
[#25](https://github.com/cairn-ehr/drugref/issues/25) PBS redistribution, which blocks bundling but not node-local ingest and
needs written Dept-of-Health confirmation.

**Verify-before-production, generally:** re-run every parser against a full current release (see "How to run / test" — and
#60) and re-confirm the aggregate numbers; fixtures from a real release are not the same thing, and 5b found five spec errors
that way, each invisible to a green suite. **Plus one data check, inherited from #17:** `claims.add_claim` canonicalises
case-bearing claim values (UNII / INCHIKEY / CHEBI), so a database populated *before* that change could hold a spelling no
lookup matches — and such rows cannot be deleted. Confirm BEFORE the first real load.

## Repo facts

- GitHub `cairn-ehr/drugref` · default branch `main` · **AGPL-3.0** · attribution in `NOTICE`, whose MED-RT/MeSH *scope*
  claims 5b and 5b.2 each corrected (5b.2's names all **six** MeSH-keyed predicates). **Plan C, the ingest-operability round
  and #35 add no source, so `NOTICE` is unchanged.** Rules: CLAUDE.md + `nextsession`.
- Public docs site: `docs-site/` (MkDocs Material) → `docs.drugref.org`, deployed by `.github/workflows/docs.yml`; `uv run
  --group docs mkdocs build --strict -f docs-site/mkdocs.yml` is its test. Its **Design decisions** section holds *living*
  records and is **where a standing correction to an artefact that cannot be edited — an immutable spec, or a MERGED
  migration's prose — goes**. **Four** live there; a reversed decision is removed, not tombstoned.

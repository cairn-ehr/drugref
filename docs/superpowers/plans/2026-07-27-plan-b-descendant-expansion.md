# Plan B — DAG-descendant expansion with a named deny-list — IMPLEMENTATION PLAN

> **Status: forward plan.** The **canonical design is
> [§3.2, §7.1 and §11 step 2 of the additive-effect & open-question design
> spec](../specs/2026-07-25-drugref-additive-effect-and-open-question-design.md)**; this file only orders
> the build into TDD-sized tasks. If the two disagree, the spec wins. If a measurement here contradicts the
> spec, **stop and update the spec first**, then continue.

**Goal:** close the 65% recall gap in `ddi_candidate_pair` — a contraindication naming a broad class today
misses every drug filed only under a *descendant* of it — without re-importing the low-specificity fan-out
that justified the direct-only default. Closes the work
[#15](https://github.com/cairn-ehr/drugref/issues/15) asks for. (That issue is already CLOSED on GitHub: it
was closed when the *measurement* landed in the spec, not when the code did.)

**Definition of done:** full suite green; `ddi_candidate_pair` expands over the class DAG by default and
carries `member_class` / `is_direct` so a consumer can still ask for direct-only; the deny-list is data in
`drugref.class_expansion_policy`, not a constant in a view; `gap_unreviewed_expansion_root` surfaces a new
abstract root as an open question so the list cannot rot silently across releases; the measured numbers
below reproduce against the real 2026.07.06 release; `NOTICE` unchanged (no new source).

## The two decisions this plan settles, and the evidence for them

Both were measured against the real release before being decided (script kept out of the repo; the numbers
below are what it printed).

### 1. Expansion is the DEFAULT, with an opt-out — not an opt-in second view

Issue #15's original text proposed shipping an expanding view *alongside* the direct one. The spec's own
revised recommendation supersedes that: **"expand descendants by default on both axes"**, because for a
contraindication *fewer rows is the unsafe direction*. So `ddi_candidate_pair` itself expands, and gains:

| column | meaning |
|---|---|
| `member_class` | the class the **partner** is actually filed under (may be a descendant of `via_class`) |
| `is_direct` | `member_class = via_class` — true reproduces exactly the pre-Plan-B row set |

A consumer that wants precision writes `WHERE is_direct`. A consumer that *forgets* the filter gets **more**
rows, which is the safe direction to fail in. Nothing outside this repo consumes the view yet (the slice-6
API is not built), so the semantics change costs no migration for anyone.

### 2. The deny-list names 11 abstract roots, and 3 large classes are explicitly ALLOWED

The `> 20 descendant classes` discovery heuristic finds **exactly 14** CI object classes in the 2026.07.06
release — **all PE, not one MoA**, confirming §3.2's central claim on real data. But §3.2 is equally clear
that size is how these were *discovered*, never the criterion: the test is *"would a contraindication naming
this class alone tell a prescriber what to avoid?"*

Ten of the fourteen are `<system> Activity Alteration` buckets — they name a system, no direction — and are
denied without argument. The remaining four name a direction *and* a function, so the spec's test says
expand. Three of them do; **one is denied on evidence from its subtree rather than its name**:

| class | direct → subtree | decision |
|---|---|---|
| Increased Immunologic Activity `[PE]` | 33 → **1,313** | **deny** — its subtree is heterogeneous: `Acquired Immunity [PE]` (1,109 drugs, essentially every vaccine) is not an "increased immunologic activity" in the additive-harm sense |
| Decreased Immunologically Active Molecule Activity `[PE]` | 35 → 327 | allow — children are specific effects (cytokine, complement, kinin, antibody) |
| Vasoconstriction `[PE]` | 54 → 119 | allow — names direction and function; only Arterial/Venous beneath it |
| Increased Sympathetic Activity `[PE]` | 16 → 16 | allow — all 21 children are empty, so expansion is a no-op today |

**The three allowed ones need explicit `allow` rows**, not just absence of a `deny` row: `absent` means
*unreviewed* and is what `gap_unreviewed_expansion_root` reports.

Pair counts, distinct `(subject, partner, via_class, predicate)`, self-pairs removed:

| policy | pairs |
|---|---|
| direct only (today) | 20,462 |
| full expansion, no deny-list | 58,288 |
| **11 denied / 3 allowed (this plan)** | **29,687** |
| deny all 14 the threshold found | 28,846 |
| deny only the 10 unambiguous | 32,247 |

So the deny-list keeps **~24% of the recall gain** and removes **~76% of the fan-out** — and the retained
gain is concentrated exactly where the design said additive harm lives (`Decreased Coagulation Activity`
4 → 109, `CNS Depression` 11 → 174, `Cardiac Rate Alteration` 0 → 122).

### The deny-list is a filter on the RULE'S OBJECT CLASS, not a traversal barrier

Load-bearing, and the wrong reading is implementable. `Decreased Coagulation Activity` is a **descendant of
the denied** `Hematologic Activity Alteration`. A barrier during traversal would leave the coagulation rules
unexpanded — deleting the single most important case this work exists to fix. A rule whose object class is
denied expands to its direct members only; every other rule expands over its full closure regardless of what
sits above it. **Task 2's test for this is the one that must never be deleted.**

## Ground rules (do not relax)

- **TDD, failing-test-first** for every task: write the test, watch it fail, implement, watch it pass.
- **Integrity in the DB.** The policy table is data with CHECK constraints, not app-side validation.
- **Migrations are immutable once applied** — `db/006`/`db/007`/`db/008` are in the ledger, so every change
  to their objects is a new `db/010`, including re-issuing a `COMMENT ON` whose contract has changed.
- **No new source, no licence question.** The deny-list is drugref's own AGPL-3.0 curation.
- **Baseline first:** 347 tests green before touching anything, so a new red is unambiguously yours.

## Task graph (each task = one red → green cycle)

### Task 1 — `db/010`: the policy table and the per-predicate switch

**Red — `tests/test_expansion_policy.py`:**
- `drugref.class_expansion_policy` exists, keyed `(source, source_code)`, with `decision` CHECK-constrained
  to `deny`/`allow` and NOT NULL `class_name` / `rationale` / `reviewed_by`.
- The 14 seeded rows are present: 11 `deny`, 3 `allow`.
- **Every seeded `source_code` resolves to a real class** once the MED-RT fixture is ingested (this is what
  makes NUI literals in a migration safe).
- `drugref.ci_axis.expands_descendants` exists, is NOT NULL, and defaults true for both existing predicates.
- Idempotent replay: `apply_migrations` twice neither errors nor duplicates a seed row.
- A MED-RT re-ingest leaves the policy table untouched (it is **not** a rebuildable projection — an ingest
  must never wipe curator policy).

**Green:** `db/010_descendant_expansion.sql` part 1.

**Why keyed on `(source, source_code)` and not `class_uuid`:** a migration runs before any class exists, so
a FK to `substance_class` could not be satisfied; and storing a derived `class_uuid` would put the
`ids.mint_class_uuid` derivation in a second place, which is the exact footgun `db/006` was written to
remove. The NUI and the class name sit in the seed where a reviewer can read them.

### Task 2 — `db/010`: `ddi_candidate_pair` expands

**Red — extend `tests/test_ddi_pairs.py`:**
1. A rule on a parent reaches a member filed only under a **child** (the bug #15 reports).
2. …and under a **grandchild** (the walk is transitive, not one level).
3. A rule on a **denied** root reaches direct members only.
4. **A rule on a class that is a DESCENDANT of a denied root expands fully** — the coagulation case.
5. An `allow` row behaves exactly as an absent row for expansion (it differs only for the review gate).
6. Control (§3.2's serotonin case): a class with no descendants returns an identical set with and without
   expansion — i.e. every row has `is_direct` true.
7. The axis is still not cross-wired: a `CI_PE` rule does not reach a `has_MoA` member of a descendant.
8. A drug is still never paired with itself, including when it is reached via a descendant.
9. A partner filed under **two** descendants of the same root appears **once**, with `is_direct` false.
10. A partner filed under both the root and a descendant appears once with `is_direct` **true** (direct
    wins the tie, deterministically).
11. `expands_descendants = false` on a predicate reduces it to direct-only.
12. **A cycle in `class_parent` does not hang the view** (`UNION` over `(root, class)`, per `db/008`).

**Green:** `db/010` part 2 — drop and recreate the view with a recursive `subtree` CTE and `DISTINCT ON`.

### Task 3 — `db/010`: the review gate

**Red — extend `tests/test_gap_views.py`:**
- A CI object class with more than 20 descendant classes and **no** policy row appears in
  `gap_unreviewed_expansion_root`, with its `descendant_class_count` and `ci_rule_count`.
- Adding a `deny` row removes it; adding an `allow` row removes it too.
- A class under the threshold never appears, policy row or not.
- A class with descendants but **no CI rule** never appears (the view is about rules, not topology).
- `drugref.expansion_policy_unresolved` lists a policy row whose `(source, source_code)` matches no class —
  the other half of the rot problem, when upstream re-keys a class the list names.

**Green:** `db/010` part 3.

### Task 4 — the new question kind

**Red — extend `tests/test_questions.py` and `tests/test_question_ids.py`:**
- `open_question.gap_kind`'s CHECK admits `unreviewed_expansion_root` and still rejects a typo.
- `register_from_gaps` registers one question per unreviewed root, with `gap_key = 'CLASS:{uuid}'`.
- A **pinned `question_uuid` literal** for the new kind (`gap_key` format is frozen per kind, and an
  external notifier holds these).
- Recording a policy decision closes the question on the next rebuild; a question carrying curator work is
  retained with `is_current` false rather than deleted.

**Green:** `db/010` part 4 (CHECK widening) + a fourth `_GAP_SOURCES` entry in `questions.py`.

### Task 5 — the contracts that just went stale

`db/006`'s and `db/008`'s `COMMENT ON` text describes direct-only expansion as current fact. Those files are
immutable, so `db/010` re-issues the comments:

- `ddi_candidate_pair`: replace *"Expansion uses DIRECT class membership only"* with the new contract —
  expands over the DAG, `is_direct` opts out, denied roots do not expand, still directional, still
  candidate-tier.
- `gap_unpopulated_contraindication`: caveat (1) said the view *understates* what returns nothing "until
  descendant expansion (#15) lands". It has landed — but the caveat **survives in narrowed form**, because a
  rule on a *denied* root still yields no pair from a descendant-only population. Narrow it; do not delete
  it.

### Task 6 — verify against the real release

Ingest the real `Core_MEDRT_2026.07.06_XML.xml` into a scratch database and confirm: 14 classes over the
threshold, all seeded; `gap_unreviewed_expansion_root` empty; the pair count matches the 29,687 measured
above; no policy row unresolved.

## Out of scope (deliberately)

- **Plan C** (the accumulation model, the four curated assertion tables, the remaining gap views) — gated on
  slice 5b, per §11 and §12-H.
- **Retuning the discovery threshold.** 20 is a heuristic for the *worklist*, not a rule; retuning is a
  `CREATE OR REPLACE VIEW` in a later migration and needs a reason from a curator, not from this plan.
- **`has_SC` / class-level `has_*` inheritance** ([#8](https://github.com/cairn-ehr/drugref/issues/8)) — the
  other half of making the DAG carry knowledge, and independent of this.

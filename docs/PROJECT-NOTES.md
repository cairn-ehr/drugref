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

- **ANALYSE A BULK-LOADED TABLE BEFORE LOADING ANYTHING THAT REFERENCES IT** — *and the guard for it says
  `reltuples > 0`, never `>= 0`, because 0.0 means "analysed while still empty", which pins the same wrong plan*
  (issue 160, 2026-09-01, and it
  cost 630 seconds of every SPL ingest). A foreign-key check is a QUERY, and the planner may satisfy it with
  any parent index whose leading columns its equality quals cover — so a parent carrying a second index whose
  key is a *proper subset* of the referenced columns offers a plan that matches the whole table and filters.
  On a freshly `COPY`d parent (`relpages = 0`, `reltuples = -1`) the good and the catastrophic plan **cost the
  same**, and the choice is a coin toss. **The plan is chosen at first use, inside the load**, so by the time
  an end-of-run `ANALYZE` exists the `COPY` has already been paid for at the bad plan's price. Measured:
  493,539 ms → 1,352 ms for one `COPY`, bought by 112 ms of `ANALYZE`. Censused across all 138 foreign keys in
  the schema and pinned by two tests, against four mutants; full account in § "The COPY-cost round".
  ⇒ **And its meta-rule: A REFUTATION IS A MEASUREMENT PLUS AN EXPLANATION, AND ONLY THE EXPLANATION IS
  LOAD-BEARING WHEN IT IS QUOTED FORWARD.** This cause was ruled out for a round by a docstring whose 175 ms
  measurement was real and whose stated reason was invented. **Where a cost is concentrated in one statement,
  sample the process before designing an experiment about it** — the three hypotheses on the issue were all
  wrong, and eight seconds of `sample` named the fourth.
  ⇒ **And the meta-rule that round then broke while writing it: A ROUND IS MOST LIKELY TO COMMIT THE FAILURE IT
  IS CURRENTLY NAMING.** Its own fix docstring invented a second mechanism ("the plan is CACHED for the rest of
  the session") one paragraph after retracting the first. **Reasoning is not measurement even when it sits in
  the same sentence as one, and least of all in the paragraph that just said so.**
- **CODE MUST EXPLAIN ITS OWN CONTRACT.** The repository-wide house rules now live in
  [`CONTRIBUTING.md`](../CONTRIBUTING.md): docstrings are mandatory; behavioural numbers are named constants;
  dynamically typed code carries complete type hints; and pure reusable logic belongs in focused modules where
  meaningful. New work complies in the same change, and a touched older unit is brought forward with it.
- **INGEST WHAT IS UNAMBIGUOUS; SET ASIDE FOR CLINICIAN REVIEW WHAT IS NOT. ERR ON THE SIDE OF CAUTION.**
  Stated by the project owner during the 5c.2g design round (2026-08-16) as the rule governing **every source
  round**, and written here rather than in that slice's spec because it is not that slice's rule. Two
  corollaries it already forced, both of which look like extra work and are not:
  - **A disposition records what was OBSERVED, never what the round suspects it MEANS.** 5c.2g's
    resolution residue splits into six recognisable categories — enantiomer, synonym, metabolite, group
    term, combination, non-drug — and only the last two are stored, because only those two are asserted by
    the SOURCE. Labelling `R-venlafaxine` an "enantiomer of a held racemate" is a chemical relationship
    inferred **from a string prefix**, which is #122's manufactured-cause defect wearing a different hat.
    The other four collapse to one honest `unresolved_substance`.
  - **A near-name candidate is EVIDENCE, never coverage, and no count may be quoted against it.** The
    DrugCentral evaluation already paid for this one: its prefix heuristic "matched" `glycerol` to
    `glycerol 1,3-dimethacrylate`, a different substance, and its own note says *"treat it as the shape of
    the problem, not a count to quote."*
  The first thing this rule refused is filed as [#128](https://github.com/cairn-ehr/drugref/issues/128)
  (stereoisomer assertions against a held racemate — pharmacology with a literature behind it, not a naming
  convention), and it is scoped to every source, not to the three rows that raised it.
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
  effective settings from the tool (`ruff check --show-settings`), never from reading the file.** Issue 79's
  round added the second half: **a nested config REPLACES its parent, it does not layer onto it.** `tests/`
  is bounded at 120 by `tests/ruff.toml` (measured: the suite's ceiling has been 119 at every measurement,
  while the count over 88 went 324 → 334 → **415** — a count that grows while the ceiling stands still is an
  unstated convention, not drift). Its `extend = "../pyproject.toml"` is load-bearing **and its absence has no
  symptom**: without it `select = ["E", "F", "W"]` never reaches `tests/`, the suite silently falls back to
  ruff's default `["E4","E7","E9","F"]`, and the column bound the file exists to set carries on working
  perfectly while lint checks LESS than before. `tests/test_lint_bounds.py` drives ruff at a path in each tree
  and pins all of it, using **W291** as the discriminator — that rule is absent from ruff's default set, so it
  is the one probe whose verdict can only be explained by `extend` having worked. Every case there is a
  positive control: a bound asserted only by what passes is satisfied by a lint run that checks nothing.
- **A COUNT WITH NO DENOMINATOR CANNOT TELL HEALTH FROM AN EMPTY TIER** (issue 111, the low-hanging-debt
  round). `drugref status`'s class-grain block printed three zeros on a fully-migrated database — **byte-
  identical to a healthy, fully-curated registry** — because all three detectors report only on rules that
  EXIST. Per-source rebuilds are delete-and-rebuild, so an ONCHIGH re-ingest whose parser yields nothing
  empties the tier and silences every detector at once, while `loaded_release` still shows ONCHIGH loaded and
  the command still exits 0. The compensating control (`curated_target_unresolved`) fires only if curated rows
  already exist, so on any node that has not begun curating an emptied tier is invisible. **Print the
  population beside the faults**, and count it at the SAME grain as the numerators or the operator diffing them
  sees rules appear and disappear on a re-ingest that changed nothing. The test asserts the two renderings
  DIFFER (`emptied != healthy`) with all three detectors pinned at zero on both sides — a substring check on
  the new wording would be satisfied by any two identical outputs.
- **A TEST WHOSE EXPECTED RESULT IS OVER-DETERMINED CANNOT FAIL** (the review of PR #80). A negative assertion
  (`== []`, "no rows", "no error") is only evidence if **exactly one** thing could have produced it. The superseded
  -orphan test held whether or not the view filtered on `superseded_by`, because it never removed the candidate,
  so two independent clauses each sufficed. **Before trusting a test that asserts absence, name every reason the
  result could be empty; if there is more than one, the test is pinning none of them.** Mutation is how you check.
- **A SHELL PIPELINE SWALLOWS THE EXIT CODE OF EVERYTHING BUT ITS LAST STAGE** (the review of PR #80). CI's
  anti-skip guard ran `pytest … | tee out.txt && ! grep …` under `bash -e`, which does **not** set `pipefail`, so
  five test failures exited 0 and only the grep decided the step. **In any CI step, redirect rather than pipe, or
  set `-o pipefail` explicitly** — and remember `uv sync` re-resolves a drifted lockfile silently, so a pinned
  tool version is only pinned under `uv sync --locked`.
- **A GREP-SHAPED GUARD MUST MATCH THE PARSE, NOT THE SOURCE TEXT** (the review of PR #80). The "no SQL in
  `cli.py`" test matched a literal `FROM drugref.<table>` against raw source, so a two-line string, an `INSERT`,
  an `UPDATE`, a `JOIN` or a double space all walked past it — and **the same round's 88-column lint rule is what
  forces long SQL to wrap**, so one gate was quietly weakening another. `ast.parse` folds implicit concatenation,
  which closes every one of those at once. **When you add a formatting rule, re-check every guard that reads
  source text.**
- **DERIVE THE COVERED SET FROM THE CATALOG, NEVER FROM A LIST YOU MAINTAIN** (the review of PR #80). Seven
  single-live tables were covered by three hand-written literal lists, and the number "seven" appeared in prose
  four times while nothing asserted it — so an eighth table was invisible to the entire suite. Reading
  `pg_trigger.tgargs` makes the fixture assert what the trigger actually asks for, and makes new tables covered
  the day their migration lands. Same for any "all N of these" claim: if a catalog knows N, ask it.
- **A TEST PINNING AN ABSOLUTE DATE AGAINST `now()` PASSES ONLY UNTIL IT DOESN'T** (slice 5c.4, task 9 → task 10).
  `test_signature_backdated_*` compared a hardcoded `SIGNED_AT = 2026-08-09` against `recorded_at` (= `now()`), so
  the moment wall-clock crossed 2026-08-10 every "normal" signature became backdated and the test went red
  overnight — **its own review had run clean the day before**, and the next round's implementer then reported it
  as "pre-existing, unrelated" and deselected it. Two failures for the price of one: a time bomb, and a green
  claim built on a deselection. **Derive both sides of any time comparison relative to `now()`** so they sit on
  opposite sides of the boundary *by construction*; keep literal dates only where the value is payload, not a
  comparand. The fix is verifiable by simulation — shifting both literals back 1, 3 and 10 years must leave the
  suite green. And: **a `git diff` showing your commits didn't touch a file is not evidence the failure predates
  the branch; `git log --diff-filter=A -- <file>` is.**
- **CONSOLIDATING A DUPLICATED RULE WITHOUT PINNING IT MAKES THE SINGLE POINT OF FAILURE QUIETER, NOT SAFER**
  (slice 5c.4, task 8). Two rules above push toward one home — "a vocabulary written down twice" and "derive the
  covered set from the catalog" — and this is the half they do not say. A duplicated verdict-precedence table was
  correctly collapsed to one definition, and the survivor was left untested: **reversing it left 177 tests
  green**, and replacing the collapsing function's body with `verdicts[0]` left every releases test green. Two
  copies at least disagree loudly when one drifts; one unpinned copy fails in silence. **When you delete the
  second copy, add the test that kills the first** — and pin it against *behaviour* (drive the real function and
  derive the ordering from what it does), not against a second literal, which is the duplication coming back.
- **A WRONG *WHY* COMMENT IS WORSE THAN NONE, BECAUSE A READER ACTS ON IT** (slice 5c.4 — **four** in one slice).
  A missing rationale makes a reader cautious; a confident wrong one gives them a reason to delete the guard it
  describes. All four sounded right: psycopg returns `bytea` as `memoryview` (**false** — it returns `bytes` in
  both binary modes); the two-predecessor state is "unreachable at `COMMIT`" (**false** — the reviewer committed
  it with `SET CONSTRAINTS ALL IMMEDIATE`, and this one pointed the wrong way about a guard that demonstrably
  fires); "so there is no cycle" (**false** — there is one, broken only by a function-local import); and
  `rollback()` is "the one place every recovery path goes through" (overstated by one handler). Three came from a
  task brief, i.e. from the person with the most context. **Measure the claim before you write it down, and when
  a review reworks a comment, re-verify the NEW wording against the code rather than accepting the rewording** —
  the whole finding was that plausible prose was wrong.
- **VERIFICATION MUST RECONSTRUCT THE PAST, NOT DESCRIBE THE PRESENT** (slice 5c.4, tasks 7 and 8). Code checking
  a historical record must read its parameters **back from the record**, never re-derive them from current
  configuration. `payload_context` and `algorithm` were both re-derived from the live `signature_target_kind`
  catalog instead of from the signature row — **twice, in two modules, the second time after the first was found
  and fixed**, which is what makes it a rule rather than a bug. It passes every test until a `/v2` context or a
  second algorithm exists, and then reports every historical `/v1` signature as forged. **Wherever a stored
  record has a format/version/algorithm field, the verifier reads that field; if it never reads it, the field is
  decoration and the guard is missing.** The same instinct is why `signing.FIELD_LISTS` is frozen rather than
  derived — the one place in this repo where deriving from the catalog is the wrong answer.
- **A PROSE RULE THAT HAS FAILED SIX TIMES IS NOT A RULE, IT IS A WISH — AND IT IS NOW A HOOK** (#114 and #118,
  closed by the guard round). The sweep-closed-but-unfixed pattern happened **six** times — #31, #35, #40, #61,
  **#108**, #114 — and `ed1ab5e`'s body reads *"Filed rather than fixed: #114 …"*, the **identical sentence
  template** that closed #61 via `92baaea`, in a repo where this very file already documented the trap, named
  the token-adjacency mechanism, and warned that *"a colon in between does not save you"*. Only #114 closed,
  because no keyword sits next to #115, #116 or #117 in the same sentence.
  **⇒ AND THE COUNT ITSELF WAS WRONG UNTIL A MACHINE TOOK IT.** #108 (`293758c`, the same sentence, one round
  before #114) was found only by running the finished guard over all 363 commits, and every document here said
  five. **A failure that is silent by construction cannot be counted by hand, and the count was the evidence the
  prose rule was failing** — so the undercount understated the case for fixing it, for a round.
  **The rule stands** (near `close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#`), and it
  is now **enforced** by `.githooks/commit-msg` → `drugref.commit_lint`; install with
  `git config core.hooksPath .githooks` (§ "How to run / test"), escape with `--no-verify`. **The hook cannot
  see PR DESCRIPTIONS, which GitHub also parses** — [#124](https://github.com/cairn-ehr/drugref/issues/124),
  so keep writing *"issue 114"* in a PR body.
- **A FIGURE QUOTED FROM AN ISSUE'S PROSE INTO A `COMMENT ON` OUTLIVES THE ISSUE** (#117, the db/038 round).
  `db/035` quoted issue 96's failure-scenario prose faithfully — `class_rules_written=9` — and that number was
  never reconciled against issue 94, which **withheld SEVEN** class×class ONC entries. The 9 then landed in the
  catalog, where it is shipped data rather than a draft, and `db/037`'s first draft re-imported it: **one file
  carrying "seven" on line 10 and "~9" on line 63**, because its author read db/035 for one and the issue for
  the other. Corrected in the catalog by `db/038` § 3 (a `COMMENT ON` re-issue, db/027's precedent — db/035 is
  merged and immutable, so its `--` file prose CANNOT be corrected and this note is the only record of that
  half). **The rule: a figure copied out of an issue into a migration is a MEASUREMENT as soon as it merges —
  re-derive it from the data before writing it down, and never from the issue that motivated the work.**
- **A RE-ISSUED `COMMENT ON` MUST BE DIFFED *WHOLE* AGAINST THE *LIVE CATALOG*, NEVER AGAINST A MIGRATION FILE**
  (the PR #119 review). `COMMENT ON` **overwrites; it does not merge**, so the text you are replacing is
  whichever statement ran LAST — not the one you happen to be reading. `db/038` § 3 rebuilt this same comment
  from **db/035** while the live text was **db/036's**, and thereby reverted db/036 § 1's correction of the
  frozen `gap_key` spelling (`AXIS:` → `CI_AXIS:`) and deleted the parenthetical recording it. Three migrations
  now state that one comment; **count them before re-issuing** (`grep -c "COMMENT ON VIEW drugref.<name>" db/`).
  **AND THE VERIFICATION MUST NOT BE SCOPED TO THE WORD YOU CHANGED**: db/038 grepped `%nine ingested%` /
  `%seven ingested%`, which is structurally incapable of reporting what the overwrite DROPPED. Pin the whole
  comment (`tests/test_class_grain_comment.py`, `tests/test_curated_interaction_comment.py`).
- **`CREATE OR REPLACE VIEW` PRESERVES THE VIEW'S COMMENT** (same review). Re-defining a view does NOT refresh
  its `COMMENT ON`, so a migration that corrects a rule in SQL leaves every prose statement of that rule
  standing. `db/038` § 1 changed the precedence to `effective_rank` and left db/037's
  `COMMENT ON VIEW curated_ddi_pair` prescribing `ORDER BY severity_rank NULLS FIRST` — the FIRST thing `\d+`
  prints. **When a migration changes a rule, re-issue every catalog comment that states it, in the same file.**

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
before commit by **five of the seven orchestrators**. **EIGHTEEN** gap kinds — twelve by Slice 3, then `db/029`'s
two (5c.1), `db/031`'s `unresolved_onc_endpoint` (5c.2), `db/035`'s `uncurated_class_interaction_rule`, `db/039`'s
`fda_cyp_unadjudicated` (5c.2g) and `db/049`'s `unresolved_ddi_endpoint` (DrugCentral). **This line said TWELVE for
four rounds after it stopped being true, and then said SIXTEEN for two more**, because each round updates its own
section and not this one; the count belongs here, so **change it here** — and read it off `pg_constraint` rather
than off this file. `db/049`'s own comment records that the live catalog already held SEVENTEEN where the plan that
wrote it assumed sixteen, which is exactly how `db/039` found sixteen where its plan assumed fifteen: **the
migrations are right both times because they copy the live CHECK verbatim before extending it, and it is this line
that has now been stale twice.** Re-derived end to end on `drugref_db035` (2026-08-14, `register_from_gaps` in
4.1 s, rolled back), **21,840 live** — the table below is *that* measurement, and the two kinds added since are
deliberately NOT folded into it, because their counts come from different databases and a table carrying two
denominators is how [#115](https://github.com/cairn-ehr/drugref/issues/115) happened:

| kind | live | | kind | live |
|---|---|---|---|---|
| unclassified_moiety | 16,089 | | unpopulated_contraindication | 13 |
| unruled_composition_activity | 2,245 | | dead_by_expansion_policy | 1 |
| unmatched_ingredient | 2,150 | | unreviewed_expansion_root | 0 |
| uncurated_interaction_rule | 593 | | uncurated_threshold | 0 |
| uncurated_additive_effect | 381 | | ineffective_contribution | 0 |
| uncurated_condition_contradiction | 168 | | ungraded_contribution | 0 |
| unresolved_ci_object | 103 | | unresolved_onc_endpoint | 0 |
| condition_without_indication | 97 | | **uncurated_class_interaction_rule** | **0** |

**The two kinds added after that measurement, each named with the database it was measured on rather than folded
into a table that predates them.** `fda_cyp_unadjudicated` (kind **17**, `db/039`, slice 5c.2g) — **55** questions
on `drugref_5c2g`, split `withheld_qualified` 33 · `combination_regimen` 9 · `unresolved_substance` 8 ·
`non_drug_entity` 5. `unresolved_ddi_endpoint` (kind **18**, `db/049`, DrugCentral) — **10** on `drugref_dc049`,
one per folded endpoint name over 37 unresolvable rows. Neither shows up in the `drugref_db035` table above, and
neither shows up in the `drugref_dc101` reference database either: the documented `ingest chain` runs neither
FDA-CYP nor DrugCentral, because both are standalone subcommands.

**21,840 DERIVED vs 21,848 STORED, and the 8-row gap is a real finding, not rounding**
([#104](https://github.com/cairn-ehr/drugref/issues/104)): `drugref curate` is deliberately not a chain step, so
the 8 questions ONCHIGH curation answered stay on the worklist until the next ingest re-derives. **Verified
identical on `drugref_db034`, so it predates `db/035`.** The views are right (`gap_uncurated_interaction_rule`
returns 593); only the stored projection lags. The earlier **EXPECTED 2,226 / 21,060** hedging was settled at Slice
3 — the assembled registry gave **2,245**, 19 more than the raw-extract query predicted, the 19 being composites
whose only activity ruling sits on a mirror record the orchestrator does not read a ruling from (Slice 3 erratum
below). `unruled_composition_activity` is gap kind 12 (`db/028`, Slice 3 Task 5): composites carrying components
but no activity ruling at all, populated from day one like the coverage kinds, not curation-dependent like Plan
C's four.

**Slice 5c.4 — signing, the overlay's authenticity layer** (`db/030`). Six tables and no projection: `signing_key`
(on the overlay floor — revocation is a correction, never a column edit), the seeded `signing_key_status_kind`
carrying the revocation rule **as data** (`is_revocation` × `invalidates_all_signatures`), the strictly insert-only
`assertion_signature`, `signature_target_kind`, and `release_manifest` + `release_manifest_entry`. Curators hold
their own Ed25519 private keys — **drugref never holds one**, because a server-held key proves only what the
unauthenticated `reviewed_by` column already claims. Both 5c.1 read views gained a trailing `signature_status`
column by `CREATE OR REPLACE`, and **a signature never gates a read**: fewer rows is the harm direction for a
contraindication, so a key-management event must not be able to withdraw advice. `signed` means "the registry
raises no objection", **not** "the mathematics was checked" — Postgres cannot verify Ed25519, only `drugref
verify` can. Full account and the traps: § "Slice 5c.4" below.

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
else — the drift the paragraph above predicted, sitting in the database the Dev DSN bullet pointed readers at.
**Both checksums are quoted in full, once, in § "How to run / test"** (at the twelve hex characters
`db.apply_migrations` itself prints, so the documented value can be compared to its error text as a string
rather than eyeballed). Rebuilt
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

Eight tables carry a single-live natural-key partial index (**seven** when this round ran; `db/030`'s `signing_key`
is the eighth, and the catalog-derived guard below picked it up the day that migration landed). The tests
protecting them asserted the property
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

**`extend-exclude = ["downloads", "docs-site/site"]` is BELT-AND-BRACES, and this entry originally had the
causation backwards.** It claimed the exclusion is what stops `ruff check .` hanging on the GSRS dump — which is
why every instruction in this repo used to say `ruff check src tests`. **The review of PR #80 measured it and the
claim does not reproduce**: with `extend-exclude` emptied, `ruff check .` still completes in **0.18 s** over a
614 MB `downloads/`. Two things make it safe, neither of them this setting — ruff honours `.gitignore` and
`downloads/` is ignored (`.gitignore:7`, since 2026-07-24), and ruff only ever opens `.py`/`.pyi`, so a data blob
costs nothing even when gitignore is bypassed. The setting earns its place only for the day one of those paths
stops being ignored; `docs-site/site` is the one that would bite (bypassing gitignore surfaces 662 E501s from it).
**Note the shape of the error**: a true statement ("the bare command is now safe") was given a false cause, and
the false cause is what a later contributor would act on.

Also corrected there: the dump is **one** artefact, not two — 321,487,817 bytes gzip → ~2.05 GB, exactly as
§ "How to run / test" states it. An earlier draft read that as "a 2.05 GB dump and a 321 MB gzip".

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

**The reflow is provably content-preserving — FOR STRING CONSTANTS, which is not the same as for comments.**
Every NON-DOCSTRING string constant was compared between `HEAD` and the working tree via `ast.parse` across all
16 touched files — Python folds implicit concatenation at parse time, so splitting `"AAA BBB"` into `"AAA "
"BBB"` is invisible while a lost or doubled space is not. **All 16 identical** (`cli.py` 119 strings,
`questions.py` 117, `cli_policy.py` 92, …). Do this check after any line-wrapping pass over SQL string literals;
there were nine of them here.

**The review of PR #80 then found what that check by construction could not see**, and it is worth stating
because the phrase "provably content-preserving" invites over-reading:

- **One comment lost a word.** `medrt.py`'s `inactive_concepts` counter went from `# right CTY, but upstream no
  longer marks it active` to the same line without `but` — the line was 89 characters, so the script dropped a
  word rather than the alignment padding, and the two sibling counters beside it still read "but". Restored by
  trimming the padding. A word-bag diff over comments (via `tokenize`, so trailing comments count) is the check
  that catches this; the string-constant check cannot.
- **29 of `mesh_rel_run.py`'s 77 indented continuation lines were flattened**, inconsistently and within single
  blocks — step 7 ended up with three flat paragraphs and one still indented, which reads as though the indented
  one were a deliberate sub-point. No words moved, so every automated check passed. Restored, then re-verified
  word-identical to `main`.

**Standing rule: a reflow needs TWO checks, not one** — `ast.parse` over string constants for content, and a
comment word-bag plus an indentation count for structure. Neither sees what the other does.

### Issue 76 — `curated_target_unresolved` had no consumer

`db/029` section 5 shipped the orphan detector — live curated rows whose candidate is no longer projected after
a per-source rebuild — and **nothing read it**. The second instance of the same mistake; the first
(`expansion_policy_unresolved`, db/010) is recorded in `interactions.unresolved_expansion_policy`'s own
docstring as "precisely the failure mode it was written to catch". Now a standing rule above.

`curation.unresolved_targets(conn) -> list[UnresolvedTarget]` is the read, and `drugref status` grew a **third
block** that calls it. Two design points a later reader will otherwise re-litigate:

- **The read lives in `curation.py`, not in `cli.py` — for OWNERSHIP, not for `pg_rewrite`.** The original
  wording here (and in `cli.py`'s own docstring) said the `pg_rewrite` argument "applies in full" to the third
  block. It does not, and the review of PR #80 was right to press it: `curation.unresolved_targets` is *also* a
  SELECT embedded in Python, and moving it out of `cli.py` does not make it visible to `pg_rewrite` — nothing can.
  What the placement actually buys is that the read sits beside the curated write path it belongs to, exactly as
  `unresolved_expansion_policy` sits in `interactions.py`, and that a grep test can then hold the line. Keep the
  rule; state the real reason for it.
- **`drugref status`, not an ingest summary**, which is what issue 76 itself proposed. `curated_target_unresolved`
  has **no `source` column** — it compares curated rows against three projections at once — so unlike its
  expansion-policy sibling it cannot be scoped per-run, and `db/029` is merged and frozen, so adding one would
  need a new migration. That makes it a whole-database question.

`UnresolvedTarget` **was** built positionally from the SELECT, pinned by one test asserting all six fields
against real SQL. **The review of PR #80 replaced testing-for with cannot-happen**: a single
`_UNRESOLVED_COLUMNS` tuple now generates the SELECT *and* binds the record by keyword, so the two hand-maintained
lists that sat a few lines apart are one, and `zip(..., strict=True)` turns a gained or lost column into a
`ValueError` instead of a mis-populated record. Worth keeping the arithmetic that motivated it: four text columns
and two uuid ones admit **48 type-compatible orderings**, exactly one correct, and every wrong one is well-typed.

Note what was NOT the risk, because the PR description got this wrong and a later reader will too: the SELECT
names its columns explicitly, and `CREATE OR REPLACE VIEW` cannot reorder or rename existing columns anyway, so
**a view change could never have silently reordered the result**. The exposure was entirely inside `curation.py`.

Confirmed end to end on `drugref_5c1m` — `drugref status` prints `unresolved curated targets: none` alongside the
five loaded releases. A database predating db/029 now raises a `RuntimeError` naming `drugref migrate` rather than
a raw psycopg `UndefinedTable` traceback arriving after two blocks of real answers.

`cli.py` is now **508 lines — OVER CLAUDE.md's ~500 cap**, having been 479 when this round first flagged it as
"close enough that the next handler should split". The review's fixes (the `UndefinedTable` re-raise, the docstring
correcting the `pg_rewrite` claim) pushed it past. **Splitting it is the next change to that file, before any new
handler** — the natural seam is the four `_handle_*` entry points, which already take a connection and are
deliberately thin, versus the DB-free argument layer above them. Filed as debt here rather than done in a review
round, because moving 500 lines while fixing gates would have made both unreviewable.

### What the review of PR #80 found — three gates this round ADDED that did not fire

The round's own thesis, one level up. All three confirmed by mutation against the real database, and all three
now verified dead the same way.

1. **`test_a_superseded_judgement_is_not_an_orphan` could not fail.** It recorded a judgement, corrected it, and
   asserted the result was empty — but never deleted the candidate, so *both* rows resolved through the view's
   `NOT EXISTS` and the empty result was over-determined. Removing `WHERE c.superseded_by IS NULL` from **both**
   arms of db/029's view left the whole suite green. Its own failure message admitted it ("the candidate is still
   projected, so neither row is an orphan"). Now it orphans the candidate and asserts exactly one row, carrying
   the **correction's** `reviewed_against` rather than the predecessor's.
2. **CI's `Confirm nothing was skipped` step could not fail on a failing pytest.** `pytest … | tee out.txt && !
   grep -q skipped out.txt` runs under GitHub's default `bash -e`, which has **no `pipefail`**, so the pipeline's
   status is `tee`'s — always 0 — and only the grep decided the step. A run with 5 failures and 0 skips passed.
   This is the second pytest run against a service container the first run has already written to, so it is
   precisely where a state-dependent failure would first appear. Redirect, don't pipe. (The grep was also
   case-sensitive against `-rs`'s `SKIPPED` lines; it is anchored on `[0-9]+ skipped` now.)
3. **The "no SQL in `cli.py`" guard was being weakened by this round's own lint rule.** It matched
   `f"FROM drugref.{table}"` against raw source, so it was blind to a SELECT split across two lines — and
   **E501 at 88 columns is what forces long SQL to wrap**; this branch splits SQL literals in eleven places. Also
   blind to `INSERT`, `UPDATE`, `JOIN` and a double space. A Python-embedded *writer* to an append-only curated
   table is strictly worse than a reader, and neither the guard nor `pg_rewrite` could see one. Rewritten over
   `ast.parse`d string constants (which fold implicit concatenation, killing all seven evasions at once) and
   extended to **`cli_policy.py`**, which the module docstring's rule is actually about and which nothing scanned.

**Two more surviving mutants**, both now dead: the view's `cc.relationship = c.relationship` predicate could be
replaced by `true` (both orphan tests deleted the candidate table *wholesale*, so only the all-or-nothing case was
exercised — a MED-RT **re-key** is the realistic orphan and now has its own test), and the UNION's second arm had
no field-level pin, so swapping `reviewed_by`/`reviewed_against` there survived while arm 1's assertion stayed
green. **Standing rule: a UNION's arms are two independent column lists. Pin each one.**

Two gates that never fired anywhere are also closed. The seven live-key tables were **three hand-maintained
literal lists across three files**, with "seven" appearing in prose four times and asserted nowhere — an eighth
table shipping a `*_single_live` trigger with a UNIQUE index, or none, was invisible. They are now **derived from
`pg_trigger.tgargs`**, which is the trigger's own natural key, so the fixture asserts the real invariant ("the
index matches what the trigger asks") rather than a literal someone typed; verified by building a synthetic
eighth table in a transaction and watching it be discovered and rejected. And `conftest`'s CI hard-fail branch —
the one thing standing between this suite and a vacuous green — **had never been observed firing**, because the
DSN is set both locally and in CI. It is now a pure `dsn_verdict(dsn, in_ci)` predicate with
`tests/test_dsn_verdict.py` driving all three verdicts DB-free, and it keys on the **presence** of `CI` rather
than its truthiness (some runners export `CI=""`, which the old `os.environ.get("CI")` read as falsy).

Suite **956 → 969**. Orphan exit-code channel deferred to
[#82](https://github.com/cairn-ehr/drugref/issues/82) — `drugref status` still `return 0`s on an orphan, so the
rebuild script that caused it cannot gate on it; that is a CLI-contract decision, not a cleanup.

## Slice 5c.4 — signing the curated overlay (`db/030`, measured 2026-08-10 on `drugref_5c4`)

Spec: [slice-5c.4 signing](superpowers/specs/2026-08-09-drugref-slice-5c4-signing-design.md). Published record:
[signing the curated overlay](https://docs.drugref.org/decisions/signing-the-curated-overlay/). Suite **969 → 1297**
(1260 before the five-reviewer round below).

**⇒ THE FIVE-REVIEWER ROUND (PR [#84](https://github.com/cairn-ehr/drugref/pull/84)) FOUND FOUR THINGS THE FOUR
EARLIER ROUNDS DID NOT, and two of them were MEASURED rather than argued.** Read this block before trusting any
"already reviewed" claim about this slice.

1. **THE RELEASE LAYER'S ED25519 CHECK HAD NO NEGATIVE TEST.** Replacing `_verify_manifest_signature`'s
   `signature_ok = key is not None and signing.verify(...)` with `signature_ok = key is not None` — deleting the
   cryptography from the release layer outright — left the suite **green at 1260 passed**. The row layer had
   `test_a_forged_signature_reports_bad_signature`; the release layer's only BAD_SIGNATURE mentions called
   `_worst_verdict` directly or hand-built a `ManifestVerdict`, so the production call site was never driven.
   Closed by `test_a_manifest_signed_by_a_different_key_reports_bad_signature` and
   `test_a_manifest_body_tampered_after_signing_reports_bad_signature`, **both confirmed to fail under that exact
   mutation**.
2. **A `compromised` REVOCATION WAS UNDOABLE BY ONE ORDINARY COMMAND.** `keys.key_status` and
   `curated_signature_status` both resolved a key's status from the LIVE ROW, and `keys.revoke` refuses no
   transition — so `drugref keys revoke --status active` on a compromised key returned every signature it ever
   made, INCLUDING the thief's, to `valid`/`signed`. Blanket revocation is the design's only answer to a stolen
   key, and it was reversible by the same command that applied it. **db/030 section 3's own comment justified the
   whole insert-then-supersede shape on the grounds that the status history is readable — and NOTHING read it.**
   Both halves now read the whole history; only `invalidates_all_signatures` is permanent, so `rotated`/`retired`
   stay correctable. Distinct from [#85](https://github.com/cairn-ehr/drugref/issues/85), which a floor on
   `signing_key_status_kind` would close and this needed no raw SQL to reach.
3. **A PLANTED `payload_context` DENIED VERIFICATION PERMANENTLY.** `verify_target` subscripted
   `signing.FIELD_LISTS` with the value off the signature row, so one INSERT (`bogus/v9`, or another kind's
   context) raised `KeyError`/`UndefinedColumn` — neither a `RuntimeError`, so `cli.main` printed a traceback —
   and `assertion_signature` being insert-only, the row could never be removed. `signing.entry_context_is_
   reproducible` existed for exactly this and had been wired into the RELEASE path only. Now
   `signing.context_is_usable_for` gates both, and an unusable context is a **verdict** (BAD_SIGNATURE), so the
   honest signatures on the same row still report VALID.
4. **`release_manifest_entry` HAD NO APPEND-ONLY FLOOR TEST.** Removing its trigger left the suite green;
   `DELETE FROM release_manifest_entry` is the most direct way to erase a `dropped` finding. Five tests now fail
   without it. Spec §12 item 10 named all three insert-only tables; only two were covered.

**Also closed in that round:** `drugref verify` exited **0** on `unknown_key` — the CHEAPER forgery (an attacker's
own keypair) — while failing only on `bad_signature`, the harder one; `generate_keypair` returned an unpackable
`tuple[bytes, bytes]`, so a transposed unpack wrote the PRIVATE key into `signing_key.public_key` (32 bytes either
way, on a table the floor forbids DELETE and UPDATE on) — it is now `signing.Keypair`, which raises `TypeError` on
unpacking; `ManifestVerdict`'s three finding lists were MUTABLE inside a `frozen=True` dataclass, so
`verdict.dropped.clear()` flipped `is_intact`; `_CURATED_KINDS`' alarm fired on editing the Python constant and
never on the CATALOG gaining a curated kind; `upstream_releases`' only coverage was `assert isinstance(x, list)`
(true for `[]`, which is what it always got) riding on ambient rows another test file left behind; and four
verification-core paths raised `ValueError`/`KeyError` outside `cli.main`'s catch, now all `signing.SigningError`.
**`signature_backdated` had no caller at all** and is now `drugref status`' fourth block.

**The published canonical-format reference still specified `$`** — the regex this branch had already fixed to `\Z`
because `$` also matches before a trailing newline, which is the context-line injection the validator exists to
stop. It sat inside the comment that bills itself as "reimplementable from this comment alone", so a third-party
reimplementation would have inherited the exact defect. **`db/030` and the canonical payload format are otherwise
UNCHANGED; no committed vector moved** (`make_signing_vectors` reproduces `signing_vectors.json` byte for byte).

**RULE 4 BREACHED AND LODGED, NOT HIDDEN**: `signing.py` 480 → **582** and `release_verification.py` 467 → **532**
crossed the ~500-line guideline in this wave — almost entirely the mandatory prose documenting the four defects
above. Splitting the most security-critical pure module inside the same PR that changed how verdicts are reached
would have made the diff unreviewable as a set of fixes, so it is
[#89](https://github.com/cairn-ehr/drugref/issues/89), with the natural seam named there.

**HOT PATH RE-MEASURED after the view change**, because the compromise fix adds a `NOT EXISTS` to
`curated_signature_status`: on a `TEMPLATE drugref_5c4` clone, old view **1.337–1.371 ms**, new view
**1.309–1.455 ms** — no regression, consistent with the ~1.4 ms below.

**Two layers, one payload format, one key registry.** Curator-held Ed25519 keys sign one curated row's canonical
payload (`assertion_signature`); an institutional key signs a per-release **content manifest** enumerating every
live curated assertion (`release_manifest` + `release_manifest_entry`). Signatures are **detached rows, never a
column** — which is what lets a row be signed at any later time and lets a second reviewer counter-sign.
`cli.py` 508 → 347 lines, split into `cli.py` + `cli_chain.py` (the pure, DB-free argument layer), then
`cli_signing.py` + `cli_signing_release.py`.

**MEASURED on a fresh `drugref_5c4`**, built from the real releases (UNII 26Feb2026 → MED-RT 2026.07.06 → MeSH 2026
→ MeSH-relations 2026.07.06 → GSRS 2026-02-26). **Every count that must not move held exactly** —
`ddi_candidate_pair` **21,664** · `substance_moiety` **19,438** · `open_question` **21,842** ·
`gap_uncurated_interaction_rule` **595** · `gap_uncurated_condition_contradiction` **168**. This slice adds no
projection and no gap kind, so none of them had licence to move; every ingest summary also reproduced 5c.1's.
**ALL FIVE WERE TAKEN BEFORE THE END-TO-END SIGNING EXERCISE**, which afterwards left two curated rows in that
database — so `drugref_5c4` READS 593 for `gap_uncurated_interaction_rule` TODAY, not 595, and the qualifier
belongs here at the claim rather than only in § "How to run / test"'s Dev DSN bullet. Re-measure on a fresh
build. (No line distance is given: the two earlier drafts of this pointer both stated one, and both rotted.)

**Chain wall-clock 132.96 s, and this is [#81](https://github.com/cairn-ehr/drugref/issues/81)'s per-leg
breakdown** — the thing that issue has been waiting for, against 127.5 s (5c.1) and 144 s (post-merge):

| leg | wall-clock | share |
|---|---|---|
| `unii` | 7.17 s | 5.4% |
| `medrt` | 9.78 s | 7.4% |
| `mesh` | 41.78 s | 31.4% |
| `mesh-relations` | 58.91 s | 44.3% |
| `gsrs` | 15.02 s | 11.3% |
| startup + input resolution | 0.28 s | 0.2% |

**The two MeSH legs are 75.7% of the run** — which CONFIRMS the long-standing "the MeSH-keyed leg is the slowest"
note in § issues 7/29 and locates the cost precisely for the first time. GSRS costs **15 s**, not the "~23 s" the
run-instructions block estimates. Nothing here explains the 127.5 → 144 → 133 spread, which stays uncontrolled
and machine-level; **#81 should be read as answered on the breakdown and still open on the variance**, if it is
kept open at all.

**Hot path re-measured**, against 5c.1's recorded 2.5 ms: filtered `curated_ddi_pair` runs **~1.32 ms** with an
empty overlay (matching task 9) and **~1.42 ms** with a populated, signed overlay where the new LEFT JOIN
actually executes and returns 9 rows. The signature join costs **~0.1 ms**. No regression.

**Exercised end to end on that database** — key generate → register → sign a real judgement (cyclosporine ×
`Immunologic Adjuvants [MoA]`, `CI_MoA`, the 9-pair gap row) → verify (`valid`) → publish → verify release
(`intact`) → revoke `compromised` → verify (`key_revoked_compromised`, **and all 9 rows still served**, labelled
`signed_by_revoked_key`). Also confirmed live: a second key counter-signing restores `signed` (one good
signature outweighs one revoked); a `rotated` key's earlier signature stays `valid` (time-scoped, unlike
blanket); adding an uncurated row to a published release is caught as `added` with `signature=valid intact=False`
and exit 1 — **authenticity and integrity are separate answers, and the CLI reports them separately**.

**Traps a future change can break.**
- **The frozen field lists invert this project's own standing rule ON PURPOSE.** Everywhere else a column list is
  derived so it cannot drift; `signing.FIELD_LISTS` is written down, because a signature must verify against the
  payload that *was* signed. Deriving it means the payload silently changes the day the table gains a column,
  invalidating every historical signature. A reviewer who "fixes" this to match the house style breaks every
  signature ever made. The catalog-drift alarm is the guard: it fails when a frozen list and the live table
  disagree, so the mismatch is *reported* rather than silently absorbed.
- **Verification must reconstruct the PAST, not describe the present.** `payload_context` and `algorithm` are
  read back from the recorded signature row, never re-derived from `signature_target_kind`. This was found and
  fixed TWICE, independently — in `signatures.py` (task 7) and then again in `releases.py` (task 8), the second
  time *after* the first was known. Re-deriving passes every test until a `/v2` context or a second algorithm
  exists, at which point every historical `/v1` signature reports `bad_signature`. There is a standing rule for
  this now.
- **`is_revocation` is what makes `status_from` an END boundary**, and dropping it is nearly invisible. Only a
  signature made at or after a *revoking* status's `status_from` is expired; an `active` key's `status_from` is
  its registration time, so treating it as a boundary would expire every signature ever made. Note the covering
  test is narrow: `test_a_signed_curated_row_reads_signed` does NOT pin it (its fixed `SIGNED_AT` predates the
  key's `status_from`, masking the comparison) — only
  `test_one_good_signature_outweighs_one_revoked_one`, which signs LATER, catches its removal. **That one test
  was itself a dated time bomb that failed OPEN** (final review): the key it compares against registered at the
  DATABASE's `now()` while the signature sat at the fixed literal `2026-12-01`, so on and after that date the
  comparison flips and the mutation survives with the suite green. Measured both ways. **Standing rule, second
  instance in this same file: when a test compares two instants, BOTH must be absolute** — moving the literal
  alone does not fix it. `test_the_sql_filter_and_signing_verdict_agree` is now a second, independent guard,
  driving `db/030`'s SQL re-typing and `signing.verdict` over the same nine inputs (Postgres cannot call Python,
  so the duplication is unavoidable — what was missing was anything pinning the two together).
- **The read views join signatures with a LEFT join, and INNER would be a silent recall cut.** An unsigned
  curated row must still be served, labelled `unsigned`. Flipping either join to INNER fails exactly the
  unsigned-row tests and nothing else — **fewer rows is the harm direction for a contraindication**, and a
  key-management event must never be able to cause it.
- **`signed` ≠ verified, and the column cannot ever mean that.** Postgres cannot check Ed25519, so
  `signature_status` reports registry-level facts only. **No verification result is ever cached in a column** — a
  stored "verified" flag is a claim nothing re-checks, which is the failure mode the slice exists to remove.
- **The payload is rebuilt PER SIGNATURE, not once per target.** Two signatures on one row can legitimately carry
  different `signed_at` and different signer fingerprints, both of which are *inside* the signed payload.
  Hoisting the rebuild out of the loop is an obvious-looking optimisation that silently makes the second
  signature unverifiable; two tests guard it, and hoisting even the shared attestation fields trips three more.
- **An empty manifest is a statement, not a wildcard.** Verifying a database that holds curated rows against a
  manifest over zero rows must FAIL with `added`. This is the vacuous-pass shape this project keeps rediscovering,
  and it has its own test in both directions.
- **`row_count_ok` / `manifest_digest_ok` answer manifest SELF-consistency, not live drift.** Both columns are
  writer-asserted, and the verifier recomputes them from `release_manifest_entry` — so a row added to the live
  overlay leaves `row_count_ok=True` while `added` is non-empty, which is correct and looks wrong. Live drift is
  reported by `dropped`/`added`/`altered` alone.
- **Manifest entries key on `natural_key`, never `target_id`.** A `target_id` is a DB-local IDENTITY value, so a
  node that *rebuilt* rather than restored gets different ones and would report 100% churn against a manifest
  whose signed payload is byte-identical. Correction-vs-alteration is decided by walking the supersession chain
  with the digest comparison FIRST, never by comparing ids.
- **WHICH COLUMNS render a `natural_key` is FROZEN in `signing.NATURAL_KEY_COLUMNS`, not read from
  `pg_trigger`** (final review, C1). This is the SECOND place the house derive-from-the-catalog rule is
  deliberately inverted, and it is much easier to miss than the field lists: a natural key looks like
  bookkeeping, but `release_manifest_entry.natural_key` is a *rendered string* recorded at publish time, a signed
  member of the entry group, AND the key verification pairs on. Deriving it from `pg_trigger.tgargs` (as
  `releases._natural_key_columns` did) compares a past recording against today's schema — widen a curated table's
  single-live trigger by one column, the additive migration `db/029` itself contemplates, and every live key
  re-renders, none pairs, and an untouched database reports 100% churn. `enumerate_live` selects the frozen list
  by the CURRENT context when publishing; `verify_release` passes the contexts read back off the manifest's own
  entries. The alarm the rule exists for is rebuilt in `test_signing_payload_coverage.py`, exactly as it is for
  `FIELD_LISTS`.
- **A MANIFEST ENTRY'S `payload_context` IS UNCONSTRAINED INPUT, and the verifier must never subscript a frozen
  dict with it.** `release_manifest_entry.payload_context` carries a regex CHECK and, deliberately, NO foreign
  key, so `'bogus/v9'` — or a real context belonging to the OTHER target kind — is one INSERT away. The first
  version of the C1 fix above raised `KeyError` on both, and `KeyError` is not a `RuntimeError`, so `cli.main`
  does not catch it and `drugref verify --release` printed a raw traceback: a REGRESSION, since the code C1
  replaced reported drop+add. `signing.entry_context_is_reproducible` is the single test (containment, both the
  field list and the natural-key columns, so the cross-kind case cannot reach `UndefinedColumn` one layer over),
  and an unusable entry now FAILS TO PAIR — dropped + added, never a raise. `enumerate_live` falls back to the
  current context rather than skipping the kind, deliberately: skipping would empty the live side and silently
  stop reporting genuinely unpublished rows as `added`, and **fewer findings is the wrong direction for a
  verifier** exactly as fewer rows is for a contraindication.
- **`signatures._target_kind_catalog` and `._row_content_fields` have callers OUTSIDE `signatures.py`** despite
  their leading underscore (`releases.py`, `release_verification.py`), and both docstrings now say so. A
  near-duplicate `releases._target_table` existed for a while, justified in its own docstring on the grounds that
  `_target_kind_catalog` "is private to signatures.py" — while `releases.py` was already calling it two functions
  away. Deleted. **Fifth false WHY comment of this branch**: a comment that explains a choice by asserting
  something about another module is a comment that has to be re-checked, not trusted. **The count reached SEVEN**,
  and the last two were written by the round that fixed the first five — `_render_natural_key` called
  `curated_interaction.relationship` a "CHECK vocabulary" when it is a FOREIGN KEY into `ci_axis(relationship)`,
  whose primary key is unconstrained `text` (so a slash-bearing axis is one INSERT away, not a migration away),
  and a test claimed Postgres refuses `DROP TRIGGER` on a table with pending deferred events. **Measured false:**
  `DROP TRIGGER` succeeds; `ALTER TABLE` and `TRUNCATE` are what Postgres refuses. Writing a WHY is not the same
  as checking one, and a fix round is no safer than the code it fixes.
- **`release_manifest` is a real `signature_target_kind` row but NOT a per-row target.** Its payload is built
  from `release_manifest_entry` and derived counts, so `drugref sign`/`verify --target-kind release_manifest`
  used to die with an uncaught `psycopg.errors.UndefinedColumn: column "entry_count" does not exist` — and only
  once a real release existed to name. `cli.main` catches `RuntimeError`, not `psycopg.Error`. Both commands now
  reject the kind and point at `verify --release`.
- **`signing_key_status_kind` carries no append-only floor** — now lodged as **issue 85** (deferred, matching
  the `ci_axis` / `source_tier` precedent), so `UPDATE signing_key_status_kind SET invalidates_all_signatures =
  false WHERE status = 'compromised'` silently disarms every compromise verdict. A floor here is purely additive
  later — a trigger, not a column — which is why it did not have to land in the immutable file.
  **CORRECTED by the final whole-branch review:** this note used to read "the **two** seeded vocabulary tables",
  which names the wrong remedy. The floor belongs on `signing_key_status_kind` **alone**. Its sibling
  `signature_target_kind` is *designed* to be updated — moving a target kind to a `/v2` `payload_context` is
  precisely the migration the whole read-back mechanism exists to support (`payload_fields`' override,
  `verify_target` rebuilding each signature under its own stored context, `FIELD_LISTS` keeping every retired
  version forever, and since the final review `releases.publish` reading that column instead of a literal).
  Flooring it would forbid the one migration that machinery was built for.
- **`tests/test_cli_signing*.py` cannot really commit**, because other modules assert blanket unfiltered counts on
  shared tables. Its `_NoCommit` harness therefore fakes commit with `RELEASE SAVEPOINT`, which **cannot fire
  DEFERRED constraints** — nine deferred triggers were disabled for that file until `SET CONSTRAINTS ALL
  IMMEDIATE` was added to the fake commit, and the mode is restored in `rollback()` (not `commit()`: when
  `SET CONSTRAINTS` itself raises, the transaction is aborted and only `ROLLBACK TO SAVEPOINT` can recover).
  A commit-call spy is the only thing in the repo that detects a missing `conn.commit()`.

**What it deliberately does not do**: close [issue 2](https://github.com/cairn-ehr/drugref/issues/2) — a superuser
can still drop the append-only triggers, which is arguably *more* visible now, since that is the remaining way to
remove a signature. Also no enrolment protocol or trust root beyond "an operator with database access registered
it", no threshold/quorum interpretation of counter-signatures, and `upstream_releases` is a snapshot rather than a
constraint on what a consumer loaded.

## Slice 5c.2 — the ONC high-priority DDI floor (`db/031`–`db/034`, measured 2026-08-12)

Spec: [slice-5c.2](superpowers/specs/2026-08-11-drugref-slice-5c2-onc-ddi-floor-design.md); plan:
[2026-08-11](plans/2026-08-11-slice-5c2-onc-ddi-floor.md); published record: [the ONC high-priority
floor](https://docs.drugref.org/decisions/the-onc-high-priority-floor/). Suite **1297 → 1395**, then **1409**
after the PR-review round below. **The first slice
that writes clinical content** — and the one where every large decision was reversed at least once by a
measurement.

### ⇒ THE FINDING THAT GOVERNS EVERY FUTURE CLASS-GRAIN RULE

**A class-grain rule inherits its population from the source's class boundary, and that is only safe when the
class was defined by the same mechanism the interaction runs on.**

- **Mechanism-defined classes are clinically sound populations.** `Cytochrome P450 3A4 Inhibitors [MoA]`
  genuinely *is* the right population for an irinotecan exposure interaction: the class boundary and the
  interaction boundary are the same fact. **All four shipped entries are of this kind.**
- **Therapeutic and structural classes are not.** They are taxonomy. `Opioid Agonist [EPC]` conflates two
  distinct mechanisms — serotonergic amplification (tramadol, pethidine) and opioid-action amplification — and
  includes **loperamide**, whose dominant peripheral action makes it the exception at labelled doses.
  `Central Nervous System Stimulant [EPC]` sweeps in **caffeine**, whose risk is dose-dependent and which the
  rule has no way to qualify. Statins carry one severity across 18 members although simvastatin and lovastatin
  are markedly more CYP3A4-dependent than rosuvastatin or pravastatin.

**Seven drafted class×class entries were therefore withheld from an append-only table** and deferred to
[#94](https://github.com/cairn-ehr/drugref/issues/94), with their full encodings retrievable from commit
`389a560`. **A class-grain rule is not automatically cheaper than a moiety one** — it is only cheaper when the
class was minted by the mechanism the interaction runs on. Drafting 11 and shipping 4 is the intended outcome
of a clinical review gate, not a shortfall.

### The shape changed twice, and both reversals were measured

1. **The list was first treated as curator-ORIGINATED content** needing a `basis` column and a widened read
   path — until the schema showed 5c.1 had already keyed `class_contraindication` on `(subject, object,
   relationship, SOURCE)` and had written into `curated_interaction`'s own comment that its key omits `source`
   *"however many upstream authorities asserted it"*. **The candidate tier was built for multiple authorities;
   MED-RT was merely the only one.** So ONC enters as `source = 'ONCHIGH'` and **db/029 was not touched at
   all**. Spec §2.3 keeps the rejected shape on record for the round that has genuinely source-less content.
2. **Then the list was retrieved and refuted the grain.** Spec §2.1 had measured whether *MED-RT* carried these
   pairs; it never asked what shape the *ONC list* is in. Of Phansalkar 2012's fifteen: **4 drug×class, 2
   drug×drug, 8 class×class, 1 class self-pair.** Four of fifteen is not a floor. Flattening by inverting
   orientation was costed at **~155 curated rows for the five MAOI facts alone** and rejected. Hence `db/032`'s
   class-subject grain — spec §14.

### The four migrations

- **`db/031`** — widen `class_contraindication_source` to `('MED-RT','ONCHIGH')`, widen `ingest_run`'s source
  and writer vocabularies, add `ci_axis` row **`CI_EPC → has_EPC`**, add `ingest_unresolved_onc_endpoint` +
  `gap_unresolved_onc_endpoint` + **gap kind fifteen** `unresolved_onc_endpoint`. The table is needed because
  `_GAP_SOURCES` derives every kind **from a view**, and an endpoint resolving to nothing is in no table until
  something puts it there — db/016's exact precedent.
- **`db/032`** — `class_pair_contraindication` (candidate) + `curated_class_interaction` (overlay). **Two
  tables, not a polymorphic subject column**: `forbid_multiple_live_assertions` compares natural-key columns by
  **equality**, and `NULL = NULL` is not true, so a nullable subject would have made the single-live guard
  silently stop guarding. Slice 5b's precedent — two relations, because the endpoints are different kinds of
  thing. A **class** self-pair is legal here while a **moiety** self-pair stays forbidden (db/014); the
  expansion excludes identical moieties.
- **`db/033`** — `curated_ddi_pair` `CREATE OR REPLACE`d to carry both grains, gaining `rule_grain`
  (`moiety_rule`|`class_rule`) and `via_subject_class`. **One view, not two**: fewer rows is the harm direction
  for a contraindication, so a consumer who forgets a filter must get *more* advice, never less.
- **`db/034`** — the hot-path recovery, below.

### THE PERFORMANCE ROUND — a 3.6× regression found, escalated, and fixed at its cause

`db/033` widened `ci_class_subtree`'s seed so a class subject could expand through it. That **inflated the
planner's row estimate for the recursive CTE ~5×** even when actual rows barely moved, tipping a Hash Join into
a Merge Join + Sort. Measured, then **independently reproduced by the reviewer on its own `TEMPLATE
drugref_5c4` copy**:

| state | hot path |
|---|---|
| moiety grain only (5c.4 baseline) | 1.4–1.7 ms |
| `db/033`, class overlay **EMPTY** | **4.7–5.4 ms (~3.6×)** |
| `db/033`, populated (SSRIs 73 × MAOIs 31) | ~9.0–9.3 ms (~6.5×) |

**The empty-overlay row is what made this a decision rather than a note**: the cost was *structural*, paid by
every existing consumer on every query, for content most of them do not have. `db/034` restored
`ci_class_subtree`'s original seed and gave the class grain **its own walk** (`ci_class_pair_subtree`):

| state | after `db/034` |
|---|---|
| empty class overlay | **1.50–1.68 ms** — baseline restored |
| populated class grain | **2.87–3.28 ms** |

The reviewer verified the moiety-grain half's plan is now **byte-identical** to the pre-`db/033` plan
(`cost=14.94..3886.97 rows=37414`, actual 1233 — **1235 on `drugref_db034`, and the +2 is attributed in § "The
reference-database rebuild"**) against `drugref_5c4` itself, and confirmed **no planner GUCs or
statistics tricks** — the gain is structural. A residual ~2.2× floor is disclosed rather than hidden: `UNION
ALL` still evaluates the class arm before filtering, now proportional to the class grain's own content.

### Measured with the four entries loaded (scratch DB from the real releases, 2026-08-12)

**ONCHIGH candidates 8** (4 entries × salt-form expansion) · **pairs 213** — atazanavir-PPI 12, irinotecan-CYP3A4
138, ramelteon-CYP1A2 21, tizanidine-CYP1A2 42 · **unresolved endpoints 0** ·
`gap_uncurated_interaction_rule` **593 → 591** (**on a `drugref_5c4` copy whose baseline was already 593; from a
clean baseline the same measurement is 595 → 593, same net −2 — § "The reference-database rebuild" derives the
mechanism**) · `open_question` **21,842 → 21,848**, reconciled at row level ·
hot path **1.551–1.679 ms**. **The two counts that must not move did not: `ddi_candidate_pair` MED-RT 21,664 and
`substance_moiety` 19,438.**

**Why the worklist dropped by 2 and not 4, and it is the design's payoff rather than a discrepancy:**
`curated_interaction`'s key **omits `source`**, so curating tizanidine against the CYP1A2 class also answered a
pre-existing **MED-RT-sourced** rule on the same natural key. One clinical fact, one live drugref judgement,
however many upstream authorities asserted it — demonstrated rather than asserted.

### Salt forms are expanded on the PROJECTION side

The orchestrator resolves a subject to the base moiety plus every gated-in moiety the composition tree marks as
carrying it as an active component, writing one candidate per form. **Not at read time**, for three reasons in
order of weight: issue 68 measured ~19% of moieties carrying a questionable GSRS `ACTIVE MOIETY` edge, and
inheriting clinical *advice* along that population is the wrong first use of it; a rebuildable projection
**re-derives**, so a salt form arriving later becomes a *visible ungraded candidate* rather than a silent hole;
and it costs the hot path nothing. **Not in the curated rows either** — those are immortal.

### Four ONC entries are unencodable, each for a different reason

- **#6 febuxostat–azathioprine/mercaptopurine** and **#30 tranylcypromine–procarbazine** — the object must be a
  class. `Purine Antimetabolite [EPC]` actually *excludes* mercaptopurine; the only class both share is a
  77-member grab-bag spanning antivirals and antimalarials.
- **#21 QT × QT** — **MED-RT carries no QT/torsades/prolongation class at all** (0 rows, verified twice). The one
  entry `db/032`'s self-pair shape was *designed* for, failing for want of upstream data.
  [#93](https://github.com/cairn-ehr/drugref/issues/93). Confirmed by search that **no open redistributable QT
  list exists** — neither FDA, EMA nor BfArM maintains one, and CredibleMeds is registration-gated and
  non-redistributable.
- **#27 CYP3A4 inhibitors × ergot alkaloids** — CYP3A4 inhibitors exist only as `[MoA]`, ergot alkaloids only as
  `[EPC]`. **A class-pair rule has ONE axis, so it selects ONE `class_membership.relationship`** — a mixed-kind
  rule ingests cleanly and then expands to **zero pairs forever, with no error anywhere**. That is db/006's
  failure mode one tier up, filed as [#92](https://github.com/cairn-ehr/drugref/issues/92).

### Traps and standing notes

- **`MEDRT` is not a spelling of `MED-RT`, and the difference is permanent.** An `identifier_scheme` lands
  inside the FROZEN `gap_key` (`'ONCHIGH:' || entry_id || ':' || identifier_scheme || ':' || identifier_value`)
  and `question_uuid = uuid5(gap_kind, gap_key)` is immortal and externally cited. Caught in review; the reason
  is now a comment so it is not "tidied" back.
- **The `curate` step is deliberately NOT a chain step.** `drugref ingest onchigh` writes the projection and
  joins the chain; `drugref curate onchigh` writes the append-only overlay and never does. Folding them would
  let a routine chain re-run write to the one tier where a mistake is permanent. Curate is idempotent **by
  comparison** — only graded fields, never `reviewed_at`/`reviewed_by`, which would supersede the whole file
  every run.
- **`IngestStep.packaged_defaults`** exists because the first wiring put the packaged-data default in the
  per-source parser only, so a chain run selecting `onchigh` aborted the **whole eight-step chain** with
  `InputResolutionError`. Declared on the step, read by both consumers; the review reproduced the abort by
  execution and a worktree at the pre-fix commit proved the new test fails there.
- **`curate_onchigh` counts what it skips.** It first dropped unresolved entries with no table, no counter and
  no log, while `rules_seen` counted them anyway — numbers that could not reconcile. `CurateSummary` now carries
  `entries_resolved`/`entries_unresolved` and a test asserts every entry lands in exactly one bucket. Issue 71's
  standing rule, re-learned.
- **`onchigh_run.py` was split at 490 lines** into `onchigh_resolve.py` (410) + `onchigh_run.py` (268) on the
  resolution/writing seam; the reviewer diffed the moved functions and confirmed a verbatim move.
- **Two runs of the drafting task were killed by the machine sleeping and lost everything**, twice, because work
  was composed in memory and committed at the end. The fix was incremental commits (`d669d76`, `39f2fc8`).
  Worth remembering for any long agent-driven task.

### The PR review round (PR #95), and the one pattern behind it

Six review passes over the finished branch. **The findings had a single root cause, and it is the thing to carry
forward: the class grain inherited 5c.1's WRITE path and none of its DETECTORS.** The moiety grain has a gap view
for every way a rule can fail — ungraded, unpopulated, orphaned, unreviewed-root — and `db/032`–`db/034` added a
second grain that has none of them. Individually each omission reads as a reasonable follow-up; together they
mean a class-grain contraindication can be **ingested, graded, committed and reported successful while reaching
zero patients, with `drugref status` printing health**. That is why #96–#99 are filed as one group below.

**Two defects were fixed in this round, both about a frozen or cascading identifier:**

- **The `unresolved_onc_endpoint` `gap_key` omitted `endpoint_role`.** `db/031`'s own COMMENT states the rule
  the key broke — the view's grain is `(source, entry_id, endpoint_role)` and "the grain a gap_key built from
  this view must also use". It was invisible while every entry had a moiety subject, because the two roles then
  carry *different* schemes (`UNII` vs `MED-RT`) and so differ anyway. A **class** subject records
  `OBJECT_SCHEME` on both roles, so a class **self-pair** — the shape `db/032`'s DECISION 2 deliberately permits
  for the ONC list's real QT×QT entry, and the one issue 93 says cannot resolve — folded two independently
  failing endpoints onto **one immortal `question_uuid`**, silently overwrote one role's text with the other's,
  and let closing either role retire the question for both. `counts` over-reported at the same time.
  **`identifier_value` is now canonicalised at the same moment** (`UnresolvedEndpoint.__post_init__`), because
  the raw file spelling was also reaching the frozen key — `qzu4h47a3s` and `QZU4H47A3S` minted two different
  permanent questions for one unknown identifier. **Both changed together on purpose: one break of a frozen key,
  not two.** Existing test `test_unresolved_endpoint_table_is_keyed_per_run_and_role` already used colliding
  data and stopped one step short of registration, which is how it survived four review rounds.
- **`register_from_gaps`' retention guard never learned `curated_class_interaction`.** Every curated table is
  `ON DELETE CASCADE` from `open_question` *and* carries an append-only trigger that refuses `DELETE`, so a
  citing table missing from the guard does not lose data quietly — the cascade hits `forbid_overlay_rewrite`,
  which RAISEs, which **aborts the whole ingest transaction, for every source, permanently**. Reachable through
  a public keyword argument on `curation.record_class_interaction_judgement`; latent only because `cli_curate`
  never passes it, which is precisely the state `curated_condition`'s own guard was added in. **Standing rule
  now written at the call site: whenever a table gains a `question_uuid` FK, it belongs in that list.** Fixing
  this also made `db/032`'s justification for the `curated_class_interaction_by_question` index TRUE — it
  claimed `register_from_gaps` probes the table, which until now it did not, leaving the index dead.

**Also fixed:** `_optional_str` treated a present-but-wrong-typed value as absent, so `management = ["…", "…"]`
(an easy slip when every real value is a `"""` block) silently dropped the prescriber-facing instruction — and
nothing downstream requires `mechanism`/`management`, unlike severity/evidence_grade, so it exited 0 · the chain
now **logs at WARNING when it falls back to a packaged default** (`glob` is non-recursive while the rest of the
download tree is nested, so an operator's corrected list one directory deeper was silently replaced by ours) ·
`curate_onchigh` **refuses two entries that resolve to one natural key** (`CollidingRuleError`) — unguarded, the
second superseded the first *within one run*, counted as a routine regrade, and never converged · `OncSummary`
gained `class_rules_attempted` so the class grain reconciles like the moiety grain's
`salt_forms_expanded`/`rules_written` · the ingest half now names an unresolved entry at WARNING, as
`curate onchigh` already did.

**Tests added for behaviour that was correct but unpinned** — the `ingest_onchigh` rollback path (only
`resolve_entry` had been tested, never the orchestrator around it, so dropping the re-raise would have returned
a normal-looking summary having cleared ONCHIGH and written nothing), idempotence against a **differing**
`reviewed_by` (both existing tests passed `"Dr X"` twice, so a comparison that wrongly included it would still
have reported `unchanged`), the class half's `superseded_by`/`applies` predicates, `ci_class_pair_subtree` past
one level including a diamond, and the shipped `onc_high_priority.toml` itself — which no test had ever parsed,
only referenced by path, leaving the four-entry clinical floor unasserted after `66321f3` cut it from eleven.
Suite **1395 → 1409**.

**One reported finding was investigated and rejected:** the gap view folds `identifier_scheme`/`identifier_value`
through independent `max()`, which can in principle emit a scheme/value pair no release ever asserted. It is not
reachable — `onchigh_run` clears the worklist per source before re-recording, so every view group has exactly
one row and `max()` is an identity. It was reproduced only by inserting rows directly, bypassing the orchestrator
that the architecture makes the sole writer. Worth knowing the guard is the clear step, not the view.

### Filed, not fixed

**#90 and #96–#99 are one group — the missing class-grain detectors described above.**
[#96](https://github.com/cairn-ehr/drugref/issues/96) **no worklist gap kind**, so an ungraded
`class_pair_contraindication` asks nobody to grade it — the grain's *primary* question, while `db/031` added a
kind for the lesser one · [#97](https://github.com/cairn-ehr/drugref/issues/97) **both grains can grade one pair
with different severities** and `curated_ddi_pair` states no precedence; `via_subject_class` being NULL on every
moiety row defeats the obvious consumer-side tie-break, which is where `db/032`'s NULL-comparison hazard
resurfaces — not in a trigger, but in every consumer query · [#98](https://github.com/cairn-ehr/drugref/issues/98)
**a signed release silently omits the whole class grain** (`curated_class_interaction` is not a
`signature_target_kind`, so `verify_release` PASSES on an incomplete set — worse than failing) ·
[#99](https://github.com/cairn-ehr/drugref/issues/99) **class-grain roots are outside
`gap_unreviewed_expansion_root`**, so the `allow`-by-default expansion policy is honoured but never reviewed.
Also [#100](https://github.com/cairn-ehr/drugref/issues/100) replaying `db/033` alone reinstates the 3.6×
regression, and a note on [#92](https://github.com/cairn-ehr/drugref/issues/92): **`db/032`'s own preamble cites
`statins × CYP3A4 inhibitors` as the worked example motivating the grain, and that is a `[EPC]`×`[MoA]` pair the
schema cannot express** — measured at 0 pairs. All four shipped entries are same-kind, so nothing in tree trips it.

[#90](https://github.com/cairn-ehr/drugref/issues/90) `curated_target_unresolved` does not cover the class grain
· [#91](https://github.com/cairn-ehr/drugref/issues/91) **`drugref_5c4`'s ledger checksum for `030_signing.sql`
is stale**, so the reference DB and every `TEMPLATE` copy refuse `drugref migrate` (db/030 was edited after being
applied there, during the five-reviewer round; the test suite never sees it because it drops the schema each
session) — **RESOLVED 2026-08-13 by rebuilding onto `drugref_db034`, § "The reference-database rebuild";
`drugref_5c4` keeps its stale ledger deliberately, as a kept control** ·
[#92](https://github.com/cairn-ehr/drugref/issues/92) mixed-kind class-pair rules ·
[#93](https://github.com/cairn-ehr/drugref/issues/93) no QT class ·
[#94](https://github.com/cairn-ehr/drugref/issues/94) the seven deferred entries.

**Licence-clean sources found while researching the QT gap** — OnSIDES and DrugCentral — were recorded here as
"worth evaluating"; they have since been **measured**, and one of the two hopes recorded in this paragraph was
wrong. See § "The 5c.3 source evaluation" below.

## The reference-database rebuild (issue 91, 2026-08-13) — `drugref_db034`

Issue 91: `drugref_5c4`'s ledger recorded `ef136553cc52` for `030_signing.sql` where the merged file hashes to
`914b6d0049ac`, so `apply_migrations` refused there **and on every `TEMPLATE` copy** — the documented way to
measure a slice. Option 1 of the issue was taken: **rebuild from the real releases against the merged
migrations**, which also re-verifies every count the docs quote. Nothing was patched; `drugref_5c4` is kept
as-is, exactly as the "never patch a verification database" rule says.

**The name states the checkable fact.** `drugref_db034` is named for its migration head, not its slice, because
a reader can verify the claim in one query — `SELECT max(filename) FROM drugref.schema_migration` → `034` — and
the slice names had already gone out of order (5c.4 was built before 5c.2). This file naming a database
authoritatively is an assertion; make it one the database can answer.

**What it holds** — a clean ledger (34 rows, no drift), the full chain, **and** slice 5c.2's shipped clinical
floor, in that order. `reviewed_by` on its eight curated rows is the marker `reference-rebuild (issue 91)`, not
a clinician: the *content* is the committed, clinically-reviewed `onc_high_priority.toml`, but the act of
running `curate` here was a rebuild, and an append-only row that names a physician who did not run it would be
a small lie in an immortal table. A production load passes the real curator.

**The measurement ladder, all on this one database.** Recording the intermediate states is the point — 5c.1's
figures and 5c.2's are now readable off one build, and each stage's delta is attributable:

| after | key counts |
|---|---|
| **chain** (unii 26Feb2026 → medrt 2026.07.06 → mesh 2026 → mesh-relations 2026.07.06 → gsrs 2026-02-26; **148.6 s**) | `substance_moiety` **19,438** · `ddi_candidate_pair` **21,664** · `condition_contraindication_expanded` **192,161** · `gap_uncurated_condition_contradiction` **168** · `gap_uncurated_interaction_rule` **595** · `open_question` **21,842** · `class_contraindication` (MED-RT) **635** · every curated view **0** |
| **`ingest onchigh --release ONCHigh-2015`** | ONCHIGH candidates **8**, pairs **213**, unresolved endpoints **0**; worklist **595 → 601**, `open_question` **21,842 → 21,848**; MED-RT's 21,664 and `substance_moiety` 19,438 **unmoved** |
| **`curate onchigh`** | `curated_interaction` **8** · `curated_ddi_pair` **255** · worklist **601 → 593** · `open_question` unchanged |

**Every count and ingest summary § "Slice 5c.1" records reproduced exactly**, from a clean ledger, which is the
second thing the rebuild was for — including the summaries themselves (`UniiSummary(moieties=19438,
gated_out=148608)`, MED-RT `contraindications=635`, `also_contraindicated_pairs=168`, GSRS `rows_written=8671`).
**What it does NOT reproduce, and the claim is deliberately narrow: nothing from the signing layer.** No key was
registered, no row signed, no release published here, so 5c.4's signing-specific measurements are *not*
re-verified by this build and stay readable only on `drugref_5c4`. Chain wall-clock **148.6 s** against 5c.4's
132.96 s and 5c.1's 127.5 s — and **uncontrolled**, since a 1.4 GB download was streaming on the same machine;
that is issue 81's variance, not a regression to chase.

**The two numbers that look different from § "Slice 5c.2" are the same measurement from a clean baseline, and
this is worth reading before re-quoting either.** 5c.2 recorded the worklist as **593 → 591**; here it is **595
→ 593**. Both are a net **−2**. 5c.2 measured on a `TEMPLATE drugref_5c4` copy, whose baseline was already 593
because 5c.4's end-to-end exercise had left two curated rows there. The pristine ladder also makes the
*mechanism* legible, which the net figure hides: the ONCHIGH projection adds **8** rules but only **+6** to the
worklist, because 2 of the 8 share a natural key with rules MED-RT already asserted; curating then closes **8**
— the 6 new plus those 2 — for a net −2. `curated_ddi_pair` **255 = 213 ONCHIGH + 42 MED-RT** (tizanidine and
tizanidine hydrochloride, 21 pairs each), the same fact from the other side. **One clinical fact, one live
drugref judgement, however many upstream authorities asserted it** — now visible as arithmetic.

**Hot path re-measured, and the subject is named this time.** `EXPLAIN ANALYZE SELECT * FROM
drugref.curated_ddi_pair WHERE subject_moiety = '825bbad7-3253-548c-8324-ccfae8ae3d68'` (**irinotecan** — one of
the three joint-largest curated subjects at 46 pairs each; irinotecan hydrochloride and irinotecan sucrosofate
tie with it, which is the salt-form expansion working as designed), five runs: 2.748 → 2.291 → 1.881 → 1.595 →
**1.550 ms**, so the warm band sits inside the **1.50–1.68 ms** recorded in § "Slice 5c.2"'s table above — **not
in `db/034`, which records the coarser `~1.4 ms` baseline**; the band has only ever had one home and it is this
file. The moiety grain's recursive union is **`cost=14.94..3886.97 rows=37414`** — byte-identical to the
signature the 5c.2 reviewer recorded against `drugref_5c4`. Its **actual** rows are **1235** where that reviewer
recorded 1233 (`db/034`'s own frozen `COMMENT ON VIEW` states the actual as a band, **1,233–1,238**, of which
1,233 is the narrow-seed end — so 1235 is inside it, not a contradiction of it), and the +2 is fully attributed
rather than shrugged at: of ONCHIGH's three object classes, exactly one — **`Proton Pump Inhibitor [EPC]`** — is
not already a MED-RT contraindication object, so it joins `ci_class_subtree`'s seed and contributes itself plus
one descendant. Both verified by query. **Which moiety was measured had never been written down**; a plan whose
subject is unknown cannot be re-run, so it is written down now.

**The broken workflow was re-tested, not assumed fixed:** `CREATE DATABASE drugref_tmpl_check TEMPLATE
drugref_db034` → `drugref migrate` → `migrations applied`, ledger still 34 rows, database dropped. That is the
exact sequence issue 91 reported broken.

## The class-grain detector round (2026-08-14) — `db/035`, issues 90, 96, 97, 98, 99

ROADMAP § 5c.2a. Suite **1409 → 1451**, `ruff` clean. **One migration, because the five issues are one defect
reported five times**: `db/032`–`db/034` gave the class × class grain slice 5c.1's WRITE path and none of the
moiety grain's DETECTORS, so a class-grain contraindication could be ingested, graded, committed and reported
successful **while reaching zero patients, with `drugref status` printing health**.

### Nothing on real data moved, and that is the expected result

Measured on `drugref_db035` against `drugref_db034` as control — **every count byte-identical**:
`ddi_candidate_pair` MED-RT **21,664** · `substance_moiety` **19,438** · `curated_ddi_pair` **255** ·
`gap_uncurated_interaction_rule` **593** · `gap_unreviewed_expansion_root` **0** · `curated_target_unresolved`
**0** · `open_question` **21,848**. All four new class-grain objects read **0**, because `class_pair_contraindication`
is empty: #94 withheld the seven class × class entries and nothing else writes the grain. **A detector's correct
reading on today's data is zero, and a round that changed a count would have been the surprise.**

### The hot path was measured INTERLEAVED, because the alternative is how #81 happened

A first, naive pass read ~1.5 ms before and ~1.72 ms after and looked like a **13% regression**. It was warm-up.
Re-measured by alternating the same query between a `db/034` control and the `db/035` database, 12 runs each, both
pre-warmed:

| database | mean | spread |
|---|---|---|
| `drugref_db034` (control) | **1.626 ms** | 1.518–1.857 |
| `drugref_db035` | **1.662 ms** | 1.523–1.767 |

**2.2% apart, with the control's own spread (0.34 ms) wider than the difference, and the control's slowest run
slower than anything the new schema produced.** The moiety grain's recursive union is **byte-identical** —
`cost=14.94..3886.97 rows=37414`, actual **1235** — so the plan did not move at all; the `severity_kind` join is
two hash joins against a four-row table. Query: `EXPLAIN ANALYZE SELECT * FROM drugref.curated_ddi_pair WHERE
subject_moiety = '825bbad7-3253-548c-8324-ccfae8ae3d68'` (irinotecan), the subject § "The reference-database
rebuild" wrote down for exactly this reason. **The sequential before/after is the shape that produced #81's
unexplained +13%; an interleaved control is what it costs to not repeat it.**

### The seven pieces, and the decision inside each

- **`severity_kind`** — the four grades become **ordered data**, and the five identical `CHECK (severity IN (...))`
  constraints (`db/020` ×2, `db/029` ×2, `db/032`) become five foreign keys into it. Needed because #97's answer
  must be **writable in SQL** and `severity` is text: `ORDER BY severity` sorts `'contraindicated' < 'major' <
  'minor' < 'moderate'`, putting **minor above moderate** — not merely useless but inverted. **Rank 1 is the most
  severe**, so `ORDER BY severity_rank` is most-severe-first with no `DESC` to forget. db/006's finding for the
  fifth time. **An illegal severity now raises `ForeignKeyViolation`, not `CheckViolation`** — "a different
  exception class naming the identical hazard", `cli_signing.py`'s own phrase for the same substitution; nothing
  catches either on any path, so operator-visible behaviour is unchanged.
- **`class_pair_rule_reach`** — `ci_rule_partner_reach` one grain over, and a **product** rather than a count,
  because a class × class rule expands on both sides: 4×0 and 0×4 are both dead and are fixed in different places.
  Carries subtree, direct AND **effective** counts (subtree or direct per today's `class_expansion_policy`, using
  `db/034`'s predicate verbatim) — without the effective pair, the worklist would queue a rule whose root is
  DENIED and which therefore reaches nobody, which is #36's measured mistake one grain over. `max_pair_count` is an
  **upper bound** (the read path excludes a drug pairing with itself) and is **exact about zero**, which is the only
  threshold anything tests. Walks `ci_class_pair_subtree`, never `ci_class_subtree` — re-merging them is #100.
- **`gap_uncurated_class_interaction_rule`, gap kind sixteen** — the grain's PRIMARY question, which it shipped
  without while `db/031` added a kind for the lesser one. **Grouped on the three natural-key columns WITHOUT
  `source`**: the candidate PK includes source and the overlay's key omits it, so ungrouped, one rule asserted by
  two authorities would raise two rows on one `gap_key` and `register_from_gaps` would upsert them onto **one
  immortal `question_uuid`**, silently overwriting one text with the other — the 5c.2 review's own defect, avoided
  rather than re-learned. Key `CLASS:{subject}/CLASS:{object}/CI_AXIS:{relationship}`, `CI_AXIS:` matching
  `uncurated_interaction_rule`'s existing spelling rather than inventing a second convention.
- **`gap_unreviewed_expansion_root` WIDENED IN PLACE, not copied** — the design decision of the round. The question
  is "may this class expand?", the answer is **one** `class_expansion_policy` row, and `question_uuid =
  uuid5(gap_kind, 'CLASS:' || class_uuid)` is immortal. A second gap kind over the same class would mint a **second
  permanent question one decision answers**, and a curator answering it would retire one and not the other, for
  ever. So the class arm joins under the same kind and the same key: **not one existing `question_uuid` moves**.
  `ci_rule_count` now counts expanding rules of **either** grain, which is what "ride on the answer" always meant.
  The class arm contributes **both** classes (`db/034` expands both sides), with a `DISTINCT` inside the lateral so
  a legal class **self-pair** (`db/032` DECISION 2) counts once rather than twice.
- **`curated_target_unresolved`** — third arm, plus **one trailing column** `subject_class`. Not a rename:
  `subject_moiety` cannot carry a class UUID under a name that says moiety, and `CREATE OR REPLACE VIEW` cannot
  rename or reorder anyway. `db/030`'s precedent. `target_table` was always the discriminator and still is —
  filtering on either nullable subject column silently drops the other arms.
- **`curated_ddi_pair`** — fifth `CREATE OR REPLACE`, one trailing `severity_rank`, and the precedence **stated in
  the view's own COMMENT** (below). The `severity_kind` join is **LEFT in both halves**, which looks pedantic
  against a four-row table and is not: `INNER` would let an unrankable severity **delete a row of clinical
  advice**, and fewer rows is the harm direction. The FK makes a miss unreachable; the LEFT makes it harmless if it
  ever became reachable again.
- **`curated_grain_disagreement`** — rule-PAIR grain, not drug-pair: two rules can overlap on thousands of pairs
  (SSRIs × MAOIs alone is ~2,263) and one curator decision must not be reported thousands of times. Counts
  `DISTINCT` partners, not join rows, because `ddi_candidate_pair`'s `DISTINCT ON` includes `source` and a
  two-authority rule yields two rows per pair. **An operator view, deliberately NOT a gap kind yet**
  ([#105](https://github.com/cairn-ehr/drugref/issues/105)): a `gap_key` is frozen for ever, this project has
  broken one twice and caught it in review both times, and **zero class-grain rows ship**, so the key's grain would
  be chosen against no real instance. The detector lands now; the immortal identifier waits for content.
- **`signature_target_kind` += `curated_class_interaction`** — with `signing.CURATED_CLASS_INTERACTION_V1` and its
  key tuple. **The SQL is only half the fix and the SQL is the half that fires the alarm**: the existing
  `test_every_curated_catalog_kind_is_covered_by_a_release` derives its expectation from the CATALOG, so the
  `INSERT` failed the suite until `releases._CURATED_KINDS` was widened. That is #98's actual severity — a kind
  absent from that tuple is absent from the manifest **and** from the live side of the comparison, so its rows are
  never even reported as `added` and `verify_release` calls an incomplete release intact.

### ⇒ THE PRECEDENCE, and why most-severe-wins needs the disagreement view to be defensible

**`ORDER BY severity_rank, (rule_grain = 'moiety_rule') DESC`** — most severe first, moiety grain breaking ties
(the rule naming an actual drug carries better mechanism/management text than one naming its whole class). Chosen
by the human partner from three options. Severity-first because **under-warning is the harm direction** on this
path, the same reason a signature never gates a read and a missing expansion policy expands. **It is an ORDER, not
a filter** — both rows still appear, because dropping one would make the view state less than it knows.

**The half that is easy to get wrong: over-warning is not free.** Click-through fatigue is a leading reason
prescribers stop reading alerts at all, and refusing to flood them with clinically irrelevant warnings is part of
why drugref exists (docs/essays/why_drugref.md). Most-severe-wins is defensible **only because
`curated_grain_disagreement` turns every such case into finite work somebody reconciles** — a class rule
out-ranking a curator's specific milder grade is a row that gets answered once, not permanent noise. The order and
the detector are one decision, not two.

### Traps and standing notes

- **`drugref status` gained a FIFTH block, and it is the one a gap kind could not carry.** A class rule reaching
  **zero** drug pairs is not a curator question (grading it changes nothing — #36), yet it is exactly the state
  this round is named for. A curator cannot be told; an operator must be. `_print_class_grain_block` is split out
  of `_handle_status` because it is the only block a test can drive alone — the four above it need a whole status
  run, which is why three shipped untested and two of those shipped unreached (issues 74, 76, review I7). Its
  dead-rule count is `DISTINCT` on the three natural-key columns, or a two-authority rule would print twice while
  the gap view beside it printed once.
- **Two stub connections in the test suite needed a `fetchone`.** The new block reads scalar counts;
  `tests/test_cli.py`'s `_EmptyConn` and `tests/test_curation_orphans.py`'s `_Conn` only had `fetchall`. Both now
  return `(0,)` — deliberately not real rows, since the class grain's own output is tested against a real database
  in `tests/test_class_grain_detectors.py` and those two files exist to pin *rendering*.
- **Two of the 39 new tests were written, observed PASSING, and kept anyway — and two others were rewritten
  because they passed for the wrong reason.** `test_an_unknown_severity_is_still_refused` and
  `test_both_rows_still_appear` are characterisation guards over behaviour the round must NOT change, and say so.
  But `test_one_class_named_by_both_grains_raises_ONE_question` and `test_a_ruled_class_grain_root_leaves_the_gate`
  originally asserted only "one row" and "zero rows" — **both true before the widening existed**, so neither could
  fail. Fixed by asserting `ci_rule_count = 2` (only the widened view counts both grains) and by asserting the row
  is PRESENT before the policy decision retires it. That is issues 74/66/76's shape caught inside one round rather
  than four rounds later.
- **`_UNRESOLVED_COLUMNS` being ONE list is what made #90's Python a one-line change.** The tuple drives both the
  `SELECT` and the `UnresolvedTarget` construction, so adding `subject_class` there was the whole edit. Its
  `ORDER BY` gained the column too: on the class arm `subject_moiety` is NULL for **every** row, so the first sort
  key stops discriminating there and two class rules sharing an object and an axis would tie on all four original
  columns — the same flake that ORDER BY was widened once before to prevent.
- **`UnresolvedTarget`'s docstring predicted this exact migration** ("a third UNION arm in a later migration would
  then make `drugref status` refuse a legitimate row") and is why nothing had to change beyond a field: the
  discrimination was deliberately never enforced in a `__post_init__`.
- **Editing `db/035` after applying it to `drugref_db035` invalidated that database's ledger checksum** — issue
  91's exact failure, reached deliberately, on an UNMERGED branch where editing is the documented exception. The
  fix is the documented one and it is cheap: drop and re-create from `TEMPLATE drugref_db034`, then `drugref
  migrate`. **Do that after the last edit to a migration file, never before.**

## The PR #107 review round (2026-08-14) — `db/036`, `cli_status.py`, suite 1451 → 1465

Five specialist reviewers over `db/035`'s diff. The migration's factual density held up — every measured
figure, every prior-migration attribution and the whole `severity_kind` / `ForeignKeyViolation` chain verified
clean — and the defects clustered in the layer the migration was *not* about: the Python that reads it.

**⇒ THE STANDING RULE THIS ROUND BOUGHT, and it is the one to carry forward: A MIGRATION THAT WIDENS A VIEW A
GUARDED BLOCK READS MUST WIDEN THAT BLOCK'S EXCEPTION TUPLE IN THE SAME COMMIT.** `db/035` added
`subject_class` to `curated_target_unresolved`, and `curation.unresolved_targets` selects it by name — so on a
database that HAS the view but predates `db/035` the failure is **`UndefinedColumn`, not `UndefinedTable`**, and
those are *siblings* under `ProgrammingError`, not subclasses (`issubclass(...)` is `False`). The guard written
for exactly this moment did not fire. **Reproduced on `drugref_db034`:** `drugref status` — the first command an
operator runs after pulling — exited 1 with a raw psycopg traceback *after two blocks of real answers*, which is
verbatim the failure mode `cli.py:249`'s own comment exists to prevent. Now exit **2** with one sentence.

**The other four, and what each cost:**

- **`drugref status` printed `None` for every class-grain orphan.** `subject_moiety` is NULL on that arm by
  construction, and the renderer read only that column — so issue #90's detector reported *that* a judgement was
  orphaned without saying *which*, and two class rules sharing an object and an axis rendered identically. Fixed
  with an `UnresolvedTarget.subject` property (no arm labels, so a fourth arm needs no change) rather than a
  branch at the call site. **The dataclass docstring's "nothing here had to change except a field" is what made
  it invisible** — the open-to-extension argument is sound for the *discriminator* and was silently extended to
  cover a structural XOR that does not need arm labels to state.
- **The class-grain block had no guard at all**, justified by "any database this code can reach has migrated to
  at least db/029". True premise, invalid conclusion — db/029 does not imply db/035. Fixing only the block above
  would have moved the traceback thirty lines down.
- **Frozen signing field-list ORDER was unpinned.** `test_signing_payload_coverage` compares **sets**, and the
  committed vectors carry their `fields` as literals that never consult `signing.FIELD_LISTS`. **Measured:
  permuting `CURATED_CLASS_INTERACTION_V1`'s first two entries — which changes the signed bytes for every
  class-grain row — passed all 249 signing/release/class-grain tests.** Publish and verify read the same tuple,
  so a permutation is self-consistent and breaks only signatures recorded BEFORE it, in production, as
  `BAD_SIGNATURE`, with nothing naming the cause. Now pinned against the fixture, and
  `curated_class_interaction/v1` finally has a vector case (it was the only registered context without one).
- **Three docstrings still said `CheckViolation` after the five CHECKs became foreign keys** — including
  `cli_curate.py`'s module docstring, which pointed the reader at the handler docstring *this same PR* had
  updated to say the opposite. One file, two homes, disagreeing.

**`cli.py` breached CLAUDE.md rule 4 (500 lines) and the fix was a module, not a diet.** Shaving comments to fit
sets rule 4 against rule 3, and the comments that would go are the ones recording why the guard exists — the
knowledge this round already lost once. The read moved to `curation.class_grain_counts` (curated SQL belongs
where the `pg_rewrite` sweep can see it) and the voice to **`cli_status.py`**, the sixth `cli_*` module.
`cli.py` 483 → **450**. `cli_status` joins `test_the_cli_embeds_no_sql_against_a_curated_table` from its first
commit rather than from the round somebody notices, which is how `cli_curate` came to be added late.

**`db/036` is three `COMMENT ON` statements and no schema change.** Catalog comments ship in `pg_description` —
they are what `\d+` prints, so they are the authoritative answer for a DBA on a running node with no checkout,
and `db/035` is applied and immutable. Corrected: the frozen `gap_key` was documented as `AXIS:` when the value
is **`CI_AXIS:`** (anyone reconstructing `question_uuid = uuid5(gap_kind, gap_key)` from `\d+` computed a
different, silently unmatched uuid); `max_pair_count` claimed to be "exact about ZERO" when the read path's
self-pair exclusion makes a one-member self-pair rule read **1** and reach **0**; and
`curated_grain_disagreement` enumerated one deliberate omission when it has two.

**Five issues filed rather than fixed** — [#108](https://github.com/cairn-ehr/drugref/issues/108) make
`max_pair_count` exact (it currently both queues a pointless curator question *and* hides the dead rule from the
operator — #36's mistake and db/035's own target failure, at once) ·
[#109](https://github.com/cairn-ehr/drugref/issues/109) `curated_grain_disagreement` misses mirror-oriented rule
pairs (these rows are directional per db/006; the read path stays safe, the worklist under-reports) ·
[#110](https://github.com/cairn-ehr/drugref/issues/110) the #97 precedence is stated in prose and applied by no
view — nothing in `src/` reads `severity_rank`, so no test can regress it — plus `ORDER BY ... ASC` sorting
NULLs last, which inverts the harm direction in the one path documented as safe ·
[#111](https://github.com/cairn-ehr/drugref/issues/111) the block's bare zeros carry no denominator, so
"healthy" and "a rebuild emptied the tier" render identically ·
[#112](https://github.com/cairn-ehr/drugref/issues/112) measure the disagreement self-join before class-grain
content ships (db/024's 59 s → 465 ms precedent: "a synthetic probe looked fine because its fixture had no
edges").

## The low-hanging-debt round (2026-08-14) — `db/037`, `curated_read.py`, suite 1465 → 1511

A sweep of the **51** open issues for work that is small, self-contained and needs no design decision. **Seven
FIXED** (79, 87, 100, 108, 109, 110, 111) **plus 19 and 106 answered by measurement** — nine touched. (This
read "Eight cleared" over a list of nine numbers, in this file, ROADMAP, HANDOVER and the commit message at
once. A count beside the list it counts is the same defect as one quantity in two places, and it disagreed in
four homes; the fix is to state the seven and the two and never a total.) One migration, no behaviour change on
any published count. Everything deliberately NOT taken is listed at the end, with why —
the point of a debt round is as much to record what is not fruit as to eat what is.

**`db/037` is three view corrections and one new view**, all in the class grain, all provably content-neutral
today: `class_pair_contraindication` is **EMPTY on every database in existence** (#94 withheld the seven
class×class ONC entries pending literature research and nothing else writes the grain). **Every published count
is byte-identical between `drugref_db036` and `drugref_db037`** — `ddi_candidate_pair` 21,877 ·
`curated_ddi_pair` 255 · `open_question` 21,848 · `gap_unpopulated_contraindication` 13 ·
`condition_contraindication_expanded` 192,161 · `class_expansion_policy` 14 · `loaded_release` 6.

- **#108 — `max_pair_count` is now exact.** It was `subject_effective × object_effective`, while
  `curated_ddi_pair` additionally requires `subject_moiety <> partner_moiety`. It now subtracts a published
  `shared_effective_member_count`, so reach is `|S|·|O| − |S ∩ O|`. **The self-pair rule is the special case,
  not the fix**: two different classes sharing members overstate identically, and MED-RT files one drug under
  many classes, so overlap is ordinary. **Both detectors inverted on the same wrong number** — the worklist
  `HAVING max(max_pair_count) > 0` *admitted* a one-member self-pair rule and minted it an immortal
  `question_uuid` asking about "up to 1 drug pair(s)" (#36's mistake), while `status`'s
  `WHERE max_pair_count = 0` *omitted* it (db/035's own target failure). Both are filters over the one column,
  which is why correcting the column corrects both.
- **#109 — `curated_grain_disagreement` is orientation-blind.** These rows are DIRECTIONAL (db/006), the two
  candidate tiers come from different upstreams, so a moiety rule on `(a,b)` and a class rule on `(b,a)` were
  one clinical pair the self-join never brought together. **`LEAST`/`GREATEST`, not an `OR` of two arm pairs** —
  both express the match, only one is an equi-join, and this view is read unfiltered by `drugref status` on
  every invocation.
- **#110 — the precedence is a view, `curated_ddi_pair_effective`.** db/035 shipped the *ordinal* and left the
  *ordering rule* in prose; nothing in `src/` read `severity_rank`, so no test could regress it. **`NULLS
  FIRST`, not Postgres's default**: rank 1 is most severe, so `ASC` sorted an unrankable severity *below*
  `minor` and a `LIMIT 1` client never saw it — under-warning, in the one path db/035 documented as safe.
  Unreachable while the `severity` FK stands, so it is pinned by **dropping that one FK inside the rolled-back
  fixture transaction**.

**THE NEW VIEW IS NOT A NO-OP TODAY, and that was the round's surprise.** With zero class-grain content it
still collapses **255 rows to 213** — 42 doubled pairs, **all 42 explained by `candidate_source` alone**, zero
differing in severity, `via_class` or `rule_grain`. A rule both MED-RT and ONCHIGH assert is one grade and two
rows, and every client reading `curated_ddi_pair` was seeing that duplication.

**Traps a future change can still break.**
- **`curated_read.py` is the SEVENTH module split out to hold rule 4**, and the reason it is not in
  `curation.py` (485 lines) is arithmetic, not taste. It must not move into `interactions.py` either — that
  module's first sentence is "The ONLY module that writes the interaction tables".
- **The `ORDER BY` in `curated_read.effective_grades_for` is NOT the precedence rule**, though both mention
  `severity_rank`. The precedence chooses between two rows describing ONE pair and lives in the view; the
  function orders DIFFERENT pairs so the most concerning partner reads first. Re-implementing the *tie-break*
  in Python would put db/035's own defect back — one rule, two homes.
- **`class_pair_rule_reach` now carries member ARRAYS as well as counts**, because an intersection size cannot
  be recovered from two cardinalities. The counts are still `count(DISTINCT …)` and NOT `cardinality(members)`:
  deriving one from the other puts one quantity in two shapes. `ci_rule_partner_reach` (db/018) already
  aggregates the same array for the moiety grain's one-sided version of the same exclusion.
- **The LEAST/GREATEST join is UNMEASURED in the sense that matters.** Interleaved against `drugref_db036`
  (#81's method, 6 alternating runs each) it reads 2777.9 ms → 2750.2 ms on `SELECT count(*) FROM
  curated_grain_disagreement` and 2759.6 → 2735.2 on a filtered `curated_ddi_pair` — both −1%, i.e. noise —
  **but the class-grain half of `curated_ddi_pair` is empty, so the join had nothing to join.** Both probes are
  dominated by `ddi_candidate_pair`'s ~2.7 s unfiltered scan (issue 75), inherited whole. This is a fair A/B
  and it is NOT evidence about the new arm's cost: **issue 112 still owns that measurement**, and db/024's
  59 s → 465 ms precedent ("a synthetic probe looked fine because its fixture had no edges") is why.

**Two issues answered by measurement rather than code**, both recorded on the issue itself:

- **Issue 19 — the "41 vs 13" puzzle in HANDOVER resolves, and both numbers were right.** 41 of 739 is the
  TERMINOLOGY grain; at drugref's grain `class_contraindication` holds **643** rows (635 MED-RT + 8 ONCHIGH,
  which did not exist when 19 was filed): **590** with direct members, **15** only deeper, **38** with no member
  anywhere by raw membership. The gap view reports **13 classes / 39 rules**, one more of each, and the whole
  difference is **`Urease Inhibitors [MoA]`**, whose single subtree member IS the rule's own subject —
  `ci_rule_partner_reach` subtracts it (db/018's #31/#50 finding, *"clomiphene is its own rule's subject"*), so
  **the view is right and the raw count is wrong**. Authoritative figure: **39 dead rules across 13 classes.**
  Two of the issue's three asks already shipped; the third's stated justification ("right now it is invisible")
  is false, and it now carries a scoping question — `gap_unpopulated_contraindication` is source-blind while
  `MedrtSummary`'s other detector-backed field is source-scoped.
- **Issue 106 — 46 of 21,370 candidate pairs (0.22%) are reachable on two axes, and NONE of them is graded.**
  The prediction ("probably zero today") holds, and the 46 is the number it did not have: it bounds the
  `same_pair_different_axis` arm the issue proposes, so that widening is cheap when curation scales.

**What was examined and deliberately NOT taken**, so the next sweep does not re-derive it: **65** (the issue
says do not act until curation scales) · **30** (blocked — no PBS release on disk; `downloads/` holds UNII,
MED-RT, MeSH and GSRS only) · **112 / 105** (blocked on class-grain content existing) · **89** (splitting the
security-critical `signing.py`, now **605** lines against the filed 582, is a scheduled refactor, and
`release_verification.py` went 532 → **540** here — rule 3 documentation for the #87 change, so the figure on
that issue needs re-reading, not re-deriving) · **88** (a type checker is a real ongoing cost and a decision) ·
**6 / 25 / 5** (licence deeds need the owner's sign-off) · **82 / 104** (both change the operator surface; held
back deliberately) · **86** — **DECIDED BUT NOT BUILT: add `signed_by_unknown_key` as a fourth
`signature_status` value.** That is a published-vocabulary widening with spec and consumer consequences, so it
is a round of its own; the decision is recorded on the issue so the next round does not re-litigate it.

## The PR #113 review round (2026-08-15) — suite 1511 → 1516, no new migration

Six review agents over the debt round's own diff. **Three real defects, every one SILENT** — no test failed, no
count moved, nothing raised — and all three now mutation-verified: revert any one and a named test goes red.
Four further findings were filed rather than fixed (114–117). The pattern behind all three is the one this repo
keeps paying for: **a claim stated in prose beside code that does not enforce it.**

- **`GradedPair` was built by positional splat, and five of its nine fields were asserted nowhere.**
  `curated_read.py` spelled the nine column names twice — once as dataclass fields, once in the SELECT — and
  bound them with `GradedPair(*row)`. Seven of the nine are text or nullable text, so a transposition builds a
  WELL-TYPED WRONG record: **the review swapped `mechanism` and `management` and the entire suite stayed
  green**, while drugref handed clinical management advice to a client under the label "mechanism". Fixed the
  way `keys._COLUMNS` and `curation._UNRESOLVED_COLUMNS` each already fix it — ONE list generating the SELECT
  and binding by keyword — which makes the swap **unrepresentable rather than tested**: re-running that same
  mutation against the fixed module changes nothing observable, because both halves come from one list. What
  remains testable is a *wrong column name*, and that now fails loudly (`TypeError`, plus the new
  field-by-field test).
- **`curated_ddi_pair_effective`'s determinism tail closed the moiety grain and not the class grain.** A
  class-grain row is identified by `(via_subject_class, via_class, relationship)` — `curated_class_interaction`'s
  live-unique natural key — plus `candidate_source`; `via_subject_class` was missing. Two class rules over one
  pair (one drug filed under two subject classes, which db/037's own §1 argues is ORDINARY since MED-RT files
  one drug under many classes) then tied on **every** key: `member_class` identical (same object class),
  `reviewed_at` identical because it defaults to `now()`, the TRANSACTION timestamp, so one `drugref curate` run
  stamps every ruling alike. `DISTINCT ON` fell through to **heap order** — writing X1 first yielded X1's
  mechanism, X2 first yielded X2's. Which mechanism and management text a prescribing client read was decided
  by physical row position, flippable by a per-source rebuild, a `VACUUM FULL` or a dump/restore, and **silent**:
  severity is equal, so no detector fires and `curated_grain_disagreement` never sees it (both rows are
  `class_rule`).
- **The class-grain guard did not cover `db/037`.** `cli.py` states the standing rule — *"a migration widening a
  view a guarded block reads must widen the guard in the same commit"* — and db/037 is the case it was not
  written for: it corrects `class_pair_rule_reach`'s ARITHMETIC and appends a column nobody read, while every
  name `class_grain_counts` reads still resolved under db/035. So on a db/035-or-036 database the guard stayed
  quiet, nothing raised, and the block printed counts from the OLD, OVERSTATED `max_pair_count` — `dead`
  under-reporting exactly the self-pair-over-a-one-member-class rule db/037 exists to surface, across precisely
  the window the guard's own docstring invokes ("every deployment between pulling this code and running
  `drugref migrate`"). `_RULE_COUNT` now names `shared_effective_member_count`; it cannot move the count,
  because every input to this view's arithmetic is a function of the three natural-key columns alone.
  **Verified on the real reference databases: `drugref_db036` raises the operator sentence, `drugref_db037`
  prints.** The previous round recorded `status` exiting 0 on db036 as evidence the new denominator was safe —
  it was evidence the guard was blind, and HANDOVER now says so.

**TWO TESTS PASSED FOR THE WRONG REASON, which is worth more than the fixes.** Both were over-determined
fixtures, and both had docstrings claiming the opposite:

- `test_the_moiety_grain_breaks_a_tie` said *"a view that dropped it would return an arbitrary row here and
  flake rather than fail."* It did not flake — **deleting `(rule_grain = 'moiety_rule') DESC` from db/037
  outright left the whole suite green**, because the fixture sourced the moiety rule from `MED-RT` and the class
  rule from `ONCHIGH`, and `'MED-RT' < 'ONCHIGH'` meant `candidate_source` — a LATER key — already picked the
  expected row. Fixed by sourcing both grains from `MED-RT` (legal: `class_pair_contraindication_source` admits
  exactly those two), which ties `candidate_source` and hands the decision to `via_subject_class`, non-NULL on
  the class row and NULL on the moiety row, so it favours the CLASS row and only the grain key can produce the
  expected answer.
- The caller's own `NULLS FIRST` was pinned by nothing: the unrankable-severity test drives the VIEW, never
  `effective_grades_for`, so removing `NULLS FIRST` from `curated_read`'s ORDER BY left fifteen tests green.
  The view would have sorted the unrankable row first and the Python caller re-buried it one layer up — the
  whole harm-direction argument defeated on the last hop.

**FACTUAL CORRECTIONS — comment rot shipped on day one, in a round whose own subject was comment rot.** The
`keys.py` line saying `_record` "keeps unpacking positionally" (it binds by keyword, and the block 50 lines
above argues at length that positional binding is the failure mode keyword binding removes) · **"415 lines over
88" was stale when committed** — 490 at that commit, **497** now — and it appeared in five places across two
files plus this one, in a passage arguing that a count which grows while the ceiling stands still is the
signature of an unstated convention. The ceiling is still **119** · `tests/ruff.toml`'s documented re-measure
command **produced no output at all**, because ruff resolves that very file's `line-length = 120` for a path
under `tests/`; it needs `--config 'line-length=88'`, which is the replacement for the
`--config 'lint.per-file-ignores={}'` the previous version passed for the same reason · db/037 said "~9" on
line 63 and "seven" on line 10 (issue 117) · six further errors in the new test files' own comments, including
a 378-line migration described as 200 lines, and a "cheap on this data, ~640 rules" that quoted the REFERENCE
database's figure for a test schema holding **zero** rows — which makes that test vacuous for both parametrised
views, now said plainly rather than papered over.

**Standing lesson, and it is not "write fewer comments".** Every one of these files documents its reasoning at
length and that is what let the review find the defects at all — a claim you can check is worth more than
silence. The lesson is narrower: **a comment that states a MEASUREMENT must name how to re-take it, and the
recipe must be run before it ships.** `tests/ruff.toml` now carries a working recipe, an instruction to APPEND
to the series rather than overwrite it, and an explicit warning that the override is load-bearing and its
absence has no symptom.

## The db/038 round (2026-08-15) — closing PR #113's four filed issues, suite 1516 → 1540

The four issues PR #113's review **filed rather than fixed** (114–117), taken as one round because three of
them touch the same read path and the fourth needed the migration the first one was already writing. One new
migration, **one new command**, one rename, one correction to a merged migration's prose. Suite **1516 → 1540**,
`ruff` clean, docs build clean.

### ⇒ ISSUE 114 WAS NOT OPEN WHEN THIS ROUND STARTED, AND IT SHOULD HAVE BEEN

`ed1ab5e`'s commit body reads *"Filed rather than fixed: #114 effective_grades_for has no consumer in src/,"* —
and GitHub's linker matched the literal substring **`fixed: #114`** as a closing keyword and closed it. The
sentence *declaring the issue unfixed* is what closed it. #115, #116 and #117 sit in the same sentence and
survived, because no keyword is adjacent to them: that asymmetry is what pins the mechanism rather than
inferring it.

**Fifth occurrence** (#31, #35, #40, #61, #114) — and the second using the **identical sentence template**, after
`92baaea` did it to #61 and this file documented the trap in full, named token adjacency as the mechanism, and
warned that "a colon in between does not save you". A prose rule that has failed five times is not a rule; see
the standing rule added above, and **[#118](https://github.com/cairn-ehr/drugref/issues/118)** for the
mechanical guard, which is the only intervention not yet tried.

### Issue 116 — `NULLS FIRST` fixed the sort and left the payload

**What db/037 got right and this round keeps:** an unrankable severity must sort ABOVE `contraindicated`, not
below `minor`, because under-warning is the harm direction. **What it left open:** inside a `DISTINCT ON` the
sort key does not merely SHOW that row, it makes it **WIN**, and the rankable competitor is discarded from the
view outright. The client then gets `severity_rank = NULL` with **no second row behind it**, and every form of
the threshold `GradedPair`'s own docstring tells clients to write drops it — SQL `<= 2` is UNKNOWN, Python
`<= 2` raises, `x and x <= 2` is silently False.

| competitor | NULLS LAST (pre-db/037) | NULLS FIRST (db/037) |
|---|---|---|
| `minor` (rank 4) | client sees `minor` | client sees the unrankable word — improved |
| `contraindicated` (rank 1) | client sees rank 1 | **threshold drops the pair entirely** |

**So db/037 traded one under-warning for a worse one, visible only in the case its test did not drive** — the
existing test grades the competitor `minor`, which is precisely why the suite could not see it.

**`db/038` § 1: a second column, never a changed one.** `effective_rank = COALESCE(severity_rank, 0)::smallint`
appended to both halves of `curated_ddi_pair` (CREATE OR REPLACE admits new columns only at the END, and
`curated_ddi_pair_effective` depends on it), and the effective view's ORDER BY now reads `effective_rank`
instead of `severity_rank NULLS FIRST` — the **same order**, 0 preceding 1 exactly as NULLS FIRST placed the
NULL, but ONE spelling of one rule. Two spellings is how this issue happened.

**COALESCEing `severity_rank` itself would have been the worse bug**, and the tests are written so it cannot
pass: a broken row would become indistinguishable from a genuine rank 0, destroying the only evidence the
schema is wrong. `severity_rank` stays NULLABLE; **both columns are asserted together** in every new test.

**`db/038` § 2: the fault reaches an operator.** A mitigation that hides its own trigger is how issues 74 and 76
happened, so `curated_unrankable_severity` (both curated tables, live rulings only) is read by
`curated_read.unrankable_severities` and printed as `drugref status`'s **sixth** block. **It counts RULES, not
expanded pairs** — one class rule expands to ~2,263 (db/035) and an operator fixes the rule — which also keeps
`ddi_candidate_pair`'s ~2.7 s scan (#75) out of every status run.

**`AND applies` IS LOAD-BEARING HERE FOR A NON-OBVIOUS REASON, and it is the one case reachable on a HEALTHY
database.** db/029's completeness CHECK is `(NOT applies AND severity IS NULL …)`, so a **withdrawn** ruling
carries no severity — and `NULL = anything` is NULL, so the LEFT JOIN finds nothing and `sk.severity IS NULL`
is TRUE. Without the filter the view would report **every withdrawn ruling in the overlay** as a schema fault,
a false positive growing with every correction a curator ever makes. Mutation-verified: delete that one
predicate and the withdrawn ruling appears.

### Issue 115 — a denominator that denominated three numbers, one of which was not in its population

`total` → **`rules_total`**, renamed and not aliased (a test asserts the old name is gone). `ungraded` and `dead`
are filters over the same `class_pair_rule_reach` tier, so they are bounded by it; **`disagreements` counts
PAIRS** — rows in `curated_grain_disagreement`, whose grain is the rule pair over the two-grain expansion — and
never was. Once class-grain content ships, `ClassGrainCounts(rules_total=7, …, disagreements=2263)` is the
EXPECTED shape, so `{disagreements} of {total}` would have been wrong by two orders of magnitude on the
operator surface. The three invariants are now **asserted**, not just described, and the disjointness of
`ungraded`/`dead` (via `HAVING max(max_pair_count) > 0`, #36) is stated so nobody reconstructs it as overlapping.

**COST, RECORDED RATHER THAN HIDDEN: `curation.py` went 500 → 523 lines** (520 in the db/038 round; the
PR #119 review added three), past rule 4. That is rule 3 against
rule 4, which this repo has ruled on twice (`cli_status.py`'s docstring): **move code, never shave comments.**
Measured onto [#89](https://github.com/cairn-ehr/drugref/issues/89) with the natural seam named
(`ClassGrainCounts` + `class_grain_counts` + `_RULE_COUNT`, ~90 lines, one consumer) rather than split inside a
correctness diff — db/030's own precedent for exactly this.

### Issue 117 — BOTH options, because they cover different halves

The `9` traces to issue 96's prose, quoted faithfully by db/035, never reconciled against #94's **seven**.
`db/038` § 3 re-issues the `COMMENT ON` with seven (db/027's precedent). db/035's **plain `--` comments cannot
be corrected by anything** — stripped at load, and the file is merged and immutable — so that half is the
standing rule added above.

**⚠ THE FIRST DRAFT OF THIS SECTION SHIPPED A REGRESSION, and the sentence above is how.** It read *"only the
figure changes, so a diff shows one word"* — measured against **db/035**. See § "The PR #119 review round"
below: the text being replaced was **db/036's**, and rebuilding from the wrong ancestor reverted db/036 § 1's
correction of the frozen `gap_key` spelling. The verification quoted here — `%nine ingested%` / `%seven
ingested%` — is exactly the shape of check that could not see it.

### Issue 114 — the consumer, and what building it actually taught

`drugref interactions <moiety> [--with <other>]`, in a new `cli_interactions.py` (the pattern `cli_policy`,
`cli_signing`, `cli_curate` and three more already follow; cli.py was 461 lines). No SQL in the handler.

**The issue's judgement that option 1 "would actually test the design" was correct.** The two questions a user
asks are not the same shape, and only building the command makes that concrete:

- `interactions X` is ONE lookup and can only ever return rules stated with X as SUBJECT — a genuinely partial
  answer, so it prints a note saying so. An unqualified empty list here reads as "these do not interact", which
  **drugref has not asserted**.
- `interactions X --with Y` does **TWO** lookups, which is exactly what `effective_grades_for`'s docstring
  prescribes — done in the caller, visibly, rather than folded into the library where it would hide that two
  lookups happened.

**Measured on the real ONC atazanavir/PPI entry**: `interactions <ppi> --with <atazanavir>` finds the rule
though it is stated the other way round, while `interactions <ppi>` alone correctly finds nothing and says why.
That asymmetry was a docstring paragraph until there was a command to feel it. Neither form ever says "safe" —
an absent rule is not evidence of absence — and a test screens the output for the specific verdicts drugref
must not make.

### An over-determined pin, found by mutation while changing what it pins

`test_the_callers_own_order_by_puts_an_unrankable_severity_first` — added by PR #113's review — put the
unrankable partner on the **smaller** uuid (`TESTUNIIG4` → `505e7055…`, `TESTUNIIG5` → `f06c401d…`), so
`partner_moiety` alone produced the expected order and **deleting the rank key from the caller's `ORDER BY` left
it green**. The severities are swapped now, so only the rank can produce the answer; the same mutation makes it
red. This is the third time `_a_pair_graded_by_both_grains`'s own lesson has applied — **a test whose expected
result is over-determined cannot fail** — and the only reason it was caught is that the round mutated a pin it
was editing rather than trusting it.

### Measured on `drugref_db038` (from `TEMPLATE drugref_db037` + `drugref migrate`, the SIXTH round running)

**Every published count byte-identical to `drugref_db037`** — `ddi_candidate_pair` 21,877 · `curated_ddi_pair`
255 · `curated_ddi_pair_effective` 213 · `open_question` 21,848 · `gap_unpopulated_contraindication` 13 ·
`condition_contraindication_expanded` 192,161 · `class_expansion_policy` 14 · `loaded_release` 6.

**And the new surfaces read correctly on real data**, which is what makes the migration content-neutral rather
than merely believed so: `effective_rank` differs from `severity_rank` in **0** of 255 rows and is NULL in **0**
(the COALESCE never fires on a healthy database, exactly as intended), and `curated_unrankable_severity` is
**empty**. `drugref status` prints `unrankable severities: none` as its sixth block.

**NO TIMING WAS TAKEN, and that is deliberate rather than an omission.** § 1 adds one `COALESCE` over a column
already selected and swaps one ORDER BY key for an equivalent one; § 2's view is a new read nothing else
depends on. [#112](https://github.com/cairn-ehr/drugref/issues/112) still owns the class-grain measurement, and
its precondition is unchanged — `class_pair_contraindication` is empty on every database in existence, so a
probe here would measure a join with nothing to join, which is precisely the mistake db/024's 59 s → 465 ms
precedent records.

## The PR #119 review round (2026-08-15) — suite 1540 → 1564, `db/038` edited in place

Five review agents plus hand verification against the live catalog. `db/038` was still unmerged, so every SQL
fix went into the file itself and `drugref_db038` was rebuilt — the ledger binds a *database*, not the repo.

### ⇒ THE HEADLINE: a `COMMENT ON` re-issue rebuilt from the wrong ancestor

**`COMMENT ON` OVERWRITES; IT DOES NOT MERGE.** Three migrations state a comment over
`gap_uncurated_class_interaction_rule` — db/035 § 6, db/036 § 1, db/038 § 3 — so the text a re-issue replaces is
**whichever ran last**. db/038 § 3 rebuilt from db/035 and therefore silently reverted db/036 § 1, restoring the
wrong `AXIS:` gap_key spelling and deleting the parenthetical that recorded the correction.

**WHY THAT MATTERS MORE THAN THE FIGURE IT CAME IN TO FIX.** `question_uuid = uuid5(gap_kind, gap_key)`, the key
is frozen and externally citable, and the real value is `CI_AXIS:` (`questions.py` emits `'/CI_AXIS:' ||
relationship` twice; four tests pin it literally). A consumer reconstructing the uuid from `\d+` on a running
node computes one that matches nothing **and gets no error** — db/036's own "hardest kind of wrong answer to
notice".

**⇒ THE TRANSFERABLE RULE, and it is new: A RE-ISSUED `COMMENT ON` MUST BE DIFFED *WHOLE* AGAINST THE *LIVE*
CATALOG TEXT, never against the migration file you happen to be reading.** db/038's verification grepped
`%nine ingested%` / `%seven ingested%` — scoped to the word being *changed*, and therefore structurally
incapable of seeing what else moved in the same overwrite. A check that only looks for what you set cannot
report what you dropped. `tests/test_class_grain_comment.py` now pins both halves of this comment and the
`curated_ddi_pair` precedence sentence, each with a guard test driving the text that actually shipped.

**A SECOND STALE CATALOG COMMENT, same theme.** `CREATE OR REPLACE VIEW` **preserves** comments, so db/037's
`COMMENT ON VIEW drugref.curated_ddi_pair` survived db/038 untouched — still prescribing `ORDER BY severity_rank
NULLS FIRST`, the column § 1 exists to stop clients thresholding on. That is the first thing `\d+` prints, so
the two column comments were corrected while the most-read statement of the rule still pointed at the wrong
column. Re-issued in § 1.

### Six mutations that survived the whole suite, and now do not

Verified by mutating and re-running, not by inspection:

| mutation | was | now |
|---|---|---|
| delete the **moiety arm** of `curated_unrankable_severity` | green | 3 tests fail |
| drop `AND c.applies` (moiety arm) | green | fails |
| drop `superseded_by IS NULL` (either arm) | green | fails |
| revert the **moiety half's** `COALESCE(severity_rank, 0)` | green | 2 tests fail |
| detector predicate `sk.severity_rank IS NULL` → `sk.severity IS NULL` | green | fails |
| delete the gap view's `HAVING max(max_pair_count) > 0` | green | fails |

**THE MOIETY ARM WAS THE PRODUCTION-DOMINANT ONE.** Every unrankable test in the db/038 round dropped the
*class* constraint and graded the *class* side; all 255 curated pairs on the reference database are
moiety-grain. The tell was visible in the round's own code: `test_a_withdrawn_ruling_is_not_an_unrankable_one`'s
in-test replacement view defines **only** the class arm and still passed.

**THE `COALESCE` PIN IS NOW GRAIN-AGNOSTIC** — `count(*) FROM curated_ddi_pair WHERE effective_rank IS NULL`
must be 0, with both grains graded `unrankable` so the COALESCE actually fires. A per-grain test would have to
be remembered again for a third half; the invariant kills the mutation on any half added later.

### The detector was keyed on the cause it imagined, not the condition that harms

`AND sk.severity IS NULL` tests whether the **join missed**. What does the harm is a **NULL rank** — that is
what `COALESCE` swallows, what wins the `DISTINCT ON`, and what discards the competing grade. The two coincide
only while `severity_kind.severity_rank` is `NOT NULL`. Drop that — squarely inside the fault family the view's
own COMMENT claims to cover, *"a dropped constraint"* — and you get **full harm, zero detection, and `drugref
status` printing an affirmative `none`** over a live ambiguity. Now `sk.severity_rank IS NULL`, which **strictly
widens** (a join miss makes every `sk` column NULL, rank included). § 1 wrote the ordering rule in one place for
exactly this reason and § 2 then spelled the same rule a second way.

### `rank 0` was a promise, not a rule — now a CHECK

db/038 argued the sentinel was safe because *"severity_kind's ranks start at 1"*. True, and unenforced: db/035
declared `severity_rank smallint NOT NULL UNIQUE` with no lower bound. A later migration adding a level **above**
contraindicated at rank 0 would make a real grade indistinguishable from the fault — **silently**, because
`curated_unrankable_severity` would stay empty (such a row *is* in `severity_kind`). Closed by
`CHECK (severity_rank >= 1)`, with a `COMMENT ON CONSTRAINT` saying why rank 0 is reserved.

### `severity_rank` had no Python reader at all

The whole argument for keeping it NULLABLE is that it is *"the only evidence the schema is broken"* — and
`grep -rn severity_rank src/drugref/*.py` returned docstrings, the field declaration and the column list, **and
no reader**. `cli_interactions.py` printed `effective_rank` alone, so a schema fault rendered as a bland
`rank 0` — and in every other numbering a reader has met, 0 means *least*. Here it means drugref cannot rank
this **and this row discarded a real grade for the same pair**. The line now banners `** UNRANKABLE ... **` and
names `drugref status`. Deferring it to the operator put the warning in front of everyone except the person
being under-warned.

### Two more over-determined pins (fourth and fifth occurrences)

`ClassGrainCounts`' disjointness assertion reduced to `1 + 0 <= 1`: its fixture built one rule that reached
pairs, so `dead` was always 0 and deleting the `HAVING` guard it claimed to pin left it green. Now builds a
genuinely dead rule via `_an_ungraded_class_rule(object_axis=...)` — which existed for this and said so in its
own docstring. And `test_the_command_embeds_no_sql`'s first assertion was
`"SELECT" not in source.upper() or "curated_read" in source` — the right disjunct is **always** true, because
`curated_read.effective_grades_for` is in that function's source. It could not fail whatever SQL the module
embedded.

**AND `cli_interactions` WAS MISSING FROM THE PROJECT-WIDE GUARD** (`test_curation_orphans.py`) whose own
comment states the discipline — *"covered from its first commit rather than from the round somebody notices,
which is how cli_curate came to be added late"*. Third time that paragraph has described a rule the list then
failed to follow. Added; the local substitute is now narrowed to the one thing the general guard does not check
(naming the view at all) and parses with `ast` rather than grepping, because this module's *docstring*
legitimately names `curated_ddi_pair_effective` while explaining why it must not query it.

### `GradedPair` now enforces its own identity

`effective_rank = COALESCE(severity_rank, 0)` was documented and separately assignable — a second place one
value can be written, which is the same drift db/038 diagnoses in db/037, one layer up. The harmful direction is
silent: `severity_rank=1, effective_rank=4` drops out of every `<= 2`. A `__post_init__` now raises rather than
repairs (repairing would hide a view regression). **The `UnresolvedTarget` precedent for declining one does not
transfer** — that check would restate the view's UNION *arm labels*, which a later migration legitimately
extended; `COALESCE(x, 0)` is a closed identity with no arms to grow. Stated in the docstring so the next reader
does not apply the precedent by analogy.

### Filed rather than fixed, because each needs a design call

[#120](https://github.com/cairn-ehr/drugref/issues/120) an unknown `moiety_uuid` renders identically to an
ungraded drug — **the one with a harm direction**; needs a registry-existence reader and `curated_read.py` is
scoped to the overlay, `classes.py` declares itself a writer, so placement is the open question ·
[#121](https://github.com/cairn-ehr/drugref/issues/121) an orphaned curated grade reads as "no curated grade"
(pre-existing; `curated_target_unresolved` already detects it *for the operator*) ·
[#122](https://github.com/cairn-ehr/drugref/issues/122) all four `UndefinedTable` guards assert one cause as
fact, and `cli.main` prints only the outer message so `__cause__` reaches nobody — worst case is
self-referential, since a lost `severity_kind` is one of the faults this very view reports ·
[#123](https://github.com/cairn-ehr/drugref/issues/123) the detector sweeps 2 of the 5 tables carrying a
`severity_kind` FK; the status line is now labelled `(DDI grain)` so a bounded check stops reading as an
all-clear.

### Re-verified after the edits

`drugref_db038` rebuilt from `TEMPLATE drugref_db037` + `drugref migrate`. **Every published count still
byte-identical** (figures unchanged from the section above). `effective_rank` differs from `severity_rank` in 0
of 255 rows and is NULL in 0; `curated_unrankable_severity` empty; the gap comment carries `CI_AXIS:` **and**
`seven ingested` **and** db/036's parenthetical; `curated_ddi_pair`'s comment prescribes `effective_rank` and no
longer prescribes `severity_rank`; `CHECK (severity_rank >= 1)` present. Suite **1564 passed**, `ruff` clean.

## The guard round (2026-08-15) — issues 118, 120, 122; suite 1564 → 1598, NO migration

**The first round since 5c.1 that touches no `db/*.sql` at all.** `db/038` merged with PR
[#119](https://github.com/cairn-ehr/drugref/pull/119) (`20c4701`) and is therefore **FROZEN**; every defect
closed here was fixable in Python, which is why they were separable from a schema round in the first place.
Three issues, three new modules (`commit_lint.py` 175, `registry_read.py` 64, `migration_guard.py` 144), one
shipped git hook. **Ten mutations were run against the new branches and all ten fail** — the list is at the end
of this section, because "an over-determined test cannot fail" has now cost this project five rounds.

### ⇒ THE HEADLINE: THE COMMIT GUARD FOUND A SIXTH OCCURRENCE ON ITS FIRST RUN, AND IT HAD GONE UNCOUNTED

Issue 118 was filed against **five** commits that closed an issue nobody meant to close — #31, #35, #40, #61,
#114. Running the finished check over the **whole history** flagged 14, and the sixth issue was **#108**:

> **CORRECTED BY THIS ROUND'S OWN REVIEW.** The split was first recorded here as *"6 accidental and 8
> deliberate"*. It is **10 accidental and 4 deliberate**: four of the fourteen re-closed an
> **already-known** issue by QUOTING the offending sentence while documenting the rule — `e3d8322`,
> `8709d98`, `180d613` (all `fixed: #61`) and `5353bbb` (`fixed: #114`). **Writing about this bug re-arms
> it**, which is the strongest single argument for the guard and was the part the first count filed under
> "deliberate". Six ISSUES, ten COMMITS. A commit count is also why `_REPORT` can say #114 was closed
> "twice" while the issue list says six — both are true, of different things.

```
293758c  Filed rather than fixed: #108 (make max_pair_count exact), #109 (mirror-oriented rule
         pairs), #110 (ship the precedence as a view), #111 (...), #112 (...).
```

`gh api repos/cairn-ehr/drugref/issues/108/timeline` names it exactly: **`closed at 2026-08-14T12:16:38Z by
commit 293758c`**, while #109–#112 in the same sentence stayed open. Same sentence as `ed1ab5e`, one round
EARLIER, and **every document in this repo said five** — this file, ROADMAP, HANDOVER and issue 118 itself.

**THE LESSON IS NOT THE ARITHMETIC, IT IS WHY THE ARITHMETIC WAS WRONG.** This failure is silent *by
construction*: nothing announces it, so the only way to count it is to go looking, and every count taken by
hand has undercounted. **The count was the evidence the prose rule was failing**, so an undercount understated
the case for fixing it — for a whole round. #108 was in fact fixed later by db/037, so unlike #114 no work was
lost; **that is luck, not a mitigation**, and it is exactly the coincidence that let the occurrence pass
unnoticed.

### What ships for 118, and the one thing that deliberately does NOT

`.githooks/commit-msg` (a short `sh` dispatch — **the line count is deliberately not written down here**; the
file said "three lines" in its own header while being six, and this section said 24, which is the two-homes
failure this repo keeps paying for) + `drugref.commit_lint` (pure), installed with
**`git config core.hooksPath .githooks`** — recorded in § "How to run / test", and **already installed in this
checkout**, so the round's own commit was the guard's first live exercise.

- **The predicate is TOKEN ADJACENCY, matched per LINE.** `[^\S\n]*` for the gap rather than `\s*`, because
  `\s` spans the newline: a body ending "... and this is fixed." above a line opening "#115 is next" would
  otherwise be rejected for a pairing GitHub never makes.
- **Both spellings.** Bare `#N` is the commoner form in this repo's prose (about 3:1 across `docs/` — an
  earlier version of this bullet claimed "every issue in `docs/`" uses the URL form, which is backwards), but
  the markdown link `[#120](https://github.com/cairn-ehr/...)` is used throughout ROADMAP and PROJECT-NOTES,
  so a body pasted from either carries URLs and GitHub closes on those too. Missing either is a false
  negative, which is the direction this module refuses.
- **⇒ ONLY GIT'S OWN TRAILING BLOCK IS STRIPPED, AND THE FIRST VERSION GOT THIS BADLY WRONG.** It dropped
  *every* line starting with `#`, justified by "git strips them before the commit exists". **That is true of
  an EDITOR commit and false for `git commit -m` and `-F`**, where cleanup is `whitespace`, not `strip`, and
  `#` lines are stored verbatim — measured with real git, `-m $'feat: x\n\n## Done\n# fixes #999'` stores the
  line and GitHub closes 999, with the guard silent. **A silent close produced by the guard's own blind
  spot.** Not a contrived shape either: this project pastes bodies out of HANDOVER and ROADMAP markdown, whose
  headings begin with `#`, and the history already holds sixteen such body lines. It now truncates at git's
  own block marker (`# Please enter…`, `# On branch `, or the scissors line) and **scans everything else** —
  so a branch named `fix/closes-118` is still safe, and `core.commentChar` set to something else now makes
  `#` lines scannable rather than invisible, which is the correct direction.
- **The escape is `--no-verify` alone. The `Closes-intentionally:` trailer the issue proposed is NOT shipped,
  and the reason is worth keeping**: that exact spelling would not close anything on GitHub — `-intentionally:`
  sits between keyword and reference, so the linker never matches — so it would have been a **second vocabulary
  whose name states the opposite of its effect**, which is the defect this repo keeps paying for.
- **The shell wrapper is exercised by a subprocess test, not just the Python.** Three pieces (pure function,
  `__main__`, `sh` script) and the first two being green says nothing about whether the third finds an
  interpreter or propagates an exit code. **A hook that exits 0 on every message is indistinguishable from no
  hook**, which is issues 74/66/76's "a gate that exists and never fires" in a new place.
- **Scope limit, and it is now its own issue**: GitHub also parses **PR descriptions**, which no commit hook
  can see — [#124](https://github.com/cairn-ehr/drugref/issues/124). It needs no new logic, only a second
  caller of `closing_references` and a workflow, plus an opt-out that is not itself a string GitHub parses as
  a close. **Its count is UNKNOWN rather than zero: nothing has measured that surface.**

### Issue 120 — an absence about the OVERLAY, printed as an answer about a DRUG

`drugref interactions <uuid>` printed `no curated grade` both for a drug drugref knows and has not graded (the
ordinary case — the overlay is small on purpose) and for a uuid naming **nothing in the registry**, exit 0
either way; the pair form additionally asserted "drugref holds no curated grade for this pair in either
direction" about a pair that may not exist. **Reachable with no typo at all**: `--with` is documented as "a
second moiety_uuid" and a `class_uuid` parses identically.

**The fix is a read of the IDENTITY SPINE, in a module of its own** — `registry_read.known_moieties`, one
`= ANY(%s)` against `substance_moiety`. **Neither existing candidate was right, and the argument is the
boundary itself**: `curated_read.py` opens by scoping itself to "the curated overlay", and that scope is the
whole reason the view cannot answer this; `classes.py` declares itself "the ONLY module that writes the
classification tables". `cli_interactions.py` stays SQL-free.

- **Existence is checked BEFORE the self-pair branch**, and the ordering is pinned by a test. `interactions X
  --with X` where X names nothing satisfies both conditions, and "the two moieties are the same drug" is a
  confident claim about a drug that does not exist — the same shape as 122's guards.
- **Exit 2, like the self-pair** ("nothing was asked, so nothing was answered"), so a script gets a signal
  rather than a banner a human may scroll past — #82's objection applied here.
- **No grade block is printed at all** beside the banner: an empty block would restate the ambiguity.
- **The old test asserted the DEFECT as the contract** — `== 0` and `"no curated grade" in out` — so it was
  replaced, not extended. It is the fourth test in this project found pinning the wrong thing.

### Issue 122 — a guard may not assert a cause it has not confirmed

Four blocks answered every `UndefinedTable` with one cause stated as fact ("this database predates db/0NN. Run
`drugref migrate`"). 42P01 has more causes than a pending migration: a wrong `search_path`, a role without
USAGE, a manual repair, or a **base table** of the view being gone.

**⇒ AND THE WORST CASE IS SELF-REFERENTIAL.** "A restore that lost the vocabulary table" is one of the three
faults `curated_unrankable_severity` exists to REPORT. Drop `severity_kind` and the view goes with it → the
operator is told the database predates db/038 → **migrations are ledger-backed and db/038 is recorded applied,
so `drugref migrate` is a NO-OP** → status prints the same sentence again. **A closed loop, authored by the
detector whose purpose is diagnosing that exact fault.**

**⇒ PROBING THE RELATION ALONE DOES NOT CLOSE IT, which is the finding worth carrying forward.** In the CASCADE
case the view really *is* absent, so absence-alone still reads as "behind on migrations". **THE LEDGER IS THE
ONLY DISCRIMINATOR.** Two booleans, four states, one wording (`migration_guard.guard_message`, **pure** — no
connection, so all four are tested without constructing four broken schemas):

| relation | migration in ledger | what the operator is told |
|---|---|---|
| absent | not applied | the original sentence: predates db/0NN, run `drugref migrate` |
| **absent** | **applied** | **DROPPED, not pending — `drugref migrate` is a NO-OP and will print this again** |
| present | not applied | the `UndefinedColumn` shape: right relation, older columns — run `drugref migrate` |
| present | applied | **NOT a missing migration** — look at search_path, USAGE, a dropped base table |

- **Every branch carries `exc.diag.message_primary`.** `raise ... from exc` *looks* like it preserves the
  cause, and `cli.main` prints only `f"drugref: {exc}"` — `__cause__` is never rendered, so
  `relation "drugref.severity_kind" does not exist`, the one string that resolves this in five seconds,
  reached nobody.
- **`db.missing_relations` ROLLS BACK FIRST, and that is the point of the function.** `connect` uses psycopg's
  default `autocommit=False`, so the caught error has ABORTED the transaction; without the rollback the probe
  raises `InFailedSqlTransaction` **from inside the guard** and replaces a wrong-but-readable sentence with an
  unrelated traceback — strictly worse than the defect. Pinned by a test that aborts the transaction with the
  real error first.
- **`db.migration_applied` matches `NNN\_`, never a bare substring**, and the control has teeth: substring
  matching errs in the harmful direction — it reports a migration applied when it is not, so the guard tells an
  operator **not** to run the migration that would fix them.
- **A FIFTH GUARD, on the clinician path.** `curated_read.effective_grades_for` had none, unlike all four
  status readers: on a db/035–db/037 database the view exists without `effective_rank`, so `UndefinedColumn`
  escaped as a raw traceback — exactly what `cli_interactions.register()`'s own comment says the `uuid.UUID`
  typing exists to prevent, left out at the one command a clinician runs.
- **Each guarded view name now has ONE home**, exported by the module owning its read (`curated_read.
  EFFECTIVE_VIEW` / `UNRANKABLE_VIEW`, `curation.UNRESOLVED_VIEW` / `CLASS_GRAIN_VIEWS`,
  `signatures.BACKDATED_VIEW`). A guard carrying its own copy would survive a rename and then probe a relation
  that no longer exists, **reporting a healthy database's view permanently absent**. This is also what keeps
  `test_the_command_reads_the_view_only_through_curated_read` passing unweakened — that test caught the second
  spelling, and the right answer was to remove the copy, not to relax the test.

### The ten mutations, all of which fail

`db.missing_relations` without its rollback · `db.migration_applied` by bare substring · `guard_message`
ignoring the ledger · the DROPPED branch prescribing the no-op anyway · the "predates" branch dropping the
Postgres detail · `cli_interactions` skipping the existence check · the clinician guard dropping
`UndefinedColumn` · `commit_lint` without the adjacency constraint · `commit_lint` scanning git's comment
lines · `registry_read` reporting an absent moiety as known.

### Traps and standing notes

- **A CRUDE AUTOMATED RE-WRAP CORRUPTED PRE-EXISTING CODE, and `git checkout` was the fix.** Two scripted
  passes at reflowing over-long comment lines merged `@dataclass` field declarations into one line and split an
  f-string mid-literal — in `db.py` it damaged `referenced_vocabulary`, which this round never touched.
  `ruff check` caught all of it as `invalid-syntax`. **Reflow prose by hand, or restore from git and re-apply**;
  a regex that cannot tell a docstring paragraph from a field list will eventually meet a field list.
- **`_Conn` (tests/test_curation_orphans.py) grew two probe answers rather than being replaced by a fixture,
  and the reason is a real constraint**: the DROPPED state is *absent WHILE its migration is applied*, and
  producing it against the session-scoped migrated database means **committing** a DROP — breaking every test
  after it. Dropping a view inside the rolled-back `conn` fixture does not work either, because
  `missing_relations`' own rollback undoes it. The pieces are each tested where they can be tested honestly:
  the four messages purely, the two probes live, the wiring on a stub.
- **`questions.py` is 568 lines and was never on issue 89's list** — measured at `HEAD`, so pre-existing.
  `curation.py` moved 523 → 534 here (the two exported constants). Both recorded on
  [#89](https://github.com/cairn-ehr/drugref/issues/89).

## The guard round's own review (2026-08-16) — suite 1598 → 1644, still no migration

Six specialist agents over the round above, on its own diff. **Two of the three guards it shipped did not
work, and one test asserted the inverse of its name while green.** Every finding below was reproduced before
it was fixed, and **thirteen mutations were run against the fixes — all thirteen fail**. The theme is uniform
and worth naming, because it is the round's own thesis turned back on it: *a guard may not assert a cause it
has not confirmed* — and the round asserted, without confirming, that its hook ran, that its fallback ran,
and that its tests could fail.

### ⇒ 1. The hook's `python3` fallback aborted every commit

`commit_lint.main` was annotated `argv: Sequence[str] | None`, a PEP 604 union **evaluated at import time**.
The fallback runs whatever `python3` the OS ships — 3.9.6 on current macOS — where that raises
`TypeError: unsupported operand type(s) for |`. Exit 1 rejects the commit, so on that path **no commit could
be made at all**, clean ones included. The branch whose entire justification is *"still gets the guard rather
than silently getting none"* delivered a hard block plus a traceback.

Reachable wherever `uv` is off the hook's `PATH`: GUI git clients (VS Code, Tower, GitHub Desktop) and many
CI images. **The existing subprocess test ran with the ambient `PATH`, so line 24 had never executed.**

Fixed with `from __future__ import annotations`, and `commit_lint` now imports stdlib only, deliberately, so
the fallback stays version-portable. **Two new tests, and the second is the one that matters**: a test that
only asserts the bad message is rejected passes on a module that cannot be imported, because a crash also
exits non-zero. *"Rejects the bad message"* and *"rejects everything"* are the same observation until
something asserts the good message survives.

### ⇒ 2. The hook was blind to `git commit -m` — see § "What ships for 118" above

Recorded at the bullet itself rather than twice.

### ⇒ 3. Two real-database guard tests were vacuous, and asserted the inverse of their names

`test_a_database_predating_db038_is_told_to_migrate` and its class-grain sibling set up a fault with
uncommitted DDL, then called a guard whose **first action is `conn.rollback()`** — which put the dropped view
straight back. The guard then probed a healthy database and answered *"this is NOT a missing migration"*,
the precise opposite of both test names and both docstrings. They passed because `match="drugref migrate"` is
a **substring of all four** of `guard_message`'s messages: two prescribe it, two say it would do nothing.

**The mechanism was already written down** — the bullet three items above says "dropping a view inside the
rolled-back `conn` fixture does not work either, because `missing_relations`' own rollback undoes it" — and
the two tests it applies to were left alone anyway. Knowing a trap and checking for it are different acts.

Both now assert a **branch-unique** string (`"predates db/NNN"`, `"is DROPPED"`, `"NOT a missing migration"`,
`"an older shape"`) and say in their docstrings which branch a rolled-back fixture can actually reach. **This
is not a production defect**: nothing restores a dropped view in the field.

### The rest, in one list

- **`db.migration_applied` read `drugref.schema_migration` unguarded, from inside the guard.** The ledger is
  created by `db.apply_migrations`, not by any `db/*.sql`, so a hand-replayed or selectively-restored database
  has every view and no ledger — and the surviving traceback then named `schema_migration`, *not* the relation
  the operator was reading, while `cli.main` (which catches only `RuntimeError`) rendered no sentence at all.
  **Both probes are now wrapped**, and a failed probe is a **fifth state** that leads with the original error.
- **`migration="38"` for `"038"` silently restored the closed loop.** The pattern `38\_%` matches no
  zero-padded row, so every caller was told its migration was unapplied. `"%"` fails the other way, reporting
  every migration applied. `db.migration_applied` now **rejects anything that is not three digits**, and a
  test checks all five call-site literals against the files in `db/`.
- **The `\_` escape had nothing holding it.** Both rows in its test failed to match with *or* without the
  backslash. `5001_a_migration_one_digit_longer.sql` is the row that makes it load-bearing.
- **Two of five call sites caught `UndefinedTable` alone**, making `guard_message`'s `UndefinedColumn` branch
  unreachable from both — the standing rule db/035 wrote in prose, lost twice in the round that quoted it.
  `migration_guard.WRONG_SHAPE` is now one tuple and `migration_guard.guarded` the one context manager;
  `import psycopg` fell out of all three CLI modules as a result.
- **`consequence.capitalize()` lower-cased the remainder**, rendering `DISCARDS` as "discards" and `NULL rank`
  as "null rank" in three of four branches — untestable by construction, since the only test asserting the
  consequence sat on the fourth. **The refuted branch also named three causes that cannot produce the state it
  describes** (schema-qualified reads make `search_path` irrelevant; missing `USAGE` raises 42501, uncaught;
  Postgres refuses to drop a base table under a live view). Both fixed; the branch now says it cannot narrow
  further and hands over Postgres's own message.
- **#120's banner repeated #122's defect in its own voice**: its three causes all blame the operator's typing,
  and on a migrated-but-never-ingested database every uuid lands there and none applies.
  `registry_read.registry_is_empty` now separates the two. The registry read itself — **the first relation
  `interactions` touches, added by #120** — was also the only unguarded one, and now uses `guarded` too.
- **`CLASS_GRAIN_VIEWS[0]/[1]/[2]`** gave one tuple two incompatible jobs; `[1]` and `[2]` are interchangeable
  at the SQL level, so swapping them silently reported the disagreement count as ungraded. Three named
  constants now, with the tuple **derived** from them, so its order stops mattering.
- **The report counted matches, not issues** — "would CLOSE 2 issues" for one issue named twice, and a
  suggestion reading "issue 114, issue 114". Deduped on `int`, displayed as written.
- **Smaller**: `errors="replace"` so an undecodable message reports instead of tracebacking; plural agreement
  (`A, B, C are missing`); `_said` collapses psycopg's `LINE 1:`/caret block so it cannot break the sentence
  it is spliced into; `raise_missing` rejects an empty `relations` **and a bare string** (a missing trailing
  comma probed the name one character at a time); the hook now triages `uv`'s exit status instead of
  forwarding it, so a stale lock no longer rejects a good commit.
- **Test coverage that did not exist**: the `signature_backdated` guard had **none** — that call site had never
  executed. Nor had `exc.diag.message_primary`: every guard test hand-builds its exception, where
  `message_primary` is `None`, so all of them were validating the `str(exc)` fallback. Nor seven of the nine
  closing keywords, the plural unknown-uuid branch, or the `\b` word boundary.
- **Comment accuracy**: the test constants were **paraphrases** of the offending commit bodies while claiming
  to be verbatim — in a file whose own comment says "a paraphrase of a token-adjacency bug is a different
  input". They are now copied byte for byte, and `ed1ab5e`'s real four-line block is a better input than the
  paraphrase was: it names five issues, closes one, and puts `#115` at the start of the line after one ending
  in a comma, which is the cross-line case the gap class exists for.

## The 5c.3 source evaluation (2026-08-13) — OnSIDES and DrugCentral, measured rather than assumed

Both sources were licence-checked during 5c.2 and recorded as "worth evaluating". They have now been retrieved
and measured, before any commitment to `5c.3`'s shape. **The headline: the paragraph above was right about
DrugCentral and wrong about OnSIDES, in the specific way a licence check cannot catch — a source can be
perfectly clean and simply not contain the data you want.**

### OnSIDES carries NO interaction content, and cannot be made to

Licence re-confirmed at the source: code **MIT**, data **CC BY 4.0** in a separate `LICENSE-DATA`, attribution
by citation (Tanaka et al., *Med* 2025, PMID 40179876). Latest code release **v3.2.1** (2026-07-20); latest
**data** release **v3.1.1** (2026-04-22), one 84.9 MB zip. So the clearance stands — and is irrelevant, because:

- **The schema has nowhere to put a second drug.** All eight shipped CSVs (seven of them tables in
  `schema/postgres.sql`; `high_confidence` ships without one): `product_label`, `product_adverse_effect`
  **(product_label_id, label_section, effect_meddra_id, match_method, pred0, pred1)**,
  `vocab_meddra_adverse_effect`, `vocab_rxnorm_{ingredient,product}`, `product_to_rxnorm`,
  `vocab_rxnorm_ingredient_to_product`, `high_confidence`. The unit is one label × one MedDRA term. **A pair has
  no representation.**
- **Measured over all 6,928,666 rows** (streamed from the release zip): sections `AR` 5,283,772 · `WP` 1,196,843
  · `BW` 59,644 · `NA` 388,407. **Exactly ONE interaction-flavoured term exists in the whole MedDRA vocabulary
  OnSIDES ships** — `10022527 "Interaction with alcohol"` (LLT) — used by **13 rows**. There is no
  `Drug interaction` PT at all. **The predicate is stated so the claim can be refuted rather than trusted:** of
  the **6,423** rows in `vocab_meddra_adverse_effect.csv`, exactly **one** contains the case-insensitive
  substring **`interact`** in any column. A weaker predicate than a curated concept list, deliberately — a
  substring this broad *over-*matches, so finding one hit is the strong form of the result.
- **The hope recorded in 5c.2 was that Warnings and Precautions is "where interaction warnings live". WP is
  parsed — 1.2M rows of it — and it yields adverse-effect terms, because that is what the model extracts.** The
  section is right; the extraction target is not. Getting a partner drug out of it is a different task (drug NER
  + relation extraction), not a threshold change.
- **OnSIDES does not read the section that does carry interactions.** Its US pipeline
  (`snakemake/us/parse/Snakefile`) enumerates seven LOINC section codes — AR `34084-4`, BW `34066-1`, WP
  `43685-7`, WA `34071-1`, PR `42232-9`, SP `43684-0`, OV `34088-5` — and **`34073-7` DRUG INTERACTIONS is not
  among them.**

**What OnSIDES is still good for is exactly what ROADMAP already said: the *method*, MIT-licensed.** The label
fetch, the section split by LOINC code, the annotation/train/threshold loop, the RxNorm bridge — all reusable.
The *data* release is not a DDI source and must stop being listed as a candidate one.

### SPL section 7 is the real material, and it is public and rich

Verified against a live DailyMed label rather than from the spec: prescription SPLs carry
**`34073-7` DRUG INTERACTIONS SECTION** alongside the 27 other sections. Tizanidine
(`8d0b2b22-e1df-4ad5-92e6-f9a369108e4b`) states, in 690 characters, exactly the ONC floor entry drugref already
ships — *"Concomitant use of tizanidine with strong cytochrome P450 1A2 (CYP1A2) inhibitors (e.g., fluvoxamine,
ciprofloxacin) is contraindicated"* — plus a distinction **drugref currently cannot express**: strong CYP1A2
inhibitors are contraindicated, **moderate or weak** ones are "avoid concomitant use". MED-RT's
`Cytochrome P450 1A2 Inhibitors [MoA]` is one undifferentiated class. **That is a finding for 5c.3's design, not
a defect to file**: the label's grain is (drug × inhibitor class × *potency band*), and a schema that cannot
carry the band will either over-warn or drop the qualifier silently.

**Scale, roughly measured — and it took THREE attempts, each wrong in a different way, which is the point.**
DailyMed holds **158,508** SPLs (`/services/v2/spls.json` → `metadata.total_elements`, re-checked 2026-08-13).

1. A first **25**-label sample said 3 carried section 7. It had landed on a run of OTC sunscreens — **sampled
   without classifying at all.**
2. A **50**-label re-sample, classified, was recorded here as *"14 of 23 prescription, 0 of 17 OTC"*. **That
   tally accounts for only 40 of its 50 labels and is superseded** — the missing 10 were never written down, so
   the sample could not be checked, only believed.
3. Re-measured 2026-08-13, and this one **closes**: five pages × 10, each label classified by its **document-type
   code** — `34391-3` prescription, `34390-5` OTC — with `34073-7` looked up among the label's own LOINC codes.
   **14 of 16 HUMAN PRESCRIPTION labels carry `34073-7`; 0 of 30 HUMAN OTC do**; the remaining 4 are neither
   (2 × `50577-6`, 2 × `81203-2`, both animal/bulk). **16 + 30 + 4 = 50.**

**The classification step has its own trap, found while fixing the arithmetic: do NOT key on `displayName`.** It
carries case variants in the same 50-label draw (`HUMAN OTC DRUG LABEL` *and* `Human OTC Drug Label`), and the
first LOINC-coded element in a document is not reliably the document type — one label in the 50 opened on
`RECENT MAJOR CHANGES SECTION`. Key on the code.

Still a small sample, indicative only — **re-measure, never re-quote.** What it supports is not a rate but a
requirement: the material is on prescription labels specifically, and **a 5c.3 that samples DailyMed without
filtering by document type will mis-measure its own corpus exactly as attempts 1 and 2 did.**

### DrugCentral DOES carry a real DDI table, and it is worth a slice

Licence re-confirmed at the source: **CC BY-SA 4.0** (`drugcentral.org/privacy` links the legalcode), no
registration, one 1.4 GB gzipped `pg_dump`. Bundle-OK *because* drugref's data layer is itself share-alike.
**Its DDI content is no longer unverified** — measured by streaming the dump and extracting the `ddi` COPY block:

- **7,621 rows**, shape `(drug_class1, drug_class2, ddi_ref_id, ddi_risk, description, source_id)`, table
  comment *"Drug-Drug and Drug class - Drug class interaction table"*. Severity vocabulary is small and
  reference-scoped: `Significant` 5,264 · `Critical` 2,307 · `Potentially significant` 26 · `Avoid combination`
  15 · `Contraindicated` 9. Every row carries a `description` (none empty).
- **Both endpoints are free text in a `varchar(500)`, mixing drugs and classes with no code at all** — the
  integration cost is a resolution problem, and it was measured against this project's own registry rather than
  guessed. Of **970 distinct endpoint names**: **860 match a `substance_moiety.display_name`** exactly
  (case-insensitive), **8 match a MED-RT class name**, **102 match neither**. **7,000 of 7,621 pairs (91.9%)
  have both endpoints keyable today**, 6,973 of them moiety × moiety.
  **⇒ THE CLASS HALF OF THAT SENTENCE IS WRONG IN BOTH ITS NUMBER AND ITS AUTHORITY, corrected by the
  2026-08-23 re-measurement — see § "The DrugCentral re-measurement" below.** It is **4**, not 8, and all
  four are **MeSH** classes, not MED-RT ones; the residue is therefore **106**, not 102, and *keyable* is
  **6,991 (91.7%)**, not 7,000. Re-measured against the db/034-era registry the original run used, so it is
  not a schema-drift artefact. The moiety half — 860, 6,973, 6,941, 604, 6,337 — reproduces EXACTLY.
  **⇒ FOUR DENOMINATORS LIVE IN THIS SECTION AND THEY ARE NOT INTERCHANGEABLE — read this before re-quoting any
  of them.** *Keyable* (**7,000**) counts moiety-**or**-class matches; *moiety × moiety* (**6,973**) is the
  subset with two moiety endpoints, and the **27**-row difference is the rows with exactly one class endpoint.
  *Unresolvable* (**648**, below) is `7,621 − 6,973` — it is the complement of the **moiety** figure, not of the
  keyable one, which is why `7,621 − 7,000 = 621` does **not** equal it. Finally those 6,973 **rows** collapse to
  **6,941 distinct unordered pairs**, and it is the 6,941 the overlap arithmetic runs on. Rows, pairs and
  distinct-pairs are three different units; quote **6,941** downstream and none of the others.
- **The 102 unmatched split into two different jobs, and conflating them would under-cost the slice.** **87** are
  INN spellings against drugref's UNII-derived (USAN) names — `ciclosporin` (100 uses), `dicoumarol` (65),
  `ethinylestradiol` (47), `acetylsalicylic acid`, `amfetamine`, `methylthioninium chloride`, `suxamethonium`:
  a synonym bridge, not new data. The other **15 are base names whose *forms* drugref carries but whose base it
  does not** — `azithromycin` (40 uses) exists only as `azithromycin dihydrate` / `anhydrous` / `monohydrate`,
  `heparin` only as `heparin sodium`, `norepinephrine` only as `norepinephrine bitartrate`. That is the
  composition tree's job (slice 3), not a synonym list. **The 15 is a prefix heuristic and reads slightly high**
  — `glycerol` "matches" `glycerol 1,3-dimethacrylate`, a different substance — so treat it as the shape of the
  problem, not a count to quote.
- **The grain is overwhelmingly the one drugref's moiety rule already handles.** Despite the table's name, only
  8 endpoint names are classes at all, so this is not a second class-grain problem.
- **It does NOT close the QT gap (issue 93).** Three rows in 7,621 mention QT or torsades, and two of them are
  a high-/moderate-risk `... QT Prolonging Agents` self-pair — **class names with no member list**: `pharma_class`
  (25,687 rows) contains the string `QT` **zero** times, so the dump names those populations and never defines
  them. Issue 93 restated, not solved. **The exact class strings were NOT recorded verbatim** — this file and
  ROADMAP quoted them with the risk words in opposite order (`High/Moderate` vs `Moderate/High`), which is proof
  that neither was transcribed rather than paraphrased, and the dump is not retained locally to settle it.
  **⇒ SETTLED 2026-08-23, and transcribed at last: `High Risk QT Prolonging Agents` and
  `Moderate Risk QT Prolonging Agents`.** The `pharma_class` zero reproduces. **And the sharper fact neither
  file recorded: all THREE QT rows are `ddi_ref_id = 3` (Lexicomp), so every one of them is already excluded
  by rule 6.** Issue 93 is not merely un-closed by DrugCentral — the QT content is in the half drugref may
  not bundle at all. **The remaining routes to a QT list are unchanged: re-derive from SPL, or
  use the owner's Holbrook-group archive — which needs WRITTEN permission first, to the standard issues 6 and 25
  are held to.** (Recorded here because it had lived only in HANDOVER, whose history is disposable.)
- **Staleness is the real cost:** the only published dump is **`drugcentral.dump.11012023.sql.gz`**, `dbversion`
  **54**, dated **2023-11-01** — approaching three years old, and the download page has offered no newer one.
  A rebuildable projection pinned to a 2023 release is honest (provenance is recorded per `ingest_run`), but it
  is a floor that does not refresh.

**⇒ THE RULE-6 QUESTION WAS THE WHOLE EVALUATION, AND IT ANSWERS CLEANLY — but only because the answer was read
rather than inferred from DrugCentral's own CC BY-SA.** Every `ddi` row cites one of **three** references, and
the `reference` table names them:

| `ddi_ref_id` | rows | what it actually is | rule 6 |
|---|---|---|---|
| **2** | **7,571** | **VHA National Drug File – Reference Terminology (NDF-RT)** | **clean** — US federal work, and **MED-RT's own predecessor** |
| 1 | 13 | *Stockley's Drug Interactions*, Karen Baxter, 2010, **ISBN 0853699143** | **copyrighted book** — out |
| 3 | 37 | **Lexicomp Online**, Wolters Kluwer Health | **commercial compendium** — out |

**Bundle `ddi_ref_id = 2` only.** A CC BY-SA licence over a compilation is not evidence of the right to
relicense a third-party compendium inside it, and two of the three references are exactly that. **The exclusion
costs nothing measurable, which is the nice part: all 50 non-NDF-RT rows are also the rows whose endpoints do
not resolve** (they are class-named — `MAOIs or RIMAs`, `Strong CYP3A4 Inhibitors`), verified by the resolution
run — 648 unresolvable rows over the whole table, 598 over the NDF-RT subset, a difference of exactly 50.
**⇒ THE 648/598/50 ARITHMETIC REPRODUCES EXACTLY. Two things in the sentence around it do not.** (a)
**`MAOIs or RIMAs` is not an ENDPOINT** — `drug_class1`/`drug_class2` carry the letters `MAOI` on **zero** of the
7,621 rows, and the endpoint string this example was reaching for is `Monoamine Oxidase Inhibitors`, so the
example above was paraphrased from memory rather than read. **It does exist in the table, though, in a column
neither this note nor the correction that first fixed it was looking at:** `ddi.source_id` carries `MAOIs or
RIMAs` on **10** rows and the shorter `MAOIs` on **3** more — **all 13 of them `ddi_ref_id = 1`**, which is every
Stockley's row and nothing else, i.e. entirely inside the half rule 6 excludes. Re-derived 2026-08-23 against the
recorded extract; the over-general first correction is § "The DrugCentral ddi ingest"'s own finding. (b) *"the rows whose endpoints are class-named"* reads as BOTH endpoints and — measured directly against
`ddi.tsv` during the PR-150 review — **21 of the 50** carry an ordinary drug name at one end; at least three
rows have an ordinary drug at one end (`fusidic acid`, `methyldopa`, `risedronic acid`, each paired with a
class). The claim is true **per row** — every excluded row has at least one class-named endpoint — and the
2026-08-23 run states it exactly: **the 50 excluded rows contribute ZERO resolvable pairs**, so whole-table and
NDF-RT-only pair counts are identical to the row.

**⇒ AND THE FOLLOW-UP QUESTION THAT ONLY BECAME ASKABLE ONCE REFERENCE 2 WAS READ: if it is NDF-RT, and drugref
already ingests MED-RT, is any of it NEW?** Measured, not assumed — resolve both endpoints to moieties and
compare unordered pairs against `ddi_candidate_pair` (MED-RT, **20,238** distinct unordered pairs):

- **6,941** distinct moiety pairs resolve · **604 (8.7%) drugref already holds** · **6,337 are NEW.**
- **Same authority, different extraction, and that is why the overlap is small**: drugref reads MED-RT's
  **class-level contraindication rules** (635 rules inheriting to 21,664 pairs), while DrugCentral's `ddi`
  carries NDF-RT's **drug-level** interaction assertions. Neither is a superset of the other.
- **This is the number that justifies a slice.** A second candidate source that is 91% new, public-domain,
  moiety-grained and 92%-resolvable is worth `source = 'DRUGCENTRAL'`; one that merely restated MED-RT would
  not have been. **Admitting that source is NOT a one-line change** — it needs two CHECKs widened *and* an
  explicit `ids._SOURCE_CANONICAL` entry, in the same migration; `ids.py` warns by name against leaning on the
  upper-case fall-through. Detail and the silent-rebuild failure mode it prevents:
  [#101](https://github.com/cairn-ehr/drugref/issues/101).
  **⇒ CORRECTED 2026-08-23 BY THE INGEST THAT ACTUALLY DID IT: this sentence named the second CHECK as
  `class_contraindication_source`, and HANDOVER then quoted it forward. Both were wrong.** DrugCentral asserts
  unordered moiety pairs carrying a severity — neither a class rule nor a directional moiety rule — so it writes
  no row into `class_contraindication` or `moiety_contraindication`, and `db/049` deliberately leaves both source
  CHECKs (`('MED-RT','ONCHIGH')` and `('MED-RT')`) exactly as they were;
  `test_class_contraindication_source_is_NOT_widened` pins that they are untouched. The second CHECK a new source
  really needs is **`ingest_run_writer`**, which no document had named at all — so the sentence was not merely
  imprecise, it pointed at the wrong constraint and hid the right one.

### Which of these figures can be RE-DERIVED, and which must be taken on trust

Stated because a future session is told to act on them, and the two halves are not equally checkable. **Nothing
in this section is backed by a committed script** — the repo had no home for one when this was written, so the
method is written out above instead, which is what makes the first two groups re-runnable at all.

**⇒ THAT EXCUSE EXPIRED ON 2026-08-16 AND THE FIGURES ABOVE DID NOT BENEFIT.** `tools/` now exists as a
committed top-level package (PR [#127](https://github.com/cairn-ehr/drugref/pull/127)) holding exactly the
kind of measurement script this paragraph says had nowhere to live — `tools/pregnancy_lactation_spike.py`
re-runs its own evaluation and *writes the results file*, which is why that spike's numbers are re-derivable
and DrugCentral's are not. **A future source evaluation puts its measurement in `tools/`**; the numbers above
stay measured-once because the dump is gone, not because the repo still lacks a home.

- **Re-derivable from this checkout, and were re-derived during PR #103's review:** every drugref-side count
  (rebuild the chain at the pinned releases, then one `SELECT count(*)`); the hot path, because the subject
  moiety's UUID is written down — that is the whole reason for writing it down.
- **Re-derivable only while the local artefact survives:** the OnSIDES figures. `downloads/eval/onsides-v3.1.1.zip`
  (84,862,297 bytes) is **gitignored** — machine-local, not repo state. Every OnSIDES number above was
  re-derived from it independently and matched exactly, including the 6,928,666 total, all four section counts
  and the single `interact` vocabulary hit.
- **~~NOT re-derivable here at all~~ — RE-DERIVABLE SINCE 2026-08-23, and six of those figures were wrong.**
  This bullet used to read *"the 1.4 GB dump is not retained on this machine and is not in the repo, so
  7,621 / 970 / 860 / 102 / 7,000 / 6,941 / 604 / 6,337 and the three-reference table rest on a single
  unrepeated run"*. The dump was re-fetched, the measurement now lives in `tools/` as this bullet's own
  paragraph demanded, and the whole thing re-runs in ~40 s from a cached extract. **The rule-6 determination —
  the load-bearing one — reproduces exactly**, read from the `reference` table rather than inferred. Full
  account, including which figures did NOT reproduce: § "The DrugCentral re-measurement" below.
- **Re-measured 2026-08-13 and now closing:** the DailyMed document-type sample (above). Its predecessor did
  not close, which is why it is the one figure in this section that was redone rather than restated.

## Two further source spikes (2026-08-16) — FDA/toxicity and pregnancy/lactation

**Both landed on `main` from a DIFFERENT AGENT while the guard round's review was the newest thing every
document described, and that is the fact to carry forward, not the sources.** PR
[#126](https://github.com/cairn-ehr/drugref/pull/126) updated ROADMAP only; PR
[#127](https://github.com/cairn-ehr/drugref/pull/127) — **2,147 lines, two new `src/drugref/ingest/` parsers, a
new top-level `tools/` package, three test files, +16 tests** — updated **no document at all**. The next
session read a HANDOVER whose suite count, ROADMAP position and code map were all one round behind, and only
running the suite showed it. **Rule 8 is not ceremony: check `git log` against HANDOVER before trusting it.**

### The FDA spike (PR #126) — the potency vocabulary 5c.3's evaluation found missing

Spec: [FDA interaction and toxicity source
spike](superpowers/specs/2026-08-16-drugref-fda-interaction-and-toxicity-source-spike.md). Four rule-6
determinations, all made against live sources with checksums recorded in the spec's §2 reproduction manifest:

- **`FDA-CYP` — bundle.** FDA's CYP/transporter examples table, public domain. **This is the answer to the
  question 5c.3's evaluation left open**: SPL section 7 qualifies interactions by potency band (*strong*
  CYP1A2 inhibitors contraindicated, *moderate or weak* "avoid"), and MED-RT's single undifferentiated
  `Cytochrome P450 1A2 Inhibitors [MoA]` cannot express it. **The page has no release identifier**, so fetch
  time + SHA-256 *are* the release identity.
- **`FDA-DICT` / `FDA-DILI` — bundle**; **`FDA-DIRIL` — bundle ONLY the narrow FDA-authored projection**
  (raw name · UNII 1 · UNII 2 · `My Findings (Toxicity)` · FDA link). The workbook also carries DrugBank
  identifiers and descriptions, ATC/DDD fields and two third-party literature classifications: **a
  public-domain FDA publication does not turn copied third-party material into federal work.** That is the
  sharpest rule-6 refinement this project has recorded — the unit of clearance is the COLUMN, not the file.
- **`DRUGCENTRAL-OMOP` — do NOT bundle, and the reason is a provenance audit rather than a licence.**
  DrugCentral's contraindication content is pre-2012 OMOP 4.4 plus later label curation, and the published row
  carries **no source kind, date, label id, citation or curator** — so the clean subset is *not selectable*.
  **A high row id is not evidence of creation date and must not be used as a proxy.** Separate from, and no
  threat to, the `ddi_ref_id = 2` decision.

**The spike's implementation order is 1) DrugCentral DDI, 2) FDA-CYP before SPL mining, 3) DIRIL first into a
non-firing toxicity projection, 4) DICTrank + DILIrank as second and third writers, 5) promotion only after
clinician review.** Its §7 verification gates are written as a checklist any source round must pass, and its
one-line invariant is worth quoting whole: **ingest preserves evidence; curation creates clinical judgement.**

**A DIRIL parser trap that will bite anyone using a generic reader:** the workbook's declared used range is
`A1:Y1048381` although data ends at row 318, and its worksheet XML is **209 MB uncompressed**. Stream rows
1–318, require the exact header, reject any non-empty cell after 318 — **do not infer the range from the
workbook's own dimension.**

**DICTrank qualifies issue 93 rather than closing it.** FDA *does* publish an open cardiotoxicity dataset
carrying QT evidence (a broad `qt|torsad` scan finds 228 rows; 149 over 133 ingredients once no-concern rows
are dropped) — but it is a **review population only**, since no-concern rows include negative phrasings like
"QT interval is not prolonged". It is still **not** a CredibleMeds-equivalent torsades list.

### The pregnancy/lactation spike (PR #127) — four sources, all still non-firing

Specs: [design](superpowers/specs/2026-08-16-drugref-pregnancy-and-lactation-source-spike.md) ·
[measured results](superpowers/specs/2026-08-16-drugref-pregnancy-and-lactation-source-spike-results.md),
run against `drugref_db038`. MED-RT stays the **candidate floor** (549 direct pregnancy rules, 66 lactation);
LactMed (1,940 evidence records, 1,702 moieties resolved, **1,679 outside the MED-RT lactation floor**), AEMPS
CIMA (20,422 authorised products) and ANSM BDPM (15,857 specialties) are all **"design next"** — which the
results doc is explicit does **not** approve normalization or any write to `curated_condition`.

- **A clinician review is PENDING and is a gate, not a formality.** The results doc ships a 23-row worklist of
  identifiers and asks a human to verify extraction boundaries, product scope, and whether normalization
  would be unsafe. Nothing downstream may treat these sources as cleared until that happens.
- **The three sources have UNLIKE GRAINS — active-substance review, Spanish product section, French product
  section — and a production design must not collapse them into one moiety recommendation.** That is the
  finding, and it is the same shape as 5c.2's class-grain lesson.
- **`tools/` is now a committed top-level package**, and `tools/pregnancy_lactation_spike.py` *writes its own
  results file*. See § "Which of these figures can be RE-DERIVED" — this is the home that section says the
  repo lacked.
- The two `src/drugref/ingest/` modules (`lactmed.py` 211 lines, `regulatory_population.py` 296) are **spike
  parsers reachable from `tools/` and the tests only** — no CLI subcommand, no orchestrator, no migration, no
  `ingest_run.source` spelling. They are pure parsers by the architecture rule, and nothing writes.

## Slice 5c.2g — FDA-CYP potency classes (`db/039`–`db/043`, measured 2026-08-17)

Spec: [slice-5c.2g](superpowers/specs/2026-08-16-drugref-slice-5c2g-fda-cyp-classes-design.md). The potency
vocabulary the 5c.3 evaluation found missing: SPL section 7 qualifies interactions by band (*strong* CYP1A2
inhibitors contraindicated, *moderate or weak* "avoid") and MED-RT's single undifferentiated class cannot
express it. **65 PK classes, `has_PK` membership, and a projection holding every parsed tuple including the
ones deliberately not promoted.** ROADMAP § 5c.2g carries the shape; this section carries the traps.

### ⇒ THE HEADLINE: SEVEN OF THE DESIGN'S OWN FIGURES WERE WRONG, AND EVERY ONE WAS FOUND BY IMPLEMENTATION

Not by review, not by re-reading — by a task running the real bytes and reporting a number that disagreed.
**They share one shape, and it is the same defect the slice exists to prevent:** something asserted a
property it had not confirmed. **Numbers 6 and 7 were found by the FINAL review, after this table said
five** — so the count of wrong figures was itself a wrong figure, in the paragraph about wrong figures. That
is not an embarrassment to bury; it is the measurement of how strong the pull is. The design round's probe was a partially-working parser, and **a
partially-working parser does not announce itself — it hands you a plausible value, and a plausible value
gets written down as a measurement.**

| # | the spec said | the truth | how the wrong value was produced |
|---|---|---|---|
| 1 | FDA prints `ritonavir 14, 15,` | `ritonavir 14, 15, 16` | the probe's `(\s+\d+)+$` ate the trailing ` 16` and left the comma. **That string appears nowhere on FDA's page.** |
| 2 | 415 tuples | **419** | the probe could not parse four tuples and *rejected* them; the round recorded the survivors of its own mis-parse as the total |
| 3 | 29 qualified cells / 22 substances | **31 / 24** | the probe saw a footnote only at a name's or cell's very END, so it missed the mid-cell markers |
| 4 | the closed vocabulary "must **reject**" three cells | it rejects **zero** | the gate was described as a *filter*; it is a **tripwire** |
| 5 | `ddi_candidate_pair` **21,664** must not move | `drugref_db038` holds **21,877** | the figure was measured on `drugref_policy` and `drugref_5c4`, two earlier databases, and quoted as an expectation for a third |
| 6 | **18** footnotes, "a re-fetch can add a nineteenth" | **21** numbered, plus one lettered `b` the page defines nowhere | counted by hand off the rendered page rather than by the parser that reads the block |
| 7 | §14: "the **8.6%** withheld could grow" | **9.2%** | §5 was corrected to 9.2% and §14 was not — **one number, two homes, inside the very document arguing against that** |

**Number 3 was in the unsafe direction and is the one to remember:** the undercounted cells were ones drugref
would have **promoted to membership while FDA had qualified them**. Number 4 is the one that changed a rule
rather than a number — the correct statement is that **the vocabulary rejects zero tokens on a correct parse,
and that is the passing state.** Its job is to fire when the *grammar* is wrong. **A round that sees it reject
something should suspect its own parser first and the data second**, because that is the way this one broke.

Number 5 generalises furthest: **an invariance claim must be checked as an invariance** — same query, same
database, either side of the change — never against a constant transcribed from somewhere else. The spec now
says so and names no absolute values.

### The source, and why a regex parse of HTML is defensible here

Retrieved 2026-08-16; **the fetch reproduced the source spike's SHA-256 exactly**
(`7400dc89…7ffa73`), which makes that manifest the first source figure in this project verified by a second
independent run rather than trusted.

**Table 1 is a MATRIX, not a list of facts:** 245 data rows × 11 columns, where the first column names the
substance and **each of the other ten IS a `(system, role, potency)` tuple**, the cell holding the pathway
list. 337 non-empty cells → **419 tuples over 65 classes**; 244 distinct substances (`aprepitant` occupies two
rows, which is why 245 ≠ 244).

The parse is guarded on both sides, and that is what makes it safe rather than reckless: **row and cell counts
are asserted** (exactly 11 in each of 245), **the pathway vocabulary is closed and partitioned by system**, and
**the column heading and the cell text state the role and potency independently, so they are cross-checked**.
A lenient parse of the same page yields **69 classes reporting zero errors**, four of them garbage minted with
real immortal UUIDs (`cyp:1a2 20`, `transporter:oatp1b1 inhibitor`).

### The cell grammar, dirty in five ways, and footnotes in two namespaces and three positions

A cell is a `;`-separated list of `pathway [footnote] [role phrase]` closed by a trailing role phrase covering
the items that state none. **Three separators for one concept** (`;`, `,`, `and`) — and rifampin's
`1A2, 2B6; 2C8; 2C9 moderate inducer` **mixes two of them**, four pathways from one cell, the only such cell in
the 337. Plus `CYP3A` beside bare `3A`; `OATP1B` where others say `OATP1B1`/`OATP1B3` (**its own class, never
expanded — that would manufacture specificity FDA declined to state**); `moderately sensitive` against the
column's `Mod SENS SUB`; and teriflunomide's `BCRP; OATP1B1 inhibitor; OAT3 inhibitor`, where **the role word
repeats per item**.

**Footnote markers are numbered AND lettered, and sit in three places:** glued to the name (`adefovir 1`, 21
rows), **as a comma-separated list** (`ritonavir 14, 15, 16`), inside a cell at the end (conivaptan's
`3A moderate inhibitor 5`), **attached to one pathway mid-cell** (ciprofloxacin's `1A2 20 ; 3A moderate
inhibitor`), and as a letter (cenobamate's `inducer b`).

**Order is load-bearing inside the per-item loop: peel the role phrase, THEN split footnotes, THEN match the
pathway.** Rifampin's `OATP1B1 13 ; OATP1B3 13 inhibitor` splits into an item where the marker sits *before*
the role phrase, so the other order silently mints `transporter:oatp1b1 13`.

### ⇒ TWO FOOTNOTES NEGATE THE ROW THEY SIT ON, AND THAT IS THE WHOLE DESIGN

| row | the row asserts | its own footnote |
|---|---|---|
| `bupropion 2` | `2B6 sensitive substrate` | *"Bupropion itself is **not** a sensitive substrate."* |
| `rolapitant 17` | `P-gp; BCRP inhibitor` | *"**Intravenously administered** rolapitant does **not** inhibit BCRP and P-gp."* |

So a footnoted cell writes **no membership** — 31 cells, 24 substances, 9.2%. It lands in `fda_cyp_assertion`
with the footnote text and raises a question. **Ingest never decides whether the footnote negates**, because
that is a clinical reading of prose: *ingest preserves evidence; curation creates clinical judgement.*

**The footnote text is PARSED from the page (21 markers), not transcribed.** The first implementation
hardcoded FDA's prose as a Python dict. `checksum` and `dateModified` exist to make a source change loud, and
**that one column escaped both**: a reworded footnote would re-ingest *green* while storing the old wording in
the column whose entire purpose is carrying FDA's words. The fixture gained the footnote block; the dict is
gone. **All 65 classes are minted even when every member is withheld**, so a withheld row can name the class
it would have joined, and a zero-member class is distinguishable from a band FDA never defined.

### Name resolution: 224 of 244, five different jobs, and nothing bridged

Exact, case-insensitive, against `substance_moiety.display_name`. **Ambiguity is unresolved, never "pick the
first"** — pinned by a test although nothing is ambiguous today, because the registry grows.

The 20-name residue splits into **five jobs, and conflating them would under-cost the next slice** (the lesson
the DrugCentral evaluation recorded when it split its own 102): **9** combination regimens, **3** non-drug
entities, **3** enantiomers, **3** apparent synonyms (`rifampin`/`rifampicin`, `glyburide`/`glibenclamide`,
`peginterferon alpha-2a`/`alfa-2a`), **1** apparent metabolite, **1** group term (`oral contraceptives`).

**⇒ A TRAP THAT INVERTS THE OBVIOUS ASSUMPTION: `curcumin` and `diosmin` — two of FDA's five declared
non-drugs — RESOLVE as ordinary moieties.** Non-drug and unresolvable are independent properties, so **the
non-drug list must be FDA's own pinned five, read from its prose, never inferred from a resolution failure.**
Disposition order is therefore stated rather than left to fall out: `non_drug_entity` → `combination_regimen`
→ `unresolved_substance` → `withheld_qualified` → `member`, because grapefruit juice is *both* a non-drug and
footnoted. Pinned by a test, which was mutation-tested to prove it pins the order rather than passing
incidentally.

### The stored vocabulary has FIVE values, not nine — the standing rule at work

Only **`combination_regimen`** and **`non_drug_entity`** name a category, because only those two are asserted
by **FDA** (the regimen string it wrote; its own five-substance sentence). The other four recognisable
categories collapse to **`unresolved_substance`**, because calling `R-venlafaxine` an "enantiomer of a held
racemate" is a chemical relationship inferred **from a string prefix** — [#122](https://github.com/cairn-ehr/drugref/issues/122)'s
manufactured-cause defect in a new coat. See § "Standing rules": *a disposition records what was OBSERVED,
never what the round suspects it MEANS.*

### `db/040` and `db/041` — two migrations spent on the question register, and why

**`db/040`: the gap view's grain was wrong in the FINER direction.** It grouped every disposition on
`(substance, column_heading, pathway)`, so *"which drugref moiety is FDA's rifampin?"* was asked **eight
times**, once per cell mentioning it — **71 immortal `question_uuid`s where 55 belong.** Only
`withheld_qualified` is genuinely per-cell (each footnoted cell is its own adjudication); the other three are
per-substance. The view became a `UNION ALL` of two grains, and **the `COALESCE` in the key was chosen so every
`withheld_qualified` UUID stayed byte-identical** — proven by computing the old and new key sets over the same
data and diffing them, not argued. [#41](https://github.com/cairn-ehr/drugref/issues/41)'s standing rule, in
the direction it is usually not caught.

**`db/041`: splitting the view left its own silent hole.** Two halves need two `WHERE` clauses, and those
became an **allowlist** of the four known dispositions — so a future fifth or sixth value would be **dropped
from the worklist entirely**, never reaching the question `CASE`, while `questions.py`'s comment claimed it
"aborts the ingest loudly". **A gate that does not fire, with a comment saying it does** (issues 74/66/76 plus
122). The subject half now reads `NOT IN ('member', 'withheld_qualified')`, so an unknown disposition reaches
the `CASE`, matches no `WHEN`, yields `NULL` and trips `question_text`'s `NOT NULL`. Verified by widening the
CHECK with a synthetic sixth value: **before, 0 gap rows; after, 1.**

### Measured on `drugref_5c2g` (from `TEMPLATE drugref_db038` + `drugref migrate`, the seventh round running)

Ingest wall-clock **4.5 s** (second run 4.0 s), via the CLI against the pinned page.

| | |
|---|---|
| classes minted | **65** (5 of them with zero members — expected: FDA defines the band, drugref has adjudicated none of its members) |
| assertions written | **419** |
| memberships written | **348** |
| dispositions | `member` 348 · `withheld_qualified` 33 · `combination_regimen` 17 · `unresolved_substance` 16 · `non_drug_entity` 5 |
| questions raised | **55** — `withheld_qualified` 33 · `combination_regimen` 9 · `unresolved_substance` 8 · `non_drug_entity` 5 |
| substances resolved | **224 / 244** |

**Must not move, and did not** — read before and after on the same database, per § "THE HEADLINE" number 5:
`substance_moiety` 19,438 → 19,438 · `ddi_candidate_pair` 21,877 → 21,877 ·
`gap_uncurated_interaction_rule` 593 → 593 · `gap_uncurated_condition_contradiction` 168 → 168.
`class_contraindication` holds **zero** FDA-CYP rows and no DDI pair was created. A second ingest reproduces
every figure byte-for-byte, and MED-RT's and MeSH's class counts are untouched.

**⇒ ONE FIGURE MOVED THAT LOOKED LIKE A DEFECT AND WAS SOMEBODY ELSE'S: `open_question` grew by 47, not 55.**
55 questions were minted, and the same run **closed 8 stale ones belonging to a different gap kind entirely**.
Cause: `questions.register_from_gaps()` runs at the end of **every** orchestrator and re-derives **all** gap
kinds, not the one being ingested — so ingesting FDA-CYP, a classification source with no relationship to
interaction rules, healed [#104](https://github.com/cairn-ehr/drugref/issues/104)'s 8 rows
(`uncurated_interaction_rule` cached 601 against a live 593). **That issue is therefore still open two
migrations later, and its title understates it: "the next ingest" means ANY source's ingest**, so whether the
register is accurate depends on what unrelated feed happened to run last. Recorded as a comment on #104 rather
than a new issue. **The lesson for a future measured round: do not put `open_question` on a must-not-move
list** — it legitimately moves by other sources' arithmetic, and a round that pinned it would have failed on
somebody else's staleness.

### Traps and standing notes

- **`ClassConcept.code` is now `str | None`.** FDA-CYP is the first source publishing **no code at all**, and
  `substance_class.published_code` has been nullable since `db/003`. Inventing a string for a column reserved
  for "the code as published" would be a manufactured fact in a provenance field.
- **`class_name` is source-tagged** — `CYP3A strong inhibitor [FDA-CYP]`, not MED-RT's `[MoA]` shape. MED-RT's
  bracketed suffix is *published by MED-RT*; this one is drugref's own label and says so.
- **The release identity is the page's own `dateModified`** (`2026-05-29T14:00`, in JSON-LD and two meta
  tags), **not fetch time** — the source spike said the page carries no release identifier and it does.
  **Fetch time records when drugref looked; `dateModified` records when FDA changed the content**, and only
  the second distinguishes a re-fetch of unchanged material from a revision. A page without it **fails and
  names the field** rather than substituting fetch time, which would put a value with a different meaning in
  the same column.
- **`--release` on the CLI is optional, and supplying it is a CHECK, not an override**: it must match the
  page's own stamp or the ingest fails naming both values, **before `provenance.open_run`**, so a wrong
  `--release` leaves no history behind.
- **A test module exercising an orchestrator needs its own autouse cleanup fixture.** `provenance.open_run`
  commits in its own transaction, so an `ingest_run` row escapes the `conn` fixture's rollback —
  `tests/conftest.py` says so in its own docstring, and `tests/test_gsrs_run.py:13` is the model.
- **Seeding a `substance_class` row does NOT test per-source clearing.** Class rows *accumulate* and are never
  deleted; only `class_membership` edges are rebuilt. A clear-scope test must seed an **edge**. Found by an
  implementer mutation-testing its own test, which is the sharpest self-check this slice produced.

### ⇒ THE REVIEW ROUND: FOUR CRITICALS, AND EVERY ONE WAS A GATE THAT WAS REASONED ABOUT AND NOT WRITTEN

Six review agents ran over the finished slice. **The pattern is worth more than the findings**: this is a
codebase whose comments argue carefully for the guards it needs, and in four places the argument was
written and the code was not. **Three of them carried a comment asserting the guard existed** — so reading
the module would have told you it was safe.

| # | The defect | How it was found | Fix |
|---|---|---|---|
| 1 | **The parser's headline claim was false.** `fda_cyp.py`'s "why a regex parse is defensible" argument said *"the row and cell COUNTS are asserted (245 x 11 exactly)"*. Only the cell count ever existed. Truncating the real page to six `<tr>` gave **5 tuples instead of 419, no error** — and the projection is delete-and-rebuild, so that run deletes 240 substances and commits, exit 0. | Measured against the real page | Shrink guard in `fda_cyp_run` (below), and the docstring now says what it actually asserts |
| 2 | **A cross-source abort.** `questions.py` concatenated `row_footnote_markers` unguarded. db/042 shipped that column nullable with no backfill, so in its own migration window every pre-existing withheld row is NULL, `\|\|` propagates NULL, and `open_question.question_text` is NOT NULL. `register_from_gaps` runs at the end of **every ingest of every source**, so the next MeSH/GSRS/PBS run died on FDA-CYP's residue, naming neither. | Reproduced on the measurement DB: 419 rows, all `substance` NULL, 33 in the window → `NotNullViolation` | `COALESCE(row_footnote_markers, footnote_markers, '(unrecorded)')` |
| 3 | **`parse_footnotes` returned `{}` silently.** `_FOOTNOTE_ITEM` requires a bare `<p><sup>`; adding one class attribute took 21 footnotes to 0 with the `<h2>Footnotes</h2>` heading still present. Every withheld question would then read *"FDA's note: (not captured)"* with the run green — the exact inversion of that function's own "a source change made loud" argument. | Mutated the real page | Raises when the section is present but yields nothing |
| 4 | **`parse_table` raised a bare `IndexError`** on a page with no table and on an empty table, against a module that documents `FdaCypParseError` everywhere. `_column_headings` reached the table directly and runs *first*, so `extract_rows`' own guards were unreachable through the only caller. | Ran it | One `_data_table_rows` gate both callers go through |

**The grain defect, which no test could have caught.** db/042 moved the `gap_key` onto the clean `substance`
(right: keying on FDA's footnote *numbering* meant a renumbered footnote changed the identity of every open
question about that substance) but left **both halves of the view grouping by `raw_substance`** — a strictly
finer grain. Two printed forms of one name (`aprepitant 3` / `aprepitant`) therefore yield **two view rows
carrying one `gap_key`**, and `register_from_gaps` upserts `ON CONFLICT (question_uuid) DO UPDATE` over an
**unordered** view: the second silently overwrites the first's text, non-deterministically, for an immortal
externally-citable UUID. **Measured: it does not fire on the 2026-05-29 release**, which is exactly why it had
to be found by reading — and why `db/043` regroups both halves onto the clean name before the FDA release that
introduces one. Demonstrated with synthetic rows: 2 rows, 1 key, before; 1 row after.

**Two figures were wrong again, in the same direction as the seven above.** The `31 of the 33 withheld gap
rows carry a name-level marker only` claim (in `questions.py` and in db/042's header) is **30**: 31 rows
*carry* a row-level marker, but **cenobamate carries both at once**, so the figure that sizes the two arms of
the nested CASE is 30 name-level-only / 3 cell-level (ciprofloxacin, conivaptan, cenobamate). Re-measured
directly off the real page against the measurement registry.

**A stated precedent that never happened.** db/041's header and a test both justify the sixth-disposition
design with *"this project has widened the CHECK on this exact column once already"*. It never has: db/039
creates `fda_cyp_assertion_disposition` with five values, db/040 and db/041 replace views, db/042 adds
columns. The genuine precedent — db/035 adding a gap kind mid-plan — sits in the same sentence. db/041 is
frozen, so the correction is recorded in db/043's header and in the test.

**What the fixes added.** `MIN_RETAINED_FRACTION` refuses a re-ingest that would drop more than half the
stored projection, compared against **what is stored** rather than a pinned 245 — so no constant needs
bumping when FDA grows the table, and a first ingest is never blocked (it destroys nothing); `--allow-shrink`
authorises a real one. `DISPOSITIONS` is checked **before the INSERT**, not after, so a sixth value cannot be
banked uncounted. `classes_minted` became `classes_in_release` + `classes_added` (it printed 65 on every
re-ingest while minting nothing). `db/043` holds the closed `(system, pathway)` vocabulary as a table the
assertion foreign-keys to — it was enforced only in Python, and `system`/`pathway` are only meaningful as a
pair, which two independent CHECKs cannot express. FDA-CYP was also **missing from `NOTICE` and the published
sources page**, which is a rule-6 blocker rather than a documentation nicety.

**Test coverage the round exposed.** The autouse `_registry` fixture seeded only `bupropion` and `cenobamate`
and **both are footnoted**, so no row could reach `member` in any DB test: `memberships_written` was 0
everywhere, `add_membership` and `RELATIONSHIP` were executed by nothing, and one test counted `member` rows
in a table that had none — **it could not fail**. Two enantiomer tests were unfalsifiable for the same reason
(no racemate registered, so nothing to mis-map *to*). A separate opt-in `_wider_registry` fixture now seeds
the member, ambiguity and independence cases without disturbing the counts the existing tests were measured
against.

## Reviewer GUI foundation (2026-08-17) — `reviewer-app/`, no migration

Canonical design: [`2026-08-17-drugref-reviewer-gui-foundation-design.md`](superpowers/specs/2026-08-17-drugref-reviewer-gui-foundation-design.md).

**The architectural decision:** Tauri 2 with plain Svelte/TypeScript and a Rust core. The production desktop client talks to
an authenticated review service over HTTPS; it never holds a shared PostgreSQL credential. A password session authorises an
operation but is not a clinical signature. The private Ed25519 key remains on the reviewer device; the existing
`signing_key` registry remains the public-key authority. One valid signature on the live assertion is the initial sign-off
policy, with the existing detached signature shape already supporting counter-signing.

**What this round actually ships:** a compact native shell, a clearly-labelled preview login/profile, queue metrics and
filters, master-detail review records, provenance and UUID display, disabled decision/annotation/signing surfaces, a strict
CSP, and one read-only Tauri command. Both native IPC and Vite browser preview consume the same committed JSON fixture; Rust
validates the fingerprint and stable targets before returning it. The five representative rows came from the live
`gap_uncurated_interaction_rule` / `gap_uncurated_condition_contradiction` views. The dated summary is 593 interaction rules,
168 condition contradictions and 255 expanded curated DDI pairs. None is a code invariant.

**A dependency defect was removed rather than waived.** The official Svelte template selected SvelteKit and resolved an old
`cookie` with three low advisories. This app has no Node server or SSR, so SvelteKit and the static adapter were removed in
favour of plain Svelte + Vite. `npm audit` then reported 0 advisories; output is 0.63 kB HTML, 14.51 kB CSS and 58.21 kB JS
(21.45 kB gzipped). The npm lock contains only AGPL/permissive licences. The Cargo tree is permissive/Unicode/Zlib/MPL-2.0,
selecting a compatible arm wherever a package is multi-licensed. The unused opener plugin and permission were also removed.

**Verified:** `cargo fmt --check`; 2 Rust unit tests; `npm run check` with 0 errors and 0 warnings; `npm run build`; `npm audit`
with 0 advisories; debug native integration and a release macOS app bundle. The measured release is **8.3 MB** with an
**8.0 MB** executable, before code signing or installer packaging. The Codex in-app browser was unavailable (no connected
browser surface), so automated screenshot QA did not run; do the design spec's desktop/narrow visual pass when one is
available. This limitation does not weaken the compilation/accessibility result and is not represented as visual approval.

**Next:** add the reviewer-account migration and authenticated Rust service skeleton. Likely tables are stable
`reviewer_account`, append-only `reviewer_profile` and `reviewer_password_credential`, `reviewer_key` mapping into
`signing_key`, revocable sessions and append-only annotations on `question_uuid`. Exact DDL is deliberately left to that
round. The next unused migration number is **044**: `db/043` belongs to the merged FDA-CYP review round.

## Reviewer accounts and first-run administration (2026-08-17) — `db/044`

Canonical design: [`2026-08-17-drugref-reviewer-user-management-design.md`](superpowers/specs/2026-08-17-drugref-reviewer-user-management-design.md).

**The GUI now has real account writes without moving the database trust boundary into the desktop app.** `reviewer-service/`
is the Axum/SQLx service; `reviewer-domain/` owns shared request/response types and validation; the Tauri core uses an HTTPS
client and retains bearer tokens in native memory. The WebView invokes narrow commands and still has no network capability.
Debug accepts loopback HTTP; release configuration requires HTTPS. The clinical queue remained the same read-only fixture
in this account round.

**First run is a database state, not a preference.** No account is seeded. Before loading the workspace, the app calls the
bootstrap-status endpoint. With no live administrator profile it renders first-admin registration and nothing beyond it.
The write takes a PostgreSQL advisory transaction lock, rechecks under the lock, forces `administrator` regardless of the
request body, and inserts account + profile + Argon2id credential + digest-only session in one transaction. Once any live
administrator profile exists, bootstrap returns conflict — including when that administrator is disabled, because disablement
must never reopen an unauthenticated privilege grant.

**`db/044` has seven objects and no seed:** stable/immutable `reviewer_account`; append-only, single-live `reviewer_profile`;
append-only, single-live `reviewer_password_credential`; append-only `reviewer_key_enrolment` corrections linked into the
existing signing registry; insert-only `auth_session` with a 32-byte SHA-256 token digest; insert-only
`auth_session_revocation`; and the insert-only role vocabulary. Creating a user is one service transaction. The GUI exposes
list/create to administrators; hiding the navigation item is only presentation, and the service checks the live role again.

**Authentication details:** Argon2id PHC hashes, the same external failure for missing user / wrong password / disabled user,
a real Argon2 sentinel verification on the missing-user path, 12-hour random sessions, per-address process-local login
limiting, and logout as an auditable revocation insert. An edge limiter is still required in production. The next admin work is
profile correction, disable/enable, password rotation, all-session revocation and signing-key enrolment UI over the schema
already landed here.

**Verification:** 16 db/044 schema tests; full Python/PostgreSQL suite **1,779 passed**; shared-domain, service and
Tauri unit suites **3 + 4 + 3 passed**; `ruff`, `cargo fmt --check`, `npm run check` (0 diagnostics), `npm run build`, and
`npm audit` (0 vulnerabilities). A local end-to-end service run observed bootstrap true → first administrator (request role
overridden) → second bootstrap HTTP 409 → password login → authenticated reviewer create/list. Native no-bundle build also
passed. Frontend output is 0.63 kB HTML + 17.54 kB CSS + 69.14 kB JS (24.75 kB gzipped). No in-app or connected browser was
available, so the new bootstrap/admin layouts still need the same desktop/narrow visual pass the foundation was missing.

## Reviewer live queue (2026-08-17) — no migration

Canonical design: [`2026-08-17-drugref-reviewer-live-queue-design.md`](superpowers/specs/2026-08-17-drugref-reviewer-live-queue-design.md).

**The installed app now reads the clinical queue from PostgreSQL without moving the trust boundary.** Any authenticated
reviewer may call `GET /v1/review-queue`; the Tauri core attaches its native-memory bearer token and forwards structured data
to the WebView. The endpoint validates bounded `page` / `pageSize`, kind, source, relationship and literal substring search.
The browser-only Vite adapter retains representative records for layout work, is labelled as a preview, and is never a native
fallback after a service error.

**One materialised SQL union owns the response snapshot.** Its interaction half reads the existing
`ci_rule_partner_reach` aggregate and applies current expansion policy instead of enumerating every candidate pair merely to
count it; the condition half retains its inexpensive gap view. Its reviewed-pair summary also sums the exact moiety- and
class-rule reach aggregates instead of expanding `curated_ddi_pair`. It then derives current totals, filter options, filtered
count and the deterministic impact/name/UUID-ordered page. On `drugref_reviewer_dev`, the authoritative interaction gap read
took 3.02 s and spilled a 387 MB temporary sort after producing 3.8 million intermediate rows; the equivalent reach-count
projection took 34.7 ms. The replacement reviewed-pair count took 32.9 ms. Both fast paths had zero row/count mismatches
against their authoritative views; the complete unfiltered 25-row queue query then ran in 87.5 ms. Sources, releases and
condition predicates remain arrays. No queue table, cache or migration was added.

**The GUI no longer invents state the database does not have.** The fixture-only `in_review`, signature and priority fields
are gone. A gap is **Unreviewed**, not **Unsigned**, because no curated row exists to sign. Search is debounced; filter changes
reset to page one; overlapping responses are sequenced so an old response cannot replace a newer one; an inline failure keeps
the last successful page visible. A failure immediately after authentication retains the native session and **Try again**
retries the workspace directly rather than sending the reviewer back through startup. All decision, annotation and signing
controls remain disabled.

**`drugref_test` is destructive test infrastructure, not a GUI database.** The PostgreSQL-backed pytest suite recreates its
schema and therefore removes reviewer accounts, credentials and sessions stored there. Run `reviewer-service` against a
separate migrated database with the desired queue content; the local persistent GUI database is `drugref_reviewer_dev`.

**Verification:** full PostgreSQL-backed Python suite **1,779 passed**; domain **6 passed**; service **5 passed + 1 populated-
database integration passed explicitly**; Tauri **1 passed**; `ruff`; Rust formatting; `npm run check` with 0 diagnostics;
production frontend build; `npm audit` with 0 vulnerabilities; native no-bundle build. The reference-database integration test
made two real queue requests (default 25-row page, then five condition rows) in **11.42 s** total, including the known expensive
interaction-gap read. No in-app or connected browser was available, so desktop/narrow visual verification is still outstanding.

## Reviewer annotations and evidence references (2026-08-17) — `db/045`

Canonical design: [`2026-08-17-drugref-reviewer-annotations-design.md`](superpowers/specs/2026-08-17-drugref-reviewer-annotations-design.md).

**The first clinical-adjacent write path records research, not a ruling.** Any authenticated reviewer can read a target's
working history and append an immutable Markdown note or a citation-only DOI/PMID/PMCID/NCT/SPL/URL reference. Authorship
comes from the native-memory bearer session, never a client-supplied reviewer field. The GUI displays attribution and history,
while question state, evidence verdicts, grades, clinical decision fields and signing remain absent or disabled.

**`db/045` adds two insert-only ledgers.** `reviewer_annotation` and `reviewer_evidence_reference` both point to the immortal
`open_question.question_uuid` and stable `reviewer_account.reviewer_uuid`. The evidence table intentionally has no verdict,
confidence, grade or applies column. `register_from_gaps` now retains a closed question carrying either kind of research row,
so a later ingest cannot erase or orphan working history.

**One canonical target key crosses the queue/write seam.** Queue items now expose the frozen registry shapes
`MOIETY:{uuid}/CLASS:{uuid}/CI_AXIS:{relationship}` and `MOIETY:{uuid}/CONDITION:{uuid}`. The service resolves that key through
the current registry row; it neither re-mints a UUID nor accepts a stale target silently. Tauri owns the three authenticated
working-record requests, and browser preview keeps isolated in-memory history solely for interaction/layout development.

**The new code stays on focused seams.** Shared working-record contracts live in `reviewer-domain/src/records.rs`, service
persistence in `reviewer-service/src/records.rs`, and GUI lifecycle/forms in `reviewer-app/src/WorkingRecords.svelte`; the
main domain and app files remain 490 and 349 lines respectively.

**Verified:** full PostgreSQL-backed Python suite **1,787 passed**; domain **8 passed**; service **8 passed + both live
queue and working-record integrations passed explicitly**; Tauri **1 passed**; Rust formatting and clippy with warnings denied; `ruff`;
`npm run check` with 0 diagnostics; production frontend build (0.63 kB HTML + 20.87 kB CSS + 78.81 kB JS, 27.77 kB JS
gzipped); `npm audit` with 0 vulnerabilities; native debug no-bundle build. Chrome desktop and 740 x 900
interaction/visual passes covered sign-in, target switching, target-scoped annotation/reference append, history rendering
and the disabled decision control. A 980 x 680 regression pass confirmed the page remains viewport-bound while queue and
detail panes independently scroll to their bottoms; the narrow pass caught and verified a compact single-column breakpoint.

**Next:** curated interaction and condition revision transactions. Local key enrolment/signing remains a separate later
slice. The administration tail is profile correction, disable/enable, password rotation, all-session revocation and
signing-key enrolment UI over `db/044`.

## Reviewer curated revisions (2026-08-18) — no migration

Canonical design: [`2026-08-18-drugref-reviewer-curated-revisions-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-curated-revisions-design.md).

**The GUI can now record clinical content without conflating authentication and signing.** Interaction targets admit
`applies` / `does_not_apply`; condition targets admit `contraindicated` / `indicated` / `context_dependent` / `spurious`.
Asserting decisions require severity and evidence grade, while retiring/spurious decisions require both absent. Mechanism
and management remain bounded optional prose. The GUI previews the exact immutable revision and predecessor before recording.

**The service owns every attribution and identity field.** It resolves the frozen target key through `open_question`, parses
the natural-key UUIDs itself, writes `reviewed_by` from the authenticated current profile and derives `reviewed_against` from
candidate ingest releases. A transaction-scoped advisory target lock plus `expectedRevisionId` rejects a stale form instead
of silently superseding another reviewer's decision. The existing append-only and deferred single-live triggers remain the
floor; `db/045` stays the latest migration.

**The response is database-derived history, not a success guess.** Both overlay tables project into one typed revision model,
including predecessor links and `curated_signature_status`. Tauri retains the bearer token; browser preview uses isolated
memory. Successful writes refresh the queue, while the footer keeps detached signing disabled and newly recorded rows visible
as unsigned.

**Verified:** full Python/PostgreSQL suite **1,787 passed**; reviewer-domain **10 passed**; reviewer-service **10 passed** plus
a live PostgreSQL interaction and condition initial-write, correction/history and stale-form integration; Tauri **1 passed**;
Rust formatting and clippy with warnings denied; `ruff`; Svelte check with 0 diagnostics; production frontend build (0.63 kB
HTML + 22.51 kB CSS + 86.32 kB JS, 29.91 kB JS gzipped); `npm audit` with 0 vulnerabilities; `git diff --check`. Chrome
passes at 1,440 x 900, 980 x 680 and 740 x 900 covered sign-in, target switching, both decision vocabularies,
ruling-dependent grade controls, immutable preview, record/history/unsigned rendering and disabled signing. The
intermediate document remained viewport-bound with independent queue/detail scrolling. The narrow pass caught a flex-item
height defect that overlaid the signing footer on clinical content; the corrected footer now follows the complete detail
content without overlap.

**Next:** local signing-key enrolment and the detached sign/verify/resume flow. The administration tail remains profile
correction, disable/enable, password rotation and all-session revocation.

## Reviewer detached signing (2026-08-18) — `db/046` catalog comment only

Canonical design: [`2026-08-18-drugref-reviewer-signing-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-signing-design.md).

**The private key stays behind a deliberately narrow native boundary.** The Tauri core integrates IOTA Stronghold directly,
using a per-reviewer encrypted snapshot under the application-local data directory; it does not register the generic plugin
commands that would let the WebView select vault procedures or arbitrary bytes. A separate signing-vault passphrase derives a
256-bit snapshot key with Argon2id. Only the Ed25519 public key, its SHA-256 fingerprint and confirmation metadata leave the
native core. Logout clears any prepared payload retained in native memory.

**Enrolment and clinical sign-off are separate authenticated transactions.** The service derives the fingerprint, records the
existing `reviewer_key_enrolment` against the authenticated reviewer, requires the current registry key to be active and makes
same-owner replay idempotent. Preparing a signature binds the frozen target key, current immutable revision, enrolled key,
server-issued microsecond timestamp, payload context and frozen field order. Native code independently encodes and hashes the
challenge before showing the digest; confirmation unlocks Stronghold and signs only those retained bytes. The service then
re-resolves the natural key, enforces a five-minute challenge lifetime, reconstructs the canonical payload, checks the digest,
verifies Ed25519 and appends `assertion_signature` before returning database-derived history.

**A digest is not human review.** The first confirmation surface showed only the revision heading, context, field count,
digest and key even though native code had already validated all canonical named values. The preview contract now copies
those values—not the encoded byte buffer—after validation and renders every field in frozen order. Mechanism, management and
release provenance are untruncated full-width text; every other identifier, ruling, attribution, fingerprint and timestamp is
visible, and SQL NULL is explicit. Native code still signs only the retained bytes whose recomputed digest the preview names.

**Queue retirement no longer strands an unsigned row.** Successful clinical recording still refreshes the unresolved queue,
so a separate `GET /v1/pending-signatures` union lists current interaction and condition revisions having no signature. The
GUI can resume their complete history and two-step sign-off after refresh or restart; a verified row disappears from that
list. The browser preview remains isolated in memory and is never a native fallback.

**No table shape moved.** `db/046` corrects `signing_key`'s catalog comment: `db/030` accurately said no enrolment protocol
existed then, but the reviewer service now makes an authenticated active account session the authority for initial public-key
registration and enrolment. Account-session possession and local-private-key possession are separate requirements. Key-status
administration, recovery/export, release-manifest signing, profile correction, disable/enable, password rotation and
all-session revocation remain separate work.

**Verified:** canonical Rust encoding matches every committed Python signing vector; the full Python/PostgreSQL suite is
**1,790 passed**; reviewer-domain **13 passed**; reviewer-service **12 passed + 4 ignored**, with the detached-signing live
PostgreSQL round trip passed explicitly; native **3 passed**, including Stronghold restart/wrong-passphrase/signature
verification; Rust formatting and clippy with warnings denied; `ruff`; Svelte check with 0 diagnostics; production frontend
build (0.63 kB HTML + 26.13 kB CSS + 99.11 kB JS, 33.63 kB JS gzipped); native debug no-bundle build; npm audit with 0
vulnerabilities; `git diff --check`. Chrome passed at 1,440 x 900, 980 x 680 and 740 x 900 across public key status,
resumable unsigned decisions, exact confirmation, simulated signing and pending-list retirement, with no horizontal overflow
or console warnings. The pass caught and verified browser-preview target labels that had fallen back to a generic title and
raw target key.

## Reviewer signing-key replacement (2026-08-18) — no migration

Canonical design: [`2026-08-18-drugref-reviewer-key-replacement-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-key-replacement-design.md).

**A lost passphrase does not justify breaking the append-only registry.** Hard deletion is already forbidden by both
`signing_key` and `reviewer_key_enrolment` triggers. `DELETE /v1/signing-keys/current` therefore names the authenticated
resource operation, not a physical row deletion: under a fingerprint advisory lock it derives the owned current row,
counts all signatures, appends a `rotated` key correction and an unenrolled account correction, then supersedes both prior
rows in one transaction. Holder, algorithm and public bytes come from PostgreSQL, never the client. A second call reads the
already-withdrawn enrolment and returns the same count, making the database/filesystem boundary retryable.

**The WebView chooses no identity or path.** Native code reads the authenticated reviewer's fixed public fingerprint,
requests the audited rotation, then deletes that reviewer's `.hold`, `.salt` and `.fingerprint` files in that order and
clears any pending signature. The public fingerprint is last so a partial cleanup can retry. No passphrase is required:
this is specifically the recovery path when it is unavailable. The two-step GUI names the signature count before deletion;
zero means no clinical record changes, while a used key's earlier signatures remain valid under `rotated`'s time-scoped
rule. The sign confirmation field also disables account-password autocomplete rather than inviting the wrong credential.

**Verified:** full Python/PostgreSQL suite **1,790 passed**; reviewer-domain **14 passed**; reviewer-service **12 passed +
4 ignored**, with the live PostgreSQL flow explicitly covering cross-reviewer refusal, zero-signature replacement,
idempotent retry and one-signature rotation; native **5 passed** including exact-field confirmation projection, fixed-path cleanup and unrelated-file retention;
Rust formatting and clippy with warnings denied; `ruff`; Svelte check with 0 diagnostics; production frontend build; npm
audit with 0 vulnerabilities; debug macOS app bundle with strict code-sign verification; `git diff --check`. Complete
15-field signing confirmation passed at 1,440 x 900 and 740 x 900 with no horizontal clipping or console warnings.

## Reviewer account administration (2026-08-18) — no migration

Canonical design: [`2026-08-18-drugref-reviewer-account-administration-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-account-administration-design.md).

**The `db/044` administration tail is complete without changing its schema.** Administrators can append a complete profile
correction, disable or re-enable access, rotate an Argon2id password, and revoke every live session. The account projection
now carries the current profile revision and live-session count; profile writes require the observed revision and return 409
on a stale form. Disablement and credential rotation append reason-specific session revocations in their own transaction.
Stable usernames, clinical records, signatures and signing-key history do not move.

**Authority is rechecked where the write occurs.** Administration mutations serialize under one transaction advisory lock,
re-read the acting account's current active-administrator profile, and refuse to disable or demote the last active
administrator. Session creation takes the same lock, rechecks enabled status and requires the credential revision whose hash
the login path verified. That last comparison closes the race where an old password could otherwise finish authentication
after a concurrent rotation committed. Re-enabling an account never revives a revoked bearer token.

**The WebView still receives no bearer token, hash or session secret.** Narrow Tauri commands forward typed profile,
password and session actions; self-disable, self-rotation and self-revocation clear the native session and prepared signing
payload before returning the GUI to sign-in. The administrator view provides selectable reviewers, immutable usernames,
complete-profile preview, separate danger confirmations and database-derived result messages. Browser preview mirrors the
flow in isolated memory only.

**Verified:** full Python/PostgreSQL suite **1,790 passed**; reviewer-domain **16 passed**; reviewer-service **12 passed +
5 ignored**, with the clean live PostgreSQL account round trip passed explicitly; native **5 passed**; Rust formatting and
clippy with warnings denied; `ruff`; Svelte check with 0 diagnostics; production frontend build (0.63 kB HTML + 30.45 kB
CSS + 114.51 kB JS, 38.01 kB JS gzipped); npm audit with 0 vulnerabilities; native debug no-bundle build; `git diff
--check`. Chrome at 1,440 x 900 and 740 x 900 covered creation, correction, disable/re-enable, last-admin refusal, password
rotation and self-session revocation with no horizontal overflow or console warnings. Next: design general reviewer/key
administration, revocation queues and counter-signing policy; the owned lost-passphrase path remains deliberately narrower.

## Reviewer key trust administration (2026-08-19) — `db/047`

Canonical design: [`2026-08-19-drugref-reviewer-key-trust-design.md`](superpowers/specs/2026-08-19-drugref-reviewer-key-trust-design.md).

**The remaining public-key trust round is complete.** Administrators can inspect every current registry key with reviewer
ownership, status boundaries, all-signature and current-revision counts, then append an explicit `retired` or `compromised`
correction. The service rechecks current administrator authority inside the transaction, serializes by fingerprint, copies
public bytes from the live registry row and withdraws a live enrolment. Retirement is allowed only from active and remains
time-scoped; compromise can escalate a rotated or retired key retrospectively and can never be downgraded.

**Pending now means no registry-unobjected signature, not no signature row.** An unsigned current GUI revision is labelled
`unsigned`; a revision carrying only compromised, expired or unknown-key attestations returns as
`needs_counter_signature`, with the number of objected rows. One independent unobjected counter-signature removes it from
the queue even when the compromised signature remains in immutable history. This reuses `curated_signature_status`'s
registry policy and does not pretend PostgreSQL verified Ed25519. Clinical rows remain served throughout.

**`db/047` closes issue #85's schema hole.** `signing_key_status_kind` is now INSERT-only, so one UPDATE cannot disarm every
historical compromise verdict. `signature_target_kind` deliberately remains mutable for `/v2` context migrations. The
WebView receives only public fingerprints, identity, timestamps and aggregate impact; bearer tokens, private bytes and
canonical payload buffers stay behind their existing native/service boundaries. General administration never deletes local
files. Administering the signed-in reviewer's own key clears any native prepared payload. A reviewer whose administrator
acted first can still run fixed-path device cleanup idempotently, and the result now reports the actual registry status
instead of misdescribing compromise as rotation.

**Verified:** full Python/PostgreSQL suite **1,792 passed**; reviewer-domain **17 passed**; reviewer-service **12 passed +
5 ignored**, with the live PostgreSQL signing lifecycle explicitly covering retirement, retrospective compromise,
counter-sign queue entry, clean counter-sign recovery and post-administration device cleanup; native **5 passed**; Rust
formatting and clippy with warnings denied; `ruff`; Svelte check with 0 diagnostics; production frontend build (0.63 kB
HTML + 33.24 kB CSS + 124.07 kB JS, 40.56 kB JS gzipped); npm audit with 0 vulnerabilities; native debug no-bundle build;
`git diff --check`. Chrome at 1,440 x 900 and 740 x 900 covered key selection, compromise confirmation/result and narrow
layout with no horizontal overflow or console warnings. Issue #86's separate compatibility round is recorded immediately
below.

## Reviewer GUI finalization (2026-08-20) - `db/048`

Canonical design: [`GUI finalization`](superpowers/specs/2026-08-20-drugref-reviewer-gui-finalization-design.md).

**Issue #86's published-vocabulary widening is built.** `curated_signature_status` now counts unknown fingerprints
separately and publishes `signed_by_unknown_key` when every signature is objected and at least one key was never
registered.
One registry-unobjected signature still wins; otherwise unknown outranks revoked. The read views continue to left-join
status, so no key event removes a clinical row, and `drugref verify` retains the six detailed verdicts.

The reviewer domain and WebView use closed four-value signature-status types. Unexpected database vocabulary fails
inside
the service instead of entering the GUI, while revision history and signing controls render readable labels and visibly
warn on objected states. The signing control now admits objected current revisions as explicit counter-signatures
instead
of listing them in Pending signatures while disabling the only completion action. The disabled global Evidence library
placeholder is gone; citations remain available in the target-scoped immutable working record that owns their clinical
context.

**Verified:** full Python/PostgreSQL suite **1,794 passed**; reviewer-domain **18 passed**; reviewer-service
**12 passed + 5 ignored**, with the live detached-signing/counter-sign lifecycle passed explicitly; native **5 passed**;
Rust formatting and clippy with warnings denied; `ruff`; Svelte check with 0 diagnostics; production frontend build
(0.63 kB HTML + 33.35 kB CSS + 124.96 kB JS, 40.82 kB JS gzipped); npm audit with 0 vulnerabilities; strict MkDocs;
`git diff --check`; debug macOS
app bundle build and strict ad-hoc signature verification. Browser preview exercised initial signing, compromise and the
resulting counter-sign state at 1,440 x 900, 740 x 900 and 520 x 900 with no horizontal overflow or console warnings.
The
native `.app` launched against the persistent reviewer service after migrations through `db/048` and remained running.

## The DrugCentral re-measurement (2026-08-23) — no migration

Canonical output, regenerated by the tool rather than transcribed:
[`drugcentral-ddi-remeasurement-results`](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-remeasurement-results.md).
**This round writes no SQL, admits no source and ships no ingest.** It exists because issue #101 asked a future
session to act on figures that § "Which of these figures can be RE-DERIVED" had already classified as
un-re-derivable, and HANDOVER told this session to re-measure before designing.

### What it is, and how to re-run it

`downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz`, **1,400,714,190 bytes**, SHA-256
**`055904d152d6c8eef4ee872b25f6476019682df8b5f49bcdf7cc018204f3e04f`** — the anchor the 2026-08-13 run never
recorded, and the reason a later session can prove it measured the same bytes — the digest is now written into
the extract's `manifest.json` and **compared before a cached extract is trusted**, because a warm cache plus a
new `--dump` used to print the new digest above the old figures. It decompresses to **4,977,218,576 characters
over 13,570,317 lines** (counted during the pass, not quoted from a terminal) and one streaming pass takes
**~14 s**.

```bash
# ~132 s: a registry to join against. The DSN must NOT be drugref_test (pytest recreates it).
uv run drugref --dsn "$DSN" migrate && uv run drugref --dsn "$DSN" ingest chain --downloads downloads \
    --unii-release 26Feb2026 --medrt-release 2026.07.06 --mesh-release 2026 \
    --mesh-relations-release 2026.07.06 --gsrs-release 2026-02-26
uv run python -m tools.drugcentral_ddi_spike --dump downloads/DRUGCENTRAL/drugcentral.dump.11012023.sql.gz \
    --dsn "$DSN" --out docs/superpowers/specs/2026-08-23-drugref-drugcentral-ddi-remeasurement-results.md
```

Five modules, all pure except the runner: `src/drugref/ingest/drugcentral_dump.py` (a streaming COPY-block
reader), `src/drugref/ingest/drugcentral_resolve.py` (the endpoint cascade) — **both promoted out of `tools/`
by the ddi-ingest round**, because what a `ddi` row means must not live in two homes; and
`tools/drugcentral_cache.py` (the extract cache),
`tools/drugcentral_ddi_measure.py` (the arithmetic) and `tools/drugcentral_ddi_report.py` (rendering, which takes
every figure through a typed `ReportContext` so it cannot consult the dump behind the report's back).
**94 tests**, none of which need the dump or a database.

### ⇒ THE FINDING: resolve on STRUCTURE, and the slice gets bigger, not smaller

Issue #101 matched endpoints against `substance_moiety.display_name`, reached 857 of 924 NDF-RT endpoint names,
and concluded the ~87 INN spellings *"need a synonym bridge"* — a hand-maintained list someone owns forever.
**They do not.** DrugCentral resolves its own free text against its own tables (**905** of 924 names are a
`structures.name`, **13** more a `synonyms.name`, leaving **6**), and a `structures` row carries an **InChIKey**
and a **CAS** number that drugref already holds as live `identity_claim` rows (**16,046** and **19,010**). Keying
on the structure a name denotes rather than on the spelling is **principle 2 applied to the resolution step**.
The split is computed by the tool (`name_provenance`) rather than transcribed; the first published version of
this line said *17 more, leaving 2*, which is correction 7 below.

Cascade `display_name` → `inchikey` → `cas`, on the bundleable `ddi_ref_id = 2` subset (7,571 rows):

| measure | name matching (#101) | + structural cascade | delta |
|---|---:|---:|---:|
| endpoint names resolved | 857 | **914** | +57 |
| rows with an unresolvable endpoint | 598 | **37** | **−561** |
| distinct unordered moiety pairs | 6,941 | **7,501** | +560 |
| pairs drugref already holds | 604 | 635 | +31 |
| **pairs that are NEW** | **6,337** | **6,866** | **+529** |

Row accounting, which the first version of this table could not state: 7,571 rows = 37 unresolvable + **0
self-pair** + 7,534 pair-yielding, and those 7,534 rows collapse to 7,501 distinct pairs — so the 33-row
difference is **duplicate pairs, not self-pairs**. `self_pair_rows` was computed and rendered nowhere until the
review round; `Measurement` now refuses to exist unless the three buckets sum to the row count.

Routes: `display_name` 857 · `inchikey` 47 · `cas` 10 · unresolved 10. **CAS is deliberately last** — an
InChIKey denotes a structure exactly, while a CAS number is an administrative identifier upstream sources reuse
loosely across hydrates and salt forms. The 10 residual names are readable and mostly biologics, mixtures and
neuromuscular blockers with no single structure (`Vitamin E`, `heparin`-shaped cases, `atracurium`,
`mivacurium`, `doxacurium`, `sodium polystyrene sulfonate`); they are the composition tree's problem, not a
synonym list's. **A blank structural key is never looked up** — `structures` stores an empty InChIKey for
biologics, and a registry that happened to hold `""` would collapse every keyless substance onto one moiety.
That guard has its own test.

### What reproduced, what did not

**Reproduced EXACTLY**: 7,621 rows · 970 endpoint names · 860 `display_name` matches · 6,973 moiety × moiety ·
648/598 unresolvable (difference exactly 50) · 6,941 distinct pairs · 604 held (8.7%) · **6,337 new** ·
`pharma_class` 25,687 with `QT` zero times · `ddi_candidate_pair` 21,664 rows / 20,238 distinct pairs · and the
**three `reference` rows `ddi` actually cites** (the table itself holds **1,195**), re-read rather than
inferred: `2` = VHA NDF-RT (7,571, clean), `1` =
Stockley's, Karen Baxter, ISBN 0853699143, 2010 (13, out), `3` = Lexicomp Online, Wolters Kluwer (37, out).
Licence re-confirmed at `drugcentral.org/privacy`: **CC BY-SA 4.0**, legalcode linked.

**Did NOT reproduce, and are corrected in place above:**

1. **"8 match a MED-RT class name" → 4, and they are MeSH, not MED-RT** (`Monoamine Oxidase Inhibitors`,
   `Phosphodiesterase 5 Inhibitors`, `Proton Pump Inhibitors`, `Selective Serotonin Reuptake Inhibitors`).
   Wrong in its number and in its authority. **Checked against `drugref_db034` — the era the original run used —
   so it is not schema drift**; it was simply wrong.
2. **"102 match neither" → 106.** Follows from (1); 860 + 4 + 106 = 970.
3. **"7,000 of 7,621 (91.9%) keyable" → 6,991 (91.7%)**, and the *"27-row difference"* between keyable and
   moiety × moiety → **18**.
4. **`MAOIs or RIMAs` is not an ENDPOINT** — `drug_class1`/`drug_class2` carry the letters `MAOI` **zero** times
   over all 7,621 rows, and the endpoint string the original example was reaching for is
   `Monoamine Oxidase Inhibitors`.
   **⇒ AS FIRST PUBLISHED THIS CORRECTION READ "`MAOIs or RIMAs` does not exist in the table", AND THAT IS ITSELF
   WRONG.** Found by the ingest round on 2026-08-23 and corrected here in place. The string occurs on **10** rows
   in `ddi.source_id`, and the shorter `MAOIs` on **3** more — **all 13 of them `ddi_ref_id = 1`**, every
   Stockley's row and nothing else. The correction had checked the endpoint columns and then generalised to the
   whole table without checking the remaining ones. **Decision impact: none** — all 13 rows sit in the half rule 6
   excludes and none of them yields a resolvable pair, so nothing that was decided on this basis changes.
   **Credibility impact is the whole point:** this is the correction round's own results file being wrong in
   exactly the way the round exists to prevent, and the scope of a sentence is a figure like any other. Full
   account: § "The DrugCentral ddi ingest".
5. **The QT class strings, transcribed at last**: `High Risk QT Prolonging Agents` /
   `Moderate Risk QT Prolonging Agents` — and **all three QT rows are `ddi_ref_id = 3`**, already excluded.
6. **The endpoint provenance split → 905 / 13 / 6**, not 905 / 17 / 2. The `structures.name` half reproduced
   exactly; the synonym half did not, and the residue is 6 rather than 2. Now computed by
   `drugcentral_ddi_measure.name_provenance` over the bundleable 924 rather than transcribed.
7. **The staleness cost stands, re-checked at source**: `drugcentral.org/download` still offers only
   **11/01/2023**, so the dump is now ~2 years 10 months old with no successor. One discrepancy worth knowing:
   the page advertises *Postgres v14.5* while the dump's own header says **"Dumped from database version
   10.11"**.

### What the code review of this branch changed, and why it belongs in this section

The instrument was reviewed before the branch merged, and the review found that **the parser's strictness did not
survive the cache underneath it** — which is the same failure this whole round exists to prevent, one layer down.
Fixed on the branch; the results file was regenerated end to end rather than edited.

- **The cache was committed by `ddi.tsv` merely existing.** A `CopyFormatError` mid-extract left well-formed but
  truncated TSVs behind, and the natural reaction to a traceback — re-run the command — printed *"using cached
  extract"* and measured the wreckage. And nothing tied the files to the dump, so a warm cache plus a new
  `--dump` printed the new SHA-256 above the old figures: **worse than recording no digest**, because the digest
  is what invites a reader to trust the table it sits in. There is now a manifest written last and validated
  first, and `extract` builds into a sibling directory and renames it into place.
- **`csv.DictWriter` invented columns and `csv.DictReader` invented rows.** A projected column the dump did not
  declare was written blank with no error — so a renamed `structures.inchikey` would have reduced the cascade to
  name matching and reproduced #101's own 857/598 as if they were the new measurement. A short TSV row was padded
  with `None`. Both now raise.
- **The registry lookups had no `ORDER BY`, and the collisions are real: 14 InChIKeys and 29 CAS numbers are
  claimed by more than one moiety.** `identity_claim` is unique on `(moiety_uuid, scheme, value)` and
  deliberately not across moieties, so "pairs that are NEW" could have differed between two runs over the same
  bytes — in the round whose entire justification is reproducibility. `classes.py` had already written this rule
  down for the same join; the spike did not follow it. Reads are now `REPEATABLE READ` and ordered.
- **The rule-6 verdict had a second home.** The renderer decided what to PRINT with a hard-coded `ref_id == "2"`
  while `BUNDLEABLE_REF_IDS` decided which rows were COUNTED. The set is passed in now.
- **A report over no evidence rendered cleanly and exited 0**, bold bundling recommendation included. It refuses.
- **The QT descriptions are withheld for rows rule 6 excludes.** All three cite Lexicomp; reproducing a
  commercial compendium's sentences verbatim into a committed AGPL repo on every run should not be a side effect
  of that section having had no reference filter. The endpoint strings — what issue 93 actually needs — stay.
- **Five figures this section had filed under "re-derivable" were not computed by the tool.** The class residue
  and its authority, 106, 6,991, 6,973 and the provenance split are now all computed; the report prints
  keyability under **both** resolvers, because #101's 7,000 was a name-matching figure and setting it beside a
  cascade number compares two different questions.

### The three lessons, none of them new to this project

- **A figure nobody can re-derive decays silently.** Seven of these were wrong and every one had been quoted
  forward into ROADMAP, an issue and this file. The instrument cost an afternoon; the figures had stood for ten
  days across three documents. The review then found five MORE figures that this section had already relabelled
  *re-derivable* while nothing in `tools/` computed them — relabelling is not the same act as instrumenting.
- **The example strings are where paraphrase hides.** `MAOIs or RIMAs` and the `High/Moderate` token order were
  both plausible, both wrong, and both survived because nobody could open the source. This file had already
  flagged the second one as *proof that neither was transcribed* — and was right. **⇒ AND THE CORRECTION TO THE
  FIRST ONE WAS ITSELF WRONG** (correction 4 above, fixed 2026-08-23 by the ingest round): it checked the two
  endpoint columns, concluded "not in the table", and never looked at `source_id`, where the string sits on 10
  rows. A correction is a figure, and one that quietly widened its own scope on the way to being published decays
  the same way the claim it replaced did.
- **Measuring before designing changed the design.** The synonym bridge #101 planned for is not needed; the
  slice is 6,866 new pairs rather than 6,337; and the QT gap is not merely un-closed but sits in the
  licence-excluded half.

## The DrugCentral ddi ingest (2026-08-23) — `db/049` + `db/050`, issue #101

Design, approved and followed:
[`drugcentral-ddi-ingest-design`](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-design.md).
Measured end to end on the real release:
[`drugcentral-ddi-ingest-measurement`](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-ingest-measurement.md).
Published record of the one decision worth publishing: [the candidate tier carries an upstream
severity](https://docs.drugref.org/decisions/upstream-severity-is-data/). The re-measurement section above is
the round this one rests on — **every figure below was re-derived here rather than quoted forward from it**,
which is why §8 of the measurement can say 12 predictions and 12 matches instead of asserting agreement.

### What shipped

**`db/049_drugcentral_ddi.sql`, in six sections**, each of which is a decision rather than a step:

1. **The source vocabulary, as a TRIO in one commit.** `ingest_run_source` gains `'DRUGCENTRAL'` and
   `ingest_run_writer` gains `'drugcentral_run'`, both **copied verbatim off the live catalog and then
   extended by one** rather than retyped from the design; `src/drugref/ids.py` gains
   `"DRUGCENTRAL": "DRUGCENTRAL"` and `src/drugref/provenance.py` gains the writer. The failure mode when one
   of the three lands without the others is silent: `ids.canonical_source` folds the source to a spelling the
   CHECK does not admit, and a per-source rebuild then deletes nothing and reports success.
2. **`ddi_source_severity`** — `(source, source_label) → severity`, a seeded two-row table, FK'd into
   `severity_kind`. §"The four decisions" below is why it is a table.
3. **`drugcentral_ddi_assertion`** — every bundleable row exactly as published: `upstream_key`, both endpoint
   names, `upstream_label`, `severity_label`, two nullable moiety UUIDs and two route labels, with a severity
   FK, two route CHECKs and two completeness CHECKs.
4. **`drugcentral_ddi_pair`** — one row per unordered pair, `DISTINCT ON (moiety_lo, moiety_hi)` ordered by
   `severity_rank` then `upstream_key`. Most-severe-wins, stated once, in SQL.
5. **`exact_ddi_pair`** — the read path exact pairs have never had. Arm 1 is `moiety_contraindication`
   (MED-RT, directional, ungraded); arm 2 is `drugcentral_ddi_pair` (unordered, graded). `UNION ALL`, not
   `UNION`.
6. **`gap_unresolved_ddi_endpoint` and the EIGHTEENTH question kind**, `unresolved_ddi_endpoint` — a view over
   the assertion table, no worklist table of its own.

**Code.** `tools/drugcentral_dump.py` and `tools/drugcentral_resolve.py` were **promoted into
`src/drugref/ingest/`, with no re-export shims**, so the instrument that produced the re-measurement's figures
and the ingest that acts on them are the same code — a shim would have been a third home. `tools/` now imports
them from `drugref.ingest`. New: `ingest/drugcentral.py` (pure — the reference guard, the rule-6 filter, row →
`AssertionRecord`), `ingest/drugcentral_run.py` (the orchestrator, the only writer, owner of the transaction),
`interactions.py`'s `DRUGCENTRAL_TABLES` / `clear_source_drugcentral` / `add_drugcentral_assertion`, and
`cli_drugcentral.py` for `drugref ingest drugcentral --release 11012023 --dump …`. **A standalone subcommand,
NOT a chain step** — following FDA-CYP rather than ONCHIGH, because the dump is 1.4 GB and pinned to a single
2023 release while `chain` is the routine rebuild-everything path. The data dependency is documented on the
subcommand: the cascade needs slice-1 identity claims, so `unii` (and `chebi`, for InChIKeys) must have run.
`ingest_run.source_checksum` receives the dump's SHA-256 from the existing `ingest/checksum.py`, so the anchor
the 2026-08-13 run never recorded is now captured on every run for free.

**Fixture.** `tests/fixtures/drugcentral_ddi_subset.sql.gz`, built by
`tests/fixtures/make_drugcentral_subset.py` (where every other subset generator lives — the design spec said
`tools/` and was corrected). It carries rows from **all three** references, so the rule-6 filter and the
reference-identity guard are exercised against a dump that really contains what it excludes, and the excluded
references' `description` text is redacted. The suite grew by **71 tests** over this round, and by **3 more**
over the final whole-branch review's fix round on the same branch — deltas, not totals: the number itself has
exactly one home, § "How to run / test", and this section deliberately does not restate it.

### ⇒ TWO MEASUREMENTS THIS ROUND ADDED, AND BOTH CHANGED THE DESIGN

Neither is in the re-measurement; both were taken off the cached extract of the recorded dump before the
design was written, and each one decided a section of it.

**1. The `description` column carries no clinical content — all 7,571 of 7,571.** Every one matches
`NAME1/NAME2 [VA Drug Interaction]`: 35 characters at the shortest, 75 at the longest, mean 46, every one
containing the `/`. Issue #101's *"every row carries a description"* is **true and empty** — no mechanism, no
management, no prose of any kind. **What DrugCentral adds over a bare pair list is one severity band and
nothing else**, which is why the whole design budget went to the band and none to text extraction. The label
is stored anyway, for one reason: it names the endpoints at **product/salt** grain (`INDINAVIR SULFATE`,
`PIOGLITAZONE HCL`) while `drug_class1`/`drug_class2` carry the base, and that is the only visible explanation
for measurement 2.

**2. The source asserts an UNORDERED pair, and 4 pairs disagree with themselves.** Over the 7,571 bundleable
rows: 7,571 distinct **ordered** endpoint pairs, **0** appearing more than once; 7,538 distinct **unordered**
pairs; **33** present in both orders, all 33 with a different `description`, **4** with a different `ddi_risk`.
The two orientations are two VA entries at different salt grains folded onto one base-name pair — so
orientation carries no meaning here, and the 33 are not noise to drop but a genuine intra-source disagreement
that has to resolve **deterministically**.

**⇒ MEASUREMENT 2 IS WHAT RULES OUT THE SMALLEST MIGRATION.** Widening `moiety_contraindication` was the
obvious shape — the table already exists for exact pairs — and `db/014` documents it as *"DIRECTIONAL: the
subject is the drug the statement is ABOUT, and swapping the columns changes the meaning."* DrugCentral asserts
no subject. Storing it there would have **fabricated an orientation, 7,534 rows of it**, and that is a
different claim from the one the source makes.

### The four decisions, and what each one rejected

**1. A storage table plus views, not a widened `moiety_contraindication` and not db/031's two-table shape.**
Rejected, in order: widening `moiety_contraindication` on the directionality above *and* on severity (that
table's comment says the candidate tier carries no grade, and its `relationship` CHECK admits only
`CI_ChemClass`, a MED-RT predicate DrugCentral does not assert). Reproducing db/031's ONCHIGH shape — a
canonical pair table plus an `ingest_unresolved_ddi_endpoint` table — rejected because db/031 needed that
second table only for endpoints that were **in no table at all**, whereas here the assertion table already
holds every row; and because it would have resolved the 33 duplicates at **write** time in Python, discarding
the losing row and putting the precedence rule in code, against db/037's standing instruction that the rule
choosing between two grades is stated once, in SQL.

**2. The severity mapping is DATA, in `ddi_source_severity`, not four lines of Python.** db/006's finding one
tier up: a vocabulary written in code and in a CHECK is two lists to widen and one way to disagree. And this
one is additionally a **clinical judgement drugref makes on a consumer's behalf**, so a node operator must be
able to `SELECT` it, disagree with it, and see exactly what it did; revising it is then a migration over two
rows rather than a re-ingest of 7,571. Rejected: mapping the band in the parser and storing only drugref's
grade (the upstream label becomes unrecoverable without re-reading a 1.4 GB dump, and a clinical judgement
lives where no query can show it); and storing no severity at all, matching MED-RT exactly (it discards the
only thing this source adds, for 7,501 pairs no curator will reach in any foreseeable round). The mapping
itself follows VA/NDF-RT's own semantics — *Critical = avoid the combination* → `contraindicated`,
*Significant = may have clinical consequences; monitor or adjust* → `moderate`. **`major` deliberately carries
no DrugCentral row**: a two-band authority has two bands, and spreading them over three grades would invent a
distinction VA does not draw. The cost is stated rather than hidden — some `Significant` pairs
(`fluvoxamine + tapentadol`, `apixaban + heparin`) are arguably major and are graded a notch low, which is what
the curated overlay corrects one pair at a time and what makes the mapping's revisability load-bearing.

**3. `upstream_key` is `ddi.source_id`, not `ddi.id`.** All 7,571 bundleable rows carry a distinct `source_id`
(`'C56^4966^'`), so it is a valid key, and it is the upstream **authority's** identifier rather than an artifact
of one dump's row numbering. A dump row number inside a key a `question_uuid` could ever be built from would
not survive a re-publication.

**4. `ddi_candidate_pair` is NOT touched, and `exact_ddi_pair` is additive.** The harm-direction argument for
unioning into the view consumers already know is real — a consumer who queries it gets silence on a pair
DrugCentral grades `contraindicated`. It is outweighed by two measured facts: **db/034 measured this exact move
costing 3.6× with the new arm EMPTY**, a structural cost paid by every existing consumer on every query; and
`ddi_candidate_pair`'s columns are class-expansion-shaped (`via_class`, `member_class`, `is_direct`), all
meaningless at moiety grain, so unioning would mean 7,501 rows of NULL in three columns — not a read path, a
second vocabulary hiding in a view. The cost of the decision, stated plainly: a consumer must learn one view
name. That is why it is in the published decision record rather than only here.

### Measured on `drugref_dc049` (`TEMPLATE drugref_dc101` + `drugref migrate`, the eighth round running)

Full transcript, commands and plans: the measurement spec. **12 predictions, 12 MATCHED, 0 mismatched, no code
defects.** Two record defects were found on review of the measurement itself and fixed there (an unearned
"reconciled" claim, and a plan-shape claim that pointed at a plan instead of quoting both).

| | |
|---|---|
| dump | `drugcentral.dump.11012023.sql.gz`, 1,400,714,190 bytes, SHA-256 `0559…3e04f` — recorded on `ingest_run.source_checksum` |
| wall-clock | **20.183 s** (15.53 s user), of which the DB transaction is **~1.5 ms** — the rest is the pure streaming parser, before any connection opens |
| rows read | **7,621** · excluded by rule 6 **50** (13 Stockley's + 37 Lexicomp) · bundleable **7,571** |
| bundleable split | resolved **7,534** + self-pair **0** + unresolved **37** = 7,571, and `DrugCentralSummary` refuses to exist otherwise |
| pairs | **7,501** distinct unordered · **43** colliding registry keys (14 InChIKey + 29 CAS, already known and ordered) |
| routes (per row, endpoint 1) | `display_name` **7,233** · `inchikey` **297** · `cas` **21** · `unresolved` **20** |
| severity, PRE-collapse (`drugcentral_ddi_assertion.severity_label`) | `Critical` **2,307** · `Significant` **5,264** |
| severity, POST-collapse (`drugcentral_ddi_pair.severity`) | `contraindicated` **2,294** · `moderate` **5,207** |
| `exact_ddi_pair` | `MED-RT` **1,442** (unchanged — this ingest never writes `moiety_contraindication`) · `DRUGCENTRAL` **7,501** |
| gap kind 18 | **10** rows, `row_count` summing to **37** · `open_question` **21,842 → 21,852**, +10 and −0 at ROW level |
| must not move | `ddi_candidate_pair` **21,664** before and after · `substance_moiety` **19,438** before and after |
| hot path | `ddi_candidate_pair`'s plan on `drugref_dc049` vs `drugref_dc101` (`db/048`): **`diff` exit 0** once `actual time`/`Buffers`/`Planning Time`/`Execution Time` are blanked |

**⇒ THE 70-ROW GAP BETWEEN 7,571 AND 7,501 HAS TWO DISJOINT CAUSES, AND THE ARITHMETIC CLOSES.** 37 rows never
reach the pair view (2 `Critical` + 35 `Significant`), and 33 are the second orientation of a both-order pair,
removed by the collapse (11 `Critical`-only pairs + 18 `Significant`-only + 4 conflicting). By label:
`Critical` 2 + 11 + 0 = 13, `Significant` 35 + 18 + 4 = 57, and 13 + 57 = 70. **This is the trap that bit the
measurement's own first draft**: `severity_label` on the assertion table and `severity` on the pair view have
**different denominators**, so a figure quoted from one against the other looks like a defect and is not.

**⇒ THE HEADLINE REAL-DATA FINDING: the 4 self-disagreeing pairs, located and named for the first time.**
`db/049`'s comment predicted them; nobody had opened them. `atazanavir + atorvastatin`,
`atazanavir + rifapentine`, `gemfibrozil + pioglitazone`, `gatifloxacin + pioglitazone` — in each case one
orientation is published at the salt grain and the other at the base
(`ATAZANAVIR SO4/ATORVASTATIN CALCIUM` → `Critical` vs `ATAZANAVIR/ATORVASTATIN CALCIUM` → `Significant`), and
all four land `contraindicated` under most-severe-wins. **Real NDF-RT content disagreeing with itself across
the two directions it published the same pair in — not a drugref defect**, and the first time the rule was
observed doing what its comment says rather than what a hypothetical predicted.

The 10 unresolved endpoint names, each on route `unresolved` (DrugCentral holds a structural key and drugref
does not — so an answer could change something, which is db/012's test for whether the gate may ask at all):
`aluminium chlorohydrate` 2 · `amyl nitrite` 1 · `atracurium` 7 · `doxacurium` 7 · `glycopyrronium bromide` 2 ·
`mivacurium` 7 · `pentosan polysulfate` 1 · `phytomenadione` 4 · `sodium polystyrene sulfonate` 2 ·
`vitamin e` 4.

### ⇒ FOUR PUBLISHED FIGURES WERE WRONG, AND THREE OF THEM WERE THIS PROJECT'S OWN STATE FILES

All four are corrected in place in the sections they belong to; they are listed together here because the
pattern is the finding.

1. **The gap kind is the EIGHTEENTH, not the seventeenth.** The live `open_question_gap_kind` CHECK already held
   seventeen — `db/039` added `fda_cyp_unadjudicated` — while § "Plan A" still said SIXTEEN and listed sixteen,
   and both the design spec and the implementation plan said "kind 17". **The migration is correct**, because
   it copies the live CHECK verbatim before extending it and its comment records finding seventeen where the
   plan assumed sixteen. Fixed in § "Plan A", which is the count's one home.
2. **`MAOIs or RIMAs` DOES exist in the table.** The re-measurement's published correction 4 read *"does not
   exist in the table"*. Measured this round against the recorded extract: **10 rows carry it in
   `ddi.source_id`** (and 3 more carry the shorter `MAOIs`), **all 13 of them `ddi_ref_id = 1`** — every
   Stockley's row and nothing else. It appears as an **endpoint** zero times, which is the claim that actually
   holds; the correction checked `drug_class1`/`drug_class2` and then generalised to the whole table.
   **Decision impact: none** — all 13 sit in the half rule 6 excludes and none yields a resolvable pair.
   **Credibility impact: this is the correction round's own results file being wrong in exactly the way the
   round exists to prevent**, which is worth more than the fact it corrects. The scope of a sentence is a
   figure, and it decays the same way a number does.
3. **`class_contraindication_source` did NOT need widening.** HANDOVER said admitting this source required it,
   quoting § "The 5c.3 source evaluation" which said it first. It did not: that CHECK is `('MED-RT','ONCHIGH')`
   and DrugCentral writes no class rule, so `db/049` leaves it and `moiety_contraindication_source` alone and
   `test_class_contraindication_source_is_NOT_widened` pins that they are untouched. **The second CHECK a new
   source really needs is `ingest_run_writer`, which no document had named** — so the claim was not merely
   imprecise, it pointed at the wrong constraint and hid the right one.
4. **The suite count.** That line was stale again — the 71 tests this round added had landed in twelve commits
   without it moving. It has now drifted six times and is issue #146. This round read the collected number off
   `pytest --collect-only -q` at the START of its documentation task, wrote it in § "How to run / test", and
   **deliberately did not restate it** here, in HANDOVER, in ROADMAP or in a section heading — restating it is
   precisely the act that created the sixth occurrence, in the round that filed the issue about it.

### Traps and standing notes

- **The route vocabulary has TWO homes, deliberately, and a test is what makes that safe.**
  `drugcentral_resolve` holds `RESOLVED_ROUTES` (3), `UNRESOLVED_ROUTES` (4) and their union `ROUTES` (7); the
  two route CHECKs restate `ROUTES` in SQL and the two completeness CHECKs restate `RESOLVED_ROUTES`. That is
  the defect db/006 exists to remove, admitted on purpose and pinned by
  `test_the_route_checks_match_the_python_vocabulary`, which reads the live definitions out of `pg_constraint`
  and asserts the admitted sets equal the frozensets. A lookup table the column FKs into was considered and
  rejected: a descriptive column nothing joins on does not earn a table, and the FK would not catch the drift
  the test catches — a route removed from Python while the CHECK still admits it.
- **`missing_keys_row` is in the vocabulary on purpose.** It means a `struct_id` was found by name and is
  absent from the key index, which cannot happen on a well-formed extract. It is counted apart so **a corrupt
  extract does not pass for a difficult one**.
- **`p.upstream_key` is the final sort key of `drugcentral_ddi_pair` and it is load-bearing, not decoration.**
  29 of the 33 duplicate pairs tie on `severity_rank`, and `DISTINCT ON` keeps the first row of a group — so
  without a total order the *reported* `upstream_key` and `upstream_label` could differ between two runs over
  the same bytes. That is the exact defect the re-measurement's review found in three unordered registry
  lookups, in the round whose whole justification was reproducibility.
  `test_the_collapse_is_stable_when_the_two_orientations_tie` pins it.
- **`class_contraindication_source` is NOT widened** — see correction 3 above. Do not "fix" it.
- **Self-pairs are PERMITTED in the assertion table and excluded by the view**, and the asymmetry with db/014's
  `moiety_contraindication_not_self` is deliberate: there a self-pair is a malformed assertion, here it is a
  consequence of resolution (two endpoint names legitimately folding onto one moiety), so refusing it would
  abort an ingest over a correct reading of the source. 0 of 7,571 today, and `DrugCentralSummary` carries it
  as its own bucket so it cannot become nonzero unnoticed.
- **`gap_unresolved_ddi_endpoint`'s `WHERE e.endpoint_name <> ''` exclusion is now tested** — it was carried
  for one round as "verified by manual probe, nothing in the suite would fail if the predicate were deleted",
  and the final review closed it: `test_a_blank_endpoint_is_no_question_and_mints_no_uuid` writes a blank AND a
  whitespace-only endpoint (the guard applies to the FOLDED value) and asserts both the view and
  `open_question` stay empty. **The failure mode is what earned the three lines**: with the predicate removed,
  the register mints `DRUGCENTRAL:ENDPOINT:` with empty text, and a `question_uuid` is immortal.
- **The gap key folds the name.** `gap_key = 'DRUGCENTRAL:ENDPOINT:' || lower(btrim(name))`, matching
  `drugcentral_resolve.fold_name`, because `question_uuid` is immortal and externally cited and two spellings of
  one endpoint must never mint two questions that can then be answered differently. The view is filtered on
  `moiety_uuid IS NULL`, **never on the route vocabulary** — filtering on routes would put that list in a third
  place and force a widening every time a route is added.
- **The severity FK is what refuses a future release quietly.** A third band is refused at INSERT, loudly,
  rather than stored and silently mapped to nothing by the view's join.
- **The reference guard is not the constant.** `BUNDLEABLE_REF_IDS = frozenset({"2"})` has exactly one home,
  `src/drugref/ingest/drugcentral.py` — **and it took three rounds to actually become true.** The
  re-measurement's review found a second hard-coded `ref_id == "2"` in the renderer; the final whole-branch
  review found the spike still defining its own copy of the set and the fixture generator still deciding
  redaction on a bare literal, so the design spec's bold "exactly one home" was false in two more places. Both
  now import it. **In the generator that is a licence rule, not a style one**: that literal decides which rows'
  `description` prose is committed into an AGPL repo, so a drifted copy would keep committing text from a
  reference rule 6 no longer clears. *And* the
  orchestrator reads the `reference` row for every admitted id and **aborts** unless the recorded authors and
  title still match. `2` is a surrogate key in a dump published once; a re-publication is free to renumber it,
  and a silent renumber would bundle Lexicomp under a constant that still reads `2`. A mismatch is a hard abort
  with both strings printed — not a warning, not a skip.
- **`description` is the ONLY column redacted on the excluded rows; FOUR text columns are committed as they
  stand, and each has a recorded determination.** `NOTICE` said "one field" for a round and then argued two
  lines later from a second one — the final review caught it. What is actually kept: `drug_class1`,
  `drug_class2` (the two endpoint names), `source_id` (the compendium's own monograph heading —
  `MAOIs or RIMAs + Buspirone`, `Conivaptan: CYP3A4 Substrates`) and **`ddi_risk`**, which had no determination
  anywhere until the final review round made one. All four rest on the same two grounds: short words and
  phrases are categorically outside copyright (37 C.F.R. §202.1(a)), and what they express is fact rather than
  expression (Feist), the argument `NOTICE` already makes for ONCHIGH. `ddi_risk` is a one- or two-word band
  from the dump's own CLOSED five-value vocabulary, and on an excluded row it is the excluded compendium's
  label rather than the VHA's — which is why it needed assessing rather than waving through. The reasoning is
  in `make_drugcentral_subset.py`'s docstring; the conclusion is in `NOTICE`. **A future regeneration that
  selects an excluded row whose `source_id` is free-form prose, or whose `ddi_risk` is anything but a band from
  that vocabulary, is not covered** and needs the same redaction `description` gets.
- **The writer's own column-list-to-tuple alignment is tested, and it was NOT before.** The final review proved
  by execution that transposing `endpoint_1_name`/`endpoint_2_name` — or `route_1`/`route_2` — inside
  `interactions.add_drugcentral_assertion` left all 1959 tests passing, because every test writing through that
  function resolved BOTH endpoints or NEITHER, and a transposition is invisible on a symmetric row. **37 rows
  of the real release are MIXED.** Under the name transposition `gap_unresolved_ddi_endpoint` would publish the
  RESOLVED partner's name and mint ten wrong immortal `question_uuid`s.
  `test_a_mixed_row_keeps_every_value_beside_its_own_endpoint` writes the asymmetric shape and kills both
  mutations. The keyword-only signature protects the CALLER; only this test protects the function.
- **`DrugCentralSummary`'s identities are Python-side, so the orchestrator reads the table back.** Both
  `__post_init__` checks count records the loop MADE, never rows the table KEPT, so a skipped
  `ON CONFLICT DO NOTHING` insert would drift them silently. `ingest_drugcentral` now runs
  `SELECT count(*) … WHERE ingest_run = %s` inside the work transaction and RAISES on a disagreement, rolling
  the whole run back. The trigger the old comment named — "widening `BUNDLEABLE_REF_IDS` or `upstream_key`'s
  source column" — was too narrow: `resolve_row` falls back to `""` on a NULL `source_id`, so two blank ones in
  a release collide on the empty key with neither of those having changed.
- **The 2023 pin is honest provenance, not a defect to fix.** `upstream_release = '11012023'`;
  `drugcentral.org/download` still offers no successor as of 2026-08-23. The tier is CANDIDATE and the table
  comment says so: rows feed review and must not auto-alert.

### Filed rather than fixed

- **[#148](https://github.com/cairn-ehr/drugref/issues/148) — `exact_ddi_pair` adds a THIRD population to the
  ungraded cross-source disagreement question.** 635 of the 7,501 DrugCentral pairs are already reachable
  through MED-RT's class expansion and nothing compares them. That is #97/#106's question one tier down, over a
  population neither covers: the arms are different grains (asserted vs expanded) carrying different
  information (graded vs no grade stated), and `exact_ddi_pair` uses `UNION ALL` precisely so the disagreement
  stays visible rather than being folded away. This slice adds to the question without answering it.
- **[#149](https://github.com/cairn-ehr/drugref/issues/149) — `fda_cyp_run.FDA_CYP_TABLES` is not registered in
  `test_source_clear_contract.py`'s `EXPECTED_TABLES`**, so a table dropped from that tuple would be caught by
  nothing. Pre-existing and unrelated to this round; found while registering
  `interactions.DRUGCENTRAL_TABLES`. `interactions.py`'s comment was corrected to stop citing it as a peer with
  the same guard, and the gap was filed rather than fixed in passing.
- **[#151](https://github.com/cairn-ehr/drugref/issues/151) — `questions.py` is over rule 4's ~500-line
  guideline**, and **71% of it is the single `_GAP_SOURCES` literal**, which grows with every source that adds
  a gap kind (this round added the eighteenth). Pre-existing; split out of #89 the way #130 was for `cli.py`,
  because the failure mode differs — a declarative table with a visible seam, not dense prose with none. **The
  figures live on the issue.**

### The PR-150 review round — `db/050`, and the diagnosis that unified it

Five specialist agents reviewed the branch (`/pr-review-toolkit:review-pr 150`): general code, tests, silent
failures, comments, type design. **1 critical, 9 important, plus a coverage report from mutation testing.**
Every finding below was reproduced before it was believed, and the two that did not survive reproduction are
recorded here too, because a review round that only lists what it confirmed is the same genre of unfalsifiable
document this project keeps replacing.

**THE ONE DIAGNOSIS WORTH CARRYING FORWARD, because it explains four separate findings at once: every
reconciliation in this slice proved the orchestrator SELF-CONSISTENT, and none proved it PUBLISHED ANYTHING.**
`rows_read = excluded + bundleable` holds at `0 = 0 + 0`. `bundleable = resolved + self_pair + unresolved`
holds at `0 = 0 + 0 + 0`. The read-back holds because `stored (0) == len(bundleable) (0)`. So the all-zeros run
— the one run that destroys the projection — satisfied every guard the round had been proud of. A reconciliation
between two numbers the same loop computed is not a guard; it is arithmetic.

**The critical finding, measured end to end.** Renaming ONE column in the fixture — `ddi_ref_id` →
`reference_id`, exactly what a re-publication is free to do — took the projection from 4 rows to 0, printed
`0 bundleable of 8 rows (8 excluded by rule 6)`, and exited **0**. The summary line was the worst part: it
**blamed rule 6** for a loss rule 6 had no part in, because `bundleable_rows` reads a column that is no longer
there and every row fails the test for the same wrong reason. `check_reference_identity` could not see it — it
reads a different table. `db/050`'s answer is `check_dump_is_readable` and `check_something_is_bundleable`,
both **before `open_run`**, with three distinct messages: a table that decoded to nothing, a `ddi` table missing
a column this code reads, and a dump every row of which rule 6 excludes. The third is the interesting one — a
release that dropped NDF-RT is *well-formed* and is still refused, because rebuilding a source to empty is a
decision an operator makes deliberately, not one an ingest makes on their behalf while reporting success.

**What `db/050` adds, and why each was a comment rather than a constraint.**

- **`upstream_key <> ''`.** One blank `source_id` published a row keyed by the empty string and reported clean
  success; it took TWO to trip the read-back, because a count cannot tell a blank key from a real one. The
  empty string sorts before every real key, so a blank silently won every `DISTINCT ON` tie it took part in —
  in the tie-break whose entire purpose is making most-severe-wins reproducible.
- **The `blank_endpoint` route.** A blank endpoint resolved to `not_a_substance`, which the resolver documents
  as *"A CORRECT miss"* — so a malformed row was labelled a correct reading of an upstream class name, and was
  then invisible everywhere else: dropped from the pair view by the NULL-uuid filter, dropped from the question
  view by the `<> ''` filter that has to be there, and summed into `rows_unresolved` beside genuine misses. The
  argument for a route of its own is the one `missing_keys_row` already made in db/049.
- **`ddi_source_severity.source`.** db/049 wrote the rule down twelve lines below this table and did not apply
  it here. Symmetry, not a defect — the assertion FK rejects any unmapped label anyway.
- **A route-aware `gap_unresolved_ddi_endpoint`.** The view filters on a NULL uuid and **never** on the route
  vocabulary (db/006's reason), so it admits `not_a_substance` and `no_structural_key` — and the question text
  asserted the `unresolved` story about all of them: *"DrugCentral resolves it to a structure with an InChIKey
  or a CAS number"*. False for a class name, whose struct_id does not exist. `question_uuid` is IMMORTAL and
  externally cited, so that text cannot be quietly reworded once minted. It measured 0 wrong questions on this
  release **only because all 10 names happen to land on `unresolved` — the guard was the data, not the code.**
- **The fold.** db/049 said the view restated `fold_name`'s rule. One-argument `btrim()` strips SPACES ONLY;
  `str.strip()` also strips tab, newline, CR, form feed and vertical tab. Two homes, two different rules,
  feeding an immortal uuid. **Latent, not live**: all 7,621 endpoint values on this release are clean — which
  is why it is cheaper to close than to keep re-verifying.

**Code findings, all reproduced.**

- **`Registry.__post_init__` silently reversed `first_wins`.** `first_wins` de-duplicated on the RAW key and
  `Registry` re-keyed the survivors through the fold with a dict comprehension, which is **last**-wins. So
  `Warfarin` and `warfarin` were two distinct keys (collision count: **0**, and that count is what the summary
  publishes) and the fold kept the SECOND. Which one that is depends on the database's collation, so the same
  dump resolved differently on two nodes — the exact failure the caller's `ORDER BY` exists to prevent, and
  `substance_moiety.display_name` carries no uniqueness constraint. `first_wins` now takes the fold, so
  de-duplication, first-wins and the count all happen in the key space `Registry` looks up in; and it counts
  **distinct keys**, not surplus rows, so the figure can be checked against "14 InChIKeys and 29 CAS numbers".
- **REPEATABLE READ was held across the whole run.** This was the only orchestrator in the repo that raised
  isolation, and the snapshot covered `questions.register_from_gaps`, which upserts `open_question` for all
  eighteen gap kinds — most of which this run never touches. Under RR an upsert onto a row a concurrent
  transaction has updated raises `SerializationFailure` **immediately** rather than blocking; nothing here
  retries. Measured directly: the same upsert against the same concurrent update fails under RR and succeeds
  under READ COMMITTED. **The fix removed the need rather than the symptom** — `load_registry` is now ONE
  statement, and a single statement sees a single snapshot at any isolation level.
- **An autocommit connection voided every guarantee, and Postgres only whispered.** The server answers a
  mis-placed `SET TRANSACTION` with a **notice**, not an error, and psycopg discards notices unless a handler
  is installed — so the ingest reported success having silently lost its atomicity, `conn.rollback()` rolling
  back nothing. Now refused outright.
- **`resolved`/`self_pair` were two overlapping booleans whose branch ORDER was load-bearing**, defended by 21
  lines of docstring, a 4-line caller comment, three tests and a ~50-line DB fixture built solely so a swap
  would fail. Replaced by a total, disjoint `Outcome` enum — a caller cannot get an enum's branches in the
  wrong order. `tools/drugcentral_ddi_measure` had already reached the same shape by a different route.
- **The summary was built AFTER `conn.commit()`**, so a failing bucket identity would have raised with the
  projection already published — the reverse of the harm direction every other guard here chooses. It is now
  built inside the transaction, and carries the one check that is **not** tautological at its call site:
  `pairs > rows_resolved`. Both bucket identities are satisfied by construction where the summary is built, and
  an earlier comment credited `__post_init__` with catching a swapped-branch miscount it never could.
- **`BUNDLEABLE_REF_IDS` was a second frozenset** beside `EXPECTED_REFERENCE`; widening only the admitted set
  made the guard die on a bare `KeyError` instead of refusing the reference. Now **derived** from the
  identities, in the file whose stated thesis is that a rule kept in two places is a rule this repo loses.

**Three published figures were wrong again.** The decision record on docs.drugref.org said the excluded labels
included *"a further `Critical` usage"* — `ddi_risk` has six rows, `Critical` appears **once**, and the
twice-appearing label is `Potentially significant`; `make_drugcentral_subset.py` **in the same PR** stated it
correctly, so two committed files contradicted each other. `bundleable_rows` cited *"648 … against 598"*, which
are the **name-matching** column of the re-measurement — the approach the cascade replaced; the cascade
measures **87 and 37**, also exactly 50 apart, which is what made the wrong pair look right. And *"those same
50 rows are the ones whose endpoints are class-named"* reads as BOTH endpoints: **21 of the 50** carry an
ordinary drug name at one end. PROJECT-NOTES had already retired that phrasing once and it came back.

**Coverage: 17 mutants survived, all in the orchestrator's TAIL.** Everything after the insert loop —
`register_from_gaps`, `finish_run`, `conn.commit()`, the `pairs` count, the checksum, the `open_run` arguments,
the rollback — was executed by tests and asserted by none, so eight consecutive lines could be deleted or
transposed with the suite green. Two were worth more than the rest: **`superseded_by IS NULL` could be dropped
from both registry reads** (a retracted identifier resurrecting a resolution puts a WRONG MOIETY on a
contraindication pair), and **`test_two_blank_source_ids...` asserted its own setup** — its docstring credited
"the rollback" while the test itself called `conn.rollback()` before counting, so deleting the orchestrator's
rollback left the suite green. All now killed, each verified by re-running its own mutation. The
`exact_ddi_pair` **DrugCentral arm** could also transpose `moiety_lo`/`moiety_hi` while the MED-RT arm's
identical mutation was caught — asymmetric coverage on the one contract the view's `COMMENT ON` calls out.

**Two claims did NOT survive reproduction, and that is recorded deliberately.** An intermediate probe suggested
`finish_run` was not stamping `finished_at`; tracing the UPDATE showed `rowcount = 1` and a committed timestamp
— an artifact of an ad-hoc script. And one agent reported a "file changed" notice showing
`Registry(inchikey=cas, cas=inchikey)` on disk; the file never contained it. Both were **cross-talk between
review agents running concurrently against one working tree and one test database** — which is also what
produced a run of phantom `SerializationFailure` and `DeadlockDetected` failures across unrelated modules, and
is now filed as **#153**. A review that runs agents in parallel must treat its own contention as a suspect
before it treats the branch as one.

### Filed rather than fixed, this round

- **[#152](https://github.com/cairn-ehr/drugref/issues/152) — synthesise the fixture's excluded-reference rows
  rather than committing their text.** The four excluded rows ship Stockley's and Lexicomp monograph headings
  in `source_id` verbatim (`MAOIs or RIMAs + Buspirone`, `Conivaptan: CYP3A4 Substrates`). `NOTICE` argues this
  out under *Feist* and 37 C.F.R. §202.1(a), and that section was **verified accurate throughout**. It is filed
  rather than fixed because it is a licensing-POSTURE decision, not a defect — but the risk is free to
  eliminate: the rule-6 filter keys on `ddi_ref_id` and never on the text, so invented rows exercise the
  exclusion path identically, and `make_drugcentral_subset.py` never weighed that option.
- **[#153](https://github.com/cairn-ehr/drugref/issues/153) — two concurrent pytest sessions on one database
  wipe each other's schema.** `conftest.py` runs `DROP SCHEMA ... CASCADE` in a session-scoped fixture.
  Pre-existing; the failure mode is worse than a crash because it is *plausible* — it invents evidence against
  whatever branch is under review.

## The 5c.3 SPL measurement round (2026-08-24) — no migration, no ingest

Full account and every figure:
[slice 5c.3 SPL mining measurement](superpowers/specs/2026-08-24-drugref-slice-5c3-spl-mining-measurement.md).
A **measurement round only**: it commits no schema, no migration and no design spec. Three brainstorm
decisions scoped it — the slice produces **both** drug × class rules and drug × drug exemplars kept separate
with shared provenance; extraction is **deterministic entity recognition with NO relation extraction**
(deciding a sentence means "contraindicated" is a clinical reading, and *ingest preserves evidence; curation
creates clinical judgement*); and the corpus is measured **in full**, both corpora, never sampled.

### ⇒ MEASURING FIRST CHANGED THE SHAPE OF THE SLICE FOR THE THIRD ROUND RUNNING, AND THIS TIME IT CHANGED THE CORPUS

The round opened committed to DailyMed's 18 GB Human Rx release. **openFDA carries the same section and beats
it on every axis**: CC0 1.0 against NLM's explicit *"cannot guarantee the copyright status"*; 1.73 GB against
18 GB; `drug_interactions` **pre-split as a field** against nested zips → XML → LOINC splitting; and
`openfda.unii` — which resolves the subject drug for free, `moiety_uuid` being UUIDv5-on-UNII. Both were taken
in the end, DailyMed as the cross-check, and that was right: the cross-check is what turns "openFDA's field
looks correct" into a measured claim.

### ⇒ RULE 6: THE TWO PUBLISHERS OF ONE CORPUS TAKE OPPOSITE POSITIONS

**NLM asserts nothing and disclaims** (*"It is your responsibility to determine and satisfy copyright…"*;
*"NLM cannot guarantee the copyright status for any item"*), and DailyMed describes its content as labeling
*"submitted to the FDA **by companies**"* — **the exact shape of the DIRIL determination**, where a
public-domain FDA publication did not turn copied third-party material into federal work. **openFDA, FDA's own
service, dedicates the same bytes to the public domain under CC0 1.0**, carving out only GMDN.

The determination splits by what is stored, because the unit of clearance is the field: **derived facts —
entity occurrences, offsets, `set_id`/`version` — are clear under either reading** (facts are not
copyrightable, a citation is not a copy, and `db/045` already admits citation-only **SPL** references).
**Verbatim prose is the contested part**, because a CC0 dedication waives the dedicator's own rights and
cannot extinguish a third party's. **Recommendation: reference the prose, do not bundle it** — it satisfies
both readings, costs nothing that matters, and matches the reviewer tier. Filed as
**[#154](https://github.com/cairn-ehr/drugref/issues/154)**; it is a posture call for the owner, not a defect.

### The corpus, and the de-duplication factor that was assumed wrong

**262,032 records → 68,550 carry section 34073-7 → 27,406 DISTINCT WORDINGS.** The factor is **2.50 labels per
wording**, and it is far lower than expected: one UNII appears on up to **498** labels, which invited the
assumption that generic labels copy each other. Measured, **they do not — each manufacturer writes its own
section 7**. Every rate is quoted against 27,406 wordings and never against 68,550 labels.

**23 OTC labels of 68,550** independently confirm the 2026-08-13 finding that 34073-7 is a prescription
section. **40,413 labels (59%) carry no `openfda` block at all** — section present, subject unkeyable.

### ⇒ THE HEADLINE: THE POTENCY BAND IS PAIR-SCOPED, AND 7× MORE COMMON THAN drugref CAN SEE

Two findings against issue #102, and each one moves it.

**1. The band is a property of the PAIR, not of the inhibitor.** `CYP1A2 strong inhibitor [FDA-CYP]` ships with
**0 members**. FDA's only strong 1A2 inhibitor, fluvoxamine, is `withheld_qualified` on footnote 8 (which
concerns CYP3A substrates and does not negate the 1A2 claim — a conservative withhold working as designed).
Ciprofloxacin, which the tizanidine label calls *strong*, FDA files under `CYP Mod INH` — and **FDA's footnote
20 names tizanidine explicitly**: *"generally classified a moderate CYP 1A2 inhibitor… however, it can
sometimes behave like a strong inhibitor… when it interacts with certain CYP 1A2 substrates that are
considered highly sensitive (e.g., tizanidine)."* **So the label and the table never disagreed.** ⇒ This
**retires options 1 and 2 of #102** — both hang the band on the class, and a per-class band is not coarser
than the source, it is *wrong*: it would assert `strong` for ciprofloxacin against every CYP1A2 substrate.

**2. The band looked rare only because drugref spells its classes backwards.** Through the stored vocabulary,
0.8% of class occurrences carry a band (1.2% on PK axes). Against the prose directly: **`band + CYP<n> + role`
appears 15,708 times in 4,236 wordings (15.5%)**, and any band word near a role word in **6,973 wordings
(25.4%)** — against the **2,212** occurrences FDA-CYP's stored names actually matched. **Roughly 7×.** The
cause is word order: labels write *"strong CYP1A2 inhibitors"*, drugref stores *"CYP1A2 strong inhibitor"*.
⇒ **The band is not a corner to sweep into a gap view; it is in a quarter of all wordings.**

### ⇒ MED-RT's PK AXIS IS NOT A DRUG-CLASS VOCABULARY, AND USING IT MANUFACTURES FALSE POSITIVES

Splitting class yield by whether the class **has any members** — a class with none cannot be an endpoint,
however often it is named — is what makes the encouraging 93.2% honest: **32.3% of all class occurrences name
an EMPTY class.**

| axis | occurrences | of which empty |
|---|---|---|
| MED-RT (non-PK) | 265,955 | 71,944 |
| MeSH | 115,583 | **112** |
| **MED-RT PK** | 80,042 | **77,795 (97.2%)** |
| **FDA-CYP** | **2,212** | **0** |

MED-RT's 59 PK concepts are pharmacokinetic **properties** — `Absorption`, `Clearance`, `Half-Life`,
`Cytochromes`, `Hair Excretion` — and **only 6 have a single member**. Matching them recognises ordinary
pharmacokinetic English: `Clearance [PK]` scores 22,277 "mentions". **These are false positives carrying a
class UUID.** Filed as **[#155](https://github.com/cairn-ehr/drugref/issues/155)**. MeSH is the opposite and is
the quiet good news. Separately, `Diuretics` (MeSH) and `Diuretic [APC]` (MED-RT) both score 17,118 because
they fold to one string and the matcher returns **both** rather than picking one — deliberate, per FDA-CYP's
*ambiguity is unresolved, never "pick the first"*, but it means class occurrences are not distinct concepts.

### ⇒ THE ROUND ASSERTED A CAUSE IT HAD NOT MEASURED, AND GOT ONE BACKWARDS

The first pass reported the pair count as a **range** between "all names" and "all names minus the 477 in
`/usr/share/dict/words`", justified by *"`prothrombin` is a lab test, `lead` is a verb"*. **Neither half had
been checked, and the framing was wrong.** Recorded rather than quietly fixed, because it is the standing rule
at work — *a disposition records what was OBSERVED, never what the round suspects it MEANS*, which is
[#122](https://github.com/cairn-ehr/drugref/issues/122)'s manufactured-cause defect reached again.

| name | occurrences | measured reality |
|---|---|---|
| `lead` | 9,160 | **the verb** — 9,157 (100.0%) followed by `to`. False positive |
| `prothrombin` | 9,363 | **a lab test** — 81.6% `time`, 10.0% `times`. False positive |
| `serotonin` | 19,804 | **a syndrome / a class** — 50.2% `syndrome`, 23.6% `reuptake`. Mostly false |
| `alcohol` | 13,530 | **ethanol, a REAL interactant** — 0.2% excipient-qualified. **True positive, wrongly accused** |

**The dictionary endpoint was wrong in BOTH directions**: `serotonin` is not a dictionary word and survived,
while `alcohol`, `iron` and **`lithium` — the corpus's most-matched moiety at 28,368 occurrences and a
clinically critical interactant — are, and were deleted.** So it is not a lower bound; it is a
differently-wrong number, and calling it the bottom of a range implied a guarantee it never carried.

**The real mechanism is "head of a longer term naming something else", not "ordinary English"** — and
longest-match-wins already handles that *once drugref holds the longer term*. ⇒ **The fix is a negative
vocabulary, not a stop-list**, and it was tested rather than argued: nine measured terms in
`tools/spl_suppress_terms.txt`, each carrying its own distribution. A stop-list deletes a name everywhere,
including where it really is the drug — **lead the element (Pb) is a genuine moiety with a genuine interaction
through chelation therapy**, and only `lead to` is ever noise.

| | all names | dictionary-excluded | **suppression (measured)** |
|---|---|---|---|
| distinct candidate pairs | 21,201 | 17,279 | **20,554** |
| NOVEL vs everything held | 18,754 (88.5%) | 15,007 (86.9%) | **18,107 (88.1%)** |
| novel vs `exact_ddi_pair` alone | 19,339 (91.2%) | 15,558 (90.0%) | 18,692 (90.9%) |

**⇒ Quote the suppression column.** It is the only one whose exclusions were each measured.
**DrugCentral's whole slice was justified on 7,501 pairs at 91% new; SPL yields nearly three times that at the
same novelty rate** — and that conclusion holds under all three columns, which is the one virtue the range
framing did have. The counterweight is **41,056 labels (60%) discarded before a pair can form** for want of a
resolvable subject.

### The cross-check, and what it says about trusting a derived field

openFDA's `drug_interactions` is FDA's own derivation from the SPL XML, so it was **verified rather than
trusted**. All six DailyMed Rx parts scanned: **54,813 labels**, **39,743 carry section 34073-7**, and
**39,678 of those set_ids are present in openFDA — 65 missing (0.16%)**. On 2,000 labels read from both
sides, **containment is 1.0000 on all 2,000**: openFDA reproduces the section exactly, nested 7.1/7.2
subsections included.

**And the two corpora are NOT two views of one population.** openFDA carries **68,550** section-bearing labels
against DailyMed's 39,743 — **28,807 more**, because DailyMed's release is current in-use Human Rx only.
A figure from one may not be quoted against the other's denominator. *(The download page states 50,813 files;
the six parts contain 54,813. Counted, not quoted.)*

**The perfect score is evidence only because the check was shown it could fail** — re-run with each label
paired against a *different* label's text, mean containment collapses **1.0000 → 0.4276** and 1,937 of 2,000
fall below 0.80. That is db/050's lesson applied before the review round instead of during it.

### Traps and standing notes

- **The de-duplication factor must be divided out before any rate is quoted.** Labels and wordings are
  different units, and the 2026-08-13 evaluation was already burned once by quoting one as the other.
- **Key on the document-type CODE, never `displayName`** — still true, and openFDA's `product_type` values are
  `HUMAN PRESCRIPTION DRUG`, **not** DailyMed's `HUMAN PRESCRIPTION DRUG LABEL`. A query written against the
  wrong one returns "No matches found!" rather than an error.
- **`openfda.product_type` is populated on only 86,574 of 262,032 records.** Absence is a population, not a bug.
- **The matcher is CONTIGUOUS on purpose.** The tizanidine label's *"strong cytochrome P450 1A2 **(CYP1A2)**
  inhibitors"* does not match, and that miss is pinned as a passing test. A matcher that skips words produces
  spans it cannot quote back to a reader.
- **Containment, not Jaccard, is the fidelity metric** for comparing a derived field against its source. The
  question is whether anything was DROPPED, which is asymmetric; Jaccard scores a perfect short-section
  reproduction at **0.50**. Pinned as a test so it cannot be "simplified" back.
- **Folding erases stereochemistry, and that is a bounded but real cost.** The registry spells stereoisomers
  with a punctuation suffix -- `carvone, (+)-`, `carvone, (-)-`, `epinephrine,(+/-)-`, `.beta.-pinene` -- and
  the matcher's fold strips punctuation, so **24 folded keys carry more than one registry name, covering 55 of
  19,438 (0.28%)**. The matcher handles it correctly by returning EVERY colliding entry and refusing
  `Match.entry`, per FDA-CYP's *ambiguity is unresolved, never "pick the first"*. It is recorded because the
  direction matters for DDI specifically -- S- and R-warfarin take different CYP pathways -- and because it is
  [#128](https://github.com/cairn-ehr/drugref/issues/128)'s problem reached from the other side: there the
  racemate cannot carry a stereoisomer's assertion, here the label's stereoisomer folds onto the racemate's
  name. **It is also why the dictionary-collision endpoint is 477 and not the 463 a plain `lower()` finds** --
  the extra 14 are stereo-suffixed names folding onto a common word.
- **A fidelity check that scores 1.0 proves nothing until it has been shown it can score low.** The negative
  control — pairing each label with a *different* label's text — is what makes the perfect score evidence.
  That is db/050's lesson applied before the review round rather than during it.

## The 5c.3 subject-recovery round and the design spec (2026-08-24) — no migration, no ingest

Second half of the 5c.3 design round, in the same session as the branch above. **The design spec now exists**:
[slice 5c.3 SPL DDI ingest design](superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md),
resting on [the subject-recovery measurement](superpowers/specs/2026-08-24-drugref-slice-5c3-subject-recovery-measurement.md).
Still **no migration and no ingest** — `db/051` is designed, not written.

**Four owner decisions scope it, and none of them should be re-litigated without a reason:**

1. **[#154](https://github.com/cairn-ehr/drugref/issues/154) is ANSWERED: bundle a quoted window only.** Not
   reference-only and not the full prose — the matched span plus a bounded context, with the rest cited.
2. **Drug × drug only.** The class half is deferred to its own slice; every unsolved problem lives there.
3. **Structural subject routes only.** The rank-0 name heuristic does not ship.
4. **The quote budget is proportional**: 25% of the section's characters.

### ⇒ THE COUNTERWEIGHT WAS QUOTED IN THE WRONG UNIT, AND IT WAS UNDERSTATED

The mining round published the loss as **41,056 labels (60%)** — and labels are the wrong unit, which that
same round said in a different context (*"the de-duplication factor must be divided out before any rate is
quoted"*). Split properly it cuts both ways:

- **14,455 of those labels are REDUNDANT** — another manufacturer reprinting a wording a keyed label already
  carries. Recovering them rediscovers statements drugref already has. So 60% overstates the loss.
- **But in WORDINGS the loss is 56.0%** — 15,345 of 27,406 are reachable only through unkeyed labels, and the
  published 20,554 pairs came from **just 11,939 wordings**. So 60% also understates it, on the axis that
  matters.

**⇒ MIND THE DENOMINATOR: 41,056 AND 40,856 ARE DIFFERENT POPULATIONS, 200 APART.** 41,056 is
`68,550 − 27,494`, labels with no *resolvable* subject. 40,856 is `68,550 − 27,694`, labels with no UNII at
all — and that is what the probe's classifiers actually split, because they branch on presence. The 200 in
between carry a UNII drugref does not hold. `14,455 + 26,401 = 40,856`, not 41,056, and the round's first
write-up mixed the two in one sentence.

**And the orphan half is not inferior material**: it names a known moiety in **97.2%** of wordings against the
keyed half's 97.8%, at **higher** density (49.3 moiety occurrences per wording against 44.0) and across
slightly more distinct drugs (1,862 against 1,846).

### The three subject routes, and why only two ship

| route | mechanism | wordings with a **resolved** subject | pairs | novel |
|---|---|---|---|---|
| 1. `openfda.unii` | structural | 11,939 (43.6%) | 20,554 | 88.1% |
| **2. + DailyMed XML** | **structural** | **16,610 (60.6%)** | **29,258** | **88.7%** |
| 3. + rank-0 name | heuristic | *withdrawn — see [#158](https://github.com/cairn-ehr/drugref/issues/158)* | | |

**Route 2 adds 8,704 pairs (+42.3%), 7,853 of them novel (90.2%)** — a *higher* novelty rate than the
baseline it extends, and **on its own still bigger than DrugCentral's entire slice** (7,501 pairs at 91%),
though by 1,203 rather than 3,563. Of the 26,401 labels targeted, **6,539 are in DailyMed (24.8%)**, **6,514
of those resolve (99.6%)**, zero carry no UNII and **25 carry a UNII drugref does not hold**. The limit is
the release, not the reading — and that is now measured, not inferred: all four of the scan's drop counters (there are now six, two added by the review round)
(unreadable, no `setId`, pre-filter disagreement, parse failure) are **zero**.

**⇒ THE FIRST READING OF THIS TABLE WAS WRONG, AND IT PUBLISHED 31,618 WHERE THE RULE GIVES 29,258.**
`augment_rows` fed the pair counter the recovered moiety UNII **and** the salt UNII together, and drugref
registers a salt as its own moiety with its own live UNII claim — so a salt product contributed two subjects
and paired twice, on **56.7%** of resolvable DailyMed labels, while the `openfda.unii` arm contributed one.
The delta was measured with a looser rule than its own baseline, and it contradicted the round's own route
table, where the salt route is 16 labels counted apart. Separately the "wordings with a subject" column meant
*any UNII present* here and *resolves against drugref* in the rescued-wording figure beside it — the whole of
the 22-wording gap between the published 16,754 and `12,061 + 4,671`. Corrected, **`11,939 + 4,671 = 16,610`
closes exactly.** `subject_uniis` is now the one subject rule and `spl_ddi_measure.form_candidate_pairs` the
one pair rule, both called by both arms.

**Route 3 was found this round and rejected.** `openfda` is present on 100% of unkeyed records and is simply
EMPTY, but `spl_product_data_elements` is populated on 40,633 of 40,856 (99.5%) — one flattened uppercase
string holding product name, active ingredients, active moieties and excipients with **no delimiter**.
Measured against route 2's output as ground truth (6,317 labels): the true moiety is among the names
**98.9%** of the time, but the field averages **7.69 registry matches per label**.

| positional rule | picks exactly the truth | extra is only a SALT of the truth | **genuinely wrong** |
|---|---|---|---|
| rank 0 only | 52.2% | 41.6% | **6.2%** |
| ranks 0–1 | 8.5% | 40.8% | **50.7%** |

**Splitting salt spellings out of the error is what makes it honest** — rank 0 reads 47.8% wrong or 6.2%
wrong depending on whether *right drug, wrong grain* counts as a miss, and only one of those supports a
decision. Excipients enter at rank 1 (`silicon dioxide` 421, `lactose monohydrate` 412, `magnesium stearate`
271), exactly as SPL's generation order predicts. **Route 3's pair yield is withdrawn (#158); it was rejected
on its 6.2% wrong-subject rate
and does not ship** — but the **6,317-label overlap is a permanent calibration set**, and any future
heuristic route has ground truth to be measured against before it ships.

### ⇒ A PER-OCCURRENCE QUOTED WINDOW IS NOT A QUOTE — IT IS THE SECTION, REASSEMBLED

The owner's #154 answer needed a window rule, and the obvious ones do not survive measurement. The corpus
averages **48.2 moiety occurrences per wording** over a mean section of **3,898 characters** (re-derived
from committed code — `tools/spl_quote_budget.py`, `probe quotes`, over all 26,721 wordings naming a moiety;
the round's first pass published this section with no producer at all):

| per-occurrence rule | mean % of section stored | median | ≥90% of section |
|---|---|---|---|
| the containing sentence | **82.7%** | 87.2% | 41.4% |
| ±120 characters | 89.0% | 94.0% | 64.4% |
| ±60 characters | 74.9% | 77.9% | 15.6% |

**The bound must be per WORDING**, and the shipped rule is **±60 chars around the FIRST occurrence of each
distinct moiety, in document order, until 25% of the section's characters are spent** — measured at
**20.4% of a section stored on average** (NOT the 14.7% first published — that figure's code was never
committed and cannot be audited; the per-occurrence rows re-derive to within ~1 point, this one did not),
5.1 merged windows per wording, covering 71.6% of distinct
moieties. The other 52.7% lose only the window: occurrence, offsets and citation are stored regardless,
because those are clear under either reading of rule 6. **It is a schema constraint, not a convention** — the
failure mode is silent, additive and visible only in aggregate.

### ⇒ THE ROUND'S OWN TALLY WAS WRONG BY 44, AND ITS 18 TESTS DID NOT CATCH IT

The recovery summary first reported **6,583 labels found and 6,558 resolved**, because it counted the scan's
ROWS. DailyMed ships successive **versions** of one label as separate documents sharing a `set_id`, so 44
labels were counted twice. **What caught it was cross-checking the total against an independent pass** that
computed resolution straight from the cache — not the probe's own tests, every one of which passed.
⇒ **A tally that only ever agrees with itself is not checked.** Now de-duplicated by `set_id` and pinned by
`test_one_set_id_read_TWICE_is_one_label`. The rescued-wording figure was unaffected: it was already a set.

### Traps and standing notes

- **Count wordings, not labels — and re-derive the unit every round.** The mining round wrote that rule down
  and still quoted a figure the wrong way in its own summary. 60% of labels is 56% of wordings, and the two
  say opposite things about whether the work is worth doing.
- **A perfect resolution rate and a poor coverage rate are separate facts.** DailyMed resolves 99.6% of what
  it holds and holds 24.8% of what was asked for; either alone describes a different source.
- **Split salt-grain errors out of any precision figure.** [#67](https://github.com/cairn-ehr/drugref/issues/67)
  is now wanted by three sources, and folding it into a precision number changes that number eightfold.
- **Ground truth from one route is how another route gets measured.** Route 2's output was route 3's
  validation set; a heuristic with no ground truth available is unmeasured, not unmeasurable.
- **`openfda` present ≠ `openfda` populated.** The block exists on 100% of unkeyed records and is empty, so a
  presence check reports full coverage.
- **EVERY PAIR FIGURE IS A FLOOR.** The scan targeted orphan-wording labels only, so the **14,455 redundant
  unkeyed labels were never read** — and a label's SUBJECT is its own even when its wording is shared, so
  their pairs are uncounted. The ingest must scan them; the design's floor check asserts `>=`, not `==`.

## Slice 5c.3 — the SPL ddi ingest (2026-08-27) — `db/051`, measured on the real releases

**The slice is BUILT.** [Design spec](superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md)
· [what it actually produced](superpowers/specs/2026-08-27-drugref-slice-5c3-spl-ddi-ingest-results.md).
`drugref ingest spl --openfda <dir> --dailymed <parts...>` reads both corpora (19.3 GB, **2 min 09 s** since
issue 160; ~12.5 min before it), resolves
each label's subject, matches the shipped resolver's rule over every wording, stores a bounded quoted window
and refuses on any of six grounds. **Read the figures in the results record, not here.**

### What shipped

`db/051_spl_ddi_evidence.sql` — the source-admission **trio**, five tables, two views, one gap view and one
deferred constraint trigger. Pure modules (`ingest/spl.py`, `spl_dailymed.py`, `spl_release.py`,
`spl_match.py`, `spl_quote.py`, `spl_subject.py`, plus `spl_checks.py` for the guards), the writer
`spl_evidence.py`, the orchestrator `ingest/spl_run.py` and `cli_spl.py`. (`spl_release.py` — the release WALK,
`ScanResult`/`scan_release`/`iter_release_labels` — was split out of `spl_dailymed.py` on 2026-08-31 under
rule 4; `spl_dailymed.py` reads ONE label's XML and never opens a zip.)

**⇒ TWO VIEWS AT TWO GRAINS, EACH NAMED FOR ITS OWN.** The design said "two views" and named one. `spl_ddi_pair`
is PAIR grain — `count(*)` **is** the pair count, directly comparable with `drugcentral_ddi_pair` — and
`spl_ddi_evidence` is EVIDENCE grain, one row per citation (1,470,708 of them against 29,952 pairs, a factor of
49). This project has published a figure in the wrong unit in three consecutive rounds; a consumer who counts
the wrong view now gets a name that contradicts them.

**⇒ `gap_unresolved_spl_subject` IS DELIBERATELY NOT A NINETEENTH QUESTION KIND**, and `db/051` §8 is the one
place that says so, because every other `gap_*` view in this schema feeds `questions._GAP_SOURCES`. A curator
cannot answer *"not in the current DailyMed release"* — db/012's test for whether the review gate may ask at all
— and the measurement below shows that is **99.7%** of the register rather than a corner of it.

### ⇒ THE DESIGN'S `unresolved` BUCKET SAID 14,680. IT IS 92.

The design's route table filed **14,455 labels the probe had never read** into a bucket whose own definition is
*"present, read, and still unkeyable"*. Scanned for real: **30,386 of the 41,056 targets are simply absent from
the current DailyMed release**, and only **92 labels in the whole corpus are present in it and still
unkeyable**. So the recovery register is **99.7% a RELEASE gap and 0.3% a REGISTRY gap** — the opposite of what
a reader of that table would plan for, and it points a future recovery route at a fuller corpus rather than at
registry coverage.

⇒ **A population you did not read is not evidence about the population you did.** The design's own prose said
the 14,455 were unscanned, two paragraphs below the table that counted them as scanned. Nothing checked the two
against each other, because both were prose.

### ⇒ DROPPING THE CLASS VOCABULARY MOVED THE DRUG × DRUG YIELD, BY 193 PAIRS

The openFDA-only arm yields **20,747** here against the design's published **20,554**. Measured rather than
asserted (`tools/spl_class_vocabulary_delta.py`, committed — this round already recorded what happens to a
figure whose producer is not): adding the **8,534** class entries back reproduces the design's two figures
**exactly**, 20,554 pairs over 26,721 wordings, against the shipped 20,747 over 26,760. Longest-match-wins had a
class name consuming **11,169** moiety spans — `Serotonin Uptake Inhibitors` swallowing `serotonin`.

⇒ **A vocabulary is part of a measurement's definition, not its scenery.** Deferring the class half did not
merely postpone the class figures; it moved the drug × drug ones. **A future round that re-adds classes must
expect the drug × drug yield to FALL, and must not read the fall as a regression.**

### ⇒ THE INGEST DID NOT FINISH ON ITS FIRST RUN, AND THE FIRST DIAGNOSIS WAS WRONG

It sat **25 minutes at 100% CPU** in the self-pair read-back and was cancelled. The obvious cause was foreign-key
checks against a freshly bulk-loaded parent with no statistics — and that was **measured and refuted**: 20,000
child rows against an unanalyzed 68,550-row parent insert in **175 ms**, because PostgreSQL's RI triggers use a
plan pinned to the parent's primary key rather than a re-planned query.

> **⚠ THE REASON IN THAT LAST SENTENCE IS FALSE, and issue #160 cost a round to it.** The measurement was real;
> the explanation was invented and quoted forward. The plan is pinned to whatever was chosen at FIRST USE, not
> to the primary key, and the 175 ms did not generalise because that parent had no wrong plan available to pin.
> Left standing here because this section records what the round believed; corrected in § "The COPY-cost
> round", which is where the current state lives.

The real cause was the same missing statistics one table further on: **every read-back joins tables the same
transaction just `COPY`'d**, so the planner costs them as if empty and picks a nested loop over 1.3 million
occurrence rows. `spl_evidence.analyze_source_tables` now runs inside the transaction before them.

⇒ **The guard is pinned by its CAUSE, not by a timing.** A performance property cannot be asserted as a
stopwatch on a fixture, so `test_the_projection_is_ANALYZED_before_its_own_read_backs` asserts
`pg_class.reltuples >= 0` for all five tables — which is exactly the state that produced the stall. Removing the
`ANALYZE` fails it.

### ⇒ THE FIRST FIXTURE COULD NOT SEE A WRONG QUOTE BUDGET, AND THAT WAS MEASURED

With the end-to-end corpus as first written, setting `spl_quote.QUOTE_SHARE` from 0.25 to **0.95** left all 28
tests passing: every wording named two moieties over 3,700 characters, so the budget never bound and the
writer's share was unobservable. **That is db/050's "every guard in a slice passed vacuously" recurring inside
the round that quotes db/050 about it.** The corpus now carries a wording naming every fixture substance in a
short section, so the rule must SKIP windows — and the same mutation now fails **16** tests, because the writer
exceeds the budget `db/051`'s trigger computes for itself in SQL.

The same fixture was blind to raw-versus-normalised text: the synthetic wordings had no whitespace runs, so
`raw == normalised` and every offset assertion passed whichever the ingest indexed. They are now wrapped and
double-spaced, a test pins that premise, and mutating `read_corpus` to keep the raw text fails four.

### THREE EXISTING GUARDS FIRED, AND ONE CAUGHT A REAL DEFECT

`test_only_checksum_py_hashes_an_ingest_input` is the single-place pin for hashing an ingest input.
`spl_run.py`'s first draft computed its own digest-of-digests over the two corpora with a **function-local**
`import hashlib`, which the line-anchored grep could not see. It now calls the shared `checksum`, and **the test
now also refuses any indented hashlib import** — the hole it fell through. `spl.py` keeps its own hashlib for
`section_key`, which mints a content identity rather than checksumming a file; that exemption is checked with
`ast` rather than asserted in a comment. The other two — the schema inventory and `provenance.WRITERS` — are
the "exact inventory" tests doing exactly their job.

### The fixture carries NO PROSE, and that is rule 6

`tests/fixtures/spl/` holds label IDENTITY from openFDA's export and prose-free SPL **ingredient skeletons**
extracted verbatim from DailyMed — facts, not expression. The section text is synthesised by the test, because
the owner's #154 determination is a *bounded* quoted window and a section committed whole to a git repository
is 100% of it, not 25%. `tools/spl_make_fixture.py` is the extractor. Everything else is real: the archives are
zipped into both publishers' actual nested shapes at test time, so the readers, the `set_id` join, the
classCode nesting and the salt/moiety split all run against structures nobody wrote for a test.

### Traps and standing notes

- **`overlaps` is a RESERVED SQL keyword** (the period-overlap operator). A plpgsql variable of that name fails
  to parse with an error pointing at a line that is correct.
- **`python -m drugref.cli` silently does nothing and exits 0** — the module has no `__main__` guard. Use the
  `drugref` console script; `PYTHONUNBUFFERED=1` if you want to watch a long ingest through a redirect.
- **The route table's ROW count is not its LABEL count.** 73,867 rows over 68,550 labels, because a combination
  product carries several subjects on one route. Reporting rows would publish the combination rate as if it were
  the resolution rate.
- **`ingest_run.finished_at − started_at` is not a duration for ANY feed** — [#159](https://github.com/cairn-ehr/drugref/issues/159).
- **A parent must be ANALYSEd before the child that references it is loaded** — [#160](https://github.com/cairn-ehr/drugref/issues/160),
  CLOSED 2026-09-01. Without it the subject `COPY` ran 630 s; with it, ~2 s, and the whole ingest 12m51s → 2m09s.
  § "The COPY-cost round" has the cause and the census.
- **A registry is allowed to be incomplete, and narrowing one is how a route gets tested.** The salt route and
  `unresolved` have no natural example in the fixture; withholding a label's active-moiety UNII while keeping
  its salt reaches the first, withholding both reaches the second. That is what #67 and the 200 unheld-UNII
  labels actually are.

## Slice 5c.3's review round (2026-08-29) — `db/052`, and the mutants the slice's own headline predicted

The round that shipped `db/051` led with *"the end-to-end fixture could not see a wrong quote budget, and that
was measured"*. A five-agent review of PR #161 found **the same vacuity class alive in five more places** — and
the worst of them was the guard enforcing the licensing determination that headline is about. 53 tests added,
2296 → 2349; one migration, `db/052`, comments only.

### ⇒ THE QUOTE BUDGET HAD THREE HOMES, AND THE TEST NAMED FOR PINNING IT WAS THE THIRD

`test_the_budget_in_the_catalog_is_the_SAME_expression_as_the_python_one` ran `SELECT ceil(0.25 * %s)` **with
the 0.25 typed in the test**. It therefore compared Python against a literal a reviewer had retyped, never
against `db/051`'s trigger. Measured: **mutating the trigger to `ceil(0.35 * wording_length)` left all 29 tests
in that file green.** So did `ceil` → `floor` in the trigger; the same mutation in Python failed, which is
exactly the one-sided shape the name denied. The third home was `budgeted_windows`' own inline
`math.ceil(share * text_length)` — **the expression the shipped writer actually ran** — reachable through a
`share=` keyword that let a caller in the shipped module build windows this module called fine and the trigger
refused at COMMIT.

Fixed: the test reads `pg_proc.prosrc`; `share=` is gone and every rule reaches the constant through
`quote_budget`; the number now has exactly two homes. ⇒ *A test that restates the number it is checking cannot
detect the disagreement it is named for.* `spl_subject.py`'s route check — which reads `pg_get_constraintdef`
and compares both directions — was the model, and it was in the same slice the whole time.

Two boundary mutants survived alongside it and now do not: `spent > allowed` → `>=`, and
`q.char_end > wording_length` → `>=`. The second is not hypothetical — `fixed_window` clamps to the text
length, so **every quote over a moiety named within 60 characters of a section's end has
`char_end == char_length`**, and the mutated trigger would have refused in production what the suite called
fine. The budget tests bracketed 24% accepted against 39% refused and never touched the edge between.

### ⇒ `reconcile` COULD BE DELETED WITHOUT FAILING A TEST

Three mutations — `if stored != written:` → `if False:`, `if past_end:` → `if False:`, and `>` → `>=` on the
cross-table span check — each left the whole suite green. It is the **only** check in the slice comparing what
Python believes it wrote against what the database holds, and its own docstring calls the second half *"THE ONE
NO CONSTRAINT CAN EXPRESS"*. A `COPY` that silently dropped rows would have shipped. Both halves are now watched
refusing.

### ⇒ THE SCAN RAN INSIDE AN OPEN SNAPSHOT (called 12.5 minutes then; measured at ~50 s in #160)

`load_registry` is the first statement on the non-autocommit connection `ingest_spl` requires, so it opens a
transaction; `open_run` is on the far side of `scan_release` and a 19.3 GB checksum. Verified against the test
database: a bare `SELECT` leaves the backend `idle in transaction` with a live `xact_start`. On a production
node that pins `xmin` **database-wide** — autovacuum reclaims nothing in any table for the duration — and offers
the connection to `idle_in_transaction_session_timeout` at the far end of the most expensive step in the ingest.
`drugcentral_run` never had this window; 5c.3 inverted the ordering for a good reason (a run row must not sit
unfinished across the scan) and the inversion left the snapshot open. One `conn.rollback()`, pinned by a test
that asserts the CAUSE — `conn.info.transaction_status` at the moment the scan starts — because a fixture that
scans in milliseconds cannot see a cost that is duration.

### ⇒ THE READER'S SKIPS WERE UPSTREAM OF THE COUNTERS THAT EXIST TO SEE THEM

`ScanResult`'s docstring says a silently-skipped document *"is republished three stages later as
`absent_from_dailymed` — a fact about the READING sold as a fact about the RELEASE"*. Two branches in
`iter_release_labels` did exactly that with a bare `continue` **inside the generator**, before
`documents_read += 1` in `scan_release`: a member that is not a zip, and a member zip with no XML. Neither
`documents_read` nor any drop counter could see them, so `check_scan_dropped_nothing` was structurally
incapable of refusing. *"All counters measured zero"* was a measurement over the documents that reached the
counters.

`scan_release` and `iter_release_labels` had **no direct test at all**; six mutations survived them, including
never incrementing `documents_read` and dropping the `.xml` filter entirely. All eight fixture XMLs were
well-formed with exactly one matching `setId`, so the three counters were pinned at zero **by construction** —
the suite reproduced the release's measured zeros without ever showing a counter could move.

Fixed: `on_skip(member, reason)`, three new `ScanResult` fields, and a fixture that builds the shapes the real
release is not known to contain. A member zip holding SEVERAL XMLs is now **refused rather than read from
`xml_names[0]`** — member order is not a rule, which `dedupe_by_set_id` argues at length two hundred lines
away. `skipped_not_a_member_zip` is counted but is **not** a drop: a manifest was never a label container, and
calling it a lost label is the same reader-versus-release confusion running the other way.

⇒ **THE TWO NEW DROP COUNTERS ARE UNMEASURED ON A REAL RELEASE, AND THAT IS STATED WHEREVER THE ZEROS ARE
CLAIMED.** They are folded into `total_dropped`, so the first real run after this change **may refuse where the
previous one succeeded** — a member zip this reader cannot read is a lost label, and the alternative was losing
it silently, but until a release has been scanned with them in place that is an inference. This round's own
review caught the module docstring having been widened from *"all four"* to *"every one"* without the re-run
that would justify it. #162's three remaining skips are deliberately NOT folded in for the same reason.

### The numbers db/051 shipped into the DATABASE CATALOG were the design's, not the measurement's

`db/051` was written against the design round's route census and `COMMENT ON`'d it into the catalog, where
`\d+` and every consumer reads it. The measurement then contradicted it and only `docs/` was updated. The
`unresolved` comment said **14,680** where the answer is **92** — the figure this slice's own headline is
about. `db/052` corrects them, plus a `COMMENT ON COLUMN` that named `drugref.ingest.spl_run.SUBJECT_ROUTES` as
the vocabulary's second home: **that attribute does not exist** (the tuple is in `spl_subject`), so the pointer
whose entire job is naming the other home did not resolve. A catalog comment is not a schema edit, so the
correction is a new numbered file and `db/051` stays immutable.

### ⇒ AND THE FIX ROUND'S OWN REVIEW FOUND THE SAME SHAPE AGAIN, TWICE

Reviewing the fixes before committing them caught two defects **of the exact kind the fixes were written to
remove**, which is the third consecutive round in this slice where that has happened:

- **The new `Registry` type broke two committed tools, and the suite stayed green.** `spl_class_vocabulary
  _delta.py` and `spl_suppress_derive.py` both kept `names, uniis = load_registry(conn)` against a type that
  now has four fields, so both raised `ValueError: too many values to unpack` on their first line of real
  work. Neither tool has a test. One of them is the measurement `spl_match`'s own docstring cites as the
  evidence for deferring the class vocabulary — **the round added `KIND_CLASS` specifically so that tool would
  keep working, and broke it one function away.** `Registry` is now a frozen dataclass rather than a
  `NamedTuple`: a type that cannot be destructured at all fails at EVERY call site the moment its shape
  changes, instead of only at the ones whose arity stops matching.
- **The brand-new entity guard shipped an assertion that passed with the guard deleted.** `<! ENTITY` (with a
  space) is not well-formed XML, so `ET.fromstring` raises anyway and the assertion never exercised the guard.
  Worse, the guard itself was a bare byte search for `<!ENTITY`, which **matches inside a legal XML comment**:
  `<!-- <!ENTITY a "x" --><d/>` parses cleanly, so one such comment anywhere in 41,056 documents would have
  been counted as a drop and aborted the whole ingest. The guard now matches `<!DOCTYPE`, where an entity
  declaration is the only thing that can legally live, and both the refusal and the non-refusal are watched.

⇒ *A round that has just written five tests to kill five surviving mutants is not thereby immune to writing a
sixth that survives. Review the fix the way the thing being fixed was reviewed.*

### Traps and standing notes

- **A `COMMENT ON` correction is a migration, not an edit.** `db/051` is applied and immutable; `db/052` carries
  only `COMMENT ON` statements and changes no object's shape.
- **`ScanResult`'s counters take no defaults, deliberately.** A counter defaulting to zero is one a future field
  can be forgotten out of, which is precisely how two of them failed to exist for a whole slice. The
  convenience lives in the test helper `_scan(**overrides)`.
- **A schema test that drives the writer stops being a schema test once the row type validates.**
  `QuoteRow` and `SubjectRow` now refuse the states db/051's CHECKs refuse, so the tests proving those CHECKs
  work were rewritten to `INSERT` raw SQL. The CHECK's job is binding a FUTURE writer that will not use these
  types; attacking the table directly is the only faithful way to show it holds.
- **`Entry` gained a third kind, `KIND_CLASS`**, because `tools/spl_class_vocabulary_delta.py` legitimately
  builds class entries and the new `(kind == moiety) == (moiety_uuid is not None)` check would otherwise have
  refused the committed tool that measures what excluding classes bought.
- **`_ENTITY_DECL`**: `ET.fromstring` expands internal entities and SPL is third-party content. Refused in the
  bytes rather than through a parser callback — `xml.etree`'s C parser exposes no handle for it, and every
  alternative means parsing the document first, which is the thing being avoided.
- **`read_pairs` is deliberately UNSCOPED and now checks its precondition.** `spl_ddi_pair` GROUPs without
  `ingest_run`, so a scoped variant would re-derive the pair grain — a second home for the definition of a
  pair, in the one place the floors assert. It counts the published view instead, and verifies that no SPL
  label row belongs to another run.
- **Filed rather than fixed**: [#162](https://github.com/cairn-ehr/drugref/issues/162) three reader skips still
  uncounted (each needs a real-release run to know whether folding it into `total_dropped` would start refusing
  legitimate releases) · [#163](https://github.com/cairn-ehr/drugref/issues/163) the openFDA arm cannot tell
  "no section" from "section present but blank" · [#164](https://github.com/cairn-ehr/drugref/issues/164)
  db/051's unreachable NULL guard, in the comment block that forbids exactly that ·
  [#165](https://github.com/cairn-ehr/drugref/issues/165) frozen dataclasses over live dicts, and
  `Match.ambiguous` counting entries rather than moiety entries ·
  [#166](https://github.com/cairn-ehr/drugref/issues/166) no size cap on nested release zips.

## The reader-skip census round (2026-08-31) — issue #162, no migration, `spl_release.py` split out

**[Measurement record](superpowers/specs/2026-08-31-drugref-spl-reader-skip-census.md).** The round HANDOVER
called *"a measurement, not an edit"*, and it stayed one: `tools/spl_skip_census.py` reads all six DailyMed
parts in **163.6 s** with no database and no target set, and every verdict below is read off that pass.

### ⇒ THE STANDING RISK IS RETIRED, AND IT WAS ALREADY ANSWERABLE FROM TWO PUBLISHED NUMBERS

PR #161's review folded `dropped_no_xml_member` and `dropped_several_xml_members` into the total
`check_scan_dropped_nothing` aborts over, while its own docstring conceded they were **UNMEASURED on a real
release** — so `main` might have refused the very release the previous run read. **All three member-level
counters are ZERO** (`not_a_member_zip` too), so it does not.

**And the answer was already sitting in two published numbers nobody had put side by side.** The results record
of 2026-08-27 states **54,813 documents read**; the six parts' central directories hold exactly **54,813 outer
members**, every one a `.zip`. A member skipped for *any* of the three reasons yields no document, so the two
being equal already implied all three counters were zero — derivable in seconds from the zip directories,
without reading 17.6 GB. ⇒ *Before measuring, check whether the measurement has already been published in two
halves.*

### ⇒ THE FIX ISSUE #162 PROPOSED WOULD HAVE ABORTED THE INGEST ON ITS OWN CORPUS

#162's suggested shape was *"count all three; fold 2 and 3 into `total_dropped`"*. Applied literally, **case 3
refuses this release**: the release carries `COLR` **ten times**, `total_dropped` would have been 10, and the
guard aborts before the run row exists. The slice would have lost its ingest to a guard added to protect it —
which is exactly the risk #162 named when it said this needed a measurement rather than an edit, and exactly
why #161 did not fix it.

**What `COLR` is, established from the release rather than from an HL7 table.** All ten sit on three labels in
part 3, name a colour (`WHITE`, `RED`, `BLUE`, `YELLOW`), and **none carries a `<code>` element at all** — so
not one could have contributed a subject even if the code were admitted as active, because `_unii_of` requires
a `<code>` whose `codeSystem` is FDA SRS. It is now in `_DOCUMENTED_INACTIVE_CLASS_CODES`: ruled on, not merely
tolerated.

So the shipped guard is keyed on **the condition that harms** rather than the cause imagined — the same lesson
`db/038`'s detector round recorded:

- an unknown classCode **carrying a UNII** is a DROP (a future ACTIVE code looks exactly like this, and with
  only a 2.3% margin over the pair floor a small silent degradation passes every downstream check);
- an unknown classCode **carrying no UNII** is REPORTED and not refused — `COLR`'s measured shape.

### The census, in full (54,813 documents, Human Rx 2026-08-21)

| branch | count | verdict |
| --- | --- | --- |
| `not_a_member_zip` | 0 | reported, not a drop |
| `no_xml_member` · `several_xml_members` | 0 · 0 | drops — **were shipped unmeasured** |
| #162 case 1 — pre-filter setId ≠ the document's own | 0 | now a drop |
| #162 case 2 — unparseable `<versionNumber>` | 0 | now a drop |
| #162 case 3 — classCode outside the vocabulary | **`COLR` × 10** | **reported, NOT a drop** |
| `no_set_id_in_bytes` · `doctype` · `parse_error` · `no_set_id_in_tree` | 0 each | unchanged |
| labels with no `<versionNumber>` at all | 0 | — |

classCode histogram: `IACT` 635,954 · `ACTIB` 79,207 · `ACTIM` 21,075 · `ACTIR` 2,849 · `INGR` 1,827 ·
`CNTM` 556 · **`COLR` 10**.

### Case 1 is closed at its CAUSE, not only at its outcome

`set_id_in_bytes` takes the FIRST `setId` in the bytes, and SPL's `<relatedDocument>` carries a `setId` of its
own naming the label being replaced. `scan_release` compared pre-filter against tree **only for documents
already in `targets`**, so a document mis-named OUT of `targets` was skipped before any comparison — the
module's *"the pre-filter is never the authority"* held for the in-targets case alone. Both halves measured
zero: the **outcome** (pre-filter ≠ tree setId, over every document rather than only targeted ones) and the
**cause** (`<relatedDocument` before the first `<setId`). `spl_dailymed.prefilter_is_trustworthy` now tests the
cause in bytes already in memory — no tree is built — and an untrustworthy non-target is a **drop** rather than
a recovery, deliberately: recovery would be a policy invented against zero observations, while refusing
surfaces the condition to a human who can decide it with real data in hand.

### A counter nobody reports is a silent skip with extra steps

`skipped_not_a_member_zip` has been documented as *"counted and reported"* since it was added and was
**reported nowhere** — no `say()`, no summary field. `skipped_unknown_class_code` would have inherited exactly
that, which would have made admitting `COLR` to the vocabulary a way of HIDING it rather than of ruling on it.
`spl_release.describe_reported_skips` is the one line that prints them, empty when there is nothing to say
because a line of zeroes on every run is a line nobody reads.

### `spl_release.py` was split out of `spl_dailymed.py` (rule 4)

`spl_dailymed.py` was at 491 lines and this round needed ~100 more. The seam was already there: one module
reads ONE label's XML and never opens a zip; the other walks a release's nested zips and never looks at a drug.
`ScanResult`, `scan_release` and `iter_release_labels` moved **verbatim** first, with the whole suite green
before a single counter was added, so refactor risk and behaviour change were never mixed in one step.
`spl_dailymed.py` is now 411 lines and `spl_release.py` 275.

### Verified with the SHIPPED code on `drugref_spl162` (TEMPLATE `drugref_spl` → migrate → ingest spl)

A census is a probe, and the counters that refuse a run are new code — so the ingest was re-run end to end.
**10 min 43 s** against the ~12.5 min the ingest round published, so the per-document trustworthiness check
costs nothing measurable (it is bounded with `endpos` to the bytes before the selected `setId`; searching whole
documents would scan 17.6 GB a second time). **It did not abort**, so every new drop counter is zero on the
real release as measured by the code that ships. No reported-skip line printed, so both report counters are
zero for this run. Nothing that had no licence to move moved: `spl_ddi_pair` **29,952** (26,598 novel) ·
`spl_label_subject` **73,867** · `spl_wording_quote` **138,187** · `spl_entity_occurrence` **1,297,944** ·
routes `openfda_unii` 27,494 / `dailymed_active_moiety` 10,555 / `dailymed_active_substance` 23 /
`absent_from_dailymed` 30,386 / `unresolved` 92 — every one reproducing 2026-08-27 exactly.

### Traps and standing notes

- **The shipped counters and the census count DIFFERENT POPULATIONS, and the run turned that from a caveat
  into a number.** `skipped_unknown_class_code` is **0** while the census counts `COLR` **10 times**. Both are
  right: the shipped counter is scoped to the documents the scan reads a subject from — **10,670 is the
  DE-DUPLICATED label count**, and the document count behind it is higher by the labels shipping several
  versions — and `COLR`'s three labels are not among the 41,056 targeted; the census is release-wide over all
  54,813. Comparing them as
  one number finds a discrepancy that is not there — the mistake the design round made when it filed 14,455
  never-read labels into a bucket meaning *"read"*.
- The census re-parses each tree because `extract_subject_uniis` folds three situations into one `None`.
  `test_the_census_NEVER_disagrees_with_the_shipped_reader` pins that second parse as a REFINEMENT of the
  shipped one and never a rival — this project has published seven wrong figures from partially-working probes.
- Both class-code vocabularies are READ at call time, never restated, in the probe as well as the library: a
  vocabulary with two homes is the defect this slice has now found **five** times — the fifth being this
  round's own census tool, below.

### ⇒ THE REVIEW OF THIS ROUND: SIX DEFECTS, ALL IN THE CODE THE CENSUS COULD NOT CHECK

Spec §6a is the record. The measurement stood in full; every finding was in the **new code written in response
to it**, which the census had no way to test because the census was written first.

- **The vocabulary drifted into two homes inside one commit.** `COLR` went into
  `spl_dailymed._DOCUMENTED_INACTIVE_CLASS_CODES` and *not* into `tools/spl_skip_census`'s retyped copy —
  three lines beneath a comment explaining that a vocabulary with two homes is the defect this slice keeps
  finding. Re-running the census would have reported `COLR` as unruled: **the instrument contradicting the
  verdict it had produced.** Both sets are read at call time now, and
  `test_the_INACTIVE_vocabulary_is_READ_not_retyped` moves each frozenset in turn.
- **The census disagreed with the shipped reader on `<versionNumber/>`** (element present, no `value`): junk to
  the reader — a drop that aborts the run — and "absent version" to the census, a benign context line. The
  pinning test compared `version`, `None` on both sides, and never the junk verdict. Since the census's
  `junk_version = 0` is the *sole* evidence licensing that drop, the instrument certifying the guard could not
  see one of the conditions the guard refuses over.
- **`total_dropped` could exceed `documents_read`.** The three document-level counters fell through instead of
  `continue`-ing, so one document tripping two was two drops *and* stayed in `found`.
- **Three shapes lost a label with every counter clean**: an unknown `encoding=` raises `LookupError`, not
  `ET.ParseError`, and aborted the whole scan naming nothing; a corrupt member zip raised `BadZipFile` out of
  the generator, likewise unnamed; and membership was decided by a `.zip` **suffix**, so `M.ZIP` was filed under
  `not_a_member_zip` — the one member bucket that does not refuse.
- **The counters could be mis-bound undetectably.** Every counter in the fixture was seeded with exactly **1**,
  so swapping two at the construction site passed all 2402 tests. They are 1/2/3/4 now.

⇒ **A CENSUS RETIRES A RISK ABOUT THE CORPUS; ONLY A TEST RETIRES ONE ABOUT THE READER.** Four of the six are
conditions the 2026-08-21 release does not contain, so no amount of reading it could have surfaced them. This is
the sharper form of the lesson the round already carried about guards: measuring the world tells you nothing
about the code you wrote to measure it.

Also fixed while in there: `dedupe_by_set_id`'s `(row.version or -1)` collapsed **version 0** into the
no-version sentinel; the unknown classCodes are now **named** in both the refusal message and the reported-skip
line (case 3 is reported precisely so a human can rule on the code, which requires knowing which code);
`describe_reported_skips` rides on `SplSummary` rather than only through `say()`, which is a no-op whenever
`progress` is None — every library caller and every test; `on_skip` is now **required** rather than defaulting
to a silent discard; and `_scan` had been written twice, so it lives once in `tests/conftest.py`.

Deferred as issues: **#168** (three more homes of one vocabulary and a second `iter_release_labels`, all
pre-existing in `tools/`), **#169** (a `SkipReason` enum, when the 12th counter arrives), **#170** (SPL version
spelled three ways), **#171** (a census crash on the last part discards every part already counted).

## The COPY-cost round (2026-09-01) — issue #160, no migration, the ingest 12m51s → 2m09s

[Measurement record](superpowers/specs/2026-09-01-drugref-spl-copy-fk-plan.md). Two calls added to the
orchestrator and one function to the writer; **no migration, no schema change, no figure moved.**

**⇒ FIRST, THE RE-VERIFICATION THE LAST ROUND OWED.** The census round's end-to-end run predated its own
review's fixes to the reader, so the shipped code had never been run against the real releases in its current
form. It was, on `drugref_spl160`, and **every published figure reproduced exactly** — 68,550 labels of 262,032
records, 27,406 wordings, 29,952 pairs (26,598 novel), 73,867 subjects, 1,297,944 occurrences, 138,187 quoted
windows, `source_checksum` `5d6a894b30ce…`, all five route tallies. 12 min 51 s against the census round's
10 min 43 s, on a machine with other work on it.

**⇒ THE CONTROL FOR ISSUE 160 WAS ALREADY INSIDE THAT RUN, AND NOBODY HAD LOOKED.** Polling
`pg_stat_activity` once a second — which times each statement **without modifying the code under
measurement** — gave: `COPY spl_label_subject`, 73,867 rows, **630 s**; `COPY spl_entity_occurrence` +
`spl_wording_quote`, 1,436,131 rows, **35 s**; `COPY spl_label`, 68,550 rows, **6 s**. *19.4× more rows in 18×
less time, same transaction, same writer, same client.* Two of the three causes the issue listed as untried die
on that one line: not the row volume, and not `COPY`.

**⇒ THE CAUSE CAME FROM A STACK SAMPLE, NOT FROM THE HYPOTHESIS LIST.** `sample <backend> 8`:
**6,748 of 6,748 samples** under `RI_FKey_check_ins` — the foreign-key check, fired as an after-row trigger —
and inside it in heap fetches and visibility checks, with `ReleaseAndReadBuffer` walking page to page. **A
FOREIGN-KEY CHECK IS A QUERY**, and the planner may satisfy `WHERE ingest_run=$1 AND source=$2 AND set_id=$3
AND version=$4 FOR KEY SHARE` with **any** parent index whose leading columns those quals cover. `spl_label`
carries two — `spl_label_pkey` on all four, `spl_label_by_wording` on `(ingest_run, source, text_key)`. On a
freshly `COPY`d parent (`relpages = 0`, `reltuples = -1`) **both plans cost an identical `8.44`**, and the tie
landed on `spl_label_by_wording`, whose index condition matches **all 68,550 rows** — `ingest_run` and `source`
are constant for the whole load — and discards 68,549 in a filter, once per child row.

**⇒ THE FIX IS 112 ms AND BUYS 365×.** One-variable ablation, full scale, each variant in its own fresh clone
so B does not inherit the `relpages` A's rollback leaves behind: **493,539 ms** as shipped against **1,352 ms**
with `ANALYZE spl_wording; ANALYZE spl_label` (11.8 + 99.8 ms) inserted. End to end on the real releases,
`drugref_spl160fix`: the subject `COPY` **630 s → ~2 s** and the whole ingest **12 min 51 s → 2 min 09 s**,
with every count, both checksums and all five routes identical.

**⇒ THE RULE, AND IT IS NOT "ANALYZE AT THE END":** *a table is analysed as soon as it is loaded and **before
anything that references it** is loaded.* The plan is chosen at first use — inside the load — so
`analyze_source_tables` at the end of the run, which exists for the read-backs and is still needed for them,
**arrives after the `COPY` has already paid for the bad plan**. ⇒ *Not* because a plan is cached for the
session: the review round measured that an `ANALYZE` after first use DOES re-plan (4,874 ms → 15.7 ms in one
transaction), so the reason is ordering in time, not plan lifetime. `spl_evidence.analyze_loaded_table`
carries the mechanism and the numbers.

**⇒ AND THE REFUTATION THAT CLOSED THE RIGHT DOOR FOR A ROUND.** `analyze_source_tables`'s docstring said the
foreign key had been *"measured and REFUTED: 20,000 child rows against an unanalyzed 68,550-row parent insert
in 175 ms, **because PostgreSQL's RI triggers use a plan pinned to the parent's primary key rather than a
re-planned query**"*. The measurement was real; **the reason is false**, and it is the half that got quoted
forward. The plan is pinned — to whatever was chosen at FIRST USE, before any `ANALYZE`. *Pinned is not the
same as pinned to the primary key*, and the 175 ms did not generalise because a parent whose only index is its
primary key has no wrong plan available to pin. ⇒ **A REFUTATION IS A MEASUREMENT PLUS AN EXPLANATION, AND ONLY
THE MEASUREMENT WAS TAKEN** — reasoning in the voice of the measurement beside it, in a docstring, which is the
most durable place in this repo to put a wrong sentence. The same round that wrote it had caught and removed
exactly this in its own `+13%` paragraph.

**⇒ AND ALL THREE OF THE ISSUE'S OWN CANDIDATES WERE WRONG** — `COPY` vs `INSERT`, ICU collation on
`set_id`/`version`, drop-and-rebuild indexes. No amount of ablating them would have reached the fourth. **Where
a cost is concentrated in one statement, sample the process before designing an experiment about it**: eight
seconds of `sample` beat a list of three hypotheses that had stood for five days.

**The exposure is censused, not assumed.** Over all **138 foreign keys in schema `drugref`**, exactly **one**
parent carries an index whose leading columns are a proper subset of the referenced columns:
`spl_label_subject → spl_label` via `spl_label_by_wording`, 2 of 4. Every other parent in the schema has only
its primary key, which is why no other feed has shown this — and why the `ANALYZE` guarantee is made for every
parent rather than for the one that failed: **the exposure is created by adding an index to a parent, an edit
nowhere near the orchestrator.** `test_ONE_foreign_key_in_the_schema_can_be_planned_onto_a_LOOSE_index` pins
the census; `test_a_FK_PARENT_is_ANALYZED_BEFORE_THE_CHILD_THAT_REFERENCES_IT_is_loaded` pins the cause (a
fixture of three wordings cannot show a 630-second stall), and **both mutants — each `ANALYZE` deleted in turn
— were run and killed.**

### ⇒ AND ITS REVIEW ROUND, WHICH IS THE HALF WORTH READING

The fix was correct, complete and correctly ordered, and **no code defect was found**. Everything below is a
sentence that was wrong or a guard that did not guard — and the round had just finished writing down the rule
about exactly that.

**⇒ THE ROUND COMMITTED ITS OWN META-RULE'S ERROR WHILE WRITING IT DOWN.** `analyze_loaded_table`'s docstring
said the RI plan is *"CACHED for the rest of the session, so analysing afterwards cannot repair it"*. **Measured
and false.** In ONE session and ONE transaction, on a replica of `spl_label`'s shape at 68,550 parent rows:
3,000 child rows at first use against the unanalyzed parent took **4,874 ms**; then `ANALYZE` of the parent;
then the next 3,000 took **15.7 ms** and the next **14.0 ms**. RI plans are SPI plans and participate in
relcache invalidation, so `ANALYZE` invalidates them — analysing afterwards **does** repair the plan; it simply
cannot refund the rows already written. The rule stands and the ablation stands; only the mechanism sentence
bolted onto them was invented — one paragraph after the paragraph retracting an invented mechanism from the
same docstring. ⇒ **A ROUND IS MOST LIKELY TO COMMIT THE FAILURE IT IS CURRENTLY NAMING**, because the failure
is on its mind as a thing to *describe*, not as a thing to *avoid*.

**⇒ THE GUARD ADMITTED THE EXACT STATE IT EXISTED TO FORBID.** `reltuples >= 0` is satisfied by **0.0**, which
is what `ANALYZE` of a still-EMPTY table writes — and 0.0 is not a milder form of the bug, it *is* the bug: an
empty parent carries `relpages = 0` exactly as a never-analysed one does, so it pins the same catastrophic plan.
Two mutants lived under `>= 0`: analysing a parent **before** its own write, and replacing both calls with one
`analyze_source_tables` after `clear_source_spl` — the tidy-up a future reader is likeliest to attempt. It is
`> 0` now, and **four** mutants are run against it rather than two; all four are killed.
⇒ **A GUARD WRITTEN AS "HAS STATISTICS" WHEN IT MEANS "HAS STATISTICS DESCRIBING ITS ROWS" IS OFF BY THE BUG.**

**⇒ AND ITS COVERAGE WAS HAND-LISTED, SO ITS "EVERY" WAS FALSE.** The test named three writers and claimed every
in-source foreign key. There are **four** — `spl_wording_quote → spl_wording` was missing — and one of the three
watches was **inert**, because two keyed on the same parent and `setdefault` kept the first. So the headline
`assert set(seen) == …` would have passed with `write_occurrences` never called at all. The edges are now read
from `pg_constraint` at the `_copy` chokepoint, which every writer goes through, so a child table added to the
orchestrator is covered the day it is added. ⇒ **A HAND-WRITTEN LIST CANNOT SAY "EVERY"; ONLY THE CATALOG CAN.**

**Two more sentences that were measured false**, both in this round's own prose: *"a parent whose ONLY index is
its primary key -- which is every other parent in this schema"* — **26** of the schema's FK parents carry a
non-primary-key index, and the property that matters is the narrower one the census encodes; and *"the
statistics … are rolled back with it if the run is refused"* — `pg_statistic` rows do roll back, but
`pg_class.relpages`/`reltuples` **survive** (`vac_update_relstats` writes them in place), which the measurement
record itself relied on two sections earlier when it gave each ablation variant a fresh clone.

**Filed rather than fixed:** **#174** — `ANALYZE` on a table the ingest role does not own emits a **WARNING**,
skips, and **returns success**, and psycopg discards notices unless a handler is installed (which nothing in
`src/` installs, a discard `drugcentral_run.py:211` already documents from an earlier round). Under an
admin-migrates/app-role-ingests split the whole #160 fix reverts at runtime, invisibly, and the ingest still
reports success — `reconcile`, `read_pairs` and `check_floors` check counts, not plans.

**And rule 4 broke, as #172 predicted it would.** `spl_evidence.py` went 494 → **512**, because each of the three
false sentences cost more lines to correct than it did to assert. Left at 512 and recorded on #172 rather than
shaved back under the cap: trading measured content for assertion-without-evidence is the failure this whole
round is about. ⇒ **A LINE CAP IS A BUDGET FOR CODE, NOT FOR EVIDENCE.**

**One measurement trap worth keeping.** macOS `ps -o pcpu` reports a **lifetime average**, not an interval, so
a backend that burned a core an hour ago still reads high and one burning it now can read low. The attribution
here ("96% of a core, and the backend's whole lifetime CPU is this statement") came from diffing cumulative CPU
time between two `ps` samples. **`pcpu` cannot answer "who is burning the CPU right now", which is the only
question that matters here.**

## The ingest-duration round (2026-09-02) — issue #159, `db/053`

[Measurement record](superpowers/specs/2026-09-02-drugref-ingest-run-duration.md). One dataclass and one
required argument in `provenance.py`, one line at the top of each of eleven orchestrators, one migration, one
CLI column. **No projection changed and no published figure moved** — the SPL ingest reproduced all of them
from an empty database.

**⇒ WHAT WAS WRONG, AND WHY IT WAS WRONG FOR EVERY FEED AT ONCE.** `started_at DEFAULT now()` and
`finish_run`'s `now()` are both `transaction_timestamp()`, and `open_run` COMMITS (db/025's design), so the two
stamps belong to two DIFFERENT transactions. The subtraction measured **the gap between two transaction start
times** — the interval between `open_run`'s INSERT and the work transaction's first statement, which is the
time the orchestrator spent NOT touching the database. Eight of nine feeds read 1.3–24 ms; the ninth,
`mesh_rel_run` at 48.32 s, was reporting how long it takes to parse 750 MB of MeSH before its first write.

**⇒ THE ISSUE'S OWN HEADLINE EXAMPLE HAD ALREADY EVAPORATED, AND NOBODY LOOKED.** #159 was filed from
`drugref_spl051`, where `spl_run` read **49.85 s** and the issue explained it correctly. Five days later the
COPY-cost round put a `conn.rollback()` in front of the DailyMed scan — to close a ~50 s `idle in transaction`
window — which moved `open_run` to the far side of the scan and collapsed that figure to **0.0026 s**.
Measured on both databases that round built. The issue, the suite and that round's own review all read past it.
⇒ **A NUMBER IN A FILED ISSUE IS A MEASUREMENT WITH NO OWNER**: the round that moves it is not the round that
reads it, so re-measure an issue's premise before designing against it. Eight of nine figures were unchanged
here; the ninth was gone.

**⇒ WHAT SHIPPED.** `provenance.RunClock` / `start_clock()` — a frozen dataclass over `time.monotonic()`
(monotonic so an NTP step mid-ingest cannot produce a negative duration; **a TYPE rather than a `float`**
because `open_run` cannot tell `time.monotonic()` from `time.time()` by looking, Python enforces no annotation
at runtime, and the two differ by 56 years). `open_run` takes a required keyword-only `clock` and writes
`started_at = clock_timestamp() - make_interval(secs => %s)`: **only the ELAPSED INTERVAL crosses from the
client, never a client timestamp**, so both ends of the subtraction are the server's clock and an ingest driven
from a host whose clock is out still records a true duration. `finish_run` writes `clock_timestamp()`, its
no-commit contract untouched — so the duration **excludes the caller's final COMMIT**, which is 3.8 s for SPL
(the deferred quote-budget trigger over 138,187 windows) and is named in the column comment rather than left to
be found.

**⇒ THE VERIFICATION IS THE RATIO, MEASURED NINE TIMES.** A fresh `drugref_dur159` (NOT a template — a
template carries nine rows written under the old meaning), all nine feeds, `/usr/bin/time -p` against
`finished_at - started_at` read back afterwards: `ingest chain` (five feeds) **137.46 s recorded / 137.82 s
wall = 99.7%**, `drugcentral` 19.64 / 20.00, `onchigh` 3.87 / 4.26, `fda-cyp` 4.11 / 4.44, `spl` **135.86 /
140.06 = 97.0%**. The residual is the interpreter start plus argparse plus `db.connect`, **measured at
0.29–0.34 s** (two mis-quoted invocations that died in argparse), and for SPL the final COMMIT on top.
`spl_run` went **0.0026 s → 135.86 s**, a factor of 51,657.

**⇒ `mesh_rel_run` IS THE INTERNAL CROSS-CHECK.** Its old number, 48.32 s, is now a SUBSET of its new one,
56.81 s: the parse is 48 s and the writes are 9 s. Nothing about that orchestrator changed. Had the diagnosis
been wrong the two numbers would bear no such relation.

**⇒ THE CHECK FOUND TWO LIVE OCCURRENCES OF THE IDIOM THE ROUND WAS REMOVING — IN THE SUITE.** `db/053` adds
`CHECK (finished_at IS NULL OR finished_at >= started_at)`, and five tests failed the moment it existed, all
with a row that **finished 3.8 ms before it started**: two helpers (`test_ingest_observability._run`, one
INSERT in `test_releases.py`) stamped `finished_at = now()` while letting `started_at` take the default db/053
had just changed to `clock_timestamp()`. ⇒ **MIXING `now()` AND `clock_timestamp()` IN ONE TRANSACTION PRODUCES
A NEGATIVE DURATION**, and without the constraint both helpers would have gone on producing one silently. A
constraint added "for completeness" caught the round's own blind spot.

**⇒ OLD ROWS ARE REFUSED AT THE OPERATOR SURFACE, NOT ONLY IN A COMMENT.** Nothing rewrites rows written
before db/053 and nothing could — what would be needed was never recorded — so `drugref status` prints
`pre-db/053` instead of a runtime for them, reading the watershed out of the migration ledger
(`db.migration_applied_at`, which shares `migration_applied`'s three-digit prefix guard through a new
`_ledger_pattern`). Subtracting two transaction stamps still yields a number, and **a number is what an
operator believes**. Both paths verified without patching any verification database: `drugref_spl160fix` with
db/053 unapplied (unknown watershed ⇒ everything refused, the safe direction), and `drugref_dur159mixed` — a
fresh clone of it, then `migrate` — which is the production upgrade path: **db/053 applied cleanly over nine
pre-existing rows and the CHECK validated all nine**, as it had to, since `open_run`'s transaction always
commits before the work's begins.

**⇒ THE DERIVED CONTRACT AND THE MUTANT IT COULD NOT KILL — REPLACED IN THE REVIEW ROUND.**
`test_every_module_that_opens_a_run_takes_a_clock` grepped the tree for `provenance.open_run(` and required
`provenance.start_clock()` in the same module — **eleven modules, derived rather than counted by hand**, one
round after a hand-listed coverage named three writers where four edges existed. It passed unchanged against
the mutant that moves `start_clock()` down to the line above `open_run`, which measures nothing, **while its
own docstring claimed to catch exactly that**. The one behavioural killer,
`test_a_run_records_the_work_done_before_it_opened`, injects a delay into work `ingest_unii` does BEFORE
`open_run` — the CHEAPEST feed — so the mutation stayed invisible in `spl_run` (108 lines and a 17.6 GB scan
above its `open_run`) and `mesh_rel_run`, the two writers every figure in this section comes from. The grep
also matched a **comment** in `onchigh_run.py`, so a module could have satisfied it with no call at all.

Replaced by `test_every_orchestrator_starts_its_clock_on_its_very_first_line`, which **parses** each module and
asserts `start_clock()` is the first executable statement (docstring aside) of every function that starts a
clock — exact for all eleven, mutation-verified by moving `spl_run`'s call down and watching it fail. It also
retires eleven copies of a `# FIRST:` comment as the thing holding the rule.
⇒ **A DERIVED CHECK OUTLIVES A HAND-LISTED ONE ONLY FOR WHAT IT DERIVES — AND A GREP DERIVES TEXT, NOT
STRUCTURE.**

**⇒ THE REVIEW ROUND: THREE SHIPPED DEFECTS, ONE DEFERRED, AND WHAT EACH ONE TEACHES.** Full account in the
[measurement record](superpowers/specs/2026-09-02-drugref-ingest-run-duration.md) § 8.

1. **`drugref status` crashed mid-output on a ledger-less database.** The watershed read was unguarded on the
   happy path, so a database built by replaying `db/*.sql` by hand — a shape `migration_guard`'s own docstring
   names as reachable — printed one header, then a psycopg traceback, and skipped five of six blocks. ⇒ **A
   READ ADDED TO A DIAGNOSTIC COMMAND IS A NEW WAY FOR THAT COMMAND TO FAIL**, and `status` is the command an
   operator reaches for precisely when the database is the wrong shape. Fixed with `to_regclass`, deliberately
   NOT `db.missing_relations` — that helper rolls back before probing, which is right for its own callers
   (all inside `except UndefinedTable`, holding an already-aborted transaction) and would silently discard a
   caller's open transaction here.
2. **The `started_at` catalog comment refuted itself in nine words.** *"every one of the nine feeds reported
   between 1.3 ms and 24 ms … and the one that reported anything else"*, while all four documents said *eight
   of nine*. ⇒ **THE PROSE THAT SHIPS INTO `pg_description` NEEDS THE SAME REVIEW AS THE DDL AROUND IT** — it
   is the only documentation a `\d+` reader gets, and correcting it after merge costs a whole migration.
3. **`format_run_duration` printed `0m60s` / `1m60s` / `60m60s`** for any duration in `[N·60 − 0.5, N·60)`
   — 0.83 % of runs over a minute — because the `< 60` branch tested `round(seconds, 1)` while the minutes
   branch re-rounded the *unrounded* remainder. ⇒ **TWO ROUNDINGS OF ONE QUANTITY IS ONE RULE KEPT IN TWO
   PLACES**, the defect this project keeps finding, in arithmetic instead of vocabulary.
4. **Deferred as [#176](https://github.com/cairn-ehr/drugref/issues/176):** the watershed decides by **when** a
   row was written when the question is **which code** wrote it. An older client on a migrated database takes
   the new `clock_timestamp()` default for `started_at` and old `finish_run`'s `now()` for `finished_at`;
   neither the CHECK nor the refusal fires, and a two-second run publishes as `0.0s` — reproduced. ⇒ **A
   TIMESTAMP CANNOT ANSWER A QUESTION ABOUT CODE PROVENANCE.** A boolean set by `open_run` can. Not taken here
   because it rewrites the round's central mechanism after its measurements were verified; what WAS taken is
   removing every claim that the failure cannot happen.

Also in that round: `RunClock.__post_init__` (the `isinstance` check guarded the wrapper, not the value —
`RunClock(time.time())` committed a run dated 2083 and then lost the whole ingest to the CHECK); a
`COMMENT ON CONSTRAINT` naming all three causes including a **backward server clock**, which the migration had
denied outright; `gsrs_run`'s missing `except` (the only orchestrator of eleven without one, and db/053 gave
`finish_run` a new way to raise); and the loaded-release block moved to `cli_status.py`, which took `cli.py`
from 499 back to **477**.

### Traps and standing notes

- **`now()` IS NOT A CLOCK.** It is `transaction_timestamp()`. Two `now()`s in one transaction are equal by
  definition; two across a commit boundary measure the boundary. `clock_timestamp()` is the clock.
- **The clock is taken in the PUBLIC entry point**, not in the private `_ingest` body, for the five
  orchestrators that have both — the public function is where the command begins. It is threaded in as a
  parameter.
- **The window is the ORCHESTRATOR, not the command**: it starts at the orchestrator's first line (so
  `~0.3 s` of interpreter start and argparse sit outside it) and ends before the final COMMIT.
- **`cli.py` is 477 lines.** The runtime column took it to 499; the review round moved the whole loaded-release
  block to `cli_status.py` (238) rather than shave comments, which rule 3 forbids. That module exists for
  exactly this trade and this is its third block.
- **A GREP DERIVES TEXT, NOT STRUCTURE.** `test_every_orchestrator_starts_its_clock_on_its_very_first_line`
  parses each module with `ast` because the substring form matched a **comment** in `onchigh_run.py` and could
  not see a `start_clock()` moved next to `open_run`. Reach for `ast` whenever the property is about WHERE
  something is, not WHETHER it appears.
- **`db.missing_relations` ROLLS BACK before probing** — correct for its own callers, which all arrive inside
  `except UndefinedTable` holding an aborted transaction, and wrong for a happy-path probe, which would lose a
  caller's open transaction. Use `to_regclass` directly there.
- **Editing an APPLIED migration breaks every database carrying it**: `apply_migrations` is checksum-immutable
  and raises. db/053 was corrected in review while still unmerged, which is the only window in which that is
  free — after merge the same correction is a whole new migration.
- **A `COMMENT ON CONSTRAINT` is the only documentation an operator gets when a CHECK fires.** db/053 shipped
  three column comments and a view comment and forgot the constraint's, which is the one object a human meets
  BY NAME, mid-ingest, with a rolled-back run behind them.

## The standing open-issue ledger

**Moved here from HANDOVER by the PR #113 review round, and this is now its ONE home.** It lived in HANDOVER
for four rounds while HANDOVER's own header said *"put anything whose history is worth reading there, not
here"* — and the duplication had already cost: **#52's "422 broadened assertions" existed in the HANDOVER copy
and nowhere else**, so the bounded, deliberately-disposable file was the sole record of a figure a future slice
needs. HANDOVER now carries only what gates the NEXT session and points here.

**Examined by the debt round and deliberately NOT taken** — **#65** the issue itself says do not act until
curation scales · **#30 blocked: no PBS release on disk** (`downloads/` holds UNII, MED-RT, MeSH, GSRS only) ·
**#112/#105** blocked on class-grain content existing · **#89 `signing.py` is now 605 lines against the filed
582, and `release_verification.py` went 532 → 540** (rule-3 documentation for #87) — **re-read that issue's
figures, do not re-derive them**; `curation.py` **has since crossed the cap at 523** (the db/038 round, issue
115's docstring — it was 500 with no headroom when this line was written) · **#88** a type checker
is a real ongoing cost and a decision · **#82/#104** both change the operator surface, held back deliberately ·
**Filed by slice 5c.3's implementation round (2026-08-27)** — **[#159](https://github.com/cairn-ehr/drugref/issues/159)
`ingest_run.finished_at − started_at` is not a duration for ANY feed** — **CLOSED 2026-09-02 by `db/053`**, and
its own headline figure had already evaporated before anyone read it: the 49.85 s it cited for `spl_run` became
0.0026 s when the COPY-cost round moved `open_run` past the DailyMed scan. Both stamps are clock readings now
and the nine recorded durations account for 97–99.7% of each command's wall clock —
§ "The ingest-duration round". ·
**[#160](https://github.com/cairn-ehr/drugref/issues/160) the `spl_label_subject` `COPY` runs >4 min at 100%
CPU for 73,867 rows** — **CLOSED 2026-09-01, and by NONE of the three causes it named.** It was the foreign-key
check being planned onto `spl_label_by_wording` while the parent had no statistics; the fix is two `ANALYZE`s
costing 112 ms, and the ingest fell to 2 min 09 s. The issue's own ruling-out of the foreign key ("175 ms")
measured a real thing and explained it wrongly — § "The COPY-cost round".

**Filed by the COPY-cost round and its review (2026-09-01)** —
**[#174](https://github.com/cairn-ehr/drugref/issues/174) `ANALYZE` is skipped with a WARNING, not an error,
when the ingest role does not own the table**, and psycopg discards the warning: the #160 fix reverts at
runtime, invisibly, under an admin-migrates/app-ingests role split while the ingest still reports success. Two
halves to it — a postcondition in `_analyze`, and a notice handler in `db.connect` so server warnings stop going
to `/dev/null` across every orchestrator. ·
**[#172](https://github.com/cairn-ehr/drugref/issues/172) `spl_evidence.py` is at 512/500** — filed at 494 and
**breached by the review round exactly as it predicted**, the same shape as #130 (`cli.py` at exactly 500/500) one edit earlier, and
unlike `cli.py` this module has **no cap test**, so the breach would arrive silently. Not split in that round
deliberately: the census round's `spl_release.py` precedent is *verbatim move first, suite green, then the
behaviour change*, and splitting here would have mixed refactor risk into a fix whose whole value is that
nothing else moved. The seam and the cap test are named in the issue.

**#6, #25, #5** licence deeds need the owner's sign-off.

**Answered by measurement, still open** — **#19: the "41 vs 13" puzzle RESOLVES.** 41-of-739 was the TERMINOLOGY
grain; drugref holds **643** rules and the authoritative figure is **39 dead rules across 13 classes** — the
view's extra one is `Urease Inhibitors [MoA]`, whose only member is the rule's own subject (db/018 subtracts
it). **Two of its three asks already shipped.** · **#106: 46 of 21,370 pairs (0.22%) are reachable on two axes
and NONE is graded** — the shape is not live, and the 46 bounds the widening it proposes.

**Left open by 5c.2** — **#92 a mixed-kind class-pair rule expands to ZERO pairs silently** (the real fix is
schema-level: a rule naming two axes) · **#93 MED-RT carries no QT class** · **#94 the seven withheld entries**
need research. **#100 is CLOSED**: `ci_class_subtree`'s narrow definition is pinned from `pg_depend`,
mutation-verified against db/033's wide seed.

**Filed by 5c.4 and its review** - **#85 is CLOSED by `db/047`**: `signing_key_status_kind` is INSERT-only while
`signature_target_kind` remains free to move to a `/v2` · **#86 is implemented by `db/048`** with
`signed_by_unknown_key` as the fourth `signature_status` · #88 · #89. Unfiled:
`tests/test_cli_signing*.py` **cannot commit for real** — test isolation, shaped like #2.

**Filed by the PR #113 review, ALL FOUR NOW CLOSED by the db/038 round** — #114 (`effective_grades_for` had no
consumer → `drugref interactions`) · #115 (`total` → `rules_total`) · #116 (`effective_rank`, plus the
unrankable-severity detector) · #117 (`COMMENT ON` re-issued with seven). Full account: § "The db/038 round".
**#114 had to be REOPENED first** — it was auto-closed by `ed1ab5e`'s own "Filed rather than fixed: #114"
sentence, the fifth occurrence of that pattern and the second with that exact template.

**Closed by the guard round (2026-08-15)** — **118** (the `commit-msg` hook, which found a **sixth**
occurrence on its first run: #108 via `293758c`, uncounted for a round) · **120** (an unknown `moiety_uuid` now
banners and exits 2, via `registry_read`) · **122** (all four guards confirm the cause before asserting it, a
fifth guards the clinician path, and the LEDGER is what separates "not migrated yet" from "DROPPED"). Full
account: § "The guard round". **The unclosed half is now [#124](https://github.com/cairn-ehr/drugref/issues/124)**: GitHub
also parses PR DESCRIPTIONS, which no commit hook can see.

**#89's figures live ON THE ISSUE and nowhere else — do not re-derive them and do not restate them here.**
This paragraph used to carry the numbers, which made two homes for one set, and they had duly drifted:
`questions.py` was recorded here as **568** while the file was **664**. Re-measured at 5c.2g's `HEAD` and
posted to the issue, with the natural seam for `curation.py`. **`cli.py` is a separate issue
([#130](https://github.com/cairn-ehr/drugref/issues/130)) because its failure mode differs** — it sits at
exactly 500 against a HARD cap test, so the next line added to it breaks CI, and the cap has already begun
dictating where functions live rather than merely measuring size.

**Filed by the DrugCentral rounds (2026-08-23)** — **[#146](https://github.com/cairn-ehr/drugref/issues/146)**
the suite-count line in § "How to run / test" has drifted six times and is guarded by prose only; it wants a
test that reads the stated number and counts the collected suite (filed by the re-measurement round, recorded
here because this ledger is the ONE home and it had lived only in HANDOVER) ·
**[#148](https://github.com/cairn-ehr/drugref/issues/148)** `exact_ddi_pair` adds a THIRD population to the
ungraded cross-source disagreement question — **635 of the 7,501 DrugCentral pairs are already reachable
through MED-RT's class expansion and nothing compares them**, which is #97/#106 one tier down ·
**[#149](https://github.com/cairn-ehr/drugref/issues/149)** `fda_cyp_run.FDA_CYP_TABLES` is not registered in
`test_source_clear_contract.py`'s `EXPECTED_TABLES`, so a table dropped from that tuple would be caught by
nothing (pre-existing, found while registering `interactions.DRUGCENTRAL_TABLES`) ·
**[#151](https://github.com/cairn-ehr/drugref/issues/151)** `questions.py` is over rule 4's ~500-line
guideline and **71% of it is the one `_GAP_SOURCES` literal**, which grows with every source that adds a gap
kind — split out of #89 the way #130 was for `cli.py`, because the failure mode differs (a declarative table
with a visible seam, not dense prose with none). **Its figures live on the issue; do not restate them here.**
Full account: § "The DrugCentral ddi ingest".

**Filed by the 5c.3 SPL measurement round (2026-08-24)** —
**[#154](https://github.com/cairn-ehr/drugref/issues/154)** rule 6 for SPL section prose: NLM disclaims
(*"cannot guarantee the copyright status for any item"*) over labeling *"submitted to the FDA by companies"*,
while **openFDA dedicates the same bytes to the public domain under CC0 1.0** — the DIRIL shape, and it is a
posture call for the owner rather than a defect. The recommendation is to **reference the prose, not bundle
it**, which satisfies both readings and matches `db/045`'s citation-only SPL references ·
**[#155](https://github.com/cairn-ehr/drugref/issues/155)** MED-RT's PK axis is not a drug-class vocabulary:
**77,795 of its 80,042 matched occurrences (97.2%) name an EMPTY class**, its 59 concepts are pharmacokinetic
properties (`Clearance`, `Half-Life`, `Cytochromes`) and only 6 have a member, so matching them recognises
ordinary English and mints false positives carrying real class UUIDs. **Its figures live on the issues and in
§ "The 5c.3 SPL measurement round"; do not restate them here.** The round also **re-opened #102 in new terms**
— the potency band is pair-scoped, not class-scoped, which retires two of that issue's four options.

**Earlier rounds** — #81 chain-time variance (**its interleaved-control method is what the debt round used**) ·
#82 · **#75 `gap_uncurated_interaction_rule` costs ~2.7 s** — it is what both of that round's hot-path probes
were actually measuring · #65.

**Owned by 5c** — **#51 the 168 contradicted pairs**, which now also own the `spurious` deferral 5c.1 wrongly
handed to 5c.2 (ROADMAP § 5c.2) · **#52 the 422 broadened assertions** (no `concept_ui` on the row) · #55 ·
**#67 salt↔base strength equivalence has no source** · **#73 both views read every source at once** — for
`ddi_candidate_pair` that is now *wanted*, so **re-read it against 5c.2**; it is also why #19's third ask has a
scoping question · #20 n-ary interactions.

**From the slice-3 design** — **#68 3,631 moieties carry a GSRS `ACTIVE MOIETY` edge to something else** (~19%;
why 5c.2 expands salt forms on the projection side, not at read time) · #69 · **#70 354 all-false composites
reachable by nothing** · **#71 8,163 edges dropped, transiently counted**.

**Interaction model and identity** — #8 class-level `has_*` unused · **#36 discovery counts descendant classes,
not reachable members** · **#37 the DAG is expanded unprunably on every query** — restricting the *root set* is
safe, restricting the *walk* deletes the coagulation case · #48 · **#2 floor hardening** (`TRUNCATE` +
owner-role bypass; **13** `TRUNCATE`-ing modules depend on it — re-grep before quoting) · #3 UNII-change
immortality · #33 MeSH CAS keys name specific forms (behind #68) · #5 INN from UNII's `Display Name` ·
**#7/#29 row-at-a-time ingest** — the two MeSH legs are 75.7% of a 133 s chain.

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

**"Signed" was an overstatement in 5c.1 and is accurate again since 5c.4 (2026-08-10).** The 5c.1 design round
corrected the word to **signable** because no signing infrastructure existed — no key management, no signing identity,
no verification path. `db/030` built all three, so the tier is **SIGNED**: see § "Slice 5c.4" below and the
[signing the curated overlay](https://docs.drugref.org/decisions/signing-the-curated-overlay/) record. The constraint
5c.1 reasoned from — the floor refuses UPDATE, so a row committed before signing exists could never be signed
retrospectively — is why 5c.1 shipped empty and why 5c.4 landed ahead of 5c.2's ONC content. **5c.4 also dissolved it**:
signatures are DETACHED ROWS, not a column, so any row can be signed at any later time. Good ordering, not a trap.

**Rule-6 determination, made in the same round: DDInter is CC BY-NC-SA and is OUT of the bundled ladder permanently** —
non-commercial, so not AGPL-3.0-compatible. ROADMAP's old "DDInter *if its licence confirms*" predated the check. It may
attach only as a node-local, separately-licensed plug-in. The surviving ladder is ONC high-priority floor → SPL/DailyMed
(ONSIDES-*method*, **never its data** — § "The 5c.3 source evaluation" measured that its data holds no
interactions at all) → **DrugCentral's `ddi_ref_id = 2` subset, rule 6 ANSWERED** (its other two references are a
copyrighted book and a commercial compendium and are out — same section) → drugref's own curation.
Beside ROADMAP's two orthogonal structures, 5b adds a **third graph**, the MeSH condition DAG — an *object* structure, not a
subject one. **Substrate**: Python 3.12 + `uv`, `psycopg` v3, PostgreSQL ≥ 18. The reviewer client adds Tauri 2, Rust and
plain Svelte/TypeScript; its production trust boundary is an authenticated service, never direct client SQL. Advisory tier,
**integrity in the DB**.

## How to run / test

```bash
uv sync

# INSTALL THE COMMIT-MSG GUARD ONCE PER CLONE (issue 118). core.hooksPath is LOCAL git
# config, not a committed file, so a fresh checkout has no hook until this runs -- and a
# guard nobody installed is the "gate that exists and never fires" of issues 74/66/76.
# It refuses a commit body where a GitHub closing keyword sits next to an issue
# reference; `git commit --no-verify` is the escape for a deliberate close.
git config core.hooksPath .githooks

# 2467 tests (THE ONE HOME FOR THIS NUMBER -- it said 958 while the suite was at 969,
# then 1260 while it was at 1297, then 1395 while it was at 1409, and then 1451 while it
# was at 1564: FOUR occurrences, every one because the round that added the tests updated
# its OWN section and not this line. THE FOURTH RAN FOR FIVE ROUNDS (1465, 1511, 1516,
# 1540, 1564) BEFORE THE GUARD ROUND NOTICED, which is longer than any of the first
# three, so the comment demonstrably is NOT enough on its own: a slice section may record
# a suite delta, but it must ALSO land here -- verified green on 2026-09-02 at 2467
# passed in 102.18 s (db/044 added 16: 1763 → 1779; the live-queue round added no
# Python tests; db/045 and its registry-retention coverage added 8: 1779 → 1787;
# db/046's catalog-comment guard added 3: 1787 → 1790; db/047's key-trust round added
# 2: 1790 → 1792; db/048's GUI finalization added 2: 1792 → 1794; the DrugCentral
# re-measurement added 34 with NO migration: 1794 → 1828; the review-fix
# round on the same branch added 60 more: 1828 → 1888; and db/049, the DrugCentral
# ddi ingest, added 71: 1888 → 1959; and the final whole-branch review's fix round,
# on that same branch, added 3: 1959 → 1962 -- the writer's mixed-row transposition
# test, the orchestrator's stored-count reconciliation, and the blank-endpoint gap
# guard; and the PR-150 review-fix round -- db/050 -- added 43: 1962 -> 2005, which
# is where the floor checks, the Outcome partition, the folded first_wins, the
# autocommit refusal and the eight orchestrator-tail assertions that killed the
# surviving mutants all landed; and the 5c.3 SPL MEASUREMENT round added 59 with
# NO migration and no ingest: 2005 -> 2064, all of them on throwaway probe code
# under tools/, which is deliberate -- the figures that round published are worth
# exactly as much as the parser that produced them, and this project has recorded
# seven wrong figures from partially-working probes; then 3 more when a REVIEW
# CATCH forced that round to re-measure its own false-positive claim: 2064 ->
# 2067, the suppression tests, and see that section's headline -- the round had
# asserted 'lead is a verb' without checking, and its dictionary endpoint was
# deleting lithium; and the 5c.3 SUBJECT-RECOVERY round added 19, again with no
# migration and no ingest: 2067 -> 2086, all on throwaway probe code under
# tools/ -- and one of those 19 exists because the round's OWN tally was wrong
# by 44 labels while its other 18 tests passed, see that section's headline;
# and that round's REVIEW-FIX half added 32 more, 2086 -> 2118, when the delta
# whose two arms used different subject rules was corrected; and slice 5c.3's
# IMPLEMENTATION round -- db/051 and the SPL ingest -- added 178: 2118 -> 2296,
# the first round since db/049 to add a migration, and the first ever to add
# tests for a licensing determination that can be shown REFUSING a write; and
# the PR-161 REVIEW-FIX round -- db/052 -- added 53: 2296 -> 2349, almost all of
# them written to kill a mutant that had SURVIVED, not to cover a new line: the
# quote budget read out of pg_proc.prosrc instead of retyped, reconcile watched
# refusing at all, scan_release and iter_release_labels given their first direct
# tests, every floor including the novel one shown refusing, and cli_spl.py given
# its first test of any kind; and that round's OWN review added 8 more, 2349 ->
# 2357, when it found the new Registry type had broken two committed tools that
# no test exercised -- including the one the matcher's docstring cites as its
# evidence -- and that the brand-new entity guard carried an assertion which
# passed with the guard deleted); and the READER-SKIP CENSUS round -- issue
# #162, no migration -- added 45: 2357 -> 2402, being 18 on the census probe
# itself (which measures the SHIPPED reader, pinned by a test that the probe's
# second parse never disagrees with `extract_subject_uniis`), 24 on the three
# newly-counted reader skips and the line that finally REPORTS the two that
# refuse nothing, and 3 more parametrised cases on the existing guard test whose
# docstring already claimed it covered EVERY counter; and that round's OWN
# REVIEW-FIX half added 41: 2402 -> 2443, nearly all of them written against a
# defect the census could not have found because the 2026-08-21 release does not
# contain it -- the two vocabularies pinned to each other by monkeypatching EACH
# frozenset in turn, `<versionNumber/>` added to the census-versus-reader
# parametrize list (which had compared only `version`, `None` on both sides,
# while the two disagreed on the junk verdict that actually decides), the
# uppercase `M.ZIP` that lost a real label through the one bucket that does not
# refuse, the corrupt member zip and the unknown `encoding=` that each aborted
# the whole scan naming nothing, and the drop/report split made structural.
# ⇒ AND THE EIGHTH OCCURRENCE WAS CAUGHT MID-ROUND, BY THIS ROUND, AGAINST
# ITSELF: 2401 was written here off a `--collect-only` taken BEFORE the round's
# last test existed, and the full run said 2402. Even a number measured in the
# same session goes stale if it is measured before the work stops. Read it off
# the run that VERIFIES green, not off an earlier count -- and issue 146 (a test
# that reads this line and counts the suite) is still the only real fix.
# AND THE COPY-COST ROUND -- issue #160, no migration -- added 3: 2443 -> 2446,
# being the FK-parent-analysed-before-its-child pin, the whole-schema census of
# which foreign keys can be planned onto a loose index, and the ANALYZE
# identifier refusal. ITS OWN REVIEW ROUND then added 1: 2446 -> 2447, the
# empty-table-list refusal -- and rewrote the first of those three, because
# `reltuples >= 0` let two mutants live (it is `> 0` now: 0.0 means analysed
# WHILE EMPTY, which is the bug, not a milder version of it). FOUR mutants are
# now run against it, not two, and all four are killed.
# AND THE INGEST-DURATION ROUND -- issue #159, db/053 -- added 20: 2447 -> 2467,
# being 5 on provenance (the two stamps read off the clock, the RunClock type
# refusing a bare float, the derived eleven-module clock contract, and the one
# test that kills the mutant the derived contract cannot see) and 15 on db/053
# and the operator surface (the default, the CHECK shown refusing AND admitting
# NULL, three catalog comments asserted against the catalog, the pure duration
# formatter's five cases, the ledger watershed reader, and the status line).
# TWO of the 2447 that already existed had to CHANGE: the CHECK caught two test
# helpers stamping finished_at = now() against a clock_timestamp() default, i.e.
# finishing 3.8 ms before they started. Read off the run that
# VERIFIED GREEN AFTER THE LAST EDIT -- 2467 in 102.18 s -- which is the eighth
# occurrence's lesson.
# THE SEVENTH OCCURRENCE HAPPENED, AND IT HAPPENED IN THE ONE PLACE THIS COMMENT
# SAID WAS SAFE. The review-fix commit (26a2a7d) wrote "suite 2118 passed with
# DRUGREF_TEST_DSN set" into its own COMMIT MESSAGE and did not touch this line,
# so the number was measured, published, and still not landed here -- the file
# read 2086 while the suite was at 2118 for the whole of the merged PR #157 and
# was caught by the START-OF-SESSION check the fifth occurrence added. A commit
# message is not a home: it cannot be edited after the fact and nobody greps it.
# That is SEVEN occurrences of one failure mode against a comment rewritten
# three times to prevent it. Issue 146 is still the only real fix and is still
# not written.
# THE SIXTH-AND-A-HALF CASE DID NOT HAPPEN, AND THAT IS WORTH RECORDING TOO: the db/049
# round read the collected count off `pytest --collect-only -q` at the START of its
# documentation task, wrote it HERE, and deliberately did not restate it in HANDOVER,
# ROADMAP or its own section heading -- which is the exact act that created the sixth
# occurrence. Issue 146 (a test that reads this number and counts the suite) is still
# the only thing that would make prose unnecessary, and it is still not written.
# THE SIXTH OCCURRENCE WAS CAUGHT IN REVIEW ON THIS BRANCH, and is issue 146: the
# re-measurement round wrote 1828 into THREE more places (HANDOVER, ROADMAP and its
# own section heading) while filing an issue about this line drifting. All three now
# point here instead of restating it -- a number with four homes has four chances to
# be the one that is stale, and prose alone has now failed six times.
# THE FIFTH OCCURRENCE WAS A NEAR MISS AND IS WHY THE COMMENT NOW NAMES
# A SECOND FAILURE MODE: the pregnancy/lactation spike (PR #127) added 16 tests
# (1644 + 16) and updated NO document at all -- not this line, not ROADMAP, not its own
# section, because it had none. A round that lands via a different agent will not have
# read this comment, so CHECK THE COUNT AT THE START OF A SESSION, not only at its end.
# THE SIXTH OCCURRENCE HAPPENED ANYWAY, AND THE START-OF-SESSION CHECK IS WHAT CAUGHT IT:
# db/047 and db/048 each added 2 tests and each updated only its OWN § "Verified:" line,
# so this line read 1790 for two rounds while the suite was at 1792 and then 1794. That
# is now SIX occurrences of one failure mode against a comment that has been rewritten
# twice to prevent it -- prose is not a gate. A test that reads this number and counts
# the collected suite would be, and it does not exist: filed as issue 146.
# If you add tests, change it HERE.) The DB-gated majority SKIP without this DSN, exercising
# none of the schema, floor, views or orchestrators -- so always run WITH it before
# claiming green, and never with -k or --deselect: a skip is not a pass, and a
# deselected failure is not a pass either.
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest

# `ruff check .` is now the RIGHT command (issue 66). It used to walk downloads/ and
# hang, which is why this line said `ruff check src tests` for six rounds; pyproject's
# extend-exclude drops downloads/ and docs-site/site, so the bare form runs in 0.18 s.
# ruff is pinned in the dev group, so this resolves the lockfile's version rather than
# whatever is on PATH, and CI runs the same command in its own `lint` job.
uv run ruff check .

# Reviewer GUI, including live queue and append-only working records.
cd reviewer-app
npm install
npm run check
npm run build
npm audit
(cd src-tauri && cargo fmt --check && cargo test)
npm run tauri build -- --debug --no-bundle
cd ..

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
  `class_expansion_policy_current` and its four readers re-issued) · **`028` the composition tree** (Slice 3, +
  gap kind 12) · **`029` the curated overlay** (5c.1: `curated_interaction`, `curated_condition`,
  `curated_ddi_pair`, `curated_condition_ruling`, two gap views + `curated_target_unresolved`, gap kinds 13–14) ·
  **`030` signing** (5c.4: six tables, `curated_signature_status`, `signature_backdated`, and
  `signature_status` appended to both 5c.1 read views) · **`031`–`034` the ONC floor** (5c.2: the `ONCHIGH` source
  and `CI_EPC` axis + gap kind 15; the class × class candidate and overlay tables; both grains in one
  `curated_ddi_pair`; then the hot-path recovery that gave the class grain its own `ci_class_pair_subtree`) ·
  **`035` the class grain's detectors** (`severity_kind` + five CHECK→FK conversions, `class_pair_rule_reach`,
  `gap_uncurated_class_interaction_rule` + gap kind 16, `gap_unreviewed_expansion_root` widened to both grains,
  `curated_target_unresolved`'s third arm, `curated_ddi_pair.severity_rank`, `curated_grain_disagreement`,
  `curated_class_interaction` as a `signature_target_kind`). **Read the LATEST file that touches an object for its
  actual shape** — 004's relationship CHECK is replaced by 006's FK, 006's `ddi_candidate_pair` by 010's then 012's then
  027's, 016's `gap_unresolved_ci_object` by 017's, 008's/012's `gap_unpopulated_contraindication` and 008's
  `gap_unmatched_ingredient` by 018's, and 018's by 026's; 029's `curated_ddi_pair` by 030's, 033's, 034's and now
  035's, 029's `curated_target_unresolved` by 035's, and 012's/027's `gap_unreviewed_expansion_root` by 035's; the
  five severity CHECKs in 020/029/032 are 035's foreign keys; and **025's `ingest_run_incomplete` comment is 053's** (issue #159), which also gave `ingest_run.started_at` its `clock_timestamp()` default, both columns a `COMMENT ON COLUMN`, and the table its finishes-after-it-starts CHECK.
- **Migrations are immutable once applied — and immutability starts at MERGE.** `apply_migrations` records each file's
  checksum and raises if an applied file changed, so altering a MERGED migration (*including* re-issuing a `COMMENT ON`) means
  a new `db/NNN_*.sql`. One still on an unmerged branch may be edited — the ledger binds a *database*, not the repo — as
  `db/013`–`db/016` and `db/019` were; verify with a full run after any such edit.
- **Code:** `src/drugref/{ids,claims,classes,conditions,db,interactions,local,questions}.py` +
  `src/drugref/{indications,accumulation,provenance,cli,cli_chain,cli_policy,overlay,curation}.py` +
  the 5c.4 signing stack `src/drugref/{signing,keys,signatures,releases,release_verification,cli_signing,cli_signing_release}.py`
  + the curated READ surface `src/drugref/{curated_read,cli_status,cli_interactions}.py` — `curated_read` owns
  both reads of the graded overlay (`effective_grades_for`, `unrankable_severities`), `cli_status` owns the two
  later `drugref status` blocks, and `cli_interactions` is `drugref interactions` (db/038, issue 114)
  + `src/drugref/ingest/*.py`; seed data under
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
  **`curated_read.py`** (`db/037`, issue 110) is the consumer-facing READ of graded drug pairs —
  `effective_grades_for` over `curated_ddi_pair_effective` — and it exists because a view with no caller is
  half a feature: db/035 shipped `severity_rank` and nothing in `src/` read it, so drugref never applied its
  own two-grain precedence. **NO PRECEDENCE LOGIC LIVES THERE**: the rule that chooses between two grains is
  stated once, in the view, so a consumer querying from any language gets it. Its own `ORDER BY` sorts a LIST
  of different pairs and is presentation, not that rule. It is a seventh module rather than four functions in
  `curation.py` (485 lines) purely to hold rule 4, and it must not migrate into `interactions.py`, whose first
  sentence is "The ONLY module that writes the interaction tables".
- Dev DSN: **stated once, in [`HANDOVER.md`](HANDOVER.md) § Current DSN** — it is a volatile machine detail, and CLAUDE.md
  and the `nextsession` skill both already send readers there. It used to be restated here under "update both", which is the
  same two-homes defect the standing rules above warn about. **THE CURRENT MEASUREMENT DATABASE IS
  `drugref_5c2g`** — `drugref_db038` plus `db/039`–`db/041` and one FDA-CYP ingest, kept as slice 5c.2g's
  measured record. **IT SITS AT `db/041`, NOT AT HEAD**, and that is deliberate: it is a measured record, so
  the review round verified `db/042`+`db/043` against a `TEMPLATE` copy rather than migrating it in place.
  That copy is what proved the cross-source abort was real — 419 rows, every `substance` NULL, 33 withheld
  rows in db/042's migration window, `register_from_gaps` raising `NotNullViolation` before the fix and
  returning 55 questions after it. **Anything re-measured here from now on must state which migration the
  database was on**, because the two answers differ. **`drugref_db038` is retained as its immediate before/after control** and remains the one
  to reproduce a pre-5c.2g claim on. **Two cautions about `drugref_db038` specifically, both measured:** its
  `ddi_candidate_pair` is **21,877**, NOT the 21,664 that several earlier sections quote from
  `drugref_policy`/`drugref_5c4` — a figure carries the database it came from; and it holds **8 stale
  `open_question` rows** for `uncurated_interaction_rule` (601 cached against a live 593), which is
  [#104](https://github.com/cairn-ehr/drugref/issues/104) and which any ingest heals, so a round measuring
  question counts on a database built from it must read them AFTER a first ingest, not before.
  It was `drugref_db037` plus `db/038` (the db/038 round: `effective_rank`, the
  unrankable-severity detector, and issue 117's `COMMENT ON` correction) applied through the documented
  `CREATE DATABASE ... TEMPLATE` + `drugref migrate` path, which is that workflow re-tested rather than assumed
  for the **fifth** round running. `drugref_db037` was `drugref_db036` plus `db/037`, `drugref_db036` was
  `drugref_db035` plus `db/036`, and `drugref_db035` was `drugref_db034` (built 2026-08-13 from the real
  releases against the merged migrations, clean ledger, issue 91's answer) plus `db/035`. **THREE CONTROLS ARE
  KEPT, and they answer different questions**: `drugref_db037` is `db/038`'s immediate before/after (the one to
  reproduce a `db/038` claim on), `drugref_db036` is `db/037`'s and holds the interleaved hot-path measurement,
  and **`drugref_db034` is the pre-`db/035` control** — the database that reproduced the `db/035` `status`
  regression (§ "The PR #107 review round") and the only one that still exercises the class-grain block's
  missing-view guard. Same discipline that keeps `drugref_5c4`. Named for its migration head so the claim is
  checkable: `SELECT max(filename) FROM drugref.schema_migration` → `038`, ledger **38 rows**. **Every count
  below is unchanged from `drugref_db034`** — `db/035` adds detectors and no content, `db/036` adds no SQL
  object at all, `db/037` corrects arithmetic in a grain that holds zero rows, and `db/038` adds one derived
  column that equals its source on every one of the 255 live rows plus a detector that is empty on a healthy
  database (§§ "The class-grain detector round", "The low-hanging-debt round", "The db/038 round").
  It reproduced **every** count and ingest summary in § "Slice 5c.1" **at the end of the chain**, and every
  projection figure in § "Slice 5c.2": chain + `ingest onchigh` + `curate onchigh`, and **nothing else** — no
  keys, no signatures, no published release, no exercise rows, which is also why it re-verifies **nothing from
  the signing layer**. Its state and every figure: § "The reference-database rebuild". The `TEMPLATE` +
  `drugref migrate` workflow was re-tested on it and works.

  **What it STILL HOLDS today is the post-curate state, NOT § "Slice 5c.1"'s chain-end figures — three of that
  section's five must-not-move counts have legitimately moved here, and reading them off this database without
  the qualifier is the mistake this bullet exists to prevent.** `substance_moiety` **19,438** and
  `gap_uncurated_condition_contradiction` **168** hold unqualified. The other three are post-`onchigh`:
  `ddi_candidate_pair` is **21,877** table-wide (5c.1's **21,664** is now the **MED-RT-scoped** count — write the
  scope, as § "Slice 5c.2" already learned to), `open_question` **21,848** (was 21,842), and
  `gap_uncurated_interaction_rule` **593** (was 595). The ladder table in § "The reference-database rebuild"
  gives each stage's value; quote from there, not from § "Slice 5c.1", for anything downstream of the chain.

  **`drugref_5c4` is now a KEPT CONTROL, not the database to read** (built 2026-08-10, migrated through
  `db/030`). It reproduced every count and ingest summary in § "Slice 5c.1" and § "Slice 5c.4" when its chain
  finished, and it is where the only hot-path `EXPLAIN ANALYZE` against a **populated, signed** overlay was
  taken — the one measurement `drugref_db034` cannot supply, which is why it is kept. **It does not still HOLD
  all of them** — two sentences claiming both stood here until the final review — and the distinction decides
  whether a figure may be read off it today: the 5c.4 end-to-end exercise afterwards left **two** curated
  interaction rows, three registered keys (one `compromised`, one `rotated`), two row signatures and one
  published release in it, all deliberately. The five must-not-move counts were taken **before** any of that and
  are unaffected (that slice added no projection); the two curated rows have since moved
  `gap_uncurated_interaction_rule` from **595** to **593** there. It also carries **no** `db/031`–`db/034`
  objects — 5c.2's `psql -f` workaround went into `TEMPLATE` copies that were dropped — so it cannot answer a
  two-grain question at all. Re-measure on a fresh build before quoting a gap count.

  **CORRECTED 2026-08-10 — `drugref_5c1m` IS EMPTY, and this file said otherwise for two rounds.** It used to read
  "`drugref_5c1m` holds the real releases with the MERGED `db/029` … the current measurement database and the one to
  read rather than re-running the chain." **False on this machine, and verified twice** (slice 5c.4 tasks 9 and 11):
  `schema_migration` max is `029_curated_overlay.sql` but `substance_moiety` is **0**. It has the schema and no data.
  The *figures* attributed to it are sound — both task 9 and task 11 independently rebuilt from `downloads/` and
  reproduced them exactly, which is stronger evidence than reading them back would have been — but **the claim about
  where they live was wrong**. The general lesson is in § "Standing rules": a state file naming a database as
  authoritative is asserting something checkable, and nothing checked it. **`drugref_5c1m` is the ONLY empty one**
  — task 11 probed all five and the other four carry `substance_moiety` **19,438** as claimed (`drugref_5c1` 029,
  `drugref_policy` 027, `drugref_ops` 026, `drugref_planc` 022), so this is one database's history, not a general
  rot. Either drop `drugref_5c1m` or rebuild it under a new name; do not quote it.

  **`drugref_5c1` is still the control and is still kept:** it was migrated
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
- **`drugref_dur159` is the ingest-duration round's verification database** (2026-09-02, issue #159): built
  from **nothing** rather than from a template — `createdb` → `migrate` → `ingest chain` + `onchigh` +
  `fda-cyp` + `drugcentral` + `spl`, all nine feeds, because a template carries nine `ingest_run` rows written
  under the OLD meaning and the whole point was to measure new ones. **`drugref_dur159mixed` is its
  counterpart**: a fresh clone of `drugref_spl160fix` then `migrate`, i.e. the production upgrade path, and the
  only artefact showing `db/053` applying over rows that predate it (the CHECK validated all nine) and
  `drugref status` refusing to call their stamps a duration. Keep both.
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
  migration's prose — goes**. **Thirteen** live there (re-count with `ls docs-site/docs/decisions/*.md` and
  SUBTRACT `index.md`, which is the section's landing page and not a record — 14 files, 13 records; the count
  read "eleven" for two rounds because that subtraction was never written down here); a reversed decision is
  removed, not tombstoned.

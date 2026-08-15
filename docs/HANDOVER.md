# HANDOVER — drugref

> **The volatile half: where we are right now.** Regenerated at the end of every working session
> (nextsession rule 9) and kept **under 130 lines**, so a rewrite costs nothing.
>
> **THIS LINE IS THE ONLY HOME FOR THAT NUMBER.** It was also in `CLAUDE.md` (twice) and the `nextsession`
> skill; all three said `~120` while this header said `~130` and the file was 136. A bound is a vocabulary
> like any other, and this repo has lost four rounds to one rule kept in two places. **Change it here, alone.**
>
> **The stable half is [`PROJECT-NOTES.md`](PROJECT-NOTES.md)** — traps, state by layer, how to run and test,
> the schema and code map, upstream errata, repo facts. Edited in place, under no bound. **Put anything whose
> history is worth reading there, not here** (#63): this file's history is deliberately disposable.
>
> Slice sequencing is [`ROADMAP.md`](ROADMAP.md); the canonical what/why is the specs under
> [`superpowers/specs/`](superpowers/specs/).

## ⇒ NEXT

**Merged to `main`**: through **5c.4 — signing**, PLUS **5c.2 — the ONC floor**, merged LAST despite its lower number — **ROADMAP's order is NOT the merge order.** **`db/029`–`db/036` FROZEN.**

**⇒ JUST FINISHED — the LOW-HANGING-DEBT ROUND: `db/037` + `curated_read.py` + `tests/ruff.toml`**, a sweep of
all **51** open issues for work that is small, self-contained and needs no design decision. **SEVEN FIXED — 79,
87, 100, 108, 109, 110, 111 — plus 19 and 106 answered by measurement**, so nine touched. (This line said
"Eight cleared" over a list of nine numbers for a round; the count and the list disagreed, in three files at
once. Fixed the way this repo fixes that: state the seven, then the two, and never a total.) Suite **1465 →
1516**, `ruff` clean. Full account, every measurement, and **the list of what was deliberately NOT taken and
why**: PROJECT-NOTES § "The low-hanging-debt round"; ROADMAP § 5c.2c.

**⇒ THEN THE PR #113 REVIEW ROUND, APPLIED — THREE silent defects, all three mutation-verified, full account in
PROJECT-NOTES § "The PR #113 review round".** (1) **`GradedPair` was built by positional splat**, five of nine
fields asserted nowhere — swapping `mechanism`/`management` in the SELECT left the WHOLE SUITE GREEN while
drugref labelled management advice as mechanism. One `_COLUMNS` list now generates the SELECT *and* binds by
keyword (`keys._COLUMNS`' shape, third module to need it), making the transposition **unrepresentable**.
(2) **`curated_ddi_pair_effective`'s tie-break was not total** — `via_subject_class` missing, so two class rules
over one pair (one drug under two subject classes, MED-RT's ordinary shape) tied on every key and `DISTINCT ON`
followed **heap order**: which mechanism/management a prescribing client read was decided by physical row
position, flippable by a rebuild or dump/restore, silent because severity is equal. (3) **The class-grain guard
did not cover db/037** — it corrects `class_pair_rule_reach`'s ARITHMETIC while every name read still resolved
under db/035, so on db/035-or-036 the block printed the OLD, OVERSTATED numbers. `_RULE_COUNT` now names a
db/037 column; **`drugref_db036` raises, `drugref_db037` prints**. **This REVERSES last round's line that
"status on db036 exits 0" — that was the defect recorded as a feature.**

**⇒ FOUR ISSUES FILED, NOT FIXED** — **[#114](https://github.com/cairn-ehr/drugref/issues/114)**
`effective_grades_for` has no consumer in `src/` ("half a feature", one layer up) ·
**[#115](https://github.com/cairn-ehr/drugref/issues/115)** `ClassGrainCounts.total` reads as a denominator for
`disagreements`, which counts PAIRS (~2,263 against a `total` of 9) ·
**[#116](https://github.com/cairn-ehr/drugref/issues/116) `NULLS FIRST` inside `DISTINCT ON` makes an unrankable
severity WIN**, so the client gets `severity_rank = NULL` and every threshold form drops it — db/037 fixed the
sort, not the payload · **[#117](https://github.com/cairn-ehr/drugref/issues/117)** db/035 says NINE class rules
where #94 and the data say SEVEN.

**⇒ EVERY PUBLISHED COUNT IS BYTE-IDENTICAL `db036` → `db037`, and again across the review round's rebuild** —
`ddi_candidate_pair` 21,877 · `curated_ddi_pair` 255 · `open_question` 21,848 ·
`gap_unpopulated_contraindication` 13 · `condition_contraindication_expanded` 192,161 · `class_expansion_policy`
14 · `loaded_release` 6. `class_pair_contraindication` is **EMPTY on every database in existence** (#94 withheld
its **seven** entries — db/035 and #96 say nine, which is #117), and that is what made this the cheap moment to
correct the class grain's arithmetic.

**⇒ THE ONE SURPRISE, and it changes what a consumer sees today: `curated_ddi_pair_effective` is NOT a no-op.**
With zero class-grain content it still collapses **255 rows to 213** — 42 doubled pairs, **all 42 explained by
`candidate_source` alone** (a rule both MED-RT and ONCHIGH assert is one grade and two rows), zero differing in
severity, `via_class` or `rule_grain`. Every client reading `curated_ddi_pair` was seeing that duplication.

**⇒ WHAT `db/037` CHANGES, AND WHAT IT IS STILL NOT MEASURED ON.** #108 exact reach `|S|·|O| − |S ∩ O|` (the
self-pair rule is the special case, not the fix) · #109 orientation-blind disagreement join via `LEAST`/
`GREATEST`, **an equi-join where the obvious `OR` of two arm pairs is not** · #110 the precedence becomes a view,
with `NULLS FIRST`. Full account and the arithmetic: PROJECT-NOTES § "The low-hanging-debt round".
**The timings there are honestly NOT evidence** — interleaved against `drugref_db036` both probes moved −1%
(noise), but **the class-grain half is EMPTY so the new join had nothing to join**, and both are dominated by
`ddi_candidate_pair`'s ~2.7 s scan (#75). **[#112](https://github.com/cairn-ehr/drugref/issues/112) still owns
that measurement** — db/024's 59 s → 465 ms precedent is "a synthetic probe looked fine because its fixture had
no edges".

**⇒ THE REFERENCE DATABASE IS NOW `drugref_db037`** (ledger 37), from `TEMPLATE drugref_db036` + `drugref
migrate` — that workflow re-tested for the FIFTH round running, and **rebuilt again after the review round
edited `db/037`**: an unmerged migration may be edited (PROJECT-NOTES § repo facts — the ledger binds a
*database*, not the repo), but every database that already applied it holds the old checksum, so it must be
rebuilt or it will refuse to migrate. The pre-review copy is kept as **`drugref_db037_pre_review`** rather than
dropped. **Every published count is byte-identical across the rebuild** — verified after the edit, same eight
figures as below, plus `curated_ddi_pair_effective` **213**.
**TWO CONTROLS ARE KEPT and they answer different questions**: `drugref_db036` is `db/037`'s before/after (what
the timings above were taken against), and **`drugref_db034` is the pre-`db/035` control** — the only one that
still exercises the class-grain block's missing-view guard, verified again this round (exit **2**, one sentence,
no traceback). **`drugref status` on `drugref_db036` with the new code now RAISES the operator sentence, and
that is the fix, not a regression** — see the guard paragraph above. The previous round recorded its exiting 0
as evidence the denominator was safe; it was evidence the guard was blind.

**⇒ DO THIS NEXT — the next slice, and the evaluation says the cheap one is DrugCentral, not SPL**: 6,337 new
public-domain moiety-grained pairs, hard part name resolution. Either way it opens with its own design round.
**Both slices' shapes, rules and open questions are in ROADMAP § 5c.3 and PROJECT-NOTES — read them there, not
here** ([#101](https://github.com/cairn-ehr/drugref/issues/101) DrugCentral,
[#102](https://github.com/cairn-ehr/drugref/issues/102) SPL potency bands and the document-type filter).
**Whichever lands is the first slice that can POPULATE the class grain**, so `db/035`'s detectors and `db/037`'s
arithmetic get their first real exercise then — and #105, #106, #112 and now **#116** all become answerable
against content rather than against nothing.

**⇒ ONE DECISION IS TAKEN AND NOT BUILT — do not re-litigate it.**
[#86](https://github.com/cairn-ehr/drugref/issues/86): **add `signed_by_unknown_key` as a fourth
`signature_status` value** — a published-vocabulary widening, so a round of its own; decision on the issue.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61). **Standing rule:** near `close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#` ("issue 65"). Full account: PROJECT-NOTES.

## Open follow-ups (all filed as GitHub issues)

**THE FULL LEDGER MOVED TO [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md)** — every
category, every figure, verbatim. It was duplicated here for four rounds against this file's own header rule,
and the duplication had already cost: **#52's "422 broadened assertions" existed ONLY in the HANDOVER copy**, so
the bounded, deliberately-disposable file was the sole record of a figure a future slice needs. Read it there.

**What gates the NEXT session, and only that** — **#112/#105/#116** all wait on class-grain CONTENT, so the next
slice unblocks four issues at once · **#89** `signing.py` **605** lines, `release_verification.py` **540**, and
`curation.py` is now **500** with no headroom — **re-read that issue's figures, do not re-derive them** ·
**#100 is CLOSED** (`ci_class_subtree` pinned from `pg_depend`, mutation-verified against db/033's wide seed) ·
**#94's seven withheld entries** still need research, and **#117** says db/035 miscounts them as nine.
**Before the first production load**: every parser re-run against a current release, #17's `add_claim`
canonicalisation check, **three** rule-6 deeds (#6, #25, GSRS) — PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`. Set it as `DRUGREF_TEST_DSN` for the DB-gated tests.
- **WHICH VERIFICATION DATABASE TO READ IS NOT NAMED HERE** — it is stated once, in `PROJECT-NOTES.md`'s "Dev DSN" bullet of § How to run / test. An earlier version of this bullet said exactly that and then named it anyway; the name moved this round, and that copy would have outlived it.

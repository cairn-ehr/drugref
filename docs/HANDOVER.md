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

**Merged to `main`**: through **5c.4 — signing**, PLUS **5c.2 — the ONC floor**, merged LAST despite its lower
number — **ROADMAP's order is NOT the merge order.** **`db/029`–`db/037` FROZEN** (`db/037` joined them when PR
[#113](https://github.com/cairn-ehr/drugref/pull/113) merged, `b449e7f`). **`db/038` is UNMERGED on
`fix/db038-review-113-followups`** and therefore still editable — the ledger binds a *database*, not the repo —
but every database that applied it holds the old checksum, so an edit means rebuilding `drugref_db038`.

**⇒ JUST FINISHED — the db/038 ROUND: PR #113's FOUR FILED ISSUES, ALL FOUR CLOSED** (114, 115, 116, 117).
One migration, one new command, one rename. Suite **1516 → 1540**, `ruff` clean, docs build clean.
Full account and every measurement: PROJECT-NOTES § "The db/038 round"; ROADMAP § 5c.2d.

**⇒ #116 WAS THE REAL DEFECT, and it is worth knowing what db/037 actually did.** `NULLS FIRST` was RIGHT for
the sort — under-warning is the harm direction — but inside a `DISTINCT ON` it makes an unrankable severity
**WIN** and **DISCARDS the rankable competitor**, so the client received `severity_rank = NULL` with **no
second row behind it**, and every threshold form drops a NULL (SQL `<= 2` is UNKNOWN, Python raises, `x and x
<= 2` is silently False). Against a `minor` competitor that was an improvement; against `contraindicated` a
numeric client sees **NOTHING** — and the existing test graded the competitor `minor`, which is exactly why the
suite could not see it. `db/038` § 1 publishes **`effective_rank = COALESCE(severity_rank, 0)`** and orders on
it; **`severity_rank` stays NULLABLE on purpose**, because COALESCEing it would destroy the only evidence the
schema is broken. § 2 gives that fault a detector and `drugref status`'s **sixth** block.

**⇒ #114 WAS ALREADY CLOSED WHEN THIS ROUND STARTED, AND NOTHING HAD BEEN DONE.** `ed1ab5e`'s body reads
*"Filed rather than fixed: #114 …"* and GitHub matched `fixed: #114` as a closing keyword — **the sentence
declaring it unfixed is what closed it.** #115–#117 sit in the same sentence and survived, which pins the
mechanism rather than inferring it. **FIFTH occurrence** (#31, #35, #40, #61, #114) and the **SECOND with the
identical sentence template**, after `92baaea` did it to #61 and PROJECT-NOTES documented the trap in full,
mechanism included. **A prose rule that has failed five times is not a rule** —
**[#118](https://github.com/cairn-ehr/drugref/issues/118)** proposes the `commit-msg` hook, the one intervention
not yet tried. Until it lands: write *"issue 114"*, no `#`, nothing for the linker to match.

**⇒ EVERY PUBLISHED COUNT IS BYTE-IDENTICAL `db037` → `db038`** — `ddi_candidate_pair` 21,877 ·
`curated_ddi_pair` 255 · `curated_ddi_pair_effective` 213 · `open_question` 21,848 ·
`gap_unpopulated_contraindication` 13 · `condition_contraindication_expanded` 192,161 · `class_expansion_policy`
14 · `loaded_release` 6. **And the new surfaces were read on real data rather than assumed neutral:**
`effective_rank` differs from `severity_rank` in **0** of 255 rows and is NULL in **0** (the COALESCE never
fires on a healthy database), and `curated_unrankable_severity` is **empty**.

**⇒ NO TIMING WAS TAKEN, deliberately.** § 1 adds one `COALESCE` over an already-selected column and swaps one
ORDER BY key for an equivalent one. **[#112](https://github.com/cairn-ehr/drugref/issues/112) still owns the
class-grain measurement** and its precondition is unchanged: `class_pair_contraindication` is EMPTY on every
database in existence, so a probe here would measure a join with nothing to join — db/024's 59 s → 465 ms
precedent is exactly that mistake.

**⇒ ONE EXISTING TEST WAS PASSING FOR THE WRONG REASON, found by mutating a pin this round was editing.**
`test_the_callers_own_order_by_puts_an_unrankable_severity_first` — added by the PR #113 review — put the
unrankable partner on the **smaller** uuid, so `partner_moiety` alone produced the expected order and deleting
the rank key from the caller's `ORDER BY` left it GREEN. Severities swapped; the same mutation now makes it red.
**Third time this repo's own "an over-determined test cannot fail" lesson has applied.**

**⇒ ONE RULE-4 BREACH WAS MADE AND IS RECORDED, NOT HIDDEN.** `curation.py` went **500 → 520** lines — issue
115 required the population boundary to live on the TYPE rather than in a comment in another module. That is
rule 3 against rule 4, and this repo has twice ruled the answer is to **move code, never shave comments**, so
it is measured onto **[#89](https://github.com/cairn-ehr/drugref/issues/89)** with the seam named
(`ClassGrainCounts` + `class_grain_counts` + `_RULE_COUNT`, ~90 lines, one consumer → `curation.py` back to
~430) rather than split inside a correctness diff. db/030's own precedent. **#89's figures: `signing.py` 605,
`release_verification.py` 540, `curation.py` 520 — re-read them off the issue, do not re-derive.**

**⇒ THE REFERENCE DATABASE IS NOW `drugref_db038`** (ledger 38), from `TEMPLATE drugref_db037` + `drugref
migrate` — that workflow re-tested for the FIFTH round running. **THREE CONTROLS ARE KEPT and they answer
different questions**: `drugref_db037` is `db/038`'s before/after, `drugref_db036` holds `db/037`'s interleaved
hot-path measurement, and **`drugref_db034` is the pre-`db/035` control** — the only one still exercising the
class-grain block's missing-view guard. `drugref_db037_pre_review` also survives from last round.

**⇒ DO THIS NEXT — the next content slice, and the evaluation says the cheap one is DrugCentral, not SPL**:
6,337 new public-domain moiety-grained pairs, rule 6 clear for `ddi_ref_id = 2` ONLY. Hard part is name
resolution. **Either way it opens with its own design round.** **Both slices' shapes, rules and open questions
are in ROADMAP § 5c.3 and PROJECT-NOTES § "The 5c.3 source evaluation" — read them there, not here**
([#101](https://github.com/cairn-ehr/drugref/issues/101) DrugCentral,
[#102](https://github.com/cairn-ehr/drugref/issues/102) SPL potency bands and the document-type filter).
**EVERY DrugCentral FIGURE RESTS ON ONE UNREPEATED RUN and the 1.4 GB dump is not retained — re-measure before
acting, and re-read the `reference` table before bundling anything.** **Whichever lands is the first slice that
can POPULATE the class grain**, so db/035's detectors and db/037's arithmetic get their first real exercise
then — and **#105, #106 and #112 all become answerable against content rather than against nothing**.

**⇒ ONE DECISION IS TAKEN AND NOT BUILT — do not re-litigate it.**
[#86](https://github.com/cairn-ehr/drugref/issues/86): **add `signed_by_unknown_key` as a fourth
`signature_status` value** — a published-vocabulary widening, so a round of its own; decision on the issue.

## Open follow-ups (all filed as GitHub issues)

**THE FULL LEDGER LIVES IN [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md)** — every
category, every figure, verbatim. It was duplicated here for four rounds against this file's own header rule,
and the duplication had already cost: **#52's "422 broadened assertions" existed ONLY in the HANDOVER copy**, so
the bounded, deliberately-disposable file was the sole record of a figure a future slice needs. Read it there.

**What gates the NEXT session, and only that** — **#112/#105** wait on class-grain CONTENT, so the next slice
unblocks them · **#118** is new and cheap, and every round that writes "filed rather than fixed" pays for its
absence · **#89** now has THREE files over the cap (figures above) · **#94's seven withheld entries** still need
research, and db/035's catalog comment now says seven (`db/038` § 3) while its stripped `--` prose still says
nine and cannot be corrected. **Before the first production load**: every parser re-run against a current
release, #17's `add_claim` canonicalisation check, **three** rule-6 deeds (#6, #25, GSRS) — PROJECT-NOTES
§ "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`. Set it as `DRUGREF_TEST_DSN` for the DB-gated tests.
- **WHICH VERIFICATION DATABASE TO READ IS NOT NAMED HERE** — it is stated once, in `PROJECT-NOTES.md`'s "Dev DSN" bullet of § How to run / test. An earlier version of this bullet said exactly that and then named it anyway; the name moved this round, and that copy would have outlived it.

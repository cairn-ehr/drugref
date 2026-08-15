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
`fix/db038-review-113-followups`** and therefore still editable — the ledger binds a *database*, not the repo.
**This round exercised that licence twice**, so any further edit means rebuilding `drugref_db038` again.

**⇒ JUST FINISHED — the db/038 ROUND: PR #113's FOUR FILED ISSUES, ALL FOUR CLOSED** (114, 115, 116, 117),
**THEN ITS OWN REVIEW ROUND ON TOP** (five agents + hand verification against the live catalog). One migration,
one new command, one rename. Suite **1516 → 1540 → 1564**, `ruff` clean. Full account and every measurement:
PROJECT-NOTES § "The db/038 round"; ROADMAP § 5c.2d.

**⇒ THE REVIEW'S HEADLINE FINDING — `db/038` § 3 SILENTLY REVERTED `db/036`.** `COMMENT ON` **overwrites, it
does not merge**, and THREE migrations state a comment over `gap_uncurated_class_interaction_rule` (035 § 6,
036 § 1, 038 § 3). § 3 rebuilt from **db/035's** text — but the text it replaced was **db/036's**, so it
restored the wrong `AXIS:` gap_key spelling db/036 existed to fix and deleted the parenthetical recording it.
`question_uuid = uuid5(gap_kind, gap_key)` and the key is **frozen and externally citable**, so a reader
reconstructing it from `\d+` computes a uuid matching nothing **with no error**. **THE ROUND'S OWN VERIFICATION
COULD NOT SEE IT**: it grepped `%nine ingested%` / `%seven ingested%`, scoped to the word being changed and so
blind to what else moved in the same overwrite. **A re-issued `COMMENT ON` must be diffed WHOLE against the LIVE
text, never against the file you happen to be reading.** Pinned by `tests/test_class_grain_comment.py`.

**⇒ SIX MUTATIONS THAT PREVIOUSLY SURVIVED THE WHOLE SUITE NOW FAIL.** The moiety arm of
`curated_unrankable_severity` was **entirely unexercised** (whole arm, `AND c.applies`, `superseded_by` — the
grain carrying all 255 curated pairs) and the **moiety half's `COALESCE`** was unpinned. The detector also keyed
on `sk.severity IS NULL` (the *cause*) not `sk.severity_rank IS NULL` (the *condition that harms*), so dropping
`severity_kind`'s `NOT NULL` gave **full harm, zero detection, an affirmative `none`**. And
`severity_rank` had **no Python reader at all**, so the CLI printed a schema fault as a bland `rank 0`.
**TWO EXISTING TESTS WERE ALSO PASSING FOR THE WRONG REASON** — the `ORDER BY` pin sat on the smaller uuid, and
the **disjointness** assertion reduced to `1 + 0 <= 1` for want of a dead rule in its fixture. **Fourth and
fifth times this repo's "an over-determined test cannot fail" lesson has applied.**

**⇒ FOUR FINDINGS NEEDING A DESIGN CALL ARE FILED, NOT BODGED** —
**[#120](https://github.com/cairn-ehr/drugref/issues/120)** an unknown moiety_uuid renders identically to an
ungraded drug (needs a registry-existence reader; **the one with a harm direction**) ·
**[#121](https://github.com/cairn-ehr/drugref/issues/121)** an orphaned curated grade reads as "no curated
grade" on the clinician path · **[#122](https://github.com/cairn-ehr/drugref/issues/122)** all four
`UndefinedTable` guards assert one cause as fact and `cli.main` drops `__cause__` ·
**[#123](https://github.com/cairn-ehr/drugref/issues/123)** the detector sweeps 2 of 5 tables with a
`severity_kind` FK (line now labelled `(DDI grain)`).

**⇒ #116 WAS THE REAL DEFECT.** `NULLS FIRST` was RIGHT for the sort, but inside a `DISTINCT ON` it makes an
unrankable severity **WIN** and **DISCARDS the rankable competitor** — the client got `severity_rank = NULL`
with no second row behind it, and every threshold form drops a NULL. § 1 publishes **`effective_rank =
COALESCE(severity_rank, 0)`**; **`severity_rank` stays NULLABLE on purpose**, as the only evidence the schema is
broken. § 2 gives the fault a detector and `status`'s **sixth** block. Full account: PROJECT-NOTES.

**⇒ #114 WAS ALREADY CLOSED WHEN THIS ROUND STARTED, AND NOTHING HAD BEEN DONE.** `ed1ab5e`'s body reads
*"Filed rather than fixed: #114 …"*; GitHub matched `fixed: #114` — **the sentence declaring it unfixed closed
it**, while #115–#117 in the same sentence survived, which pins the mechanism. **FIFTH occurrence** (#31, #35,
#40, #61, #114). **[#118](https://github.com/cairn-ehr/drugref/issues/118)** proposes the `commit-msg` hook;
until it lands, write *"issue 114"*, no `#`.

**⇒ EVERY PUBLISHED COUNT IS BYTE-IDENTICAL `db037` → `db038`**, re-verified after the review's edits —
`ddi_candidate_pair` 21,877 · `curated_ddi_pair` 255 · `curated_ddi_pair_effective` 213 · `open_question`
21,848 · `gap_unpopulated_contraindication` 13 · `condition_contraindication_expanded` 192,161 ·
`class_expansion_policy` 14 · `loaded_release` 6. **New surfaces read on real data:** `effective_rank` differs
from `severity_rank` in **0** of 255 rows and is NULL in **0**, `curated_unrankable_severity` is **empty**, the
gap comment carries `CI_AXIS:` and `seven ingested`, and `severity_kind` now has `CHECK (severity_rank >= 1)` —
**rank 0 is the sentinel, and until this review that was a comment, not a rule.**

**⇒ NO TIMING WAS TAKEN, deliberately.** § 1 adds one `COALESCE` over an already-selected column and swaps one
ORDER BY key for an equivalent one. **[#112](https://github.com/cairn-ehr/drugref/issues/112) still owns the
class-grain measurement**: `class_pair_contraindication` is EMPTY everywhere, so a probe here would measure a
join with nothing to join — db/024's 59 s → 465 ms precedent is that mistake.

**⇒ ONE RULE-4 BREACH WAS MADE AND IS RECORDED, NOT HIDDEN.** `curation.py` went **500 → 523** lines — issue
115 required the population boundary to live on the TYPE, not in a comment in another module. Rule 3 against
rule 4, and this repo has twice ruled the answer is **move code, never shave comments**, so it is measured onto
**[#89](https://github.com/cairn-ehr/drugref/issues/89)** with the seam named (`ClassGrainCounts` +
`class_grain_counts` + `_RULE_COUNT`, ~90 lines, one consumer → ~430) rather than split inside a correctness
diff. **#89's figures: `signing.py` 605, `release_verification.py` 540, `curation.py` 523 — read them off the
issue, do not re-derive.**

**⇒ THE REFERENCE DATABASE IS NOW `drugref_db038`** (ledger 38), **rebuilt after the review edited `db/038`** —
`TEMPLATE drugref_db037` + `drugref migrate`, that workflow re-tested for the FIFTH round. **THREE CONTROLS ARE
KEPT**: `drugref_db037` is `db/038`'s before/after, `drugref_db036` holds `db/037`'s interleaved hot-path
measurement, `drugref_db034` is the pre-`db/035` control — the only one still exercising the class-grain
block's missing-view guard.

**⇒ DO THIS NEXT — the next content slice; the evaluation says the cheap one is DrugCentral, not SPL**: 6,337
new public-domain moiety-grained pairs, rule 6 clear for `ddi_ref_id = 2` ONLY, hard part is name resolution.
**It opens with its own design round.** **Both slices' shapes, rules and open questions are in ROADMAP § 5c.3
and PROJECT-NOTES § "The 5c.3 source evaluation" — read them there**
([#101](https://github.com/cairn-ehr/drugref/issues/101) DrugCentral,
[#102](https://github.com/cairn-ehr/drugref/issues/102) SPL). **EVERY DrugCentral FIGURE RESTS ON ONE UNREPEATED
RUN and the 1.4 GB dump is not retained — re-measure before acting.** **Whichever lands is the first slice that
can POPULATE the class grain**, so db/035's detectors and db/037's arithmetic get their first exercise, and
**#105, #106 and #112 become answerable against content**.

**⇒ ONE DECISION IS TAKEN AND NOT BUILT — do not re-litigate it.** [#86](https://github.com/cairn-ehr/drugref/issues/86):
**add `signed_by_unknown_key` as a fourth `signature_status`** — a vocabulary widening, so a round of its own.

## Open follow-ups (all filed as GitHub issues)

**THE FULL LEDGER LIVES IN [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md)** — every
category, every figure, verbatim. It was duplicated here for four rounds against this file's own header rule,
and that cost: **#52's "422 broadened assertions" existed ONLY in the HANDOVER copy**, so the deliberately
disposable file was the sole record of a figure a future slice needs. Read it there.

**What gates the NEXT session, and only that** — **#112/#105** wait on class-grain CONTENT · **#118** is cheap,
and every round that writes "filed rather than fixed" pays for its absence · **#89** has THREE files over the
cap · **#120–#123** are this review's, and **#120 is the one with a harm direction** · **#94's seven withheld
entries** still need research, and db/035's catalog comment now says seven (`db/038` § 3) while its stripped
`--` prose still says nine and cannot be corrected. **Before the first production load**: every parser re-run
against a current release, #17's `add_claim` check, **three** rule-6 deeds (#6, #25, GSRS) — PROJECT-NOTES
§ "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`. Set it as `DRUGREF_TEST_DSN` for the DB-gated tests.
- **WHICH VERIFICATION DATABASE TO READ IS NOT NAMED HERE** — it is stated once, in `PROJECT-NOTES.md`'s "Dev DSN" bullet of § How to run / test. An earlier version of this bullet said exactly that and then named it anyway; the name moved this round, and that copy would have outlived it.

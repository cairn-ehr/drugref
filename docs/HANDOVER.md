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

**Merged to `main`**: through **5c.2f — the guard round**, plus two source spikes
([#126](https://github.com/cairn-ehr/drugref/pull/126) FDA, [#127](https://github.com/cairn-ehr/drugref/pull/127)
pregnancy/lactation). **`db/029`–`db/038` ARE ALL FROZEN, and so are `db/039`–`db/042`** — 5c.2g's four, applied to `drugref_test`
and to the measurement database, so a correction to any of them needs `db/043` **even though the PR is still
open.** `db/042` exists precisely because that rule was followed rather than argued with.

**⇒ JUST FINISHED — 5c.2g, `FDA-CYP` potency classes: 65 PK classes, 348 memberships, 55 curator questions,
four migrations, suite 1660 → 1739.** The potency vocabulary 5c.3 needs. **It creates NO DDI pair, and that
is a refusal rather than a deferral** — joining FDA's inhibitor and substrate columns would manufacture ~800
pairs no source asserts. Full account: PROJECT-NOTES § "Slice 5c.2g"; shape: ROADMAP § 5c.2g.

**⇒ READ THIS BEFORE TRUSTING ANY FIGURE THIS PROJECT WROTE DOWN. SEVEN OF 5c.2g's OWN SPEC FIGURES WERE
WRONG, AND IMPLEMENTATION FOUND EVERY ONE** — the last two found by the FINAL review, **after the count had
been written down as five, making the count of wrong figures itself a wrong figure** — not review, not re-reading: a task ran the real bytes and
reported a number that disagreed. They share one shape, and it is the shape the slice exists to prevent:
**something asserted a property it had not confirmed.** The design round's probe was a partially-working
parser, and **a partially-working parser does not announce itself — it hands you a plausible value, and a
plausible value gets written down as a measurement.** All seven, and the two that changed a RULE rather than a
number, are tabulated in PROJECT-NOTES § "Slice 5c.2g". The two rules:

1. **The closed vocabulary rejects ZERO tokens on a correct parse, and that is the passing state.** It is a
   tripwire for a broken *grammar*, not a filter on data. **A round that sees it reject something should
   suspect its own parser first and the data second.**
2. **An invariance claim must be checked as an invariance** — same query, same database, either side of the
   change — **never against a constant transcribed from somewhere else.** The spec had said
   `ddi_candidate_pair` **21,664**, measured on two *earlier* databases; `drugref_db038` holds **21,877**.

**⇒ DO NOT PUT `open_question` ON A MUST-NOT-MOVE LIST.** 5c.2g minted 55 questions and the total moved by
**47**, because the same run closed **8 stale rows of an unrelated gap kind**: `register_from_gaps` re-derives
**every** kind on **every** orchestrator, so ingesting FDA-CYP healed
[#104](https://github.com/cairn-ehr/drugref/issues/104). **That issue is still open two migrations later and
its title understates it — "the next ingest" means ANY source's ingest**, so register accuracy depends on
which unrelated feed ran last. New datum recorded as a comment on #104.

**⇒ THREE ISSUES FILED THIS ROUND, ALL DELIBERATELY NOT BUILT.**
[#128](https://github.com/cairn-ehr/drugref/issues/128) stereoisomer assertions against a held racemate
(`S-mephenytoin` is the reference CYP2C19 probe substrate; carrying it on the racemate is pharmacology with a
literature behind it, **scoped to every source — DrugCentral will meet it too**) ·
[#129](https://github.com/cairn-ehr/drugref/issues/129) `registry_near_name` ships NULL, because a near-name
heuristic with no measured output is the exact pattern this slice spent seven corrections catching ·
[#130](https://github.com/cairn-ehr/drugref/issues/130) **`cli.py` sits at exactly 500/500 against a HARD cap
test — the next line added to it breaks CI**, and the cap has already begun dictating where functions live.

**⇒ #89's FILE-SIZE FIGURES LIVE ON THE ISSUE AND NOWHERE ELSE.** PROJECT-NOTES used to restate them and they
had drifted — `questions.py` recorded as 568 while the file was **664**. Re-measured and posted to #89; the
paragraph now points there. **Do not re-derive them and do not copy them back.**

**⇒ DO THIS NEXT — DrugCentral, the next CONTENT slice**
([#101](https://github.com/cairn-ehr/drugref/issues/101)): 6,337 new public-domain moiety-grained pairs, rule 6
clear for `ddi_ref_id = 2` **ONLY**. **EVERY DrugCentral FIGURE RESTS ON ONE UNREPEATED RUN and the 1.4 GB dump
is not retained — re-measure before acting**, and after this round that warning should read as a promise
rather than boilerplate. It opens with its own design round; shape and open questions in ROADMAP § 5c.3 and
PROJECT-NOTES § "The 5c.3 source evaluation". **It is the first slice that can POPULATE the class grain**, so
db/035's detectors and db/037's arithmetic get their first exercise and **#105, #106, #112 become answerable**.
Its name-resolution residue will meet **#128** directly.

**⇒ TWO OTHER ROUNDS ARE READY AND SMALLER.** **5c.5 pregnancy/lactation** — PR #127's spike measured LactMed
(1,679 moieties outside the MED-RT lactation floor), AEMPS CIMA and ANSM BDPM, all **non-firing**, and **a
clinician review is a GATE that has not happened**; ROADMAP § 5c.5. And the **FDA toxicity projection**
(DIRIL first, then DICTrank and DILIrank) — spike §4, and its DIRIL parser trap is written down there:
the workbook declares `A1:Y1048381` while data ends at row 318.

**⇒ ONE DECISION IS TAKEN AND NOT BUILT — do not re-litigate it.**
[#86](https://github.com/cairn-ehr/drugref/issues/86): add `signed_by_unknown_key` as a fourth
`signature_status` — a vocabulary widening, so a round of its own.

## Open follow-ups (all filed as GitHub issues)

**THE FULL LEDGER LIVES IN [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md)** — every
category, every figure, verbatim. It was duplicated here for four rounds against this file's own header rule,
and that cost: **#52's "422 broadened assertions" existed ONLY in the HANDOVER copy**, so the deliberately
disposable file was the sole record of a figure a future slice needs. Read it there.

**What gates the NEXT session, and only that** — **#128/#129/#130** are this round's own tail · **#112/#105**
wait on class-grain CONTENT · **#124** is the guard round's tail, and the surface it names is unmeasured ·
**#121 and #123** are the two review findings the guard round did not take · **#104** is confirmed still open
and now better understood · **#94's seven withheld entries** still need research, and db/035's catalog comment
says seven (`db/038` § 3) while its stripped `--` prose still says nine and cannot be corrected.
**Before the first production load**: every parser re-run against a current release, #17's `add_claim` check,
**three** rule-6 deeds (#6, #25, GSRS) — PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`. Set it as `DRUGREF_TEST_DSN` for the DB-gated tests.
- **WHICH VERIFICATION DATABASE TO READ IS NOT NAMED HERE** — it is stated once, in `PROJECT-NOTES.md`'s "Dev DSN" bullet of § How to run / test. An earlier version of this bullet said exactly that and then named it anyway; the name moved this round, and that copy would have outlived it.

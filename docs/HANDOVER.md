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
number — **ROADMAP's order is NOT the merge order.** **`db/029`–`db/038` ARE ALL FROZEN**: `db/038` joined them
when PR [#119](https://github.com/cairn-ehr/drugref/pull/119) merged (`20c4701`, 2026-08-15). The previous
handover said `db/038` was "UNMERGED and therefore still editable" — **that licence is spent; a correction now
needs `db/039`.**

**⇒ JUST FINISHED — THE GUARD ROUND: issues 118, 120, 122 closed, and NO MIGRATION AT ALL** — the first such
round since 5c.1, because all three were fixable in Python. Suite **1564 → 1598**, `ruff` clean, **ten
mutations run against the new branches and all ten fail**. Three new modules (`commit_lint.py` 175,
`registry_read.py` 64, `migration_guard.py` 144) and one shipped git hook. Full account: PROJECT-NOTES § "The
guard round"; ROADMAP § 5c.2f.

**⇒ THE HEADLINE — THE COMMIT GUARD FOUND A SIXTH OCCURRENCE ON ITS FIRST RUN, AND IT HAD GONE UNCOUNTED FOR A
ROUND.** #118 was filed against **five** commits that closed an issue nobody meant to close. Running the
finished check over **all 363 commits** flagged 14 — **6 accidental, 8 deliberate** — and turned up **#108**,
closed by `293758c`'s *"Filed rather than fixed: #108 …"* **one round BEFORE #114**, with #109–#112 in the same
sentence left open (`gh api .../issues/108/timeline` names the commit). **Every document in this repo said
five.** The failure is silent by construction, so every hand count undercounted — **and the count was the
evidence the prose rule was failing**, so the undercount understated the case for fixing it. #108 was fixed
later by db/037 anyway, so no work was lost; **that is luck, not a mitigation.**

**⇒ INSTALL THE HOOK IN EVERY CLONE — `git config core.hooksPath .githooks`.** It is LOCAL git config, not a
committed file, so a fresh checkout has none, and **a guard nobody installed is issues 74/66/76's "gate that
exists and never fires"**. Escape is `--no-verify`. Recorded in PROJECT-NOTES § "How to run / test".
**THE HOOK CANNOT SEE PR DESCRIPTIONS, WHICH GITHUB ALSO PARSES** — filed as
[#124](https://github.com/cairn-ehr/drugref/issues/124) (a CI check reusing the same pure function; no new
logic, only a second caller and a workflow). **Until it lands, keep writing *"issue 114"*, no `#`, in a PR
body** — and note its count is UNKNOWN rather than zero: nothing has ever measured that surface.

**⇒ #122's REAL FINDING: PROBING THE RELATION DOES NOT CLOSE THE LOOP — THE LEDGER DOES.** The worst case is
self-referential: dropping `severity_kind` takes `curated_unrankable_severity` with it, so the detector written
to REPORT that fault told the operator to run a migration the ledger says already ran — **a no-op, printing the
same sentence forever**. Absence alone still reads as "behind on migrations"; **absent + recorded-applied means
DROPPED**, and nothing else says so. Four states, one PURE wording function, and
`exc.diag.message_primary` carried in **every** branch — `cli.main` prints only the outer message, so
`raise ... from exc` had been preserving a cause nobody rendered. **`db.missing_relations` ROLLS BACK FIRST**
or the probe raises `InFailedSqlTransaction` from inside the guard. **A FIFTH guard now covers the clinician
path**, which alone had none.

**⇒ #120 — AN ABSENCE ABOUT THE OVERLAY WAS PRINTED AS AN ANSWER ABOUT A DRUG.** A uuid naming nothing rendered
identically to an ungraded drug, exit 0. Now `registry_read.known_moieties` (a read of the identity SPINE, in a
module of its own — `curated_read` is scoped to the overlay, and that scope is precisely why the view cannot
answer), a banner naming each unknown uuid, **no grade block at all**, **exit 2**, and existence checked
BEFORE the self-pair branch. **The old test asserted the DEFECT as the contract** (`== 0`, `"no curated
grade" in out`) and was replaced — the fourth test in this project found pinning the wrong thing.

**⇒ TWO STALE FIGURES WERE FOUND AND CORRECTED, both of the "one home" kind this repo keeps paying for.**
PROJECT-NOTES' suite-count line said **1451 while the suite was 1564** — its own comment calls itself THE ONE
HOME for that number, and this is the **fourth** occurrence, which ran five rounds (1465→1564) before anyone
noticed. And **`questions.py` is 568 lines, over the cap and never on
[#89](https://github.com/cairn-ehr/drugref/issues/89)'s list** — measured at `HEAD`, so pre-existing.
**#89's figures are now: `signing.py` 605, `questions.py` 568, `release_verification.py` 540, `curation.py` 534
(523 + this round's two exported view-name constants). Read them off the issue, do not re-derive.**

**⇒ A TRAP THIS ROUND WALKED INTO AND RECORDED: DO NOT SCRIPT A COMMENT RE-WRAP.** Two crude passes at
reflowing over-long lines merged `@dataclass` field declarations into one line and split an f-string
mid-literal — in `db.py` it damaged `referenced_vocabulary`, which this round never touched. `ruff check` caught
all of it as `invalid-syntax`, and `git checkout` + re-apply was the fix. **Reflow prose by hand.**

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

**What gates the NEXT session, and only that** — **#112/#105** wait on class-grain CONTENT · **#124** is this round's own tail, and the surface it names is unmeasured · **#121 and #123
are the two review findings this round did NOT take** (#121 an orphaned curated grade reads as "no curated
grade" on the clinician path; #123 the detector sweeps 2 of 5 tables with a `severity_kind` FK) · **#89 now has
FOUR files over the cap** · **#94's seven withheld entries** still need research, and db/035's catalog comment
says seven (`db/038` § 3) while its stripped `--` prose still says nine and cannot be corrected.
**Before the first production load**: every parser re-run against a current release, #17's `add_claim` check,
**three** rule-6 deeds (#6, #25, GSRS) — PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`. Set it as `DRUGREF_TEST_DSN` for the DB-gated tests.
- **WHICH VERIFICATION DATABASE TO READ IS NOT NAMED HERE** — it is stated once, in `PROJECT-NOTES.md`'s "Dev DSN" bullet of § How to run / test. An earlier version of this bullet said exactly that and then named it anyway; the name moved this round, and that copy would have outlived it.

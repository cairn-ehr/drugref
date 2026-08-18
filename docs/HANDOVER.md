# HANDOVER — drugref

> **The volatile half: where we are right now.** Regenerated at the end of every working session
> (nextsession rule 9) and kept **under 130 lines**, so a rewrite costs nothing.
>
> **THIS LINE IS THE ONLY HOME FOR THAT NUMBER.** Do not restate it in `CLAUDE.md`, a skill, or elsewhere.
> A bound is a vocabulary like any other, and this repo has repeatedly lost rounds to one rule kept in two
> places.
>
> **The stable half is [`PROJECT-NOTES.md`](PROJECT-NOTES.md)** — traps, state by layer, commands, schema and
> code map. Edited in place, under no bound. Slice sequencing is [`ROADMAP.md`](ROADMAP.md); canonical what/why
> is in the immutable specs under [`superpowers/specs/`](superpowers/specs/).

## ⇒ NEXT

**Current branch: `codex/reviewer-account-administration`, from merged PR #141 on `main` at `dd8e653`.** Migrations through
**`db/046`** are frozen; `db/046` changes only the signing registry's catalog comment.

**PREVIOUS SLICE — local key enrolment and detached sign/verify/resume.** Canonical design:
[`2026-08-18-drugref-reviewer-signing-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-signing-design.md).
Stronghold retains the encrypted private key locally while the service enrols only its public half, rebuilds canonical
bytes and verifies detached signatures. Pending signatures survive queue refresh/restart. Lost-passphrase replacement
appends a time-scoped `rotated` correction before native cleanup; it never deletes signing history.

**⇒ JUST FINISHED — append-only reviewer account administration.** Canonical design:
[`2026-08-18-drugref-reviewer-account-administration-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-account-administration-design.md).
The administrator GUI now selects current reviewers, previews complete profile corrections, disables/enables access,
rotates Argon2id passwords and revokes all live sessions. Stable usernames remain immutable. Self-disable, self-rotation
and self-revocation clear native authentication plus prepared signing state and return to sign-in.

**ADMINISTRATION CANNOT ERASE HISTORY OR LOCK OUT THE SERVICE.** Profile forms carry `expectedProfileRevisionId`; the
service serializes mutations, rechecks current administrator authority inside the transaction and refuses to disable or
demote the last active administrator. Disablement and rotation append reason-specific revocations. Session creation binds
to the credential revision actually verified, so an old password cannot finish login after a concurrent rotation.

**⇒ DO THIS NEXT FOR THE GUI:** design the remaining reviewer/key trust round: general retired/compromised key
administration, revocation queues and counter-signing policy. Do not broaden the existing owned-device lost-passphrase
replacement while doing so. Issue #86's `signed_by_unknown_key` vocabulary widening remains its own explicit round.

**Verification completed:** full Python/PostgreSQL suite 1,790; domain 16; service 12 plus the live clean-database account
lifecycle integration; native 5; `ruff`; Rust formatting/clippy with warnings denied; `npm run check` with 0 diagnostics;
production frontend build (0.63 kB HTML + 30.45 kB CSS + 114.51 kB JS, 38.01 kB JS gzipped); native debug no-bundle
build; npm audit with 0 vulnerabilities; `git diff --check`. Chrome passed creation, correction, disable/re-enable,
last-admin refusal, password rotation and self-session revocation at 1,440 x 900 and 740 x 900, with no horizontal
overflow or console warnings.

## Parallel project sequencing

The next **content** slice remains DrugCentral ([#101](https://github.com/cairn-ehr/drugref/issues/101)):
6,337 candidate public-domain moiety-grained pairs, rule 6 clear for `ddi_ref_id = 2` only. Every figure rests
on one unretained 1.4 GB dump run, so re-measure before design. It is the first content capable of populating
the class grain and meeting #105/#106/#112. Pregnancy/lactation and FDA toxicity remain ready but separately
gated as ROADMAP states.

Do not lose 5c.2g's two broad lessons: a comment claiming a guard exists is not evidence the code contains the
guard; and a plausible figure from a partially working parser is not a measurement. Full findings and seven
spec-figure corrections live in PROJECT-NOTES § "Slice 5c.2g".

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). For the
next rounds: #128/#129 and #132–#135 are FDA-CYP residue; #112/#105 wait on class-grain content; #124 is the
guard-round tail; #121/#123 remain open; #104 makes question counts depend on which unrelated ingest ran last;
#94's seven withheld entries need research. Before production: re-run every parser on current releases, resolve
#17, and complete the three outstanding rule-6 deeds (#6, #25, GSRS).

One decision is taken and not built: #86 adds `signed_by_unknown_key` as a fourth signature status in its own
vocabulary-widening round.

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use this database for persistent reviewer accounts or GUI service data because pytest recreates it.
- The verification database and its exact migration state live once in PROJECT-NOTES § "How to run / test";
  do not copy that volatile map here.

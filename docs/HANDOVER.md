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

**Current branch: `codex/reviewer-gui-finalization`, from merged PR #144 on `main` at `cfc542f`.** Migrations through
**`db/048`** are frozen; `db/048` widens only the published registry-level signature status.

**PREVIOUS SLICE — local key enrolment and detached sign/verify/resume.** Canonical design:
[`2026-08-18-drugref-reviewer-signing-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-signing-design.md).
Stronghold retains the encrypted private key locally while the service enrols only its public half, rebuilds canonical
bytes and verifies detached signatures. Pending signatures survive queue refresh/restart. Lost-passphrase replacement
appends a time-scoped `rotated` correction before native cleanup; it never deletes signing history.

**PREVIOUS SLICE — public signing-key trust administration and counter-sign queues.** Canonical design:
[`2026-08-19-drugref-reviewer-key-trust-design.md`](superpowers/specs/2026-08-19-drugref-reviewer-key-trust-design.md).
Administrators inspect every public fingerprint, reviewer enrolment, status boundary and current-review impact, then append
time-scoped retirement or permanent compromise. Compromise can escalate a rotated/retired key and cannot be downgraded.

**PENDING MEANS ZERO UNOBJECTED SIGNATURES.** Unsigned revisions enter normally; current revisions left only with registry-
objected attestations return as explicit counter-sign tasks. One independent unobjected signature resolves the task while
the compromised signature remains immutable. Clinical rows remain served. PostgreSQL supplies registry policy, not an
Ed25519 verdict. `db/047` closes #85; `signature_target_kind` remains mutable for versioned payload contexts.

**⇒ JUST FINISHED — internal reviewer GUI finalization.** `db/048` publishes `signed_by_unknown_key`
separately from revoked keys; the Rust/service/WebView contract is a closed four-state vocabulary with human labels.
Unknown outranks revoked only when every signature is objected, while one unobjected signature still restores `signed`.
Clinical rows remain served and `drugref verify` retains its six verdicts. The audit also fixed a real GUI dead end:
counter-sign tasks were listed but the
shared control admitted only literal `unsigned`; every non-`signed` current revision can now be independently attested.
The disabled global Evidence library placeholder is removed; target-scoped immutable citations remain fully functional.

**⇒ DO THIS NEXT:** publish this GUI-finalization change as its own PR and close #86 on merge, then return to the
separately
gated DrugCentral content slice #101. Do not bundle release-manifest signing, class-grain issue #98 or public installer
distribution. The internal GUI has no known open functional follow-up; signing/notarization and downloads are
release work.

**GUI-finalization verification:** full Python/PostgreSQL suite 1,794; domain 18; service 12 plus live detached
signing and
counter-sign lifecycle; native 5; `ruff`; Rust formatting/clippy with warnings denied; `npm run check` with 0 diagnostics;
production frontend and debug macOS app builds; npm audit with 0 vulnerabilities; strict MkDocs; `git diff --check`.
Browser preview exercised record/sign/compromise/counter-sign at 1,440, 740 and 520 px with no overflow or console
warnings.
The strictly ad-hoc-signed `.app` launched against the persistent migrated reviewer service and remained running.

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

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use this database for persistent reviewer accounts or GUI service data because pytest recreates it.
- The verification database and its exact migration state live once in PROJECT-NOTES § "How to run / test";
  do not copy that volatile map here.

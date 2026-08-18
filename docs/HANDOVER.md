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

**Current branch: `codex/reviewer-signing`, from merged PR #140 on `main` at `c9f9653`.** Migrations through
**`db/046`** are frozen; `db/046` changes only the signing registry's catalog comment.

**PREVIOUS SLICE — transactional curated interaction and condition revisions.** Canonical design:
[`2026-08-18-drugref-reviewer-curated-revisions-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-curated-revisions-design.md).
The service resolves the frozen target key and question, derives release provenance, attributes the immutable row from
the authenticated profile, and uses a target advisory lock plus `expectedRevisionId` to reject stale concurrent forms.
The GUI provides distinct interaction/condition vocabularies, grade completeness, two-step preview and full history.

**⇒ JUST FINISHED — local key enrolment and detached sign/verify/resume.** Canonical design:
[`2026-08-18-drugref-reviewer-signing-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-signing-design.md).
Tauri integrates Stronghold directly behind narrow commands: a per-reviewer Argon2id-encrypted local snapshot retains
the Ed25519 private key, while only its public key/fingerprint reaches the authenticated service. No generic vault/sign
procedure is exposed to the WebView, no path is client-controlled, and the signing passphrase is zeroized after use.

**RECORDING AND SIGNING REMAIN TWO ACTIONS.** `GET /v1/review-signature` resolves the current natural key and produces
the exact frozen `/v1` fields, server-issued signing instant and SHA-256 digest. Native code independently validates and
retains those canonical bytes for explicit confirmation. The GUI now shows every validated named value in canonical order,
with complete clinical narrative text and explicit NULL, rather than asking a reviewer to trust a heading and digest.
`POST /v1/review-signature` re-resolves the row and active key,
enforces a five-minute challenge window, rebuilds the payload, verifies Ed25519 and appends `assertion_signature`.
The shared encoder is pinned against every committed Python signing vector.

**QUEUE REFRESH CANNOT STRAND SIGN-OFF.** `GET /v1/pending-signatures` lists current interaction/condition revisions with
no signature. The new Pending signatures view reloads history and resumes the same prepare-confirm-unlock flow after a
queue refresh or app restart; verified rows leave the list. Browser preview remains isolated in memory and never becomes
a native failure fallback. `db/046` replaces `db/030`'s obsolete “no enrolment protocol” catalog claim and names
authenticated active reviewer enrolment as the initial-registration trust root; no table shape changed.

**LOST-PASSPHRASE REPLACEMENT IS AUDITED, NOT A HARD DELETE.** Canonical design:
[`2026-08-18-drugref-reviewer-key-replacement-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-key-replacement-design.md).
The authenticated service records `rotated` plus an unenrolment correction in one transaction, reports the number of
preserved signatures, and is idempotent for native cleanup retry. Tauri derives the fingerprint and fixed file paths,
deletes `.hold`, `.salt` and `.fingerprint` only after the transaction commits, then permits a fresh key/passphrase.
An unused key has zero preserved signatures and changes no clinical record.

**⇒ DO THIS NEXT FOR THE GUI:** finish the `db/044` administration tail: append-only profile correction, disable/enable,
password rotation and all-session revocation. General retired/compromised key administration remains a separate trust round;
the lost-passphrase path now supports only owned device-key replacement through time-scoped `rotated` correction.

**Verification completed:** full Python/PostgreSQL suite 1,790; domain 14; service 12 plus the live PostgreSQL
enrol/replace/prepare/sign/verify/rotate/persist/resume integration; native 5 including fixed-path replacement cleanup,
Stronghold restart, wrong-passphrase, signature verification and exact-field projection; `ruff`; Rust
formatting/clippy with warnings denied; `npm run check` with 0 diagnostics; production frontend build (0.63 kB HTML +
28.06 kB CSS + 104.04 kB JS, 35.30 kB JS gzipped); debug macOS app bundle with strict code-sign verification; npm audit
with 0 vulnerabilities; `git diff --check`. Complete 15-field signing review passed at 1,440 x 900 and 740 x 900 with no
horizontal clipping or console warnings.

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

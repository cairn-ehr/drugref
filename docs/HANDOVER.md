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

**Current branch: `codex/reviewer-curated-revisions`, from merged PR #139 on `main` at `a1291cd`.** Migrations through
**`db/045`** are frozen.

**PREVIOUS SLICE — authenticated live, paginated reviewer queue.** Canonical design:
[`2026-08-17-drugref-reviewer-live-queue-design.md`](superpowers/specs/2026-08-17-drugref-reviewer-live-queue-design.md).
Any live reviewer session may call `GET /v1/review-queue`; Tauri attaches the bearer token from native memory and
the WebView retains no network permission or database credential. Page size is bounded, search is a literal
substring, and kind/source/relationship filters come from the current database queue.

**ONE MATERIALISED UNION OWNS EACH RESPONSE SNAPSHOT.** The interaction half reads the existing
`ci_rule_partner_reach` aggregate and applies current expansion policy instead of enumerating millions of candidate-pair
join rows merely to count them; the condition half reads its inexpensive gap view. The reviewed-pair summary likewise
sums the exact moiety- and class-rule reach aggregates instead of expanding `curated_ddi_pair`. The local interaction
plan fell from 3.02 s and a 387 MB temporary sort to 34.7 ms, while the replacement pair count took 32.9 ms; both had
zero mismatches against their authoritative views. The complete unfiltered 25-row queue query then ran in 87.5 ms.
Sources, releases and condition predicates remain arrays. No queue table, cache or migration was added.

**THE CLINICAL QUEUE IS LIVE AND STILL DELIBERATELY READ-ONLY.** The fixture-only `in_review`, priority and
signature fields are removed; gaps say **Unreviewed**, because no curated row exists to sign. Search is debounced,
filters reset pagination, stale responses cannot overwrite a newer request, and inline failures preserve the last
successful page. The browser-only Vite adapter remains explicit preview data and is never a native fallback.

**GUI HOUSE-RULE CATCH-UP IS COMPLETE.** `CONTRIBUTING.md` is now the durable repository rule: functions and
public contracts require docstrings, behavioural values require documented constants, dynamically typed code
requires complete type hints, and pure reusable logic belongs in focused modules where meaningful. The GUI now
centralises validation and paging constants, keeps pure queue/presentation transforms outside components, and
enforces Rust public API documentation with `deny(missing_docs)`.

**PREVIOUS SLICE — append-only working notes and citation-only evidence references.** Canonical design:
[`2026-08-17-drugref-reviewer-annotations-design.md`](superpowers/specs/2026-08-17-drugref-reviewer-annotations-design.md).
`db/045` adds two immutable reviewer-attributed ledgers against the immortal open-question UUID. Evidence references
carry a structured DOI/PMID/PMCID/NCT/SPL/URL identifier and optional context, deliberately no verdict, confidence,
grade, clinical ruling or signature.

**THE QUEUE/WRITE SEAM NOW USES THE REGISTRY'S FROZEN TARGET KEY.** The service resolves current canonical keys rather
than minting a second question UUID, rejects stale targets, takes authorship only from the authenticated session, and
returns insertion-ordered history. Tauri owns all HTTP and token access. The browser preview keeps isolated in-memory
working history and is never a native fallback. Clinical decision fields and signing were hard-disabled in that slice.

**⇒ JUST FINISHED — transactional curated interaction and condition revisions.** Canonical design:
[`2026-08-18-drugref-reviewer-curated-revisions-design.md`](superpowers/specs/2026-08-18-drugref-reviewer-curated-revisions-design.md).
The service resolves the canonical target and question, derives current release provenance, attributes the row from the
authenticated profile, and performs the existing insert-then-supersede sequence. A target advisory lock plus the form's
`expectedRevisionId` refuses stale concurrent submissions rather than silently overwriting another reviewer's decision.

**THE GUI NOW RECORDS BUT DOES NOT SIGN.** Interaction and condition forms use their distinct ruling vocabularies, enforce
the overlay's grade completeness, preview the immutable row and predecessor, and render full revision/signature-status
history. A successful write refreshes the queue. New revisions remain prominently unsigned; private keys, enrolment,
signing and verification are absent. Browser preview uses isolated in-memory history.

**⇒ DO THIS NEXT FOR THE GUI:** local signing-key enrolment and the detached sign/verify/resume flow. Then finish profile
correction, disable/enable, password rotation and all-session revocation administration over `db/044`.

**Verification completed:** full Python/PostgreSQL suite 1,787; domain 10; service 10 plus a live PostgreSQL interaction and
condition initial-write, correction/history and stale-form integration; Tauri 1; `ruff`; Rust formatting and clippy with
warnings denied; `npm run check` with 0 diagnostics; production frontend build (0.63 kB HTML + 22.51 kB CSS + 86.32 kB JS,
29.91 kB JS gzipped); `npm audit` with 0 vulnerabilities; `git diff --check`. Chrome passes at 1,440 x 900, 980 x 680 and
740 x 900 covered sign-in, target switching, interaction and condition vocabularies, ruling-dependent grade controls,
immutable preview, record/history/unsigned rendering and disabled signing. The intermediate document remained
viewport-bound with independent queue/detail scrolling. The narrow pass caught and verified a footer-flow fix.

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

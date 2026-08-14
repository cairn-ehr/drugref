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
all **51** open issues for work that is small, self-contained and needs no design decision. **Eight cleared —
79, 87, 100, 108, 109, 110, 111, plus 19 and 106 answered by measurement.** Suite **1465 → 1511**, `ruff` clean.
Full account, every measurement, and **the list of what was deliberately NOT taken and why**: PROJECT-NOTES §
"The low-hanging-debt round"; ROADMAP § 5c.2c.

**⇒ EVERY PUBLISHED COUNT IS BYTE-IDENTICAL BETWEEN `drugref_db036` AND `drugref_db037`** — `ddi_candidate_pair`
21,877 · `curated_ddi_pair` 255 · `open_question` 21,848 · `gap_unpopulated_contraindication` 13 ·
`condition_contraindication_expanded` 192,161 · `class_expansion_policy` 14 · `loaded_release` 6.
`class_pair_contraindication` is **EMPTY on every database in existence** (#94 withheld its seven entries), which
is precisely what made this the cheap moment to correct the class grain's arithmetic.

**⇒ THE ONE SURPRISE, and it changes what a consumer sees today: `curated_ddi_pair_effective` is NOT a no-op.**
With zero class-grain content it still collapses **255 rows to 213** — 42 doubled pairs, **all 42 explained by
`candidate_source` alone** (a rule both MED-RT and ONCHIGH assert is one grade and two rows), zero differing in
severity, `via_class` or `rule_grain`. Every client reading `curated_ddi_pair` was seeing that duplication.

**⇒ WHAT `db/037` ACTUALLY CHANGES.** **#108** — `max_pair_count` subtracts a new published
`shared_effective_member_count`, so reach is `|S|·|O| − |S ∩ O|`; **the self-pair rule is the special case, not
the fix** (two classes sharing members overstate identically, and MED-RT files one drug under many classes), and
the *same wrong number* had the worklist queueing a rule no answer could change while `status` hid it. **#109** —
`curated_grain_disagreement` normalises orientation with `LEAST`/`GREATEST`, **an equi-join where the obvious
`OR` of two arm pairs is not**, on a view `status` reads unfiltered every invocation. **#110** — the precedence
is a view at last, with **`NULLS FIRST`**: Postgres's default sorted an unrankable severity *below* `minor`,
inverting the harm direction in the one path db/035 called safe.

**⇒ THE MEASUREMENT THAT IS HONESTLY NOT EVIDENCE — read this before quoting it.** Interleaved against
`drugref_db036` (#81's method, 6 alternating runs each): `SELECT count(*) FROM curated_grain_disagreement`
**2777.9 → 2750.2 ms**, filtered `curated_ddi_pair` **2759.6 → 2735.2 ms** — both −1%, noise. **But the
class-grain half of `curated_ddi_pair` is EMPTY, so the LEAST/GREATEST join had nothing to join**, and both
probes are dominated by `ddi_candidate_pair`'s ~2.7 s unfiltered scan (#75) inherited whole. It is a fair A/B
and it says nothing about the new arm's cost. **[#112](https://github.com/cairn-ehr/drugref/issues/112) still
owns that measurement** — db/024's 59 s → 465 ms precedent is "a synthetic probe looked fine because its fixture
had no edges".

**⇒ THE REFERENCE DATABASE IS NOW `drugref_db037`** (ledger 37), from `TEMPLATE drugref_db036` + `drugref
migrate` — that workflow re-tested for the FOURTH round running. **TWO CONTROLS ARE KEPT and they answer
different questions**: `drugref_db036` is `db/037`'s before/after (what the timings above were taken against),
and **`drugref_db034` is the pre-`db/035` control** — the only one that still exercises the class-grain block's
missing-view guard, verified again this round (exit **2**, one sentence, no traceback). `drugref status` was
also re-run on `drugref_db036` **with the new code** and exits 0: last round's `UndefinedColumn` shape does not
recur, because the new denominator reads only pre-`db/037` columns.

**⇒ DO THIS NEXT — the next slice, and the evaluation says the cheap one is DrugCentral, not SPL**: 6,337 new
public-domain moiety-grained pairs, hard part name resolution. Either way it opens with its own design round;
[#101](https://github.com/cairn-ehr/drugref/issues/101) holds the DrugCentral shape and its two rules
(`ddi_ref_id = 2` only; the 2023-11-01 dump does not refresh). **`5c.3` — SPL/DailyMed mining** must answer two
things: section 7 qualifies by **potency band**, which MED-RT's one undifferentiated class **cannot express**
([#102](https://github.com/cairn-ehr/drugref/issues/102)), and its corpus must be filtered by **document type** — key
on the CODE (`34391-3`/`34390-5`), not `displayName` (a 50-label sample gave 14/16 prescription, 0/30 OTC —
**indicative only, re-measure**). **Whichever lands is the first slice that can POPULATE the class grain**, so
`db/035`'s detectors and `db/037`'s arithmetic get their first real exercise then — and #105, #106 and #112 all
become answerable against content rather than against nothing.

**⇒ ONE DECISION IS TAKEN AND NOT BUILT — do not re-litigate it.**
[#86](https://github.com/cairn-ehr/drugref/issues/86): **add `signed_by_unknown_key` as a fourth
`signature_status` value.** It is a published-vocabulary widening with spec and consumer consequences, so it is a
round of its own; the decision is recorded on the issue.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61). **Standing rule:** near `close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#` ("issue 65"). Full account: PROJECT-NOTES.

## Open follow-ups (all filed as GitHub issues)

**Examined by the debt round and deliberately NOT taken** — **#65** the issue itself says do not act until
curation scales · **#30 blocked: no PBS release on disk** (`downloads/` holds UNII, MED-RT, MeSH, GSRS only) ·
**#112/#105** blocked on class-grain content existing · **#89 `signing.py` is now 605 lines against the filed
582, and `release_verification.py` went 532 → 540 in THIS round** (rule-3 documentation for #87) — **re-read
that issue's figures, do not re-derive them** · **#88** a type checker is a real ongoing cost and a decision ·
**#82/#104** both change the operator surface, held back deliberately · **#6, #25, #5** licence deeds need the
owner's sign-off.
**Answered by measurement this round, still open** — **#19: the "41 vs 13" puzzle RESOLVES.** 41-of-739 was the
TERMINOLOGY grain; drugref holds **643** rules and the authoritative figure is **39 dead rules across 13
classes** — the view's extra one is `Urease Inhibitors [MoA]`, whose only member is the rule's own subject
(db/018 subtracts it). **Two of its three asks already shipped.** · **#106: 46 of 21,370 pairs (0.22%) are
reachable on two axes and NONE is graded** — the shape is not live, and the 46 bounds the widening it proposes.
**Left open by 5c.2** — **#92 a mixed-kind class-pair rule expands to ZERO pairs silently** (the real fix is
schema-level: a rule naming two axes) · **#93 MED-RT carries no QT class** · **#94 the seven withheld entries**
need research. **#100 is CLOSED**: `ci_class_subtree`'s narrow definition is pinned from `pg_depend`,
mutation-verified against db/033's wide seed.
**Filed by 5c.4 and its review** — **[#85](https://github.com/cairn-ehr/drugref/issues/85)
`signing_key_status_kind` has no append-only floor**, so one `UPDATE` disarms every compromise verdict; **floor
that one ALONE** — `signature_target_kind` is *designed* to move to a `/v2` · #86 (decided, above) · #88 · #89.
Unfiled: `tests/test_cli_signing*.py` **cannot commit for real** — test isolation, shaped like #2.
**Earlier rounds** — #81 chain-time variance (**its interleaved-control method is what this round used**) · #82 ·
**#75 `gap_uncurated_interaction_rule` costs ~2.7 s**, re-confirmed incidentally — it is what both hot-path
probes above are actually measuring · #65.
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
safe, restricting the *walk* deletes the coagulation case · #48 · **#2 floor hardening** (`TRUNCATE` + owner-role
bypass; **13** `TRUNCATE`-ing modules depend on it — re-grep before quoting) · #3 UNII-change immortality ·
#33 MeSH CAS keys name specific forms (behind #68) · #5 INN from UNII's `Display Name` · **#7/#29 row-at-a-time
ingest** — the two MeSH legs are 75.7% of a 133 s chain. **Before the first production load**: every parser
re-run against a current release, #17's `add_claim` canonicalisation check, **three** rule-6 deeds (#6, #25,
GSRS) — PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`. Set it as `DRUGREF_TEST_DSN` for the DB-gated tests.
- **WHICH VERIFICATION DATABASE TO READ IS NOT NAMED HERE** — it is stated once, in `PROJECT-NOTES.md`'s "Dev DSN" bullet of § How to run / test. An earlier version of this bullet said exactly that and then named it anyway; the name moved this round, and that copy would have outlived it.

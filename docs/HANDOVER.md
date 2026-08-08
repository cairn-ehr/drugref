# HANDOVER — drugref

> **The volatile half: where we are right now.** Regenerated at the end of every working session
> (nextsession rule 9) and kept **under 130 lines**, so a rewrite costs nothing.
>
> **THIS LINE IS THE ONLY HOME FOR THAT NUMBER.** It was also written in `CLAUDE.md` (twice) and in the
> `nextsession` skill, all three said `~120` while this header said `~130`, and the file was 136 — a bound
> is a vocabulary like any other, and this repo has lost four rounds to one rule kept in two places. Both
> now say "the number its own header states" and point here. Change it here, alone.
>
> **The stable half is [`PROJECT-NOTES.md`](PROJECT-NOTES.md)** — traps, state by layer, how to run and test,
> the schema and code map, upstream errata, repo facts. It is edited in place and under no bound. **Put
> anything whose history is worth reading there, not here** (#63): this file's history is deliberately
> disposable, and content that lands here gets compressed away.
>
> Slice sequencing is [`ROADMAP.md`](ROADMAP.md); the canonical what/why is the specs under
> [`superpowers/specs/`](superpowers/specs/).

## ⇒ NEXT

**Merged to `main`** (ROADMAP orders them, full list there): everything through **slice 5c.1 — the curated overlay's assertion
shape** (PR [#77](https://github.com/cairn-ehr/drugref/pull/77), merged **2026-08-06**, `db/029`, EMPTY as planned). **`db/029` is
MERGED and FROZEN** — corrections need a new `db/NNN`. Figures and traps: PROJECT-NOTES § "Slice 5c.1".

**⇒ RE-MEASURED POST-MERGE (2026-08-08) on a fresh `drugref_5c1m`** (PR [#78](https://github.com/cairn-ehr/drugref/pull/78)), because
every published 5c.1 figure was taken on a schema the two review rounds then edited twice. **Every COUNT and INGEST SUMMARY
reproduces EXACTLY** (`ddi_candidate_pair` 21,664 · **168** contradicted pairs · **595** interaction rules · `open_question`
**21,842**); the `EXPLAIN ANALYZE` timings and the suite figure were **not** re-run. Chain 144 s against 127.5 s — **uncontrolled**,
filed as #81, not a warm cache. **Its own review** closed twelve findings, all in the state files; the two substantive (the `~739`
catalog fix that no test killed → now `tests/test_curated_interaction_comment.py`; `pair_count` "verified" on a reading identical on
the BROKEN view → `drugref_5c1` **kept as the control**) plus two new standing rules are in PROJECT-NOTES.

**⇒ THE GATES-THAT-DO-NOT-FIRE ROUND (issues 74, 66, 76) — no migration, suite 943 → 969.** Three checks that existed and never
fired. **74**: five of the seven single-live index tests **passed a `UNIQUE` mutation** — measured, not reasoned — which would forbid
every correction the overlay exists to make. All seven now share one `assert_live_key_index` fixture (**four** properties, not three)
with its own guard file mutating the real index inside the test transaction, and **the seven are DERIVED from `pg_trigger.tgargs`**
so an eighth cannot arrive unguarded. **66**: there was **no lint gate at all** — no `[tool.ruff]`, `ruff` not a project dependency
(a pyenv shim answered `uv run ruff`), **no lint job in CI**. Now `line-length = 88` + `E`/`F`/`W`, `src/`'s 52 lines reflowed, ruff
pinned, a CI `lint` job, and **`ruff check .` is the right command** — 0.18 s *because ruff honours `.gitignore`*, NOT because of
`extend-exclude`, which is belt-and-braces; the old "used to hang on `downloads/`" claim does **not** reproduce. `tests/`' **334**
long lines are carved out as #79 — a figure with a date, not a constant. **76**: `curated_target_unresolved` had no consumer — the
second time — now `curation.unresolved_targets`, printed as `drugref status`'s third block. Full account: PROJECT-NOTES § "The
gates-that-do-not-fire round".

**⇒ THE REVIEW OF PR #80 — the round's own thesis turned on it; suite 956 → 969. Three gates this branch ADDED did not fire either.**
(1) `test_a_superseded_judgement_is_not_an_orphan` never deleted the candidate, so its empty result was over-determined — removing
`superseded_by IS NULL` from **both** arms of db/029's view left the suite green. (2) CI's `Confirm nothing was skipped` step piped
pytest through `tee` under `bash -e` — **no pipefail** — so 5 failures exited 0. (3) The "no SQL in cli.py" grep was blind to
line-split literals, `INSERT`, `UPDATE` and `JOIN` — and **this round's own 88-column gate is what forces SQL to wrap**, so the lint
rule weakened the guard beside it. All three are now mutation-verified dead; the grep is rewritten over `ast.parse`d string constants
and covers `cli_policy.py`, which the rule was always about. Orphan exit-code channel → #82.

**⇒ NEXT SLICE: `5c.4` — signing, BEFORE `5c.2`'s first curated row**: a row committed before signing exists can never be signed
retrospectively, since the floor refuses `UPDATE`. **The order lives in ROADMAP § 5c as an execution-order callout** — the file
CLAUDE.md makes authoritative for sequencing. Nothing in the repo signs anything today — no key management, no signing identity, no
verification path — so this is a subsystem from scratch, not a column. `5c.1`'s worklist is the payload waiting on it: **168**
contradicted pairs and **595** ungraded interaction rules, both queryable today.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61). **Standing rule:** near
`close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#` ("issue 65"). Full account: PROJECT-NOTES.

## Open follow-ups (all filed as GitHub issues)

**Filed by the last three rounds** — [#79](https://github.com/cairn-ehr/drugref/issues/79) **`tests/` is exempt from E501** (its
title's 324 has drifted to **334** — re-measure, never quote; the carve-out is **debt, not policy** — delete the block in
`pyproject.toml` when 79 closes) · [#81](https://github.com/cairn-ehr/drugref/issues/81) **the chain's 127.5 s → 144 s delta is
uncontrolled**: take a per-leg breakdown and a repeat on the next full run · [#82](https://github.com/cairn-ehr/drugref/issues/82)
**`drugref status` reports orphans to humans only** — it exits 0, so the rebuild script that CAUSED them cannot see it; a
CLI-contract call, not a cleanup.

**Still open from slice 5c.1** — [#75](https://github.com/cairn-ehr/drugref/issues/75) **`gap_uncurated_interaction_rule` costs
~2.7s**, an unfiltered read of `ddi_candidate_pair` inherited whole from that view, not a new defect; not urgent at today's
cardinality and no consumer yet. (**76 and 74 are closed by the gates round**, whose fix also covered the fifth Plan C index the
parametrized test had never named.)

**Filed by the slice-3 design, its measurement, and the whole-branch review** — [#67](https://github.com/cairn-ehr/drugref/issues/67)
**salt↔base strength equivalence has no source** (409 *assay* specs, not conversion factors; MW covers 5.4%), routed to 5c ·
[#68](https://github.com/cairn-ehr/drugref/issues/68) **3,631 moieties carry a GSRS `ACTIVE MOIETY` edge to something else** (~19%;
unrepairable — immortal `moiety_uuid`, monotone gate; why issue 33 stays open) ·
[#69](https://github.com/cairn-ehr/drugref/issues/69) the 27-edge scope question above ·
[#70](https://github.com/cairn-ehr/drugref/issues/70) **354 all-false composites reachable and queued by nothing** ·
[#71](https://github.com/cairn-ehr/drugref/issues/71) **8,163 of 16,834 unregistered-component edges dropped, counted only
transiently** · [#73](https://github.com/cairn-ehr/drugref/issues/73) **both views read every source at once**; `db/028` is
immutable, so the next migration there carries it.

**Filed by the policy-surface round** — [#65](https://github.com/cairn-ehr/drugref/issues/65) **no index serves a HISTORY query** on
`class_expansion_policy`; unfixed at 14 rows, revisit at curation. (**66 is closed by the gates round**, which found the gap wider
than the issue said: ruff was not a dependency and CI never linted.)

**Owned by 5c** (5c.1's own design round routed all three here, unanswered) — [#51](https://github.com/cairn-ehr/drugref/issues/51)
**the 168 contradicted pairs**: 5c.1 gives them a queue (`gap_uncurated_condition_contradiction`) and a home for the ruling
(`curated_condition`); answering them is 5c.2+ · [#52](https://github.com/cairn-ehr/drugref/issues/52) **the 422 broadened
assertions**: no `concept_ui` on the row · [#55](https://github.com/cairn-ehr/drugref/issues/55) **`indications_for_condition`
generalises through a boolean**.

**Filed by the interaction debt round** — [#48](https://github.com/cairn-ehr/drugref/issues/48) **a non-expanding predicate with no
direct member is equally dead and is deliberately not reported**; unreachable until a *class-side* predicate stops expanding.
**Retired by the five debt rounds** (#50, #39, #31, #45 · #40, #17, #42, #41, #43 · #16, #47 · #35 ·
#59, #60, #61, #63), each verified against the code first; their **standing rules** are in
[`PROJECT-NOTES.md`](PROJECT-NOTES.md) § "Standing rules".

**Floor, identity and ingest correctness** — [#2](https://github.com/cairn-ehr/drugref/issues/2) **floor hardening** (the `TRUNCATE`
+ owner-role bypass; blocked on test isolation — **eleven** `TRUNCATE`-ing modules depend on it, re-grep before quoting) ·
[#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality** ·
[#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms** — slice 3 does **not** settle it, its own
proposed fix is refuted, blocked behind **#68** · [#30](https://github.com/cairn-ehr/drugref/issues/30) (`strip_salt`) **unmeasured**
for slice 3 · [#5](https://github.com/cairn-ehr/drugref/issues/5) INN sourced from UNII's `Display Name` ·
[#7](https://github.com/cairn-ehr/drugref/issues/7)/[#29](https://github.com/cairn-ehr/drugref/issues/29) **row-at-a-time ingest**,
MED-RT ~31k round trips and PBS ~28k; the MeSH-keyed leg is the slowest of ~127-137 s.

**Interaction model** — [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is unpopulated**, filed
as 41 of 739 but `gap_unpopulated_contraindication` returns **13**, so **re-measure before acting on the issue text** ·
[#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions**, Plan C's `interaction_group` is the shape ·
[#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` assertions unused** ·
[#36](https://github.com/cairn-ehr/drugref/issues/36) **discovery counts descendant classes, not reachable members** ·
[#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** — restricting the *root set*
is safe, restricting the *walk* deletes the coagulation case; **#75 above is the same cost, measured unfiltered**.

**Before the first production load** — every parser re-run against a current release, the `add_claim` canonicalisation check from
#17, and **three** rule-6 deeds (#6, #25, GSRS's *"unless otherwise noted"*): see PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`. Set it
  as `DRUGREF_TEST_DSN` for the DB-gated tests. **Which verification database to read, what it holds and what it does not, is stated
  once in `PROJECT-NOTES.md` § Repo facts** — read **`drugref_5c1m`**. Not restated here: this file is compressed every session, so a
  second copy would outlive the first and disagree with it.

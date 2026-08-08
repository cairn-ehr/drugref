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

**Merged to `main`** (ROADMAP orders them, full list there): everything through **slice 5c.1 — the curated
overlay's assertion shape** (PR [#77](https://github.com/cairn-ehr/drugref/pull/77), merged **2026-08-06**,
`db/029`, shipped EMPTY as planned). **`db/029` is MERGED and therefore FROZEN** — a correction needs a new
`db/NNN`. Every figure, every trap: PROJECT-NOTES § "Slice 5c.1".

**⇒ RE-MEASURED POST-MERGE (2026-08-08) on a fresh `drugref_5c1m`**, because every published 5c.1 figure was
taken on a schema the two review rounds then edited twice, and the merged `db/029` had never been run end to
end. Chain 144 s; **every count reproduces EXACTLY** (`ddi_candidate_pair` 21,664 · `gap_uncurated_condition_
contradiction` **168** · `gap_uncurated_interaction_rule` **595** · `open_question` **21,842**), every ingest
summary matched, and all four review fixes are confirmed in the live catalog. `pair_count` reads **identically**
on the drifted and merged databases — the fix is **latent, not unnecessary**: it diverges only once a second
authority exists. PR [#78](https://github.com/cairn-ehr/drugref/pull/78).

**⇒ THE GATES-THAT-DO-NOT-FIRE ROUND (issues 74, 66, 76) — no migration, suite 943 → 956.** Three checks that
existed and never fired. **74**: five of the seven single-live index tests **passed a `UNIQUE` mutation** —
measured, not reasoned — which would forbid every correction the overlay exists to make; the weakest counted
the index by name alone. All seven now share one `assert_live_key_index` fixture, and that fixture has its own
guard file mutating the real index inside the test transaction. **66**: there was **no lint gate at all** — no
`[tool.ruff]`, `ruff` not a project dependency (a pyenv shim answered `uv run ruff`), **no lint job in CI**. Now
`line-length = 88` + `E`/`F`/`W`, `src/`'s 52 lines reflowed, ruff pinned, a CI `lint` job, and **`ruff check .`
is now the right command** (0.18 s; it used to hang on `downloads/`). `tests/`' 324 long lines are carved out as
[#79](https://github.com/cairn-ehr/drugref/issues/79). **76**: `curated_target_unresolved` had no consumer —
the second time — now `curation.unresolved_targets`, printed as `drugref status`'s third block. Full account
and both traps this round walked into: PROJECT-NOTES § "The gates-that-do-not-fire round".

**⇒ NEXT SLICE: `5c.4` — signing. Per ROADMAP's own sequencing it must land BEFORE `5c.2`'s first curated
row**: a row committed before signing exists can never be signed retrospectively, since the floor refuses
`UPDATE`. Nothing in the repo signs anything today — no key management, no signing identity, no verification
path — so this is a subsystem from scratch, not a column. `5c.2` (the ONC high-priority DDI floor, Phansalkar
2012 / Ayvaz 2015) and `5c.3` (SPL/DailyMed mining) are the other two successors to 5c.1, in no forced order
relative to each other, but **neither writes a curated row before 5c.4 exists**. **No spec exists yet for any
of the three** — each starts with its own brainstorm/design round, like every slice before it. `5c.1`'s
worklist is the payload waiting for them: **168** contradicted pairs and **595** ungraded interaction rules,
both queryable today.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61; full account:
PROJECT-NOTES.md). **Standing rule:** near `close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#`
("issue 65"). The linker binds a keyword to the next `#N` in the phrase; intervening words do not save you.

## Open follow-ups (all filed as GitHub issues)

**Filed by this round** — [#79](https://github.com/cairn-ehr/drugref/issues/79) **`tests/` is exempt from
E501**: 324 lines exceed the 88 that issue 66 now enforces on `src/`, so `per-file-ignores` carries a carve-out
that is **debt, not policy** — delete the block in `pyproject.toml` when 79 closes.

**Still open from slice 5c.1** — [#75](https://github.com/cairn-ehr/drugref/issues/75)
**`gap_uncurated_interaction_rule` costs ~2.7s**, an unfiltered read of `ddi_candidate_pair` inherited whole
from that view, not a new defect; not urgent at today's cardinality and no consumer yet. (**76 and 74 are
closed by the round above**; 74's fix also covered the fifth Plan C index, `class_expansion_policy_live_key`,
which the parametrized test had never named.)

**Filed by the slice-3 design, its measurement, and the whole-branch review** —
[#67](https://github.com/cairn-ehr/drugref/issues/67) **salt↔base strength equivalence has no source** (409 *assay*
specs, not conversion factors; MW covers 5.4%), routed to 5c · [#68](https://github.com/cairn-ehr/drugref/issues/68)
**3,631 moieties carry a GSRS `ACTIVE MOIETY` edge to something else** (~19% of the registry; unrepairable — immortal
`moiety_uuid`, monotone gate; why issue 33 stays open) · [#69](https://github.com/cairn-ehr/drugref/issues/69) the
27-edge scope question above · [#70](https://github.com/cairn-ehr/drugref/issues/70) **354 all-false composites
reachable and queued by nothing** · [#71](https://github.com/cairn-ehr/drugref/issues/71) **8,163 of 16,834
unregistered-component edges dropped, counted only transiently** · [#73](https://github.com/cairn-ehr/drugref/issues/73)
**both views read every source at once**; `db/028` is applied and immutable, so the next migration there carries it.

**Filed by the policy-surface round** — [#65](https://github.com/cairn-ehr/drugref/issues/65) **no index serves a HISTORY
query** on `class_expansion_policy`; deliberately unfixed at 14 rows, revisit at curation. (**66 is closed by the
round above**, which found the gap wider than the issue said: ruff was not a dependency and CI never linted.)

**Owned by 5c** (5c.1's own design round routed all three here, unanswered) —
[#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168 contradicted pairs**: 5c.1 gives them a queue
(`gap_uncurated_condition_contradiction`) and a home for the ruling (`curated_condition`); answering them is 5c.2+ ·
[#52](https://github.com/cairn-ehr/drugref/issues/52) **the 422 broadened assertions**: the row carries no `concept_ui` ·
[#55](https://github.com/cairn-ehr/drugref/issues/55) **`indications_for_condition` generalises through a boolean, not a structure**.

**Filed by the interaction debt round** — [#48](https://github.com/cairn-ehr/drugref/issues/48) **a non-expanding predicate
with no direct member is equally dead and is deliberately not reported**. Still unreachable until a *class-side*
predicate stops expanding. **Retired by the five debt rounds** (#50, #39, #31, #45 · #40, #17, #42, #41, #43 · #16, #47 ·
#35 · #59, #60, #61, #63), each verified against the code first; the **four standing rules** they produced are in
[`PROJECT-NOTES.md`](PROJECT-NOTES.md) § "Standing rules".

**Floor, identity and ingest correctness** — [#2](https://github.com/cairn-ehr/drugref/issues/2) **floor hardening** (the
`TRUNCATE` + owner-role bypass; blocked on test isolation — **eleven** `TRUNCATE`-ing modules depend on it, re-grep
before quoting) · [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality** ·
[#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms** — slice 3 does **not** settle
it, its own proposed fix is refuted, blocked behind **#68** · [#30](https://github.com/cairn-ehr/drugref/issues/30)
(`strip_salt`) **unmeasured** for slice 3 · [#5](https://github.com/cairn-ehr/drugref/issues/5) INN sourced from UNII's
`Display Name` · [#7](https://github.com/cairn-ehr/drugref/issues/7)/[#29](https://github.com/cairn-ehr/drugref/issues/29)
**row-at-a-time ingest**, MED-RT ~31k round trips and PBS ~28k; the MeSH-keyed leg is the slowest of ~127-137 s.

**Interaction model** — [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is
unpopulated**, filed as 41 of 739 but `gap_unpopulated_contraindication` returns **13**, so **re-measure before acting on
the issue text** · [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions**, Plan C's
`interaction_group` is the shape · [#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` assertions
unused** · [#36](https://github.com/cairn-ehr/drugref/issues/36) **discovery counts descendant classes, not reachable
members** · [#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** —
restricting the *root set* is safe, restricting the *walk* deletes the coagulation case; **#75 above is this same
cost, now measured on an unfiltered read rather than the 3.1 ms filtered one**.

**Before the first production load** — every parser re-run against a current release, the `add_claim` canonicalisation
check from #17, and **three** rule-6 deeds (#6, #25, GSRS's *"unless otherwise noted"*): see PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
  Set it as `DRUGREF_TEST_DSN` for the DB-gated tests. The verification databases are listed in `PROJECT-NOTES.md` § Repo facts;
  **`drugref_5c1m`** is the current one, holding the real releases with the **merged** `db/029` at every figure above.
  **Not `drugref_5c1`** — it holds a pre-merge `db/029`, so `apply_migrations` refuses there; drop it when convenient.

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

**Merged to `main`** (ROADMAP orders them): slices 1 (#1) · 2a (#9) · 2a.1 (#10) · 2b · 5a · 8a (#28) · the foundation
review · Plan A · Plan B (#32) · the identity-spine fix round (#34) · the Plan B review round (#38) · 5b (#44) · the
post-5b debt round (#46) · the interaction debt round (#49) · 5b.2 (#54) · #53's population-label round (#56) · Plan C (#57)
· the ingest-operability round (#58, closing #16 and #47) · **the expansion-policy history round (#35, PR
[#62](https://github.com/cairn-ehr/drugref/pull/62))**.

**In flight: the policy-surface debt round (#59, #60, #61, #63)** on `fix/policy-surface-debt-round`, PR
[#64](https://github.com/cairn-ehr/drugref/pull/64), closing all four. **844 tests green** (835 before the PR-#64
review round), `ruff check src tests` + `mkdocs build --strict` clean. Re-measured through the EXACT `ingest chain`
invocation #60 used to refuse — fresh `drugref_policy_cli`, **113.99 s**, every published figure reproduced exactly
(table: ROADMAP.md), since this round changed no SQL and no ingest logic.

**The PR-#64 review round** (9 tests) took back one thing this round had itself introduced: `except CheckViolation`
had been added to `cli.main`'s `try`, **which wraps every handler, ingest included** — so an ingest defect printed one
context-free line and exited 2, this CLI's OPERATOR-ERROR code. The catch moved to `cli_policy._write`, where the
failing value demonstrably came off the command line. It also **got better where it belongs**: the message now quotes
`pg_get_constraintdef` (`db.constraint_definition`), so an operator is told what the CHECK accepts *by reading the
CHECK* — actionable without becoming db/006's second vocabulary. Also fixed: `policy show` stated flatly that an
unruled class "raises a question" **25 lines below the comment explaining why that does not always follow**, and a
test pinned the false sentence · `show` accepted a blank key half and answered about a class that cannot exist ·
`medrt_run`'s remedy trailed off in `...` where all five flags are `required=True`, so following it literally hit an
argparse error. Traps: [`PROJECT-NOTES.md`](PROJECT-NOTES.md). Errata: `docs-site/docs/decisions/`.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61; full account and the
#61 reopening: PROJECT-NOTES.md). **Standing rule, sharpened twice more this round:** near `close`/`fix`/`resolve` in any
inflection, write the number WITHOUT a `#` ("issue 65"). PR #64's header said "Also closes the CLI half of #61" and linked
it anyway — the linker binds a keyword to the next `#N` in the phrase, and intervening words do not save you.

**⇒ Next candidates:**

- **Slice 5c — the curated, signed overlay.** A projection may not invent a line of therapy, evidence strength or drug
  ordering; 5c is where a human adds those. **Plan C built the MACHINERY**; #35 exercised it on a fifth table. Owns #51/#52/#55.
- **Slice 3 — composition tree (salts/esters/hydrates via GSRS).** Salt-strip is down to **0.03%** of bridge rows, and #33/#30
  both wait on form→moiety. **The UNII release carries no parent-moiety column** (25 columns, none a relationship) — needs the
  GSRS full export, **a new source, so the rule-6 licence check runs BEFORE anything is downloaded.**
- **Step 8 — curation itself**, driven by Plan C's worklist (**381** effects awaiting a ruling), bound by §12-H: audit every
  file and predicate of a source before curating a gap it may already cover. **#36** blocks nothing but shapes it — the
  discovery heuristic counts descendant classes, not reachable members; needs a curator ruling and its own re-measure.

## Open follow-ups (all filed as GitHub issues)

**Filed by the policy-surface round** — [#65](https://github.com/cairn-ehr/drugref/issues/65) **no index serves a HISTORY
query** on `class_expansion_policy`: `db/027`'s only one is partial on the live rows (it serves `db/023`'s single-live
trigger), and history is the complementary population. Split out of #61, not closed with it. `decision_history` makes it
real (`EXPLAIN` shows a Seq Scan) but **deliberately unfixed** at 14 rows on an interactive command; revisit at curation,
weighing a second index's write cost on a bulk-load path. · [#66](https://github.com/cairn-ehr/drugref/issues/66) **no
enforced line length**: `pyproject.toml` has no `[tool.ruff]`, so the default rule set runs and **`E501` is not in it** —
`ruff check` reports "All checks passed!" on a 115-column line, and 52 lines exceed the ~88 every file is written to. The
two #64 introduced are fixed there; the rest is a decision (one number, and whether `tests/fixtures/` is exempt).

**Filed by slice 5b.2 and its review (all three for 5c)** — [#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168
pairs both indicated and contraindicated**, counted not resolved · [#52](https://github.com/cairn-ehr/drugref/issues/52) **the
422 broadened assertions**: the row carries no `concept_ui`, so a consumer cannot detect which, and storing it is what would
make the row-grain figure queryable · [#55](https://github.com/cairn-ehr/drugref/issues/55) **`indications_for_condition`
generalises through a boolean, not a structure** — the mitigation `db/019` rejected for `induces`. Whichever option wins
revises the living record.

**Filed by the interaction debt round** — [#48](https://github.com/cairn-ehr/drugref/issues/48) **a non-expanding predicate
with no direct member is equally dead and is deliberately not reported** by `gap_dead_by_expansion_policy`; it wants its own
view. **Still unreachable — 5b.2, Plan C and #35 all left it so**: it goes live when a *class-side* predicate stops expanding.
**#59/#60/#61/#63**, filed by the expansion-policy history round, are all closed by the in-flight round above (fix-by-fix
account: ROADMAP.md; detail and traps: `PROJECT-NOTES.md` §"The policy-surface debt round").

**Closed by the four debt rounds** (#50, #39, #31, #45 · #40, #17, #42, #41, #43 · #16, #47 · **#35**), each verified against
the code before closing. **Four standing rules came out of them and outlive the issues** — grain = `gap_key` grain · one
reader/clear/checksum/supersession · one home per vocabulary · unexercisable branches pinned by mutation. Stated in full in
[`PROJECT-NOTES.md`](PROJECT-NOTES.md) § "Standing rules", where they are edited in place instead of recompressed each round.

**Floor & identity**
- [#2](https://github.com/cairn-ehr/drugref/issues/2) **Floor hardening** — close the `TRUNCATE` + owner-role bypass via
  RLS + privilege separation; blocked on a replacement test-isolation strategy (**eleven** `TRUNCATE`-ing test modules
  depend on the very bypass this closes — re-run the grep before quoting the count). Detail: ROADMAP.md's own "Floor
  hardening" bullet.
- [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality** — structural re-key by InChIKey, deferred.
  **#17 is CLOSED**; its third part is carried under "Verify-before-production" below.

**Ingest correctness (all found by measuring the real releases)**
- [#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms**, which cannot reach a moiety
  held as UNII's unspecified form. Counted, not dropped. **Closed by slice 3**, as is
  [#30](https://github.com/cairn-ehr/drugref/issues/30) (`strip_salt` drops only one trailing token).
- [#5](https://github.com/cairn-ehr/drugref/issues/5) INN sourced from UNII's `Display Name`, not an authoritative WHO list.
  `UNII_Names_*.txt`'s `TYPE='of'` rows (24,127 UNIIs) may yield one — but `of` also covers excipients: a *name* source.
- [#7](https://github.com/cairn-ehr/drugref/issues/7) / [#29](https://github.com/cairn-ehr/drugref/issues/29) **Row-at-a-time
  ingest** — MED-RT (~31k round trips, `ElementTree.parse` holding 45 MB) and PBS (~28k); the MeSH-keyed run writes
  40,211 rows, the slowest leg of the ~103 s ingest.

**Interaction model**
- [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is unpopulated** — filed as 41 of 739;
  `gap_unpopulated_contraindication` returns **13**, Plan B's expansion having absorbed the rest, so **re-measure before
  acting on the issue text.** An **indexing loss, not a knowledge gap**: openFDA labels carry the statements, which is why
  the cost ladder puts `openFDA-SPL` above `literature`.
- [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions** — **Plan C's `interaction_group` is the shape
  that expresses it**, so this is now about the consumer contract rather than the schema ·
  [#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` assertions unused** (~756 edges), the other half
  of making the DAG carry knowledge.
- [#36](https://github.com/cairn-ehr/drugref/issues/36) **discovery counts descendant classes, not reachable members**, so a
  curator `allow` can be spent on a provable no-op · [#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is
  expanded unprunably on every query** — restricting the *root set* is safe, restricting the *walk* deletes the coagulation
  case; **`db/018`'s ancestor-walk function is the shape that fixes it.** Not urgent at **3.1 ms**.

**Before the first production load** — re-run every parser against a full current release, the `add_claim` canonicalisation
check inherited from #17, and the two rule-6 licence deeds (#6 MED-RT, #25 PBS): stated in full in
[`PROJECT-NOTES.md`](PROJECT-NOTES.md) § "Verify before the first production load".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
  Set it as `DRUGREF_TEST_DSN` for the DB-gated tests. The verification databases are listed in `PROJECT-NOTES.md` § Repo facts.

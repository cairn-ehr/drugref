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
· the ingest-operability round (#58) · the expansion-policy history round (#35, PR #62) · **the policy-surface debt round
(PR [#64](https://github.com/cairn-ehr/drugref/pull/64), which closed #59, #60, #61 and #63)**. `main` is green at
**844 tests**.

**In flight: slice 3 — the composition tree**, on `feat/slice-3-composition-tree`. **DESIGNED AND MEASURED, NOT YET
BUILT.** Spec: [slice-3 composition
tree](superpowers/specs/2026-08-05-drugref-slice-3-composition-tree-design.md). Traps and full state:
[`PROJECT-NOTES.md`](PROJECT-NOTES.md) § "Slice 3".

**⇒ The next action is to write the implementation plan and build it** (TDD, `db/028`). Nothing is implemented yet;
the branch holds the spec and the three working docs only.

**The rule-6 gate cleared BEFORE anything was downloaded**: GSRS data is **CC0 1.0**, software **Apache-2.0** — both
AGPL-3.0-compatible, and CC0 imposes no attribution or share-alike at all. `NOTICE` gains a bundled-source entry when the
code lands. The dedication's *"unless otherwise noted"* clause is a per-record exception and joins #6/#25 on the
verify-before-production list.

**Four things ROADMAP asserted about this slice were refuted by the release** (detail: ROADMAP § Slice 3):
`ACTIVE MOIETY` is the **ion** level, not the composition edge (71% self-edges; as an equivalence join it merges
**levomefolate magnesium** with magnesium sulfate) · **`parent_moiety_uuid` cannot hold the data** — 1,089 salts (7.7%)
have >1 parent · the **direction convention is inverted** from the naive reading, the MED-RT `Parent Of` erratum again ·
**salt↔base strength equivalence is not in this source** (409 assay specs), now issue
[#67](https://github.com/cairn-ehr/drugref/issues/67).

**It does not close #33 or #30 — those annotations are WITHDRAWN in ROADMAP.** Nothing in GSRS points at `DE08037SAB`
(0 inbound references across 173,080 records); a composition hop recovers **94 of 706** MeSH UNII keys and **68 of
1,977** CAS keys, and the magnesium flagship is not among them. Issue 33 now carries the measurement as a comment.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61; full account:
PROJECT-NOTES.md). **Standing rule:** near `close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#`
("issue 65"). PR #64's header said "Also closes the CLI half of #61" and linked it anyway — the linker binds a keyword to
the next `#N` in the phrase, and intervening words do not save you.

**⇒ After slice 3:**

- **Slice 5c — the curated, signed overlay.** A projection may not invent a line of therapy, evidence strength or drug
  ordering; 5c is where a human adds those. **Plan C built the MACHINERY**; #35 exercised it on a fifth table. Owns
  #51/#52/#55, and now **#67** (strength equivalence).
- **Step 8 — curation itself**, driven by Plan C's worklist (**381** effects awaiting a ruling), bound by §12-H: audit every
  file and predicate of a source before curating a gap it may already cover. **#36** blocks nothing but shapes it — the
  discovery heuristic counts descendant classes, not reachable members; needs a curator ruling and its own re-measure.

## Open follow-ups (all filed as GitHub issues)

**Filed by the slice-3 design** — [#67](https://github.com/cairn-ehr/drugref/issues/67) **salt↔base strength equivalence
has no source**: `BASIS OF STRENGTH` is 409 *assay* specs (`99–101 WEIGHT PERCENT`), not conversion factors, and MW covers
5.4% of records — deriving a dose factor would have a projection compute a clinical quantity. Routed to 5c. ·
[#68](https://github.com/cairn-ehr/drugref/issues/68) **3,631 drugref moieties carry a GSRS `ACTIVE MOIETY` edge to
something else**, i.e. ~19% of the registry sits at the wrong level of the composition tree. Cannot be repaired
(`moiety_uuid` is immortal, the gate strictly monotone) and slice 3 is built so it need not be; it is the gate question
under #26's lineage, and it is why #33 stays open — GSRS holds `MAGNESIUM CATION` as the moiety and drugref does not.

**Filed by the policy-surface round** — [#65](https://github.com/cairn-ehr/drugref/issues/65) **no index serves a HISTORY
query** on `class_expansion_policy`: `db/027`'s only one is partial on the live rows, and history is the complementary
population. **Deliberately unfixed** at 14 rows on an interactive command; revisit at curation, weighing a second index's
write cost on a bulk-load path. · [#66](https://github.com/cairn-ehr/drugref/issues/66) **no enforced line length**:
`pyproject.toml` has no `[tool.ruff]`, so **`E501` is not in the default rule set** — `ruff check` passes a 115-column
line, and 52 lines exceed the ~88 every file is written to. A decision (one number, and whether `tests/fixtures/` is
exempt).

**Filed by slice 5b.2 and its review (all three for 5c)** — [#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168
pairs both indicated and contraindicated**, counted not resolved · [#52](https://github.com/cairn-ehr/drugref/issues/52) **the
422 broadened assertions**: the row carries no `concept_ui`, so a consumer cannot detect which, and storing it is what would
make the row-grain figure queryable · [#55](https://github.com/cairn-ehr/drugref/issues/55) **`indications_for_condition`
generalises through a boolean, not a structure** — the mitigation `db/019` rejected for `induces`.

**Filed by the interaction debt round** — [#48](https://github.com/cairn-ehr/drugref/issues/48) **a non-expanding predicate
with no direct member is equally dead and is deliberately not reported** by `gap_dead_by_expansion_policy`; it wants its own
view. **Still unreachable — 5b.2, Plan C and #35 all left it so**: it goes live when a *class-side* predicate stops expanding.

**Closed by the five debt rounds** (#50, #39, #31, #45 · #40, #17, #42, #41, #43 · #16, #47 · #35 · **#59, #60, #61, #63**),
each verified against the code before closing. **Four standing rules came out of them and outlive the issues** — grain =
`gap_key` grain · one reader/clear/checksum/supersession · one home per vocabulary · unexercisable branches pinned by
mutation. Stated in full in [`PROJECT-NOTES.md`](PROJECT-NOTES.md) § "Standing rules".

**Floor & identity**
- [#2](https://github.com/cairn-ehr/drugref/issues/2) **Floor hardening** — close the `TRUNCATE` + owner-role bypass via RLS
  + privilege separation; blocked on test isolation (**eleven** `TRUNCATE`-ing modules depend on that bypass — re-grep
  before quoting) · [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality**, re-key by InChIKey.

**Ingest correctness (all found by measuring the real releases)**
- [#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms** — **NOT closed by slice 3**;
  the issue's own proposed fix is refuted and the comment there carries the measurement. Blocked behind **#68**.
  [#30](https://github.com/cairn-ehr/drugref/issues/30) (`strip_salt` drops one trailing token) is **unmeasured** for
  slice 3 — the verification DB carries no PBS release, so it is an implementation-step measurement.
- [#5](https://github.com/cairn-ehr/drugref/issues/5) INN sourced from UNII's `Display Name`, not an authoritative WHO list.
- [#7](https://github.com/cairn-ehr/drugref/issues/7) / [#29](https://github.com/cairn-ehr/drugref/issues/29) **Row-at-a-time
  ingest** — MED-RT (~31k round trips) and PBS (~28k); the MeSH-keyed run writes 40,211 rows, the slowest leg of ~114 s.

**Interaction model**
- [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is unpopulated** — filed as 41 of 739;
  `gap_unpopulated_contraindication` returns **13**, so **re-measure before acting on the issue text.**
- [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions** — Plan C's `interaction_group` is the shape
  that expresses it · [#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` assertions unused** (~756 edges).
- [#36](https://github.com/cairn-ehr/drugref/issues/36) **discovery counts descendant classes, not reachable members** ·
  [#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** — restricting the
  *root set* is safe, restricting the *walk* deletes the coagulation case. Not urgent at **3.1 ms**.

**Before the first production load** — re-run every parser against a full current release, the `add_claim` canonicalisation
check inherited from #17, and **three** rule-6 deeds (#6 MED-RT, #25 PBS, and GSRS's *"unless otherwise noted"* clause):
stated in full in [`PROJECT-NOTES.md`](PROJECT-NOTES.md) § "Verify before the first production load".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
  Set it as `DRUGREF_TEST_DSN` for the DB-gated tests. The verification databases are listed in `PROJECT-NOTES.md` § Repo facts.

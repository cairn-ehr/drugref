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
post-5b debt round (#46) · the interaction debt round (#49) · 5b.2 (#54) · #53's population-label round (#56) · Plan C
(#57) · the ingest-operability round (#58) · the expansion-policy history round (#35, PR #62) · the policy-surface debt
round (PR [#64](https://github.com/cairn-ehr/drugref/pull/64)).

**In flight: slice 3 — the composition tree**, on `feat/slice-3-composition-tree`. **BUILT, AND MEASURED END TO END
AGAINST THE REAL RELEASES.** All 8 plan tasks are done; the branch is green at **897 tests**. Spec: [slice-3 composition
tree](superpowers/specs/2026-08-05-drugref-slice-3-composition-tree-design.md). Traps and full state:
[`PROJECT-NOTES.md`](PROJECT-NOTES.md) § "Slice 3".

**⇒ PR [#72](https://github.com/cairn-ehr/drugref/pull/72) is OPEN and its review round is CLOSED** — five findings
fixed on the branch (account: PROJECT-NOTES § "Slice 3"), the worst being that **collapsing "unruled" to `false` passed
all 895 tests**. New issue: #73. **⇒ Next: merge #72**, then slice 5c.

**What landed:** `db/028` (projection, relation vocabulary, `moiety_active_in_composite`, gap kind 12) · `ingest/gsrs.py`
(pure streaming parser, 2.05 GB) · `composition.py` (single writer) · `ingest/gsrs_run.py` · a `gsrs` chain step and
subcommand · `NOTICE`'s GSRS entry · the record
[GSRS relationship direction](../docs-site/docs/decisions/gsrs-relationship-direction.md).

**Measured on the assembled chain** (UNII 26Feb2026 → MED-RT 2026.07.06 → MeSH 2026 → GSRS 2026-02-26, 137 s): **8,671
rows** (7,962 salt + 709 solvate) over **7,377 composites** and **4,433 component moieties**; `is_active_component`
**TRUE 5,011 / FALSE 992 / NULL 2,668**; **gap kind 12 = 2,245** composites. **Nothing pre-existing moved:**
`ddi_candidate_pair` **21,664**, `substance_moiety` **19,438**, `open_question` 18,834 → **21,079**.

**⇒ THE PREDICTED ACTIVITY SPLIT WAS REFUTED; the row set was not.** Design predicted 5,029 / 1,001 / 2,641 and 2,226
gap-12 composites; the edge set matched, only the split moved. Cause, reproduced against the dump: predictions used a
**global** `unii → active moieties` map, while `gsrs_run.py` rules only from the composite's **own record** — they
differ on the **27** in-registry edges GSRS stores solely on the component's record (18 TRUE + 9 FALSE become NULL, 19
more composites unruled). Conservative (adds NULLs, never downgrades); left as-is deliberately during a verification
round: filed as issue 69. The **EXPECTED 2,226 / 21,060** hedging in PROJECT-NOTES is settled and removed.

**The rule-6 gate cleared BEFORE anything was downloaded**: GSRS data is **CC0 1.0**, software **Apache-2.0** — both
AGPL-3.0-compatible, and CC0 imposes no attribution or share-alike at all. `NOTICE` now carries the entry. The
dedication's *"unless otherwise noted"* clause joins #6/#25 on the verify-before-production list; no noted exception was
found on any ingested record.

**Four things ROADMAP asserted about this slice were refuted by the release** (detail: ROADMAP § Slice 3): `ACTIVE
MOIETY` is the **ion** level, not the composition edge (71% self-edges) · **`parent_moiety_uuid` cannot hold the data**
(1,089 salts have >1 parent) · the **direction convention is inverted**, the MED-RT `Parent Of` erratum again ·
**salt↔base strength equivalence is not in this source** (409 assay specs), now issue 67.

**This slice resolves neither issue 33 nor issue 30 — those ROADMAP annotations are WITHDRAWN.** Nothing in GSRS points
at `DE08037SAB` (0 inbound references across 173,080 records); a composition hop recovers **94 of 706** MeSH UNII keys
and **68 of 1,977** CAS keys, and the magnesium flagship is not among them. Issue 30 stayed unmeasured: the verification
database carries no PBS release.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61; full account:
PROJECT-NOTES.md). **Standing rule:** near `close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#`
("issue 65"). The linker binds a keyword to the next `#N` in the phrase; intervening words do not save you.

**⇒ After slice 3:** **slice 5c — the curated, signed overlay**, since a projection may not invent a line of therapy,
evidence strength or drug ordering. **Plan C built the MACHINERY**; #35 exercised it on a fifth table. Owns #51/#52/#55
and now **#67**. Then **step 8 — curation itself**, driven by Plan C's worklist (**381** effects awaiting a ruling),
bound by §12-H: audit every file and predicate of a source before curating a gap it may already cover. **#36** blocks
nothing but shapes it and needs its own re-measure.

## Open follow-ups (all filed as GitHub issues)

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
query** on `class_expansion_policy`; deliberately unfixed at 14 rows, revisit at curation ·
[#66](https://github.com/cairn-ehr/drugref/issues/66) **no enforced line length**: no `[tool.ruff]`, so **`E501` is not
in the default rule set** and 52 lines exceed the ~88 every file is written to.

**Filed by slice 5b.2 and its review (all three for 5c)** — [#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168
pairs both indicated and contraindicated**, counted not resolved · [#52](https://github.com/cairn-ehr/drugref/issues/52)
**the 422 broadened assertions**: the row carries no `concept_ui` · [#55](https://github.com/cairn-ehr/drugref/issues/55)
**`indications_for_condition` generalises through a boolean, not a structure**.

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
**row-at-a-time ingest**, MED-RT ~31k round trips and PBS ~28k; the MeSH-keyed leg is the slowest of ~137 s.

**Interaction model** — [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is
unpopulated**, filed as 41 of 739 but `gap_unpopulated_contraindication` returns **13**, so **re-measure before acting on
the issue text** · [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions**, Plan C's
`interaction_group` is the shape · [#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` assertions
unused** · [#36](https://github.com/cairn-ehr/drugref/issues/36) **discovery counts descendant classes, not reachable
members** · [#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** —
restricting the *root set* is safe, restricting the *walk* deletes the coagulation case. Not urgent at **3.1 ms**.

**Before the first production load** — every parser re-run against a current release, the `add_claim` canonicalisation
check from #17, and **three** rule-6 deeds (#6, #25, GSRS's *"unless otherwise noted"*): see PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
  Set it as `DRUGREF_TEST_DSN` for the DB-gated tests. The verification databases are listed in `PROJECT-NOTES.md` § Repo facts.

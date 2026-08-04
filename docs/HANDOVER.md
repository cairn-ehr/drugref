# HANDOVER — drugref

> **The volatile half: where we are right now.** Regenerated at the end of every working session
> (nextsession rule 9) and kept **under ~120 lines**, so a rewrite costs nothing.
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
(#57) · the ingest-operability round (#58, closing #16 and #47).

**In flight: the expansion-policy history round (#35)** on `fix/expansion-policy-history` — `db/027`, implemented, measured and
pushed; **PR [#62](https://github.com/cairn-ehr/drugref/pull/62) is open and reviewed** (its findings are folded in below, and two
became #61/#63). **810 tests green**, `ruff check src tests` + `mkdocs build --strict` clean, re-measured against the real releases
on a fresh `drugref_policy` (**103.28 s**): `ddi_candidate_pair` **21,664** unchanged, filtered lookup **2.876 ms** against the
recorded 3.1 ms. Traps below. Errata live in `docs-site/docs/decisions/` — one per MeSH-keyed slice, plus Plan C's and this round's.

**⇒ Issue-tracker hygiene — the sweep-closed-but-unfixed pattern has happened three times** (#31, #35, #40), each time because
a commit or PR body saying *filed, not fixed* still named the number. The tracker is true today. **A number in a commit
message is a claim about the code — verify it**; when filing rather than fixing, use prose no closing keyword can be parsed
out of (#59–#61 and #63 are written that way).

**⇒ Next candidates:**

- **Slice 5c — the curated, signed overlay.** A projection may not invent a line of therapy, a strength of evidence or an
  ordering among the drugs that treat one condition; 5c is where a human adds those. **Plan C built the overlay MACHINERY**
  and #35 has now exercised it on a fifth table. Owns #51, #52, #55.
- **Slice 3 — composition tree (salts/esters/hydrates via GSRS).** Triply motivated: the salt-strip heuristic is down to
  **0.03%** of bridge rows, #33 needs form→moiety relationships, and #30 waits on the same thing. **The UNII release carries
  no parent-moiety column** (checked: 25 columns, none a relationship), so this needs the GSRS full export — **a new source,
  so the rule-6 licence check runs BEFORE anything is downloaded.**
- **Step 8 — curation itself**, driven by the worklist Plan C published (**381** effects awaiting a ruling), bound by §12-H:
  audit every file and predicate of a source before curating a gap it may already cover.
- **#36** — the discovery heuristic counts descendant classes rather than reachable members. Same table #35 just moved,
  deliberately left alone by it: the metric moves which roots get asked about, so it needs a curator ruling and its own
  re-measure.

## Open follow-ups (all filed as GitHub issues)

**Filed by slice 5b.2 and its review (all three for 5c)** — [#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168
pairs both indicated and contraindicated**, counted not resolved · [#52](https://github.com/cairn-ehr/drugref/issues/52) **the
422 broadened assertions**: the row carries no `concept_ui`, so a consumer cannot detect which, and storing it is what would
make the row-grain figure queryable · [#55](https://github.com/cairn-ehr/drugref/issues/55) **`indications_for_condition`
generalises through a boolean, not a structure** — the mitigation `db/019` rejected for `induces`. Whichever option wins
revises the living record.

**Filed by the interaction debt round** — [#48](https://github.com/cairn-ehr/drugref/issues/48) **a non-expanding predicate
with no direct member is equally dead and is deliberately not reported** by `gap_dead_by_expansion_policy`; it wants its own
view. **Still unreachable — 5b.2, Plan C and #35 all left it so**: it goes live when a *class-side* predicate stops expanding.

**Filed by the expansion-policy history round, both deliberately NOT fixed in it** —
[#59](https://github.com/cairn-ehr/drugref/issues/59) the insert-then-supersede rule lives in **three** places
(`accumulation._supersede`, `questions.set_state` since `db/007`, `interactions.record_expansion_decision`), so the "promote it
when a third owner appears" trigger has already fired — and `_supersede` is already generic over table and pk, so reuse is an
import, not a refactor. Deferred anyway, for the honest reason: this round chose not to widen its blast radius into two other
modules · [#60](https://github.com/cairn-ehr/drugref/issues/60) **`drugref ingest chain` cannot run all four sources together on
merged `main`** — `mesh` and `mesh-relations` share `desc*.gz`/`supp*.gz` but take different release tags, so
`check_release_agreement` refuses the documented invocation. The guard (`98b346f`) landed AFTER the measurement (`b56d26e`)
inside #58, so the command was never re-run against it; the remedy is a design decision, because `mesh_rel_run` genuinely reads
two authorities and one tag per step cannot say so. **Use the four `ingest <source>` subcommands in `STEPS` order meanwhile —
that is what #35's measurement did.**

**Closed by the four debt rounds** (#50, #39, #31, #45 · #40, #17, #42, #41, #43 · #16, #47 · **#35**), each verified against
the code before closing. **Three standing rules came out of them and outlive the issues:**
- **THE VIEW'S GRAIN MUST BE THE `gap_key`'S GRAIN** (#41) — a gap view that groups more coarsely than its key folds two gaps
  onto one immortal `question_uuid`. Pinned per kind, Plan C's two compound-key views included.
- **One reader, one clear, one checksum** (#40, #43): `mesh.iter_records`, `db.clear_source_tables` and `ingest/checksum.py`
  each live in one place, and every writer's table tuple is **restated independently** in
  `tests/test_source_clear_contract.py` so a dropped table fails.
- **A branch the release cannot exercise is pinned on controlled input and verified by mutation** (#42): desc2026 and supp2026
  share **0** ConceptUIs. **#53's `is_cap_exempt`, #47's named-row tie-break and ALL of #35's new behaviour** — no
  release-derived database holds a superseded or withdrawn row — are the same shape.

**Floor & identity**
- [#2](https://github.com/cairn-ehr/drugref/issues/2) **Floor hardening** — close the `TRUNCATE` + owner-role bypass via RLS +
  privilege separation. **Note the test-suite coupling** (re-run the grep before quoting the count): `grep -l TRUNCATE
  tests/*.py` finds **eleven** modules, one the shared helper `tests/mesh_rel_fixtures.py`, each truncating in an autouse
  fixture because their orchestrators commit internally and escape the `conn` fixture's rollback — so hardening needs a
  replacement isolation strategy.
- [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality** — structural re-key by InChIKey, deferred.
  **#17 is CLOSED**; its third part is carried under "Verify-before-production" below.

**Ingest correctness (all found by measuring the real releases)**
- [#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms**, which cannot reach a moiety
  held as UNII's unspecified form. Counted, not dropped. **Closed by slice 3**, as is
  [#30](https://github.com/cairn-ehr/drugref/issues/30) (`strip_salt` drops only one trailing token).
- [#5](https://github.com/cairn-ehr/drugref/issues/5) INN sourced from UNII's `Display Name`, not an authoritative WHO list.
  `UNII_Names_*.txt`'s `TYPE='of'` rows (24,127 UNIIs) may yield one — but `of` also covers excipients: a *name* source, not a
  membership signal.
- [#7](https://github.com/cairn-ehr/drugref/issues/7) / [#29](https://github.com/cairn-ehr/drugref/issues/29) **Row-at-a-time
  ingest** — MED-RT (~31k round trips, plus `ElementTree.parse` holding 45 MB) and PBS (~28k). The MeSH-keyed run writes
  40,211 rows and is the slowest leg of the ~103 s ingest.

**Interaction model**
- [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is unpopulated** — filed as 41 of 739;
  `gap_unpopulated_contraindication` returns **13**, Plan B's expansion having absorbed the rest. **Re-measure before acting
  on the issue text.** Largely an **indexing loss, not a knowledge gap**: openFDA labels carry the statements, which is why
  the cost ladder puts `openFDA-SPL` above `literature`.
- [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions** — **Plan C's `interaction_group` is the shape
  that expresses it**, so this is now about the consumer contract rather than the schema ·
  [#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` assertions unused** (~756 edges), the other half
  of making the DAG carry knowledge.
- [#36](https://github.com/cairn-ehr/drugref/issues/36) **The discovery heuristic counts descendant classes, not reachable
  members**, so a curator `allow` can be spent on a provable no-op; changing the metric needs a curator and a re-measure.
  [#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** — the trap is that
  restricting the *root set* is safe while restricting the *walk* deletes the coagulation case; **`db/018`'s ancestor-walk
  function is the shape that fixes it.** Not urgent at **3.1 ms**.

**Licence deeds (blockers before production, per rule 6)** — [#6](https://github.com/cairn-ehr/drugref/issues/6) re-confirm
the MED-RT deed against the live NLM source-release doc (the distribution ships no licence file) ·
[#25](https://github.com/cairn-ehr/drugref/issues/25) PBS redistribution, which blocks bundling but not node-local ingest and
needs written Dept-of-Health confirmation.

**Verify-before-production, generally:** re-run every parser against a full current release (see "How to run / test" — and
#60) and re-confirm the aggregate numbers; fixtures from a real release are not the same thing, and 5b found five spec errors
that way, each invisible to a green suite. **Plus one data check, inherited from #17:** `claims.add_claim` canonicalises
case-bearing claim values (UNII / INCHIKEY / CHEBI), so a database populated *before* that change could hold a spelling no
lookup matches — and such rows cannot be deleted. Confirm BEFORE the first real load.

## Current DSN

- Current dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.

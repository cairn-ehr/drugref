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

**Branch `codex/drugcentral-remeasurement`, from `main` at `c10b97b`** (PR #145 merged 2026-08-20; #86 CLOSED).
Migrations through **`db/048`** are frozen and **this round adds none**. Suite total lives in
PROJECT-NOTES § "How to run / test" and nowhere else (issue 146); this branch adds 94 tests.

**⇒ JUST FINISHED — the DrugCentral re-measurement, and it changes the slice that follows.** Results:
[`drugcentral-ddi-remeasurement-results`](superpowers/specs/2026-08-23-drugref-drugcentral-ddi-remeasurement-results.md);
full account in PROJECT-NOTES § "The DrugCentral re-measurement". No SQL, no source admitted, no ingest.
Issue [#101](https://github.com/cairn-ehr/drugref/issues/101)'s figures rested on one unrepeated 2026-08-13 run
over a 1.4 GB dump that was then deleted. The dump is re-fetched (SHA-256 recorded in PROJECT-NOTES), the
measurement lives in `tools/` as six modules with **94 tests**, and it re-runs in ~25 s from a
cached extract.

**Rule 6 reproduces EXACTLY, re-read from the `reference` table rather than inferred:** `ddi_ref_id = 2` is the
VHA's NDF-RT (7,571 rows, clean, US federal); `1` is Stockley's (copyrighted book) and `3` is Lexicomp
(commercial) — both **out**. Licence re-confirmed at source as CC BY-SA 4.0. Staleness stands: the download page
still offers only 11/01/2023.

**⇒ THE FINDING: resolve endpoints on STRUCTURE, not on spelling.** DrugCentral resolves its own free text to a
`struct_id` for 918 of 924 NDF-RT endpoint names, and `structures` carries an InChIKey and a CAS number drugref
already holds as `identity_claim` rows (16,046 / 19,010). A `display_name → inchikey → cas` cascade takes
resolution from 857/924 names to **914**, unresolvable rows from 598 to **37**, and **NEW pairs from 6,337 to
6,866** — with **no synonym bridge**, which is exactly what #101 had budgeted for. CAS is last on purpose; a
blank structural key is never looked up (a registry holding `""` would collapse every keyless substance onto one
moiety — that guard has a test).

**Seven published figures were wrong and are corrected in place** in PROJECT-NOTES and ROADMAP: "8 MED-RT class
names" is **4, and they are MeSH**; 102 → **106**; 7,000 keyable → **6,991**; `MAOIs or RIMAs` **is not in the
table** (the only `MAOI` hit is `clotrimazole`); the QT strings are `High Risk QT Prolonging Agents` /
`Moderate Risk QT Prolonging Agents`; **all three QT rows are `ddi_ref_id = 3`**, so issue #93's content sits
in the half rule 6 forbids; and the endpoint provenance split is **905 / 13 / 6**, not 905 / 17 / 2 — the
`structures` half reproduced exactly and the synonym half did not. Everything on the moiety side — 7,621 / 970 /
860 / 6,973 / 6,941 / 604 / 6,337 — reproduced to the row.

**⇒ THEN: the code review of this branch, and it found real defects in the instrument.** All fixed on the same
branch; the results file was regenerated end to end, not edited. The five that mattered: the TSV cache was
committed by `ddi.tsv` merely existing, so a crashed extract left a truncated cache the next run measured, and a
warm cache plus a new `--dump` printed the new SHA-256 over the old figures — there is now a manifest, written
last and validated first, and `extract` builds into a sibling directory and renames. `csv.DictWriter` wrote a
blank column for any projection the dump did not declare (so a renamed `structures.inchikey` would have silently
reduced the cascade to name matching and reproduced #101's own numbers); `csv.DictReader` padded short rows with
`None`; the three registry lookups had **no `ORDER BY`**, and **14 InChIKeys and 29 CAS numbers are claimed by
more than one moiety**, so "pairs that are NEW" could differ between two runs over the same bytes; and the rule-6
verdict was a second hard-coded `ref_id == "2"` in the renderer, unconnected to the set that filtered the rows.
`measure`, `class_coverage`, the cache and the renderer now have tests — 60 of the 94.

**Five figures the docs called "re-derivable" were not computed by the tool, and now are**: the class residue and
its authority (`4`, MeSH), 106, 6,991 keyable, 6,973 moiety × moiety, and the endpoint provenance split. The
report prints keyability under **both** resolvers, because #101's 7,000 was a name-matching figure and quoting it
beside a cascade number compares two different questions.

**⇒ DO THIS NEXT: design and build the DRUGCENTRAL ingest itself (#101). It has not been started.** Brainstorm
before designing — the measurement moved the target (6,866 new pairs, structural resolution, no synonym list) and
the open questions are now design questions, listed below. Admitting the source is **not** a one-line change:
two CHECKs widened (`ingest_run_source`, `class_contraindication_source`) **and** an explicit
`ids._SOURCE_CANONICAL` entry, **in the same migration** — `ids.py` warns by name against leaning on the
upper-case fall-through, and the failure mode is a per-source rebuild that silently deletes nothing. Follow
`db/031` (ONCHIGH) and `db/039` (FDA-CYP): copy the live catalog's CHECK **verbatim** before adding one value.
Do not bundle release-manifest signing, class-grain #98, or public installer distribution.

**The design questions the measurement leaves open** — none of them answerable from the dump alone:
1. **Severity.** The bundleable subset uses exactly two labels, `Significant` (5,264) and `Critical` (2,307).
   They must map onto drugref's own `severity_kind`, and `ddi_risk` is reference-scoped so the mapping cannot be
   inherited from the other four labels.
2. **Grain.** 7,501 pairs are moiety × moiety — the grain the moiety rule already handles — so this is *not* a
   second class-grain problem. But it is drug-level assertion content arriving beside MED-RT's class-level
   rules, and #97/#106 already ask what happens when two sources grade one pair differently.
3. **Projection or overlay.** `ddi` is a rebuildable projection by every existing precedent, but the 2023 pin
   means it never refreshes; decide deliberately rather than by default.
4. **The 10 residual names** (`Vitamin E`, `atracurium`, `mivacurium`, `doxacurium`, `sodium polystyrene
   sulfonate`, …) are biologics and mixtures with no single structure — the composition tree's problem, not the
   ingest's. Decide whether they raise questions or are simply dropped with a count.

## Parallel project sequencing

DrugCentral remains the next **content** slice and is now measured rather than estimated. Pregnancy/lactation and
FDA toxicity remain ready but separately gated as ROADMAP states.

Do not lose 5c.2g's two broad lessons — **this round is a third instance of both**: a comment claiming a guard
exists is not evidence the code contains the guard; and a plausible figure from a partially working parser is not
a measurement. Six figures decayed here precisely because nobody could re-derive them.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). New this
round: **[#146](https://github.com/cairn-ehr/drugref/issues/146)** — the suite-count line in PROJECT-NOTES has
now drifted **six** times and is guarded by prose only; it wants a test that reads the stated number and counts
the collected suite. For the next rounds: #128/#129 and #132–#135 are FDA-CYP residue; #112/#105 wait on
class-grain content; #124 is the guard-round tail; #121/#123 remain open; #104 makes question counts depend on
which unrelated ingest ran last; #94's seven withheld entries need research. Before production: re-run every
parser on current releases, resolve #17, and complete the three outstanding rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use this database for persistent reviewer accounts or GUI service data because pytest recreates it.
- **`drugref_dc101`** is this round's reference database — `db/048` plus the full documented `ingest chain`
  (built in 131.9 s), and what every DrugCentral resolution figure was joined against. Keep it or rebuild it;
  the spike needs *a* release-carrying database, not that one specifically.
- The verification database and its exact migration state live once in PROJECT-NOTES § "How to run / test";
  do not copy that volatile map here.

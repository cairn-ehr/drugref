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

**Merged to `main`** (ROADMAP orders them, full list there): everything through **slice 5c.2 — the ONC floor** (PR
[#95](https://github.com/cairn-ehr/drugref/pull/95)). **`db/029`–`db/034` are FROZEN** — corrections need a `db/NNN`.

**⇒ JUST FINISHED — the two things the last handover ordered first, both done. No migration, no code change;
suite 1409 green, `ruff` clean. (1) Issue 91 is fixed by rebuild: the reference database is now
`drugref_db034`** — 2026-08-13, real releases,
merged migrations, **clean ledger (34 rows)**, named for its migration head so the claim is checkable in one query;
`TEMPLATE` + `drugref migrate` re-tested and works. **`drugref_5c4` is kept as a control and is no longer the one
to read** (only it holds a signed-overlay measurement; it has no `db/031`–`034` objects). Every count § "Slice
5c.1" quotes reproduced exactly, and 5c.2's did too. **Two recorded numbers are now explained, not merely
re-quoted**: the worklist's `593 → 591` is `595 → 593` from a clean baseline (same net −2), and the hot path's
`actual 1233` is **1235**, +2 for `Proton Pump Inhibitor [EPC]`, the one ONCHIGH object class MED-RT lacks.
**PROJECT-NOTES § "The reference-database rebuild" — read it before quoting a count.**

**(2) The two licence-clean sources are measured, and one hope died. OnSIDES's DATA is not a DDI source** — no
second-drug column in any of its eight tables, **one** interaction-flavoured MedDRA term across 6,928,666 rows;
its *method* stays the precedent 5c.3 is named for, and **the material is SPL section `34073-7`, which OnSIDES
does not parse**. **DrugCentral carries a real DDI table and clears rule 6 for the part that matters**: 7,621
pairs, **7,000 (91.9%) keyable against drugref today**; **7,571 come from the VHA's NDF-RT** (US federal,
MED-RT's predecessor) — the other 50 cite **Stockley's** (a copyrighted book) and **Lexicomp**, are **OUT**, and
are the same 50 whose endpoints do not resolve. **Nor is it a restatement of MED-RT: 6,337 of 6,941 resolvable
pairs are NEW (8.7% overlap).** It does **not** close the QT gap. **PROJECT-NOTES § "The 5c.3 source
evaluation"**, ROADMAP § 5c.3.

**⇒ THE ONE THING FROM 5c.2 THAT GOVERNS FUTURE WORK: a class-grain rule inherits its population from the source's
class boundary, and that is only safe when the class was defined by the same mechanism the interaction runs on.**
`CYP3A4 Inhibitors [MoA]` *is* the population an irinotecan interaction runs over; `Opioid Agonist [EPC]` is
**not** — it includes **loperamide**, as `CNS Stimulant [EPC]` sweeps in **caffeine**. **4 of 15 shipped, 7
withheld from an append-only table** ([#94](https://github.com/cairn-ehr/drugref/issues/94), encodings `389a560`).

**⇒ DO THESE NEXT, in this order. (1) `db/035` — the class-grain detectors, as ONE migration** (#90, #96–#99
below): the class grain has 5c.1's write path and **none** of the moiety grain's detectors, so a class rule can
be ingested, graded and reported successful while reaching zero patients, with `drugref status` printing health.
Piecemeal, each reads as a reasonable follow-up — that is exactly how the group was created.
**(2) Then the next content slice, and the evaluation says the cheap one is DrugCentral, not SPL** — 6,337 new
public-domain moiety-grained pairs whose hard part is name resolution, against 5c.3's NLP pipeline. Either way it
opens with its own design round; [#101](https://github.com/cairn-ehr/drugref/issues/101) holds the DrugCentral
shape and its two rules (`ddi_ref_id = 2` only; the 2023-11-01 dump does not refresh). **`5c.3` — SPL/DailyMed
mining** must answer two things measured this session: section 7 qualifies by **potency band**, which MED-RT's one
undifferentiated class **cannot express** ([#102](https://github.com/cairn-ehr/drugref/issues/102)), and its
corpus must be filtered by **document type** (14/23 prescription labels carry section 7, 0/17 OTC). **No open
redistributable QT list exists** — not FDA, EMA, BfArM, not DrugCentral
([#93](https://github.com/cairn-ehr/drugref/issues/93)); routes: PROJECT-NOTES.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61). **Standing rule:** near
`close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#` ("issue 65"). Full account: PROJECT-NOTES.

## Open follow-ups (all filed as GitHub issues)

**Filed by slice 5c.2 and its REVIEW** (detail in PROJECT-NOTES § "Slice 5c.2"). **#90 and #96–#99 are ONE SHAPE — the class
grain has the write path but none of the moiety grain's DETECTORS; take them as one `db/035`** —
[#90](https://github.com/cairn-ehr/drugref/issues/90) `curated_target_unresolved` misses it ·
[#96](https://github.com/cairn-ehr/drugref/issues/96) no worklist gap kind, so an ungraded rule **asks nobody to grade it** ·
[#97](https://github.com/cairn-ehr/drugref/issues/97) both grains can grade one pair with **different severities**, no
precedence · [#98](https://github.com/cairn-ehr/drugref/issues/98) a **signed release silently omits the grain**,
`verify_release` still passes · [#99](https://github.com/cairn-ehr/drugref/issues/99) class roots evade
`gap_unreviewed_expansion_root`. **The rest** — [#92](https://github.com/cairn-ehr/drugref/issues/92) **a mixed-kind class-pair
rule expands to ZERO pairs silently** (db/032's preamble advertises `statins × CYP3A4`, exactly such a pair) ·
[#93](https://github.com/cairn-ehr/drugref/issues/93) **MED-RT carries no QT class** ·
[#94](https://github.com/cairn-ehr/drugref/issues/94) **the seven withheld entries** need research ·
[#100](https://github.com/cairn-ehr/drugref/issues/100) replaying `db/033` ALONE reinstates the 3.6× regression.
**Filed by this session's source evaluation** — [#101](https://github.com/cairn-ehr/drugref/issues/101) the DrugCentral
ingest, with its measurements · [#102](https://github.com/cairn-ehr/drugref/issues/102) the potency band.
**Filed by 5c.4 and its review** — [#85](https://github.com/cairn-ehr/drugref/issues/85) `signing_key_status_kind` has **no
append-only floor**, so one `UPDATE` disarms every compromise verdict; **floor that one ALONE** — `signature_target_kind` is
*designed* to move to a `/v2` · [#86](https://github.com/cairn-ehr/drugref/issues/86) ·
[#87](https://github.com/cairn-ehr/drugref/issues/87) · [#88](https://github.com/cairn-ehr/drugref/issues/88) ·
[#89](https://github.com/cairn-ehr/drugref/issues/89) rule-4 breach. Unfiled: `tests/test_cli_signing*.py` **cannot commit for
real** — test isolation, shaped like [#2](https://github.com/cairn-ehr/drugref/issues/2). **Earlier rounds** —
[#79](https://github.com/cairn-ehr/drugref/issues/79) **`tests/` is exempt from E501** (its title's 324 has drifted —
re-measure, never quote; **debt, not policy**) · [#81](https://github.com/cairn-ehr/drugref/issues/81) chain-time variance
(**148.6 s this session, uncontrolled**) · [#82](https://github.com/cairn-ehr/drugref/issues/82) **`status` reports orphans to
humans only** · [#75](https://github.com/cairn-ehr/drugref/issues/75) **`gap_uncurated_interaction_rule` costs ~2.7 s** ·
[#65](https://github.com/cairn-ehr/drugref/issues/65) no index serves a `class_expansion_policy` HISTORY query.

**Owned by 5c, still open** — [#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168 contradicted pairs**, which now
also **own the `spurious` deferral 5c.1 wrongly handed to 5c.2** (`spurious` is a `curated_condition` ruling; 5c.2 curated the
*interaction* half — ROADMAP § 5c.2) · [#52](https://github.com/cairn-ehr/drugref/issues/52) **the 422 broadened assertions**:
no `concept_ui` on the row · [#55](https://github.com/cairn-ehr/drugref/issues/55) **`indications_for_condition` generalises
through a boolean** · [#67](https://github.com/cairn-ehr/drugref/issues/67) **salt↔base strength equivalence has no source** ·
[#73](https://github.com/cairn-ehr/drugref/issues/73) **both views read every source at once** — for `ddi_candidate_pair` that
is now *wanted*, so **re-read it against 5c.2** · [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions**
— 5c.2 stayed pairwise. **From the slice-3 design** — [#68](https://github.com/cairn-ehr/drugref/issues/68) **3,631 moieties
carry a GSRS `ACTIVE MOIETY` edge to something else** (~19%; why 5c.2 expands salt forms on the projection side, not at read
time) · [#69](https://github.com/cairn-ehr/drugref/issues/69) the 27-edge scope question ·
[#70](https://github.com/cairn-ehr/drugref/issues/70) **354 all-false composites reachable by nothing** ·
[#71](https://github.com/cairn-ehr/drugref/issues/71) **8,163 edges dropped, transiently counted** — re-learned in 5c.2.

**Interaction model and identity** — [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is
unpopulated**, filed as 41 of 739 but the gap view returns **13**, so **re-measure before acting** ·
[#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` unused** ·
[#36](https://github.com/cairn-ehr/drugref/issues/36) **discovery counts descendant classes, not reachable members** ·
[#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** — restricting the *root
set* is safe, restricting the *walk* deletes the coagulation case · [#48](https://github.com/cairn-ehr/drugref/issues/48) ·
[#2](https://github.com/cairn-ehr/drugref/issues/2) **floor hardening** (`TRUNCATE` + owner-role bypass; **13** `TRUNCATE`-ing
modules depend on it — re-grep before quoting) · [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change
immortality** · [#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms**, blocked behind
#68 · [#30](https://github.com/cairn-ehr/drugref/issues/30) (`strip_salt`) unmeasured · [#5](https://github.com/cairn-ehr/drugref/issues/5) INN from UNII's `Display Name` ·
[#7](https://github.com/cairn-ehr/drugref/issues/7)/[#29](https://github.com/cairn-ehr/drugref/issues/29) **row-at-a-time
ingest** — the two MeSH legs are 75.7% of a 133 s chain. **Before the first production load**: every parser re-run against a
current release, #17's `add_claim` canonicalisation check, **three** rule-6 deeds (#6, #25, GSRS) — PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
  Set it as `DRUGREF_TEST_DSN` for the DB-gated tests. **Which verification database to read is stated once in
  `PROJECT-NOTES.md`, in the "Dev DSN" bullet ending § How to run / test** — read **`drugref_db034`**, whose ledger
  is clean, so a `TEMPLATE` copy takes `drugref migrate` normally. Not restated here: this file is compressed every
  session, so a second copy would outlive the first and disagree with it.

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

**Merged to `main`** (ROADMAP orders them, full list there): everything through **slice 5c.4 — signing** (PR
[#84](https://github.com/cairn-ehr/drugref/pull/84)). **`db/029`/`db/030` are MERGED and FROZEN** — corrections need a `db/NNN`.

**⇒ JUST FINISHED — SLICE `5c.2`, THE ONC FLOOR (`db/031`–`db/034`), branch `feat/slice-5c2-onc-floor`. Suite 1297 → 1409
green, `ruff` clean.** drugref's **first clinical content**. Measured: 8 ONCHIGH candidates · 213 pairs · 0 unresolved · hot
path 1.55–1.68 ms · **`ddi_candidate_pair` MED-RT 21,664 and `substance_moiety` 19,438 both unmoved**. **Every figure, every
trap and the two reversals that produced them: PROJECT-NOTES § "Slice 5c.2" — read it before touching this area.**

**⇒ THE ONE THING FROM 5c.2 THAT GOVERNS FUTURE WORK, repeated here rather than left in PROJECT-NOTES: a class-grain rule
inherits its population from the source's class boundary, and that is only safe when the class was defined by the same
mechanism the interaction runs on.** `CYP3A4 Inhibitors [MoA]` *is* the population an irinotecan interaction runs over;
`Opioid Agonist [EPC]` is **not** — it conflates serotonergic with opioid-action amplification and includes **loperamide**,
and `CNS Stimulant [EPC]` sweeps in **caffeine**. **4 of 15 shipped, 7 withheld from an append-only table**
([#94](https://github.com/cairn-ehr/drugref/issues/94), encodings in `389a560`). **A class-grain rule is not automatically
cheaper than a moiety one.**

**⇒ THIS BRANCH IS DONE — PR [#95](https://github.com/cairn-ehr/drugref/pull/95), reviewed, every finding fixed or filed.** Two
were load-bearing, both fixed with tests: the `unresolved_onc_endpoint` `gap_key` **omitted `endpoint_role`** (a class self-pair
folded two independently-failing endpoints onto ONE immortal `question_uuid`), and `register_from_gaps`' guard never learned
`curated_class_interaction`, whose cascade + append-only trigger turn a closed gap into a **permanently aborted ingest for
every source**. PROJECT-NOTES § "Slice 5c.2"; reads: `db/034`, `cli_curate.py`, spec §14.

**⇒ DO THESE TWO FIRST NEXT SESSION, BEFORE ANY NEW SLICE. (1) Fix issue 91 — the reference database cannot be
migrated.** `drugref_5c4` (the DB PROJECT-NOTES § Repo facts names) has a
ledger checksum for `030_signing.sql` the file no longer hashes to, so `drugref migrate` refuses on it **and on every
`TEMPLATE` copy**; 5c.2 worked around it with `psql -f` four times. **The suite never sees this** — it drops the schema each
session. Preferred fix is a rebuild from the real releases, which also re-verifies the counts every doc quotes.

**(2) Evaluate the two licence-clean sources, before committing to `5c.3`'s shape.** Both licence-checked during 5c.2, neither
in the repo. **OnSIDES** — code MIT, **data CC BY 4.0** (separate `LICENSE-DATA`), attribution only, 3.6M drug–ADE pairs from
47k DailyMed labels, and since v2.1.0 carries **Warnings and Precautions**, where interaction warnings actually live.
**DrugCentral** — **CC BY-SA 4.0**, no registration, full SQL dump, bundle-OK *because* drugref's data layer is share-alike;
**its actual DDI content is UNVERIFIED — check before counting on it.** Open question: does either supply *clinically relevant*
interactions or only label-derived associations? Issue 94 needs that answer.

**Then: `5c.3` — SPL/DailyMed mining.** **No open redistributable QT list exists** — not FDA, EMA or BfArM; CredibleMeds is
registration-gated ([#93](https://github.com/cairn-ehr/drugref/issues/93)) — so QT risk must be re-derived from SPL, or the
owner's Holbrook-group archive used, which needs **written** permission first, to the standard issues 6 and 25 are held to.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61). **Standing rule:** near
`close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#` ("issue 65"). Full account: PROJECT-NOTES.

## Open follow-ups (all filed as GitHub issues)

**Filed by slice 5c.2 and its REVIEW** (detail in PROJECT-NOTES § "Slice 5c.2"). **#90 and #96–#99 are ONE SHAPE — the class
grain has the write path but none of the moiety grain's DETECTORS, so a class rule can be ingested, graded and reported
successful while reaching zero patients; each needs a new `db/035+`** — [#90](https://github.com/cairn-ehr/drugref/issues/90)
`curated_target_unresolved` misses it · [#96](https://github.com/cairn-ehr/drugref/issues/96) no worklist gap kind, so an
ungraded rule **asks nobody to grade it** · [#97](https://github.com/cairn-ehr/drugref/issues/97) both grains can grade one pair
with **different severities**, no precedence · [#98](https://github.com/cairn-ehr/drugref/issues/98) a **signed release silently
omits the grain**, `verify_release` still passes · [#99](https://github.com/cairn-ehr/drugref/issues/99) class roots evade
`gap_unreviewed_expansion_root`. **The rest** — [#91](https://github.com/cairn-ehr/drugref/issues/91) **`drugref_5c4`'s ledger
checksum is stale**, so it and every `TEMPLATE` copy refuse `drugref migrate` — use `psql -f` ·
[#92](https://github.com/cairn-ehr/drugref/issues/92) **a mixed-kind class-pair rule expands to ZERO pairs silently** (db/032's
preamble advertises `statins × CYP3A4`, exactly such a pair) · [#93](https://github.com/cairn-ehr/drugref/issues/93) **MED-RT
carries no QT class** · [#94](https://github.com/cairn-ehr/drugref/issues/94) **the seven withheld entries** need research ·
[#100](https://github.com/cairn-ehr/drugref/issues/100) replaying `db/033` ALONE reinstates the 3.6× regression.

**Filed by slice 5c.4 and its review** — [#85](https://github.com/cairn-ehr/drugref/issues/85) `signing_key_status_kind` has
**no append-only floor**, so one `UPDATE` disarms every compromise verdict; **floor that one ALONE** — `signature_target_kind`
is *designed* to move to a `/v2` · [#86](https://github.com/cairn-ehr/drugref/issues/86) ·
[#87](https://github.com/cairn-ehr/drugref/issues/87) · [#88](https://github.com/cairn-ehr/drugref/issues/88) ·
[#89](https://github.com/cairn-ehr/drugref/issues/89) rule-4 breach. Unfiled: `tests/test_cli_signing*.py` **cannot commit for
real** — test isolation, shaped like [#2](https://github.com/cairn-ehr/drugref/issues/2).

**Filed by earlier rounds** — [#79](https://github.com/cairn-ehr/drugref/issues/79) **`tests/` is exempt from E501** (its
title's 324 has drifted — re-measure, never quote; **debt, not policy**) · [#81](https://github.com/cairn-ehr/drugref/issues/81)
chain-time variance · [#82](https://github.com/cairn-ehr/drugref/issues/82) **`status` reports orphans to humans only** ·
[#75](https://github.com/cairn-ehr/drugref/issues/75) **`gap_uncurated_interaction_rule` costs ~2.7s** ·
[#65](https://github.com/cairn-ehr/drugref/issues/65) no index serves a `class_expansion_policy` HISTORY query.

**Owned by 5c, still open** — [#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168 contradicted pairs**, which now
also **own the `spurious` deferral 5c.1 wrongly handed to 5c.2** (`spurious` is a `curated_condition` ruling; 5c.2 curated the
*interaction* half — ROADMAP § 5c.2) · [#52](https://github.com/cairn-ehr/drugref/issues/52)
**the 422 broadened assertions**: no `concept_ui` on the row · [#55](https://github.com/cairn-ehr/drugref/issues/55)
**`indications_for_condition` generalises through a boolean** · [#67](https://github.com/cairn-ehr/drugref/issues/67)
**salt↔base strength equivalence has no source** · [#73](https://github.com/cairn-ehr/drugref/issues/73) **both views read
every source at once** — for `ddi_candidate_pair` that is now *wanted*, so **re-read the issue against 5c.2** ·
[#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions** — 5c.2 stayed pairwise.

**Filed by the slice-3 design and its measurement** — [#68](https://github.com/cairn-ehr/drugref/issues/68) **3,631 moieties
carry a GSRS `ACTIVE MOIETY` edge to something else** (~19%; why 5c.2 expands salt forms on the projection side, not at read
time) · [#69](https://github.com/cairn-ehr/drugref/issues/69) the 27-edge scope question ·
[#70](https://github.com/cairn-ehr/drugref/issues/70) **354 all-false composites reachable by nothing** ·
[#71](https://github.com/cairn-ehr/drugref/issues/71) **8,163 edges dropped, counted only transiently** — re-learned in 5c.2.

**Interaction model and identity** — [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is
unpopulated**, filed as 41 of 739 but the gap view returns **13**, so **re-measure before acting** ·
[#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` unused** ·
[#36](https://github.com/cairn-ehr/drugref/issues/36) **discovery counts descendant classes, not reachable members** ·
[#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** — restricting the *root
set* is safe, restricting the *walk* deletes the coagulation case · [#48](https://github.com/cairn-ehr/drugref/issues/48) ·
[#2](https://github.com/cairn-ehr/drugref/issues/2) **floor hardening** (`TRUNCATE` + owner-role bypass; **13** `TRUNCATE`-ing
modules depend on it — re-grep before quoting) · [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality**
· [#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms**, blocked behind #68 ·
[#30](https://github.com/cairn-ehr/drugref/issues/30) (`strip_salt`) unmeasured ·
[#5](https://github.com/cairn-ehr/drugref/issues/5) INN from UNII's `Display Name` ·
[#7](https://github.com/cairn-ehr/drugref/issues/7)/[#29](https://github.com/cairn-ehr/drugref/issues/29) **row-at-a-time
ingest** — the two MeSH legs are 75.7% of a 133 s chain. **Before the first production load**: every parser re-run against a
current release, the `add_claim` canonicalisation check from #17, and **three** rule-6 deeds (#6, #25, GSRS's *"unless
otherwise noted"*) — PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`.
  Set it as `DRUGREF_TEST_DSN` for the DB-gated tests. **Which verification database to read is stated once in
  `PROJECT-NOTES.md` § Repo facts** — read **`drugref_5c4`**, but note **issue 91**: its ledger is stale, so a `TEMPLATE` copy
  needs `psql -f db/031`–`034` rather than `drugref migrate`. Not restated here: this file is compressed every session, so a
  second copy would outlive the first and disagree with it.

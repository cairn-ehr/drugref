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
[#84](https://github.com/cairn-ehr/drugref/pull/84)). **`db/029` and `db/030` are MERGED and FROZEN** — corrections need a new
`db/NNN`.

**⇒ JUST FINISHED — SLICE `5c.2`, THE ONC FLOOR (`db/031`–`db/034`), on branch `feat/slice-5c2-onc-floor`. Suite 1297 → 1395,
green; `ruff check .` clean.** drugref's **first clinical content**. The ONC list enters as a **second candidate source**
(`source='ONCHIGH'`) rather than curator-originated content — 5c.1 had already keyed the candidate tier on `source` for exactly
this — so **`db/029` was never touched**. Retrieving the list then refuted the grain, and `db/032` added a **class-subject**
rule; `db/033` carries both grains in one `curated_ddi_pair`; `db/034` recovered a measured hot-path regression. Full account,
every figure and every trap: **PROJECT-NOTES § "Slice 5c.2"**, which leads with the finding below.

**⇒ THE FINDING THAT GOVERNS EVERY FUTURE CLASS-GRAIN RULE. A class-grain rule inherits its population from the source's class
boundary, and that is only safe when the class was defined by the same mechanism the interaction runs on.**
`Cytochrome P450 3A4 Inhibitors [MoA]` *is* the right population for an irinotecan exposure interaction. `Opioid Agonist [EPC]`
is **not** the right population for an MAOI interaction — it conflates serotonergic with opioid-action amplification and
includes **loperamide**; `Central Nervous System Stimulant [EPC]` sweeps in **caffeine** at a dose-dependent risk the rule
cannot qualify. **So 4 of 15 entries shipped and 7 were withheld from an append-only table** ([#94](https://github.com/cairn-ehr/drugref/issues/94),
encodings retrievable from commit `389a560`). **That is the clinical review gate working, not a shortfall** — and a class-grain
rule is not automatically cheaper than a moiety one.

**⇒ MEASURED with the four loaded** (scratch DB from the real releases, 2026-08-12): **8 ONCHIGH candidates · 213 pairs · 0
unresolved endpoints** · `gap_uncurated_interaction_rule` **593 → 591** · `open_question` **21,842 → 21,848** · hot path
**1.551–1.679 ms**. **Both counts that must not move held: `ddi_candidate_pair` MED-RT 21,664 and `substance_moiety` 19,438.**
**The worklist dropped by 2, not 4, and that is the payoff**: `curated_interaction`'s key omits `source`, so curating tizanidine
also answered a pre-existing MED-RT rule on the same key.

**⇒ A 3.6× REGRESSION WAS FOUND, ESCALATED AND FIXED AT ITS CAUSE.** `db/033` widened `ci_class_subtree`'s seed, inflating the
recursive CTE's row estimate ~5× and tipping a Hash Join into a Merge Join — **4.7–5.4 ms even with an EMPTY class overlay**,
i.e. structural, paid by every consumer. `db/034` gave the class grain its **own** walk: **1.50–1.68 ms empty, 2.87–3.28 ms
populated**, with the moiety-grain plan verified byte-identical to the pre-`db/033` one and **no planner GUCs**. Residual ~2.2×
floor disclosed, not hidden.

**⇒ REMAINING FOR THIS BRANCH: nothing but the PR.** Docs are current. If reviewing it, the highest-value reads are
`db/034` (why the class grain needs its own walk), `cli_curate.py` (idempotence by comparison over graded fields only), and
spec §14 (why the grain changed mid-slice).

**⇒ NEXT SLICE: `5c.3` — SPL/DailyMed mining**, or a **sourcing round** first. Two licence-clean sources were confirmed while
researching the QT gap: **OnSIDES** (code MIT, **data CC BY 4.0**, and since v2.1.0 it carries the *Warnings and Precautions*
section where interaction warnings live) and **DrugCentral** (**CC BY-SA 4.0**, no registration, bundle-OK because drugref's
data layer is share-alike). **DrugCentral's actual DDI content is UNVERIFIED** — check before counting on it. **No open
redistributable QT list exists**: neither FDA, EMA nor BfArM maintains one and CredibleMeds is registration-gated
([#93](https://github.com/cairn-ehr/drugref/issues/93)); the owner has an archive from the Holbrook group but it is
copyright-restricted and would need **written** permission first, to the standard issues 6 and 25 are held to.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61). **Standing rule:** near
`close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#` ("issue 65"). Full account: PROJECT-NOTES.

## Open follow-ups (all filed as GitHub issues)

**Filed by slice 5c.2** (detail in PROJECT-NOTES § "Slice 5c.2") — [#90](https://github.com/cairn-ehr/drugref/issues/90)
`curated_target_unresolved` **does not cover the class grain** · [#91](https://github.com/cairn-ehr/drugref/issues/91)
**`drugref_5c4`'s ledger checksum for `030_signing.sql` is stale**, so the reference DB and every `TEMPLATE` copy refuse
`drugref migrate` (db/030 was edited after being applied there; the suite never sees it because it drops the schema each
session — use `psql -f`) · [#92](https://github.com/cairn-ehr/drugref/issues/92) **a mixed-kind class-pair rule expands to ZERO
pairs silently** (one axis selects one membership relationship — db/006's failure mode one tier up) ·
[#93](https://github.com/cairn-ehr/drugref/issues/93) **MED-RT carries no QT class at all** ·
[#94](https://github.com/cairn-ehr/drugref/issues/94) **the seven withheld entries** need literature research, not borrowed
taxonomy.

**Filed by slice 5c.4 and its review** — [#85](https://github.com/cairn-ehr/drugref/issues/85) `signing_key_status_kind` has
**no append-only floor**, so one `UPDATE` disarms every compromise verdict; **floor that one ALONE** —
`signature_target_kind` is *designed* to move to a `/v2` · [#86](https://github.com/cairn-ehr/drugref/issues/86) ·
[#87](https://github.com/cairn-ehr/drugref/issues/87) · [#88](https://github.com/cairn-ehr/drugref/issues/88) ·
[#89](https://github.com/cairn-ehr/drugref/issues/89) `signing.py` (582) and `release_verification.py` (532) breach rule 4.
Still carried, unfiled: `tests/test_cli_signing*.py` **cannot commit for real** — other modules assert blanket unfiltered
counts on shared tables, a test-isolation problem shaped like [#2](https://github.com/cairn-ehr/drugref/issues/2).

**Filed by earlier rounds** — [#79](https://github.com/cairn-ehr/drugref/issues/79) **`tests/` is exempt from E501** (its
title's 324 has drifted — re-measure, never quote; **debt, not policy**) · [#81](https://github.com/cairn-ehr/drugref/issues/81)
chain-time variance · [#82](https://github.com/cairn-ehr/drugref/issues/82) **`drugref status` reports orphans to humans only**
· [#75](https://github.com/cairn-ehr/drugref/issues/75) **`gap_uncurated_interaction_rule` costs ~2.7s** ·
[#65](https://github.com/cairn-ehr/drugref/issues/65) no index serves a `class_expansion_policy` HISTORY query.

**Owned by 5c, still open** — [#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168 contradicted pairs**, which now
also **own the `spurious` deferral 5c.1 wrongly handed to 5c.2** (see ROADMAP § 5c.2: `spurious` is a `curated_condition`
ruling and 5c.2 curated the *interaction* half, so it could not discharge it) · [#52](https://github.com/cairn-ehr/drugref/issues/52)
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
[#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` assertions unused** ·
[#36](https://github.com/cairn-ehr/drugref/issues/36) **discovery counts descendant classes, not reachable members** ·
[#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** — restricting the *root
set* is safe, restricting the *walk* deletes the coagulation case · [#48](https://github.com/cairn-ehr/drugref/issues/48) ·
[#2](https://github.com/cairn-ehr/drugref/issues/2) **floor hardening** (`TRUNCATE` + owner-role bypass; **13** `TRUNCATE`-ing
modules depend on it — re-grep before quoting) · [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change
immortality** · [#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms**, blocked behind #68
· [#30](https://github.com/cairn-ehr/drugref/issues/30) (`strip_salt`) unmeasured ·
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

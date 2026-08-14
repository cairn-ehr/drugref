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

**Merged to `main`**: through **5c.4 — signing**, PLUS **5c.2 — the ONC floor**, merged LAST despite its lower
number — **ROADMAP's order is NOT the merge order.** **`db/029`–`db/034` FROZEN.**

**⇒ JUST FINISHED — `db/035`, the class-grain detectors** (PR [#107](https://github.com/cairn-ehr/drugref/pull/107)),
closing **#90, #96, #97, #98, #99 as ONE migration**, which is what the last handover ordered first. Suite **1409 →
1451**, `ruff` clean. **Seven objects, no clinical content, every count on the reference database byte-identical to
`drugref_db034`** — all four new class-grain objects read **0**, since #94 withheld the seven class×class entries and
nothing else writes the grain. **A detector's correct reading on today's data is zero; a moved count would have been
the surprise.** Full account: **PROJECT-NOTES § "The class-grain detector round"**, ROADMAP § 5c.2a.

**⇒ THE ONE THING FROM `db/035` THAT GOVERNS FUTURE CONSUMER WORK — `curated_ddi_pair` now STATES a precedence, in
its own COMMENT: `ORDER BY severity_rank, (rule_grain = 'moiety_rule') DESC`.** Most severe first, moiety grain
breaking ties. **An ORDER, never a filter** — both rows still appear, because fewer rows is the harm direction. It
needed a new `severity_kind` table (rank 1 = `contraindicated`), since `ORDER BY severity` sorts **`minor` above
`moderate`**; the five identical severity CHECKs became five FKs into it, so **an illegal severity now raises
`ForeignKeyViolation`, not `CheckViolation`**. Most-severe-wins is only defensible because
`curated_grain_disagreement` makes over-warning **finite work somebody reconciles** rather than permanent noise —
the order and the detector are one decision.

**⇒ THE REFERENCE DATABASE IS NOW `drugref_db035`** (ledger 35 rows), from `TEMPLATE drugref_db034` + `drugref
migrate` — that workflow re-tested, not assumed; **`drugref_db034` is KEPT as the before/after control.** **Editing
an applied migration invalidates its ledger checksum** (issue 91's exact failure): edit `db/035` again while this
branch is unmerged and you must **drop and rebuild `drugref_db035` from the template afterwards**.

**⇒ THE MEASUREMENT LESSON, worth more than the migration: a sequential before/after read as a 13% hot-path
regression and it was warm-up.** Interleaved against a `db/034` control, 12 runs each: **1.626 vs 1.662 ms**, 2.2%
apart, the control's own spread (0.34 ms) wider than the difference and its slowest run slower than anything the new
schema produced; the moiety grain's recursive union is **byte-identical** (`cost=14.94..3886.97 rows=37414`, actual
1235). **That sequential shape is how #81's unexplained +13% happened; interleave.**

**⇒ DO THIS NEXT — the next slice, and the evaluation says the cheap one is DrugCentral, not SPL**: 6,337 new
public-domain moiety-grained pairs, hard part name resolution, against 5c.3's NLP pipeline. Either way it opens with
its own design round; [#101](https://github.com/cairn-ehr/drugref/issues/101) holds the DrugCentral shape and its two
rules (`ddi_ref_id = 2` only; the 2023-11-01 dump does not refresh). **`5c.3` — SPL/DailyMed mining** must answer two
things: section 7 qualifies by **potency band**, which MED-RT's one undifferentiated class **cannot express**
([#102](https://github.com/cairn-ehr/drugref/issues/102)), and its corpus must be filtered by **document type** — key
on the CODE (`34391-3`/`34390-5`), not `displayName`; a 50-label sample gave 14/16 prescription, 0/30 OTC,
**indicative only, re-measure**. **No open redistributable QT list exists** ([#93](https://github.com/cairn-ehr/drugref/issues/93));
routes: PROJECT-NOTES. **Whichever lands is the first slice that can POPULATE the class grain**, so `db/035`'s
detectors get their first real exercise then and #105 becomes answerable against content rather than against nothing.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61). **Standing rule:**
near `close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#` ("issue 65"). Full account: PROJECT-NOTES.

## Open follow-ups (all filed as GitHub issues)

**Filed by THIS round** — [#104](https://github.com/cairn-ehr/drugref/issues/104) **`curate` leaves the question
registry stale until the next ingest**: **601 stored vs 593 derived** on the reference DB, the 8 rules ONCHIGH
curation answered still sitting on the worklist, because `curate` is deliberately not a chain step and only ingest
re-derives — **pre-existing, verified identical on `drugref_db034`, so do NOT attribute it to `db/035`** ·
[#105](https://github.com/cairn-ehr/drugref/issues/105) promote `curated_grain_disagreement` to a gap kind once
class-grain content ships (a `gap_key` is frozen forever; zero class-grain rows exist to choose its grain against) ·
[#106](https://github.com/cairn-ehr/drugref/issues/106) two **moiety**-grain rules on different axes can also grade
one pair differently — visibility, not correctness, since the new precedence orders it deterministically.

**Left open by 5c.2, NOT closed by `db/035`** — [#92](https://github.com/cairn-ehr/drugref/issues/92) **a mixed-kind
class-pair rule expands to ZERO pairs silently** (db/032's preamble advertises `statins × CYP3A4`, exactly such a
pair): `db/035` is its **substrate**, not its fix — such a rule now reads 0 on one side of `class_pair_rule_reach`
and `drugref status` prints the count, but the real fix is schema-level (a rule that can name two axes) ·
[#93](https://github.com/cairn-ehr/drugref/issues/93) **MED-RT carries no QT class** ·
[#94](https://github.com/cairn-ehr/drugref/issues/94) **the seven withheld entries** need research ·
[#100](https://github.com/cairn-ehr/drugref/issues/100) replaying `db/033` ALONE reinstates the 3.6× regression.
**Filed by 5c.4 and its review** — [#85](https://github.com/cairn-ehr/drugref/issues/85) `signing_key_status_kind` has **no
append-only floor**, so one `UPDATE` disarms every compromise verdict; **floor that one ALONE** — `signature_target_kind` is
*designed* to move to a `/v2` · [#86](https://github.com/cairn-ehr/drugref/issues/86) ·
[#87](https://github.com/cairn-ehr/drugref/issues/87) · [#88](https://github.com/cairn-ehr/drugref/issues/88) ·
[#89](https://github.com/cairn-ehr/drugref/issues/89) rule-4 breach. Unfiled: `tests/test_cli_signing*.py` **cannot commit for
real** — test isolation, shaped like [#2](https://github.com/cairn-ehr/drugref/issues/2). **Earlier rounds** —
[#79](https://github.com/cairn-ehr/drugref/issues/79) **`tests/` is exempt from E501** (its title's 324 has drifted —
re-measure, never quote; **debt, not policy**) · [#81](https://github.com/cairn-ehr/drugref/issues/81) chain-time variance
(**now has an interleaved-control method that works — see ⇒ above**) · [#82](https://github.com/cairn-ehr/drugref/issues/82)
**`status` reports orphans to humans only** · [#75](https://github.com/cairn-ehr/drugref/issues/75)
**`gap_uncurated_interaction_rule` costs ~2.7 s** · [#65](https://github.com/cairn-ehr/drugref/issues/65) no index serves a
`class_expansion_policy` HISTORY query.

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
  `PROJECT-NOTES.md`, in the "Dev DSN" bullet of § How to run / test** — read **`drugref_db035`**, whose ledger
  is clean, so a `TEMPLATE` copy takes `drugref migrate` normally. Not restated here: this file is compressed every
  session, so a second copy would outlive the first and disagree with it.

# HANDOVER — drugref

> **The volatile half: where we are right now.** Regenerated at the end of every working session
> (nextsession rule 9) and kept **under 130 lines**, so a rewrite costs nothing.
>
> **THIS LINE IS THE ONLY HOME FOR THAT NUMBER.** It was also in `CLAUDE.md` (twice) and the `nextsession`
> skill; all three said `~120` while this header said `~130` and the file was 136. A bound is a vocabulary
> like any other, and this repo has lost four rounds to one rule kept in two places. **Change it here, alone.**
>
> **The stable half is [`PROJECT-NOTES.md`](PROJECT-NOTES.md)** — traps, state by layer, how to run and test,
> the schema and code map, upstream errata, repo facts. Edited in place, under no bound. **Put anything whose
> history is worth reading there, not here** (#63): this file's history is deliberately disposable.
>
> Slice sequencing is [`ROADMAP.md`](ROADMAP.md); the canonical what/why is the specs under
> [`superpowers/specs/`](superpowers/specs/).

## ⇒ NEXT

**Merged to `main`**: through **5c.4 — signing**, PLUS **5c.2 — the ONC floor**, merged LAST despite its lower number — **ROADMAP's order is NOT the merge order.** **`db/029`–`db/034` FROZEN.**

**⇒ JUST FINISHED — `db/035`, the class-grain detectors, PLUS the PR #107 review round that followed**
(PR [#107](https://github.com/cairn-ehr/drugref/pull/107)), closing **#90, #96, #97, #98, #99 as ONE migration**.
Suite **1409 → 1465**, `ruff` clean. **Seven objects, no clinical content, every count byte-identical to
`drugref_db034`** — all four new class-grain objects read **0** (#94 withheld the seven class×class entries and
nothing else writes the grain), so **a detector's correct reading on today's data is zero**. Full account:
**PROJECT-NOTES §§ "The class-grain detector round" and "The PR #107 review round"**, ROADMAP §§ 5c.2a, 5c.2b.

**⇒ THE REVIEW ROUND'S SHIP-BLOCKER AND THE RULE IT BOUGHT: `drugref status` crashed with a raw traceback on every
database not yet migrated to `db/035`**, reproduced on the `drugref_db034` control. Widening
`curated_target_unresolved` with `subject_class` made the stale-database failure **`UndefinedColumn`, a *sibling* of
`UndefinedTable`, not a subclass**, so the guard written for that moment never fired. **⇒ A MIGRATION THAT WIDENS A
VIEW A GUARDED BLOCK READS MUST WIDEN THAT BLOCK'S EXCEPTION TUPLE IN THE SAME COMMIT.** Three siblings: `status`
printed literal `None` for every class-grain orphan's subject (#90 half-closed — now `UnresolvedTarget.subject`),
the class-grain block had no guard at all, and **permuting a frozen signing field list passed all 249 signing tests**
(set-based coverage test, vectors that never read `FIELD_LISTS`; now pinned). `cli.py` hit rule 4's cap, so the
block moved to **`cli_status.py`** — 483 → 450.

**⇒ THE ONE THING GOVERNING FUTURE CONSUMER WORK — `curated_ddi_pair` STATES a precedence in its own COMMENT:
`ORDER BY severity_rank, (rule_grain = 'moiety_rule') DESC`** — most severe first, moiety breaking ties, **an ORDER
never a filter**. It needed `severity_kind` (rank 1 = `contraindicated`, since `ORDER BY severity` sorts **`minor`
above `moderate`**); the five severity CHECKs became FKs into it, so **an illegal severity now raises
`ForeignKeyViolation`**. **But no view APPLIES the precedence and nothing in `src/` reads `severity_rank` — #110.**

**⇒ THE REFERENCE DATABASE IS NOW `drugref_db036`** (ledger 36), from `TEMPLATE drugref_db035` + `drugref migrate`
— that workflow re-tested for the third round running; **`drugref_db034` is KEPT as the before/after control**, and
is what reproduced the crash above. **`db/036` is three `COMMENT ON` statements and no schema change**: catalog
comments ship in `pg_description`, so a wrong one is what `\d+` tells a DBA on a running node. **Editing an applied
migration invalidates its ledger checksum** (issue 91): edit `db/035` or `db/036` again while this branch is
unmerged and you must **rebuild the reference database from the template afterwards**. (The db/035 hot-path
measurement and its lesson — sequential before/after read as +13% and it was warm-up, so **interleave against a
control**, which is #81's method — are in PROJECT-NOTES § "The class-grain detector round".)

**⇒ DO THIS NEXT — the next slice, and the evaluation says the cheap one is DrugCentral, not SPL**: 6,337 new
public-domain moiety-grained pairs, hard part name resolution. Either way it opens with its own design round;
[#101](https://github.com/cairn-ehr/drugref/issues/101) holds the DrugCentral shape and its two rules
(`ddi_ref_id = 2` only; the 2023-11-01 dump does not refresh). **`5c.3` — SPL/DailyMed mining** must answer two
things: section 7 qualifies by **potency band**, which MED-RT's one undifferentiated class **cannot express**
([#102](https://github.com/cairn-ehr/drugref/issues/102)), and its corpus must be filtered by **document type** — key
on the CODE (`34391-3`/`34390-5`), not `displayName` (a 50-label sample gave 14/16 prescription, 0/30 OTC —
**indicative only, re-measure**). **Whichever lands is the first slice that can POPULATE the class grain**, so
`db/035`'s detectors get their first real exercise then — and #105, #108, #109 and #112 all become answerable
against content rather than against nothing.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61). **Standing rule:** near `close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#` ("issue 65"). Full account: PROJECT-NOTES.

## Open follow-ups (all filed as GitHub issues)

**Filed by THIS PR (db/035 + its review)** — [#108](https://github.com/cairn-ehr/drugref/issues/108)
**`max_pair_count` is not exact about zero**: a one-member self-pair rule reads 1 and reaches 0, so it is **both**
queued as a pointless curator question **and** hidden from the operator's dead-rule line — #36's mistake and
db/035's own target failure at once (`db/036` fixed the wording, this owns the arithmetic) ·
[#109](https://github.com/cairn-ehr/drugref/issues/109) `curated_grain_disagreement` misses **mirror-oriented** rule
pairs (rows are directional per db/006) · [#110](https://github.com/cairn-ehr/drugref/issues/110) ship the
precedence as a view (+ `NULLS FIRST`) · [#111](https://github.com/cairn-ehr/drugref/issues/111) the status block's
zeros need a **denominator** ("healthy" and "a rebuild emptied the tier" render identically) ·
[#112](https://github.com/cairn-ehr/drugref/issues/112) measure the disagreement self-join before content ships ·
[#104](https://github.com/cairn-ehr/drugref/issues/104) **`curate` leaves the question registry stale until the next
ingest** (601 stored vs 593 derived — **pre-existing, verified identical on `drugref_db034`, do NOT attribute it to
`db/035`**) · [#105](https://github.com/cairn-ehr/drugref/issues/105) promote `curated_grain_disagreement` to a gap
kind once content ships · [#106](https://github.com/cairn-ehr/drugref/issues/106) two **moiety**-grain rules on
different axes can also grade one pair differently — visibility, not correctness.
**Left open by 5c.2, NOT closed by `db/035`** — [#92](https://github.com/cairn-ehr/drugref/issues/92) **a mixed-kind
class-pair rule expands to ZERO pairs silently** (db/032 advertises `statins × CYP3A4`, exactly such a pair): `db/035`
is its **substrate**, not its fix — the real fix is schema-level (a rule naming two axes) ·
[#93](https://github.com/cairn-ehr/drugref/issues/93) **MED-RT carries no QT class** ·
[#94](https://github.com/cairn-ehr/drugref/issues/94) **the seven withheld entries** need research ·
[#100](https://github.com/cairn-ehr/drugref/issues/100) replaying `db/033` ALONE reinstates the 3.6× regression.
**Filed by 5c.4 and its review** — [#85](https://github.com/cairn-ehr/drugref/issues/85) `signing_key_status_kind` has **no
append-only floor**, so one `UPDATE` disarms every compromise verdict; **floor that one ALONE** — `signature_target_kind` is
*designed* to move to a `/v2` · [#86](https://github.com/cairn-ehr/drugref/issues/86) ·
[#87](https://github.com/cairn-ehr/drugref/issues/87) · [#88](https://github.com/cairn-ehr/drugref/issues/88) ·
[#89](https://github.com/cairn-ehr/drugref/issues/89) rule-4 breach. Unfiled: `tests/test_cli_signing*.py` **cannot commit for
real** — test isolation, shaped like [#2](https://github.com/cairn-ehr/drugref/issues/2). **Earlier rounds** —
[#79](https://github.com/cairn-ehr/drugref/issues/79) **`tests/` is exempt from E501** (title count drifted — re-measure) ·
[#81](https://github.com/cairn-ehr/drugref/issues/81) chain-time variance (**has an interleaved-control method now**) ·
[#82](https://github.com/cairn-ehr/drugref/issues/82) **`status` reports orphans to humans only** ·
[#75](https://github.com/cairn-ehr/drugref/issues/75) **`gap_uncurated_interaction_rule` costs ~2.7 s** ·
[#65](https://github.com/cairn-ehr/drugref/issues/65) no index serves a policy HISTORY query.
**Owned by 5c, still open** — [#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168 contradicted pairs**, which now
also **own the `spurious` deferral 5c.1 wrongly handed to 5c.2** (ROADMAP § 5c.2) ·
[#52](https://github.com/cairn-ehr/drugref/issues/52) **the 422 broadened assertions**: no `concept_ui` on the row ·
[#55](https://github.com/cairn-ehr/drugref/issues/55) **`indications_for_condition` generalises through a boolean** ·
[#67](https://github.com/cairn-ehr/drugref/issues/67) **salt↔base strength equivalence has no source** ·
[#73](https://github.com/cairn-ehr/drugref/issues/73) **both views read every source at once** — for `ddi_candidate_pair` that
is now *wanted*, so **re-read it against 5c.2** · [#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions**.
**From the slice-3 design** — [#68](https://github.com/cairn-ehr/drugref/issues/68) **3,631 moieties carry a GSRS `ACTIVE
MOIETY` edge to something else** (~19%; why 5c.2 expands salt forms on the projection side, not at read time) ·
[#69](https://github.com/cairn-ehr/drugref/issues/69) the 27-edge scope question ·
[#70](https://github.com/cairn-ehr/drugref/issues/70) **354 all-false composites reachable by nothing** ·
[#71](https://github.com/cairn-ehr/drugref/issues/71) **8,163 edges dropped, transiently counted**.
**Interaction model and identity** — [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is
unpopulated**, filed as 41 of 739 but the gap view returns **13**, so **re-measure before acting** ·
[#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` unused** ·
[#36](https://github.com/cairn-ehr/drugref/issues/36) **discovery counts descendant classes, not reachable members** ·
[#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** — restricting the *root
set* is safe, restricting the *walk* deletes the coagulation case · [#48](https://github.com/cairn-ehr/drugref/issues/48) ·
[#2](https://github.com/cairn-ehr/drugref/issues/2) **floor hardening** (`TRUNCATE` + owner-role bypass; **13** `TRUNCATE`-ing
modules depend on it — re-grep before quoting) · [#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change
immortality** · [#33](https://github.com/cairn-ehr/drugref/issues/33) **MeSH CAS keys name specific forms** (behind #68) ·
[#30](https://github.com/cairn-ehr/drugref/issues/30) (`strip_salt`) unmeasured ·
[#5](https://github.com/cairn-ehr/drugref/issues/5) INN from UNII's `Display Name` ·
[#7](https://github.com/cairn-ehr/drugref/issues/7)/[#29](https://github.com/cairn-ehr/drugref/issues/29) **row-at-a-time
ingest** — the two MeSH legs are 75.7% of a 133 s chain. **Before the first production load**: every parser re-run against a
current release, #17's `add_claim` canonicalisation check, **three** rule-6 deeds (#6, #25, GSRS) — PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`. Set it as `DRUGREF_TEST_DSN` for the DB-gated tests.
- **WHICH VERIFICATION DATABASE TO READ IS NOT NAMED HERE** — it is stated once, in `PROJECT-NOTES.md`'s "Dev DSN" bullet of § How to run / test. An earlier version of this bullet said exactly that and then named it anyway; the name moved this round, and that copy would have outlived it.

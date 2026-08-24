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

**Branch `claude/spl-ddi-mining-measure`, from `main` at `20380ac`** (PR #150 merged 2026-08-23);
**this round is open as [PR #156](https://github.com/cairn-ehr/drugref/pull/156) and is not merged.** Migrations
through **`db/050`** — **this round added none.** The suite total lives in PROJECT-NOTES § "How to run / test"
and **nowhere else** ([#146](https://github.com/cairn-ehr/drugref/issues/146)); read it there at the START of
the session.

**⇒ JUST FINISHED — the slice 5c.3 SPL source MEASUREMENT round. No migration, no ingest, no design spec.**
[Measurement record](superpowers/specs/2026-08-24-drugref-slice-5c3-spl-mining-measurement.md); full account
and **every figure**: PROJECT-NOTES § "The 5c.3 SPL measurement round". Do not re-derive them from here.

**Three brainstorm decisions scope the slice**, and the design round should not re-litigate them without
reason: it produces **both** drug × class rules and drug × drug exemplars, **kept separate with shared
provenance**; extraction is **deterministic entity recognition with NO relation extraction** (deciding a
sentence means "contraindicated" is a clinical reading — *ingest preserves evidence; curation creates clinical
judgement*); and the corpus is measured **in full**, both corpora, never sampled.

**⇒ MEASURING FIRST CHANGED THE SHAPE FOR THE THIRD ROUND RUNNING, AND THIS TIME IT CHANGED THE CORPUS.** The
round opened committed to DailyMed's 18 GB Rx release. **openFDA carries the same section under an explicit
CC0 1.0 dedication, at 1.73 GB, with `drug_interactions` pre-split as a field and an `openfda.unii` bridge**
(`moiety_uuid` is UUIDv5-on-UNII, so the subject resolves for free). Both were taken in the end — DailyMed as
the cross-check, which is what turns "openFDA's field looks right" into a measured claim.

**⇒ RULE 6 IS NOT SETTLED, AND IT GATES THE SCHEMA — [#154](https://github.com/cairn-ehr/drugref/issues/154).**
NLM disclaims (*"cannot guarantee the copyright status for any item"*) over labeling *"submitted to the FDA by
companies"* — the DIRIL shape exactly — while **openFDA dedicates the same bytes CC0**. Derived facts, offsets
and `set_id` citations are clear under either reading; **verbatim prose is not**. **Recommendation: reference
the prose, do not bundle it** — it satisfies both readings, costs nothing that matters, and matches `db/045`'s
citation-only SPL references. **This is a posture call for the owner, not a defect, and it needs an explicit
answer before the design round sets a column.**

**⇒ THE HEADLINE, and it re-opens [#102](https://github.com/cairn-ehr/drugref/issues/102) in new terms.** The
potency band is **a property of the (inhibitor, substrate) PAIR, not of the inhibitor** — FDA's own footnote 20
bands ciprofloxacin *moderate* and then **names tizanidine** as the substrate against which it behaves strong,
so the label and the table never disagreed. **That retires options 1 and 2 of #102**, which both hang the band
on the class. And the band is **not rare**: it looked like 0.8% through drugref's stored class names, but the
prose carries `band + CYP<n> + role` **15,708 times in 15.5% of wordings**, and a band near a role word in
**25.4%** — against the **2,212** occurrences FDA-CYP's names actually matched, **roughly 7×**. The cause is
word order (labels write *"strong CYP1A2 inhibitors"*, drugref stores *"CYP1A2 strong inhibitor"*).

**⇒ AND THE CLASS VOCABULARY DOES NOT FIT — [#155](https://github.com/cairn-ehr/drugref/issues/155).** Split
by whether the matched class **has any members**, **32.3% of all class occurrences name an EMPTY class**.
MED-RT's PK axis is the worst: **97.2% empty**, because its 59 concepts are pharmacokinetic *properties*
(`Clearance`, `Half-Life`, `Cytochromes`) of which only 6 have a member — matching them recognises ordinary
English and mints false positives carrying real class UUIDs. MeSH is the opposite (112 empty of 115,583).

**The pair yield justifies the slice on its own: 20,554 distinct candidate pairs, 18,107 (88.1%) novel**,
against the 7,501 at 91% that justified DrugCentral — **nearly 3×, at the same novelty rate.** ⇒ **That figure
is the SUPPRESSION variant, and it is the one to quote**: the round's first pass published a range whose low
end deleted **`lithium`, the corpus's most-matched moiety**, along with `alcohol` and `iron`, while keeping
`serotonin` — because it excluded dictionary words on an *unmeasured* guess ("`lead` is a verb"). Measured,
three of the four suspects are the **head of a longer term** (`lead to`, `prothrombin time`, `serotonin
syndrome`) which longest-match-wins already handles once drugref holds the longer term, and `alcohol` was a
**true positive all along**. See PROJECT-NOTES for the distributions. **The counterweight: 41,056 labels (60%)
are discarded before a pair can form** for want of a resolvable subject.

## ⇒ DO THIS NEXT

**The 5c.3 DESIGN round** — brainstorm is done, measurement is done, and the spec does not exist yet. It opens
with **four inherited answers and one blocking question**. The blocking one is **#154**: ask the owner whether
SPL prose may be bundled, because the answer decides whether the schema stores text or a citation. The four:
the corpus is openFDA (pinned by `export_date` + per-partition SHA-256); the band is pair-scoped and belongs on
the **assertion**, not the class; MED-RT's PK axis is not an endpoint vocabulary; and the moiety grain is ready
now while the class grain is where every unsolved problem lives.

**If 5c.3's design is not the choice, `5c.5` pregnancy & lactation is still spiked-not-designed** — LactMed
alone puts 1,679 moieties outside MED-RT's thin lactation floor, and it is gated on a **clinician review that
has not happened** (a 23-row worklist ships with the spike results).

## Parallel project sequencing

DrugCentral is done and is a **candidate-tier floor pinned to the 2023 release** — it does not refresh, and
nothing in that tier may auto-alert. FDA toxicity remains cleared and unscheduled; class-grain content (#98)
still gates #112/#105.

## Open follow-ups

The full ledger lives once in [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). New this
round: **[#154](https://github.com/cairn-ehr/drugref/issues/154)** (rule 6 for SPL prose — the owner's call,
and it gates the design round) and **[#155](https://github.com/cairn-ehr/drugref/issues/155)** (MED-RT's PK
axis is not a drug-class vocabulary). **#102 was re-opened in new terms** rather than re-filed. Still standing:
#148, #149, #151, #152, #153, #146, #128/#129 and #132–#135 (FDA-CYP residue), #124, #121/#123, #104, #94.
Before production: re-run every parser on current releases, resolve #17, and the three rule-6 deeds (#6, #25,
GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres`. Set `DRUGREF_TEST_DSN` for DB
  tests; never use it for reviewer accounts or GUI service data — pytest recreates it, and see #153 before
  running two sessions against it at once.
- **`drugref_spl`** is this round's measurement database and is the one to reuse: it is the **only** database
  holding every vocabulary at once (`TEMPLATE drugref_dc049` → `migrate` to `050` → `ingest fda-cyp` →
  `ingest onchigh`). Neither predecessor was enough — `drugref_5c2g` has FDA-CYP but no DrugCentral,
  `drugref_dc049` has DrugCentral but neither FDA-CYP nor the ONC floor. Rebuild command: measurement record §2.
- **`drugref_dc049`** and **`drugref_dc101`** are the DrugCentral round's databases; `dc049` still predates
  `db/050`, so migrate it before re-measuring anything against it.
- Corpora on disk: `downloads/OPENFDA/` (14 partitions, `export_date` 2026-08-22) and `downloads/DAILYMED/`
  (6 Human Rx parts, `last-modified` 2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is
  recorded in the measurement record §2 instead** — re-fetch and verify against that table, not against a
  manifest file that disappears with the bytes it describes.
- The verification database and its migration state live once in PROJECT-NOTES § "How to run / test"; do not
  copy that volatile map here.

# drugref — slice 5c.2g: `FDA-CYP` potency classes

**Date:** 2026-08-16 · **Status:** design, approved · **Sequencing:** ROADMAP § 5c — **a prerequisite for
5c.3, taken before it.**

The [FDA interaction and toxicity source
spike](2026-08-16-drugref-fda-interaction-and-toxicity-source-spike.md) § 3 found the vocabulary the 5c.3
evaluation discovered was missing: SPL section 7 qualifies interactions by **potency band** — *strong* CYP1A2
inhibitors contraindicated, *moderate or weak* "avoid" — and MED-RT's single undifferentiated
`Cytochrome P450 1A2 Inhibitors [MoA]` cannot express it. This slice lands that vocabulary as source-defined
PK classes so an SPL rule can point at the exact band rather than build a temporary one.

**The spike's own implementation order puts DrugCentral's DDI slice first. That is a preference between two
independent slices, not a dependency**: FDA-CYP blocks 5c.3 and DrugCentral does not block FDA-CYP. Taking
FDA-CYP first was the session's explicit decision; DrugCentral ([#101](https://github.com/cairn-ehr/drugref/issues/101))
remains the next *content* slice and is untouched by this one.

## 1. Scope

| | |
|---|---|
| **In scope** | FDA's CYP/transporter examples table as a **classification** source · `db/039` · a pure parser and an orchestrator · the closed pathway vocabulary and its failure behaviour · withholding qualified rows and raising questions on them · three reported-not-exploded populations · the rule-6 determination, already discharged by the spike · end-to-end measurement |
| Not in scope | **creating DDI pairs by joining inhibitors to substrates** — §9, and the reason it is a refusal rather than a deferral |
| Not in scope | adjudicating what any footnote *means* clinically — §5; that is curation's job, and this slice's whole shape follows from it |
| Not in scope | the three FDA toxicity sources (DICTrank, DIRIL, DILIrank). They share this source's authority and nothing else: they are evidence assessments needing a non-firing projection, spike §4 |
| Not in scope | bridging FDA's name spellings to drugref's — §7 records why each residue category is a different job, and none of them is a fuzzy match |
| Not in scope | `class_parent` edges. FDA states no hierarchy; inventing one is §4's rejected alternative |

## 2. What the source actually is

Retrieved live on 2026-08-16 from
<https://www.fda.gov/drugs/drug-interactions-labeling/healthcare-professionals-fdas-examples-drugs-interact-cyp-enzymes-and-transporter-systems>.

**The independent fetch reproduced the spike's SHA-256 exactly** —
`7400dc898509e83d888ecd713897e59f3dc9d1c5f6cbd2f62a5d6ff8377ffa73` — which makes the spike's §2 reproduction
manifest the first source figure in this project verified by a second run rather than trusted. Every count
below was measured on those bytes.

### 2.1 The table is a matrix, not a list of facts

The page carries six tables. Table 1 is the data: **245 data rows × 11 columns**. The first column is the
substance; **each of the other ten IS a `(system, role, potency)` tuple**, and the cell holds the *pathway
list*:

| # | column header | means |
|---|---|---|
| 1–3 | `CYP Strg INH` · `CYP Mod INH` · `CYP WK INH` | CYP inhibitor, strong / moderate / weak |
| 4–6 | `CYP Strg IND` · `CYP Mod IND` · `CYP WK IND` | CYP inducer, strong / moderate / weak |
| 7–8 | `CYP SENS SUB` · `CYP Mod SENS SUB` | CYP sensitive / moderately sensitive substrate |
| 9–10 | `TRNSP INH` · `TRNSP SUB` | transporter inhibitor / substrate — **no potency vocabulary** |

**337 non-empty cells expand to 419 `(substance × pathway × role × potency)` tuples over 65 classes.** 244
distinct substances; `aprepitant` is the one substance on two rows, which is why 245 ≠ 244.

> **419 corrects the 415 this section first carried, and §8 explains why the wrong number looked right.**
> The design round's probe could not parse four tuples and *rejected* them; the round read those rejections
> as the closed vocabulary working, and recorded the survivors as the total. The shipped parser handles all
> four. **245, 337 and 65 were re-verified against the shipped parser and are unchanged.**

FDA gives the bands quantitative definitions in tables 2–5 (a strong CYP inhibitor raises a sensitive
substrate's AUC ≥ 5-fold; moderate 2 to < 5; weak 1.25 to < 2; inducers ≥ 80% / 50–80% / 20–50% decrease).
**Those definitions are the reason the band is worth carrying and are not themselves ingested** — drugref
stores the class, not the pharmacokinetics.

### 2.2 The cell grammar, which is dirty in five distinct ways

Every one of these was found by parsing the real bytes, and each has a test in §10:

1. **Three list separators for one concept** — `;` (`P-gp; BCRP`), `and` (`3A and 2C19`), `,` (`1A2, 2B6`)
   — **and one cell mixes two of them**: rifampin's `1A2, 2B6; 2C8; 2C9 moderate inducer`, four pathways
   from one cell, the only such cell in the 337. A parser that treats the separators as alternatives rather
   than as a set silently reads it as fewer facts than it states.
2. **Inconsistent pathway spelling** — bare `3A` beside `CYP3A`; `MATE2-K`; `P-gp` with a trailing noun in
   `BCRP and P-gp transporters inhibitor` (Pirtobrutinib). **Quoted in full deliberately**: an earlier draft
   of this line shortened it to `BCRP and P-gp transporters`, dropping the role word, and Task 4's test was
   then written against the shortened form — where it failed, because a cell with no role phrase is one the
   parser is *supposed* to reject. Same defect as §2.3's, twice in one document.
3. **A coarser pathway that is not a typo** — `OATP1B` appears where other rows say `OATP1B1` / `OATP1B3`.
   §4.2 rules on it.
4. **The legend's word is not always the cell's word** — `moderately sensitive substrate` against the
   column's `Mod SENS SUB`.
5. **The role phrase can repeat per list item** — `BCRP; OATP1B1 inhibitor; OAT3 inhibitor` (teriflunomide),
   where the trailing phrase covers only the items that do not state their own.

So the grammar a parser must implement is: **a `;`-separated list of `pathway [footnote] [role phrase]`, with
a trailing role phrase applying to every item that did not state one.**

### 2.3 Footnote markers live in two namespaces and three positions

| position | example | note |
|---|---|---|
| glued to the substance name | `adefovir 1` | 21 rows |
| **a comma-separated list on the name** | `ritonavir 14, 15, 16` | three markers |
| inside a cell, at the end | `3A moderate inhibitor 5` (conivaptan) | 2 cells |
| **inside a cell, attached to one pathway** | `1A2 20 ; 3A moderate inhibitor` (ciprofloxacin) | mid-cell |
| as a **letter**, not a digit | `CYP3A moderate inducer b` (cenobamate) | a second namespace |

**`ritonavir 14, 15, 16` is the load-bearing case.** A stripper that handles `adefovir 1` but not a
comma-separated list leaves the substance named with its markers attached, which resolves to nothing — so
**one of the most important CYP3A inhibitors in medicine disappears from the ingest silently, and the run
still reports success.** It was found only because the unresolved-name residue was read row by row.

**⇒ AND THE FIRST DRAFT OF THIS ROW QUOTED THE BUG INSTEAD OF THE SOURCE, which is worth more than the
correction.** It recorded FDA's text as `ritonavir 14, 15,` — with a trailing comma and no `16`. That string
appears nowhere on FDA's page. It was the *output of the design round's own probe stripper*, whose
`(\s+\d+)+$` ate the trailing ` 16` and left the comma behind; the figure was then written down as a
measurement of the source. Task 2 of the implementation caught it by printing the cleaned name.
**The lesson generalises past this row: a partially-working parser does not announce itself — it hands you a
plausible string, and a plausible string gets quoted.** Every substance name in this spec was produced by
that same probe, so any of them may carry the same defect; the parser's own output, not the probe's, is the
authority from here.

## 3. The decisive finding: two footnotes negate the row they sit on

| row | the row asserts | its own footnote says |
|---|---|---|
| `bupropion 2` | `2B6 sensitive substrate` | *"Bupropion itself is **not** a sensitive substrate."* |
| `rolapitant 17` | `P-gp; BCRP inhibitor` | *"**Intravenously administered** rolapitant does **not** inhibit BCRP and P-gp."* |

The spike wrote that silently dropping a qualifier is not permitted. **The page shows why it is not merely
lossy: for these two rows, dropping the qualifier makes drugref assert the opposite of its cited source.**

The other footnotes narrow rather than negate — dose (`4`: "based on 200 mg daily dose"), route (`5`:
"intravenously administered conivaptan"), preparation (`9`, `18`: grapefruit juice and St John's wort "vary
widely"), metabolite (`2`), genotype (`11`: "in CYP2C19 extensive metabolizer subjects"), single dose (`13`),
and pharmacogenetic basis (`3`). **Ingest does not sort them into negating and narrowing**, because that
sorting is a clinical reading of free-text prose, and it is exactly the judgement the spike's invariant
reserves: *ingest preserves evidence; curation creates clinical judgement.* 18 footnotes exist today and a
re-fetch can add a nineteenth nobody classified.

## 4. What lands

### 4.1 Source identity

- `ingest_run.source = 'FDA-CYP'`, `writer = 'fda_cyp_run'`.
- **An explicit `ids._SOURCE_CANONICAL` entry**, even though `FDA-CYP` survives the upper-case fall-through
  unchanged. That is the house idiom `GSRS` and `DRUGREF` already follow, and `ids.py`'s own docstring warns
  by name against leaning on the fall-through: the entry records that the luck was **checked**.
- `substance_class.source = 'FDA-CYP'`, `concept_type = 'PK'`, `class_membership.relationship = 'has_PK'`.
  **Both vocabularies already exist** in db/003's CHECKs, so this slice widens no membership grammar — only
  the two `source` CHECKs.

### 4.2 The class vocabulary — 65 classes

One class per `(system, pathway, role, potency)`:

- `source_code` is deterministic and lower-case: `cyp:3a:inhibitor:strong`, `cyp:2c19:substrate:sensitive`,
  `transporter:pgp:substrate`. It is **explicitly a drugref normalisation key, not an FDA identifier** —
  the live URL, `dateModified`, checksum and raw column heading carry the provenance.
- `published_code` is **NULL**: FDA publishes no code for these classes, and inventing one in the column
  reserved for "the code as published" would be a manufactured fact in a provenance field.
- `class_name` renders as **`CYP3A strong inhibitor [FDA-CYP]`** — source-tagged, so no consumer or UI can
  mistake it for one of MED-RT's `[MoA]` classes. MED-RT's bracketed suffix is *published by MED-RT*; this
  one is drugref's own label and says so.
- **`OATP1B` is its own class, never expanded to `OATP1B1` + `OATP1B3`.** Expanding it would manufacture a
  specificity FDA declined to state, on exactly the reasoning §7 applies to `S-mephenytoin`.
- **No `class_parent` edges in the first release.** FDA publishes no hierarchy. A plausible one
  (`cyp:3a:inhibitor:strong` under a notional `cyp:3a:inhibitor`) is drugref inventing structure and then
  inheriting clinical advice along it — rejected.

**All 65 classes are minted from the parsed vocabulary, not from the memberships that survive §5's
withholding.** Stated explicitly because it reads both ways otherwise: a class whose only members are
withheld still exists, so a withheld assertion row can name the class it *would* have joined, and 5c.3 can
point at every band FDA defines rather than only the bands that happened to survive adjudication. A class
that exists with zero members is the correct representation of "FDA defines this band; drugref has adjudicated
none of its members yet" — and it is visible as such, rather than absent and indistinguishable from a band
FDA never defined.

### 4.3 Membership

`class_membership(moiety_uuid, class_uuid, 'has_PK', ingest_run)`, written **only** for a tuple that is all
three of: a single resolved substance, an unqualified cell, and a pathway in the closed vocabulary.

## 5. What is withheld, and why that is the whole design

**A footnoted cell writes no `class_membership` row.** It lands in `fda_cyp_assertion` (§6) carrying the raw
cell, the footnote markers and the footnote text verbatim, and it raises an `open_question` asking a curator
to adjudicate.

Measured cost, **re-measured with the shipped parser**: **31 of 337 cells (9.2%), over 24 substances** —
38 tuples, since a qualified cell may state several pathways. The other 90.8% land clean.

> **This corrects "29 cells over 22 substances", and in the direction that matters.** The design round's
> probe detected a footnote only at the very end of a name or cell, so it missed the markers sitting
> *mid-cell* — ciprofloxacin's `1A2 20`, rifampin's two `13`s. Those cells were counted as unqualified,
> which is the unsafe direction: **the undercount was of cells drugref would have promoted to membership
> while FDA had qualified them.** The shipped parser finds them.

This is the answer to the question the spike left open, and the reason is §3: ingest cannot promote
`bupropion → 2B6 sensitive substrate` to a membership without contradicting the footnote it would store
beside it, and it cannot decide *not* to without reading prose clinically. Withholding is the only option
that neither asserts nor discards. **A withheld row is not a drop and not an error — it is a worklist entry**,
which is the shape `ingest_unresolved_onc_endpoint` (db/031) already established for a different failure.

## 6. `db/039` — the migration

**`db/029`–`db/038` are all frozen**, so every change below is a new file.

1. **Two `source` CHECKs widened**, each copied verbatim from the live catalog before adding one value —
   db/031's stated discipline, because retyping either from memory is how a value goes silently missing:
   - `ingest_run_source` gains `'FDA-CYP'`; `ingest_run_writer` gains `'fda_cyp_run'`.
   - `substance_class_source` gains `'FDA-CYP'` (db/003 created it as `MED-RT`, `MeSH`; **its own comment
     says "Extend it and `_SOURCE_CANONICAL` together when a source lands"** — this is the first source to
     land since, and that instruction is being followed rather than rediscovered).
2. **`fda_cyp_assertion`** — a rebuildable projection keyed by `ingest_run.source`, holding **every** parsed
   tuple including the withheld ones: `ingest_run`, `source` (CHECK `= 'FDA-CYP'`), `row_ordinal`,
   `raw_substance`, `resolved_moiety_uuid` (nullable), `column_heading`, `raw_cell`, `system`, `pathway`,
   `role`, `potency` (nullable — transporters have no band), `class_uuid` (nullable), `footnote_markers`
   (nullable), `footnote_text` (nullable), `registry_near_name` (nullable), `disposition`. `disposition` is
   a CHECK'd closed set of **five** values: `member` · `withheld_qualified` · `unresolved_substance` ·
   `combination_regimen` · `non_drug_entity`. **§7.1 governs that vocabulary and is the reason it is five
   rather than nine** — only the last two name a category, because only those two are asserted by FDA rather
   than inferred by drugref. `registry_near_name` is curator evidence and **never coverage**; its column
   comment must say so, because a nullable text column sitting beside an unresolved row is exactly the shape
   a future reader will be tempted to count.
3. **`gap_fda_cyp_unadjudicated`** — grouped on `(source, raw_substance, column_heading, pathway)`, dropping
   only `ingest_run`, so one view row is one independently-answerable fact and its grain matches the
   `gap_key` a question is minted from. **db/017's lesson, restated because it has bitten twice:** a coarser
   grouping folds two independent facts into one question; a finer one mints two questions for one fact.
4. **One new `open_question` gap kind**, `fda_cyp_unadjudicated`, admitted by the constraint-text-guarded
   idiom db/016, db/019, db/022, db/028, db/029 and db/031 all reuse.

## 7. Name resolution — 224 of 244, and no fuzzy matching

Measured against `drugref_db038` (19,438 moieties) by exact case-insensitive `display_name` match: **223/244
resolve as parsed, 224/244 (91.8%) once `ritonavir 14, 15,` is stripped correctly.**

The 20-name residue is **five different jobs, and conflating them would under-cost the slice** — the lesson
the DrugCentral evaluation recorded when it split its own 102 unmatched into two:

| # | category | examples | why not auto-bridged |
|---|---|---|---|
| 9 | combination regimen | `atazanavir and ritonavir`; `paritaprevir and ritonavir and (ombitasvir and/or dasabuvir)` | FDA reports the role **for the regimen**; assigning it to a component is an inference FDA did not make |
| 3 | non-drug entity | `grapefruit juice`, `St. John's wort`, `tobacco (smoking)` | not moieties at all |
| 3 | enantiomer against a held racemate | `R-`/`S-venlafaxine`, `S-mephenytoin` | **`S-mephenytoin` is the classic CYP2C19 probe substrate**; treating it as `mephenytoin` asserts a stereochemistry claim FDA did not make — and §7.2 records why this one needs literature, not a rule |
| 3 | apparent synonym | `rifampin`/`rifampicin`, `glyburide`/`glibenclamide`, `peginterferon alpha-2a`/`alfa-2a` | a synonym bridge is real work with its own evidence, not a string edit — and drugref's names are UNII-derived |
| 1 | apparent metabolite | `oseltamivir carboxylate` beside drugref's `oseltamivir acid` | same |
| 1 | group term | `oral contraceptives` | names a population, not a substance |

### 7.1 The standing rule, and why ingest does not store the category above

**Ingest what is unambiguous; set aside for clinician review what is not. Err on the side of caution.**

That rule governs this slice and every source round after it. Applying it honestly costs the table above its
place in the database, which is the part worth writing down:

**The six categories are this design's reasoning, NOT a vocabulary ingest may assert.** Labelling
`R-venlafaxine` as *"enantiomer of a held racemate"* is a chemical relationship drugref inferred **from a
string prefix** — precisely the manufactured-cause defect [#122](https://github.com/cairn-ehr/drugref/issues/122)
was filed for, where a detector reported a cause it had not confirmed. `rifampin`/`rifampicin` looks certain
and `glycerol`/`glycerol 1,3-dimethacrylate` looked certain too, in the DrugCentral evaluation, and was two
different substances.

So the stored `disposition` records only what was **observed**, never what it was inferred to mean:

- **`combination_regimen`** and **`non_drug_entity`** are **FDA's own assertions**, not drugref's readings —
  the first from the regimen string FDA wrote, the second from FDA's pinned five-substance sentence. Both
  are safe to store as categories because the source states them.
- **Everything else is one disposition, `unresolved_substance`**, whatever this design suspects the reason
  to be. Six shades of "drugref did not resolve this" collapse to the one fact drugref actually established.

**Near-name candidates are carried as EVIDENCE, never as a resolution**: a nullable
`registry_near_name` column holding what a stated, mechanical prefix rule found, with the rule named beside
it. It exists so a curator does not re-derive the search, and it is **explicitly not coverage** — a row with
a near name is exactly as unresolved as one without, and **no count may ever be quoted against it**. The
DrugCentral evaluation's own warning applies unchanged: *"treat it as the shape of the problem, not a count
to quote."*

### 7.2 Enantiomers are deferred to literature research, not to a later rule

The three enantiomer names are the clearest case for the standing rule, and the one this design explicitly
refuses to settle. `S-mephenytoin` is the reference CYP2C19 probe substrate; `R-` and `S-venlafaxine` are
metabolised along measurably different routes. Whether a stereoisomer-specific FDA assertion may ever be
carried by the racemate drugref holds — and in which direction, for which pathway, and with what caveat — is
a **pharmacological question with a literature behind it**, not a naming convention.

It is therefore filed as [#128](https://github.com/cairn-ehr/drugref/issues/128) for research rather than
answered here, and **nothing in this slice or any later one may bridge an enantiomer to a racemate until that
research is done and recorded.** The three rows sit in `unresolved_substance` with their question, which is
the correct resting place for a fact drugref cannot yet assert either way.

**The question is not FDA-CYP's.** Any source naming stereoisomers meets it, and DrugCentral
([#101](https://github.com/cairn-ehr/drugref/issues/101)) is likely to — which is why #128 is scoped to the
general rule and not to this table's three rows.

**Every residue row is recorded as data and raises a question. None is guessed, and none carries a cause
drugref has not confirmed.**

**A trap worth stating because it inverts the obvious assumption: `curcumin` and `diosmin` — two of FDA's
five declared non-drugs — resolve as ordinary moieties.** Non-drug and unresolvable are independent
properties, so **the non-drug list must be FDA's own pinned five, read from its prose, never inferred from a
resolution failure.** FDA's sentence is quoted in the parser: *"St. John's wort (a dietary supplement),
curcumin (a supplement), diosmin (a supplement), tobacco (smoking) and grapefruit juice (a food)"*.

## 8. The parser fails loudly

`ingest/fda_cyp.py` is pure and does no I/O beyond reading the bytes it is handed — the architecture
invariant every parser in this project follows, and the reason the orchestrator is the only writer.

**The pathway vocabulary is closed**: `1A2 2B6 2C8 2C9 2C19 2D6 3A` · `P-gp BCRP OATP1B1 OATP1B3 OATP1B OAT1
OAT3 OCT2 MATE1 MATE2-K`. **An unrecognised token aborts the ingest** rather than being skipped or passed
through.

This is not defensiveness; it is the finding that justified the section. A lenient parse of the real page —
one that strips only trailing footnotes and accepts whatever remains — produces **69 classes instead of 65,
reporting zero errors**, and four of them are garbage minted with real immortal UUIDs. **Four bad tokens
across three cells** (rifampin's one cell contributes two, which is why the counts differ):

```
cyp:1a2 20:inhibitor:moderate            ← ciprofloxacin, footnote "20" attached to the first pathway
transporter:oatp1b1 13:inhibitor         ← rifampin, footnote "13" on both pathways
transporter:oatp1b3 13:inhibitor
transporter:oatp1b1 inhibitor:inhibitor  ← teriflunomide, per-item role phrase eaten as a pathway
```

**⇒ THE VOCABULARY IS A TRIPWIRE, NOT A FILTER, AND THE FIRST DRAFT OF THIS SECTION CONFUSED THE TWO.**
It described those three cells as ones "a closed vocabulary must **reject**", and counted the round's own
probe rejecting them as the gate working. That was backwards. **The four tokens are not bad data — they are
four legitimate tuples the probe could not parse**, and the shipped parser reads all four correctly
(ciprofloxacin → `1A2`, rifampin → `OATP1B1` + `OATP1B3`, teriflunomide → `OATP1B1`). That is the whole
415-versus-419 gap in §2.1: the round recorded the survivors of its own mis-parse as the total.

**So the correct statement of this gate is narrower and stronger.** The closed vocabulary does not discard
anything on this release — **it rejects zero tokens, and that is the passing state.** Its job is to fire when
the *grammar* is wrong, converting a silent mis-parse into a stopped ingest. A round that sees it reject
something should suspect its own parser first and the data second, because that is the way this one broke.

- **The column heading and the cell text restate the role and potency independently** (`CYP Mod INH` /
  `2D6 moderate inhibitor`). The parser cross-checks them and **fails on disagreement** rather than
  preferring one — a disagreement means the table's shape changed under an unchanged checksum.
- **The row and cell counts are asserted**: 245 data rows, 11 cells in every row. The measured
  `Counter({11: 245})` is exact today, so a ragged row is a structural change, not a parse variation.

## 9. What this slice refuses to do

**It does not create DDI pairs by joining inhibitors to substrates**, and this is a refusal rather than a
deferral. FDA describes the table as *an optional, non-exhaustive resource* for reviewing labelling, and
explicitly excludes other interaction mechanisms. Joining its columns would manufacture a combinatorial
interaction set — 20 strong CYP3A inhibitors × 40 sensitive CYP3A substrates is 800 pairs — that **no source
asserts**, carrying drugref's own provenance. A pair still requires an SPL assertion or a curated clinical
source before it may enter `class_contraindication` or the curated overlay.

It also does not touch `curated_*`, `class_contraindication`, `ddi_candidate_pair`, or any read path. **This
slice adds classification membership and nothing else.**

## 10. Tests, and the fixture rule

TDD throughout: the failing test first. Two modules — `tests/test_fda_cyp_parser.py` (pure, no DB) and
`tests/test_fda_cyp_run.py` (DB-gated).

**The fixture is extracted verbatim from the live page, never hand-written** — the repo's standing rule,
whose cost is on record: the last hand-written fixture invented an `INN_ID`, a CAS and a UNII. It carries
every trap this design was derived from, because a fixture of clean rows would pass a parser with all four
garbage classes in it:

- the three cells whose shape defeats a naive parser and which the shipped one must read *correctly*
  (ciprofloxacin, rifampin — two pathways in one cell — and teriflunomide);
- Pirtobrutinib's `BCRP and P-gp transporters inhibitor` and rifampin's `1A2, 2B6; 2C8; 2C9 moderate
  inducer`, the trailing-noun and mixed-separator cells;
- both negating-footnote rows (bupropion, rolapitant);
- `ritonavir 14, 15,` and the cell-level and letter footnotes (conivaptan, cenobamate);
- the combination regimens, the five non-drug entries, the enantiomers and `oral contraceptives`;
- `aprepitant`, the one substance on two rows.

Named tests the design owes, each pinning a decision rather than an implementation:

- a footnoted cell writes **no** membership and **does** write an assertion row and a question;
- `bupropion`'s `2B6 sensitive substrate` is absent from `class_membership` — the §3 case, pinned directly;
- an unknown pathway token **raises**, and the transaction leaves no partial run;
- a column/cell role disagreement **raises**;
- `OATP1B` mints its own class and **does not** expand to `OATP1B1` + `OATP1B3`;
- `S-mephenytoin` resolves to nothing and is recorded as `unresolved_substance` — **not** to `mephenytoin`,
  and **not** under any disposition naming it an enantiomer (§7.1: the disposition records what was observed,
  never what this design suspects it means);
- a row carrying a `registry_near_name` is counted as **unresolved**, identically to one without — the test
  exists because the column's whole risk is being read as coverage;
- `curcumin` resolves as a moiety **and** is still `non_drug_entity` (the independence in §7);
- a second run at the same checksum is idempotent and rebuilds rather than duplicates.

## 11. What must be measured, and what must not move

On a scratch database built from `drugref_db038` by the documented `CREATE DATABASE ... TEMPLATE` +
`drugref migrate` path — the workflow re-tested rather than assumed, for the sixth round running:

**Measured and recorded:** classes minted (**expected 65**) · memberships written · assertion rows by
`disposition` · questions raised · substances resolved (**expected 224/244**) · wall-clock.

**Two of those are predictions and one deliberately is not.** 65 and 224/244 were measured on the real bytes
during this design and are stated as expectations a test may pin. **Memberships written is NOT predicted
here**: 419 is the parsed-tuple count *before* withholding and before resolution, so the written figure is
419 minus the 38 tuples from 31 qualified cells and minus those from 20 unresolved substances — an arithmetic
this design has not performed, because the two exclusions overlap (grapefruit juice is both footnoted and
unresolvable) and guessing at the overlap is how a figure that looks measured enters the record. **Measure
it; do not derive it here.**

**Must not move — and the check is BEFORE/AFTER ON ONE DATABASE, not against a number written here:**
`substance_moiety` · `ddi_candidate_pair` · `gap_uncurated_interaction_rule` ·
`gap_uncurated_condition_contradiction` · every `curated_*` count. Read each on the scratch database, run
the ingest, read each again, assert equality. This slice adds no interaction content and no condition
content, so **none of them has licence to move** — the spike's own §7 gate, and the same discipline 5c.4
used.

> **⇒ AN EARLIER DRAFT WROTE THE ABSOLUTE VALUES HERE, AND THREE OF THE FOUR WERE FROM THE WRONG DATABASE.**
> It said `ddi_candidate_pair` **21,664** — a figure measured on `drugref_policy` and `drugref_5c4`, two
> earlier databases. **`drugref_db038`, the current measurement database, holds 21,877.** Task 6's
> implementer hit the mismatch and chased it down; it is drift between databases, not a defect this slice
> introduced. The lesson is the one this document has now recorded four times in four different guises: a
> figure carries its context, and lifting it out of that context makes it wrong without making it look
> wrong. **An invariance claim must be checked as an invariance — the same query, the same database, either
> side of the change — never against a constant transcribed from somewhere else.** Absolute values belong
> in the measured-results record, where the database they came from is named beside them.

**Per-source rebuild safety:** clearing `FDA-CYP` must delete no MED-RT or MeSH class, pinned by a test
rather than argued. `class_membership` has no `source` column of its own, so the clear is scoped through
`ingest_run` — the mechanism, and the failure mode, that `db.clear_source_tables`'s `match=` keeps in one
place (#43).

## 12. Rule 6

**Discharged by the spike and re-confirmed here, not re-litigated.** FDA states that unless otherwise noted,
text and graphics on `fda.gov` are public domain (<https://www.fda.gov/about-fda/about-website/website-policies>),
and asks downstream users to record the copy date and link the live source because pages are updated.

This slice bundles **only FDA-authored table content** — substance names, pathway/role/potency cells and
FDA's own footnote prose. It copies nothing from a third party, so the column-level exclusion discipline the
spike had to apply to DIRIL does not arise here. The retrieval timestamp, `dateModified` and SHA-256 are
recorded on `ingest_run`, which is what FDA's request asks for.

## 13. Release identity — a correction to the spike

The spike says to pin fetch time and checksum *"because the HTML has no release identifier"*. **It has one.**
The page carries `dateModified` in its JSON-LD, mirrored in `og:updated_time` and `article:modified_time`:

```
Fri, 05/29/2026 - 14:00
```

`upstream_release` is therefore **`2026-05-29T14:00`**, normalised from that string, with the SHA-256 in
`source_checksum`. The distinction matters and is not cosmetic: **fetch time records when drugref looked;
`dateModified` records when FDA changed the content.** Only the second can tell a re-fetch of unchanged
material from a genuine revision, which is precisely the question `check_release_agreement` and every
per-source rebuild ask.

**The fallback is stated because the field is not contractual:** if a future fetch carries no `dateModified`,
the ingest **fails and names the missing field** rather than silently substituting fetch time — a substitution
would put a value with different meaning in the same column, and this project has already lost rounds to one
field carrying two meanings.

## 14. Risks

- **The page is unversioned and can change under drugref's feet.** Mitigated, not solved: checksum +
  `dateModified` + asserted row/cell counts mean a change is *detected loudly*, not absorbed. §8's count
  assertions are the mechanism.
- **65 classes with no hierarchy may be awkward for 5c.3 to point at.** Accepted deliberately (§4.2):
  inventing a hierarchy now, before any consumer exists, is how a structure gets frozen wrong. 5c.3 can
  propose one with a real requirement behind it.
- **The 8.6% withheld could grow** if FDA footnotes more rows. That is the correct direction — more
  qualification means more curator attention, not more silent assertion — but it is worth watching, and the
  measured figure is recorded so a future round can see the drift.
- **`aprepitant` on two rows** is handled today, but the design assumes a substance's rows are independent.
  If FDA ever splits one substance's roles across rows *with conflicting potencies*, the cross-check in §8
  will fire rather than pick one. That is the intended behaviour, and it will look like a parser failure to
  whoever sees it first — which is why it is written down here.

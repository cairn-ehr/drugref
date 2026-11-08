# drugref — SPL class mentions: what labels say about a class

**Design spec, 2026-11-08.** The class half that slice 5c.3 deferred
([5c.3 ingest design](2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md) §1), now feeding the
[class profiles](2026-11-07-drugref-class-profiles-design.md). Marks: **(owner)** = decided by the owner in the
2026-11-08 brainstorm; **(designer's call)** = settled by the designer, open to the owner's correction;
everything else is left to the builder (§9).

## 1. Intent

**The point is to lighten what a clinician must memorise (owner).** Where several drugs share an effect or a
property, drugref should state it **once, under the class's name**, instead of drug by drug. Class profiles
(the 2026-11-07 slice) do this today only by **observation** — "at least 75% of the direct members carry X".
No source we hold states things *about a class*, so every condition property is observed and the only
asserted interaction properties come from MED-RT/ONCHIGH class rules.

Drug labels state class-level facts constantly, but in a particular shape: a label is about **its own drug**
and names a **class as the other party** — lisinopril's label: *"avoid potassium-sparing diuretics"*;
tizanidine's: *"strong CYP1A2 inhibitors … contraindicated"*. Each such mention is the label saying something
about the named class as a whole.

**This round: the named class collects what labels say about it (owner).** The profile of *Potassium-sparing
diuretics* gains "lisinopril's label says *avoid*", "spironolactone's … ", with the label's own advice level.

## 2. Goals, non-goals, anti-goals

**Goals**

1. Recognise class names in SPL section `34073-7` DRUG INTERACTIONS, against the class vocabulary drugref
   already holds, in the forms labels actually write them (§4.2).
2. **Read the label's own advice level** for each class mention (owner, reversing 5c.3's "no relation
   extraction" for this grain — §5) and store it as machine-read evidence.
3. Show, on each class's profile, what labels say about it: which labelling drugs, at which advice level, how
   many labels, with the citation and (where the existing budget bought one) the quoted window.
4. Measure everything before it ships, as every slice here does (§7).

**Non-goals (this round)**

- **The labelling drug's class summarising across its members' labels** ("most ACE-inhibitor labels name
  potassium-sparing diuretics"). The owner ranked it second; it is the obvious next round and §5's advice
  reader is built standalone so it can reuse it.
- **Reading advice for the existing ~30,000 drug × drug pairs.** Same reuse, later round.
- **The prescribing-alert path.** Label-read statements stay off `exact_ddi_pair`, `ddi_candidate_pair` and
  `curated_ddi_pair`. Whether any class-profile answer reaches alerts is #204, still the owner's call.
- **Signed releases.** Not a `signature_target_kind` this round (cf. #98).
- MED-RT's PK axis as a vocabulary (#155: not a drug-class vocabulary, excluded as class profiles exclude it).
- An AI/LLM reader (§5.1).

**Anti-goals — it fails even if it "works" when:**

- **It under-warns.** A *contraindicated* sentence read as *monitor*, or anything read as "no meaningful
  interaction" that the label does not say in so many words. Under-warning is the harm direction (`db/033`).
- **It manufactures class statements out of ordinary English** — the PK axis's *Clearance* / *Half-Life*
  failure (#155), now with an advice level attached to make it look authoritative.
- **It flattens the strength band** ("strong" vs "moderate or weak" CYP1A2 inhibitors) into one class and so
  over-warns — alert fatigue is the opposite of lightening the clinician's load (#102).
- **It changes what already ships.** The drug × drug ingest's tables, views and figures stay as they are (§4.4).
- **It reads as a ruling.** A machine-read advice level must always be shown as *label says, machine-read, not
  confirmed*; "not confirmed" stays "not confirmed".

## 3. What exists to build on

- `ingest/spl.py`, `spl_run.py` (orchestrator, sole writer), `spl_match.py` (pure matcher: exact,
  case-insensitive, contiguous, whole-token, longest-match-wins, `fold`-normalised; `KIND_CLASS` already
  defined for `tools/spl_class_vocabulary_delta.py`), `spl_quote.py` (the rule-6 quote budget), `spl_subject.py`.
- `db/051`: `spl_wording` (digest only, **no prose column, ever**), `spl_label`, `spl_label_subject`,
  `spl_wording_quote` (25% budget), `spl_entity_occurrence` (moiety spans), views `spl_ddi_evidence`,
  `spl_ddi_pair`. Its comments say class occurrences are absent "until issue 155 is answered" — this round
  answers it by exclusion.
- Class vocabulary: `substance_class` (MeSH, MED-RT, FDA-CYP; `FDA-CYP` names are source-tagged and stored
  **band-in-the-middle**: `CYP1A2 strong inhibitor [FDA-CYP]`), `class_membership`, `class_parent`.
- Class profiles (`db/058`–`db/065`): `class_asserted_property` (asserted route; already maps MED-RT's
  `class_contraindication` *drug X → class C* to "C interacts with X"), `class_property`, the
  `unconfirmed_class_property` worklist, `class_profile_read.py`, `cli_class_profile.py`.
- Curated overlay: `curated_property_ruling` (`db/058`; subject moiety → `interacts_with` → object class,
  verdict `holds` / `does_not_hold` / `cannot_establish`) is the class profiles' own ruling table and has no
  axis limit. (`curated_interaction` is **not** usable: its `relationship` is a foreign key into `ci_axis`,
  which holds only MED-RT's three axes, and widening `ci_axis` widens the alert path's class expansion.)
- Measurements to reuse rather than redo: PROJECT-NOTES § "The 5c.3 SPL measurement round" (class yield per
  axis, 32.3% of class occurrences name an EMPTY class; the band appears in 15.5% of wordings but stored names
  match only 1/7 of them, because of word order).

## 4. Recognising class mentions

### 4.1 Vocabulary (designer's call)

MeSH, MED-RT except the PK axis, and FDA-CYP. MED-RT's axis tag (`[MoA]`, `[EPC]`) is stripped before matching,
exactly as `tools/spl_class_vocabulary_delta.py` does. **Empty classes (no members) are still matched and
stored** — a label saying "avoid MAO inhibitors" is a statement about that class even if drugref tags no
members to it — but every count in §7 splits empty from populated, per #155's lesson that "does drugref hold
this name" is the wrong question.

### 4.2 Label-form names (designer's call)

Labels and drugref spell classes differently. The builder generates **additional match keys** per class, as
pure functions of what the class already carries, never hand-typed lists:

- **FDA-CYP word order**: from the stored (enzyme, band, role) generate the label orders — `strong CYP1A2
  inhibitor(s)`, `CYP1A2 inhibitor(s), strong` is not needed unless measured. Measured motivation: the band
  appears ~7× more often in prose than the stored names match.
- **Plurals** where the stored name is singular (`Diuretic [APC]` → `diuretics`), and the reverse.
- Anything else only if §7's measurement shows it pays; each added key form is reported with its yield.

The matcher rule itself **stays the shipped one** (contiguous, longest-match-wins). The known miss *"strong
cytochrome P450 1A2 (CYP1A2) inhibitors"* is **counted and reported**, not papered over with a skipping matcher.

### 4.3 One name, several classes (designer's call)

`Diuretics` (MeSH) and `Diuretic [APC]` (MED-RT) fold to one string with no cross-source class identity. As
with ambiguous moieties in `db/051`: **every class the span folds to gets a row, flagged ambiguous, none is
chosen.** The profile of each shows the statement.

### 4.4 A separate pass; drug × drug unchanged (designer's call, on the owner's "changes nothing that ships")

Class matching runs as its **own pass** over the same wording, with its own vocabulary. The moiety pass, its
output, `spl_ddi_pair`'s figures and `spl_checks`' floors are untouched. The known overlap — a moiety span
inside a class span, e.g. `potassium` in *potassium-sparing diuretics*; measured at 11,169 spans in August — is
**measured and reported** this round (§7), not corrected; correcting it changes the shipped drug × drug yield
and is its own decision.

## 5. Reading the label's advice (owner: read it)

### 5.1 Method (designer's call)

A **pure, deterministic phrase reader** in its own module (`ingest/spl_advice.py` or similar, no DB), using
fixed, inspectable phrase rules **derived from the corpus and measured**, the way `spl_suppress` derived the
negative vocabulary. No AI model: the result is reproducible from the same release and adds no dependency or
licence question. The reader takes (normalised wording text, mention span) and returns a reading; it knows
nothing about classes, so the drug × drug grain can reuse it later.

### 5.2 Scope and levels

- **Scope: the sentence containing the mention.** Sentence splitting must survive label formatting (numbered
  subsections `7.1`, `e.g.`, bullet runs, headings without full stops); the builder measures its failure rate.
- **Headings and table rows (designer's call).** PLR-format labels often name the class in a subsection or
  table-row heading (*"7.2 Potassium-Sparing Diuretics"*) and put the advice in the following text or an
  *Intervention* cell. Read sentence-only, those come out `not_read`. The builder first **measures** how often
  a class mention sits in a heading; if it is material, the reader may also take the **block under the
  heading** as scope, recorded with a `scope` value (`sentence` / `heading_block`) so the two are counted,
  sampled (§7, its own stratum) and gated separately. A heading-block reading never overrides a sentence
  reading of the same mention.
- **Levels**, strongest first: `contraindicated`, `avoid`, `adjust_dose`, `monitor`, `no_meaningful_interaction`,
  and `not_read`. The builder may split or merge levels only on measured grounds and must record why.
- **Strongest wins, flagged mixed (owner-approved guess).** A sentence carrying two levels records the stronger
  and sets a `mixed` flag.
- **`not_read` rather than a guess.** No recognised phrase → `not_read`. Never default to `monitor`.
- **`no_meaningful_interaction` only on explicit phrasing** ("no clinically significant interaction",
  "no dose adjustment is needed"), and **never when any stronger level is also in the sentence**. A wrong
  "safe" reading is the worst error this round can make (§2 anti-goals).
- Negation and conditionals ("if … is necessary, reduce the dose") are handled by the phrase rules or fall to
  `not_read`; the builder documents which.

### 5.3 A label naming its own drug's class (designer's call)

*"Avoid use with other NSAIDs"* on an NSAID's label is real advice; *"like other ACE inhibitors, lisinopril
may cause …"* is not an interaction at all. Both are kept, **flagged `subject_in_class`** (the labelling drug is
a direct member of the named class), counted separately in §7, and shown on the profile as such. They are
never promoted by themselves (§6.3: nothing machine-read is) — the same treatment `db/051` gives
drug × drug self-pairs (kept, counted, never silently promoted).

### 5.4 What a reading is

**Machine-read evidence, not an assertion and not a ruling.** Every surface shows it as *label says
\<level\> (machine-read, not confirmed)*. It carries the rule that fired (an identifier from the phrase
vocabulary) and the sentence offsets, so a reviewer can see *why* it was read that way.

## 6. Storage and read path

### 6.1 Rule 6 (designer's call)

Unchanged determination (#154): **facts and offsets only, plus the existing quoted windows.** A class span, an
advice level, a rule id and sentence offsets are derived facts — clear under either publisher's reading. **No
new quoting this round**: a class mention shows the `spl_wording_quote` window that already covers it, if
any; the share covered is measured (§7). Widening the quote budget's spend to class spans would change the
quotes that already ship. `spl_wording` keeps no prose column.

### 6.2 Schema (one new migration, `db/066`; shape left to the builder within these rules)

- A class-occurrence table beside `spl_entity_occurrence`: (run, source, text_key, char_start, char_end,
  class_uuid, match_ambiguous, the key form that matched §4.2), keyed like its sibling.
- The advice reading, per occurrence: level, mixed flag, rule id, sentence start/end. (On the occurrence row or
  a sibling table — builder's choice; the reading must be rebuildable with the run.)
- An **evidence-grain view** (one row per labelling drug × class × citing label, with level, citation, quote
  where present) and a **statement-grain view** (one row per labelling drug × class: label count, wording
  count, strongest level, level distribution). **The grain is in the name**, per `db/051` §7's rule.
- Per-source rebuild: the new tables are delete-and-rebuild with the SPL run, as every projection.
- `db/051`'s comments saying class occurrences are absent get corrected **by comment in the new migration**,
  never by editing `db/051`.

### 6.3 Class profiles

Each class profile gains a **"What labels say about this class"** section: per labelling drug, the strongest
level, the label count, the mixed flag, and a citation. Shown on the class's profile **and on every class the
span was ambiguous with** (§4.3). `no_meaningful_interaction` and `not_read` statements are listed (they are
information) but are visibly not interactions.

**Separate until confirmed (owner, 2026-11-08).** A machine-read label statement does **not** enter
`class_asserted_property`, so members and subclass-only drugs do not inherit it. It stays in its own "What labels
say" section, shown as *machine-read, not confirmed*. Only a curator's `holds` ruling (§6.4) makes it an
asserted class property — and that path already exists. The reason: a misread must not spread to every member.

### 6.4 Curation

A curator confirming or rejecting *drug X × class C* writes a `curated_property_ruling` (subject moiety,
`interacts_with`, object class) — **designer's call**, because it is axis-free and stays off the alert path
(§3). Its question is cited through the shared guard (`questions.require_question_for`), so a new question kind
and its candidate function join `curation_property.answerable_questions`. A worklist of unconfirmed label
class statements, ordered strongest level first, is in scope; its gap view is the builder's to design within
the question-registry rules. A `holds` verdict already feeds `class_asserted_property` (`db/059`), so a
**confirmed** statement becomes asserted with no new code. A `does_not_hold` verdict leaves the label
statement visible in "What labels say", **marked *curator ruled: does not hold***, never hidden — the
confirmed-exception display class profiles already use (designer's call).

## 7. Measurement (before anything ships)

A measurement record `docs/superpowers/specs/2026-11-xx-drugref-spl-class-mentions-results.md`, on a
database built from nothing (chain + SPL), reporting at least:

- Class occurrences, **per wording** (not per label — the 2.5× trap), per axis, split **empty vs populated**;
  distinct (labelling drug × class) statements; how many classes gain at least one statement.
- Yield of each §4.2 key form; the parenthetical-form miss count; the moiety-inside-class overlap count (§4.4).
- Advice level distribution, mixed rate, `not_read` rate; sentence-splitter failure rate.
- **A hand-read precision sample, stratified by level** (at least 50 per level, more for
  `no_meaningful_interaction` and `contraindicated`), pasted in the record for the owner's clinical read, with
  the designer's own reading of each beside it.
- **Release gates (owner, 2026-11-08: merge on the gates; the owner's clinical read of the sample follows
  the merge, and nothing reaches alerts meanwhile, #204):**
  - `no_meaningful_interaction` ships only if the sample shows **zero** readings where the label in fact
    advises anything; otherwise that level is withheld (mapped to `not_read`) and the reason recorded.
  - **Zero** `contraindicated` sentences read lower than `contraindicated`.
  - **≥ 90% agreement** between the machine reading and the designer's hand reading for each of `avoid`,
    `adjust_dose`, `monitor` (and per `scope`, §5.2). A level that cannot reach it after tuning is withheld
    the same way, with its measured figure recorded. Tuning stops when every shipped level clears its gate.
- The drug × drug figures before and after: **identical** (§4.4).

## 8. Documentation

- **Revise the living record** `docs-site/docs/decisions/evidence-is-not-an-assertion.md` in place: SPL drug ×
  drug co-mentions are still evidence only; class mentions now carry a machine-read advice level, which is
  still evidence and not an assertion. State why the line moved (owner, 2026-11-08: lightening memory needs the
  label's advice, not just its mention).
- ROADMAP (new entry under 5c.3's class half and § "Class profiles"' "feeds from next"), PROJECT-NOTES (a round
  section, the suite count), HANDOVER.
- Module docstrings at the repo's usual standard: why, not just what, readable by a junior contributor.

## 9. Left to the builder

Table and column names; whether the reading lives on the occurrence row; the exact phrase vocabulary and its
derivation tool; the sentence splitter; the key-form generators beyond §4.2's two; the worklist's question
kind and gap view; CLI surface (extend `drugref ingest spl` and `drugref class-profile`, no new top-level
command unless needed); fixture design (a wording where each level, mixed, `not_read`, ambiguity and the
moiety-inside-class overlap all occur). TDD throughout; files under ~500 lines (`spl_match.py` and
`spl_run.py` are already near it — add modules rather than grow them).

## 10. Owner decisions taken in review (2026-11-08)

1. Machine-read class statements stay separate until a curator confirms them (§6.3).
2. Merge on the §7 gates; the owner's clinical read of the sample follows the merge (§7).

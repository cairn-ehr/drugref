# drugref — drug effects on laboratory values: model, read view and gap view

**Date:** 2026-11-27 · **Issue:** [#294](https://github.com/cairn-ehr/drugref/issues/294) (requested by
ReportTriage) · **Status:** design, step 2 of 4. Step 1 is the
[source evaluation](2026-11-27-drugref-lab-effects-source-evaluation.md) (its figures are not repeated here);
step 3 is the first openFDA label ingest; step 4 is review and signing through the reviewer app.

## 1. Why

ReportTriage puts the most urgent incoming laboratory reports at the front of a clinician's queue. Part of how it
judges urgency is the patient's medicines: a drug that **causes** the abnormality (started recently), one that
makes it **more dangerous** (low potassium on digoxin), one with a **monitoring protocol** (clozapine and
neutrophils), and, by owner ruling, one that **interferes** with the result (a false reading, or a reference
range that no longer applies; consumer side ReportTriage#36). Today those lists are hand-written in
ReportTriage's rule file and marked illustrative. The knowledge belongs in drugref: versioned, sourced, reviewed
and signed.

Success for this step: a schema and two views ReportTriage can code against, which step 3 fills from labels
without changing their shape, and which a curator can correct and extend from day one.

## 2. Decisions already made (owner, 2026-11-27)

1. **Record and grade; the consumer filters.** Label statements are recorded with a strength grade taken from
   the section they appear in. Curated salience is the later refinement.
2. **Four roles:** `causes`, `more_dangerous`, `monitoring`, `interferes`.
3. **Analytes are drugref's own concepts, derived from MeSH, with no LOINC codes in drugref tables.** The
   consumer maps them to LOINC.
4. **Statements key to an analyte and a direction** (option 2 of this round's discussion), not to a MeSH
   condition. The condition the label named is kept as *what the label said*. Reason: two test-set rows have no
   MeSH condition to key to. *INR high* exists in MeSH only as a test (D019934), and the only condition for a
   creatinine rise is *Acute Kidney Injury* (D058186), which would record "trimethoprim causes kidney injury"
   when it blocks creatinine secretion instead.
5. **The role is machine-read from the label and marked unconfirmed** (option a). When no role rule fires the
   role is `not_read`, **never a default `causes`**. Reason: there is no real-world deployment yet, and this lets
   testing start sooner; the rules change with experience. The clinical risk this accepts: ReportTriage only
   gates `causes` on how recently the drug was started, so a `more_dangerous` statement misread as `causes`
   would not raise a long-standing digoxin patient's low potassium. The precision sample measures role errors
   separately (§9).
6. **A curator table for exceptions and special rulings that cannot be derived** (owner, 2026-11-27): confirm or
   refute a machine statement, correct its role, add what no label gives (biotin interference, a time to onset,
   a co-medication condition), and carve a member out of a class statement.

## 3. Goals, non-goals, anti-goals

**Goals.**
- Answer ReportTriage's two questions: *for this analyte and direction, which substances and classes are
  relevant and in which role* (the read view, §7), and *is this moiety in this class, including subclasses*
  (`class_subtree`, unchanged).
- Tell "drugref has no statement" apart from "drugref says there is no effect" (the coverage view, §8).
- Every read-view row says why it is there (curated ruling or label evidence, section, label count) and which
  release it came from.

**Non-goals (this design).**
- Thresholds and urgency arithmetic: they stay in ReportTriage.
- LOINC codes, or any crosswalk to them (owner: left open, separately noticed if ever).
- MED-RT `CI_with` / physiologic-effect seeds, LiverTox, DILIrank: later sources. The model has room for them
  (`basis`, §7) but nothing reads them yet.
- Combination labels: the first ingest skips them and counts the skips; an attribution rule is step 3's
  decision (source evaluation §5.3).
- Reviewer-app screens: step 4.
- Australian-only products (Slice 8).

**Anti-goals: a failure even if it works.**
- **"No statement" reaching a consumer as reassurance.** The coverage view never returns a state that reads as
  "no effect" unless a curator ruled `does_not_hold`.
- **A machine reading looking like a confirmed fact.** Every label-derived row carries `confirmed = false`,
  in every view, not only in a comment.
- **LOINC-derived content in drugref tables**, or any analyte list built from LOINC material (field-of-use
  clause; source evaluation §2.1).
- **Bulk label prose.** Facts, offsets and short quote windows under the existing SPL quote budget only.

## 4. Constraints

- **Licensing (rule 6):** MeSH (analytes, conditions), openFDA/DailyMed labels (statements) are cleared in
  `NOTICE`. MedDRA-coded data (OnSIDES, SIDER) stays out.
- **Append-only curated content** with supersession, the deferred single-live guard and signature registration,
  exactly as `curated_property_ruling` (`db/058`) does.
- **Ingest parsers pure; orchestrators own the transaction; per-source projections are delete-and-rebuild.**
- **Applied migrations are immutable**; new numbered files from `db/089`.
- **Decision audit (#258):** a new curated table must be in the curated export the release replay reads, and in
  `current_release`'s `re-curated` drift probe (it lists each curated table by name, `db/081`).
- Modules under ~500 lines (the cap sweep test enforces it).

## 5. Approaches considered

1. **A separate lab-effect layer modelled on existing patterns (chosen).** Small authored vocabulary tables, a
   machine-read statement table on the `db/066` pattern, a curator table that is a sibling of `db/058`, and two
   views. More tables, but each says one true thing and every part has a precedent.
2. **A new relationship in `curated_property_ruling`.** Fewer tables, but the class-profile views
   (`class_asserted_property` and its neighbours) read every relationship in that table, so lab effects would
   leak into class profiles; and role, co-medication, onset and method mean nothing for the other relationships.
   Rejected.
3. **Reuse the SPL class-mention tables** with a lab vocabulary. They are tied to the drug-interactions section
   and its advice levels; mixing lab statements in breaks "each table, read unfiltered, is one true sentence"
   (the `induces` lesson, PROJECT-NOTES § Slice 5b.2). Rejected.

## 6. The model

### 6.1 `lab_analyte` — drugref's analytes (authored, INSERT-only)

One row per analyte drugref can make statements about.

| column | meaning |
|---|---|
| `analyte_code` text PK | drugref's own stable slug, e.g. `potassium`, `inr`. **Immortal**: consumers key on it; never renamed or reused |
| `mesh_ui` text NOT NULL | the MeSH descriptor it is derived from (Potassium D011188) |
| `specimen` text NOT NULL | `blood` / `urine` / `any`. Needed because interference is specimen-specific (cephalosporins and urine glucose, not blood glucose). UNIQUE (`mesh_ui`, `specimen`) |
| `display_name` text NOT NULL | for people |

**Seed (the test set):** `potassium` D011188, `sodium` D012964, `creatinine` D003404, `haemoglobin` D006454
(Hemoglobins), `inr` D019934, `neutrophils` D009504, `platelets` D001792, all `blood`. Later rounds add rows
(liver tests with LiverTox; urine glucose with the interference rules). Each `mesh_ui` is checked against the
MeSH release at ingest (`downloads/mesh/desc2026.gz`); a descriptor MeSH has deleted is refused loudly, never
dropped. A floor trigger forbids UPDATE and DELETE (the `signing_key_status_kind` pattern): analytes are a
vocabulary consumers code against.

**Direction** is a fixed vocabulary everywhere: `high`, `low`, `unspecified`. For `interferes`, it is the
direction the *reported value* moves (a false-high urine glucose is `high`); `unspecified` covers "makes the
result unreliable" and label wording with no direction (the survey's INR pattern).

### 6.2 `lab_effect_term` — the translation map from label wording (authored)

Every way a label can name an effect, mapped to (analyte, direction). This is the owner's "condition →
(analyte, direction) map", widened to test wording.

| column | meaning |
|---|---|
| `term_id` text PK | e.g. `hyperkalemia`, `serum_potassium_increased`, `bleeding` |
| `condition_mesh_ui` text NULL | the MeSH condition when the term is one (Hyperkalemia D006947); NULL for test wording. **A MeSH UI, not an FK into `condition`**: `condition` is a rebuildable projection that holds only conditions MED-RT references, and an authored table must not block its rebuild or depend on what MED-RT happens to cite |
| `analyte_code` FK | |
| `direction` | |
| `via_pathway` boolean NOT NULL | TRUE when the analyte moves only through a pathway (bleeding → haemoglobin low). Carried to the read view so the consumer can weigh it |

The phrase patterns that find each term live in Python (pure, beside `spl_advice.py`), keyed by `term_id`. A test
pins that the Python vocabulary and the table hold the same term set: the repo's "second home" pattern
(`spl_advice_level`).

### 6.3 `lab_effect_section` — the grade (authored)

The section a label statement sits in, with `section_rank` (1 = strongest): `boxed_warning` 1,
`warnings_and_cautions` 2, `warnings` 3, `precautions` 4, `drug_and_or_laboratory_test_interactions` 5,
`laboratory_tests` 6, `drug_interactions` 7, `adverse_reactions` 8, `adverse_reactions_table` 9. The order is the
survey's, with the interference section placed after precautions (*my call*: ranked last it would vanish under
any consumer filter, and it is the main home of the fourth role). A lookup table rather than a CHECK because the
views need the order; changing the grade is a one-row migration.

### 6.4 `spl_lab_effect_occurrence` — machine readings (projection, filled in step 3)

One row per term span the reader found in a label section, on the `spl_class_occurrence` pattern (`db/066`):
`ingest_run`, `source`, the section wording's digest and section, `char_start`/`char_end`, `term_id`, and the
reading:

- `role_read`: the four roles, `not_read` (no role rule fired), or `not_an_effect` (the reader recognised the
  mention as a precondition, not something the drug does: "do not use in patients with hyperkalemia"). Kept in
  the evidence, excluded from the read view. *My call*: it is the mention-level over-count the survey measured,
  and dropping it silently would hide the reader's reasoning from the precision sample.
- `role_rule`: the rule that fired; NULL exactly when `role_read = 'not_read'` (CHECK, the `db/066` shape).
- `direction_read`: from the term, or overridden by the scope ("decreased serum potassium").
- `co_medication_moiety_uuid` / `co_medication_class_uuid`, at most one, NULL when the scope names none: found by
  the shipped entity and class matchers (`spl_match`) in the same scope. This is how "INR increased in patients
  on warfarin" carries warfarin.
- `scope` (`sentence` / `heading_block`) and its offsets.

**Storage the ingest owns (step 3, constraints only):** the ingest is its own orchestrator with its **own
`ingest_run.source`** (e.g. `SPL_LAB`) and its own wording, label, subject and quote tables, so rebuilding the
DDI ingest (`source = 'SPL'`) can never delete lab readings, nor the reverse. That means widening
`ingest_run_source` and `ingest_run_writer` (last widened by `db/067`), and the `spl_*` tables' `source = 'SPL'`
CHECKs stay as they are. It reuses the pure subject resolver (`spl_subject.resolve_subject`) and the DailyMed
recovery. No prose column anywhere.

- **Quotes.** The 25% budget trigger (`spl_wording_quote_budget`, `db/051`) names its two tables, so the lab
  quote table needs its own trigger function (or a generalised one; builder's choice). **The budget is per
  section wording.** *My call:* the lab ingest stores **no quote windows for the `drug_interactions` section**,
  which the DDI ingest already quotes up to 25%; two quote tables on one section could together publish 50%.
  Evidence rows there carry offsets and the set id, so a reader can follow the label.
- **Salts.** *My call:* the subject is resolved exactly as the DDI ingest resolves it (the moiety where a moiety
  UNII resolves, the salt only where none does). No salt → base folding until #67 settles it, so a ruling on a
  base moiety does not cover a salt-subject reading; the worklist shows the salt row separately.
- **Matchers.** Whether `spl_match` and `spl_advice_scope` run unchanged on sections other than 34073-7 is for
  step 3's plan to check (not checked here).

### 6.5 `curated_lab_effect` — rulings, exceptions and additions (curated, append-only, signed)

A sibling of `curated_property_ruling`, copied on purpose (floor, deferred single-live guard, live-key and
by-question indexes, `signature_target_kind` row `curated_lab_effect/v1`).

| column | meaning |
|---|---|
| `subject_class_uuid` / `subject_moiety_uuid` | exactly one (CHECK), plus generated `subject_kind`, `subject_uuid` |
| `analyte_code`, `direction` | the object |
| `role` | one of the four. **Required**: a curator always states the role |
| `co_medication_moiety_uuid` / `co_medication_class_uuid` | at most one; generated `co_medication_kind` (`none` / `moiety` / `class`) and `co_medication_uuid` (the nil UUID for `none`), because the single-live guard refuses NULL key columns (`db/058` preamble) |
| `verdict` | `holds` / `does_not_hold` / `cannot_establish`. A class subject may not `cannot_establish` (the `db/058` rule) |
| `onset_min_days`, `onset_max_days` | nullable integers, the usual time from starting the drug to the effect; min ≤ max |
| `method` | nullable text, the assay method for `interferes` only (CHECK); no open method vocabulary exists |
| `question_uuid` | nullable FK to `open_question`: NULL for a special ruling no worklist asked for |
| `source` (`DRUGREF`), `reviewed_by`, `reviewed_against`, `reviewed_at`, `superseded_by` | as `db/058` |

**Natural key:** (`subject_kind`, `subject_uuid`, `analyte_code`, `direction`, `role`, `co_medication_kind`,
`co_medication_uuid`). One drug can hold several facts about one analyte (clozapine: `causes` and `monitoring`
for neutrophils), hence role and direction in the key.

**Evidence.** `reviewed_against` (NOT NULL) is the citation for every ruling. The reviewer's structured evidence
references (`db/045`) need a `question_uuid` (NOT NULL there), so they attach only to rulings that answer a
worklist question. A structured evidence path for unasked special rulings (biotin) is step 4's question.

**Signing** needs, besides the `signature_target_kind` row, the payload and key entries in `signing_contexts.py`
and the curated-kind lists in `releases.py`, `release_archive.py` and `replay_loader.py` (export and replay).

**What a class ruling reached (#258).** Class membership is rebuilt by every ingest, so a class ruling's reach
must be written down when the ruling is made: a `curated_lab_effect_reach` table, the sibling of
`curated_class_interaction_reach` (`db/080`), insert-only, one row per (ruling, member moiety), snapshotted again
under the new id on a correction. It is in the curated export.

**The review unit, and how corrections and exceptions work.** A curator reviews a **(moiety, analyte)** pair, not
one machine row. *My call*, because the machine's role or direction may be the very thing that is wrong:

- **Any live moiety ruling on an analyte answers every machine reading for that moiety and analyte.** Those
  readings leave the read view (they stay in the evidence view) and their worklist question closes. So a
  `not_read` reading is closed by a ruling with the real role, and a misread `causes` is replaced by a
  `more_dangerous` ruling without the curator having to file a `does_not_hold` against the machine's guess.
- **A class `holds`** adds a row for every member, through the member rule in §7.
- **A moiety `does_not_hold`** on the same key as a class `holds` carves that member out (the exception).
- **A class ruling never hides a member's label readings.** *My call*, on the repo's standing rule that fewer
  rows is the harm direction (a signature never gates a read): one class `does_not_hold` must not silence every
  member's labels. Only a moiety ruling answers a moiety's readings.

## 7. The read view: `lab_effect`

One row per (analyte, direction, subject, role, co-medication) that drugref currently states. Columns:

- `analyte_code`, `direction`, `specimen`, `via_pathway`
- `subject_kind` (`moiety` / `class`), `subject_uuid`, `subject_name`
- `reached_via_class_uuid`: set on a moiety row that exists only because a class ruling reached it (so the
  consumer can show "counted as a thiazide diuretic"); NULL on direct rows
- `role` (including `not_read` for an unread label statement), `co_medication_kind`, `co_medication_uuid`
- `basis`: `curated` or `label` (later `medrt_seed`, `livertox`)
- `confirmed`: TRUE only for a curated `holds`
- for label rows: `strongest_section`, `section_rank`, `label_count`, `stated_as` (the term ids the labels used)
- for curated rows: `curated_lab_effect_id`, `onset_min_days`, `onset_max_days`, `method`
- `release_tag`, `release_drifted` (from `current_release` by a **LEFT** join: that view has zero rows in a
  database with no published release, so both columns are NULL there rather than the read view going empty)

**Rules.** Only `holds` rulings and label readings appear; `does_not_hold` and `cannot_establish` appear in the
coverage view, not here. A (moiety, analyte) with any live moiety ruling shows only rulings; one with none shows
the machine readings (§6.5, the review unit). `unspecified` direction rows are returned under both `high` and
`low` queries, with `direction = 'unspecified'` so the consumer sees it. Class expansion uses **the class
profiles' member rule**: `class_subtree` over `class_profile_member` (`db/059`: direct members, excluding
MED-RT PK classes), so a class profile and a lab effect never disagree about who is in a class. It is
not `class_rule_member` (`db/079`), which is the interaction rules' expansion. Whether a `class_expansion_policy`
deny applies is HANDOVER's open owner question 5, and this view inherits the answer.

An evidence-grain twin, `lab_effect_label_evidence` (one row per citing label occurrence, with set id, version,
section, rule and quote), is what a clinician's "why" link reads. As in `db/066`, the grain is in the name and
`count(*)` on the evidence view is never a statement count.

## 8. The coverage view: `lab_effect_coverage`

One row per (moiety, analyte, direction) for every gated-in moiety and every analyte, with exactly one `state`:

| state | meaning | consumer reading |
|---|---|---|
| `stated` | the read view has at least one row for it (any role, either basis) | context available |
| `ruled_no_effect` | a live `does_not_hold` (moiety, or inherited from a class with no moiety `holds`) and nothing stated | **the only "no effect"** |
| `cannot_establish` | a curator looked and the sources do not settle it | unavailable context |
| `examined_nothing_found` | the latest lab ingest read at least one resolved label for this moiety and found nothing for this analyte | **unavailable context, never reassurance** |
| `not_examined` | no label for this moiety was read | unavailable context |

`examined_nothing_found` exists because a label that does not mention hyperkalaemia is not evidence of no
hyperkalaemia, but it is still worth knowing that drugref looked. The view also carries `label_count` (labels
read) so a consumer can tell one label from fifty.

## 9. The worklist and validation

- **Gap kind `unconfirmed_label_lab_effect`** (the 21st; the `db/066` DO-block widening, list copied verbatim from
  the latest migration that touched the constraint): one question per label statement with no live ruling of
  any verdict, at the review unit: one question per **(moiety, analyte)** whose label readings no live moiety
  ruling answers (§6.5). Frozen key `LABEFFECT:<moiety_uuid>:<analyte_code>`. `not_read` role readings are on it
  too: they need a curator most.
- **Step 3 validates** against #294's test set (which rows it reproduces, which it knowingly contradicts, and
  why) and a hand precision sample (n = 100, stratified by section grade, Wilson lower bounds published) that
  scores **role and direction separately** from "is it a drug effect at all", because decision 5 accepted the
  role risk on the promise of measuring it.

## 10. Build order (recommended)

1. **This round's implementation:** `lab_analyte`, `lab_effect_term`, `lab_effect_section` with seeds;
   `curated_lab_effect` with its floor, guard, reach snapshot, signing entries, curated export and replay, and
   `current_release` probe; the empty `spl_lab_effect_occurrence` and its label/subject storage; the read, evidence and coverage
   views; the gap kind. Tested with fixture rows, which is enough for ReportTriage to code against the views.
2. **Step 3:** the pure reader (term and role rules), the orchestrator, the test-set comparison and the
   precision sample.
3. **Step 4:** reviewer-app screens for `curated_lab_effect`.

## 11. Left to the builder

Migration split and numbering; module names; the exact phrase and role rules (step 3); column types beyond what
is stated; index choices; how the evidence view joins quotes; the CLI surface (a `drugref lab-effects` read
command is likely useful but not required by ReportTriage, which reads the views).

# Lab effects: model, read view and coverage view — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the schema and views ReportTriage codes against for drug effects on laboratory values (#294 step
2): analytes, the wording map, the curator table, the empty label-reading storage, the read view, the coverage
view and the worklist.

**Architecture:** Four migrations (`db/089`–`db/092`) on the existing patterns: `db/058`'s curated ruling
(append-only, single-live, signed, released, exported, replayed), `db/066`'s machine-reading occurrence table and
statement views, `db/080`'s ruling-time reach snapshot. One Python writer module and one gap-source module. No
ingest: the label tables ship empty and the views are tested on fixture rows.

**Tech Stack:** PostgreSQL (plain SQL migrations applied by `db.apply_migrations`), Python 3 + psycopg, pytest
(DB-gated tests need `DRUGREF_TEST_DSN`).

**Spec:** [`docs/superpowers/specs/2026-11-27-drugref-lab-effects-design.md`](../specs/2026-11-27-drugref-lab-effects-design.md)
— read it first; this plan does not repeat its reasons.

## Global Constraints

- All code AGPL-3.0; no new dependency. No LOINC codes or LOINC-derived content anywhere.
- Applied migrations are immutable: new files only, `db/089` onward. A migration that widens a CHECK copies the
  latest live list **verbatim** from the latest migration that set it, then extends it.
- Directions are exactly `high`, `low`, `unspecified`. Curated roles are exactly `causes`, `more_dangerous`,
  `monitoring`, `interferes`. Machine `role_read` adds `not_read` and `not_an_effect`.
- Verdicts are exactly `holds`, `does_not_hold`, `cannot_establish`; a class subject may not `cannot_establish`.
- Every label-derived row in every view carries `confirmed = false`; only a curated `holds` is `confirmed = true`.
- No prose column in any `spl_lab_*` table.
- Inline documentation a junior contributor can follow (migration preambles and `COMMENT ON` in the repo's style).
- Modules under ~500 lines (`tests/test_module_size_cap.py`). Match the hand style; do not run `ruff format`.
- Writers never commit; the caller owns the transaction.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **No published release** (a fresh or test database): `lab_effect` must still return rows, with NULL
   `release_tag` — Task 4 pins it.
2. **A class ruling on a parent class whose drugs are tagged only to a subclass**: the ruling must reach them
   through `class_subtree` — Task 2 (reach snapshot) and Task 4 (read view) pin it.
3. **A curator corrects the machine's role** (label reads `causes`, curator rules `more_dangerous`): only the
   ruling shows in `lab_effect`; the machine reading stays in the evidence view — Task 4 pins it.
4. **A label reading with `unspecified` direction**: a consumer asking for `high` and one asking for `low` both
   get it — Task 4 pins it.
5. **A corrected class ruling**: the new id gets its own reach snapshot and the superseded one keeps its own —
   Task 2 pins it.

---

## File Structure

| File | Responsibility |
|---|---|
| `db/089_lab_effect_vocabulary.sql` (create) | `lab_analyte` (+ insert-only floor), `lab_effect_section`, `lab_effect_term`, with seeds |
| `db/090_curated_lab_effect.sql` (create) | `curated_lab_effect`, `lab_effect_class_member` view, `curated_lab_effect_reach`, signature kind, `current_release` probe |
| `db/091_spl_lab_storage.sql` (create) | `ingest_run` source/writer widening; `spl_lab_label`, `spl_lab_label_subject`, `spl_lab_wording`, `spl_lab_label_section`, `spl_lab_effect_occurrence` |
| `db/092_lab_effect_views.sql` (create) | `lab_effect_label_evidence`, `lab_effect_label_statement`, `lab_effect`, `lab_effect_coverage`, `gap_unconfirmed_label_lab_effect`, gap-kind widening |
| `src/drugref/curation_lab_effect.py` (create) | writer: `record_lab_effect_ruling`, `answerable_questions`, `snapshot_reach` |
| `src/drugref/gap_sources_lab_effect.py` (create) | gap kind 21's key/rank/text SQL |
| `src/drugref/questions.py` (modify ~line 27, 46) | merge `LAB_EFFECT_GAP_SOURCES` |
| `src/drugref/provenance.py` (modify line 39) | `WRITERS` gains `spl_lab_run` |
| `src/drugref/signing_contexts.py` (modify ~90–180) | field list and natural key for `curated_lab_effect/v1` |
| `src/drugref/releases.py` (modify line 65) | `_CURATED_KINDS` gains `curated_lab_effect` |
| `src/drugref/release_archive.py` (modify ~line 30–62) | `_EXPORT_TABLES` gains the ruling and reach tables; `NOT_EXPORTED` gains the three vocabularies if their triggers make the coverage test see them |
| `src/drugref/replay_loader.py` (modify) | `_TIMED` gains the ruling; reach rows load with their ruling and are compared |
| `tests/lab_effect_world.py` (create) | row builders: an `SPL_LAB` run, a label with a subject, a wording in a section, an occurrence |
| `tests/test_lab_effect_vocabulary.py`, `tests/test_curated_lab_effect.py`, `tests/test_spl_lab_schema.py`, `tests/test_lab_effect_read.py`, `tests/test_lab_effect_coverage.py`, `tests/test_lab_effect_gap.py` (create) | one per task |
| `docs/PROJECT-NOTES.md`, `docs/ROADMAP.md`, `docs/HANDOVER.md`, `docs-site/docs/decisions/lab-effects-keyed-to-analytes.md` (+ nav) | Task 6 |

Test helpers reuse `tests/class_profile_world.py` (`run`, `moiety`, `klass`, `member`, `parent`) for the registry
side.

---

### Task 1: The vocabulary (`db/089`)

**Files:**
- Create: `db/089_lab_effect_vocabulary.sql`
- Test: `tests/test_lab_effect_vocabulary.py`

**Interfaces:**
- Produces: tables `drugref.lab_analyte(analyte_code PK, mesh_ui, specimen, display_name)`,
  `drugref.lab_effect_section(section PK, section_rank UNIQUE NOT NULL)`,
  `drugref.lab_effect_term(term_id PK, condition_mesh_ui NULL, analyte_code FK, direction, via_pathway NOT NULL)`.

- [ ] **Step 1: Write the failing tests** (DB-gated, `conn` fixture)

```python
def test_the_seeded_analytes_are_the_test_set(conn):
    assert conn.execute("SELECT analyte_code, mesh_ui, specimen FROM drugref.lab_analyte "
                        "ORDER BY 1").fetchall() == [
        ("creatinine", "D003404", "blood"), ("haemoglobin", "D006454", "blood"),
        ("inr", "D019934", "blood"), ("neutrophils", "D009504", "blood"),
        ("platelets", "D001792", "blood"), ("potassium", "D011188", "blood"),
        ("sodium", "D012964", "blood")]

def test_an_analyte_can_never_be_renamed_or_deleted(conn):
    # UPDATE and DELETE each raise (forbid_any_rewrite), checked separately
    ...

def test_mesh_ui_and_specimen_together_are_unique(conn):
    # inserting ('potassium_dup', 'D011188', 'blood', ...) raises UniqueViolation;
    # ('potassium_urine', 'D011188', 'urine', ...) succeeds

def test_the_section_grade_order(conn):
    assert [s for (s,) in conn.execute(
        "SELECT section FROM drugref.lab_effect_section ORDER BY section_rank")] == [
        "boxed_warning", "warnings_and_cautions", "warnings", "precautions",
        "drug_and_or_laboratory_test_interactions", "laboratory_tests",
        "drug_interactions", "adverse_reactions", "adverse_reactions_table"]

def test_the_seeded_terms(conn):
    assert conn.execute("SELECT term_id, condition_mesh_ui, analyte_code, direction, via_pathway "
                        "FROM drugref.lab_effect_term ORDER BY 1").fetchall() == [
        ("acute_kidney_injury", "D058186", "creatinine", "high", False),
        ("agranulocytosis", "D000380", "neutrophils", "low", False),
        ("bleeding", "D006470", "haemoglobin", "low", True),
        ("hyperkalemia", "D006947", "potassium", "high", False),
        ("hypokalemia", "D007008", "potassium", "low", False),
        ("hyponatremia", "D007010", "sodium", "low", False),
        ("inr_increased", None, "inr", "high", False),
        ("neutropenia", "D009503", "neutrophils", "low", False),
        ("platelet_count_decreased", None, "platelets", "low", False),
        ("serum_creatinine_increased", None, "creatinine", "high", False),
        ("serum_potassium_decreased", None, "potassium", "low", False),
        ("serum_potassium_increased", None, "potassium", "high", False),
        ("siadh", "D007177", "sodium", "low", False),
        ("thrombocytopenia", "D013921", "platelets", "low", False)]

def test_a_term_direction_outside_the_vocabulary_is_refused(conn):
    # direction 'up' -> CheckViolation; specimen 'csf' on lab_analyte -> CheckViolation
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_lab_effect_vocabulary.py -v`
Expected: FAIL (`relation "drugref.lab_analyte" does not exist`).

- [ ] **Step 3: Write `db/089_lab_effect_vocabulary.sql`**

Preamble says: drugref's own analytes, derived from MeSH descriptors, never from LOINC (spec §2, §6.1); the
`analyte_code` is immortal, hence the floor. `specimen` CHECK `('blood','urine','any')`, `direction` CHECK
`('high','low','unspecified')` on `lab_effect_term`. Floor trigger named `lab_analyte_insert_only`, `BEFORE UPDATE
OR DELETE ... EXECUTE FUNCTION drugref.forbid_any_rewrite()` (as `db/080`). `condition_mesh_ui` is a MeSH UI text,
**not** an FK into `drugref.condition` (say why in a comment: spec §6.2). `COMMENT ON` each table. Seeds with
`ON CONFLICT DO NOTHING`. The two `display_name`s worth fixing: `haemoglobin` → `Haemoglobin`, `inr` →
`INR (international normalised ratio)`; others capitalised codes.

- [ ] **Step 4: Verify every seeded MeSH UI exists in MeSH 2026** (one-off, not a test: `downloads/` is gitignored)

Run a short script over `downloads/mesh/desc2026.gz` printing each seeded UI's descriptor name.
Expected: all 7 analyte UIs and 10 condition UIs found, names matching the spec (Potassium, Hemoglobins,
International Normalized Ratio, ...).

- [ ] **Step 5: Run the tests, then the release-export coverage test**

Run: `uv run pytest tests/test_lab_effect_vocabulary.py tests/test_release_export_coverage.py -v`
Expected: the new tests PASS. If the coverage test now flags `lab_analyte` (it finds `*_insert_only` triggers),
add `"lab_analyte": "a vocabulary db/089 seeds"` to `release_archive.NOT_EXPORTED` and re-run: PASS.

- [ ] **Step 6: Commit** `feat: lab-effect vocabulary — analytes, sections, terms (db/089, #294)`

---

### Task 2: The curator table and its reach (`db/090`, `curation_lab_effect.py`)

**Files:**
- Create: `db/090_curated_lab_effect.sql`, `src/drugref/curation_lab_effect.py`
- Modify: `src/drugref/signing_contexts.py`, `src/drugref/releases.py`, `src/drugref/release_archive.py`,
  `src/drugref/replay_loader.py`
- Test: `tests/test_curated_lab_effect.py`

**Interfaces:**
- Consumes: `lab_analyte` (Task 1); `overlay.supersede`, `questions.require_question_for` (existing, used as
  `curation_property.py` uses them).
- Produces:
  - table `drugref.curated_lab_effect` — columns in this order: `curated_lab_effect_id`, `subject_class_uuid`,
    `subject_moiety_uuid`, `analyte_code`, `direction`, `role`, `co_medication_moiety_uuid`,
    `co_medication_class_uuid`, `subject_kind` (gen), `subject_uuid` (gen), `co_medication_kind` (gen:
    `none`/`moiety`/`class`), `co_medication_uuid` (gen: nil UUID for `none`), `verdict`, `onset_min_days`,
    `onset_max_days`, `method`, `question_uuid`, `source`, `reviewed_by`, `reviewed_against`, `reviewed_at`,
    `superseded_by`.
  - view `drugref.lab_effect_class_member(class_uuid, moiety_uuid)` = DISTINCT `class_subtree.root_uuid` ×
    `class_profile_member.moiety_uuid` joined on `class_subtree.class_uuid`.
  - table `drugref.curated_lab_effect_reach(curated_lab_effect_id PK FK, members uuid[] NOT NULL, captured text
    NOT NULL CHECK = 'at_ruling', recorded_at timestamptz DEFAULT now())`, insert-only.
  - `curation_lab_effect.record_lab_effect_ruling(conn, *, subject_class_uuid=None, subject_moiety_uuid=None,
    analyte_code: str, direction: str, role: str, verdict: str, reviewed_by: str, reviewed_against: str,
    co_medication_moiety_uuid=None, co_medication_class_uuid=None, onset_min_days: int | None = None,
    onset_max_days: int | None = None, method: str | None = None, question_uuid=None) -> tuple[int, bool]`
  - `curation_lab_effect.answerable_questions(moiety, analyte_code) -> list[tuple[str, dict]]` →
    `[("unconfirmed_label_lab_effect", {"moiety_uuid": moiety, "analyte_code": analyte_code})]`, `[]` for a class
    subject.
  - `curation_lab_effect.snapshot_reach(conn, curated_lab_effect_id: int) -> int` (members written).

- [ ] **Step 1: Write the failing tests** — one per clause, in `test_class_profile_ruling.py`'s style:

- a moiety `holds` round-trips; the same call again returns `(id, False)` and writes nothing;
- a changed verdict (or onset, or method) supersedes: rows `[(old, new), (new, None)]`;
- `causes` and `monitoring` for one (moiety, analyte, direction) are two live rows (role is in the key);
- a co-medication moiety and a co-medication class on one row → CheckViolation; with neither,
  `co_medication_kind = 'none'` and `co_medication_uuid = '00000000-0000-0000-0000-000000000000'`;
- a class subject with `cannot_establish` → CheckViolation;
- `method` with a role other than `interferes` → CheckViolation; `onset_min_days = 30, onset_max_days = 7` →
  CheckViolation; negative onset → CheckViolation;
- an unknown `analyte_code` → ForeignKeyViolation; `role = 'not_read'` → CheckViolation;
- UPDATE of `verdict` and DELETE both raise (the floor);
- **reach:** a class ruling on parent class P, where drug A is a member of child class C only (`w.parent(C, P)`),
  snapshots `members == [A]`; a moiety ruling writes no reach row; a class with no members snapshots `[]`;
- **correction reach:** correcting that class ruling writes a second reach row under the new id; the old row is
  untouched;
- registration: `releases._CURATED_KINDS` contains `"curated_lab_effect"`;
  `signing_contexts.NATURAL_KEY_COLUMNS["curated_lab_effect/v1"] == ("subject_kind", "subject_uuid",
  "analyte_code", "direction", "role", "co_medication_kind", "co_medication_uuid")`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_curated_lab_effect.py -v`
Expected: FAIL (`ModuleNotFoundError: drugref.curation_lab_effect`).

- [ ] **Step 3: Write `db/090_curated_lab_effect.sql`**

Copy `db/058`'s shape: `AFTER UPDATE OR DELETE` floor via `drugref.forbid_overlay_rewrite('curated_lab_effect_id',
<the seven key columns>)` (AFTER, for the generated-column reason `db/058` records); deferred
`forbid_multiple_live_assertions(<seven key columns>)`; the partial live-key index and the by-question index;
`source CHECK IN ('DRUGREF')`; `question_uuid` FK `ON DELETE CASCADE` to `open_question`. CHECKs: one subject;
at most one co-medication; `direction`/`role`/`verdict` vocabularies; class subject not `cannot_establish`;
`method IS NULL OR role = 'interferes'`; onsets `>= 0` and `min <= max` when both set. `signature_target_kind` row
`('curated_lab_effect', 'curated_lab_effect', 'curated_lab_effect_id', 'curated_lab_effect/v1')`. The
`lab_effect_class_member` view and the reach table (trigger `curated_lab_effect_reach_insert_only`,
`forbid_any_rewrite`). Then `CREATE OR REPLACE VIEW drugref.current_release` copied **verbatim from `db/081`**
with one more `OR EXISTS (SELECT 1 FROM drugref.curated_lab_effect WHERE reviewed_at > rb.recorded_at)` in the
`re-curated` probe.

- [ ] **Step 4: Write `src/drugref/curation_lab_effect.py`**

Same sequence as `curation_property.record_property_ruling` (validate one subject → `require_question_for` →
compare the live row on the key; identical `verdict, onset_min_days, onset_max_days, method, question_uuid,
reviewed_by, reviewed_against` → `(id, False)` → INSERT → `overlay.supersede`). For a class subject, call
`snapshot_reach` after supersession. `snapshot_reach` inserts one row: `members =
COALESCE(array_agg(moiety_uuid ORDER BY moiety_uuid), '{}')` from `lab_effect_class_member` for the ruling's
subject class. No vocabulary restated in Python (the CHECKs raise).

- [ ] **Step 5: Register the kind** — add `CURATED_LAB_EFFECT_V1` (every column except the pk and
`superseded_by`, in table order, plus `*ATTESTATION_FIELDS`) and `CURATED_LAB_EFFECT_V1_KEY` to
`signing_contexts.py` and both dicts; `"curated_lab_effect"` to `releases._CURATED_KINDS`;
`"curated_lab_effect": "curated_lab_effect_id"` and `"curated_lab_effect_reach": "curated_lab_effect_id"` to
`release_archive._EXPORT_TABLES`; in `replay_loader.py`, `"curated_lab_effect": ("curated_lab_effect_id",
"reviewed_at")` in `_TIMED`, a `_LAB_REACH = "curated_lab_effect_reach"` constant in the module assertion's set,
and in `load()` after a `curated_lab_effect` row: insert its exported reach row, and report a difference when
its `members` differ from the target's `lab_effect_class_member` for that class (message shaped like
`_ruling_reach`'s).

- [ ] **Step 6: Run the task's tests and the guards it touches**

Run: `uv run pytest tests/test_curated_lab_effect.py tests/test_signing_payload_coverage.py tests/test_release_export_coverage.py tests/test_release_archive.py tests/test_release_replay.py -v`
Expected: all PASS. (The payload-coverage test compares the frozen key with the live single-live trigger and the
catalog's kinds with `_CURATED_KINDS`; a miss there is a registration step skipped.)

- [ ] **Step 7: Add one replay test** in `tests/test_release_replay.py`'s style: a class lab-effect ruling
published and replayed into an empty database loads its reach row and reports no difference. Run it: PASS.

- [ ] **Step 8: Commit** `feat: curated_lab_effect — rulings, exceptions, reach snapshot (db/090, #294)`

---

### Task 3: The label-reading storage (`db/091`)

**Files:**
- Create: `db/091_spl_lab_storage.sql`, `tests/lab_effect_world.py`
- Modify: `src/drugref/provenance.py` (`WRITERS` gains `"spl_lab_run"`)
- Test: `tests/test_spl_lab_schema.py`

**Interfaces:**
- Produces (all with `ingest_run bigint FK`, `source text CHECK (source = 'SPL_LAB')`, no prose column):
  - `spl_lab_label(ingest_run, source, set_id, version, effective_time date NULL)`, PK (ingest_run, source,
    set_id, version);
  - `spl_lab_label_subject(spl_lab_label_subject_id identity PK, ingest_run, source, set_id, version,
    moiety_uuid uuid NULL FK substance_moiety, resolution text NOT NULL)` — FK to the label; `resolution` is free
    text here (step 3 fixes its vocabulary, as the DDI ingest did);
  - `spl_lab_wording(ingest_run, source, text_key, char_count int NOT NULL > 0)`, PK (ingest_run, source,
    text_key);
  - `spl_lab_label_section(ingest_run, source, set_id, version, section FK lab_effect_section, text_key)`, PK
    (ingest_run, source, set_id, version, section), FKs to label and wording;
  - `spl_lab_effect_occurrence(ingest_run, source, text_key, char_start, char_end, term_id FK lab_effect_term,
    direction_read, role_read, role_rule NULL, co_medication_moiety_uuid NULL, co_medication_class_uuid NULL,
    scope, scope_start, scope_end)`, PK (ingest_run, source, text_key, char_start, term_id).
  - `tests/lab_effect_world.py`: `lab_run(conn) -> int`; `label(conn, run, set_id, moiety) -> None` (label + one
    subject row, `resolution = 'moiety'`); `section(conn, run, set_id, section, text_key) -> None` (wording +
    label-section); `reading(conn, run, text_key, term_id, *, role_read="causes", direction_read=None,
    char_start=0, co_medication_moiety_uuid=None) -> None` (`direction_read` defaults to the term's direction;
    `role_rule` is `'test.rule'` unless `role_read == 'not_read'`).

- [ ] **Step 1: Write the failing tests:** an `SPL_LAB` run with the writer `spl_lab_run` is accepted (and
`source = 'SPL'` on an `spl_lab_*` row is refused); `role_read = 'not_read'` with a `role_rule` → CheckViolation,
and a strength role with NULL `role_rule` → CheckViolation; a scope not containing the span → CheckViolation;
two co-medications → CheckViolation; `role_read = 'precondition'` → CheckViolation;
`provenance.WRITERS` contains `"spl_lab_run"`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_spl_lab_schema.py -v` — Expected: FAIL.

- [ ] **Step 3: Write `db/091_spl_lab_storage.sql`** — widen `ingest_run_source` (add `'SPL_LAB'`) and
`ingest_run_writer` (add `'spl_lab_run'`), each list copied verbatim from `db/067`, with `db/067`'s comment that
`provenance.WRITERS` changes in the same commit. Then the five tables, with `db/066`'s CHECK shapes
(`rule_iff_read` inverted for this vocabulary: `(role_read = 'not_read') = (role_rule IS NULL)`;
`scope_contains_span`; span positive), `scope CHECK IN ('sentence','heading_block')`, `direction_read` and
`role_read` vocabularies, and an index on `spl_lab_label_subject (moiety_uuid)`. Preamble: own source so a DDI
rebuild cannot delete lab readings (spec §6.4); quotes come in step 3, and never for `drug_interactions`.

- [ ] **Step 4: Update `provenance.WRITERS`; run**

Run: `uv run pytest tests/test_spl_lab_schema.py tests/test_provenance.py -v` — Expected: PASS.

- [ ] **Step 5: Commit** `feat: SPL_LAB label-reading storage, empty until step 3 (db/091, #294)`

---

### Task 4: The read and evidence views (`db/092`, part 1)

**Files:**
- Create: `db/092_lab_effect_views.sql` (this task writes sections 1–3 of it)
- Test: `tests/test_lab_effect_read.py`

**Interfaces:**
- Consumes: Tasks 1–3; `tests/lab_effect_world.py`; `curation_lab_effect.record_lab_effect_ruling`.
- Produces:
  - `lab_effect_label_evidence` — one row per (subject moiety, citing occurrence): `subject_moiety`, `set_id`,
    `version`, `section`, `section_rank`, `text_key`, `char_start`, `char_end`, `term_id`, `analyte_code`,
    `direction` (= `direction_read`), `via_pathway`, `role_read`, `role_rule`, `co_medication_kind`,
    `co_medication_uuid` (same generated encoding as Task 2), `ingest_run`. Resolved subjects only; includes
    `not_an_effect`.
  - `lab_effect_label_statement` — (subject_moiety, analyte_code, direction, role_read, co_medication_kind,
    co_medication_uuid) grain, excluding `not_an_effect`: `strongest_section`, `section_rank` (min),
    `label_count` (distinct set_id), `stated_as` (sorted distinct term ids), `via_pathway` (bool_or).
  - `lab_effect` — the columns in spec §7, plus `query_direction`: a row whose `direction = 'unspecified'`
    appears twice, with `query_direction` `high` and `low`; every other row once, `query_direction = direction`.
    Columns, in order: `analyte_code, query_direction, direction, specimen, via_pathway, subject_kind,
    subject_uuid, subject_name, reached_via_class_uuid, role, co_medication_kind, co_medication_uuid, basis,
    confirmed, strongest_section, section_rank, label_count, stated_as, curated_lab_effect_id, onset_min_days,
    onset_max_days, method, release_tag, release_drifted`.

The four arms of `lab_effect` (UNION ALL), each joined to `lab_analyte`:
1. live moiety `holds` rulings (`basis 'curated'`, `confirmed true`);
2. live class `holds` rulings, as class rows;
3. the same class rulings expanded through `lab_effect_class_member` to moiety rows (`reached_via_class_uuid` =
   the class), **minus** members with a live moiety ruling of any verdict on the same seven-column key;
4. `lab_effect_label_statement` rows (`basis 'label'`, `confirmed false`, `role = role_read`) **minus** any
   (moiety, analyte) with a live moiety ruling of any verdict (the review unit, spec §6.5).

Release columns by `LEFT JOIN drugref.current_release`: `release_tag`, `release_drifted =
cardinality(drift_reasons) > 0`.

- [ ] **Step 1: Write the failing tests** (fixture rows via `lab_effect_world` and `class_profile_world`):

```python
def test_a_label_reading_reads_back_unconfirmed(...):
    # drug A, label in 'warnings', reading 'hyperkalemia' role causes
    assert rows("SELECT analyte_code, query_direction, role, basis, confirmed, strongest_section, "
                "label_count, stated_as FROM drugref.lab_effect WHERE subject_uuid = %s", A) == [
        ("potassium", "high", "causes", "label", False, "warnings", 1, ["hyperkalemia"])]

def test_the_strongest_section_wins_and_labels_are_counted(...):
    # two labels: one 'adverse_reactions', one 'boxed_warning' -> strongest boxed_warning, label_count 2

def test_a_not_an_effect_reading_is_evidence_only(...):
    # present in lab_effect_label_evidence, absent from lab_effect

def test_a_curator_role_correction_replaces_the_machine_reading(...):   # Review Focus 3
    # reading causes; ruling holds more_dangerous -> lab_effect has only the ruling (basis curated,
    # confirmed True, role more_dangerous); the evidence view still has the causes reading

def test_a_not_read_reading_is_shown_until_ruled(...):
    # role 'not_read' appears with basis 'label'; after any moiety ruling on (A, potassium) it is gone

def test_a_class_ruling_reaches_a_subclass_member(...):                 # Review Focus 2
    # drug A in child class C; holds on parent P -> a class row for P and a moiety row for A with
    # reached_via_class_uuid == P

def test_a_member_does_not_hold_carves_it_out_of_a_class_ruling(...):
    # class holds causes; A does_not_hold same key -> no row for A, the class row remains

def test_a_class_ruling_never_hides_a_member_label_reading(...):
    # class does_not_hold; A's label reading still shows

def test_an_unspecified_direction_answers_both_questions(...):          # Review Focus 4
    # reading with direction_read 'unspecified' -> two rows, query_direction high and low,
    # direction 'unspecified' on both

def test_the_view_works_with_no_published_release(...):                 # Review Focus 1
    # no release_manifest rows -> the reading still shows, release_tag IS NULL, release_drifted IS NULL

def test_a_co_medication_is_carried(...):
    # reading 'inr_increased' with co_medication_moiety_uuid = warfarin -> co_medication_kind 'moiety'
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_lab_effect_read.py -v` — FAIL.

- [ ] **Step 3: Write sections 1–3 of `db/092`** with `COMMENT ON` each view stating its grain, that label rows
are machine-read and not confirmed, and (on `lab_effect`) the `query_direction` rule and the precedence rules.

- [ ] **Step 4: Run** — `uv run pytest tests/test_lab_effect_read.py -v` — PASS.

- [ ] **Step 5: Commit** `feat: lab_effect read view and label evidence (db/092, #294)`

---

### Task 5: The coverage view and the worklist (`db/092`, part 2)

**Files:**
- Modify: `db/092_lab_effect_views.sql` (sections 4–5; still unapplied on any named database, so editing it in
  this branch is allowed until it merges)
- Create: `src/drugref/gap_sources_lab_effect.py`
- Modify: `src/drugref/questions.py`
- Test: `tests/test_lab_effect_coverage.py`, `tests/test_lab_effect_gap.py`

**Interfaces:**
- Produces:
  - `lab_effect_coverage(moiety_uuid, analyte_code, direction, state, label_count)` over every
    `substance_moiety` × `lab_analyte` × `('high','low')`. `state`, first match wins: `stated` (a `lab_effect`
    moiety row with that `query_direction`) → `ruled_no_effect` (a live `does_not_hold` on that analyte and
    direction, the moiety's own or a class's reaching it through `lab_effect_class_member`) → `cannot_establish`
    (a live moiety `cannot_establish`) → `examined_nothing_found` (any `spl_lab_label_subject` row for the
    moiety) → `not_examined`. `label_count` = distinct labels with a subject row for the moiety.
  - `gap_unconfirmed_label_lab_effect(moiety_uuid, analyte_code, section_rank, label_count)` — distinct
    (moiety, analyte) in `lab_effect_label_statement` with no live moiety ruling of any verdict on that analyte;
    best (min) `section_rank`; `label_count` across the pair.
  - gap kind `unconfirmed_label_lab_effect`: the `db/066` DO-block, list copied verbatim from `db/066` plus the
    new value.
  - `gap_sources_lab_effect.LAB_EFFECT_GAP_SOURCES = {"unconfirmed_label_lab_effect": {"view": ..., "key_sql":
    "'LABEFFECT:' || moiety_uuid || ':' || analyte_code", "rank_sql": "section_rank", "text_sql": ...}}`. The
    question text names the drug and analyte, says the reading is machine-read and not confirmed, asks for the
    role and verdict, and never suggests the drug is safe.

- [ ] **Step 1: Write the failing tests**

Coverage, one test per state, plus: a `stated` row wins over a class `does_not_hold`; a label read with nothing
for sodium gives sodium `examined_nothing_found` while potassium is `stated`; a moiety with no label is
`not_examined` for every analyte and direction (14 rows); a class `does_not_hold` gives a member with no reading
`ruled_no_effect`.

Gap: one question per (moiety, analyte) even with two roles read; a moiety ruling of any verdict on the analyte
removes it; `registry.register_from_gaps` (the call `tests/test_class_profile_gap.py` uses) mints a question
whose key is `LABEFFECT:<moiety>:<analyte>`; `record_lab_effect_ruling(..., question_uuid=<that question>)` is
accepted, and citing it from a ruling on another analyte raises ValueError.

- [ ] **Step 2: Run to verify they fail** —
`uv run pytest tests/test_lab_effect_coverage.py tests/test_lab_effect_gap.py -v` — FAIL.

- [ ] **Step 3: Write sections 4–5 of `db/092`, `gap_sources_lab_effect.py`, and merge it in `questions.py`**
(beside `CLASS_PROFILE_GAP_SOURCES`). `COMMENT ON lab_effect_coverage` says `examined_nothing_found` is
unavailable context, never reassurance, and only `ruled_no_effect` means "no effect".

- [ ] **Step 4: Run these tests and the gap-registry guards**

Run: `uv run pytest tests/test_lab_effect_coverage.py tests/test_lab_effect_gap.py tests/test_class_profile_gap.py -v`, then `uv run pytest -k "gap_kind or registry or question" -q`
Expected: PASS. Any test that pins the gap-kind count or the gap-source set gets the new kind added (read its
failure message; that is the registration it wants).

- [ ] **Step 5: Commit** `feat: lab-effect coverage view and worklist, gap kind 21 (db/092, #294)`

---

### Task 6: Full suite, docs, PR

**Files:** `docs/PROJECT-NOTES.md`, `docs/ROADMAP.md`, `docs/HANDOVER.md`,
`docs-site/docs/decisions/lab-effects-keyed-to-analytes.md` (+ its nav entry in `docs-site/mkdocs.yml`).

- [ ] **Step 1: Full suite** — `uv run pytest` (with `DRUGREF_TEST_DSN` set). Expected: only
`tests/test_suite_count.py` fails, stating old and new counts. Update the one count in PROJECT-NOTES § "How to run
/ test" to the collected number; re-run: all PASS. `ruff check .`: clean.
- [ ] **Step 2: PROJECT-NOTES** — new section "Lab effects: the model (#294 step 2, `db/089`–`db/092`)": what each
migration adds, the precedence rules, the traps (role in the key but the review unit is (moiety, analyte);
`query_direction`; `SPL_LAB` is its own source; reach is a `uuid[]` written by the writer, not a trigger); gap
kinds now 21 (update the count line in § "Current state, by layer"). Edit the existing #294 section's heading in
place to point to it.
- [ ] **Step 3: ROADMAP** — #294 entry: step 2 ✅ with the spec and plan links; step 3 next.
- [ ] **Step 4: docs-site decision record** — living record: statements keyed to drugref's own MeSH-derived
analytes, why not conditions (the INR and trimethoprim cases), no LOINC in tables; review unit; class rulings
never hide member labels.
- [ ] **Step 5: HANDOVER** — regenerate within its own header's bound: branch, schema ends at `db/092`, next is
step 3 (reader, orchestrator, test-set comparison, precision sample; check `spl_match`/`spl_advice_scope` on
other sections; combination-label rule; quotes and their budget trigger).
- [ ] **Step 6: Commit, push, open the PR** to `main`, body `Part of #294` (never "closes": #294 is closed and
its steps continue) and ending with the Claude Code line.

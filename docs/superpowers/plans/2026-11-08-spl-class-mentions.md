# SPL class mentions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recognise drug-class names in SPL section 7 prose, read the label's own advice level for each, and
show them on class profiles as "What labels say about this class" — machine-read, not confirmed.

**Architecture:** A second, class-only match pass over each wording the SPL ingest already reads (the moiety
pass is untouched), a pure phrase reader for the advice level, one new projection table plus two views in
`db/066`, a new section on the class profile, and a curator worklist that answers through
`curated_property_ruling`. Nothing reaches the alert path or `class_asserted_property`.

**Tech Stack:** Python 3 / psycopg 3 / PostgreSQL; pytest (DB tests need `DRUGREF_TEST_DSN`).

**Spec:** [docs/superpowers/specs/2026-11-08-drugref-spl-class-mentions-design.md](../specs/2026-11-08-drugref-spl-class-mentions-design.md)

## Global Constraints

- Parsers and readers are pure (no DB, no I/O); `ingest/spl_run.py` is the only writer and owns the transaction.
- `spl_wording` keeps **no prose column**; store facts and offsets only. **No new quoting**: reuse
  `spl_wording_quote` windows that already cover a span.
- The moiety pass, `spl_entity_occurrence`, `spl_ddi_evidence`, `spl_ddi_pair`, `spl_checks.MEASURED_PAIR_FLOOR`
  (29,258) and `MEASURED_NOVEL_FLOOR` (25,960) are unchanged in behaviour and figures.
- MED-RT's PK axis (`source='MED-RT' AND concept_type='PK'`) is never matched. FDA-CYP (also `PK`) is.
- Advice levels, exactly: `contraindicated`, `avoid`, `adjust_dose`, `monitor`, `no_meaningful_interaction`,
  `not_read`. Strength order: contraindicated > avoid > adjust_dose > monitor.
- Nothing machine-read enters `class_asserted_property`, `exact_ddi_pair`, `ddi_candidate_pair`,
  `curated_ddi_pair` or a signature target. Only a curator's `holds` in `curated_property_ruling` promotes.
- Never edit an applied migration; `db/051` comments are corrected by `COMMENT ON` in `db/066`.
- Files under ~500 lines: `spl_run.py` (467) and `spl_evidence.py` (490) get thin hooks only; logic goes in
  new modules.
- Inline docs readable by a junior contributor: say *why*. Match the hand style; do not run `ruff format`.
- Every task that adds tests updates the suite total in PROJECT-NOTES § "How to run / test" (the one home —
  `tests/test_suite_count.py` fails otherwise). Never run the full suite with `-p no:cacheprovider`.
- Test fixtures synthesise section prose (rule 6): never commit real label text.

## Review Focus

1. **"not contraindicated" / "no dose adjustment"** — a negated strong phrase must not read as the strong level
   and must not leak into a weaker one. → Task 3 tests.
2. **Two levels in one sentence** ("contraindicated with strong …; avoid with moderate …") — strongest wins,
   `mixed` set. → Task 3 tests.
3. **A class name that is also a substring of a moiety span** (`potassium` in *potassium-sparing diuretics*) —
   the drug pass still emits `potassium` (unchanged), and the overlap is counted. → Task 6 test.
4. **A class span folding onto two classes** (`Diuretics` MeSH + `Diuretic [APC]`) — one row per class, both
   flagged, both profiles show it. → Tasks 2 and 7 tests.
5. **An empty class or a class with no statements** — profile renders with no "What labels say" section and
   no crash; an empty class with statements shows them. → Task 7 tests.

---

### Task 1: Class match keys (pure)

**Files:**
- Create: `src/drugref/ingest/spl_class_vocab.py`
- Modify: `tools/spl_class_vocabulary_delta.py` (import `strip_axis_tag`/`number_variants` instead of its own
  `_AXIS_TAG`/`class_variants` body; its output must not change)
- Test: `tests/test_spl_class_vocab.py`

**Interfaces:**
- Produces:
  - `FORM_STORED = "stored"`, `FORM_NUMBER = "number"`, `FORM_LABEL_ORDER = "label_order"`;
    `MATCH_FORMS = (FORM_STORED, FORM_NUMBER, FORM_LABEL_ORDER)`
  - `strip_axis_tag(class_name: str) -> str` — removes a trailing `[MoA]`/`[FDA-CYP]`-style tag.
  - `number_variants(base: str) -> tuple[str, ...]` — the other grammatical number only (`Diuretics` →
    `("Diuretic",)`, `Diuretic` → `("Diuretics",)`).
  - `fda_cyp_label_orders(base: str) -> tuple[str, ...]` — for `<enzyme> <strong|moderate|weak>
    <inhibitor|inducer>` only: `("<band> <enzyme> <role>", "<band> <enzyme> <role>s")`; `()` for anything
    else (substrates, unbanded transporters).
  - `is_matchable(source: str, concept_type: str) -> bool` — False only for MED-RT PK.
  - `class_match_keys(class_name: str, source: str) -> tuple[tuple[str, str], ...]` — `(key, form)` pairs,
    stored form first, deduplicated by `spl_match.fold`, label orders only when `source == "FDA-CYP"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_strip_axis_tag():
    assert strip_axis_tag("Calcium Channel Blocker [EPC]") == "Calcium Channel Blocker"
    assert strip_axis_tag("CYP1A2 strong inhibitor [FDA-CYP]") == "CYP1A2 strong inhibitor"

def test_number_variants_both_directions():
    assert number_variants("Diuretics") == ("Diuretic",)
    assert number_variants("Diuretic") == ("Diuretics",)

def test_fda_cyp_label_orders_inhibitor_and_inducer():
    assert fda_cyp_label_orders("CYP1A2 strong inhibitor") == (
        "strong CYP1A2 inhibitor", "strong CYP1A2 inhibitors")
    assert fda_cyp_label_orders("CYP3A moderate inducer") == (
        "moderate CYP3A inducer", "moderate CYP3A inducers")

def test_fda_cyp_label_orders_skips_substrates_and_unbanded():
    assert fda_cyp_label_orders("CYP1A2 moderate sensitive substrate") == ()
    assert fda_cyp_label_orders("P-gp inhibitor") == ()

def test_is_matchable_excludes_only_medrt_pk():
    assert not is_matchable("MED-RT", "PK")
    assert is_matchable("FDA-CYP", "PK") and is_matchable("MeSH", "PA")
    assert is_matchable("MED-RT", "EPC")

def test_class_match_keys_fda_cyp():
    keys = class_match_keys("CYP1A2 strong inhibitor [FDA-CYP]", "FDA-CYP")
    assert keys[0] == ("CYP1A2 strong inhibitor", FORM_STORED)
    assert ("strong CYP1A2 inhibitors", FORM_LABEL_ORDER) in keys
    assert ("CYP1A2 strong inhibitors", FORM_NUMBER) in keys

def test_class_match_keys_no_label_order_outside_fda_cyp():
    assert {f for _, f in class_match_keys("Diuretics", "MeSH")} == {FORM_STORED, FORM_NUMBER}

def test_class_match_keys_dedupes_by_fold():
    keys = class_match_keys("Thiazides [EPC]", "MED-RT")
    assert len({spl_match.fold(k) for k, _ in keys}) == len(keys)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_spl_class_vocab.py -v` → ImportError.
- [ ] **Step 3: Implement** `spl_class_vocab.py` with the interfaces above; then point the tool's
  `class_variants` at `(strip_axis_tag(name), *number_variants(...))` so it keeps its exact output.
- [ ] **Step 4: Run** `uv run pytest tests/test_spl_class_vocab.py tests/test_spl_tools_smoke.py -v` → PASS.
- [ ] **Step 5: Update the suite total; commit** `feat: SPL class match keys (spl_class_vocab)`.

### Task 2: Matcher carries classes

**Files:**
- Modify: `src/drugref/ingest/spl_match.py` (`Entry`, new `ClassOccurrence`, `class_occurrences`; update the
  module docstring's "CLASSES ARE NOT MATCHED" paragraph to say a separate class pass exists)
- Modify: `tools/spl_class_vocabulary_delta.py` (`load_class_entries` selects `class_uuid` and passes it)
- Test: `tests/test_spl_match.py`

**Interfaces:**
- Consumes: Task 1 `MATCH_FORMS`, `FORM_STORED`.
- Produces:
  - `Entry` gains `class_uuid: str | None = None` and `form: str = FORM_STORED`; `__post_init__` also
    refuses `(kind == KIND_CLASS) != (class_uuid is not None)` and a `form` outside `MATCH_FORMS`.
  - `@dataclass(frozen=True) class ClassOccurrence: class_uuid: str; display: str; char_start: int;
    char_end: int; ambiguous: bool; form: str`
  - `class_occurrences(matches: Iterable[Match]) -> tuple[ClassOccurrence, ...]` — one per distinct
    `class_uuid` per span, document order; `ambiguous` = more than one distinct `class_uuid` on the span.
    When one class reaches a span through two forms, keep the first form in `MATCH_FORMS` order.

- [ ] **Step 1: Write the failing tests**

```python
def test_class_entry_requires_class_uuid():
    with pytest.raises(ValueError):
        Entry(kind=KIND_CLASS, key="diuretics", display="Diuretics")

def test_moiety_entry_refuses_class_uuid():
    with pytest.raises(ValueError):
        Entry(kind=KIND_MOIETY, key="x", display="x", moiety_uuid="m", class_uuid="c")

def test_class_occurrences_one_per_class_flagged_ambiguous():
    vocab = build_vocabulary([
        Entry(kind=KIND_CLASS, key="Diuretics", display="Diuretics", class_uuid="c-mesh"),
        Entry(kind=KIND_CLASS, key="Diuretics", display="Diuretic [APC]", class_uuid="c-apc",
              form=FORM_NUMBER)])
    found = class_occurrences(find_matches("Avoid diuretics.", vocab))
    assert {o.class_uuid for o in found} == {"c-mesh", "c-apc"}
    assert all(o.ambiguous for o in found) and found[0].char_start == 6

def test_class_occurrences_ignores_moiety_entries():
    vocab = build_vocabulary([Entry(kind=KIND_MOIETY, key="warfarin", display="warfarin",
                                    moiety_uuid="m")])
    assert class_occurrences(find_matches("warfarin", vocab)) == ()

def test_moiety_occurrences_ignores_class_entries():
    vocab = build_vocabulary([Entry(kind=KIND_CLASS, key="Diuretics", display="Diuretics",
                                    class_uuid="c")])
    assert moiety_occurrences(find_matches("diuretics", vocab)) == ()
```

- [ ] **Step 2: Run** `uv run pytest tests/test_spl_match.py -v` → new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `uv run pytest tests/test_spl_match.py tests/test_spl_type_invariants.py tests/test_spl_tools_smoke.py -v` → PASS.
- [ ] **Step 5: Update the suite total; commit** `feat: spl_match class entries and class_occurrences`.

### Task 3: Advice reader (pure)

**Files:**
- Create: `src/drugref/ingest/spl_advice.py`
- Test: `tests/test_spl_advice.py`

**Interfaces:**
- Produces:
  - `LEVELS = ("contraindicated", "avoid", "adjust_dose", "monitor", "no_meaningful_interaction", "not_read")`;
    `STRENGTH = ("contraindicated", "avoid", "adjust_dose", "monitor")` (strongest first).
  - `SCOPE_SENTENCE = "sentence"`, `SCOPE_HEADING_BLOCK = "heading_block"`; `SCOPES`.
  - `@dataclass(frozen=True) class AdviceRule: rule_id: str; level: str; pattern: re.Pattern[str]`
  - `ADVICE_RULES: tuple[AdviceRule, ...]` — seed vocabulary; `rule_id`s unique, shaped `<level>.<name>`.
    Seed at least: `contraindicated.contraindicated`, `avoid.avoid`, `avoid.do_not_coadminister`,
    `adjust_dose.reduce_dose`, `adjust_dose.dose_adjustment`, `monitor.monitor`, `monitor.observe_closely`,
    `no_meaningful_interaction.no_clinically_significant`, `no_meaningful_interaction.no_dose_adjustment`.
    Task 9 tunes this list on the corpus.
  - `@dataclass(frozen=True) class AdviceReading: level: str; mixed: bool; rule_id: str | None; scope: str;
    scope_start: int; scope_end: int`
  - `sentence_span(text: str, char_start: int, char_end: int) -> tuple[int, int]` — the sentence holding the
    span. Boundaries are `.` or `;` followed by whitespace and an upper-case letter or digit, or end
    of text; a subsection marker (`\d+(\.\d+)+\s+[A-Z]`) starts a
    new sentence. Never split after `e.g.`, `i.e.`, `approx.`, `vs.`, or inside a decimal (`7.1`, `0.5`).
  - `heading_block_span(text: str, char_start: int) -> tuple[int, int] | None` — when a subsection marker
    precedes the mention by at most 8 tokens with no sentence boundary between them, the span from that
    marker to the next marker (or end); else `None`.
  - `read_advice(text: str, char_start: int, char_end: int) -> AdviceReading`

Algorithm `read_advice` (not determined by the signature):
1. Read the sentence scope: find all `no_meaningful_interaction` rule matches first and **mask** their spans;
   run the four strength levels' rules on the masked text. Levels found = the set of STRENGTH levels whose
   rule matched.
2. If any STRENGTH level found → level = strongest, `mixed = len(found) > 1`, `rule_id` = first-in-text match
   of the winning level, scope = sentence.
3. Else if a `no_meaningful_interaction` rule matched → that level, `mixed=False`, scope sentence.
4. Else if `heading_block_span` is not None → repeat 1–3 over the heading block with scope `heading_block`
   (`no_meaningful_interaction` is **not** read at heading-block scope: a block can mix statements).
5. Else `not_read`, `rule_id=None`, `mixed=False`, scope sentence.

- [ ] **Step 1: Write the failing tests** (each builds text in-test, never real label prose)

```python
def _read(text, name):
    i = text.index(name); return read_advice(text, i, i + len(name))

def test_contraindicated():
    r = _read("Use with strong CYP1A2 inhibitors is contraindicated.", "strong CYP1A2 inhibitors")
    assert (r.level, r.mixed, r.rule_id, r.scope) == (
        "contraindicated", False, "contraindicated.contraindicated", "sentence")

def test_strongest_wins_and_mixed():
    r = _read("Avoid MAO inhibitors; use is contraindicated within 14 days.", "MAO inhibitors")
    assert (r.level, r.mixed) == ("contraindicated", True)

def test_not_contraindicated_is_not_contraindicated():
    assert _read("Use with antacids is not contraindicated.", "antacids").level == "not_read"

def test_no_dose_adjustment_is_nmi_not_adjust_dose():
    r = _read("No dose adjustment is needed with antacids.", "antacids")
    assert r.level == "no_meaningful_interaction"

def test_nmi_never_beats_a_strength_level():
    r = _read("No clinically significant interaction with antacids, but monitor INR.", "antacids")
    assert r.level == "monitor"

def test_sentence_scope_only():
    text = "Monitor patients on diuretics. Thiazides are fine."
    assert _read(text, "Thiazides").level == "not_read"

def test_eg_and_decimal_do_not_split():
    text = "Avoid strong inhibitors (e.g. ketoconazole) at doses above 0.5 mg."
    s, e = sentence_span(text, text.index("ketoconazole"), text.index("ketoconazole") + 12)
    assert (s, e) == (0, len(text))

def test_heading_block_scope():
    text = ("7.1 Warfarin text here. 7.2 Potassium-Sparing Diuretics Concomitant use "
            "increases potassium. Avoid concomitant use. 7.3 Other text.")
    r = _read(text, "Potassium-Sparing Diuretics")
    assert (r.level, r.scope) == ("avoid", "heading_block")
    assert text[r.scope_start:r.scope_end].startswith("7.2")

def test_unknown_phrase_is_not_read():
    r = _read("Diuretics were studied.", "Diuretics")
    assert (r.level, r.rule_id) == ("not_read", None)

def test_rule_ids_unique_and_levels_known():
    ids = [r.rule_id for r in ADVICE_RULES]
    assert len(ids) == len(set(ids))
    assert all(r.level in LEVELS[:5] and r.rule_id.startswith(r.level + ".") for r in ADVICE_RULES)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_spl_advice.py -v` → ImportError.
- [ ] **Step 3: Implement** `spl_advice.py` per the interfaces and algorithm. Docstring states the owner's
  2026-11-08 reversal of 5c.3's "no relation extraction" for this grain and the harm direction.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Update the suite total; commit** `feat: spl_advice -- the label's own advice level, machine-read`.

### Task 4: `db/066` — class occurrences, statements, worklist view

**Files:**
- Create: `db/066_spl_class_mentions.sql`
- Test: `tests/test_spl_class_schema.py` (DB-gated, pattern of `tests/test_spl_schema.py`)

**Interfaces:**
- Consumes: Task 1 `MATCH_FORMS`; Task 3 `LEVELS`, `STRENGTH`, `SCOPES`.
- Produces (SQL):
  - `drugref.spl_advice_level(level text PRIMARY KEY, strength_rank integer NULL)` seeded: contraindicated 1,
    avoid 2, adjust_dose 3, monitor 4, no_meaningful_interaction NULL, not_read NULL.
  - `drugref.spl_class_occurrence(ingest_run, source CHECK = 'SPL', text_key, char_start, char_end,
    class_uuid → substance_class, match_ambiguous bool, match_form text CHECK IN MATCH_FORMS,
    advice_level → spl_advice_level, advice_mixed bool, advice_rule text NULL, advice_scope text CHECK IN
    SCOPES, scope_start int, scope_end int)`; PK `(ingest_run, source, text_key, char_start, class_uuid)`; FK
    `(ingest_run, source, text_key)` → `spl_wording`; CHECKs: span positive; `scope_start <= char_start AND
    scope_end >= char_end`; `(advice_level = 'not_read') = (advice_rule IS NULL)`; `advice_mixed` only when
    `advice_level` has a strength rank. Index by `class_uuid`.
  - View `drugref.spl_class_statement_evidence` — one row per (subject moiety, class, citing label
    occurrence): `subject_moiety, class_uuid, set_id, version, effective_time, text_key, char_start,
    char_end, match_ambiguous, match_form, advice_level, advice_mixed, advice_rule, advice_scope,
    quote_text (LEFT JOIN, NULL where no window covers the span), subject_in_class` (EXISTS a direct
    `class_membership` row for subject × class), `ingest_run`. Resolved subjects only (`moiety_uuid IS NOT
    NULL`), joined exactly as `spl_ddi_evidence` joins.
  - View `drugref.spl_class_statement` — one row per (subject_moiety, class_uuid): `label_count`,
    `wording_count`, `strongest_level` (lowest `strength_rank`; if none, `no_meaningful_interaction` when any
    row has it, else `not_read`), `level_counts jsonb` (level → label count), `any_mixed`,
    `any_ambiguous`, `subject_in_class`.
  - View `drugref.gap_unconfirmed_label_class_statement` — `spl_class_statement` rows whose
    `strongest_level` has a strength rank and that have **no live** `curated_property_ruling` for
    (`moiety`, subject, `interacts_with`, `class`, class_uuid); columns `moiety_uuid, class_uuid,
    strongest_level, label_count`.
  - Widen `open_question_gap_kind` by `'unconfirmed_label_class_statement'` using `db/061`'s DO-block pattern
    (copy the live list verbatim from `db/061`, add one).
  - `COMMENT ON` for every new object (grain in the name; "machine-read, not confirmed, never an
    assertion"), and corrected `COMMENT ON TABLE drugref.spl_entity_occurrence` saying class occurrences live
    in `spl_class_occurrence` (db/066).

- [ ] **Step 1: Write the failing tests** — `test_advice_level_table_matches_python` (rows ==
  `spl_advice.LEVELS`, ranks follow `STRENGTH` order); `test_match_form_check_matches_python` (catalog CHECK
  text holds exactly `MATCH_FORMS`); `test_scope_check_matches_python`; `test_not_read_requires_null_rule`
  (insert `not_read` with a rule → `CheckViolation`); `test_scope_must_contain_span`;
  `test_mixed_refused_without_strength`; `test_gap_kind_admitted` (insert an `open_question` row with the
  new kind succeeds); `test_spl_entity_occurrence_comment_names_db066`.
- [ ] **Step 2: Run** `DRUGREF_TEST_DSN=... uv run pytest tests/test_spl_class_schema.py -v` → FAIL.
- [ ] **Step 3: Write `db/066_spl_class_mentions.sql`.**
- [ ] **Step 4: Run** the file plus `tests/test_spl_schema.py tests/test_class_profile_gap.py` → PASS.
- [ ] **Step 5: Update the suite total; commit** `feat: db/066 SPL class occurrences, statements, worklist view`.

### Task 5: Class evidence rows and writer

**Files:**
- Create: `src/drugref/spl_class_evidence.py`
- Modify: `src/drugref/spl_evidence.py` (add `"ClassOccurrenceRow": "spl_class_occurrence"` to
  `_TABLE_FOR_ROW`, `write_class_occurrences`, and `drugref.spl_class_occurrence` to `SPL_TABLES` **before**
  `spl_wording` so the clear deletes children first; `spl_class_occurrence` into `analyze_source_tables`)
- Test: `tests/test_spl_class_evidence.py`

**Interfaces:**
- Consumes: Task 2 `ClassOccurrence`, `class_occurrences`, `find_matches`, `Entry`, `build_vocabulary`;
  Task 3 `read_advice`; Task 1 `class_match_keys`, `is_matchable`.
- Produces:
  - `@dataclass(frozen=True, kw_only=True) class ClassOccurrenceRow:` fields in `db/066` column order after
    `(ingest_run, source)`: `text_key, char_start, char_end, class_uuid, match_ambiguous, match_form,
    advice_level, advice_mixed, advice_rule, advice_scope, scope_start, scope_end`.
  - `load_class_entries(conn: psycopg.Connection) -> list[spl_match.Entry]` — one `KIND_CLASS` entry per
    `class_match_keys` pair for every matchable `substance_class` row; `display` = stored `class_name`.
  - `class_vocabulary(entries: Sequence[spl_match.Entry]) -> spl_match.Vocabulary` — class entries only, no
    suppression terms (they were derived for moieties).
  - `class_rows_for_wording(text_key: str, text: str, vocab: spl_match.Vocabulary) -> list[ClassOccurrenceRow]`
    — pure: match, then `read_advice` per occurrence.
  - `spl_evidence.write_class_occurrences(conn, rows, *, ingest_run_id: int, source: str) -> int`

- [ ] **Step 1: Write the failing tests** — pure: `test_class_rows_for_wording_reads_advice` (synthetic
  wording with one contraindicated class mention → one row, level `contraindicated`, form as matched);
  `test_class_rows_for_wording_ambiguous_two_rows`; `test_class_rows_empty_when_no_class`. DB-gated:
  `test_load_class_entries_excludes_medrt_pk` (seed one MED-RT PK and one FDA-CYP class → only FDA-CYP keys);
  `test_write_and_clear_class_occurrences` (write a row under a run; `clear_source_spl` removes it).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the file plus `tests/test_spl_run.py` → PASS.
- [ ] **Step 5: Update the suite total; commit** `feat: SPL class occurrence rows, loader and writer`.

### Task 6: Orchestrator — the class pass

**Files:**
- Modify: `src/drugref/ingest/spl_run.py` (load class entries beside `load_registry`, before
  `conn.rollback()`; in `_write_evidence`, per wording, also call `class_rows_for_wording` and write per
  chunk; return the class counts too)
- Modify: `src/drugref/ingest/spl_checks.py` (`SplSummary` gains `class_occurrences: int`,
  `wordings_with_a_class: int`, `moiety_spans_inside_class_spans: int`; `__str__` prints them; `reconcile`
  takes `class_occurrences=` and checks the stored count)
- Test: `tests/test_spl_run.py`

**Interfaces:**
- Consumes: Task 5 `load_class_entries`, `class_vocabulary`, `class_rows_for_wording`,
  `spl_evidence.write_class_occurrences`.
- Produces: `ingest_spl(...)` signature unchanged; `SplSummary` fields above. `moiety_spans_inside_class_spans`
  = moiety occurrence rows whose span lies within some class span of the same wording (counted in Python
  during the pass, §4.4 of the spec).

- [ ] **Step 1: Write the failing tests** — extend the synthesised fixture section text so one label names a
  registered class (seed one FDA-CYP class with a member) in a contraindicated sentence, and one sentence names
  a moiety inside a class name. Tests: `test_class_pass_writes_class_occurrences` (count and level read back
  from `spl_class_statement`); `test_drug_pairs_unchanged_by_class_pass` (`spl_checks.read_pairs` equals the
  value with the class vocabulary empty — run twice, compare); `test_overlap_counted`
  (`summary.moiety_spans_inside_class_spans == 1`); `test_reconcile_refuses_class_count_mismatch`.
- [ ] **Step 2: Run** `DRUGREF_TEST_DSN=... uv run pytest tests/test_spl_run.py -v` → FAIL.
- [ ] **Step 3: Implement.** Keep `spl_run.py` under 500 lines; move helper logic into `spl_class_evidence`.
- [ ] **Step 4: Run** `tests/test_spl_run.py tests/test_spl_run_census.py tests/test_cli_spl.py` → PASS.
- [ ] **Step 5: Update the suite total; commit** `feat: SPL ingest runs a class pass beside the moiety pass`.

### Task 7: Class profile — "What labels say about this class"

**Files:**
- Modify: `src/drugref/class_profile_read.py` (new `LabelStatement`, `ClassProfile.label_statements`)
- Modify: `src/drugref/cli_class_profile.py` (`render_class_profile` prints the section)
- Test: `tests/test_class_profile_read.py`, `tests/test_cli_class_profile.py`

**Interfaces:**
- Consumes: `db/066` `spl_class_statement`; `curated_property_ruling` live rows.
- Produces:
  - `@dataclass(frozen=True) class LabelStatement: drug: str; strongest_level: str; label_count: int;
    mixed: bool; ambiguous: bool; subject_in_class: bool; ruling: str | None` (`ruling` = live verdict for
    moiety → `interacts_with` → this class, else None).
  - `ClassProfile.label_statements: tuple[LabelStatement, ...]`, ordered by strength rank (unranked last),
    then label_count desc, then drug name.
  - Render, only when non-empty: header `  What labels say about this class (machine-read, not confirmed):`
    then per statement `    <drug>'s label: <level> (<n> label(s))` plus suffixes `; mixed`, `; name also
    matches another class`, `; its own class`, `; curator ruled: <verdict>`.

- [ ] **Step 1: Write the failing tests** — `test_label_statements_ordered_and_flagged`;
  `test_ambiguous_statement_on_both_classes`; `test_does_not_hold_ruling_still_shown`;
  `test_label_statement_not_in_asserted_properties` (the statement's class gains **no** `ClassProperty` from
  it); `test_render_omits_section_when_empty`; `test_render_section_lines` (exact strings above).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** One query against `spl_class_statement` LEFT JOIN live rulings; names via `_names`.
- [ ] **Step 4: Run** `tests/test_class_profile_*.py tests/test_cli_class_profile.py` → PASS.
- [ ] **Step 5: Update the suite total; commit** `feat: class profiles show what labels say about the class`.

### Task 8: Curator worklist

**Files:**
- Modify: `src/drugref/gap_sources_class_profile.py` (new kind)
- Modify: `src/drugref/curation_property.py` (`answerable_questions` adds the new kind for a moiety subject
  and a class object)
- Modify: `src/drugref/ingest/spl_run.py` (call `questions.register_from_gaps(conn, run_id)` before
  `finish_run`; replace the "DELIBERATELY NOT CALLED" comment with why it now is)
- Modify: `src/drugref/citation_audit.py` only if its candidate wiring needs the kind (it reuses
  `answerable_questions`; verify by test)
- Test: `tests/test_class_profile_gap.py`, `tests/test_class_profile_ruling.py`, `tests/test_spl_run.py`

**Interfaces:**
- Produces: gap kind `unconfirmed_label_class_statement`, view `gap_unconfirmed_label_class_statement`,
  **frozen** `key_sql` `'SPLCLASS:' || moiety_uuid || ':' || class_uuid`; `text_sql` asks "Does <drug>
  interact with <class>? <n> label(s) for <drug> say <level> (machine-read, not confirmed). Answer: holds,
  does not hold, or cannot be established. Answering does not change prescribing alerts." Candidate tuple
  `("unconfirmed_label_class_statement", {"moiety_uuid": m, "class_uuid": o})`.

- [ ] **Step 1: Write the failing tests** — `test_label_class_question_registered_after_spl_run`;
  `test_ruling_may_cite_label_class_question` (`record_property_ruling` with that question_uuid succeeds);
  `test_ruling_refuses_label_question_for_other_class`; `test_holds_ruling_promotes_and_leaves_worklist`
  (after `holds`: the gap row is gone **and** `class_asserted_property` now carries it — the only promotion
  path); `test_citation_census_accepts_label_question`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the three test files plus `tests/test_questions*.py tests/test_citation_audit*.py` → PASS.
- [ ] **Step 5: Update the suite total; commit** `feat: worklist for unconfirmed label class statements`.

### Task 9: Measure on the real releases, tune, gate

**Files:**
- Create: `tools/spl_class_census.py` (reads a built DB; prints every §7 figure; `--sample N` writes a
  stratified sample as a Markdown table of set_id, class, level, rule, scope, and the **existing quote
  window or offsets only** — never uncovered prose)
- Create: `docs/superpowers/specs/2026-11-xx-drugref-spl-class-mentions-results.md` (date of the run)
- Modify: `src/drugref/ingest/spl_advice.py` (`ADVICE_RULES` tuning only, each change with a test)
- Test: `tests/test_spl_tools_smoke.py` (the census tool on the test DB)

- [ ] **Step 1: Smoke test first** — `test_spl_class_census_runs` on the fixture DB → FAIL, then implement the
  tool, → PASS.
- [ ] **Step 2: Build `drugref_classmentions` from nothing**: chain + `onchigh` + `fda-cyp` + `drugcentral` +
  `ingest spl` on `downloads/OPENFDA` and `downloads/DAILYMED` (verify SHA-256s against the mining record §2
  first). Record wall time.
- [ ] **Step 3: Run the census** and record, per wording: class occurrences per axis split empty/populated;
  distinct statements; classes with ≥1 statement; yield per `match_form`; parenthetical-form misses (count
  of `cytochrome P450 \d\w* \(CYP` near a role word); `moiety_spans_inside_class_spans`; level distribution,
  mixed rate, `not_read` rate, scope split; share with a quote window; mentions within 80 chars of
  `Intervention` / `Clinical Impact` (table rows). Confirm drug × drug pairs equal the previous run's.
- [ ] **Step 4: Sample ≥ 50 per level and per scope (100 for `contraindicated` and
  `no_meaningful_interaction`)**; read each by hand from DailyMed by set_id; write the hand reading beside the
  machine one in the results record.
- [ ] **Step 5: Apply the gates**: zero wrong `no_meaningful_interaction` (else withhold the level: map to
  `not_read` in `read_advice`, with a test); zero `contraindicated` read lower; ≥ 90% agreement for `avoid`,
  `adjust_dose`, `monitor` per scope (else tune `ADVICE_RULES` with a failing test per fix and re-run, or
  withhold the level). Stop when every shipped level clears its gate. Record every figure and every change.
- [ ] **Step 6: Commit** `measure: SPL class mentions on the 2026-08 releases` (tool, record, tuning).

### Task 10: Documentation

**Files:**
- Modify: `docs-site/docs/decisions/evidence-is-not-an-assertion.md` (in place: drug × drug co-mentions
  stay evidence only; class mentions carry a machine-read advice level, still evidence, never an assertion;
  why the line moved — owner, 2026-11-08; "Last reviewed" date; Applies-to gains the db/066 objects)
- Modify: `docs/ROADMAP.md` (5c.3 class-half entry; § "Class profiles" "Feeds from next" now fed)
- Modify: `docs/PROJECT-NOTES.md` (round section with the measured figures by reference to the results
  record; code map gains the new modules; suite total)
- Modify: `docs/HANDOVER.md` (within its own stated bound; owner's post-merge clinical read of the sample
  as the first open item)

- [ ] **Step 1:** Edit the four files.
- [ ] **Step 2:** `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml` → no warnings; `uv run pytest` with the DB → green at the stated total; `ruff check src tests tools` clean.
- [ ] **Step 3: Commit** `docs: SPL class mentions round`.

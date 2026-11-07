# Class Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For every drug class, publish which properties the class shares (asserted, or observed in ≥ 75% of its
direct members) and what each member adds, plus a drug-through-a-chosen-class view, a curator ruling table and a
worklist gap for members the class property is *not confirmed* for.

**Architecture:** Four new migrations (`db/058`–`db/061`) of plain SQL views over the existing candidate and
curated tables — **the views are the consumer contract** (Cairn reads the schema directly). One new append-only,
signed curated table (`curated_property_ruling`) carries both class-level condition assertions and per-member
worklist answers. A thin Python read module and one CLI command make the views inspectable.

**Tech Stack:** PostgreSQL views/plpgsql triggers, Python 3 + psycopg, pytest, argparse.

**Spec:** [`docs/superpowers/specs/2026-11-07-drugref-class-profiles-design.md`](../specs/2026-11-07-drugref-class-profiles-design.md)
— read it first; this plan argues from it and does not restate its reasons.

## Global Constraints

- CLAUDE.md applies in full: TDD (failing test first), pure functions where logic lives in Python, inline
  docs a junior can follow, files under ~500 lines (`tests/test_module_size_cap.py` enforces a hard cap), no
  technical debt (fix or file an issue), AGPL-compatible only — **this slice adds no dependency and no source**.
- **Applied migrations are immutable.** Each task adds its own numbered file; a later task never edits an
  earlier task's file. Fix forward with a new file.
- **The suite count lives ONLY in PROJECT-NOTES § "How to run / test"** (the line carrying
  `THE ONE HOME FOR THIS NUMBER`). Every task that adds tests updates that number in the same commit, or the
  full `uv run pytest` run fails `tests/test_suite_count.py`. Never run the full suite with `-p no:cacheprovider`.
- DB tests need `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres'`; never run two
  pytest sessions on that database at once (#153).
- `ruff check .` must be clean. Do **not** run `ruff format` (the repo is hand-styled).
- Commit messages pass `.githooks/commit-msg` (`commit_lint.py`): never put a closing keyword (`fix`, `close`,
  `resolve`… any case, optional colon) immediately before a `#N`. End each commit message with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Spec values, verbatim: observed route = **at least 75%** of **direct** members (`carriers * 4 >= members * 3`)
  and **at least 2** direct members; MED-RT `concept_type = 'PK'` classes excluded (FDA-CYP's PK classes kept);
  *not confirmed* is never rendered as an exception or as safe; SPL (`spl_ddi_pair`) is never a property source;
  member properties come only from STATED records, never from `ddi_candidate_pair` or `curated_ddi_pair`.

**The property vocabulary** (used by every task): a property is `(relationship, object_kind, object_uuid)` with

| relationship | object_kind | member-level stated source |
|---|---|---|
| `may_treat` / `may_prevent` / `may_diagnose` | `condition` | `moiety_condition_indication` |
| `CI_with` | `condition` | `moiety_condition_contraindication` |
| `induces` | `condition` | `moiety_induced_condition` |
| `interacts_with` | `moiety` | `exact_ddi_pair`, **both orientations** |
| `interacts_with` | `class` | `class_contraindication` (subject side) |

## Review Focus

1. **A curator negates ONE record of a property that another record still states** (e.g. `curated_interaction`
   `applies = false` on `(X, C, CI_PE)` while `(X, C, CI_MoA)` stands) — the property must survive and must NOT
   become a confirmed exception. Test in Task 2.
2. **A drug appears twice in one class** — `class_membership` is keyed by `(moiety, class, relationship)`, so
   two relationship rows into one class are representable — it must count once in `members`. Test in Task 3.
3. **A class whose members are all subclass-only** (zero direct members) — it shows asserted properties only,
   no observed ones, no division by zero. Test in Task 3.
4. **A drug raised as *not confirmed* by two classes** — one question, one gap row. Test in Task 4.
5. **A live `cannot_establish` answer** — the member stays *not confirmed* on the profile AND leaves the
   worklist. Test in Task 4.

---

### Task 1: `curated_property_ruling` — the signed curated table and its writer

**Files:**
- Create: `db/058_class_profile_ruling.sql`
- Create: `src/drugref/curation_property.py`
- Create: `tests/class_profile_world.py` (plain helper functions, imported by Tasks 1–5's tests)
- Create: `tests/test_class_profile_ruling.py`
- Modify: `src/drugref/signing_contexts.py` (payload tuple + natural-key tuple + both registries)
- Modify: `src/drugref/releases.py:62` (`_CURATED_KINDS`)
- Modify: `src/drugref/questions.py` (the referencing-table guard in the large DELETE, and its docstring count)
- Modify: whichever existing tests enumerate curated tables and fail once the table exists — find them with
  `grep -ln curated_class_interaction tests/` (expect `test_signing_payload_coverage.py`, the live-key-index
  guard, `test_questions.py`'s citing-table test, `make_signing_vectors.py` only if it enumerates kinds).

**Interfaces:**
- Produces table `drugref.curated_property_ruling`:
  `curated_property_ruling_id bigint identity PK`; subject as **two nullable FKs** `subject_class_uuid`
  (→ `substance_class`) / `subject_moiety_uuid` (→ `substance_moiety`), `num_nonnulls(...) = 1`; object as
  **three nullable FKs** `object_condition_uuid` (→ `condition`) / `object_moiety_uuid` / `object_class_uuid`,
  `num_nonnulls(...) = 1`; **STORED generated** `subject_kind` (`'class'|'moiety'`), `subject_uuid`,
  `object_kind` (`'condition'|'moiety'|'class'`), `object_uuid` (the natural key needs NOT NULL columns:
  `forbid_multiple_live_assertions` raises on a NULL key column); `relationship text` CHECK in the six values
  above and coherent with `object_kind` (`interacts_with` ⇔ object is moiety or class); `verdict text` CHECK in
  `('holds','does_not_hold','cannot_establish')`; **a class subject may only rule a condition relationship and
  only `holds`/`does_not_hold`** (class × class interactions already live in `curated_class_interaction`);
  `question_uuid` FK → `open_question` `ON DELETE CASCADE`, nullable; `source` CHECK `('DRUGREF')`;
  `reviewed_by`, `reviewed_against` NOT NULL, `reviewed_at` default `now()`; `superseded_by` self-FK.
  Natural key: `(subject_kind, subject_uuid, relationship, object_kind, object_uuid)`. Triggers, indexes and
  `COMMENT ON` exactly as `db/032`'s `curated_class_interaction` (append-only, deferred single-live,
  `<table>_live_key` partial index, `<table>_by_question` index). `signature_target_kind` row
  `('curated_property_ruling', 'curated_property_ruling', 'curated_property_ruling_id',
  'curated_property_ruling/v1')`.
- Produces `curation_property.record_property_ruling(conn, *, subject_class_uuid: UUID | None = None,
  subject_moiety_uuid: UUID | None = None, relationship: str, object_kind: str, object_uuid: UUID,
  verdict: str, reviewed_by: str, reviewed_against: str, question_uuid: UUID | None = None) -> int` —
  returns the live row's id. **Idempotent by comparison**: if the live row for the key already has this
  verdict, returns its id and writes nothing; otherwise INSERTs and supersedes via `overlay.supersede`. Raises
  `ValueError` unless exactly one subject is given. Vocabulary is NOT restated in Python: a bad value reaches
  the INSERT and raises there.
- Produces `tests/class_profile_world.py`: `run(conn, source="MED-RT") -> int`,
  `moiety(conn, run, name) -> UUID`, `klass(conn, run, name, *, source="MED-RT", concept_type="EPC") -> UUID`,
  `member(conn, run, moiety, klass, relationship="has_EPC")`, `parent(conn, run, child, parent)`,
  `condition(conn, run, name) -> UUID`, `indication(conn, run, moiety, condition, relationship="may_treat")`,
  `contraindication(conn, run, moiety, condition)`, `induced(conn, run, moiety, condition)`,
  `class_ci(conn, run, moiety, klass, relationship="CI_MoA", source="MED-RT")`,
  `class_pair_ci(conn, run, subject_class, object_class, relationship="CI_MoA")`,
  `moiety_ci(conn, run, subject, object)` (the `moiety_contraindication` arm of `exact_ddi_pair`). Each inserts
  the minimum valid row (copy column lists from the existing tests that already insert into these tables).

- [ ] **Step 1: Write the failing tests** in `tests/test_class_profile_ruling.py`:
  - `test_a_moiety_holds_ruling_round_trips` — `record_property_ruling(subject_moiety_uuid=m,
    relationship="CI_with", object_kind="condition", object_uuid=c, verdict="holds", ...)` → one live row with
    `subject_kind='moiety'`, `object_kind='condition'`, generated `subject_uuid == m`.
  - `test_re_recording_the_same_verdict_writes_nothing` — second identical call returns the same id; row count 1.
  - `test_a_changed_verdict_supersedes` — `holds` then `does_not_hold` → 2 rows, exactly 1 live, old row's
    `superseded_by` = new id.
  - `test_a_class_subject_may_not_rule_an_interaction` and `test_a_class_subject_may_not_answer_cannot_establish`
    — `CheckViolation`.
  - `test_relationship_must_match_object_kind` — `interacts_with` + condition object → `CheckViolation`.
  - `test_exactly_one_subject` — both or neither → `ValueError` from the writer.
  - `test_rows_are_append_only` — `UPDATE ... SET verdict` raises (the `forbid_overlay_rewrite` message).
  - `test_the_table_is_a_signature_target_and_in_every_release` — row in `signature_target_kind`;
    `"curated_property_ruling" in releases._CURATED_KINDS`; `"curated_property_ruling/v1"` in
    `signing_contexts`' payload and natural-key registries.
- [ ] **Step 2: Run** `uv run pytest tests/test_class_profile_ruling.py -v` — Expected: FAIL (relation does not exist).
- [ ] **Step 3: Write `db/058_class_profile_ruling.sql`, `curation_property.py`, and the registry edits** listed
  under Files. Payload context `CURATED_PROPERTY_RULING_V1` lists the stored (non-generated) ruling columns in
  table order, mirroring `CURATED_CLASS_INTERACTION_V1`'s choice of columns; its natural-key tuple is the five
  natural-key columns above.
- [ ] **Step 4: Run** `uv run pytest tests/test_class_profile_ruling.py tests/test_signing_payload_coverage.py
  tests/test_questions.py -v` and then the whole suite with the DSN — Expected: PASS everywhere, including the
  pre-existing "every curated kind is covered by a release" and live-key-index tests.
- [ ] **Step 5: Update the suite count in PROJECT-NOTES, `ruff check .`, commit**
  `feat: curated_property_ruling, the class-profile ruling table (db/058)`.

---

### Task 2: member properties and class assertions — `db/059`

**Files:**
- Create: `db/059_class_profile_member_property.sql`
- Create: `tests/test_class_profile_member_property.py`

**Interfaces:**
- Consumes: Task 1's table and `tests/class_profile_world.py`.
- Produces view `drugref.class_profile_member(class_uuid, moiety_uuid)` — DISTINCT direct members from
  `class_membership`, excluding classes with `source = 'MED-RT' AND concept_type = 'PK'`.
- Produces view `drugref.member_property(moiety_uuid, relationship, object_kind, object_uuid, sources text[])`
  — the **effective** stated properties: every row of the five stated sources in the vocabulary table, minus
  individual records a live curated row negates (`curated_interaction.applies = false` on the same
  `(subject_moiety_uuid, object_class_uuid, relationship)` removes only that `class_contraindication` record),
  grouped to one row per property; plus live `curated_property_ruling` moiety rulings with `verdict='holds'`
  (source `'DRUGREF'`); minus properties with a live moiety ruling `verdict='does_not_hold'`.
- Produces view `drugref.member_property_negation(moiety_uuid, relationship, object_kind, object_uuid)` — live
  moiety `does_not_hold` rulings, plus `interacts_with`/class properties ALL of whose `class_contraindication`
  records are negated by `curated_interaction.applies = false`. (These are the *confirmed exceptions*.)
- Produces view `drugref.class_asserted_property(class_uuid, relationship, object_kind, object_uuid,
  asserted_by text[])` — one row per (class, property) **asserted on that class itself**:
  `class_pair_contraindication` gives `interacts_with`/class on **both** classes (record-level negation by
  `curated_class_interaction.applies = false` on the same `(subject, object, relationship)`);
  live `curated_class_interaction.applies = true` likewise both sides; `class_contraindication` *X → C* gives C
  `interacts_with`/moiety X (record-level negation as above); live class `curated_property_ruling` with
  `verdict='holds'`. `asserted_by` lists the contributing `source` values.

- [ ] **Step 1: Write the failing tests**:
  - `test_membership_is_direct_only` — drug in subclass D (`parent(D, C)`) is in `class_profile_member` for D,
    not C.
  - `test_medrt_pk_classes_are_excluded_but_fda_cyp_pk_classes_are_kept`.
  - `test_each_condition_source_yields_its_relationship` — one indication, one contraindication, one induced
    condition → three `member_property` rows with `may_treat`, `CI_with`, `induces`.
  - `test_a_drug_pair_is_a_property_of_both_drugs` — `moiety_ci(a, b)` → `(a, interacts_with, moiety, b)` and
    `(b, interacts_with, moiety, a)`.
  - `test_expanded_pairs_are_not_member_properties` — `class_ci(x, C)` with C having members p, q →
    `member_property` has `(x, interacts_with, class, C)` and **no** `(x, interacts_with, moiety, p)`.
  - `test_two_sources_for_one_fact_count_once` — the same `(m, CI_with, c)` from two sources → one row,
    `sources` has both.
  - `test_negating_one_record_keeps_a_property_another_record_states` (Review Focus 1) — `class_ci(x, C,
    'CI_MoA')`, `class_ci(x, C, 'CI_PE')`, curated `applies=false` on the CI_PE one → property present, absent
    from `member_property_negation`.
  - `test_negating_every_record_makes_a_confirmed_exception` — the same with only one record → property absent
    from `member_property`, present in `member_property_negation`.
  - `test_holds_adds_and_does_not_hold_removes` — moiety rulings.
  - `test_a_class_pair_rule_is_asserted_on_both_classes` and
    `test_a_drug_to_class_rule_is_asserted_on_the_object_class` (C gets `interacts_with`/moiety x).
  - `test_a_curated_applies_false_class_rule_is_not_asserted`.
  - `test_a_class_holds_ruling_asserts_a_condition_property`.
- [ ] **Step 2: Run** `uv run pytest tests/test_class_profile_member_property.py -v` — Expected: FAIL.
- [ ] **Step 3: Write `db/059_class_profile_member_property.sql`** with the three views and a `COMMENT ON VIEW`
  for each stating its contract in one paragraph (what a row means, what it deliberately excludes — the
  expanded pair views and SPL).
- [ ] **Step 4: Run** the file, then the full suite — Expected: PASS.
- [ ] **Step 5: Suite count, `ruff check .`, commit** `feat: class-profile member properties and class assertions (db/059)`.

---

### Task 3: class properties, member standing, peculiarities, subclass-only — `db/060`

**Files:**
- Create: `db/060_class_profile_class_property.sql`
- Create: `tests/test_class_profile_class_property.py`

**Interfaces:**
- Consumes: Task 2's four views.
- Produces view `drugref.class_property(class_uuid, relationship, object_kind, object_uuid, is_asserted bool,
  asserted_via uuid[], asserted_by text[], is_observed bool, carriers int, members int)`:
  - **asserted** = asserted on the class **or on any proper ancestor** (join `class_subtree(root_uuid,
    class_uuid)` — the root is in its own subtree, so `asserted_via` holds the class itself and/or ancestors).
    This is how asserted properties pass down to subclass-only drugs (spec §4, owner).
  - **observed** = `members >= 2 AND carriers * 4 >= members * 3`, where `members` = count of direct members
    and `carriers` = direct members whose `member_property` holds it. Observed is computed **only from the
    class's own direct members** and never inherited.
  - One row per (class, property) that is asserted, observed, or both; `carriers`/`members` filled for every row.
- Produces view `drugref.class_property_member(class_uuid, relationship, object_kind, object_uuid,
  moiety_uuid, standing text)` — one row per `class_property` row × direct member, `standing` in **this
  precedence**: `confirmed_exception` (in `member_property_negation`) › `carries` (in `member_property`) ›
  `inherits` (`is_asserted`) › `not_confirmed`.
- Produces view `drugref.class_member_peculiarity(class_uuid, moiety_uuid, relationship, object_kind,
  object_uuid)` — a direct member's `member_property` rows that are not a `class_property` of that class.
- Produces view `drugref.class_subclass_only_member(class_uuid, moiety_uuid, via_class_uuid)` — drugs that are
  direct members of a proper descendant (`class_subtree`, `class_uuid <> root_uuid`) and **not** direct members
  of the class.
- **The drug-through-a-lens view needs no view of its own**: for drug d and lens L (one of d's
  `class_profile_member` classes), *shared* = `class_property_member` rows for (L, d) with standing `carries` or
  `inherits`; *peculiar* = `class_member_peculiarity` for (L, d). State this in the `COMMENT ON VIEW` of both.

- [ ] **Step 1: Write the failing tests**:
  - `test_three_of_four_is_observed_and_two_of_four_is_not` (owner's *at least 75%*).
  - `test_a_one_member_class_observes_nothing` and `test_two_of_two_is_observed`.
  - `test_a_drug_in_a_class_twice_counts_once` (Review Focus 2) — two `class_membership` rows for one drug and
    one class with two different admitted relationships → `members` counts it once.
  - `test_a_class_with_no_direct_members_has_only_asserted_properties` (Review Focus 3).
  - `test_exact_match_only` — members indicated for *hypertension* and *essential hypertension* (with
    `condition_parent` linking them) do not pool.
  - `test_an_ancestor_assertion_reaches_a_subclass` — asserted on C, D is a child of C: `class_property` for D
    has it with `asserted_via = {C}`; a D member without its own record stands `inherits`.
  - `test_an_observed_property_does_not_descend` — observed on C, absent on D.
  - `test_standing_precedence` — one class, four members covering `confirmed_exception`, `carries`, `inherits`,
    `not_confirmed`; and a member with both a stated record and a `does_not_hold` ruling stands
    `confirmed_exception`.
  - `test_peculiarities_exclude_class_properties`.
  - `test_subclass_only_drugs_are_listed_not_counted` — atenolol in D only: in `class_subclass_only_member` for C
    with `via_class_uuid = D`, absent from C's `class_property_member`, C's `members` unchanged.
- [ ] **Step 2: Run** `uv run pytest tests/test_class_profile_class_property.py -v` — Expected: FAIL.
- [ ] **Step 3: Write `db/060_class_profile_class_property.sql`** with the four views and their comments.
- [ ] **Step 4: Run** the file, then the full suite — Expected: PASS.
- [ ] **Step 5: Suite count, `ruff check .`, commit** `feat: class properties, member standing and peculiarities (db/060)`.

---

### Task 4: the worklist gap kind `unconfirmed_class_property` — `db/061`

**Files:**
- Create: `db/061_class_profile_gap.sql`
- Create: `src/drugref/gap_sources_class_profile.py`
- Create: `tests/test_class_profile_gap.py`
- Modify: `src/drugref/questions.py:27` (merge `CLASS_PROFILE_GAP_SOURCES` into `_GAP_SOURCES`, last)
- Modify: `tests/test_question_ids.py:197` (the ordered kind list gains `"unconfirmed_class_property"` last)

**Interfaces:**
- Consumes: Task 3's `class_property_member`, Task 1's table.
- Produces view `drugref.gap_unconfirmed_class_property(moiety_uuid, relationship, object_kind, object_uuid,
  raising_classes uuid[])` — one row per **(member, property)** with `standing = 'not_confirmed'` in at least
  one class, and **no live `curated_property_ruling` for that moiety and property, of any verdict**.
- Produces `gap_sources_class_profile.CLASS_PROFILE_GAP_SOURCES = {"unconfirmed_class_property": {"view": ...,
  "key_sql": ..., "text_sql": ...}}`, in the shape of `gap_sources_registry.py:18-33`. **Frozen gap key**:
  `'CLASSPROP:' || moiety_uuid || ':' || relationship || ':' || object_kind || ':' || object_uuid`. Question
  text names the drug (`substance_moiety.display_name`), the relationship, the object's name
  (`condition.name` / `display_name` / `class_name`) and the raising classes' names, and asks the curator to
  answer *holds*, *does not hold* or *cannot be established*.
- `db/061` widens `open_question_gap_kind` by restating **the live catalog's 18 kinds verbatim** (copy them from
  `pg_get_constraintdef` on a migrated test DB, as `db/049:380-409` did) plus `'unconfirmed_class_property'`,
  guarded by the same `DO $$ … IF NOT EXISTS … $$` idiom.

- [ ] **Step 1: Write the failing tests**:
  - `test_the_gap_kind_is_admitted_and_registered` — the trio (view, `_GAP_SOURCES`, CHECK), as in
    `test_drugcentral_gap.py:111`.
  - `test_a_not_confirmed_member_raises_one_question_with_a_frozen_key` — after
    `questions.register_from_gaps(conn, run)`, one `open_question` with
    `gap_key == f"CLASSPROP:{m}:CI_with:condition:{c}"` and `question_uuid ==
    ids.mint_question_uuid("unconfirmed_class_property", that_key)`.
  - `test_two_classes_raise_one_question` (Review Focus 4) — `raising_classes` has both.
  - `test_any_live_answer_closes_the_question` — parametrize over `holds`, `does_not_hold`, `cannot_establish`;
    gap row gone. And for `cannot_establish` (Review Focus 5): `class_property_member.standing` is still
    `not_confirmed`.
  - `test_inherited_and_carried_members_raise_nothing`.
- [ ] **Step 2: Run** `uv run pytest tests/test_class_profile_gap.py -v` — Expected: FAIL.
- [ ] **Step 3: Write `db/061`, `gap_sources_class_profile.py`, and the two edits.**
- [ ] **Step 4: Run** the file, `tests/test_question_ids.py`, then the full suite — Expected: PASS.
- [ ] **Step 5: Suite count, `ruff check .`, commit** `feat: unconfirmed_class_property gap kind (db/061)`.

---

### Task 5: read module and `drugref profile` CLI

**Files:**
- Create: `src/drugref/class_profile_read.py`
- Create: `src/drugref/cli_class_profile.py`
- Create: `tests/test_class_profile_read.py`, `tests/test_cli_class_profile.py`
- Modify: `src/drugref/cli.py` (one `cli_class_profile.register(commands)` line beside `cli_curate.register`;
  cli.py is at 486 of 500 lines — add nothing else)

**Interfaces:**
- Consumes: Tasks 1–3's views and `curation_property.record_property_ruling`.
- Produces in `class_profile_read.py` (frozen dataclasses; SQL lives here, never in the cli module):
  - `PropertyLine(relationship: str, object_kind: str, object_uuid: UUID, object_name: str)`
  - `ClassProperty(prop: PropertyLine, is_asserted: bool, asserted_via: tuple[str, ...], asserted_by:
    tuple[str, ...], is_observed: bool, carriers: int, members: int, not_confirmed: tuple[str, ...],
    confirmed_exceptions: tuple[str, ...])` — names, not UUIDs, in the tuples.
  - `ClassProfile(class_uuid: UUID, class_name: str, source: str, concept_type: str, members: tuple[str, ...],
    properties: tuple[ClassProperty, ...], peculiarities: Mapping[str, tuple[PropertyLine, ...]],
    subclass_only: tuple[tuple[str, str], ...])` — `(drug name, via class name)`.
  - `DrugLens(class_uuid: UUID, class_name: str, shared: tuple[tuple[PropertyLine, str], ...],
    peculiar: tuple[PropertyLine, ...])` — `shared` pairs a property with its standing (`carries`/`inherits`)
    and, when inherited from an ancestor, the reader may append `via <class>` to the standing string.
  - `DrugProfile(moiety_uuid: UUID, name: str, class_tags: tuple[tuple[UUID, str], ...], lenses:
    tuple[DrugLens, ...])`
  - `class_profile(conn, class_uuid: UUID) -> ClassProfile` (raises `LookupError` for an unknown class)
  - `drug_profile(conn, moiety_uuid: UUID, lens_class_uuids: Sequence[UUID] = ()) -> DrugProfile` — empty
    `lens_class_uuids` means every class tag; a lens that is not one of the drug's tags raises `ValueError`.
- Produces in `cli_class_profile.py`: `register(commands)` adding `drugref profile class <class-uuid>`,
  `drugref profile drug <moiety-uuid> [--lens CLASS_UUID ...]`, and `drugref profile rule
  (--class UUID | --moiety UUID) --relationship R --object-kind K --object UUID --verdict V --reviewed-by S
  --reviewed-against S [--question UUID]`; and two PURE renderers `render_class_profile(p: ClassProfile) ->
  list[str]` and `render_drug_profile(p: DrugProfile) -> list[str]`. Exact copy the renderers must use:
  `"not confirmed for: <names>"`, `"confirmed exception: <names>"`, `"in subclasses only:"`,
  `"asserted (<sources>)"`, `"observed in <carriers> of <members> members"`. Never the words "safe", "does not
  apply" or "exception" without "confirmed".
  `shortcut: UUIDs only on the command line, no name lookup — add one when a human operator uses it daily.`

- [ ] **Step 1: Write the failing tests** — renderer tests need no DB: build a `ClassProfile` by hand and assert
  the exact lines above appear, and that `"safe"` and a bare `"exception"` never do. DB tests:
  `test_class_profile_reads_the_views` (one class with an observed property, one not-confirmed member, one
  subclass-only drug), `test_drug_profile_defaults_to_every_tag`, `test_a_foreign_lens_is_refused`,
  `test_profile_rule_writes_through_the_writer` (CLI via `cli.main([...])` with `--dsn`; then the row exists).
- [ ] **Step 2: Run** both test files — Expected: FAIL.
- [ ] **Step 3: Implement** the two modules and the one-line `cli.py` registration.
- [ ] **Step 4: Run** the files, `tests/test_module_size_cap.py`, then the full suite — Expected: PASS.
- [ ] **Step 5: Suite count, `ruff check .`, commit** `feat: drugref profile -- class and drug-through-a-lens views`.

---

### Task 6: measure on a real database, then document

**Files:**
- Create: `docs/superpowers/specs/2026-11-07-drugref-class-profiles-results.md`
- Modify: `docs/PROJECT-NOTES.md` (a new § "The class-profiles round", the schema/code map, the open-issue
  ledger), `docs/ROADMAP.md` (the new slice entry, marked done or partially done by what was measured),
  `docs/HANDOVER.md` (regenerated, within the bound its own header states)
- Possibly create: `tools/class_profile_census.py` only if the measurement queries are long enough to deserve a
  file (one SQL query per figure is fine inline in the results record).

**Interfaces:** none produced.

- [ ] **Step 1: Build a fresh database** under a new name (`drugref_profiles`): `createdb`, `drugref migrate`,
  then `drugref ingest chain --downloads downloads/ …` with every release tag the chain accepts, plus
  `ingest fda-cyp` and `ingest drugcentral` (SPL is not a property source and may be skipped). Record each
  command and its wall time. Never patch an older `drugref*` database.
- [ ] **Step 2: Measure and record**, per class axis (`source`, `concept_type`) and per property kind: classes
  with ≥ 1 observed property; observed vs asserted vs both; `class_property_member` standings totals; **the
  number of `gap_unconfirmed_class_property` rows** and how long `register_from_gaps` takes with the new kind;
  the wall time of `SELECT count(*)` on each new view.
- [ ] **Step 3: Spot-check three familiar classes** (beta blockers, ACE inhibitors, SSRIs — find their
  `class_uuid` by `class_name ILIKE`) with `drugref profile class` and paste the output into the results record
  for the owner's clinical read.
- [ ] **Step 4: Decide, in writing**: if the gap count makes the worklist unusable or a view is too slow for a
  per-ingest gap registration, file an issue with the numbers rather than tuning in this round (spec §7).
- [ ] **Step 5: Update the three docs**, run the full suite once more (green, count unchanged), commit
  `docs: class-profiles round -- measured on drugref_profiles`.

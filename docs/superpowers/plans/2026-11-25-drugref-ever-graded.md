# Ever-graded pairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every pair a reviewed interaction grade has ever reached (both grains), banner any such pair
that is no longer graded under its ruling until a curator acknowledges it, and list open departures for
curators.

**Architecture:** `db/083` adds an insert-only observation table, an insert-only acknowledgement table, and a
SQL refresh function fired by deferred constraint triggers at COMMIT of any transaction that inserts a ruling
or policy row or finishes an ingest run. A new pure-SQL Python module `graded_pairs` reads departures and
writes acknowledgements; the CLI prints them (`interactions` banner, `audit departures`, `curate
acknowledge-departure`). The release export carries both tables; the replay compares observations and loads
acknowledgements.

**Tech Stack:** Python 3 + psycopg 3, PostgreSQL (plpgsql), pytest (DB-gated via `DRUGREF_TEST_DSN`), Rust
reviewer-service (sqlx, one ignored DB test only).

**Spec:** `docs/superpowers/specs/2026-11-24-drugref-ever-graded-design.md` (approved 2026-11-25). Read it
first; section numbers below (§5.x) refer to it.

## Global Constraints

- Migration file: `db/083_graded_pair.sql`. Never edit `db/001`–`db/082`.
- Insert-only tables use `drugref.forbid_any_rewrite()` (db/080 precedent), BEFORE UPDATE OR DELETE.
- Observation keys use `curated_ddi_pair`'s vocabulary: `rule_grain IN ('moiety_rule', 'class_rule')`.
- `captured IN ('observed', 'backfill_db083')`.
- Exit codes: `3` = `EXIT_INCOMPLETE` (departures exist), `2` = unknown id, `1` = refused, `0` = ok.
- Code files under ~500 lines: `release_replay.py` (498) is split first; `cli_interactions.py` (438) loses
  its banner to a new module rather than growing.
- Hand style, no `ruff format`; `ruff check .` clean. Inline docs a junior can follow (rule 3). No new
  dependencies. Every DB test runs with `DRUGREF_TEST_DSN="host=localhost port=5532 dbname=drugref_test
  user=postgres"`.
- The suite total lives only in PROJECT-NOTES § "How to run / test" (`# NNNN tests` line, currently 3649);
  `uv run pytest` fails if it disagrees, so update it in the task that changes the count.
- Tests run in a rolled-back transaction, so deferred triggers never fire there. Tests stand in for a commit
  with `tests/graded_pair_helpers.py::commit_point(conn)` (Task 2), which runs `SET CONSTRAINTS <the refresh
  triggers> IMMEDIATE` then `... DEFERRED`; that fires the queued refreshes exactly as COMMIT would.

## Review Focus

1. **Two open episodes for one pair** (two concurrent commits both refreshed): the banner must list the pair
   once, and acknowledging must close both. Test in Task 4.
2. **A class ruling of a class against itself**: acknowledging drug D must close D's pairs in both
   directions (D as subject and as partner). Test in Task 4.
3. **A curator pastes a superseded ruling id** from an old banner: acknowledge resolves it to the ruling's
   natural key and works. Test in Task 4.
4. **Withdrawn, then reinstated**: a departed key corrected to `applies = false` stops bannering; corrected
   back to `applies = true` while the drug is still gone, it banners again (the episode was never
   acknowledged). Test in Task 3.
5. **A moiety ruling written from the GUI (Rust)** is observed at its commit without any Python. Test in
   Task 8.

---

### Task 1: Split `release_replay.py` (verbatim move, no behaviour change)

**Files:**
- Create: `src/drugref/replay_loader.py`
- Modify: `src/drugref/release_replay.py` (remove `_columns` and `class _Loader`, lines ~299–436)

**Interfaces:**
- Produces: `replay_loader._Loader` and `replay_loader._columns`, unchanged signatures; `release_replay`
  imports `_Loader` from it. `_TIMED`, `_REACH`, `_SNAPSHOT`, `_RULINGS` stay in `release_replay`
  (the loader imports them from there only if it already used them; otherwise move them with it and
  re-export from `release_replay`, since tests patch `release_replay.*`).

- [ ] **Step 1:** Record the suite count: `uv run pytest -q 2>&1 | tail -1` (expect `3649 passed`).
- [ ] **Step 2:** Move the exact line range of `_columns` and `_Loader` into `replay_loader.py` with a module
  docstring saying why it was split (the 500-line cap; the loader is the part the curated export grows).
  Check the range for off-by-one against `grep -n "^def \|^class " src/drugref/release_replay.py`.
- [ ] **Step 3:** Run `uv run pytest -q tests/test_release_replay*.py` then the full suite; the count must be
  exactly 3649 and every per-module test that scans modules (e.g. the one that picked up
  `replay_adapters.py` in #270) must pass. `wc -l` both files: each under 500.
- [ ] **Step 4:** Commit: `refactor: move the replay loader out of release_replay (500-line cap)`.

---

### Task 2: `db/083` — observation and acknowledgement tables, refresh function, triggers, backfill

**Files:**
- Create: `db/083_graded_pair.sql`
- Create: `src/drugref/graded_pairs.py`
- Create: `tests/graded_pair_helpers.py`, `tests/test_graded_pairs_refresh.py`

**Interfaces:**
- Produces (SQL): `drugref.graded_pair_observation(observation_id bigserial PK, rule_grain, subject_key uuid,
  object_class_uuid uuid → substance_class, relationship text, subject_moiety uuid → substance_moiety,
  partner_moiety uuid → substance_moiety, captured, first_seen_at timestamptz DEFAULT now())` with an index on
  (rule_grain, subject_key, object_class_uuid, relationship, subject_moiety, partner_moiety);
  `drugref.graded_pair_departure_ack(ack_id bigserial PK, observation_id bigint NOT NULL UNIQUE →
  graded_pair_observation, rationale text NOT NULL CHECK (btrim(rationale) <> ''), reviewed_by text NOT
  NULL, reviewed_against text NOT NULL, acknowledged_at timestamptz NOT NULL DEFAULT now())`;
  `drugref.refresh_graded_pairs(captured text DEFAULT 'observed') RETURNS integer`.
  `subject_key` = `subject_moiety` for `moiety_rule`, `via_subject_class` for `class_rule`; `object_class_uuid`
  = `curated_ddi_pair.via_class`.
- Produces (Python, `graded_pairs.py`): `OBSERVATION_TABLE`, `ACK_TABLE` (qualified names),
  `REFRESH_TRIGGERS: tuple[str, ...]` (the four qualified trigger names), `refresh(conn) -> int`.
- Produces (tests): `graded_pair_helpers.commit_point(conn) -> None`, `observations(conn) -> set[tuple]` of
  (rule_grain, subject_key, object_class_uuid, relationship, subject_moiety, partner_moiety, captured).

Refresh rule (§5.2): insert one row per DISTINCT key+pair `curated_ddi_pair` returns, unless that key+pair
already has an observation with no row in `graded_pair_departure_ack`. Triggers (all `CREATE CONSTRAINT
TRIGGER ... DEFERRABLE INITIALLY DEFERRED FOR EACH ROW`, calling one trigger function that `PERFORM`s the
refresh and returns NULL): `AFTER INSERT` on `curated_interaction`, `curated_class_interaction`,
`class_expansion_policy`; `AFTER UPDATE OF finished_at ON ingest_run ... WHEN (OLD.finished_at IS NULL AND
NEW.finished_at IS NOT NULL)`. Backfill (§5.5) between `-- backfill:begin` / `-- backfill:end`:
`SELECT drugref.refresh_graded_pairs('backfill_db083')`, then for every `curated_class_interaction` version
with `applies`, the cross product of its db/080 subject and partner reach rows, minus self-pairs, as
`class_rule` observations not already open. Comment each design reason in the migration (why natural key,
why deferred, why ingest_run is the ingest choke point, why order relative to the single-live deferred
triggers does not matter: the refresh only reads).

- [ ] **Step 1: Write the failing tests** in `tests/test_graded_pairs_refresh.py` (reuse
  `tests.test_class_subject_read_path._a_graded_class_rule` and the moiety-grain fixture that
  `tests/test_class_subject_read_path.py` or `tests/test_curated_read*.py` already uses for a graded
  `curated_interaction`; find it with `grep -n "def _a_" tests/test_curated*.py tests/test_class_subject*.py`):
  - `test_a_class_ruling_is_observed_at_its_commit_point`: before `commit_point` the class ruling's pairs are
    not in `observations(conn)`; after it, each (subject, partner) pair appears once as
    `('class_rule', A, B, 'CI_MoA', s, p, 'observed')`.
  - `test_a_moiety_ruling_is_observed_at_its_commit_point`: same for `('moiety_rule', s, B, rel, s, p,
    'observed')`.
  - `test_finishing_an_ingest_run_observes_new_members`: rule graded and committed; add a moiety to the
    subject class; `provenance.finish_run(conn, run)` on a fresh open run; `commit_point`; the new pair is
    observed.
  - `test_an_open_episode_is_not_observed_twice`: two `commit_point`s → `refresh` count 0 the second time
    and one row per pair.
  - `test_an_acknowledged_pair_graded_again_opens_a_new_episode`: insert an ack row for the observation;
    `refresh(conn)` returns 1 and the pair now has two observations.
  - `test_applies_false_and_orphans_are_never_observed`.
  - `test_every_refresh_trigger_exists_and_is_deferred`: each name in `graded_pairs.REFRESH_TRIGGERS` is in
    `pg_trigger` with `tgdeferrable AND tginitdeferred` (the spec's guard, §6.6).
  - `test_observations_and_acks_are_insert_only`: UPDATE and DELETE each raise
    `psycopg.errors.RaiseException` (match how `tests/test_class_ruling_reach*.py` asserts db/080's).
  - `test_the_backfill_records_reach_and_marks_it`: run the backfill block's SQL (extract it between the
    markers the way existing backfill tests do: `grep -rn "backfill:begin" tests/`) on a ruling whose drug
    left after its db/080 snapshot; the departed pair is observed with `captured = 'backfill_db083'`.
- [ ] **Step 2:** `uv run pytest tests/test_graded_pairs_refresh.py -v` → FAIL (no table / no module).
- [ ] **Step 3:** Write `db/083_graded_pair.sql` and `graded_pairs.py` (`refresh` = `SELECT
  drugref.refresh_graded_pairs()`) and `tests/graded_pair_helpers.py`.
- [ ] **Step 4:** Run the new file, then the full suite. Expected: all pass. The export-coverage guard
  (`tests/test_release_export_coverage.py`) WILL FAIL on the two new write-once tables: add both to
  `release_archive._EXPORT_TABLES` now (key columns `observation_id`, `ack_id`) and to the
  `release_replay` assertion set as untimed tables, and leave their replay handling to Task 7 with a
  `# Task 7` comment only if the replay tests still pass; otherwise do the minimum in Task 7 now. Update the
  suite count line.
- [ ] **Step 5:** Commit: `feat: db/083 records every pair a reviewed grade reached (#281, #279)`.

---

### Task 3: The departure read (both grains, per ruling)

**Files:**
- Modify: `src/drugref/graded_pairs.py`
- Create: `tests/test_graded_pairs_departed.py`
- Modify: `tests/test_cli_interactions_departed.py` (its §1 read tests move to the new file, rewritten)

**Interfaces:**
- Consumes: Task 2's tables, `refresh`, `commit_point`.
- Produces:

```python
@dataclass(frozen=True)
class Departure:
    rule_grain: str                 # 'moiety_rule' | 'class_rule'
    ruling_id: int                  # the LIVE version's curated_interaction_id / curated_class_interaction_id
    subject_key: uuid.UUID          # subject moiety (moiety grain) or subject class (class grain)
    object_class: uuid.UUID
    relationship: str
    severity: str
    reviewed_by: str
    reviewed_against: str
    backfilled: bool                # any open episode captured 'backfill_db083'
    first_seen_at: datetime         # earliest open episode among `pairs`
    pairs: frozenset[tuple[uuid.UUID, uuid.UUID]]   # (subject moiety, partner moiety)

def departed(conn, subject: uuid.UUID | None = None,
             partner: uuid.UUID | None = None) -> list[Departure]: ...
```

A departure (§5.3): an open episode (no ack) whose key+pair `curated_ddi_pair` does not return now, whose
key's live version exists and `applies`, and whose live version is not in `curated_target_unresolved`
(match on `target_table` + natural key; that is #121's banner). `subject` filters `subject_moiety`,
`partner` filters `partner_moiety`; both None = every departure (Task 6). One `Departure` per (grain, live
ruling). Order: live ruling's `reviewed_at` DESC, then grain, then id. Read `curated_ddi_pair` once per call
(`MATERIALIZED` CTE).

- [ ] **Step 1: Write the failing tests** in `tests/test_graded_pairs_departed.py`, porting #256's cases from
  `tests/test_cli_interactions_departed.py` §1 (`_a_rule_x_left` gains a `commit_point(conn)` before the
  DELETE) and adding:
  - the six #256 cases, asserting on `graded_pairs.departed` (`ruling_id`, `pairs`, `severity`,
    `rule_grain == 'class_rule'`, `backfilled is False`);
  - `test_a_correction_after_the_drug_left_is_a_departure` (replaces
    `test_a_correction_after_the_drug_left_is_not_a_departure`; #281 §1): after `_rerule` + `commit_point`,
    `departed(conn, x)` has one entry whose `ruling_id` is the NEW live id and whose pairs are `{(x, p)}`;
  - `test_a_drug_that_joined_after_the_ruling_and_left_is_a_departure` (#281 §2): ruling committed; Y added
    to A; `commit_point` (as a finished ingest would); Y deleted; `departed(conn, y)` reports `(y, p)`;
  - `test_a_moiety_rule_partner_that_left_its_class_is_a_departure` (#279) and
    `test_a_deny_that_cuts_a_moiety_rules_subclass_is_a_departure` (#279), `rule_grain == 'moiety_rule'`;
  - `test_a_class_rule_departing_while_a_moiety_rule_still_grades_the_pair_is_a_departure` (per ruling);
  - `test_withdrawn_then_reinstated_banners_again` (Review Focus 4);
  - `test_a_drug_that_comes_back_is_no_longer_departed`;
  - `test_no_subject_lists_every_departure`.
- [ ] **Step 2:** Run → FAIL (`graded_pairs.departed` missing).
- [ ] **Step 3:** Implement `Departure` and `departed`. Leave `class_ruling_reach.departed_reach` in place:
  the banner still calls it until Task 5, which deletes it.
- [ ] **Step 4:** Run the new file and the full suite; update the suite count line.
- [ ] **Step 5:** Commit: `feat: a reviewed grade that stops reaching a pair departs, both grains (#281, #279)`.

---

### Task 4: Acknowledging a departure (library + `drugref curate acknowledge-departure`)

**Files:**
- Modify: `src/drugref/graded_pairs.py`, `src/drugref/cli_curate.py`
- Create: `tests/test_graded_pairs_ack.py`

**Interfaces:**
- Produces:

```python
class UnknownRuling(LookupError): ...      # no ruling (any version) with that id in that grain
class NothingDeparted(ValueError): ...     # DRUG is in no open departed episode of that ruling's key

def acknowledge(conn, grain: str, ruling_id: int, drug: uuid.UUID, *, rationale: str,
                reviewed_by: str, reviewed_against: str) -> frozenset[tuple[uuid.UUID, uuid.UUID]]:
    """Ack every open, currently-departed episode of the key of `ruling_id` (any version)
    in which `drug` is the subject or partner moiety; return the pairs acknowledged.
    Does not commit."""
```

`grain` is `'class'` or `'moiety'` (CLI spelling; map to `class_rule` / `moiety_rule`). CLI:
`drugref curate acknowledge-departure --grain {class,moiety} RULING_ID DRUG --rationale TEXT --reviewed-by NAME
--reviewed-against REF` (`RULING_ID` int, `DRUG` `uuid.UUID`, both positional); prints one line per pair
acknowledged; exit 0; `UnknownRuling` → stderr + exit 2; `NothingDeparted` → stderr + exit 1, nothing
written. Commit the way `_handle_curate_onchigh` does.

- [ ] **Step 1: Write the failing tests:**
  - `test_acknowledging_silences_the_banner_for_that_drug_only`: X and W both departed from one ruling; ack
    X; `departed` still lists W's pair, not X's.
  - `test_an_acknowledged_drug_that_returns_and_leaves_again_departs_again`.
  - `test_two_open_episodes_for_one_pair_are_both_acknowledged` (Review Focus 1: insert a second open
    observation for the pair directly).
  - `test_a_class_against_itself_acknowledges_both_directions` (Review Focus 2).
  - `test_a_superseded_ruling_id_acknowledges_its_key` (Review Focus 3).
  - `test_nothing_departed_refuses_and_writes_nothing`; `test_an_unknown_ruling_id_is_refused`;
    `test_a_blank_rationale_is_refused` (the CHECK raises).
  - CLI: `test_cli_acknowledge_prints_pairs_and_exits_0`, `..._exits_1_when_nothing_departed`,
    `..._exits_2_for_an_unknown_ruling` (drive through `cli.main` the way `tests/test_cli_curate*.py` does).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement; register the subparser in `cli_curate.register` beside `onchigh`. Check
  `wc -l src/drugref/cli_curate.py` stays under 500; if not, put the handler in `cli_departures.py`
  (Task 5) and register it from there.
- [ ] **Step 4:** Run file + full suite; update the suite count line.
- [ ] **Step 5:** Commit: `feat: a curator acknowledges a departed grade (#281)`.

---

### Task 5: The `drugref interactions` banner reads departures (both grains)

**Files:**
- Create: `src/drugref/cli_departures.py` (the banner and, in Task 6, the listing)
- Modify: `src/drugref/cli_interactions.py` (delete `_report_departures`; call
  `cli_departures.report_departures`)
- Modify: `src/drugref/class_ruling_reach.py` (delete `DepartedReach` and `departed_reach`, now unused;
  update the module docstring)
- Modify: `tests/test_cli_interactions_departed.py` (its CLI cases)

**Interfaces:**
- Consumes: `graded_pairs.departed`, `graded_pairs.OBSERVATION_TABLE`.
- Produces: `cli_departures.report_departures(conn, directions: list[tuple[uuid.UUID, uuid.UUID | None]])
  -> bool | None` — same contract as today's `_report_departures` (True printed / False none / None cannot
  tell), and `cli_departures.format_departure(d: Departure) -> list[str]` (pure; the lines for one entry,
  reused by Task 6).

Behaviour (§5.7): the relation probe and `migration_guard` note now name `graded_pairs.OBSERVATION_TABLE`
and migration `"083"`. Entry lines keep #256's shape, add the grain (`class ruling N` / `moiety ruling N`;
moiety grain prints `drug <subject> -> class <object>`), the backfill note becomes "(recorded at db/083's
migration, not when graded)", and each entry ends with
`acknowledge: drugref curate acknowledge-departure --grain {class|moiety} N <drug> --rationale ...` (one
line per departed drug in the entry, the drug being the subject for a subject-side leaver, else the
partner). The headline drops "class": `** CHANGED SINCE REVIEW: N curated ruling(s) graded the pair(s) below
... NO LONGER REACH THEM ... **`. Pair-form "ABSENT RULE" handling and exit 3 unchanged.

- [ ] **Step 1: Write/adjust the failing tests** in `tests/test_cli_interactions_departed.py`: keep #256's
  CLI cases (one-drug form, both pair orders, one ruling two directions, orphan banner not hiding a
  departure, a database without the table in both forms — now without db/083), updating expected text;
  add `test_a_moiety_rule_departure_is_bannered_with_its_grain` and
  `test_each_entry_names_the_acknowledge_command`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** File + full suite, `wc -l` on
  `cli_interactions.py` and `cli_departures.py`, suite count line.
- [ ] **Step 5:** Commit: `feat: the interactions banner covers both grains and names the acknowledge step`.

---

### Task 6: `drugref audit departures`

**Files:**
- Modify: `src/drugref/cli_departures.py`, `src/drugref/cli_audit.py` (register the subparser only)
- Create: `tests/test_cli_audit_departures.py`

**Interfaces:**
- Consumes: `graded_pairs.departed(conn)` (no filters), `cli_departures.format_departure`.
- Produces: `cli_departures.handle_audit_departures(conn, args) -> int`.

Prints `open departures: N ruling(s), M pair(s)` then each entry via `format_departure` plus its
`first_seen_at`; exit 0 when empty, 3 when not; without db/083, the same migration_guard note and exit 2
("cannot tell").

- [ ] **Step 1:** Tests: `test_no_departures_exits_0`, `test_departures_across_rulings_are_listed_and_exit_3`
  (one class-grain and one moiety-grain departure, different subjects), `test_acknowledged_ones_are_not_listed`,
  `test_without_db083_exits_2_with_a_note`.
- [ ] **Step 2–4:** fail → implement → pass; full suite; suite count line.
- [ ] **Step 5:** Commit: `feat: drugref audit departures lists every open departure`.

---

### Task 7: Export and replay

**Files:**
- Modify: `src/drugref/release_archive.py` (if Task 2 deferred anything), `src/drugref/replay_loader.py`,
  `src/drugref/release_replay.py` (docstring §5 list, warning)
- Modify/Create: `tests/test_release_replay_graded_pairs.py`

**Interfaces:**
- Consumes: Task 1's `_Loader`, Task 2's tables.
- Produces: in `release_replay`, `_OBSERVATIONS = "graded_pair_observation"` (untimed: compared, never
  loaded) and `graded_pair_departure_ack` in `_TIMED` as `("ack_id", "acknowledged_at")`, loaded after
  `curated_interaction` / `curated_class_interaction` in `_TIMED`'s order.

Rules (§5.6): at the end of the replay, the set of (rule_grain, subject_key, object_class_uuid, relationship,
subject_moiety, partner_moiety) in the export's observations versus the replayed table's — each side's
extras are one difference line each (`graded pair observed in the origin but not the replay: ...` / `...
in the replay but not the origin: ...`). If any exported observation has `captured = 'backfill_db083'`,
skip that comparison and add the warning `observations predate db/083: not reproducible`. An ack is loaded
with its `observation_id` rewritten to the replayed observation with the same key+pair that is open at that
moment (latest `first_seen_at`); none open → a difference (`acknowledgement N has no open episode in the
replay`), the ack is skipped. `reset_sequences` covers both new sequences.

- [ ] **Step 1:** Tests (follow `tests/test_release_replay*.py`'s fixtures for building and replaying a release):
  `test_a_replay_reproduces_observations` (a release with a ruling and a departure → no difference,
  `reproduced`), `test_an_observation_the_replay_lacks_is_a_difference` (tamper the export),
  `test_a_backfilled_export_warns_and_skips_the_comparison`, `test_an_acknowledgement_is_replayed_onto_its_episode`
  (after replay, `departed` on the target matches the origin), `test_an_ack_with_no_open_episode_is_a_difference`.
- [ ] **Step 2–4:** fail → implement → pass; full suite; `wc -l` both replay files under 500; suite count line.
- [ ] **Step 5:** Commit: `feat: releases carry and replay graded-pair observations and acknowledgements`.

---

### Task 8: Rust reviewer-service — a GUI ruling is observed at commit

**Files:**
- Modify: `reviewer-service/src/decisions.rs` (test module only)

- [ ] **Step 1:** Add `#[tokio::test] #[ignore = "requires a migrated Drugref PostgreSQL database"] async fn
  live_decision_is_observed_as_a_graded_pair()`, modelled on `live_decision_revision_round_trip`: set up a
  moiety, a class with one partner member, a `class_contraindication` candidate and whatever
  `ddi_candidate_pair` needs (copy from that test's setup), record an `applies` interaction decision through
  the service's write function, then assert `SELECT count(*) FROM drugref.graded_pair_observation WHERE
  rule_grain = 'moiety_rule' AND subject_key = $1` is 1. No production Rust change.
- [ ] **Step 2:** Run against a database migrated to db/083 (create `drugref_test_rust083` with `createdb`,
  migrate via `uv run drugref --dsn ... migrate`): `DRUGREF_REVIEW_TEST_DATABASE_URL=postgres://postgres@localhost:5532/drugref_test_rust083
  cargo test --manifest-path reviewer-service/Cargo.toml -- --ignored live_decision_is_observed`
  → PASS (it should pass first time, since the trigger is already there; to prove it tests something, drop
  the moiety trigger in that scratch DB and see it fail, then re-create the DB). If cargo is blocked on a build
  lock, use a separate `CARGO_TARGET_DIR`. `cargo clippy` clean.
- [ ] **Step 3:** Commit: `test: a reviewer-GUI ruling is observed at commit (db/083)`.

---

### Task 9: Docs, issues, size figure

**Files:** `docs/PROJECT-NOTES.md`, `docs/ROADMAP.md`, `docs/HANDOVER.md` (keep within its header's bound),
`docs-site/` *Design decisions* (a new living record "A reviewed grade never vanishes silently"; add it to
`mkdocs.yml` nav like its siblings).

- [ ] **Step 1:** Measure size and cost: in a scratch database `createdb -T drugref_profiles` (it has real
  MED-RT membership; migrate it to db/083), record one class ruling over two large MED-RT classes, call
  `drugref.refresh_graded_pairs()` under `EXPLAIN ANALYZE`, and record the row count and time in
  PROJECT-NOTES. Drop the scratch database afterwards; never migrate `drugref_profiles` itself.
- [ ] **Step 2:** Lodge issues (`gh issue create`): acknowledging from the reviewer GUI; signing
  acknowledgements; "move to a per-key refresh or delta export if a real database passes 10⁶ observations"
  (reference #260).
- [ ] **Step 3:** PROJECT-NOTES: a section "Ever-graded pairs (#281, #279, 2026-11-25) — db/083, suite
  3649 → N" in the style of the #256 section; update the #256 section's "Still silent" and "Not handled"
  bullets to point at it; code-map entries for `graded_pairs.py`, `cli_departures.py`, `replay_loader.py`;
  the open-issue ledger. ROADMAP: the #256 paragraph's "Open follow-ups" (#279, #281 fixed). HANDOVER: NEXT
  block, STANDING note "no named database carries db/079–db/083".
- [ ] **Step 4:** `ruff check .`, full suite, commit `docs: ever-graded pairs (#281, #279)`.

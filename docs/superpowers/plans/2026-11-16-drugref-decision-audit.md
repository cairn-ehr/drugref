# Decision audit (#258) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every drugref data state a decision can rest on is named by a release tag that drugref can rebuild,
and every class-grain ruling remembers which drugs it reached when written, so the orphan banner stops
under-warning (#258).

**Architecture:** Two migrations. `db/080` adds an insert-only reach snapshot written beside each
`curated_class_interaction` ruling. `db/081` adds an insert-only `release_build` row per publish, plus a
`current_release` view that reports drift. A content-addressed file archive (`DRUGREF_ARCHIVE`) holds every ingest
run's input files, a release record and a curated export. Publish refuses when the archive is configured but
incomplete.

**Tech Stack:** Python 3 (stdlib `hashlib`, `json`, `shutil`, `pathlib`, `subprocess`), psycopg 3, PostgreSQL,
pytest. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-11-16-drugref-decision-audit-design.md`](../specs/2026-11-16-drugref-decision-audit-design.md)

## Global Constraints

- AGPL-3.0; **no new dependencies**.
- Migrations are checksum-immutable: add `db/080_*.sql`, `db/081_*.sql`; never edit `db/079` or earlier.
- Curated and audit tables are insert-only: reuse `drugref.forbid_any_rewrite()` (db/030) as the trigger.
- Files under ~500 lines. `curation.py` is at 483 lines: add at most a 2-line call there; logic goes in new modules.
- TDD: each task's failing test comes first. DB tests use the `conn` fixture (`tests/conftest.py`) and need
  `DRUGREF_TEST_DSN=host=localhost port=5532 dbname=drugref_test user=postgres`.
- Inline docs for junior contributors, in the house style: explain *why* in the docstring, and give the issue
  number.
- `ruff check .` clean. Do NOT run `ruff format` on existing files; match the hand style.
- Archive env var: `DRUGREF_ARCHIVE`. **Unset means no archive I/O at all**, so tests and dev ingests are unchanged.
- Archive layout (exact): `<root>/blobs/sha256/<first 2 hex>/<64 hex>`, `<root>/runs/<source_checksum>.json`,
  `<root>/releases/<release_tag>.json`, `<root>/releases/<release_tag>.curated.json`.
- Drift timestamps are compared on the database clock (`release_build.recorded_at DEFAULT now()`).
- Header copy (exact): `drugref release: <tag>`;
  `drugref release: <tag> (DRIFTED since publication: <reason>, <reason>) — answers may differ from that release`;
  `drugref release: none published (answers are not tied to a reconstructable release)`;
  `drugref release: unknown (database predates db/081)`.
- Never edit cairn-ehr from here.
- The suite total lives only in PROJECT-NOTES § "How to run / test"; `tests/test_suite_count.py` fails until that
  line matches the number collected.

## Review Focus

1. **An existing ruling superseded by a correction:** the banner must still match drugs the *old* version reached
   (look up reach rows by natural key, not by id). Test in Task 2.
2. **A source the release never carried** (a node's PBS run after a restore) must not read as drift, while a
   re-ingest of a carried source must. Test in Task 6.
3. **An archive root set but a blob deleted or truncated by hand:** `drugref audit archive` must name it, and publish
   must refuse. Tests in Tasks 4 and 7.
4. **`spl_run`'s checksummed inputs include `importlib.resources` Traversables, not only paths:** archiving must
   read them through `.open("rb")`. Test in Task 4.
5. **A database at `db/079` but not `db/080`:** `drugref interactions` must fall back to banner-every-class-orphan
   with a stderr note, not exit 2. Test in Task 2.

---

### Task 1: `db/080` reach snapshot, written beside each class ruling

**Files:**
- Create: `db/080_class_ruling_reach.sql`
- Create: `src/drugref/class_ruling_reach.py`
- Modify: `src/drugref/curation.py` (`record_class_interaction_judgement`, after `overlay.supersede(...)`)
- Test: `tests/test_class_ruling_reach.py`

**Interfaces:**
- Produces: table `drugref.curated_class_interaction_reach(curated_class_interaction_id bigint FK, side text CHECK
  IN ('subject','partner'), moiety_uuid uuid FK substance_moiety, captured text CHECK IN ('at_ruling',
  'backfill_db080'), recorded_at timestamptz NOT NULL DEFAULT now(), PK(curated_class_interaction_id, side,
  moiety_uuid))`, with an insert-only trigger.
- Produces: `class_ruling_reach.REACH_TABLE = "drugref.curated_class_interaction_reach"`;
  `snapshot_reach(conn, curated_class_interaction_id: int) -> int` (rows written).

- [ ] **Step 1: Write the failing tests.** Build a ruling with the class fixtures already used in
  `tests/test_cli_interactions_orphans.py` (subject class with member X, partner class with member Y). Then:
  - `test_a_ruling_snapshots_its_reach`: after `curation.record_class_interaction_judgement(...)`, reach rows ==
    `{(id,'subject',X,'at_ruling'), (id,'partner',Y,'at_ruling')}`.
  - `test_reach_survives_a_member_leaving`: delete X's `class_membership` row; reach rows unchanged.
  - `test_reach_is_insert_only`: `UPDATE` and `DELETE` on the table raise.
  - `test_a_correction_snapshots_again_and_keeps_the_old`: a second judgement on the same natural key leaves both
    ids' reach rows.
  - `test_db080_backfills_live_rulings_as_backfill`: on a ruling inserted with raw SQL (bypassing the writer), run
    db/080's backfill `INSERT ... SELECT` statement and assert `captured = 'backfill_db080'`. Extract the statement
    into a constant shared by the migration and the test only if it can't be run otherwise. Otherwise re-apply via
    `db.apply_migrations` on a fresh session DB fixture the way `tests/test_db.py` does.
- [ ] **Step 2: Run** `uv run pytest tests/test_class_ruling_reach.py -v`. Expected: FAIL (table missing).
- [ ] **Step 3: Write `db/080`:** the table, its trigger, and the backfill
  `INSERT INTO ... SELECT curated_class_interaction_id, side, moiety_uuid, 'backfill_db080' FROM
  drugref.class_rule_member ON CONFLICT DO NOTHING`. Add a `COMMENT ON TABLE` saying `backfill_db080` rows are
  membership at migration time, not at ruling time.
- [ ] **Step 4: Implement `snapshot_reach`:** one `INSERT ... SELECT ... 'at_ruling' FROM class_rule_member WHERE
  curated_class_interaction_id = %s`. Call it from `record_class_interaction_judgement` right after
  `overlay.supersede`, when the new row is live and so visible in `class_rule_member`.
- [ ] **Step 5: Run** the new file plus `tests/test_db.py` (it lists migrations). Update any migration-count
  assertion there. Expected: PASS.
- [ ] **Step 6: Commit** `feat: snapshot each class ruling's reach at write time (#258)`.

### Task 2: the orphan banner reads reach-at-ruling too

**Files:**
- Modify: `src/drugref/class_grain_read.py` (`class_rules_reaching`; remove the `shortcut:` paragraph)
- Modify: `src/drugref/cli_interactions.py` (`_report_orphans`: the `to_regclass` probe checks both
  `RULE_MEMBER_VIEW` and `class_ruling_reach.REACH_TABLE`; the docstring drops "needs membership history, #258")
- Test: `tests/test_cli_interactions_orphans.py`

**Interfaces:**
- Consumes: `class_ruling_reach.REACH_TABLE` (Task 1).
- Produces: `class_rules_reaching(conn, moieties)` unchanged signature; now also returns a rule whose natural key
  has **any** reach row (any version, superseded included) with `moiety_uuid = ANY(moieties)`.

- [ ] **Step 1: Write the failing tests**:
  - `test_a_drug_that_alone_left_the_class_is_still_bannered`: rule A×B graded while X ∈ A (A also holds Z). Delete
    X's membership and the `class_pair_contraindication` row. `drugref interactions X` prints `** INCOMPLETE`
    and exits `EXIT_INCOMPLETE`. This is #258's exact case.
  - `test_a_superseded_versions_reach_still_counts`: grade, then correct (new id). X left A between the two, so
    only the first version's reach has X. Still bannered.
  - `test_without_db080_every_class_orphan_is_shown`: drop the reach table in the test transaction. Expect the
    stderr note and the banner, not exit 2.
- [ ] **Step 2: Run** them. Expected: the first two FAIL (exit 0, no banner).
- [ ] **Step 3: Implement:** add a fourth `OR EXISTS` arm joining `REACH_TABLE r` to
  `curated_class_interaction v ON v.curated_class_interaction_id = r.curated_class_interaction_id` on the same three
  natural-key columns as `cci`. Widen the probe in `_report_orphans`, and its stderr note names `db/079 or db/080`.
- [ ] **Step 4: Run** `uv run pytest tests/test_cli_interactions_orphans.py tests/test_class_ruling_reach.py -v`.
  Expected: PASS. Grep that `shortcut:` and "#258" are gone from `class_grain_read.py`.
- [ ] **Step 5: Commit** `fix: banner a class orphan for drugs it reached when ruled (#258)`.

### Task 3: audit read for one class ruling

**Files:**
- Modify: `src/drugref/class_ruling_reach.py`
- Create: `src/drugref/cli_audit.py`; register in `src/drugref/cli.py` beside `cli_interactions.register(commands)`
- Test: `tests/test_class_ruling_audit.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class SideReach: side: str; then: frozenset[uuid.UUID]; now:
  frozenset[uuid.UUID]; captured: str` with properties `added` (now − then) and `removed` (then − now).
- Produces: `@dataclass(frozen=True) class RulingAudit: curated_class_interaction_id: int; reviewed_by: str;
  reviewed_against: str; reviewed_at: datetime; sides: tuple[SideReach, SideReach]; loaded_runs:
  tuple[tuple[str, str, int, str], ...]` (source, writer, ingest_run_id, upstream_release: the latest run per
  (source, writer) that finished at or before `reviewed_at`).
- Produces: `audit_ruling(conn, curated_class_interaction_id: int) -> RulingAudit | None` (None = no such id).
- Produces: CLI `drugref audit class-ruling <id>`. Exit 0 on success, exit 2 for an unknown id. It prints both
  sides with counts and the added/removed uuids, then the loaded runs. `now` is empty for a superseded ruling
  (class_rule_member covers live rulings only), and the output says so.

- [ ] **Step 1: Write failing tests:** `test_audit_reports_a_member_who_left` (X in `removed` on the subject side),
  `test_audit_names_the_runs_loaded_at_review` (two runs of one source; the later one finished after
  `reviewed_at`, so only the earlier is listed), `test_audit_of_an_unknown_id_is_none`, and a CLI test for exit 2.
- [ ] **Step 2: Run.** Expected: FAIL (ImportError).
- [ ] **Step 3: Implement** `audit_ruling` and the CLI handler. The SQL stays in `class_ruling_reach.py`; the CLI
  only prints.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: drugref audit class-ruling shows reach then vs now (#258, #256)`.

### Task 4: the content-addressed archive (files only, no DB)

**Files:**
- Create: `src/drugref/archive.py`
- Test: `tests/test_archive.py` (pure, `tmp_path`, no DB)

**Interfaces:**
- Produces: `ENV_VAR = "DRUGREF_ARCHIVE"`; `archive_root() -> pathlib.Path | None`.
- Produces: `@dataclass(frozen=True) class ArchivedFile: name: str; size: int; sha256: str`.
- Produces: `file_digests(*inputs) -> tuple[str, list[ArchivedFile]]`: ONE read pass feeding both the combined
  hasher (must equal `ingest.checksum.checksum(*inputs)`) and a per-file hasher. `inputs` are `str`, `Path` or
  `importlib.resources.abc.Traversable`; read them via `.open("rb")` in `CHUNK_BYTES` chunks. `name` is the input's
  basename.
- Produces: `blob_path(root, sha256) -> Path`; `run_record_path(root, source_checksum) -> Path`.
- Produces: `store_run(root, *inputs) -> str` (returns the combined digest). It copies each blob only if absent,
  writing to `<blob>.tmp` and then `os.replace`, so a crash never leaves a half blob under the final name. It then
  writes the run record JSON `{"source_checksum": ..., "files": [{"name","size","sha256"}, ...]}` in input order,
  `ensure_ascii=False`.
- Produces: `@dataclass(frozen=True) class ArchiveProblem: source_checksum: str; what: str` (`"no run record"`,
  `"missing blob <sha>"`, `"corrupt blob <sha>"`).
- Produces: `missing_runs(root, checksums: Iterable[str]) -> list[ArchiveProblem]` checks only that records and
  blobs exist, without re-hashing (publish uses it). `verify_runs(root, checksums) -> list[ArchiveProblem]` also
  re-hashes each blob (the audit command uses it).

- [ ] **Step 1: Write failing tests:** combined digest equals `checksum()` for 1 and 3 files;
  `test_a_traversable_input_is_archived` (use `importlib.resources.files("drugref")` on a shipped data file);
  `test_store_is_idempotent_and_dedupes` (two runs sharing a file make one blob);
  `test_missing_runs_names_a_missing_record_and_a_missing_blob`; `test_verify_names_a_truncated_blob`;
  `test_archive_root_is_none_when_unset` (monkeypatch env).
- [ ] **Step 2: Run** `uv run pytest tests/test_archive.py -v`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement** `archive.py` to the interfaces above.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: content-addressed archive of upstream input files (#258)`.

### Task 5: every orchestrator archives its inputs

**Files:**
- Modify: `src/drugref/ingest/checksum.py` (add `archived_checksum`)
- Modify: the 14 orchestrators that call `checksum(`: `ingest/{chebi, allergy_group_run, gsrs_run,
  drugcentral_run, fda_cyp_run, medrt_run, mesh_rel_run, mesh_run, mesh_synonym_run, onchigh_run, moiety_name_run,
  pbs_run, run, spl_run}.py`
- Test: `tests/test_archive_orchestrators.py`

**Interfaces:**
- Consumes: `archive.archive_root`, `archive.store_run` (Task 4).
- Produces: `archived_checksum(*inputs) -> str`. It returns `checksum(*inputs)` when `archive_root()` is None,
  otherwise `archive.store_run(root, *inputs)`. The archive is written before the run opens, so a failed run
  leaves harmless extra blobs, and the spec's "publish refuses on a missing run record" still covers a failed
  archive write.

- [ ] **Step 1: Write failing tests:**
  - `test_no_orchestrator_calls_bare_checksum`: parse each `src/drugref/ingest/*.py` with `ast`, in the style of
    `test_every_orchestrator_starts_its_clock_on_its_very_first_line`. No module other than `checksum.py` calls
    `checksum(...)` directly.
  - `test_an_ingest_with_the_archive_set_writes_its_run_record`: run `ingest_unii` (or the smallest
    fixture-driven orchestrator) with `DRUGREF_ARCHIVE=tmp_path`, then assert
    `run_record_path(tmp_path, <ingest_run.source_checksum>)` exists.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `archived_checksum`, then swap the 14 call sites and their imports. Use `perl -pi -e`
  (not BSD sed), then `git diff --stat` and a grep for any remaining bare `checksum(` in `ingest/*_run.py`,
  `chebi.py` and `run.py`.
- [ ] **Step 4: Run** the full suite `uv run pytest` (orchestrator tests must be unchanged with the env unset).
  Expected: PASS.
- [ ] **Step 5: Commit** `feat: every ingest archives its input files when DRUGREF_ARCHIVE is set (#258)`.

### Task 6: `db/081` release_build and the current_release view

**Files:**
- Create: `db/081_release_build.sql`
- Create: `src/drugref/release_state.py`
- Test: `tests/test_release_state.py`

**Interfaces:**
- Produces: table `drugref.release_build(release_tag text PRIMARY KEY REFERENCES
  release_manifest(release_tag), git_commit text NOT NULL, ledger_head text NOT NULL, run_ids bigint[] NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now())`, insert-only.
- Produces: view `drugref.current_release(release_tag, published_at, git_commit, ledger_head, drift_reasons
  text[])`, holding zero or one row: the latest `release_manifest` by `recorded_at`, LEFT JOINed to `release_build`.
  `drift_reasons` holds these exact strings, in this order, when their condition is true:
  - `'no build record (published before db/081)'`: the build row is missing.
  - `'re-ingested'`: some `ingest_run` finished after `rb.recorded_at` and its `source` is one of the sources of
    `rb.run_ids`.
  - `'re-curated'`: some row in `curated_interaction`, `curated_condition`, `curated_class_interaction` or
    `curated_property_ruling` has `reviewed_at` after `rb.recorded_at`.
  - `'expansion policy changed'`: some `class_expansion_policy` row has `reviewed_at` after `rb.recorded_at`.
  - `'newer migration'`: `(SELECT max(filename) FROM schema_migration)` differs from `rb.ledger_head`.
- Produces: `@dataclass(frozen=True) class CurrentRelease: release_tag: str; published_at: datetime; git_commit:
  str | None; drift_reasons: tuple[str, ...]` with property `drifted`. `current_release(conn) -> CurrentRelease |
  None`. `release_line(cr: CurrentRelease | None) -> str` (pure, exact header copy from Global Constraints).
  `RELEASE_VIEW = "drugref.current_release"`.

- [ ] **Step 1: Write failing tests:** `release_line` for all four copies (the pure ones need no DB). Then DB tests
  with a hand-inserted manifest and build row:
  - `test_a_fresh_release_has_no_drift`.
  - `test_a_pbs_run_after_publication_is_not_drift`: the release's runs are UNII only; a new PBS run is not drift.
    This is Review Focus 2.
  - `test_a_carried_source_reingested_is_drift`.
  - `test_a_new_ruling_is_drift`.
  - `test_a_policy_row_is_drift`.
  - `test_a_ledger_mismatch_is_drift`.
  - `test_no_manifest_is_none`.
  - `test_manifest_without_build_row_says_so`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Write `db/081`** and `release_state.py`. Comment the view to say it is cheap: max-timestamp probes,
  no manifest verification.
- [ ] **Step 4: Run.** Expected: PASS (plus `tests/test_db.py`).
- [ ] **Step 5: Commit** `feat: release_build and current_release drift view (#258)`.

### Task 7: publish writes the release record, curated export and build row

**Files:**
- Create: `src/drugref/release_archive.py`
- Modify: `src/drugref/cli_signing_release.py` (the `publish` handler calls `release_archive.publish_archived`
  instead of `releases.publish`)
- Test: `tests/test_release_archive.py`

**Interfaces:**
- Consumes: `releases.publish(...)` (unchanged), `archive.missing_runs`, `archive.archive_root`.
- Produces: `class ArchiveIncomplete(Exception)` carrying `problems: list[ArchiveProblem]`.
- Produces: `git_commit() -> str`: `git rev-parse HEAD` run in the package's repo directory, or `"unknown"` on any
  failure.
- Produces: `finished_runs(conn) -> list[dict]`: every `ingest_run` with `finished_at IS NOT NULL`, ordered by
  `ingest_run_id`, with keys source, writer, ingest_run_id, upstream_release, source_checksum, and finished_at as an
  ISO string.
- Produces: `curated_export(conn) -> dict[str, list[dict]]`: `{table: [to_jsonb(row) ...]}` for the four
  `releases._CURATED_KINDS` tables plus `class_expansion_policy`. All rows, superseded included, ordered by primary
  key.
- Produces: `publish_archived(conn, *, root: Path | None, **publish_kwargs) -> int` (manifest_id). Order:
  1. If `root` is set, run `missing_runs(root, {r["source_checksum"] for r in finished_runs})`. Any problem raises
     `ArchiveIncomplete` before anything is written.
  2. `releases.publish(...)`.
  3. INSERT `release_build` (git_commit, ledger_head, run_ids).
  4. If `root` is set, write `releases/<tag>.json` (`{"release_tag", "git_commit", "ledger_head", "runs": [...]}`)
     and `releases/<tag>.curated.json`, both with `ensure_ascii=False`.
  Nothing commits; the CLI owns the transaction. The files are written before the commit: if the commit then fails,
  the stray files name a tag that is absent from the database, which is harmless because the tag is still free.
- CLI: `ArchiveIncomplete` prints each problem and exits 1. With `root` None it prints `note: DRUGREF_ARCHIVE is not
  set, so release <tag> is NOT reconstructable` and publishes.

- [ ] **Step 1: Write failing tests:**
  - `test_publish_refuses_when_a_run_is_unarchived` (asserts no `release_manifest` row was written).
  - `test_publish_writes_record_export_and_build_row`.
  - `test_publish_without_archive_still_writes_build_row`.
  - `test_curated_export_includes_superseded_rows`.
  - `test_release_record_lists_every_run_not_only_loaded`: two runs of one source, both listed.
  - CLI: `test_publish_cli_exits_1_and_names_the_missing_run`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Keep `releases.py` (424 lines) untouched.
- [ ] **Step 4: Run** the new tests plus every existing release/publish test (`uv run pytest -k "release or
  publish"`). Expected: PASS.
- [ ] **Step 5: Commit** `feat: publish archives the release record and curated export (#258)`.

### Task 8: the release header line, and `drugref audit archive`

**Files:**
- Modify: `src/drugref/cli_interactions.py` (print `release_line` first, inside a `migration_guard`-style probe:
  when the view is absent print the "predates db/081" copy and never abort)
- Modify: `src/drugref/cli_status.py` (same line at the top of status output)
- Modify: `src/drugref/cli_audit.py` (add `audit archive [--release TAG]`)
- Test: `tests/test_release_header.py`, `tests/test_cli_audit_archive.py`

**Interfaces:**
- Consumes: `release_state.current_release`, `release_line`, `RELEASE_VIEW` (Task 6); `archive.verify_runs`,
  `archive.archive_root` (Task 4).
- Produces: `drugref audit archive [--release TAG]` reads `releases/<TAG>.json` (default: the
  `current_release` tag) and runs `verify_runs` over its run checksums. It prints each problem, or `archive
  complete for <tag>: <n> runs, <m> blobs verified`. Exit codes: 0 when complete, 1 on problems, 2 when
  `DRUGREF_ARCHIVE` is unset or the release record is missing.

- [ ] **Step 1: Write failing tests:** the header is the first stdout line of `interactions` and `status` in each of
  the four states (the drifted one includes the reason text). The drifted and none states keep the command's
  existing exit code. The archive command covers complete, a deleted blob (exit 1, names its sha), and unset env
  (exit 2).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Check that `cli_status.py` and `cli_interactions.py` stay under ~500 lines (`wc -l`).
- [ ] **Step 4: Run** `uv run pytest`. Existing tests that assert on the first stdout line of
  `interactions`/`status` will need the header accounted for. Fix the assertions, never the copy. Expected: PASS.
- [ ] **Step 5: Commit** `feat: drugref reads state the release they answer from (#258)`.

### Task 9: docs, issues, suite count

**Files:**
- Modify: `docs/PROJECT-NOTES.md` (state by layer, schema map for db/080–081, traps: "a backfill_db080 reach row is
  migration-time membership", "DRUGREF_ARCHIVE unset = no reconstruction"; the suite-count line)
- Modify: `docs/ROADMAP.md` (a "Decision audit (#258) ✅" entry); `docs/HANDOVER.md` (regenerate within its own
  header's bound)
- Create: a docs-site *Design decisions* page "Release tag as the decision-audit anchor" under `docs-site/` (find
  the section's directory and nav entry in `mkdocs.yml`)

- [ ] **Step 1:** Run `uv run pytest`. Set the PROJECT-NOTES suite-count line to the collected number, then rerun
  and confirm `tests/test_suite_count.py` passes.
- [ ] **Step 2:** Lodge two GitHub issues. (a) "Release replay: rebuild a release into a fresh database from the
  archive" (the spec's non-goal). (b) "Diff-based membership tracking if archive disk use grows" (owner's fallback).
  Comment on #256 that `drugref audit class-ruling` now exposes added/removed members.
- [ ] **Step 3:** Write the docs. Reflow edited paragraphs to the file's width. Run `ruff check .` and `uv run
  mkdocs build --strict` if docs-site builds locally.
- [ ] **Step 4: Commit** `docs: decision audit round (#258)`, then push and open a PR to `main` that closes #258.

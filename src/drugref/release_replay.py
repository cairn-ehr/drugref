"""Rebuild a published release into an EMPTY database from the archive (#259).

WHY. #258 made a release reconstructable: the archive holds every input file of
every ingest run, the release record names the runs and the code, and the
curated export holds every curated row. This module is the tool that uses them,
so "what did drugref know when this decision was made?" can be answered from the
archive alone, even after the publishing database is gone.

WHAT IT DOES, in order (`replay`):

1. Refuses BEFORE WRITING ANYTHING (`ReplayRefused`) when the release record or
   curated export is missing or malformed (a timestamp included), the archive
   fails its re-hash, the target database is not empty, a run names a writer no
   orchestrator implements, two of a run's input files share a name, the
   checked-out migrations do not end at the release's ledger head, or the running
   code is not the release's git commit (unless `allow_code_mismatch`: then it is
   a warning). Different code can build different memberships from the same bytes.
2. Migrates the empty target. From here on any exception is `ReplayFailed`: the
   target is half-built and must be dropped.
3. Replays EVERY run in the release record, in order, from the archived blobs
   (symlinked under their original names, because parsers read names: `.gz`,
   openFDA's partition glob), and checks each run re-derives its recorded
   `source_checksum`.
4. Loads the curated rows INTERLEAVED with the runs: a row goes in before the
   first run that FINISHED after the curator wrote it (a run's work commits at
   its finish, so a curator writing mid-run saw the database without it -- the
   same rule `class_ruling_reach` uses). Loading them all at the
   end would be wrong twice over: a later ingest can delete a question a ruling
   cites (its FK insert then fails), and a ruling's reach would be checked
   against membership the curator never saw.
5. Checks, and reports as DIFFERENCES (never raises for them):
   - each class ruling's ruling-time reach (db/080) against what the replayed
     `class_rule_member` gives at the moment the ruling is loaded -- the direct
     test of "the replay rebuilt what the curator saw";
   - the live curated digests against the release's manifest entries;
   - each row a migration already seeded (the expansion policies) against its
     exported copy, which is never re-inserted (#277); a seeded row the export
     lacks, or a superseder it names but lacks, is a difference too (#278).

A replay with no differences is a verified reproduction (`ReplayReport.reproduced`)
only when it also has no warnings: other code, or a record from before the
manifest was archived, leaves "nothing differed" short of "reproduced".

Ingest guards that compare with STORED counts (fda-cyp's shrink checks, SPL's
floors) are passed open: the original run already passed them. Every run replays
with the release's code, even a run first ingested by older code.

Orchestrators commit, so a replay that fails part-way leaves a half-built target
(`ReplayFailed`); it is a scratch database, and a second replay into it is
refused (not empty).

The reviewer-service's notes and references load the same way, with the accounts,
profiles, public keys (revocations included) and signatures behind them (#270): a
note loaded before a run keeps the question it cites, as it did in the origin. A
release's own signature stays in the archive (`_signs_a_release`). An export
missing one of these tables predates this code and is refused as such.
NOT REPLAYED: the question bookkeeping and accumulation tables (no writer, #275).
"""
import contextlib
import datetime as dt
import json
import os
import pathlib
import tempfile
from dataclasses import dataclass, field

import psycopg

from drugref import archive, db, release_archive, releases
# Re-exported: tests and callers patch `release_replay.ADAPTERS` (the same dict).
from drugref.replay_adapters import ADAPTERS, split_spl_inputs  # noqa: F401
from drugref.replay_loader import _TIMED, _Loader


class ReplayRefused(Exception):
    """Nothing was written: each entry of `problems` says why."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class ReplayFailed(Exception):
    """Writing had begun when this was raised: the target is half-built; drop it.

    Distinct from `ReplayRefused` so the CLI never reports a part-written target
    as "refused, nothing written" (an orchestrator's own ValueError included).
    """


@dataclass
class ReplayReport:
    """What a finished replay found: `differences` did not reproduce; `warnings`
    are checks the replay could not make, so either one means not `reproduced`.
    `curated_rows` counts every archived timed row loaded (`_TIMED`): rulings, and
    the accounts, keys, signatures and notes behind them."""
    runs: int = 0
    curated_rows: int = 0
    rulings_checked: int = 0
    differences: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def reproduced(self) -> bool:
        return not self.differences and not self.warnings


# ---- pure helpers ------------------------------------------------------------


def _when(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp)


def _signs_a_release(table: str, row: dict) -> bool:
    """A release's own signature points at a `release_manifest` id, and the replay
    does not restore manifests (the release record carries it). Loaded, it would
    dangle -- and attach to whatever manifest a later publish in the target gets
    under the same id, failing that release's check. It stays in the archive."""
    return table == "assertion_signature" and row["target_kind"] == "release_manifest"


def schedule(rows: list[tuple[str, dict]], runs: list[dict]) -> list[list]:
    """Split (table, row) pairs into len(runs) + 1 batches: batch i loads before
    run i, the last after every run.

    A row joins the batch of the first run that FINISHED after the row was written
    (its table's timestamp in `_TIMED`, e.g. `reviewed_at`): a run's work is
    invisible until it commits at `finished_at`, so that is the first run whose
    effect the writer had not seen. Within a batch: time, then table order
    (`_TIMED`), then id, so a correction loads after the row it supersedes.
    """
    ends = [_when(r["finished_at"]) for r in runs]
    rank = {t: i for i, t in enumerate(_TIMED)}

    def written(table_row):
        table, row = table_row
        return _when(row[_TIMED[table][1]])

    ordered = sorted(rows, key=lambda tr: (written(tr), rank[tr[0]],
                                           tr[1][_TIMED[tr[0]][0]]))
    batches: list[list] = [[] for _ in range(len(runs) + 1)]
    for table, row in ordered:
        when = written((table, row))
        i = next((k for k, e in enumerate(ends) if e > when), len(runs))
        batches[i].append((table, row))
    return batches


def code_problem(current: str, recorded: str) -> str | None:
    """None when this checkout is exactly the code that built the release."""
    if recorded == "unknown" or recorded.endswith("-dirty"):
        return (f"the release was built from {recorded!r} code, which no checkout "
                "reproduces")
    if current != recorded:
        return (f"this checkout is {current}, the release was built from {recorded}: "
                f"replay from `git worktree add <dir> {recorded}`")
    return None


def manifest_differences(expected: list[dict], live: list[dict]) -> list[str]:
    """Each manifest entry missing, added or with other content, as one line."""
    def index(entries):
        return {(e["target_kind"], e["natural_key"]): e["payload_digest"]
                for e in entries}
    want, got = index(expected), index(live)
    out = []
    for kind, key in sorted(want.keys() | got.keys()):
        if (kind, key) not in got:
            out.append(f"manifest: {kind} {key} missing from the replay")
        elif (kind, key) not in want:
            out.append(f"manifest: {kind} {key} not in the release")
        elif want[kind, key] != got[kind, key]:
            out.append(f"manifest: {kind} {key} content differs")
    return out


# ---- reading the archive -----------------------------------------------------


def _read_json(path: pathlib.Path, what: str):
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        raise ReplayRefused([f"no {what} at {path}"]) from None
    except OSError as exc:
        raise ReplayRefused([f"cannot read {path}: {exc.strerror or exc}"]) from exc
    except ValueError:
        raise ReplayRefused([f"malformed {what} {path}"]) from None


def _run_files(root, checksum) -> list[archive.ArchivedFile]:
    """A run record's files; only called after `verify_runs` passed it."""
    body = _read_json(archive.run_record_path(root, checksum), "run record")
    return [archive.ArchivedFile(f["name"], f["size"], f["sha256"])
            for f in body["files"]]


def _is_empty(conn) -> bool:
    probe = "SELECT to_regclass('drugref.schema_migration')"
    if conn.execute(probe).fetchone()[0] is None:
        return True
    return not conn.execute("SELECT 1 FROM drugref.schema_migration LIMIT 1").fetchone()


def _refusals(conn, root, record, *, allow_code_mismatch, warnings) -> list[str]:
    """Everything that stops a replay before it writes; fills `warnings`."""
    runs = record["runs"]
    out = []
    try:
        checksums = [r["source_checksum"] for r in runs]
        out += [f"{p.source_checksum}: {p.what}"
                for p in archive.verify_runs(root, checksums)]
    except OSError as exc:
        out.append(f"cannot read the archive: {exc.filename or root}: "
                   f"{exc.strerror or exc}")
    out += [f"run {r['ingest_run_id']}: writer {r['writer']!r} has no replay adapter"
            for r in runs if r["writer"] not in ADAPTERS]
    if not out:      # names only make sense on records verify_runs accepted
        for r in runs:
            names = [f.name for f in _run_files(root, r["source_checksum"])]
            if len(set(names)) != len(names):
                out.append(f"run {r['ingest_run_id']}: two input files share a name")
    if not _is_empty(conn):
        out.append("the target database is not empty (it has a migration ledger); "
                   "replay only into a freshly created database")
    head = max(p.name for p in db.migration_dir().glob("*.sql"))
    if head != record["ledger_head"]:
        out.append(f"this checkout's migrations end at {head}, the release's at "
                   f"{record['ledger_head']}")
    problem = code_problem(release_archive.git_commit(), record["git_commit"])
    if problem and allow_code_mismatch:
        warnings.append(f"code mismatch allowed: {problem}")
    elif problem:
        out.append(f"{problem} (or pass --allow-code-mismatch)")
    return out


# ---- writing the target ------------------------------------------------------


@contextlib.contextmanager
def _no_archiving():
    """Replayed runs read the archive; they must not write into it again."""
    saved = os.environ.pop(archive.ENV_VAR, None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ[archive.ENV_VAR] = saved


def _replay_run(conn, root, rec, report) -> None:
    """Stage one run's blobs under their names, run its orchestrator, check it."""
    files = _run_files(root, rec["source_checksum"])
    with tempfile.TemporaryDirectory(prefix="drugref-replay-") as tmp:
        staged = []
        for f in files:
            link = pathlib.Path(tmp) / f.name
            link.symlink_to(archive.blob_path(root, f.sha256))
            staged.append(link)
        ADAPTERS[rec["writer"]](conn, staged, rec["upstream_release"])
    got = conn.execute("SELECT source_checksum FROM drugref.ingest_run "
                       "ORDER BY ingest_run_id DESC LIMIT 1").fetchone()[0]
    if got != rec["source_checksum"]:
        report.differences.append(
            f"run {rec['ingest_run_id']} ({rec['writer']}) replayed with checksum "
            f"{got}, recorded {rec['source_checksum']}")
    report.runs += 1


def replay(conn: psycopg.Connection, root: pathlib.Path, tag: str, *,
           allow_code_mismatch: bool = False) -> ReplayReport:
    """Rebuild release `tag` into the empty database `conn` points at.

    Raises `ReplayRefused` before writing anything (see the module docstring), or
    `ReplayFailed` once writing began; otherwise returns the report, whose
    `differences` are what did not reproduce (and `reproduced` the verdict).
    """
    record = _read_json(archive.release_record_path(root, tag), "release record")
    export = _read_json(archive.curated_export_path(root, tag), "curated export")
    report = ReplayReport()
    refusals = _refusals(conn, root, record, allow_code_mismatch=allow_code_mismatch,
                         warnings=report.warnings)
    runs = record["runs"]
    older = sorted(set(release_archive._EXPORT_TABLES) - set(export))
    if older:     # an export from before these tables were archived, not damage
        refusals.append(f"the curated export predates {', '.join(older)} joining "
                        f"it: replay it from `git worktree add <dir> "
                        f"{record['git_commit']}`")
    try:     # before writing: a bad timestamp is a damaged archive, not a failure
        batches = schedule([(t, r) for t in _TIMED for r in export.get(t, [])
                            if not _signs_a_release(t, r)], runs)
    except (KeyError, ValueError) as exc:
        refusals.append(f"a run or curated row has a missing or malformed "
                        f"timestamp: {exc}")
    if refusals:
        raise ReplayRefused(refusals)
    step = "migrating the target"
    try:
        db.apply_migrations(conn)
        loader = _Loader(conn, export, report)
        with _no_archiving():
            for i, batch in enumerate(batches):
                step = (f"loading curated rows before run {i + 1} of {len(runs)}"
                        if i < len(runs) else "loading curated rows after every run")
                for table, row in batch:
                    loader.load(table, row)
                conn.commit()      # deferred single-live checks fire here
                if i < len(runs):
                    step = (f"replaying run {runs[i]['ingest_run_id']} "
                            f"({runs[i]['writer']})")
                    _replay_run(conn, root, runs[i], report)
        loader.unmatched()
        step = "resetting sequences"
        loader.reset_sequences()
        conn.commit()
        step = "comparing the manifest"
        if "manifest" in record:
            live = [{"target_kind": e.target_kind, "natural_key": e.natural_key,
                     "payload_context": e.payload_context,
                     "payload_digest": e.payload_digest.hex()}
                    for e in releases.enumerate_live(conn)]
            report.differences += manifest_differences(record["manifest"], live)
    except Exception as exc:
        with contextlib.suppress(psycopg.Error):
            conn.rollback()     # so the caller's connection closes cleanly
        raise ReplayFailed(f"{step}: {type(exc).__name__}: {exc}") from exc
    if "manifest" not in record:
        report.warnings.append("the release record predates manifest entries (#259): "
                               "curated digests were not compared")
    return report

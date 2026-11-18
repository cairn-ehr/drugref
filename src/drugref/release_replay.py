"""Rebuild a published release into an EMPTY database from the archive (#259).

WHY. #258 made a release reconstructable: the archive holds every input file of
every ingest run, the release record names the runs and the code, and the
curated export holds every curated row. This module is the tool that uses them,
so "what did drugref know when this decision was made?" can be answered from the
archive alone, even after the publishing database is gone.

WHAT IT DOES, in order (`replay`):

1. Refuses BEFORE WRITING ANYTHING when the archive fails its re-hash, the target
   database is not empty, a run names a writer no orchestrator implements, the
   checked-out migrations do not end at the release's ledger head, or the running
   code is not the release's git commit (unless `allow_code_mismatch`: then it is
   a warning). Different code can build different memberships from the same bytes.
2. Migrates the empty target.
3. Replays EVERY run in the release record, in order, from the archived blobs
   (symlinked under their original names, because parsers read names: `.gz`,
   openFDA's partition glob), and checks each run re-derives its recorded
   `source_checksum`.
4. Loads the curated rows INTERLEAVED with the runs: a row goes in before the
   first run that started after the curator wrote it. Loading them all at the
   end would be wrong twice over: a later ingest can delete a question a ruling
   cites (its FK insert then fails), and a ruling's reach would be checked
   against membership the curator never saw.
5. Checks, and reports as DIFFERENCES (never raises for them):
   - each class ruling's ruling-time reach (db/080) against what the replayed
     `class_rule_member` gives at the moment the ruling is loaded -- the direct
     test of "the replay rebuilt what the curator saw";
   - the live curated digests against the release's manifest entries.

Ingest guards that compare with STORED counts (fda-cyp's shrink checks, SPL's
floors) are passed open: the original run already passed them. Every run replays
with the release's code, even a run first ingested by older code.

Orchestrators commit, so a replay that fails part-way leaves a half-built target;
it is a scratch database, and a second replay into it is refused (not empty).
"""
import contextlib
import datetime as dt
import fnmatch
import json
import os
import pathlib
import tempfile
from dataclasses import dataclass, field

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from drugref import archive, db, overlay, release_archive, releases
from drugref.ingest import (allergy_group_run, chebi, drugcentral_run, fda_cyp_run,
                            gsrs_run, medrt_run, mesh_rel_run, mesh_run,
                            mesh_synonym_run, moiety_name_run, onchigh_run, pbs_run,
                            run, spl_run)

# Curated tables with a timestamp, in load order for rows written at one instant,
# and each one's primary key. The reach and snapshot tables have no timestamp: they
# load with their ruling.
_TIMED = {"curated_interaction": "curated_interaction_id",
          "curated_condition": "curated_condition_id",
          "curated_class_interaction": "curated_class_interaction_id",
          "curated_property_ruling": "curated_property_ruling_id",
          "class_expansion_policy": "policy_id"}
_RULINGS = "curated_class_interaction"
_REACH = "curated_class_interaction_reach"
_SNAPSHOT = "curated_class_interaction_snapshot"
assert set(_TIMED) | {_REACH, _SNAPSHOT} == set(release_archive._EXPORT_TABLES)


class ReplayRefused(Exception):
    """Nothing was written: each entry of `problems` says why."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class ReplayReport:
    """What a finished replay found. Empty `differences` = reproduced."""
    runs: int = 0
    curated_rows: int = 0
    rulings_checked: int = 0
    differences: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---- one adapter per orchestrator --------------------------------------------
# Each takes (conn, files in run-record order, upstream_release) and maps the files
# to the orchestrator's arguments in the order it passed them to `archived_checksum`.


def _unii(conn, f, release):
    from drugref import cli    # lazy: cli imports cli_audit, which imports us
    run.ingest_unii(conn, unii_path=f[0], crosswalk_path=cli.CROSSWALK,
                    allowlist_path=cli.ALLOWLIST, upstream_release=release)


def split_spl_inputs(files: list[pathlib.Path]):
    """(openFDA partitions, DailyMed parts, UNII_Names) from an SPL run's files.

    The record lists them sorted by name, then two packaged lists the orchestrator
    reads itself (`spl_run.checksummed_inputs`), so the last two are dropped. The
    partitions are named by the glob the orchestrator uses; UNII_Names is the one
    text file; the rest are DailyMed zips.
    """
    corpora = files[:-2]
    partitions = [p for p in corpora
                  if fnmatch.fnmatch(p.name, "drug-label-*.json.zip")]
    names = [p for p in corpora if p.suffix == ".txt"]
    parts = [p for p in corpora if p not in partitions and p not in names]
    return partitions, parts, (names[0] if names else None)


def _spl(conn, f, release):
    partitions, parts, names = split_spl_inputs(f)
    # Every file is staged in one directory, so it doubles as the openFDA directory:
    # the orchestrator globs only the partitions out of it.
    spl_run.ingest_spl(conn, openfda_dir=f[0].parent, dailymed_parts=parts,
                       release=release, unii_names=names)


ADAPTERS = {
    "unii_run": _unii,
    "chebi": lambda c, f, r: chebi.enrich_from_chebi(
        c, chebi_path=f[0], upstream_release=r),
    "medrt_run": lambda c, f, r: medrt_run.ingest_medrt(
        c, medrt_path=f[0], upstream_release=r),
    "mesh_run": lambda c, f, r: mesh_run.ingest_mesh(
        c, pa_path=f[0], desc_path=f[1], supp_path=f[2], upstream_release=r),
    "mesh_rel_run": lambda c, f, r: mesh_rel_run.ingest_mesh_relations(
        c, medrt_path=f[0], desc_path=f[1], supp_path=f[2], upstream_release=r),
    "pbs_run": lambda c, f, r: pbs_run.ingest_pbs(c, f[0], r),
    "gsrs_run": lambda c, f, r: gsrs_run.ingest_gsrs(c, dump_path=f[0],
                                                     upstream_release=r),
    "onchigh_run": lambda c, f, r: onchigh_run.ingest_onchigh(
        c, path=f[0], upstream_release=r),
    "fda_cyp_run": lambda c, f, r: fda_cyp_run.ingest_fda_cyp(
        c, page_path=f[0], upstream_release=r, allow_shrink=True,
        allow_resolution_drop=True),
    "drugcentral_run": lambda c, f, r: drugcentral_run.ingest_drugcentral(
        c, dump_path=f[0], release=r),
    "spl_run": _spl,
    "allergy_group_run": lambda c, f, r: allergy_group_run.ingest_allergy_groups(
        c, allow_list_path=f[0], desc_path=f[1], supp_path=f[2], upstream_release=r),
    "moiety_name_run": lambda c, f, r: moiety_name_run.ingest_moiety_names(
        c, unii_names_path=f[0], upstream_release=r),
    "mesh_synonym_run": lambda c, f, r: mesh_synonym_run.ingest_mesh_synonyms(
        c, desc_path=f[0], supp_path=f[1], upstream_release=r),
}


# ---- pure helpers ------------------------------------------------------------


def _when(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp)


def schedule(rows: list[tuple[str, dict]], runs: list[dict]) -> list[list]:
    """Split (table, row) pairs into len(runs) + 1 batches: batch i loads before
    run i, the last after every run.

    A row joins the batch of the first run that STARTED after the row was written
    (`reviewed_at`), i.e. the first run that could have seen it. A record written
    before `started_at` was recorded falls back to `finished_at`. Within a batch:
    time, then table order (`_TIMED`), then id, so a correction loads after the row
    it supersedes.
    """
    starts = [_when(r.get("started_at") or r["finished_at"]) for r in runs]
    rank = {t: i for i, t in enumerate(_TIMED)}
    ordered = sorted(rows, key=lambda tr: (_when(tr[1]["reviewed_at"]), rank[tr[0]],
                                           tr[1][_TIMED[tr[0]]]))
    batches: list[list] = [[] for _ in range(len(runs) + 1)]
    for table, row in ordered:
        when = _when(row["reviewed_at"])
        i = next((k for k, s in enumerate(starts) if s > when), len(runs))
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


def _columns(conn, table) -> list[str]:
    """Insertable columns (generated ones are computed by the database)."""
    return [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = "
        "'drugref' AND table_name = %s AND is_generated = 'NEVER' "
        "ORDER BY ordinal_position", (table,))]


class _Loader:
    """Inserts exported rows verbatim (ids included) and replays supersession."""

    def __init__(self, conn, export, report):
        self.conn, self.report = conn, report
        self.cols = {t: _columns(conn, t) for t in release_archive._EXPORT_TABLES}
        # Rows a migration seeds (expansion policies) are already in the fresh
        # target: never re-inserted, but a curator's later row may supersede them.
        self.seeded = {t: {r[0] for r in conn.execute(sql.SQL(
            "SELECT {pk} FROM drugref.{t}").format(
            pk=sql.Identifier(pk), t=sql.Identifier(t)))} for t, pk in _TIMED.items()}
        self.loaded = {t: set(ids) for t, ids in self.seeded.items()}
        # superseder id -> ids it superseded, per table
        self.points: dict[str, dict[int, list[int]]] = {t: {} for t in _TIMED}
        for t, pk in _TIMED.items():
            for row in export[t]:
                if row["superseded_by"] is not None:
                    self.points[t].setdefault(row["superseded_by"], []).append(row[pk])
        self.reach: dict[int, list[dict]] = {}
        for row in export[_REACH]:
            self.reach.setdefault(row["curated_class_interaction_id"], []).append(row)
        self.snapshot = {r["curated_class_interaction_id"]: r
                         for r in export[_SNAPSHOT]}

    def _insert(self, table, row) -> None:
        cols = sql.SQL(", ").join(map(sql.Identifier, self.cols[table]))
        self.conn.execute(
            sql.SQL("INSERT INTO drugref.{t} ({c}) OVERRIDING SYSTEM VALUE "
                    "SELECT {c} FROM jsonb_populate_record(NULL::drugref.{t}, %s)"
                    ).format(t=sql.Identifier(table), c=cols), (Jsonb(row),))

    def load(self, table, row) -> None:
        pk = _TIMED[table]
        rid, sup = row[pk], row["superseded_by"]
        if rid in self.seeded[table]:
            return
        # Point at the superseder only if it is already in; otherwise it points
        # back at this row when it loads.
        self._insert(table, dict(row, superseded_by=sup if sup in self.loaded[table]
                                 else None))
        self.loaded[table].add(rid)
        earlier = [o for o in self.points[table].get(rid, [])
                   if o in self.loaded[table]]
        if earlier:
            overlay.point_rows(self.conn, table, pk, rid, earlier)
        self.report.curated_rows += 1
        if table == _RULINGS:
            self._ruling_reach(rid)

    def _ruling_reach(self, rid) -> None:
        """Load a ruling's reach rows; compare a ruling-time snapshot with now."""
        rows = self.reach.get(rid, [])
        marker = self.snapshot.get(rid)
        if marker is not None and marker["captured"] == "at_ruling":
            self.report.rulings_checked += 1
            for side in ("subject", "partner"):
                want = {r["moiety_uuid"] for r in rows if r["side"] == side}
                got = {str(m) for (m,) in self.conn.execute(
                    "SELECT moiety_uuid FROM drugref.class_rule_member "
                    "WHERE curated_class_interaction_id = %s AND side = %s",
                    (rid, side))}
                if want != got:
                    self.report.differences.append(
                        f"class ruling {rid}: {side} reach differs from ruling time: "
                        f"{len(got - want)} added, {len(want - got)} missing")
        for r in rows:
            self._insert(_REACH, r)
        if marker is not None:
            self._insert(_SNAPSHOT, marker)

    def reset_sequences(self) -> None:
        """Move each identity past the loaded ids, so a later write cannot collide."""
        for table, pk in _TIMED.items():
            self.conn.execute(sql.SQL(
                "SELECT setval(pg_get_serial_sequence({q}, {p}), max({pk})) "
                "FROM drugref.{t} HAVING max({pk}) IS NOT NULL").format(
                q=sql.Literal(f"drugref.{table}"), p=sql.Literal(pk),
                pk=sql.Identifier(pk), t=sql.Identifier(table)))


def replay(conn: psycopg.Connection, root: pathlib.Path, tag: str, *,
           allow_code_mismatch: bool = False) -> ReplayReport:
    """Rebuild release `tag` into the empty database `conn` points at.

    Raises `ReplayRefused` before writing anything (see the module docstring);
    otherwise returns the report, whose `differences` are what did not reproduce.
    """
    record = _read_json(archive.release_record_path(root, tag), "release record")
    export = _read_json(archive.curated_export_path(root, tag), "curated export")
    report = ReplayReport()
    refusals = _refusals(conn, root, record, allow_code_mismatch=allow_code_mismatch,
                         warnings=report.warnings)
    if refusals:
        raise ReplayRefused(refusals)
    db.apply_migrations(conn)
    loader = _Loader(conn, export, report)
    rows = [(t, r) for t in _TIMED for r in export[t]]
    runs = record["runs"]
    with _no_archiving():
        for i, batch in enumerate(schedule(rows, runs)):
            for table, row in batch:
                loader.load(table, row)
            conn.commit()      # deferred single-live checks fire here
            if i < len(runs):
                _replay_run(conn, root, runs[i], report)
    loader.reset_sequences()
    conn.commit()
    if "manifest" in record:
        live = [{"target_kind": e.target_kind, "natural_key": e.natural_key,
                 "payload_context": e.payload_context,
                 "payload_digest": e.payload_digest.hex()}
                for e in releases.enumerate_live(conn)]
        report.differences += manifest_differences(record["manifest"], live)
    else:
        report.warnings.append("the release record predates manifest entries (#259): "
                               "curated digests were not compared")
    return report

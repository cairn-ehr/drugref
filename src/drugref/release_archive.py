"""Publish a release together with its audit record (#258).

WHY. `releases.publish` signs WHAT was published. An audit also needs to know
WHICH build and WHICH ingest runs stood behind it, and to hold the curated rows
as they were. This module wraps `releases.publish` (untouched) and adds:

- a `release_build` row (git commit, migration ledger head, run ids), which the
  `current_release` view needs to detect drift (db/081);
- when `DRUGREF_ARCHIVE` is set, `releases/<tag>.json` (every finished ingest
  run, NOT only the ones feeding the live registry) and
  `releases/<tag>.curated.json` (every curated row, superseded included).

It never commits: the CLI owns the transaction. The files are written before
that commit; if the commit then fails they name a tag absent from the database,
which is harmless because the tag is still free.
"""
import json
import pathlib
import subprocess
from pathlib import Path

from drugref import archive, releases

# Primary key per exported table, so the export order is stable and diffable.
_EXPORT_TABLES = {
    "curated_interaction": "curated_interaction_id",
    "curated_condition": "curated_condition_id",
    "curated_class_interaction": "curated_class_interaction_id",
    "curated_property_ruling": "curated_property_ruling_id",
    "class_expansion_policy": "policy_id",
}
assert set(releases._CURATED_KINDS) <= set(_EXPORT_TABLES)


class ArchiveIncomplete(Exception):
    """A finished run's inputs are not (fully) archived; publishing would
    produce a release that cannot be reconstructed."""

    def __init__(self, problems: list[archive.ArchiveProblem]):
        self.problems = problems
        super().__init__("; ".join(f"{p.source_checksum}: {p.what}" for p in problems))


def git_commit() -> str:
    """HEAD of the repo this package lives in, or "unknown" on any failure."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=pathlib.Path(__file__).parent,
            capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip() or "unknown"
    except Exception:  # not a checkout, no git, timeout: provenance is best-effort
        return "unknown"


def finished_runs(conn) -> list[dict]:
    """Every finished ingest run, oldest first (all of them: a source's older
    runs still shaped decisions made while they were current)."""
    cur = conn.execute(
        "SELECT source, writer, ingest_run_id, upstream_release, source_checksum, "
        "finished_at FROM drugref.ingest_run WHERE finished_at IS NOT NULL "
        "ORDER BY ingest_run_id")
    names = [c.name for c in cur.description]
    out = []
    for row in cur.fetchall():
        rec = dict(zip(names, row))
        rec["finished_at"] = rec["finished_at"].isoformat()
        out.append(rec)
    return out


def curated_export(conn) -> dict[str, list[dict]]:
    """All rows of the curated tables plus the expansion policy, superseded
    ones included, ordered by primary key."""
    return {
        table: [r[0] for r in conn.execute(
            f"SELECT to_jsonb(t) FROM drugref.{table} t ORDER BY {pk}")]
        for table, pk in _EXPORT_TABLES.items()}


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def publish_archived(conn, *, root: Path | None, **publish_kwargs) -> int:
    """`releases.publish` + build row + (with `root`) record and export files.

    Raises `ArchiveIncomplete` BEFORE writing anything when `root` is set and a
    finished run is not fully archived. Returns the manifest id.
    """
    tag = publish_kwargs["release_tag"]
    try:
        archive.check_release_tag(tag)
    except ValueError as exc:
        raise ArchiveIncomplete([archive.ArchiveProblem(tag, str(exc))]) from exc
    runs = finished_runs(conn)
    if root is not None:
        for path in (archive.release_record_path(root, tag),
                     archive.curated_export_path(root, tag)):
            if path.exists():
                raise ArchiveIncomplete([archive.ArchiveProblem(
                    tag, f"archive file already exists, never overwritten: {path}")])
        problems = archive.missing_runs(root, {r["source_checksum"] for r in runs})
        if problems:
            raise ArchiveIncomplete(problems)
    manifest_id = releases.publish(conn, **publish_kwargs)
    head = conn.execute(
        "SELECT max(filename) FROM drugref.schema_migration").fetchone()[0]
    commit = git_commit()
    conn.execute(
        "INSERT INTO drugref.release_build "
        "(release_tag, git_commit, ledger_head, run_ids)"
        " VALUES (%s, %s, %s, %s)",
        (tag, commit, head, [r["ingest_run_id"] for r in runs]))
    if root is not None:
        _write_json(archive.release_record_path(root, tag),
                    {"release_tag": tag, "git_commit": commit,
                     "ledger_head": head, "runs": runs})
        _write_json(archive.curated_export_path(root, tag), curated_export(conn))
    return manifest_id

"""Publish a release together with its audit record (#258).

WHY. `releases.publish` signs WHAT was published. An audit also needs to know
WHICH build and WHICH ingest runs stood behind it, and to hold the curated rows
as they were. This module wraps `releases.publish` (untouched) and adds:

- a `release_build` row (git commit, migration ledger head, run ids), which the
  `current_release` view needs to detect drift (db/081);
- when `DRUGREF_ARCHIVE` is set, `releases/<tag>.json` (every finished ingest
  run, NOT only the ones feeding the live registry) and
  `releases/<tag>.curated.json` (every curated row, superseded included, and
  every class-ruling reach and snapshot-marker row).

It never commits: the CLI owns the transaction. The files are written before
that commit; if the commit then fails they name a tag absent from the database.
Publish never overwrites a release file, so that tag stays refused until an
operator removes the leftover files by hand -- the refusal says so.
"""
import json
import logging
import pathlib
import subprocess
from pathlib import Path

from drugref import archive, releases

log = logging.getLogger(__name__)

# Primary key per exported table, so the export order is stable and diffable.
_EXPORT_TABLES = {
    "curated_interaction": "curated_interaction_id",
    "curated_condition": "curated_condition_id",
    "curated_class_interaction": "curated_class_interaction_id",
    "curated_property_ruling": "curated_property_ruling_id",
    "class_expansion_policy": "policy_id",
    # Audit data no upstream file can rebuild: who a class ruling reached (db/080).
    "curated_class_interaction_reach":
        "curated_class_interaction_id, side, moiety_uuid",
    # ...and which rulings were snapshotted at all, so an empty one reads "reached
    # no one", not "never recorded" (db/082, #266).
    "curated_class_interaction_snapshot": "curated_class_interaction_id",
}
assert set(releases._CURATED_KINDS) <= set(_EXPORT_TABLES)

# Tables no rebuild touches (append-only / insert-only) that the export above
# leaves out, each with why. tests/test_release_export_coverage.py fails on any
# such table in neither place, so a new one forces a decision instead of making
# releases silently not reconstructable for it (#270).
_GAP = "curated, NOT YET EXPORTED: a replay cannot restore it (#270)"
NOT_EXPORTED = {
    # Curator work with no writer outside its own module yet.
    "question_state": _GAP,
    "question_source_check": _GAP,   # no trigger: observations, never superseded
    "question_evidence": _GAP,
    "additive_effect": _GAP,
    "effect_contribution": _GAP,
    "interaction_group": _GAP,       # the identity interaction_group_* rows name
    "interaction_group_assertion": _GAP,
    "interaction_group_member": _GAP,
    # Written by the reviewer-service today, so these releases already lack them.
    "reviewer_annotation": _GAP,
    "reviewer_evidence_reference": _GAP,
    "reviewer_account": _GAP + "; who wrote an annotation",
    "reviewer_profile": _GAP + "; who wrote an annotation",
    "reviewer_key_enrolment": _GAP + "; the key a reviewer signs with",
    "signing_key": _GAP + "; the keys signatures verify against",
    "assertion_signature": _GAP + "; curator and release signatures",
    # Never exported: secrets and sessions stay on the node that issued them.
    "reviewer_password_credential": "a password hash: never leaves the node",
    "auth_session": "a login session: node-local and short-lived",
    "auth_session_revocation": "a login session: node-local and short-lived",
    # Vocabularies the migrations seed; a replay migrates its target first.
    "reviewer_role_kind": "a vocabulary db/044 seeds",
    "signing_key_status_kind": "a vocabulary db/030 seeds",
    # Publish's own bookkeeping: the release record carries the manifest, and the
    # replay compares against it rather than restoring it.
    "release_manifest": "written by publish; the release record carries it",
    "release_manifest_entry": "written by publish; the release record carries it",
    "release_build": "written by publish; the release record carries it",
}


class ArchiveIncomplete(Exception):
    """Publish refused before writing anything: a path-like tag, a release file
    that already exists, a finished run whose inputs are not (fully) archived
    (publishing would produce a release that cannot be reconstructed), or an
    archive this process cannot read (#268)."""

    def __init__(self, problems: list[archive.ArchiveProblem]):
        self.problems = problems
        super().__init__("; ".join(f"{p.source_checksum}: {p.what}" for p in problems))


def _git(*argv: str) -> str:
    """stdout of one `git` call in this package's checkout; raises on any failure."""
    return subprocess.run(
        ["git", *argv], cwd=pathlib.Path(__file__).parent,
        capture_output=True, text=True, timeout=10, check=True).stdout


def git_commit() -> str:
    """HEAD of the repo this package lives in: `<sha>`, `<sha>-dirty`, or "unknown".

    `-dirty` when `git status --porcelain` prints anything: the code that built
    the release then differs from the commit, so the sha alone would mislead an
    audit (#258, spec 7). Any failure (not a checkout, no git, timeout) is
    "unknown"; the publish CLI warns on both. So is a repo that does not track
    this file: a non-editable install in a venv inside ANOTHER checkout would
    otherwise record that checkout's HEAD.
    """
    try:
        sha = _git("rev-parse", "HEAD").strip()
        if not sha:
            return "unknown"
        _git("ls-files", "--error-unmatch", pathlib.Path(__file__).name)
        return f"{sha}-dirty" if _git("status", "--porcelain").strip() else sha
    except Exception as exc:  # provenance is best-effort; the CLI says when it failed
        log.warning("git provenance unavailable: %s", exc)
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


def manifest_entries(conn, manifest_id: int) -> list[dict]:
    """A release's signed entries, so a replay can compare without the database
    that published them (#259): the archive held no copy before."""
    return [{"target_kind": kind, "natural_key": key, "payload_context": context,
             "payload_digest": bytes(digest).hex()}
            for kind, key, context, digest in conn.execute(
                "SELECT target_kind, natural_key, payload_context, payload_digest "
                "FROM drugref.release_manifest_entry WHERE manifest_id = %s "
                "ORDER BY target_kind, natural_key", (manifest_id,))]


def curated_export(conn) -> dict[str, list[dict]]:
    """All rows of the curated tables, the expansion policy and the class-ruling
    reach snapshots, superseded ones included, ordered by primary key."""
    return {
        table: [r[0] for r in conn.execute(
            f"SELECT to_jsonb(t) FROM drugref.{table} t ORDER BY {pk}")]
        for table, pk in _EXPORT_TABLES.items()}


def _write_json(path: Path, obj) -> None:
    """Atomic (temp + link), so a crash never leaves a half record to audit, and
    never-overwrite: raises `FileExistsError` if `path` appeared meanwhile (#262)."""
    body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    archive.atomic_write(path, lambda fh: fh.write(body), overwrite=False)


def _existing_file_problem(conn, tag: str, path: Path) -> str:
    """Why `path` blocks this tag: a real release, or debris of an uncommitted one."""
    published = conn.execute("SELECT 1 FROM drugref.release_manifest "
                             "WHERE release_tag = %s", (tag,)).fetchone()
    if published:
        return f"archive file already exists, never overwritten: {path}"
    return (f"archive file {path} has no release_manifest row for {tag} in this "
            "database: another database sharing this archive may have published "
            "the tag, or a publish here did not commit; only in the second case, "
            "after inspecting it, remove it by hand to reuse this tag -- drugref "
            "never deletes archive files")


def _name_runs(problem: archive.ArchiveProblem, runs: list[dict]):
    """Add the run(s) behind a checksum, and the fix, to an archive problem."""
    named = ", ".join(
        f"run {r['ingest_run_id']} ({r['source']}/{r['writer']}, release "
        f"{r['upstream_release']})"
        for r in runs if r["source_checksum"] == problem.source_checksum)
    return archive.ArchiveProblem(problem.source_checksum, (
        f"{problem.what} for {named} -- re-ingest this exact input with "
        f"{archive.ENV_VAR} set"))


def publish_archived(conn, *, root: Path | None, **publish_kwargs) -> int:
    """`releases.publish` + build row + (with `root`) record and export files.

    Raises `ArchiveIncomplete` BEFORE writing anything when the tag is path-like
    (with or without `root`), or when `root` is set and a release file for the
    tag already exists, a finished run is not fully archived, or the archive
    cannot be read. A release file that appears after that check (a racing
    publish) raises the same refusal from the write itself, and this publish's
    database rows are rolled back.
    If the curated export then fails, the release record this call just wrote
    is removed, so no record outlives its rolled-back rows. Returns the
    manifest id.
    """
    tag = publish_kwargs["release_tag"]
    try:
        archive.check_release_tag(tag)
    except ValueError as exc:
        raise ArchiveIncomplete([archive.ArchiveProblem(tag, str(exc))]) from exc
    runs = finished_runs(conn)
    if root is not None:
        try:
            # `stat_or_none`, not `exists()`: that returns False for an unsearchable
            # `releases/` on Python 3.14 but raises on 3.12 (#268).
            for path in (archive.release_record_path(root, tag),
                         archive.curated_export_path(root, tag)):
                if archive.stat_or_none(path) is not None:
                    raise ArchiveIncomplete([archive.ArchiveProblem(
                        tag, _existing_file_problem(conn, tag, path))])
            problems = archive.missing_runs(
                root, {r["source_checksum"] for r in runs})
        except OSError as exc:
            # Unreadable, not missing: one refusal line, never a traceback, and
            # never "re-ingest" for bytes that are there. A read error on an open
            # file has no filename, so fall back to the archive root.
            advice = ("; fix its permissions and publish again"
                      if isinstance(exc, PermissionError) else "")
            raise ArchiveIncomplete([archive.ArchiveProblem(tag, (
                f"cannot read the archive: {exc.filename or root}: "
                f"{exc.strerror or exc}{advice}"))]) from exc
        if problems:
            raise ArchiveIncomplete([_name_runs(p, runs) for p in problems])
    # The exists-check above gives the friendly early refusal; the no-clobber write
    # below is the real guard, for a file that appears in between (#262: e.g. a
    # second database publishing the same tag into a shared archive). The
    # SAVEPOINT makes that late refusal undo this publish's rows: the CLI's
    # `with db.connect` would otherwise COMMIT them on its way out.
    try:
        with conn.transaction():
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
                record = archive.release_record_path(root, tag)
                _write_json(record, {"release_tag": tag, "git_commit": commit,
                                     "ledger_head": head, "runs": runs,
                                     "manifest": manifest_entries(conn, manifest_id)})
                try:
                    _write_json(archive.curated_export_path(root, tag),
                                curated_export(conn))
                except BaseException:
                    # Ours: the no-clobber link just created it, nobody can have
                    # replaced it. Left, it would describe rows rolled back below.
                    record.unlink()
                    raise
    except FileExistsError as exc:
        if exc.filename2 is None:       # not os.link's (e.g. mkdir): not a taken file
            raise
        taken = Path(exc.filename2)     # os.link(tmp, dest): filename2 is dest
        raise ArchiveIncomplete([archive.ArchiveProblem(
            tag, _existing_file_problem(conn, tag, taken))]) from exc
    return manifest_id

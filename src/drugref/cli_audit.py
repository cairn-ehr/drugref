"""`drugref audit` -- reconstruct what was known when a decision was made (#258, #256).

`class-ruling <id>` prints which drugs a class ruling reached when the curator ruled
versus now, and which data loads preceded the review. The SQL lives in
`class_ruling_reach`; this module only prints, per the rule in cli.py's docstring.

`archive [--release TAG]` re-hashes a release's archived inputs (#258); the checks
live in `archive`, and this module reads the release record and curated export.

`replay --release TAG` rebuilds that release into the (empty) database `--dsn`
names, from the archive alone (#259); the work lives in `release_replay`.
"""
import json
import sys

from drugref import (archive, cli_departures, class_ruling_reach, release_replay,
                     release_state)


def _handle_class_ruling(conn, args) -> int:
    """Print the audit; exit 2 for an unknown id."""
    audit = class_ruling_reach.audit_ruling(conn, args.ruling_id)
    if audit is None:
        print(f"drugref: no class ruling with id {args.ruling_id}", file=sys.stderr)
        return 2
    print(f"class ruling {audit.curated_class_interaction_id}: reviewed by "
          f"{audit.reviewed_by} against {audit.reviewed_against} "
          f"at {audit.reviewed_at}")
    for s in audit.sides:
        print(f"{s.side}: then {len(s.then)}, now {len(s.now)} "
              f"(captured: {s.captured})")
        if s.captured == "backfill_db080":
            print("  note: 'then' was reconstructed by db/080, "
                  "NOT recorded at ruling time")
        if s.captured == "none":
            print("  note: no ruling-time snapshot for this side")
        elif not s.then:    # db/082's marker: taken, and empty (#266)
            when = ("when db/080 rebuilt it" if s.captured == "backfill_db080"
                    else "then")
            print(f"  note: snapshot taken: this side reached no one {when}")
        for label, ids in (("added", s.added), ("removed", s.removed)):
            print(f"  {label}: {len(ids)}")
            for u in sorted(ids, key=str):
                print(f"    {u}")
    if audit.sides[0].now == audit.sides[1].now == frozenset():
        print("note: 'now' is empty -- the ruling is superseded (or reaches no one); "
              "class_rule_member covers live rulings only")
    print(f"data loaded at review: {len(audit.loaded_runs)} run(s)")
    for source, writer, run_id, release in audit.loaded_runs:
        print(f"  {source} / {writer}: run {run_id}, release {release}")
    return 0


def _handle_archive(conn, args) -> int:
    """Verify (re-hash) every archived input of a release; exit 0 / 1 / 2 (#258).

    0 = complete, 1 = problems found (each printed), 2 = nothing to check: the
    archive env var is unset, the tag is path-like, no release is named or
    published (or the database predates db/081), the release record is
    missing or malformed, or a file could not be read (an `OSError`, e.g.
    permissions: "could not check", not "problems found"; #261). An unreadable
    file skips only its own run; the rest are still checked, and any problem
    found there wins (exit 1). Default release is the one the database answers
    from. A tag this database has no
    `release_build` row for is still checked -- the database may be what was lost
    -- but gets a note: its files may be debris of a publish that never committed.
    A missing or unparseable curated export is a PROBLEM (exit 1), not "nothing
    to check": it is the only copy of the curated inputs if the DB is lost.
    """
    root = archive.archive_root()
    if root is None:
        print(f"drugref: {archive.ENV_VAR} is not set", file=sys.stderr)
        return 2
    tag = args.release
    if tag is None:
        current = release_state.current_release_or_absent(conn)
        if current is release_state.VIEW_ABSENT:
            print("drugref: the database predates db/081, so it cannot say which "
                  "release it answers from; name one with --release", file=sys.stderr)
            return 2
        if current is None:
            print("drugref: no release is published; name one with --release",
                  file=sys.stderr)
            return 2
        tag = current.release_tag
    try:
        record = archive.release_record_path(root, tag)
    except ValueError as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
    # Read, never `is_file()` first: on an unsearchable directory it raises on
    # Python 3.12 but returns False on 3.14, so "missing" would depend on the version.
    try:
        body = record.read_bytes()     # decoded below: bad bytes are "malformed"
    except FileNotFoundError:
        print(f"drugref: no release record for {tag} at {record}", file=sys.stderr)
        return 2
    except OSError as exc:      # unreadable is "could not check", not a traceback
        print(_unreadable(exc, record), file=sys.stderr)
        return 2
    try:
        checksums = [archive.require_sha256(r["source_checksum"])
                     for r in json.loads(body.decode("utf-8"))["runs"]]
    except (ValueError, KeyError, TypeError):
        print(f"drugref: malformed release record {record}", file=sys.stderr)
        return 2
    if args.release is not None and not _built_here(conn, tag):
        print(f"note: {tag} has no release_build row in this database (published "
              "elsewhere, or a publish that did not commit)", file=sys.stderr)
    blobs: set[str] = set()
    problems: list[archive.ArchiveProblem] = []
    unreadable: list[str] = []
    # One run at a time, so an unreadable file skips only its own run and never
    # hides a problem already found in another.
    for cs in checksums:
        try:
            problems += archive.verify_runs(root, [cs], seen=blobs)
        except OSError as exc:
            unreadable.append(_unreadable(exc, f"run {cs}"))
    export = archive.curated_export_path(root, tag)
    try:
        problems += _curated_export_problems(tag, export)
    except OSError as exc:
        unreadable.append(_unreadable(exc, export))
    for p in problems:
        print(f"{p.source_checksum}: {p.what}")
    for line in unreadable:
        print(line, file=sys.stderr)
    if problems:
        return 1
    if unreadable:
        return 2
    print(f"archive complete for {tag}: {len(checksums)} runs, "
          f"{len(blobs)} blobs verified")
    return 0


def _handle_replay(conn, args) -> int:
    """Exit 0 reproduced, 1 replayed but not reproduced (differences, or a check it
    could not make), 2 refused (nothing written), 3 failed part-way (drop the target).
    """
    root = archive.archive_root()
    if root is None:
        print(f"drugref: {archive.ENV_VAR} is not set", file=sys.stderr)
        return 2
    try:
        archive.check_release_tag(args.release)
    except ValueError as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
    try:
        report = release_replay.replay(conn, root, args.release,
                                       allow_code_mismatch=args.allow_code_mismatch)
    except release_replay.ReplayRefused as exc:
        print(f"drugref: replay of {args.release} refused, nothing written:",
              file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 2
    except release_replay.ReplayFailed as exc:
        print(f"drugref: replay of {args.release} FAILED part-way, the target is "
              f"half-built: drop it before replaying again.\n  {exc}", file=sys.stderr)
        return 3
    for line in report.warnings:
        print(f"warning: {line}", file=sys.stderr)
    for line in report.differences:
        print(line)
    summary = (f"{report.runs} runs, {report.curated_rows} curated rows, "
               f"{report.rulings_checked} ruling reaches checked")
    if report.differences:
        print(f"replay of {args.release}: {len(report.differences)} difference(s) "
              f"({summary})")
        return 1
    if not report.reproduced:
        print(f"replay of {args.release}: no differences found, but NOT verified as a "
              f"reproduction (see the warnings) ({summary})")
        return 1
    print(f"replay of {args.release} reproduced ({summary})")
    return 0


def _built_here(conn, tag: str) -> bool:
    """True when this database recorded publishing `tag` (or predates db/081)."""
    probe = conn.execute("SELECT to_regclass('drugref.release_build')").fetchone()
    if probe[0] is None:
        return True        # cannot tell; the predates-db/081 path says so elsewhere
    return conn.execute("SELECT 1 FROM drugref.release_build WHERE release_tag = %s",
                        (tag,)).fetchone() is not None


def _unreadable(exc: OSError, what) -> str:
    """`drugref: <path>: <reason>`; a read error on an open file has no filename."""
    return f"drugref: {exc.filename or what}: {exc.strerror or exc}"


def _curated_export_problems(tag, path) -> list[archive.ArchiveProblem]:
    """[] when the curated export at `path` exists and parses as a JSON object.

    An `OSError` (unreadable, not corrupt) propagates: the caller reports it as
    "could not check", like any other unreadable archive file.
    """
    try:
        body = path.read_bytes()     # not is_file(): see the record read
    except FileNotFoundError:
        return [archive.ArchiveProblem(tag, f"missing curated export {path}")]
    try:
        if isinstance(json.loads(body.decode("utf-8")), dict):
            return []
    except ValueError:
        pass
    return [archive.ArchiveProblem(tag, f"malformed curated export {path}")]


def register(commands) -> None:
    """Add the `audit` group: `class-ruling`, `archive`, `replay` and `departures`."""
    audit = commands.add_parser(
        "audit", help="reconstruct what was known at decision time")
    sub = audit.add_subparsers(dest="audit_command", required=True)
    cr = sub.add_parser("class-ruling", help="reach of one class ruling, then vs now")
    cr.add_argument("ruling_id", type=int)
    cr.set_defaults(handler=_handle_class_ruling)
    ar = sub.add_parser("archive", help="verify a release's archived input files")
    ar.add_argument("--release", help="release tag (default: the current release)")
    ar.set_defaults(handler=_handle_archive)
    rp = sub.add_parser(
        "replay", help="rebuild a release into the empty database --dsn names")
    rp.add_argument("--release", required=True, help="release tag to rebuild")
    rp.add_argument("--allow-code-mismatch", action="store_true",
                    help="replay with code other than the release's git commit "
                         "(the result may then differ; a warning says so)")
    rp.set_defaults(handler=_handle_replay)
    dp = sub.add_parser(
        "departures", help="every open departure: a reviewed grade that stopped "
                           "reaching a pair and is not acknowledged")
    dp.set_defaults(handler=cli_departures.handle_audit_departures)

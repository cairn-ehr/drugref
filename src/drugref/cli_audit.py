"""`drugref audit` -- reconstruct what was known when a decision was made (#258, #256).

`class-ruling <id>` prints which drugs a class ruling reached when the curator ruled
versus now, and which data loads preceded the review. The SQL lives in
`class_ruling_reach`; this module only prints, per the rule in cli.py's docstring.
"""
import json
import sys

from drugref import archive, class_ruling_reach, release_state


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
    archive env var is unset, no release is named or published, or its record is
    missing. Default release is the one the database currently answers from.
    """
    root = archive.archive_root()
    if root is None:
        print(f"drugref: {archive.ENV_VAR} is not set", file=sys.stderr)
        return 2
    tag = args.release
    if tag is None:
        # An absent view (pre-db/081) is "nothing published", not a crash.
        has_view = conn.execute("SELECT to_regclass(%s)",
                                (release_state.RELEASE_VIEW,)).fetchone()[0]
        current = release_state.current_release(conn) if has_view else None
        if current is None:
            print("drugref: no release is published; name one with --release",
                  file=sys.stderr)
            return 2
        tag = current.release_tag
    record = root / "releases" / f"{tag}.json"
    if not record.is_file():
        print(f"drugref: no release record for {tag} at {record}", file=sys.stderr)
        return 2
    checksums = [r["source_checksum"]
                 for r in json.loads(record.read_text(encoding="utf-8"))["runs"]]
    problems = archive.verify_runs(root, checksums)
    for p in problems:
        print(f"{p.source_checksum}: {p.what}")
    if problems:
        return 1
    blobs = {f["sha256"] for cs in checksums for f in json.loads(
        archive.run_record_path(root, cs).read_text(encoding="utf-8"))["files"]}
    print(f"archive complete for {tag}: {len(checksums)} runs, "
          f"{len(blobs)} blobs verified")
    return 0


def register(commands) -> None:
    """Add the `audit` group: `class-ruling` and `archive`."""
    audit = commands.add_parser(
        "audit", help="reconstruct what was known at decision time")
    sub = audit.add_subparsers(dest="audit_command", required=True)
    cr = sub.add_parser("class-ruling", help="reach of one class ruling, then vs now")
    cr.add_argument("ruling_id", type=int)
    cr.set_defaults(handler=_handle_class_ruling)
    ar = sub.add_parser("archive", help="verify a release's archived input files")
    ar.add_argument("--release", help="release tag (default: the current release)")
    ar.set_defaults(handler=_handle_archive)

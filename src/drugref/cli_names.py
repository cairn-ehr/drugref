"""`drugref ingest moiety-names --unii-names F --release TAG` (#231).

Not a chain step: run it after `ingest unii` (it reads the registry that run
builds), like `ingest allergy-groups`. The ingest is ingest/moiety_name_run.
"""
import pathlib
import sys

from drugref.ingest import moiety_name_run


def _handle_ingest(conn, args) -> int:
    try:
        summary = moiety_name_run.ingest_moiety_names(
            conn, unii_names_path=args.unii_names, upstream_release=args.release)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        # Bad path, a file that is not UTF-8, or a refused file/registry: an
        # operator problem, one line, exit 1. A KeyError here is a bug and keeps
        # its traceback.
        print(f"drugref: moiety-names ingest failed: {exc}", file=sys.stderr)
        return 1
    for field, value in vars(summary).items():
        print(f"{field}={value}")
    return 0


def add_parser(sources) -> None:
    """Register `moiety-names` on `drugref ingest`'s subparser set."""
    p = sources.add_parser(
        "moiety-names",
        help="store UNII official names as searchable moiety names (NOT a chain "
             "step: run it after `ingest unii`)")
    p.add_argument("--release", required=True, help="upstream UNII release tag")
    p.add_argument("--unii-names", required=True, type=pathlib.Path,
                   help="path to UNII_Names_*.txt")
    p.set_defaults(handler=_handle_ingest)

"""`drugref ingest moiety-names` and `drugref ingest mesh-synonyms` (#231).

Neither is a chain step: run both after `ingest unii` (they read the registry
that run builds), like `ingest allergy-groups`. The ingests are
ingest/moiety_name_run (UNII official names) and ingest/mesh_synonym_run (MeSH
entry terms, the BAN/AAN names; search only).
"""
import pathlib
import sys
from xml.etree.ElementTree import ParseError

from drugref.ingest import mesh_synonym_run, moiety_name_run


def _run(label: str, ingest) -> int:
    """Run one ingest and print its summary, or report a refusal in one line."""
    try:
        summary = ingest()
    except (OSError, EOFError, ParseError, UnicodeDecodeError, ValueError) as exc:
        # Bad path, a truncated .gz (EOFError), broken XML, a file that is not
        # UTF-8, or a refused file/registry: an operator problem, one line, exit 1.
        # A KeyError here is a bug and keeps its traceback.
        print(f"drugref: {label} ingest failed: {exc}", file=sys.stderr)
        return 1
    for field, value in vars(summary).items():
        print(f"{field}={value}")
    return 0


def _handle_ingest(conn, args) -> int:
    return _run("moiety-names", lambda: moiety_name_run.ingest_moiety_names(
        conn, unii_names_path=args.unii_names, upstream_release=args.release))


def _handle_mesh_synonyms(conn, args) -> int:
    return _run("mesh-synonyms", lambda: mesh_synonym_run.ingest_mesh_synonyms(
        conn, desc_path=args.desc, supp_path=args.supp,
        upstream_release=args.release))


def add_parser(sources) -> None:
    """Register `moiety-names` and `mesh-synonyms` on `drugref ingest`."""
    p = sources.add_parser(
        "moiety-names",
        help="store UNII official names as searchable moiety names (NOT a chain "
             "step: run it after `ingest unii`)")
    p.add_argument("--release", required=True, help="upstream UNII release tag")
    p.add_argument("--unii-names", required=True, type=pathlib.Path,
                   help="path to UNII_Names_*.txt")
    p.set_defaults(handler=_handle_ingest)

    p = sources.add_parser(
        "mesh-synonyms",
        help="store MeSH entry terms (BAN/AAN names) as search-only moiety names "
             "(NOT a chain step: run it after `ingest unii`)")
    p.add_argument("--release", required=True, help="upstream MeSH release tag")
    p.add_argument("--desc", required=True, type=pathlib.Path,
                   help="path to desc20XX.xml (or .gz)")
    p.add_argument("--supp", required=True, type=pathlib.Path,
                   help="path to supp20XX.xml (or .gz)")
    p.set_defaults(handler=_handle_mesh_synonyms)

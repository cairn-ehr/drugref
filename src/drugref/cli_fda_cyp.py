# src/drugref/cli_fda_cyp.py
"""The `drugref ingest fda-cyp` subcommand: its parser wiring and its handler.

WHY ITS OWN MODULE, when every other ingest source is a STEPS entry that
cli.py's generic `_handle_ingest` drives: fda-cyp is the one hand-built
exception, because its release is OPTIONAL (the page states its own stamp) and
because it carries a refusal no other source has (--allow-shrink). That is
three arguments and a handler of its own, and cli.py sits at CLAUDE.md rule 4's
~500-line cap -- which the rule says to answer by refactoring, not by shaving
the comments that explain the surface. cli_curate, cli_policy, cli_status and
the rest are the established shape for exactly this, so this file follows them
rather than inventing a second convention.

Unlike cli_chain, this module DOES import from drugref: cli_chain is pinned
import-free by test_cli_chain_imports_nothing_from_drugref because it is the
pure argument-checking layer, and that is precisely why check_fda_cyp_release
takes `page_release` as a parameter instead of reading the page itself. The
read happens here.
"""
import pathlib

from drugref.cli_chain import check_fda_cyp_release
from drugref.ingest import fda_cyp, fda_cyp_run


def handle_fda_cyp(conn, args) -> int:
    """`drugref ingest fda-cyp --page <path> [--release <tag>] [--allow-shrink]`.

    --release is optional because the page states its own (fda_cyp.parse_release);
    cli_chain.check_fda_cyp_release refuses one that disagrees with it.

    --allow-shrink is threaded through, never re-implemented here: the count it
    guards is what is already STORED, which only the writer can see.
    """
    if args.release is not None:
        page_text = args.page.read_text(encoding="utf-8")
        check_fda_cyp_release(args.page, args.release, fda_cyp.parse_release(page_text))
    summary = fda_cyp_run.ingest_fda_cyp(
        conn, page_path=args.page, allow_shrink=args.allow_shrink)
    print(f"fda-cyp: {summary}")
    return 0


def add_parser(sources) -> None:
    """Register the `fda-cyp` subcommand on `drugref ingest`'s subparser set."""
    parser = sources.add_parser(
        "fda-cyp", help="ingest FDA's CYP/transporter examples table")
    parser.add_argument("--page", required=True, type=pathlib.Path,
                        help="path to the downloaded FDA CYP/transporter page")
    parser.add_argument("--release", default=None,
                        help="upstream release tag; if given, must match the "
                             "page's own stamp (default: read from page)")
    # DEFAULT-OFF IS THE WHOLE POINT. A truncated fetch parses green -- every
    # surviving row is still the right width and its pathway is still in the
    # closed vocabulary -- and fda_cyp_assertion is delete-and-rebuild, so an
    # unguarded run replaces the full projection with a handful of rows and
    # reports success. FDA genuinely shrinking the table is a real event; it
    # just has to be a decision somebody made.
    parser.add_argument("--allow-shrink", action="store_true",
                        help="authorise a page far shorter than the stored "
                             "projection (refused by default)")
    parser.set_defaults(handler=handle_fda_cyp)

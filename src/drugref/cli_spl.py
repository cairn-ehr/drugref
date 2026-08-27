# src/drugref/cli_spl.py
"""The `drugref ingest spl` subcommand: its parser wiring and handler.

WHY ITS OWN MODULE rather than a STEPS entry that cli.py's generic
`_handle_ingest` drives: this source is NOT a chain step, on exactly
`cli_drugcentral.py`'s and `cli_fda_cyp.py`'s terms, and cli.py sits at CLAUDE.md
rule 4's ~500-line cap.

WHY NOT A CHAIN STEP, and here the reason is stronger than for either sibling:
the inputs are **19.3 GB across two publishers** -- openFDA's 14 bulk partitions
(1.73 GB) and DailyMed's 6 Human Rx parts (17.6 GB) -- and the DailyMed pass
reads all of it. The chain is the routine rebuild-everything path; a step there
resolves its inputs BEFORE any step runs, so a node without 19.3 GB on disk would
watch the whole chain abort.

THE DATA DEPENDENCY IS REAL and is stated in --help rather than only in a
comment: every subject resolves through a live `identity_claim` UNII and every
occurrence through `substance_moiety.display_name`, so `unii` and `chebi` must
have run first.

THE MEASURED FLOORS ARE ON BY DEFAULT HERE, and that is the point of putting them
at the CLI rather than in the orchestrator's signature: this is the production
path, so a real run asserts `>= 29,258` pairs without anyone remembering to, and
a deliberately partial corpus has to say `--no-pair-floor` out loud.
"""
import pathlib

from drugref.ingest import spl_run


def handle_spl(conn, args) -> int:
    """`drugref ingest spl --openfda <dir> --dailymed <parts...> --release <tag>`."""
    summary = spl_run.ingest_spl(
        conn,
        openfda_dir=args.openfda,
        dailymed_parts=args.dailymed,
        release=args.release,
        pair_floor=None if args.no_pair_floor else spl_run.MEASURED_PAIR_FLOOR,
        novel_floor=None if args.no_pair_floor else spl_run.MEASURED_NOVEL_FLOOR,
        # Printed as it goes, not held to the end: the DailyMed pass takes tens
        # of minutes and an operator watching a silent terminal cannot tell a
        # slow scan from a hung one.
        progress=print)
    print(f"spl: {summary}")
    return 0


def add_parser(sources) -> None:
    """Register the `spl` subcommand on `drugref ingest`'s subparser set."""
    parser = sources.add_parser(
        "spl",
        help="ingest SPL section 34073-7 drug-interaction evidence from "
             "openFDA + DailyMed (run AFTER unii and chebi: subjects resolve "
             "through UNII and occurrences through display_name)")
    parser.add_argument(
        "--openfda", required=True, type=pathlib.Path,
        help="directory holding openFDA's drug-label-*.json.zip partitions")
    parser.add_argument(
        "--dailymed", required=True, nargs="+", type=pathlib.Path,
        help="DailyMed Human Rx release parts (dm_spl_release_human_rx_part*.zip)")
    # REQUIRED, as DrugCentral's is and for the same reason: openFDA publishes an
    # `export_date` and DailyMed a `last-modified`, and this ingest reads TWO
    # corpora with two different stamps, so a tag drugref guessed would be a
    # provenance claim nobody made.
    parser.add_argument(
        "--release", required=True,
        help="upstream release tag covering BOTH corpora, e.g. "
             "'openfda-2026-08-22+dailymed-2026-08-21'")
    parser.add_argument(
        "--no-pair-floor", action="store_true",
        help="skip the measured >= 29,258-pair / >= 25,960-novel floor check. "
             "For a deliberately partial corpus ONLY -- on the full releases a "
             "shortfall means the matcher, the subject rule or the corpus "
             "changed, and that is what the check is for")
    parser.set_defaults(handler=handle_spl)

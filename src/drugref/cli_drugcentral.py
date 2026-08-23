# src/drugref/cli_drugcentral.py
"""The `drugref ingest drugcentral` subcommand: its parser wiring and handler.

WHY ITS OWN MODULE rather than a STEPS entry that cli.py's generic
`_handle_ingest` drives: this source is NOT a chain step. cli_fda_cyp.py is the
established shape for exactly that, and cli.py sits at CLAUDE.md rule 4's
~500-line cap.

WHY NOT A CHAIN STEP. The only published dump is
`drugcentral.dump.11012023.sql.gz` -- 1.4 GB, dated 2023-11-01, with no successor
offered as of 2026-08-23. The chain is the routine rebuild-everything path; a
step there resolves its inputs BEFORE any step runs, so a node without the dump
would watch the whole chain abort, which is the failure
IngestStep.packaged_defaults was added to fix once already.

THE DATA DEPENDENCY IS REAL and is stated in --help rather than only in a
comment: the resolution cascade joins substance_moiety.display_name and live
INCHIKEY/CAS identity_claim rows, so `unii` and `chebi` must have run first.
"""
import pathlib

from drugref.ingest import drugcentral_run


def handle_drugcentral(conn, args) -> int:
    """`drugref ingest drugcentral --dump <path> --release <tag>`."""
    summary = drugcentral_run.ingest_drugcentral(
        conn, dump_path=args.dump, release=args.release)
    print(f"drugcentral: {summary}")
    return 0


def add_parser(sources) -> None:
    """Register the `drugcentral` subcommand on `drugref ingest`'s subparser set."""
    parser = sources.add_parser(
        "drugcentral",
        help="ingest DrugCentral's NDF-RT drug-drug interactions "
             "(run AFTER unii and chebi: the cascade needs the registry)")
    parser.add_argument("--dump", required=True, type=pathlib.Path,
                        help="path to drugcentral.dump.<release>.sql.gz")
    # REQUIRED, unlike fda-cyp's. The dump carries a `dbversion` but drugref does
    # not read it, so nothing here could contradict the operator -- and a
    # provenance tag drugref guessed would be worse than one it was given.
    parser.add_argument("--release", required=True,
                        help="upstream release tag, e.g. 11012023")
    parser.set_defaults(handler=handle_drugcentral)

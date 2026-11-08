# tools/citation_census.py
"""Report curated rows that cite a question about something else (#210).

READ-ONLY: it runs `citation_audit.miscited` and prints one line per finding. See
that module for why a miscited row matters and how it is fixed (re-record the ruling
through its writer; never UPDATE an append-only row).

Usage:
    uv run python -m tools.citation_census --dsn "host=localhost port=5532 dbname=..."
    (or set DRUGREF_DSN). Exit status: 0 when nothing is miscited, 1 otherwise.
"""
import argparse
import sys

from drugref import citation_audit, db, server_messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--dsn", help="PostgreSQL DSN (default: $DRUGREF_DSN)")
    args = parser.parse_args(argv)
    with db.connect(args.dsn) as conn:
        found = citation_audit.miscited(conn)
    for f in found:
        state = "live" if f["live"] else "superseded"
        print(f"{f['table']} #{f['row_id']} ({state}) cites {f['question_uuid']} "
              f"= {f['cited_gap_kind']} {f['cited_gap_key']}")
    print(f"{len(found)} miscited row(s)")
    return 1 if found else 0


if __name__ == "__main__":
    # One log format per entry point (issue 179), so server NOTICEs are not dropped.
    server_messages.configure_logging()
    sys.exit(main())

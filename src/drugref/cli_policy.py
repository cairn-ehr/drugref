# src/drugref/cli_policy.py
"""`drugref policy` -- the operator surface for expansion decisions (#61).

SPLIT OUT OF cli.py, not because the surface is unrelated to it, but because cli.py's
subject is the INGEST step table and this is curation. medrt_run warns an operator
when a release stops defining a class somebody ruled on, and tells them to "re-key or
withdraw"; since db/027 both verbs are unavailable as raw SQL -- DELETE raises, and so
does UPDATE ... SET source_code -- so following that warning meant writing Python
against the library.

LIKE cli.py, THIS MODULE WRITES NO SQL. Every read and write goes through
interactions.py. That is load-bearing rather than stylistic: the test proving only one
VIEW reads class_expansion_policy directly works from pg_rewrite, which sees views and
matviews and CANNOT see a query embedded in Python -- so a handler with its own SELECT
would be a reader of an append-only curated table that no test in this repository
could notice. tests/test_overlay_contract.py pins the Python half by grep.
"""
import sys

from drugref import interactions


def _handle_policy_record(conn, args) -> int:
    """Record or revise an expansion decision. COMMITS -- the CLI is the caller, and
    in these modules the caller owns the transaction."""
    if args.decision == interactions.WITHDRAWN:
        # The library accepts this and deliberately does not guard it (a guard would
        # put a member of db/027's vocabulary back into Python). An operator surface
        # is a different matter: this path skips the NoLiveDecisionError that catches
        # a caller believing something false, and skips carrying class_name forward
        # from the row being retracted.
        print(f"drugref: --decision {interactions.WITHDRAWN} is not recorded here. "
              "Use `drugref policy withdraw`, which refuses to withdraw a decision "
              "nobody made and carries the reviewed class name forward.",
              file=sys.stderr)
        return 2
    policy_id = interactions.record_expansion_decision(
        conn, args.source, args.code, args.decision, args.class_name,
        args.rationale, args.reviewed_by, args.reviewed_against)
    conn.commit()
    print(f"recorded policy_id={policy_id}: "
          f"{args.source} {args.code} -> {args.decision}")
    return 0


def _handle_policy_withdraw(conn, args) -> int:
    """Retract the live decision, returning the class to gap_unreviewed_expansion_root.

    NoLiveDecisionError propagates to main, which reports it without a traceback.
    """
    policy_id = interactions.withdraw_expansion_decision(
        conn, args.source, args.code, args.rationale, args.reviewed_by,
        args.reviewed_against)
    conn.commit()
    print(f"withdrawn policy_id={policy_id}: {args.source} {args.code} "
          "(the class is unreviewed again, so it expands AND raises a question)")
    return 0


def _handle_policy_show(conn, args) -> int:
    """What binds, or one class's whole history. Reads only -- nothing to commit."""
    if args.code is None:
        rows = interactions.live_decisions(conn)
        print("binding decisions:" if rows else "binding decisions: none")
        for source, code, decision, class_name in rows:
            print(f"  {source:<8} {code:<12} {decision:<10} {class_name}")
        # The other half of the answer, for the same reason `status` prints two
        # blocks: a decision that binds nothing looks exactly like one that works.
        # Hardcoded to MED-RT: it is the only source with policy rows today, and
        # unresolved_expansion_policy is scoped by source by design. A known
        # simplification, not an oversight -- revisit if a second source arrives.
        unresolved = interactions.unresolved_expansion_policy(conn, "MED-RT")
        print(f"\nbinding but matching no class: {len(unresolved)}"
              + (f" ({', '.join(unresolved)})" if unresolved else ""))
        return 0

    history = interactions.decision_history(conn, args.source, args.code)
    if not history:
        print(f"{args.source} {args.code}: no decision — unreviewed, so it expands "
              "and raises a question on gap_unreviewed_expansion_root")
        return 0
    print(f"{args.source} {args.code}, oldest first:")
    for policy_id, decision, rationale, by, against, superseded_by in history:
        mark = "  " if superseded_by else "* "      # * marks the live row
        print(f"{mark}#{policy_id} {decision:<10} [{by} vs {against}] {rationale}")
    return 0


def register(commands) -> None:
    """Add the `policy` command group to an existing subparsers object.

    Called by cli.build_parser. Registration rather than a parser of its own, so the
    global --dsn/--log-level flags and the single connect-and-dispatch path in
    cli.main keep serving every command.

    The --source/--code-must-arrive-together rule for `show` lives on cli._Parser,
    not here: it is a property of the PARSER (argparse has no declarative way to
    express it), and threading a parser instance through this function just to run
    that one check here would buy nothing.
    """
    policy = commands.add_parser(
        "policy", help="record, withdraw or inspect class-expansion decisions")
    policy_actions = policy.add_subparsers(dest="action", required=True)

    record = policy_actions.add_parser(
        "record", help="record or revise whether a class expands over its subtree")
    record.add_argument("--source", required=True,
                        help="who DEFINES the class (half the natural key), e.g. MED-RT")
    record.add_argument("--code", required=True, help="the class's source_code")
    # No `choices`: the vocabulary lives in db/027's CHECK, and a second list is a
    # second thing to disagree with the first (db/006). An unrecognised value reaches
    # the database and raises CheckViolation.
    record.add_argument("--decision", required=True,
                        help="the ruling, as db/027's CHECK defines it")
    record.add_argument("--class-name", required=True,
                        help="the class's name, as reviewed")
    record.add_argument("--rationale", required=True,
                        help="why -- this is what survives as history")
    record.add_argument("--reviewed-by", required=True)
    record.add_argument("--reviewed-against", required=True,
                        help="the release the ruling was measured against")
    record.set_defaults(handler=_handle_policy_record)

    withdraw = policy_actions.add_parser(
        "withdraw", help="retract a ruling, returning the class to unreviewed")
    withdraw.add_argument("--source", required=True)
    withdraw.add_argument("--code", required=True)
    withdraw.add_argument("--rationale", required=True)
    withdraw.add_argument("--reviewed-by", required=True)
    withdraw.add_argument("--reviewed-against", required=True)
    withdraw.set_defaults(handler=_handle_policy_withdraw)

    show = policy_actions.add_parser(
        "show", help="what binds, or one class's whole history")
    show.add_argument("--source", help="with --code, the class to show history for")
    show.add_argument("--code", help="with --source, the class to show history for")
    show.set_defaults(handler=_handle_policy_show)

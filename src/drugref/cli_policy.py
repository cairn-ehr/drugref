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


class _BlankArgumentError(ValueError):
    """A required flag was passed, but its value strips to empty."""


def _reject_blank(args, *dests: str) -> None:
    """Refuse a flag the operator passed with a blank (or whitespace-only) value.

    argparse's `required=True` checks PRESENCE, not content -- the same gap
    cli.selected_steps guards against for `--<source>-release`, under the heading
    "PRESENCE, NOT TRUTHINESS": a flag the operator DID pass with an empty value is a
    silently wrong answer, not a missing one. The stakes here are higher than a
    skipped ingest step -- db/010 has NOT NULL with no non-blank CHECK on
    class_name/rationale/reviewed_by/reviewed_against, so a blank slips straight
    through into a row the append-only floor then makes UNCORRECTABLE (no DELETE).
    `withdraw`'s carry-forward would then propagate a blank class_name into every
    later row for that class, through the very mechanism meant to prevent an
    unreviewed name.

    Checked here, before any write, rather than left to db/010's NOT NULL: NOT NULL
    does not reject '  '. This is a check on EMPTINESS, not on content, so unlike a
    `choices=` on `--decision` it adds no second vocabulary for db/006's lesson to
    disagree with.
    """
    for dest in dests:
        if not getattr(args, dest).strip():
            flag = "--" + dest.replace("_", "-")
            raise _BlankArgumentError(f"{flag} was given a blank value")


def _handle_policy_record(conn, args) -> int:
    """Record or revise an expansion decision. COMMITS -- the CLI is the caller, and
    in these modules the caller owns the transaction."""
    try:
        _reject_blank(args, "source", "code", "decision", "class_name", "rationale",
                     "reviewed_by", "reviewed_against")
    except _BlankArgumentError as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
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
    try:
        _reject_blank(args, "source", "code", "rationale", "reviewed_by",
                     "reviewed_against")
    except _BlankArgumentError as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
    policy_id = interactions.withdraw_expansion_decision(
        conn, args.source, args.code, args.rationale, args.reviewed_by,
        args.reviewed_against)
    conn.commit()
    # "It expands" always holds -- absent means unreviewed, which expands by
    # default. "Raises a question" does NOT: gap_unreviewed_expansion_root also
    # requires a substance_class row for this code, which is exactly what is
    # missing when medrt_run's warning is what sent the operator here (a release
    # that stopped defining the class). Overstating it would read as confirmation
    # a worklist entry exists when it may not.
    print(f"withdrawn policy_id={policy_id}: {args.source} {args.code} "
          "(the class is unreviewed again, so it expands by default; if this "
          "release still defines the class, that also raises a question on "
          "gap_unreviewed_expansion_root)")
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
    # * marks the LIVE row -- NOT the binding one. A withdrawn row is live without
    # binding (class_expansion_policy_current is where "live" and "binding" part
    # ways), so "* #19 withdrawn" reads as "this is what applies" unless the legend
    # says otherwise up front.
    print("  (* = live; a live 'withdrawn' row is live but binds nothing)")
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

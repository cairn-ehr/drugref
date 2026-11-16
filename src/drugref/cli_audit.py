"""`drugref audit` -- reconstruct what was known when a decision was made (#258, #256).

`class-ruling <id>` prints which drugs a class ruling reached when the curator ruled
versus now, and which data loads preceded the review. The SQL lives in
`class_ruling_reach`; this module only prints, per the rule in cli.py's docstring.
"""
import sys

from drugref import class_ruling_reach


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


def register(commands) -> None:
    """Add the `audit` group (a later task adds `audit archive` beside class-ruling)."""
    audit = commands.add_parser(
        "audit", help="reconstruct what was known at decision time")
    sub = audit.add_subparsers(dest="audit_command", required=True)
    cr = sub.add_parser("class-ruling", help="reach of one class ruling, then vs now")
    cr.add_argument("ruling_id", type=int)
    cr.set_defaults(handler=_handle_class_ruling)

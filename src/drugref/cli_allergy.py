"""`drugref ingest allergy-groups` and `drugref allergy check`.

No SQL beyond a name lookup: the ingest is ingest/allergy_group_run and the check is
allergy_read.check (a wrapper over the SQL function cairn-ehr calls directly).

EXIT CODES of `allergy check`: 0 for `match` or `no-match`, 3 for `unplaced`, so a
script can never mistake "not checked" for "no match" (a wrong no-match is a missed
allergy warning).
"""
import pathlib
import sys
import uuid

from drugref import allergy_read, migration_guard
from drugref.ingest import allergy_group_run

# The allow-list ships inside the package (same place the other closed seeds live).
DEFAULT_ALLOW_LIST = (pathlib.Path(__file__).parent / "data" / "allergy_groups.toml")

UNPLACED_EXIT = 3


def _handle_ingest(conn, args) -> int:
    try:
        summary = allergy_group_run.ingest_allergy_groups(
            conn, desc_path=args.desc, supp_path=args.supp,
            allow_list_path=args.allow_list, upstream_release=args.release)
    except (OSError, ValueError, LookupError) as exc:
        # Bad path or malformed input: an operator problem, one line, exit 1.
        print(f"drugref: allergy-groups ingest failed: {exc}", file=sys.stderr)
        return 1
    for field, value in vars(summary).items():
        print(f"{field}={value}")
    return 0


def _name(conn, moiety_uuid) -> str:
    """display_name of a moiety (the UUID itself if somehow absent)."""
    row = conn.execute("SELECT display_name FROM drugref.substance_moiety "
                       "WHERE moiety_uuid = %s", (moiety_uuid,)).fetchone()
    return row[0] if row else str(moiety_uuid)


def render_match(conn, m: allergy_read.AllergyMatch) -> str:
    """One line per path: `kind  group [status] UI  allergen:X -> prescribed:Y`."""
    parts = [m.kind]
    if m.group_name:  # same-drug has no group
        parts.append(f"{m.group_name} [{m.review_status}] {m.group_mesh_ui}")
    # Two spaces separate the fields; the group field itself holds single spaces.
    allergen = (_name(conn, m.allergen_component) if m.allergen_component
                else m.group_name)
    prescribed = _name(conn, m.prescribed_component)
    parts.append(f"allergen:{allergen} -> prescribed:{prescribed}")
    return "  ".join(parts)


def _handle_check(conn, args) -> int:
    # A database predating db/068 has no allergy_check function: say so, no traceback.
    with migration_guard.guarded(
            conn, relations=("drugref.allergy_group",), migration="068",
            consequence="allergy checks cannot be answered"):
        ans = allergy_read.check(conn, args.prescribed, args.allergen)
    print(ans.outcome + (f"  ({ans.reason})" if ans.reason else ""))
    for m in ans.matches:
        print(render_match(conn, m))
    return UNPLACED_EXIT if ans.outcome == "unplaced" else 0


def add_parser(sources) -> None:
    """Register `allergy-groups` on `drugref ingest`'s subparser set."""
    p = sources.add_parser(
        "allergy-groups",
        help="build allergy groups from MeSH + the allow-list (NOT a chain step: "
             "run it after the chain, like `drugref curate`)")
    p.add_argument("--release", required=True, help="upstream MeSH release tag")
    p.add_argument("--desc", required=True, type=pathlib.Path,
                   help="path to the MeSH descriptor XML")
    p.add_argument("--supp", required=True, type=pathlib.Path,
                   help="path to the MeSH supplementary-concept XML")
    p.add_argument("--allow-list", type=pathlib.Path, default=DEFAULT_ALLOW_LIST,
                   help="allow-list TOML (default: packaged "
                        f"{DEFAULT_ALLOW_LIST.name})")
    p.set_defaults(handler=_handle_ingest)


def register(commands) -> None:
    """Register the top-level `allergy` command."""
    allergy = commands.add_parser("allergy", help="allergy-group checks")
    sub = allergy.add_subparsers(dest="allergy_command", required=True)
    c = sub.add_parser("check", help="would PRESCRIBED trigger an ALLERGEN allergy? "
                                     "(exit 3 = unplaced, i.e. not checked)")
    c.add_argument("prescribed", type=uuid.UUID, metavar="PRESCRIBED_UUID")
    c.add_argument("allergen", type=uuid.UUID, metavar="ALLERGEN_UUID")
    c.set_defaults(handler=_handle_check)

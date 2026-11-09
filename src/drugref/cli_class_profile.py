"""`drugref profile` -- inspect class profiles and record property rulings.

No SQL here (it lives in class_profile_read / curation_property).
The renderers are pure.

COPY RULE: a missing property is "not confirmed", never "safe" or "does not apply";
the word "exception" appears only as "confirmed exception" (spec §5).
"""
# shortcut: UUIDs only on the command line, no name lookup — add one when a human operator uses it daily.  # noqa: E501
import sys
import uuid

import psycopg

from drugref import class_profile_read as cpr
from drugref import curation_property


def _prop(p: cpr.PropertyLine) -> str:
    return f"{p.relationship} {p.object_kind} {p.object_name}"


def render_class_profile(p: cpr.ClassProfile) -> list[str]:
    out = [f"{p.class_name} [{p.source} {p.concept_type}] -- {len(p.members)} members: "
           f"{', '.join(p.members)}"]
    for cp in p.properties:
        out.append(f"  {_prop(cp.prop)}")
        if cp.is_asserted:
            out.append(f"    asserted ({', '.join(cp.asserted_by)})"
                       + (f" via {', '.join(cp.asserted_via)}"
                          if cp.asserted_via else ""))
        if cp.is_observed:
            # #206: the partner drug is left out of its own count; say so, or the
            # smaller number reads as a missing member.
            of = (f"the {cp.members} members other than {cp.prop.object_name}"
                  if cp.members < len(p.members) else f"{cp.members} members")
            out.append(f"    observed in {cp.carriers} of {of}")
        if cp.not_confirmed:
            out.append(f"    not confirmed for: {', '.join(cp.not_confirmed)}")
        if cp.confirmed_exceptions:
            out.append(f"    confirmed exception: {', '.join(cp.confirmed_exceptions)}")
    for drug, lines in p.peculiarities.items():
        out.append(f"  peculiar to {drug}: " + "; ".join(_prop(x) for x in lines))
    if p.subclass_only:
        out.append("  in subclasses only:")
        n_asserted = sum(cp.is_asserted for cp in p.properties)
        out.extend(_subclass_only_line(e, n_asserted) for e in p.subclass_only)
    if p.label_statements:
        # Kept apart from the properties above on purpose: a machine reading of label
        # prose is not a confirmed fact; a curator's does_not_hold is shown, not hidden.
        out.append("  What labels say about this class (machine-read, not confirmed):")
        for s in p.label_statements:
            out.extend(_label_lines(s))
    return out


def _label_lines(s: cpr.LabelStatement) -> list[str]:
    # §6.3: no_meaningful_interaction and not_read are listed but VISIBLY not an
    # interaction, so neither is printed in the "label: <level>" form a strength uses.
    if s.strongest_level == "not_read":
        line = (f"    {s.drug}'s label mentions the class; advice not read "
                f"({s.label_count} label(s))")
    elif s.strongest_level == "no_meaningful_interaction":
        line = (f"    {s.drug}'s label says no meaningful interaction "
                f"({s.level_labels} of {s.label_count} label(s))")
    else:
        line = (f"    {s.drug}'s label: {s.strongest_level} "
                f"({s.level_labels} of {s.label_count} label(s))")
    if s.mixed:
        line += "; mixed"
    if s.ambiguous:
        line += "; name also matches another class"
    if s.subject_in_class:
        line += "; its own class"
    if s.ruling:
        line += f"; curator ruled: {s.ruling}"
    if s.strongest_level == "not_read":
        return [line]  # nothing was read, so there is no reading to cite
    cite = f"      cited: SPL set {s.cite_set_id} version {s.cite_version}"
    return [line, cite + (f': "{s.quote}"' if s.quote else "")]


def _subclass_only_line(e: cpr.SubclassOnlyDrug, n_asserted: int) -> str:
    """Spec §4: how many of the asserted properties above the drug inherits (observed
    ones never pass down), naming each one it does not: its confirmed exceptions and
    an interaction with itself."""
    line = f"    {e.name} (via {e.via})"
    if not n_asserted:
        return line
    if len(e.inherits) == n_asserted:
        line += ": inherits every asserted property above"
    else:
        line += (f": inherits {len(e.inherits)} of the {n_asserted} asserted "
                 "properties above")
    if e.confirmed_exceptions:
        line += "; confirmed exception: " + "; ".join(
            _prop(x) for x in e.confirmed_exceptions)
    # The reader drops at most one row beyond those: "interacts_with" this drug itself.
    if len(e.inherits) + len(e.confirmed_exceptions) < n_asserted:
        line += f"; not with itself: interacts_with moiety {e.name}"
    return line


def render_drug_profile(p: cpr.DrugProfile) -> list[str]:
    out = [f"{p.name} -- classes: {', '.join(n for _, n in p.class_tags) or '(none)'}"]
    for lens in p.lenses:
        out.append(f"  as a member of {lens.class_name}:")
        out.extend(f"    shared: {_prop(x)} ({st})" for x, st in lens.shared)
        out.extend(f"    not confirmed for this drug: {_prop(x)}"
                   for x in lens.not_confirmed)
        out.extend(f"    confirmed exception: {_prop(x)}"
                   for x in lens.confirmed_exceptions)
        out.extend(f"    peculiar: {_prop(x)}" for x in lens.peculiar)
    if p.own_properties:
        out.append("  own properties (no class tag):")
        out.extend(f"    {_prop(x)}" for x in p.own_properties)
    return out


def _handle_class(conn, args) -> int:
    try:
        p = cpr.class_profile(conn, args.class_uuid)
    except LookupError as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
    print("\n".join(render_class_profile(p)))
    return 0


def _handle_drug(conn, args) -> int:
    try:
        p = cpr.drug_profile(conn, args.moiety_uuid, args.lens or ())
    except (LookupError, ValueError) as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
    print("\n".join(render_drug_profile(p)))
    return 0


def _handle_rule(conn, args) -> int:
    # The values came straight off the command line, so a refusal is an operator's typo:
    # one line and exit 2, the cli_policy._write pattern (cli.main deliberately does not
    # catch database errors for every handler). CheckViolation is an IntegrityError; the
    # DEFERRED single-live guard raises (RaiseException) at COMMIT, so the commit is
    # inside the try too.
    try:
        rid, written = curation_property.record_property_ruling(
            conn, subject_class_uuid=args.klass, subject_moiety_uuid=args.moiety,
            relationship=args.relationship, object_kind=args.object_kind,
            object_uuid=args.object, verdict=args.verdict, reviewed_by=args.reviewed_by,
            reviewed_against=args.reviewed_against, question_uuid=args.question)
        conn.commit()  # the writer never commits; the CLI owns the transaction
    except (ValueError, psycopg.errors.IntegrityError,
            psycopg.errors.RaiseException) as exc:
        conn.rollback()  # never rely on COMMIT of an aborted transaction rolling back
        print(f"drugref: {exc}", file=sys.stderr)
        return 2
    print(f"ruling {rid} " + ("recorded" if written else "already live (unchanged)"))
    if args.klass and args.verdict == "does_not_hold":
        # db/062's class_asserted_property COMMENT: not a negation. Say so where the
        # curator is, or they will believe they refuted an observed property.
        print("drugref: note: a class does_not_hold only withdraws this class's own "
              "earlier holds; an observed property, or one asserted on an ancestor, "
              "still stands", file=sys.stderr)
    return 0


def register(commands) -> None:
    profile = commands.add_parser(
        "profile", help="class and drug-through-a-lens profiles")
    sub = profile.add_subparsers(dest="profile_command", required=True)
    c = sub.add_parser("class", help="profile of one class")
    c.add_argument("class_uuid", type=uuid.UUID)
    c.set_defaults(handler=_handle_class)
    d = sub.add_parser("drug", help="a drug seen through its class(es)")
    d.add_argument("moiety_uuid", type=uuid.UUID)
    d.add_argument("--lens", action="append", type=uuid.UUID, metavar="CLASS_UUID")
    d.set_defaults(handler=_handle_drug)
    r = sub.add_parser("rule", help="record a property ruling")
    who = r.add_mutually_exclusive_group(required=True)
    who.add_argument("--class", dest="klass", type=uuid.UUID)
    who.add_argument("--moiety", type=uuid.UUID)
    r.add_argument("--relationship", required=True)
    r.add_argument("--object-kind", required=True,
                   choices=("condition", "moiety", "class"))
    r.add_argument("--object", required=True, type=uuid.UUID)
    r.add_argument("--verdict", required=True)
    r.add_argument("--reviewed-by", required=True)
    r.add_argument("--reviewed-against", required=True)
    r.add_argument("--question", type=uuid.UUID)
    r.set_defaults(handler=_handle_rule)

# src/drugref/cli_departures.py
"""The "CHANGED SINCE REVIEW" banner of `drugref interactions` (#256, #281, #279).

Moved out of `cli_interactions.py` (which sat near the ~500-line cap) when the
banner stopped reading db/080's class-only reach snapshot and started reading
`graded_pairs.departed`: db/083's observations, both grains. NO SQL LIVES HERE;
the read is `graded_pairs.departed`, this module only probes for its table and
words the result.
"""
import dataclasses
import sys
import uuid

import psycopg

from drugref import db, graded_pairs, migration_guard

_MIGRATION = "083"


def ack_hints(conn, d: graded_pairs.Departure) -> list[tuple[uuid.UUID, int, int]]:
    """(drug that left, pairs acknowledging it closes, of those how many are not
    among `d.pairs`) per leaver. Acknowledging closes by DRUG, not by the pairs on
    screen, so the curator is told when it reaches further than the banner shows."""
    grain = "class" if d.rule_grain == "class_rule" else "moiety"
    out = []
    for drug in sorted(graded_pairs.leavers(conn, d), key=str):
        closes = graded_pairs.pairs_to_acknowledge(conn, grain, d.ruling_id, drug)
        out.append((drug, len(closes), len(closes - d.pairs)))
    return out


def format_departure(d: graded_pairs.Departure,
                     hints: list[tuple[uuid.UUID, int, int]]) -> list[str]:
    """The banner lines for one departed ruling (pure; the audit listing reuses it).
    `hints` is `ack_hints` output: one acknowledge line per
    (drug, closes, not_shown)."""
    grain = "class" if d.rule_grain == "class_rule" else "moiety"
    subject = (f"class {d.subject_key}" if grain == "class"
               else f"drug {d.subject_key}")
    when = (" (recorded at db/083's migration, not when graded)"
            if d.backfilled else "")
    pairs = ", ".join(f"{s} -> {p}" for s, p in sorted(d.pairs, key=str))
    audit = (f". See `drugref audit class-ruling {d.ruling_id}`"
             if grain == "class" else "")
    lines = [f"   {grain} ruling {d.ruling_id}: {subject} -> class {d.object_class} "
             f"[{d.relationship}] {d.severity}, reviewed by {d.reviewed_by} "
             f"against {d.reviewed_against}{when}",
             f"      no longer reached: {pairs}{audit}"]
    for drug, closes, not_shown in hints:
        scope = f"closes {closes} pair(s)" + (
            f", {not_shown} not shown above" if not_shown else "")
        lines.append(f"      acknowledge ({scope}): drugref curate "
                     f"acknowledge-departure --grain {grain} {d.ruling_id} {drug} "
                     "--rationale '...'")
    return lines


# AN ANSWER KNOWN TO BE INCOMPLETE gets its own exit code, for #82's reason: a banner
# alone is human-only, and a script reading 0 would take the list as complete. Not 2,
# which means "nothing was asked".
EXIT_INCOMPLETE = 3
_NEEDED = (graded_pairs.OBSERVATION_TABLE, graded_pairs.ACK_TABLE)


def _cannot_tell(conn) -> bool:
    """True (after a stderr note in migration_guard's own words) when db/083's
    tables are absent. Probed with `to_regclass`, not by catching a failed read, so
    the transaction is never aborted."""
    absent = tuple(rel for rel in _NEEDED if conn.execute(
        "SELECT to_regclass(%s)", (rel,)).fetchone()[0] is None)
    if absent:
        consequence = ("drugref cannot tell whether a reviewed grade stopped "
                       "reaching a pair since it was reviewed")
        try:
            applied = db.migration_applied(conn, _MIGRATION)
        except psycopg.Error:   # no ledger (hand-bootstrapped): say only what is known
            print(f"note: {', '.join(absent)} missing (db/083), so {consequence}. "
                  "Run `drugref status`.", file=sys.stderr)
            return True
        print("note: " + migration_guard.guard_message(
            migration_guard.Diagnosis(
                absent=absent, migration_applied=applied,
                detail="to_regclass found no such relation"),
            migration=_MIGRATION, consequence=consequence), file=sys.stderr)
        return True
    return False


def report_departures(conn, directions) -> bool | None:
    """Banner any live ruling (either grain) that graded an asked pair and no
    longer reaches it, its candidate intact. Returns whether it printed, or None
    for "cannot tell" (the pair form then withholds "ABSENT RULE").

    `directions` is (subject, partner-or-None) per lookup the caller printed, so
    the banner speaks about exactly the lists above it. ONE ENTRY PER RULING: a
    class against itself can depart both directions of one pair, and that is one
    ruling (`departed` already groups by ruling; entries are merged across
    directions here).

    WITHOUT db/083 THERE IS NOTHING TO COMPARE: a note on stderr, the exit code
    is left alone -- "cannot tell" is not "known incomplete". The note is
    migration_guard's own sentence, so a table DROPPED after db/083 ran is named
    as dropped. Probed with `to_regclass` first, not by catching the failed read,
    so the transaction is never aborted.
    """
    if _cannot_tell(conn):
        return None
    by_ruling: dict[tuple[str, int], graded_pairs.Departure] = {}
    with migration_guard.guarded(
            conn, relations=_NEEDED, migration=_MIGRATION,
            consequence="drugref cannot tell whether a reviewed grade stopped "
                        "reaching a pair"):
        # One call per direction (the pair form asks two): same read, same relations.
        for subject, partner in directions:
            for d in graded_pairs.departed(conn, subject, partner):
                key = (d.rule_grain, d.ruling_id)
                seen = by_ruling.get(key)
                by_ruling[key] = d if seen is None else dataclasses.replace(
                    seen, pairs=seen.pairs | d.pairs)
    if not by_ruling:
        return False
    print(f"** CHANGED SINCE REVIEW: {len(by_ruling)} curated ruling(s) graded the "
          "pair(s) below and NO LONGER REACH THEM (a drug left a class, or an "
          "expansion-policy deny now cuts the class it was reached through), so "
          "their grade no longer reaches the list above **")
    for d in by_ruling.values():
        with migration_guard.guarded(
                conn, relations=_NEEDED, migration=_MIGRATION,
                consequence="drugref cannot say who left"):
            hints = ack_hints(conn, d)
        print("\n".join(format_departure(d, hints)))
    return True


def handle_audit_departures(conn, args) -> int:
    """`drugref audit departures`: every open departure, all rulings, both grains.
    Exit 0 none, 3 some (incomplete), 2 when db/083 is absent ("cannot tell")."""
    if _cannot_tell(conn):
        return 2
    with migration_guard.guarded(
            conn, relations=_NEEDED, migration=_MIGRATION,
            consequence="drugref cannot tell whether a reviewed grade stopped "
                        "reaching a pair"):
        found = graded_pairs.departed(conn)
        entries = [(d, ack_hints(conn, d)) for d in found]
    print(f"open departures: {len(found)} ruling(s), "
          f"{len(set().union(*(d.pairs for d in found)))} pair(s)")
    for d, hints in entries:
        print("\n".join(format_departure(d, hints)))
        print(f"      first seen: {d.first_seen_at:%Y-%m-%d %H:%M}")
    return EXIT_INCOMPLETE if found else 0

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


def _drugs_to_name(d: graded_pairs.Departure) -> list[uuid.UUID]:
    """The drug(s) a curator passes to `acknowledge-departure` for this entry.

    Acknowledging either side of a pair closes it, so this is only about telling
    the curator who left. Moiety grain: the subject is the rule's own drug and
    cannot leave, so name the partners. Class grain: if every pair shares one
    partner, that partner left; else if every pair shares one subject, that
    subject left; otherwise (including a single pair, where we cannot tell which
    side left) name the partner(s), as the brief says.
    """
    subjects = {s for s, _ in d.pairs}
    partners = {p for _, p in d.pairs}
    if d.rule_grain == "class_rule" and len(partners) > 1 and len(subjects) == 1:
        return sorted(subjects, key=str)
    return sorted(partners, key=str)


def format_departure(d: graded_pairs.Departure) -> list[str]:
    """The banner lines for one departed ruling (pure; the audit listing reuses it)."""
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
    lines += [f"      acknowledge: drugref curate acknowledge-departure "
              f"--grain {grain} {d.ruling_id} {drug} --rationale '...'"
              for drug in _drugs_to_name(d)]
    return lines


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
    needed = (graded_pairs.OBSERVATION_TABLE,)
    absent = tuple(rel for rel in needed if conn.execute(
        "SELECT to_regclass(%s)", (rel,)).fetchone()[0] is None)
    if absent:
        consequence = ("drugref cannot tell whether a reviewed grade stopped "
                       "reaching a pair since it was reviewed")
        try:
            applied = db.migration_applied(conn, _MIGRATION)
        except psycopg.Error:   # no ledger (hand-bootstrapped): say only what is known
            print(f"note: {', '.join(absent)} missing (db/083), so {consequence}. "
                  "Run `drugref status`.", file=sys.stderr)
            return None
        print("note: " + migration_guard.guard_message(
            migration_guard.Diagnosis(
                absent=absent, migration_applied=applied,
                detail="to_regclass found no such relation"),
            migration=_MIGRATION, consequence=consequence), file=sys.stderr)
        return None
    by_ruling: dict[tuple[str, int], graded_pairs.Departure] = {}
    with migration_guard.guarded(
            conn, relations=needed, migration=_MIGRATION,
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
        print("\n".join(format_departure(d)))
    return True

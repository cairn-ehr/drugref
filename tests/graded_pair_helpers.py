"""Test helpers for db/083's ever-graded record (#281, #279).

The refresh runs from DEFERRED constraint triggers, i.e. at COMMIT. Every DB test
runs in a transaction the `conn` fixture rolls back, so those triggers would never
fire. `commit_point` stands in for the commit: switching the refresh triggers to
IMMEDIATE fires every refresh they have queued so far, exactly as COMMIT would, and
switching them back to DEFERRED makes later writes queue again.
"""
from drugref import graded_pairs


def commit_point(conn) -> None:
    """Fire the queued refreshes now, as a COMMIT would, without committing."""
    names = ", ".join(graded_pairs.REFRESH_TRIGGERS)
    conn.execute(f"SET CONSTRAINTS {names} IMMEDIATE")
    conn.execute(f"SET CONSTRAINTS {names} DEFERRED")


def observations(conn) -> set[tuple]:
    """Every observation as (rule_grain, subject_key, object_class_uuid,
    relationship, subject_moiety, partner_moiety, captured). A set: count rows
    yourself when a test cares about duplicates."""
    return set(conn.execute(
        "SELECT rule_grain, subject_key, object_class_uuid, relationship, "
        f"subject_moiety, partner_moiety, captured FROM {graded_pairs.OBSERVATION_TABLE}"))

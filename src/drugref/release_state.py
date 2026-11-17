# src/drugref/release_state.py
"""Which published release the live database answers from, and has it drifted (#258).

Reads `drugref.current_release` (db/081). That view is cheap (timestamp probes, no
manifest verification), so every `interactions`/`status` run can print `release_line`.
"""
from dataclasses import dataclass
from datetime import datetime

import psycopg

RELEASE_VIEW = "drugref.current_release"
UNKNOWN_LINE = "drugref release: unknown (database predates db/081)"


@dataclass(frozen=True)
class CurrentRelease:
    """`git_commit` is None when the release has no release_build row (published
    before db/081); the view then carries the drift reason that says so."""
    release_tag: str
    published_at: datetime
    git_commit: str | None
    drift_reasons: tuple[str, ...]

    @property
    def drifted(self) -> bool:
        return bool(self.drift_reasons)


def current_release(conn: psycopg.Connection) -> CurrentRelease | None:
    """The latest release, or None when nothing has been published."""
    row = conn.execute(
        "SELECT release_tag, published_at, git_commit, drift_reasons "
        f"FROM {RELEASE_VIEW}").fetchone()
    if row is None:
        return None
    return CurrentRelease(row[0], row[1], row[2], tuple(row[3]))


class _ViewAbsent:
    """Type of `VIEW_ABSENT`; a class so the sentinel reads well in a repr."""

    def __repr__(self) -> str:
        return "VIEW_ABSENT"


#: The database predates db/081: there is no view to ask, so no answer at all.
VIEW_ABSENT = _ViewAbsent()


def current_release_or_absent(
        conn: psycopg.Connection) -> CurrentRelease | None | _ViewAbsent:
    """`current_release`, or `VIEW_ABSENT` when db/081 is not applied.

    ONE home for the `to_regclass` probe: the header and `audit archive` both need
    to tell "nothing published" from "this database cannot say" (#258).
    """
    if conn.execute("SELECT to_regclass(%s)", (RELEASE_VIEW,)).fetchone()[0] is None:
        return VIEW_ABSENT
    return current_release(conn)


def release_line(cr: CurrentRelease | None) -> str:
    """The one-line header; pure. Copy follows #258's design §6.1, with the drift
    reasons inserted.

    A release with no build row is DRIFTED (its first reason says why), not
    "unknown": the view exists, so the database is not the one predating db/081.
    """
    if cr is None:
        return ("drugref release: none published "
                "(answers are not tied to a reconstructable release)")
    if cr.drifted:
        reasons = ", ".join(cr.drift_reasons)
        return (f"drugref release: {cr.release_tag} (DRIFTED since publication: "
                f"{reasons}) — answers may differ from that release")
    return f"drugref release: {cr.release_tag}"


def release_header(conn: psycopg.Connection) -> str:
    """`release_line` for a live connection; never raises on a pre-db/081 database.

    The header is printed by commands that must still answer on an under-migrated
    database, so a missing view yields the "unknown" copy rather than an abort (#258).
    """
    cr = current_release_or_absent(conn)
    return UNKNOWN_LINE if cr is VIEW_ABSENT else release_line(cr)

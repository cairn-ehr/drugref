# src/drugref/release_state.py
"""Which published release the live database answers from, and has it drifted (#258).

Reads `drugref.current_release` (db/081). That view is cheap (timestamp probes, no
manifest verification), so every `interactions`/`status` run can print `release_line`.
"""
from dataclasses import dataclass
from datetime import datetime

import psycopg

RELEASE_VIEW = "drugref.current_release"


@dataclass(frozen=True)
class CurrentRelease:
    """`git_commit` is None when the release has no release_build row (pre-db/081)."""
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


def release_line(cr: CurrentRelease | None) -> str:
    """The one-line header; pure. Exact copy is fixed by #258's design."""
    if cr is None:
        return ("drugref release: none published "
                "(answers are not tied to a reconstructable release)")
    if cr.git_commit is None:
        return "drugref release: unknown (database predates db/081)"
    if cr.drifted:
        reasons = ", ".join(cr.drift_reasons)
        return (f"drugref release: {cr.release_tag} (DRIFTED since publication: "
                f"{reasons}) — answers may differ from that release")
    return f"drugref release: {cr.release_tag}"

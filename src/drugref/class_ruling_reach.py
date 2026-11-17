# src/drugref/class_ruling_reach.py
"""Record which drugs a class ruling reached at the moment it was made (#258).

WHY. Class membership is delete-and-rebuild per ingest, so `class_rule_member`
(db/079) only ever answers "who does this ruling reach NOW". A decision audit needs
"who did it reach WHEN THE CURATOR RULED", and that is lost the next time membership
is rebuilt. db/080's table keeps it; this module writes it. Pure SQL, no vocabulary
in Python: the view already says which drugs a live ruling reaches.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg

REACH_TABLE = "drugref.curated_class_interaction_reach"
# One row per snapshotted ruling (db/082): tells "reached no one" from "never recorded".
SNAPSHOT_TABLE = "drugref.curated_class_interaction_snapshot"


def snapshot_reach(conn: psycopg.Connection, curated_class_interaction_id: int) -> int:
    """Copy the live ruling's current reach into REACH_TABLE; return rows written.

    Also marks the ruling in SNAPSHOT_TABLE, so a side that reached no one (zero
    rows) still reads as "snapshot taken" (#266). Call it after the ruling is live
    (append-then-point done), or the view does not yet list it. Does not commit:
    the caller owns the transaction.
    """
    conn.execute(
        f"INSERT INTO {SNAPSHOT_TABLE} (curated_class_interaction_id, captured) "
        "VALUES (%s, 'at_ruling') ON CONFLICT DO NOTHING",
        (curated_class_interaction_id,))
    return conn.execute(
        f"INSERT INTO {REACH_TABLE} "
        "(curated_class_interaction_id, side, moiety_uuid, captured) "
        "SELECT curated_class_interaction_id, side, moiety_uuid, 'at_ruling' "
        "FROM drugref.class_rule_member WHERE curated_class_interaction_id = %s "
        "ON CONFLICT DO NOTHING", (curated_class_interaction_id,)).rowcount


@dataclass(frozen=True)
class SideReach:
    """One side of a ruling: who it reached when made (`then`) vs who it reaches now.

    `captured` is 'at_ruling' (a true ruling-time snapshot), 'backfill_db080' (the
    snapshot was reconstructed by the migration, so `then` is NOT ruling-time; wins
    if any row is backfill), or 'none' (no snapshot on record: never taken, or the
    class was empty before db/082 could mark it -- see db/082). A side with no
    rows takes the ruling's SNAPSHOT_TABLE marker, so `captured` != 'none' with an
    empty `then` means "snapshot taken, reached no one" (#266).
    """
    side: str
    then: frozenset[uuid.UUID]
    now: frozenset[uuid.UUID]
    captured: str

    @property
    def added(self) -> frozenset[uuid.UUID]:
        return self.now - self.then

    @property
    def removed(self) -> frozenset[uuid.UUID]:
        return self.then - self.now


@dataclass(frozen=True)
class RulingAudit:
    """Everything `drugref audit class-ruling` shows (#258, #256).

    `loaded_runs` is (source, writer, ingest_run_id, upstream_release): the latest
    finished run per (source, writer) at or before `reviewed_at`, i.e. the data the
    curator could have been looking at.
    """
    curated_class_interaction_id: int
    reviewed_by: str
    reviewed_against: str
    reviewed_at: datetime
    sides: tuple[SideReach, SideReach]
    loaded_runs: tuple[tuple[str, str, int, str], ...]


def audit_ruling(conn: psycopg.Connection,
                 curated_class_interaction_id: int) -> RulingAudit | None:
    """Reach then vs now for one ruling, or None if the id does not exist.

    `now` comes from class_rule_member, which covers LIVE rulings only, so a
    superseded ruling has an empty `now` (everything then shows as removed).
    """
    row = conn.execute(
        "SELECT reviewed_by, reviewed_against, reviewed_at "
        "FROM drugref.curated_class_interaction "
        "WHERE curated_class_interaction_id = %s", (curated_class_interaction_id,)
    ).fetchone()
    if row is None:
        return None
    then: dict[str, set] = {"subject": set(), "partner": set()}
    captured: dict[str, set] = {"subject": set(), "partner": set()}
    for side, m, cap in conn.execute(
            f"SELECT side, moiety_uuid, captured FROM {REACH_TABLE} "
            "WHERE curated_class_interaction_id = %s", (curated_class_interaction_id,)):
        then[side].add(m)
        captured[side].add(cap)
    now: dict[str, set] = {"subject": set(), "partner": set()}
    for side, m in conn.execute(
            "SELECT side, moiety_uuid FROM drugref.class_rule_member "
            "WHERE curated_class_interaction_id = %s", (curated_class_interaction_id,)):
        now[side].add(m)
    marker = conn.execute(
        f"SELECT captured FROM {SNAPSHOT_TABLE} "
        "WHERE curated_class_interaction_id = %s",
        (curated_class_interaction_id,)).fetchone()

    def cap(side: str) -> str:
        c = captured[side]
        if not c:       # no rows: either reached no one, or never snapshotted
            return marker[0] if marker else "none"
        return "backfill_db080" if "backfill_db080" in c else "at_ruling"

    sides = tuple(SideReach(s, frozenset(then[s]), frozenset(now[s]), cap(s))
                  for s in ("subject", "partner"))
    runs = conn.execute(
        "SELECT DISTINCT ON (source, writer) source, writer, ingest_run_id, "
        "upstream_release FROM drugref.ingest_run "
        "WHERE finished_at IS NOT NULL AND finished_at <= %s "
        "ORDER BY source, writer, finished_at DESC, ingest_run_id DESC", (row[2],)
    ).fetchall()
    return RulingAudit(curated_class_interaction_id, row[0], row[1], row[2], sides,
                       tuple(tuple(r) for r in runs))

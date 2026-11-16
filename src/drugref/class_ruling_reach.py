# src/drugref/class_ruling_reach.py
"""Record which drugs a class ruling reached at the moment it was made (#258).

WHY. Class membership is delete-and-rebuild per ingest, so `class_rule_member`
(db/079) only ever answers "who does this ruling reach NOW". A decision audit needs
"who did it reach WHEN THE CURATOR RULED", and that is lost the next time membership
is rebuilt. db/080's table keeps it; this module writes it. Pure SQL, no vocabulary
in Python: the view already says which drugs a live ruling reaches.
"""
import psycopg

REACH_TABLE = "drugref.curated_class_interaction_reach"


def snapshot_reach(conn: psycopg.Connection, curated_class_interaction_id: int) -> int:
    """Copy the live ruling's current reach into REACH_TABLE; return rows written.

    Call it after the ruling is live (append-then-point done), or the view does not
    yet list it. Does not commit: the caller owns the transaction.
    """
    return conn.execute(
        f"INSERT INTO {REACH_TABLE} "
        "(curated_class_interaction_id, side, moiety_uuid, captured) "
        "SELECT curated_class_interaction_id, side, moiety_uuid, 'at_ruling' "
        "FROM drugref.class_rule_member WHERE curated_class_interaction_id = %s "
        "ON CONFLICT DO NOTHING", (curated_class_interaction_id,)).rowcount

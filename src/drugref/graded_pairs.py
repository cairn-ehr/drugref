# src/drugref/graded_pairs.py
"""Every pair a reviewed grade ever reached (db/083, #281, #279).

WHY. `curated_ddi_pair` answers "which pairs do rulings grade NOW". When a drug
leaves a class, or a correction re-grades a key, the pairs that dropped out leave
no trace there, so nobody can be told a reviewed warning stopped reaching a drug.
db/083 writes one observation per (ruling key, pair) episode, refreshed at every
COMMIT that could change the view; later code compares it with the live view to
find departures. The SQL owns the rule (`drugref.refresh_graded_pairs()`); this
module only names its objects and lets a caller refresh before committing.
"""
import psycopg

OBSERVATION_TABLE = "drugref.graded_pair_observation"
ACK_TABLE = "drugref.graded_pair_departure_ack"

# The deferred constraint triggers that run the refresh at COMMIT, schema-qualified
# so `SET CONSTRAINTS` can name them (tests fire them early that way).
REFRESH_TRIGGERS: tuple[str, ...] = (
    "drugref.curated_interaction_graded_pairs",
    "drugref.curated_class_interaction_graded_pairs",
    "drugref.class_expansion_policy_graded_pairs",
    "drugref.ingest_run_graded_pairs",
)


def refresh(conn: psycopg.Connection) -> int:
    """Record every pair graded now that has no open episode; return rows added.

    The triggers do this at COMMIT anyway; call it to see the observations before
    committing. Does not commit.
    """
    return conn.execute("SELECT drugref.refresh_graded_pairs()").fetchone()[0]

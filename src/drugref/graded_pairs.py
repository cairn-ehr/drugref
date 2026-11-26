# src/drugref/graded_pairs.py
"""Every pair a reviewed grade ever reached (db/083, #281, #279).

WHY. `curated_ddi_pair` answers "which pairs do rulings grade NOW". When a drug
leaves a class, or a correction re-grades a key, the pairs that dropped out leave
no trace there, so nobody can be told a reviewed warning stopped reaching a drug.
db/083 writes one observation per (ruling key, pair) episode, refreshed at every
COMMIT that could change the view. `departed` compares it with the live view to
find the pairs a still-standing ruling stopped grading. The SQL owns the refresh
(`drugref.refresh_graded_pairs()` at an ingest or policy commit, db/084's
`refresh_graded_pairs_for_key` at a ruling commit); this module names its objects,
lets a caller refresh before committing, and reads the departures.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime

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


@dataclass(frozen=True)
class Departure:
    """One live ruling that no longer grades pairs it was observed grading (spec 5.3).

    `ruling_id` is the LIVE version's id (`curated_interaction_id` for a
    'moiety_rule', `curated_class_interaction_id` for a 'class_rule'), even when the
    pairs were observed under an earlier version of the same key: a correction
    inherits its key's history (#281 section 1). `subject_key` is the subject moiety
    (moiety grain) or subject class (class grain). `severity`, `reviewed_by` and
    `reviewed_against` are the live version's. `backfilled` is True when any open
    episode was reconstructed by db/083's migration (not seen when graded);
    `first_seen_at` is the earliest open episode's. Each pair is (subject moiety,
    partner moiety).
    """
    rule_grain: str
    ruling_id: int
    subject_key: uuid.UUID
    object_class: uuid.UUID
    relationship: str
    severity: str
    reviewed_by: str
    reviewed_against: str
    backfilled: bool
    first_seen_at: datetime
    pairs: frozenset[tuple[uuid.UUID, uuid.UUID]]


# The rule lives in db/087's `drugref.departed_graded_pairs` (one home: the reviewer
# GUI's Rust service reads it too, #283). This only orders and unpacks it.
_DEPARTED_SQL = """
SELECT rule_grain, ruling_id, subject_key, object_class_uuid, relationship, severity,
       reviewed_by, reviewed_against, backfilled, first_seen_at, subject_moieties,
       partner_moieties
FROM   drugref.departed_graded_pairs(%(subject)s::uuid, %(partner)s::uuid)
ORDER  BY reviewed_at DESC, rule_grain, ruling_id DESC
"""


def departed(conn: psycopg.Connection, subject: uuid.UUID | None = None,
             partner: uuid.UUID | None = None) -> list[Departure]:
    """Live, applying, non-orphaned rulings (both grains) with open episodes the
    view no longer returns under the same ruling key; most recent ruling first.

    PER RULING, NOT PER PAIR: a class rule that stops reaching (X, P) departs even
    if a moiety rule still grades (X, P), since the clinician may now see a weaker
    grade. DIRECTIONAL: `subject` filters the pair's subject moiety, `partner` its
    partner moiety; both None lists every departure (`drugref audit departures`).
    An `applies = false` live version reports nothing, but its episodes stay open,
    so reinstating it while the drug is still gone reports them again.
    """
    rows = conn.execute(_DEPARTED_SQL, {"subject": subject, "partner": partner})
    return [Departure(*r[:10], frozenset(zip(r[10], r[11]))) for r in rows]


class UnknownRuling(LookupError):
    """No ruling (of any version) has that id in that grain."""


class LedgerBusy(RuntimeError):
    """`acknowledge` waited ACK_LOCK_TIMEOUT (None = db/087's 30 s) for the ledger
    lock (#289)."""


class NothingDeparted(ValueError):
    """The drug is in no open departed episode of that ruling's key."""


# Ruling-key lookup: any version's id names its key (a curator may paste a
# superseded id from an old banner). Keyed by the CLI's grain spelling.
_KEY_SQL = {
    "moiety": ("moiety_rule",
               "SELECT subject_moiety_uuid, object_class_uuid, relationship "
               "FROM drugref.curated_interaction WHERE curated_interaction_id = %s"),
    "class": ("class_rule",
              "SELECT subject_class_uuid, object_class_uuid, relationship "
              "FROM drugref.curated_class_interaction "
              "WHERE curated_class_interaction_id = %s"),
}


def _ruling_key(conn: psycopg.Connection, grain: str, ruling_id: int):
    """(rule_grain, (subject_key, object_class, relationship)) of the ruling's key,
    from any version's id. Raises `UnknownRuling`."""
    rule_grain, sql = _KEY_SQL[grain]
    key = conn.execute(sql, (ruling_id,)).fetchone()
    if key is None:
        raise UnknownRuling(f"no {grain} ruling with id {ruling_id}")
    return rule_grain, tuple(key)


def pairs_to_acknowledge(conn: psycopg.Connection, grain: str, ruling_id: int,
                         drug: uuid.UUID) -> frozenset[tuple[uuid.UUID, uuid.UUID]]:
    """A read-only preview of the UNFILTERED set `acknowledge` would close: every
    open, currently-departed pair of the key of `ruling_id` (any version) in which
    `drug` is the subject or partner moiety. Empty when nothing departed. Raises
    `UnknownRuling`.

    It mirrors db/087's `acknowledge_departure` selection over the same
    `departed_graded_pairs` read (once per direction, filtered to the key); the
    acknowledgement itself does not call it. A banner shows only the pairs its
    question asked about, so this can be larger than what the curator saw.
    """
    rule_grain, key = _ruling_key(conn, grain, ruling_id)
    return frozenset(
        pair
        for d in departed(conn, subject=drug) + departed(conn, partner=drug)
        if (d.rule_grain, d.subject_key, d.object_class, d.relationship)
        == (rule_grain, *key)
        for pair in d.pairs)


def leavers(conn: psycopg.Connection, d: Departure) -> frozenset[uuid.UUID]:
    """The drugs that actually LEFT in `d`'s displayed pairs (db/087's
    `drugref.departure_leavers`, which the reviewer GUI reads too).

    Class grain: a pair's subject left if it is no longer a subject-side member of
    the live ruling in `class_rule_member`; likewise the partner on its side (both
    can be missing, then both left). Moiety grain: the subject is the ruling's own
    drug, so the partner left. If a pair has no missing side (cause unknown), its
    partner is named, so the result is never empty for a non-empty `d.pairs`.
    """
    subjects, partners = zip(*d.pairs) if d.pairs else ((), ())
    return frozenset(r[0] for r in conn.execute(
        "SELECT * FROM drugref.departure_leavers(%s, %s, %s::uuid[], %s::uuid[])",
        (d.rule_grain, d.ruling_id, list(subjects), list(partners))))


# How long the interactive `acknowledge` waits for the ledger lock (db/086) before
# raising LedgerBusy (#289). None = db/087's default, 30 s: an ingest's commit-time
# refresh holds the lock ~5 s (#285), so a longer wait means a transaction was left
# open. Only `acknowledge` is bounded: a bound on the commit-time triggers would
# fail an hours-long ingest at COMMIT. Tests shorten it.
ACK_LOCK_TIMEOUT: str | None = None

# db/087's SQLSTATEs for `acknowledge_departure`'s refusals.
_ACK_REFUSALS = {"LG001": UnknownRuling, "LG002": NothingDeparted,
                 "LG003": LedgerBusy}


def acknowledge(conn: psycopg.Connection, grain: str, ruling_id: int,
                drug: uuid.UUID, *, rationale: str, reviewed_by: str,
                reviewed_against: str) -> frozenset[tuple[uuid.UUID, uuid.UUID]]:
    """Ack every open, currently-departed episode of the key of `ruling_id` (any
    version) in which `drug` is the subject or partner moiety; return the distinct
    pairs closed (the set `pairs_to_acknowledge` previews), closing EVERY open
    episode of them, duplicates included. The rule is db/087's
    `drugref.acknowledge_departure` (the reviewer GUI calls it too); it takes
    db/086's ledger lock before reading (#287).

    Raises `UnknownRuling` / `NothingDeparted` (also when another curator's ack
    committed first: the lock makes this call wait for it, then see nothing
    departed), or `LedgerBusy` naming the holder after ACK_LOCK_TIMEOUT, or
    RuntimeError on an autocommit connection; a blank rationale trips the table's
    CHECK. Does not commit: on success the caller holds the ledger lock until it
    commits or rolls back, so end the transaction promptly. A refusal rolls back to
    a savepoint, leaving the caller's transaction usable.
    """
    if conn.autocommit:
        # "Does not commit" is the contract the CLI relies on: in autocommit the
        # savepoint below would be a BEGIN/COMMIT of its own.
        raise RuntimeError("acknowledge needs a transaction, not autocommit (db/086)")
    # Opens the caller's transaction if none is open, so `conn.transaction()` below
    # is a SAVEPOINT; on an idle connection it would be a BEGIN/COMMIT, releasing the
    # lock before the caller commits (#287's race, silently).
    conn.execute("SELECT 1")
    try:
        with conn.transaction():
            rows = conn.execute(
                "SELECT * FROM drugref.acknowledge_departure("
                "%s, %s, %s, %s, %s, %s, %s)",
                (_KEY_SQL[grain][0], ruling_id, drug, rationale, reviewed_by,
                 reviewed_against, ACK_LOCK_TIMEOUT)).fetchall()
    except psycopg.Error as error:
        refusal = _ACK_REFUSALS.get(error.sqlstate or "")
        if refusal is None:
            raise
        raise refusal(error.diag.message_primary) from error
    return frozenset((s, p) for s, p, _against in rows)

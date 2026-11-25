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


# `now` is `curated_ddi_pair` reduced to (ruling key, pair), read ONCE per call
# (MATERIALIZED) rather than once per open episode. It takes the same subject /
# partner filters as the outer query, so the one-drug banner reduces only that
# drug's rows instead of the whole view. `live` is the live version of
# every ruling key, both grains in `curated_ddi_pair`'s vocabulary, with the
# `curated_target_unresolved` arm name the orphan check matches on.
_DEPARTED_SQL = f"""
WITH now AS MATERIALIZED (
    SELECT DISTINCT p.rule_grain,
           CASE p.rule_grain WHEN 'class_rule' THEN p.via_subject_class
                             ELSE p.subject_moiety END AS subject_key,
           p.via_class, p.relationship, p.subject_moiety, p.partner_moiety
    FROM   drugref.curated_ddi_pair p
    WHERE  (%(subject)s::uuid IS NULL OR p.subject_moiety = %(subject)s)
    AND    (%(partner)s::uuid IS NULL OR p.partner_moiety = %(partner)s)),
live AS (
    SELECT 'moiety_rule' AS rule_grain, 'curated_interaction' AS target_table,
           curated_interaction_id AS ruling_id, subject_moiety_uuid AS subject_key,
           object_class_uuid, relationship, applies, severity, reviewed_by,
           reviewed_against, reviewed_at
    FROM   drugref.curated_interaction WHERE superseded_by IS NULL
    UNION ALL
    SELECT 'class_rule', 'curated_class_interaction',
           curated_class_interaction_id, subject_class_uuid,
           object_class_uuid, relationship, applies, severity, reviewed_by,
           reviewed_against, reviewed_at
    FROM   drugref.curated_class_interaction WHERE superseded_by IS NULL)
SELECT l.rule_grain, l.ruling_id, l.subject_key, l.object_class_uuid,
       l.relationship, l.severity, l.reviewed_by, l.reviewed_against,
       bool_or(o.captured = 'backfill_db083'), min(o.first_seen_at),
       array_agg(o.subject_moiety ORDER BY o.observation_id),
       array_agg(o.partner_moiety ORDER BY o.observation_id)
FROM   {OBSERVATION_TABLE} o
JOIN   live l
       ON  l.rule_grain = o.rule_grain AND l.subject_key = o.subject_key
       AND l.object_class_uuid = o.object_class_uuid
       AND l.relationship = o.relationship
WHERE  (%(subject)s::uuid IS NULL OR o.subject_moiety = %(subject)s)
AND    (%(partner)s::uuid IS NULL OR o.partner_moiety = %(partner)s)
-- open: no acknowledgement closes this episode
AND    NOT EXISTS (SELECT 1 FROM {ACK_TABLE} a
                   WHERE a.observation_id = o.observation_id)
-- departed: the view no longer grades this pair under this key
AND    NOT EXISTS (SELECT 1 FROM now n
                   WHERE n.rule_grain = o.rule_grain
                   AND   n.subject_key = o.subject_key
                   AND   n.via_class = o.object_class_uuid
                   AND   n.relationship = o.relationship
                   AND   n.subject_moiety = o.subject_moiety
                   AND   n.partner_moiety = o.partner_moiety)
-- withdrawn: an applies = false ruling grades nothing, so nothing fell out of it
AND    l.applies
-- orphaned: the candidate is gone, which #121's banner already reports
AND    NOT EXISTS (SELECT 1 FROM drugref.curated_target_unresolved u
                   WHERE u.target_table = l.target_table
                   AND   u.object_uuid = l.object_class_uuid
                   AND   u.relationship = l.relationship
                   AND   CASE l.target_table WHEN 'curated_interaction'
                              THEN u.subject_moiety ELSE u.subject_class END
                         = l.subject_key)
GROUP  BY l.rule_grain, l.ruling_id, l.subject_key, l.object_class_uuid,
          l.relationship, l.severity, l.reviewed_by, l.reviewed_against,
          l.reviewed_at
ORDER  BY l.reviewed_at DESC, l.rule_grain, l.ruling_id DESC
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
    """The UNFILTERED set `acknowledge` would close: every open, currently-departed
    pair of the key of `ruling_id` (any version) in which `drug` is the subject or
    partner moiety. Empty when nothing departed. Raises `UnknownRuling`.

    "Currently departed" is exactly what `departed` reports, so this reuses it
    (once per direction) instead of repeating its SQL. A banner shows only the pairs
    its question asked about, so this can be larger than what the curator saw.
    """
    rule_grain, key = _ruling_key(conn, grain, ruling_id)
    return frozenset(
        pair
        for d in departed(conn, subject=drug) + departed(conn, partner=drug)
        if (d.rule_grain, d.subject_key, d.object_class, d.relationship)
        == (rule_grain, *key)
        for pair in d.pairs)


def leavers(conn: psycopg.Connection, d: Departure) -> frozenset[uuid.UUID]:
    """The drugs that actually LEFT in `d`'s displayed pairs.

    Class grain: a pair's subject left if it is no longer a subject-side member of
    the live ruling in `class_rule_member`; likewise the partner on its side (both
    can be missing, then both left). Moiety grain: the subject is the ruling's own
    drug, so the partner left. If a pair has no missing side (cause unknown), its
    partner is named, so the result is never empty for a non-empty `d.pairs`.
    """
    if d.rule_grain == "moiety_rule":
        return frozenset(p for _, p in d.pairs)
    now = {"subject": set(), "partner": set()}
    for side, moiety in conn.execute(
            "SELECT side, moiety_uuid FROM drugref.class_rule_member "
            "WHERE curated_class_interaction_id = %s", (d.ruling_id,)):
        now[side].add(moiety)
    out: set[uuid.UUID] = set()
    for s, p in d.pairs:
        gone = {x for x, side in ((s, "subject"), (p, "partner"))
                if x not in now[side]}
        out |= gone or {p}
    return frozenset(out)


def acknowledge(conn: psycopg.Connection, grain: str, ruling_id: int,
                drug: uuid.UUID, *, rationale: str, reviewed_by: str,
                reviewed_against: str) -> frozenset[tuple[uuid.UUID, uuid.UUID]]:
    """Ack every open, currently-departed episode of the key of `ruling_id` (any
    version) in which `drug` is the subject or partner moiety; return the pairs
    (`pairs_to_acknowledge`), closing EVERY open episode of them, duplicates
    included. Raises `UnknownRuling` / `NothingDeparted` (also when a concurrent
    ack closed them first, so this INSERT wrote nothing); a blank rationale trips
    the table's CHECK. Does not commit, so the caller holds the ledger lock (db/086)
    until it does: commit promptly.
    """
    # Before reading (#287): an ingest re-grading the pair either committed first,
    # so the read below sees it graded and raises NothingDeparted, or waits for
    # this commit and then opens a new episode after the ack.
    conn.execute("SELECT drugref.lock_graded_pair_ledger()")
    rule_grain, key = _ruling_key(conn, grain, ruling_id)
    pairs = pairs_to_acknowledge(conn, grain, ruling_id, drug)
    if not pairs:
        raise NothingDeparted(f"{drug} is in no open departed episode of ruling "
                              f"{ruling_id}")
    subjects, partners = zip(*pairs)
    written = conn.execute(
        f"""INSERT INTO {ACK_TABLE} (observation_id, rationale, reviewed_by,
                                     reviewed_against)
            SELECT o.observation_id, %s, %s, %s FROM {OBSERVATION_TABLE} o
            WHERE o.rule_grain = %s AND o.subject_key = %s
            AND   o.object_class_uuid = %s AND o.relationship = %s
            AND   (o.subject_moiety, o.partner_moiety) IN
                  (SELECT * FROM unnest(%s::uuid[], %s::uuid[]))
            AND   NOT EXISTS (SELECT 1 FROM {ACK_TABLE} a
                              WHERE a.observation_id = o.observation_id)""",
        (rationale, reviewed_by, reviewed_against, rule_grain, *key,
         list(subjects), list(partners))).rowcount
    # Another curator's ack can commit between the read above and this INSERT
    # (READ COMMITTED): then nothing was written, and "acknowledged" would be false.
    if not written:
        raise NothingDeparted(f"{drug}'s departures of ruling {ruling_id} were "
                              "already acknowledged by another curator")
    return pairs

# src/drugref/graded_pairs.py
"""Every pair a reviewed grade ever reached (db/083, #281, #279).

WHY. `curated_ddi_pair` answers "which pairs do rulings grade NOW". When a drug
leaves a class, or a correction re-grades a key, the pairs that dropped out leave
no trace there, so nobody can be told a reviewed warning stopped reaching a drug.
db/083 writes one observation per (ruling key, pair) episode, refreshed at every
COMMIT that could change the view. `departed` compares it with the live view to
find the pairs a still-standing ruling stopped grading. The SQL owns the refresh
(`drugref.refresh_graded_pairs()`); this module names its objects, lets a caller
refresh before committing, and reads the departures.
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
# (MATERIALIZED) rather than once per open episode. `live` is the live version of
# every ruling key, both grains in `curated_ddi_pair`'s vocabulary, with the
# `curated_target_unresolved` arm name the orphan check matches on.
_DEPARTED_SQL = f"""
WITH now AS MATERIALIZED (
    SELECT DISTINCT p.rule_grain,
           CASE p.rule_grain WHEN 'class_rule' THEN p.via_subject_class
                             ELSE p.subject_moiety END AS subject_key,
           p.via_class, p.relationship, p.subject_moiety, p.partner_moiety
    FROM   drugref.curated_ddi_pair p),
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

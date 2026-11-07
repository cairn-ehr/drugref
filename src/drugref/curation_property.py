"""Writer for the class-profile ruling overlay (db/058).

A ruling answers one question a class profile cannot answer from the sources alone:
does this property (relationship + object) HOLD for this class or moiety, NOT HOLD,
or is
it beyond what the sources can establish. See db/058 and the class-profiles design spec
section 5.

Same append-then-point sequence as curation.py (INSERT the new row, then point whatever
was live at it, via overlay.supersede), plus one rule curation.py does not need: writing
the SAME verdict again is a no-op, so a re-run of a curation file does not pile up
identical superseded rows that every signed release would have to carry.

NO VOCABULARY IS RESTATED IN PYTHON: relationship, verdict, object kind and the class
subject's restrictions live in db/058's CHECKs. A bad value raises from the database.
NOTHING HERE COMMITS; the caller owns the transaction.
"""
import uuid

import psycopg

from drugref import overlay

# Which FK column an object of each kind lives in. The column names are the schema's,
# not a vocabulary: an unknown kind is a KeyError, which is the right loudness.
_OBJECT_COLUMN = {"condition": "object_condition_uuid", "moiety": "object_moiety_uuid",
                  "class": "object_class_uuid"}

_KEY = ("subject_kind", "subject_uuid", "relationship", "object_kind", "object_uuid")


def record_property_ruling(
        conn: psycopg.Connection, *,
        subject_class_uuid: uuid.UUID | None = None,
        subject_moiety_uuid: uuid.UUID | None = None,
        relationship: str, object_kind: str, object_uuid: uuid.UUID,
        verdict: str, reviewed_by: str, reviewed_against: str,
        question_uuid: uuid.UUID | None = None) -> int:
    """Record (or revise) one ruling; returns the live row's id.

    Idempotent by comparison: if the live row for the key already carries `verdict`,
    its id is returned and nothing is written. Otherwise the new row is INSERTed and
    supersedes the old one.
    """
    if (subject_class_uuid is None) == (subject_moiety_uuid is None):
        raise ValueError("give exactly one of subject_class_uuid / subject_moiety_uuid")
    subject_kind, subject = (("class", subject_class_uuid) if subject_class_uuid
                             else ("moiety", subject_moiety_uuid))
    key = (subject_kind, subject, relationship, object_kind, object_uuid)

    live = conn.execute(
        "SELECT curated_property_ruling_id, verdict "
        "FROM drugref.curated_property_ruling "
        "WHERE subject_kind = %s AND subject_uuid = %s AND relationship = %s "
        "AND object_kind = %s AND object_uuid = %s AND superseded_by IS NULL",
        key).fetchone()
    if live is not None and live[1] == verdict:
        return live[0]

    new_id = conn.execute(
        f"INSERT INTO drugref.curated_property_ruling "
        f"(subject_class_uuid, subject_moiety_uuid, relationship, "
        f" {_OBJECT_COLUMN[object_kind]}, verdict, question_uuid, source, "
        f" reviewed_by, reviewed_against) "
        f"VALUES (%s, %s, %s, %s, %s, %s, 'DRUGREF', %s, %s) "
        f"RETURNING curated_property_ruling_id",
        (subject_class_uuid, subject_moiety_uuid, relationship, object_uuid, verdict,
         question_uuid, reviewed_by, reviewed_against)).fetchone()[0]
    overlay.supersede(conn, "curated_property_ruling", "curated_property_ruling_id",
                      new_id, _KEY, key)
    return new_id

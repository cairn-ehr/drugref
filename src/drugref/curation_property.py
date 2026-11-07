"""Writer for the class-profile ruling overlay (db/058).

A ruling answers one question a class profile cannot answer from the sources alone:
does this property (relationship + object) HOLD for this class or moiety, NOT HOLD,
or is beyond what the sources can establish. See db/058 and the class-profiles design
spec section 5.

Same append-then-point sequence as curation.py (INSERT the new row, then point whatever
was live at it, via overlay.supersede), plus one rule curation.py does not need: writing
the SAME verdict again is a no-op, so a re-run of a curation file does not pile up
identical superseded rows that every signed release would have to carry. "The same"
means every field the writer supplies, not only the verdict.

DRUG PAIRS ARE STORED IN ONE ORIENTATION. An interaction is symmetric, so (X, Y) and
(Y, X) are the same fact; the writer stores the lower uuid as the subject (db/063 CHECKs
it), so a correction recorded "from the other side" supersedes the earlier ruling
instead of standing beside it.

NO VOCABULARY IS RESTATED IN PYTHON: relationship, verdict and the class subject's
restrictions live in db/058's CHECKs, so a bad value raises from the database. An
unknown object kind is a KeyError from _OBJECT_COLUMN below.
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
        question_uuid: uuid.UUID | None = None) -> tuple[int, bool]:
    """Record (or revise) one ruling; returns (live row id, whether a row was written).

    Idempotent by comparison: if the live row for the key already carries the same
    verdict, question_uuid, reviewed_by and reviewed_against, (its id, False) is
    returned and nothing is written. Any difference is new provenance worth signing,
    so the new row is INSERTed, supersedes the old one, and (new id, True) is returned.
    """
    if (subject_class_uuid is None) == (subject_moiety_uuid is None):
        raise ValueError("give exactly one of subject_class_uuid / subject_moiety_uuid")
    if (subject_moiety_uuid is not None and object_kind == "moiety"
            and object_uuid < subject_moiety_uuid):
        subject_moiety_uuid, object_uuid = object_uuid, subject_moiety_uuid
    subject_kind, subject = (("class", subject_class_uuid) if subject_class_uuid
                             else ("moiety", subject_moiety_uuid))
    key = (subject_kind, subject, relationship, object_kind, object_uuid)

    live = conn.execute(
        "SELECT curated_property_ruling_id, verdict, question_uuid, reviewed_by, "
        "reviewed_against "
        "FROM drugref.curated_property_ruling "
        "WHERE subject_kind = %s AND subject_uuid = %s AND relationship = %s "
        "AND object_kind = %s AND object_uuid = %s AND superseded_by IS NULL",
        key).fetchone()
    if live is not None and live[1:] == (verdict, question_uuid, reviewed_by,
                                         reviewed_against):
        return live[0], False

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
    return new_id, True

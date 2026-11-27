"""Writer for the lab-effect ruling overlay (db/090, #294).

A ruling says whether one lab effect -- (subject class or moiety, analyte, direction,
role, optional co-medication) -- HOLDS, does NOT hold, or cannot be established from
the sources. It confirms, refutes or corrects the machine-read label statements, and
carries what no label gives (a usual onset, an assay method, a co-medication, a
special ruling no worklist asked for). Design spec
2026-11-27-drugref-lab-effects-design.md section 6.5.

The same append-then-point sequence as curation_property.py: INSERT the new row, then
point whatever was live on the key at it (overlay.supersede). Recording the SAME
ruling again is a no-op, so re-running a curation file does not pile up identical rows
every signed release would have to carry.

A CLASS ruling also snapshots which drugs it reached at that moment (snapshot_reach),
because class membership is rebuilt by every ingest and the decision audit (#258)
must be able to say who a ruling covered when it was made.

NO VOCABULARY IS RESTATED IN PYTHON: direction, role, verdict and the method/onset
rules live in db/090's CHECKs, so a bad value raises from the database.
NOTHING HERE COMMITS; the caller owns the transaction.
"""
import uuid

import psycopg

from drugref import overlay, questions

_KEY = ("subject_kind", "subject_uuid", "analyte_code", "direction", "role",
        "co_medication_kind", "co_medication_uuid")
_NIL = uuid.UUID(int=0)
# The fields that, if any differs from the live row's, make a new row worth signing.
_COMPARED = ("verdict", "onset_min_days", "onset_max_days", "method", "question_uuid",
             "reviewed_by", "reviewed_against")


def answerable_questions(moiety, analyte_code):
    """The worklist questions a ruling may cite: the (moiety, analyte) label question.

    A class-subject ruling has none (the worklist asks about one drug's labels).
    """
    if moiety is None:
        return []
    return [("unconfirmed_label_lab_effect",
             {"moiety_uuid": moiety, "analyte_code": analyte_code})]


def snapshot_reach(conn: psycopg.Connection, curated_lab_effect_id: int) -> int:
    """Write the class ruling's current reach into curated_lab_effect_reach.

    One row: the sorted members of the ruling's subject class (empty array if it
    reached no one -- record_lab_effect_ruling refuses such a ruling first). Returns
    the number of members written, also 0 when no row is written (a moiety ruling or
    an unknown id). Call it after the ruling is live. Does not commit.
    """
    members = conn.execute(
        "INSERT INTO drugref.curated_lab_effect_reach "
        "(curated_lab_effect_id, members, captured) "
        "SELECT r.curated_lab_effect_id, "
        "COALESCE((SELECT array_agg(m.moiety_uuid ORDER BY m.moiety_uuid) "
        "          FROM drugref.lab_effect_class_member m "
        "          WHERE m.class_uuid = r.subject_class_uuid), '{}'), 'at_ruling' "
        "FROM drugref.curated_lab_effect r "
        "WHERE r.curated_lab_effect_id = %s AND r.subject_class_uuid IS NOT NULL "
        "RETURNING members", (curated_lab_effect_id,)).fetchone()
    return len(members[0]) if members else 0


def record_lab_effect_ruling(
        conn: psycopg.Connection, *,
        subject_class_uuid: uuid.UUID | None = None,
        subject_moiety_uuid: uuid.UUID | None = None,
        analyte_code: str, direction: str, role: str, verdict: str,
        reviewed_by: str, reviewed_against: str,
        co_medication_moiety_uuid: uuid.UUID | None = None,
        co_medication_class_uuid: uuid.UUID | None = None,
        onset_min_days: int | None = None, onset_max_days: int | None = None,
        method: str | None = None,
        question_uuid: uuid.UUID | None = None) -> tuple[int, bool]:
    """Record (or revise) one ruling; returns (live row id, whether a row was written).

    Idempotent by comparison: if the live row for the key already carries the same
    _COMPARED fields, (its id, False) is returned and nothing is written. Any
    difference is new provenance: the new row is INSERTed, supersedes the old one, and
    (new id, True) is returned.

    A given question_uuid must be the (moiety, analyte) worklist question for this
    ruling's own subject and analyte, else ValueError; a class subject can cite none.
    A class subject that reaches no drug is refused (ValueError) before anything is
    written.
    """
    if (subject_class_uuid is None) == (subject_moiety_uuid is None):
        raise ValueError("give exactly one of subject_class_uuid / subject_moiety_uuid")
    questions.require_question_for(
        conn, question_uuid, answerable_questions(subject_moiety_uuid, analyte_code))
    # A class no drug is a member of (or a MED-RT PK class, which the member rule
    # excludes) would be a ruling about nobody, accepted without a word.
    if subject_class_uuid is not None and conn.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM drugref.lab_effect_class_member "
            "WHERE class_uuid = %s)", (subject_class_uuid,)).fetchone()[0]:
        raise ValueError(f"class {subject_class_uuid} reaches no drug "
                         "(lab_effect_class_member): rule on its members instead")
    # The generated key columns, computed as db/090 computes them, to find the live row.
    # (Two co-medications are left for the CHECK to refuse on INSERT.)
    co_kind, co_uuid = (("moiety", co_medication_moiety_uuid)
                        if co_medication_moiety_uuid is not None else
                        ("class", co_medication_class_uuid)
                        if co_medication_class_uuid is not None else ("none", _NIL))
    key = ("class" if subject_class_uuid else "moiety",
           subject_class_uuid or subject_moiety_uuid,
           analyte_code, direction, role, co_kind, co_uuid)
    supplied = (verdict, onset_min_days, onset_max_days, method, question_uuid,
                reviewed_by, reviewed_against)

    live = conn.execute(
        "SELECT curated_lab_effect_id, " + ", ".join(_COMPARED) +
        " FROM drugref.curated_lab_effect WHERE superseded_by IS NULL AND " +
        " AND ".join(f"{c} = %s" for c in _KEY), key).fetchone()
    if live is not None and tuple(live[1:]) == supplied:
        return live[0], False

    new_id = conn.execute(
        "INSERT INTO drugref.curated_lab_effect "
        "(subject_class_uuid, subject_moiety_uuid, analyte_code, direction, role, "
        " co_medication_moiety_uuid, co_medication_class_uuid, verdict, "
        " onset_min_days, onset_max_days, method, question_uuid, source, "
        " reviewed_by, reviewed_against) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'DRUGREF', %s, %s) "
        "RETURNING curated_lab_effect_id",
        (subject_class_uuid, subject_moiety_uuid, analyte_code, direction, role,
         co_medication_moiety_uuid, co_medication_class_uuid, *supplied)).fetchone()[0]
    overlay.supersede(conn, "curated_lab_effect", "curated_lab_effect_id",
                      new_id, _KEY, key)
    if subject_class_uuid is not None:
        snapshot_reach(conn, new_id)
    return new_id, True

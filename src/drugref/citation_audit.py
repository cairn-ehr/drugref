"""Census of curated rows that cite a question about something else (#210).

#207 (PR #208) made every curated writer refuse a `question_uuid` that is not the
question minted from the row's own key. Rows written BEFORE that guard were never
checked, and a wrong citation is not harmless: register_from_gaps keeps any cited
question alive for as long as the citing row exists, so the row pins an unrelated
question to the registry forever.

This module only REPORTS. The tables are append-only, so a miscited row is never
UPDATEd. A curator re-recording the ruling through its writer (with the right
question, or none) corrects the LIVE ruling's citation -- but the superseded row
stays, and so does the pin: the retention guard reads every row, live or not. So the
census reads every row too and says which are live; a superseded finding is permanent
and is reported for awareness, not action.

The check is the writers' own: each table's candidate list comes from the function
its writer calls (curation.*_questions, curation_property.answerable_questions), and
the comparison is questions.cites_own_question. Nothing here restates a key format.

A finding whose cited gap_key IS the row's own key is a hand-inserted question whose
uuid was not minted from that key (register_from_gaps never makes one). The guard
would refuse it too, so it is reported; first seen in `drugref_reviewer_dev`'s GUI
test data (2026-11-08).
"""
import psycopg

from drugref import curation, curation_property, questions

# Per table: its id column, the columns its candidate function takes (in order), and
# that function. Every table with a question_uuid FK written by a curated writer.
_TABLES = {
    "curated_interaction": (
        "curated_interaction_id",
        "subject_moiety_uuid, object_class_uuid, relationship",
        curation.interaction_questions),
    "curated_condition": (
        "curated_condition_id",
        "subject_moiety_uuid, object_condition_uuid",
        curation.condition_questions),
    "curated_class_interaction": (
        "curated_class_interaction_id",
        "subject_class_uuid, object_class_uuid, relationship",
        curation.class_interaction_questions),
    "curated_property_ruling": (
        "curated_property_ruling_id",
        # subject_moiety_uuid is NULL for a class subject, which answers nothing.
        "subject_moiety_uuid, relationship, object_kind, object_uuid",
        curation_property.answerable_questions),
}


def miscited(conn: psycopg.Connection) -> list[dict]:
    """Every curated row whose question_uuid is not one its own key may cite.

    Each report: table, row_id, live (not superseded), question_uuid, and the cited
    question's gap_kind and gap_key, so a reader can see what it is actually about.
    Rows citing no question are skipped: citing none is legal and means "rests on
    nothing recorded".
    """
    found = []
    for table, (id_column, key_columns, candidates_of) in _TABLES.items():
        rows = conn.execute(
            f"SELECT t.{id_column}, t.superseded_by IS NULL, t.question_uuid, "
            f"       q.gap_kind, q.gap_key, {key_columns} "
            f"FROM drugref.{table} t "
            f"JOIN drugref.open_question q USING (question_uuid) "
            f"ORDER BY t.{id_column}").fetchall()
        for row_id, live, question_uuid, gap_kind, gap_key, *key in rows:
            if not questions.cites_own_question(conn, question_uuid,
                                                candidates_of(*key)):
                found.append({"table": table, "row_id": row_id, "live": live,
                              "question_uuid": question_uuid,
                              "cited_gap_kind": gap_kind, "cited_gap_key": gap_key})
    return found

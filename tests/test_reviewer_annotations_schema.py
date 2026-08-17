"""db/045 reviewer annotation and working-reference integrity floor. DB-gated."""

import uuid

import psycopg
import pytest

from drugref import ids


def _run(conn: psycopg.Connection) -> int:
    """Insert one ingest run that can own a deterministic open question."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'test', 'deadbeef', 'medrt_run') "
        "RETURNING ingest_run_id"
    ).fetchone()[0]


def _question(conn: psycopg.Connection) -> uuid.UUID:
    """Insert one current interaction question and return its stable UUID."""
    run_id = _run(conn)
    gap_key = f"MOIETY:{uuid.uuid4()}/CLASS:{uuid.uuid4()}/CI_AXIS:CI_with"
    question_uuid = ids.mint_question_uuid("uncurated_interaction_rule", gap_key)
    conn.execute(
        "INSERT INTO drugref.open_question "
        "(question_uuid, gap_kind, gap_key, question_text, first_derived_ingest, "
        "last_derived_ingest) VALUES (%s, 'uncurated_interaction_rule', %s, "
        "'How severe is this interaction?', %s, %s)",
        (question_uuid, gap_key, run_id, run_id),
    )
    return question_uuid


def _reviewer(conn: psycopg.Connection) -> uuid.UUID:
    """Insert one stable reviewer identity used for authorship tests."""
    reviewer_uuid = uuid.uuid4()
    conn.execute(
        "INSERT INTO drugref.reviewer_account (reviewer_uuid, username) "
        "VALUES (%s, 'maya.chen')",
        (reviewer_uuid,),
    )
    return reviewer_uuid


def test_annotation_is_attributed_and_insert_only(conn: psycopg.Connection) -> None:
    """A working note keeps its question and reviewer and cannot be rewritten."""
    question_uuid = _question(conn)
    reviewer_uuid = _reviewer(conn)
    annotation_id = conn.execute(
        "INSERT INTO drugref.reviewer_annotation "
        "(question_uuid, reviewer_uuid, annotation_markdown) VALUES (%s, %s, %s) "
        "RETURNING reviewer_annotation_id",
        (question_uuid, reviewer_uuid, "Reviewed the current product label."),
    ).fetchone()[0]

    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(
            "UPDATE drugref.reviewer_annotation SET annotation_markdown = 'changed' "
            "WHERE reviewer_annotation_id = %s",
            (annotation_id,),
        )


def test_annotation_rejects_blank_or_unattributed_text(conn: psycopg.Connection) -> None:
    """Whitespace and unknown authors cannot enter the working-note ledger."""
    question_uuid = _question(conn)
    reviewer_uuid = _reviewer(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.reviewer_annotation "
            "(question_uuid, reviewer_uuid, annotation_markdown) "
            "VALUES (%s, %s, '   ')",
            (question_uuid, reviewer_uuid),
        )
    conn.rollback()

    question_uuid = _question(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.reviewer_annotation "
            "(question_uuid, reviewer_uuid, annotation_markdown) "
            "VALUES (%s, %s, 'note')",
            (question_uuid, uuid.uuid4()),
        )


def test_evidence_reference_records_no_clinical_verdict(conn: psycopg.Connection) -> None:
    """A working citation stores identity and context but no ruling vocabulary."""
    question_uuid = _question(conn)
    reviewer_uuid = _reviewer(conn)
    reference_id = conn.execute(
        "INSERT INTO drugref.reviewer_evidence_reference "
        "(question_uuid, reviewer_uuid, reference_scheme, reference_value, "
        "note_markdown) VALUES (%s, %s, 'PMID', '12345678', 'Primary study') "
        "RETURNING reviewer_evidence_reference_id",
        (question_uuid, reviewer_uuid),
    ).fetchone()[0]
    columns = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'drugref' "
            "AND table_name = 'reviewer_evidence_reference'"
        )
    }
    assert not {"verdict", "confidence", "evidence_grade", "applies"} & columns

    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(
            "DELETE FROM drugref.reviewer_evidence_reference "
            "WHERE reviewer_evidence_reference_id = %s",
            (reference_id,),
        )


@pytest.mark.parametrize("scheme", ["doi", "OTHER", ""])
def test_evidence_reference_scheme_uses_the_existing_closed_vocabulary(
    conn: psycopg.Connection, scheme: str
) -> None:
    """Misspelled or unresolvable citation schemes fail at the database boundary."""
    question_uuid = _question(conn)
    reviewer_uuid = _reviewer(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.reviewer_evidence_reference "
            "(question_uuid, reviewer_uuid, reference_scheme, reference_value) "
            "VALUES (%s, %s, %s, '123')",
            (question_uuid, reviewer_uuid, scheme),
        )

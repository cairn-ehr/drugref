# tests/test_schema_question_registry.py
"""Schema guarantees for the open-question registry (Plan A, db/007).

The registry is a HYBRID store and the split is the whole point:

  * `open_question` is a REBUILDABLE PROJECTION -- re-derived from the gap views on
    every ingest, keyed on the deterministic question_uuid, no append-only floor.
  * `question_state`, `question_source_check` and `question_evidence` are what a
    CURATOR or an external notifier contributes. They are append-only and keyed off
    that same UUID, because a rebuild must not be able to destroy them.

The tests that matter most are the ones that would pass under a natural-key primary
key and fail in production: a correction must INSERT beside the row it supersedes,
which is impossible if the natural key is the PK. That is the defect db/001 shipped
on identity_claim and db/005 repaired; these pin it so it cannot come back here.
"""
import uuid

import pytest
import psycopg

from drugref import ids


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _question(conn, run_id, gap_kind="unclassified_moiety", gap_key=None):
    gap_key = gap_key or f"MOIETY:{uuid.uuid4()}"
    qu = ids.mint_question_uuid(gap_kind, gap_key)
    conn.execute(
        "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, "
        "question_text, search_expression, first_derived_ingest, last_derived_ingest) "
        "VALUES (%s, %s, %s, 'why?', 'q', %s, %s)",
        (qu, gap_kind, gap_key, run_id, run_id))
    return qu


def _state(conn, run_id, qu, state="withdrawn"):
    return conn.execute(
        "INSERT INTO drugref.question_state (question_uuid, state, rationale, source, "
        "ingest_run) VALUES (%s, %s, 'because', 'DRUGREF', %s) "
        "RETURNING question_state_id", (qu, state, run_id)).fetchone()[0]


# ---- open_question: the rebuildable half ------------------------------------


def test_open_question_is_keyed_on_the_deterministic_uuid(conn):
    run_id = _run(conn)
    qu = _question(conn, run_id, "unmatched_ingredient", "RXNORM_IN:5640")
    assert qu == ids.mint_question_uuid("unmatched_ingredient", "RXNORM_IN:5640")


def test_re_deriving_the_same_gap_does_not_duplicate(conn):
    """The rebuild is an upsert on question_uuid: deriving the same gap twice is one
    row, which is what makes the derived half safe to rebuild every ingest."""
    run_id = _run(conn)
    _question(conn, run_id, "unmatched_ingredient", "RXNORM_IN:5640")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _question(conn, run_id, "unmatched_ingredient", "RXNORM_IN:5640")


def test_open_question_rows_may_be_deleted(conn):
    """No append-only floor here, deliberately: a gap that closes must be able to
    leave the projection. The floor belongs on the curated tables, not this one."""
    run_id = _run(conn)
    qu = _question(conn, run_id)
    conn.execute("DELETE FROM drugref.open_question WHERE question_uuid = %s", (qu,))
    assert conn.execute("SELECT count(*) FROM drugref.open_question "
                        "WHERE question_uuid = %s", (qu,)).fetchone()[0] == 0


def test_gap_kind_is_constrained_to_the_shipped_vocabulary(conn):
    run_id = _run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, "
            "question_text, first_derived_ingest, last_derived_ingest) "
            "VALUES (%s, 'not_a_gap_kind', 'X:1', 'why?', %s, %s)",
            (uuid.uuid4(), run_id, run_id))


# ---- question_state: the curated half, and the surrogate-key proof -----------


def test_a_state_correction_inserts_beside_the_row_it_supersedes(conn):
    """THE test for the surrogate key. Correction-by-overlay means inserting a second
    row with the SAME natural key (question_uuid) and pointing the old one at it. A
    natural-key primary key rejects that insert outright, leaving in-place mutation
    as the only option -- exactly what the overlay exists to prevent.

    Note the ORDER, which is why single-live is a DEFERRED constraint here and an
    immediate index on question_evidence: superseded_by must reference a row that
    already exists, so the new row is necessarily live for the instant between the
    INSERT and the UPDATE. An immediate check would reject the only sequence that
    can express a correction. question_evidence has no such problem because its
    natural key includes the reference, so a correction never collides.
    """
    run_id = _run(conn)
    qu = _question(conn, run_id)
    first = _state(conn, run_id, qu, "evidence_under_review")

    second = _state(conn, run_id, qu, "answered")          # both live, mid-transaction
    conn.execute("UPDATE drugref.question_state SET superseded_by = %s "
                 "WHERE question_state_id = %s", (second, first))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")          # invariant must now hold

    live = conn.execute(
        "SELECT state FROM drugref.question_state "
        "WHERE question_uuid = %s AND superseded_by IS NULL", (qu,)).fetchall()
    assert live == [("answered",)]
    # and the superseded row is still there: what was believed, and when, stays answerable
    assert conn.execute("SELECT count(*) FROM drugref.question_state "
                        "WHERE question_uuid = %s", (qu,)).fetchone()[0] == 2


def test_two_live_states_for_one_question_are_rejected(conn):
    """The invariant the deferral must not weaken: leaving BOTH rows live is a
    contradiction (which state is the question in?) and fails when the constraint is
    checked -- at commit in production, forced here without committing."""
    run_id = _run(conn)
    qu = _question(conn, run_id)
    _state(conn, run_id, qu, "open")
    _state(conn, run_id, qu, "answered")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_state_is_constrained_to_the_four_values(conn):
    run_id = _run(conn)
    qu = _question(conn, run_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        _state(conn, run_id, qu, "sort_of_answered")


def test_question_state_is_append_only(conn):
    run_id = _run(conn)
    qu = _question(conn, run_id)
    sid = _state(conn, run_id, qu)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.question_state WHERE question_state_id = %s",
                     (sid,))


def test_state_cannot_be_rewritten_in_place(conn):
    run_id = _run(conn)
    qu = _question(conn, run_id)
    sid = _state(conn, run_id, qu, "open")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.question_state SET state = 'answered' "
                     "WHERE question_state_id = %s", (sid,))


def test_supersession_is_one_way(conn):
    """Mirrors db/005's forbid_claim_rewrite: set once, never unset, never re-pointed."""
    run_id = _run(conn)
    qu = _question(conn, run_id)
    first, second = _state(conn, run_id, qu, "open"), _state(conn, run_id, qu, "answered")
    conn.execute("UPDATE drugref.question_state SET superseded_by = %s "
                 "WHERE question_state_id = %s", (second, first))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.question_state SET superseded_by = NULL "
                     "WHERE question_state_id = %s", (first,))


def test_supersession_must_point_forward(conn):
    """A strictly increasing chain is what makes a cycle unrepresentable -- and the
    surrogate id is the only thing that gives an ordering to check."""
    run_id = _run(conn)
    qu = _question(conn, run_id)
    first, second = _state(conn, run_id, qu, "open"), _state(conn, run_id, qu, "answered")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.question_state SET superseded_by = %s "
                     "WHERE question_state_id = %s", (first, second))


def test_supersession_may_not_cross_questions(conn):
    """A correction replaces a statement about THIS question. Pointing across is a
    merge, and the registry has no merge semantics (question_uuid is immortal)."""
    run_id = _run(conn)
    qu_a, qu_b = _question(conn, run_id), _question(conn, run_id)
    a, b = _state(conn, run_id, qu_a, "open"), _state(conn, run_id, qu_b, "open")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.question_state SET superseded_by = %s "
                     "WHERE question_state_id = %s", (b, a))


# ---- question_source_check: the per-tier watermark ---------------------------


def _check(conn, qu, source="openFDA-SPL", version="2026-07-01", outcome="not_covered"):
    return conn.execute(
        "INSERT INTO drugref.question_source_check (question_uuid, source, "
        "source_version, outcome, note) VALUES (%s, %s, %s, %s, 'n') "
        "RETURNING question_source_check_id", (qu, source, version, outcome)).fetchone()[0]


def test_a_literature_check_is_recordable(conn):
    """The case the first design's PK could not represent at all: literature has no
    release version, so the search DATE is its version."""
    run_id = _run(conn)
    qu = _question(conn, run_id)
    _check(conn, qu, "literature", "2026-07-25", "not_covered")


def test_rechecking_a_newer_version_is_a_new_row(conn):
    """Append-only: a re-check against a newer release does not overwrite the old
    one, which is what makes 'has this been looked at since?' answerable."""
    run_id = _run(conn)
    qu = _question(conn, run_id)
    _check(conn, qu, "openFDA-SPL", "2026-07-01")
    _check(conn, qu, "openFDA-SPL", "2026-08-01")
    assert conn.execute("SELECT count(*) FROM drugref.question_source_check "
                        "WHERE question_uuid = %s", (qu,)).fetchone()[0] == 2


def test_rechecking_the_same_version_conflicts(conn):
    run_id = _run(conn)
    qu = _question(conn, run_id)
    _check(conn, qu, "openFDA-SPL", "2026-07-01")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _check(conn, qu, "openFDA-SPL", "2026-07-01")


def test_source_spelling_is_constrained(conn):
    """The cheapest-unchecked-tier ordering JOINs on these literals, so a row written
    as 'openfda-spl' would make the question look never-checked and re-earn expensive
    literature effort forever."""
    run_id = _run(conn)
    qu = _question(conn, run_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        _check(conn, qu, "openfda-spl")


def test_outcome_is_constrained(conn):
    run_id = _run(conn)
    qu = _question(conn, run_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        _check(conn, qu, "MED-RT", "2026.07.06", "probably")


# ---- question_evidence -------------------------------------------------------


def _evidence(conn, run_id, qu, scheme="DOI", value="10.1000/x", verdict="supports"):
    return conn.execute(
        "INSERT INTO drugref.question_evidence (question_uuid, reference_scheme, "
        "reference_value, verdict, confidence, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, 'moderate', 'DRUGREF', %s) "
        "RETURNING question_evidence_id", (qu, scheme, value, verdict, run_id)).fetchone()[0]


def test_reference_scheme_is_constrained(conn):
    run_id = _run(conn)
    qu = _question(conn, run_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        _evidence(conn, run_id, qu, "vibes", "trust me")


def test_the_same_citation_twice_is_one_live_row(conn):
    """Splitting reference into scheme+value is what makes dedup possible at all."""
    run_id = _run(conn)
    qu = _question(conn, run_id)
    _evidence(conn, run_id, qu, "DOI", "10.1000/x")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _evidence(conn, run_id, qu, "DOI", "10.1000/x")


def test_evidence_may_be_superseded_by_a_later_finding(conn):
    """Medicine revises. A later finding supersedes an earlier one; nothing is
    deleted, so the record of what was believed before survives."""
    run_id = _run(conn)
    qu = _question(conn, run_id)
    first = _evidence(conn, run_id, qu, "DOI", "10.1000/x", "supports")
    conn.execute("UPDATE drugref.question_evidence SET superseded_by = %s "
                 "WHERE question_evidence_id = %s",
                 (_evidence(conn, run_id, qu, "DOI", "10.1000/y", "refutes"), first))
    assert conn.execute(
        "SELECT count(*) FROM drugref.question_evidence WHERE question_uuid = %s",
        (qu,)).fetchone()[0] == 2


def test_question_evidence_is_append_only(conn):
    run_id = _run(conn)
    qu = _question(conn, run_id)
    eid = _evidence(conn, run_id, qu)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.question_evidence "
                     "WHERE question_evidence_id = %s", (eid,))

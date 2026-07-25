# tests/test_questions.py
"""Deriving the register from the gap views, and the curator API over it.

The load-bearing test in this file is test_curator_state_survives_a_rebuild. Every
other property here would hold just as well under the design that put `state` on
open_question -- on a FRESH database. That design fails only on the second ingest of
a long-lived one, which is exactly the shape of bug that reaches production.
"""
import uuid

import pytest

from drugref import ids, questions


@pytest.fixture(autouse=True)
def _isolate(conn):
    """The gap views read the WHOLE registry, so any moiety or class another module
    committed shows up as a gap here and makes these counts non-deterministic. The
    orchestrator tests (test_medrt_run, test_ingest_run) commit internally, so the
    conn fixture's rollback cannot isolate against them -- truncate first, exactly as
    those modules do for the same reason."""
    conn.execute("TRUNCATE drugref.class_contraindication, drugref.class_membership, "
                 "drugref.class_parent, drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _moiety(conn, run_id, name="orphanium"):
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run_id))
    return m


def _empty_ci_class(conn, run_id, code="N0000000001"):
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
        "published_code, class_name, concept_type, first_seen_ingest) "
        "VALUES (%s, 'MED-RT', %s, %s, 'Renal Arterial Vasoconstriction [PE]', 'PE', %s)",
        (cu, code, code, run_id))
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_PE', 'MED-RT', %s)", (_moiety(conn, run_id, "subj"), cu, run_id))
    return cu


# ---- derivation --------------------------------------------------------------


def test_an_unclassified_moiety_becomes_a_question(conn):
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    row = conn.execute(
        "SELECT question_uuid, gap_kind, gap_key FROM drugref.open_question").fetchone()
    assert row == (ids.mint_question_uuid("unclassified_moiety", f"MOIETY:{m}"),
                   "unclassified_moiety", f"MOIETY:{m}")


def test_an_unpopulated_contraindication_becomes_a_question(conn):
    run_id = _run(conn)
    cu = _empty_ci_class(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    row = conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'unpopulated_contraindication'").fetchone()
    assert row[0] == f"CLASS:{cu}"
    # The text is what a literature search or a label probe is run against, so the
    # class must be named in it rather than referenced only by UUID.
    assert "Renal Arterial Vasoconstriction [PE]" in row[1]


def test_registration_is_idempotent(conn):
    """The derived half is rebuilt every ingest; running it twice must not duplicate,
    or the register would grow without bound while describing the same gaps."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    assert conn.execute("SELECT count(*) FROM drugref.open_question").fetchone()[0] == 1


def test_a_rebuild_refreshes_the_last_seen_watermark(conn):
    """first_derived_ingest is write-once provenance; last_derived_ingest answers
    'is this gap still open' without anyone writing a state row."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    later = _run(conn)
    questions.register_from_gaps(conn, later)

    first, last = conn.execute("SELECT first_derived_ingest, last_derived_ingest "
                               "FROM drugref.open_question").fetchone()
    assert (first, last) == (run_id, later)


def test_a_closed_gap_leaves_the_register(conn):
    """A gap that closes must be able to leave -- the projection tracks reality, and
    a register that only ever grows is the stale document these views replace."""
    run_id = _run(conn)
    m = _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    cu = ids.mint_class_uuid("MED-RT", "N0000000009")
    conn.execute("INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
                 "published_code, class_name, concept_type, first_seen_ingest) "
                 "VALUES (%s, 'MED-RT', 'N9', 'N9', 'C [PE]', 'PE', %s)", (cu, run_id))
    conn.execute("INSERT INTO drugref.class_membership "
                 "(moiety_uuid, class_uuid, relationship, ingest_run) "
                 "VALUES (%s, %s, 'has_PE', %s)", (m, cu, run_id))
    questions.register_from_gaps(conn, run_id)

    assert conn.execute("SELECT count(*) FROM drugref.open_question").fetchone()[0] == 0


# ---- the curated half, and the property that motivated it --------------------


def test_curator_state_survives_a_rebuild(conn):
    """THE test. `state` lived on open_question in the first design -- the same table
    the rebuild re-derives -- so every ingest silently erased every `withdrawn` and
    the suppressed question came straight back. Keyed off the immortal question_uuid
    in its own append-only table, it survives."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    questions.set_state(conn, qu, "withdrawn", "duplicate", run_id)

    questions.register_from_gaps(conn, _run(conn))          # the next ingest

    assert questions.current_state(conn, qu) == "withdrawn"
    assert qu not in [r[0] for r in
                      conn.execute("SELECT question_uuid FROM drugref.question_worklist")]


def test_a_question_with_no_state_row_is_open(conn):
    """Absence means open, so thousands of questions register without writing any
    state at all -- which is what makes auto-registration affordable."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    assert questions.current_state(conn, qu) == "open"
    assert conn.execute("SELECT count(*) FROM drugref.question_state").fetchone()[0] == 0


def test_changing_state_supersedes_rather_than_overwrites(conn):
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]

    questions.set_state(conn, qu, "evidence_under_review", "probing openFDA", run_id)
    questions.set_state(conn, qu, "answered", "label names the class", run_id)

    assert questions.current_state(conn, qu) == "answered"
    # both are still on record: what was believed, and when, stays answerable
    assert conn.execute("SELECT count(*) FROM drugref.question_state "
                        "WHERE question_uuid = %s", (qu,)).fetchone()[0] == 2


# ---- the worklist and the cost ladder ---------------------------------------


def test_an_unchecked_question_outranks_a_checked_one(conn):
    """The ladder that governs where effort goes: a question with no openFDA-SPL row
    has not yet earned literature-mining effort, so it must sort first. Asserted
    rather than assumed, because nothing else makes the ordering real."""
    run_id = _run(conn)
    _moiety(conn, run_id, "unchecked")
    _empty_ci_class(conn, run_id)
    questions.register_from_gaps(conn, run_id)

    checked = conn.execute(
        "SELECT question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unpopulated_contraindication'").fetchone()[0]
    questions.record_source_check(conn, checked, "openFDA-SPL", "2026-07-01", "not_covered")

    order = [r[0] for r in conn.execute(
        "SELECT question_uuid FROM drugref.question_worklist")]
    assert order.index(checked) == len(order) - 1


def test_a_withdrawn_question_is_off_the_worklist_but_still_registered(conn):
    """Withdrawal suppresses noise; it does not delete the question, which an
    external tool may already have cited."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    questions.set_state(conn, qu, "withdrawn", "malformed", run_id)

    assert conn.execute("SELECT count(*) FROM drugref.question_worklist").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM drugref.open_question").fetchone()[0] == 1


def test_an_old_check_does_not_close_a_question(conn):
    """Watermark, not closure: 'no evidence found' leaves the question open with a
    recent check row. Medicine moves, and a question unanswerable this month may be
    answerable next."""
    run_id = _run(conn)
    _moiety(conn, run_id)
    questions.register_from_gaps(conn, run_id)
    qu = conn.execute("SELECT question_uuid FROM drugref.open_question").fetchone()[0]
    questions.record_source_check(conn, qu, "literature", "2020-01-01", "not_covered")

    assert questions.current_state(conn, qu) == "open"
    assert conn.execute("SELECT count(*) FROM drugref.question_worklist").fetchone()[0] == 1

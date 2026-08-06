# tests/test_curation_writer.py
"""The writers for the curated overlay (db/029, slice 5c.1).

Correction-by-overlay is INSERT-then-point-the-old-row-at-it, and the ORDER is the part
that is easy to get wrong: `superseded_by` is a foreign key to a row that must already
exist, so pointing first cannot work -- and getting it backwards fails at COMMIT, far
from the call that caused it. That is why these are functions and not a paragraph of
documentation telling every curator to write the sequence themselves.

NO VOCABULARY IS RESTATED HERE. severity, evidence_grade and ruling live in db/029's
CHECKs; a bad value raises CheckViolation from the database, which one test asserts.
"""
import psycopg
import pytest

from drugref import curation
from tests.test_curated_overlay import _a_class, _a_condition


def test_recording_a_judgement_makes_it_live(conn, a_moiety, ingest_run_id):
    klass = _a_class(conn, ingest_run_id)
    curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", True, severity="major",
        evidence_grade="established", mechanism="additive bleeding risk",
        management="monitor INR", reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT severity FROM drugref.curated_interaction "
        "WHERE superseded_by IS NULL AND subject_moiety_uuid = %s", (a_moiety,)
    ).fetchone() == ("major",)


def test_revising_a_judgement_supersedes_rather_than_overwrites(conn, a_moiety, ingest_run_id):
    """The whole reason the tier exists: the previous grade must still be answerable
    afterwards, because it is what fired yesterday's alert."""
    klass = _a_class(conn, ingest_run_id)
    first = curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", True, severity="major",
        evidence_grade="suspected", reviewed_by="test", reviewed_against="2026.07.06")
    second = curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", True, severity="moderate",
        evidence_grade="established", reviewed_by="test", reviewed_against="2026.08.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")   # a test that never commits proves nothing
    assert conn.execute(
        "SELECT curated_interaction_id, severity FROM drugref.curated_interaction "
        "WHERE superseded_by IS NULL").fetchall() == [(second, "moderate")]
    assert conn.execute(
        "SELECT superseded_by, severity FROM drugref.curated_interaction "
        "WHERE curated_interaction_id = %s", (first,)).fetchone() == (second, "major")


def test_retiring_a_rule_leaves_nothing_live_and_asserting(conn, a_moiety, ingest_run_id):
    """`applies = false` is how a rule is WITHDRAWN, since supersession alone retires
    nothing: a correction must point at a later row with the same key, so every
    correction leaves one live."""
    klass = _a_class(conn, ingest_run_id)
    curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", True, severity="major",
        evidence_grade="established", reviewed_by="test", reviewed_against="2026.07.06")
    curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", False, reviewed_by="test",
        reviewed_against="2026.08.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_interaction "
        "WHERE superseded_by IS NULL AND applies").fetchone() == (0,)


def test_recording_a_condition_ruling_makes_it_live(conn, a_moiety, ingest_run_id):
    condition = _a_condition(conn, ingest_run_id)
    curation.record_condition_ruling(
        conn, a_moiety, condition, "context_dependent", severity="major",
        evidence_grade="established",
        mechanism="negative inotropy in acute decompensation",
        management="first-line in stable chronic HFrEF; withhold when decompensated",
        reviewed_by="test", reviewed_against="2026.07.06")
    assert conn.execute(
        "SELECT ruling FROM drugref.curated_condition WHERE superseded_by IS NULL"
    ).fetchone() == ("context_dependent",)


def test_revising_a_condition_ruling_supersedes(conn, a_moiety, ingest_run_id):
    condition = _a_condition(conn, ingest_run_id)
    first = curation.record_condition_ruling(
        conn, a_moiety, condition, "contraindicated", severity="major",
        evidence_grade="probable", reviewed_by="test", reviewed_against="2026.07.06")
    second = curation.record_condition_ruling(
        conn, a_moiety, condition, "context_dependent", severity="moderate",
        evidence_grade="established", reviewed_by="test", reviewed_against="2026.08.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT superseded_by FROM drugref.curated_condition "
        "WHERE curated_condition_id = %s", (first,)).fetchone() == (second,)


def test_an_unknown_grade_is_refused_by_the_database(conn, a_moiety, ingest_run_id):
    """The vocabulary has ONE home, in db/029. A second list in Python is a second
    thing to disagree with the first (db/006)."""
    klass = _a_class(conn, ingest_run_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        curation.record_interaction_judgement(
            conn, a_moiety, klass, "CI_MoA", True, severity="major",
            evidence_grade="anecdotal", reviewed_by="test",
            reviewed_against="2026.07.06")


def test_the_writer_does_not_commit(conn, a_moiety, ingest_run_id):
    """The caller owns the transaction, as everywhere in these modules -- so a rollback
    must take the row with it."""
    klass = _a_class(conn, ingest_run_id)
    curation.record_interaction_judgement(
        conn, a_moiety, klass, "CI_MoA", True, severity="major",
        evidence_grade="established", reviewed_by="test", reviewed_against="2026.07.06")
    conn.rollback()
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_interaction").fetchone() == (0,)

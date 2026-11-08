"""The miscited-question census (#210): curated rows written before #207 may cite a
question about something else, and nothing refused them at the time.

DB-gated. "Written before #207" is simulated by switching the guard off for the write
(monkeypatching questions.require_question_for to a no-op), which is exactly the
state the writers were in before PR #208 added it.
"""
import pytest

from drugref import citation_audit, curation, curation_property, ids, questions
from tests import class_profile_world as w


def _question(conn, run, kind, key):
    """Register one question exactly as register_from_gaps would mint it."""
    q = ids.mint_question_uuid(kind, key)
    conn.execute(
        "INSERT INTO drugref.open_question (question_uuid, gap_kind, gap_key, "
        "question_text, first_derived_ingest, last_derived_ingest) "
        "VALUES (%s, %s, %s, 'q', %s, %s)", (q, kind, key, run, run))
    return q


@pytest.fixture
def world(conn, monkeypatch):
    """A tiny registry, an unrelated question to miscite, and the guard switched off."""
    r = w.run(conn)
    a, b = w.moiety(conn, r, "drugA"), w.moiety(conn, r, "drugB")
    k1, k2 = w.klass(conn, r, "ClassA"), w.klass(conn, r, "ClassB")
    cond = w.condition(conn, r, "Epilepsy")
    unrelated = _question(conn, r, "unclassified_moiety", f"MOIETY:{b}")
    monkeypatch.setattr(questions, "require_question_for", lambda *a, **k: None)
    return conn, r, a, b, k1, k2, cond, unrelated


def _who(rows):
    return sorted(row["table"] for row in rows)


def test_rows_citing_a_question_about_something_else_are_reported(world):
    """One miscited row in each of the four tables that carry a question_uuid."""
    conn, _r, a, b, k1, k2, cond, unrelated = world
    curation.record_interaction_judgement(
        conn, a, k1, "CI_MoA", False, question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")
    curation.record_condition_ruling(
        conn, a, cond, "spurious", question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")
    curation.record_class_interaction_judgement(
        conn, k1, k2, "CI_MoA", False, question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=a, relationship="CI_with", object_kind="condition",
        object_uuid=cond, verdict="holds", question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")

    found = citation_audit.miscited(conn)
    assert _who(found) == ["curated_class_interaction", "curated_condition",
                           "curated_interaction", "curated_property_ruling"]
    # Each report names the question it cites, so a curator can see what it is about.
    assert {(f["question_uuid"], f["cited_gap_kind"], f["cited_gap_key"], f["live"])
            for f in found} == {(unrelated, "unclassified_moiety", f"MOIETY:{b}", True)}


def test_rows_citing_their_own_question_or_none_are_not_reported(world):
    conn, r, a, b, k1, k2, cond, _unrelated = world
    own = _question(conn, r, "unconfirmed_class_property",
                    f"CLASSPROP:{a}:interacts_with:moiety:{b}")
    # Recorded from the partner's side: a drug pair is one fact asked of either drug.
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=b, relationship="interacts_with", object_kind="moiety",
        object_uuid=a, verdict="holds", question_uuid=own, reviewed_by="t",
        reviewed_against="t")
    curation.record_condition_ruling(
        conn, a, cond, "spurious", reviewed_by="t", reviewed_against="t")
    assert citation_audit.miscited(conn) == []


def test_a_superseded_row_is_still_reported(world):
    """Superseded rows keep their question alive too (the retention guard reads every
    row, not only the live one), so the census must not stop at the live row."""
    conn, _r, a, _b, _k1, _k2, cond, unrelated = world
    curation.record_condition_ruling(
        conn, a, cond, "spurious", question_uuid=unrelated, reviewed_by="t",
        reviewed_against="t")
    curation.record_condition_ruling(
        conn, a, cond, "spurious", reviewed_by="t2", reviewed_against="t")
    (found,) = citation_audit.miscited(conn)
    assert (found["table"], found["live"]) == ("curated_condition", False)


def test_a_class_subject_property_ruling_citing_anything_is_reported(world):
    """Gap 19 asks about a MEMBER, never a class: a class ruling can cite nothing."""
    conn, r, a, _b, k1, _k2, cond, _unrelated = world
    q = _question(conn, r, "unconfirmed_class_property",
                  f"CLASSPROP:{a}:CI_with:condition:{cond}")
    curation_property.record_property_ruling(
        conn, subject_class_uuid=k1, relationship="CI_with", object_kind="condition",
        object_uuid=cond, verdict="holds", question_uuid=q, reviewed_by="t",
        reviewed_against="t")
    assert _who(citation_audit.miscited(conn)) == ["curated_property_ruling"]

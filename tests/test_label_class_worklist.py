"""Gap kind unconfirmed_label_class_statement (Task 8). DB-gated.

A machine-read label statement about a (drug, class) pair becomes a curator question;
only a curator's `holds` ruling promotes it (to class_asserted_property).
"""
import pytest

from drugref import citation_audit, curation_property, ids, questions
from tests.test_spl_class_schema import _label, _occ, world  # noqa: F401  (fixture)

KIND = "unconfirmed_label_class_statement"


def _registered(conn, r, a, k):
    """Run the registry for real and return the pair's question row (or None)."""
    questions.register_from_gaps(conn, r)
    return conn.execute(
        "SELECT question_uuid, question_text, gap_key FROM drugref.open_question "
        "WHERE gap_kind = %s AND gap_key = %s", (KIND, f"SPLCLASS:{a}:{k}")).fetchone()


def _rule(conn, a, k, verdict="holds", question_uuid=None):
    return curation_property.record_property_ruling(
        conn, subject_moiety_uuid=a, relationship="interacts_with", object_kind="class",
        object_uuid=k, verdict=verdict, reviewed_by="t", reviewed_against="t",
        question_uuid=question_uuid)


def test_label_class_question_registered_after_register_from_gaps(conn, world):  # noqa: F811
    r, k, a, _b = world
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    q, text, _key = _registered(conn, r, a, k)
    assert q == ids.mint_question_uuid(KIND, f"SPLCLASS:{a}:{k}")
    assert "1 label(s)" in text and "avoid" in text
    assert "machine-read, not confirmed" in text
    assert text.endswith("Answering does not change prescribing alerts.")
    assert "safe" not in text.lower().replace("not confirmed", "")


def test_ruling_may_cite_label_class_question(conn, world):  # noqa: F811
    r, k, a, _b = world
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    q = _registered(conn, r, a, k)[0]
    assert _rule(conn, a, k, question_uuid=q)[1] is True


def test_ruling_refuses_label_question_for_other_class(conn, world):  # noqa: F811
    from tests import class_profile_world as w
    r, k, a, _b = world
    other = w.klass(conn, r, "Fibrates")
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    q = _registered(conn, r, a, k)[0]
    with pytest.raises(ValueError, match="does not ask about this row"):
        _rule(conn, a, other, question_uuid=q)


def test_holds_ruling_promotes_and_leaves_worklist(conn, world):  # noqa: F811
    r, k, a, b = world
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    gap = "SELECT count(*) FROM drugref.gap_unconfirmed_label_class_statement"
    # A drug -> class rule is asserted on the OBJECT class: (class, interacts_with, drug).
    prop = ("SELECT count(*) FROM drugref.class_asserted_property WHERE class_uuid = %s "
            "AND relationship = 'interacts_with' AND object_kind = 'moiety' "
            "AND object_uuid = %s")
    # Machine-read alone promotes nothing.
    assert conn.execute(gap).fetchone() == (1,)
    assert conn.execute(prop, (k, a)).fetchone() == (0,)
    _rule(conn, a, k, "holds")
    assert conn.execute(gap).fetchone() == (0,)
    assert conn.execute(prop, (k, a)).fetchone() == (1,)


def test_citation_census_accepts_label_question(conn, world):  # noqa: F811
    r, k, a, _b = world
    _occ(conn, r, k)
    _label(conn, r, a, "S1")
    _rule(conn, a, k, question_uuid=_registered(conn, r, a, k)[0])
    assert citation_audit.miscited(conn) == []

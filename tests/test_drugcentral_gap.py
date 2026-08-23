# tests/test_drugcentral_gap.py
"""Gap kind 18: the endpoint names DrugCentral keys and drugref does not.

Measured 2026-08-23: 37 rows over 10 folded names, ALL on route 'unresolved' --
DrugCentral holds a structural key and drugref does not. That matters for
whether the gate may ask at all: db/012's rule is that the review gate must only
ask what an answer COULD change, and these are registry-coverage work
(phytomenadione is the INN for phytonadione, atracurium for the besylate).
"""
import pytest

from drugref import ids, questions


def _run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def _unresolved(conn, run, key, name, other="warfarin"):
    """One assertion row where endpoint_1 (`name`) is unresolved and endpoint_2
    (`other`) resolves onto a throwaway moiety.

    The brief's version marked BOTH routes 'unresolved'. That makes `other` a
    gap row too, and test_one_question_per_folded_name_not_per_row calls this
    three times with the same default other="warfarin" -- three unresolved
    'warfarin' rows fold to one more gap name than that test's own assertion
    admits. Resolving `other` keeps the only unresolved endpoint the one under
    test, the same split test_a_resolved_endpoint_raises_no_question uses below
    (moiety_1 resolved, endpoint_2 left as the gap).
    """
    other_moiety = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (gen_random_uuid(), %s, %s) RETURNING moiety_uuid",
        (other, run)).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, moiety_2_uuid, route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', %s, %s, %s, 'X/Y [VA]', 'Critical', %s, "
        "        'unresolved', 'display_name')",
        (run, key, name, other, other_moiety))


@pytest.mark.usefixtures("conn")
def test_one_question_per_folded_name_not_per_row(conn):
    """A curator resolves a NAME. 37 rows over 10 names is 10 questions."""
    run = _run(conn)
    _unresolved(conn, run, "a", "Phytomenadione")
    _unresolved(conn, run, "b", "phytomenadione ")
    _unresolved(conn, run, "c", "atracurium")
    rows = conn.execute(
        "SELECT endpoint_name, row_count FROM drugref.gap_unresolved_ddi_endpoint "
        "ORDER BY endpoint_name").fetchall()
    assert rows == [("atracurium", 1), ("phytomenadione", 2)]


@pytest.mark.usefixtures("conn")
def test_a_resolved_endpoint_raises_no_question(conn):
    run = _run(conn)
    moiety = conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (gen_random_uuid(), 'warfarin', %s) RETURNING moiety_uuid",
        (run,)).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, moiety_1_uuid, route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', 'a', 'warfarin', 'vitamin e', 'W/V [VA]', "
        "        'Critical', %s, 'display_name', 'unresolved')", (run, moiety))
    rows = conn.execute(
        "SELECT endpoint_name FROM drugref.gap_unresolved_ddi_endpoint").fetchall()
    assert rows == [("vitamin e",)]


@pytest.mark.usefixtures("conn")
def test_the_gap_kind_is_admitted_and_registered(conn):
    """The CHECK, _GAP_SOURCES and the view are a TRIO: a kind registered with no
    view raises, and a view with no registration is a detector nobody reads --
    issues 74, 66 and 76, three times over."""
    assert "unresolved_ddi_endpoint" in questions._GAP_SOURCES
    (definition,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'open_question_gap_kind'").fetchone()
    assert "unresolved_ddi_endpoint" in definition


@pytest.mark.usefixtures("conn")
def test_the_question_is_minted_with_a_folded_immortal_key(conn):
    """question_uuid is immortal and externally cited, so two spellings of one
    endpoint must not mint two questions."""
    run = _run(conn)
    _unresolved(conn, run, "a", "Phytomenadione")
    questions.register_from_gaps(conn, run)
    rows = conn.execute(
        "SELECT gap_key, question_uuid FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ddi_endpoint'").fetchall()
    assert len(rows) == 1
    gap_key, question_uuid = rows[0]
    assert gap_key == "DRUGCENTRAL:ENDPOINT:phytomenadione"
    assert question_uuid == ids.mint_question_uuid(
        "unresolved_ddi_endpoint", "DRUGCENTRAL:ENDPOINT:phytomenadione")

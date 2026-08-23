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
from drugref.ingest import drugcentral_resolve


def _run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def _unresolved(conn, run, key, name, other="warfarin", route="unresolved"):
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
        "        %s, 'display_name')",
        (run, key, name, other, other_moiety, route))


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
def test_a_blank_endpoint_is_no_question_and_mints_no_uuid(conn):
    """The view's `endpoint_name <> ''` guard, exercised rather than read.

    A blank endpoint CAN reach the assertion table: `resolve_endpoint` returns
    Resolution(None, ROUTE_NOT_A_SUBSTANCE) for an empty name, so the row lands
    with a NULL uuid and is a gap row on every other test the view applies. What
    stops it becoming a question is that one guard -- and without it the register
    would mint a question keyed 'DRUGCENTRAL:ENDPOINT:' with empty text, whose
    question_uuid is IMMORTAL and externally cited. A garbage question is not
    free to withdraw, which is why three lines of test are worth writing for a
    case no row on the 2023 release exercises (zero `ddi` rows have a blank
    `drug_class1` or `drug_class2`).

    Both spellings of "blank" are covered, because the guard is applied to the
    FOLDED value: a truly empty name and a whitespace-only one, which
    `lower(btrim(...))` turns into the same empty string.
    """
    run = _run(conn)
    _unresolved(conn, run, "a", "")
    _unresolved(conn, run, "b", "   ")
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unresolved_ddi_endpoint").fetchone() == (0,)
    questions.register_from_gaps(conn, run)
    assert conn.execute(
        "SELECT count(*) FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ddi_endpoint'").fetchone() == (0,)


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


@pytest.mark.usefixtures("conn")
def test_the_view_PUBLISHES_the_route_rather_than_assuming_one(conn):
    """The view is route-AGNOSTIC by design, so the route has to be a column.

    It filters on a NULL uuid and never on the route vocabulary (db/006's reason:
    filtering there would put the list in a second place). So it admits
    `not_a_substance` and `no_structural_key` alongside `unresolved` -- and the
    question text derived from it asserted the `unresolved` story about all three.
    """
    run = _run(conn)
    _unresolved(conn, run, "A", "Strong CYP3A4 Inhibitors",
                route="not_a_substance")
    _unresolved(conn, run, "B", "phytomenadione", other="aspirin")
    assert conn.execute(
        "SELECT endpoint_name, route FROM drugref.gap_unresolved_ddi_endpoint "
        "ORDER BY endpoint_name").fetchall() == [
            ("phytomenadione", "unresolved"),
            ("strong cyp3a4 inhibitors", "not_a_substance")]


@pytest.mark.usefixtures("conn")
def test_the_question_text_TELLS_THE_TRUTH_for_each_route(conn):
    """question_uuid is IMMORTAL and externally cited, so the text must be right
    the first time -- it cannot be quietly reworded once minted.

    For `not_a_substance` DrugCentral has no struct_id at all, so "DrugCentral
    resolves it to a structure with an InChIKey or a CAS number" was simply false;
    for `no_structural_key` it has one carrying neither key, so the second half
    ("no live identity_claim in drugref carries either") was.
    """
    run = _run(conn)
    _unresolved(conn, run, "A", "Strong CYP3A4 Inhibitors",
                route="not_a_substance")
    _unresolved(conn, run, "B", "phytomenadione", other="aspirin")
    _unresolved(conn, run, "C", "some biologic", other="digoxin",
                route="no_structural_key")
    questions.register_from_gaps(conn, run)
    text = dict(conn.execute(
        "SELECT gap_key, question_text FROM drugref.open_question "
        "WHERE gap_kind = 'unresolved_ddi_endpoint'").fetchall())

    class_name = text["DRUGCENTRAL:ENDPOINT:strong cyp3a4 inhibitors"]
    assert "drug CLASS" in class_name
    assert "resolves it to a structure" not in class_name

    unresolved = text["DRUGCENTRAL:ENDPOINT:phytomenadione"]
    assert "resolves it to a structure with an InChIKey or a CAS number" in unresolved

    keyless = text["DRUGCENTRAL:ENDPOINT:some biologic"]
    assert "neither an InChIKey nor a CAS number" in keyless


@pytest.mark.usefixtures("conn")
def test_the_view_folds_the_way_fold_name_DOES_not_merely_the_way_btrim_would(conn):
    """One rule, two homes, and they were not the same rule.

    db/049's comment says the fold "is drugcentral_resolve.fold_name's rule --
    restated here". One-argument btrim() strips SPACES ONLY; Python's str.strip()
    also strips tab, newline, CR, form feed and vertical tab. Both spellings below
    must fold to ONE name, because question_uuid is immortal and two spellings of
    one endpoint must never mint two questions that can be answered differently.
    """
    run = _run(conn)
    _unresolved(conn, run, "A", "\tPhytomenadione\n")
    _unresolved(conn, run, "B", " phytomenadione ", other="aspirin")
    assert conn.execute(
        "SELECT endpoint_name, row_count FROM drugref.gap_unresolved_ddi_endpoint"
    ).fetchall() == [("phytomenadione", 2)]
    assert drugcentral_resolve.fold_name("\tPhytomenadione\n") == "phytomenadione"

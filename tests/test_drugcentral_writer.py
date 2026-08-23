# tests/test_drugcentral_writer.py
"""interactions.py's DrugCentral half: one insert, one per-source clear.

The clear is what makes "rebuildable projection" true. An assertion retracted
upstream must be able to DISAPPEAR, which an insert-only merge can never express.
"""
import pytest

from drugref import interactions


def _run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('DRUGCENTRAL', '11012023', 'deadbeef', 'drugcentral_run') "
        "RETURNING ingest_run_id").fetchone()[0]


def _moiety(conn, run, name):
    """A gated-in moiety to resolve an endpoint onto. Returns its uuid.

    Takes `run` and writes it into `first_seen_ingest`: that column is
    `NOT NULL REFERENCES drugref.ingest_run(ingest_run_id)` with no default, so a
    two-argument insert of only (moiety_uuid, display_name) raises
    NotNullViolation. Copies the approach test_drugcentral_schema.py's
    `_a_moiety`, test_drugcentral_read_path.py's `_moiety` and
    test_drugcentral_gap.py already use -- the live schema is the authority, not
    the brief that predates this column being read from it.
    """
    return conn.execute(
        "INSERT INTO drugref.substance_moiety "
        "(moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (gen_random_uuid(), %s, %s) RETURNING moiety_uuid",
        (name, run)).fetchone()[0]


def _write(conn, run, key, one=None, two=None, route="unresolved"):
    return interactions.add_drugcentral_assertion(
        conn, ingest_run_id=run, source="DRUGCENTRAL", upstream_key=key,
        endpoint_1_name="one", endpoint_2_name="two",
        upstream_label="ONE/TWO [VA Drug Interaction]", severity_label="Critical",
        moiety_1_uuid=one, moiety_2_uuid=two,
        route_1="display_name" if one else route,
        route_2="display_name" if two else route)


@pytest.mark.usefixtures("conn")
def test_a_row_is_written_once(conn):
    run, a, b = _run(conn), None, None
    a = _moiety(conn, run, "a")
    b = _moiety(conn, run, "b")
    assert _write(conn, run, "C56.1", a, b) is True
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (1,)


@pytest.mark.usefixtures("conn")
def test_repeating_one_upstream_key_is_harmless(conn):
    """ON CONFLICT DO NOTHING, matching every sibling writer: a dump that repeats
    an assertion must not abort an ingest halfway through."""
    run = _run(conn)
    a = _moiety(conn, run, "a")
    b = _moiety(conn, run, "b")
    assert _write(conn, run, "C56.1", a, b) is True
    assert _write(conn, run, "C56.1", a, b) is False
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (1,)


@pytest.mark.usefixtures("conn")
def test_an_unresolved_row_is_written_with_null_uuids(conn):
    run = _run(conn)
    assert _write(conn, run, "C56.2") is True
    row = conn.execute(
        "SELECT moiety_1_uuid, moiety_2_uuid, route_1 "
        "FROM drugref.drugcentral_ddi_assertion").fetchone()
    assert row == (None, None, "unresolved")


@pytest.mark.usefixtures("conn")
def test_the_clear_is_per_source_and_covers_the_whole_projection(conn):
    """Writes ONE resolved row and ONE unresolved row, and clears both.

    A resolved-only fixture would still pass this test's assertion even if
    someone added a `moiety_1_uuid IS NOT NULL` filter to
    clear_source_drugcentral -- the clear's docstring specifically claims it
    also covers the unresolved worklist rows (an endpoint that starts
    resolving must LEAVE the worklist), and that half of the claim needs its
    own row to be at risk of failing.
    """
    run = _run(conn)
    a = _moiety(conn, run, "a")
    b = _moiety(conn, run, "b")
    _write(conn, run, "C56.1", a, b)
    _write(conn, run, "C56.2")  # unresolved: NULL uuids, route 'unresolved'
    interactions.clear_source_drugcentral(conn, "DRUGCENTRAL")
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_assertion").fetchone() == (0,)


@pytest.mark.usefixtures("conn")
def test_a_mixed_row_keeps_every_value_beside_its_own_endpoint(conn):
    """THE TRANSPOSITION TEST. Catches endpoint_1_name and endpoint_2_name being
    swapped against each other INSIDE add_drugcentral_assertion -- the column
    list and the parameter tuple drifting apart, which is the one drift the
    function's keyword-only signature cannot prevent, because the signature only
    protects the CALLER.

    Nothing else in the suite could see it. The final review proved that by
    execution: with `endpoint_1_name`/`endpoint_2_name` transposed in the INSERT,
    and again with `route_1`/`route_2` transposed, all 1959 tests still passed.
    The reason is that every other test writing through this function resolves
    BOTH endpoints or NEITHER, and a transposition of two equal-shaped columns is
    invisible on a symmetric row. This test writes the asymmetric shape instead --
    endpoint 1 resolved onto a real moiety on route 'display_name', endpoint 2
    unresolved with a NULL uuid on route 'unresolved' -- which is the shape 37
    rows of the real 2023 release actually have.

    WHY IT IS WORTH A TEST OF ITS OWN RATHER THAN A CODE READING. Under the name
    transposition, gap_unresolved_ddi_endpoint (which selects endpoint_1_name
    WHERE moiety_1_uuid IS NULL, and the mirror for endpoint 2) would publish the
    RESOLVED partner's name as the unresolvable endpoint -- 'rifabutin' where
    'cortisone' belongs -- and questions.register_from_gaps would mint
    question_uuids from it. Those UUIDs are immortal and externally cited, so a
    wrong one is not cheaply retractable; the gap-view assertion at the end of
    this test is therefore not decoration, it is the consequence being pinned.
    """
    run = _run(conn)
    resolved = _moiety(conn, run, "rifabutin")
    assert interactions.add_drugcentral_assertion(
        conn, ingest_run_id=run, source="DRUGCENTRAL", upstream_key="C56.3",
        endpoint_1_name="rifabutin", endpoint_2_name="cortisone",
        upstream_label="RIFABUTIN/CORTISONE [VA Drug Interaction]",
        severity_label="Significant",
        moiety_1_uuid=resolved, moiety_2_uuid=None,
        route_1="display_name", route_2="unresolved") is True

    # Every column read back beside the endpoint it belongs to. Asserted as one
    # tuple rather than six separate asserts so a transposition cannot be half
    # caught: the whole row is either aligned or it is not.
    assert conn.execute(
        "SELECT endpoint_1_name, endpoint_2_name, moiety_1_uuid, moiety_2_uuid, "
        "       route_1, route_2 "
        "FROM drugref.drugcentral_ddi_assertion").fetchone() == (
            "rifabutin", "cortisone", resolved, None, "display_name", "unresolved")

    # The consequence, through the view that mints immortal question keys: the
    # UNRESOLVED endpoint's name is what gets published, never the resolved
    # partner's.
    assert conn.execute(
        "SELECT endpoint_name, row_count "
        "FROM drugref.gap_unresolved_ddi_endpoint").fetchall() == [("cortisone", 1)]


def test_the_projection_tuple_is_restated_independently():
    """Pinned by name, as every sibling table tuple is: dropping a table from one
    of these leaves a projection that grows a little on every ingest."""
    assert interactions.DRUGCENTRAL_TABLES == ("drugcentral_ddi_assertion",)

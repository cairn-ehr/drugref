# tests/test_ddi_pairs.py
"""ddi_candidate_pair expands class-level CI rules into concrete drug pairs over
the class_membership drugref already builds -- so the pair explosion is never
stored. Two properties carry clinical weight and are pinned here: the axis mapping
(CI_MoA joins has_MoA members, CI_PE joins has_PE members) must not cross-wire, or
the meaning inverts; and a drug is never paired with itself.
"""
import uuid

from drugref import classes, ids, interactions


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _class(conn, run_id, code, cty="MoA"):
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, published_code, class_name, concept_type, "
        " first_seen_ingest) VALUES (%s, 'MED-RT', %s, %s, 'C', %s, %s)",
        (cu, code, code, cty, run_id))
    return cu


def _moiety(conn, run_id, name):
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run_id))
    return m


def _partners(conn, subject):
    return [r[0] for r in conn.execute(
        "SELECT moiety_b FROM drugref.ddi_candidate_pair WHERE moiety_a = %s",
        (subject,)).fetchall()]


def test_ci_moa_expands_to_the_classs_has_moa_members(conn):
    run_id = _run(conn)
    subject, other = _moiety(conn, run_id, "subjectium"), _moiety(conn, run_id, "otherium")
    c = _class(conn, run_id, "N0000000601", "MoA")
    interactions.add_contraindication(conn, subject, c, "CI_MoA", "MED-RT", run_id)
    classes.add_membership(conn, other, c, "has_MoA", run_id)
    assert conn.execute(
        "SELECT moiety_a, moiety_b, relationship, via_class "
        "FROM drugref.ddi_candidate_pair WHERE moiety_a = %s", (subject,)
    ).fetchall() == [(subject, other, "CI_MoA", c)]


def test_ci_pe_joins_has_pe_members_and_never_has_moa_members(conn):
    """The CASE mapping is not cross-wired: a CI_PE reaches has_PE members only. A
    member linked to the same class on the wrong axis must not be paired."""
    run_id = _run(conn)
    subject = _moiety(conn, run_id, "s")
    pe_member, moa_member = _moiety(conn, run_id, "pe"), _moiety(conn, run_id, "moa")
    c = _class(conn, run_id, "N0000000602", "PE")
    interactions.add_contraindication(conn, subject, c, "CI_PE", "MED-RT", run_id)
    classes.add_membership(conn, pe_member, c, "has_PE", run_id)
    classes.add_membership(conn, moa_member, c, "has_MoA", run_id)  # wrong axis
    assert _partners(conn, subject) == [pe_member]


def test_a_drug_is_never_contraindicated_with_itself(conn):
    """The subject is itself a member of the class it is contraindicated against
    (common: a drug of MoA C contraindicated with co-administered MoA-C drugs). It
    must not be paired with itself."""
    run_id = _run(conn)
    subject = _moiety(conn, run_id, "s")
    c = _class(conn, run_id, "N0000000603", "MoA")
    interactions.add_contraindication(conn, subject, c, "CI_MoA", "MED-RT", run_id)
    classes.add_membership(conn, subject, c, "has_MoA", run_id)
    assert _partners(conn, subject) == []

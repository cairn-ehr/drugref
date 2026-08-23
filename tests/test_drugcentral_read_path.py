# tests/test_drugcentral_read_path.py
"""db/049's two views: the canonical pair, and the union over all exact pairs.

The pair view carries THREE rules that exist nowhere else, so each has a test:
the orientation collapse, most-severe-wins between the two orientations, and a
total order so the collapse is stable when they tie.
"""
import pytest


def _run(conn, source="DRUGCENTRAL", writer="drugcentral_run", release="11012023"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES (%s, %s, 'deadbeef', %s) RETURNING ingest_run_id",
        (source, release, writer)).fetchone()[0]


def _moiety(conn, run, name):
    """A gated-in moiety to resolve an endpoint onto. Returns its uuid.

    Takes `run` and writes it into `first_seen_ingest`: that column is
    `NOT NULL REFERENCES drugref.ingest_run(ingest_run_id)` with no default (see
    db/001), so a two-argument insert of only (moiety_uuid, display_name) would
    raise NotNullViolation on every call. tests/test_drugcentral_schema.py's
    `_a_moiety` already does this correctly; this helper copies its approach
    rather than retyping the brief's stale two-argument version.
    """
    return conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES (gen_random_uuid(), %s, %s) RETURNING moiety_uuid",
        (name, run)).fetchone()[0]


def _assert_row(conn, run, key, one, two, label, band="Significant",
                route_1="display_name", route_2="display_name"):
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, moiety_1_uuid, moiety_2_uuid, "
        " route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', %s, 'one', 'two', %s, %s, %s, %s, %s, %s)",
        (run, key, label, band, one, two, route_1, route_2))


@pytest.mark.usefixtures("conn")
def test_both_orientations_collapse_to_one_row(conn):
    """Measured: 33 pairs are published in both orders, as two VA entries at
    different salt grains. They are one pair, not two."""
    run = _run(conn)
    a, b = _moiety(conn, run, "gatifloxacin"), _moiety(conn, run, "pioglitazone")
    _assert_row(conn, run, "fwd", a, b, "A/B HCL [VA Drug Interaction]")
    _assert_row(conn, run, "rev", b, a, "A/B [VA Drug Interaction]")
    rows = conn.execute("SELECT count(*) FROM drugref.drugcentral_ddi_pair").fetchone()
    assert rows == (1,)


@pytest.mark.usefixtures("conn")
def test_the_more_severe_orientation_wins(conn):
    """4 of the 33 disagree on the band. A consumer must not get the lower one."""
    run = _run(conn)
    a, b = _moiety(conn, run, "gemfibrozil"), _moiety(conn, run, "pioglitazone")
    _assert_row(conn, run, "fwd", a, b, "A/B HCL [VA]", band="Significant")
    _assert_row(conn, run, "rev", b, a, "A/B [VA]", band="Critical")
    (severity, rank) = conn.execute(
        "SELECT severity, severity_rank FROM drugref.drugcentral_ddi_pair").fetchone()
    assert (severity, rank) == ("contraindicated", 1)


@pytest.mark.usefixtures("conn")
def test_the_collapse_is_stable_when_the_two_orientations_tie(conn):
    """29 of the 33 duplicates carry the SAME band, so severity_rank ties and
    DISTINCT ON would otherwise keep whichever row the plan happened to emit
    first. upstream_key is the total order that makes it reproducible -- the
    defect the re-measurement's own review found in three unordered lookups.

    Rewritten from the brief's version, which ran the same query five times
    inside one transaction: a query re-run unchanged over unchanged data cannot
    vary, so that could not fail for the reason it named. This version asserts
    two things that CAN each fail on their own:
      (a) the surviving row is the one the documented tiebreak says it must be
          -- the LOWEST upstream_key, not merely "some deterministic row"; a
          collapse that instead broke the tie by, say, upstream_label would
          still pass an "always the same answer" test while violating the rule
          this view claims to implement.
      (b) the tiebreak column is textually present in the view's own ORDER BY,
          so a later edit that deletes `upstream_key` from it -- restoring the
          exact non-determinism this view exists to remove -- fails this test
          even on a single run, rather than requiring a five-times comparison
          that a stable-but-wrong plan could still pass by accident.
    """
    run = _run(conn)
    a, b = _moiety(conn, run, "atazanavir"), _moiety(conn, run, "tadalafil")
    key_fwd, key_rev = "C56^4084^", "C23304710162045"
    _assert_row(conn, run, key_fwd, a, b, "ATAZANAVIR/TADALAFIL [VA]")
    _assert_row(conn, run, key_rev, b, a, "ATAZANAVIR SO4/TADALAFIL [VA]")

    winner = conn.execute(
        "SELECT upstream_key FROM drugref.drugcentral_ddi_pair").fetchone()[0]
    assert winner == min(key_fwd, key_rev), (
        "the collapse must keep the row with the LOWEST upstream_key, the "
        "documented tiebreak; got " + repr(winner))

    (viewdef,) = conn.execute(
        "SELECT pg_get_viewdef('drugref.drugcentral_ddi_pair'::regclass)").fetchone()
    assert "upstream_key" in viewdef, (
        "the tiebreak column must appear in the view's own ORDER BY; its "
        "absence is exactly the regression this test exists to catch")


@pytest.mark.usefixtures("conn")
def test_an_unresolved_row_yields_no_pair(conn):
    run = _run(conn)
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion "
        "(ingest_run, source, upstream_key, endpoint_1_name, endpoint_2_name, "
        " upstream_label, severity_label, route_1, route_2) "
        "VALUES (%s, 'DRUGCENTRAL', 'X', 'vitamin e', 'warfarin', 'V/W [VA]', "
        "        'Critical', 'unresolved', 'unresolved')", (run,))
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_pair").fetchone() == (0,)


@pytest.mark.usefixtures("conn")
def test_a_self_pair_yields_no_pair(conn):
    """Two endpoint names folding onto one moiety asserts nothing about an
    interaction between two drugs -- the rule ddi_candidate_pair already applies."""
    run = _run(conn)
    a = _moiety(conn, run, "azithromycin")
    _assert_row(conn, run, "X", a, a, "A/A [VA]")
    assert conn.execute(
        "SELECT count(*) FROM drugref.drugcentral_ddi_pair").fetchone() == (0,)


@pytest.mark.usefixtures("conn")
def test_the_pair_carries_the_upstream_band_beside_the_drugref_grade(conn):
    """Both, always. The authority's word is what a reviewer checks the mapping
    against, and a grade with no visible provenance cannot be disagreed with."""
    run = _run(conn)
    a, b = _moiety(conn, run, "a"), _moiety(conn, run, "b")
    _assert_row(conn, run, "X", a, b, "A/B [VA]", band="Significant")
    row = conn.execute(
        "SELECT severity, upstream_severity_label, candidate_source, upstream_release "
        "FROM drugref.drugcentral_ddi_pair").fetchone()
    assert row == ("moderate", "Significant", "DRUGCENTRAL", "11012023")

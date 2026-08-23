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


@pytest.mark.usefixtures("conn")
def test_medrt_exact_pairs_reach_a_consumer_at_last(conn):
    """moiety_contraindication has had NO read view since db/014 --
    ddi_candidate_pair expands class_contraindication only. This is the first."""
    run = _run(conn, "MED-RT", "medrt_run", "2026.07.06")
    a, b = _moiety(conn, run, "warfarin"), _moiety(conn, run, "aspirin")
    conn.execute(
        "INSERT INTO drugref.moiety_contraindication "
        "(subject_moiety_uuid, object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_ChemClass', 'MED-RT', %s)", (a, b, run))
    row = conn.execute(
        "SELECT candidate_source, relationship, severity, subject_moiety = %s "
        "FROM drugref.exact_ddi_pair", (a,)).fetchone()
    assert row == ("MED-RT", "CI_ChemClass", None, True)


@pytest.mark.usefixtures("conn")
def test_a_medrt_pair_is_keyed_unordered_even_though_it_is_directional(conn):
    """moiety_lo/moiety_hi is the LOOKUP key -- 'am I about to co-prescribe these
    two?' is an unordered question. The direction is not lost; it moves to
    subject_moiety/object_moiety, which stay populated."""
    run = _run(conn, "MED-RT", "medrt_run", "2026.07.06")
    a, b = _moiety(conn, run, "warfarin"), _moiety(conn, run, "aspirin")
    conn.execute(
        "INSERT INTO drugref.moiety_contraindication "
        "(subject_moiety_uuid, object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_ChemClass', 'MED-RT', %s)", (a, b, run))
    lo, hi, subject, obj = conn.execute(
        "SELECT moiety_lo, moiety_hi, subject_moiety, object_moiety "
        "FROM drugref.exact_ddi_pair").fetchone()
    assert (lo, hi) == (min(a, b), max(a, b))
    assert (subject, obj) == (a, b)


@pytest.mark.usefixtures("conn")
def test_a_drugcentral_pair_asserts_no_direction(conn):
    """NULL states a fact about the source rather than hiding a missing value:
    DrugCentral publishes an unordered pair and names no subject."""
    run = _run(conn)
    a, b = _moiety(conn, run, "a"), _moiety(conn, run, "b")
    _assert_row(conn, run, "X", a, b, "A/B [VA]", band="Critical")
    row = conn.execute(
        "SELECT candidate_source, subject_moiety, object_moiety, relationship, "
        "       severity, severity_rank, upstream_severity_label "
        "FROM drugref.exact_ddi_pair").fetchone()
    assert row == ("DRUGCENTRAL", None, None, None, "contraindicated", 1, "Critical")


@pytest.mark.usefixtures("conn")
def test_both_authorities_appear_for_one_pair_rather_than_one_hiding_the_other(conn):
    """Fewer rows is the harm direction for a contraindication, so this is a
    UNION ALL and a consumer sees both authorities. Which one wins is a curated
    question (issues 97 and 106), deliberately not answered here."""
    medrt = _run(conn, "MED-RT", "medrt_run", "2026.07.06")
    a, b = _moiety(conn, medrt, "warfarin"), _moiety(conn, medrt, "aspirin")
    conn.execute(
        "INSERT INTO drugref.moiety_contraindication "
        "(subject_moiety_uuid, object_moiety_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_ChemClass', 'MED-RT', %s)", (a, b, medrt))
    _assert_row(conn, _run(conn), "X", a, b, "A/B [VA]", band="Critical")
    sources = conn.execute(
        "SELECT candidate_source FROM drugref.exact_ddi_pair "
        "ORDER BY candidate_source").fetchall()
    assert sources == [("DRUGCENTRAL",), ("MED-RT",)]


@pytest.mark.usefixtures("conn")
def test_ddi_candidate_pair_is_untouched_by_this_migration(conn):
    """db/034 measured an arm added to that view costing 3.6x with the new grain
    EMPTY -- a structural cost paid by every existing consumer. This slice is
    additive, and this test is what keeps it that way."""
    (definition,) = conn.execute(
        "SELECT pg_get_viewdef('drugref.ddi_candidate_pair'::regclass)").fetchone()
    assert "drugcentral" not in definition.lower()
    assert "exact_ddi_pair" not in definition

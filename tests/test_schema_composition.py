# tests/test_schema_composition.py
"""db/028: the composition projection, its vocabulary, and its two views."""
import psycopg
import pytest

from drugref import ids


@pytest.fixture
def gsrs_run(conn):
    """An ingest_run under the NEW source and writer -- both CHECKs must admit them."""
    return conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('GSRS', '2026-02-26', 'test', 'gsrs_run') "
        "RETURNING ingest_run_id").fetchone()[0]


@pytest.fixture
def two_moieties(conn, gsrs_run):
    out = []
    for unii in ("COMPONENT1", "COMPONENT2"):
        moiety_uuid = ids.mint_moiety_uuid(unii)
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (moiety_uuid, unii, gsrs_run))
        out.append(moiety_uuid)
    return out


def test_the_source_and_writer_checks_admit_gsrs(gsrs_run):
    """The trio: ingest_run's source CHECK, its writer CHECK, ids._SOURCE_CANONICAL.
    Missing the ingest_run source CHECK stops everything, because every projection
    row carries an ingest_run."""
    assert gsrs_run is not None


def test_the_relation_vocabulary_has_exactly_two_rows(conn):
    rows = conn.execute(
        "SELECT relation FROM drugref.composition_relation ORDER BY relation").fetchall()
    assert [r[0] for r in rows] == ["SALT_SOLVATE", "SOLVATE_ANHYDROUS"]


def test_relation_is_a_foreign_key_not_a_check(conn, gsrs_run, two_moieties):
    """db/006's precedent: the vocabulary lives in a TABLE the column references,
    so it has one home. A CHECK would be a second."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.substance_composition "
            "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
            "VALUES ('SALT000001', %s, 'INVENTED', NULL, %s)",
            (two_moieties[0], gsrs_run))


def test_is_active_component_has_no_default_and_accepts_null(conn, gsrs_run, two_moieties):
    """NULL means UNRULED, not inactive (spec 5.2). 2,668 rows land here."""
    conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES ('SALT000001', %s, 'SALT_SOLVATE', NULL, %s)",
        (two_moieties[0], gsrs_run))
    stored = conn.execute(
        "SELECT is_active_component FROM drugref.substance_composition "
        "WHERE substance_unii = 'SALT000001'").fetchone()[0]
    assert stored is None

    default = conn.execute(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = 'substance_composition' "
        "AND column_name = 'is_active_component'").fetchone()[0]
    assert default is None, "a DEFAULT would turn 'unruled' into an answer nobody gave"


def test_substance_unii_is_not_a_foreign_key(conn):
    """4,425 of 7,377 composites are not moieties (spec 5.1). An FK deletes them."""
    fks = conn.execute(
        "SELECT count(*) FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage k "
        "  ON tc.constraint_name = k.constraint_name "
        "WHERE tc.table_schema = 'drugref' "
        "  AND tc.table_name = 'substance_composition' "
        "  AND tc.constraint_type = 'FOREIGN KEY' "
        "  AND k.column_name = 'substance_unii'").fetchone()[0]
    assert fks == 0


def test_a_composite_may_carry_several_components(conn, gsrs_run, two_moieties):
    """ZINC GLYCINATE CITRATE has three. The PK must not collapse them."""
    for moiety_uuid in two_moieties:
        conn.execute(
            "INSERT INTO drugref.substance_composition "
            "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
            "VALUES ('MULTI00001', %s, 'SALT_SOLVATE', NULL, %s)",
            (moiety_uuid, gsrs_run))
    assert conn.execute(
        "SELECT count(*) FROM drugref.substance_composition "
        "WHERE substance_unii = 'MULTI00001'").fetchone()[0] == 2


def test_the_read_view_shows_only_TRUE(conn, gsrs_run, two_moieties):
    """false propagates nothing and NULL propagates nothing (spec 6.1)."""
    active, counterion = two_moieties
    conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES ('SALT000001', %s, 'SALT_SOLVATE', true, %s), "
        "       ('SALT000001', %s, 'SALT_SOLVATE', false, %s), "
        "       ('SALT000002', %s, 'SALT_SOLVATE', NULL, %s)",
        (active, gsrs_run, counterion, gsrs_run, active, gsrs_run))
    rows = conn.execute(
        "SELECT moiety_uuid, substance_unii FROM drugref.moiety_active_in_composite "
        "ORDER BY substance_unii").fetchall()
    assert rows == [(active, "SALT000001")]


def test_the_gap_view_reports_only_wholly_unruled_composites(conn, gsrs_run, two_moieties):
    """A composite with ANY ruling has been reviewed and leaves the queue --
    the same posture as gap_ungraded_contribution, where an explicit `minor`
    is a review."""
    active, other = two_moieties
    conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES ('RULED00001', %s, 'SALT_SOLVATE', true, %s), "
        "       ('UNRULED001', %s, 'SALT_SOLVATE', NULL, %s), "
        "       ('UNRULED001', %s, 'SALT_SOLVATE', NULL, %s)",
        (active, gsrs_run, active, gsrs_run, other, gsrs_run))
    rows = conn.execute(
        "SELECT substance_unii, component_count "
        "FROM drugref.gap_unruled_composition_activity").fetchall()
    assert rows == [("UNRULED001", 2)]


def test_the_gap_views_grain_is_the_gap_keys_grain(conn, gsrs_run, two_moieties):
    """Standing rule (#41): a view grouping more coarsely than its key folds two
    gaps onto one immortal question_uuid. The key is the composite; so is the grain."""
    active, other = two_moieties
    conn.execute(
        "INSERT INTO drugref.substance_composition "
        "(substance_unii, component_moiety, relation, is_active_component, ingest_run) "
        "VALUES ('UNRULED001', %s, 'SALT_SOLVATE', NULL, %s), "
        "       ('UNRULED002', %s, 'SALT_SOLVATE', NULL, %s)",
        (active, gsrs_run, other, gsrs_run))
    keys = conn.execute(
        "SELECT substance_unii FROM drugref.gap_unruled_composition_activity "
        "ORDER BY substance_unii").fetchall()
    assert [k[0] for k in keys] == ["UNRULED001", "UNRULED002"]

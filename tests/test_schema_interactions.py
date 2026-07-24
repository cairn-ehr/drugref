# tests/test_schema_interactions.py
"""Schema-level guarantees for the slice-5a interaction table.

`class_contraindication` is drugref's first drug-drug interaction data: MED-RT
CI_MoA / CI_PE ("contraindicated mechanism/physiological-effect of a
co-administered ingredient"). Like the slice-2a edge tables it is a REBUILDABLE
PROJECTION of MED-RT -- CHECK/FK/PK in the DB, but deliberately NO append-only
floor, or a re-ingest of a newer MED-RT release could not replace its rows.

These pin the decisions easiest to regress: the CHECK that keeps `relationship`
to exactly the two co-administered-ingredient predicates whose object is an
already-ingested MED-RT class (not membership, not the MeSH-keyed CI/indication
predicates that are slice 5b), the source CHECK, and the read-time pair-expansion
view.
"""
import uuid

import pytest
import psycopg

from drugref import db, ids


def _run(conn, source="MED-RT"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _class(conn, run_id, code, cty="MoA", name="Test Class [MoA]"):
    cu = ids.mint_class_uuid("MED-RT", code)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, published_code, class_name, concept_type, "
        " first_seen_ingest) VALUES (%s, 'MED-RT', %s, %s, %s, %s, %s)",
        (cu, code, code, name, cty, run_id))
    return cu


def _moiety(conn, run_id, name="testium"):
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
                 (m, name, run_id))
    return m


def _ci(conn, run_id, moiety, klass, relationship="CI_MoA", source="MED-RT"):
    conn.execute(
        "INSERT INTO drugref.class_contraindication "
        "(subject_moiety_uuid, object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s)", (moiety, klass, relationship, source, run_id))


@pytest.mark.parametrize("rel,cty", [("CI_MoA", "MoA"), ("CI_PE", "PE")])
def test_the_two_co_administered_predicates_are_accepted(conn, rel, cty):
    run_id = _run(conn)
    _ci(conn, run_id, _moiety(conn, run_id), _class(conn, run_id, "N0000000001", cty), rel)


@pytest.mark.parametrize("rel", ["has_MoA", "may_treat", "CI_with", "CI_ChemClass", "nonsense"])
def test_other_relationships_are_rejected(conn, rel):
    """Membership (has_*), indications (may_treat), and the MeSH-keyed CI predicates
    (CI_with / CI_ChemClass -> slice 5b) are not this table's business.

    Enforced by the ci_axis foreign key: a predicate is admissible exactly when it
    has been given a membership axis to expand over."""
    run_id = _run(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _ci(conn, run_id, _moiety(conn, run_id), _class(conn, run_id, "N0000000002"), rel)


def test_admitting_a_predicate_requires_declaring_its_membership_axis(conn):
    """The coupling that used to be a comment. db/004 mapped CI predicates to
    membership axes in a CASE inside the view, while the CHECK that admitted them
    lived on the table -- two lists kept in step by the word "COUPLED" in a comment.
    Widening the CHECK without the CASE (exactly what slice 5b needs to do) inserted
    rows that expanded to ZERO pairs, silently: an unmapped CASE arm yields NULL and
    `m.relationship = NULL` joins nothing.

    Now the axis table IS the vocabulary, so a predicate cannot be admitted without
    saying what it expands over -- and once it is, the view picks it up for free.
    """
    run_id = _run(conn)
    m, c = _moiety(conn, run_id), _class(conn, run_id, "N0000000021", "MoA")
    # Registering the axis is the whole act of adding a predicate.
    conn.execute("INSERT INTO drugref.ci_axis (relationship, membership_relationship) "
                 "VALUES ('CI_ChemClass', 'has_MoA')")
    _ci(conn, run_id, m, c, "CI_ChemClass")
    from drugref import classes as class_writer
    partner = _moiety(conn, run_id, "partnerium")
    class_writer.add_membership(conn, partner, c, "has_MoA", run_id)
    assert conn.execute(
        "SELECT partner_moiety FROM drugref.ddi_candidate_pair "
        "WHERE subject_moiety = %s AND relationship = 'CI_ChemClass'",
        (m,)).fetchall() == [(partner,)]


def test_two_sources_may_each_assert_the_same_contraindication(conn):
    """`source` belongs in the key. Without it, a second authority confirming a
    contraindication MED-RT already recorded was swallowed by ON CONFLICT DO
    NOTHING -- and a routine MED-RT rebuild then deleted the shared row, taking the
    other source's independent assertion with it.

    MED-RT is still the only authority the production CHECK admits (slice 5c is
    what adds one), so the second source is admitted only inside this test's
    transaction, which the conn fixture rolls back. Widening the CHECK for real
    without this PK would be the breaking change; that is the point of fixing it
    while the table holds one source's data.
    """
    from drugref import interactions
    conn.execute("ALTER TABLE drugref.class_contraindication "
                 "DROP CONSTRAINT class_contraindication_source")
    conn.execute("ALTER TABLE drugref.class_contraindication "
                 "ADD CONSTRAINT class_contraindication_source "
                 "CHECK (source IN ('MED-RT', 'MeSH'))")
    medrt_run, mesh_run = _run(conn, "MED-RT"), _run(conn, "MeSH")
    m, c = _moiety(conn, medrt_run), _class(conn, medrt_run, "N0000000022")
    assert interactions.add_contraindication(conn, m, c, "CI_MoA", "MED-RT", medrt_run) is True
    assert interactions.add_contraindication(conn, m, c, "CI_MoA", "MeSH", mesh_run) is True
    interactions.clear_source_contraindications(conn, "MED-RT")
    assert conn.execute(
        "SELECT source FROM drugref.class_contraindication "
        "WHERE subject_moiety_uuid = %s", (m,)).fetchall() == [("MeSH",)]


def test_source_must_be_a_known_authority(conn):
    """Widened per source exactly as substance_class.source was; MED-RT only today."""
    run_id = _run(conn, source="MED-RT")
    with pytest.raises(psycopg.errors.CheckViolation):
        _ci(conn, run_id, _moiety(conn, run_id), _class(conn, run_id, "N0000000003"),
            source="DrugBank")


def test_the_by_object_index_exists(conn):
    """Pair expansion is driven from the object (co-administered drug's) class, so
    that is the indexed read direction."""
    assert conn.execute(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'drugref' "
        "AND indexname = 'class_contraindication_by_object'").fetchone() is not None


def test_the_table_is_deletable_because_it_is_a_rebuildable_projection(conn):
    """No append-only floor: re-ingesting a newer MED-RT release depends on being
    able to DELETE this source's prior contraindications."""
    run_id = _run(conn)
    m, c = _moiety(conn, run_id), _class(conn, run_id, "N0000000004")
    _ci(conn, run_id, m, c)
    conn.execute("DELETE FROM drugref.class_contraindication "
                 "WHERE subject_moiety_uuid = %s", (m,))
    assert conn.execute("SELECT count(*) FROM drugref.class_contraindication "
                        "WHERE subject_moiety_uuid = %s", (m,)).fetchone()[0] == 0


def test_a_contraindication_is_not_duplicated(conn):
    """PK (subject, object, relationship): the same assertion twice is one row."""
    run_id = _run(conn)
    m, c = _moiety(conn, run_id), _class(conn, run_id, "N0000000005")
    _ci(conn, run_id, m, c)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _ci(conn, run_id, m, c)


def test_ddi_candidate_pair_view_exists_with_the_expected_shape(conn):
    """The read-time expansion of class-level rules into concrete drug pairs.

    The pair columns are named for their ROLES. `moiety_a`/`moiety_b` read as an
    unordered pair, but this view is strictly directional -- moiety_a was the drug
    the contraindication is ABOUT -- so a consumer querying one direction and
    concluding "no interaction" was the likeliest way to misread it.
    """
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = 'ddi_candidate_pair'").fetchall()}
    assert {"subject_moiety", "partner_moiety", "relationship", "via_class"} <= cols
    assert "moiety_a" not in cols


def test_the_view_surfaces_which_release_the_advice_came_from(conn):
    """Staleness has to be answerable from the read path. The design's own safety
    argument is that MED-RT is a structural seed rather than a current evidence
    feed, but the view exposed only the opaque `ingest_run` bigint -- so a consumer
    could not tell how old the advice was without joining a table the view never
    mentions."""
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = 'ddi_candidate_pair'").fetchall()}
    assert {"upstream_release", "ingested_at"} <= cols


def test_the_directional_contract_is_documented_in_the_catalog(conn):
    """Every caveat this projection carries lived in `--` comments in the migration
    file, which Postgres strips: `\\d+` showed a view named like an unordered pair
    with empty descriptions. A consumer inspecting the database has to be able to
    see the contract."""
    comment = conn.execute(
        "SELECT obj_description('drugref.ddi_candidate_pair'::regclass, 'pg_class')"
    ).fetchone()[0]
    assert comment and "directional" in comment.lower()


def test_migrations_replay_is_idempotent(conn):
    """db/004 is guarded (CREATE ... IF NOT EXISTS / CREATE OR REPLACE VIEW), so
    replaying the whole db/ directory over an already-migrated database is a no-op,
    never an error -- the same property db/003 has."""
    db.apply_migrations(conn)  # a second application must not raise
    assert conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'drugref' "
        "AND table_name = 'class_contraindication'").fetchone()[0] == 1

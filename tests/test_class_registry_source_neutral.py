# tests/test_class_registry_source_neutral.py
"""The class registry must hold classes from MORE THAN ONE upstream authority.

Slice 2a built the registry around MED-RT and named its columns after it
(`medrt_nui`, `medrt_code`), and `mint_class_uuid` hard-coded the "MEDRT:" key
prefix. Slice 2b adds MeSH Pharmacological Actions -- a second authority whose
concepts have a MeSH descriptor UI (e.g. "D000894"), not a MED-RT NUI -- so the
registry has to become source-neutral before a second source can enter it.

The single hard constraint on that generalisation, and the reason most of these
tests exist: **every class_uuid MED-RT already minted must come back byte for
byte identical.** Class UUIDs are the join key of `class_parent` and
`class_membership`, and the whole projection is dropped and rebuilt on each
ingest. If the derivation drifts, a rebuild silently re-keys 3,634 classes and
orphans every edge pointing at the old UUIDs -- with no error anywhere.
"""
import uuid

import pytest
import psycopg

from drugref import ids


# ---- identity: generalise the key WITHOUT moving any existing UUID ----------

# The values MED-RT classes actually carry today, captured from the slice-2a
# implementation before this refactor. These are frozen literals on purpose: a
# test that re-derives the expectation with the same expression as the
# implementation drifts along with it and would stay green through exactly the
# regression that matters here (cf. test_ids.py's namespace literals).
MEDRT_FROZEN = {
    "N0000175722": "84a81016-7abe-5716-bf37-2f949fcabf0b",
    "N0000008836": "0e3614ce-91cd-551e-aa26-4bebd8eb4487",
    "N0000000001": "878e55c4-fe56-5641-8a34-f0020a94a501",
}


@pytest.mark.parametrize("nui,expected", sorted(MEDRT_FROZEN.items()))
def test_existing_medrt_class_uuids_are_unchanged_by_the_generalisation(nui, expected):
    """The regression guard for the whole refactor: MED-RT keeps its UUIDs."""
    assert str(ids.mint_class_uuid("MED-RT", nui)) == expected


def test_mint_class_uuid_is_deterministic_per_source():
    assert ids.mint_class_uuid("MESH", "D000894") == ids.mint_class_uuid("MESH", "D000894")


def test_the_same_code_from_different_sources_is_a_different_class():
    """Source is part of the key, so an accidental code collision between two
    authorities can never merge two unrelated classes into one row."""
    assert ids.mint_class_uuid("MESH", "D000894") != ids.mint_class_uuid("MED-RT", "D000894")


def test_source_and_code_are_normalised():
    """Both arrive as XML text nodes; incidental case/whitespace must not fork
    identity the way it must not for a UNII or an NUI."""
    assert ids.mint_class_uuid("  mesh ", " d000894 ") == ids.mint_class_uuid("MESH", "D000894")


def test_source_name_variants_do_not_fork_medrt_identity():
    """`MED-RT` is the ingest_run.source spelling; the UUID key has always been the
    unhyphenated "MEDRT:". Both spellings must land on the same class, or a rebuild
    re-keys the registry."""
    assert ids.mint_class_uuid("MEDRT", "N0000175722") == ids.mint_class_uuid("MED-RT", "N0000175722")


def test_class_uuids_still_cannot_collide_with_moiety_uuids():
    assert ids.mint_class_uuid("MESH", "X") != ids.mint_moiety_uuid("X")


def test_canonical_source_folds_incidental_spellings():
    """The stored substance_class.source and the class_uuid key are minted from the
    SAME canonicalisation, so they cannot drift; canonical_source is that fold. The
    hyphen-less UUID-key spelling and case/whitespace slips all reach one string."""
    assert ids.canonical_source("MEDRT") == "MED-RT"
    assert ids.canonical_source("  med-rt ") == "MED-RT"
    assert ids.canonical_source("MESH") == "MeSH"
    assert ids.canonical_source(" mesh ") == "MeSH"


def test_an_unlisted_authority_still_folds_to_one_spelling():
    """The invariant is 'stored source and UUID key derive from ONE fold', and it
    has to hold for an authority that has not been added to _SOURCE_CANONICAL yet
    -- because the db CHECK is widened in a migration and the Python table in a
    separate edit, so there is always a window where a source is accepted by the
    database but unlisted here.

    The UUID key upper-cases; while the fallback preserved case, three spellings of
    one new authority minted ONE class_uuid but stored THREE different `source`
    strings, and upsert_class's ON CONFLICT never corrects the stored one -- so a
    per-source rebuild would silently miss rows it owns.
    """
    folded = {ids.canonical_source(s) for s in ("RxClass", "RXCLASS", " rxclass ")}
    assert folded == {"RXCLASS"}
    # ...and that one spelling is the same string the class_uuid key is built from.
    assert ids.mint_class_uuid("RxClass", "C1") == ids.mint_class_uuid("RXCLASS", "C1")


# ---- schema: the registry accepts a second authority ------------------------


def _run(conn, source="MeSH"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _insert(conn, run_id, source, code, cty, name="Test Class"):
    """Insert a class row directly. `source` must be a canonical spelling, because
    the db/003 CHECK now refuses any other -- the same discipline classes.upsert_class
    enforces in code via ids.canonical_source."""
    cu = ids.mint_class_uuid(source, code)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, published_code, class_name, concept_type, "
        " first_seen_ingest) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (cu, source, code, code, name, cty, run_id))
    return cu


def test_a_mesh_pharmacologic_action_is_a_valid_class(conn):
    """'PA' is MeSH's classification axis and must be accepted alongside MED-RT's."""
    cu = _insert(conn, _run(conn), "MeSH", "D000894", "PA", "Anti-Inflammatory Agents")
    assert conn.execute(
        "SELECT source, concept_type FROM drugref.substance_class WHERE class_uuid = %s",
        (cu,)).fetchone() == ("MeSH", "PA")


def test_has_pa_is_a_valid_membership_relationship(conn):
    run_id = _run(conn)
    cu = _insert(conn, run_id, "MeSH", "D000895", "PA")
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, 'testium', %s)",
                 (m, run_id))
    conn.execute("INSERT INTO drugref.class_membership "
                 "(moiety_uuid, class_uuid, relationship, ingest_run) VALUES (%s, %s, %s, %s)",
                 (m, cu, "has_PA", run_id))
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_membership "
        "WHERE class_uuid = %s AND relationship = 'has_PA'", (cu,)).fetchone()[0] == 1


def test_two_sources_may_publish_the_same_code(conn):
    """Uniqueness is per (source, source_code), not global -- MeSH 'D000894' and a
    hypothetical MED-RT 'D000894' are different classes and must coexist."""
    run_id = _run(conn)
    _insert(conn, run_id, "MeSH", "D0000AA", "PA")
    _insert(conn, run_id, "MED-RT", "D0000AA", "MoA")
    assert conn.execute(
        "SELECT count(*) FROM drugref.substance_class WHERE source_code = 'D0000AA'"
    ).fetchone()[0] == 2


def test_one_source_may_not_publish_a_code_twice(conn):
    """The per-source uniqueness that replaced the old global UNIQUE on medrt_nui."""
    run_id = _run(conn)
    _insert(conn, run_id, "MeSH", "D0000BB", "PA")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO drugref.substance_class "
            "(class_uuid, source, source_code, published_code, class_name, concept_type, "
            " first_seen_ingest) VALUES (%s, 'MeSH', 'D0000BB', 'D0000BB', 'Dup', 'PA', %s)",
            (uuid.uuid4(), run_id))


def test_a_noncanonical_source_spelling_is_refused_by_the_db(conn):
    """The db/003 CHECK is the floor under ids.canonical_source: a row that reached
    the table under a second spelling of one authority ("MESH" beside "MeSH") would
    share a class_uuid yet answer a per-source query under only one of the two, so
    the database refuses the spelling outright rather than trusting every writer to
    canonicalise first."""
    run_id = _run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.substance_class "
            "(class_uuid, source, source_code, published_code, class_name, concept_type, "
            " first_seen_ingest) VALUES (%s, 'MESH', 'D0000DD', 'D0000DD', 'Bad Spelling', 'PA', %s)",
            (uuid.uuid4(), run_id))


def test_the_per_source_index_exists(conn):
    """db/003 step 6: per-source rebuilds ask 'which classes are this authority's?',
    so the source column must be indexed like the other query paths in db/002."""
    assert conn.execute(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'drugref' "
        "AND indexname = 'substance_class_by_source'").fetchone() is not None


def test_a_class_must_declare_which_authority_defined_it(conn):
    """Without a source column the registry cannot answer 'whose class is this?',
    which is what makes a per-source rebuild possible at all."""
    run_id = _run(conn)
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            "INSERT INTO drugref.substance_class "
            "(class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) "
            "VALUES (%s, NULL, 'D0000CC', 'No Source', 'PA', %s)", (uuid.uuid4(), run_id))

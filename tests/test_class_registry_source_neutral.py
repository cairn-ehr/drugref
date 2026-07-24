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


# ---- schema: the registry accepts a second authority ------------------------


def _run(conn, source="MeSH"):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES (%s, 'test', 'deadbeef') RETURNING ingest_run_id", (source,)).fetchone()[0]


def _insert(conn, run_id, source, code, cty, name="Test Class"):
    cu = ids.mint_class_uuid(source, code)
    conn.execute(
        "INSERT INTO drugref.substance_class "
        "(class_uuid, source, source_code, published_code, class_name, concept_type, "
        " first_seen_ingest) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (cu, source, code, code, name, cty, run_id))
    return cu


def test_a_mesh_pharmacologic_action_is_a_valid_class(conn):
    """'PA' is MeSH's classification axis and must be accepted alongside MED-RT's."""
    _insert(conn, _run(conn), "MESH", "D000894", "PA", "Anti-Inflammatory Agents")


def test_has_pa_is_a_valid_membership_relationship(conn):
    run_id = _run(conn)
    cu = _insert(conn, run_id, "MESH", "D000895", "PA")
    m = uuid.uuid4()
    conn.execute("INSERT INTO drugref.substance_moiety "
                 "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, 'testium', %s)",
                 (m, run_id))
    conn.execute("INSERT INTO drugref.class_membership "
                 "(moiety_uuid, class_uuid, relationship, ingest_run) VALUES (%s, %s, %s, %s)",
                 (m, cu, "has_PA", run_id))


def test_two_sources_may_publish_the_same_code(conn):
    """Uniqueness is per (source, source_code), not global -- MeSH 'D000894' and a
    hypothetical MED-RT 'D000894' are different classes and must coexist."""
    run_id = _run(conn)
    _insert(conn, run_id, "MESH", "D0000AA", "PA")
    _insert(conn, run_id, "MED-RT", "D0000AA", "MoA")


def test_one_source_may_not_publish_a_code_twice(conn):
    """The per-source uniqueness that replaced the old global UNIQUE on medrt_nui."""
    run_id = _run(conn)
    _insert(conn, run_id, "MESH", "D0000BB", "PA")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO drugref.substance_class "
            "(class_uuid, source, source_code, published_code, class_name, concept_type, "
            " first_seen_ingest) VALUES (%s, 'MESH', 'D0000BB', 'D0000BB', 'Dup', 'PA', %s)",
            (uuid.uuid4(), run_id))


def test_a_class_must_declare_which_authority_defined_it(conn):
    """Without a source column the registry cannot answer 'whose class is this?',
    which is what makes a per-source rebuild possible at all."""
    run_id = _run(conn)
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            "INSERT INTO drugref.substance_class "
            "(class_uuid, source, source_code, class_name, concept_type, first_seen_ingest) "
            "VALUES (%s, NULL, 'D0000CC', 'No Source', 'PA', %s)", (uuid.uuid4(), run_id))

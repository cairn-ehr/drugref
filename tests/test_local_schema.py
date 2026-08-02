# tests/test_local_schema.py
"""db/009: the local (jurisdiction-specific) tier's three tables.

These are REBUILDABLE PROJECTIONS, deliberately outside slice 1's append-only
floor: PBS re-lists monthly and a de-listed item must be able to disappear,
which an insert-only merge can never express (spec section 3).
"""
import pytest


def test_local_product_round_trips(conn, ingest_run_id):
    """A product row inserts and reads back under its deterministic UUID."""
    from drugref import ids
    product_uuid = ids.mint_local_product_uuid("AU", "PBS", "10001J_14023")
    conn.execute(
        "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, source, "
        "source_code, pbs_code, brand_name, drug_name, form_strength, program_code, "
        "benefit_type_code, ingest_run) "
        "VALUES (%s, 'AU', 'PBS', '10001J_14023', '10001J', 'Xifaxan', 'Rifaximin', "
        "'Tablet 550 mg', 'GE', 'A', %s)",
        (product_uuid, ingest_run_id))
    row = conn.execute(
        "SELECT drug_name, benefit_type_code FROM drugref.local_product "
        "WHERE local_product_uuid = %s", (product_uuid,)).fetchone()
    assert row == ("Rifaximin", "A")


def test_ingest_run_accepts_pbs_source(conn):
    """db/005 CHECK-constrains ingest_run.source; 009 must widen it for PBS."""
    run_id = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('PBS', '2026-07-01', 'x', 'pbs_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    assert run_id > 0


def test_local_product_rejects_unknown_jurisdiction(conn, ingest_run_id):
    """jurisdiction is CHECK-constrained, like every other rebuild-scoping key."""
    from drugref import ids
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, source, "
            "source_code, ingest_run) VALUES (%s, 'XX', 'PBS', 'c', %s)",
            (ids.mint_local_product_uuid("XX", "PBS", "c"), ingest_run_id))


def test_bridge_requires_a_real_moiety(conn, ingest_run_id):
    """The bridge is FK'd to substance_moiety: it can never point at a ghost."""
    import uuid
    from drugref import ids
    product_uuid = ids.mint_local_product_uuid("AU", "PBS", "solo")
    conn.execute(
        "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, source, "
        "source_code, ingest_run) VALUES (%s, 'AU', 'PBS', 'solo', %s)",
        (product_uuid, ingest_run_id))
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO drugref.local_product_moiety (local_product_uuid, moiety_uuid, "
            "component_name, match_method, ingest_run) VALUES (%s, %s, 'x', 'exact', %s)",
            (product_uuid, uuid.uuid4(), ingest_run_id))


def test_match_method_vocabulary_is_closed(conn, ingest_run_id, a_moiety):
    """match_method separates the salt-strip heuristic from exact matches, so a
    consumer can discard it (spec 5.1). A CHECK keeps the vocabulary honest."""
    from drugref import ids
    product_uuid = ids.mint_local_product_uuid("AU", "PBS", "mm")
    conn.execute(
        "INSERT INTO drugref.local_product (local_product_uuid, jurisdiction, source, "
        "source_code, ingest_run) VALUES (%s, 'AU', 'PBS', 'mm', %s)",
        (product_uuid, ingest_run_id))
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO drugref.local_product_moiety (local_product_uuid, moiety_uuid, "
            "component_name, match_method, ingest_run) VALUES (%s, %s, 'x', 'guessed', %s)",
            (product_uuid, a_moiety, ingest_run_id))

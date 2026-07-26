# tests/test_local_writer.py
"""The single writer for the local tier -- mirrors classes.py's role.

The discipline it enforces is the REBUILDABLE-PROJECTION one: clear_source_*
deliberately DELETEs, because a de-listed PBS item must be able to disappear.
"""
import dataclasses

from drugref import local
from drugref.ingest.pbs import PbsItem

ITEM = PbsItem(source_code="10001J_14023", pbs_code="10001J", brand_name="Xifaxan",
               drug_name="Rifaximin", form_strength="Tablet 550 mg",
               program_code="GE", benefit_type_code="A")


def test_upsert_product_is_idempotent(conn, ingest_run_id):
    """Re-ingesting the same release must not duplicate: the UUID is derived."""
    first = local.upsert_product(conn, ITEM, ingest_run_id)
    second = local.upsert_product(conn, ITEM, ingest_run_id)
    assert first == second
    count = conn.execute("SELECT count(*) FROM drugref.local_product").fetchone()[0]
    assert count == 1


def test_upsert_product_refreshes_mutable_fields(conn, ingest_run_id):
    """Price-adjacent attributes churn monthly; identity does not."""
    product_uuid = local.upsert_product(conn, ITEM, ingest_run_id)
    renamed = dataclasses.replace(ITEM, brand_name="Xifaxan XL")
    assert local.upsert_product(conn, renamed, ingest_run_id) == product_uuid
    brand = conn.execute(
        "SELECT brand_name FROM drugref.local_product WHERE local_product_uuid = %s",
        (product_uuid,)).fetchone()[0]
    assert brand == "Xifaxan XL"


def test_add_product_moiety_reports_insert_vs_conflict(conn, ingest_run_id, a_moiety):
    product_uuid = local.upsert_product(conn, ITEM, ingest_run_id)
    assert local.add_product_moiety(
        conn, product_uuid, a_moiety, "rifaximin", "exact", ingest_run_id) is True
    assert local.add_product_moiety(
        conn, product_uuid, a_moiety, "rifaximin", "exact", ingest_run_id) is False


def test_clear_source_products_removes_bridge_and_products(conn, ingest_run_id, a_moiety):
    """A rebuild must clear the bridge FIRST or the FK blocks the product delete."""
    product_uuid = local.upsert_product(conn, ITEM, ingest_run_id)
    local.add_product_moiety(conn, product_uuid, a_moiety, "rifaximin", "exact", ingest_run_id)
    local.add_unmatched_components(conn, [("10001J_14023", "mystery")], ingest_run_id)
    local.clear_source_products(conn, "PBS")
    for table in ("local_product", "local_product_moiety", "local_unmatched_ingredient"):
        assert conn.execute(f"SELECT count(*) FROM drugref.{table}").fetchone()[0] == 0


def test_add_unmatched_components_batches(conn, ingest_run_id):
    written = local.add_unmatched_components(
        conn, [("a", "foo"), ("b", "bar")], ingest_run_id)
    assert written == 2
    assert conn.execute(
        "SELECT count(*) FROM drugref.local_unmatched_ingredient").fetchone()[0] == 2

# tests/test_source_clear_contract.py
"""The per-source clear is what makes "rebuildable projection" true (#43).

Six writers dropped a source's rows with six copies of one DELETE, differing only
in the table list. That duplication has already produced a real defect once: the
final slice-5b review found `ingest_unresolved_ci_object` missing from a test's
assertions, and removing a table from one of those tuples is a ONE-TOKEN edit with
no local signal that anything is wrong -- the ingest still succeeds, and a gap view
that sums across runs then multiplies its curator-facing counts on every re-ingest
(405 -> 810 -> 1,215) with nothing failing.

So the SQL now lives once, in db.clear_source_tables, and each writer declares only
WHICH tables it owns. This module pins both halves:

* the shared helper -- per-source scoping, and that it deletes in the ORDER given
  (load-bearing: local_product_moiety references local_product, so a parent-first
  order is refused by the foreign key);
* every writer's declared table tuple, restated here INDEPENDENTLY rather than
  imported. Driving the expectation off the constant would pass whatever the
  constant said; restating it is what makes a DROPPED table fail, which is the
  defect above. (Same reasoning as test_medrt_parser's REDISTRIBUTABLE_NAMESPACES.)

The rows themselves are still proved to disappear by each writer's own tests --
test_schema_classes, test_interactions, test_conditions_writer, test_local_writer,
test_gap_views. This module is about the contract those tests cannot see.
"""
import psycopg
import pytest

from drugref import classes, conditions, db, interactions, local
from drugref.ingest.pbs import PbsItem

# A local product and a bridge row on it: the real FK pair the ORDER tests need.
ITEM = PbsItem(source_code="10001J_14023", pbs_code="10001J", brand_name="Xifaxan",
               drug_name="Rifaximin", form_strength="Tablet 550 mg",
               program_code="GE", benefit_type_code="A")

# What each writer owns, restated. A table added to a writer without being added
# here fails; a table dropped from a writer fails too. Both directions matter.
EXPECTED_TABLES = {
    "classes.CLASS_EDGE_TABLES": (
        classes.CLASS_EDGE_TABLES, ("class_membership", "class_parent")),
    "classes.UNMATCHED_INGREDIENT_TABLES": (
        classes.UNMATCHED_INGREDIENT_TABLES, ("ingest_unmatched_ingredient",)),
    "conditions.CONDITION_EDGE_TABLES": (
        conditions.CONDITION_EDGE_TABLES, ("condition_parent",)),
    "interactions.CONTRAINDICATION_TABLES": (
        interactions.CONTRAINDICATION_TABLES, ("class_contraindication",)),
    "interactions.MESH_CONTRAINDICATION_TABLES": (
        interactions.MESH_CONTRAINDICATION_TABLES,
        ("moiety_condition_contraindication", "moiety_contraindication",
         "ingest_unresolved_ci_object")),
    "local.LOCAL_PRODUCT_TABLES": (
        local.LOCAL_PRODUCT_TABLES,
        ("local_product_moiety", "local_unmatched_ingredient", "local_product")),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_TABLES))
def test_the_declared_table_tuple_is_what_it_should_be(name):
    declared, expected = EXPECTED_TABLES[name]
    assert declared == expected


def test_the_mesh_contraindication_clear_still_covers_the_worklist():
    """Called out on its own because it is the entry that WAS lost once.

    ingest_unresolved_ci_object is not a contraindication relation, so it reads as
    the odd one out in that tuple -- and it is exactly the one whose omission is
    invisible: the two relations rebuild correctly, the ingest succeeds, and only
    gap_unresolved_ci_object's rule counts creep upward release after release.
    """
    assert "ingest_unresolved_ci_object" in interactions.MESH_CONTRAINDICATION_TABLES


def test_the_local_clear_lists_children_before_parents():
    """Order is part of local's tuple, not an accident of how it was written:
    local_product_moiety and local_unmatched_ingredient both reference
    local_product, so a parent-first list is refused by the foreign key."""
    tables = local.LOCAL_PRODUCT_TABLES
    assert tables.index("local_product") == len(tables) - 1


# ---- the shared helper -------------------------------------------------------


def _a_product_and_its_bridge_row(conn, ingest_run_id, moiety_uuid):
    """One local_product with one local_product_moiety hanging off it.

    drugref's own tables, not a temp pair: this FK is the reason the order matters
    at all, so testing the contract against a stand-in would prove the stand-in.
    """
    product_uuid = local.upsert_product(conn, ITEM, ingest_run_id)
    local.add_product_moiety(conn, product_uuid, moiety_uuid, "rifaximin",
                             "exact", ingest_run_id)


def test_clear_source_tables_deletes_in_the_order_given(conn, ingest_run_id, a_moiety):
    """Children first succeeds. If the helper reordered the tables -- sorted them,
    or took a set -- the local rebuild would break on a live database and nowhere
    else, because no other writer's tuple has an FK inside it."""
    _a_product_and_its_bridge_row(conn, ingest_run_id, a_moiety)

    db.clear_source_tables(conn, ("local_product_moiety", "local_product"), "PBS")
    for table in ("local_product", "local_product_moiety"):
        assert conn.execute(f"SELECT count(*) FROM drugref.{table}").fetchone()[0] == 0


def test_clear_source_tables_refuses_a_parent_first_order(conn, ingest_run_id, a_moiety):
    """The other half of the same fact: the order is not cosmetic."""
    _a_product_and_its_bridge_row(conn, ingest_run_id, a_moiety)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.clear_source_tables(conn, ("local_product", "local_product_moiety"), "PBS")


def test_clear_source_tables_scopes_the_delete_to_one_source(conn, ingest_run_id):
    """THE PROPERTY THE WHOLE REBUILDABLE-PROJECTION MODEL RESTS ON: a MED-RT
    re-ingest must not remove another feed's rows. Six independent restatements of
    this DELETE were six chances for one of them to quietly stop being per-source.

    `ingest_run_id` is a PBS run (see conftest); the second run below is MED-RT, so
    clearing MED-RT must leave the PBS product standing.
    """
    local.upsert_product(conn, ITEM, ingest_run_id)
    medrt = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('MED-RT', 'r1', 'test') RETURNING ingest_run_id").fetchone()[0]

    db.clear_source_tables(conn, ("local_product",), "MED-RT")
    assert conn.execute(
        "SELECT ingest_run FROM drugref.local_product").fetchall() == [(ingest_run_id,)]
    assert medrt                     # the run exists; it simply owns no product rows

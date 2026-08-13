# tests/test_source_clear_contract.py
"""The per-source clear is what makes "rebuildable projection" true (#43).

Eight writers dropped a source's rows with eight copies of one DELETE, differing only
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
test_schema_classes, test_interactions, test_conditions_writer, test_indications_writer,
test_local_writer, test_gap_views. This module is about the contract those tests cannot see.
"""
import psycopg
import pytest

from drugref import classes, composition, conditions, db, indications, interactions, local
from drugref.ingest import onchigh_run
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
    "composition.COMPOSITION_TABLES": (
        composition.COMPOSITION_TABLES, ("substance_composition",)),
    "conditions.CONDITION_EDGE_TABLES": (
        conditions.CONDITION_EDGE_TABLES, ("condition_parent",)),
    "indications.INDICATION_TABLES": (
        indications.INDICATION_TABLES,
        ("moiety_condition_indication", "moiety_induced_condition")),
    "interactions.CONTRAINDICATION_TABLES": (
        interactions.CONTRAINDICATION_TABLES, ("class_contraindication",)),
    # Task 10 (db/032, design spec section 14): the class-subject grain's own
    # candidate table, cleared by a SEPARATE function
    # (clear_source_class_pair_contraindications) from the moiety-grain one
    # above -- two relations, because a class-subject rule and a
    # moiety-subject rule are different kinds of statement (db/032's own
    # preamble), so dropping either table from its writer's tuple must fail
    # loudly here rather than letting a rebuild quietly stop covering it.
    "interactions.CLASS_PAIR_CONTRAINDICATION_TABLES": (
        interactions.CLASS_PAIR_CONTRAINDICATION_TABLES,
        ("class_pair_contraindication",)),
    "interactions.MESH_CONTRAINDICATION_TABLES": (
        interactions.MESH_CONTRAINDICATION_TABLES,
        ("moiety_condition_contraindication", "moiety_contraindication",
         "ingest_unresolved_ci_object")),
    "local.LOCAL_PRODUCT_TABLES": (
        local.LOCAL_PRODUCT_TABLES,
        ("local_product_moiety", "local_unmatched_ingredient", "local_product")),
    # Slice 5c.2, gap kind fifteen: onchigh_run's OWN worklist, distinct from
    # interactions.CONTRAINDICATION_TABLES above (which it also clears, for
    # class_contraindication -- ONCHIGH is a second source sharing that table,
    # not a second table). Added when the ONCHIGH write half (Task 5) first
    # gave this contract a fifteenth writer to restate -- see that task's
    # commit message for why this entry, and not a silent gap, is the correct
    # outcome of adding one.
    "onchigh_run.UNRESOLVED_ENDPOINT_TABLES": (
        onchigh_run.UNRESOLVED_ENDPOINT_TABLES, ("ingest_unresolved_onc_endpoint",)),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_TABLES))
def test_the_declared_table_tuple_is_what_it_should_be(name):
    declared, expected = EXPECTED_TABLES[name]
    assert declared == expected


def test_the_reason_vocabulary_is_what_it_should_be():
    """Restated independently, like every writer's table tuple above. A fourth value
    was added by #47; EXACTLY ONE WRITER PER (source, reason) is what makes them safe,
    so a value appearing here without a writer -- or a writer sharing one -- is the
    defect this pins."""
    assert classes.REASONS == ("classification", "contraindication", "indication",
                               "contraindication_class")


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


def test_clear_source_tables_can_narrow_to_one_writers_rows(conn, ingest_run_id):
    """#39. Two orchestrators write ingest_unmatched_ingredient under source 'MED-RT'
    -- medrt_run the ingredients MED-RT CLASSIFIES that no moiety carries, mesh_rel_run
    the SUBJECTS of a contraindication that no moiety carries. Neither set contains
    the other, so a source-scoped clear let whichever ran last delete the other's
    rows and be unable to re-add them.

    `match` narrows the same one DELETE to the writer's own bucket. It is a Mapping
    rather than a second positional string so the call site says WHICH column it is
    narrowing on -- the reason a bare extra argument would not.
    """
    medrt = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'r1', 'test', 'medrt_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    for reason in ("classification", "contraindication"):
        conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                     "(ingest_run, rxcui, name, reason) VALUES (%s, '5640', 'x', %s)",
                     (medrt, reason))

    db.clear_source_tables(conn, ("ingest_unmatched_ingredient",), "MED-RT",
                           match={"reason": "classification"})

    assert conn.execute(
        "SELECT reason FROM drugref.ingest_unmatched_ingredient").fetchall() == \
        [("contraindication",)]


def test_an_unnarrowed_clear_still_takes_everything(conn, ingest_run_id):
    """The narrowing is OPT-IN: seven of the eight writers own their whole table for a
    source and must keep clearing it wholesale. Pinned so the default cannot drift
    into "clears nothing unless asked", which fails silently -- the projection simply
    grows a little on every ingest."""
    medrt = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'r1', 'test', 'medrt_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    for reason in ("classification", "contraindication"):
        conn.execute("INSERT INTO drugref.ingest_unmatched_ingredient "
                     "(ingest_run, rxcui, name, reason) VALUES (%s, '5640', 'x', %s)",
                     (medrt, reason))

    db.clear_source_tables(conn, ("ingest_unmatched_ingredient",), "MED-RT")

    assert conn.execute("SELECT count(*) FROM "
                        "drugref.ingest_unmatched_ingredient").fetchone()[0] == 0


def test_clear_source_tables_scopes_the_delete_to_one_source(conn, ingest_run_id):
    """THE PROPERTY THE WHOLE REBUILDABLE-PROJECTION MODEL RESTS ON: a MED-RT
    re-ingest must not remove another feed's rows. Eight independent restatements of
    this DELETE were eight chances for one of them to quietly stop being per-source.

    `ingest_run_id` is a PBS run (see conftest); the second run below is MED-RT, so
    clearing MED-RT must leave the PBS product standing.
    """
    local.upsert_product(conn, ITEM, ingest_run_id)
    medrt = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'r1', 'test', 'medrt_run') RETURNING ingest_run_id"
    ).fetchone()[0]

    db.clear_source_tables(conn, ("local_product",), "MED-RT")
    assert conn.execute(
        "SELECT ingest_run FROM drugref.local_product").fetchall() == [(ingest_run_id,)]
    assert medrt                     # the run exists; it simply owns no product rows

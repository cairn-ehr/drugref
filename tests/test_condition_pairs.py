"""The condition read path (db/015): descendant expansion, and its opt-out."""
import pytest

from drugref import conditions, interactions
from drugref.ingest.mesh_concepts import MeshRecord


def _condition(conn, run_id, code, name, trees):
    rec = MeshRecord(concept_ui="M0", record_ui=code, record_kind="DESCRIPTOR",
                     name=name, tree_numbers=trees, unii=frozenset(),
                     cas=frozenset(), is_preferred_concept=True)
    cu, _ = conditions.upsert_condition(conn, rec, run_id, "MeSH")
    return cu


@pytest.fixture
def epilepsy_tree(conn, a_moiety, ingest_run_id):
    """Epilepsy, with one descendant, and a rule naming only the PARENT."""
    parent = _condition(conn, ingest_run_id, "D004827", "Epilepsy",
                        ("C10.228.140.490",))
    child = _condition(conn, ingest_run_id, "D004829", "Epilepsy, Generalized",
                       ("C10.228.140.490.360",))
    conditions.add_condition_parent_edge(conn, child, parent, ingest_run_id)
    interactions.add_condition_contraindication(
        conn, a_moiety, parent, "CI_with", "MED-RT", ingest_run_id)
    return {"parent": parent, "child": child, "moiety": a_moiety}


def test_a_rule_reaches_a_descendant_condition(conn, epilepsy_tree):
    """THE POINT OF THE SLICE'S READ PATH. A rule written against Epilepsy must fire
    for a patient coded Epilepsy, Generalized -- for a contraindication, fewer rows
    is the harm direction (Plan B)."""
    rows = conn.execute(
        "SELECT member_condition, is_direct FROM "
        "drugref.condition_contraindication_expanded "
        "WHERE subject_moiety = %s ORDER BY is_direct DESC",
        (epilepsy_tree["moiety"],)).fetchall()
    assert (epilepsy_tree["parent"], True) in rows
    assert (epilepsy_tree["child"], False) in rows


def test_is_direct_reproduces_the_unexpanded_set(conn, epilepsy_tree):
    """WHERE is_direct must return exactly what an unexpanded view would, so a
    precision-sensitive consumer opts out explicitly -- and one who FORGETS the
    filter errs toward recall, which is the safe direction to fail in."""
    rows = conn.execute(
        "SELECT member_condition FROM drugref.condition_contraindication_expanded "
        "WHERE subject_moiety = %s AND is_direct", (epilepsy_tree["moiety"],)
    ).fetchall()
    assert rows == [(epilepsy_tree["parent"],)]


def test_object_condition_is_what_the_rule_named(conn, epilepsy_tree):
    """The provenance column: a consumer must be able to see that the match came via
    an ancestor, not that the rule named the patient's exact condition."""
    row = conn.execute(
        "SELECT object_condition FROM drugref.condition_contraindication_expanded "
        "WHERE subject_moiety = %s AND NOT is_direct", (epilepsy_tree["moiety"],)
    ).fetchone()
    assert row == (epilepsy_tree["parent"],)


def test_expansion_is_gated_on_the_axis(conn, epilepsy_tree):
    """Switching a predicate off must need no view edit -- one UPDATE, and the
    read path stops expanding it. This is what makes 5b.2's per-predicate decision
    a data change rather than a migration."""
    conn.execute("UPDATE drugref.condition_ci_axis "
                 "SET expands_descendants = false WHERE relationship = 'CI_with'")
    rows = conn.execute(
        "SELECT member_condition FROM drugref.condition_contraindication_expanded "
        "WHERE subject_moiety = %s", (epilepsy_tree["moiety"],)).fetchall()
    assert rows == [(epilepsy_tree["parent"],)]


def test_subtree_includes_its_own_root(conn, epilepsy_tree):
    """is_direct is computed from this, so the root MUST be in its own subtree."""
    rows = conn.execute(
        "SELECT condition_uuid FROM drugref.condition_subtree WHERE root_uuid = %s",
        (epilepsy_tree["parent"],)).fetchall()
    assert (epilepsy_tree["parent"],) in rows
    assert len(rows) == 2


def test_a_condition_no_rule_names_is_absent_from_the_subtree(conn, epilepsy_tree):
    """Scoped to contraindicated conditions: computing a subtree for each of the
    registry's 5,203 conditions when only 641 are ever named would be pure waste."""
    assert conn.execute(
        "SELECT count(*) FROM drugref.condition_subtree WHERE root_uuid = %s",
        (epilepsy_tree["child"],)).fetchone()[0] == 0


def test_the_walk_survives_a_cycle(conn, epilepsy_tree, ingest_run_id):
    """db/013 forbids only SELF-parenting; a longer cycle must terminate rather than
    recurse forever. UNION over (root, condition) is what guarantees that."""
    conditions.add_condition_parent_edge(
        conn, epilepsy_tree["parent"], epilepsy_tree["child"], ingest_run_id)
    rows = conn.execute(
        "SELECT count(*) FROM drugref.condition_subtree WHERE root_uuid = %s",
        (epilepsy_tree["parent"],)).fetchone()[0]
    assert rows == 2

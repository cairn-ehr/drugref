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


# ---- contraindications_for_condition (#45) -----------------------------------
#
# THE SAME ANSWER, REACHED FROM THE OTHER END. condition_contraindication_expanded
# walks DOWN from every contraindicated condition, so Postgres -- which cannot push a
# predicate into a recursive CTE -- computes the whole graph before filtering to the
# handful of rows a patient lookup wanted. This function starts at the PATIENT'S
# condition and walks UP, which is O(ancestors) instead of O(graph).
#
# MEASURED on the real 2026 release (5,203 conditions, 7,157 edges, 9,471 rules), the
# Epilepsy lookup: the view 9-10 ms, materialising 11,512 subtree rows to return 15;
# this function 0.7-0.9 ms. Neither is slow today -- the issue was filed because 5b.2
# (~18k more assertions) reuses this DAG, and the fix had to be measured, not guessed.
#
# EQUIVALENCE IS THE CONTRACT, and the tests below assert it directly rather than
# re-deriving what each side "should" return. Two implementations of one expansion
# rule is the two-lists-in-two-places footgun db/006 exists to remove; the only thing
# that makes a second one safe is a test that fails the moment they disagree.


def _by_function(conn, condition_uuid):
    return conn.execute(
        "SELECT subject_moiety, object_condition, member_condition, is_direct, "
        "relationship, source FROM drugref.contraindications_for_condition(%s) "
        "ORDER BY subject_moiety, object_condition", (condition_uuid,)).fetchall()


def _by_view(conn, condition_uuid):
    return conn.execute(
        "SELECT subject_moiety, object_condition, member_condition, is_direct, "
        "relationship, source FROM drugref.condition_contraindication_expanded "
        "WHERE member_condition = %s ORDER BY subject_moiety, object_condition",
        (condition_uuid,)).fetchall()


def test_the_ancestor_walk_answers_exactly_what_the_view_answers(conn, epilepsy_tree):
    """Both directions of the tree, both non-empty -- an equivalence that holds only
    because both sides return nothing would prove nothing."""
    for key in ("parent", "child"):
        rows = _by_function(conn, epilepsy_tree[key])
        assert rows == _by_view(conn, epilepsy_tree[key])
        assert rows, f"{key} must actually match a rule"


def test_a_rule_on_an_ANCESTOR_reaches_the_patients_condition(conn, epilepsy_tree):
    """The clinical point, from the patient's end: a rule written against Epilepsy
    fires for a patient coded Epilepsy, Generalized, and says so -- object_condition
    names what the rule actually said, is_direct says it was not the patient's own
    code."""
    assert _by_function(conn, epilepsy_tree["child"]) == \
        [(epilepsy_tree["moiety"], epilepsy_tree["parent"], epilepsy_tree["child"],
          False, "CI_with", "MED-RT")]


def test_a_rule_on_the_patients_OWN_condition_is_direct(conn, epilepsy_tree):
    assert _by_function(conn, epilepsy_tree["parent"]) == \
        [(epilepsy_tree["moiety"], epilepsy_tree["parent"], epilepsy_tree["parent"],
          True, "CI_with", "MED-RT")]


def test_the_axis_opt_out_governs_the_function_TOO(conn, epilepsy_tree):
    """The gate is per predicate and lives in condition_ci_axis, so switching it off
    must stop BOTH read paths. A function that kept expanding after the data said not
    to would be the second implementation quietly disagreeing with the first."""
    conn.execute("UPDATE drugref.condition_ci_axis "
                 "SET expands_descendants = false WHERE relationship = 'CI_with'")
    assert _by_function(conn, epilepsy_tree["child"]) == []
    assert _by_function(conn, epilepsy_tree["parent"]) == _by_view(
        conn, epilepsy_tree["parent"])


def test_a_condition_no_rule_reaches_returns_nothing(conn, epilepsy_tree,
                                                     ingest_run_id):
    """An unrelated condition, not merely an unknown UUID: the walk must climb and
    find nothing, rather than fail to climb at all."""
    other = _condition(conn, ingest_run_id, "D003920", "Diabetes Mellitus",
                       ("C18.452.394.750",))
    assert _by_function(conn, other) == []


def test_the_upward_walk_survives_a_cycle(conn, epilepsy_tree, ingest_run_id):
    """The down-walk's cycle-safety is pinned above; the up-walk needs its own, for
    the same reason and by the same means (UNION over the node, not the path). A view
    that never returns is worse than a wrong answer, because nothing reports it."""
    conditions.add_condition_parent_edge(
        conn, epilepsy_tree["parent"], epilepsy_tree["child"], ingest_run_id)
    assert len(_by_function(conn, epilepsy_tree["child"])) == 1


def test_two_paths_to_one_rule_return_ONE_row(conn, epilepsy_tree, ingest_run_id):
    """THE TOPOLOGY THE EQUIVALENCE TEST ABOVE CANNOT SEE. A two-node chain reaches
    every rule exactly one way, so it would pass whether or not either side deduped --
    and 1,690 of the registry's 5,203 conditions have several parents, so the real DAG
    is nothing like a chain.

    Here a grandchild reaches the contraindicated root through BOTH its parents. The
    view dedupes in condition_subtree's UNION and the function in `ancestor`'s, so one
    rule must still be one row on both sides. A path-counting walk would return two,
    and a consumer would see one contraindication twice.
    """
    second = _condition(conn, ingest_run_id, "D004828", "Epilepsy, Partial",
                        ("C10.228.140.490.375",))
    conditions.add_condition_parent_edge(
        conn, second, epilepsy_tree["parent"], ingest_run_id)
    grandchild = _condition(conn, ingest_run_id, "D017034", "Epilepsy, Rolandic",
                            ("C10.228.140.490.360.680", "C10.228.140.490.375.680"))
    for parent in (epilepsy_tree["child"], second):
        conditions.add_condition_parent_edge(conn, grandchild, parent, ingest_run_id)

    rows = _by_function(conn, grandchild)
    assert rows == [(epilepsy_tree["moiety"], epilepsy_tree["parent"], grandchild,
                     False, "CI_with", "MED-RT")]
    assert rows == _by_view(conn, grandchild)

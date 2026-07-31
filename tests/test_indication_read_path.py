# tests/test_indication_read_path.py
"""Generalisation, and the one place it happens (slice 5b.2, db/019).

Nothing derived is STORED, so unlike slice 5b there is no expanded view to compare the
function against. What is pinned instead is that the function and
condition_indication_reach -- the only other statement of the same rule -- agree, which
is what makes two statements of one rule safe (db/006, and db/018's round, where a
quantity stated twice disagreed and a whole class of dead rules went unreported).
"""
import pytest

from drugref import conditions, indications
from drugref.ingest.mesh_concepts import DESCRIPTOR, MeshRecord

pytestmark = pytest.mark.usefixtures("conn")


def record(ui: str, name: str, *trees: str) -> MeshRecord:
    return MeshRecord(concept_ui=f"M{ui}", record_ui=ui, record_kind=DESCRIPTOR,
                      name=name, tree_numbers=trees, unii=frozenset(),
                      cas=frozenset(), is_preferred_concept=True)


@pytest.fixture
def dag(conn, ingest_run_id):
    """Epilepsy -> Temporal Lobe Epilepsy -> a deeper node, plus an unrelated root."""
    made = {}
    for ui, name, trees in (
            ("D004827", "Epilepsy", ("C10.228.140.490",)),
            ("D004833", "Epilepsy, Temporal Lobe", ("C10.228.140.490.360",)),
            ("D017034", "Epilepsy, Frontal Lobe", ("C10.228.140.490.360.300",)),
            ("D006973", "Hypertension", ("C14.907.489",))):
        made[ui], _ = conditions.upsert_condition(conn, record(ui, name, *trees),
                                                  ingest_run_id, "MeSH")
    for child, parent in (("D004833", "D004827"), ("D017034", "D004833")):
        conditions.add_condition_parent_edge(conn, made[child], made[parent],
                                             ingest_run_id)
    return made


def test_a_direct_indication_is_returned_as_direct(conn, a_moiety, dag, ingest_run_id):
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    rows = conn.execute(
        "SELECT is_direct, object_condition FROM "
        "drugref.indications_for_condition(%s)", (dag["D004827"],)).fetchall()
    assert rows == [(True, dag["D004827"])]


def test_an_ancestors_indication_is_offered_as_a_generalisation(conn, a_moiety, dag,
                                                                ingest_run_id):
    """The clinical case: a rule on Epilepsy reaches a patient coded Frontal Lobe
    Epilepsy TWO levels down, and the row says which condition it was written against
    so a consumer can render 'indicated for Epilepsy, a more general form'."""
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    rows = conn.execute(
        "SELECT is_direct, object_condition FROM "
        "drugref.indications_for_condition(%s)", (dag["D017034"],)).fetchall()
    assert rows == [(False, dag["D004827"])]


def test_a_sibling_branch_is_not_reached(conn, a_moiety, dag, ingest_run_id):
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    assert conn.execute("SELECT count(*) FROM drugref.indications_for_condition(%s)",
                        (dag["D006973"],)).fetchone()[0] == 0


def test_expansion_never_runs_DOWNWARD(conn, a_moiety, dag, ingest_run_id):
    """THE CENTRAL GUARANTEE OF THIS SLICE. A rule on a SPECIFIC condition must never
    reach the general one: 'treats Frontal Lobe Epilepsy' does not mean 'treats
    epilepsy', and the inverse direction is what would manufacture 702 claims from one
    rule on Neoplasms."""
    indications.add_condition_indication(conn, a_moiety, dag["D017034"], "may_treat",
                                         "MED-RT", ingest_run_id)
    assert conn.execute("SELECT count(*) FROM drugref.indications_for_condition(%s)",
                        (dag["D004827"],)).fetchone()[0] == 0


def test_a_non_generalising_predicate_returns_only_direct_rows(conn, a_moiety, dag,
                                                               ingest_run_id):
    """Switching generalisation off is ONE UPDATE and needs no view or function edit."""
    conn.execute("UPDATE drugref.condition_indication_axis "
                 "SET generalises_to_descendants = false WHERE relationship = 'may_treat'")
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    assert conn.execute("SELECT count(*) FROM drugref.indications_for_condition(%s)",
                        (dag["D004833"],)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM drugref.indications_for_condition(%s)",
                        (dag["D004827"],)).fetchone()[0] == 1


def test_induced_states_never_appear(conn, a_moiety, dag, ingest_run_id):
    """'can cause' must not be readable through the indication path, at any distance."""
    indications.add_induced_condition(conn, a_moiety, dag["D004827"], "MED-RT",
                                      ingest_run_id)
    for uuid_ in (dag["D004827"], dag["D004833"]):
        assert conn.execute(
            "SELECT count(*) FROM drugref.indications_for_condition(%s)",
            (uuid_,)).fetchone()[0] == 0


def test_the_reach_view_counts_direct_and_generalised_separately(conn, a_moiety, dag,
                                                                 ingest_run_id):
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    rows = dict(conn.execute(
        "SELECT condition_uuid, direct_indication_rules || '/' || "
        "generalised_indication_rules FROM drugref.condition_indication_reach"
    ).fetchall())
    assert rows[dag["D004827"]] == "1/0"
    assert rows[dag["D004833"]] == "0/1"
    assert rows[dag["D017034"]] == "0/1"
    assert rows[dag["D006973"]] == "0/0"     # present with zeroes, never absent


def test_the_function_and_the_reach_view_agree(conn, a_moiety, dag, ingest_run_id):
    """The pin that makes two statements of one rule safe. db/018's round found a
    quantity stated twice where only one copy learned a correction; here the equality is
    asserted rather than assumed, and the real-release run checks it over every
    condition."""
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    indications.add_condition_indication(conn, a_moiety, dag["D004833"], "may_prevent",
                                         "MED-RT", ingest_run_id)
    for condition_uuid in dag.values():
        from_view = conn.execute(
            "SELECT direct_indication_rules + generalised_indication_rules "
            "FROM drugref.condition_indication_reach WHERE condition_uuid = %s",
            (condition_uuid,)).fetchone()[0]
        from_function = conn.execute(
            "SELECT count(*) FROM drugref.indications_for_condition(%s)",
            (condition_uuid,)).fetchone()[0]
        assert from_view == from_function, f"disagreement at {condition_uuid}"


def test_the_walk_terminates_under_a_cycle(conn, a_moiety, dag, ingest_run_id):
    """db/013 forbids only SELF-parenting; a longer cycle must be survived by the walk
    itself, as db/012's ci_class_subtree explains."""
    conditions.add_condition_parent_edge(conn, dag["D004827"], dag["D017034"],
                                         ingest_run_id)
    indications.add_condition_indication(conn, a_moiety, dag["D004827"], "may_treat",
                                         "MED-RT", ingest_run_id)
    assert conn.execute("SELECT count(*) FROM drugref.indications_for_condition(%s)",
                        (dag["D004833"],)).fetchone()[0] == 1

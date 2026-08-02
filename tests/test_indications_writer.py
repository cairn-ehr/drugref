# tests/test_indications_writer.py
"""The indication relations and their single writer (slice 5b.2, db/019)."""
import pytest

from drugref import conditions, indications
from drugref.ingest.mesh_concepts import DESCRIPTOR, MeshRecord

pytestmark = pytest.mark.usefixtures("conn")

EPILEPSY = MeshRecord(concept_ui="M0007720", record_ui="D004827",
                      record_kind=DESCRIPTOR, name="Epilepsy",
                      tree_numbers=("C10.228.140.490",), unii=frozenset(),
                      cas=frozenset(), is_preferred_concept=True)


@pytest.fixture
def a_condition(conn, ingest_run_id):
    condition_uuid, _ = conditions.upsert_condition(conn, EPILEPSY, ingest_run_id,
                                                    "MeSH")
    return condition_uuid


def test_an_indication_is_recorded(conn, a_moiety, a_condition, ingest_run_id):
    assert indications.add_condition_indication(
        conn, a_moiety, a_condition, "may_treat", "MED-RT", ingest_run_id) is True
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication").fetchone()[0] == 1


def test_the_same_assertion_twice_is_harmless(conn, a_moiety, a_condition,
                                              ingest_run_id):
    """A release that states one assertion through two MeSH concepts collapses onto
    the primary key -- 19 assertions do exactly that in the 2026.07.06 release."""
    args = (conn, a_moiety, a_condition, "may_treat", "MED-RT", ingest_run_id)
    assert indications.add_condition_indication(*args) is True
    assert indications.add_condition_indication(*args) is False


def test_two_predicates_on_one_pair_are_two_rows(conn, a_moiety, a_condition,
                                                 ingest_run_id):
    """relationship is IN the key: a drug may both treat and prevent one condition."""
    for predicate in ("may_treat", "may_prevent"):
        assert indications.add_condition_indication(
            conn, a_moiety, a_condition, predicate, "MED-RT", ingest_run_id) is True


def test_an_undeclared_predicate_is_refused(conn, a_moiety, a_condition,
                                            ingest_run_id):
    """The FK into condition_indication_axis is what stops a predicate reaching the
    table before anyone has declared whether it may generalise."""
    with pytest.raises(Exception):
        indications.add_condition_indication(
            conn, a_moiety, a_condition, "may_cure", "MED-RT", ingest_run_id)


def test_a_mistyped_source_is_refused(conn, a_moiety, a_condition, ingest_run_id):
    """db/012 finding 3: an unconstrained source once let 'MEDRT' insert cleanly and
    then match nothing, ever -- a per-source rebuild cannot find rows it cannot name."""
    with pytest.raises(Exception):
        indications.add_condition_indication(
            conn, a_moiety, a_condition, "may_treat", "MEDRT", ingest_run_id)


def test_an_induced_condition_is_recorded(conn, a_moiety, a_condition, ingest_run_id):
    assert indications.add_induced_condition(
        conn, a_moiety, a_condition, "MED-RT", ingest_run_id) is True
    row = conn.execute("SELECT relationship FROM drugref.moiety_induced_condition"
                       ).fetchone()
    assert row[0] == "induces"


def test_induces_cannot_be_filed_as_an_indication(conn, a_moiety, a_condition,
                                                  ingest_run_id):
    """The tables are separate BECAUSE the unfiltered read of each must be one true
    sentence: 'used for this condition' vs 'can CAUSE this condition'. A consumer who
    forgets a WHERE clause must not read 'treats agranulocytosis' off an induces row."""
    with pytest.raises(Exception):
        indications.add_condition_indication(
            conn, a_moiety, a_condition, "induces", "MED-RT", ingest_run_id)


def test_the_axis_forces_a_declaration(conn):
    """NO DEFAULT on generalises_to_descendants: a predicate added later must state
    its own answer (db/014's discipline, after db/012 finding 5)."""
    with pytest.raises(Exception):
        conn.execute("INSERT INTO drugref.condition_indication_axis (relationship) "
                     "VALUES ('may_palliate')")


def test_the_three_therapeutic_predicates_are_declared(conn):
    rows = dict(conn.execute(
        "SELECT relationship, generalises_to_descendants "
        "FROM drugref.condition_indication_axis").fetchall())
    assert rows == {"may_treat": True, "may_prevent": True, "may_diagnose": True}
    assert "induces" not in rows      # it licenses no walk and has no axis row


def test_the_clear_is_scoped_by_source(conn, a_moiety, a_condition):
    """Rebuildable projection: a re-ingest REPLACES this source's rows, and an
    unrelated feed's survive.

    THE RUN IS OPENED HERE, NOT TAKEN FROM THE ingest_run_id FIXTURE, and that is the
    whole point of the test: clear_source_indications scopes on ingest_run.source, NOT
    on the row's own `source` column. The fixture's run is opened under 'PBS', so a row
    written through it would be deleted by a 'PBS' clear while carrying source
    'MED-RT' -- the test would then assert the opposite of what it claims to prove.
    """
    medrt_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('MED-RT', 'test', 'test', 'medrt_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    indications.add_condition_indication(conn, a_moiety, a_condition, "may_treat",
                                         "MED-RT", medrt_run)

    indications.clear_source_indications(conn, "PBS")     # an unrelated feed
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication").fetchone()[0] == 1

    indications.clear_source_indications(conn, "MED-RT")
    assert conn.execute(
        "SELECT count(*) FROM drugref.moiety_condition_indication").fetchone()[0] == 0

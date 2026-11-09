"""DB-gated tests for the allergy read (db/068 drugref.allergy_check, allergy_read.check).

The world is tests/allergy_world.py: real UNIIs, Task 2's MeSH fixtures, the shipped
allow-list, plus the three real GSRS compositions (penicillin g procaine, clemizole
penicillin, ampicillin sodium). The ingest commits, so every test starts from a
TRUNCATE rather than relying on the conn fixture's rollback.

The point of this read is that a wrong `no-match` is a MISSED allergy warning, so
every test pins which of the three outcomes comes back, not just "is it a match".
"""
import uuid

import pytest

from drugref import allergy_read
from drugref.classes import ClassConcept, upsert_class
from tests import allergy_world as world

P = uuid.UUID(world.PENICILLINS_UUID)


@pytest.fixture(autouse=True)
def _clean(conn):
    conn.execute("TRUNCATE drugref.allergy_group_member, drugref.allergy_group, "
                 "drugref.allergy_mesh_placement, drugref.substance_composition, "
                 "drugref.class_membership, drugref.class_parent, "
                 "drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    yield


@pytest.fixture
def m(conn):
    """The seeded, ingested world: {moiety name: moiety_uuid}."""
    moieties = world.seed_moieties(conn)
    world.seed_compositions(conn, moieties)
    conn.commit()
    world.run_allergy_ingest(conn)
    return moieties


def _kinds(answer) -> set[str]:
    return {x.kind for x in answer.matches}


def test_case_a_perindopril(conn, m):
    assert allergy_read.check(conn, m["perindopril"], P).outcome == "no-match"


@pytest.mark.parametrize("name", ["amoxicillin", "amoxicillin anhydrous"])
def test_case_b_amoxicillin(conn, m, name):
    a = allergy_read.check(conn, m[name], P)
    assert a.outcome == "match" and _kinds(a) == {"same-group"}
    assert all(x.group_class_uuid == P for x in a.matches)


def test_case_c_cephalexin_known_gap(conn, m):
    # KNOWN GAP, not a safety claim: the penicillin<->cephalosporin cross-allergy
    # rule is a spec section 2 non-goal this round. cairn must not read this
    # `no-match` as "cephalosporins are safe".
    assert allergy_read.check(conn, m["cephalexin"], P).outcome == "no-match"


def test_drug_allergen_expands(conn, m):
    a = allergy_read.check(conn, m["flucloxacillin"], m["amoxicillin"])
    assert a.outcome == "match" and _kinds(a) == {"same-group"}
    assert {x.group_name for x in a.matches} == {"Penicillins (allergy group)"}
    assert a.matches[0].review_status == "structural-seed"


def test_same_drug(conn, m):
    a = allergy_read.check(conn, m["amoxicillin"], m["amoxicillin"])
    assert a.outcome == "match" and "same-drug" in _kinds(a)


def test_fold_ignores_active_flag(conn, m):
    # GSRS marks penicillin G is_active_component FALSE in clemizole penicillin;
    # an active-only fold would answer this with a silent no-match.
    a = allergy_read.check(conn, m["clemizole penicillin"], P)
    assert a.outcome == "match"
    assert {x.prescribed_component for x in a.matches} == {m["penicillin g"]}


def test_salt(conn, m):
    a = allergy_read.check(conn, m["ampicillin sodium"], P)
    assert a.outcome == "match"
    assert m["ampicillin"] in {x.prescribed_component for x in a.matches}


def test_procaine_partner(conn, m):
    # The accepted trade (spec 4.3.1): a salt's partner matches too, and the path
    # names the component so cairn can say "via its procaine component".
    a = allergy_read.check(conn, m["penicillin g procaine"], m["procaine"])
    assert a.outcome == "match"
    assert all(x.allergen_component == m["procaine"] for x in a.matches)
    assert any(x.kind == "same-drug" and x.prescribed_component == m["procaine"]
               for x in a.matches)


def _non_ag_class(conn) -> uuid.UUID:
    run_id = world._run(conn, "MeSH", "mesh_run")
    class_uuid, _ = upsert_class(
        conn, ClassConcept(nui="D000900", code="D000900", concept_type="PA",
                           name="Anti-Bacterial Agents"), run_id, "MeSH")
    return class_uuid


@pytest.mark.parametrize("prescribed, allergen, outcome, reason", [
    ("<unknown>", "<P>", "unplaced", "unknown-prescribed"),
    ("perindopril", "<unknown>", "unplaced", "unknown-allergen"),
    ("perindopril", "<non-AG class>", "unplaced", "allergen-not-a-group"),
    ("unplaced test moiety", "<P>", "unplaced", "prescribed-not-in-mesh"),
    ("perindopril", "perindopril", "match", None),
    ("cephalexin", "perindopril", "unplaced", "allergen-in-no-group"),
])
def test_unplaced_reasons(conn, m, prescribed, allergen, outcome, reason):
    def resolve(name):
        if name == "<unknown>":
            return uuid.uuid4()
        if name == "<P>":
            return P
        if name == "<non-AG class>":
            return _non_ag_class(conn)
        return m[name]
    a = allergy_read.check(conn, resolve(prescribed), resolve(allergen))
    assert (a.outcome, a.reason) == (outcome, reason)
    if outcome == "match":
        assert _kinds(a) == {"same-drug"}
    else:
        assert a.matches == ()


def test_moiety_allergen_with_unplaced_prescribed(conn, m):
    # Allergen is in a group, prescribed side has no MeSH bridge: unplaced, never
    # no-match (it could be a penicillin MeSH never filed).
    a = allergy_read.check(conn, m["unplaced test moiety"], m["amoxicillin"])
    assert (a.outcome, a.reason) == ("unplaced", "prescribed-not-in-mesh")


def test_fold_view_shape(conn, m):
    rows = set(conn.execute(
        "SELECT component_moiety FROM drugref.moiety_allergy_fold "
        "WHERE moiety_uuid = %s", (m["clemizole penicillin"],)).fetchall())
    assert rows == {(m["clemizole penicillin"],), (m["penicillin g"],),
                    (m["clemizole"],)}

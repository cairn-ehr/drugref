"""class_profile_member, member_property, member_property_negation, class_asserted_property
(db/059). DB-gated. Member properties come only from STATED records; see the migration."""
import pytest

from drugref import curation_property
from tests import class_profile_world as w


@pytest.fixture
def world(conn):
    return conn, w.run(conn)


def _members(conn, k):
    return {r[0] for r in conn.execute(
        "SELECT moiety_uuid FROM drugref.class_profile_member WHERE class_uuid=%s", (k,))}


def _props(conn, m):
    return {(r[0], r[1], r[2]): r[3] for r in conn.execute(
        "SELECT relationship, object_kind, object_uuid, sources "
        "FROM drugref.member_property WHERE moiety_uuid=%s", (m,))}


def _negated(conn, m):
    return {(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT relationship, object_kind, object_uuid "
        "FROM drugref.member_property_negation WHERE moiety_uuid=%s", (m,))}


def _asserted(conn, k):
    return {(r[0], r[1], r[2]): r[3] for r in conn.execute(
        "SELECT relationship, object_kind, object_uuid, asserted_by "
        "FROM drugref.class_asserted_property WHERE class_uuid=%s", (k,))}


def _rule(conn, verdict, relationship, object_kind, object_uuid, **subject):
    curation_property.record_property_ruling(
        conn, relationship=relationship, object_kind=object_kind,
        object_uuid=object_uuid, verdict=verdict, reviewed_by="t",
        reviewed_against="t", **subject)


def test_membership_is_direct_only(world):
    conn, r = world
    c, d = w.klass(conn, r, "C"), w.klass(conn, r, "D")
    w.parent(conn, r, d, c)
    m = w.moiety(conn, r, "m")
    w.member(conn, r, m, d)
    assert _members(conn, d) == {m}
    assert _members(conn, c) == set()


def test_medrt_pk_classes_are_excluded_but_fda_cyp_pk_classes_are_kept(world):
    conn, r = world
    pk = w.klass(conn, r, "Clearance", concept_type="PK")
    cyp = w.klass(conn, r, "Strong CYP1A2 inhibitors", source="FDA-CYP",
                  concept_type="PK")
    m = w.moiety(conn, r, "m")
    w.member(conn, r, m, pk)
    w.member(conn, r, m, cyp)
    assert _members(conn, pk) == set()
    assert _members(conn, cyp) == {m}


def test_each_condition_source_yields_its_relationship(world):
    conn, r = world
    m = w.moiety(conn, r, "m")
    a, b, c = (w.condition(conn, r, n) for n in "abc")
    w.indication(conn, r, m, a)
    w.contraindication(conn, r, m, b)
    w.induced(conn, r, m, c)
    assert set(_props(conn, m)) == {("may_treat", "condition", a),
                                    ("CI_with", "condition", b),
                                    ("induces", "condition", c)}


def test_a_drug_pair_is_a_property_of_both_drugs(world):
    conn, r = world
    a, b = w.moiety(conn, r, "a"), w.moiety(conn, r, "b")
    w.moiety_ci(conn, r, a, b)
    assert ("interacts_with", "moiety", b) in _props(conn, a)
    assert ("interacts_with", "moiety", a) in _props(conn, b)


def test_expanded_pairs_are_not_member_properties(world):
    conn, r = world
    k = w.klass(conn, r, "C")
    x, p, q = (w.moiety(conn, r, n) for n in "xpq")
    w.member(conn, r, p, k)
    w.member(conn, r, q, k)
    w.class_ci(conn, r, x, k)
    props = _props(conn, x)
    assert ("interacts_with", "class", k) in props
    assert ("interacts_with", "moiety", p) not in props


def test_two_sources_for_one_fact_count_once(world):
    conn, r = world
    onc = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer) VALUES ('ONCHIGH', 'test', 'deadbeef', 'onchigh_run') "
        "RETURNING ingest_run_id").fetchone()[0]
    k, x = w.klass(conn, r, "C"), w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, k)
    w.class_ci(conn, onc, x, k, source="ONCHIGH")
    rows = conn.execute("SELECT sources FROM drugref.member_property WHERE moiety_uuid=%s",
                        (x,)).fetchall()
    assert len(rows) == 1 and rows[0][0] == ["MED-RT", "ONCHIGH"]


def test_negating_one_record_keeps_a_property_another_record_states(world):
    conn, r = world
    k, x = w.klass(conn, r, "C"), w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, k, "CI_MoA")
    w.class_ci(conn, r, x, k, "CI_PE")
    w.curated_ci(conn, x, k, "CI_PE", applies=False)
    key = ("interacts_with", "class", k)
    assert key in _props(conn, x)
    assert key not in _negated(conn, x)


def test_negating_every_record_makes_a_confirmed_exception(world):
    conn, r = world
    k, x = w.klass(conn, r, "C"), w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, k, "CI_PE")
    w.curated_ci(conn, x, k, "CI_PE", applies=False)
    key = ("interacts_with", "class", k)
    assert key not in _props(conn, x)
    assert key in _negated(conn, x)


def test_holds_adds_and_does_not_hold_removes(world):
    conn, r = world
    m, c = w.moiety(conn, r, "m"), w.condition(conn, r, "c")
    d = w.condition(conn, r, "d")
    w.indication(conn, r, m, d)
    _rule(conn, "holds", "CI_with", "condition", c, subject_moiety_uuid=m)
    _rule(conn, "does_not_hold", "may_treat", "condition", d, subject_moiety_uuid=m)
    props = _props(conn, m)
    assert props[("CI_with", "condition", c)] == ["DRUGREF"]
    assert ("may_treat", "condition", d) not in props
    assert ("may_treat", "condition", d) in _negated(conn, m)


def test_a_class_pair_rule_is_asserted_on_both_classes(world):
    conn, r = world
    a, b = w.klass(conn, r, "A"), w.klass(conn, r, "B")
    w.class_pair_ci(conn, r, a, b)
    assert ("interacts_with", "class", b) in _asserted(conn, a)
    assert _asserted(conn, b)[("interacts_with", "class", a)] == ["MED-RT"]


def test_a_drug_to_class_rule_is_asserted_on_the_object_class(world):
    conn, r = world
    k, x = w.klass(conn, r, "C"), w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, k)
    assert ("interacts_with", "moiety", x) in _asserted(conn, k)


def test_a_curated_applies_false_class_rule_is_not_asserted(world):
    conn, r = world
    a, b = w.klass(conn, r, "A"), w.klass(conn, r, "B")
    w.class_pair_ci(conn, r, a, b)
    w.curated_class_ci(conn, a, b, applies=False)
    assert _asserted(conn, a) == {} and _asserted(conn, b) == {}


def test_a_curated_applies_true_class_rule_is_asserted_on_both_classes(world):
    conn, r = world
    a, b = w.klass(conn, r, "A"), w.klass(conn, r, "B")
    w.curated_class_ci(conn, a, b)
    assert _asserted(conn, a)[("interacts_with", "class", b)] == ["DRUGREF"]
    assert ("interacts_with", "class", a) in _asserted(conn, b)


def test_a_class_holds_ruling_asserts_a_condition_property(world):
    conn, r = world
    k, c = w.klass(conn, r, "C"), w.condition(conn, r, "c")
    _rule(conn, "holds", "may_treat", "condition", c, subject_class_uuid=k)
    assert _asserted(conn, k)[("may_treat", "condition", c)] == ["DRUGREF"]


# --- fix round 1: symmetric interaction rulings, record-level negation, supersession ---

def test_a_drug_pair_holds_ruling_applies_to_both_drugs(world):
    conn, r = world
    x, y = w.moiety(conn, r, "x"), w.moiety(conn, r, "y")
    _rule(conn, "holds", "interacts_with", "moiety", y, subject_moiety_uuid=x)
    assert _props(conn, x)[("interacts_with", "moiety", y)] == ["DRUGREF"]
    assert _props(conn, y)[("interacts_with", "moiety", x)] == ["DRUGREF"]


def test_a_drug_pair_does_not_hold_ruling_applies_to_both_drugs(world):
    conn, r = world
    x, y = w.moiety(conn, r, "x"), w.moiety(conn, r, "y")
    w.moiety_ci(conn, r, x, y)
    _rule(conn, "does_not_hold", "interacts_with", "moiety", y, subject_moiety_uuid=x)
    for a, b in ((x, y), (y, x)):
        assert ("interacts_with", "moiety", b) not in _props(conn, a)
        assert ("interacts_with", "moiety", b) in _negated(conn, a)


def test_a_does_not_hold_on_one_orientation_beats_a_holds_on_the_other(world):
    conn, r = world
    x, y = w.moiety(conn, r, "x"), w.moiety(conn, r, "y")
    _rule(conn, "holds", "interacts_with", "moiety", y, subject_moiety_uuid=x)
    _rule(conn, "does_not_hold", "interacts_with", "moiety", x, subject_moiety_uuid=y)
    for a, b in ((x, y), (y, x)):
        assert ("interacts_with", "moiety", b) not in _props(conn, a)
        assert ("interacts_with", "moiety", b) in _negated(conn, a)


def test_a_drug_to_class_holds_ruling_is_asserted_on_the_class(world):
    conn, r = world
    k, x = w.klass(conn, r, "C"), w.moiety(conn, r, "x")
    _rule(conn, "holds", "interacts_with", "class", k, subject_moiety_uuid=x)
    assert _asserted(conn, k)[("interacts_with", "moiety", x)] == ["DRUGREF"]


def test_a_drug_to_class_does_not_hold_ruling_withdraws_the_class_assertion(world):
    conn, r = world
    k, x = w.klass(conn, r, "C"), w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, k)
    _rule(conn, "does_not_hold", "interacts_with", "class", k, subject_moiety_uuid=x)
    assert ("interacts_with", "moiety", x) not in _asserted(conn, k)


def test_record_level_negation_applies_to_the_drug_to_class_arm(world):
    conn, r = world
    k, x = w.klass(conn, r, "C"), w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, k, "CI_PE")
    w.curated_ci(conn, x, k, "CI_PE", applies=False)
    assert ("interacts_with", "moiety", x) not in _asserted(conn, k)


def test_a_superseded_curated_row_is_ignored(world):
    conn, r = world
    k, x = w.klass(conn, r, "C"), w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, k, "CI_PE")
    w.curated_ci(conn, x, k, "CI_PE", applies=False)
    # Correct the negation: a LATER applies=true row supersedes the applies=false one.
    w.curated_ci(conn, x, k, "CI_PE", applies=True)
    conn.execute("UPDATE drugref.curated_interaction SET superseded_by = "
                 "(SELECT max(curated_interaction_id) FROM drugref.curated_interaction) "
                 "WHERE NOT applies")
    assert ("interacts_with", "class", k) in _props(conn, x)
    assert ("interacts_with", "class", k) not in _negated(conn, x)


def test_a_drugcentral_pair_reaches_member_property(world):
    conn, r = world
    a, b = w.moiety(conn, r, "a"), w.moiety(conn, r, "b")
    lo, hi = sorted((a, b))
    dc = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer) VALUES ('DRUGCENTRAL', 'test', 'deadbeef', 'drugcentral_run') "
        "RETURNING ingest_run_id").fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.drugcentral_ddi_assertion (ingest_run, source, upstream_key, "
        "endpoint_1_name, endpoint_2_name, upstream_label, severity_label, moiety_1_uuid, "
        "moiety_2_uuid, route_1, route_2) VALUES (%s, 'DRUGCENTRAL', 'k1', 'a', 'b', "
        "'A/B [VA Drug Interaction]', 'Critical', %s, %s, 'display_name', 'display_name')",
        (dc, lo, hi))
    assert _props(conn, a)[("interacts_with", "moiety", b)] == ["DRUGCENTRAL"]

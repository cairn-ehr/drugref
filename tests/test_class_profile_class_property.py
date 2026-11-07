"""class_property, class_property_member, class_member_peculiarity,
class_subclass_only_member (db/060). DB-gated. Observed = at least 75% of >= 2 direct
members; asserted = on the class or any ancestor and passes DOWN, observed never does."""
import pytest

from drugref import curation_property
from tests import class_profile_world as w


@pytest.fixture
def world(conn):
    return conn, w.run(conn)


def _cp(conn, k):
    return {(r[0], r[1], r[2]): r[3:] for r in conn.execute(
        "SELECT relationship, object_kind, object_uuid, is_asserted, asserted_via, "
        "asserted_by, is_observed, carriers, members "
        "FROM drugref.class_property WHERE class_uuid=%s", (k,))}


def _standing(conn, k):
    return {r[0]: r[1] for r in conn.execute(
        "SELECT moiety_uuid, standing FROM drugref.class_property_member "
        "WHERE class_uuid=%s", (k,))}


def _cohort(conn, r, n_carry, n_total, kind="may_treat", cond_name="htn"):
    """A class with n_total members, the first n_carry indicated for one condition."""
    k = w.klass(conn, r, "K" + cond_name)
    cond = w.condition(conn, r, cond_name)
    ms = [w.moiety(conn, r, f"m{i}{cond_name}") for i in range(n_total)]
    for i, m in enumerate(ms):
        w.member(conn, r, m, k)
        if i < n_carry:
            w.indication(conn, r, m, cond, kind)
    return k, cond, ms


def test_three_of_four_is_observed_and_two_of_four_is_not(world):
    conn, r = world
    k, cond, _ = _cohort(conn, r, 3, 4)
    row = _cp(conn, k)[("may_treat", "condition", cond)]
    assert row[3] is True and row[4:] == (3, 4)
    k2, cond2, _ = _cohort(conn, r, 2, 4, cond_name="other")
    assert ("may_treat", "condition", cond2) not in _cp(conn, k2)


def test_a_one_member_class_observes_nothing(world):
    conn, r = world
    k, _, _ = _cohort(conn, r, 1, 1)
    assert _cp(conn, k) == {}


def test_two_of_two_is_observed(world):
    conn, r = world
    k, cond, _ = _cohort(conn, r, 2, 2)
    assert _cp(conn, k)[("may_treat", "condition", cond)][3] is True


def test_members_counts_distinct_drugs_even_if_tagged_twice(world):
    """Two class_membership rows (has_EPC and has_MoA) for one drug and class ARE
    representable (the PK includes relationship), so this is the literal case."""
    conn, r = world
    k, cond, ms = _cohort(conn, r, 2, 2)
    w.member(conn, r, ms[0], k, "has_MoA")
    assert _cp(conn, k)[("may_treat", "condition", cond)][4:] == (2, 2)


def test_a_class_with_no_direct_members_has_only_asserted_properties(world):
    conn, r = world
    k = w.klass(conn, r, "Empty")
    x = w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, k)
    rows = _cp(conn, k)
    assert list(rows) == [("interacts_with", "moiety", x)]
    assert rows[("interacts_with", "moiety", x)] == (True, [k], ["MED-RT"], False, 0, 0)


def test_exact_match_only(world):
    conn, r = world
    k = w.klass(conn, r, "K")
    h, eh = w.condition(conn, r, "hypertension"), w.condition(conn, r, "essential")
    conn.execute("INSERT INTO drugref.condition_parent VALUES (%s, %s, %s)", (eh, h, r))
    a, b = w.moiety(conn, r, "a"), w.moiety(conn, r, "b")
    for m, c in ((a, h), (b, eh)):
        w.member(conn, r, m, k)
        w.indication(conn, r, m, c)
    rows = _cp(conn, k)
    assert rows == {}  # 1 of 2 each: nothing pools across the condition hierarchy


def test_an_ancestor_assertion_reaches_a_subclass(world):
    conn, r = world
    c, d = w.klass(conn, r, "C"), w.klass(conn, r, "D")
    w.parent(conn, r, d, c)
    x, m = w.moiety(conn, r, "x"), w.moiety(conn, r, "m")
    w.class_ci(conn, r, x, c)
    w.member(conn, r, m, d)
    row = _cp(conn, d)[("interacts_with", "moiety", x)]
    assert row[0] is True and row[1] == [c]
    assert _standing(conn, d) == {m: "inherits"}


def test_an_observed_property_does_not_descend(world):
    conn, r = world
    c, cond, _ = _cohort(conn, r, 2, 2)
    d = w.klass(conn, r, "D")
    w.parent(conn, r, d, c)
    w.member(conn, r, w.moiety(conn, r, "dm"), d)
    assert _cp(conn, d) == {}
    assert ("may_treat", "condition", cond) in _cp(conn, c)


def _rule_dnh(conn, m, cond):
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=m, relationship="may_treat",
        object_kind="condition", object_uuid=cond, verdict="does_not_hold",
        reviewed_by="t", reviewed_against="t")


def test_standing_precedence(world):
    conn, r = world
    # 9 carriers of 12 -> observed; exc and both are negated, so they do not carry.
    k, cond, ms = _cohort(conn, r, 9, 9)
    exc, both, nc = (w.moiety(conn, r, n) for n in ("exc", "both", "nc"))
    for m in (exc, both, nc):
        w.member(conn, r, m, k)
    w.indication(conn, r, both, cond)  # stated AND ruled out -> confirmed_exception
    _rule_dnh(conn, exc, cond)
    _rule_dnh(conn, both, cond)
    got = {r_[0]: r_[1] for r_ in conn.execute(
        "SELECT moiety_uuid, standing FROM drugref.class_property_member "
        "WHERE class_uuid=%s AND object_uuid=%s", (k, cond))}
    assert got[exc] == got[both] == "confirmed_exception"
    assert got[nc] == "not_confirmed" and got[ms[0]] == "carries"
    # an asserted property: a member nobody has spoken for stands inherits
    x = w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, k)
    ax = {r_[0]: r_[1] for r_ in conn.execute(
        "SELECT moiety_uuid, standing FROM drugref.class_property_member "
        "WHERE class_uuid=%s AND object_uuid=%s", (k, x))}
    assert set(ax.values()) == {"inherits"}


def test_not_confirmed_is_a_standing(world):
    conn, r = world
    k, cond, ms = _cohort(conn, r, 3, 4)
    assert _standing(conn, k)[ms[3]] == "not_confirmed"
    assert _standing(conn, k)[ms[0]] == "carries"


def test_peculiarities_exclude_class_properties(world):
    conn, r = world
    k, cond, ms = _cohort(conn, r, 3, 4)
    odd = w.condition(conn, r, "odd")
    w.indication(conn, r, ms[0], odd)
    got = {(r_[0], r_[1]) for r_ in conn.execute(
        "SELECT moiety_uuid, object_uuid FROM drugref.class_member_peculiarity "
        "WHERE class_uuid=%s", (k,))}
    assert got == {(ms[0], odd)}


def test_subclass_only_drugs_are_listed_not_counted(world):
    conn, r = world
    c, d = w.klass(conn, r, "C"), w.klass(conn, r, "D")
    w.parent(conn, r, d, c)
    cond = w.condition(conn, r, "c")
    a, b, ateno = (w.moiety(conn, r, n) for n in ("a", "b", "atenolol"))
    for m in (a, b):
        w.member(conn, r, m, c)
        w.indication(conn, r, m, cond)
    w.member(conn, r, ateno, d)
    w.indication(conn, r, ateno, cond)
    assert conn.execute(
        "SELECT via_class_uuid FROM drugref.class_subclass_only_member "
        "WHERE class_uuid=%s AND moiety_uuid=%s", (c, ateno)).fetchall() == [(d,)]
    assert ateno not in _standing(conn, c)
    assert _cp(conn, c)[("may_treat", "condition", cond)][4:] == (2, 2)
    assert conn.execute(
        "SELECT count(*) FROM drugref.class_subclass_only_member "
        "WHERE class_uuid=%s AND moiety_uuid IN (%s,%s)", (c, a, b)).fetchone()[0] == 0


def test_a_pk_ancestors_assertion_contributes_nothing_to_its_descendant(world):
    """MED-RT PK classes are excluded everywhere, including as an ancestor an assertion
    arrives from: neither asserted_via nor asserted_by may name what P asserts."""
    conn, r = world
    p = w.klass(conn, r, "Clearance", concept_type="PK")
    c = w.klass(conn, r, "C")
    w.parent(conn, r, c, p)
    x = w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, c)                      # C's own assertion, MED-RT
    w.class_ci(conn, r, x, p, source="ONCHIGH")    # the PK ancestor's, a second source
    row = _cp(conn, c)[("interacts_with", "moiety", x)]
    assert (row[1], row[2]) == ([c], ["MED-RT"])
    assert _cp(conn, p) == {}                      # nor is the PK class profiled itself


def test_two_asserting_levels_both_name_themselves_and_their_sources(world):
    conn, r = world
    onc = w.run(conn, source="ONCHIGH")
    p, c = w.klass(conn, r, "P"), w.klass(conn, r, "C")
    w.parent(conn, r, c, p)
    x = w.moiety(conn, r, "x")
    w.class_ci(conn, r, x, c)
    w.class_ci(conn, onc, x, p, source="ONCHIGH")
    row = _cp(conn, c)[("interacts_with", "moiety", x)]
    assert (row[1], row[2]) == (sorted([c, p]), ["MED-RT", "ONCHIGH"])


def test_a_member_has_no_standing_against_an_interaction_with_itself(world):
    """C interacts with x and x is itself a member of C. The property is C's, but "does
    x interact with x" is meaningless: no standing for x, so no self-question either --
    whether the property was asserted (x -> C rule) or observed (the others all
    interact with x)."""
    conn, r = world
    c = w.klass(conn, r, "C")
    x, *others = (w.moiety(conn, r, n) for n in ("x", "y", "z", "v"))
    for m in (x, *others):
        w.member(conn, r, m, c)
    w.class_ci(conn, r, x, c)                          # asserted route
    assert ("interacts_with", "moiety", x) in _cp(conn, c)
    assert x not in _standing(conn, c)
    d = w.klass(conn, r, "D")
    for m in (x, *others):
        w.member(conn, r, m, d)
    for m in others:
        w.moiety_ci(conn, r, m, x)                     # observed route, 3 of 4
    assert _cp(conn, d)[("interacts_with", "moiety", x)][3] is True
    assert x not in {m for m, in conn.execute(
        "SELECT moiety_uuid FROM drugref.class_property_member WHERE class_uuid = %s "
        "AND object_uuid = %s", (d, x))}
    assert conn.execute(
        "SELECT count(*) FROM drugref.gap_unconfirmed_class_property "
        "WHERE moiety_uuid = %s AND object_uuid = %s", (x, x)).fetchone() == (0,)


def test_the_partner_is_not_in_its_own_denominator(world):
    """#206. C has 5 members, x among them; 3 of the other 4 interact with x. x can
    never carry "interacts with x", so it is not counted: 3 of 4 eligible, observed
    (it was 3 of 5, 60%, not observed). `members` reports the eligible count."""
    conn, r = world
    c = w.klass(conn, r, "C206")
    x, *others = (w.moiety(conn, r, n) for n in ("x", "y", "z", "v", "u"))
    for m in (x, *others):
        w.member(conn, r, m, c)
    for m in others[:3]:
        w.moiety_ci(conn, r, m, x)
    row = _cp(conn, c)[("interacts_with", "moiety", x)]
    assert row[3] is True and row[4:] == (3, 4)
    # An asserted-only row reports the eligible count too.
    w.class_ci(conn, r, others[3], c)
    assert _cp(conn, c)[("interacts_with", "moiety", others[3])][4:] == (0, 4)


def test_two_members_one_of_them_the_partner_observe_nothing(world):
    """The >= 2 floor counts eligible members: one drug interacting with its only
    classmate is one drug's property, not the class's."""
    conn, r = world
    c = w.klass(conn, r, "C206b")
    x, y = w.moiety(conn, r, "x2"), w.moiety(conn, r, "y2")
    w.member(conn, r, x, c)
    w.member(conn, r, y, c)
    w.moiety_ci(conn, r, y, x)
    assert ("interacts_with", "moiety", x) not in _cp(conn, c)


def test_a_direct_member_of_a_class_and_its_subclass_is_not_subclass_only(world):
    conn, r = world
    c, d = w.klass(conn, r, "C"), w.klass(conn, r, "D")
    w.parent(conn, r, d, c)
    m = w.moiety(conn, r, "both")
    w.member(conn, r, m, c)
    w.member(conn, r, m, d)
    assert conn.execute("SELECT count(*) FROM drugref.class_subclass_only_member "
                        "WHERE class_uuid = %s", (c,)).fetchone() == (0,)


def _class_rule(conn, k, cond, verdict):
    curation_property.record_property_ruling(
        conn, subject_class_uuid=k, relationship="may_treat", object_kind="condition",
        object_uuid=cond, verdict=verdict, reviewed_by="t", reviewed_against="t")


def test_a_class_does_not_hold_neither_suppresses_an_ancestor_nor_an_observation(world):
    """A class-subject does_not_hold only WITHDRAWS that class's own earlier holds. It is
    not a negation: an ancestor's assertion still passes down, and an observed property
    (a count over the members) still stands."""
    conn, r = world
    k, cond, _ = _cohort(conn, r, 2, 2)            # observed on K
    p = w.klass(conn, r, "P")
    w.parent(conn, r, k, p)
    _class_rule(conn, p, cond, "holds")            # asserted on the ancestor
    _class_rule(conn, k, cond, "holds")
    _class_rule(conn, k, cond, "does_not_hold")    # withdraws K's own holds only
    row = _cp(conn, k)[("may_treat", "condition", cond)]
    assert row[0] is True and row[1] == [p]        # still asserted, via P alone
    assert row[3] is True                          # still observed


def test_the_view_comments_carry_the_consumer_contract(conn):
    """The views are the contract (spec section 6): a consumer reading only the schema must
    learn the four-way lens split and the alert-path limit of interaction rulings."""
    def comment(v):
        return conn.execute("SELECT obj_description(%s::regclass)",
                            (f"drugref.{v}",)).fetchone()[0]
    for v in ("class_property_member", "class_member_peculiarity"):
        assert "FOUR-WAY" in comment(v) and "MUST render the not_confirmed" in comment(v)
    for v in ("member_property", "class_asserted_property"):
        assert "do NOT reach the prescribing-alert path" in comment(v) and "#204" in comment(v)
    assert "only WITHDRAWS" in comment("class_asserted_property")

"""class_profile_read -- the thin Python reader over the class-profile views. DB-gated."""
import uuid

import pytest

from drugref import class_profile_read as cpr
from tests import class_profile_world as w


def _world(conn):
    """Class K with members a, b, c, all contraindicated with Asthma (observed); only a
    with Gout. A sub-class S holds drug d, so d is a subclass-only member of K."""
    r = w.run(conn)
    k = w.klass(conn, r, "K class")
    s = w.klass(conn, r, "S class")
    w.parent(conn, r, s, k)
    a, b, c, d = (w.moiety(conn, r, n) for n in ("adrug", "bdrug", "cdrug", "ddrug"))
    for m in (a, b, c):
        w.member(conn, r, m, k)
    w.member(conn, r, d, s)
    ca = w.condition(conn, r, "Asthma")
    cb = w.condition(conn, r, "Gout")
    # a, b, c: 3 of 3 carry Asthma CI -> observed; only a carries Gout (peculiar to a).
    for m in (a, b, c):
        w.contraindication(conn, r, m, ca)
    w.contraindication(conn, r, a, cb)
    return dict(k=k, s=s, a=a, b=b, c=c, d=d, ca=ca, cb=cb, r=r)


def test_class_profile_reads_the_views(conn):
    x = _world(conn)
    # add a 4th member e without the record: 3/4 still observed (>= 75%), e not_confirmed
    r = x["r"]
    e = w.moiety(conn, r, "edrug")
    w.member(conn, r, e, x["k"])
    p = cpr.class_profile(conn, x["k"])
    assert p.class_name == "K class" and p.members == ("adrug", "bdrug", "cdrug", "edrug")
    asthma = next(cp for cp in p.properties if cp.prop.object_name == "Asthma")
    assert asthma.is_observed and not asthma.is_asserted
    assert (asthma.carriers, asthma.members) == (3, 4)
    assert asthma.not_confirmed == ("edrug",)
    assert [pl.object_name for pl in p.peculiarities["adrug"]] == ["Gout"]
    assert p.subclass_only == (("ddrug", "S class"),)


def test_an_asserted_property_names_its_classes_sources_and_exceptions(conn):
    """Asserted on K (MED-RT) and on its ancestor P (ONCHIGH); b ruled out. Through the
    lens on K itself, a member with no record reads plain `inherits` (no via)."""
    from drugref import curation_property
    r = w.run(conn)
    onc = w.run(conn, source="ONCHIGH")
    p, k = w.klass(conn, r, "P class"), w.klass(conn, r, "K class")
    w.parent(conn, r, k, p)
    x, a, b = (w.moiety(conn, r, n) for n in ("xdrug", "adrug", "bdrug"))
    for m in (a, b):
        w.member(conn, r, m, k)
    w.class_ci(conn, r, x, k)
    w.class_ci(conn, onc, x, p, source="ONCHIGH")
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=b, relationship="interacts_with", object_kind="moiety",
        object_uuid=x, verdict="does_not_hold", reviewed_by="t", reviewed_against="t")
    (cp,) = cpr.class_profile(conn, k).properties
    assert cp.asserted_via == ("K class", "P class")
    assert cp.asserted_by == ("MED-RT", "ONCHIGH")
    assert cp.confirmed_exceptions == ("bdrug",) and cp.not_confirmed == ()
    (la,) = cpr.drug_profile(conn, a).lenses
    assert [(pl.object_name, st) for pl, st in la.shared] == [("xdrug", "inherits")]


def test_a_medrt_pk_class_is_refused_not_shown_empty(conn):
    """Every view excludes MED-RT PK classes (#155); printing "0 members" would read as
    "this class has no members"."""
    r = w.run(conn)
    pk = w.klass(conn, r, "Clearance", concept_type="PK")
    w.member(conn, r, w.moiety(conn, r, "pkdrug"), pk, relationship="has_PK")
    with pytest.raises(LookupError, match="not profiled"):
        cpr.class_profile(conn, pk)


def test_unknown_class_and_moiety_raise_lookuperror(conn):
    with pytest.raises(LookupError):
        cpr.class_profile(conn, uuid.uuid4())
    with pytest.raises(LookupError):
        cpr.drug_profile(conn, uuid.uuid4())


def test_drug_profile_defaults_to_every_tag(conn):
    x = _world(conn)
    r = x["r"]
    k2 = w.klass(conn, r, "A second class")
    w.member(conn, r, x["a"], k2)
    p = cpr.drug_profile(conn, x["a"])
    assert p.name == "adrug"
    assert [n for _, n in p.class_tags] == ["A second class", "K class"]
    assert [ln.class_name for ln in p.lenses] == ["A second class", "K class"]
    k_lens = p.lenses[1]
    assert [pl.object_name for pl in k_lens.peculiar] == ["Gout"]
    assert [(pl.object_name, st) for pl, st in k_lens.shared] == [("Asthma", "carries")]


def test_a_foreign_lens_is_refused(conn):
    x = _world(conn)
    with pytest.raises(ValueError):
        cpr.drug_profile(conn, x["a"], [x["s"]])


def _names(lines):
    return [pl.object_name for pl in lines]


def test_a_lens_splits_four_ways(conn):
    """Shared / not confirmed / confirmed exception / peculiar: the two middle groups are
    rendered, never dropped (a dropped not_confirmed reads as "does not apply")."""
    from drugref import curation_property
    r = w.run(conn)
    k, ca = w.klass(conn, r, "K class"), w.condition(conn, r, "Asthma")
    carriers = [w.moiety(conn, r, f"c{i}") for i in range(6)]
    e, f = w.moiety(conn, r, "edrug"), w.moiety(conn, r, "fdrug")
    for m in (*carriers, e, f):
        w.member(conn, r, m, k)
    for m in (*carriers, f):
        w.contraindication(conn, r, m, ca)
    # f is stated AND ruled out, so it leaves the numerator: 6 of 8 is still observed.
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=f, relationship="CI_with", object_kind="condition",
        object_uuid=ca, verdict="does_not_hold", reviewed_by="t",
        reviewed_against="t")
    (le,) = cpr.drug_profile(conn, e).lenses
    assert _names(le.not_confirmed) == ["Asthma"] and le.confirmed_exceptions == ()
    assert le.shared == () and le.peculiar == ()
    (lf,) = cpr.drug_profile(conn, f).lenses
    assert _names(lf.confirmed_exceptions) == ["Asthma"] and lf.not_confirmed == ()


def test_via_is_named_for_inherits_only(conn):
    """An ancestor's assertion: a drug with no record of its own `inherits via P`; a drug
    with its own record `carries` -- its own record, so no via."""
    r = w.run(conn)
    p, k = w.klass(conn, r, "P class"), w.klass(conn, r, "K class")
    w.parent(conn, r, k, p)
    x, a, b = (w.moiety(conn, r, n) for n in ("xdrug", "adrug", "bdrug"))
    w.class_ci(conn, r, x, p)
    for m in (a, b):
        w.member(conn, r, m, k)
    w.moiety_ci(conn, r, b, x)                     # b's own interacts_with x
    (la,) = cpr.drug_profile(conn, a).lenses
    assert [(pl.object_name, st) for pl, st in la.shared] == [("xdrug", "inherits via P class")]
    (lb,) = cpr.drug_profile(conn, b).lenses
    assert [(pl.object_name, st) for pl, st in lb.shared] == [("xdrug", "carries")]


def test_a_narrowed_lens_returns_only_that_class(conn):
    x = _world(conn)
    k2 = w.klass(conn, x["r"], "A second class")
    w.member(conn, x["r"], x["a"], k2)
    p = cpr.drug_profile(conn, x["a"], [x["k"]])
    assert [ln.class_name for ln in p.lenses] == ["K class"]
    assert len(p.class_tags) == 2                  # the tags are still all listed


def test_the_query_count_does_not_grow_with_the_lenses(conn, monkeypatch):
    x = _world(conn)
    r = x["r"]
    calls = []
    real = conn.execute

    def counting(*a, **kw):
        calls.append(a[0])
        return real(*a, **kw)

    def count():
        calls.clear()
        monkeypatch.setattr(conn, "execute", counting)
        cpr.drug_profile(conn, x["a"])
        monkeypatch.setattr(conn, "execute", real)
        return len(calls)

    one = count()
    for i in range(3):
        w.member(conn, r, x["a"], w.klass(conn, r, f"extra {i}"))
    assert count() == one


def test_a_drug_with_no_class_tag_shows_its_own_properties(conn):
    r = w.run(conn)
    m, c = w.moiety(conn, r, "loner"), w.condition(conn, r, "Gouty")
    w.contraindication(conn, r, m, c)
    p = cpr.drug_profile(conn, m)
    assert p.lenses == () and _names(p.own_properties) == ["Gouty"]
    tagged = cpr.drug_profile(conn, _world(conn)["a"])
    assert tagged.own_properties == ()

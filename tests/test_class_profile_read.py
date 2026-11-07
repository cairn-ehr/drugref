"""class_profile_read -- the thin Python reader over the class-profile views. DB-gated."""
import uuid

import pytest

from drugref import class_profile_read as cpr
from tests import class_profile_world as w


def _world(conn):
    """Class K with members a, b, c (a, b contraindicated with the observed condition,
    c not); a sub-class S holding drug d, so d is a subclass-only member of K."""
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
    # drop c's carriage by making a 4th member that lacks it: 3/4 still observed (>=75%)
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

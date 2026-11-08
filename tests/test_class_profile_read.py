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
    (d,) = p.subclass_only
    assert (d.name, d.via, d.inherits, d.confirmed_exceptions) == (
        "ddrug", "S class", (), ())   # K asserts nothing, so nothing passes down


def test_a_subclass_only_drug_inherits_the_asserted_properties(conn):
    """Spec §4: an ASSERTED property passes down to a subclass-only drug and is shown
    beside its entry. A confirmed exception does not pass down, and a drug never
    inherits an interaction with itself."""
    from drugref import curation_property
    r = w.run(conn)
    k, s = w.klass(conn, r, "K class"), w.klass(conn, r, "S class")
    w.parent(conn, r, s, k)
    x, y, d = (w.moiety(conn, r, n) for n in ("xdrug", "ydrug", "ddrug"))
    for m in (x, d):
        w.member(conn, r, m, s)          # x and d reach K only through S
    w.class_ci(conn, r, x, k)            # K asserts: interacts_with xdrug
    w.class_ci(conn, r, y, k)            # K asserts: interacts_with ydrug
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=d, relationship="interacts_with", object_kind="moiety",
        object_uuid=y, verdict="does_not_hold", reviewed_by="t", reviewed_against="t")
    by_name = {e.name: e for e in cpr.class_profile(conn, k).subclass_only}
    assert [pl.object_name for pl in by_name["ddrug"].inherits] == ["xdrug"]
    assert [pl.object_name for pl in by_name["ddrug"].confirmed_exceptions] == ["ydrug"]
    assert [pl.object_name for pl in by_name["xdrug"].inherits] == ["ydrug"]
    assert by_name["xdrug"].confirmed_exceptions == ()


def test_a_subclass_only_drug_never_inherits_an_observed_property(conn):
    """Spec §4: observed never passes down. K's direct members all carry Asthma CI
    (observed only) and Gout CI (observed AND asserted); d, in subclass S, inherits
    only Gout."""
    x = _world(conn)
    r, k = x["r"], x["k"]
    for m in (x["b"], x["c"]):
        w.contraindication(conn, r, m, x["cb"])   # Gout now 3 of 3: observed too
    from drugref import curation_property
    curation_property.record_property_ruling(       # ...and asserted on K
        conn, subject_class_uuid=k, relationship="CI_with", object_kind="condition",
        object_uuid=x["cb"], verdict="holds", reviewed_by="t", reviewed_against="t")
    p = cpr.class_profile(conn, k)
    by = {cp.prop.object_name: cp for cp in p.properties}
    assert by["Asthma"].is_observed and not by["Asthma"].is_asserted
    assert by["Gout"].is_observed and by["Gout"].is_asserted
    (d,) = p.subclass_only
    assert [pl.object_name for pl in d.inherits] == ["Gout"]


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


# --- "What labels say about this class" (SPL class mentions, design 6.3/6.4) -------------
def _spl_world(conn):
    """An SPL ingest run to hang label evidence on."""
    from drugref.ingest import spl
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) "
        "VALUES (%s, '2026-08-22', 'deadbeef', %s) RETURNING ingest_run_id",
        (spl.SOURCE, spl.WRITER)).fetchone()[0]


def _mention(conn, r, drug, klass, *labels, level="avoid", mixed=False,
             ambiguous=False, key="a", eff="20260101", quote=None):
    """Labels `labels` (subject = drug) sharing one wording that mentions `klass`.
    Level lives on the wording's span, so a different reading needs a different `key`."""
    from drugref import spl_evidence
    from drugref.ingest import spl
    tk = key * 64
    if not _has_wording(conn, r, tk):      # the same wording may gain a second class
        spl_evidence.write_wordings(
            conn, [spl_evidence.WordingRow(text_key=tk, char_length=1_000,
                                           label_count=len(labels))],
            ingest_run_id=r, source=spl.SOURCE)
    for set_id in labels:
        spl_evidence.write_labels(
            conn, [spl_evidence.LabelRow(set_id=set_id, version="1",
                                         effective_time=eff,
                                         product_type=None, text_key=tk)],
            ingest_run_id=r, source=spl.SOURCE)
        conn.execute(
            "INSERT INTO drugref.spl_label_subject (ingest_run, source, set_id, version, "
            "subject_ordinal, moiety_uuid, route) "
            "VALUES (%s,'SPL',%s,'1',0,%s,'openfda_unii')", (r, set_id, drug))
    conn.execute(
        "INSERT INTO drugref.spl_class_occurrence (ingest_run, source, text_key, "
        "char_start, char_end, class_uuid, match_ambiguous, match_form, advice_level, "
        "advice_mixed, advice_rule, advice_scope, scope_start, scope_end) "
        "VALUES (%s,'SPL',%s,10,15,%s,%s,'stored',%s,%s,%s,'sentence',0,100)",
        (r, tk, klass, ambiguous, level, mixed,
         None if level == "not_read" else "x.y"))
    if quote:                              # a window covering the span [10, 15)
        conn.execute("INSERT INTO drugref.spl_wording_quote (ingest_run, source, "
                     "text_key, ordinal, char_start, char_end, quote_text) "
                     "VALUES (%s,'SPL',%s,0,5,20,%s)", (r, tk, quote))


def _has_wording(conn, r, tk):
    return conn.execute("SELECT 1 FROM drugref.spl_wording WHERE ingest_run = %s "
                        "AND text_key = %s", (r, tk)).fetchone() is not None


def test_label_statements_ordered_and_flagged(conn):
    x = _world(conn)
    r, k = x["r"], x["k"]
    sr = _spl_world(conn)
    zdrug, ydrug, xdrug = (w.moiety(conn, r, n) for n in ("zdrug", "ydrug", "xdrug"))
    _mention(conn, sr, zdrug, k, "S1", level="monitor", key="b")
    _mention(conn, sr, ydrug, k, "S2", "S3", level="avoid", mixed=True, key="c")
    _mention(conn, sr, xdrug, k, "S4", level="not_read", key="d")   # unranked: last
    _mention(conn, sr, x["a"], k, "S5", level="avoid", key="e")  # member: its own class
    got = cpr.class_profile(conn, k).label_statements
    # Strict check of the documented order: rank, then label_count desc, then name.
    assert [s.drug for s in got] == ["ydrug", "adrug", "zdrug", "xdrug"]
    assert all(s.ruling is None and not s.ambiguous for s in got)


def test_ambiguous_statement_on_both_classes(conn):
    x = _world(conn)
    r, k, s = x["r"], x["k"], x["s"]
    sr = _spl_world(conn)
    drug = w.moiety(conn, r, "qdrug")
    # One span whose name folds onto two classes: a row each, both flagged ambiguous.
    _mention(conn, sr, drug, k, "S1", ambiguous=True)
    _mention(conn, sr, drug, s, ambiguous=True)     # same wording and label
    for cls in (k, s):
        (st,) = cpr.class_profile(conn, cls).label_statements
        assert st.drug == "qdrug" and st.ambiguous


def test_does_not_hold_ruling_still_shown(conn):
    from drugref import curation_property
    x = _world(conn)
    r, k = x["r"], x["k"]
    sr = _spl_world(conn)
    drug = w.moiety(conn, r, "qdrug")
    _mention(conn, sr, drug, k, "S1")
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=drug, relationship="interacts_with",
        object_kind="class", object_uuid=k, verdict="does_not_hold",
        reviewed_by="t", reviewed_against="t")
    (st,) = cpr.class_profile(conn, k).label_statements
    assert st.ruling == "does_not_hold"      # visible, marked -- never dropped


def test_label_statement_not_in_asserted_properties(conn):
    x = _world(conn)
    r, k = x["r"], x["k"]
    before = cpr.class_profile(conn, k).properties
    sr = _spl_world(conn)
    _mention(conn, sr, w.moiety(conn, r, "qdrug"), k, "S1", level="contraindicated")
    p = cpr.class_profile(conn, k)
    assert len(p.label_statements) == 1
    assert p.properties == before            # machine-read stays separate (owner decision)


def test_statement_cites_one_label_at_the_strongest_level(conn):
    x = _world(conn)
    r, k = x["r"], x["k"]
    sr = _spl_world(conn)
    drug = w.moiety(conn, r, "qdrug")
    _mention(conn, sr, drug, k, "S1", level="monitor", key="b", eff="20260901")
    _mention(conn, sr, drug, k, "S3", level="avoid", key="c", eff="20260101")
    _mention(conn, sr, drug, k, "S2", level="avoid", key="d", eff="20260601",
             quote="x" * 15)
    (st,) = cpr.class_profile(conn, k).label_statements
    # 3 labels in all, 2 at the strongest level; of those S2 is the more recent.
    assert (st.label_count, st.level_labels) == (3, 2)
    assert (st.cite_set_id, st.cite_version, st.quote) == ("S2", "1", "x" * 15)


def test_citation_tie_breaks_on_set_id_and_quote_is_none_without_window(conn):
    x = _world(conn)
    r, k = x["r"], x["k"]
    sr = _spl_world(conn)
    drug = w.moiety(conn, r, "qdrug")
    _mention(conn, sr, drug, k, "S9", "S4", "S7")      # same time: lowest set_id wins
    (st,) = cpr.class_profile(conn, k).label_statements
    assert (st.level_labels, st.cite_set_id, st.quote) == (3, "S4", None)


def test_superseded_ruling_is_ignored(conn):
    from drugref import curation_property
    x = _world(conn)
    r, k = x["r"], x["k"]
    sr = _spl_world(conn)
    drug = w.moiety(conn, r, "qdrug")
    _mention(conn, sr, drug, k, "S1")
    for verdict in ("does_not_hold", "holds"):          # the second supersedes the first
        curation_property.record_property_ruling(
            conn, subject_moiety_uuid=drug, relationship="interacts_with",
            object_kind="class", object_uuid=k, verdict=verdict,
            reviewed_by="t", reviewed_against="t")
    (st,) = cpr.class_profile(conn, k).label_statements
    assert st.ruling == "holds"                         # one row, the live verdict


def test_ruling_on_another_class_is_ignored(conn):
    from drugref import curation_property
    x = _world(conn)
    r, k, s = x["r"], x["k"], x["s"]
    sr = _spl_world(conn)
    drug = w.moiety(conn, r, "qdrug")
    _mention(conn, sr, drug, k, "S1")
    curation_property.record_property_ruling(
        conn, subject_moiety_uuid=drug, relationship="interacts_with",
        object_kind="class", object_uuid=s, verdict="does_not_hold",
        reviewed_by="t", reviewed_against="t")
    (st,) = cpr.class_profile(conn, k).label_statements
    assert st.ruling is None

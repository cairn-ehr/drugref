# tests/test_spl_class_evidence.py
"""Class evidence rows: pure matching + advice reading, the loader, and the writer.

Wordings are synthetic (rule 6): never real label text.
"""
import dataclasses

from drugref import spl_class_evidence as ce, spl_evidence
from drugref.ingest import spl, spl_match
from tests import class_profile_world as w

CU = "11111111-1111-1111-1111-111111111111"
CU2 = "22222222-2222-2222-2222-222222222222"


def _vocab(*pairs):
    return ce.class_vocabulary([
        spl_match.Entry(kind=spl_match.KIND_CLASS, key=k, display=k, class_uuid=u)
        for k, u in pairs])


def test_class_rows_for_wording_reads_advice():
    text = "Use with strong CYP1A2 inhibitors is contraindicated."
    (row,) = ce.class_rows_for_wording("k", text, _vocab(("strong CYP1A2 inhibitors", CU)))
    assert (row.class_uuid, row.advice_level, row.match_form, row.match_ambiguous) == (
        CU, "contraindicated", "stored", False)
    assert text[row.char_start:row.char_end] == "strong CYP1A2 inhibitors"
    assert row.scope_start <= row.char_start and row.scope_end >= row.char_end


def test_class_rows_for_wording_ambiguous_two_rows():
    rows = ce.class_rows_for_wording(
        "k", "Avoid statins.", _vocab(("statins", CU), ("statins", CU2)))
    assert {r.class_uuid for r in rows} == {CU, CU2}
    assert all(r.match_ambiguous for r in rows)


def test_class_rows_empty_when_no_class():
    assert ce.class_rows_for_wording("k", "Nothing here.", _vocab(("statins", CU))) == []


def test_row_fields_follow_db066_column_order():
    assert [f.name for f in dataclasses.fields(ce.ClassOccurrenceRow)] == [
        "text_key", "char_start", "char_end", "class_uuid", "match_ambiguous",
        "match_form", "advice_level", "advice_mixed", "advice_rule", "advice_scope",
        "scope_start", "scope_end"]


def _run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, writer) "
        "VALUES (%s, '2026-08-22', 'deadbeef', %s) RETURNING ingest_run_id",
        (spl.SOURCE, spl.WRITER)).fetchone()[0]


def test_load_class_entries_excludes_medrt_pk(conn):
    r = _run(conn)
    w.klass(conn, r, "Hidden PK thing [PK]", concept_type="PK")
    cyp = w.klass(conn, r, "CYP3A strong inhibitor [FDA-CYP]", source="FDA-CYP",
                  concept_type="PK")
    entries = ce.load_class_entries(conn)
    assert {e.class_uuid for e in entries} == {str(cyp)}
    assert {e.key for e in entries} >= {"CYP3A strong inhibitor", "strong CYP3A inhibitor"}
    assert all(e.kind == spl_match.KIND_CLASS for e in entries)


def test_write_and_clear_class_occurrences(conn):
    r = _run(conn)
    key = "a" * 64
    spl_evidence.write_wordings(
        conn, [spl_evidence.WordingRow(text_key=key, char_length=1_000, label_count=1)],
        ingest_run_id=r, source=spl.SOURCE)
    k = w.klass(conn, r, "Statins")
    row = ce.ClassOccurrenceRow(
        text_key=key, char_start=1, char_end=8, class_uuid=str(k), match_ambiguous=False,
        match_form="stored", advice_level="avoid", advice_mixed=False,
        advice_rule="avoid.x", advice_scope="sentence", scope_start=0, scope_end=20)
    assert spl_evidence.write_class_occurrences(
        conn, [row], ingest_run_id=r, source=spl.SOURCE) == 1
    count = "SELECT count(*) FROM drugref.spl_class_occurrence"
    assert conn.execute(count).fetchone() == (1,)
    spl_evidence.clear_source_spl(conn, spl.SOURCE)
    assert conn.execute(count).fetchone() == (0,)


def test_moiety_spans_inside_class_spans_boundaries():
    inside = ce.moiety_spans_inside_class_spans
    cls = [(10, 20)]
    assert inside([(10, 20)], cls) == 1          # equal
    assert inside([(12, 15)], cls) == 1          # nested
    assert inside([(10, 14)], cls) == 1          # shared start
    assert inside([(5, 15)], cls) == 0           # partial overlap
    assert inside([(5, 10), (20, 25)], cls) == 0  # edge-touching only
    assert inside([(12, 15)], [(10, 20), (11, 30)]) == 1  # counted once


def test_class_pass_for_wording_rows_and_overlap():
    text = "Avoid rifampin inducers and rifampin."
    rows, inside, dropped, heads = ce.class_pass_for_wording(
        "k", text, _vocab(("rifampin inducers", CU)), [(6, 14), (30, 37)])
    assert [text[r.char_start:r.char_end] for r in rows] == ["rifampin inducers"]
    assert inside == 1
    assert ce.class_pass_for_wording(
        "k", text, _vocab(("statins", CU)), [(6, 14)]) == ([], 0, 0, 0)


def test_a_class_match_spanning_a_sentence_boundary_is_dropped_and_counted():
    """Tokenising ignores punctuation, so 'Avoid Potassium. Salts are fine.' matches the
    class 'Potassium Salts' across a sentence end -- a false mention (R17)."""
    text = "Avoid Potassium. Salts are fine."
    rows, inside, dropped, _ = ce.class_pass_for_wording(
        "k", text, _vocab(("Potassium Salts", CU)), [])
    assert (rows, dropped) == ([], 1)
    # ... while the same words inside one sentence are kept.
    rows, _, dropped, _ = ce.class_pass_for_wording(
        "k", "Avoid Potassium Salts today.", _vocab(("Potassium Salts", CU)), [])
    assert len(rows) == 1 and dropped == 0


def test_split_shadowed_keys_drops_keys_that_are_moiety_names():
    ent = lambda k, u: spl_match.Entry(  # noqa: E731
        kind=spl_match.KIND_CLASS, key=k, display=k, class_uuid=u)
    kept, shadowed = ce.split_shadowed_keys(
        [ent("Calcium", CU), ent("Statins", CU2)], {"calcium", "warfarin"})
    assert [e.key for e in kept] == ["Statins"]
    assert shadowed == ["Calcium"]


# ---- R19/P2: a class word that is an adjective on a non-drug head noun ---------

def test_followed_by_non_drug_head_effect_words():
    """The class word describes an EFFECT (or is part of another class name), so the
    span is not a mention of the class: 'enhance the anticoagulant effect of X'."""
    f = ce.followed_by_non_drug_head
    for text, word in (("the anticoagulant effect of X", "anticoagulant"),
                       ("anticoagulant effects", "anticoagulant"),
                       ("the analgesic doses of Y", "analgesic"),
                       ("an analgesic dose of Y", "analgesic"),
                       ("the anticholinergic (drying) effects", "anticholinergic"),
                       ("systemic corticosteroid side effects", "corticosteroid"),
                       ("corticosteroid side-effects", "corticosteroid"),
                       ("a synergistic anticonvulsant action", "anticonvulsant"),
                       ("anticonvulsant actions", "anticonvulsant"),
                       ("an insulin secretagogue", "insulin"),
                       ("Insulin Secretagogues", "Insulin")):
        assert f(text, text.index(word) + len(word)), text


def test_followed_by_non_drug_head_keeps_real_class_phrases():
    """R19 keeps 'activity'/'properties': 'drugs with anticholinergic activity' names a
    real group, and dropping it would under-warn. Bare 'dose' is kept too: 'the PPI dose
    should not exceed' is dose advice ABOUT the class."""
    f = ce.followed_by_non_drug_head
    for text, word in (("drugs with anticholinergic activity", "anticholinergic"),
                       ("agents with anticholinergic properties", "anticholinergic"),
                       ("the proton pump inhibitor dose should not exceed", "inhibitor"),
                       ("reduce the diuretic dosage", "diuretic"),
                       ("anticoagulants such as warfarin", "anticoagulants"),
                       ("diuretic effectiveness", "diuretic"),
                       ("anticoagulant therapy", "anticoagulant"),
                       ("insulin", "insulin")):
        end = text.index(word) + len(word)
        assert not f(text, end), text


def test_a_class_span_on_an_effect_word_is_dropped_and_counted():
    text = "Drug X may enhance the anticoagulant effect of drug Y. Avoid anticoagulants."
    rows, _, boundary, heads = ce.class_pass_for_wording(
        "k", text, _vocab(("anticoagulant", CU), ("anticoagulants", CU)), [])
    assert [text[r.char_start:r.char_end] for r in rows] == ["anticoagulants"]
    assert (boundary, heads) == (0, 1)


# ---- R19/P1: class keys equal to a UNII official name --------------------------

_NAMES_HEADER = "Name\tTYPE\tUNII\tDisplay Name\n"


def test_official_names_reads_type_of_for_held_uniis_only():
    lines = [_NAMES_HEADER,
             "PARACETAMOL-LIKE\tof\tU1\tPARACETAMOL-LIKE\n",   # official, held
             "WIDGETOL\tcn\tU1\tPARACETAMOL-LIKE\n",           # common name: not official
             "GADGETOL\tof\tU9\tGADGETOL\n"]                   # official, NOT held
    assert ce.official_names(lines, {"U1"}) == {"PARACETAMOL-LIKE"}


def test_official_names_refuses_a_file_without_its_columns():
    """UNII_Records sits beside UNII_Names; handing over the wrong file must raise,
    not silently yield no names (and so drop nothing)."""
    import pytest
    with pytest.raises(ValueError, match="TYPE"):
        ce.official_names(["UNII\tDisplay Name\tRXCUI\n"], {"U1"})


def test_a_names_file_yielding_no_official_name_for_held_uniis_is_refused(tmp_path):
    """The right columns but 0 names (a changed TYPE code, a UNII format drift)
    would skip R19/P1 with no warning; it raises instead."""
    import pytest
    f = tmp_path / "UNII_Names.txt"
    f.write_text(_NAMES_HEADER + "WIDGETOL\tOF\tU1\tWIDGETOL\n")
    with pytest.raises(ValueError, match="0 official names"):
        ce.read_official_name_rows(f, {"U1"})


def test_official_name_rows_keep_each_names_unii():
    """#213 needs the UNII too: the drug pass maps the name onto its moiety."""
    lines = [_NAMES_HEADER, "ACETAMINOPHEN\tof\tU1\tX\n", "NIACIN\tof\tU2\tX\n",
             "TYLENOL\tbn\tU1\tX\n"]
    assert ce.official_name_rows(lines, {"U1", "U2"}) == [
        ("ACETAMINOPHEN", "U1"), ("NIACIN", "U2")]


def test_a_stray_quote_does_not_swallow_the_following_rows():
    """UNII names carry no quoting convention (ingest/unii.py's QUOTE_NONE trap)."""
    lines = [_NAMES_HEADER, '"5-NUCLEOTIDE\tof\tU2\tX\n', "PARACETAMOL\tof\tU1\tX\n"]
    assert ce.official_names(lines, {"U1", "U2"}) == {'"5-NUCLEOTIDE', "PARACETAMOL"}


def test_class_key_shadowing_counts_moiety_names_and_official_names_apart():
    ent = lambda k, u: spl_match.Entry(  # noqa: E731
        kind=spl_match.KIND_CLASS, key=k, display=k, class_uuid=u)
    kept, keys = ce.drop_shadowed_class_keys(
        [ent("Calcium", CU), ent("Widgetol", CU), ent("Widgetols", CU), ent("Statins", CU2)],
        moiety_names={"calcium"}, official_names={"WIDGETOL"})
    # Per KEY, as R16(b): the plural 'Widgetols' is a different key and survives.
    assert [e.key for e in kept] == ["Widgetols", "Statins"]
    assert (keys.shadowed_by_moieties, keys.shadowed_by_official_names) == (["Calcium"], ["Widgetol"])
    assert keys.kept == 2


def test_load_class_entries_skips_the_named_exclusion(conn):
    r = _run(conn)
    w.klass(conn, r, "Hormones", source="MeSH", concept_type="PA")
    kept = w.klass(conn, r, "Hormone Antagonists", source="MeSH", concept_type="PA")
    assert {e.class_uuid for e in ce.load_class_entries(conn)} == {str(kept)}


def test_class_vocabulary_without_unii_names_warns_that_p1_was_skipped():
    """`unii_names=None` skips R19/P1 silently unless the run summary says so: the
    ingest prints `str(keys)`, so the warning travels on that line."""
    class _NoClasses:  # load_class_entries only iterates conn.execute(...)
        def execute(self, _sql):
            return []

    _, keys = ce.class_vocabulary_for_run(_NoClasses(), [], None)
    assert "WARNING" in str(keys) and "unii" in str(keys).lower()
    assert ce.ClassKeyCounts(0, [], []).official_names_skipped is False

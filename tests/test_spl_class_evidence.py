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
    rows, inside = ce.class_pass_for_wording(
        "k", text, _vocab(("rifampin inducers", CU)), [(6, 14), (30, 37)])
    assert [text[r.char_start:r.char_end] for r in rows] == ["rifampin inducers"]
    assert inside == 1
    assert ce.class_pass_for_wording("k", text, _vocab(("statins", CU)), [(6, 14)]) == ([], 0)

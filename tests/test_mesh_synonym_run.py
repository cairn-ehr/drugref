"""db/071 and `mesh_synonym_run` (#231): MeSH entry terms as search-only names.

Cairn's allergy pick searches `drugref.moiety_name`. These tests pin the new
table, the view's third branch (`mesh_synonym`, hiding a synonym the same moiety
already answers to), and the orchestrator's refusals. The SPL drug pass never
reads this table: label-match keys stay the strict official-name set.

DB-gated; the ingest commits, so each test starts from a TRUNCATE.
"""
import contextlib
import gzip
import re
import uuid

import pytest

from drugref import cli, cli_status, db, provenance
from drugref.ingest import mesh_synonym_run

FURO, PARA = uuid.UUID(int=0xC001), uuid.UUID(int=0xC002)
TABLES = ("drugref.moiety_mesh_synonym, drugref.moiety_official_name, "
          "drugref.identity_claim, drugref.substance_moiety, drugref.ingest_run")


def _record(ui, unii, *terms, tag="DescriptorRecord"):
    """One record; `tag` SupplementalRecord makes a supp (C...) record."""
    ui_tag = "DescriptorUI" if tag == "DescriptorRecord" else "SupplementalRecordUI"
    body = "".join(f'<Term IsPermutedTermYN="N"><String>{t}</String></Term>'
                   for t in terms)
    return (f'<{tag}><{ui_tag}>{ui}</{ui_tag}><ConceptList>'
            f'<Concept PreferredConceptYN="Y"><RegistryNumberList><RegistryNumber>'
            f'{unii}</RegistryNumber></RegistryNumberList><TermList>{body}</TermList>'
            f'</Concept></ConceptList></{tag}>')


def _supp(*records):
    return "<SupplementalRecordSet>" + "".join(records) + "</SupplementalRecordSet>"


# 'furosemide' and 'ACETAMINOPHEN' repeat a name the moiety already has (display,
# official); the view must not list them twice.
DESC = ("<DescriptorRecordSet>"
        + _record("D005665", "UFURO00000", "Furosemide", "Frusemide")
        + _record("D000082", "UPARA00000", "Acetaminophen", "APAP")
        + "</DescriptorRecordSet>")
# One supp record whose UNII no moiety claims: read (a supp file with no record is
# refused) but linking nothing, so the desc-only figures below hold.
SUPP = _supp(_record("C000001", "UNKNOWNUNI", "Nothing", tag="SupplementalRecord"))


@pytest.fixture(autouse=True)
def _clean(conn, monkeypatch):
    conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
    conn.commit()
    monkeypatch.setattr(cli.db, "connect", lambda dsn: contextlib.nullcontext(conn))
    yield
    conn.rollback()
    conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
    conn.commit()


def _seed(conn) -> None:
    """Two moieties with their UNII claims, and paracetamol's official name."""
    run = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer) VALUES ('UNII', 'r', 't', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    for moiety, name, unii in ((FURO, "furosemide", "UFURO00000"),
                               (PARA, "paracetamol", "UPARA00000")):
        conn.execute("INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
                     "first_seen_ingest) VALUES (%s, %s, %s)", (moiety, name, run))
        conn.execute("INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, "
                     "ingest_run) VALUES (%s, 'UNII', %s, %s)", (moiety, unii, run))
    conn.execute("INSERT INTO drugref.moiety_official_name (moiety_uuid, name, "
                 "ingest_run) VALUES (%s, 'ACETAMINOPHEN', %s)", (PARA, run))
    conn.commit()


def _files(tmp_path, desc=DESC, supp=SUPP):
    (tmp_path / "desc.xml").write_text(desc)
    (tmp_path / "supp.xml").write_text(supp)
    return tmp_path / "desc.xml", tmp_path / "supp.xml"


def _ingest(conn, tmp_path, desc=DESC, release="2026", supp=SUPP):
    desc_path, supp_path = _files(tmp_path, desc, supp)
    return mesh_synonym_run.ingest_mesh_synonyms(
        conn, desc_path=desc_path, supp_path=supp_path, upstream_release=release)


def _runs(conn) -> int:
    return conn.execute("SELECT count(*) FROM drugref.ingest_run WHERE writer = "
                        "'mesh_synonym_run'").fetchone()[0]


def _stored(conn):
    return sorted(conn.execute("SELECT moiety_uuid, name, mesh_ui FROM "
                               "drugref.moiety_mesh_synonym").fetchall(),
                  key=lambda r: r[1])


# ---- the schema -----------------------------------------------------------------

def test_the_writer_vocabulary_admits_mesh_synonym_run(conn):
    live = set(re.findall(r"'([^']+)'", db.constraint_definition(
        conn, "ingest_run", "ingest_run_writer")))
    assert "mesh_synonym_run" in live and set(provenance.WRITERS) == live


# ---- the ingest and the view ----------------------------------------------------

def test_every_preferred_term_is_stored_with_its_record(conn, tmp_path):
    _seed(conn)
    summary = _ingest(conn, tmp_path)
    assert _stored(conn) == [(PARA, "APAP", "D000082"),
                             (PARA, "Acetaminophen", "D000082"),
                             (FURO, "Frusemide", "D005665"),
                             (FURO, "Furosemide", "D005665")]
    assert summary.stored == 4
    run = conn.execute("SELECT r.source, r.writer FROM drugref.moiety_mesh_synonym s "
                       "JOIN drugref.ingest_run r ON r.ingest_run_id = s.ingest_run "
                       "LIMIT 1").fetchone()
    assert run == ("MeSH", "mesh_synonym_run")


def test_the_view_finds_the_ban_name_and_lists_no_name_twice(conn, tmp_path):
    """#231's case: 'frusemide' reaches furosemide. 'Furosemide' and
    'Acetaminophen' are hidden in the view (case-insensitively the display and
    official names), so a picker does not show one moiety twice by one name."""
    _seed(conn)
    _ingest(conn, tmp_path)
    rows = sorted(conn.execute(
        "SELECT moiety_uuid, name, name_kind FROM drugref.moiety_name").fetchall(),
        key=lambda r: (r[2], r[1]))
    assert rows == [(FURO, "furosemide", "display"), (PARA, "paracetamol", "display"),
                    (PARA, "APAP", "mesh_synonym"), (FURO, "Frusemide", "mesh_synonym"),
                    (PARA, "ACETAMINOPHEN", "unii_official")]


def test_a_rebuild_drops_a_term_the_new_release_no_longer_has(conn, tmp_path):
    _seed(conn)
    _ingest(conn, tmp_path)
    _ingest(conn, tmp_path, "<DescriptorRecordSet>"
            + _record("D005665", "UFURO00000", "Frusemide") + "</DescriptorRecordSet>",
            release="2027")
    assert _stored(conn) == [(FURO, "Frusemide", "D005665")]


def test_a_failure_after_the_run_opens_keeps_the_previous_build(conn, tmp_path,
                                                                 monkeypatch):
    _seed(conn)
    _ingest(conn, tmp_path)

    def boom(*_args):
        raise RuntimeError("boom")
    monkeypatch.setattr(mesh_synonym_run.provenance, "finish_run", boom)
    with pytest.raises(RuntimeError, match="boom"):
        _ingest(conn, tmp_path)
    assert len(_stored(conn)) == 4


def test_a_run_linking_nothing_is_refused_and_keeps_the_previous_build(conn,
                                                                        tmp_path):
    _seed(conn)
    _ingest(conn, tmp_path)
    with pytest.raises(ValueError, match="0 synonyms"):
        _ingest(conn, tmp_path, "<DescriptorRecordSet>"
                + _record("D9", "UNKNOWNUNI", "Nothing") + "</DescriptorRecordSet>")
    assert len(_stored(conn)) == 4
    assert _runs(conn) == 1  # refused before open_run: no dead run row


def test_the_desc_file_passed_as_supp_is_refused(conn, tmp_path):
    """The review of #233: a copy-paste --supp desc2026.xml must not commit a
    build missing every supplementary synonym."""
    _seed(conn)
    _ingest(conn, tmp_path)
    desc_path, _ = _files(tmp_path)
    with pytest.raises(ValueError, match="no SupplementalRecord"):
        mesh_synonym_run.ingest_mesh_synonyms(
            conn, desc_path=desc_path, supp_path=desc_path, upstream_release="2026")
    assert len(_stored(conn)) == 4 and _runs(conn) == 1


def test_supplementary_records_are_read_after_the_descriptors(conn, tmp_path):
    """A supp name is stored with its C... UI; a case variant of a descriptor
    term keeps the descriptor's spelling and UI."""
    _seed(conn)
    _ingest(conn, tmp_path, supp=_supp(_record(
        "C000009", "UFURO00000", "FRUSEMIDE", "Furosemid", tag="SupplementalRecord")))
    furo = [row for row in _stored(conn) if row[0] == FURO]
    assert furo == [(FURO, "Frusemide", "D005665"), (FURO, "Furosemid", "C000009"),
                    (FURO, "Furosemide", "D005665")]


def test_the_shipped_exclusions_apply(conn, tmp_path):
    """'Glucose' is on the shipped exclusion list: never a search name."""
    _seed(conn)
    _ingest(conn, tmp_path, "<DescriptorRecordSet>" + _record(
        "D005665", "UFURO00000", "Frusemide", "Glucose") + "</DescriptorRecordSet>")
    assert _stored(conn) == [(FURO, "Frusemide", "D005665")]


def test_the_view_hides_a_synonym_only_against_its_own_moiety(conn, tmp_path):
    """A furosemide synonym equal to PARACETAMOL's display or official name is
    kept: hiding is per moiety, and a shared name is listed for both (#231)."""
    _seed(conn)
    _ingest(conn, tmp_path, "<DescriptorRecordSet>" + _record(
        "D005665", "UFURO00000", "Paracetamol", "acetaminophen")
        + "</DescriptorRecordSet>")
    rows = conn.execute("SELECT moiety_uuid, name FROM drugref.moiety_name "
                        "WHERE name_kind = 'mesh_synonym' ORDER BY name").fetchall()
    assert rows == [(FURO, "acetaminophen"), (FURO, "Paracetamol")]


def test_an_empty_registry_is_refused_before_any_run_opens(conn, tmp_path):
    with pytest.raises(ValueError, match="registry"):
        _ingest(conn, tmp_path)
    assert _runs(conn) == 0


# ---- the CLI --------------------------------------------------------------------

def test_the_cli_ingests_and_prints_the_summary(conn, tmp_path, capsys):
    _seed(conn)
    desc_path, supp_path = _files(tmp_path)
    argv = ["--dsn", "x", "ingest", "mesh-synonyms", "--release", "2026",
            "--desc", str(desc_path), "--supp", str(supp_path)]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "stored=4" in out and "2 linked records" in out


def test_the_cli_reports_a_missing_file_in_one_line(conn, capsys):
    _seed(conn)
    argv = ["--dsn", "x", "ingest", "mesh-synonyms", "--release", "2026",
            "--desc", "/nonexistent/desc.xml", "--supp", "/nonexistent/supp.xml"]
    assert cli.main(argv) == 1
    err = capsys.readouterr().err
    assert "mesh-synonyms ingest failed" in err and err.count("\n") == 1


def test_the_cli_reports_a_truncated_download_in_one_line(conn, tmp_path, capsys):
    """A partial desc20XX.xml.gz raises EOFError, broken XML a ParseError: both
    are operator problems, not tracebacks."""
    _seed(conn)
    _, supp_path = _files(tmp_path)
    truncated = tmp_path / "desc.xml.gz"
    truncated.write_bytes(gzip.compress(DESC.encode())[:40])
    broken = tmp_path / "broken.xml"
    broken.write_text(DESC[:60])
    for desc_path in (truncated, broken):
        argv = ["--dsn", "x", "ingest", "mesh-synonyms", "--release", "2026",
                "--desc", str(desc_path), "--supp", str(supp_path)]
        assert cli.main(argv) == 1
        err = capsys.readouterr().err
        assert "mesh-synonyms ingest failed" in err and err.count("\n") == 1


# ---- `drugref status` ------------------------------------------------------------

def _status_rows(synonyms_at, names_at=None):
    rows = [(mesh_synonym_run.SOURCE, mesh_synonym_run.WRITER, "2026",
             synonyms_at, synonyms_at, True),
            ("UNII", "unii_run", "r", 2, 2, True)]
    if names_at is not None:
        rows.append(("UNII", "moiety_name_run", "r", names_at, names_at, True))
    return rows


def test_status_warns_when_unii_ran_after_the_synonyms():
    assert "`drugref ingest mesh-synonyms`" in cli_status._stale_names_note(
        _status_rows(synonyms_at=1))


def test_status_names_both_ingests_when_both_are_stale():
    assert ("`drugref ingest moiety-names` and `drugref ingest mesh-synonyms`"
            in cli_status._stale_names_note(_status_rows(synonyms_at=1, names_at=1)))


def test_status_is_silent_when_the_synonyms_are_newer():
    assert cli_status._stale_names_note(_status_rows(synonyms_at=3)) == ""

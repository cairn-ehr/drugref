"""db/071 and `mesh_synonym_run` (#231): MeSH entry terms as search-only names.

Cairn's allergy pick searches `drugref.moiety_name`. These tests pin the new
table, the view's third branch (`mesh_synonym`, hiding a synonym the same moiety
already answers to), and the orchestrator's refusals. The SPL drug pass never
reads this table: label-match keys stay the strict official-name set.

DB-gated; the ingest commits, so each test starts from a TRUNCATE.
"""
import contextlib
import re
import uuid

import pytest

from drugref import cli, cli_status, db, provenance
from drugref.ingest import mesh_synonym_run

FURO, PARA = uuid.UUID(int=0xC001), uuid.UUID(int=0xC002)
TABLES = ("drugref.moiety_mesh_synonym, drugref.moiety_official_name, "
          "drugref.identity_claim, drugref.substance_moiety, drugref.ingest_run")


def _record(ui, unii, *terms):
    body = "".join(f'<Term IsPermutedTermYN="N"><String>{t}</String></Term>'
                   for t in terms)
    return (f'<DescriptorRecord><DescriptorUI>{ui}</DescriptorUI><ConceptList>'
            f'<Concept PreferredConceptYN="Y"><RegistryNumberList><RegistryNumber>'
            f'{unii}</RegistryNumber></RegistryNumberList><TermList>{body}</TermList>'
            f'</Concept></ConceptList></DescriptorRecord>')


# 'furosemide' and 'ACETAMINOPHEN' repeat a name the moiety already has (display,
# official); the view must not list them twice.
DESC = ("<DescriptorRecordSet>"
        + _record("D005665", "UFURO00000", "Furosemide", "Frusemide")
        + _record("D000082", "UPARA00000", "Acetaminophen", "APAP")
        + "</DescriptorRecordSet>")
SUPP = "<SupplementalRecordSet></SupplementalRecordSet>"


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


def _files(tmp_path, desc=DESC):
    (tmp_path / "desc.xml").write_text(desc)
    (tmp_path / "supp.xml").write_text(SUPP)
    return tmp_path / "desc.xml", tmp_path / "supp.xml"


def _ingest(conn, tmp_path, desc=DESC, release="2026"):
    desc_path, supp_path = _files(tmp_path, desc)
    return mesh_synonym_run.ingest_mesh_synonyms(
        conn, desc_path=desc_path, supp_path=supp_path, upstream_release=release)


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


def test_an_empty_registry_is_refused_before_any_run_opens(conn, tmp_path):
    with pytest.raises(ValueError, match="registry"):
        _ingest(conn, tmp_path)
    assert conn.execute("SELECT count(*) FROM drugref.ingest_run "
                        "WHERE writer = 'mesh_synonym_run'").fetchone()[0] == 0


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


# ---- `drugref status` ------------------------------------------------------------

def test_status_warns_when_unii_ran_after_the_synonyms():
    rows = [("MeSH", "mesh_synonym_run", "2026", 1, 1, True),
            ("UNII", "unii_run", "r", 2, 2, True)]
    assert "mesh-synonyms" in cli_status._stale_names_note(rows)

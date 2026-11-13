"""db/070 and `moiety_name_run` (#231): the persisted, searchable moiety names.

Cairn's allergy pick searches `drugref.moiety_name` (display names plus the UNII
official names). These tests pin the table, the view, and that the orchestrator
stores exactly the names #227's rule adopts -- no more (shadowed, excluded,
ambiguous and contested names stay out) and no fewer.

DB-gated; the ingest commits, so each test starts from a TRUNCATE.
"""
import contextlib
import re
import uuid

import psycopg
import pytest

from drugref import cli, db, provenance
from drugref.ingest import moiety_name_run

PARA, EPI, RACE, DEX, DUO = (uuid.UUID(int=0xA000 + i) for i in range(5))
# moiety -> (display name, its UNIIs). DUO1 and DUO2 both claim UNII 'UDUO' (#230).
REGISTRY = {
    PARA: ("paracetamol", ("UPARA",)),
    EPI: ("epinephrine", ("UEPI",)),
    RACE: ("racepinephrine", ("URACE",)),
    DEX: ("dextrose", ("UDEX",)),
    DUO: ("duo one", ("UDUO",)),
}
DUO2 = uuid.UUID(int=0xA0FF)
NAMES_HEADER = "Name\tTYPE\tUNII\tDisplay Name\n"
NAMES = (NAMES_HEADER
         + "ACETAMINOPHEN\tof\tUPARA\tACETAMINOPHEN\n"     # adopted
         + "PARACETAMOL\tof\tUPARA\tACETAMINOPHEN\n"       # already the display name
         + "ACETAMINOPHENUM\tcn\tUPARA\tACETAMINOPHEN\n"   # not TYPE 'of'
         + "EPINEPHRINE\tof\tURACE\tRACEPINEPHRINE\n"      # shadowed by EPI's name
         + "GLUCOSE\tof\tUDEX\tDEXTROSE\n"                 # in the shipped exclusions
         + "DUOFEN\tof\tUDUO\tDUOFEN\n")                   # contested UNII (#230)


@pytest.fixture(autouse=True)
def _clean(conn, monkeypatch):
    conn.execute("TRUNCATE drugref.moiety_official_name, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    monkeypatch.setattr(cli.db, "connect", lambda dsn: contextlib.nullcontext(conn))
    yield
    conn.rollback()
    conn.execute("TRUNCATE drugref.moiety_official_name, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()


def _seed(conn) -> None:
    run = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer) VALUES ('UNII', 'test', 'test', 'unii_run') "
        "RETURNING ingest_run_id").fetchone()[0]
    for moiety, (name, uniis) in {**REGISTRY, DUO2: ("duo two", ("UDUO",))}.items():
        conn.execute("INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
                     "first_seen_ingest) VALUES (%s, %s, %s)", (moiety, name, run))
        for unii in uniis:
            conn.execute("INSERT INTO drugref.identity_claim (moiety_uuid, scheme, "
                         "value, ingest_run) VALUES (%s, 'UNII', %s, %s)",
                         (moiety, unii, run))
    conn.commit()


def _names_file(tmp_path, text=NAMES):
    path = tmp_path / "UNII_Names.txt"
    path.write_text(text)
    return path


def _stored(conn):
    return conn.execute("SELECT moiety_uuid, name FROM drugref.moiety_official_name "
                        "ORDER BY name").fetchall()


# ---- the schema -----------------------------------------------------------------

def test_the_table_and_the_view_exist(conn):
    assert db.missing_relations(conn, "drugref.moiety_official_name",
                                "drugref.moiety_name") == ()


def test_the_writer_vocabulary_admits_moiety_name_run(conn):
    live = set(re.findall(r"'([^']+)'", db.constraint_definition(
        conn, "ingest_run", "ingest_run_writer")))
    assert "moiety_name_run" in live and "moiety_name_run" in provenance.WRITERS
    assert set(provenance.WRITERS) == live


# ---- the ingest -----------------------------------------------------------------

def test_only_the_adopted_official_names_are_stored(conn, tmp_path):
    _seed(conn)
    summary = moiety_name_run.ingest_moiety_names(
        conn, unii_names_path=_names_file(tmp_path), upstream_release="26Feb2026")
    assert _stored(conn) == [(PARA, "ACETAMINOPHEN")]
    assert summary.stored == 1
    assert "1 official names adopted" in summary.adoption
    assert "['EPINEPHRINE']" in summary.adoption        # shadowed, said by name
    assert "['DUOFEN']" in summary.adoption             # contested UNII (#230)
    assert "['GLUCOSE']" in summary.adoption            # excluded


def test_the_view_finds_a_moiety_by_either_name(conn, tmp_path):
    """The issue's case: a clinician typing 'acetaminophen' reaches paracetamol,
    and the display name is still found (as kind 'display')."""
    _seed(conn)
    moiety_name_run.ingest_moiety_names(
        conn, unii_names_path=_names_file(tmp_path), upstream_release="26Feb2026")
    rows = conn.execute(
        "SELECT name, name_kind FROM drugref.moiety_name WHERE moiety_uuid = %s "
        "ORDER BY name", (PARA,)).fetchall()
    assert rows == [("ACETAMINOPHEN", "unii_official"), ("paracetamol", "display")]


def test_a_rerun_rebuilds_rather_than_accumulates(conn, tmp_path):
    _seed(conn)
    for release in ("26Feb2026", "26Mar2026"):
        moiety_name_run.ingest_moiety_names(
            conn, unii_names_path=_names_file(tmp_path), upstream_release=release)
    assert _stored(conn) == [(PARA, "ACETAMINOPHEN")]
    run = conn.execute("SELECT r.upstream_release, r.writer, r.source "
                       "FROM drugref.moiety_official_name n "
                       "JOIN drugref.ingest_run r ON r.ingest_run_id = n.ingest_run"
                       ).fetchone()
    assert run == ("26Mar2026", "moiety_name_run", "UNII")


def test_an_empty_registry_is_refused_and_keeps_the_previous_build(conn, tmp_path):
    """Rebuilding over an empty registry would delete every name and report success."""
    _seed(conn)
    moiety_name_run.ingest_moiety_names(
        conn, unii_names_path=_names_file(tmp_path), upstream_release="26Feb2026")
    # Empty the claims (append-only, so TRUNCATE as the fixture does): no UNII left.
    conn.execute("TRUNCATE drugref.identity_claim")
    conn.commit()
    with pytest.raises(ValueError, match="registry"):
        moiety_name_run.ingest_moiety_names(
            conn, unii_names_path=_names_file(tmp_path), upstream_release="x")
    assert _stored(conn) == [(PARA, "ACETAMINOPHEN")]


def test_a_records_file_is_refused_before_any_run_opens(conn, tmp_path):
    _seed(conn)
    records = _names_file(tmp_path, "UNII\tDisplay Name\nUPARA\tACETAMINOPHEN\n")
    with pytest.raises(ValueError, match="UNII_Records"):
        moiety_name_run.ingest_moiety_names(
            conn, unii_names_path=records, upstream_release="x")
    assert conn.execute("SELECT count(*) FROM drugref.ingest_run "
                        "WHERE writer = 'moiety_name_run'").fetchone()[0] == 0


def test_a_moiety_name_row_without_a_moiety_is_refused(conn):
    run = conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum, "
        "writer) VALUES ('UNII', 'x', 'x', 'moiety_name_run') "
        "RETURNING ingest_run_id").fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("INSERT INTO drugref.moiety_official_name (moiety_uuid, name, "
                     "ingest_run) VALUES (gen_random_uuid(), 'X', %s)", (run,))


# ---- the CLI --------------------------------------------------------------------

def test_the_cli_ingests_and_prints_the_summary(conn, tmp_path, capsys):
    _seed(conn)
    argv = ["--dsn", "x", "ingest", "moiety-names", "--release", "26Feb2026",
            "--unii-names", str(_names_file(tmp_path))]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "stored=1" in out and "official names adopted" in out


def test_the_cli_reports_a_missing_file_in_one_line(conn, capsys):
    _seed(conn)
    argv = ["--dsn", "x", "ingest", "moiety-names", "--release", "x",
            "--unii-names", "/nonexistent/UNII_Names.txt"]
    assert cli.main(argv) == 1
    assert "moiety-names ingest failed" in capsys.readouterr().err

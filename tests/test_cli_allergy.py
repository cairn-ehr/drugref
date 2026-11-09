"""`drugref ingest allergy-groups` and `drugref allergy check` through cli.main.

DB-gated; the ingest commits, so each test starts from a TRUNCATE (as
test_allergy_group_run.py does).
"""
import contextlib
import uuid

import pytest

from drugref import cli
from tests import allergy_world as w


@pytest.fixture(autouse=True)
def _clean(conn, monkeypatch):
    conn.execute("TRUNCATE drugref.allergy_group_member, drugref.allergy_group, "
                 "drugref.allergy_mesh_placement, drugref.substance_composition, "
                 "drugref.class_membership, drugref.class_parent, "
                 "drugref.substance_class, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run RESTART IDENTITY CASCADE")
    conn.commit()
    # Run the CLI on the test's own connection.
    monkeypatch.setattr(cli.db, "connect", lambda dsn: contextlib.nullcontext(conn))
    yield


def _ingest_argv(**over):
    a = {"--release": "2026", "--desc": str(w.DESC), "--supp": str(w.SUPP)}
    a.update(over)
    return ["--dsn", "x", "ingest", "allergy-groups",
            *[x for kv in a.items() for x in kv]]


def test_ingest_prints_the_summary_fields(conn, capsys):
    w.seed_moieties(conn)
    conn.commit()
    assert cli.main(_ingest_argv()) == 0
    out = capsys.readouterr().out
    assert "members=" in out and "placed_moieties=" in out


def test_ingest_with_a_missing_file_exits_1(conn, capsys):
    assert cli.main(_ingest_argv(**{"--desc": "/nonexistent.xml"})) == 1
    assert "drugref:" in capsys.readouterr().err


def test_check_match_prints_group_and_component_names(conn, capsys):
    m = w.seed_moieties(conn)
    conn.commit()
    w.run_allergy_ingest(conn)
    rc = cli.main(["--dsn", "x", "allergy", "check", str(m["flucloxacillin"]),
                   str(m["amoxicillin"])])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "match"
    assert ("same-group  Penicillins (allergy group) [structural-seed] D010406  "
            "allergen:amoxicillin -> prescribed:flucloxacillin") in out


def test_check_no_match_exits_0(conn, capsys):
    m = w.seed_moieties(conn)
    conn.commit()
    w.run_allergy_ingest(conn)
    rc = cli.main(["--dsn", "x", "allergy", "check", str(m["perindopril"]),
                   str(m["amoxicillin"])])
    assert rc == 0
    assert capsys.readouterr().out.startswith("no-match")


def test_check_unplaced_exits_3(conn, capsys):
    w.seed_moieties(conn)
    conn.commit()
    w.run_allergy_ingest(conn)
    rc = cli.main(["--dsn", "x", "allergy", "check", str(uuid.uuid4()),
                   str(uuid.uuid4())])
    assert rc == 3
    assert capsys.readouterr().out.startswith("unplaced")


def test_check_on_a_database_without_the_function_is_a_guard_sentence(conn, capsys):
    # A database that predates db/068 has no drugref.allergy_check: the operator must
    # get the guard's sentence and a non-zero exit, not a psycopg traceback. The DROP
    # is rolled back by the guard's own diagnosis (missing_relations rolls back).
    conn.execute("DROP FUNCTION drugref.allergy_check(uuid, uuid)")
    rc = cli.main(["--dsn", "x", "allergy", "check", str(uuid.uuid4()),
                   str(uuid.uuid4())])
    assert rc != 0
    assert "db/068" in capsys.readouterr().err

"""A curator acknowledges a departed grade (#281 spec 5.4). DB-gated.

`graded_pairs.acknowledge` closes every open, currently-departed episode of a
ruling KEY (any version's id names it) that involves one drug, so `departed`
stops listing it; the CLI wraps it as `drugref curate acknowledge-departure`.
The CLI tests reuse test_cli_signing's `_NoCommit` wrapper so the handler's real
`conn.commit()` never escapes the rolled-back `conn` fixture (conftest.py).
"""
import uuid

import psycopg
import pytest

from drugref import cli, graded_pairs
from tests.graded_pair_helpers import commit_point
from tests.test_cli_signing import _NoCommit, _FakeConnectionContext
from tests.test_class_subject_read_path import _a_graded_class_rule, _file_member
from tests.test_graded_pairs_departed import (_a_rule_x_left, _live_ruling, _moiety_rule,
                                                _rerule)

_WHY = dict(rationale="X left A on purpose", reviewed_by="dr t", reviewed_against="2026")


def _ack_count(conn):
    return conn.execute(f"SELECT count(*) FROM {graded_pairs.ACK_TABLE}").fetchone()[0]


def test_acknowledging_silences_the_banner_for_that_drug_only(conn, ingest_run_id):
    ruling, x, w, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 1)
    # w, a second drug, also leaves the same ruling
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (w,))
    assert graded_pairs.departed(conn, w) != []

    assert graded_pairs.acknowledge(conn, "class", ruling, x, **_WHY) == {(x, p)}
    assert graded_pairs.departed(conn, x) == []
    (d,) = graded_pairs.departed(conn)
    assert d.pairs == {(w, p)}


def test_an_acknowledged_drug_that_returns_and_leaves_again_departs_again(
        conn, ingest_run_id):
    ruling, x, _z, p, a, _b = _a_rule_x_left(conn, ingest_run_id, 2)
    graded_pairs.acknowledge(conn, "class", ruling, x, **_WHY)
    _file_member(conn, x, a, ingest_run_id)
    graded_pairs.refresh(conn)     # (an ingest finishing) X reaches P again: a NEW episode opens
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    (d,) = graded_pairs.departed(conn, x)
    assert d.pairs == {(x, p)}


def test_two_open_episodes_for_one_pair_are_both_acknowledged(conn, ingest_run_id):
    ruling, x, _z, p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 3)
    t = graded_pairs.OBSERVATION_TABLE
    conn.execute("ALTER TABLE %s DISABLE TRIGGER USER" % t)
    conn.execute(
        f"INSERT INTO {t} (rule_grain, subject_key, object_class_uuid, relationship, "
        f"subject_moiety, partner_moiety, captured) SELECT rule_grain, subject_key, "
        f"object_class_uuid, relationship, subject_moiety, partner_moiety, captured "
        f"FROM {t} WHERE subject_moiety = %s", (x,))
    conn.execute("ALTER TABLE %s ENABLE TRIGGER USER" % t)
    (d,) = graded_pairs.departed(conn, x)
    assert d.pairs == {(x, p)}

    graded_pairs.acknowledge(conn, "class", ruling, x, **_WHY)
    assert _ack_count(conn) == 2
    assert graded_pairs.departed(conn, x) == []


def test_a_class_against_itself_acknowledges_both_directions(conn, ingest_run_id):
    sc, _oc, (x, z), _ = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000028150", object_code="N0000028150",
        subject_members=[("TESTUN28150A", "leaver"), ("TESTUN28150B", "stayer")],
        object_members=[], severity="major")
    commit_point(conn)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    pairs = (graded_pairs.departed(conn, x)[0].pairs
             | graded_pairs.departed(conn, None, x)[0].pairs)
    assert pairs == {(x, z), (z, x)}

    got = graded_pairs.acknowledge(conn, "class", _live_ruling(conn, sc), x, **_WHY)
    assert got == pairs
    assert graded_pairs.departed(conn, x) == [] == graded_pairs.departed(conn, None, x)


def test_a_superseded_ruling_id_acknowledges_its_key(conn, ingest_run_id):
    old, x, _z, p, sc, oc = _a_rule_x_left(conn, ingest_run_id, 4)
    _rerule(conn, sc, oc)
    commit_point(conn)
    assert _live_ruling(conn, sc) != old
    assert graded_pairs.acknowledge(conn, "class", old, x, **_WHY) == {(x, p)}
    assert graded_pairs.departed(conn, x) == []


def test_a_moiety_grain_ack_works_end_to_end(conn, ingest_run_id):
    ruling, s, p, _b = _moiety_rule(conn, ingest_run_id, 3)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (p,))
    assert graded_pairs.departed(conn, s) != []
    assert graded_pairs.acknowledge(conn, "moiety", ruling, p, **_WHY) == {(s, p)}
    assert graded_pairs.departed(conn, s) == []


def test_a_moiety_ruling_id_under_the_class_grain_acknowledges_nothing(
        conn, ingest_run_id):
    ruling, s, p, _b = _moiety_rule(conn, ingest_run_id, 4)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (p,))
    with pytest.raises((graded_pairs.UnknownRuling, graded_pairs.NothingDeparted)):
        graded_pairs.acknowledge(conn, "class", ruling, p, **_WHY)
    assert _ack_count(conn) == 0
    assert graded_pairs.departed(conn, s) != []


def test_nothing_departed_refuses_and_writes_nothing(conn, ingest_run_id):
    ruling, _x, z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 5)
    with pytest.raises(graded_pairs.NothingDeparted):
        graded_pairs.acknowledge(conn, "class", ruling, z, **_WHY)
    assert _ack_count(conn) == 0


def test_an_unknown_ruling_id_is_refused(conn):
    with pytest.raises(graded_pairs.UnknownRuling):
        graded_pairs.acknowledge(
            conn, "moiety", 2_000_000_000, uuid.uuid4(), **_WHY)


def test_a_blank_rationale_is_refused(conn, ingest_run_id):
    ruling, x, *_ = _a_rule_x_left(conn, ingest_run_id, 6)
    with pytest.raises(psycopg.errors.CheckViolation):
        graded_pairs.acknowledge(conn, "class", ruling, x, **{**_WHY, "rationale": " "})


# --- the CLI -----------------------------------------------------------------

@pytest.fixture
def cli_conn(conn, monkeypatch):
    wrapped = _NoCommit(conn)
    monkeypatch.setattr(
        cli.db, "connect", lambda dsn=None: _FakeConnectionContext(wrapped))
    return wrapped


def _argv(ruling, drug, grain="class"):
    return ["curate", "acknowledge-departure", "--grain", grain, str(ruling), str(drug),
            "--rationale", "accepted", "--reviewed-by", "dr t",
            "--reviewed-against", "2026"]


def test_cli_acknowledge_prints_pairs_and_exits_0(cli_conn, ingest_run_id, capsys):
    ruling, x, _z, p, _a, _b = _a_rule_x_left(cli_conn, ingest_run_id, 7)
    assert cli.main(_argv(ruling, x)) == 0
    assert f"{x} {p}" in capsys.readouterr().out
    assert cli_conn.commit_count == 1
    assert graded_pairs.departed(cli_conn, x) == []


def test_cli_acknowledge_exits_1_when_nothing_departed(cli_conn, ingest_run_id, capsys):
    ruling, _x, z, *_ = _a_rule_x_left(cli_conn, ingest_run_id, 8)
    assert cli.main(_argv(ruling, z)) == 1
    assert capsys.readouterr().err
    assert _ack_count(cli_conn) == 0


def test_cli_acknowledge_exits_2_for_an_unknown_ruling(cli_conn, capsys):
    assert cli.main(_argv(2_000_000_000, uuid.uuid4())) == 2
    assert capsys.readouterr().err

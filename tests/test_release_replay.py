"""`drugref audit replay`: rebuild a release into an empty database (#259).

The pure helpers are tested without a database. The end-to-end tests build an
ORIGIN database (ingest, rule, re-ingest, publish with the archive set) and replay
it into an empty TARGET, each a fresh database beside the session one, because
orchestrators commit and the replay needs a database with no ledger at all.

The fake writer below stands in for a real orchestrator so the test controls
membership exactly: run 1 files X in class A, the curator rules A x B (reaching
X), run 2 files X out of A. Only a replay that loads the ruling BETWEEN the two
runs reproduces "the ruling reached X when made"; loading curated rows at the
end would report a reach difference.
"""
import datetime as dt
import json
import os
import uuid

import psycopg
import pytest
from psycopg import sql

from drugref import (archive, cli, curation, db, ids, interactions, provenance,
                     release_archive, release_replay, signing)
from drugref.ingest.checksum import archived_checksum
from tests.conftest import _drop_database, session_dsn

TAG = "2026.11.18-replay"
SHA = "a" * 40


def _t(minutes):
    return (dt.datetime(2026, 11, 18, 10, tzinfo=dt.timezone.utc)
            + dt.timedelta(minutes=minutes)).isoformat()


# ---- pure helpers ----------------------------------------------------------


def test_schedule_puts_each_row_before_the_first_run_that_started_after_it():
    runs = [{"started_at": _t(0), "finished_at": _t(1)},
            {"started_at": _t(10), "finished_at": _t(11)}]
    rows = [("curated_interaction", {"curated_interaction_id": 2, "reviewed_at": _t(5)}),
            ("curated_interaction", {"curated_interaction_id": 1, "reviewed_at": _t(-1)}),
            ("class_expansion_policy", {"policy_id": 1, "reviewed_at": _t(20)})]
    batches = release_replay.schedule(rows, runs)
    assert [[r[1].get("curated_interaction_id", "p") for r in b] for b in batches] == [
        [1], [2], ["p"]]


def test_schedule_falls_back_to_finished_at_for_an_older_record():
    runs = [{"finished_at": _t(10)}]
    rows = [("curated_condition", {"curated_condition_id": 1, "reviewed_at": _t(5)})]
    assert release_replay.schedule(rows, runs) == [rows, []]


def test_schedule_orders_equal_times_by_table_then_id():
    runs = []
    rows = [("class_expansion_policy", {"policy_id": 1, "reviewed_at": _t(0)}),
            ("curated_interaction", {"curated_interaction_id": 9, "reviewed_at": _t(0)}),
            ("curated_interaction", {"curated_interaction_id": 3, "reviewed_at": _t(0)})]
    (only,) = release_replay.schedule(rows, runs)
    assert [r[1].get("curated_interaction_id", "p") for r in only] == [3, 9, "p"]


@pytest.mark.parametrize("current, recorded, ok", [
    (SHA, SHA, True),
    ("b" * 40, SHA, False),
    (SHA + "-dirty", SHA, False),
    (SHA + "-dirty", SHA + "-dirty", False),   # dirty code is no reproducible checkout
    ("unknown", "unknown", False),
])
def test_code_problem(current, recorded, ok):
    assert (release_replay.code_problem(current, recorded) is None) == ok


def test_code_problem_names_the_commit_to_check_out():
    assert SHA in release_replay.code_problem("b" * 40, SHA)


def test_split_spl_inputs_by_name():
    from pathlib import Path
    files = [Path(n) for n in ("UNII_Names_1.txt", "dm_spl_release_human_rx_part1.zip",
                               "drug-label-0001-of-0002.json.zip", "suppress.toml",
                               "exclusions.toml")]
    partitions, parts, names = release_replay.split_spl_inputs(files)
    assert [p.name for p in partitions] == ["drug-label-0001-of-0002.json.zip"]
    assert [p.name for p in parts] == ["dm_spl_release_human_rx_part1.zip"]
    assert names.name == "UNII_Names_1.txt"


def test_manifest_differences():
    a = {"target_kind": "curated_interaction", "natural_key": "k1",
         "payload_context": "c", "payload_digest": "00"}
    b = dict(a, natural_key="k2")
    assert release_replay.manifest_differences([a, b], [a, b]) == []
    assert release_replay.manifest_differences(
        [a, b], [dict(a, payload_digest="ff"), dict(b, natural_key="k3")]) == [
        "manifest: curated_interaction k1 content differs",
        "manifest: curated_interaction k2 missing from the replay",
        "manifest: curated_interaction k3 not in the release"]


def test_every_orchestrator_writer_has_an_adapter():
    """`curation` and `unattributed` name no orchestrator: a run with either is refused."""
    assert set(release_replay.ADAPTERS) == (
        set(provenance.WRITERS) - {"curation", "unattributed"})


# ---- end to end ------------------------------------------------------------


@pytest.fixture
def databases(_dsn):
    """(origin DSN, target DSN): origin migrated, target empty; dropped after."""
    made = []
    for role in ("origin", "target"):
        name, dsn = session_dsn(_dsn, os.getpid(), f"{role}{uuid.uuid4().hex[:6]}")
        with psycopg.connect(_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        made.append((name, dsn))
    try:
        with psycopg.connect(made[0][1]) as c:
            db.apply_migrations(c)
        yield made[0][1], made[1][1]
    finally:
        for name, _ in made:
            _drop_database(_dsn, name)


A, B = "N0000025901", "N0000025902"
X, Y = "TESTUNI259X", "TESTUNI259Y"


def _fake_ingest(conn, files, release):
    """A stand-in orchestrator: each line `class_code moiety_code` files a member.

    Delete-and-rebuild like the real ones, so a moiety missing from the next
    file leaves its class. Archives its input exactly as a real one does.
    """
    path = files[0]
    run = provenance.open_run(conn, source="ONCHIGH", upstream_release=release,
                              source_checksum=archived_checksum(path),
                              writer="onchigh_run", clock=provenance.start_clock())
    conn.execute("DELETE FROM drugref.class_membership")
    for line in path.read_text().split("\n"):
        if not line:
            continue
        code, moiety = line.split()
        klass = ids.mint_class_uuid("MED-RT", code)
        conn.execute(
            "INSERT INTO drugref.substance_class (class_uuid, source, source_code, "
            "class_name, concept_type, first_seen_ingest) VALUES "
            "(%s, 'MED-RT', %s, %s, 'MoA', %s) ON CONFLICT DO NOTHING",
            (klass, code, f"{code} [MoA]", run))
        m = ids.mint_moiety_uuid(moiety)
        conn.execute(
            "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, "
            "first_seen_ingest) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (m, moiety.lower(), run))
        conn.execute(
            "INSERT INTO drugref.class_membership (moiety_uuid, class_uuid, "
            "relationship, ingest_run) VALUES (%s, %s, 'has_MoA', %s)", (m, klass, run))
    if not conn.execute("SELECT 1 FROM drugref.class_pair_contraindication").fetchone():
        interactions.add_class_pair_contraindication(
            conn, ids.mint_class_uuid("MED-RT", A), ids.mint_class_uuid("MED-RT", B),
            "CI_MoA", "ONCHIGH", run)
    provenance.finish_run(conn, run)
    conn.commit()


@pytest.fixture
def published(databases, tmp_path, monkeypatch):
    """Origin: run 1 (X, Y in A; Y in B), a ruling and its correction, run 2 (X
    leaves A), published. The correction makes the export carry a superseded row."""
    origin, target = databases
    root = tmp_path / "archive"
    monkeypatch.setenv(archive.ENV_VAR, str(root))
    monkeypatch.setattr(release_archive, "git_commit", lambda: SHA)
    monkeypatch.setitem(release_replay.ADAPTERS, "onchigh_run", _fake_ingest)
    one, two = tmp_path / "in1", tmp_path / "in2"
    one.mkdir(), two.mkdir()
    (one / "members.txt").write_text(f"{A} {X}\n{A} {Y}\n{B} {Y}\n")
    (two / "members.txt").write_text(f"{A} {Y}\n{B} {Y}\n")
    kp = signing.generate_keypair()
    with psycopg.connect(origin) as c:
        _fake_ingest(c, [one / "members.txt"], "r1")
        for severity in ("major", "contraindicated"):
            rid = curation.record_class_interaction_judgement(
                c, ids.mint_class_uuid("MED-RT", A), ids.mint_class_uuid("MED-RT", B),
                "CI_MoA", True, severity=severity, evidence_grade="established",
                reviewed_by="test", reviewed_against="2026")
            c.commit()
        _fake_ingest(c, [two / "members.txt"], "r2")
        release_archive.publish_archived(
            c, root=root, release_tag=TAG, published_by="op",
            private_key=kp.private_key, key_fingerprint=signing.fingerprint(kp.public_key))
        c.commit()
    return {"origin": origin, "target": target, "root": root, "ruling": rid}


def _replay(p, **kw):
    with psycopg.connect(p["target"]) as c:
        return release_replay.replay(c, p["root"], TAG, **kw)


def test_publish_records_started_at_and_the_manifest_entries(published):
    record = json.loads(archive.release_record_path(published["root"], TAG).read_text())
    assert all(r["started_at"] for r in record["runs"])
    (entry,) = record["manifest"]
    assert entry["target_kind"] == "curated_class_interaction"
    assert set(entry) == {"target_kind", "natural_key", "payload_context",
                          "payload_digest"}


def test_a_replay_reproduces_the_release(published):
    report = _replay(published)
    assert report.differences == [] and report.warnings == []
    assert (report.runs, report.curated_rows, report.rulings_checked) == (2, 2, 2)
    with psycopg.connect(published["target"]) as c:
        reach = set(c.execute(
            "SELECT side, moiety_uuid, captured FROM "
            "drugref.curated_class_interaction_reach "
            "WHERE curated_class_interaction_id = %s", (published["ruling"],)))
        assert ("subject", ids.mint_moiety_uuid(X), "at_ruling") in reach
        # The correction superseded the first ruling, exactly as in the origin.
        assert c.execute(
            "SELECT curated_class_interaction_id, superseded_by FROM "
            "drugref.curated_class_interaction ORDER BY 1").fetchall() == [
            (published["ruling"] - 1, published["ruling"]), (published["ruling"], None)]
        # Run 2 was replayed AFTER the ruling: X is out of A now.
        assert not c.execute(
            "SELECT 1 FROM drugref.class_membership WHERE moiety_uuid = %s",
            (ids.mint_moiety_uuid(X),)).fetchone()
        # A curator's next ruling gets a fresh id, not a collision with a loaded one.
        nxt = c.execute("SELECT nextval(pg_get_serial_sequence("
                        "'drugref.curated_class_interaction', "
                        "'curated_class_interaction_id'))").fetchone()[0]
        assert nxt > published["ruling"]


def _edit(path, change):
    body = json.loads(path.read_text())
    change(body)
    path.write_text(json.dumps(body))


def test_a_reach_the_replay_does_not_rebuild_is_a_difference(published):
    export = archive.curated_export_path(published["root"], TAG)
    _edit(export, lambda b: b["curated_class_interaction_reach"].pop(0))
    report = _replay(published)
    assert any("reach differs" in d for d in report.differences)


def test_altered_curated_content_is_a_manifest_difference(published):
    export = archive.curated_export_path(published["root"], TAG)
    # The LIVE row: a superseded one is not in the manifest.
    _edit(export, lambda b: b["curated_class_interaction"][-1].update(severity="minor"))
    report = _replay(published)
    assert any(d.startswith("manifest:") and "content differs" in d
               for d in report.differences)


def test_a_non_empty_target_is_refused(published):
    with psycopg.connect(published["origin"]) as c:
        with pytest.raises(release_replay.ReplayRefused, match="not empty"):
            release_replay.replay(c, published["root"], TAG)


def test_other_code_is_refused_unless_allowed(published, monkeypatch):
    monkeypatch.setattr(release_archive, "git_commit", lambda: "b" * 40)
    with pytest.raises(release_replay.ReplayRefused, match="git worktree add"):
        _replay(published)
    report = _replay(published, allow_code_mismatch=True)
    assert report.differences == [] and len(report.warnings) == 1


def test_a_writer_without_an_adapter_is_refused(published):
    record = archive.release_record_path(published["root"], TAG)
    _edit(record, lambda b: b["runs"][0].update(writer="curation"))
    with pytest.raises(release_replay.ReplayRefused, match="curation"):
        _replay(published)


def test_a_damaged_archive_is_refused_before_anything_is_written(published):
    record = archive.release_record_path(published["root"], TAG)
    cs = json.loads(record.read_text())["runs"][0]["source_checksum"]
    archive.run_record_path(published["root"], cs).unlink()
    with pytest.raises(release_replay.ReplayRefused, match="no run record"):
        _replay(published)
    with psycopg.connect(published["target"]) as c:
        assert c.execute("SELECT to_regclass('drugref.schema_migration')"
                         ).fetchone()[0] is None


def test_the_cli_exit_codes(published, capsys):
    argv = ["--dsn", published["target"], "audit", "replay", "--release", TAG]
    assert cli.main(argv) == 0
    assert "reproduced" in capsys.readouterr().out
    assert cli.main(argv) == 2        # now non-empty: refused
    assert "not empty" in capsys.readouterr().err

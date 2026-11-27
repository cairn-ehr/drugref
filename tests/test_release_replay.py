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

from drugref import (archive, cli, curation, db, ids, interactions, overlay,
                     provenance, questions, release_archive, release_replay,
                     signing)
from drugref.ingest.checksum import archived_checksum
from tests.conftest import _drop_database, session_dsn

TAG = "2026.11.18-replay"
SHA = "a" * 40


def _t(minutes):
    return (dt.datetime(2026, 11, 18, 10, tzinfo=dt.timezone.utc)
            + dt.timedelta(minutes=minutes)).isoformat()


# ---- pure helpers ----------------------------------------------------------


def test_schedule_puts_each_row_before_the_first_run_that_finished_after_it():
    """A run's work commits at `finished_at`: a row written while run 2 was still
    in flight (minute 10) saw the database WITHOUT run 2, so it loads before it."""
    runs = [{"finished_at": _t(1)}, {"finished_at": _t(11)}]
    rows = [("curated_interaction", {"curated_interaction_id": 2, "reviewed_at": _t(10)}),
            ("curated_interaction", {"curated_interaction_id": 1, "reviewed_at": _t(-1)}),
            ("class_expansion_policy", {"policy_id": 1, "reviewed_at": _t(20)})]
    batches = release_replay.schedule(rows, runs)
    assert [[r[1].get("curated_interaction_id", "p") for r in b] for b in batches] == [
        [1], [2], ["p"]]


def test_schedule_a_row_written_as_a_run_finished_follows_that_run():
    """Same rule as `class_ruling_reach`: `finished_at <= reviewed_at` preceded it."""
    runs = [{"finished_at": _t(10)}]
    rows = [("curated_condition", {"curated_condition_id": 1, "reviewed_at": _t(10)})]
    assert release_replay.schedule(rows, runs) == [[], rows]


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


@pytest.mark.parametrize("unii_names, parts", [
    ("UNII_Names_1.txt", ["dm_spl_release_human_rx_part1.zip"]),
    (None, ["dm_spl_release_human_rx_part1.zip"]),
    ("UNII_Names_1.txt", []),
])
def test_split_spl_inputs_inverts_the_orchestrators_order(unii_names, parts):
    """Pinned to `spl_run.checksummed_inputs` itself, so the two cannot drift."""
    from pathlib import Path
    from drugref.ingest import spl_run
    partitions = [Path("drug-label-0002-of-0002.json.zip"),
                  Path("drug-label-0001-of-0002.json.zip")]
    files = [Path(f.name) for f in spl_run.checksummed_inputs(
        partitions, [Path(p) for p in parts], unii_names)]
    got_partitions, got_parts, got_names = release_replay.split_spl_inputs(files)
    assert sorted(got_partitions) == sorted(partitions)
    assert [p.name for p in got_parts] == parts
    assert (got_names.name if got_names else None) == unii_names


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


def _fake_ingest(conn, files, release, commit=True):
    """A stand-in orchestrator: each line `class_code moiety_code` files a member.

    Delete-and-rebuild like the real ones, so a moiety missing from the next
    file leaves its class. Archives its input and re-derives the questions
    exactly as a real one does. `commit=False` leaves the run finished but
    uncommitted (the race tests hold an ingest open at its COMMIT).
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
    questions.register_from_gaps(conn, run)     # as every real orchestrator does
    provenance.finish_run(conn, run)
    if commit:
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


def test_publish_records_the_manifest_entries(published):
    record = json.loads(archive.release_record_path(published["root"], TAG).read_text())
    (entry,) = record["manifest"]
    assert entry["target_kind"] == "curated_class_interaction"
    assert set(entry) == {"target_kind", "natural_key", "payload_context",
                          "payload_digest"}


def test_a_replay_reproduces_the_release(published):
    before = {p: p.read_bytes() for p in published["root"].rglob("*") if p.is_file()}
    report = _replay(published)
    assert report.differences == [] and report.warnings == [] and report.reproduced
    # Two rulings; the release's own signature stays in the archive (#270).
    assert (report.runs, report.curated_rows, report.rulings_checked) == (2, 2, 2)
    # Replayed runs read the archive; they never write into it.
    assert {p: p.read_bytes() for p in published["root"].rglob("*")
            if p.is_file()} == before
    assert os.environ[archive.ENV_VAR] == str(published["root"])
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
    # Row 0 is the FIRST ruling's partner reach: exactly that one is reported.
    assert report.differences == [
        f"class ruling {published['ruling'] - 1}: partner reach differs from ruling "
        "time: 1 added, 0 missing"]


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
    (warning,) = report.warnings
    assert report.differences == [] and "code mismatch allowed" in warning
    # Other code can build other memberships: never a verified reproduction.
    assert not report.reproduced


def test_other_migrations_are_refused_even_with_other_code_allowed(published):
    record = archive.release_record_path(published["root"], TAG)
    _edit(record, lambda b: b.update(ledger_head="000_other.sql"))
    with pytest.raises(release_replay.ReplayRefused, match="migrations end at"):
        _replay(published, allow_code_mismatch=True)
    _assert_nothing_written(published)


def test_two_inputs_sharing_a_name_are_refused(published, monkeypatch):
    twins = [archive.ArchivedFile("a.txt", 1, "0" * 64)] * 2
    monkeypatch.setattr(release_replay, "_run_files", lambda root, cs: twins)
    with pytest.raises(release_replay.ReplayRefused, match="share a name"):
        _replay(published)
    _assert_nothing_written(published)


def test_a_malformed_timestamp_is_refused_before_anything_is_written(published):
    record = archive.release_record_path(published["root"], TAG)
    _edit(record, lambda b: b["runs"][1].update(finished_at="yesterday"))
    with pytest.raises(release_replay.ReplayRefused, match="timestamp"):
        _replay(published)
    _assert_nothing_written(published)


def test_an_export_older_than_this_code_is_refused_as_older(published):
    """An export written before a table joined the export (here #270's) is not a
    damaged timestamp: the refusal names the missing table and the way out."""
    _edit(archive.curated_export_path(published["root"], TAG),
          lambda b: b.pop("reviewer_account"))
    with pytest.raises(release_replay.ReplayRefused, match="predates") as refused:
        _replay(published)
    assert "reviewer_account" in str(refused.value)
    assert "timestamp" not in str(refused.value)
    _assert_nothing_written(published)


def test_a_run_that_rebuilds_from_other_bytes_is_a_difference(published, monkeypatch,
                                                              tmp_path):
    def tampered(conn, files, release):
        copy = tmp_path / "tampered" / files[0].name
        copy.parent.mkdir(exist_ok=True)
        copy.write_text(files[0].read_text() + f"{B} {X}\n")
        _fake_ingest(conn, [copy], release)
    monkeypatch.setitem(release_replay.ADAPTERS, "onchigh_run", tampered)
    report = _replay(published)
    runs = [d for d in report.differences if d.startswith("run ")]
    assert len(runs) == 2 and all("replayed with checksum" in d for d in runs)


def test_a_record_without_a_manifest_is_not_a_verified_reproduction(published):
    record = archive.release_record_path(published["root"], TAG)
    _edit(record, lambda b: b.pop("manifest"))
    report = _replay(published)
    (warning,) = report.warnings
    assert report.differences == [] and "predates manifest" in warning
    assert not report.reproduced


def test_a_curator_policy_superseding_a_seeded_one_replays(databases, tmp_path,
                                                          monkeypatch):
    """db/010 seeds expansion policies: the target has them before any load, and a
    curator's override must point the SEEDED row at itself, as in the origin."""
    origin, target = databases
    root = tmp_path / "archive"
    monkeypatch.setenv(archive.ENV_VAR, str(root))
    monkeypatch.setattr(release_archive, "git_commit", lambda: SHA)
    monkeypatch.setitem(release_replay.ADAPTERS, "onchigh_run", _fake_ingest)
    (tmp_path / "members.txt").write_text(f"{A} {X}\n{B} {Y}\n")
    kp = signing.generate_keypair()
    query = ("SELECT policy_id, superseded_by FROM drugref.class_expansion_policy "
             "ORDER BY policy_id")
    with psycopg.connect(origin) as c:
        _fake_ingest(c, [tmp_path / "members.txt"], "r1")
        interactions.record_expansion_decision(
            c, "MED-RT", "N0000009065", "deny", "Hematologic Activity Alteration [PE]",
            "re-reviewed", "test", "2026")
        c.commit()
        release_archive.publish_archived(
            c, root=root, release_tag=TAG, published_by="op",
            private_key=kp.private_key, key_fingerprint=signing.fingerprint(kp.public_key))
        c.commit()
        want = c.execute(query).fetchall()
    assert sum(sup is not None for _, sup in want) == 1     # the seeded row, superseded
    with psycopg.connect(target) as c:
        report = release_replay.replay(c, root, TAG)
        # The policy (the release's signature is not loaded).
        assert report.differences == [] and report.curated_rows == 1
        assert c.execute(query).fetchall() == want


def test_a_seeded_row_the_export_spells_differently_is_a_difference(published):
    """db/010 seeds expansion policies, so the target has them before any load and
    the exported copy is never inserted. It is compared instead (#277): a seeded
    row that differs from the exported one would otherwise vanish without a word."""
    export = archive.curated_export_path(published["root"], TAG)
    seeded = {}

    def alter(body):
        row = body["class_expansion_policy"][0]
        row["rationale"] = "not what db/010 seeds"
        seeded["id"] = row["policy_id"]
    _edit(export, alter)
    report = _replay(published)
    assert report.differences == [
        f"class_expansion_policy {seeded['id']}: the exported row differs from the "
        "one a migration seeded"]


def test_a_seeded_row_the_export_lacks_is_a_difference(published):
    """Every seeded row must be compared: one the export omits never is (#278)."""
    export = archive.curated_export_path(published["root"], TAG)
    seeded = {}
    _edit(export, lambda b: seeded.update(
        id=b["class_expansion_policy"].pop(0)["policy_id"]))
    assert _replay(published).differences == [
        f"class_expansion_policy {seeded['id']}: a migration seeded it, but the "
        "export lacks it"]


def test_a_superseder_the_export_lacks_is_a_difference(published):
    """A row pointing at a superseder the export does not carry loads live, though
    the origin had it superseded: nothing would repoint it, so report it (#278).
    A seeded row is the quietest case: its pointer is not compared at all."""
    export = archive.curated_export_path(published["root"], TAG)
    seeded = {}

    def dangle(body):
        row = body["class_expansion_policy"][0]
        row["superseded_by"] = 999_999
        seeded["id"] = row["policy_id"]
    _edit(export, dangle)
    assert _replay(published).differences == [
        f"class_expansion_policy [{seeded['id']}]: superseded by 999999, which the "
        "export lacks"]


def test_point_rows_refuses_to_repoint_fewer_rows_than_asked(conn):
    """A pointer that changes no row (here the id is missing; an already-superseded
    row matches no row either) would leave the replayed history silently unlike
    the export's (#277)."""
    live = conn.execute("SELECT min(policy_id) FROM drugref.class_expansion_policy"
                        ).fetchone()[0]
    with pytest.raises(ValueError, match="repointed 0 of 1"):
        overlay.point_rows(conn, "class_expansion_policy", "policy_id", live, [-1])


def test_a_writer_without_an_adapter_is_refused(published):
    record = archive.release_record_path(published["root"], TAG)
    _edit(record, lambda b: b["runs"][0].update(writer="curation"))
    with pytest.raises(release_replay.ReplayRefused, match="curation"):
        _replay(published)


def _assert_nothing_written(p):
    with psycopg.connect(p["target"]) as c:
        assert c.execute("SELECT to_regclass('drugref.schema_migration')"
                         ).fetchone()[0] is None


def test_a_damaged_archive_is_refused_before_anything_is_written(published):
    record = archive.release_record_path(published["root"], TAG)
    cs = json.loads(record.read_text())["runs"][0]["source_checksum"]
    archive.run_record_path(published["root"], cs).unlink()
    with pytest.raises(release_replay.ReplayRefused, match="no run record"):
        _replay(published)
    _assert_nothing_written(published)


def _argv(p):
    return ["--dsn", p["target"], "audit", "replay", "--release", TAG]


def test_the_cli_exit_codes(published, capsys):
    assert cli.main(_argv(published)) == 0
    assert "reproduced" in capsys.readouterr().out
    assert cli.main(_argv(published)) == 2        # now non-empty: refused
    assert "not empty" in capsys.readouterr().err


def test_the_cli_exits_1_on_a_difference(published, capsys):
    export = archive.curated_export_path(published["root"], TAG)
    _edit(export, lambda b: b["curated_class_interaction_reach"].pop(0))
    assert cli.main(_argv(published)) == 1
    out = capsys.readouterr().out
    assert "reach differs" in out and "1 difference(s)" in out


def test_the_cli_exits_1_when_the_replay_is_not_verified(published, capsys):
    _edit(archive.release_record_path(published["root"], TAG), lambda b: b.pop("manifest"))
    assert cli.main(_argv(published)) == 1
    captured = capsys.readouterr()
    assert "NOT verified" in captured.out and "reproduced" not in captured.out
    assert "predates manifest" in captured.err


def test_a_failure_after_writing_began_exits_3_not_refused(published, capsys,
                                                          monkeypatch):
    """An orchestrator's own ValueError mid-replay is no refusal: the target is
    migrated and part-built, and the operator must be told to drop it."""
    calls = []

    def second_run_fails(conn, files, release):
        calls.append(release)
        if len(calls) == 2:
            raise ValueError("group would have no member")
        _fake_ingest(conn, files, release)
    monkeypatch.setitem(release_replay.ADAPTERS, "onchigh_run", second_run_fails)
    assert cli.main(_argv(published)) == 3
    err = capsys.readouterr().err
    assert "group would have no member" in err and "drop" in err
    assert "refused" not in err


def test_a_bad_tag_is_refused_by_the_cli(published, capsys):
    argv = _argv(published)
    argv[-1] = "../escape"
    assert cli.main(argv) == 2
    _assert_nothing_written(published)


@pytest.fixture
def lab_published(databases, tmp_path, monkeypatch):
    """Origin: run 1 (X, Y in A), a CLASS lab-effect ruling on A, run 2 (X leaves A),
    published. The ruling reached X and Y when made (db/090, #294)."""
    from drugref import curation_lab_effect
    origin, target = databases
    root = tmp_path / "archive"
    monkeypatch.setenv(archive.ENV_VAR, str(root))
    monkeypatch.setattr(release_archive, "git_commit", lambda: SHA)
    monkeypatch.setitem(release_replay.ADAPTERS, "onchigh_run", _fake_ingest)
    one, two = tmp_path / "in1", tmp_path / "in2"
    one.mkdir(), two.mkdir()
    # B is there because the fake ingest files its A x B rule on the first run.
    (one / "members.txt").write_text(f"{A} {X}\n{A} {Y}\n{B} {Y}\n")
    (two / "members.txt").write_text(f"{A} {Y}\n{B} {Y}\n")
    kp = signing.generate_keypair()
    with psycopg.connect(origin) as c:
        _fake_ingest(c, [one / "members.txt"], "r1")
        rid, _ = curation_lab_effect.record_lab_effect_ruling(
            c, subject_class_uuid=ids.mint_class_uuid("MED-RT", A),
            analyte_code="potassium", direction="high", role="causes", verdict="holds",
            reviewed_by="test", reviewed_against="2026")
        c.commit()
        _fake_ingest(c, [two / "members.txt"], "r2")
        release_archive.publish_archived(
            c, root=root, release_tag=TAG, published_by="op",
            private_key=kp.private_key, key_fingerprint=signing.fingerprint(kp.public_key))
        c.commit()
    return {"origin": origin, "target": target, "root": root, "ruling": rid}


def test_a_class_lab_effect_ruling_replays_with_its_reach(lab_published):
    report = _replay(lab_published)
    assert report.differences == [] and report.reproduced
    with psycopg.connect(lab_published["target"]) as c:
        (members,) = c.execute(
            "SELECT members FROM drugref.curated_lab_effect_reach "
            "WHERE curated_lab_effect_id = %s", (lab_published["ruling"],)).fetchone()
    assert set(members) == {ids.mint_moiety_uuid(X), ids.mint_moiety_uuid(Y)}


def test_a_lab_effect_reach_the_replay_does_not_rebuild_is_a_difference(lab_published):
    export = archive.curated_export_path(lab_published["root"], TAG)
    _edit(export, lambda b: b["curated_lab_effect_reach"][0]["members"].pop())
    assert _replay(lab_published).differences == [
        f"lab-effect class ruling {lab_published['ruling']}: reach differs from ruling "
        "time: 1 added, 0 missing"]

"""The replay reproduces or reports db/083's ever-graded record (#281, #279, #258).

A release carries every observation (an episode in which a ruling key graded a
pair) and every curator acknowledgement that closed one. The replay never LOADS
observations: db/083's triggers regenerate them in the target as rulings load and
runs finish, so the exported ones are COMPARED at the end. Acknowledgements are
curator acts, so they load, each onto the replayed episode of its (key, pair)
that is open when it loads.

The origin is `test_release_replay`'s story: run 1 files X and Y in class A and Y
in class B, a curator rules A x B (grading (X, Y)), run 2 files X out of A, so
(X, Y) departs. Some tests then acknowledge X before publishing.
"""
import dataclasses
import datetime as dt
import json

import psycopg
import pytest

from drugref import (archive, curation, graded_pairs, ids, keys, release_archive,
                     release_replay, signatures, signing)
from tests.test_release_replay import (  # noqa: F401  (`databases` is a fixture)
    A, B, SHA, TAG, X, Y, _edit, _fake_ingest, databases)

_KEY_PAIR = ("SELECT rule_grain, subject_key, object_class_uuid, relationship, "
             f"subject_moiety, partner_moiety FROM {graded_pairs.OBSERVATION_TABLE}")


def _rule(c, applies=True):
    return curation.record_class_interaction_judgement(
        c, ids.mint_class_uuid("MED-RT", A), ids.mint_class_uuid("MED-RT", B),
        "CI_MoA", applies, severity="major" if applies else None,
        evidence_grade="established" if applies else None,
        reviewed_by="test", reviewed_against="2026")


def _departed(c):
    """`departed` less `first_seen_at`: the replay stamps its own episodes."""
    return [dataclasses.replace(d, first_seen_at=None)
            for d in graded_pairs.departed(c)]


@pytest.fixture
def build(databases, tmp_path, monkeypatch):  # noqa: F811
    """`build(withdraw=False, acknowledge=False, twice=False, cycle=False)` publishes
    the origin and returns
    {origin, target, root, ruling, departed, keys, acks}: the origin's departures,
    (key, pair) set and acknowledgement rows (less ids), read after publishing."""
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

    def _build(*, withdraw=False, acknowledge=False, twice=False, cycle=False,
               sign=False):
        with psycopg.connect(origin) as c:
            _fake_ingest(c, [one / "members.txt"], "r1")
            rid = _rule(c)
            c.commit()
            if withdraw:      # a second transaction before run 2: same batch
                rid = _rule(c, applies=False)
                c.commit()
            _fake_ingest(c, [two / "members.txt"], "r2")
            if twice:     # a second open episode, as two concurrent commits leave
                c.execute(f"INSERT INTO {graded_pairs.OBSERVATION_TABLE} "
                          "(rule_grain, subject_key, object_class_uuid, relationship, "
                          "subject_moiety, partner_moiety, captured) "
                          "SELECT rule_grain, subject_key, object_class_uuid, "
                          "relationship, subject_moiety, partner_moiety, captured "
                          f"FROM {graded_pairs.OBSERVATION_TABLE}")
            if acknowledge:
                assert graded_pairs.departed(c), "run 2 must depart (X, Y)"
                graded_pairs.acknowledge(
                    c, "class", rid, ids.mint_moiety_uuid(X), rationale="X left A",
                    reviewed_by="test", reviewed_against="2026")
                c.commit()
            if sign:      # db/088: the curator signs the ack (kp also signs the release)
                keys.register(c, public_key=kp.public_key, holder="test",
                              registered_by="op")
                (ack,) = c.execute(f"SELECT ack_id FROM {graded_pairs.ACK_TABLE}"
                                   ).fetchone()
                when = dt.datetime.now(dt.timezone.utc)
                fp = signing.fingerprint(kp.public_key)
                context, payload = signatures.payload_for(
                    c, graded_pairs.ACK_TARGET_KIND, ack, key_fingerprint=fp,
                    signed_at=when)
                signatures.record(
                    c, target_kind=graded_pairs.ACK_TARGET_KIND, target_id=ack,
                    payload_context=context, payload=payload, key_fingerprint=fp,
                    signature=signing.sign(kp.private_key, payload), signed_at=when)
                c.commit()
            if cycle:     # X returns (a second episode opens), then leaves again
                _fake_ingest(c, [one / "members.txt"], "r3")
                _fake_ingest(c, [two / "members.txt"], "r4")
            release_archive.publish_archived(
                c, root=root, release_tag=TAG, published_by="op",
                private_key=kp.private_key,
                key_fingerprint=signing.fingerprint(kp.public_key))
            c.commit()
            return {"origin": origin, "target": target, "root": root, "ruling": rid,
                    "departed": _departed(c), "keys": set(c.execute(_KEY_PAIR)),
                    "acks": _acks(c)}
    return _build


def _acks(c):
    """Each ack as (ack_id, its episode's (key, pair), rationale): the episode's
    observation_id is the target's own, so the (key, pair) stands in for it."""
    return c.execute(
        "SELECT a.ack_id, o.rule_grain, o.subject_key, o.object_class_uuid, "
        "o.relationship, o.subject_moiety, o.partner_moiety, a.rationale "
        f"FROM {graded_pairs.ACK_TABLE} a JOIN {graded_pairs.OBSERVATION_TABLE} o "
        "USING (observation_id) ORDER BY a.ack_id").fetchall()


def _replay(p):
    with psycopg.connect(p["target"]) as c:
        return release_replay.replay(c, p["root"], TAG)


def _line(o):
    """An exported observation as the difference lines spell it."""
    return " ".join(o[k] for k in ("rule_grain", "subject_key", "object_class_uuid",
                                   "relationship", "subject_moiety", "partner_moiety"))


def _export(p):
    return archive.curated_export_path(p["root"], TAG)


def test_a_replay_reproduces_observations(build):
    p = build()
    assert p["departed"], "the origin must have a departure to reproduce"
    report = _replay(p)
    assert report.differences == [] and report.warnings == [] and report.reproduced
    with psycopg.connect(p["target"]) as c:
        assert set(c.execute(_KEY_PAIR)) == p["keys"]
        assert _departed(c) == p["departed"]


def test_an_observation_the_replay_lacks_is_a_difference(build):
    p = build()
    tampered = {}

    def retarget(body):
        (o,) = body["graded_pair_observation"]
        tampered["real"] = _line(o)
        o["partner_moiety"] = str(ids.mint_moiety_uuid("TESTUNI281Z"))
        tampered["fake"] = _line(o)
    _edit(_export(p), retarget)
    assert _replay(p).differences == [
        "graded pair observed in the origin but not the replay: " + tampered["fake"],
        "graded pair observed in the replay but not the origin: " + tampered["real"],
        "graded pair left open in the origin but not the replay: " + tampered["fake"],
        "graded pair left open in the replay but not the origin: " + tampered["real"]]


def test_a_backfilled_export_warns_and_skips_the_comparison(build):
    """An origin with history before db/083 cannot reproduce it (spec 5.6): the
    replay migrates an empty database, so its backfill writes nothing."""
    p = build()

    def backfilled(body):
        (o,) = body["graded_pair_observation"]
        o.update(captured="backfill_db083",
                 partner_moiety=str(ids.mint_moiety_uuid("TESTUNI281Z")))
    _edit(_export(p), backfilled)
    report = _replay(p)
    assert report.differences == []
    assert report.warnings == ["observations predate db/083: not reproducible"]
    assert not report.reproduced


def test_an_acknowledgement_is_replayed_onto_its_episode(build):
    p = build(acknowledge=True)
    assert p["departed"] == [] and len(p["acks"]) == 1
    report = _replay(p)
    assert report.differences == [] and report.reproduced
    with psycopg.connect(p["target"]) as c:
        assert _departed(c) == p["departed"]
        assert _acks(c) == p["acks"]
        # The ack id was loaded verbatim: the next one must not collide with it.
        nxt = c.execute("SELECT nextval(pg_get_serial_sequence("
                        "'drugref.graded_pair_departure_ack', 'ack_id'))").fetchone()[0]
        assert nxt > p["acks"][0][0]


def test_a_signed_ack_still_verifies_after_a_replay(build):
    """db/088 signs the ack's episode by CONTENT because the replay loads the ack
    onto ITS OWN observation id; a signature over the id would break here."""
    p = build(acknowledge=True, sign=True)
    assert _replay(p).reproduced
    with psycopg.connect(p["target"]) as c:
        (ack,) = c.execute(f"SELECT ack_id FROM {graded_pairs.ACK_TABLE}").fetchone()
        assert [v.verdict for v in signatures.verify_target(
            c, graded_pairs.ACK_TARGET_KIND, ack)] == [signing.VALID]


def test_an_ack_with_no_open_episode_is_a_difference(build):
    """A second ack of the same episode, a minute later: the first closed it, so
    the replay has no open episode to attach it to. Reported, skipped, not raised."""
    p = build(acknowledge=True)

    def again(body):
        (ack,) = body["graded_pair_departure_ack"]
        later = (release_replay._when(ack["acknowledged_at"])
                 + dt.timedelta(minutes=1))
        body["graded_pair_departure_ack"].append(
            dict(ack, ack_id=ack["ack_id"] + 1, acknowledged_at=later.isoformat()))
    _edit(_export(p), again)
    report = _replay(p)
    nxt = p["acks"][0][0] + 1
    assert report.differences == [
        f"acknowledgement {nxt} has no open episode in the replay"]
    with psycopg.connect(p["target"]) as c:
        assert _acks(c) == p["acks"]


def test_a_ruling_withdrawn_before_the_next_run_keeps_its_observation(build):
    """The ruling and its withdrawal were two origin commits, so the origin observed
    (X, Y) in between; the replay loads both in ONE batch before run 2, and must
    refresh between them as the origin's first commit did, or it never sees it."""
    p = build(withdraw=True)
    assert p["keys"], "the origin observed (X, Y) before the withdrawal"
    assert _replay(p).differences == []
    with psycopg.connect(p["target"]) as c:
        assert set(c.execute(_KEY_PAIR)) == p["keys"]


def test_the_export_carries_both_tables(build):
    body = json.loads(_export(build(acknowledge=True)).read_text())
    assert len(body["graded_pair_observation"]) == 1
    assert len(body["graded_pair_departure_ack"]) == 1


def test_an_ack_of_a_concurrent_second_episode_is_a_warning(build):
    """Two concurrent origin commits opened (X, Y) twice and one `acknowledge`
    closed both (Review Focus 1). The serial replay opens it once, so the second
    ack has no episode; that is the replay's limit, not damage: a warning."""
    p = build(acknowledge=True, twice=True)
    assert len(p["acks"]) == 2 and p["departed"] == []
    report = _replay(p)
    second = p["acks"][1][0]
    assert report.differences == []
    assert report.warnings == [
        f"acknowledgement {second} closed a second concurrent episode of its pair, "
        "which the serial replay never opened: skipped"]
    with psycopg.connect(p["target"]) as c:
        assert _departed(c) == p["departed"] and _acks(c) == p["acks"][:1]


def test_a_second_episode_after_an_ack_is_reproduced(build):
    """Ack, X comes back (episode 2 opens), X leaves again: episode 2 is open and
    departed in both, and the ack sits on episode 1 in both."""
    p = build(acknowledge=True, cycle=True)
    assert p["departed"] and len(p["acks"]) == 1
    report = _replay(p)
    assert report.differences == [] and report.reproduced
    with psycopg.connect(p["target"]) as c:
        assert _departed(c) == p["departed"] and _acks(c) == p["acks"]


def test_an_ack_that_closed_the_other_episode_is_a_difference(build):
    """Origin: ack episode 1, X returns (episode 2), X leaves: episode 2 is open.
    The export's ack is moved after run 4, so the replay loads it onto the latest
    open episode, which is episode 1 (run 3 opened nothing while it was open). Both
    sides hold the same (key, pair)s; only the origin has one left open, and that
    is reported (#286 review)."""
    p = build(acknowledge=True, cycle=True)

    def late(body):
        (ack,) = body["graded_pair_departure_ack"]
        ack["acknowledged_at"] = (release_replay._when(ack["acknowledged_at"])
                                  + dt.timedelta(hours=1)).isoformat()
    _edit(_export(p), late)
    (o, *_) = json.loads(_export(p).read_text())["graded_pair_observation"]
    assert _replay(p).differences == [
        "graded pair left open in the origin but not the replay: " + _line(o)]

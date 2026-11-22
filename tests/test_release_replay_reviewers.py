"""The replay restores who vouched for what (#270, owner ruling 2026-11-21).

The curated export carries the reviewer-service's annotations and evidence
references, the accounts and profiles they name, and the PUBLIC signing keys
(their whole status history: a revocation is a superseding row) and every
signature. It never carries a password hash or a session.

The end-to-end test reproduces #270's failure: a reviewer annotates a question
between two runs, and run 2 closes the question's gap. The origin keeps the
question (an annotation cites it); a replay that did not load the annotation
before run 2 would delete it.
"""
import datetime as dt
import uuid

import psycopg
from psycopg import sql

from drugref import (archive, curation, ids, keys, release_archive, release_replay,
                     signatures, signing)
from tests.test_release_replay import (  # noqa: F401  (`databases` is a fixture)
    A, B, SHA, TAG, X, Y, _fake_ingest, _t, databases)

#: The tables the owner's ruling adds, in the order a replay must load them.
_VOUCHED = ("reviewer_account", "reviewer_profile", "signing_key",
            "reviewer_key_enrolment", "assertion_signature", "reviewer_annotation",
            "reviewer_evidence_reference")


def test_credentials_and_sessions_are_never_exported():
    never = {"reviewer_password_credential", "auth_session", "auth_session_revocation"}
    assert never <= set(release_archive.NOT_EXPORTED)
    assert never & set(release_archive._EXPORT_TABLES) == set()
    assert set(_VOUCHED) <= set(release_archive._EXPORT_TABLES)


def test_schedule_reads_each_tables_own_timestamp():
    runs = [{"finished_at": _t(5)}]
    rows = [("reviewer_annotation", {"reviewer_annotation_id": 1, "recorded_at": _t(9)}),
            ("signing_key", {"signing_key_id": 1, "registered_at": _t(1)})]
    assert release_replay.schedule(rows, runs) == [[rows[1]], [rows[0]]]


def test_schedule_loads_what_a_row_references_first_at_one_instant():
    """One transaction stamps one `now()`: an account, its profile, a key and its
    enrolment, a ruling, its signature and a note all tie, and each must load after
    the rows its foreign keys name."""
    at = _t(0)
    rows = [("reviewer_evidence_reference",
             {"reviewer_evidence_reference_id": 1, "recorded_at": at}),
            ("reviewer_annotation", {"reviewer_annotation_id": 1, "recorded_at": at}),
            ("assertion_signature", {"signature_id": 1, "recorded_at": at}),
            ("curated_interaction", {"curated_interaction_id": 1, "reviewed_at": at}),
            ("reviewer_key_enrolment",
             {"reviewer_key_enrolment_id": 1, "enrolled_at": at}),
            ("signing_key", {"signing_key_id": 1, "registered_at": at}),
            ("reviewer_profile", {"reviewer_profile_id": 1, "recorded_at": at}),
            ("reviewer_account", {"reviewer_uuid": "u", "created_at": at})]
    (only,) = release_replay.schedule(rows, [])
    assert [t for t, _ in only] == [
        "reviewer_account", "reviewer_profile", "signing_key", "reviewer_key_enrolment",
        "curated_interaction", "assertion_signature", "reviewer_annotation",
        "reviewer_evidence_reference"]


def _reviewer(c, kp) -> uuid.UUID:
    """An account, its profile and an enrolled key, as the reviewer-service writes
    them (bootstrap administrator: created by nobody, profile recorded by itself)."""
    who = uuid.uuid4()
    c.execute("INSERT INTO drugref.reviewer_account (reviewer_uuid, username) "
              "VALUES (%s, 'curator.one')", (who,))
    c.execute("INSERT INTO drugref.reviewer_profile (reviewer_uuid, full_name, role, "
              "active, recorded_by) VALUES (%s, 'Curator One', 'administrator', true, "
              "%s)", (who, who))
    key = keys.register(c, public_key=kp.public_key, holder="Curator One",
                        registered_by="curator.one")
    c.execute("INSERT INTO drugref.reviewer_key_enrolment (reviewer_uuid, "
              "signing_key_id, enrolled, enrolled_by) VALUES (%s, %s, true, %s)",
              (who, key, who))
    return who


def _sign(c, kp, kind, target_id) -> None:
    fp = signing.fingerprint(kp.public_key)
    when = dt.datetime.now(dt.timezone.utc)
    context, payload = signatures.payload_for(c, kind, target_id, key_fingerprint=fp,
                                              signed_at=when)
    signatures.record(c, target_kind=kind, target_id=target_id,
                      payload_context=context, payload=payload, key_fingerprint=fp,
                      signature=signing.sign(kp.private_key, payload), signed_at=when)


def _rows(c, table):
    return c.execute(sql.SQL("SELECT to_jsonb(t) FROM drugref.{} t ORDER BY 1").format(
        sql.Identifier(table))).fetchall()


def test_a_replay_restores_who_vouched_for_what(databases, tmp_path,  # noqa: F811
                                                monkeypatch):
    origin, target = databases
    root = tmp_path / "archive"
    monkeypatch.setenv(archive.ENV_VAR, str(root))
    monkeypatch.setattr(release_archive, "git_commit", lambda: SHA)
    monkeypatch.setitem(release_replay.ADAPTERS, "onchigh_run", _fake_ingest)
    one, two = tmp_path / "in1", tmp_path / "in2"
    one.mkdir(), two.mkdir()
    (one / "members.txt").write_text(f"{A} {X}\n{A} {Y}\n{B} {Y}\n")
    (two / "members.txt").write_text(f"{A} {Y}\n{B} {Y}\n")
    curator, institution = signing.generate_keypair(), signing.generate_keypair()
    rule = (f"CLASS:{ids.mint_class_uuid('MED-RT', A)}/"
            f"CLASS:{ids.mint_class_uuid('MED-RT', B)}/CI_AXIS:CI_MoA")
    question = ids.mint_question_uuid("uncurated_class_interaction_rule", rule)
    with psycopg.connect(origin) as c:
        _fake_ingest(c, [one / "members.txt"], "r1")
        assert c.execute("SELECT 1 FROM drugref.open_question WHERE question_uuid = %s",
                         (question,)).fetchone(), "run 1 must raise the class question"
        who = _reviewer(c, curator)
        c.execute("INSERT INTO drugref.reviewer_annotation (question_uuid, "
                  "reviewer_uuid, annotation_markdown) VALUES (%s, %s, 'see SmPC')",
                  (question, who))
        c.execute("INSERT INTO drugref.reviewer_evidence_reference (question_uuid, "
                  "reviewer_uuid, reference_scheme, reference_value) "
                  "VALUES (%s, %s, 'PMID', '12345')", (question, who))
        ruling = curation.record_class_interaction_judgement(
            c, ids.mint_class_uuid("MED-RT", A), ids.mint_class_uuid("MED-RT", B),
            "CI_MoA", True, severity="major", evidence_grade="established",
            reviewed_by="curator.one", reviewed_against="2026")
        _sign(c, curator, "curated_class_interaction", ruling)
        c.commit()
        # The revocation history travels with the key: a later superseding row.
        keys.revoke(c, key_fingerprint=signing.fingerprint(curator.public_key),
                    status="rotated", revoked_by="curator.one")
        c.commit()
        _fake_ingest(c, [two / "members.txt"], "r2")
        release_archive.publish_archived(
            c, root=root, release_tag=TAG, published_by="op",
            private_key=institution.private_key,
            key_fingerprint=signing.fingerprint(institution.public_key))
        c.commit()
        want = {t: _rows(c, t) for t in (*_VOUCHED, "open_question")}
        want_status = c.execute("SELECT * FROM drugref.curated_signature_status "
                                "ORDER BY 1, 2").fetchall()
    assert len(want["signing_key"]) == 2            # registered, then rotated
    assert len(want["assertion_signature"]) == 2    # the curator's and the release's
    with psycopg.connect(target) as c:
        report = release_replay.replay(c, root, TAG)
        assert report.differences == [] and report.reproduced
        # Run 2 closed the gap; the annotation loaded before it kept the question.
        assert {t: _rows(c, t) for t in want} == want
        assert c.execute("SELECT * FROM drugref.curated_signature_status "
                         "ORDER BY 1, 2").fetchall() == want_status
        # Every identity moved past the loaded ids: the next note gets a fresh one.
        nxt = c.execute("SELECT nextval(pg_get_serial_sequence("
                        "'drugref.reviewer_annotation', 'reviewer_annotation_id'))"
                        ).fetchone()[0]
        assert nxt > 1

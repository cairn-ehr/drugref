"""`drugref audit departures` (#281): one listing of every open departure. DB-gated."""
from drugref import cli_departures, graded_pairs
from tests.graded_pair_helpers import commit_point  # noqa: F401  (fixture parity)
from tests.test_graded_pairs_departed import _a_rule_x_left, _moiety_rule

_WHY = dict(rationale="left on purpose", reviewed_by="dr t", reviewed_against="2026")


def test_no_departures_exits_0(conn, capsys):
    assert cli_departures.handle_audit_departures(conn, None) == 0
    assert "open departures: 0 ruling(s), 0 pair(s)" in capsys.readouterr().out


def test_departures_across_rulings_are_listed_and_exit_3(conn, ingest_run_id, capsys):
    cruling, x, _z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 1)
    mruling, _s, p, _b2 = _moiety_rule(conn, ingest_run_id, 2)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (p,))

    assert cli_departures.handle_audit_departures(conn, None) == 3
    out = capsys.readouterr().out
    assert "open departures: 2 ruling(s), 2 pair(s)" in out
    assert f"class ruling {cruling}" in out and f"moiety ruling {mruling}" in out
    assert out.count("first seen:") == 2


def test_acknowledged_ones_are_not_listed(conn, ingest_run_id, capsys):
    ruling, x, _z, _p, _a, _b = _a_rule_x_left(conn, ingest_run_id, 3)
    graded_pairs.acknowledge(conn, "class", ruling, x, **_WHY)

    assert cli_departures.handle_audit_departures(conn, None) == 0
    assert "open departures: 0" in capsys.readouterr().out


def test_without_db083_exits_2_with_a_note(conn, capsys):
    conn.execute(f"DROP TABLE {graded_pairs.OBSERVATION_TABLE} CASCADE")

    assert cli_departures.handle_audit_departures(conn, None) == 2
    captured = capsys.readouterr()
    assert "open departures" not in captured.out
    assert "083" in captured.err and "DROPPED" in captured.err


def test_one_pair_departed_by_two_rulings_is_counted_once(conn, ingest_run_id, capsys):
    """A class rule (departs (X,P) and (Z,P)) and a moiety rule (departs (X,P)):
    3 pair-per-ruling entries, but only 2 distinct pairs."""
    from drugref import curation
    _c, x, _z, p, _a, b = _a_rule_x_left(conn, ingest_run_id, 4)
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)", (x, b, ingest_run_id))
    curation.record_interaction_judgement(
        conn, x, b, "CI_MoA", True, severity="major", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="2026.07.06")
    commit_point(conn)
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (p,))

    assert cli_departures.handle_audit_departures(conn, None) == 3
    assert "open departures: 2 ruling(s), 2 pair(s)" in capsys.readouterr().out


def test_an_orphan_is_counted_and_exits_3(conn, ingest_run_id, capsys):
    """`departed` leaves an orphaned ruling to #121's banner, so the audit, which is
    the post-ingest gate, must not read "0" over it (#286 review)."""
    _a_rule_x_left(conn, ingest_run_id, 5)
    conn.execute("DELETE FROM drugref.class_pair_contraindication")

    assert cli_departures.handle_audit_departures(conn, None) == 3
    out = capsys.readouterr().out
    assert "open departures: 0 ruling(s), 0 pair(s)" in out
    assert "orphaned rulings: 1" in out and "drugref status" in out


# ---- db/088 (#284): acknowledgement signature status -------------------------


def _ack_id(conn):
    return conn.execute(f"SELECT max(ack_id) FROM {graded_pairs.ACK_TABLE}").fetchone()[0]


def test_no_acknowledgements_reads_zero(conn, capsys):
    cli_departures.handle_audit_departures(conn, None)
    assert "acknowledgements: 0" in capsys.readouterr().out


def test_an_unsigned_ack_is_named_with_the_command_that_signs_it(
        conn, ingest_run_id, capsys):
    """Unsigned is a provenance gap, not a missing departure: named, exit unchanged."""
    ruling, x, *_ = _a_rule_x_left(conn, ingest_run_id, 6)
    graded_pairs.acknowledge(conn, "class", ruling, x, **_WHY)

    assert cli_departures.handle_audit_departures(conn, None) == 0
    out = capsys.readouterr().out
    assert "acknowledgements: 1 (unsigned 1)" in out
    assert f"not signed by a registry-trusted key: ack {_ack_id(conn)}" in out
    assert "drugref sign --target-kind graded_pair_departure_ack" in out


def _sign_the_ack(conn, ingest_run_id, n):
    """Acknowledge a departure and sign its ack with a fresh registered key.
    Returns the key's fingerprint."""
    import datetime as dt

    from drugref import keys, signatures, signing
    ruling, x, *_ = _a_rule_x_left(conn, ingest_run_id, n)
    graded_pairs.acknowledge(conn, "class", ruling, x, **_WHY)
    kp = signing.generate_keypair()
    fp = signing.fingerprint(kp.public_key)
    keys.register(conn, public_key=kp.public_key, holder="dr t", registered_by="op")
    at = dt.datetime(2026, 11, 26, tzinfo=dt.timezone.utc)
    context, payload = signatures.payload_for(
        conn, "graded_pair_departure_ack", _ack_id(conn), key_fingerprint=fp,
        signed_at=at)
    signatures.record(conn, target_kind="graded_pair_departure_ack",
                      target_id=_ack_id(conn), payload_context=context,
                      payload=payload, key_fingerprint=fp,
                      signature=signing.sign(kp.private_key, payload), signed_at=at)
    return fp


def test_a_signed_ack_needs_no_signing(conn, ingest_run_id, capsys):
    _sign_the_ack(conn, ingest_run_id, 7)

    cli_departures.handle_audit_departures(conn, None)
    out = capsys.readouterr().out
    assert "acknowledgements: 1 (signed 1)" in out
    assert "not signed" not in out


def test_an_ack_whose_signature_no_longer_verifies_needs_signing(
        conn, ingest_run_id, capsys):
    """Acks are not in a signed release, so the audit is the routine check that
    their signatures still verify: an altered rationale keeps its registry-trusted
    signature row but must not count as signed."""
    _sign_the_ack(conn, ingest_run_id, 9)
    conn.execute("ALTER TABLE drugref.graded_pair_departure_ack "
                 "DISABLE TRIGGER graded_pair_departure_ack_insert_only")
    conn.execute(f"UPDATE {graded_pairs.ACK_TABLE} SET rationale = 'something else'")

    cli_departures.handle_audit_departures(conn, None)
    out = capsys.readouterr().out
    assert "acknowledgements: 1 (bad_signature 1)" in out
    assert f"not signed by a registry-trusted key: ack {_ack_id(conn)}" in out


def test_an_ack_signed_only_by_a_compromised_key_needs_signing(conn, ingest_run_id,
                                                                capsys):
    """"Needs a signature" is anything but `signed`, not just `unsigned`: a
    compromised key's signature must not count as trusted."""
    from drugref import keys
    fp = _sign_the_ack(conn, ingest_run_id, 8)
    keys.revoke(conn, key_fingerprint=fp, status="compromised", revoked_by="op")

    cli_departures.handle_audit_departures(conn, None)
    out = capsys.readouterr().out
    assert "(signed_by_revoked_key 1)" in out
    assert f"not signed by a registry-trusted key: ack {_ack_id(conn)}" in out

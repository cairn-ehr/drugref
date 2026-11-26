"""db/088: departure acknowledgements are signable (#284). DB-gated.

WHY. An acknowledgement silences a CHANGED SINCE REVIEW banner, so a forged or
altered one hides a departure from every later reviewer. Curated rulings carry
detached curator signatures (db/030); acks carried none. db/088 registers them as a
fifth curator target kind, so `drugref sign` / `drugref verify` and the reviewer
GUI sign them the same way.

WHY A VIEW IS THE TARGET, NOT THE TABLE. The ack row names its episode by
`observation_id`, a database-local id that a release replay renumbers
(`replay_loader._ack_episode`). A signature over it would stop verifying after a
replay, and would not say WHICH pair was accepted. The view signs the episode's
content instead: the ruling key, the drug pair and the episode's ordinal within
them, plus the curator's own fields.
"""
import datetime as dt

from drugref import graded_pairs, keys, signatures, signing
from tests.test_class_subject_read_path import _file_member
from tests.test_cli_signing import _run, a_key_file, wconn  # noqa: F401 (fixtures)
from tests.test_graded_pairs_departed import _a_rule_x_left

KIND = "graded_pair_departure_ack"
SIGNED_AT = dt.datetime(2026, 11, 26, 4, 5, 6, 7000, tzinfo=dt.timezone.utc)
_WHY = dict(rationale="left on purpose", reviewed_by="dr t",
            reviewed_against="MED-RT 2026.07.06")


def _an_ack(conn, ingest_run_id, n):
    """One class ruling whose subject drug X left; X acknowledged. Returns
    (ack_id, X, partner P, subject class A, object class B)."""
    ruling, x, _z, p, a, b = _a_rule_x_left(conn, ingest_run_id, n)
    graded_pairs.acknowledge(conn, "class", ruling, x, **_WHY)
    (ack_id,) = conn.execute(
        f"SELECT ack_id FROM {graded_pairs.ACK_TABLE} ORDER BY ack_id DESC "
        "LIMIT 1").fetchone()
    return ack_id, x, p, a, b


def _key():
    kp = signing.generate_keypair()
    return kp.private_key, kp.public_key, signing.fingerprint(kp.public_key)


def _sign(conn, ack_id, private, fingerprint):
    context, payload = signatures.payload_for(
        conn, KIND, ack_id, key_fingerprint=fingerprint, signed_at=SIGNED_AT)
    signatures.record(conn, target_kind=KIND, target_id=ack_id,
                      payload_context=context, payload=payload,
                      key_fingerprint=fingerprint,
                      signature=signing.sign(private, payload), signed_at=SIGNED_AT)
    return context


def test_an_ack_is_a_signature_target_kind(conn):
    assert conn.execute(
        "SELECT target_table, pk_column, payload_context "
        "FROM drugref.signature_target_kind WHERE target_kind = %s",
        (KIND,)).fetchone() == ("graded_pair_departure_ack_payload", "ack_id",
                                "graded_pair_departure_ack/v1")


def test_a_signed_ack_verifies_and_reads_signed(conn, ingest_run_id):
    ack_id, *_ = _an_ack(conn, ingest_run_id, 41)
    private, public, fingerprint = _key()
    keys.register(conn, public_key=public, holder="dr t", registered_by="an operator")

    assert _sign(conn, ack_id, private, fingerprint) == "graded_pair_departure_ack/v1"
    assert [v.verdict for v in signatures.verify_target(conn, KIND, ack_id)] == [
        signing.VALID]
    assert conn.execute(
        "SELECT signature_status FROM drugref.departure_ack_signature "
        "WHERE ack_id = %s", (ack_id,)).fetchone() == ("signed",)


def test_an_unsigned_ack_reads_unsigned(conn, ingest_run_id):
    ack_id, *_ = _an_ack(conn, ingest_run_id, 42)
    assert conn.execute(
        "SELECT signature_status, signature_count FROM drugref.departure_ack_signature "
        "WHERE ack_id = %s", (ack_id,)).fetchone() == ("unsigned", 0)


def test_the_payload_names_the_episode_not_its_observation_id(conn, ingest_run_id):
    """The signed fields are the pair and the ruling key a curator accepted; the
    database-local observation id is not among them."""
    ack_id, x, p, a, b = _an_ack(conn, ingest_run_id, 43)
    _context, fields = signatures.payload_fields(
        conn, KIND, ack_id, key_fingerprint="0" * 64, signed_at=SIGNED_AT)
    named = dict(fields)
    assert "observation_id" not in named
    assert (named["rule_grain"], named["subject_key"], named["object_class_uuid"],
            named["subject_moiety"], named["partner_moiety"]) == (
        "class_rule", str(a), str(b), str(x), str(p))
    assert (named["rationale"], named["reviewed_by"], named["reviewed_against"]) == (
        "left on purpose", "dr t", "MED-RT 2026.07.06")


def test_an_altered_rationale_no_longer_verifies(conn, ingest_run_id):
    """The point of signing: a payload rebuilt over different content fails. The
    table is insert-only, so the test alters what the target view reads instead."""
    ack_id, *_ = _an_ack(conn, ingest_run_id, 44)
    private, public, fingerprint = _key()
    keys.register(conn, public_key=public, holder="dr t", registered_by="an operator")
    _sign(conn, ack_id, private, fingerprint)
    conn.execute("ALTER TABLE drugref.graded_pair_departure_ack "
                 "DISABLE TRIGGER graded_pair_departure_ack_insert_only")
    conn.execute(f"UPDATE {graded_pairs.ACK_TABLE} SET rationale = 'something else' "
                 "WHERE ack_id = %s", (ack_id,))

    assert [v.verdict for v in signatures.verify_target(conn, KIND, ack_id)] == [
        signing.BAD_SIGNATURE]


def test_a_signature_copied_onto_a_later_episode_does_not_verify(conn, ingest_run_id):
    """The (key, pair) is not unique per episode: X returns, the pair re-opens, X
    leaves again. A writer who copies the first ack's fields AND its signature onto
    an ack of the second episode must not get a VALID verdict; episode_ordinal is
    what differs (db/088)."""
    ack_id, x, _p, a, _b = _an_ack(conn, ingest_run_id, 46)
    private, public, fingerprint = _key()
    keys.register(conn, public_key=public, holder="dr t", registered_by="an operator")
    _sign(conn, ack_id, private, fingerprint)
    _file_member(conn, x, a, ingest_run_id)
    graded_pairs.refresh(conn)          # X reaches P again: episode 2 opens
    conn.execute("DELETE FROM drugref.class_membership WHERE moiety_uuid = %s", (x,))
    (forged,) = conn.execute(
        f"INSERT INTO {graded_pairs.ACK_TABLE} (observation_id, rationale, "
        "reviewed_by, reviewed_against, acknowledged_at) "
        f"SELECT o2.observation_id, a.rationale, a.reviewed_by, a.reviewed_against, "
        f"a.acknowledged_at FROM {graded_pairs.ACK_TABLE} a "
        f"JOIN {graded_pairs.OBSERVATION_TABLE} o1 ON o1.observation_id = a.observation_id "
        f"JOIN {graded_pairs.OBSERVATION_TABLE} o2 "
        "ON (o2.rule_grain, o2.subject_key, o2.object_class_uuid, o2.relationship, "
        "    o2.subject_moiety, o2.partner_moiety) = (o1.rule_grain, o1.subject_key, "
        "    o1.object_class_uuid, o1.relationship, o1.subject_moiety, o1.partner_moiety) "
        "AND o2.observation_id > o1.observation_id "
        "WHERE a.ack_id = %s RETURNING ack_id", (ack_id,)).fetchone()
    columns = [c for (c,) in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = "
        "'drugref' AND table_name = 'assertion_signature' AND is_generated = 'NEVER' "
        "AND identity_generation IS NULL AND column_name <> 'target_id'").fetchall()]
    listed = ", ".join(columns)
    conn.execute(f"INSERT INTO drugref.assertion_signature (target_id, {listed}) "
                 f"SELECT %s, {listed} FROM drugref.assertion_signature "
                 "WHERE target_kind = %s AND target_id = %s", (forged, KIND, ack_id))

    ordinals = dict(conn.execute(
        "SELECT ack_id, episode_ordinal FROM drugref.graded_pair_departure_ack_payload "
        "WHERE ack_id = ANY(%s)", ([ack_id, forged],)).fetchall())
    assert ordinals == {ack_id: 1, forged: 2}
    assert [v.verdict for v in signatures.verify_target(conn, KIND, forged)] == [
        signing.BAD_SIGNATURE]


def test_drugref_sign_and_verify_take_an_ack(conn, ingest_run_id, wconn, a_key_file,  # noqa: F811
                                             capsys):
    """The CLI needs no ack-specific code: `sign` and `verify` read the catalog."""
    ack_id, *_ = _an_ack(conn, ingest_run_id, 45)
    keys.register(conn, public_key=a_key_file["public"], holder="dr t",
                  registered_by="an operator")
    target = ["--target-kind", KIND, "--target-id", str(ack_id)]

    assert _run(wconn, ["sign", *target, "--key", str(a_key_file["path"])]) == 0
    assert "graded_pair_departure_ack/v1" in capsys.readouterr().out
    assert _run(wconn, ["verify", *target]) == 0
    assert "valid" in capsys.readouterr().out

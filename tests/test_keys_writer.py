# tests/test_keys_writer.py
"""The signing_key registry writer (spec 5.1, 6). DB-gated."""
import datetime as dt
import inspect

import pytest

from drugref import keys, signing

NOW = dt.datetime(2026, 8, 9, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def a_key(conn):
    _, public = signing.generate_keypair()
    keys.register(conn, public_key=public, holder="a curator",
                  registered_by="an operator", status_from=NOW)
    return public


def test_a_registered_key_is_live_and_findable_by_fingerprint(conn, a_key):
    record = keys.live(conn, signing.fingerprint(a_key))
    assert record is not None
    assert record.holder == "a curator"
    assert record.status == "active"
    assert record.public_key == a_key
    assert record.status_from == NOW


def test_public_key_reads_back_as_bytes_not_a_driver_specific_buffer(conn, a_key):
    """KeyRecord annotates `public_key: bytes`, and _record's cast is what makes that
    true regardless of what the driver actually hands back. Equality alone does not
    cover this -- `record.public_key == a_key` passes identically whether or not the
    cast runs, because a memoryview compares equal to the bytes it wraps. isinstance
    does not: it is the one check a driver-version change could silently break, and the
    one this test exists to pin."""
    record = keys.live(conn, signing.fingerprint(a_key))
    assert isinstance(record.public_key, bytes)


def test_an_unregistered_fingerprint_reads_as_None_not_an_error(conn):
    """A signature naming a key nobody registered is an ORDINARY finding -- it is the
    UNKNOWN_KEY verdict -- so the read returns None and the verdict rule decides. An
    exception here would force every verification path to wrap it, and a caller would
    eventually wrap too widely."""
    assert keys.live(conn, "f" * 64) is None
    assert keys.key_status(conn, "f" * 64) is None


def test_register_derives_the_fingerprint_rather_than_accepting_one():
    """register() takes the PUBLIC KEY. Accepting a fingerprint as well would let a
    caller store one that does not match its key -- a row that verifies nothing, reports
    UNKNOWN_KEY forever, and looks entirely healthy in every listing."""
    assert "key_fingerprint" not in inspect.signature(keys.register).parameters


def test_the_stored_fingerprint_always_matches_the_stored_key(conn, a_key):
    """The parameter-name check above is cheap but only catches ONE spelling of the
    hazard -- a differently-named argument that reintroduced a caller-supplied
    fingerprint would walk straight past it. This is the property that actually
    matters and holds for every row this module ever writes: the fingerprint recorded
    beside a key is always the one THAT KEY derives, never a value taken on trust from
    a caller."""
    record = keys.live(conn, signing.fingerprint(a_key))
    assert record.key_fingerprint == signing.fingerprint(record.public_key)


def test_revoking_supersedes_rather_than_editing(conn, a_key):
    fp = signing.fingerprint(a_key)
    first = keys.live(conn, fp).signing_key_id
    keys.revoke(conn, key_fingerprint=fp, status="compromised",
                revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert keys.live(conn, fp).status == "compromised"
    history = keys.history(conn, fp)
    assert [r.status for r in history] == ["active", "compromised"]
    assert history[0].signing_key_id == first
    assert history[0].superseded_by == history[1].signing_key_id


def test_revocation_carries_the_key_material_and_holder_forward(conn, a_key):
    """The new row is the SAME key with a new status, so it must carry public_key,
    algorithm and holder forward -- exactly as withdraw_expansion_decision carries
    class_name forward. Taking them from the caller instead would let a revocation
    quietly re-attribute a key to somebody else."""
    fp = signing.fingerprint(a_key)
    keys.revoke(conn, key_fingerprint=fp, status="retired",
                revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    record = keys.live(conn, fp)
    assert record.public_key == a_key
    assert record.holder == "a curator"
    assert record.algorithm == signing.ED25519
    assert record.registered_by == "an operator"


def test_revoking_a_key_nobody_registered_raises(conn):
    """NoLiveKeyError rather than a silent no-op, on withdraw_expansion_decision's
    precedent: an operator revoking a typo'd fingerprint has been told nothing and would
    reasonably believe the key is now revoked. That is the worst possible outcome of a
    revocation command."""
    with pytest.raises(keys.NoLiveKeyError):
        keys.revoke(conn, key_fingerprint="c" * 64, status="compromised",
                    revoked_by="an operator")


def test_key_status_assembles_the_rule_from_the_vocabulary_table(conn, a_key):
    """The two booleans come from signing_key_status_kind, never from a Python mapping
    -- that is the whole reason db/030 holds them as data."""
    fp = signing.fingerprint(a_key)
    assert keys.key_status(conn, fp) == signing.KeyStatus(
        "active", is_revocation=False, invalidates_all_signatures=False,
        status_from=NOW)
    keys.revoke(conn, key_fingerprint=fp, status="compromised",
                revoked_by="op", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    revoked = keys.key_status(conn, fp)
    assert revoked.invalidates_all_signatures is True
    assert revoked.is_revocation is True
    assert revoked.status_from == LATER


def test_nothing_here_commits(conn, a_key):
    """The caller owns the transaction, as everywhere in these modules. Proved by
    rolling back and finding nothing rather than by reading the source.

    ASSERTS PRESENCE BEFORE THE ROLLBACK, not just absence after: an empty result after
    rollback is equally consistent with register() writing nothing at all, or with
    live() being broken and unable to find a row regardless of commit state. Confirming
    the key is found FIRST rules both out, so only a genuine commit-vs-rollback
    difference can produce the absence asserted below."""
    fp = signing.fingerprint(a_key)
    assert keys.live(conn, fp) is not None
    conn.rollback()
    assert keys.live(conn, fp) is None


def test_two_keys_for_one_holder_coexist(conn, a_key):
    """A rotation registers a NEW key beside the old one. `holder` is not a natural key,
    and two live keys for one person is an ordinary state during a rotation."""
    _, second = signing.generate_keypair()
    keys.register(conn, public_key=second, holder="a curator",
                  registered_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert len(keys.all_live(conn)) == 2


def test_history_is_oldest_first_and_totally_ordered(conn, a_key):
    """Matching interactions.decision_history. Totally ordered on the surrogate key
    rather than on status_from, which an operator may supply out of order."""
    fp = signing.fingerprint(a_key)
    keys.revoke(conn, key_fingerprint=fp, status="rotated", revoked_by="op",
                status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    ids = [r.signing_key_id for r in keys.history(conn, fp)]
    assert ids == sorted(ids)

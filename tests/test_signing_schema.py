# tests/test_signing_schema.py
"""db/030's floor and vocabularies (spec 5). DB-gated."""
import datetime as dt

import psycopg
import pytest

from drugref import signing

FP = "a" * 64
OTHER_FP = "b" * 64
NOW = dt.datetime(2026, 8, 9, tzinfo=dt.timezone.utc)


def _key(conn, fingerprint=FP, status="active", status_from=NOW):
    return conn.execute(
        "INSERT INTO drugref.signing_key (key_fingerprint, public_key, algorithm, "
        "holder, status, status_from, registered_by) "
        "VALUES (%s, %s, 'Ed25519', 'a curator', %s, %s, 'an operator') "
        "RETURNING signing_key_id",
        (fingerprint, b"\x01" * 32, status, status_from)).fetchone()[0]


def _signature(conn, target_id=1, kind="curated_interaction", digest=b"\x02" * 32):
    return conn.execute(
        "INSERT INTO drugref.assertion_signature (target_kind, target_id, "
        "payload_context, payload_digest, key_fingerprint, algorithm, signature, "
        "signed_at) VALUES (%s, %s, 'curated_interaction/v1', %s, %s, 'Ed25519', %s, %s)"
        " RETURNING signature_id",
        (kind, target_id, digest, FP, b"\x03" * 64, NOW)).fetchone()[0]


@pytest.mark.parametrize("status,is_revocation,invalidates", [
    ("active", False, False),
    ("rotated", True, False),
    ("retired", True, False),
    ("compromised", True, True),
])
def test_the_status_vocabulary_carries_its_rule_as_data(
        conn, status, is_revocation, invalidates):
    """The revocation rule lives in a TABLE an auditor can read, not in a Python
    if-statement -- the same shape as ci_axis.expands_descendants and
    class_expansion_policy. Asserted value by value: a seed that silently flipped
    `compromised` to non-invalidating would be the single most consequential wrong row
    in this schema, and no aggregate count would show it."""
    row = conn.execute(
        "SELECT is_revocation, invalidates_all_signatures "
        "FROM drugref.signing_key_status_kind WHERE status = %s", (status,)).fetchone()
    assert row == (is_revocation, invalidates)


def test_a_fifth_status_cannot_inherit_a_guess_about_either_boolean(conn):
    """NO DEFAULT on either column. class_expansion_policy's `allow` != absent and
    is_active_component's NULL != false are the same lesson; here the guess would decide
    whether a revocation destroys a curator's evidence."""
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute("INSERT INTO drugref.signing_key_status_kind (status, note) "
                     "VALUES ('suspended', 'x')")


def test_the_catalog_and_signing_py_agree_on_the_contexts(conn):
    """Two vocabularies that must not drift: a target kind whose context signing.py
    cannot encode is a row that makes `drugref sign` fail at the last moment, and a
    frozen field list no target kind names is dead code nothing exercises."""
    contexts = {row[0] for row in conn.execute(
        "SELECT payload_context FROM drugref.signature_target_kind").fetchall()}
    assert contexts == set(signing.FIELD_LISTS)


def test_signing_key_refuses_a_delete(conn):
    _key(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.signing_key WHERE key_fingerprint = %s", (FP,))


def test_signing_key_refuses_an_in_place_edit(conn):
    """Revocation is a CORRECTION -- insert the new status, point the old row at it --
    never an UPDATE. Editing in place would overwrite the history that makes 'was this
    key already revoked when that signature was made?' answerable at all."""
    _key(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.signing_key SET status = 'compromised' "
                     "WHERE key_fingerprint = %s", (FP,))


def test_signing_key_permits_only_superseded_by_to_change(conn):
    first = _key(conn)
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    second = _key(conn, status="compromised")
    conn.execute("UPDATE drugref.signing_key SET superseded_by = %s "
                 "WHERE signing_key_id = %s", (second, first))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT status FROM drugref.signing_key WHERE superseded_by IS NULL "
        "AND key_fingerprint = %s", (FP,)).fetchone()[0] == "compromised"


def test_two_live_rows_for_one_fingerprint_are_refused_at_commit(conn):
    """The single-live check is DEFERRED, so this fails at SET CONSTRAINTS IMMEDIATE
    rather than at the second INSERT. A test that never forces the check proves nothing
    -- Plan C's standing note -- which is why the immediate call is here."""
    _key(conn)
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    _key(conn, status="retired")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_two_live_rows_for_DIFFERENT_fingerprints_coexist(conn):
    """The control for the test above: without it, a trigger that rejected EVERY second
    row would pass that one and forbid ever registering a second key."""
    _key(conn, FP)
    _key(conn, OTHER_FP)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT count(*) FROM drugref.signing_key "
        "WHERE superseded_by IS NULL").fetchone()[0] == 2


@pytest.mark.parametrize("bad", ["ABC", "A" * 64, "a" * 63, "g" * 64, ""])
def test_a_malformed_fingerprint_is_refused(conn, bad):
    """The fingerprint is the identity a signature names, in a text column. A truncated
    or upper-case value is a row that silently matches no signature -- which looks
    exactly like a key nobody registered, and so reports UNKNOWN_KEY forever."""
    with pytest.raises(psycopg.errors.CheckViolation):
        _key(conn, fingerprint=bad)


def test_signing_key_is_discovered_as_an_eighth_single_live_table(conn):
    """DERIVED FROM THE CATALOG, not asserted as a literal eight. The gates round
    rebuilt the live-key coverage set from pg_trigger.tgargs precisely so a new table is
    guarded the day its migration lands with no list to edit. This asserts the
    derivation actually picked db/030's table up -- the property that matters."""
    from tests.test_live_key_index_guard import _single_live_tables
    tables = dict(_single_live_tables(conn))
    assert "signing_key" in tables
    assert tables["signing_key"] == "key_fingerprint"


def test_a_signature_cannot_be_deleted(conn):
    _signature(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.assertion_signature")


@pytest.mark.parametrize("column,value", [
    ("signed_at", NOW), ("key_fingerprint", OTHER_FP), ("target_id", 99),
    ("target_kind", "curated_condition"), ("payload_context", "curated_condition/v1"),
    ("payload_digest", b"\x09" * 32), ("signature", b"\x09" * 64),
    ("algorithm", "Ed25519"), ("recorded_at", NOW),
])
def test_no_column_of_a_signature_can_be_updated(conn, column, value):
    """STRICTER THAN forbid_overlay_rewrite, which exists to permit exactly one column
    to change. A signature has no superseded_by and needs none: a curator who mis-signed
    corrects the JUDGEMENT (a new curated row), and a key whose signatures must all be
    repudiated is handled at the KEY layer by `compromised`. A signature is a historical
    fact about a moment, not an assertion that can be revised.

    ONE TEST PER COLUMN rather than one for the table: a trigger comparing a SUBSET of
    columns is exactly the defect a single-column check would pass."""
    _signature(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(f"UPDATE drugref.assertion_signature SET {column} = %s", (value,))


def test_a_signature_of_the_wrong_length_is_refused(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.assertion_signature (target_kind, target_id, "
            "payload_context, payload_digest, key_fingerprint, algorithm, signature, "
            "signed_at) VALUES ('curated_interaction', 1, 'curated_interaction/v1', "
            "%s, %s, 'Ed25519', %s, %s)", (b"\x02" * 32, FP, b"\x03" * 10, NOW))


def test_an_unknown_target_kind_is_refused_by_the_foreign_key(conn):
    """An FK into signature_target_kind rather than a CHECK, for db/006's reason: the
    mapping from a kind to its table, key column and context has one home, so a fourth
    kind is one INSERT there rather than an edit in three places."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _signature(conn, kind="something_else")


def test_the_same_key_cannot_record_one_identical_attestation_twice(conn):
    _signature(conn)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _signature(conn)


def test_the_same_key_may_re_sign_the_same_target_at_a_later_moment(conn):
    """The control for the dedupe guard: a later signed_at yields a different payload
    and therefore a different digest, so a second row is legitimate and both are true.
    A uniqueness constraint on (kind, id, key) alone would forbid it."""
    _signature(conn, digest=b"\x02" * 32)
    _signature(conn, digest=b"\x05" * 32)
    assert conn.execute(
        "SELECT count(*) FROM drugref.assertion_signature").fetchone()[0] == 2


def test_a_manifest_is_insert_only(conn):
    conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.09', %s, 0, '[]'::jsonb, 'an operator', %s)",
        (b"\x04" * 32, NOW))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.release_manifest SET row_count = 1")


def test_a_manifest_cannot_be_deleted(conn):
    conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.10', %s, 0, '[]'::jsonb, 'an operator', %s)",
        (b"\x04" * 32, NOW))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.release_manifest")


def test_a_release_tag_cannot_be_reused(conn):
    conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.11', %s, 0, '[]'::jsonb, 'op', %s)", (b"\x04" * 32, NOW))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
            "row_count, upstream_releases, published_by, published_at) "
            "VALUES ('2026.08.11', %s, 0, '[]'::jsonb, 'op', %s)", (b"\x04" * 32, NOW))

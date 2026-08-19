"""Schema guards required before the reviewer GUI can administer key trust."""

import pytest
from psycopg import errors


def test_signing_status_vocabulary_is_append_only(conn):
    """One ordinary UPDATE must not disarm every historical compromise verdict."""
    with pytest.raises(errors.RaiseException, match="insert-only"):
        conn.execute(
            "UPDATE drugref.signing_key_status_kind "
            "SET invalidates_all_signatures = false WHERE status = 'compromised'"
        )


def test_signing_status_vocabulary_can_grow_by_insert(conn):
    """The floor preserves the designed additive path for a future fifth status."""
    conn.execute(
        "INSERT INTO drugref.signing_key_status_kind "
        "(status, is_revocation, invalidates_all_signatures, note) "
        "VALUES ('test_future_status', true, false, 'transaction-local test status')"
    )
    assert conn.execute(
        "SELECT is_revocation FROM drugref.signing_key_status_kind "
        "WHERE status = 'test_future_status'"
    ).fetchone() == (True,)

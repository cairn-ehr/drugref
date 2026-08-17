"""db/044 reviewer-account and session integrity floor. DB-gated."""

import datetime as dt
import uuid

import psycopg
import pytest


NOW = dt.datetime(2026, 8, 17, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2026, 8, 18, tzinfo=dt.timezone.utc)
PHC = "$argon2id$v=19$m=19456,t=2,p=1$c2FsdHNhbHQ$YWFhYWFhYWFhYWFhYWFhYQ"


def _account(conn, username="maya.chen", created_by=None):
    reviewer_uuid = uuid.uuid4()
    conn.execute(
        "INSERT INTO drugref.reviewer_account "
        "(reviewer_uuid, username, created_at, created_by) VALUES (%s, %s, %s, %s)",
        (reviewer_uuid, username, NOW, created_by),
    )
    return reviewer_uuid


def _profile(conn, reviewer_uuid, *, role="reviewer", active=True, recorded_by=None):
    return conn.execute(
        "INSERT INTO drugref.reviewer_profile "
        "(reviewer_uuid, full_name, qualifications, bio_markdown, role, active, "
        "recorded_at, recorded_by) VALUES (%s, 'Maya Chen', 'MD', 'Bio', %s, %s, "
        "%s, %s) RETURNING reviewer_profile_id",
        (reviewer_uuid, role, active, NOW, recorded_by or reviewer_uuid),
    ).fetchone()[0]


def _credential(conn, reviewer_uuid, *, recorded_by=None):
    return conn.execute(
        "INSERT INTO drugref.reviewer_password_credential "
        "(reviewer_uuid, password_hash, recorded_at, recorded_by) "
        "VALUES (%s, %s, %s, %s) RETURNING credential_id",
        (reviewer_uuid, PHC, NOW, recorded_by or reviewer_uuid),
    ).fetchone()[0]


def test_accounts_start_empty_so_bootstrap_is_a_real_database_state(conn):
    assert conn.execute(
        "SELECT count(*) FROM drugref.reviewer_profile "
        "WHERE role = 'administrator' AND superseded_by IS NULL"
    ).fetchone()[0] == 0


def test_username_is_lowercase_unique_and_account_rows_are_immutable(conn):
    reviewer_uuid = _account(conn)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _account(conn, "maya.chen")
    conn.rollback()

    reviewer_uuid = _account(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(
            "UPDATE drugref.reviewer_account SET username = 'other' "
            "WHERE reviewer_uuid = %s",
            (reviewer_uuid,),
        )


@pytest.mark.parametrize("username", ["MC", "Maya.Chen", "-maya", "maya chen", "a" * 65])
def test_malformed_usernames_are_refused(conn, username):
    with pytest.raises(psycopg.errors.CheckViolation):
        _account(conn, username)


def test_profile_correction_is_append_then_supersede(conn):
    reviewer_uuid = _account(conn)
    first = _profile(conn, reviewer_uuid)
    second = conn.execute(
        "INSERT INTO drugref.reviewer_profile "
        "(reviewer_uuid, full_name, qualifications, bio_markdown, role, active, "
        "recorded_at, recorded_by) VALUES (%s, 'Maya Chen', 'MD, PhD', 'New bio', "
        "'administrator', true, %s, %s) RETURNING reviewer_profile_id",
        (reviewer_uuid, LATER, reviewer_uuid),
    ).fetchone()[0]
    conn.execute(
        "UPDATE drugref.reviewer_profile SET superseded_by = %s "
        "WHERE reviewer_profile_id = %s",
        (second, first),
    )
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT role FROM drugref.reviewer_profile "
        "WHERE reviewer_uuid = %s AND superseded_by IS NULL",
        (reviewer_uuid,),
    ).fetchone()[0] == "administrator"


def test_two_live_profiles_for_one_account_are_refused_at_commit(conn):
    reviewer_uuid = _account(conn)
    _profile(conn, reviewer_uuid)
    _profile(conn, reviewer_uuid, role="administrator")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_password_history_accepts_only_argon2id_and_one_live_credential(conn):
    reviewer_uuid = _account(conn)
    first = _credential(conn, reviewer_uuid)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.reviewer_password_credential "
            "(reviewer_uuid, password_hash, recorded_by) VALUES (%s, 'plaintext', %s)",
            (reviewer_uuid, reviewer_uuid),
        )
    conn.rollback()

    reviewer_uuid = _account(conn)
    first = _credential(conn, reviewer_uuid)
    second = _credential(conn, reviewer_uuid)
    conn.execute(
        "UPDATE drugref.reviewer_password_credential SET superseded_by = %s "
        "WHERE credential_id = %s",
        (second, first),
    )
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_password_hash_cannot_be_changed_or_deleted(conn):
    reviewer_uuid = _account(conn)
    credential_id = _credential(conn, reviewer_uuid)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(
            "UPDATE drugref.reviewer_password_credential SET password_hash = %s "
            "WHERE credential_id = %s",
            (PHC + "x", credential_id),
        )


def test_key_enrolment_points_at_the_existing_signing_registry(conn):
    reviewer_uuid = _account(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO drugref.reviewer_key_enrolment "
            "(reviewer_uuid, signing_key_id, enrolled, enrolled_by) "
            "VALUES (%s, 999999, true, %s)",
            (reviewer_uuid, reviewer_uuid),
        )


def test_key_enrolment_can_be_withdrawn_without_erasing_history(conn):
    reviewer_uuid = _account(conn)
    signing_key_id = conn.execute(
        "INSERT INTO drugref.signing_key "
        "(key_fingerprint, public_key, algorithm, holder, status, status_from, "
        "registered_by) VALUES (%s, %s, 'Ed25519', 'Maya Chen', 'active', %s, "
        "'test') RETURNING signing_key_id",
        ("a" * 64, b"x" * 32, NOW),
    ).fetchone()[0]
    first = conn.execute(
        "INSERT INTO drugref.reviewer_key_enrolment "
        "(reviewer_uuid, signing_key_id, enrolled, enrolled_by) "
        "VALUES (%s, %s, true, %s) RETURNING reviewer_key_enrolment_id",
        (reviewer_uuid, signing_key_id, reviewer_uuid),
    ).fetchone()[0]
    second = conn.execute(
        "INSERT INTO drugref.reviewer_key_enrolment "
        "(reviewer_uuid, signing_key_id, enrolled, enrolled_by) "
        "VALUES (%s, %s, false, %s) RETURNING reviewer_key_enrolment_id",
        (reviewer_uuid, signing_key_id, reviewer_uuid),
    ).fetchone()[0]
    conn.execute(
        "UPDATE drugref.reviewer_key_enrolment SET superseded_by = %s "
        "WHERE reviewer_key_enrolment_id = %s",
        (second, first),
    )
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT enrolled FROM drugref.reviewer_key_enrolment "
        "WHERE signing_key_id = %s AND superseded_by IS NULL",
        (signing_key_id,),
    ).fetchone()[0] is False


def test_sessions_store_fixed_length_digests_and_revocation_is_insert_only(conn):
    reviewer_uuid = _account(conn)
    session_uuid = uuid.uuid4()
    conn.execute(
        "INSERT INTO drugref.auth_session "
        "(session_uuid, reviewer_uuid, token_digest, issued_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (session_uuid, reviewer_uuid, b"x" * 32, NOW, LATER),
    )
    conn.execute(
        "INSERT INTO drugref.auth_session_revocation "
        "(session_uuid, revoked_at, revoked_by, reason) VALUES (%s, %s, %s, 'logout')",
        (session_uuid, LATER, reviewer_uuid),
    )
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(
            "UPDATE drugref.auth_session_revocation SET reason = 'administrative' "
            "WHERE session_uuid = %s",
            (session_uuid,),
        )


def test_session_expiry_must_follow_issue_time(conn):
    reviewer_uuid = _account(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.auth_session "
            "(session_uuid, reviewer_uuid, token_digest, issued_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (uuid.uuid4(), reviewer_uuid, b"x" * 32, LATER, NOW),
        )


def test_live_profile_and_credential_indexes_match_the_deferred_guards(
    conn, assert_live_key_index
):
    assert_live_key_index(
        "reviewer_profile_live_key", "reviewer_profile", "reviewer_uuid"
    )
    assert_live_key_index(
        "reviewer_password_credential_live_key",
        "reviewer_password_credential",
        "reviewer_uuid",
    )
    assert_live_key_index(
        "reviewer_key_enrolment_live_key", "reviewer_key_enrolment", "signing_key_id"
    )

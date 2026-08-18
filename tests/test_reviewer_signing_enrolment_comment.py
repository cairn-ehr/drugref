"""db/046 pins the signing registry's current enrolment trust statement."""


def _comment_defects(comment):
    """Return every way the catalog can regress to db/030's pre-GUI contract."""
    if not comment:
        return ["no COMMENT ON TABLE at all"]
    defects = []
    if "authenticated, active reviewer" not in comment:
        defects.append("does not name authenticated active reviewer enrolment")
    if "account-session possession and local-private-key possession" not in comment:
        defects.append("does not separate session and private-key possession")
    if "no enrolment protocol" in comment.lower():
        defects.append("still claims there is no enrolment protocol")
    return defects


def _signing_key_comment(conn):
    """Read the live catalog description shown by ``\\d+ signing_key``."""
    return conn.execute(
        "SELECT obj_description('drugref.signing_key'::regclass, 'pg_class')"
    ).fetchone()[0]


def test_a_missing_signing_key_comment_is_a_defect():
    """Cover the absence branch without requiring a database mutation."""
    assert _comment_defects(None) == ["no COMMENT ON TABLE at all"]


def test_the_catalog_states_the_reviewer_enrolment_trust_root(conn):
    """Pin the complete new obligations against PostgreSQL, not migration text."""
    assert _comment_defects(_signing_key_comment(conn)) == []


def test_the_guard_rejects_db030s_pre_enrolment_statement(conn):
    """Prove the predicate fails on the exact obsolete trust-root claim."""
    conn.execute(
        "COMMENT ON TABLE drugref.signing_key IS "
        "'There is no enrolment protocol and no trust root beyond an operator.'"
    )
    assert _comment_defects(_signing_key_comment(conn)) == [
        "does not name authenticated active reviewer enrolment",
        "does not separate session and private-key possession",
        "still claims there is no enrolment protocol",
    ]

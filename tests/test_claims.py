# tests/test_claims.py
import uuid  # noqa: F401  (used by the existing tests below)
from drugref import claims

M = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _new_run(conn):
    return conn.execute(
        "INSERT INTO drugref.ingest_run (source, upstream_release, source_checksum) "
        "VALUES ('UNII','r1','x') RETURNING ingest_run_id").fetchone()[0]


def test_upsert_moiety_then_add_claims(conn):
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "amlodipine", run)
    claims.add_claim(conn, M, "UNII", "1J444QC288", run)
    claims.add_claim(conn, M, "INN", "amlodipine", run)
    rows = conn.execute(
        "SELECT scheme, value FROM drugref.identity_claim WHERE moiety_uuid = %s ORDER BY scheme",
        (M,)).fetchall()
    assert rows == [("INN", "amlodipine"), ("UNII", "1J444QC288")]


def test_add_claim_is_idempotent(conn):
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "amlodipine", run)
    claims.add_claim(conn, M, "UNII", "1J444QC288", run)
    claims.add_claim(conn, M, "UNII", "1J444QC288", run)  # duplicate -> no-op
    n = conn.execute("SELECT count(*) FROM drugref.identity_claim WHERE moiety_uuid = %s", (M,)).fetchone()[0]
    assert n == 1


def test_add_claim_reports_insert_vs_conflict(conn):
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "amlodipine", run)
    # First assertion inserts a new row -> True; re-asserting the same claim is
    # the ON CONFLICT no-op -> False. Callers rely on this to count new claims.
    assert claims.add_claim(conn, M, "UNII", "1J444QC288", run) is True
    assert claims.add_claim(conn, M, "UNII", "1J444QC288", run) is False


def test_case_ambiguous_claim_values_are_stored_canonically(conn):
    """The moiety UUID is minted from `unii.strip().upper()`, but the claim value
    was stored only stripped -- so the identifier the UUID derives from could sit
    in the table under a spelling no lookup would match.

    Two consequences, both silent: the same UNII arriving in two cases inserts TWO
    claims for one code, and moieties_by_scheme (an exact-match index) buckets them
    separately, so MeSH's UNII bridge matches one and misses the other.
    """
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "acetaminophen", run)
    assert claims.add_claim(conn, M, "UNII", "362o9itl9d", run) is True
    assert claims.add_claim(conn, M, "UNII", "362O9ITL9D", run) is False   # same code
    values = [r[0] for r in conn.execute(
        "SELECT value FROM drugref.identity_claim "
        "WHERE moiety_uuid = %s AND scheme = 'UNII'", (M,)).fetchall()]
    assert values == ["362O9ITL9D"]


def test_display_name_claims_are_not_case_folded(conn):
    """INN is a display label, not a code -- folding it to upper case would corrupt
    the very thing substance_moiety.display_name caches."""
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "paracetamol", run)
    claims.add_claim(conn, M, "INN", "paracetamol", run)
    assert conn.execute(
        "SELECT value FROM drugref.identity_claim "
        "WHERE moiety_uuid = %s AND scheme = 'INN'", (M,)).fetchone()[0] == "paracetamol"


def test_a_superseded_value_can_be_reasserted_by_a_later_release(conn):
    """Upstream corrections are not always permanent -- a release may revert one.

    The uniqueness that makes re-ingest idempotent must therefore cover only LIVE
    claims. Covering superseded rows too made supersession a one-way trapdoor:
    re-asserting the reverted value hit the index, ON CONFLICT DO NOTHING swallowed
    it (returning False, indistinguishable from a normal no-op), and the claim
    stayed invisible to every `superseded_by IS NULL` join forever.
    """
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "acetaminophen", run)
    assert claims.add_claim(conn, M, "RXNORM_IN", "161", run) is True
    # Scoped to THIS moiety: other test modules commit their own claims (their
    # orchestrators commit internally), so an unscoped lookup by value alone
    # would occasionally pick up someone else's row.
    old = conn.execute("SELECT identity_claim_id FROM drugref.identity_claim "
                       "WHERE moiety_uuid = %s AND value = '161'", (M,)).fetchone()[0]
    # A correction arrives: RxCUI 161 -> 999999.
    claims.add_claim(conn, M, "RXNORM_IN", "999999", run)
    new = conn.execute("SELECT identity_claim_id FROM drugref.identity_claim "
                       "WHERE moiety_uuid = %s AND value = '999999'", (M,)).fetchone()[0]
    conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s "
                 "WHERE identity_claim_id = %s", (new, old))
    # The next release reverts it. That must land as a live claim again.
    assert claims.add_claim(conn, M, "RXNORM_IN", "161", run) is True
    live = {r[0] for r in conn.execute(
        "SELECT value FROM drugref.identity_claim "
        "WHERE moiety_uuid = %s AND scheme = 'RXNORM_IN' AND superseded_by IS NULL",
        (M,)).fetchall()}
    assert live == {"161", "999999"}


def test_upsert_moiety_refreshes_display_name(conn):
    run = _new_run(conn)
    claims.upsert_moiety(conn, M, "acetaminophen", run)
    claims.upsert_moiety(conn, M, "paracetamol", run)   # display cache may refresh
    name = conn.execute("SELECT display_name FROM drugref.substance_moiety WHERE moiety_uuid = %s", (M,)).fetchone()[0]
    assert name == "paracetamol"

# tests/test_schema_floor.py
"""The append-only floor must reject rewrites even from raw SQL."""
import psycopg
import pytest


def _seed_one(conn):
    run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII','r1','x','unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa','amlodipine', %s)", (run,))
    cid = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa','UNII','ABC123', %s) "
        "RETURNING identity_claim_id", (run,)).fetchone()[0]
    return run, cid


def test_moiety_delete_forbidden(conn):
    _seed_one(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.substance_moiety")


def test_moiety_uuid_immutable(conn):
    _seed_one(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.substance_moiety "
                     "SET moiety_uuid = '00000000-0000-0000-0000-0000000000bb'")


def test_claim_delete_forbidden(conn):
    _seed_one(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.identity_claim")


def test_claim_value_immutable_but_supersede_allowed(conn):
    run, cid = _seed_one(conn)
    # Changing value in place is forbidden...
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.identity_claim SET value = 'XYZ' WHERE identity_claim_id = %s", (cid,))
    conn.rollback()
    run, cid = _seed_one(conn)
    # ...but the real overlay path is permitted: insert a SECOND claim (the
    # correction) and point the FIRST claim's superseded_by at it. A claim
    # superseding itself is not a valid overlay (see identity_claim_no_self_supersede)
    # -- correction is always insert-new-then-point-old-at-new.
    new_cid = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa','UNII','ABC123-CORRECTED', %s) "
        "RETURNING identity_claim_id", (run,)).fetchone()[0]
    conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s WHERE identity_claim_id = %s",
                 (new_cid, cid))


def test_claim_cannot_supersede_itself(conn):
    """Guarded twice over: db/001's identity_claim_no_self_supersede CHECK, and
    db/005's stricter 'must reference a LATER claim' rule in the floor trigger.
    The trigger is BEFORE ROW so it fires first -- either refusal is correct, the
    point is that the database will not store it."""
    run, cid = _seed_one(conn)
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.RaiseException)):
        conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s WHERE identity_claim_id = %s",
                     (cid, cid))


# ---- supersession is a ONE-WAY overlay, not a free-form pointer --------------
#
# `superseded_by` is the only mutable thing in the whole identity spine, so it is
# the only place the append-only floor can be subverted without an INSERT. Left
# unconstrained it permits three states that are not corrections at all:
# un-superseding (resurrecting a retired identifier), re-pointing, and pointing
# at another moiety's claim -- and a two-claim cycle makes BOTH identifiers
# invisible to every `superseded_by IS NULL` join at once.


def _supersede(conn, run, cid, value="ABC123-CORRECTED"):
    """Insert the correction claim and point `cid` at it. Returns the new id."""
    new_cid = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa','UNII',%s, %s) "
        "RETURNING identity_claim_id", (value, run)).fetchone()[0]
    conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s "
                 "WHERE identity_claim_id = %s", (new_cid, cid))
    return new_cid


def test_supersession_cannot_be_undone(conn):
    run, cid = _seed_one(conn)
    _supersede(conn, run, cid)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.identity_claim SET superseded_by = NULL "
                     "WHERE identity_claim_id = %s", (cid,))


def test_supersession_cannot_be_repointed(conn):
    run, cid = _seed_one(conn)
    _supersede(conn, run, cid)
    other = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa','UNII','ABC123-THIRD', %s) "
        "RETURNING identity_claim_id", (run,)).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s "
                     "WHERE identity_claim_id = %s", (other, cid))


def test_a_claim_cannot_be_superseded_by_another_moietys_claim(conn):
    run, cid = _seed_one(conn)
    conn.execute(
        "INSERT INTO drugref.substance_moiety (moiety_uuid, display_name, first_seen_ingest) "
        "VALUES ('00000000-0000-0000-0000-0000000000bb','other drug', %s)", (run,))
    foreign = conn.execute(
        "INSERT INTO drugref.identity_claim (moiety_uuid, scheme, value, ingest_run) "
        "VALUES ('00000000-0000-0000-0000-0000000000bb','UNII','ZZZ999', %s) "
        "RETURNING identity_claim_id", (run,)).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.identity_claim SET superseded_by = %s "
                     "WHERE identity_claim_id = %s", (foreign, cid))


def test_first_seen_ingest_is_immutable(conn):
    """Write-once provenance, guarded the way identity_claim.ingest_run already is:
    'when did drugref first register this moiety' is not a value a later fix-up
    script should be able to silently recompute."""
    run, _ = _seed_one(conn)
    later = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII','r2','y','unii_run') RETURNING ingest_run_id").fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.substance_moiety SET first_seen_ingest = %s "
                     "WHERE moiety_uuid = '00000000-0000-0000-0000-0000000000aa'", (later,))

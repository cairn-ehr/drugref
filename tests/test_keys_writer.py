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
    public = signing.generate_keypair().public_key
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


def test_a_compromise_is_not_undone_by_a_later_status_change(conn, a_key):
    """A BLANKET REVOCATION IS PERMANENT, and this is the test that says so.

    `revoke` writes whatever status it is handed, so `--status active` on a compromised
    key is one ordinary command -- no raw SQL, no superuser, no dropped trigger. Before
    this, `key_status` read the LIVE row alone, so that command silently returned every
    signature the key ever made to `valid`, INCLUDING the attacker's. That is the one
    outcome blanket revocation exists to prevent: after a compromise there is no way to
    tell the holder's signatures from the thief's, and a status change cannot create
    that knowledge retrospectively.

    db/030 section 3 justifies the whole insert-then-supersede shape on the grounds that
    a key's status HISTORY is readable -- "the only thing that makes 'was this key
    already revoked when that signature was made?' answerable". Nothing read it until
    this fix; the history was written and never consulted.
    """
    fp = signing.fingerprint(a_key)
    keys.revoke(conn, key_fingerprint=fp, status="compromised",
                revoked_by="an operator", status_from=LATER)
    keys.revoke(conn, key_fingerprint=fp, status="active",
                revoked_by="an attacker", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    assert keys.live(conn, fp).status == "active"      # the live row really did move
    status = keys.key_status(conn, fp)
    assert status.invalidates_all_signatures is True   # ...and the verdict did not
    assert status.status == "compromised"


def test_a_compromise_is_not_downgraded_to_a_time_scoped_revocation(conn, a_key):
    """The quieter half of the same defect. `--status rotated` on a compromised key
    turned a BLANKET revocation into a TIME-SCOPED one, so every signature the attacker
    backdated before `status_from` verified again -- and `signed_at` is chosen by
    whoever holds the private key, which after a compromise is the attacker."""
    fp = signing.fingerprint(a_key)
    keys.revoke(conn, key_fingerprint=fp, status="compromised",
                revoked_by="an operator", status_from=LATER)
    keys.revoke(conn, key_fingerprint=fp, status="rotated",
                revoked_by="an attacker", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    assert keys.key_status(conn, fp).invalidates_all_signatures is True


def test_a_time_scoped_revocation_IS_still_reversible(conn, a_key):
    """THE ANTI-VACUITY CONTROL, and the reason the fix reads the vocabulary table
    rather than freezing a status name: only a status carrying
    `invalidates_all_signatures` is permanent. A key rotated onto a new laptop and then
    legitimately reinstated must go back to `active` -- a mistaken revocation has to be
    correctable on an append-only floor, which is exactly what supersession is for.

    Without this test the two above would pass on a `key_status` that simply refused
    every reinstatement."""
    fp = signing.fingerprint(a_key)
    keys.revoke(conn, key_fingerprint=fp, status="rotated",
                revoked_by="an operator", status_from=LATER)
    keys.revoke(conn, key_fingerprint=fp, status="active",
                revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    status = keys.key_status(conn, fp)
    assert status.status == "active"
    assert status.invalidates_all_signatures is False
    assert status.is_revocation is False


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
    second = signing.generate_keypair().public_key
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


# ============================================================================
# for_verification -- the two reads a verifier needs, taken together (issue 87)
# ============================================================================


class _CountingConn:
    """A connection that counts `execute` calls and proxies THAT ONE METHOD.

    Deliberately not a general delegate -- there is no `__getattr__` here, so any other
    attribute access raises AttributeError rather than silently reaching the real
    connection. That is the safer default for a counter: a function that reached the
    database by some route other than `.execute()` would fail loudly instead of being
    reported as costing zero queries. (An earlier version of this line said "delegates
    everything else", which described a class this is not.)

    The round-trip claim in issue 87 is the kind that is easy to assert in prose and
    never check, so it is checked. Deliberately NOT a mock: every query runs against the
    real database, so this can only ever measure the real code path.
    """

    def __init__(self, conn):
        self._conn = conn
        self.queries = 0

    def execute(self, *args, **kwargs):
        self.queries += 1
        return self._conn.execute(*args, **kwargs)


def test_for_verification_reads_the_registry_once(conn, a_key):
    """ISSUE 87: one query where there were two.

    `verify_target` is called once per curated row across the WHOLE overlay by the
    release verifier, and its own docstring already records hoisting the catalog lookup
    and the target-row read out of the per-signature loop for exactly this reason. The
    two registry reads were the pair left behind.

    THE CONTROL IS THE OLD PAIR, measured in the same test rather than quoted from the
    issue: two calls cost two queries, so `1` here is a halving that is observed rather
    than asserted. Without the control this test would pass just as well if `execute`
    had stopped being the way this module reaches the database.
    """
    fp = signing.fingerprint(a_key)

    old = _CountingConn(conn)
    keys.live(old, fp)
    keys.key_status(old, fp)
    assert old.queries == 2

    new = _CountingConn(conn)
    assert keys.for_verification(new, fp) is not None
    assert new.queries == 1


def test_for_verification_is_all_or_nothing(conn, a_key):
    """The two halves are present together or absent together, and that is the point.

    `SignatureVerdict`'s docstring states that `holder is None` EXACTLY when the verdict
    is `UNKNOWN_KEY`. Before this that held only because two separately-issued queries
    happened to run compatible predicates -- nothing enforced it, and a future edit to
    either predicate could have produced a verdict with a holder and no status, or a
    status and no holder, with no test anywhere to notice.

    One row returning both makes the invariant structural: there is a single `None` to
    check, so the two halves cannot disagree about whether the key exists.
    """
    assert keys.for_verification(conn, "0" * 64) is None

    registered = keys.for_verification(conn, signing.fingerprint(a_key))
    assert registered is not None
    assert registered.record.holder == "a curator"
    assert registered.status.status == "active"


def test_for_verification_takes_MATERIAL_from_the_live_row_and_STATUS_from_history(
        conn, a_key):
    """THE DIFFERENCE THE MERGE HAD TO PRESERVE, and the one way it could go wrong.

    The two queries this replaces are not the same query twice. `live` reads the LIVE
    row; `key_status` reads the key's WHOLE HISTORY, because a blanket revocation is
    PERMANENT -- `revoke` writes whatever status it is handed, so `--status active` on a
    compromised key is one ordinary command, and reading the live row for the status
    would silently return every signature that key ever made to `valid`, the attacker's
    included. Issue 87 names this explicitly as the thing any merge must not flatten.

    A merge written as one plain row read would take BOTH halves from the live row and
    pass every other test in this file, because every other test asks the two functions
    separately. This is the case that fails.
    """
    fp = signing.fingerprint(a_key)
    keys.revoke(conn, key_fingerprint=fp, status="compromised",
                revoked_by="an operator", status_from=LATER)
    keys.revoke(conn, key_fingerprint=fp, status="active",
                revoked_by="an attacker", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    registered = keys.for_verification(conn, fp)
    assert registered.record.status == "active"           # material: the LIVE row
    assert registered.status.status == "compromised"      # verdict: the HISTORY
    assert registered.status.invalidates_all_signatures is True


@pytest.mark.parametrize("revocations", [
    [],
    [("compromised", "an operator")],
    [("compromised", "an operator"), ("active", "an attacker")],
    [("rotated", "an operator"), ("active", "an operator")],
    [("retired", "an operator")],
])
def test_for_verification_agrees_with_the_two_reads_it_replaces(conn, a_key,
                                                                revocations):
    """EQUIVALENCE, over every history shape the tests above establish as meaningful.

    A simplification's only real obligation is that it changed nothing, and this project
    has twice shipped a consolidation whose survivor was pinned by nothing -- a verdict
    precedence that could be REVERSED with 177 tests green, and a collapsing function
    replaceable by `verdicts[0]`. So the new read is driven against BOTH old reads on the
    same connection, across the reinstated compromise, the reversible rotation and the
    ordinary cases either side of them.

    `keys.live` and `keys.key_status` are deliberately kept rather than deleted: `revoke`
    and `cli_signing_release` both need the live row alone, and a caller wanting only the
    status rule should not have to take the key material with it.
    """
    fp = signing.fingerprint(a_key)
    for status, by in revocations:
        keys.revoke(conn, key_fingerprint=fp, status=status, revoked_by=by,
                    status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    registered = keys.for_verification(conn, fp)
    assert registered.record == keys.live(conn, fp)
    assert registered.status == keys.key_status(conn, fp)

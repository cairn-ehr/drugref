# tests/test_cli_signing.py
"""`drugref keys` / `sign` / `verify` / `publish` -- the whole signing operator
surface (slice 5c.4, db/030). DB-gated.

MODELLED ON tests/test_cli_policy.py, DELIBERATELY -- same reason cli_signing.py
is modelled on cli_policy.py. Two things about THIS file's shape diverge from
that precedent, and both are explained rather than silent.

WHY THIS FILE NEVER COMMITS FOR REAL, UNLIKE test_cli_policy.py's `committed`
fixture. An earlier draft of this file DID drive every write command through
`cli.main([...])`, exactly as test_cli_policy.py does -- and it broke fourteen
tests in FIVE OTHER FILES the moment it ran (measured: `test_signing_schema.py`,
`test_keys_writer.py`, `test_curation_writer.py`, `test_curated_overlay.py`,
`test_curated_gap_views.py`, `test_curation_orphans.py`,
`test_signature_read_path.py`, `test_releases.py`). Those tests assert BLANKET,
UNFILTERED counts against `signing_key`/`assertion_signature`/
`class_contraindication`/`curated_interaction` -- e.g.
`test_signing_schema.py`'s `SELECT count(*) FROM drugref.signing_key WHERE
superseded_by IS NULL`, asserted `== 2` -- on the standing assumption that
NOTHING ELSE in the same pytest session ever writes a REAL row to those tables.
That assumption held for every file written before this one, because no CLI
command that commits to a curated or signing table existed yet; `cli_signing.py`
is the first, and cli_policy.py's own `committed`/real-commit shape does not
generalise here.

The floor makes the obvious fix (delete the extra rows in a teardown) impossible
-- `signing_key`/`assertion_signature`/`class_contraindication`/
`curated_interaction`/`release_manifest` are all append-only. `_NoCommit` below
is the fix that IS available: it wraps the ordinary, rollback-per-test `conn`
fixture so a handler's own `conn.commit()`/`conn.rollback()` calls -- required,
correct production behaviour, "the CLI is the caller and the caller owns the
transaction" -- operate on a SQL SAVEPOINT instead of the real transaction.
Every write below therefore still runs the REAL commit-calling code path, is
still visible to a later read WITHIN the same test (ordinary same-transaction
MVCC visibility), and is still undone completely at teardown by conftest's
`conn` fixture -- it simply never reaches disk, so no other file's blanket count
can ever see it. See `_NoCommit`'s own docstring for why a plain "swallow every
commit() call" wrapper is not enough on its own (it would also swallow the
transaction BOUNDARY a later command's rollback needs to stop at).

WHY THIS FILE STILL CALLS THE REAL `cli.main` (`_run`), NOT A HANDLER
DIRECTLY. Once nothing may commit for real, `cli.main`'s own connection --
opened fresh via `db.connect(args.dsn)` from `$DRUGREF_DSN` -- is no longer
usable as-is: it would be a SEPARATE connection from the wrapped `conn` this
file's fixtures build a test's rows on, so it would see none of them. The
`wconn` fixture below monkeypatches `cli.db.connect` to hand back the wrapped
connection instead, so `_run` can call `cli.main(argv)` UNCHANGED -- real
argument parsing, real dispatch, AND `main`'s own try/except around the
RuntimeError family, all still exercised. An earlier draft called
`args.handler(wconn, args)` directly, skipping `main` entirely, and a review
round measured the cost: `keys.NoLiveKeyError` (and every other RuntimeError
this module's handlers raise) is caught in `main`, not in `cli_signing.py`,
so that draft could not tell "handler returns exit 2" from "handler raises
and nothing catches it" for any of them.

WHY `_NoCommit.commit()` ALSO NEEDS `SET CONSTRAINTS ALL IMMEDIATE`, NOT JUST
`RELEASE SAVEPOINT` -- a second review-round finding, on the first one's
heels. `signing_key`'s single-live check is one of NINE `DEFERRABLE INITIALLY
DEFERRED` triggers in this schema, meaning it fires at a REAL `COMMIT`, not
at `RELEASE SAVEPOINT` -- so the harness, left as `RELEASE SAVEPOINT` alone,
silently disarmed all nine for every test in this file, which is a semantic
difference from a real commit, not merely a durability one. `_NoCommit.
commit()` now forces the pending deferred checks to run (and raise, if one
fails) with `SET CONSTRAINTS ALL IMMEDIATE` before releasing the savepoint,
then restores DEFERRED mode afterwards so the rest of a test's writes behave
under the same deferred semantics a fresh transaction starts in.
`test_keys_register_a_second_time_for_the_same_key_is_reported_cleanly`
below is the test that could not have caught the bug this fixes without it.
"""
import argparse
import datetime as dt

import pytest

from drugref import (cli, cli_signing, cli_signing_release, curation, db, keys,
                     release_verification, releases, signatures, signing)


class _NoCommit:
    """Wraps a real, DB-gated `conn` so a handler's own conn.commit()/
    conn.rollback() operate on a SQL SAVEPOINT rather than the real
    transaction. See the module docstring's second paragraph for WHY this
    file needs it at all.

    commit() RELEASES the savepoint and immediately opens A NEW ONE under the
    same name -- not a bare no-op -- because a bare no-op would leave a LATER
    command's rollback() (`_write`'s own error-recovery path, on a
    subsequent failed command in the same test) rolling back to the state
    BEFORE THE FIRST command in the test, undoing work a real COMMIT would
    have made permanent before that later command ever ran. Releasing and
    re-opening under the same name is what makes each commit() call a fresh
    boundary, exactly as a real COMMIT starting a new transaction would be,
    without ever letting a byte reach disk.

    `SET CONSTRAINTS ALL IMMEDIATE` RUNS FIRST, and this is the part a
    review round found missing: nine triggers in this schema are
    `DEFERRABLE INITIALLY DEFERRED` (`signing_key`'s single-live check among
    them), meaning postgres checks them at a REAL `COMMIT`, never at
    `RELEASE SAVEPOINT` -- so `RELEASE SAVEPOINT` alone silently disarms
    every one of the nine for the whole test, which is a SEMANTIC gap, not a
    durability one: a write this harness accepts could be one a real `drugref
    keys register` would reject at commit. Forcing an immediate check here,
    then releasing, is what makes `commit()` raise in exactly the cases a
    real commit would. `SET CONSTRAINTS ALL DEFERRED` restores deferred mode
    afterwards so a LATER write in the same test starts from the same mode a
    fresh transaction would (every deferred trigger here is `INITIALLY
    DEFERRED`) -- without it, every write after the first commit() would run
    under IMMEDIATE mode instead, which is a second way to diverge from a
    real session, just in the opposite direction.

    `commit_count` IS COUNTED, NOT MERELY CALLED -- a second, separate review
    finding: nothing before this counted whether a handler called commit()
    AT ALL. A read WITHIN one test's still-open transaction sees an earlier
    write regardless of whether that write was ever committed (ordinary
    same-transaction MVCC visibility), so a test asserting "the row is
    there" cannot tell a handler that commits from one that silently does
    not -- and removing `conn.commit()` from `_handle_sign` was measured to
    leave the full suite passing.

    WHAT ASSERTING THIS COUNT DOES AND DOES NOT PROVE -- stated here, the
    one place it actually lives, rather than pointed at from a "module
    docstring's closing note" that does not exist (a round-3 review finding:
    the pointer was wrong). It proves a handler's code path reached
    `.commit()`, and nothing more. It is NOT a substitute for
    `test_cli_policy.py`'s `_and_commits` tests, which prove theirs by
    reading the row back on a genuinely SEPARATE connection -- a write
    reaching disk is not a claim this file's own design (nothing here may
    ever commit for real; see the module docstring's second paragraph) can
    demonstrate at all. Every assertion-site comment that says "see
    `_NoCommit`'s own docstring" means exactly this paragraph.
    """

    _SAVEPOINT = "drugref_cli_test"

    def __init__(self, real):
        self._real = real
        self.commit_count = 0
        self._real.execute(f"SAVEPOINT {self._SAVEPOINT}")

    def commit(self) -> None:
        self.commit_count += 1
        # NO try/finally HERE -- see rollback()'s own docstring for why a
        # finally clause restoring DEFERRED mode immediately after this
        # SET CONSTRAINTS call fails would itself fail: the transaction is
        # ABORTED the instant a deferred trigger raises, and postgres
        # refuses every statement (including SET CONSTRAINTS) on an
        # aborted transaction until something un-aborts it. Only
        # ROLLBACK TO SAVEPOINT can do that, and only the CALLER's own
        # recovery path (`_write`'s `except`, which always calls
        # `conn.rollback()`) is positioned to run it -- so the restore has
        # to live there, not here.
        self._real.execute("SET CONSTRAINTS ALL IMMEDIATE")
        self._real.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
        self._real.execute(f"SAVEPOINT {self._SAVEPOINT}")
        self._real.execute("SET CONSTRAINTS ALL DEFERRED")

    def rollback(self) -> None:
        """Recover after ANY failure -- a rejected write, or a fake commit
        whose own `SET CONSTRAINTS ALL IMMEDIATE` raised -- and restore
        DEFERRED mode as part of that recovery, not as an afterthought.

        A ROUND-3 REVIEW FINDING, AND THE ONE THIS FILE'S OWN C1 TEST COULD
        NOT SEE: `commit()`'s restore (`SET CONSTRAINTS ALL DEFERRED`,
        formerly its own last line) only ran on the SUCCESS path -- when
        `SET CONSTRAINTS ALL IMMEDIATE` itself raises (test_keys_register_a_
        second_time_...'s exact case, and the only case this whole fix
        exists for), the three statements after it never execute, and
        `ROLLBACK TO SAVEPOINT` does NOT undo a constraint-mode change --
        measured directly by a reviewer's control, not assumed: a
        LEGITIMATE `keys.revoke` (insert-then-supersede, which genuinely
        needs DEFERRED mode) raised `RaiseException` immediately after a
        prior command's failed fake commit and this method's own recovery
        rollback, with nothing wrong in `revoke` at all. Every statement
        after a failed fake commit was therefore running under IMMEDIATE
        mode no real session is ever in -- state that did not exist before
        the `SET CONSTRAINTS ALL IMMEDIATE` fix landed (this harness used
        to be unconditionally DEFERRED).

        THE FIX BELONGS HERE, NOT IN A `try`/`finally` INSIDE `commit()`,
        because `rollback()` is the one place EVERY recovery path already
        goes through -- `_write`'s `except` calls `conn.rollback()`
        unconditionally after ANY caught exception, whether it came from
        the writer call or from `commit()` itself -- and because it is the
        only place safe to run `SET CONSTRAINTS ALL DEFERRED` at all: that
        statement cannot run until `ROLLBACK TO SAVEPOINT` has already
        un-aborted the transaction, so a restore attempted inside
        `commit()`'s own failure path would have to duplicate this exact
        rollback first. Centralising it here also covers the harmless case
        for free -- a failure from the WRITER call, before `commit()` ever
        ran, never touched constraint mode in the first place, so
        re-issuing DEFERRED here is a no-op, not a risk.

        `test_a_legitimate_write_still_works_after_a_failed_fake_commit`
        reproduces the reviewer's control directly: it forces the leak,
        then proves recovery with a real `keys.revoke` in the same test.
        """
        self._real.execute(f"ROLLBACK TO SAVEPOINT {self._SAVEPOINT}")
        self._real.execute("SET CONSTRAINTS ALL DEFERRED")

    def __getattr__(self, name):
        return getattr(self._real, name)


class _FakeConnectionContext:
    """What `wconn`'s monkeypatch hands back in place of a real `psycopg.
    connect(dsn)` call -- `cli.main` uses its connection as a `with` block
    (`with db.connect(args.dsn) as conn: ...`), so the replacement needs
    `__enter__`/`__exit__` too, not just the connection methods `_NoCommit`
    already forwards. `__exit__` returns False (never swallows an exception):
    that is what lets a RuntimeError-family exception raised inside the
    `with` block keep propagating out to `cli.main`'s own try/except, exactly
    as a real connection's `__exit__` would.
    """

    def __init__(self, wconn):
        self._wconn = wconn

    def __enter__(self):
        return self._wconn

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def wconn(conn, monkeypatch):
    """`conn`, wrapped through `_NoCommit` (see its docstring), with
    `cli.db.connect` patched to hand it back in place of a real connection --
    so `_run` can call the REAL `cli.main` (parsing, dispatch, and `main`'s
    own try/except around the RuntimeError family) through this wrapped,
    non-committing connection instead of a genuinely separate, genuinely-
    committing one. See the module docstring's third paragraph.
    """
    wrapped = _NoCommit(conn)
    monkeypatch.setattr(
        cli.db, "connect", lambda dsn=None: _FakeConnectionContext(wrapped))
    return wrapped


def _run(wconn, argv):
    """Call the REAL `cli.main`, routed through `wconn` via the monkeypatch
    the `wconn` fixture installs.

    `db.connect` IS MONKEYPATCHED, NOT BYPASSED: an earlier draft called
    `args.handler(wconn, args)` directly, which runs the handler but skips
    `main`'s own except clause entirely -- and `keys.NoLiveKeyError` (like
    every RuntimeError this module's handlers can raise) is caught THERE, not
    in `cli_signing.py`, on `interactions.NoLiveDecisionError`'s own
    precedent. Calling `cli.main` here keeps that whole clause in the loop.
    """
    return cli.main(argv)


@pytest.fixture
def committed(_migrated, monkeypatch):
    """A DSN `cli.main` will pick up via $DRUGREF_DSN -- used by exactly one
    test below (`test_keys_list_is_wired_into_cli_main`), which is read-only
    and therefore the one command class safe to run through `cli.main`'s own,
    genuinely-committing connection. Every write command in this file uses
    `_run`/`wconn` instead; see the module docstring."""
    monkeypatch.setenv("DRUGREF_DSN", _migrated)
    yield _migrated


@pytest.fixture
def a_signable_target(conn, a_graded_rule):
    """One curated_interaction row, on the ordinary `conn` -- no commit
    needed, since `_run` keeps a whole test on one open transaction and a
    handler sees this row through ordinary same-transaction visibility."""
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    return {"target_id": target_id, **a_graded_rule}


@pytest.fixture
def a_key_file(tmp_path):
    """A private key file on disk, plus its derived public half and
    fingerprint -- what `sign` and `publish` read from `--key`. Built directly
    through signing.py rather than through `drugref keys generate`, so a bug in
    the CLI's OWN generate command cannot mask a bug in sign/publish's own
    key-file handling (the two would otherwise rise and fall together, and
    neither test would catch the other breaking).
    """
    private, public = signing.generate_keypair()
    path = tmp_path / "curator.key"
    path.write_bytes(private)
    return {"path": path, "private": private, "public": public,
            "fingerprint": signing.fingerprint(public)}


@pytest.fixture
def a_registerable_key(tmp_path):
    """A public key file on disk, for `keys register --public-key`. Separate
    from `a_key_file`: register operates on the PUBLIC half, sign/publish on
    the PRIVATE half, and no single test needs both from one fixture."""
    private, public = signing.generate_keypair()
    path = tmp_path / "curator.pub"
    path.write_bytes(public)
    return {"path": path, "public": public, "private": private,
            "fingerprint": signing.fingerprint(public)}


# ---- the size cap -----------------------------------------------------------


def test_cli_signing_py_is_under_the_size_cap():
    """CLAUDE.md rule 4, measured rather than assumed -- test_cli.py's
    `test_cli_py_is_under_the_size_cap` precedent, one file over. Task 10's
    own brief predicted a single `cli_signing.py` would land at 515 lines,
    over the ~500 cap, and named the split (keys half / sign-verify-publish
    half) as the remedy rather than shipping over it -- this pins BOTH halves
    of that split so neither can silently grow back past the line the first
    draft was measured crossing."""
    import pathlib

    from drugref import cli_signing, cli_signing_release
    for module in (cli_signing, cli_signing_release):
        lines = len(pathlib.Path(module.__file__).read_text().splitlines())
        assert lines <= 500, f"{module.__name__} is {lines} lines, over the ~500 cap"


# ---- the one full-stack proof -------------------------------------------------


def test_keys_list_is_wired_into_cli_main(committed, capsys):
    """One proof that the FULL stack -- registration in `cli.build_parser`,
    argument parsing, `db.connect`, dispatch -- works end to end for this
    module's commands, not just that a handler behaves correctly called in
    isolation. Read-only, and therefore the one command in this file safe to
    run through `cli.main`'s own, genuinely-committing connection; see the
    module docstring for why every OTHER test uses `_run` instead."""
    assert cli.main(["keys", "list"]) == 0
    assert "registered keys:" in capsys.readouterr().out


# ---- keys generate -----------------------------------------------------------


def test_keys_generate_writes_the_private_key_0600(wconn, tmp_path):
    """The failure mode of a world-readable private key is silent -- nothing
    ever tells the holder their key leaked -- so the permission bit is
    asserted directly rather than inferred from the command succeeding."""
    out = tmp_path / "curator.key"
    assert _run(wconn, ["keys", "generate", "--out", str(out)]) == 0
    assert oct(out.stat().st_mode)[-3:] == "600"
    assert len(out.read_bytes()) == 32
    assert len((tmp_path / "curator.key.pub").read_bytes()) == 32


def test_keys_generate_refuses_to_overwrite_an_existing_private_key(
        wconn, tmp_path, capsys):
    """THE FAILURE MODE IS SILENT AND UNRECOVERABLE: a curator who overwrites
    their own key file loses the ability to sign as themselves, with no error
    to tell them it happened. Proved by reading the ORIGINAL bytes back
    afterwards, not merely by the exit code -- an exit-code-only assertion
    would still pass if `generate` printed an error but wrote the new key
    anyway."""
    out = tmp_path / "curator.key"
    assert _run(wconn, ["keys", "generate", "--out", str(out)]) == 0
    original = out.read_bytes()

    assert _run(wconn, ["keys", "generate", "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "Traceback" not in err
    assert out.read_bytes() == original


def test_keys_generate_refuses_to_overwrite_the_public_half_too(wconn, tmp_path):
    """The pre-check covers BOTH files, not only the security-sensitive
    private one -- a stray `curator.key.pub` from an unrelated file would
    otherwise be silently clobbered by a `generate` whose --out just happens
    to collide with it."""
    out = tmp_path / "curator.key"
    (tmp_path / "curator.key.pub").write_bytes(b"not a key")
    assert _run(wconn, ["keys", "generate", "--out", str(out)]) == 2
    assert not out.exists()  # nothing written once EITHER file conflicts


# ---- keys register ------------------------------------------------------------


def test_keys_register_prints_the_fingerprint_it_derived(
        wconn, a_registerable_key, capsys):
    """The operator handed over a PUBLIC KEY FILE, not a fingerprint -- this is
    how they read back what they just registered without a second `keys list`
    round trip."""
    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "a curator", "--registered-by", "an operator"]) == 0
    out = capsys.readouterr().out
    assert a_registerable_key["fingerprint"] in out
    assert "registered signing_key_id=" in out
    # `wconn.commit_count` proves the handler actually CALLED conn.commit(),
    # which a same-transaction read of the row above cannot: that read would
    # see an uncommitted INSERT exactly as readily as a committed one. See
    # _NoCommit's own docstring for what this counter does and does not
    # prove -- it is not a substitute for a second-connection read-back, only
    # the weaker check that remains possible once nothing here may commit
    # for real (test_cli_policy.py's own `_and_commits` tests prove theirs
    # by reading back on a genuinely separate connection; that proof is not
    # available to a file whose whole design is to never let a write reach
    # disk).
    assert wconn.commit_count == 1


def test_keys_register_refuses_a_blank_holder_before_any_write(
        wconn, conn, a_registerable_key, capsys):
    """IMPORTANT: `signing_key.holder` is NOT NULL with no non-blank CHECK
    (db/030), so a blank value satisfies BOTH argparse's `required=True`
    (presence, not content) and the schema -- and then sits on a row the
    append-only floor makes UNCORRECTABLE. Proved by checking the fingerprint
    was never registered, not merely by the exit code."""
    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "   ", "--registered-by", "an operator"]) == 2
    err = capsys.readouterr().err
    assert "--holder" in err
    assert "Traceback" not in err
    assert keys.live(conn, a_registerable_key["fingerprint"]) is None


def test_keys_register_refuses_a_blank_registered_by(
        wconn, conn, a_registerable_key, capsys):
    """The other half of IMPORTANT 3's guard -- `registered_by` carries the
    identical NOT-NULL-no-CHECK hazard `--holder` does, and `keys.revoke` later
    writes a REVOCATION into this same column, so a blank slipping through here
    would be indistinguishable in the schema from an honest revoker's name."""
    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "a curator", "--registered-by", " "]) == 2
    err = capsys.readouterr().err
    assert "--registered-by" in err
    assert keys.live(conn, a_registerable_key["fingerprint"]) is None


def test_keys_register_rejects_a_public_key_of_the_wrong_length(
        wconn, conn, tmp_path, capsys):
    """signing_key_public_key_length's CHECK (octet_length = 32) is the one
    constraint `keys register` can genuinely trip from an operator mistake --
    the wrong file handed to --public-key -- and `_write` quotes it exactly as
    cli_policy's `--decision` does, rather than restating "32 bytes" as a
    second, driftable copy."""
    bad = tmp_path / "not-a-key.pub"
    bad.write_bytes(b"too short")
    expected = _constraint_definition(conn, "signing_key", "signing_key_public_key_length")
    assert _run(wconn, [
        "keys", "register", "--public-key", str(bad), "--holder", "a curator",
        "--registered-by", "an operator"]) == 2
    err = capsys.readouterr().err
    assert expected in err
    assert "InFailedSqlTransaction" not in err and "Traceback" not in err


def test_keys_register_a_second_time_for_the_same_key_is_reported_cleanly(
        wconn, conn, a_registerable_key, capsys):
    """CRITICAL: `signing_key`'s single-live check
    (`forbid_multiple_live_assertions`) is `DEFERRABLE INITIALLY DEFERRED`
    (db/030 section 3; `keys.register`'s own docstring: "the single-live
    check is DEFERRED -- so registering a second live row for one
    fingerprint surfaces at the caller's COMMIT, not here"). Re-running `keys
    register` for a key already registered -- ordinary shell-history error,
    not a novel input -- therefore succeeds at the INSERT and fails only at
    COMMIT, which a review round found landing as a raw `RaiseException`
    traceback because `_write`'s `conn.commit()` used to sit AFTER its own
    `try`. Proved by the absence of a traceback (a bare exit-code check
    would not distinguish "one clean line" from "unhandled exception, pytest
    still reports the assertion that follows as failed") and by the FIRST
    registration surviving with its ORIGINAL signing_key_id, unmoved by the
    rejected second attempt.

    ALSO THE TEST THAT NEEDS `_NoCommit.commit()`'s `SET CONSTRAINTS ALL
    IMMEDIATE` fix to mean anything: `RELEASE SAVEPOINT` alone never fires a
    DEFERRED trigger, so without that fix this test's second `_run` call
    would silently commit clean, both rows would read back live, and the
    exit-code assertion below would simply be wrong about what a real
    `drugref keys register` does.
    """
    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "a curator", "--registered-by", "an operator"]) == 0
    first_id = keys.live(conn, a_registerable_key["fingerprint"]).signing_key_id
    capsys.readouterr()

    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "a curator", "--registered-by", "an operator"]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "live rows" in err

    record = keys.live(conn, a_registerable_key["fingerprint"])
    assert record is not None
    assert record.signing_key_id == first_id
    assert record.status == "active"


def test_a_legitimate_write_still_works_after_a_failed_fake_commit(
        wconn, conn, a_registerable_key):
    """IMPORTANT (round 3): `_NoCommit.commit()` used to restore DEFERRED
    mode only on the SUCCESS path -- the three statements after `SET
    CONSTRAINTS ALL IMMEDIATE` (including the restore) never ran when
    IMMEDIATE itself raised, which is `test_keys_register_a_second_time_...`'s
    own case, and the ONLY case this fix exists for. `ROLLBACK TO SAVEPOINT`
    does NOT undo a constraint-mode change (measured directly, not assumed:
    a control run confirmed a legitimate `keys.revoke` -- insert-then-
    supersede, which genuinely needs DEFERRED mode because the natural key
    is briefly two live rows between the INSERT and the UPDATE that
    supersedes -- raised `RaiseException` immediately after a prior
    command's failed fake commit and `_write`'s own recovery rollback, with
    no bug in `revoke` at all). So every statement after a failed fake
    commit used to run under IMMEDIATE mode no real session is ever in --
    the dangerous direction, since a future "this command is rightly
    rejected" test could pass for the wrong reason (the leaked mode) rather
    than the one it claims to test.

    This test reproduces that exact sequence: force the leak (register the
    same key twice, exactly as the CRITICAL test above does), then prove
    recovery by running a LEGITIMATE `keys.revoke` immediately afterwards,
    in the SAME test, on the SAME connection -- it must succeed.
    """
    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "a curator", "--registered-by", "an operator"]) == 0
    # Force the leak: the second registration fails at _NoCommit.commit()'s
    # own SET CONSTRAINTS ALL IMMEDIATE (the CRITICAL test's exact path),
    # and is recovered by _write's conn.rollback() -- which is exactly the
    # recovery step that used to leave constraint mode stuck on IMMEDIATE.
    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "a curator", "--registered-by", "an operator"]) == 2

    # A LEGITIMATE revoke, immediately after the leak. If constraint mode is
    # still IMMEDIATE here, keys.revoke's own insert-then-supersede raises
    # RaiseException -- not because anything about THIS command is wrong.
    assert _run(wconn, [
        "keys", "revoke", "--key-fingerprint", a_registerable_key["fingerprint"],
        "--status", "retired", "--revoked-by", "an operator"]) == 0
    assert keys.live(conn, a_registerable_key["fingerprint"]).status == "retired"


# ---- keys revoke ---------------------------------------------------------------


def test_keys_revoke_with_an_unrecognised_status_quotes_the_constraint(
        wconn, conn, a_registerable_key, capsys):
    """NO `choices=` ON --status: the vocabulary lives in
    signing_key_status_kind (db/030 section 2), and signing_key.status is a
    FOREIGN KEY rather than a CHECK -- so the constraint `_write` quotes here
    is a FOREIGN KEY definition, not a CHECK, and the test proves the catch
    reaches that exception class too.

    ALSO ASSERTS `db.referenced_vocabulary`'s OWN LINE, and this is the part
    a CHECK-only test would not need: `pg_get_constraintdef` degrades for a
    FOREIGN KEY the way it never does for a CHECK -- it names the referenced
    table and stops, rather than enumerating the values -- so the message
    would otherwise send an operator to psql to learn what
    signing_key_status_kind actually holds.
    """
    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "a curator", "--registered-by", "an operator"]) == 0
    capsys.readouterr()

    expected = _constraint_definition(conn, "signing_key", "signing_key_status_fkey")
    expected_values = db.referenced_vocabulary(
        conn, "signing_key", "signing_key_status_fkey")
    assert _run(wconn, [
        "keys", "revoke", "--key-fingerprint", a_registerable_key["fingerprint"],
        "--status", "nonsense", "--revoked-by", "an operator"]) == 2
    err = capsys.readouterr().err
    assert expected in err
    assert expected_values in err
    assert "active" in expected_values and "compromised" in expected_values
    assert "Traceback" not in err
    assert keys.live(conn, a_registerable_key["fingerprint"]).status == "active"


def test_keys_revoke_changes_the_live_status(wconn, conn, a_registerable_key, capsys):
    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "a curator", "--registered-by", "an operator"]) == 0
    capsys.readouterr()

    assert _run(wconn, [
        "keys", "revoke", "--key-fingerprint", a_registerable_key["fingerprint"],
        "--status", "compromised", "--revoked-by", "an operator"]) == 0
    out = capsys.readouterr().out
    assert "compromised" in out
    assert keys.live(conn, a_registerable_key["fingerprint"]).status == "compromised"
    # One commit for the register above, one for this revoke -- see
    # test_keys_register_prints_the_fingerprint_it_derived's comment for what
    # this counter does and does not prove.
    assert wconn.commit_count == 2


def test_keys_revoke_a_fingerprint_nobody_registered_is_reported_cleanly(
        wconn, capsys):
    """keys.NoLiveKeyError is a RuntimeError, so it reaches cli.main's existing
    catch unhandled by this module -- proved here by the ABSENCE of a
    traceback, not merely by the exit code, which is what would distinguish
    "caught generically" from "caught nowhere and printed by Python."""
    assert _run(wconn, [
        "keys", "revoke", "--key-fingerprint", "f" * 64,
        "--status", "compromised", "--revoked-by", "an operator"]) == 2
    err = capsys.readouterr().err
    assert "no live signing key" in err
    assert "Traceback" not in err


def test_keys_revoke_refuses_a_blank_revoked_by(
        wconn, conn, a_registerable_key, capsys):
    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "a curator", "--registered-by", "an operator"]) == 0
    capsys.readouterr()

    assert _run(wconn, [
        "keys", "revoke", "--key-fingerprint", a_registerable_key["fingerprint"],
        "--status", "retired", "--revoked-by", "  "]) == 2
    err = capsys.readouterr().err
    assert "--revoked-by" in err
    assert keys.live(conn, a_registerable_key["fingerprint"]).status == "active"


# ---- keys list -------------------------------------------------------------


def test_keys_list_prints_none_on_an_empty_registry(capsys):
    """Matching `drugref status`'s three blocks: a bare header with nothing
    under it reads as output that got cut off, not as an answer. DB-FREE, on
    tests/test_curation_orphans.py's `_Conn` precedent."""

    class _Conn:
        def execute(self, sql, params=None):
            return self

        def fetchall(self):
            return []

    assert cli_signing._handle_keys_list(_Conn(), None) == 0
    assert "registered keys: none" in capsys.readouterr().out


def test_keys_list_shows_a_registered_key(conn, capsys):
    """The populated case, isolated on the ordinary rollback-per-test `conn`
    fixture directly (no `_run`/`wconn` needed: `keys.all_live` is read-only,
    so there is no commit() to intercept)."""
    _, public = signing.generate_keypair()
    keys.register(conn, public_key=public, holder="a curator",
                  registered_by="an operator")
    assert cli_signing._handle_keys_list(conn, None) == 0
    out = capsys.readouterr().out
    assert signing.fingerprint(public) in out
    assert "a curator" in out


# ---- sign -------------------------------------------------------------------


def test_sign_dry_run_prints_the_payload_and_writes_nothing(
        wconn, conn, a_signable_target, a_key_file, capsys):
    """spec 4.5's display step: `--dry-run` prints the canonical payload and
    signs nothing. Nothing-written is proved by re-checking signatures on a
    FRESH read (verify_target), not merely by the exit code."""
    assert _run(wconn, [
        "sign", "--target-kind", "curated_interaction",
        "--target-id", str(a_signable_target["target_id"]),
        "--key", str(a_key_file["path"]), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "drugref-sig-v1" in out          # signing.PROLOGUE, decoded
    assert "curated_interaction/v1" in out  # the context line

    assert signatures.verify_target(
        conn, "curated_interaction", a_signable_target["target_id"]) == []


def test_sign_records_a_verifiable_signature(
        wconn, conn, a_signable_target, a_key_file, capsys):
    """The non-dry-run path: `sign` both prints the payload (the same display
    step dry-run exercises) AND commits a signature a fresh read can verify."""
    assert _run(wconn, [
        "sign", "--target-kind", "curated_interaction",
        "--target-id", str(a_signable_target["target_id"]),
        "--key", str(a_key_file["path"])]) == 0
    out = capsys.readouterr().out
    assert "drugref-sig-v1" in out
    assert "signed: signature_id=" in out
    assert a_key_file["fingerprint"] in out

    # The signature exists but its key is UNREGISTERED (a_key_file was never
    # passed to `keys register`), so it verifies as unknown_key -- not valid --
    # which also confirms _handle_sign never silently auto-registers a key.
    verdicts = signatures.verify_target(
        conn, "curated_interaction", a_signable_target["target_id"])
    assert [v.verdict for v in verdicts] == [signing.UNKNOWN_KEY]
    # THE REGRESSION THIS COUNTER EXISTS FOR: a same-transaction read (the
    # verify_target call above) sees an uncommitted INSERT exactly as
    # readily as a committed one, so it cannot by itself prove `_handle_sign`
    # calls conn.commit() -- measured directly by deleting that line and
    # finding this file's suite still 24 green. `commit_count` is the
    # narrower thing that IS still checkable once nothing here may commit
    # for real; see _NoCommit's own docstring for the full accounting of
    # what it does and does not prove.
    assert wconn.commit_count == 1


# ---- verify -----------------------------------------------------------------


def test_verify_reports_unsigned_and_exits_zero(
        wconn, a_signable_target, capsys):
    """UNSIGNED IS THE ORDINARY STATE of the overlay (signing is optional per
    row) -- making it a failing command would make the normal case an error."""
    assert _run(wconn, [
        "verify", "--target-kind", "curated_interaction",
        "--target-id", str(a_signable_target["target_id"])]) == 0
    assert "unsigned" in capsys.readouterr().out


def test_verify_exits_nonzero_on_a_bad_signature(
        wconn, conn, a_signable_target, a_key_file, capsys):
    """THE PROPERTY THAT MATTERS MOST: verify's non-zero exit code. A key is
    registered (so the verdict rule can even check the mathematics), then a
    signature recording the WRONG bytes is stored directly through
    signatures.record -- `signing.verify` will reject it, which is exactly
    the bad_signature case this exit code exists to surface."""
    keys.register(conn, public_key=a_key_file["public"], holder="a curator",
                  registered_by="an operator")
    context, payload = signatures.payload_for(
        conn, "curated_interaction", a_signable_target["target_id"],
        key_fingerprint=a_key_file["fingerprint"],
        signed_at=dt.datetime(2026, 8, 9, tzinfo=dt.timezone.utc))
    signatures.record(
        conn, target_kind="curated_interaction",
        target_id=a_signable_target["target_id"], payload_context=context,
        payload=payload, key_fingerprint=a_key_file["fingerprint"],
        signature=b"\x00" * 64,  # NOT a valid signature over `payload`
        signed_at=dt.datetime(2026, 8, 9, tzinfo=dt.timezone.utc))

    exit_code = _run(wconn, [
        "verify", "--target-kind", "curated_interaction",
        "--target-id", str(a_signable_target["target_id"])])
    out = capsys.readouterr().out
    assert "bad_signature" in out
    # == 1, NOT != 0 -- `!= 0` would still pass if `_verify_target` returned
    # 2 (this surface's OPERATOR-ERROR code) for a bad signature instead of
    # its own dedicated integrity code, which would make a bad_signature
    # indistinguishable from a malformed --target-kind to a script checking
    # the exit status alone.
    assert exit_code == 1


def test_verify_exits_zero_on_an_unknown_key_signature(
        wconn, a_signable_target, a_key_file, capsys):
    """THE WORD 'ONLY' IN 'non-zero only on bad_signature', pinned directly:
    an unknown-key signature is a real, printed finding -- but not one that
    fails the command, exactly as an unsigned row does not. Only mathematical
    forgery (bad_signature) gates the exit code."""
    assert _run(wconn, [
        "sign", "--target-kind", "curated_interaction",
        "--target-id", str(a_signable_target["target_id"]),
        "--key", str(a_key_file["path"])]) == 0
    capsys.readouterr()

    assert _run(wconn, [
        "verify", "--target-kind", "curated_interaction",
        "--target-id", str(a_signable_target["target_id"])]) == 0
    assert "unknown_key" in capsys.readouterr().out


def test_verify_needs_either_release_or_both_target_flags():
    """DB-free: the validation runs before `conn` is ever touched, proved by
    handing it an object that would raise on any attribute access."""
    ns = argparse.Namespace(release=None, target_kind=None, target_id=None)
    assert cli_signing_release._handle_verify(object(), ns) == 2


def test_verify_refuses_both_release_and_target_together():
    ns = argparse.Namespace(release="2026.08.09",
                            target_kind="curated_interaction", target_id=1)
    assert cli_signing_release._handle_verify(object(), ns) == 2


# ---- publish ------------------------------------------------------------------


def test_publish_and_verify_release_round_trip(
        wconn, conn, a_signable_target, a_key_file, capsys):
    """The institutional key never needs registering for `publish` to sign
    (exactly as a curator's key needs no registration for `sign` to succeed);
    it DOES need registering for `verify --release` to call the signature
    `valid` rather than `unknown_key` -- so this test registers it first,
    round-tripping the whole spec-8 operation end to end."""
    keys.register(conn, public_key=a_key_file["public"], holder="drugref.org",
                  registered_by="an operator")

    assert _run(wconn, [
        "publish", "--release-tag", "2026.08.09-test", "--published-by",
        "an operator", "--key", str(a_key_file["path"])]) == 0
    out = capsys.readouterr().out
    assert "published manifest_id=" in out
    assert a_key_file["fingerprint"] in out

    assert _run(wconn, ["verify", "--release", "2026.08.09-test"]) == 0
    out = capsys.readouterr().out
    assert "signature=valid" in out
    assert "intact=True" in out
    # One commit for `publish` -- `verify` never writes, so it adds none.
    # See test_keys_register_prints_the_fingerprint_it_derived's comment for
    # what this counter does and does not prove.
    assert wconn.commit_count == 1


def test_publish_refuses_a_blank_release_tag_before_any_write(
        wconn, conn, a_key_file, capsys):
    """release_manifest.release_tag is NOT NULL with no non-blank CHECK and is
    UNIQUE besides -- a blank slipping through would permanently claim the
    empty string as a release tag on an insert-only table."""
    assert _run(wconn, [
        "publish", "--release-tag", "   ", "--published-by", "an operator",
        "--key", str(a_key_file["path"])]) == 2
    err = capsys.readouterr().err
    assert "--release-tag" in err
    assert conn.execute(
        "SELECT 1 FROM drugref.release_manifest "
        "WHERE release_tag = '   '").fetchone() is None


def test_verify_release_exits_nonzero_when_a_row_is_added_after_publish(
        wconn, conn, a_signable_target, a_key_file, ingest_run_id, a_moiety,
        capsys):
    """PRIORITY-1 RESIDUAL: the exit rule `_verify_release` uses (`is_intact`)
    has to differ from `_verify_target`'s ("non-zero only on bad_signature")
    for THIS case specifically to be caught. Applying the unified rule here
    -- `return 0 if verdict.signature != signing.BAD_SIGNATURE else 1` --
    leaves this test failing: the manifest's own signature is genuinely
    `valid` (nothing forged it), so a rule that looks only at `signature`
    reports exit 0 on a release a later row was added to. A script gating a
    deploy on this command would then pass silently on exactly the
    completeness check `verify_release`'s bidirectional comparison exists to
    make.

    THE ADDED ROW IS A GENUINELY SEPARATE NATURAL KEY (a fresh class, a
    different relationship), not a revision of `a_signable_target`'s own row
    -- `enumerate_live` pairs on `(target_kind, natural_key)`, so reusing the
    same key would make this a content ALTERATION test, not an ADDITION one,
    and prove a different (already-covered, in test_releases.py) code path.
    """
    from tests.test_curated_overlay import _a_class

    keys.register(conn, public_key=a_key_file["public"], holder="drugref.org",
                  registered_by="an operator")
    assert _run(wconn, [
        "publish", "--release-tag", "2026.08.09-added", "--published-by",
        "an operator", "--key", str(a_key_file["path"])]) == 0
    capsys.readouterr()

    second_class = _a_class(conn, ingest_run_id, code="N0000000099",
                            name="A second test MoA [MoA]")
    curation.record_interaction_judgement(
        conn, a_moiety, second_class, "CI_PE", True, severity="minor",
        evidence_grade="theoretical", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")

    exit_code = _run(wconn, ["verify", "--release", "2026.08.09-added"])
    out = capsys.readouterr().out
    assert "signature=valid" in out   # the manifest's signature is genuine
    assert "intact=False" in out
    assert "added=[" in out and "added=[]" not in out
    assert exit_code == 1


def _constraint_definition(conn, table, name):
    """A short alias for `db.constraint_definition`, matching this file's
    other helpers' brevity for the two constraint-quoting tests above."""
    return db.constraint_definition(conn, table, name)


# ---- final review: release_manifest is not a per-row target kind ---------------


@pytest.mark.parametrize("command,extra", [
    ("verify", []),
    # `sign` also needs a --key; `verify` does not accept one. The guard fires
    # before either command reads the file, but argparse still has to be given
    # a well-formed command line for each.
    ("sign", ["--key"]),
])
def test_release_manifest_is_rejected_as_a_per_row_target_kind(
        wconn, conn, a_key_file, capsys, command, extra):
    """I2 (final review). Both commands used to DIE with an uncaught
    `psycopg.errors.UndefinedColumn: column "entry_count" does not exist`, and
    -- worse -- only once a real release existed to name, so the crash was
    unreachable on any database that had not published yet.

    THE PATH TO THE CRASH: `release_manifest` is a legitimate row in
    `signature_target_kind` (that is how a manifest's signature finds its
    table), so nothing before this guard rejected it. `signatures.
    _row_content_fields` then built its SELECT from `release_manifest/v1`'s
    frozen field list read as literal COLUMNS -- and `entry_count` is a DERIVED
    scalar, not a column on `release_manifest` at all. `cli.main` catches
    `RuntimeError`, not `psycopg.Error`, so the operator got a traceback.

    ARGPARSE CANNOT DO THIS WITH `choices=`: the value is a real member of a
    vocabulary that lives in a table (db/006), and a Python list here would be
    the second home that rule forbids. The rejection is a guard with a message
    pointing at the command that DOES serve a manifest.
    """
    manifest_id = releases.publish(
        conn, release_tag="2026.09.01-manifest-kind", published_by="an operator",
        private_key=a_key_file["private"], key_fingerprint=a_key_file["fingerprint"])
    capsys.readouterr()
    argv = [command, "--target-kind", "release_manifest",
            "--target-id", str(manifest_id)]
    argv += [*extra, str(a_key_file["path"])] if extra else []
    assert _run(wconn, argv) == 2
    err = capsys.readouterr().err
    assert "release_manifest is not a per-row target kind" in err
    assert "--release" in err


def test_sign_warns_when_the_key_is_not_registered(
        wconn, a_signable_target, a_key_file, capsys):
    """A signature by an unregistered key is a LEGITIMATE state -- `unknown_key`
    is one of the six verdicts, and registration can honestly follow signing --
    so this warns rather than refusing. But silence was the wrong default: the
    operator gets a `signed:` line and no hint that every reader will call the
    result `unknown_key` until somebody runs `keys register`.
    """
    assert _run(wconn, [
        "sign", "--target-kind", "curated_interaction",
        "--target-id", str(a_signable_target["target_id"]),
        "--key", str(a_key_file["path"])]) == 0
    err = capsys.readouterr().err
    assert "no live signing_key row" in err
    assert a_key_file["fingerprint"] in err


def test_publish_warns_when_the_institutional_key_is_not_registered(
        wconn, conn, a_key_file, capsys):
    """The same warning, where it costs the most. `release_manifest.release_tag`
    is UNIQUE and the table is insert-only, so publishing under an unregistered
    key BURNS that tag: there is no second publish under it to correct with, and
    `drugref verify --release` exits 1 on it for the life of the database.

    THE RELEASE IS STILL PUBLISHED (exit 0) -- refusing would make the ordinary
    air-gapped ordering impossible, and `releases.publish` is not the place to
    invent an enrolment protocol the decision record explicitly says does not
    exist. One line on stderr before the write is the whole fix.
    """
    assert _run(wconn, [
        "publish", "--release-tag", "2026.09.02-unregistered", "--published-by",
        "an operator", "--key", str(a_key_file["path"])]) == 0
    captured = capsys.readouterr()
    assert "no live signing_key row" in captured.err
    assert "published manifest_id=" in captured.out
    assert release_verification.verify_release(
        conn, "2026.09.02-unregistered").signature == signing.UNKNOWN_KEY


def test_publish_does_not_warn_when_the_key_is_registered(
        wconn, conn, a_key_file, capsys):
    """The control. Without it, a warning printed unconditionally would satisfy
    both tests above while crying wolf on every correct publish."""
    keys.register(conn, public_key=a_key_file["public"], holder="drugref.org",
                  registered_by="an operator")
    assert _run(wconn, [
        "publish", "--release-tag", "2026.09.03-registered", "--published-by",
        "an operator", "--key", str(a_key_file["path"])]) == 0
    assert "no live signing_key row" not in capsys.readouterr().err

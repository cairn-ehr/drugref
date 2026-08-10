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

WHY THIS FILE CALLS HANDLERS DIRECTLY (`_run`), NOT `cli.main`. Once nothing may
commit for real, `cli.main`'s own connection -- opened fresh via `db.connect
(args.dsn)` from `$DRUGREF_DSN` -- is no longer usable: it is a SEPARATE
connection from the wrapped `conn` this file's fixtures build a test's rows on,
so it would see none of them. `_run` parses argv with the REAL parser
(`cli.build_parser`) and calls the resulting handler directly against a wrapped
connection, which still exercises real argument parsing and real handler logic
-- everything `cli.main` adds on top is `db.connect` plus dispatch, and
`test_keys_list_is_wired_into_cli_main` below is the one test that proves THAT
still works, through a read-only command specifically because it is the one
command class safe to run for real.
"""
import argparse
import datetime as dt

import pytest

from drugref import (cli, cli_signing, cli_signing_release, curation, db, keys,
                     signatures, signing)


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
    """

    _SAVEPOINT = "drugref_cli_test"

    def __init__(self, real):
        self._real = real
        self._real.execute(f"SAVEPOINT {self._SAVEPOINT}")

    def commit(self) -> None:
        self._real.execute(f"RELEASE SAVEPOINT {self._SAVEPOINT}")
        self._real.execute(f"SAVEPOINT {self._SAVEPOINT}")

    def rollback(self) -> None:
        self._real.execute(f"ROLLBACK TO SAVEPOINT {self._SAVEPOINT}")

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


# ---- keys revoke ---------------------------------------------------------------


def test_keys_revoke_with_an_unrecognised_status_quotes_the_constraint(
        wconn, conn, a_registerable_key, capsys):
    """NO `choices=` ON --status: the vocabulary lives in
    signing_key_status_kind (db/030 section 2), and signing_key.status is a
    FOREIGN KEY rather than a CHECK -- so the constraint `_write` quotes here
    is a FOREIGN KEY definition, not a CHECK, and the test proves the catch
    reaches that exception class too."""
    assert _run(wconn, [
        "keys", "register", "--public-key", str(a_registerable_key["path"]),
        "--holder", "a curator", "--registered-by", "an operator"]) == 0
    capsys.readouterr()

    expected = _constraint_definition(conn, "signing_key", "signing_key_status_fkey")
    assert _run(wconn, [
        "keys", "revoke", "--key-fingerprint", a_registerable_key["fingerprint"],
        "--status", "nonsense", "--revoked-by", "an operator"]) == 2
    err = capsys.readouterr().err
    assert expected in err
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
    assert exit_code != 0


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


def _constraint_definition(conn, table, name):
    """A short alias for `db.constraint_definition`, matching this file's
    other helpers' brevity for the two constraint-quoting tests above."""
    return db.constraint_definition(conn, table, name)

# src/drugref/cli_signing.py
"""`drugref keys` -- the signing-key registry's operator surface (slice 5c.4,
db/030), plus the shared helpers `cli_signing_release.py` reuses for `sign`,
`verify` and `publish`.

SPLIT FROM `cli_signing_release.py` ON SIZE, NOT ON DEPENDENCY DIRECTION. A
single `cli_signing.py` covering `keys generate|register|revoke|list` AND
`sign`/`verify`/`publish` measured 515 lines against CLAUDE.md's ~500 cap --
the same debt Task 1 split `cli.py`/`cli_chain.py` to avoid accumulating a
second time (see that split's own note in cli.py). The seam is the natural
one: this file owns the KEY REGISTRY, `cli_signing_release.py` owns everything
that CONSUMES a key (signing a row, verifying one, publishing a release).

THERE IS A REAL IMPORT CYCLE HERE, NOT AN ABSENT ONE -- an earlier draft of
this paragraph claimed otherwise, which was wrong and worth correcting rather
than leaving for a reader to discover by hoisting an import and watching it
break. The two shared helpers (`_reject_blank`, `_write`) are DEFINED here and
IMPORTED, module-level, at the top of `cli_signing_release.py`; THIS file
imports `cli_signing_release` back, inside `register()`'s own body, to reach
its `register(commands)` function. That is the cycle both ways round, and it
is broken only by WHEN the second half runs: `register()` is a function body,
not top-level module code, so Python does not execute that `import` statement
until something calls `cli_signing.register(commands)` -- by which point this
module has already finished executing top to bottom and `_reject_blank`/
`_write`/`_BlankArgumentError` already exist for `cli_signing_release` to
import. Hoisting that one import to this file's own top-level import block
would reintroduce the cycle for real: Python would reach it while THIS
module is still mid-definition, `cli_signing_release`'s own top-level `from
drugref.cli_signing import ...` would find those three names not yet bound,
and the result is `ImportError: cannot import name '_write' from partially
initialized module 'drugref.cli_signing'`. The local import is therefore
load-bearing, not a style choice -- do not hoist it.

MODELLED ON `cli_policy.py`, DELIBERATELY. That file is the precedent for
every shape below: `_reject_blank` guards a required flag whose value strips
to empty BEFORE any write, because `required=True` checks presence and not
content; `_write` rolls back a rejected write and quotes
`db.constraint_definition` rather than restating a vocabulary in Python; and
`register(commands)` is the entry point `cli.build_parser` calls, so the
global `--dsn`/`--log-level` flags and the one connect-and-dispatch path in
`cli.main` keep serving every command in both files.

LIKE `cli_policy.py`, NEITHER FILE WRITES SQL. Every read and write of
`signing_key`, `assertion_signature`, `release_manifest` and
`release_manifest_entry` goes through `keys.py`, `signatures.py`,
`releases.py` or `release_verification.py`. That is load-bearing for the
identical reason `cli_policy.py`'s docstring states: those four tables are
curated and append-only, and a query embedded in Python is invisible to the
pg_rewrite sweep that finds every OTHER reader.
tests/test_curation_orphans.py's grep, which already scans `cli.py` and
`cli_policy.py`, is extended to `cli_signing.py` at the same time this file
lands.

`_write` HERE TAKES A `catches` ARGUMENT `cli_policy._write` DOES NOT NEED, and
the reason is the shape of the two libraries' writers. `interactions.py`'s
writers only ever fail a CHECK, because every column an operator's flag can
reach is CHECK-constrained. `signing_key.status` is a FOREIGN KEY into
`signing_key_status_kind` instead (db/030 section 1's own reasoning: a
vocabulary a verifier branches on belongs in a table, not a CHECK list), so an
unrecognised `--status` on `keys revoke` raises `ForeignKeyViolation`, not
`CheckViolation` -- a different exception class naming the identical hazard
db/006 found. `release_manifest.release_tag` is UNIQUE rather than either, so
a reused tag on `drugref publish` raises `UniqueViolation`. `keys register`
on a fingerprint that is ALREADY live is a fourth shape again:
`signing_key`'s single-live trigger is `DEFERRABLE INITIALLY DEFERRED` (db/030
section 3, `keys.register`'s own docstring), so the INSERT itself succeeds
and only the COMMIT raises -- as a bare PL/pgSQL `RAISE EXCEPTION`, which
psycopg surfaces as `RaiseException`, not any of the other three. `db.
constraint_definition` quotes the first three identically (it reads `pg_get_
constraintdef`, which does not care what KIND of constraint it is
describing) and does nothing for the fourth, which has no `pg_constraint` row
at all -- it is a trigger, not a constraint. `db.referenced_vocabulary`
answers the one part `constraint_definition` leaves flat for a FOREIGN KEY
specifically: unlike a CHECK, an FK's own definition does not enumerate the
values it admits, only the table it defers to. One pair of helpers still
serves every call -- it is only the exception CLASS (and, for a FOREIGN KEY,
the extra lookup) that differs per writer, and each call site states its own
explicitly rather than one shared tuple catching all four everywhere. That
distinction matters most for `sign` (`cli_signing_release.py`):
`signatures.record`'s own CHECKs all check values THAT MODULE computed, never
a raw operator string, so a violation there is a drugref bug, not a typo --
`sign` calls the writer directly and lets it raise, on cli.main's own
standing rule (see cli.py's docstring on why `except CheckViolation` cannot
live on `main`'s `try`): the same exception class means "operator typo" from
a value that came straight off the command line and "drugref bug" from a
value this process built itself, and only the call site that knows which one
it is may decide.
"""
import os
import pathlib
import sys

import psycopg

from drugref import db, keys, signing


class _BlankArgumentError(ValueError):
    """A required flag was passed, but its value strips to empty.

    Duplicated from `cli_policy._reject_blank`'s SHAPE rather than imported
    from `cli_policy` -- `cli_policy.py`'s docstring calls out the same "copy
    the shape" precedent this whole file follows, and a private cross-module
    import reaching into a genuinely SEPARATE, unrelated CLI module (one this
    file has no other dependency on at all) would be a stranger coupling
    than the six lines copying it saves.

    THIS IS NOT THE SAME QUESTION `cli_signing_release.py` ANSWERS THE OTHER
    WAY, and the difference is worth being explicit about rather than
    leaving two docstrings that look like they disagree: that module DOES
    import this exact class (and `_reject_blank`, `_write`) by name, from
    here. The two files are not unrelated -- they are the two halves of one
    module split apart on line count alone (see this module's own docstring,
    "SPLIT... NOT ON DEPENDENCY DIRECTION"), on `cli.py`/`cli_chain.py`'s own
    precedent, which imports across that identical kind of split rather than
    duplicating `IngestStep`/`ChainError`. A sibling file born from a size
    cap sharing its parent's private helpers is the ordinary case that
    precedent covers; reaching into `cli_policy.py`, a module this file
    shares no split history with, is the "stranger dependency" the first
    paragraph above still refuses.
    """


def _reject_blank(args, *dests: str) -> None:
    """Refuse a flag the operator passed with a blank (or whitespace-only)
    value, before any write.

    THE HAZARD IS THE SAME ONE `cli_policy._reject_blank` GUARDS AGAINST: every
    text column a flag below reaches is `NOT NULL` with NO non-blank CHECK
    (`signing_key.holder`/`.registered_by`, `release_manifest.release_tag`/
    `.published_by` -- db/030), so a blank value satisfies both argparse's
    `required=True` (presence, not content) and the schema, and then sits on a
    row the append-only floor makes UNCORRECTABLE. `keys.revoke`'s carry-
    forward makes this sharper still for `--revoked-by`: it lands in the SAME
    `registered_by` column a registration used, so a blank there is
    indistinguishable in the schema from an honestly-attributed row.
    """
    for dest in dests:
        if not getattr(args, dest).strip():
            flag = "--" + dest.replace("_", "-")
            raise _BlankArgumentError(f"{flag} was given a blank value")


def _write(conn, writer, catches, **kwargs) -> int | None:
    """Call one keyword-only writer from keys.py/releases.py and COMMIT it.

    Returns the new id, or None having already reported a constraint violation
    -- the caller turns that into exit 2. `catches` is the exception class (or
    tuple) THIS call site expects a genuine operator typo to raise -- see the
    module docstring for why that varies (CheckViolation, ForeignKeyViolation
    or UniqueViolation depending on the writer) rather than being one constant
    every caller shares.

    THE COMMIT IS INSIDE THE TRY, NOT AFTER IT -- a defect a review round
    caught: `signing_key`'s single-live check
    (`forbid_multiple_live_assertions`) is `DEFERRABLE INITIALLY DEFERRED`,
    and `keys.py`'s own docstring says so in as many words -- "the single-live
    check is DEFERRED -- so registering a second live row for one fingerprint
    surfaces at the caller's COMMIT, not here." An operator re-running `keys
    register` for a key already registered (ordinary shell-history error) is
    exactly this case: the INSERT itself succeeds, and only `conn.commit()`
    raises. A commit sitting after the try, as `cli_policy._write`'s own
    shape has it, would let that exception reach `cli.main` as a raw
    traceback -- `cli_policy._write` never needed this because none of
    `interactions.py`'s writers commit through a deferred trigger.
    `_handle_keys_register` therefore passes `psycopg.errors.RaiseException`
    (the class a bare PL/pgSQL `RAISE EXCEPTION` surfaces as) in its
    `catches` tuple alongside `CheckViolation`.

    ROLLED BACK EXPLICITLY, not left to `with db.connect(...)`, for
    `cli_policy._write`'s own reason: relying on a COMMIT-of-an-aborted-
    transaction behaving like a rollback is relying on a coincidence, and the
    explicit rollback is what makes the connection usable for the catalogue
    read below (a query issued on a still-aborted transaction raises
    InFailedSqlTransaction instead of running). This holds whether the
    exception came from the INSERT or from the deferred check at COMMIT: a
    failed COMMIT leaves the same aborted-transaction state a failed INSERT
    does, and needs the same recovery.
    """
    try:
        new_id = writer(conn, **kwargs)
        conn.commit()
    except catches as exc:
        conn.rollback()
        # Rendered from exc.diag, never from a Python list of valid values --
        # db/006's lesson, cli_policy._write's own comment restated: the
        # vocabulary lives in the constraint the database enforces, and
        # quoting it is what makes the message actionable without becoming a
        # second copy of it.
        print(f"drugref: {exc.diag.message_primary}", file=sys.stderr)
        definition = db.constraint_definition(
            conn, exc.diag.table_name, exc.diag.constraint_name)
        if definition:
            print(f"drugref: that constraint is {definition}", file=sys.stderr)
        # A FOREIGN KEY, UNLIKE A CHECK, DOES NOT ENUMERATE ITS OWN
        # VOCABULARY -- pg_get_constraintdef above names the referenced
        # table and stops, so an operator would still have to open psql to
        # learn what it holds. db.referenced_vocabulary reads those values
        # from the SAME place constraint_definition read the constraint's
        # shape from: the catalogue, never a second Python list.
        if isinstance(exc, psycopg.errors.ForeignKeyViolation):
            values = db.referenced_vocabulary(
                conn, exc.diag.table_name, exc.diag.constraint_name)
            if values:
                print(f"drugref: valid values are: {values}", file=sys.stderr)
        return None
    return new_id


def _handle_keys_generate(conn, args) -> int:
    """Create a fresh Ed25519 keypair on disk. Writes NO SQL and commits
    nothing -- `conn` is unused, present only because `cli.main` opens one
    before dispatching to any handler (db/030's own opening line: the private
    half never enters this database or any drugref infrastructure at all).

    REFUSES TO OVERWRITE EITHER FILE, checked before either is written. The
    failure mode of overwriting a private key is silent and unrecoverable: its
    holder loses the ability to sign as themselves, with no error to warn them
    it happened. `os.O_EXCL` on the private key's own `open` call is what
    actually closes the race between this check and the write (two concurrent
    `keys generate` runs against the same `--out`, or the same operator
    running the command twice from habit) -- the pre-check above just gives
    BOTH files a fast, friendly rejection instead of only the one O_EXCL
    protects.
    """
    private_path = pathlib.Path(args.out)
    public_path = pathlib.Path(str(private_path) + ".pub")
    for existing in (private_path, public_path):
        if existing.exists():
            print(
                f"drugref: {existing} already exists -- refusing to overwrite "
                "a signing key file. Generate under a different --out path if "
                "you meant to create a second key.", file=sys.stderr)
            return 2

    private_key, public_key = signing.generate_keypair()
    # 0600 AT CREATION, not a chmod afterwards -- os.open's mode argument sets
    # the permission atomically, so there is no window where the private key
    # sits world-readable on disk waiting for a second syscall that has not
    # run yet.
    fd = os.open(private_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(private_key)
    public_path.write_bytes(public_key)

    print(f"generated Ed25519 keypair: private={private_path} (mode 0600) "
          f"public={public_path}")
    print(f"fingerprint={signing.fingerprint(public_key)}")
    return 0


def _handle_keys_register(conn, args) -> int:
    """Register a public key as trusted. THE TRUST ROOT IS THIS COMMAND: a key
    is trusted because an operator with database access ran it (db/030
    section 6) -- there is no enrolment protocol behind it to check.
    """
    try:
        _reject_blank(args, "holder", "registered_by")
    except _BlankArgumentError as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2

    public_key = pathlib.Path(args.public_key).read_bytes()
    # signing_key_public_key_length's CHECK (octet_length = 32) is the one
    # CHECK this writer can hit from an operator mistake -- the wrong file
    # handed to --public-key -- so it is the one CheckViolation this call
    # catches; algorithm and the fingerprint shape are both values THIS
    # process computes, never operator text, so neither can genuinely fail
    # here. RaiseException is the SECOND real operator mistake, and a
    # different shape of one: registering a fingerprint that is ALREADY
    # live raises nothing at the INSERT (there is no UNIQUE(key_fingerprint)
    # -- db/030's own note, since a correction briefly needs two live rows),
    # only at this call's own COMMIT, from signing_key's DEFERRED single-
    # live trigger. See _write's docstring for why the commit had to move
    # inside the try for this catch to ever fire.
    signing_key_id = _write(
        conn, keys.register,
        (psycopg.errors.CheckViolation, psycopg.errors.RaiseException),
        public_key=public_key, holder=args.holder,
        registered_by=args.registered_by)
    if signing_key_id is None:
        return 2
    # PRINTS THE FINGERPRINT IT DERIVED -- the operator handed over a public
    # key file, not a fingerprint, and this is how they read back what they
    # just registered without a second `keys list` round trip.
    print(f"registered signing_key_id={signing_key_id} holder={args.holder!r}")
    print(f"fingerprint={signing.fingerprint(public_key)}")
    return 0


def _handle_keys_revoke(conn, args) -> int:
    """Change a key's status by correction (db/030: insert, then supersede --
    never a column edit). `keys.NoLiveKeyError` propagates to `cli.main`
    unhandled here: it is already a RuntimeError, exactly as
    `interactions.NoLiveDecisionError` is caught there, so a mistyped
    fingerprint is reported in one clean line without this module repeating
    that catch.
    """
    try:
        _reject_blank(args, "revoked_by")
    except _BlankArgumentError as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2

    # NO `choices=` ON --status. The vocabulary lives in
    # signing_key_status_kind (db/030 section 2), and a second list here is
    # exactly the defect db/006 named -- cli_policy's `--decision` is the
    # precedent, including quoting the constraint rather than restating it.
    # signing_key.status is a FOREIGN KEY, not a CHECK (unlike
    # class_expansion_policy.decision), so the violation this write can raise
    # is ForeignKeyViolation.
    new_id = _write(
        conn, keys.revoke, psycopg.errors.ForeignKeyViolation,
        key_fingerprint=args.key_fingerprint, status=args.status,
        revoked_by=args.revoked_by)
    if new_id is None:
        return 2
    print(f"revoked signing_key_id={new_id}: {args.key_fingerprint} "
          f"-> {args.status}")
    return 0


def _handle_keys_list(conn, args) -> int:
    """Every currently-live registered key. Read-only -- nothing to commit.

    PRINTS `none` ON AN EMPTY REGISTRY, matching `drugref status`'s three
    blocks (cli.py's `_handle_status`): a bare header with nothing under it
    reads as output that got cut off, not as an answer.
    """
    records = keys.all_live(conn)
    print("registered keys:" if records else "registered keys: none")
    for r in records:
        print(f"  {r.key_fingerprint} holder={r.holder!r} status={r.status} "
              f"algorithm={r.algorithm} registered_by={r.registered_by!r} "
              f"registered_at={r.registered_at}")
    return 0


def register(commands) -> None:
    """Add `keys`, `sign`, `verify` and `publish` to an existing subparsers
    object. Called by `cli.build_parser` (one line, per the task brief) --
    the sign/verify/publish half is registered by delegating to
    `cli_signing_release.register`, so `cli.py` still needs only this one
    call despite the surface living in two files.
    """
    from drugref import cli_signing_release

    keys_parser = commands.add_parser(
        "keys", help="manage the registry of trusted signing keys")
    keys_actions = keys_parser.add_subparsers(dest="action", required=True)

    generate = keys_actions.add_parser(
        "generate", help="create a fresh Ed25519 keypair on disk")
    generate.add_argument(
        "--out", required=True,
        help="path for the private key (written mode 0600); the public half "
             "is written beside it as <out>.pub")
    generate.set_defaults(handler=_handle_keys_generate)

    reg = keys_actions.add_parser(
        "register", help="register a public key as trusted, out of band")
    reg.add_argument(
        "--public-key", required=True,
        help="path to a raw 32-byte Ed25519 public key, e.g. the .pub file "
             "`keys generate` wrote")
    reg.add_argument("--holder", required=True,
                     help="who this key belongs to")
    reg.add_argument("--registered-by", required=True,
                     help="the operator running this command")
    reg.set_defaults(handler=_handle_keys_register)

    revoke = keys_actions.add_parser(
        "revoke", help="change a key's status by correction, never a "
                       "column edit")
    revoke.add_argument("--key-fingerprint", required=True)
    # No `choices`: see _handle_keys_revoke's comment. The vocabulary lives in
    # signing_key_status_kind; an unrecognised value reaches the database.
    # THE HELP TEXT NAMES NO VALUES, deliberately -- db/006's lesson applies
    # to a help string exactly as it does to a Python `choices=` list: an
    # "(e.g. rotated, retired, compromised)" parenthetical here would be a
    # second, partial copy of the same table a review round already flagged
    # this file for approaching. `keys revoke --status <run it to see>`
    # answers the question from the one place the vocabulary actually lives.
    revoke.add_argument(
        "--status", required=True,
        help="the new status -- see the signing_key_status_kind table for "
             "the live vocabulary; an unrecognised value is rejected with "
             "the current list")
    revoke.add_argument("--revoked-by", required=True,
                        help="the operator running this command")
    revoke.set_defaults(handler=_handle_keys_revoke)

    keys_actions.add_parser(
        "list", help="every currently-live registered key"
    ).set_defaults(handler=_handle_keys_list)

    cli_signing_release.register(commands)

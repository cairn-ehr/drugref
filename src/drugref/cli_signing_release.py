# src/drugref/cli_signing_release.py
"""`drugref sign` / `verify` / `publish` -- the operator surface for everything
that CONSUMES a registered key (slice 5c.4, db/030). The key registry itself
(`drugref keys ...`) lives in `cli_signing.py`; see that module's docstring
for why the two are split and for the shared `_reject_blank`/`_write` helpers
imported from it below.

LIKE `cli_signing.py`, THIS MODULE WRITES NO SQL. Every read and write goes
through `signatures.py`, `releases.py` or `release_verification.py`.
tests/test_curation_orphans.py's no-embedded-SQL grep covers this file too --
it scans by IMPORTING `drugref.cli_signing` (the module `cli.build_parser`
calls), and this file's own SQL-shaped string constants are caught the same
way `cli_policy.py`'s are, because the grep walks whichever module name is
handed to it and this file is added to that parametrised list alongside
`cli_signing`.
"""
import pathlib
import sys
from datetime import datetime, timezone

import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from drugref import release_verification, releases, signatures, signing
from drugref.cli_signing import _BlankArgumentError, _reject_blank, _write


def _read_private_key(path) -> bytes | None:
    """A raw 32-byte Ed25519 private key from `path`, or None having already
    printed why not. Shared by `sign` and `publish` -- both take a `--key`
    file and both must refuse the same malformed shape the same way, rather
    than one silently handing `signing.sign` 31 or 64 garbage bytes and
    producing a signature over nothing meaningful.
    """
    raw = pathlib.Path(path).read_bytes()
    if len(raw) != 32:
        print(f"drugref: {path} is {len(raw)} bytes, not the 32 a raw "
              "Ed25519 private key is -- `keys generate` writes exactly this "
              "shape. A PEM/DER-wrapped key or a copy-paste error would land "
              "here instead.", file=sys.stderr)
        return None
    return raw


def _public_key_of(private_key: bytes) -> bytes:
    """The public half of a raw Ed25519 private key.

    DERIVED, NEVER TAKEN AS A SEPARATE FLAG -- `keys.register`'s own
    reasoning, one step further back: accepting a fingerprint (or a public key
    file) alongside the private key would let an operator's mismatch record a
    signature under a fingerprint that does not name the key that actually
    made it, which `verify_target` would then check against the WRONG public
    key. Deriving it here closes that off exactly as `keys.register` closes
    off a caller-supplied fingerprint that does not match its key.
    """
    return Ed25519PrivateKey.from_private_bytes(
        private_key).public_key().public_bytes_raw()


def _handle_sign(conn, args) -> int:
    """Sign one curated row with a local private key file (spec 10.3: signing
    runs on the curator's machine, and the private key never touches
    drugref's infrastructure).
    """
    private_key = _read_private_key(args.key)
    if private_key is None:
        return 2
    fingerprint = signing.fingerprint(_public_key_of(private_key))
    signed_at = datetime.now(timezone.utc)

    # UnknownTargetError (a RuntimeError) propagates to cli.main unhandled:
    # a mistyped --target-kind/--target-id is reported in one clean line by
    # main's existing catch, with no local try/except needed here.
    context, payload = signatures.payload_for(
        conn, args.target_kind, args.target_id,
        key_fingerprint=fingerprint, signed_at=signed_at)

    # SPEC 4.5'S DISPLAY STEP -- ALWAYS, not only under --dry-run: printing
    # the payload before signing is how "you can read exactly what you are
    # about to attest" is satisfied by the tool, rather than by breaking
    # `payload_for`'s own API to make a row's content inspectable some other
    # way. canonical_payload's bytes are always valid UTF-8 -- every value
    # signing.render produces is ASCII or UTF-8 text, and bytea renders as
    # lowercase hex -- so decoding here can never raise.
    print(payload.decode("utf-8"))
    if args.dry_run:
        return 0

    # signatures.record's own CHECKs all constrain values THIS FUNCTION
    # computed (a SHA-256 digest, a 64-byte Ed25519 signature, a fingerprint
    # this module derived) -- never a raw operator string -- so a violation
    # here is a drugref bug, not a typo, and is left to raise with its
    # traceback rather than caught by _write. See cli_signing.py's docstring.
    signature = signing.sign(private_key, payload)
    signature_id = signatures.record(
        conn, target_kind=args.target_kind, target_id=args.target_id,
        payload_context=context, payload=payload, key_fingerprint=fingerprint,
        signature=signature, signed_at=signed_at)
    conn.commit()
    print(f"signed: signature_id={signature_id} fingerprint={fingerprint}")
    return 0


def _verify_target(conn, args) -> int:
    """Every recorded signature over one curated row, each checked fresh."""
    verdicts = signatures.verify_target(conn, args.target_kind, args.target_id)
    if not verdicts:
        # UNSIGNED IS THE ORDINARY STATE (signatures.py's own docstring):
        # signing is optional per row and the overlay ships with most rows
        # unsigned. Exiting non-zero here would make the normal case a
        # failing command.
        print(f"{args.target_kind} {args.target_id}: unsigned")
        return 0
    for v in verdicts:
        print(f"  signature_id={v.signature_id} key_fingerprint={v.key_fingerprint} "
              f"holder={v.holder or '(unregistered key)'} signed_at={v.signed_at} "
              f"verdict={v.verdict}")
    # NON-ZERO ONLY ON bad_signature (spec 7.1's precedence, one further
    # decision layered on top for the EXIT CODE specifically). unknown_key,
    # key_revoked_compromised and key_expired are all printed above, and are
    # real findings an operator should read -- but they are REGISTRY-level
    # facts, the same class db/030's own read-path view reports as `signed`
    # vs `signed_by_revoked_key` rather than as a hard failure (section 9: "a
    # signature is not an admission gate"). bad_signature alone means the
    # mathematics itself disagrees with what is on record, which is the one
    # finding worth failing a script over.
    if any(v.verdict == signing.BAD_SIGNATURE for v in verdicts):
        return 1
    return 0


def _verify_release(conn, args) -> int:
    """One published release's signature, content and self-consistency."""
    verdict = release_verification.verify_release(conn, args.release)
    print(f"release {args.release}: signature={verdict.signature} "
          f"intact={verdict.is_intact}")
    print(f"  dropped={verdict.dropped}")
    print(f"  added={verdict.added}")
    print(f"  altered={verdict.altered}")
    print(f"  row_count_ok={verdict.row_count_ok} "
          f"manifest_digest_ok={verdict.manifest_digest_ok}")
    # is_intact, NOT "signature == bad_signature" alone -- and deliberately a
    # DIFFERENT rule from _verify_target's. A release manifest is signed
    # UNCONDITIONALLY at publish time (releases.publish signs within the same
    # transaction that writes it), so unlike a curated row there is no
    # ordinary "unsigned" state here worth protecting from a failing exit
    # code. Every one of is_intact's five ANDed conditions -- signature,
    # dropped, added, altered, the manifest's own bookkeeping -- is therefore
    # a real thing an operator running this in a script wants to gate on.
    return 0 if verdict.is_intact else 1


def _handle_verify(conn, args) -> int:
    """`drugref verify --target-kind K --target-id N` or `drugref verify
    --release TAG` -- one command, two mutually exclusive modes. Validated
    here rather than by argparse: a mutually exclusive GROUP cannot express
    "this flag alone, or these other two together", the same gap `cli._Parser`
    documents for `policy show`'s --source/--code pair.
    """
    have_release = args.release is not None
    have_target = args.target_kind is not None or args.target_id is not None
    if have_release and have_target:
        print("drugref: verify takes --release, or --target-kind and "
              "--target-id, not both", file=sys.stderr)
        return 2
    if have_release:
        return _verify_release(conn, args)
    if args.target_kind is None or args.target_id is None:
        print("drugref: verify needs --release, or both --target-kind and "
              "--target-id", file=sys.stderr)
        return 2
    return _verify_target(conn, args)


def _handle_publish(conn, args) -> int:
    """Enumerate the live curated overlay and sign it as a release (spec 8)."""
    try:
        _reject_blank(args, "release_tag", "published_by")
    except _BlankArgumentError as exc:
        print(f"drugref: {exc}", file=sys.stderr)
        return 2

    private_key = _read_private_key(args.key)
    if private_key is None:
        return 2
    fingerprint = signing.fingerprint(_public_key_of(private_key))

    # release_manifest_row_count/_digest_length/_upstream_releases_array are
    # all values THIS process computes and can never genuinely fail; the one
    # constraint a real operator mistake can trip is release_tag's UNIQUE --
    # reusing a tag a previous publish already claimed.
    manifest_id = _write(
        conn, releases.publish, psycopg.errors.UniqueViolation,
        release_tag=args.release_tag, published_by=args.published_by,
        private_key=private_key, key_fingerprint=fingerprint)
    if manifest_id is None:
        return 2
    print(f"published manifest_id={manifest_id} release_tag={args.release_tag}")
    print(f"fingerprint={fingerprint}")
    return 0


def register(commands) -> None:
    """Add `sign`, `verify` and `publish` to an existing subparsers object.
    Called by `cli_signing.register`, not directly by `cli.build_parser` --
    see `cli_signing.py`'s docstring for why the split keeps `cli.py`'s own
    integration to the single call the task brief asks for.
    """
    sign = commands.add_parser(
        "sign", help="sign one curated row with a local private key file")
    sign.add_argument("--target-kind", required=True,
                      help="curated_interaction or curated_condition")
    sign.add_argument("--target-id", required=True, type=int)
    sign.add_argument("--key", required=True,
                      help="path to a raw 32-byte Ed25519 PRIVATE key file")
    sign.add_argument(
        "--dry-run", action="store_true",
        help="print the canonical payload that would be signed, and sign "
             "nothing")
    sign.set_defaults(handler=_handle_sign)

    verify = commands.add_parser(
        "verify",
        help="check a signature's mathematics -- not just its registry status")
    verify.add_argument("--target-kind",
                        help="with --target-id: one curated row to check")
    verify.add_argument("--target-id", type=int,
                        help="with --target-kind: one curated row to check")
    verify.add_argument("--release",
                        help="a release_tag to check instead of one row")
    verify.set_defaults(handler=_handle_verify)

    publish = commands.add_parser(
        "publish",
        help="enumerate the live curated overlay and sign it as a release")
    publish.add_argument("--release-tag", required=True,
                         help="drugref's own version string for this release; "
                              "stated, never derived")
    publish.add_argument("--published-by", required=True)
    publish.add_argument("--key", required=True,
                         help="path to the institution's raw Ed25519 "
                              "PRIVATE key")
    publish.set_defaults(handler=_handle_publish)

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

from drugref import keys, release_verification, releases, signatures, signing
from drugref.cli_signing import _BlankArgumentError, _reject_blank, _write

# THE ONE TARGET KIND NEITHER `sign` NOR `verify --target-kind` CAN SERVE. It is a real
# row in `signature_target_kind` -- `release_manifest` is how a manifest's signature
# finds its table -- so argparse cannot rule it out with `choices=` without becoming the
# second home for that vocabulary db/006 forbids, and the catalog cannot rule it out
# either, since the value belongs there. See `_reject_manifest_kind` for what goes wrong
# without this guard.
_MANIFEST_KIND = "release_manifest"


def _reject_manifest_kind(target_kind: str) -> bool:
    """True having already explained why `release_manifest` is not a per-row target.

    WITHOUT THIS, BOTH COMMANDS CRASH WITH A RAW TRACEBACK, and only once a real release
    exists -- the worst time to find out. `signatures._row_content_fields` builds its
    SELECT from the context's frozen field list read as literal COLUMNS of the target's
    table, and `release_manifest/v1`'s list names `entry_count`, `upstream_count` and
    `release_tag`-plus-derived scalars, of which `release_manifest` actually has only
    some: the SELECT raises `psycopg.errors.UndefinedColumn: column "entry_count" does
    not exist`. `cli.main` catches `RuntimeError`, not `psycopg.Error`, so that reaches
    the operator as a traceback rather than as a sentence. (On an EMPTY database it
    fails one step earlier and more confusingly still, on the missing manifest row.)

    A MANIFEST IS NOT SIGNED FROM ITS OWN COLUMNS AT ALL -- that is the substance, not a
    missing feature. Its payload is built from `release_manifest_entry` and two derived
    counts by `releases.manifest_payload`, which is why `release_verification` cannot
    reuse `signatures.verify_target` either (see that module's docstring). So the answer
    is not to teach `sign` a special case: `drugref publish` is how a manifest gets
    signed, and `drugref verify --release` is how it gets checked.
    """
    if target_kind != _MANIFEST_KIND:
        return False
    print(f"drugref: {_MANIFEST_KIND} is not a per-row target kind -- a manifest's "
          "payload is built from its entries and derived counts, not from columns of "
          "one row. Use `drugref publish` to create and sign one, and `drugref verify "
          "--release TAG` to check it.", file=sys.stderr)
    return True


def _warn_if_key_is_unregistered(conn, fingerprint: str, what: str) -> None:
    """Warn -- never refuse -- when the private key's own fingerprint names no LIVE
    `signing_key` row.

    NOT A REFUSAL, because recording a signature by an unregistered key is a legitimate
    state the whole verdict vocabulary already has a word for: `unknown_key`, and
    registration can honestly follow signing (an air-gapped curator, a key enrolled by a
    different operator later in the day). `record` deliberately stores signatures it
    cannot vouch for -- see `signatures.record`'s own docstring on why a recorder that
    refused could not store the evidence that a bad signature exists.

    BUT SILENCE HERE IS EXPENSIVE, AND MOST EXPENSIVE FOR `publish`. A release tag is
    UNIQUE and `release_manifest` is insert-only, so this manifest is the only one that
    tag will ever name -- and until somebody registers the key, `verify --release`
    exits 1 on it and every consumer reads `unknown_key`.

    REGISTERING THE KEY LATER DOES FIX IT, which is why this warns rather than refuses.
    `_verify_manifest_signature` looks the key up FRESH on every verification and
    nothing caches a verdict, so the release verifies the moment a matching
    `signing_key` row exists. (An earlier version of this paragraph said the tag was
    burned "forever" and that `verify --release` would fail "for the rest of the
    database's life" -- which contradicted this same docstring three paragraphs up, and
    would push an operator into needlessly burning a second release tag.) One line on
    stderr before the write is the whole fix.
    """
    if keys.live(conn, fingerprint) is not None:
        return
    print(f"drugref: warning -- no live signing_key row for {fingerprint}. This "
          f"{what} will read `unknown_key` until the key is registered "
          "(`drugref keys register --public-key ...`).", file=sys.stderr)


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


def _holder_of(verdict) -> str:
    """A verdict's holder, or a stated reason there is none.

    NOT `v.holder or '(unregistered key)'`. That was an `or`-fallback on an IDENTITY
    value: `signing_key.holder` is `NOT NULL` but carries no non-blank CHECK, and
    `_reject_blank` guards only the CLI path -- so a key registered by any other writer
    with `holder=''` printed "(unregistered key)" for a key that IS registered and whose
    signature may well be VALID. `SignatureVerdict` documents the real test
    (`holder is None` exactly when the verdict is UNKNOWN_KEY), so branch on that.
    """
    if verdict.holder is None:
        return "(unregistered key)"
    return verdict.holder or "(blank holder)"


def _handle_sign(conn, args) -> int:
    """Sign one curated row with a local private key file (spec 10.3: signing
    runs on the curator's machine, and the private key never touches
    drugref's infrastructure).
    """
    if _reject_manifest_kind(args.target_kind):
        return 2
    private_key = _read_private_key(args.key)
    if private_key is None:
        return 2
    fingerprint = signing.fingerprint(_public_key_of(private_key))
    _warn_if_key_is_unregistered(conn, fingerprint, "signature")
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
    """Every recorded signature over one curated row, each checked fresh.

    THE EXIT RULE HERE AND `_verify_release`'s ARE DELIBERATELY DIFFERENT --
    see that function's own docstring for the full reasoning, and see it
    BEFORE changing either one to match the other: a review round's residual
    finding was that "make them consistent" is exactly the wrong instinct
    here, because the two commands are answering different questions
    (per-row signing is optional; a published release is always signed).
    """
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
              f"holder={_holder_of(v)} signed_at={v.signed_at} "
              f"verdict={v.verdict}")
    # NON-ZERO UNLESS SOMETHING ACTUALLY VOUCHES FOR THIS ROW. Two conditions,
    # and the second was missing (review I1):
    #
    #   * ANY bad_signature -> 1. Evidence of an attempted forgery is worth
    #     failing a script over even when a good signature sits beside it.
    #   * NO valid signature at all -> 1. A row whose only signature reports
    #     `unknown_key` is a row where THE MATHEMATICS WAS NEVER CHECKED, and
    #     that is the CHEAPER forgery, not the rarer one: an attacker generates
    #     their own keypair, writes a curated row naming any `reviewed_by`, and
    #     records a signature over it. Reaching `bad_signature` instead requires
    #     naming a REGISTERED fingerprint and then failing to sign for it.
    #     Exiting 0 on the easy attack and 1 on the hard one is backwards.
    #     `key_expired` and `key_revoked_compromised` fall under the same rule:
    #     the registry objects to every signature this row has.
    #
    # THIS IS NOT SPEC 9's "NOT AN ADMISSION GATE", which the earlier version
    # cited. That refusal is about WITHHOLDING ROWS from the read views, where
    # fewer rows is the harm direction for a contraindication. Nothing is
    # withheld here: every verdict is printed above regardless, and an exit code
    # answers a different question -- "is this row's authorship provable right
    # now?". `_verify_release` already answers it this way, since `is_intact`
    # requires VALID; only this surface disagreed. db/030's read-path view
    # agrees too: it counts an unregistered key as OBJECTED.
    #
    # ONE VALID SIGNATURE IS ENOUGH, matching `curated_signature_status`'s
    # `unobjected_count > 0`: a row counter-signed by two curators, one of whom
    # has since rotated a key, is still provably authored. The unsigned case
    # returns 0 above and never reaches here -- signing is optional per row.
    if any(v.verdict == signing.BAD_SIGNATURE for v in verdicts):
        return 1
    if not any(v.verdict == signing.VALID for v in verdicts):
        return 1
    return 0


def _verify_release(conn, args) -> int:
    """One published release's signature, content and self-consistency.

    THE EXIT RULE IS DELIBERATELY NOT `_verify_target`'s "non-zero only on
    bad_signature" -- a review round's Priority-1 finding, measured rather
    than assumed: applying that same rule here (`return 0 if verdict.
    signature != signing.BAD_SIGNATURE else 1`) leaves a published release
    that gained an EXTRA live row (`added` non-empty) exiting 0, because
    `added` is a content finding, not a signature one, and the unified rule
    never looks at it. A script gating a deploy on this command would then
    pass silently on exactly the case `verify_release`'s bidirectional
    check exists to catch. See `is_intact`'s own property docstring for the
    full accounting; the short version is below.
    """
    verdict = release_verification.verify_release(conn, args.release)
    print(f"release {args.release}: signature={verdict.signature} "
          f"intact={verdict.is_intact}")
    # PRINTED AS LISTS though the verdict holds tuples: `[]` and `[(...)]` are what an
    # operator reads, and a one-element tuple renders as `((...),)`, whose trailing
    # comma reads like a typo. The storage shape is immutable for `is_intact`'s sake
    # (see ManifestVerdict); the display shape answers a different question.
    print(f"  dropped={list(verdict.dropped)}")
    print(f"  added={list(verdict.added)}")
    print(f"  altered={list(verdict.altered)}")
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
    #
    # NOTE THE REACH OF THAT FIRST CONDITION: `is_intact` requires `signature
    # == signing.VALID` (release_verification.ManifestVerdict's own
    # property), which is a STRICTER gate than "not bad_signature" --
    # `unknown_key` and `key_revoked_compromised` both fail it too, so they
    # also exit 1 here, unlike an unregistered or revoked key on a single
    # ROW (_verify_target), which prints the finding but still exits 0. That
    # is the right default for a release specifically: an institutional key
    # going unregistered or revoked is exactly the kind of registry event a
    # deploy script gating on this command should stop for, where the
    # per-row case protects the ordinary, expected state of an unsigned
    # curator judgement instead.
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
    if _reject_manifest_kind(args.target_kind):
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
    _warn_if_key_is_unregistered(conn, fingerprint, "release")

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

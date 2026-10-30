# src/drugref/release_signature.py
"""THE SIGNATURE HALF of release verification: is the manifest's own signature good?
(db/030, spec 5.5/7.1). Split out of `release_verification.py` (issue 177).

`release_verification.verify_release` asks two independent questions of a published
release -- does its SIGNATURE verify, and does its CONTENT still match the live
overlay -- and this module answers the first. It was moved verbatim; the reasons
`_verify_manifest_signature` cannot reuse `signatures.verify_target` are in
`release_verification`'s module docstring, beside the content half they contrast with.

NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these modules.
"""
import psycopg

from drugref import keys, signatures, signing, signing_contexts
from drugref.releases import ManifestEntry, manifest_payload


class MalformedManifestError(signing.SigningError):
    """A stored manifest this verifier cannot read at all -- as opposed to one it
    reads and finds wanting, which is a VERDICT rather than an exception.

    `release_manifest.upstream_releases` is CHECKed only as `jsonb_typeof = 'array'`;
    nothing constrains its members, so a hand-written manifest can hold a scalar or
    an object with different keys. Raised as a SigningError so `cli.main` renders one
    sentence: as a bare `KeyError` this escaped that catch and printed a traceback,
    permanently, the table being insert-only."""

def _worst_verdict(verdicts: list) -> str:
    """WORST WINS, using `signing.VERDICT_PRECEDENCE` (spec 7.1) as the ONE ranking --
    review round 2's C4: an earlier draft hand-typed the same five-step order as a
    second dict here, directly beneath a comment claiming it was "reused rather than
    re-invented", which it was not. An unrecognised verdict (a future sixth constant
    this module has not been taught about) ranks WORST rather than raising `KeyError`
    -- a crash at the verification core is a strictly worse failure than treating an
    unknown outcome with maximum suspicion.
    """
    def rank(v: str) -> int:
        try:
            return signing.VERDICT_PRECEDENCE.index(v)
        except ValueError:
            return -1
    return min(verdicts, key=rank)


def _verify_manifest_signature(conn: psycopg.Connection, manifest_id: int,
                               release_tag: str, manifest_digest: bytes
                               ) -> tuple[str, bool | None]:
    """The manifest's own signature verdict, and whether `release_manifest.
    manifest_digest` matches the EARLIEST recorded signature's rebuilt payload.
    Returns `(signature, manifest_digest_ok)`; `manifest_digest_ok` is `None` when
    there is no signature to check it against at all (see below).

    EARLIEST, NOT "ANY" -- review round 2 shipped "any signature's payload matches",
    which review round 3 found unsound: `manifest_digest` is written ONCE, by
    `publish`, over exactly the payload of the ONE signature `publish` records at that
    moment. "Any" lets a LATER, legitimate counter-signature (a genuinely different
    payload -- `signed_at` is inside the signed bytes, spec 4.4) vouch for a digest the
    ORIGINAL signature no longer matches, which is reachable only through a writer bug
    -- precisely the class of defect this column exists to catch.
    `test_a_wrong_manifest_digest_is_not_excused_by_a_later_counter_signature`
    would_have_matched` is the test "any" could not pass and "earliest" does.
    `sig_rows` is already `ORDER BY signature_id` -- the order signatures were
    RECORDED, `keys.history`'s own precedent for why (an operator may supply
    `signed_at` out of order; the surrogate key is the order that actually happened)
    -- so the first entry appended to `digest_matches` below is exactly the earliest.

    `None` FOR THE UNSIGNED CASE, NOT `False` -- review round 3's other finding:
    "unverifiable" and "verified wrong" are different claims, and returning `False`
    for both conflated them. Harmless today only because `NO_SIGNATURE` independently
    sinks `ManifestVerdict.is_intact` regardless of what `manifest_digest_ok` says --
    a coincidence, not a guarantee, and exactly the kind that stops holding the day
    another caller reads this field directly.

    EACH SIGNATURE IS REBUILT UNDER ITS OWN STORED `payload_context` -- review round
    2's C2, and Task 7's C1 defect reintroduced in hard-coded form: the first draft of
    this function never selected `payload_context` at all and always rebuilt under the
    literal string `"release_manifest/v1"`, which is right by coincidence only because
    no second context has ever existed. A signature genuinely recorded under a future
    `release_manifest/v2` would rebuild under the wrong context, produce different
    bytes, and report `bad_signature` for a signature that was never forged --
    `test_verify_release_reconstructs_the_manifest_signatures_past_context` proves the
    read-back matters by pinning one via `monkeypatch`, since no real `/v2` exists yet
    to reach it with otherwise (the same technique
    `tests/test_signatures_writer.py`'s `test_verification_reconstructs_the_past_
    context_not_the_present` uses one layer down).

    `algorithm` IS CHECKED for the identical reason (Task 7's C2): `signing.verify`
    only implements Ed25519, so a row naming anything else must raise
    `UnsupportedAlgorithmError` rather than being silently checked against the wrong
    scheme.

    `keys.for_verification` IS LOOKED UP PER SIGNATURE, UNCACHED, and the reason is loop
    LENGTH, not differing keys -- an earlier version of this line said "since two
    signatures over one manifest could legitimately name different keys (a rotation)",
    which does not support the choice at all: `signatures.verify_target` caches the
    lookup keyed BY fingerprint precisely so differing keys ARE handled and repeated
    ones are not re-fetched. The honest reason is that this loop runs over the
    signatures on ONE manifest -- one at publication, two if somebody counter-signs --
    where a cache is machinery for a repetition that does not happen. `verify_target`
    caches because it is called once per curated row across the whole overlay, a
    different loop with a different cost.

    Issue 87 halved the lookup itself rather than caching it here:
    `keys.live` and `keys.key_status` were two queries about one key and are now one, so
    the uncached choice costs one round trip per signature instead of two.
    """
    published_by, published_at, upstream_releases = conn.execute(
        "SELECT published_by, published_at, upstream_releases "
        "FROM drugref.release_manifest WHERE manifest_id = %s",
        (manifest_id,)).fetchone()
    # SHAPE-CHECKED, NOT SUBSCRIPTED BLIND (review I3). `release_manifest.
    # upstream_releases` carries only `jsonb_typeof(...) = 'array'`; its ELEMENTS are
    # unconstrained, so a member that is a scalar or carries different keys raised
    # `KeyError`/`TypeError` here -- neither a `RuntimeError`, so `cli.main` printed a
    # traceback, and `release_manifest` is insert-only so the row could never be fixed.
    # Same class as the planted `payload_context` one function down.
    try:
        upstream = [(u["source"], u["writer"], u["release"]) for u in upstream_releases]
    except (KeyError, TypeError) as exc:
        raise MalformedManifestError(
            f"release_manifest {manifest_id} ({release_tag!r}) has an "
            f"upstream_releases member this verifier cannot read: {exc!r}. Every "
            "member must be an object with `source`, `writer` and `release` -- the "
            "shape `releases.publish` writes. The column's CHECK constrains only the "
            "value to an array, so a hand-written manifest can reach this.") from exc
    entries = [
        ManifestEntry(target_kind, natural_key, target_id, payload_context,
                     bytes(payload_digest))
        for target_kind, natural_key, target_id, payload_context, payload_digest in
        conn.execute(
            "SELECT target_kind, natural_key, target_id, payload_context, "
            "payload_digest FROM drugref.release_manifest_entry "
            "WHERE manifest_id = %s", (manifest_id,)).fetchall()]

    sig_rows = conn.execute(
        "SELECT key_fingerprint, algorithm, signature, signed_at, signature_id, "
        "payload_context FROM drugref.assertion_signature "
        "WHERE target_kind = 'release_manifest' AND target_id = %s "
        "ORDER BY signature_id", (manifest_id,)).fetchall()
    if not sig_rows:
        return signing.NO_SIGNATURE, None

    verdicts = []
    digest_matches = []
    for (key_fingerprint, algorithm, signature, signed_at, signature_id,
         payload_context) in sig_rows:
        if algorithm != signing.ED25519:
            raise signatures.UnsupportedAlgorithmError(
                f"assertion_signature {signature_id} was signed with {algorithm!r}, "
                "which this module cannot verify -- signing.verify only implements "
                f"{signing.ED25519}.")
        # ONE REGISTRY READ (issue 87), all-or-nothing: `keys.live` and
        # `keys.key_status` were asked separately here, and `verify_target` one layer
        # down had the same pair. See `keys.RegisteredKey` for why the halves must
        # arrive together.
        registered = keys.for_verification(conn, key_fingerprint)
        key = registered.record if registered else None
        status = registered.status if registered else None
        # THE SAME PLANTED-CONTEXT GUARD verify_target applies one layer down (review
        # C3). `assertion_signature.payload_context` is unconstrained beyond its regex
        # here too, and `manifest_payload` subscripts `signing_contexts.FIELD_LISTS`
        # with it -- so one planted `release_manifest` signature turned `drugref verify
        # --release` into a raw `KeyError` traceback for the life of the database, the
        # manifest table being insert-only. The digest cannot be compared either: there
        # are no bytes to compare.
        if not signing_contexts.context_is_usable_for(payload_context,
                                                      "release_manifest"):
            digest_matches.append(False)
            signature_ok = False
        else:
            payload = manifest_payload(
                conn, release_tag=release_tag, published_by=published_by,
                published_at=published_at, entries=entries, upstream=upstream,
                key_fingerprint=key_fingerprint, signed_at=signed_at,
                payload_context=payload_context)
            digest_matches.append(signing.digest(payload) == manifest_digest)
            signature_ok = key is not None and signing.verify(
                key.public_key, payload, signature)
        verdicts.append(signing.verdict(
            status, signature_ok=signature_ok, signed_at=signed_at))
    # `[0]` -- THE EARLIEST -- NOT `all(...)` AND NOT `any(...)`. See this function's
    # docstring for why "any" is unsound; "all" is unsound in the mirror direction and
    # was reachable for a while because the only two-signature test produced
    # `[False, True]`, on which `[0]` and `all(...)` agree. A LATER counter-signature
    # covers genuinely different bytes (`signed_at` is inside them, spec 4.4), so it
    # NEVER matches `manifest_digest` and `all(...)` would report every legitimately
    # counter-signed release as having a wrong digest.
    return _worst_verdict(verdicts), digest_matches[0]

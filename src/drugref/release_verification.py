# src/drugref/release_verification.py
"""Check a published release manifest's signature AND its content, against a
database, right now (db/030, spec 5.5/7.2/8).

SPLIT OUT OF `releases.py` ON SIZE, ONE DIRECTION ONLY. `releases.py` builds a
manifest (`enumerate_live`, `manifest_payload`, `publish`); this module checks one.
The dependency runs one way -- this module imports `ManifestEntry`, `enumerate_live`,
`manifest_payload` and `ENTRY_DIGEST_SIGNED_AT` FROM `releases.py`, and `releases.py`
imports nothing back -- so there is no cycle: a manifest can be built without ever
importing this file, and this file cannot exist without that one.

THE SIGNATURE HALF cannot reuse `signatures.verify_target`. That function assumes a
payload built by selecting the frozen field list's names as literal COLUMNS of the
target's own table (`signatures._row_content_fields`); `release_manifest/v1`'s field
list has DERIVED scalars (`entry_count`, `upstream_count`) and two GROUPS built from
`release_manifest_entry`/`upstream_releases`, none of which is a column on
`release_manifest` at all. `_verify_manifest_signature` instead rebuilds the payload
the way `publish` built it, through `manifest_payload`, and applies the identical spec
7.1 precedence via `signing.verify`/`signing.verdict`/`keys.live`/`keys.key_status`.

THE CONTENT HALF pairs `release_manifest_entry` against `enumerate_live`'s current
answer by `(target_kind, natural_key)`, never `target_id` -- db/030's own comment on
`release_manifest_entry` says plainly that nothing verifies against it, and spec 5.5
explains why: a node that RESTORED the curated overlay from a different path (or
independently RECONSTRUCTED it) assigns different `GENERATED ALWAYS AS IDENTITY`
values for identical content, and pairing on that value would report every unaltered
row as broken. See `verify_release` and `_published_content_is_history` for how a
genuine content ALTERATION is told apart from a legitimate append-only CORRECTION
without ever comparing `target_id` across the two.

NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these modules.
"""
from dataclasses import dataclass

import psycopg
from psycopg import sql

from drugref import keys, signatures, signing
from drugref.releases import (ENTRY_DIGEST_SIGNED_AT, ManifestEntry, enumerate_live,
                              manifest_payload)


@dataclass(frozen=True)
class ManifestVerdict:
    """What one published release is worth, right now -- three independent halves.

    `signature` answers AUTHENTICITY: one of `signing.py`'s six verdict constants, for
    the `assertion_signature` row(s) over the manifest itself.

    `dropped`/`added`/`altered` answer CONTENT INTEGRITY: does the database still hold
    what the manifest enumerated. Independent of `signature` on purpose --
    `test_a_row_whose_content_changed_is_an_ALTERATION` is the test that proves a VALID
    signature can sit beside a FALSE content claim, and a verifier that collapsed the
    two would report a tampered database as a bad signature. Each is a list of
    `(target_kind, natural_key)` pairs -- never `target_id`, for `ManifestEntry`'s
    reason.

    `row_count_ok`/`manifest_digest_ok` answer MANIFEST SELF-CONSISTENCY: does
    `release_manifest.row_count`/`.manifest_digest` -- both WRITER-ASSERTED, neither
    schema-enforced (db/030's own comment: "The release verifier ... is what actually
    checks it") -- still match what `release_manifest_entry` and the EARLIEST recorded
    signature actually hold. Independent of both other halves: an attacker who edits
    only these two columns (impossible in practice, since the table is insert-only, but
    a genuine write-time bug could still get either one wrong) leaves the content
    pairing and the signature both looking fine.

    `manifest_digest_ok` IS `None`, NOT `False`, WHEN THE MANIFEST IS UNSIGNED --
    "unverifiable" and "verified wrong" are different claims, and there is no signed
    payload to recompute a digest from at all. `is_intact` below still reports
    `False` in that case, because `signature` is `NO_SIGNATURE` regardless of what
    this field says -- not because this field was coerced into meaning something it
    does not.
    """
    release_tag: str
    signature: str
    dropped: list
    added: list
    altered: list
    row_count_ok: bool
    manifest_digest_ok: bool | None

    @property
    def is_intact(self) -> bool:
        """VALID signature, nothing dropped/added/altered, and the manifest's own
        bookkeeping matches its own entries. All five, ANDed: a database that matches
        the manifest byte-for-byte under a forged signature is not intact either -- it
        only means an attacker forged a manifest that happens to describe reality,
        which is not the same claim as "this is really what drugref published". `bool()`
        around `manifest_digest_ok` is deliberate: `None` (unsigned) and `False`
        (verified wrong) must both fail this property, and Python already treats `None`
        as falsy in a boolean context, but writing it explicitly says so rather than
        relying on the reader to know that."""
        return (self.signature == signing.VALID
                and not (self.dropped or self.added or self.altered)
                and self.row_count_ok and bool(self.manifest_digest_ok))


class UnknownReleaseError(RuntimeError):
    """No `release_manifest` row names this `release_tag`. Raised rather than returning
    an empty/vacuous verdict -- `keys.NoLiveKeyError`'s precedent: silence is the worst
    answer a lookup can give, and a mistyped tag would otherwise read as "this release
    verifies fine" for having nothing to check."""


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
    `test_a_wrong_manifest_digest_still_reports_wrong_when_a_later_counter_signature_
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

    `keys.live`/`keys.key_status` ARE LOOKED UP PER SIGNATURE, UNCACHED, and the reason
    is loop LENGTH, not differing keys -- an earlier version of this line said "since
    two signatures over one manifest could legitimately name different keys (a
    rotation)", which does not support the choice at all: `signatures.verify_target`
    caches these two lookups keyed BY fingerprint precisely so differing keys ARE
    handled and
    repeated ones are not re-fetched. The honest reason is that this loop runs over the
    signatures on ONE manifest -- one at publication, two if somebody counter-signs --
    where a cache is machinery for a repetition that does not happen. `verify_target`
    caches because it is called once per curated row across the whole overlay, which is
    a different loop with a different cost.
    """
    published_by, published_at, upstream_releases = conn.execute(
        "SELECT published_by, published_at, upstream_releases "
        "FROM drugref.release_manifest WHERE manifest_id = %s",
        (manifest_id,)).fetchone()
    upstream = [(u["source"], u["writer"], u["release"]) for u in upstream_releases]
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
        payload = manifest_payload(
            conn, release_tag=release_tag, published_by=published_by,
            published_at=published_at, entries=entries, upstream=upstream,
            key_fingerprint=key_fingerprint, signed_at=signed_at,
            payload_context=payload_context)
        digest_matches.append(signing.digest(payload) == manifest_digest)
        key = keys.live(conn, key_fingerprint)
        status = keys.key_status(conn, key_fingerprint)
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


def _published_content_is_history(conn: psycopg.Connection, target_kind: str,
                                  live_target_id: int, payload_context: str,
                                  manifest_digest: bytes) -> bool:
    """Does `manifest_digest` match the content of some ANCESTOR of the row now live
    for this natural key -- i.e. was the manifest's claim genuinely true of an earlier,
    since-superseded version of this row?

    Review round 2's C1 (Critical), the fix for the defect that made the module's
    first draft report the OPPOSITE of the truth on two of the reviewer's four probe
    manifests: a `target_id`-equality tie-break gated the branch BEFORE any digest
    comparison, so an unaltered row on a node whose curated overlay had merely been
    reconstructed with fresh `GENERATED ALWAYS AS IDENTITY` values reported 100% churn
    (byte-identical signed payload, verdict `not intact`), while a GENUINELY forged
    digest on the same kind of node was silently reported as an ordinary correction
    (`altered` stayed empty). Both are the worst possible failure mode for a
    verification function: confident and wrong, in opposite directions.

    THE FIX WALKS db/029'S SUPERSESSION CHAIN BACKWARDS FROM THE LIVE ROW
    (`WHERE superseded_by = <current>`, repeated) -- db/029 guarantees `superseded_by`
    is set exactly once, always pointing at a LATER row sharing the SAME natural key
    (the one sequence the overlay tier admits: INSERT the correction, THEN point the
    old row at it), so this chain enumerates exactly the row's own history and nothing
    else's. THE COMPARISON IS CONTENT DIGESTS ONLY, `target_id` used purely as the
    starting point for a LOCAL join within this one database's own history -- never as
    a cross-database identity claim, which is what made the original tie-break wrong.

    `verify_release` calls this ONLY AFTER a digest mismatch (digest comparison first
    is half the fix: the weaker variant -- keep a `target_id` tie-break but consult it
    only after a digest mismatch -- still reports a genuinely wrong digest on a
    reconstructed node as an ordinary correction, since it never looks at content
    history at all). Returning `True` here means "this looks like a legitimate
    correction, not damage to what was published" -- the caller reports drop-plus-add.
    Returning `False` means either the digest is genuinely wrong, OR this node holds
    only live rows with no supersession history at all (a node that reconstructed its
    overlay rather than restored one, keeping no history) -- in which case a genuine
    correction is conservatively misread as `altered` rather than `dropped`+`added`.
    That is a real, stated limitation, not a silent one: ONE row misclassified in a
    direction that asks a human to look, which is strictly better than either of the
    two failure modes this function replaces.
    """
    # `signatures._target_kind_catalog` IS THE ONE READER of `signature_target_kind`
    # (db/006's lesson), and its third value -- the CURRENT context -- is deliberately
    # discarded here: this function is handed the context it must reproduce by its
    # caller, read off the manifest entry being checked. An earlier draft called a
    # near-duplicate `releases._target_table` instead, whose stated justification was
    # that `_target_kind_catalog` "is private to signatures.py" -- while `releases.py`
    # was already calling it two functions away. That second query is deleted.
    table, pk_column, _current_context = signatures._target_kind_catalog(
        conn, target_kind)
    current_id = live_target_id
    seen = {current_id}
    while True:
        # EXACTLY ONE PREDECESSOR EXPECTED, made explicit the same way
        # `releases._natural_key_columns` was -- `.fetchone()` would silently pick
        # whichever row the planner returns first. NOT PREVENTED BY ANY TRIGGER:
        # `forbid_multiple_live_assertions` counts LIVE rows per natural key and says
        # nothing about how many rows point `superseded_by` AT one successor, so
        # db/020's deferred single-live trigger gives this no protection at all. What
        # actually prevents it, on the ordinary path, is `overlay.supersede`'s one
        # guarded UPDATE (WHERE the natural key matches AND superseded_by IS NULL) --
        # and that guard is exactly what raw SQL against the table bypasses. This is a
        # measured fact, not a hypothetical: round 3's re-review reached this branch by
        # committing two predecessor rows pointing at one successor directly, with
        # `SET CONSTRAINTS ALL IMMEDIATE` confirming no trigger objected.
        predecessors = conn.execute(
            sql.SQL("SELECT {pk} FROM drugref.{table} "
                    "WHERE superseded_by = %s").format(
                pk=sql.Identifier(pk_column), table=sql.Identifier(table)),
            (current_id,)).fetchall()
        if not predecessors:
            return False
        if len(predecessors) > 1:
            raise ValueError(
                f"drugref.{table} row {current_id} is pointed at by "
                f"{len(predecessors)} superseded_by values -- normal use through "
                "overlay.supersede's guarded UPDATE should make this unreachable, "
                "but this function cannot silently pick one mid-transaction.")
        predecessor_id = predecessors[0][0]
        if predecessor_id in seen:
            # Defensive only: db/029's floor forbids a cycle (supersession always
            # points at a LATER row, never back to one already superseded), so this
            # should be unreachable. An infinite loop is the wrong failure mode to
            # risk on a verification path even so -- stop rather than hang.
            return False
        seen.add(predecessor_id)
        fields = signatures._row_content_fields(
            conn, table, pk_column, predecessor_id, payload_context)
        full_fields = fields + [
            ("signer_key_fingerprint", ""),
            ("signed_at", signing.render(ENTRY_DIGEST_SIGNED_AT))]
        payload = signing.canonical_payload(payload_context, full_fields)
        if signing.digest(payload) == manifest_digest:
            return True
        current_id = predecessor_id


def verify_release(conn: psycopg.Connection, release_tag: str) -> ManifestVerdict:
    """Check one published release's signature, content and self-consistency, against
    the database `conn` is connected to, right now.

    CONTENT: entries and `enumerate_live`'s current answer are paired by
    `(target_kind, natural_key)`. A natural key in the manifest but not live now is
    DROPPED; live now but absent from the manifest is ADDED. A key present in both is
    checked for a digest match -- REUSING `enumerate_live`'s ALREADY-COMPUTED digest
    when its context matches the entry's stored one (review round 2's C5: recomputing
    a digest `enumerate_live` just built, one line above, doubled this function's query
    count for nothing), and recomputing under the ENTRY'S OWN STORED `payload_context`
    only when it does not -- Task 7's C1 fix, one layer up: re-deriving context from
    the catalog is right when SIGNING and silently wrong for VERIFYING.

    THE PAIRING KEY ITSELF IS ALSO RECONSTRUCTED FROM THE PAST, not re-derived from the
    present schema -- C1 (final review). Which COLUMNS render a natural key is frozen
    per context in `signing.NATURAL_KEY_COLUMNS`, and this function tells
    `enumerate_live` which context each kind's entries were recorded under, so a later
    migration that widens a curated table's natural key cannot silently re-key the
    published set. See the comment at the `natural_key_contexts` construction below,
    and that constant's own.

    ON A MISMATCH, `_published_content_is_history` decides DROPPED+ADDED (a legitimate
    append-only correction) from ALTERED (the manifest's claim matches nothing in this
    row's real history) -- see that function's own docstring; this is the fix for
    review round 2's C1.
    """
    row = conn.execute(
        "SELECT manifest_id, manifest_digest, row_count "
        "FROM drugref.release_manifest WHERE release_tag = %s",
        (release_tag,)).fetchone()
    if row is None:
        raise UnknownReleaseError(
            f"no release_manifest row for release_tag={release_tag!r}. Check "
            "`SELECT release_tag FROM drugref.release_manifest` -- a mistyped tag "
            "would otherwise read as a release with nothing wrong.")
    manifest_id, manifest_digest, stored_row_count = row
    manifest_digest = bytes(manifest_digest)

    signature, manifest_digest_ok = _verify_manifest_signature(
        conn, manifest_id, release_tag, manifest_digest)

    manifest_entries = {
        (target_kind, natural_key): (payload_context, bytes(payload_digest))
        for target_kind, natural_key, _target_id, payload_context, payload_digest in
        conn.execute(
            "SELECT target_kind, natural_key, target_id, payload_context, "
            "payload_digest FROM drugref.release_manifest_entry "
            "WHERE manifest_id = %s", (manifest_id,)).fetchall()
    }
    row_count_ok = len(manifest_entries) == stored_row_count

    # THE LIVE SIDE'S NATURAL KEYS ARE RENDERED UNDER THE ENTRIES' OWN STORED CONTEXTS,
    # never today's -- C1 (final review), and the same "reconstruct the past" rule
    # `payload_context` and `algorithm` already follow one column over.
    # `release_manifest_entry.natural_key` is a RENDERED STRING recorded at publish time
    # AND a signed member of the entry group; re-deriving which COLUMNS produced it from
    # the present schema means comparing a past recording against a present shape. The
    # reviewer measured the consequence by widening `curated_interaction`'s single-live
    # trigger the way db/029 says an additive migration one day will: every live key
    # re-rendered, none paired, and an untouched database reported 100% churn.
    #
    # ONE CONTEXT PER KIND, taken deterministically. `publish` writes one context for
    # every entry of a kind in a manifest, so this mapping is a singleton per kind in
    # anything drugref produced. Iterating the entries in sorted order and keeping the
    # first makes a hand-built manifest that MIXED contexts for one kind deterministic
    # too: entries under the other context simply fail to pair and report as
    # dropped + added -- a loud finding that asks a human to look, which is the same
    # conservative direction `_published_content_is_history` chose.
    #
    # THAT LAST SENTENCE USED TO END "and never a crash on the verification path", which
    # was FALSE AS WRITTEN and is now true because `unpairable` below makes it so. This
    # dict is built from a column with no foreign key behind it, so an entry can name a
    # context no frozen list knows, and the first version of this code subscripted the
    # frozen dicts directly -- an uncaught `KeyError`, which `cli.main` does not catch
    # (it catches `RuntimeError`), reachable with one INSERT.
    natural_key_contexts = {}
    for key in sorted(manifest_entries):
        target_kind, _natural_key = key
        natural_key_contexts.setdefault(target_kind, manifest_entries[key][0])

    live_entries = {
        (entry.target_kind, entry.natural_key): entry
        for entry in enumerate_live(conn, signed_at=ENTRY_DIGEST_SIGNED_AT,
                                    natural_key_contexts=natural_key_contexts)
    }

    manifest_keys = set(manifest_entries)
    live_keys = set(live_entries)

    # AN ENTRY WHOSE STORED CONTEXT CANNOT BE REBUILT FAILS TO PAIR -- removed from
    # the intersection below and reported on BOTH sides (dropped, because the
    # manifest's claim cannot be checked against anything; added, because the live row
    # it might have described is then unmatched). R1 of the final re-review, and the
    # reason it sits here rather than inside the loop: "unverifiable" is a property of
    # the ENTRY, decidable before any comparison, and deciding it here is what keeps
    # `signatures.payload_for` and `_published_content_is_history` from ever being
    # reached with a context they would raise `KeyError` (unknown) or
    # `UndefinedColumn` (another kind's) on. `enumerate_live` already applies the same
    # test to the natural-key half.
    unpairable = {
        key for key in manifest_keys & live_keys
        if not signing.entry_context_is_reproducible(
            manifest_entries[key][0], live_entries[key].payload_context)}

    dropped = sorted((manifest_keys - live_keys) | unpairable)
    added = sorted((live_keys - manifest_keys) | unpairable)
    altered = []

    for key in sorted((manifest_keys & live_keys) - unpairable):
        target_kind, _natural_key = key
        payload_context, digest_m = manifest_entries[key]
        live_entry = live_entries[key]

        if payload_context == live_entry.payload_context:
            live_digest = live_entry.payload_digest
        else:
            _, payload = signatures.payload_for(
                conn, target_kind, live_entry.target_id, key_fingerprint="",
                signed_at=ENTRY_DIGEST_SIGNED_AT, payload_context=payload_context)
            live_digest = signing.digest(payload)

        if live_digest == digest_m:
            continue
        if _published_content_is_history(conn, target_kind, live_entry.target_id,
                                         payload_context, digest_m):
            dropped.append(key)
            added.append(key)
        else:
            altered.append(key)

    dropped.sort()
    added.sort()

    return ManifestVerdict(release_tag=release_tag, signature=signature,
                           dropped=dropped, added=added, altered=altered,
                           row_count_ok=row_count_ok,
                           manifest_digest_ok=manifest_digest_ok)

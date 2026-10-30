# src/drugref/release_verification.py
"""Check a published release manifest's signature AND its content, against a
database, right now (db/030, spec 5.5/7.2/8).

SPLIT OUT OF `releases.py` ON SIZE, ONE DIRECTION ONLY. `releases.py` builds a manifest
(`enumerate_live`, `manifest_payload`, `publish`); this module checks one. The
dependency runs one way -- this module and `release_signature.py` import
`ManifestEntry`, `enumerate_live`, `manifest_payload` and `ENTRY_DIGEST_SIGNED_AT` FROM
`releases.py`, and `releases.py` imports nothing back -- so there is no cycle: a
manifest can be built without ever importing this file, and this file cannot exist
without that one.

THE SIGNATURE HALF lives in `release_signature.py` (issue 177), and it cannot reuse
`signatures.verify_target`. That function assumes a payload built by selecting the
frozen field list's names as literal COLUMNS of the target's own table
(`signatures._row_content_fields`); `release_manifest/v1`'s field list has DERIVED
scalars (`entry_count`, `upstream_count`) and two GROUPS built from
`release_manifest_entry`/`upstream_releases`, none of which is a column on
`release_manifest` at all. `_verify_manifest_signature` instead rebuilds the payload the
way `publish` built it, through `manifest_payload`, and applies the identical spec 7.1
precedence via `signing.verify`/`signing.verdict`/`keys.for_verification`.

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

from drugref import signatures, signing
from drugref.release_signature import _verify_manifest_signature
from drugref.releases import ENTRY_DIGEST_SIGNED_AT, enumerate_live


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
    # TUPLES, NOT LISTS, and `frozen=True` is exactly why it matters. Freezing blocks
    # REBINDING an attribute; it does nothing about mutating the object bound there, so
    # `verdict.dropped.clear()` flipped `is_intact` from False to True on the one type
    # whose entire job is answering "is this release intact". The element type is
    # `(target_kind, natural_key)` -- never `target_id`, for `ManifestEntry`'s reason.
    dropped: tuple[tuple[str, str], ...]
    added: tuple[tuple[str, str], ...]
    altered: tuple[tuple[str, str], ...]
    row_count_ok: bool
    manifest_digest_ok: bool | None

    @property
    def is_intact(self) -> bool:
        """VALID signature, nothing dropped/added/altered, and the manifest's own
        bookkeeping matches its own entries. All SIX fields, ANDed as four conditions
        (the three finding lists share one `not (... or ... or ...)`): a database that
        matches the manifest byte-for-byte under a forged signature is not intact
        either -- it only means an attacker forged a manifest that happens to describe
        reality, which is not the same claim as "this is really what drugref
        published". `bool()`
        around `manifest_digest_ok` is deliberate: `None` (unsigned) and `False`
        (verified wrong) must both fail this property, and Python already treats `None`
        as falsy in a boolean context, but writing it explicitly says so rather than
        relying on the reader to know that."""
        return (self.signature == signing.VALID
                and not (self.dropped or self.added or self.altered)
                and self.row_count_ok and bool(self.manifest_digest_ok))


class AmbiguousSupersessionError(signing.SigningError):
    """More than one row points `superseded_by` at a single successor, so the
    history walk cannot say which predecessor a row came from.

    REACHED IN REVIEW, not hypothetical: round 3 committed two such rows directly,
    with `SET CONSTRAINTS ALL IMMEDIATE` confirming no trigger objected -- db/020's
    deferred single-live check counts LIVE rows per natural key and says nothing
    about how many rows point AT one successor. `overlay.supersede`'s guarded UPDATE
    is what prevents it on the ordinary path, and raw SQL is what bypasses that
    guard.

    A SigningError so `cli.main` renders it as a sentence. This was a bare
    `ValueError`, which that catch misses -- so `drugref verify --release` printed a
    raw traceback on a database in the very state the docstring below documents as
    reachable."""


class UnknownReleaseError(signing.SigningError):
    """No `release_manifest` row names this `release_tag`. Raised rather than returning
    an empty/vacuous verdict -- `keys.NoLiveKeyError`'s precedent: silence is the worst
    answer a lookup can give, and a mistyped tag would otherwise read as "this release
    verifies fine" for having nothing to check."""


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
        # the now-DELETED `releases._natural_key_columns` was, before
        # `signing.NATURAL_KEY_COLUMNS` replaced it -- `.fetchone()` would silently pick
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
            raise AmbiguousSupersessionError(
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

    # FROZEN AT THE BOUNDARY. The three are built as lists because they are appended to
    # and sorted above; they become tuples the moment they enter the verdict, so no
    # caller can empty one and turn a broken release intact.
    return ManifestVerdict(release_tag=release_tag, signature=signature,
                           dropped=tuple(dropped), added=tuple(added),
                           altered=tuple(altered),
                           row_count_ok=row_count_ok,
                           manifest_digest_ok=manifest_digest_ok)

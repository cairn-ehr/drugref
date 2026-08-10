# src/drugref/releases.py
"""Release manifests: enumerate the live curated overlay, publish it as a signed
manifest, and verify a database against one later (db/030, spec 5.5/7.2/8).

ONE MECHANISM, BOTH LAYERS. `signatures.py` signs one curated row; this module signs
a whole RELEASE -- a snapshot enumeration of every live curated row at one moment --
through the identical `assertion_signature` table, with `target_kind =
'release_manifest'`. A manifest needed nothing new at the signing layer, only a new
kind of target and a new writer for it (db/030's opening comment).

A CONTENT MANIFEST, NOT A TRANSPORT SIGNATURE. A signature over the bytes drugref
shipped dies the moment those bytes are loaded into a database -- nothing is left to
re-hash against. A manifest instead ENUMERATES: for every live curated row at
publication, its natural key and content digest, so `verify_release` can recompute the
SAME digest from a row's current content years later and compare. Because it
enumerates, verification is BIDIRECTIONAL and catches omission as well as alteration --
something a transport signature could never see.

NATURAL KEY, NEVER `target_id`. `target_id` is a `GENERATED ALWAYS AS IDENTITY` value,
local to one database; signing it would mean a node that RECONSTRUCTED its curated
overlay (rather than restoring an exact copy) could never match an entry back to its
row. `natural_key` is instead the canonical rendering of a row's own identity columns
-- immortal for a moiety, deterministically re-derived for a class or condition -- so
it is stable across databases. `target_id` survives on `release_manifest_entry` only
as an UNSIGNED convenience column; see `verify_release`'s docstring for the one place
this module still reads it, and why that does not reintroduce this paragraph's problem.

NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these modules.
"""
import datetime as dt
from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from drugref import keys, signatures, signing

# THE TWO CURATED TARGET KINDS A RELEASE COVERS -- a release is, by definition, a
# snapshot of drugref's OWN judgements, never of a rebuildable ingest projection (which
# has no stable row identity to snapshot at all -- see db/029's own note on
# ddi_candidate_pair). This is a scope decision belonging to this module, not a second
# copy of the target_kind -> table mapping: `signature_target_kind` remains the one
# place that mapping lives (`_target_table` below reads it), and 'release_manifest'
# itself is deliberately absent from this tuple -- a release cannot enumerate itself.
_CURATED_KINDS = ("curated_interaction", "curated_condition")

# THE SENTINEL A MANIFEST ENTRY'S DIGEST IS BUILT UNDER. See `enumerate_live`'s
# docstring for why this must be the identical value every time an entry digest is
# computed -- at publish time, and at every later verification -- rather than "now".
ENTRY_DIGEST_SIGNED_AT = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def _target_table(conn: psycopg.Connection, target_kind: str) -> tuple[str, str]:
    """`(target_table, pk_column)` for one `target_kind`, read from
    `signature_target_kind` -- the one home for this mapping (db/006's lesson,
    `signatures._target_kind_catalog`'s twin). Kept as this module's own small query
    rather than importing that function: it is private to `signatures.py`, and the
    query itself is a single SELECT against a seed table, not a second copy of the
    vocabulary it reads.
    """
    row = conn.execute(
        "SELECT target_table, pk_column FROM drugref.signature_target_kind "
        "WHERE target_kind = %s", (target_kind,)).fetchone()
    if row is None:
        raise signatures.UnknownTargetError(
            f"{target_kind!r} is not a signature target kind. The vocabulary lives in "
            "drugref.signature_target_kind; adding one is an INSERT there.")
    return row


def _natural_key_columns(conn: psycopg.Connection, table: str) -> tuple[str, ...]:
    """The natural-key column list `table`'s single-live trigger enforces, read from
    `pg_trigger.tgargs` rather than a second Python mapping -- the gates round's own
    precedent (tests/test_live_key_index_guard.py's `_single_live_tables` derives
    exactly this list, for the same reason). `forbid_multiple_live_assertions`'s
    arguments ARE the natural key (db/020's floor: the trigger enforces "at most one
    live row per this key", which is the overlay's own definition of one), so reading
    them back is the same fact db/020's migration already wrote down once, not a second
    guess at it. A hand-maintained `{"curated_interaction": (...), ...}` dict here would
    be exactly the second home db/006 found drifting, and would silently stop covering a
    ninth curated table the day its migration lands rather than the day its own tests
    run.
    """
    row = conn.execute(
        "SELECT encode(t.tgargs, 'escape') "
        "FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_proc p ON p.oid = t.tgfoid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'drugref' AND NOT t.tgisinternal "
        "AND p.proname = 'forbid_multiple_live_assertions' AND c.relname = %s",
        (table,)).fetchone()
    if row is None:
        raise ValueError(
            f"drugref.{table} carries no single-live natural-key trigger (db/020's "
            "floor), so it has no natural key this module can derive. Every table a "
            "release covers must carry one -- see _CURATED_KINDS.")
    # Postgres's 'escape' format writes a NUL argument terminator as the literal text
    # \000 (backslash, then three digit characters), NOT an actual NUL byte -- the same
    # split test_live_key_index_guard.py uses for the identical reason.
    return tuple(filter(None, row[0].split("\\000")))


def _render_natural_key(key_values) -> str:
    """The key columns' own rendered values, slash-joined -- spec 5.5's exact format:
    "the interaction's is its (subject_moiety_uuid, object_class_uuid, relationship)
    triple ... slash-joined". Safe because every natural-key column on every curated
    table is `NOT NULL` (db/029): `signing.render` never returns `None` for one, so a
    stray key value can never collapse the join into ambiguous adjacent slashes.
    """
    return "/".join(signing.render(v) for v in key_values)


def natural_key_of(conn: psycopg.Connection, target_kind: str, target_id: int) -> str:
    """One row's own natural key, canonically rendered. PUBLIC, and the ONE place this
    rendering happens -- `enumerate_live` computes the identical string inline (see its
    docstring) rather than calling back into this function per row, but both go through
    `_render_natural_key`, so a manifest built by one drugref and verified by another
    can never disagree about what a row's natural key is.
    """
    table, pk_column = _target_table(conn, target_kind)
    columns = _natural_key_columns(conn, table)
    row = conn.execute(
        sql.SQL("SELECT {cols} FROM drugref.{table} WHERE {pk} = %s").format(
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            table=sql.Identifier(table), pk=sql.Identifier(pk_column)),
        (target_id,)).fetchone()
    if row is None:
        raise signatures.UnknownTargetError(
            f"drugref.{table} has no row with {pk_column} = {target_id}.")
    return _render_natural_key(row)


@dataclass(frozen=True)
class ManifestEntry:
    """One row of a manifest's signed `--entries--` group (spec 5.5).

    `natural_key` is what the SIGNED group carries and what `verify_release` pairs on
    -- stable across databases because it is rendered from immortal or deterministic
    identity columns, never from a surrogate id. `target_id` is an UNSIGNED convenience
    pointer (db/030's own comment on `release_manifest_entry`): present so an operator
    can join an entry back to the local row it describes, absent from
    `manifest_payload`'s signed bytes entirely.
    """
    target_kind: str
    natural_key: str
    target_id: int
    payload_context: str
    payload_digest: bytes


def enumerate_live(conn: psycopg.Connection, *, signed_at: dt.datetime
                    ) -> list[ManifestEntry]:
    """Every LIVE row across both curated tables, as a `ManifestEntry`.

    EACH DIGEST GOES THROUGH `signatures.payload_for` -- THE SAME CODE PATH A ROW
    SIGNATURE USES -- so a manifest entry and a curator's `assertion_signature` over
    the identical row can never disagree about what that row's canonical bytes are;
    sharing the one function beats re-implementing "render this row's content fields"
    a second time here.

    `signed_at` IS A FIXED SENTINEL, NOT A REAL SIGNING MOMENT -- the one place this
    module's use of `payload_for` diverges from `signatures.py`'s own. A curator's
    signature genuinely IS made at an instant; a manifest ENTRY attests CONTENT, not an
    attestation -- "this row's fields hash to this digest", never "somebody signed this
    row now" (the manifest's real attestation moment is the OUTER `release_manifest/v1`
    payload's own `signed_at`, see `manifest_payload`). So this function takes
    `signed_at` from ITS CALLER rather than inventing one, and every caller passes
    `ENTRY_DIGEST_SIGNED_AT` -- the SAME constant every time, because `payload_for`
    always appends `signed_at` (it has no attestation-free mode) and an entry digest
    built at publish time must reproduce byte-for-byte when `verify_release` recomputes
    it later. The real wall clock here would make every unaltered row look "altered"
    the moment publish-time and verify-time disagree on what time it is. Same reasoning
    for `key_fingerprint=""`: a present empty string, naming no signer, because an
    entry digest names no signer.

    ORDERED, per target_kind then by the table's own primary key -- `canonical_payload`
    re-sorts group members by their own encoding regardless (the SIGNED bytes never
    depend on this), but a deterministic Python-level order keeps `publish`'s inserts
    and any test iterating this list reproducible.

    THE CATALOG LOOKUPS ARE HOISTED OUT OF THE PER-ROW LOOP, once per target_kind
    rather than once per row -- the same N+1 concern Task 7's review found in
    `signatures.verify_target` and fixed there, flagged in that module's own docstring
    as mattering here specifically ("Task 8's release verifier runs this across the
    whole overlay").
    """
    entries = []
    for target_kind in _CURATED_KINDS:
        table, pk_column = _target_table(conn, target_kind)
        key_columns = _natural_key_columns(conn, table)
        rows = conn.execute(
            sql.SQL("SELECT {pk}, {keys} FROM drugref.{table} "
                    "WHERE superseded_by IS NULL ORDER BY {pk}").format(
                pk=sql.Identifier(pk_column),
                keys=sql.SQL(", ").join(sql.Identifier(c) for c in key_columns),
                table=sql.Identifier(table))).fetchall()
        for row in rows:
            target_id, *key_values = row
            natural_key = _render_natural_key(key_values)
            context, payload = signatures.payload_for(
                conn, target_kind, target_id, key_fingerprint="",
                signed_at=signed_at)
            entries.append(ManifestEntry(
                target_kind, natural_key, target_id, context, signing.digest(payload)))
    return entries


def manifest_payload(conn: psycopg.Connection, *, release_tag: str, published_by: str,
                     published_at: dt.datetime, entries: list, upstream: list,
                     key_fingerprint: str, signed_at: dt.datetime) -> bytes:
    """The `release_manifest/v1` canonical payload (spec 5.5): the six scalars, plus
    the `--entries--` and `--upstream--` groups.

    `conn` IS UNUSED -- kept for the same reason every function in this module (and
    `signing.canonical_payload`'s callers throughout the codebase) takes it: this
    function builds bytes from ALREADY-SUPPLIED Python values, no row of its own to
    read, but sharing one call shape with `enumerate_live`/`publish` means a caller
    building a manifest by hand (as `test_a_row_whose_content_changed_is_an_ALTERATION`
    does) does not have to remember which functions in this module need a connection.

    `entries` carries `target_kind`, `natural_key`, `payload_context`,
    `payload_digest` per member -- NEVER `target_id` (see the module docstring and
    `ManifestEntry`'s). `upstream` is `(source, writer, release)` tuples, one per
    `loaded_release` row at publication -- `publish`'s snapshot of which upstream
    releases were loaded, a different question from `reviewed_against` (which release
    a JUDGEMENT was formed against).

    THE SCALAR FIELD ORDER IS READ FROM `signing.FIELD_LISTS`, not hand-typed here a
    second time: `RELEASE_MANIFEST_V1`'s order is the frozen one this payload must
    match, so building a `{name: value}` dict and iterating the frozen list is what
    makes a field this function forgets to compute a loud `KeyError` rather than a
    silently wrong (or silently reordered) payload.

    `entry_count`/`upstream_count` are STATED, not merely implied by the groups'
    member counts -- spec 5.5's reason: a group truncated at its end is otherwise
    detectable only by recomputing the whole digest, and a scalar count makes that one
    failure nameable on its own.
    """
    context = "release_manifest/v1"
    scalar_values = {
        "release_tag": release_tag,
        "published_by": published_by,
        "published_at": signing.render(published_at),
        "entry_count": str(len(entries)),
        "upstream_count": str(len(upstream)),
        "signer_key_fingerprint": key_fingerprint,
        "signed_at": signing.render(signed_at),
    }
    fields = [(name, scalar_values[name]) for name in signing.FIELD_LISTS[context]]
    entry_members = [
        [("target_kind", entry.target_kind), ("natural_key", entry.natural_key),
         ("payload_context", entry.payload_context),
         ("payload_digest", signing.render(entry.payload_digest))]
        for entry in entries]
    upstream_members = [
        [("source", source), ("writer", writer), ("release", release)]
        for source, writer, release in upstream]
    groups = [("entries", entry_members), ("upstream", upstream_members)]
    return signing.canonical_payload(context, fields, groups)


def publish(conn: psycopg.Connection, *, release_tag: str, published_by: str,
           private_key: bytes, key_fingerprint: str,
           published_at: dt.datetime | None = None,
           signed_at: dt.datetime | None = None) -> int:
    """Enumerate the live curated overlay, write it as a manifest, sign it. Returns the
    new `manifest_id`. Spec 8's operation, as a function: `drugref publish` is a thin
    CLI wrapper over this.

    `release_tag` IS STATED BY THE CALLER, NEVER DERIVED -- `ingest_run`'s release tags'
    own discipline (PROJECT-NOTES: "stated, never parsed from a filename"), applied here
    because a manifest's version string is exactly the same kind of fact: a human
    decision about what to call this snapshot, not something inferrable from the data
    itself. `release_manifest.release_tag`'s UNIQUE constraint is what turns a reused
    tag into a loud `UniqueViolation` rather than a silently overwritten history.

    `published_at`/`signed_at` DEFAULT TO THE PYTHON CLOCK (`now()`, not the
    database's), settable so a test can pin an instant -- unlike `keys.register`'s
    `status_from`, which defaults to the DATABASE's `now()` specifically to avoid a
    mis-set client clock dating a KEY REGISTRATION wrong. A manifest's publication
    instant has no such registry-integrity stake attached to it, and `signed_at` in
    particular must be a value the CALLER can hand straight to `sign` outside this
    function too (a hardware key, an air-gapped signer) -- so both are ordinary,
    overridable Python defaults.

    THE TWO WRITES -- `release_manifest` then its entries -- HAPPEN BEFORE THE
    SIGNATURE, because `signing.sign` needs `manifest_payload`'s bytes, and those bytes
    need `entries` to already be built (though not yet written; `enumerate_live` runs
    first). Nothing here commits: the caller owns the transaction, so a failure between
    the manifest row and its signature leaves nothing published in any state a reader
    outside this transaction can observe.
    """
    published_at = (published_at if published_at is not None
                    else dt.datetime.now(dt.timezone.utc))
    signed_at = (signed_at if signed_at is not None
                else dt.datetime.now(dt.timezone.utc))

    entries = enumerate_live(conn, signed_at=ENTRY_DIGEST_SIGNED_AT)
    upstream = [
        (source, writer, release) for source, writer, release in conn.execute(
            "SELECT source, writer, upstream_release FROM drugref.loaded_release "
            "ORDER BY source, writer").fetchall()]

    payload = manifest_payload(
        conn, release_tag=release_tag, published_by=published_by,
        published_at=published_at, entries=entries, upstream=upstream,
        key_fingerprint=key_fingerprint, signed_at=signed_at)

    manifest_id = conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING manifest_id",
        (release_tag, signing.digest(payload), len(entries),
         Jsonb([{"source": s, "writer": w, "release": r} for s, w, r in upstream]),
         published_by, published_at)).fetchone()[0]

    for entry in entries:
        conn.execute(
            "INSERT INTO drugref.release_manifest_entry (manifest_id, target_kind, "
            "natural_key, target_id, payload_context, payload_digest) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (manifest_id, entry.target_kind, entry.natural_key, entry.target_id,
             entry.payload_context, entry.payload_digest))

    signatures.record(
        conn, target_kind="release_manifest", target_id=manifest_id,
        payload_context="release_manifest/v1", payload=payload,
        key_fingerprint=key_fingerprint, signature=signing.sign(private_key, payload),
        signed_at=signed_at)

    return manifest_id


@dataclass(frozen=True)
class ManifestVerdict:
    """What one published release is worth, right now -- both halves of it.

    `signature` answers AUTHENTICITY: one of `signing.py`'s six verdict constants, for
    the `assertion_signature` row over the manifest itself. `dropped`/`added`/`altered`
    answer INTEGRITY: does the database still hold what the manifest enumerated. The
    two are independent on purpose --
    `test_a_row_whose_content_changed_is_an_ALTERATION` is the test that proves a VALID
    signature can sit beside a FALSE content claim, and a verifier that collapsed the
    two would report a tampered database as a bad signature.

    Each of `dropped`/`added`/`altered` is a list of `(target_kind, natural_key)` pairs
    -- never `target_id`, for `ManifestEntry`'s reason.
    """
    release_tag: str
    signature: str
    dropped: list
    added: list
    altered: list

    @property
    def is_intact(self) -> bool:
        """VALID signature, and nothing dropped, added or altered. The AND, not an OR:
        a database that matches the manifest byte-for-byte under a forged signature is
        not intact either -- it just means an attacker forged a manifest that happens
        to describe reality, which is not the same claim as "this is really what
        drugref published"."""
        return (self.signature == signing.VALID
                and not (self.dropped or self.added or self.altered))


class UnknownReleaseError(RuntimeError):
    """No `release_manifest` row names this `release_tag`. Raised rather than returning
    an empty/vacuous verdict -- `keys.NoLiveKeyError`'s precedent: silence is the worst
    answer a lookup can give, and a mistyped tag would otherwise read as "this release
    verifies fine" for having nothing to check."""


# THE PRECEDENCE `signing.verdict`'s OWN DOCSTRING NAMES (§7.1), reused rather than
# re-invented, for combining several `assertion_signature` rows into one verdict for
# the manifest as a whole. A manifest is signed exactly once by `publish`, so this is
# defensive rather than reachable through this module's own writer -- but a re-signed
# manifest (a rotated institutional key, say) is a real possibility this module must
# not crash on. WORST WINS: a consumer checking "is this release trustworthy" wants to
# know about the worst thing any recorded signature says, not whichever one happened to
# be recorded first or last.
_VERDICT_RANK = {
    signing.UNKNOWN_KEY: 0,
    signing.BAD_SIGNATURE: 1,
    signing.KEY_REVOKED_COMPROMISED: 2,
    signing.KEY_EXPIRED: 3,
    signing.VALID: 4,
}


def _verify_manifest_signature(conn: psycopg.Connection, manifest_id: int,
                               release_tag: str) -> str:
    """The manifest's own signature verdict -- built and checked HERE, not through
    `signatures.verify_target`, deliberately: that function is built for a "select
    these columns from one row" payload (`_row_content_fields`), and `entry_count`/
    `upstream_count` are DERIVED scalars while `--entries--`/`--upstream--` are GROUPS
    built from `release_manifest_entry`/`upstream_releases` -- none of that is a
    literal column on `release_manifest`, so `verify_target` raises `UndefinedColumn`
    the moment it tries (caught while writing this function, not guessed at). This
    instead rebuilds the payload the way `publish` built it, through `manifest_payload`.

    EVERYTHING ELSE MATCHES `verify_target`'s OWN RULES (spec 7.1), not a fork of them:
    `signing.verify`/`signing.verdict` decide validity and precedence identically;
    `algorithm` is checked before trusting `signing.verify` (Ed25519 only);
    `keys.live`/`keys.key_status` are looked up PER SIGNATURE, since two signatures
    over one manifest could legitimately name different keys (a rotation).
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
        "SELECT key_fingerprint, algorithm, signature, signed_at, signature_id "
        "FROM drugref.assertion_signature "
        "WHERE target_kind = 'release_manifest' AND target_id = %s "
        "ORDER BY signature_id", (manifest_id,)).fetchall()
    if not sig_rows:
        return signing.NO_SIGNATURE

    verdicts = []
    for key_fingerprint, algorithm, signature, signed_at, signature_id in sig_rows:
        if algorithm != signing.ED25519:
            raise signatures.UnsupportedAlgorithmError(
                f"assertion_signature {signature_id} was signed with {algorithm!r}, "
                "which this module cannot verify -- signing.verify only implements "
                f"{signing.ED25519}.")
        payload = manifest_payload(
            conn, release_tag=release_tag, published_by=published_by,
            published_at=published_at, entries=entries, upstream=upstream,
            key_fingerprint=key_fingerprint, signed_at=signed_at)
        key = keys.live(conn, key_fingerprint)
        status = keys.key_status(conn, key_fingerprint)
        signature_ok = key is not None and signing.verify(
            key.public_key, payload, signature)
        verdicts.append(signing.verdict(
            status, signature_ok=signature_ok, signed_at=signed_at))
    return min(verdicts, key=lambda v: _VERDICT_RANK[v])


def verify_release(conn: psycopg.Connection, release_tag: str) -> ManifestVerdict:
    """Check one published release's signature AND its content, against the database
    `conn` is connected to, right now.

    THE SIGNATURE HALF calls `_verify_manifest_signature` -- same RULES
    `signatures.verify_target` applies to a curated row (spec 7.1's precedence via the
    same `signing.verify`/`signing.verdict`), but not literally that function; see its
    own docstring for why a manifest's payload is not the "columns from one row" shape
    `verify_target` assumes.

    THE CONTENT HALF pairs `release_manifest_entry` against `enumerate_live`'s current
    answer BY `(target_kind, natural_key)`, never `target_id`, so a node that restored
    the curated overlay from a different loading path still matches every unaltered row
    correctly. A key in the manifest but not live now is DROPPED; live now but absent
    from the manifest is ADDED; a key in both is checked for a digest match --
    RECOMPUTED USING THE ENTRY'S OWN STORED `payload_context`, never the catalog's
    current one. This is Task 7's C1 fix, one layer up: re-deriving context is right
    when SIGNING and silently wrong for VERIFYING, since a later `/v2` would rebuild
    every historical entry's comparison under the new context and report it as altered.

    A NATURAL KEY SHARED BETWEEN TWO ROWS IS NOT ALWAYS "MATCHED", because a correction
    (curation.py's only way to revise a judgement) INSERTs a new row sharing the SAME
    natural key and supersedes the old one -- so "digest A published, digest B live"
    looks identical whether the published row was genuinely altered or was correctly
    superseded by a content-differing successor. `test_a_correction_is_an_addition_
    not_an_alteration` names the distinction: nothing is ever edited on this floor, so
    a correction must read as one DROP (the published row left the live set) plus one
    ADDITION (its successor joined it) -- reading it as ALTERED would claim the
    published row itself had been changed in place.

    The fact that tells them apart -- and the ONE place this function reads
    `target_id` for anything beyond `ManifestEntry`'s convenience column -- is whether
    the entry's own `target_id` is STILL the live representative of that natural key.
    Same row (unmodified identity): a digest mismatch can only be content drift, an
    alteration. Different row: the published row demonstrably left the live set through
    supersession, so it is a drop, paired with whatever now holds the key as an add.
    This is a LOCAL, same-database check -- "does the row THIS database calls
    target_id N still stand", never "is target_id N the same across databases" -- so it
    does not reintroduce target_id as a cross-node identity: on a node that restored the
    exact data `publish` ran against, an unaltered row's own target_id is still itself
    and this branch never fires; on a node whose overlay was independently
    reconstructed, no target_id from the manifest resolves to anything meaningful, and
    this branch reports the conservative, safe answer -- drop-plus-add -- rather than a
    false "altered" claim.
    """
    row = conn.execute(
        "SELECT manifest_id FROM drugref.release_manifest WHERE release_tag = %s",
        (release_tag,)).fetchone()
    if row is None:
        raise UnknownReleaseError(
            f"no release_manifest row for release_tag={release_tag!r}. Check "
            "`SELECT release_tag FROM drugref.release_manifest` -- a mistyped tag "
            "would otherwise read as a release with nothing wrong.")
    manifest_id = row[0]

    signature = _verify_manifest_signature(conn, manifest_id, release_tag)

    manifest_entries = {
        (target_kind, natural_key): (target_id, payload_context, payload_digest)
        for target_kind, natural_key, target_id, payload_context, payload_digest in
        conn.execute(
            "SELECT target_kind, natural_key, target_id, payload_context, "
            "payload_digest FROM drugref.release_manifest_entry "
            "WHERE manifest_id = %s", (manifest_id,)).fetchall()
    }
    live_entries = {
        (entry.target_kind, entry.natural_key): entry
        for entry in enumerate_live(conn, signed_at=ENTRY_DIGEST_SIGNED_AT)
    }

    manifest_keys = set(manifest_entries)
    live_keys = set(live_entries)

    dropped = sorted(manifest_keys - live_keys)
    added = sorted(live_keys - manifest_keys)
    altered = []

    for key in sorted(manifest_keys & live_keys):
        target_id_m, payload_context, digest_m = manifest_entries[key]
        live_entry = live_entries[key]
        if live_entry.target_id != target_id_m:
            # A different row now holds this natural key: a correction happened. The
            # published row was superseded, never edited -- see this function's own
            # docstring.
            dropped.append(key)
            added.append(key)
            continue
        _, payload = signatures.payload_for(
            conn, key[0], live_entry.target_id, key_fingerprint="",
            signed_at=ENTRY_DIGEST_SIGNED_AT, payload_context=payload_context)
        if signing.digest(payload) != bytes(digest_m):
            altered.append(key)

    dropped.sort()
    added.sort()

    return ManifestVerdict(release_tag=release_tag, signature=signature,
                           dropped=dropped, added=added, altered=altered)

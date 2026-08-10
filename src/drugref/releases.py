# src/drugref/releases.py
"""Release manifests: enumerate the live curated overlay and publish it as a signed
manifest (db/030, spec 5.5/8). Verification lives in `release_verification.py` --
see that module's docstring for why the split and for the one direction this module
is imported back across it.

ONE MECHANISM, BOTH LAYERS. `signatures.py` signs one curated row; this module signs
a whole RELEASE -- a snapshot enumeration of every live curated row at one moment --
through the identical `assertion_signature` table, with `target_kind =
'release_manifest'`. A manifest needed nothing new at the signing layer, only a new
kind of target and a new writer for it (db/030's opening comment).

A CONTENT MANIFEST, NOT A TRANSPORT SIGNATURE. A signature over the bytes drugref
shipped dies the moment those bytes are loaded into a database -- nothing is left to
re-hash against. A manifest instead ENUMERATES: for every live curated row at
publication, its natural key and content digest, so a later verification can recompute
the SAME digest from a row's current content and compare. Because it enumerates,
verification is BIDIRECTIONAL and catches omission as well as alteration -- something a
transport signature could never see.

NATURAL KEY, NEVER `target_id`. `target_id` is a `GENERATED ALWAYS AS IDENTITY` value,
local to one database; signing it would mean a node that RECONSTRUCTED its curated
overlay (rather than restoring an exact copy) could never match an entry back to its
row. `natural_key` is instead the canonical rendering of a row's own identity columns
-- immortal for a moiety, deterministically re-derived for a class or condition -- so
it is stable across databases. `target_id` survives on `release_manifest_entry` only
as an UNSIGNED convenience column -- spec 5.5, db/030's own comment: "nothing verifies
against it".

NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these modules.
"""
import datetime as dt
from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from drugref import signatures, signing

# THE TWO CURATED TARGET KINDS A RELEASE COVERS -- a release is, by definition, a
# snapshot of drugref's OWN judgements, never of a rebuildable ingest projection (which
# has no stable row identity to snapshot at all -- see db/029's own note on
# ddi_candidate_pair). This is a scope decision belonging to this module, not a second
# copy of the target_kind -> table mapping: `signature_target_kind` remains the one
# place that mapping lives (`signatures._target_kind_catalog` is the one function that
# reads it, and this module calls it), and 'release_manifest' itself is deliberately
# absent from this tuple -- a release cannot enumerate itself.
_CURATED_KINDS = ("curated_interaction", "curated_condition")

# THE SENTINEL A MANIFEST ENTRY'S DIGEST IS BUILT UNDER. See `enumerate_live`'s
# docstring for why this must be the identical value every time an entry digest is
# computed -- at publish time, and at every later verification -- rather than "now".
ENTRY_DIGEST_SIGNED_AT = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def _render_natural_key(key_values) -> str:
    """The key columns' own rendered values, slash-joined -- spec 5.5's exact format:
    "the interaction's is its (subject_moiety_uuid, object_class_uuid, relationship)
    triple ... slash-joined". The ONE join site, called by both `natural_key_of` and
    `enumerate_live`, so the two can never disagree about the separator or the order.

    THE HAZARD A SLASH-JOIN CARRIES IS AN EMBEDDED SLASH, not a missing value, and an
    earlier version of this comment named the wrong one. A `None` cannot arrive
    silently: every natural-key column on every curated table is `NOT NULL` (db/029),
    and even if one were not, `signing.render(None)` returns `None` and `str.join`
    raises `TypeError` immediately -- loud, never ambiguous.

    A `/` INSIDE a value is the real ambiguity, since `a/b` + `c` and `a` + `b/c` render
    identically. No key column holds one TODAY, but note how weak the guard actually is,
    because an earlier version of this paragraph overstated it: `curated_condition`'s
    key is two UUIDs and cannot, but `curated_interaction`'s third column,
    `relationship`, is NOT CHECK-constrained -- it is a FOREIGN KEY into
    `drugref.ci_axis(relationship)`, whose primary key is plain `text` with no shape
    constraint of any kind (db/006). `CI_MoA` and `CI_PE` have no slash in them; a
    slash-bearing axis is therefore ONE ORDINARY INSERT into a seed table away, not a
    migration away, and nothing in the schema would object.

    THE SIGNED BYTES ARE NOT AT RISK EITHER WAY, which is why this is a note rather than
    a guard: `canonical_payload` length-prefixes this whole string as ONE field, so an
    ambiguous key can confuse PAIRING (two different rows rendering one key string,
    which `verify_release` would report as drop+add) but can never forge a field
    boundary. If a slash-bearing `relationship` is ever admitted, the fix is to escape
    here and mint a `/v2` -- changing this rendering under `/v1` would re-key every
    published manifest, which is the C1 defect one door over.
    """
    return "/".join(signing.render(v) for v in key_values)


def natural_key_of(conn: psycopg.Connection, target_kind: str, target_id: int, *,
                   payload_context: str | None = None) -> str:
    """One row's own natural key, canonically rendered. PUBLIC, and one of the two
    places this rendering happens -- `enumerate_live` derives the identical string from
    fields it has already fetched for a different reason (see its own docstring)
    rather than calling back into this function per row, but both read the SAME frozen
    column list and both join through `_render_natural_key`, so a manifest built by one
    drugref and verified by another can never disagree about a row's natural key.

    `payload_context` IS AN OVERRIDE with `signatures.payload_fields`' exact meaning:
    `None` reads today's context from `signature_target_kind` (right when a key is being
    minted for a manifest about to be published), an explicit value pins a past one
    (right when reproducing the key a published entry recorded). Which COLUMNS a context
    names is frozen in `signing.NATURAL_KEY_COLUMNS` -- see that constant for why
    reading them back out of `pg_trigger` instead was a measured defect.
    """
    table, pk_column, current_context = signatures._target_kind_catalog(
        conn, target_kind)
    context = payload_context if payload_context is not None else current_context
    columns = signing.NATURAL_KEY_COLUMNS[context]
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

    `natural_key` is what the SIGNED group carries and what verification pairs on --
    stable across databases because it is rendered from immortal or deterministic
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


def enumerate_live(conn: psycopg.Connection, *,
                    signed_at: dt.datetime = ENTRY_DIGEST_SIGNED_AT,
                    natural_key_contexts: dict[str, str] | None = None
                    ) -> list[ManifestEntry]:
    """Every LIVE row across both curated tables, as a `ManifestEntry`.

    ONLY LIVE ROWS -- `WHERE superseded_by IS NULL` below is load-bearing and is not
    merely tidy. Without it a corrected natural key contributes TWO entries, the old
    and the new, and `verify_release` (which indexes this list by natural key) would
    silently keep whichever came last, making the answer depend on a `SELECT`'s row
    order. A manifest is a snapshot of what drugref asserts NOW, and a superseded row
    is precisely what drugref no longer asserts.

    DEFAULTS TO `ENTRY_DIGEST_SIGNED_AT` rather than requiring the caller to name it
    every time (an earlier draft of this function made it mandatory) -- a required
    parameter whose one correct value is a module constant is a footgun with a
    docstring instead of a safe default: passing anything else silently builds a
    manifest whose entries can never reproduce their own digest again. `signed_at`
    stays a parameter (not hard-coded inline) so a caller that genuinely needs a
    different value -- there is none today -- is not blocked from supplying one, and so
    the two real callers (`publish`, `release_verification.verify_release`) both read
    as using the SAME value rather than each spelling out the same datetime literal.

    EACH DIGEST IS BUILT FROM THE SAME COLUMNS `signatures.py` SIGNS A ROW FROM, via
    the module's own hoisted catalog/row-read helpers
    (`signatures._target_kind_catalog`, `signatures._row_content_fields`) rather than
    the public `signatures.payload_for` -- deliberately, a correction from an earlier
    draft that called `payload_for` once per row. `payload_for` is a one-off
    convenience wrapper: every call re-runs `_target_kind_catalog` (a
    `signature_target_kind` SELECT) AND `_row_content_fields` (the row's own SELECT)
    from scratch, which is fine for signing one row and ruinous for enumerating an
    entire overlay -- several `execute()` calls per row before this fix (the exact
    figure this comment once quoted was never recorded anywhere, so it is not restated
    as a number nothing can check).
    `signatures.verify_target` solved the identical problem for its own loop (Task 7's
    review) by hoisting the SAME two private helpers, and its own docstring names this
    function as the reason it mattered ("Task 8's release verifier runs this across
    the whole overlay") -- so this reuses that intended path rather than re-deriving a
    third way to build a row's content fields.

    THE NATURAL KEY IS A SUBSET OF THOSE SAME CONTENT FIELDS -- `_row_content_fields`
    already returns every frozen field, ALREADY RENDERED, and a curated table's natural
    key columns are always among them (a row's identity is always part of what gets
    signed; `signing.NATURAL_KEY_COLUMNS` and `signing.FIELD_LISTS` have never disagreed
    on this, and a KeyError below is exactly how that would be caught if they ever did).
    So the natural key is read straight out of that same field list rather than issued
    as a second SELECT -- one row read, not two, per entry.

    WHICH COLUMNS MAKE THE KEY IS FROZEN, AND WHICH FROZEN LIST APPLIES IS THE CALLER'S
    CHOICE. `natural_key_contexts` maps `target_kind -> payload_context` and defaults to
    `None`, meaning "use the catalog's CURRENT context for each kind" -- right when
    PUBLISHING, because a manifest being minted now records today's keys.
    `verify_release` passes the contexts it read off the manifest's OWN entries, because
    pairing a past recording against today's columns is the C1 defect
    `signing.NATURAL_KEY_COLUMNS` exists to close: widen a curated table's single-live
    trigger by one column and every live key re-renders, pairs with nothing, and an
    untouched database reports 100% churn. This is `signatures.payload_fields`'
    `payload_context` override, one layer up and one question over.

    NOTE THE ASYMMETRY WITH THE DIGEST, which is deliberate: the digest below is still
    built under the CATALOG's current context, because `verify_release` recomputes it
    under an entry's stored context itself when the two differ (and reuses this one when
    they agree -- review round 2's C5). Only the natural key has to be reproduced here,
    because only the natural key is what the two sides are PAIRED by: get it wrong and
    there is no entry to compare a digest against at all.

    `signed_at` IS A FIXED SENTINEL, NOT A REAL SIGNING MOMENT -- the one place this
    module's construction diverges from `signatures.py`'s own. A curator's signature
    genuinely IS made at an instant; a manifest ENTRY attests CONTENT, not an
    attestation -- "this row's fields hash to this digest", never "somebody signed this
    row now" (the manifest's real attestation moment is the OUTER `release_manifest/v1`
    payload's own `signed_at`, see `manifest_payload`). An entry digest built at publish
    time must reproduce byte-for-byte when verification recomputes it later, which is
    only possible if every caller uses the identical constant. Same reasoning for
    `key_fingerprint=""` below: a present empty string, naming no signer, because an
    entry digest names no signer.

    ORDERED, per target_kind then by the table's own primary key -- `canonical_payload`
    re-sorts group members by their own encoding regardless (the SIGNED bytes never
    depend on this), but a deterministic Python-level order keeps `publish`'s inserts
    and any test iterating this list reproducible.
    """
    entries = []
    for target_kind in _CURATED_KINDS:
        table, pk_column, context = signatures._target_kind_catalog(conn, target_kind)
        key_context = (natural_key_contexts or {}).get(target_kind, context)
        # AN UNUSABLE REQUESTED CONTEXT FALLS BACK, IT NEVER RAISES -- see
        # `signing.entry_context_is_reproducible` for what "unusable" means and for the
        # traceback this replaced. Falling back to the CURRENT context (rather than
        # skipping the kind) is what keeps the `added` direction honest: a skipped kind
        # would drop its live rows out of the enumeration silently, so a manifest that
        # omits a live row would stop reporting it.
        if not signing.entry_context_is_reproducible(key_context, context):
            key_context = context
        key_columns = signing.NATURAL_KEY_COLUMNS[key_context]
        rows = conn.execute(
            sql.SQL("SELECT {pk} FROM drugref.{table} "
                    "WHERE superseded_by IS NULL ORDER BY {pk}").format(
                pk=sql.Identifier(pk_column), table=sql.Identifier(table))).fetchall()
        for (target_id,) in rows:
            fields = signatures._row_content_fields(
                conn, table, pk_column, target_id, context)
            field_map = dict(fields)
            natural_key = _render_natural_key(field_map[c] for c in key_columns)
            full_fields = fields + [
                ("signer_key_fingerprint", ""),
                ("signed_at", signing.render(signed_at))]
            payload = signing.canonical_payload(context, full_fields)
            entries.append(ManifestEntry(
                target_kind, natural_key, target_id, context, signing.digest(payload)))
    return entries


def manifest_payload(conn: psycopg.Connection, *, release_tag: str, published_by: str,
                     published_at: dt.datetime, entries: list, upstream: list,
                     key_fingerprint: str, signed_at: dt.datetime,
                     payload_context: str = "release_manifest/v1") -> bytes:
    """The `release_manifest/v1` (or a later version's) canonical payload (spec 5.5):
    the SEVEN scalars (five manifest facts plus the two attestation fields), plus
    the `--entries--` and `--upstream--` groups.

    `payload_context` IS A REAL, OVERRIDABLE PARAMETER and BOTH production callers pass
    it explicitly -- review round 2's C2: the first draft hard-coded the literal string
    `"release_manifest/v1"` with no way to build (or, critically, to REBUILD while
    VERIFYING) a payload under any other context, which is Task 7's C1 defect
    reintroduced in a different shape. `release_verification._verify_manifest_signature`
    passes the value it read back from the specific `assertion_signature` row it is
    checking, never assuming "today's" -- the same reconstruct-the-past discipline
    `signatures.payload_for` already applies one layer down -- and `publish` passes the
    catalog's CURRENT value for `release_manifest` rather than this default. The default
    therefore serves hand-built callers only (tests planting a deliberately malformed
    manifest), which is why it is kept rather than made required: making it mandatory
    would force every such caller to restate the same literal.

    `conn` IS UNUSED -- kept for the same reason every DATABASE-FACING function in
    this module (and
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

    THE SCALAR FIELD ORDER IS READ FROM `signing.FIELD_LISTS[payload_context]`, not
    hand-typed here a second time: the frozen order for that context is what this
    payload must match, so building a `{name: value}` dict and iterating the frozen
    list is what makes a field this function forgets to compute a loud `KeyError`
    rather than a silently wrong (or silently reordered) payload.

    `entry_count`/`upstream_count` are STATED, not merely implied by the groups'
    member counts -- spec 5.5's reason: a group truncated at its end is otherwise
    detectable only by recomputing the whole digest, and a scalar count makes that one
    failure nameable on its own.
    """
    scalar_values = {
        "release_tag": release_tag,
        "published_by": published_by,
        "published_at": signing.render(published_at),
        "entry_count": str(len(entries)),
        "upstream_count": str(len(upstream)),
        "signer_key_fingerprint": key_fingerprint,
        "signed_at": signing.render(signed_at),
    }
    fields = [(name, scalar_values[name])
             for name in signing.FIELD_LISTS[payload_context]]
    entry_members = [
        [("target_kind", entry.target_kind), ("natural_key", entry.natural_key),
         ("payload_context", entry.payload_context),
         ("payload_digest", signing.render(entry.payload_digest))]
        for entry in entries]
    upstream_members = [
        [("source", source), ("writer", writer), ("release", release)]
        for source, writer, release in upstream]
    groups = [("entries", entry_members), ("upstream", upstream_members)]
    return signing.canonical_payload(payload_context, fields, groups)


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

    THE `release_manifest` ROW IS WRITTEN BEFORE THE SIGNATURE because
    `signatures.record` needs `manifest_id` as its `target_id`, and that identity value
    does not exist until the INSERT returns it. The ENTRY rows could honestly go either
    side; they sit here so a manifest and its entries land as one contiguous block.
    (An earlier version of this paragraph said the writes precede the signature because
    `signing.sign` needs bytes built from `entries` -- which its own parenthetical then
    conceded are needed "though not yet written". That is the reason `enumerate_live`
    and `manifest_payload` run before `sign`; it is not a reason to write either table
    first.) Nothing here commits: the caller owns the transaction, so a failure between
    the manifest row and its signature leaves nothing published in any state a reader
    outside this transaction can observe.

    THE MANIFEST'S OWN CONTEXT IS READ FROM `signature_target_kind`, NOT TYPED HERE.
    Both the payload's context and the recorded signature's used to be the literal
    `'release_manifest/v1'`, which made the catalog's value for that kind dead: minting
    a `/v2` there changed nothing, and `publish` would have gone on signing under `/v1`
    while every other reader believed `/v2`. The other two kinds have always taken their
    context from the catalog (`signatures.payload_fields` does it for them); this is the
    third doing the same, and it is what `test_publish_signs_under_the_catalogs_context`
    now pins. Both uses read the SAME local, so the bytes and the row can never name
    different contexts.
    """
    published_at = (published_at if published_at is not None
                    else dt.datetime.now(dt.timezone.utc))
    signed_at = (signed_at if signed_at is not None
                else dt.datetime.now(dt.timezone.utc))

    _table, _pk_column, manifest_context = signatures._target_kind_catalog(
        conn, "release_manifest")
    entries = enumerate_live(conn, signed_at=ENTRY_DIGEST_SIGNED_AT)
    upstream = [
        (source, writer, release) for source, writer, release in conn.execute(
            "SELECT source, writer, upstream_release FROM drugref.loaded_release "
            "ORDER BY source, writer").fetchall()]

    payload = manifest_payload(
        conn, release_tag=release_tag, published_by=published_by,
        published_at=published_at, entries=entries, upstream=upstream,
        key_fingerprint=key_fingerprint, signed_at=signed_at,
        payload_context=manifest_context)

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
        payload_context=manifest_context, payload=payload,
        key_fingerprint=key_fingerprint, signature=signing.sign(private_key, payload),
        signed_at=signed_at)

    return manifest_id

"""Deterministic minting of drugref's immortal substance identifiers.

drugref mints its OWN moiety UUID rather than keying on a name (principle 2:
identity is a claim, never the name). The UUID is derived deterministically
(UUIDv5) from the moiety's UNII, so two independent drugref instances ingesting
the same UNII release derive the SAME UUID with zero coordination.

Immortality scope: the UUID is a pure function of the UNII, re-derived on every
ingest. It therefore survives churn in EVERY OTHER identifier (RxCUI, CAS, name,
...), which attach as new claims and never re-key. The one thing it does NOT
survive is a change to the UNII itself -- UNII is designed to be immortal, so
this is acceptable for slice 1, but a real UNII correction would mint a new
moiety and orphan the old one. Detecting that (structural re-key by InChIKey) is
tracked as a follow-up, not solved here.
"""
import uuid

# Namespaces are derived from the domain name (not magic literals) so they are
# self-documenting and reproducible. Per-level namespaces guarantee a moiety and
# a future salt/class derived from the same source string can never collide.
_DRUGREF_ROOT = uuid.uuid5(uuid.NAMESPACE_DNS, "drugref.org")
MOIETY_NAMESPACE = uuid.uuid5(_DRUGREF_ROOT, "moiety")
CLASS_NAMESPACE = uuid.uuid5(_DRUGREF_ROOT, "class")


def mint_moiety_uuid(unii: str) -> uuid.UUID:
    """Derive the immortal moiety UUID from an active moiety's UNII.

    Deterministic: same UNII -> same UUID, always, everywhere. Because it is a
    pure function of the UNII, callers may re-derive it on every ingest and get
    the registry's existing UUID back for free -- no lookup needed -- as long as
    the UNII is unchanged (see the module docstring on the UNII-change caveat).
    """
    key = f"UNII:{unii.strip().upper()}"
    return uuid.uuid5(MOIETY_NAMESPACE, key)


# The key prefix each classification authority contributes to a class UUID.
#
# Why an explicit table rather than just upper-casing the source name: MED-RT's
# UUIDs were minted with the unhyphenated "MEDRT:" prefix, while the source is
# spelled "MED-RT" everywhere else (ingest_run.source, clear_source_edges). Those
# two spellings MUST resolve to the same key or a rebuild silently re-keys every
# MED-RT class and orphans every edge pointing at the old UUIDs. Stripping
# punctuation generically would do that too, but would also risk merging two
# genuinely different future sources, so the aliasing is stated one entry at a
# time. Anything absent is used as-is (upper-cased), which is what MeSH does.
_SOURCE_KEY_PREFIX = {
    "MED-RT": "MEDRT",   # frozen: the prefix slice 2a's class_uuids were minted with
}


def mint_class_uuid(source: str, code: str) -> uuid.UUID:
    """Derive a classification class's immortal UUID from (authority, code).

    The code is the authority's own stable concept identifier -- MED-RT's NUI (an
    N-prefixed alphanumeric, e.g. "N0000175722"), or a MeSH descriptor UI (e.g.
    "D000894") -- so it is the natural key to derive from. Deterministic: same
    (source, code) -> same UUID, everywhere, with zero coordination between
    drugref instances.

    The SOURCE is part of the key, not decoration: the registry now holds classes
    from more than one authority, and without it an accidental code collision
    between two of them would silently merge two unrelated classes into one row.

    Unlike a moiety -- which is pinned on first sight because a UNII correction
    would otherwise re-key it -- a class UUID is a pure function of its inputs and
    is simply re-derived on every ingest. That is what lets a source's projection
    be dropped and rebuilt wholesale (see drugref/classes.py) while every class
    comes back with exactly the UUID it had before. Immortality across a *code*
    change is out of scope, the same caveat the moiety spine records for a UNII.
    """
    src = source.strip().upper()
    key = f"{_SOURCE_KEY_PREFIX.get(src, src)}:{code.strip().upper()}"
    return uuid.uuid5(CLASS_NAMESPACE, key)

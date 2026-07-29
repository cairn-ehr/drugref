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
QUESTION_NAMESPACE = uuid.uuid5(_DRUGREF_ROOT, "question")
LOCAL_PRODUCT_NAMESPACE = uuid.uuid5(_DRUGREF_ROOT, "local_product")
CONDITION_NAMESPACE = uuid.uuid5(_DRUGREF_ROOT, "condition")


def mint_moiety_uuid(unii: str) -> uuid.UUID:
    """Derive the immortal moiety UUID from an active moiety's UNII.

    Deterministic: same UNII -> same UUID, always, everywhere. Because it is a
    pure function of the UNII, callers may re-derive it on every ingest and get
    the registry's existing UUID back for free -- no lookup needed -- as long as
    the UNII is unchanged (see the module docstring on the UNII-change caveat).
    """
    key = f"UNII:{unii.strip().upper()}"
    return uuid.uuid5(MOIETY_NAMESPACE, key)


# The ONE canonical spelling of each authority's name, keyed by its normalised
# (stripped, upper-cased) form. This is the single source of truth for "how is
# this authority spelled", and both things that must agree derive from it: the
# class_uuid key (below) and the string stored in substance_class.source (via
# classes.upsert_class). Keeping them on the same canonicalisation is what stops
# "MeSH" and "MESH" minting one UUID while being stored as two different strings
# -- a silent split that would make a per-source rebuild query miss half its rows.
_SOURCE_CANONICAL = {
    "MED-RT": "MED-RT",
    "MEDRT":  "MED-RT",   # the UUID-key spelling; must fold into the display one
    "MESH":   "MeSH",
}

# The key prefix each canonical authority contributes to a class UUID.
#
# Why an explicit table rather than just upper-casing the name: MED-RT's UUIDs
# were minted with the unhyphenated "MEDRT:" prefix, while the authority is
# spelled "MED-RT" everywhere else (ingest_run.source, clear_source_edges). Those
# two spellings MUST resolve to the same key or a rebuild silently re-keys every
# MED-RT class and orphans every edge pointing at the old UUIDs. Stripping
# punctuation generically would do that too, but would also risk merging two
# genuinely different future sources, so the aliasing is stated one entry at a
# time. Anything absent is used as-is (upper-cased), which is what MeSH does.
_SOURCE_KEY_PREFIX = {
    "MED-RT": "MEDRT",   # frozen: the prefix slice 2a's class_uuids were minted with
}


def canonical_source(source: str) -> str:
    """The one canonical spelling of an authority's name ("MED-RT", "MeSH", ...).

    Every incidental spelling of an authority -- the hyphen-less "MEDRT" the UUID
    key uses, a case or whitespace slip in an XML feed -- folds to a single string
    here, so the value stored in substance_class.source and the value the
    class_uuid is minted from can never diverge. An unrecognised source is returned
    stripped and UPPER-CASED -- the same fold mint_class_uuid applies when building
    the key -- so even an authority nobody has added to _SOURCE_CANONICAL yet can
    only ever reach the table under one spelling.

    That fallback matters more than it looks. A new authority is admitted by
    widening a CHECK in a migration and adding an entry here in a separate edit, so
    there is always a window where the database accepts a source this table does
    not know. While the fallback preserved case, three spellings of one such source
    minted a single class_uuid but were stored as three different strings -- and
    upsert_class's ON CONFLICT does not rewrite the stored `source`, so whichever
    arrived first stuck and a per-source rebuild silently missed rows it owned.
    """
    stripped = source.strip()
    return _SOURCE_CANONICAL.get(stripped.upper(), stripped.upper())


# Identity schemes whose values are CODES with a defined case, versus schemes
# whose values are human-readable labels. A code must be folded before storage --
# the moiety UUID is minted from the upper-cased UNII, so storing the raw spelling
# lets the identifier the UUID derives from sit in the table under a form no
# exact-match lookup (moieties_by_scheme, chebi.py's InChIKey join) will find, and
# two cases of one code insert two claims for one fact. A LABEL must not be folded:
# 'INN' caches the display name, which is deliberately lower-case.
_UPPERCASE_SCHEMES = frozenset({"UNII", "INCHIKEY", "CHEBI"})


def canonical_claim_value(scheme: str, value: str) -> str:
    """The one spelling an identity claim's value is stored under.

    Pure and total: every scheme is stripped, and the code-valued ones are also
    upper-cased. Applied once, in claims.add_claim, so no caller has to remember
    which schemes are case-bearing -- the same reason canonical_source exists for
    authority names.
    """
    cleaned = value.strip()
    return cleaned.upper() if scheme in _UPPERCASE_SCHEMES else cleaned


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
    canon = canonical_source(source)
    prefix = _SOURCE_KEY_PREFIX.get(canon, canon.upper())
    key = f"{prefix}:{code.strip().upper()}"
    return uuid.uuid5(CLASS_NAMESPACE, key)


def mint_condition_uuid(source: str, code: str) -> uuid.UUID:
    """Derive a condition's immortal UUID from (authority, record code).

    A CONDITION is the patient state a drug must not be given in -- a disease, but
    also pregnancy, lactation or a procedure (slice-5b spec §4.3). `code` is the
    authority's own stable record identifier: a MeSH DescriptorUI ("D004827") or a
    SupplementalRecordUI ("C536778").

    Derived and re-derived on every ingest, never pinned -- the same discipline as
    mint_class_uuid and deliberately unlike mint_moiety_uuid. That is what lets the
    condition registry be dropped and rebuilt while every surviving condition comes
    back with exactly the UUID it had before.

    A SEPARATE NAMESPACE FROM CLASS_NAMESPACE, and that is load-bearing: a MeSH
    descriptor can be both a PA class (slice 2b) and a condition, and sharing a
    namespace would mint ONE UUID for two different kinds of thing, silently
    joining a condition row to a class row through either edge table.
    """
    canon = canonical_source(source)
    key = f"{canon.upper()}:{code.strip().upper()}"
    return uuid.uuid5(CONDITION_NAMESPACE, key)


# ---- local-tier product identity (slice 8a) --------------------------------


def normalise_name(name: str) -> str:
    """The one fold applied to any human-readable substance name.

    Strip, lower-case, collapse internal whitespace. It lives here beside
    canonical_source and canonical_claim_value because it is the same KIND of
    thing: the single spelling two independently-produced strings must agree on
    before they can be compared.

    Two consumers depend on that agreement. The INN identity claim is stored
    lower-case (it is a display label, so _UPPERCASE_SCHEMES deliberately excludes
    it), and PBS publishes Title-case drug names -- 1,085 of 1,086 distinct names
    in the 2026-07 release. If either side folded differently, the local-tier
    bridge would silently match nothing at all, which is the failure mode
    canonical_source exists to prevent for authority names.
    """
    return " ".join(name.strip().lower().split())


def mint_local_product_uuid(jurisdiction: str, source: str, code: str) -> uuid.UUID:
    """Derive a local-tier product's UUID from (jurisdiction, source, code).

    Deterministic and RE-DERIVED on every ingest, never pinned -- the same
    discipline as mint_class_uuid and deliberately unlike mint_moiety_uuid. That
    is what lets the local tier be dropped and rebuilt monthly while every
    surviving product comes back with exactly the UUID it had before.

    Jurisdiction and source are part of the key, not decoration: a second
    jurisdiction's identically-numbered item would otherwise collapse onto the
    same row. `code` is the upstream item-instance id (PBS li_item_id), which is
    unique per row upstream -- unlike the PBS Item Code, which covers many brands.
    """
    key = f"{jurisdiction.strip().upper()}:{source.strip().upper()}:{code.strip()}"
    return uuid.uuid5(LOCAL_PRODUCT_NAMESPACE, key)


# ---- open-question identity (Plan A) ---------------------------------------


def mint_question_uuid(gap_kind: str, gap_key: str) -> uuid.UUID:
    """Derive an open question's immortal UUID from the gap that produced it.

    Deterministic, so re-deriving the whole registry on every ingest yields the
    same UUIDs and the derived half stays a rebuildable projection while the
    curated half (question_state, question_source_check, question_evidence) keys
    off it and is append-only.

    `gap_kind` is the kind of gap; `gap_key` is the natural key of the thing the
    question is ABOUT, in the frozen `SCHEME:value` form (`CLASS:<uuid>`,
    `MOIETY:<uuid>`, `RXNORM_IN:<rxcui>`), with '/' joining compound keys. BOTH
    are inputs to the UUID and both are therefore frozen: changing either format
    re-mints every question and breaks every reference an external tool holds.

    The ':' joiner only separates the two fields if gap_kind cannot contain one:
    kind 'a:b' with key 'c' and kind 'a' with key 'b:c' both build "a:b:c" and
    would mint ONE question for two unrelated gaps. gap_key must keep its colons
    (they are the CLASS:/MOIETY:/RXNORM_IN: scheme prefixes), so the constraint
    goes on gap_kind -- drugref's own closed vocabulary, which has no use for one.
    Rejected here rather than validated at the call site, because a silent merge
    of two questions is invisible downstream and permanent once cited.
    """
    kind = gap_kind.strip()
    if ":" in kind:
        raise ValueError(
            f"gap_kind may not contain ':' (got {gap_kind!r}): it is the joiner "
            "separating gap_kind from gap_key, so a colon here would let two "
            "distinct gaps mint the same question_uuid")
    return uuid.uuid5(QUESTION_NAMESPACE, f"{kind}:{gap_key.strip()}")

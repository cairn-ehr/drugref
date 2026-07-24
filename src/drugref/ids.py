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


def mint_class_uuid(nui: str) -> uuid.UUID:
    """Derive a classification class's immortal UUID from its MED-RT NUI.

    The NUI (an N-prefixed alphanumeric, e.g. "N0000175722") is MED-RT's own
    stable concept identifier -- what MED-RT calls the "code in source" -- so it
    is the natural key to derive from. Deterministic: same NUI -> same UUID,
    everywhere, with zero coordination between drugref instances.

    Unlike a moiety -- which is pinned on first sight because a UNII correction
    would otherwise re-key it -- a class UUID is a pure function of the NUI and is
    simply re-derived on every ingest. That is what lets the MED-RT projection be
    dropped and rebuilt wholesale (see drugref/classes.py) while every class comes
    back with exactly the UUID it had before. Immortality across a NUI *change* is
    out of scope, the same caveat the moiety spine records for a UNII change.
    """
    key = f"MEDRT:{nui.strip().upper()}"
    return uuid.uuid5(CLASS_NAMESPACE, key)

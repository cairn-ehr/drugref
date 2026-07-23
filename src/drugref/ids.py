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


def mint_moiety_uuid(unii: str) -> uuid.UUID:
    """Derive the immortal moiety UUID from an active moiety's UNII.

    Deterministic: same UNII -> same UUID, always, everywhere. Because it is a
    pure function of the UNII, callers may re-derive it on every ingest and get
    the registry's existing UUID back for free -- no lookup needed -- as long as
    the UNII is unchanged (see the module docstring on the UNII-change caveat).
    """
    key = f"UNII:{unii.strip().upper()}"
    return uuid.uuid5(MOIETY_NAMESPACE, key)

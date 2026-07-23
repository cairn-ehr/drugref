"""Deterministic minting of drugref's immortal substance identifiers.

drugref mints its OWN moiety UUID rather than keying on a name (principle 2:
identity is a claim, never the name). The UUID is derived deterministically
(UUIDv5) from the moiety's UNII, so two independent drugref instances ingesting
the same UNII release derive the SAME UUID with zero coordination. It is minted
at first sighting and then PINNED in the registry -- never re-derived, even if
the upstream identifier later churns.
"""
import uuid

# Namespaces are derived from the domain name (not magic literals) so they are
# self-documenting and reproducible. Per-level namespaces guarantee a moiety and
# a future salt/class derived from the same source string can never collide.
_DRUGREF_ROOT = uuid.uuid5(uuid.NAMESPACE_DNS, "drugref.org")
MOIETY_NAMESPACE = uuid.uuid5(_DRUGREF_ROOT, "moiety")


def mint_moiety_uuid(unii: str) -> uuid.UUID:
    """Derive the immortal moiety UUID from an active moiety's UNII.

    Deterministic: same UNII -> same UUID, always, everywhere. Callers use this
    only at first sighting; thereafter the registry is authoritative.
    """
    key = f"UNII:{unii.strip().upper()}"
    return uuid.uuid5(MOIETY_NAMESPACE, key)

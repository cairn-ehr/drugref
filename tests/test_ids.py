import uuid
from drugref import ids


def test_mint_is_deterministic():
    a = ids.mint_moiety_uuid("362O9ITL9D")
    b = ids.mint_moiety_uuid("362O9ITL9D")
    assert a == b
    assert isinstance(a, uuid.UUID)


def test_mint_is_case_and_space_insensitive_on_unii():
    assert ids.mint_moiety_uuid("362o9itl9d") == ids.mint_moiety_uuid("  362O9ITL9D ")


def test_distinct_uniis_distinct_uuids():
    assert ids.mint_moiety_uuid("362O9ITL9D") != ids.mint_moiety_uuid("1J444QC288")


def test_namespace_is_stable_across_runs():
    # A frozen constant: if this value ever changes, every derived UUID changes.
    assert str(ids.MOIETY_NAMESPACE) == str(uuid.uuid5(uuid.uuid5(uuid.NAMESPACE_DNS, "drugref.org"), "moiety"))


def test_namespace_matches_frozen_literal():
    # Pin the ACTUAL value, not just formula-equals-formula: re-deriving MOIETY_NAMESPACE
    # from the same uuid.uuid5(...) expression as ids.py (as the test above does) would
    # stay green even if that formula drifted -- both sides drift together. A frozen
    # literal is a real regression guard on the immortality invariant (every existing
    # moiety_uuid is derived from this namespace and would silently change underneath us).
    assert str(ids.MOIETY_NAMESPACE) == "d07651ee-311d-552b-a97b-591219eb3ad3"


# ---- slice 2a: classification class identity ------------------------------


def test_mint_class_uuid_is_deterministic():
    """Same NUI -> same UUID, always, so two instances agree with no coordination."""
    assert ids.mint_class_uuid("MED-RT", "N0000175722") == ids.mint_class_uuid("MED-RT", "N0000175722")


def test_mint_class_uuid_is_case_and_space_insensitive_on_nui():
    """NUIs arrive from XML text nodes; incidental whitespace must not fork identity."""
    assert ids.mint_class_uuid("MED-RT", "  n0000175722  ") == ids.mint_class_uuid("MED-RT", "N0000175722")


def test_distinct_nuis_distinct_uuids():
    assert ids.mint_class_uuid("MED-RT", "N0000175722") != ids.mint_class_uuid("MED-RT", "N0000008836")


def test_class_and_moiety_namespaces_cannot_collide():
    """Per-level namespaces guarantee a class and a moiety derived from the SAME
    source string still land on different UUIDs (design principle: per-level
    namespace constants)."""
    assert ids.CLASS_NAMESPACE != ids.MOIETY_NAMESPACE
    assert ids.mint_class_uuid("MED-RT", "X") != ids.mint_moiety_uuid("X")


def test_class_namespace_matches_frozen_literal():
    # Same reasoning as the moiety namespace above: pin the real value, because
    # every class_uuid in the database is derived from it.
    assert str(ids.CLASS_NAMESPACE) == "98d5a3e5-fc3b-5e75-a670-4b7ecc28caef"

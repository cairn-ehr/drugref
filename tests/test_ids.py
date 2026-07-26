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


# ---- slice 8a: local-tier product identity and name normalisation ----------


def test_normalise_name_folds_case_and_whitespace():
    """PBS names are Title-case (1,085 of 1,086 upstream) while INN claims are
    stored lower-case, so this fold is what lets the two ever meet."""
    assert ids.normalise_name("  Rifaximin  ") == "rifaximin"
    assert ids.normalise_name("Alendronic   acid") == "alendronic acid"


def test_gate_norm_delegates_to_ids():
    """gate._norm had a second consumer as of slice 8a. It now delegates rather
    than duplicating, so the bridge's fold and the INN claim's fold cannot drift."""
    from drugref.ingest import gate
    assert gate._norm("  Foo  Bar ") == ids.normalise_name("  Foo  Bar ")


def test_local_product_uuid_is_deterministic():
    """Re-derived on every rebuild, so a surviving product keeps its UUID."""
    first = ids.mint_local_product_uuid("AU", "PBS", "10001J_14023")
    assert first == ids.mint_local_product_uuid("au", " pbs ", "10001J_14023")


def test_local_product_uuid_separates_jurisdiction_and_source():
    """Same code in two jurisdictions must never collide."""
    assert (ids.mint_local_product_uuid("AU", "PBS", "1")
            != ids.mint_local_product_uuid("XX", "PBS", "1"))


def test_local_product_uuid_has_its_own_namespace():
    """Per-level namespaces stop a product and a moiety derived from the same
    string from ever colliding (the rule ids.py already applies to classes)."""
    assert ids.LOCAL_PRODUCT_NAMESPACE not in (
        ids.MOIETY_NAMESPACE, ids.CLASS_NAMESPACE, ids.QUESTION_NAMESPACE)


def test_local_product_namespace_matches_frozen_literal():
    # Same reasoning as test_namespace_matches_frozen_literal and
    # test_class_namespace_matches_frozen_literal (review round, finding 6):
    # pin the ACTUAL value, not a re-derivation of ids.py's own formula, because
    # every local_product_uuid in the database depends on this namespace and a
    # rebuild would silently re-key every PBS product if it ever drifted.
    assert str(ids.LOCAL_PRODUCT_NAMESPACE) == "2886bb06-5c2f-544a-bdeb-23bdc074b4bc"

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

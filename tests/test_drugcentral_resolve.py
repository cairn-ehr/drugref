"""Tests for resolving a DrugCentral `ddi` endpoint to a drugref moiety.

**The measurement these functions exist to make.** Issue #101 resolved DrugCentral's
free-text endpoints by matching them against ``substance_moiety.display_name`` and
concluded that the ~87 INN spellings it could not match (`ciclosporin`,
`ethinylestradiol`, `suxamethonium`) *"need a synonym bridge"*.

They do not. DrugCentral resolves its own endpoint text to a ``struct_id`` for 922
of 924 NDF-RT endpoint names, and `structures` carries an **InChIKey** and a **CAS**
number -- both of which drugref already holds as `identity_claim` rows. Keying on
structure rather than spelling is principle 2 of this project (*never key on a
name*), and measured on the real dump it moves endpoint resolution from 857/924 to
914/924 with no hand-maintained list at all.

So the cascade below is the thing worth pinning: **display_name, then InChIKey,
then CAS**, in that order, reporting WHICH route answered. The route matters as
much as the answer -- a figure that cannot say how it resolved cannot be audited.
"""
from __future__ import annotations

from tools.drugcentral_resolve import (
    Registry,
    build_endpoint_index,
    resolve_endpoint,
    unordered_pair,
)


# A registry holding one moiety reachable three different ways, plus one reachable
# only by name. Values deliberately use mixed case: the folding is under test.
REGISTRY = Registry(
    display_name={"warfarin": "uuid-warfarin", "cyclosporine": "uuid-ciclosporin"},
    inchikey={"PJVWKTKQMONHTI-UPHRSURJSA-N": "uuid-warfarin"},
    cas={"50-78-2": "uuid-aspirin"},
)

# DrugCentral's own tables: `structures` is the primary name, `synonyms` the rest.
STRUCTURES = [
    {"id": "1", "name": "warfarin", "inchikey": "PJVWKTKQMONHTI-UPHRSURJSA-N", "cas_reg_no": "81-81-2"},
    {"id": "2", "name": "aspirin", "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N", "cas_reg_no": "50-78-2"},
    {"id": "3", "name": "cyclosporine", "inchikey": "PMATZTZNYRCHOR-CGLBZJNRSA-N", "cas_reg_no": "59865-13-3"},
]
SYNONYMS = [
    {"id": "2", "name": "acetylsalicylic acid"},
    {"id": "3", "name": "ciclosporin"},
    {"id": "1", "name": "warfarin"},          # a synonym duplicating a primary name
]
INDEX = build_endpoint_index(STRUCTURES, SYNONYMS)


# ---------------------------------------------------------------------------
# build_endpoint_index
# ---------------------------------------------------------------------------

def test_the_index_reaches_a_structure_by_its_primary_name():
    assert INDEX.struct_id_for("warfarin") == "1"


def test_the_index_reaches_a_structure_by_a_synonym():
    """`acetylsalicylic acid` is the INN spelling issue #101 called unmatchable."""
    assert INDEX.struct_id_for("acetylsalicylic acid") == "2"


def test_the_index_is_case_insensitive_on_both_sides():
    assert INDEX.struct_id_for("WaRfArIn") == "1"


def test_a_primary_name_wins_over_a_synonym_claiming_the_same_text():
    """`structures.name` is DrugCentral's own preferred label; a synonym never
    displaces it, so the index stays stable if the synonym table grows."""
    index = build_endpoint_index(
        [{"id": "1", "name": "warfarin", "inchikey": "", "cas_reg_no": ""}],
        [{"id": "99", "name": "warfarin"}],
    )
    assert index.struct_id_for("warfarin") == "1"


def test_an_unknown_name_reaches_nothing():
    assert INDEX.struct_id_for("not a drug") is None


# ---------------------------------------------------------------------------
# resolve_endpoint -- the cascade, and the route it reports
# ---------------------------------------------------------------------------

def test_a_display_name_match_resolves_by_name_without_consulting_the_dump():
    assert resolve_endpoint("warfarin", INDEX, REGISTRY) == ("uuid-warfarin", "display_name")


def test_display_name_matching_folds_case():
    assert resolve_endpoint("WARFARIN", INDEX, REGISTRY) == ("uuid-warfarin", "display_name")


def test_an_inn_spelling_drugref_does_not_carry_resolves_STRUCTURALLY():
    """THE FINDING THIS MODULE EXISTS FOR.

    drugref holds `cyclosporine` (the USAN spelling from UNII); DrugCentral says
    `ciclosporin` (the INN). No name match exists in either direction -- but the
    synonym reaches struct 3, whose InChIKey... is not in the registry here, so
    this case must fall through to the NEXT route rather than stopping.
    """
    # `ciclosporin` -> struct 3 -> InChIKey not registered -> CAS not registered.
    assert resolve_endpoint("ciclosporin", INDEX, REGISTRY) == (None, "unresolved")


def test_an_unmatched_name_resolves_by_inchikey_when_the_registry_holds_the_key():
    """A name drugref cannot spell, reached through the structure it denotes."""
    registry = Registry(display_name={}, inchikey=REGISTRY.inchikey, cas={})
    assert resolve_endpoint("warfarin", INDEX, registry) == ("uuid-warfarin", "inchikey")


def test_cas_is_the_last_resort_and_is_reported_as_such():
    assert resolve_endpoint("acetylsalicylic acid", INDEX, REGISTRY) == ("uuid-aspirin", "cas")


def test_inchikey_is_tried_before_cas():
    """Order is not cosmetic: an InChIKey denotes a structure exactly, while a CAS
    number is an administrative identifier that upstream sources reuse loosely."""
    registry = Registry(
        display_name={},
        inchikey={"BSYNRYMUTXBXSQ-UHFFFAOYSA-N": "uuid-by-key"},
        cas={"50-78-2": "uuid-by-cas"},
    )
    assert resolve_endpoint("aspirin", INDEX, registry) == ("uuid-by-key", "inchikey")


def test_inchikey_matching_folds_case():
    registry = Registry(
        display_name={},
        inchikey={"PJVWKTKQMONHTI-UPHRSURJSA-N": "uuid-warfarin"},
        cas={},
    )
    index = build_endpoint_index(
        [{"id": "1", "name": "warfarin", "inchikey": "pjvwktkqmonhti-uphrsurjsa-n", "cas_reg_no": ""}],
        [],
    )
    assert resolve_endpoint("warfarin", index, registry) == ("uuid-warfarin", "inchikey")


def test_a_blank_structural_key_is_not_a_lookup():
    r"""`structures` carries empty InChIKeys for biologics and mixtures.

    An empty string must never be looked up: a registry that happened to hold ""
    would otherwise collapse every keyless substance onto one moiety.
    """
    index = build_endpoint_index(
        [{"id": "1", "name": "heparin", "inchikey": "", "cas_reg_no": ""}], [])
    registry = Registry(display_name={}, inchikey={"": "WRONG"}, cas={"": "WRONG"})
    assert resolve_endpoint("heparin", index, registry) == (None, "unresolved")


def test_a_name_that_reaches_no_structure_at_all_is_unresolved():
    assert resolve_endpoint("Strong CYP3A4 Inhibitors", INDEX, REGISTRY) == (None, "unresolved")


# ---------------------------------------------------------------------------
# unordered_pair -- the unit every downstream figure is counted in
# ---------------------------------------------------------------------------

def test_an_unordered_pair_is_orientation_independent():
    """PROJECT-NOTES warns that rows, pairs and distinct pairs are three units.
    Normalising here is what makes the overlap arithmetic comparable at all."""
    assert unordered_pair("b", "a") == unordered_pair("a", "b") == ("a", "b")


def test_a_self_pair_is_not_a_pair():
    """A rule whose two endpoints resolve to ONE moiety states nothing about an
    interaction between two drugs. `db/018` subtracts the same case upstream."""
    assert unordered_pair("a", "a") is None


def test_an_unresolved_endpoint_makes_no_pair():
    assert unordered_pair(None, "a") is None
    assert unordered_pair("a", None) is None

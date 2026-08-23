"""Tests for resolving a DrugCentral `ddi` endpoint to a drugref moiety.

**What these functions do.** They take one free-text `ddi` endpoint -- DrugCentral
stores both endpoints as prose in a ``varchar(500)``, with no code of any kind --
and return the drugref moiety it denotes, together with the route that answered.

**The cascade is display_name, then InChIKey, then CAS**, and every result reports
which of them answered, or why none did. The route matters as much as the answer:
a figure that cannot say how it resolved cannot be audited.

**Background.** Issue #101 resolved endpoints against ``substance_moiety.display_name``
alone and concluded that the INN spellings it could not match (`ciclosporin`,
`ethinylestradiol`, `suxamethonium`) *"need a synonym bridge"* -- a hand-maintained
list someone would have to own forever. They do not: DrugCentral resolves its own
endpoint text to a ``struct_id``, and `structures` carries an InChIKey and a CAS
number drugref already holds as `identity_claim` rows. The measured improvement is
in the generated results file, not restated here where it would rot.
"""
from __future__ import annotations

import pytest

from drugref.ingest import drugcentral_resolve
from drugref.ingest.drugcentral_resolve import (
    ROUTE_CAS,
    ROUTE_DISPLAY_NAME,
    ROUTE_INCHIKEY,
    ROUTE_MISSING_KEYS_ROW,
    ROUTE_NO_STRUCTURAL_KEY,
    ROUTE_NOT_A_SUBSTANCE,
    ROUTE_UNRESOLVED,
    EndpointIndex,
    Registry,
    Resolution,
    build_endpoint_index,
    resolve_endpoint,
    unordered_pair,
)

WARFARIN_KEY = "PJVWKTKQMONHTI-UPHRSURJSA-N"
ASPIRIN_KEY = "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
CICLOSPORIN_KEY = "PMATZTZNYRCHOR-CGLBZJNRSA-N"

# The drugref side. Three moieties, each reachable by a different route, so no
# test can pass by accident on a route it did not mean to exercise:
#   uuid-warfarin     -- by display_name AND by InChIKey
#   uuid-aspirin      -- by CAS only (drugref does not spell `acetylsalicylic acid`)
#   uuid-cyclosporine -- by InChIKey only; drugref carries UNII's USAN spelling
#                        `cyclosporine` while DrugCentral says `ciclosporin`
REGISTRY = Registry(
    display_name={"warfarin": "uuid-warfarin", "cyclosporine": "uuid-cyclosporine"},
    inchikey={WARFARIN_KEY: "uuid-warfarin", CICLOSPORIN_KEY: "uuid-cyclosporine"},
    cas={"50-78-2": "uuid-aspirin"},
)

# DrugCentral's own tables: `structures` is the primary name, `synonyms` the rest.
STRUCTURES = [
    {"id": "1", "name": "warfarin", "inchikey": WARFARIN_KEY, "cas_reg_no": "81-81-2"},
    {"id": "2", "name": "aspirin", "inchikey": ASPIRIN_KEY, "cas_reg_no": "50-78-2"},
    {"id": "3", "name": "cyclosporine", "inchikey": CICLOSPORIN_KEY,
     "cas_reg_no": "59865-13-3"},
]
SYNONYMS = [
    {"id": "2", "name": "acetylsalicylic acid"},
    {"id": "3", "name": "ciclosporin"},
    {"id": "1", "name": "warfarin"},          # a synonym duplicating a primary name
]
INDEX = build_endpoint_index(STRUCTURES, SYNONYMS)


# ---------------------------------------------------------------------------
# Resolution -- the type that makes an unauditable answer unrepresentable
# ---------------------------------------------------------------------------

def test_a_resolution_refuses_a_route_outside_the_closed_vocabulary():
    """The vocabulary is closed, and now something other than a comment says so."""
    with pytest.raises(ValueError):
        Resolution("uuid-x", "inchikeys")            # note the typo


def test_a_resolution_refuses_a_uuid_that_disagrees_with_its_route():
    """`moiety_uuid is None` IFF the route is an unresolved one.

    The report derives its resolved-name count from the uuid and its route table
    from the route. Nothing else would notice those two disagreeing, so they are
    made unable to.
    """
    with pytest.raises(ValueError):
        Resolution(None, ROUTE_INCHIKEY)             # a route that found nothing
    with pytest.raises(ValueError):
        Resolution("uuid-x", ROUTE_UNRESOLVED)       # a miss carrying a moiety


def test_a_resolution_is_not_unpackable():
    """A moiety UUID and a route label are both strings.

    While this was a plain tuple, `route, uuid = resolve_endpoint(...)` type-checked,
    ran, and mislabelled every figure it produced. `signing.Keypair` records the
    same decision for the same reason.
    """
    with pytest.raises(TypeError):
        _uuid, _route = resolve_endpoint("warfarin", INDEX, REGISTRY)  # noqa: F841


# ---------------------------------------------------------------------------
# Registry -- keyword-only, and it folds its own keys
# ---------------------------------------------------------------------------

def test_the_registry_is_keyword_only():
    """Three interchangeable `Mapping[str, str]`s must not be positional.

    Swapping `inchikey` and `cas` would type-check, run, produce a plausible
    number AND label it with the route it did not come from -- so the audit trail
    would corroborate the wrong answer.
    """
    with pytest.raises(TypeError):
        Registry({}, {}, {})                        # deliberately positional


def test_the_registry_folds_its_own_keys_so_the_caller_does_not_have_to():
    """The case rule lived in the loader's SQL AND at the lookup site. One home now."""
    registry = Registry(
        display_name={"  WarFarin ": "uuid-warfarin"},
        inchikey={WARFARIN_KEY.lower(): "uuid-warfarin"},
        cas={" 50-78-2 ": "uuid-aspirin"},
    )
    assert resolve_endpoint("warfarin", INDEX, registry).moiety_uuid == "uuid-warfarin"
    assert registry.inchikey[WARFARIN_KEY] == "uuid-warfarin"
    assert registry.cas["50-78-2"] == "uuid-aspirin"


# ---------------------------------------------------------------------------
# build_endpoint_index
# ---------------------------------------------------------------------------

def test_the_index_reaches_a_structure_by_its_primary_name():
    assert INDEX.struct_id_for("warfarin") == "1"


def test_the_index_reaches_a_structure_by_a_synonym():
    """`acetylsalicylic acid` is a spelling drugref does not carry."""
    assert INDEX.struct_id_for("acetylsalicylic acid") == "2"


def test_the_index_is_case_insensitive_on_both_sides():
    assert INDEX.struct_id_for("WaRfArIn") == "1"


def test_the_index_trims_surrounding_whitespace():
    """A stray trailing space in a `varchar(500)` is a realistic cause of a miss."""
    assert INDEX.struct_id_for("  warfarin\t") == "1"


def test_a_primary_name_wins_over_a_synonym_claiming_the_same_text():
    """`structures.name` is DrugCentral's own preferred label; a synonym never
    displaces it, so the index stays stable if the synonym table grows."""
    index = build_endpoint_index(
        [{"id": "1", "name": "warfarin", "inchikey": "", "cas_reg_no": ""}],
        [{"id": "99", "name": "warfarin"}],
    )
    assert index.struct_id_for("warfarin") == "1"


def test_a_structure_row_whose_id_round_tripped_to_empty_is_skipped():
    """The TSV cache writes SQL NULL as "", never None, so the guard tests falsiness.

    An `is None` check here could never fire for the real caller and would read
    as a guard that is not one.
    """
    index = build_endpoint_index(
        [{"id": "", "name": "ghost", "inchikey": "X", "cas_reg_no": "Y"}], [])
    assert index.struct_id_for("ghost") is None


def test_an_unknown_name_reaches_nothing():
    assert INDEX.struct_id_for("not a drug") is None


# ---------------------------------------------------------------------------
# resolve_endpoint -- the cascade, and the route it reports
# ---------------------------------------------------------------------------

def test_a_display_name_match_resolves_by_name_without_consulting_the_dump():
    assert resolve_endpoint("warfarin", INDEX, REGISTRY) == Resolution(
        "uuid-warfarin", ROUTE_DISPLAY_NAME)


def test_display_name_matching_folds_case():
    assert resolve_endpoint("WARFARIN", INDEX, REGISTRY) == Resolution(
        "uuid-warfarin", ROUTE_DISPLAY_NAME)


def test_an_inn_spelling_drugref_does_not_carry_resolves_STRUCTURALLY():
    """THE FINDING THIS MODULE EXISTS FOR, end to end and with nothing faked.

    drugref holds `cyclosporine` (UNII's USAN spelling); DrugCentral says
    `ciclosporin` (the INN). Neither name matches the other in either direction.
    The synonym reaches struct 3, whose InChIKey drugref DOES hold -- so the
    endpoint resolves to the right moiety through the structure it denotes, with
    no hand-maintained synonym bridge anywhere.
    """
    assert resolve_endpoint("ciclosporin", INDEX, REGISTRY) == Resolution(
        "uuid-cyclosporine", ROUTE_INCHIKEY)


def test_a_structural_miss_falls_through_every_route_rather_than_stopping():
    """An InChIKey that misses must try CAS before giving up.

    Named for what it pins. It used to be named for the finding above and asserted
    the opposite of it, which is the kind of thing a reader takes at face value.
    """
    registry = Registry(display_name={}, inchikey={}, cas={})
    assert resolve_endpoint("ciclosporin", INDEX, registry) == Resolution(
        None, ROUTE_UNRESOLVED)


def test_cas_answers_when_the_inchikey_route_missed():
    """`acetylsalicylic acid` -> struct 2 -> InChIKey unheld -> CAS held."""
    assert resolve_endpoint("acetylsalicylic acid", INDEX, REGISTRY) == Resolution(
        "uuid-aspirin", ROUTE_CAS)


def test_inchikey_is_tried_before_cas():
    """Order is not cosmetic: an InChIKey denotes a structure exactly, while a CAS
    number is an administrative identifier that upstream sources reuse loosely."""
    registry = Registry(
        display_name={},
        inchikey={ASPIRIN_KEY: "uuid-by-key"},
        cas={"50-78-2": "uuid-by-cas"},
    )
    assert resolve_endpoint("aspirin", INDEX, registry) == Resolution(
        "uuid-by-key", ROUTE_INCHIKEY)


def test_inchikey_matching_folds_case():
    index = build_endpoint_index(
        [{"id": "1", "name": "warfarin", "inchikey": WARFARIN_KEY.lower(),
          "cas_reg_no": ""}],
        [],
    )
    registry = Registry(display_name={}, inchikey={WARFARIN_KEY: "uuid-warfarin"},
                        cas={})
    assert resolve_endpoint("warfarin", index, registry) == Resolution(
        "uuid-warfarin", ROUTE_INCHIKEY)


def test_a_blank_structural_key_is_not_a_lookup():
    r"""`structures` carries empty InChIKeys for biologics and mixtures.

    An empty string must never be looked up: a registry that happened to hold ""
    would otherwise collapse every keyless substance onto one moiety. The route
    says `no_structural_key` rather than `unresolved`, because a keyless substance
    is a different fact from a key drugref does not hold.
    """
    index = build_endpoint_index(
        [{"id": "1", "name": "heparin", "inchikey": "", "cas_reg_no": ""}], [])
    registry = Registry(display_name={}, inchikey={"": "WRONG"}, cas={"": "WRONG"})
    assert resolve_endpoint("heparin", index, registry) == Resolution(
        None, ROUTE_NO_STRUCTURAL_KEY)


def test_a_blank_endpoint_name_is_never_looked_up_either():
    """The guard covers the display_name route too, not only the structural ones.

    The TSV cache round-trips SQL NULL as "", so a NULL `ddi` endpoint arrives
    here as the empty string. A registry that happened to hold "" would resolve
    every one of them onto a single moiety -- and the display_name route was the
    one route with no guard.
    """
    registry = Registry(display_name={"": "WRONG"}, inchikey={}, cas={})
    assert resolve_endpoint("", INDEX, registry) == Resolution(
        None, ROUTE_NOT_A_SUBSTANCE)
    assert resolve_endpoint("   ", INDEX, registry) == Resolution(
        None, ROUTE_NOT_A_SUBSTANCE)


def test_a_name_drugcentral_itself_does_not_know_is_reported_as_not_a_substance():
    """A class-named endpoint is a CORRECT miss, not a failure of the cascade.

    Kept apart from `unresolved` so the report can say how much of the residue is
    class names drugref was never going to resolve.
    """
    assert resolve_endpoint("Strong CYP3A4 Inhibitors", INDEX, REGISTRY) == Resolution(
        None, ROUTE_NOT_A_SUBSTANCE)


def test_a_struct_id_with_no_keys_row_is_a_broken_join_not_a_miss():
    """A name reaching a `struct_id` absent from `structures` cannot happen.

    If it ever does, the extract is inconsistent -- and defaulting the missing row
    to blank keys would render it as an ordinary unresolved endpoint, letting a
    corrupt extract pass for a difficult one.
    """
    index = EndpointIndex(names={"orphan": "999"}, structural_keys={})
    assert resolve_endpoint("orphan", index, REGISTRY) == Resolution(
        None, ROUTE_MISSING_KEYS_ROW)


# ---------------------------------------------------------------------------
# unordered_pair -- the unit every downstream figure is counted in
# ---------------------------------------------------------------------------

def test_an_unordered_pair_is_orientation_independent():
    """PROJECT-NOTES warns that rows, pairs and distinct pairs are three units.
    Normalising here is what makes the overlap arithmetic comparable at all."""
    assert unordered_pair("b", "a") == unordered_pair("a", "b") == ("a", "b")


def test_a_self_pair_is_not_a_pair():
    """A rule whose two endpoints resolve to ONE moiety states nothing about an
    interaction between two drugs. `db/010` subtracts the same case upstream."""
    assert unordered_pair("a", "a") is None


# ---------------------------------------------------------------------------
# first_wins -- folding an ordered read into a lookup, colliding keys and all
# ---------------------------------------------------------------------------

def test_first_wins_keeps_the_first_row_and_counts_the_collisions():
    """The rule that makes a colliding structural key resolve the same way twice.

    identity_claim is unique on (moiety_uuid, scheme, value) and deliberately NOT
    across moieties, so two moieties may legitimately carry one CAS number. The
    caller reads under a deterministic ORDER BY; this decides what happens when
    that ordered read hands over the same key twice.
    """
    lookup, duplicates = drugcentral_resolve.first_wins(
        [("aaa", "uuid-1"), ("aaa", "uuid-2"), ("bbb", "uuid-3")])
    assert lookup == {"aaa": "uuid-1", "bbb": "uuid-3"}
    assert duplicates == 1


def test_first_wins_counts_nothing_when_every_key_is_unique():
    lookup, duplicates = drugcentral_resolve.first_wins(
        [("aaa", "uuid-1"), ("bbb", "uuid-2")])
    assert lookup == {"aaa": "uuid-1", "bbb": "uuid-2"}
    assert duplicates == 0

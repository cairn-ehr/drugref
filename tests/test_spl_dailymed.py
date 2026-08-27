# tests/test_spl_dailymed.py
"""The DailyMed reader: recovering a subject openFDA leaves empty.

59.6% of section-carrying openFDA labels carry no `openfda.unii`. `moiety_uuid`
is UUIDv5 on UNII, so no UNII means no subject, and an interaction statement with
no subject is not an interaction statement. DailyMed's own SPL XML carries the
full ingredient list under the same `set_id`, and reading it recovers 6,514 of
them.

FOUR PARSING TRAPS ARE PINNED HERE, because getting any of them wrong produces a
confident number pointing the wrong way:

1. an INACTIVE ingredient is never the subject -- excipients carry UNIIs too, and
   reading one attaches a real interaction statement to lactose;
2. SPL spells "active ingredient" TWO ways and the classCode spelling dominates,
   so reading only the other recovers close to nothing;
3. the salt is not the moiety, and drugref registers a salt as its own moiety --
   so blending the two doubles a salt product's pairs (the defect that published
   31,618 where the rule gives 29,258);
4. DailyMed ships successive VERSIONS of one label sharing a `set_id`, so
   counting documents over-counts labels.
"""
import pytest

from drugref.ingest import spl_dailymed as dm

UNII_SYSTEM = dm.UNII_CODE_SYSTEM


def _document(body: str, *, set_id: str = "SET-1", version: str | None = "4") -> bytes:
    """A minimal but structurally real SPL document."""
    version_element = (
        f'<versionNumber value="{version}"/>' if version is not None else "")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<document xmlns="urn:hl7-org:v3">'
        f'<setId root="{set_id}"/>{version_element}{body}'
        "</document>"
    ).encode()


def _ingredient(class_code: str, substance_unii: str, moiety_unii: str | None) -> str:
    """One `<ingredient classCode=...>` -- the dominant SPL spelling."""
    moiety = (
        f'<activeMoiety><activeMoiety>'
        f'<code code="{moiety_unii}" codeSystem="{UNII_SYSTEM}"/>'
        f"</activeMoiety></activeMoiety>" if moiety_unii else "")
    return (
        f'<ingredient classCode="{class_code}"><ingredientSubstance>'
        f'<code code="{substance_unii}" codeSystem="{UNII_SYSTEM}"/>'
        f"{moiety}</ingredientSubstance></ingredient>")


# --------------------------------------------------------------------------
# Trap 1: an excipient is never the subject
# --------------------------------------------------------------------------

def test_an_INACTIVE_ingredient_is_not_read_as_a_subject():
    """`IACT` is deliberately absent from the active class codes.

    Reading it would key a real interaction statement to lactose.
    """
    found = dm.extract_subject_uniis(
        _document(_ingredient("IACT", "LACTOSE-UNII", "LACTOSE-MOIETY")))
    assert found.substance_uniis == ()
    assert found.moiety_uniis == ()


def test_generic_INGR_and_container_CNTM_ingredients_are_not_subjects():
    """Neither declares the ingredient active; guessing keys the packaging."""
    for class_code in ("INGR", "CNTM"):
        found = dm.extract_subject_uniis(
            _document(_ingredient(class_code, "X-UNII", "X-MOIETY")))
        assert found.moiety_uniis == (), class_code


@pytest.mark.parametrize("class_code", ["ACTIB", "ACTIM", "ACTIR", "ACTI"])
def test_every_active_class_code_is_read(class_code):
    found = dm.extract_subject_uniis(
        _document(_ingredient(class_code, "SALT-UNII", "BASE-UNII")))
    assert found.moiety_uniis == ("BASE-UNII",)


# --------------------------------------------------------------------------
# Trap 2: two spellings, and the dominant one is the classCode
# --------------------------------------------------------------------------

def test_the_activeIngredientSubstance_spelling_is_read_too():
    """The minority spelling, where 'active' is part of the element name.

    Measured on the read release: part6 carries 2,912 classCode-active elements
    and ZERO of these; part1 carries 7,639 against 727. Reading only this one
    would have recovered close to NOTHING -- and under-counting is the direction
    that quietly kills a design option.
    """
    body = (
        "<activeIngredient><activeIngredientSubstance>"
        f'<code code="SALT-UNII" codeSystem="{UNII_SYSTEM}"/>'
        "<activeMoiety><activeMoiety>"
        f'<code code="BASE-UNII" codeSystem="{UNII_SYSTEM}"/>'
        "</activeMoiety></activeMoiety>"
        "</activeIngredientSubstance></activeIngredient>")
    found = dm.extract_subject_uniis(_document(body))
    assert found.moiety_uniis == ("BASE-UNII",)
    assert found.substance_uniis == ("SALT-UNII",)


def test_a_code_from_another_code_system_is_not_harvested_as_a_unii():
    """SPL is full of `<code>` elements -- dosage form, route, marketing category.

    Keying on the element name alone harvests all of them as if they were
    substances.
    """
    body = (
        '<ingredient classCode="ACTIB"><ingredientSubstance>'
        '<code code="C42953" codeSystem="2.16.840.1.113883.3.26.1.1"/>'
        "</ingredientSubstance></ingredient>")
    assert dm.extract_subject_uniis(_document(body)).substance_uniis == ()


# --------------------------------------------------------------------------
# Trap 3: the salt is not the moiety, and ONE rule decides which is the subject
# --------------------------------------------------------------------------

def test_both_grains_are_READ_because_which_resolves_is_the_measurement():
    found = dm.extract_subject_uniis(
        _document(_ingredient("ACTIB", "SALT-UNII", "BASE-UNII")))
    assert found.substance_uniis == ("SALT-UNII",)
    assert found.moiety_uniis == ("BASE-UNII",)


def test_the_subject_is_the_MOIETY_when_the_moiety_resolves():
    found = dm.SubjectUniis(
        set_id="S", moiety_uniis=("BASE",), substance_uniis=("SALT",))
    assert dm.subject_uniis(found, {"BASE", "SALT"}) == ("BASE",)


def test_the_subject_NEVER_blends_the_salt_in_beside_the_moiety():
    """The defect that published 31,618 pairs where the rule gives 29,258.

    drugref registers a salt as its own moiety with its own live UNII claim, so
    handing a pair counter both UNIIs makes a salt product pair against every
    partner TWICE -- on 56.7% of resolvable DailyMed labels, against zero on the
    openFDA arm it was being compared with.
    """
    found = dm.SubjectUniis(
        set_id="S", moiety_uniis=("BASE",), substance_uniis=("SALT",))
    assert "SALT" not in dm.subject_uniis(found, {"BASE", "SALT"})


def test_the_salt_is_the_subject_ONLY_when_no_moiety_unii_resolves():
    """16 labels take this route on the real release, counted apart on purpose.

    It needs the salt-to-base step drugref does not have (#67), so folding it in
    would promise a route that is not built.
    """
    found = dm.SubjectUniis(
        set_id="S", moiety_uniis=("BASE",), substance_uniis=("SALT",))
    assert dm.subject_uniis(found, {"SALT"}) == ("SALT",)


def test_a_label_whose_uniis_drugref_does_not_hold_contributes_no_subject():
    found = dm.SubjectUniis(
        set_id="S", moiety_uniis=("BASE",), substance_uniis=("SALT",))
    assert dm.subject_uniis(found, {"SOMETHING-ELSE"}) == ()


def test_the_route_names_which_grain_answered_and_they_are_EXCLUSIVE():
    """One subject per label per route -- db/051's route CHECK depends on it."""
    found = dm.SubjectUniis(
        set_id="S", moiety_uniis=("BASE",), substance_uniis=("SALT",))
    assert dm.subject_route(found, {"BASE", "SALT"}) == "dailymed_active_moiety"
    assert dm.subject_route(found, {"SALT"}) == "dailymed_active_substance"
    assert dm.subject_route(found, {"OTHER"}) is None


# --------------------------------------------------------------------------
# Trap 4: versions of one label share a set_id
# --------------------------------------------------------------------------

def test_one_set_id_read_TWICE_is_one_label():
    """Counting documents reported 6,583 labels where 6,539 exist."""
    rows = [
        dm.SubjectUniis(set_id="S", moiety_uniis=("A",), substance_uniis=(), version=1),
        dm.SubjectUniis(set_id="S", moiety_uniis=("B",), substance_uniis=(), version=2),
    ]
    assert len(dm.dedupe_by_set_id(rows)) == 1


def test_the_HIGHEST_version_wins_not_the_first_or_last_seen():
    """Neither "first" nor "last" is a rule -- both are zip-member order.

    The fix for the 44-label over-count introduced TWO de-duplication rules
    rather than one, and two published tables then described different readings
    of the same labels.
    """
    rows = [
        dm.SubjectUniis(set_id="S", moiety_uniis=("LOW",), substance_uniis=(), version=1),
        dm.SubjectUniis(set_id="S", moiety_uniis=("HIGH",), substance_uniis=(), version=9),
        dm.SubjectUniis(set_id="S", moiety_uniis=("MID",), substance_uniis=(), version=5),
    ]
    assert dm.dedupe_by_set_id(rows)["S"].moiety_uniis == ("HIGH",)


def test_an_unversioned_row_never_displaces_a_versioned_one():
    rows = [
        dm.SubjectUniis(set_id="S", moiety_uniis=("V",), substance_uniis=(), version=2),
        dm.SubjectUniis(set_id="S", moiety_uniis=("NONE",), substance_uniis=(), version=None),
    ]
    assert dm.dedupe_by_set_id(rows)["S"].moiety_uniis == ("V",)


# --------------------------------------------------------------------------
# What a document that cannot be joined must NOT be reported as
# --------------------------------------------------------------------------

def test_an_unparseable_document_is_None_not_an_empty_reading():
    """Folding the two together reports a parse failure as a source gap."""
    assert dm.extract_subject_uniis(b"<document") is None


def test_a_document_with_no_setId_is_None_because_it_can_never_be_joined():
    body = '<ingredient classCode="ACTIB"><ingredientSubstance/></ingredient>'
    assert dm.extract_subject_uniis(
        f'<document xmlns="urn:hl7-org:v3">{body}</document>'.encode()) is None


def test_a_label_read_successfully_with_no_ingredients_is_NOT_None():
    """It was read, and it had nothing. That is a different fact from a failure."""
    found = dm.extract_subject_uniis(_document(""))
    assert found is not None
    assert found.has_any_unii is False


def test_the_version_is_read_and_a_junk_version_does_not_abort_the_read():
    assert dm.extract_subject_uniis(_document("", version="7")).version == 7
    assert dm.extract_subject_uniis(_document("", version="v7")).version is None


def test_the_cheap_byte_prefilter_accepts_both_quote_styles():
    """A regex that missed `root='x'` would drop a targeted label BEFORE parsing.

    It would then land in "absent from DailyMed" -- a fact about the reading
    republished as a fact about the release.
    """
    assert dm.set_id_in_bytes(b"<setId root='SET-9'/>") == "SET-9"
    assert dm.set_id_in_bytes(b'<setId  root = "SET-9" />') == "SET-9"
    assert dm.set_id_in_bytes(b"<setId/>") is None

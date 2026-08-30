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
import io
import zipfile

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


# --------------------------------------------------------------------------
# THE READER ITSELF -- `iter_release_labels` and `scan_release`
# --------------------------------------------------------------------------
#
# ⇒ NEITHER FUNCTION HAD A DIRECT TEST. Six mutations survived them, including
# never incrementing `documents_read` and dropping the `.xml` filter entirely.
# The end-to-end fixture could not see any of it: every fixture part held
# well-formed member zips with exactly one XML and one matching setId, so all
# three drop counters were pinned at zero BY CONSTRUCTION. The suite reproduced
# the release's measured zeros without ever showing a counter could move, which
# is db/050's vacuous-guard finding at the one place the design's
# `absent_from_dailymed` commitment rests on.

def _part(tmp_path, members, *, name="dm_spl_release_human_rx_part1.zip"):
    """A release part: a zip of member zips, each holding whatever `members` says.

    `members` maps an outer member name to a dict of inner name -> bytes, so a
    test can build the shapes the real release is NOT known to contain -- a
    member with no XML, one with several, a plain file that is not a zip at all.
    """
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as outer:
        for outer_name, inner_files in members.items():
            if inner_files is None:                    # not a member zip at all
                outer.writestr(outer_name, b"not a zip")
                continue
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as inner:
                for inner_name, payload in inner_files.items():
                    inner.writestr(inner_name, payload)
            outer.writestr(outer_name, buffer.getvalue())
    return str(path)


def test_iter_release_labels_yields_the_sole_xml_of_each_member(tmp_path):
    """The control. Every refusal below could otherwise be a reader that yields
    nothing at all."""
    part = _part(tmp_path, {"a.zip": {"a.xml": _document("", set_id="SET-1"),
                                      "a.jpg": b"\xff\xd8"}})
    assert [name for name, _ in dm.iter_release_labels(part)] == ["a.zip"]


def test_a_member_the_reader_declines_is_REPORTED_not_silently_skipped(tmp_path):
    """⇒ THE HOLE. These three branches were bare `continue`s inside the
    generator, upstream of every counter `scan_release` keeps -- so a declined
    member reached neither `documents_read` nor any `ScanResult` field, and
    `check_scan_dropped_nothing` could not refuse what it could not see. Each
    one reappears three stages later as `absent_from_dailymed`.
    """
    part = _part(tmp_path, {
        "good.zip": {"a.xml": _document("", set_id="SET-1")},
        "manifest.txt": None,                          # not a member zip
        "images_only.zip": {"a.jpg": b"\xff\xd8"},     # no XML
        "ambiguous.zip": {"a.xml": b"<a/>", "b.xml": b"<b/>"},
    })
    skips = []
    read = [name for name, _ in dm.iter_release_labels(part, on_skip=(
        lambda member, reason: skips.append((member, reason))))]

    assert read == ["good.zip"]
    assert sorted(skips) == [
        ("ambiguous.zip", "several_xml_members"),
        ("images_only.zip", "no_xml_member"),
        ("manifest.txt", "not_a_member_zip")]


def test_a_member_with_SEVERAL_xml_files_is_refused_not_arbitrarily_picked(
        tmp_path):
    """`xml_names[0]` is zip member order, and this module's own
    `dedupe_by_set_id` argues at length that member order is not a rule. Reading
    the wrong document would attach a subject to the wrong wording silently."""
    part = _part(tmp_path, {"m.zip": {"first.xml": _document("", set_id="SET-1"),
                                      "second.xml": _document("", set_id="SET-2")}})
    assert list(dm.iter_release_labels(part)) == []


def test_scan_release_counts_every_member_of_the_part(tmp_path):
    """The counters have to MOVE, not merely exist. This is the fixture the
    end-to-end corpus structurally could not be."""
    part = _part(tmp_path, {
        "hit.zip": {"a.xml": _document("", set_id="SET-1")},
        "miss.zip": {"a.xml": _document("", set_id="SET-99")},  # not targeted
        "nosetid.zip": {"a.xml": b'<document xmlns="urn:hl7-org:v3"/>'},
        # Carries a targeted setId in its BYTES so it passes the cheap
        # pre-filter, then fails to parse -- the one path that reaches
        # `dropped_unreadable`, and the reason the pre-filter is never the
        # authority.
        "truncated.zip": {"a.xml": b'<document xmlns="urn:hl7-org:v3">'
                                   b'<setId root="SET-1"/>'},
        "manifest.txt": None,
        "images_only.zip": {"a.jpg": b"\xff\xd8"},
        "ambiguous.zip": {"a.xml": b"<a/>", "b.xml": b"<b/>"},
    })
    scan = dm.scan_release([part], {"SET-1"})

    assert set(scan.found) == {"SET-1"}
    assert scan.documents_read == 4           # only members that yielded an XML
    assert scan.dropped_no_set_id_bytes == 1
    assert scan.dropped_unreadable == 1
    assert scan.dropped_no_xml_member == 1
    assert scan.dropped_several_xml_members == 1
    assert scan.skipped_not_a_member_zip == 1
    assert scan.total_dropped == 4            # the non-zip member is NOT a drop


def test_scan_release_drops_a_document_whose_tree_DISAGREES_with_the_prefilter(
        tmp_path):
    """An SPL `<relatedDocument>` names the label it replaces, so the byte regex
    can select a setId the document's own tree does not carry.

    The `<relatedDocument>` is written FIRST here on purpose: `set_id_in_bytes`
    takes the first `<setId root=` in the file, and the whole reason the tree is
    re-read is that document order is not guaranteed to put the document's own
    setId first. A fixture that relied on it would be asserting the assumption
    rather than the guard.
    """
    body = ('<relatedDocument><setId root="SET-TARGET"/></relatedDocument>'
            '<setId root="SET-REAL"/>')
    xml = f'<document xmlns="urn:hl7-org:v3">{body}</document>'.encode()
    scan = dm.scan_release([_part(tmp_path, {"m.zip": {"a.xml": xml}})],
                           {"SET-TARGET"})
    assert scan.dropped_prefilter_disagreed == 1
    assert scan.found == {}


def test_a_document_declaring_an_ENTITY_is_refused_rather_than_expanded():
    """⇒ SPL IS THIRD-PARTY CONTENT: NLM publishes labeling "submitted to the
    FDA by companies", and `ET.fromstring` expands internal entities.

    Measured before the guard: five levels of nesting expand to 100 KB in under
    3 ms, each further level multiplying by ten. External entities are already
    refused by the expat build, so there is no XXE -- the exposure is memory, and
    a document a few lines long can exhaust it. Valid SPL declares no entities,
    so the whole class is refused rather than bounded.

    `None` files it under `dropped_unreadable`, which `check_scan_dropped_nothing`
    refuses the run over -- before the run row exists.
    """
    bomb = (b'<!DOCTYPE d [<!ENTITY a "AAAAAAAAAA">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            b'<document xmlns="urn:hl7-org:v3"><setId root="SET-1"/>'
            b'<x>&b;</x></document>')
    assert dm.extract_subject_uniis(bomb) is None


def test_the_literal_text_ENTITY_IN_A_COMMENT_does_not_drop_a_valid_label():
    """⇒ THE GUARD IS MATCHED ON THE DOCTYPE, NOT ON `<!ENTITY` ANYWHERE.

    An entity declaration is only legal inside a DOCTYPE's internal subset; the
    literal text `<!ENTITY` is legal anywhere a comment is. Measured: `<!-- <!ENTITY
    a "x" -->` parses cleanly, so the first version of this guard -- a bare byte
    search for `<!ENTITY` -- would have dropped a valid document. And because a
    drop here is COUNTED, one false positive anywhere in 41,056 documents would
    abort the entire ingest.
    """
    commented = (b'<!-- <!ENTITY a "x" -->'
                 b'<document xmlns="urn:hl7-org:v3"><setId root="SET-1"/></document>')
    found = dm.extract_subject_uniis(commented)
    assert found is not None and found.set_id == "SET-1"


def test_an_ordinary_document_is_still_read_after_the_entity_guard():
    """The control: a guard that refused everything would look identical in the
    drop counters, and `absent_from_dailymed` would absorb the whole release."""
    found = dm.extract_subject_uniis(_document("", set_id="SET-1"))
    assert found is not None and found.set_id == "SET-1"

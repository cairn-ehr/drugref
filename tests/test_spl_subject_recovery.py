"""Tests for the subject-recovery probe.

**Throwaway spike code for the slice 5c.3 design round.**

The design round has to decide what happens to the 41,056 section-carrying
labels whose subject drug cannot be keyed, because openFDA's normalising
``openfda`` block is absent from them. The known recovery route is
``set_id`` -> DailyMed's own XML, which carries the ingredient list.

Two questions, and this module answers them with two different passes:

* **How much is there to recover at all?** Wordings are shared 2.50 labels to
  one, so an unkeyed label whose wording ALSO appears on a keyed label adds no
  new statement -- only another manufacturer saying the same thing. Answered
  from the openFDA cache alone, with no DailyMed scan.
* **Does DailyMed actually carry a subject for the rest?** Answered by reading
  the ingredient block out of the source XML.

The parsing half is pinned here against a miniature SPL document, for the same
reason the cross-check is: a recovery probe that silently reads the WRONG
substance produces a confident number pointing the wrong way. Two mistakes are
specifically guarded -- taking an *inactive* ingredient (an excipient is not
what a label is about) and taking the salt when drugref keys the moiety.
"""
from __future__ import annotations

import pytest

from tools.spl_subject_recovery import (
    augment_rows,
    SubjectUniis,
    WordingReachability,
    classify_wordings,
    orphan_label_targets,
    split_wordings_by_reachability,
    summarise_recovery,
    extract_subject_uniis,
)

# A label whose active ingredient is a salt (the substance) of a different
# active moiety, shaped exactly like the real thing: two nested <activeMoiety>
# elements, the UNII carried in the `code` attribute under codeSystem
# 2.16.840.1.113883.4.9 (FDA SRS). The inactive ingredient carries a UNII too,
# and must never be read as the label's subject.
SPL = b"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3">
  <setId root="set-abc"/>
  <component><structuredBody><component><section>
    <subject><manufacturedProduct><manufacturedProduct>
      <activeIngredient>
        <activeIngredientSubstance>
          <code code="SALTUNII01" codeSystem="2.16.840.1.113883.4.9"/>
          <name>metoprolol tartrate</name>
          <activeMoiety><activeMoiety>
            <code code="MOIETYUNII" codeSystem="2.16.840.1.113883.4.9"/>
            <name>metoprolol</name>
          </activeMoiety></activeMoiety>
        </activeIngredientSubstance>
      </activeIngredient>
      <inactiveIngredient>
        <inactiveIngredientSubstance>
          <code code="EXCIPIENT1" codeSystem="2.16.840.1.113883.4.9"/>
          <name>lactose monohydrate</name>
        </inactiveIngredientSubstance>
      </inactiveIngredient>
    </manufacturedProduct></manufacturedProduct></subject>
  </section></component></structuredBody></component>
</document>"""


def test_the_active_moiety_and_the_substance_are_reported_SEPARATELY():
    # drugref's moiety_uuid is UUIDv5 on the MOIETY's UNII, so the salt's UNII
    # resolves to nothing. Both are returned rather than one being picked,
    # because the measurement's whole question is which of them resolves.
    recovered = extract_subject_uniis(SPL)
    assert recovered is not None
    assert recovered.set_id == "set-abc"
    assert recovered.moiety_uniis == ("MOIETYUNII",)
    assert recovered.substance_uniis == ("SALTUNII01",)


def test_an_INACTIVE_ingredient_is_never_the_subject():
    # An excipient is present in the product and is not what the label is about.
    # Reading it would attach a real interaction statement to lactose.
    recovered = extract_subject_uniis(SPL)
    assert recovered is not None
    assert "EXCIPIENT1" not in recovered.substance_uniis
    assert "EXCIPIENT1" not in recovered.moiety_uniis


def test_a_code_from_a_DIFFERENT_code_system_is_not_a_unii():
    # <code> elements are everywhere in SPL and most of them are not UNIIs.
    # Keying on the element name alone would harvest dosage-form and route
    # codes as if they were substances.
    other = SPL.replace(b'codeSystem="2.16.840.1.113883.4.9"', b'codeSystem="2.16.840.1.113883.3.26.1.1"')
    recovered = extract_subject_uniis(other)
    assert recovered is not None
    assert recovered.moiety_uniis == ()
    assert recovered.substance_uniis == ()


def test_the_older_ingredient_classCode_spelling_is_read_too():
    # SPL also spells an active ingredient <ingredient classCode="ACTIB"> with
    # an <ingredientSubstance> child. A parser that knows only the
    # activeIngredientSubstance spelling under-counts, and under-counting here
    # would understate the recovery route rather than overstate it -- which is
    # the direction that quietly kills a design option.
    older = (
        SPL.replace(b"<activeIngredient>", b'<ingredient classCode="ACTIB">')
        .replace(b"</activeIngredient>", b"</ingredient>")
        .replace(b"activeIngredientSubstance", b"ingredientSubstance")
    )
    recovered = extract_subject_uniis(older)
    assert recovered is not None
    assert recovered.moiety_uniis == ("MOIETYUNII",)
    assert recovered.substance_uniis == ("SALTUNII01",)


def test_an_inactive_ingredient_in_the_classCode_spelling_is_still_excluded():
    # The same trap in the other spelling: classCode="IACT" is an excipient.
    inactive = b"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3">
  <setId root="set-x"/>
  <ingredient classCode="IACT">
    <ingredientSubstance>
      <code code="EXCIPIENT1" codeSystem="2.16.840.1.113883.4.9"/>
    </ingredientSubstance>
  </ingredient>
</document>"""
    recovered = extract_subject_uniis(inactive)
    assert recovered is not None
    assert recovered.substance_uniis == ()
    assert recovered.moiety_uniis == ()


def test_unparseable_xml_is_None_rather_than_an_exception():
    assert extract_subject_uniis(b"<document") is None


def test_a_label_with_no_set_id_is_unusable_and_says_so():
    # The set_id is the join key back to openFDA. Without it a recovered
    # subject cannot be attached to anything.
    assert extract_subject_uniis(b'<document xmlns="urn:hl7-org:v3"/>') is None


# --- the cheap bound: what is reachable at all --------------------------------


def test_a_wording_carried_by_any_keyed_label_is_not_an_orphan():
    # Two labels, same wording; one of them carries a UNII. Recovering the
    # other adds no statement drugref does not already have -- it is a second
    # manufacturer printing the same words.
    rows = [
        {"set_id": "a", "text_key": "w1", "uniis": ["U1"]},
        {"set_id": "b", "text_key": "w1", "uniis": []},
    ]
    reach = classify_wordings(rows)
    assert reach.keyed_wordings == 1
    assert reach.orphan_wordings == 0
    assert reach.redundant_unkeyed_labels == 1
    assert reach.recoverable_unkeyed_labels == 0


def test_a_wording_carried_ONLY_by_unkeyed_labels_is_the_recoverable_population():
    rows = [
        {"set_id": "a", "text_key": "w1", "uniis": []},
        {"set_id": "b", "text_key": "w1", "uniis": []},
        {"set_id": "c", "text_key": "w2", "uniis": ["U1"]},
    ]
    reach = classify_wordings(rows)
    assert reach.keyed_wordings == 1
    assert reach.orphan_wordings == 1
    assert reach.recoverable_unkeyed_labels == 2
    assert reach.redundant_unkeyed_labels == 0


def test_the_reachability_tally_refuses_to_exist_unless_it_adds_up():
    # Same guard as Census: this project has already published a tally that
    # accounted for 40 of its 50 labels.
    with pytest.raises(ValueError):
        WordingReachability(
            keyed_wordings=1,
            orphan_wordings=1,
            distinct_wordings=3,  # 1 + 1 != 3
            keyed_labels=1,
            recoverable_unkeyed_labels=1,
            redundant_unkeyed_labels=0,
            labels=2,
        )


def test_the_label_tally_must_add_up_too():
    with pytest.raises(ValueError):
        WordingReachability(
            keyed_wordings=1,
            orphan_wordings=1,
            distinct_wordings=2,
            keyed_labels=1,
            recoverable_unkeyed_labels=1,
            redundant_unkeyed_labels=0,
            labels=5,  # 1 + 1 + 0 != 5
        )


# --- the scan target list, and what the scan's results mean -------------------


def test_only_unkeyed_labels_on_ORPHAN_wordings_are_worth_scanning_for():
    # Scanning 17.6 GB for a label whose wording a keyed label already carries
    # spends the expensive pass on a statement drugref can already reach.
    rows = [
        {"set_id": "a", "text_key": "w1", "uniis": ["U1"]},
        {"set_id": "b", "text_key": "w1", "uniis": []},   # redundant
        {"set_id": "c", "text_key": "w2", "uniis": []},   # orphan
        {"set_id": "d", "text_key": "w2", "uniis": []},   # orphan, same wording
    ]
    targets = orphan_label_targets(rows)
    assert targets == {"c": "w2", "d": "w2"}


def test_recovery_is_counted_in_WORDINGS_rescued_not_labels_found():
    # Two labels carrying one orphan wording rescue ONE statement between them.
    # Counting labels would report the de-duplication factor as a result.
    targets = {"c": "w2", "d": "w2", "e": "w3"}
    recovered = [
        SubjectUniis(set_id="c", moiety_uniis=("KNOWN1",), substance_uniis=()),
        SubjectUniis(set_id="d", moiety_uniis=("KNOWN1",), substance_uniis=()),
    ]
    summary = summarise_recovery(recovered, targets, known_uniis={"KNOWN1"})
    assert summary.labels_found == 2
    assert summary.labels_resolved == 2
    assert summary.wordings_rescued == 1
    assert summary.wordings_targeted == 2
    assert summary.labels_missing_from_dailymed == 1


def test_a_label_resolving_only_on_its_SALT_is_reported_on_its_own_route():
    # drugref keys the moiety. A label whose moiety UNII is unknown but whose
    # salt UNII is held is a DIFFERENT design question -- it needs a salt->base
    # step (issue #67) -- so it must not be folded into the moiety count.
    targets = {"c": "w2"}
    recovered = [
        SubjectUniis(set_id="c", moiety_uniis=("UNKNOWN",), substance_uniis=("SALT1",)),
    ]
    summary = summarise_recovery(recovered, targets, known_uniis={"SALT1"})
    assert summary.resolved_on_moiety == 0
    assert summary.resolved_on_substance_only == 1
    assert summary.labels_resolved == 1


def test_a_label_DailyMed_carries_but_cannot_key_is_its_own_population():
    targets = {"c": "w2"}
    recovered = [SubjectUniis(set_id="c", moiety_uniis=(), substance_uniis=())]
    summary = summarise_recovery(recovered, targets, known_uniis={"KNOWN1"})
    assert summary.labels_found == 1
    assert summary.labels_without_any_unii == 1
    assert summary.labels_resolved == 0
    assert summary.wordings_rescued == 0


def test_a_recovered_set_id_that_was_never_a_target_is_refused():
    # The scan filters on the target set, so a foreign set_id here means the
    # cache and the scan disagree about which corpus they read -- a silent
    # mismatch that would inflate every figure below it.
    with pytest.raises(ValueError):
        summarise_recovery(
            [SubjectUniis(set_id="zz", moiety_uniis=(), substance_uniis=())],
            {"c": "w2"},
            known_uniis=set(),
        )


def test_the_two_wording_populations_are_returned_as_disjoint_sets():
    # The density comparison needs the KEYS, not the counts: whether orphan
    # wordings are comparable material decides whether 56% of wordings is
    # anywhere near 56% of the yield.
    rows = [
        {"set_id": "a", "text_key": "w1", "uniis": ["U1"]},
        {"set_id": "b", "text_key": "w1", "uniis": []},
        {"set_id": "c", "text_key": "w2", "uniis": []},
    ]
    keyed, orphan = split_wordings_by_reachability(rows)
    assert keyed == {"w1"}
    assert orphan == {"w2"}
    assert not (keyed & orphan)


def test_recovery_fills_an_EMPTY_subject_and_never_overwrites_a_keyed_one():
    # openFDA's own openfda.unii is the authority where it exists. Overwriting
    # it with a DailyMed reading would silently change the baseline the delta
    # is measured against, and the delta is the whole point of the exercise.
    rows = [
        {"set_id": "a", "text_key": "w1", "uniis": ["KEYED"]},
        {"set_id": "c", "text_key": "w2", "uniis": []},
        {"set_id": "e", "text_key": "w3", "uniis": []},
    ]
    recovered = {
        "a": SubjectUniis(set_id="a", moiety_uniis=("OTHER",), substance_uniis=()),
        "c": SubjectUniis(set_id="c", moiety_uniis=("FOUND",), substance_uniis=("SALT",)),
    }
    augmented = {row["set_id"]: row["uniis"] for row in augment_rows(rows, recovered)}
    assert augmented["a"] == ["KEYED"]          # untouched
    assert augmented["c"] == ["FOUND", "SALT"]  # filled
    assert augmented["e"] == []                 # nothing was found for it


def test_one_set_id_read_TWICE_is_one_label():
    # DailyMed ships several documents carrying the same set_id -- successive
    # versions of one label -- so the scan legitimately emits more rows than
    # labels. Counting rows inflated `labels_found` by 44 on the real corpus
    # and was caught by cross-checking the total against an independent pass.
    # A label is its set_id; the scan's row count is not a population.
    targets = {"c": "w2"}
    twice = [
        SubjectUniis(set_id="c", moiety_uniis=("KNOWN1",), substance_uniis=()),
        SubjectUniis(set_id="c", moiety_uniis=("KNOWN1",), substance_uniis=()),
    ]
    summary = summarise_recovery(twice, targets, known_uniis={"KNOWN1"})
    assert summary.labels_found == 1
    assert summary.labels_resolved == 1
    assert summary.resolved_on_moiety == 1
    assert summary.labels_missing_from_dailymed == 0
    assert summary.wordings_rescued == 1

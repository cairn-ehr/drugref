"""Tests for the subject-recovery probe.

**Throwaway spike code for the slice 5c.3 design round.**

The design round has to decide what happens to the 40,856 section-carrying
labels carrying no ``unii`` in openFDA's normalising ``openfda`` block -- which
is present on all of them and merely empty. (The parent round's 41,056 is a
different population: labels with no *resolvable* subject, 200 more.) The known
recovery route is ``set_id`` -> DailyMed's own XML, which carries the ingredient
list.

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

from tools.spl_subject_read import (
    SubjectUniis,
    dedupe_by_set_id,
    extract_subject_uniis,
    subject_uniis,
)
from tools.spl_subject_recovery import (
    RecoverySummary,
    WordingReachability,
    augment_rows,
    classify_wordings,
    orphan_label_targets,
    split_wordings_by_reachability,
    summarise_recovery,
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


def test_the_DOMINANT_classCode_ingredient_spelling_is_read_too():
    # SPL also spells an active ingredient <ingredient classCode="ACTIB"> with
    # an <ingredientSubstance> child, and measured on the release this round
    # read, that spelling is the DOMINANT one -- part6 carries 2,912 of them and
    # zero <activeIngredientSubstance>. No label in a 3,000-label sample used
    # both, so they are alternatives. A parser knowing only the
    # activeIngredientSubstance spelling would have recovered close to nothing,
    # which is the direction that quietly kills a design option.
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
    augmented = {
        row["set_id"]: row["uniis"]
        for row in augment_rows(
            rows, recovered, known_uniis={"KEYED", "OTHER", "FOUND", "SALT"}
        )
    }
    assert augmented["a"] == ["KEYED"]   # untouched
    assert augmented["c"] == ["FOUND"]   # filled, on the MOIETY route only
    assert augmented["e"] == []          # nothing was found for it


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


# --- the subject rule, which must be ONE rule ---------------------------------
#
# The probe published its route-2 delta with a subject rule BROADER than the
# baseline it was compared against: `augment_rows` handed the pair counter the
# moiety UNII *and* the salt UNII, and drugref registers the salt as its own
# moiety with its own live UNII claim (`metoprolol` GEB06NHM23 vs `metoprolol
# tartrate` W5S57Y3A5L). So a salt product contributed TWO subjects and formed
# pairs against every named partner twice, while the openFDA baseline arm
# contributed one. Measured on the real release, 56.7% of resolvable DailyMed
# labels gained an extra subject that way.
#
# `subject_uniis` is now the single rule, used by both the resolve stage and the
# yield stage, and it is the design spec's route table: the moiety when the
# moiety resolves, the salt ONLY when it does not (the `dailymed_active_substance`
# route, issue #67).


def test_the_moiety_wins_when_it_resolves_and_the_salt_is_NOT_added_too():
    found = SubjectUniis(
        set_id="c", moiety_uniis=("MOIETY",), substance_uniis=("SALT",)
    )
    assert subject_uniis(found, {"MOIETY", "SALT"}) == ("MOIETY",)


def test_the_salt_is_used_ONLY_when_no_moiety_unii_resolves():
    # This is the `dailymed_active_substance` route -- 16 labels on the real
    # release. It is a real resolution, so it must not be dropped; it is a
    # DIFFERENT route, so it must not be blended into the moiety count.
    found = SubjectUniis(
        set_id="c", moiety_uniis=("UNKNOWN",), substance_uniis=("SALT",)
    )
    assert subject_uniis(found, {"SALT"}) == ("SALT",)


def test_a_label_resolving_on_neither_contributes_no_subject_at_all():
    found = SubjectUniis(
        set_id="c", moiety_uniis=("UNKNOWN",), substance_uniis=("ALSO_UNKNOWN",)
    )
    assert subject_uniis(found, {"SOMETHING_ELSE"}) == ()


def test_augment_rows_uses_the_SAME_rule_the_resolve_stage_reports():
    # The delta is only a delta if both arms key subjects the same way. Before
    # this, `resolve` reported a salt-only label on its own route while `yield`
    # silently credited the salt route in the headline pair figure.
    rows = [
        {"set_id": "a", "text_key": "w1", "uniis": ["KEYED"]},
        {"set_id": "c", "text_key": "w2", "uniis": []},
    ]
    recovered = {
        "c": SubjectUniis(
            set_id="c", moiety_uniis=("MOIETY",), substance_uniis=("SALT",)
        )
    }
    augmented = {
        row["set_id"]: row["uniis"]
        for row in augment_rows(rows, recovered, known_uniis={"MOIETY", "SALT"})
    }
    assert augmented["a"] == ["KEYED"]   # openFDA stays the authority
    assert augmented["c"] == ["MOIETY"]  # NOT ["MOIETY", "SALT"]


# --- one de-duplication policy, not two ---------------------------------------


def test_the_HIGHEST_version_of_a_duplicated_set_id_wins():
    # DailyMed ships successive versions of one label as separate documents
    # sharing a set_id. The two consumers of the scan output used to disagree --
    # `summarise_recovery` kept the FIRST row seen and `_load_recovered` the
    # LAST -- so the resolve table and the pair delta were computed from
    # different readings of the same 44 labels. Neither "first" nor "last" is a
    # rule; the version number is.
    rows = [
        SubjectUniis(set_id="c", moiety_uniis=("OLD",), substance_uniis=(), version=1),
        SubjectUniis(set_id="c", moiety_uniis=("NEW",), substance_uniis=(), version=3),
        SubjectUniis(set_id="c", moiety_uniis=("MID",), substance_uniis=(), version=2),
    ]
    assert dedupe_by_set_id(rows)["c"].moiety_uniis == ("NEW",)


def test_an_unversioned_row_never_displaces_a_versioned_one():
    rows = [
        SubjectUniis(set_id="c", moiety_uniis=("KNOWN",), substance_uniis=(), version=2),
        SubjectUniis(set_id="c", moiety_uniis=("NOVERSION",), substance_uniis=()),
    ]
    assert dedupe_by_set_id(rows)["c"].moiety_uniis == ("KNOWN",)


def test_the_version_is_read_off_the_label():
    versioned = SPL.replace(
        b'<setId root="set-abc"/>',
        b'<setId root="set-abc"/><versionNumber value="7"/>',
    )
    assert extract_subject_uniis(versioned).version == 7


# --- the population that had no bucket ----------------------------------------


def test_a_label_whose_UNII_drugref_does_not_hold_gets_its_OWN_bucket():
    # 25 labels on the real release: found in DailyMed, carrying a UNII, and
    # drugref has never heard of it. They incremented NOTHING, so `labels_found`
    # minus the named buckets left 25 labels in a population the report never
    # named -- and "99.6% of those found resolve" is only true if they do not
    # exist. They are a registry coverage gap, which is a finding, not a rounding.
    targets = {"c": "w2"}
    recovered = [
        SubjectUniis(set_id="c", moiety_uniis=("NEVER_HEARD_OF_IT",), substance_uniis=())
    ]
    summary = summarise_recovery(recovered, targets, known_uniis={"KNOWN1"})
    assert summary.labels_found == 1
    assert summary.labels_without_any_unii == 0
    assert summary.labels_resolved == 0
    assert summary.labels_found_but_unresolvable == 1


def test_the_recovery_tally_refuses_to_exist_unless_the_found_labels_add_up():
    with pytest.raises(ValueError):
        RecoverySummary(
            wordings_targeted=1, labels_targeted=10,
            labels_found=10, labels_missing_from_dailymed=0,
            labels_without_any_unii=1, labels_resolved=1,
            labels_found_but_unresolvable=1,   # 1 + 1 + 1 != 10
            resolved_on_moiety=1, resolved_on_substance_only=0,
            wordings_rescued=1,
        )


def test_the_recovery_tally_refuses_a_negative_bucket():
    # The pre-fix over-count produced `len(targets) - found` going wrong in the
    # other direction; a bucket that can go negative is a bucket nobody checked.
    with pytest.raises(ValueError):
        RecoverySummary(
            wordings_targeted=1, labels_targeted=1,
            labels_found=2, labels_missing_from_dailymed=-1,
            labels_without_any_unii=0, labels_resolved=2,
            labels_found_but_unresolvable=0,
            resolved_on_moiety=2, resolved_on_substance_only=0,
            wordings_rescued=1,
        )


def test_the_two_resolution_routes_must_sum_to_the_resolved_total():
    with pytest.raises(ValueError):
        RecoverySummary(
            wordings_targeted=1, labels_targeted=1,
            labels_found=1, labels_missing_from_dailymed=0,
            labels_without_any_unii=0, labels_resolved=1,
            labels_found_but_unresolvable=0,
            resolved_on_moiety=0, resolved_on_substance_only=0,  # 0 + 0 != 1
            wordings_rescued=1,
        )


def test_labels_missing_from_dailymed_is_COUNTED_not_derived_by_subtraction():
    # `len(targets) - found` absorbs any upstream drop without residue: the
    # round's 44-label over-count still balanced that identity exactly
    # (6,583 + 19,818 = 26,401), which is why the guard would not have caught
    # it. Counted independently, the same bad input no longer balances.
    targets = {"c": "w2", "d": "w3", "e": "w4"}
    recovered = [
        SubjectUniis(set_id="c", moiety_uniis=("KNOWN1",), substance_uniis=()),
        SubjectUniis(set_id="c", moiety_uniis=("KNOWN1",), substance_uniis=()),
    ]
    summary = summarise_recovery(recovered, targets, known_uniis={"KNOWN1"})
    assert summary.labels_found == 1
    assert summary.labels_missing_from_dailymed == 2   # d and e, by name


# --- the target list must not silently collapse -------------------------------


def test_two_cache_rows_sharing_a_set_id_are_refused_rather_than_collapsed():
    # `classify_wordings` counts ROWS and `orphan_label_targets` keys a dict by
    # set_id, so a collision would make the two published populations disagree
    # silently -- the same row-vs-label confusion the round was burned by, one
    # function to the left. They agreed on the real corpus; nothing pinned it.
    rows = [
        {"set_id": "c", "text_key": "w2", "uniis": []},
        {"set_id": "c", "text_key": "w3", "uniis": []},
    ]
    with pytest.raises(ValueError):
        orphan_label_targets(rows)


def test_a_row_with_no_set_id_is_refused_rather_than_keyed_on_empty_string():
    # `record.get("set_id") or record.get("id") or ""` mints "" upstream, and
    # every such row would collapse into one dict entry -- deleting wordings
    # from the universe before the expensive pass even starts.
    with pytest.raises(ValueError):
        orphan_label_targets([{"set_id": "", "text_key": "w2", "uniis": []}])


# --- reachability invariants that are not restatements ------------------------


def test_a_keyed_wording_cannot_be_carried_by_fewer_keyed_labels_than_wordings():
    with pytest.raises(ValueError):
        WordingReachability(
            distinct_wordings=10, keyed_wordings=10, orphan_wordings=0,
            labels=10, keyed_labels=3,          # 3 labels cannot key 10 wordings
            recoverable_unkeyed_labels=7, redundant_unkeyed_labels=0,
        )


def test_no_bucket_in_the_reachability_tally_may_be_negative():
    with pytest.raises(ValueError):
        WordingReachability(
            distinct_wordings=7, keyed_wordings=10, orphan_wordings=-3,
            labels=10, keyed_labels=10,
            recoverable_unkeyed_labels=0, redundant_unkeyed_labels=0,
        )


# --- SPL shapes the fixture did not cover -------------------------------------


def test_a_COMBINATION_product_offers_every_active_moiety_as_a_subject():
    # Combination products are ordinary and a label may carry more than one
    # subject (design spec 4.3). Each forms pairs independently, so this is a
    # direct multiplier on the published pair count and was untested.
    combo = SPL.replace(
        b"</activeIngredient>",
        b"""</activeIngredient>
      <activeIngredient>
        <activeIngredientSubstance>
          <code code="SALTUNII02" codeSystem="2.16.840.1.113883.4.9"/>
          <activeMoiety><activeMoiety>
            <code code="MOIETYUNI2" codeSystem="2.16.840.1.113883.4.9"/>
          </activeMoiety></activeMoiety>
        </activeIngredientSubstance>
      </activeIngredient>""",
        1,
    )
    recovered = extract_subject_uniis(combo)
    assert recovered.moiety_uniis == ("MOIETYUNI2", "MOIETYUNII")
    assert recovered.substance_uniis == ("SALTUNII01", "SALTUNII02")


def test_a_non_UNII_code_ALONGSIDE_a_real_one_does_not_poison_the_reading():
    # The original test mutated EVERY code system at once, so it would still
    # have passed if the filter rejected one specific system rather than
    # accepting one. A dosage-form code sitting next to a real UNII is the shape
    # that actually occurs.
    with_dosage_form = SPL.replace(
        b'<code code="SALTUNII01" codeSystem="2.16.840.1.113883.4.9"/>',
        b'<code code="C42931" codeSystem="2.16.840.1.113883.3.26.1.1"/>'
        b'<code code="SALTUNII01" codeSystem="2.16.840.1.113883.4.9"/>',
    )
    recovered = extract_subject_uniis(with_dosage_form)
    assert "C42931" not in recovered.substance_uniis
    assert recovered.moiety_uniis == ("MOIETYUNII",)

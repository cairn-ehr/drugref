# tests/test_spl_subject.py
"""Which drug is a label's interactions section ABOUT, and how do we know.

An interaction statement with no subject is not an interaction statement, and
`moiety_uuid` is UUIDv5 on UNII -- so the subject question is entirely a question
about UNIIs. Three structural routes answer it and two of them come from
DailyMed; a fourth, heuristic route (rank-0 of `spl_product_data_elements`) was
measured at **6.2% genuinely wrong** and does not ship.

**THE ROUTES ARE EXCLUSIVE BY CONSTRUCTION**, which is the rule `db/051`'s
`spl_label_subject_complete` CHECK rests on: one label, one route, and the salt
is never a second subject beside the moiety.
"""
import pytest

from drugref.ingest import spl_dailymed, spl_subject


KNOWN = {"OPENFDA-U": "uuid-openfda", "BASE-U": "uuid-base", "SALT-U": "uuid-salt"}


def _found(moiety=(), substance=()) -> spl_dailymed.SubjectUniis:
    return spl_dailymed.SubjectUniis(
        set_id="S", moiety_uniis=tuple(moiety), substance_uniis=tuple(substance))


# --------------------------------------------------------------------------
# Route 1 -- openFDA's own bridge, and it is the AUTHORITY where it exists
# --------------------------------------------------------------------------

def test_an_openfda_unii_that_resolves_is_the_subject():
    subject = spl_subject.resolve_subject(
        openfda_uniis=("OPENFDA-U",), dailymed=None, known_uniis=KNOWN)
    assert subject.route == "openfda_unii"
    assert subject.moiety_uuids == ("uuid-openfda",)


def test_a_combination_product_carries_SEVERAL_subjects_on_one_route():
    """Combination products are ordinary, which is why the subject is its own
    table rather than a column on the label."""
    subject = spl_subject.resolve_subject(
        openfda_uniis=("OPENFDA-U", "BASE-U"), dailymed=None, known_uniis=KNOWN)
    assert subject.route == "openfda_unii"
    assert sorted(subject.moiety_uuids) == ["uuid-base", "uuid-openfda"]


def test_openfda_is_NOT_overwritten_by_a_dailymed_reading():
    """openFDA's own block is the authority where it exists.

    Preferring DailyMed would move the baseline the published +42.3% delta was
    measured against.
    """
    subject = spl_subject.resolve_subject(
        openfda_uniis=("OPENFDA-U",), dailymed=_found(moiety=("BASE-U",)),
        known_uniis=KNOWN)
    assert subject.route == "openfda_unii"
    assert subject.moiety_uuids == ("uuid-openfda",)


def test_the_moiety_uuids_are_ordered_so_two_runs_over_one_release_agree():
    subject = spl_subject.resolve_subject(
        openfda_uniis=("BASE-U", "OPENFDA-U"), dailymed=None, known_uniis=KNOWN)
    other = spl_subject.resolve_subject(
        openfda_uniis=("OPENFDA-U", "BASE-U"), dailymed=None, known_uniis=KNOWN)
    assert subject.moiety_uuids == other.moiety_uuids


# --------------------------------------------------------------------------
# Route 2 and 3 -- DailyMed, and the salt counted apart
# --------------------------------------------------------------------------

def test_dailymed_answers_where_openfda_offered_nothing():
    subject = spl_subject.resolve_subject(
        openfda_uniis=(), dailymed=_found(moiety=("BASE-U",)), known_uniis=KNOWN)
    assert subject.route == "dailymed_active_moiety"
    assert subject.moiety_uuids == ("uuid-base",)


def test_dailymed_also_answers_where_openfdas_OWN_unii_does_not_resolve():
    """The 200 labels carrying a UNII drugref does not hold.

    The probe's classifiers branched on PRESENCE, so it filed them as keyed and
    excluded their wordings from the recoverable half. They have no subject, so
    the ingest targets them -- which is one of the two reasons the pair count is
    a floor.
    """
    subject = spl_subject.resolve_subject(
        openfda_uniis=("UNHELD-U",), dailymed=_found(moiety=("BASE-U",)),
        known_uniis=KNOWN)
    assert subject.route == "dailymed_active_moiety"


def test_the_salt_route_fires_ONLY_when_no_moiety_unii_resolves():
    subject = spl_subject.resolve_subject(
        openfda_uniis=(), dailymed=_found(moiety=("UNHELD-U",), substance=("SALT-U",)),
        known_uniis=KNOWN)
    assert subject.route == "dailymed_active_substance"
    assert subject.moiety_uuids == ("uuid-salt",)


def test_the_salt_is_never_a_SECOND_subject_beside_the_moiety():
    """The defect that published 31,618 pairs where the rule gives 29,258."""
    subject = spl_subject.resolve_subject(
        openfda_uniis=(), dailymed=_found(moiety=("BASE-U",), substance=("SALT-U",)),
        known_uniis=KNOWN)
    assert subject.moiety_uuids == ("uuid-base",)
    assert "uuid-salt" not in subject.moiety_uuids


# --------------------------------------------------------------------------
# The two routes that resolve NOTHING, and why they are different findings
# --------------------------------------------------------------------------

def test_a_label_absent_from_dailymed_says_so_rather_than_unresolved():
    """19,862 labels are absent from today's release and may be in tomorrow's.

    'Not published there' and 'published and unkeyable' are different findings,
    and folding them together would report a release fact as a registry gap.
    """
    subject = spl_subject.resolve_subject(
        openfda_uniis=(), dailymed=None, known_uniis=KNOWN)
    assert subject.route == "absent_from_dailymed"
    assert subject.moiety_uuids == ()


def test_a_label_READ_and_still_unkeyable_is_unresolved():
    subject = spl_subject.resolve_subject(
        openfda_uniis=(), dailymed=_found(moiety=("UNHELD-U",)), known_uniis=KNOWN)
    assert subject.route == "unresolved"
    assert subject.moiety_uuids == ()


def test_a_label_read_with_no_ingredients_at_all_is_unresolved_not_absent():
    """It was read, and it had nothing -- a different fact from a missing label."""
    subject = spl_subject.resolve_subject(
        openfda_uniis=(), dailymed=_found(), known_uniis=KNOWN)
    assert subject.route == "unresolved"


# --------------------------------------------------------------------------
# The vocabulary, and the invariant db/051's CHECK depends on
# --------------------------------------------------------------------------

def test_a_resolving_route_ALWAYS_carries_a_moiety_and_the_others_never_do():
    """`spl_label_subject_complete` makes both malformed states unrepresentable,
    so the producer must never construct one."""
    cases = [
        (("OPENFDA-U",), None),
        ((), _found(moiety=("BASE-U",))),
        ((), _found(moiety=("UNHELD-U",), substance=("SALT-U",))),
        ((), None),
        ((), _found(moiety=("UNHELD-U",))),
    ]
    for openfda_uniis, dailymed in cases:
        subject = spl_subject.resolve_subject(
            openfda_uniis=openfda_uniis, dailymed=dailymed, known_uniis=KNOWN)
        resolves = subject.route in spl_subject.RESOLVING_ROUTES
        assert resolves == bool(subject.moiety_uuids), subject


def test_the_resolving_routes_are_a_subset_of_the_whole_vocabulary():
    assert set(spl_subject.RESOLVING_ROUTES) < set(spl_subject.SUBJECT_ROUTES)


def test_every_route_the_resolver_can_return_is_in_the_vocabulary():
    """The vocabulary is db/051's CHECK's second home; a value the resolver can
    produce and the CHECK does not admit aborts an ingest at the last row."""
    cases = [
        (("OPENFDA-U",), None), ((), _found(moiety=("BASE-U",))),
        ((), _found(substance=("SALT-U",))), ((), None), ((), _found()),
    ]
    for openfda_uniis, dailymed in cases:
        subject = spl_subject.resolve_subject(
            openfda_uniis=openfda_uniis, dailymed=dailymed, known_uniis=KNOWN)
        assert subject.route in spl_subject.SUBJECT_ROUTES


# --------------------------------------------------------------------------
# Which labels the expensive DailyMed pass has to look for
# --------------------------------------------------------------------------

def test_a_label_whose_openfda_unii_resolves_is_NOT_a_scan_target():
    assert spl_subject.needs_dailymed(
        openfda_uniis=("OPENFDA-U",), known_uniis=KNOWN) is False


def test_a_label_with_no_unii_at_all_IS_a_scan_target():
    assert spl_subject.needs_dailymed(openfda_uniis=(), known_uniis=KNOWN) is True


def test_a_label_whose_unii_drugref_does_not_hold_IS_a_scan_target():
    assert spl_subject.needs_dailymed(
        openfda_uniis=("UNHELD-U",), known_uniis=KNOWN) is True


def test_the_scan_targets_include_labels_sharing_a_KEYED_labels_wording():
    """⇒ THE PROBE SKIPPED 14,455 OF THESE AND THE INGEST MUST NOT.

    Skipping them is valid for the WORDING unit -- another manufacturer
    reprinting a wording drugref can already reach adds no new statement. But a
    label's SUBJECT is its own: an unkeyed label sharing a keyed label's wording
    may be a different drug, and its pairs are uncounted. That is why every pair
    figure in this slice is a floor and the check asserts >=, not ==.
    """
    targets = spl_subject.dailymed_targets(
        [{"set_id": "A", "uniis": ("OPENFDA-U",), "text_key": "k"},
         {"set_id": "B", "uniis": (), "text_key": "k"}],
        known_uniis=KNOWN)
    assert targets == {"B"}


def test_two_cache_rows_sharing_a_set_id_are_refused_rather_than_absorbed():
    """Keying targets by set_id would silently DELETE a label from the universe
    before the expensive pass starts, and the two published populations would
    then disagree with nothing to say so."""
    with pytest.raises(ValueError, match="appears on more than one"):
        spl_subject.dailymed_targets(
            [{"set_id": "A", "uniis": (), "text_key": "k1"},
             {"set_id": "A", "uniis": (), "text_key": "k2"}],
            known_uniis=KNOWN)


def test_a_row_with_no_set_id_is_refused_because_it_can_never_be_joined():
    with pytest.raises(ValueError, match="carries no set_id"):
        spl_subject.dailymed_targets(
            [{"set_id": "", "uniis": (), "text_key": "k"}], known_uniis=KNOWN)

"""Pure orchestration helpers for the pregnancy/lactation source spike."""
from drugref.ingest.lactmed import LactMedRecord
from tools.pregnancy_lactation_spike import IdentityIndex, resolve_lactmed


def record(*, title="Example", uniis=(), cas_numbers=()):
    return LactMedRecord(
        record_id="LM1",
        title=title,
        revised="2026-08-16",
        publisher="NICHD",
        rights="Attribution",
        disclaimer="Clinical judgment required",
        keywords=(),
        cas_numbers=cas_numbers,
        uniis=uniis,
        sections=(),
    )


def index():
    return IdentityIndex(
        claims={
            "UNII": {"ABC1234567": frozenset({"moiety-1"})},
            "CAS": {"123-45-6": frozenset({"moiety-1"})},
        },
        names={"example": frozenset({"moiety-2"})},
    )


def test_exact_source_claims_win_over_a_conflicting_name_candidate():
    status, moieties = resolve_lactmed(
        record(uniis=("ABC1234567",), cas_numbers=("123-45-6",)), index())
    assert status == "resolved_exact_claim"
    assert moieties == frozenset({"moiety-1"})


def test_name_resolution_is_marked_as_a_candidate_not_an_exact_identity():
    status, moieties = resolve_lactmed(record(), index())
    assert status == "candidate_unique_name"
    assert moieties == frozenset({"moiety-2"})


def test_no_match_is_a_published_bucket_not_a_silent_drop():
    status, moieties = resolve_lactmed(record(title="Unknown"), index())
    assert (status, moieties) == ("unresolved", frozenset())

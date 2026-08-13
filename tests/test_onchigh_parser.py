# tests/test_onchigh_parser.py
"""The ONC file's structural rules. PURE -- no DSN, no database.

Every rule here is one a hand-authored file gets wrong, and each is a RAISE
rather than a skip: the file is curated, so a malformed entry is a bug and a
silently-dropped entry is a clinical claim going missing (issue 71's lesson).
"""
import pathlib

import pytest

from drugref import cli_curate
from drugref.ingest import onchigh

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "onc_fixture.toml"

# One valid entry's fields, as defaults for _entry_with below. Mirrors the
# FIXTURE file's own "warfarin-nsaid" entry so the two stay recognisably the
# same shape, but this dict is deliberately separate from the fixture: the
# fixture must stay parseable as a WHOLE FILE (test_parses_a_well_formed_entry
# calls onchigh.parse(FIXTURE) and expects it to succeed), while every test
# below needs a file that is broken in exactly one, controlled way. Mixing
# the two would mean a single committed file could serve neither job.
_DEFAULTS = {
    "entry_id": "warfarin-nsaid",
    "subject_unii": "5Q7ZVV76EI",
    "subject_medrt_code": None,
    "subject_name": "warfarin",
    "object_medrt_code": "N0000175722",
    "object_name": "Nonsteroidal Anti-inflammatory Drug [EPC]",
    "axis": "CI_EPC",
    "citation": "unit test citation, not real ONC content",
    "applies": True,
    "severity": "major",
    "evidence_grade": "established",
}


def _entry_with(**overrides):
    """Emit one valid [[entry]] TOML block, with named fields overridden or
    (passed as None) omitted entirely. Kept as dumb string formatting -- a
    helper that itself needs testing is not a helper -- so every case below
    is readable as "the default entry, minus/instead of this one thing".

    `subject_medrt_code` defaults to None (omitted), so every EXISTING case
    below stays a moiety-subject entry unchanged; a test exercising the
    class-subject shape passes it explicitly (see
    test_a_class_subject_entry_parses and its neighbours below).
    """
    fields = {**_DEFAULTS, **overrides}
    lines = ["[[entry]]", f'entry_id = "{fields["entry_id"]}"', "", "[entry.candidate]"]
    for key in ("subject_unii", "subject_medrt_code", "subject_name",
                "object_medrt_code", "object_name", "axis", "citation"):
        if fields[key] is not None:
            lines.append(f'{key} = "{fields[key]}"')
    lines += ["", "[entry.judgement]", f'applies = {str(fields["applies"]).lower()}']
    for key in ("severity", "evidence_grade"):
        if fields[key] is not None:
            lines.append(f'{key} = "{fields[key]}"')
    return "\n".join(lines) + "\n"


def test_parses_a_well_formed_entry():
    entries = onchigh.parse(FIXTURE)
    entry = next(e for e in entries if e.entry_id == "warfarin-nsaid")
    assert entry.candidate.subject_unii == "5Q7ZVV76EI"
    assert entry.candidate.axis == "CI_EPC"
    assert entry.judgement.applies is True
    assert entry.judgement.severity == "major"


def test_an_unknown_axis_raises(tmp_path):
    """ci_axis is the vocabulary's one home, but the PARSER still refuses a value
    it can see is wrong -- the alternative is an ingest that fails halfway with
    half the file already written."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with(axis="CI_INVENTED"))
    with pytest.raises(onchigh.OncFormatError, match="CI_INVENTED"):
        onchigh.parse(bad)


def test_an_asserting_entry_without_severity_raises(tmp_path):
    """db/029's completeness CHECK would refuse this row anyway. Catching it in
    the parser means the curator learns WHICH ENTRY is wrong, by entry_id, rather
    than reading a constraint name off a traceback."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with(applies=True, severity=None))
    with pytest.raises(onchigh.OncFormatError, match="warfarin-nsaid"):
        onchigh.parse(bad)


def test_a_non_asserting_entry_carrying_a_grade_raises(tmp_path):
    """The other half of the same CHECK: 'not real, but graded major' must be
    unrepresentable, not merely discouraged."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with(applies=False, severity="major"))
    with pytest.raises(onchigh.OncFormatError):
        onchigh.parse(bad)


def test_a_duplicate_entry_id_raises(tmp_path):
    """entry_id is the handle a gap_key is built from, so two entries sharing one
    would mint a single question_uuid for two different gaps."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with() + "\n" + _entry_with())
    with pytest.raises(onchigh.OncFormatError, match="duplicate"):
        onchigh.parse(bad)


def test_a_missing_citation_raises(tmp_path):
    """Rule 6: a claim with no source is exactly what this slice must not ship."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with(citation=None))
    with pytest.raises(onchigh.OncFormatError, match="citation"):
        onchigh.parse(bad)


def test_a_wrong_typed_optional_field_raises_rather_than_vanishing(tmp_path):
    """"Absent" and "present but not a string" are different facts.

    `mechanism` and `management` are the fields a PRESCRIBER reads, and unlike
    severity/evidence_grade nothing downstream requires them -- no CHECK, no
    completeness rule. So a TOML array (an easy mistake, since every real value
    in onc_high_priority.toml is a `\"\"\"` block) used to be read as "field
    absent", the management instruction silently disappeared from the curated
    row, and `curate onchigh` reported a written judgement and exited 0.

    Fewer rows is the harm direction here, and so is less text: an alert that
    says "avoid" without saying what to do instead is a worse alert.
    """
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with() .rstrip("\n")
                   + '\nmanagement = ["avoid the combination", "monitor INR"]\n')
    with pytest.raises(onchigh.OncFormatError, match="management"):
        onchigh.parse(bad)


def test_the_shipped_list_parses_and_carries_its_clinically_reviewed_floor():
    """The committed clinical data file, parsed -- not just referenced by path.

    Every other parser test here runs against tests/fixtures/onc_fixture.toml,
    so the file drugref actually SHIPS was exercised by nothing: it is named in
    tests/test_cli.py only as a path constant. Commit 66321f3 cut it from
    eleven entries to the four the project owner clinically reviewed, and
    nothing asserted that floor -- so a merge, a bad rebase or an over-eager
    edit could drop a contraindication and no test would notice.

    Asserted as `>= 4`, not `== 4`: issue 94's withheld entries are expected to
    return once researched, and a test that fails when the list GROWS would
    train people to edit the assertion rather than read it.
    """
    entries = onchigh.parse(cli_curate.ONC)
    assert len(entries) >= 4
    # Rule 6 is a blocker, not a cleanup item: every shipped claim names where
    # it came from. The parser enforces this per entry; this pins it for the
    # real file rather than only for fixtures.
    assert all(e.candidate.citation for e in entries)


# ---- Task 10: the class-subject shape (design spec section 14) --------------


def test_a_class_subject_entry_parses(tmp_path):
    """subject_medrt_code + subject_name is the alternative subject form --
    a drug CLASS, not a moiety -- and it parses to a candidate carrying no
    subject_unii at all."""
    good = tmp_path / "good.toml"
    good.write_text(_entry_with(
        subject_unii=None, subject_medrt_code="N0000175724",
        subject_name="Monoamine Oxidase Inhibitors [MoA]"))
    entries = onchigh.parse(good)
    entry = next(e for e in entries if e.entry_id == "warfarin-nsaid")
    assert entry.candidate.subject_unii is None
    assert entry.candidate.subject_medrt_code == "N0000175724"
    assert entry.candidate.is_class_subject is True


def test_a_moiety_subject_entry_is_not_a_class_subject(tmp_path):
    """The other half of is_class_subject -- pinned explicitly rather than
    inferred only from the class-subject case above."""
    entries = onchigh.parse(FIXTURE)
    entry = next(e for e in entries if e.entry_id == "warfarin-nsaid")
    assert entry.candidate.is_class_subject is False


def test_both_subject_forms_present_raises(tmp_path):
    """subject_unii and subject_medrt_code are MUTUALLY EXCLUSIVE -- a subject
    cannot be both a single moiety and a whole class at once, and a file
    carrying both leaves it ambiguous which one drugref should resolve."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with(subject_medrt_code="N0000175724"))
    with pytest.raises(onchigh.OncFormatError, match="warfarin-nsaid"):
        onchigh.parse(bad)


def test_neither_subject_form_present_raises(tmp_path):
    """The other half of the same rule: a subject named by NEITHER form is not
    a class-subject entry left implicit, it is a broken entry with no subject
    at all."""
    bad = tmp_path / "bad.toml"
    bad.write_text(_entry_with(subject_unii=None))
    with pytest.raises(onchigh.OncFormatError, match="warfarin-nsaid"):
        onchigh.parse(bad)

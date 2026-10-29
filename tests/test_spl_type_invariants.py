# tests/test_spl_type_invariants.py
"""Issue #165: invariants the SPL types claimed and did not hold.

Two families. A `frozen=True` dataclass freezes the REFERENCE to a mapping, not
the mapping, so every `__post_init__` check over a caller's live `dict` could be
broken after construction by a later `dict.update`. And three matcher types
admitted values their docstrings call impossible: an empty `Match`, a
`NextWordProfile` followed more often than it occurs, and a folded vocabulary
key carrying a suppression term AND a name (which quietly stops the suppression
working and changes what `match_ambiguous` means).
"""
from __future__ import annotations

import pytest

from drugref.ingest import spl, spl_match, spl_suppress
from tests.conftest import clean_scan
from tests.test_spl_run import _summary


# --------------------------------------------------------------------------
# frozen means frozen: the mappings are copied, not referenced
# --------------------------------------------------------------------------

def test_a_Corpus_does_not_follow_its_callers_dicts_after_construction():
    wordings: dict[str, str] = {}
    label_counts: dict[str, int] = {}
    corpus = spl.Corpus(records=0, labels=(), wordings=wordings,
                        label_counts=label_counts, field_absent=0,
                        field_blank=0, labels_keyed_on_document_id=0)
    wordings["k"] = "a wording no label carries"
    label_counts["k"] = 5
    assert dict(corpus.wordings) == {}
    assert dict(corpus.label_counts) == {}
    with pytest.raises(TypeError):
        corpus.wordings["k"] = "x"


def test_a_ScanResult_does_not_follow_its_callers_dict():
    found: dict = {}
    scan = clean_scan(found=found)
    found["SET-1"] = object()
    assert dict(scan.found) == {}


def test_an_SplSummary_cannot_be_unreconciled_after_construction():
    """`SplSummary`'s whole purpose is buckets that RECONCILE; a reconciliation
    a later `dict.update` could undo was weaker than its docstring claimed."""
    routes = {"openfda_unii": 6, "unresolved": 4}
    summary = _summary(labels_by_route=routes)
    routes["unresolved"] = 400
    assert summary.labels_by_route["unresolved"] == 4


# --------------------------------------------------------------------------
# the matcher's types
# --------------------------------------------------------------------------

def _entry(kind: str, key: str) -> spl_match.Entry:
    uuid = "00000000-0000-5000-8000-000000000001" if kind == "moiety" else None
    return spl_match.Entry(kind=kind, key=key, display=key, moiety_uuid=uuid)


def test_a_suppression_term_folding_onto_a_NAME_is_refused():
    """Otherwise `moiety_occurrences` skips the suppress entry and still emits
    the moiety, flagged `match_ambiguous` -- a suppression that silently stopped
    working, and a flag meaning something other than db/051's comment says."""
    with pytest.raises(ValueError, match="suppression term"):
        spl_match.build_vocabulary(
            [_entry("suppress", "lead to"), _entry("moiety", "Lead-To")])


def test_two_NAMES_on_one_key_are_still_admitted_as_an_ambiguity():
    """The control: ambiguity between names (stereoisomers whose suffix the fold
    strips) is real, measured, and recorded -- not refused."""
    vocab = spl_match.build_vocabulary(
        [_entry("moiety", "carvone, (+)-"), _entry("moiety", "carvone")])
    (match,) = spl_match.find_matches("avoid carvone", vocab)
    assert match.ambiguous


def test_a_Match_with_no_entries_is_refused():
    """`ambiguous` read an empty match as UNambiguous."""
    with pytest.raises(ValueError, match="no entries"):
        spl_match.Match(entries=(), char_start=0, char_end=1,
                        token_start=0, token_end=1)


def test_a_NextWordProfile_followed_more_often_than_it_occurs_is_refused():
    """`dominance` could otherwise exceed 1.0."""
    with pytest.raises(ValueError, match="followed 5 time"):
        spl_suppress.NextWordProfile(name="lead", occurrences=3,
                                     following={"to": 5})


# --------------------------------------------------------------------------
# PR #185 review: the freeze has to REFUSE a write, not merely copy
# --------------------------------------------------------------------------
# A plain `dict(...)` copy passes every "does not follow its caller" test above
# and still lets `scan.found[k] = v` undo the invariant from inside -- so each
# frozen mapping is also checked for refusing a write outright.

def test_a_ScanResult_refuses_a_write_into_found():
    with pytest.raises(TypeError):
        clean_scan(found={}).found["SET-1"] = object()


def test_an_SplSummary_refuses_a_write_into_its_buckets():
    with pytest.raises(TypeError):
        _summary(labels_by_route={"openfda_unii": 10}).labels_by_route["x"] = 1


def test_a_Corpus_freezes_a_labels_LIST_into_a_tuple():
    """`labels` is counted against `records` in `__post_init__`; a list the
    caller still holds could grow afterwards and break the count."""
    labels: list = []
    corpus = spl.Corpus(records=0, labels=labels, wordings={}, label_counts={},
                        field_absent=0, field_blank=0,
                        labels_keyed_on_document_id=0)
    assert corpus.labels == ()
    assert isinstance(corpus.labels, tuple)


def test_a_Vocabulary_does_not_follow_its_callers_dicts_and_refuses_writes():
    """Both maps feed `__post_init__`'s reach and suppress-onto-name checks; a
    later `max_tokens_by_first.clear()` would make the matcher under-match
    silently -- the failure those checks exist to prevent."""
    vocab = spl_match.build_vocabulary([_entry("moiety", "warfarin")])
    by_first = dict(vocab.max_tokens_by_first)
    rebuilt = spl_match.Vocabulary(by_key=dict(vocab.by_key), max_tokens=1,
                                   max_tokens_by_first=by_first)
    by_first.clear()
    assert dict(rebuilt.max_tokens_by_first) == {"warfarin": 1}
    with pytest.raises(TypeError):
        rebuilt.by_key[("lead", "to")] = (_entry("suppress", "lead to"),)
    with pytest.raises(TypeError):
        rebuilt.max_tokens_by_first["warfarin"] = 0


def test_a_vocabulary_key_with_NO_entries_is_refused():
    with pytest.raises(ValueError, match="no"):
        spl_match.Vocabulary(by_key={("warfarin",): ()}, max_tokens=1,
                             max_tokens_by_first={"warfarin": 1})


def test_a_NextWordProfile_does_not_follow_its_callers_dict():
    """Mutating `following` after construction made `dominance` 9.0 -- the
    exact value `__post_init__` refuses (#165)."""
    following = {"to": 1}
    profile = spl_suppress.NextWordProfile(name="lead", occurrences=1,
                                           following=following)
    following["to"] = 9
    assert profile.following["to"] == 1
    with pytest.raises(TypeError):
        profile.following["to"] = 9

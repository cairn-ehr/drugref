# src/drugref/ingest/spl_rows.py
"""The rows one SPL run writes, built from what it read. PURE: no database.

Split out of `spl_run` (#216, rule 4). `spl_run` stays the only writer: it calls
these, then hands the rows to `spl_evidence`'s COPY writers inside its transaction.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from drugref import spl_evidence
from drugref.ingest import spl, spl_dailymed, spl_match, spl_quote, spl_subject


def subject_rows(
    labels: Sequence[spl.LabelIdentity],
    recovered: Mapping[str, spl_dailymed.SubjectUniis],
    known_uniis: Mapping[str, str],
) -> tuple[list[spl_evidence.SubjectRow], dict[str, int]]:
    """One subject row per (label, moiety), plus one per label with no subject.

    Returns the rows and the per-route label tally. The tally counts LABELS, not
    rows: a combination product carries several subjects on one route, and
    reporting rows here would publish the combination rate as if it were a
    resolution rate.
    """
    rows: list[spl_evidence.SubjectRow] = []
    by_route: dict[str, int] = {}
    for label in labels:
        subject = spl_subject.resolve_subject(
            openfda_uniis=label.uniis,
            dailymed=recovered.get(label.set_id),
            known_uniis=known_uniis)
        by_route[subject.route] = by_route.get(subject.route, 0) + 1
        if not subject.moiety_uuids:
            rows.append(spl_evidence.SubjectRow(
                set_id=label.set_id, version=label.version, subject_ordinal=0,
                moiety_uuid=None, route=subject.route))
            continue
        for ordinal, moiety_uuid in enumerate(subject.moiety_uuids):
            rows.append(spl_evidence.SubjectRow(
                set_id=label.set_id, version=label.version,
                subject_ordinal=ordinal, moiety_uuid=moiety_uuid,
                route=subject.route))
    return rows, by_route


def evidence_for_wording(
    text_key: str, text: str, vocab: spl_match.Vocabulary
) -> tuple[list[spl_evidence.OccurrenceRow], list[spl_evidence.QuoteRow]]:
    """The occurrence and quote rows one wording yields. Matched ONCE.

    The two are produced together because they rest on the same match pass, and
    running the matcher twice would be two chances for the offsets a quote is cut
    at to differ from the offsets an occurrence records.
    """
    matches = spl_match.find_matches(text, vocab)
    occurrences = spl_match.moiety_occurrences(matches)
    occurrence_rows = [
        spl_evidence.OccurrenceRow(
            text_key=text_key, char_start=occurrence.char_start,
            char_end=occurrence.char_end, moiety_uuid=occurrence.moiety_uuid,
            match_ambiguous=occurrence.ambiguous)
        for occurrence in occurrences]
    # `from_window` cuts the text itself rather than taking a pre-cut string
    # beside two offsets. The offsets and the cut then cannot disagree, and the
    # window is checked against the length of the text it was measured on --
    # which is the only place the raw-versus-normalised mistake is visible,
    # because Python slicing clamps an over-long window silently.
    quote_rows = [
        spl_evidence.QuoteRow.from_window(
            text_key=text_key, ordinal=ordinal, window=window, text=text)
        for ordinal, window, _quote_text in spl_quote.quotes_for(
            text,
            [(o.moiety_uuid, o.char_start, o.char_end) for o in occurrences])]
    return occurrence_rows, quote_rows

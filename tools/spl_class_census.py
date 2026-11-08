# tools/spl_class_census.py
"""Census of the SPL CLASS pass on a built database (design §7, Task 9A).

⇒ WHY THIS TOOL EXISTS. The class pass (db/066) writes one `spl_class_occurrence`
row per drug-class mention in an interactions wording, with a machine-read advice
level. Before any of that is trusted, §7 of the design asks for plain counts: how
many mentions, on which axes, read as what, at what yield. This prints them all.

⇒ EVERY FIGURE IS PER WORDING, NOT PER LABEL. A wording is shared by ~2.5 labels on
average, so counts are of distinct wording texts; the
`labels` column is the same population weighted by `spl_wording.label_count`, shown
only so a reader can see the 2.5x trap rather than fall into it. The one exception
is "statements", which are (labelling drug x class) pairs and so label-derived.

⇒ UNITS, BECAUSE THEY WERE CONFUSED ONCE: a MENTION is a distinct SPAN
(text_key, char_start, char_end). The table holds one row per (span, class), and a
span several classes fold onto has several rows. "Rows" below means (span, class)
rows; "spans" means mentions. A reading (level, rule, scope) depends only on the span,
so level and rule distributions are counted per SPAN.

⇒ "EMPTY" vs "POPULATED" classes: a class with no direct `class_membership` row can
never be matched to a labelling drug by membership, so a mention of it is a
different kind of evidence. Both are reported, per axis (`source` + `concept_type`).

⇒ WHAT COMES FROM WHERE. Everything stored comes from the database. Two figures are
about PROSE the database deliberately does not keep (`spl_wording` has no text
column), so they need the openFDA corpus (`--openfda`): the PRESENCE of the
parenthetical (long-form enzyme) spelling, and mentions standing within 80
characters of a table-row label such as "Intervention" / "Clinical Impact".
Without `--openfda` those two print "not measured". Nothing in this tool's OUTPUT
quotes label prose (rule 6): only counts.

Usage:
    uv run python -m tools.spl_class_census --dsn "$DSN" [--openfda downloads/OPENFDA]
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

import psycopg

from drugref import db, server_messages

#: "cytochrome P450 3A4 (CYP3A4)" -- the long-form spelling a label may use for an
#: enzyme the class vocabulary only knows as "CYP3A4 ...". A role word after it is
#: what makes it a class mention (strong CYP3A4 inhibitors) rather than a bare enzyme.
_PAREN_FORM = re.compile(r"cytochrome P450 \d\w* \(CYP", re.IGNORECASE)
_ROLE_WORD = re.compile(r"inhibit|induc|substrate", re.IGNORECASE)
_ROLE_WINDOW = 80

#: Labels in an interaction TABLE put "Intervention" / "Clinical Impact" next to the
#: drug or class they describe; a mention there is a table row, not a sentence.
_TABLE_LABEL = re.compile(r"intervention|clinical impact", re.IGNORECASE)
_TABLE_WINDOW = 80


def has_parenthetical_spelling(text: str) -> bool:
    """True when `text` has the long-form enzyme spelling with a role word nearby."""
    return any(_ROLE_WORD.search(text, m.end(), m.end() + _ROLE_WINDOW)
               for m in _PAREN_FORM.finditer(text))


def near_table_label(text: str, start: int, end: int) -> bool:
    """True when a table-row label sits within 80 chars either side of the span."""
    return bool(_TABLE_LABEL.search(
        text[max(0, start - _TABLE_WINDOW):end + _TABLE_WINDOW]))


# --------------------------------------------------------------------------
# The shared CTE: one row per class occurrence, with its axis and whether the
# class has any direct member. `%(run)s` is the SPL run being measured.
# --------------------------------------------------------------------------
_OCC = """
WITH occ AS (
  SELECT o.text_key, o.char_start, o.char_end, o.class_uuid, o.match_form,
         o.match_ambiguous, o.advice_level, o.advice_mixed, o.advice_rule,
         o.advice_scope, w.label_count,
         c.source || ' ' || c.concept_type AS axis,
         CASE WHEN EXISTS (SELECT 1 FROM drugref.class_membership m
                            WHERE m.class_uuid = c.class_uuid)
              THEN 'populated' ELSE 'empty' END AS pop
    FROM drugref.spl_class_occurrence o
    JOIN drugref.spl_wording w USING (ingest_run, source, text_key)
    JOIN drugref.substance_class c USING (class_uuid)
   WHERE o.ingest_run = %(run)s)
"""

#: One row per SPAN (a mention). Every (span, class) row of a span carries the same
#: reading, so DISTINCT ON loses nothing; `ambiguous` is true when >1 class fold on it.
_SPANS = _OCC + """, spans AS (
  SELECT DISTINCT ON (text_key, char_start, char_end)
         text_key, char_start, char_end, advice_level, advice_mixed, advice_rule,
         advice_scope, match_ambiguous
    FROM occ ORDER BY text_key, char_start, char_end)
"""

#: (title, SQL). Each SQL starts with `_OCC` when it reads occurrences.
QUERIES: tuple[tuple[str, str], ...] = (
    ("Totals: (span, class) rows vs distinct spans (mentions)",
     _SPANS + """
     SELECT (SELECT count(*) FROM occ) AS span_class_rows,
            count(*) AS spans,
            count(*) FILTER (WHERE match_ambiguous) AS ambiguous_spans,
            round(100.0 * count(*) FILTER (WHERE match_ambiguous) / count(*), 1)
              AS ambiguous_pct,
            count(DISTINCT text_key) AS wordings
       FROM spans"""),
    ("Per axis, empty vs populated: (span, class) rows, distinct spans, wordings "
     "(`labels` = the same wordings weighted by label_count)",
     _OCC + """
     SELECT axis, pop, sum(n) AS span_class_rows, sum(spans) AS spans,
            count(*) AS wordings, sum(label_count) AS labels
       FROM (SELECT axis, pop, text_key, label_count, count(*) AS n,
                    count(DISTINCT (char_start, char_end)) AS spans
               FROM occ GROUP BY axis, pop, text_key, label_count) p
      GROUP BY axis, pop ORDER BY axis, pop"""),
    ("Classes mentioned at least once vs classes matchable, per axis",
     """
     SELECT c.source || ' ' || c.concept_type AS axis,
            CASE WHEN EXISTS (SELECT 1 FROM drugref.class_membership m
                               WHERE m.class_uuid = c.class_uuid)
                 THEN 'populated' ELSE 'empty' END AS pop,
            count(*) AS classes,
            count(*) FILTER (WHERE EXISTS (
                SELECT 1 FROM drugref.spl_class_occurrence o
                 WHERE o.class_uuid = c.class_uuid AND o.ingest_run = %(run)s))
              AS mentioned
       FROM drugref.substance_class c
      WHERE NOT (c.source = 'MED-RT' AND c.concept_type IN ('PK', 'APC'))
      GROUP BY 1, 2 ORDER BY 1, 2"""),
    ("Distinct statements (labelling drug x class; label-derived, not per wording) "
     "and classes gaining at least one, per axis",
     """
     SELECT c.source || ' ' || c.concept_type AS axis,
            count(*) AS statements, count(DISTINCT st.class_uuid) AS classes,
            count(*) FILTER (WHERE st.subject_in_class) AS subject_in_class
       FROM drugref.spl_class_statement st
       JOIN drugref.substance_class c USING (class_uuid)
      GROUP BY 1 ORDER BY 1"""),
    ("Yield per match_form (§4.2 key form): rows, spans, ambiguous spans",
     _OCC + """
     SELECT match_form, count(*) AS span_class_rows,
            count(DISTINCT (text_key, char_start, char_end)) AS spans,
            count(DISTINCT text_key) AS wordings,
            count(DISTINCT class_uuid) AS classes,
            count(DISTINCT (text_key, char_start, char_end))
              FILTER (WHERE match_ambiguous) AS ambiguous_spans
       FROM occ GROUP BY 1 ORDER BY 1"""),
    ("Moiety spans lying inside a class span (§4.4 overlap; equal spans count)",
     """
     SELECT count(*) AS moiety_occurrences,
            count(DISTINCT e.text_key) AS wordings
       FROM drugref.spl_entity_occurrence e
      WHERE e.ingest_run = %(run)s AND EXISTS (
            SELECT 1 FROM drugref.spl_class_occurrence o
             WHERE o.ingest_run = e.ingest_run AND o.source = e.source
               AND o.text_key = e.text_key
               AND e.char_start >= o.char_start AND e.char_end <= o.char_end)"""),
    ("Advice level distribution (per SPAN)",
     _SPANS + """
     SELECT advice_level, count(*) AS spans,
            round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct,
            count(DISTINCT text_key) AS wordings,
            count(*) FILTER (WHERE advice_mixed) AS mixed
       FROM spans GROUP BY 1 ORDER BY 2 DESC"""),
    ("Level x scope (per span)", _SPANS + """
     SELECT advice_level, advice_scope, count(*) AS spans
       FROM spans GROUP BY 1, 2 ORDER BY 1, 2"""),
    ("Level x axis ((span, class) rows)", _OCC + """
     SELECT axis, advice_level, count(*) AS span_class_rows
       FROM occ GROUP BY 1, 2 ORDER BY 1, 2"""),
    ("Phrase rules that fired (per span)", _SPANS + """
     SELECT coalesce(advice_rule, '(not_read)') AS rule, count(*) AS spans,
            count(DISTINCT text_key) AS wordings
       FROM spans GROUP BY 1 ORDER BY 2 DESC"""),
    ("Spans covered by an existing quote window, per level",
     _SPANS + """
     SELECT advice_level, count(*) AS spans,
            count(*) FILTER (WHERE EXISTS (
                SELECT 1 FROM drugref.spl_wording_quote q
                 WHERE q.ingest_run = %(run)s AND q.source = 'SPL'
                   AND q.text_key = spans.text_key
                   AND q.char_start <= spans.char_start
                   AND q.char_end >= spans.char_end)) AS quoted
       FROM spans GROUP BY 1 ORDER BY 1"""),
    ("TOP OFFENDERS: the 15 classes with most (span, class) rows (a class matching "
     "ordinary English shows up here)",
     _OCC + """
     SELECT c.class_name, occ.axis, occ.pop, count(*) AS span_class_rows,
            count(DISTINCT occ.text_key) AS wordings
       FROM occ JOIN drugref.substance_class c USING (class_uuid)
      GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 15"""),
    ("Drug x drug pairs (unchanged by the class pass; floors 29,258 / 25,960; "
     "last recorded run 29,952 / 26,598)",
     """
     SELECT (SELECT count(*) FROM drugref.spl_ddi_pair) AS pairs,
            (SELECT count(*) FROM (
               SELECT moiety_lo, moiety_hi FROM drugref.spl_ddi_pair
               EXCEPT SELECT moiety_lo, moiety_hi FROM drugref.exact_ddi_pair
               EXCEPT SELECT least(subject_moiety, partner_moiety),
                             greatest(subject_moiety, partner_moiety)
                        FROM drugref.ddi_candidate_pair) n) AS novel"""),
    ("Population (wordings and labels the class pass ran over)",
     """
     SELECT count(*) AS wordings, sum(label_count) AS labels,
            count(*) FILTER (WHERE EXISTS (
                SELECT 1 FROM drugref.spl_class_occurrence o
                 WHERE o.ingest_run = w.ingest_run AND o.source = w.source
                   AND o.text_key = w.text_key)) AS wordings_with_a_class
       FROM drugref.spl_wording w WHERE ingest_run = %(run)s"""),
)


def latest_spl_run(conn: psycopg.Connection) -> int:
    """The SPL run whose wordings are in the database (the projection is rebuilt
    per source, so there is one; max() would only matter after a failed rerun)."""
    (run,) = conn.execute(
        "SELECT max(ingest_run) FROM drugref.spl_wording").fetchone()
    if run is None:
        raise SystemExit("no spl_wording rows: run `drugref ingest spl` first")
    return int(run)


def markdown_table(columns: Sequence[str], rows: Iterable[Sequence]) -> str:
    """Render rows as a GitHub Markdown table (numbers get thousands separators)."""
    def cell(v) -> str:
        # SQL sum() yields Decimal, count() int; both get thousands separators.
        return f"{v:,}" if isinstance(v, (int, Decimal)) else str(v)
    lines = ["| " + " | ".join(columns) + " |",
             "|" + "|".join("---" for _ in columns) + "|"]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def run_queries(conn: psycopg.Connection, run: int) -> str:
    """Every stored figure, as titled Markdown tables."""
    out = []
    for title, sql in QUERIES:
        cur = conn.execute(sql, {"run": run})
        columns = [d.name for d in cur.description]
        out.append(f"### {title}\n\n{markdown_table(columns, cur.fetchall())}\n")
    return "\n".join(out)


def prose_figures(conn: psycopg.Connection, run: int,
                  wordings: Mapping[str, str],
                  label_counts: Mapping[str, int]) -> str:
    """The two figures only the corpus can give (see the module docstring)."""
    with_spelling = [k for k, t in wordings.items() if has_parenthetical_spelling(t)]
    near_occ, near_words = 0, set()
    total = 0
    with conn.cursor(name="census_occ") as cur:
        cur.execute(
            "SELECT text_key, char_start, char_end FROM drugref.spl_class_occurrence "
            "WHERE ingest_run = %s", (run,))
        for key, start, end in cur:
            total += 1
            text = wordings.get(key)
            if text is not None and near_table_label(text, start, end):
                near_occ += 1
                near_words.add(key)
    rows_a = [("wordings WITH the long-form spelling near a role word (presence)",
               len(with_spelling), sum(label_counts[k] for k in with_spelling))]
    rows_b = [("(span, class) rows within 80 chars of Intervention/Clinical Impact",
               near_occ, len(near_words), total)]
    return ("### Presence of the parenthetical spelling (corpus)\n\n"
            + markdown_table(["figure", "wordings", "labels"], rows_a)
            + "\n\n### Mentions near a table-row label (corpus)\n\n"
            + markdown_table(["figure", "span_class_rows", "wordings", "of all rows"],
                             rows_b) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", required=True, help="the BUILT database")
    parser.add_argument("--openfda", help="directory of drug-label-*.json.zip; "
                        "enables the two prose-derived figures")
    parser.add_argument("--sample", type=int, default=0,
                        help="reserved for Task 9B (stratified precision sample)")
    args = parser.parse_args(argv)
    if args.sample:
        raise SystemExit("--sample is Task 9B's: not implemented in the census")
    started = time.monotonic()
    with db.connect(args.dsn) as conn:
        run = latest_spl_run(conn)
        print(f"## SPL run {run}\n")
        print(run_queries(conn, run))
        if args.openfda:
            from drugref.ingest import spl  # lazy: the DB-only path needs no corpus
            parts = sorted(glob.glob(f"{args.openfda}/drug-label-*.json.zip"))
            if not parts:
                raise SystemExit(f"no drug-label-*.json.zip under {args.openfda}")
            corpus = spl.read_corpus(parts)
            print(prose_figures(conn, run, corpus.wordings, corpus.label_counts))
        else:
            print("### Prose-derived figures\n\nnot measured (no --openfda)\n")
    print(f"census took {time.monotonic() - started:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    server_messages.configure_logging()
    raise SystemExit(main())

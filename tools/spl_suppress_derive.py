"""Derive suppression CANDIDATES from the corpus's own next-word distributions.

The design spec puts this in scope for slice 5c.3: *"Deriving the list
systematically from next-word distributions is in scope for this slice; nine
terms is a starting point that was measured, not a finished list."*

⇒ **IT PRODUCES A CANDIDATE LIST, NOT A VOCABULARY, AND THAT IS THE FINDING.**
`lead` is followed by `to` in 9,157 of its 9,160 occurrences and the bigram is a
verb. `warfarin` is followed by `sodium` on many of its occurrences and the
bigram is still the drug. **The two distributions have the same shape**; what
separates them is whether the longer phrase names an entity, which is a reading
and not a statistic. A run of this tool is an input to a human decision, and a
term reaches `src/drugref/data/spl_suppression_terms.txt` only with the
distribution printed here written beside it.

The rules themselves are `drugref.ingest.spl_match.next_word_profiles` and
`suppression_candidates` -- shipped and tested, because the vocabulary they
justify ships. This module is only the runner: corpus in, table out.

Usage::

    uv run python -m tools.spl_suppress_derive \\
        --openfda downloads/OPENFDA \\
        --dsn "host=localhost port=5532 dbname=drugref_spl051 user=postgres"
"""
from __future__ import annotations

import argparse
import pathlib

from drugref import registry_read
from drugref.ingest import spl, spl_match, spl_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openfda", required=True, type=pathlib.Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--min-occurrences", type=int, default=500,
                        help="names below this carry too little evidence to rule "
                             "on; the shipped terms rest on 9,160 to 19,804")
    parser.add_argument("--min-dominance", type=float, default=0.5,
                        help="share of a name's occurrences the next word must "
                             "take before the bigram is worth a human's time")
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args(argv)

    import psycopg

    partitions = sorted(args.openfda.glob("drug-label-*.json.zip"))
    if not partitions:
        raise SystemExit(f"no openFDA partitions under {args.openfda}")
    print(f"reading {len(partitions)} partition(s) ...", flush=True)
    corpus = spl.read_corpus(partitions)
    print(f"  {len(corpus.labels):,} labels, {len(corpus.wordings):,} wordings")

    with psycopg.connect(args.dsn) as conn:
        names = registry_read.load_registry(conn).by_name
    print(f"  {len(names):,} registry names")

    # THE SAME VOCABULARY THE INGEST USES, suppression included. Deriving against
    # an unsuppressed vocabulary would re-propose every term already ruled on and
    # would measure a corpus the ingest does not read.
    vocab = spl_run.build_vocabulary(names)
    profiles = spl_match.next_word_profiles(
        corpus.wordings.values(), vocab, min_occurrences=args.min_occurrences)
    print(f"\n{len(profiles)} single-token names at >= {args.min_occurrences:,} "
          "occurrences")

    candidates = spl_match.suppression_candidates(
        profiles, min_dominance=args.min_dominance,
        already=spl_match.shipped_suppression_terms())

    print(f"\n=== {len(candidates)} CANDIDATES, ranked. NONE IS A DECISION ===")
    print("Read each one and ask the only question a distribution cannot: does")
    print("the longer phrase name an entity, or does it name the drug?\n")
    print(f"{'term':<34} {'occ':>9} {'share':>7}  evidence")
    for candidate in candidates[:args.top]:
        print(f"{candidate.term:<34} {candidate.occurrences:>9,} "
              f"{candidate.share:>6.1%}  {candidate.evidence}")
    if len(candidates) > args.top:
        # NO SILENT CAP: a truncated list read as a complete one is how a
        # vocabulary stops growing without anyone deciding it should.
        print(f"\n... {len(candidates) - args.top} more not shown; raise --top")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""What does DROPPING the class vocabulary do to the drug x drug yield?

The slice-5c.3 measurement round matched with a vocabulary holding **moieties and
classes together**, because it was sizing both grains at once. The shipped ingest
is **drug x drug only** (owner's call, 2026-08-24), so its vocabulary holds
moieties and the negative terms and nothing else.

That is not a neutral difference, and this tool exists because the ingest
published a figure the measurement did not: **20,747 distinct pairs on the
openFDA-only arm against the measurement's 20,554**. The obvious explanation is
that longest-match-wins let a CLASS name consume a span a moiety name would
otherwise have matched -- `Serotonin Uptake Inhibitors` swallowing `serotonin` --
so removing the classes returns those occurrences.

**Obvious is not measured.** This project has recorded seven wrong figures from
plausible reasoning, and the round that produced this slice's design published a
14.7% figure whose code was never committed and could not be audited. So the
difference is measured, both ways, over the whole corpus, by the code that ships
the number.

Usage::

    uv run python -m tools.spl_class_vocabulary_delta \\
        --openfda downloads/OPENFDA \\
        --dsn "host=localhost port=5532 dbname=drugref_spl051 user=postgres"
"""
from __future__ import annotations

import argparse
import pathlib
import re

from drugref import registry_read
from drugref.ingest import spl, spl_match, spl_run

#: MED-RT and drugref tag a class with its axis -- 'Cytochrome P450 1A2
#: Inhibitors [MoA]'. That tag never appears on a drug label, so the measurement
#: round stripped it before matching, and this reproduces that exactly: a
#: comparison against a vocabulary built differently would measure the rebuild.
_AXIS_TAG = re.compile(r"\s*\[[A-Za-z0-9-]+\]\s*$")


def class_variants(class_name: str) -> tuple[str, ...]:
    """The measurement round's class spellings: the stored name, plus its other
    number -- a plural for a singular name, and the SINGULAR for a name already
    ending in 's', which is the dominant case here ('Diuretics' -> 'Diuretic').

    Deliberately small and mechanical, exactly as it was there -- nothing here
    rewrites word order or expands 'Cytochrome P450' to 'CYP', because those are
    real differences between drugref's vocabulary and label prose.
    """
    base = _AXIS_TAG.sub("", class_name).strip()
    variants = [base, base[:-1] if base.endswith("s") else base + "s"]
    seen: dict[str, None] = {}
    for variant in variants:
        if variant:
            seen.setdefault(variant, None)
    return tuple(seen)


def load_class_entries(dsn: str) -> list[spl_match.Entry]:
    """Every class name, as the measurement round offered them to the matcher."""
    import psycopg

    entries: list[spl_match.Entry] = []
    with psycopg.connect(dsn) as conn:
        for (class_name,) in conn.execute(
                "SELECT class_name FROM drugref.substance_class").fetchall():
            for variant in class_variants(class_name):
                entries.append(spl_match.Entry(
                    kind="class", key=variant, display=class_name,
                    moiety_uuid=None))
    return entries


def count(corpus: spl.Corpus, vocab: spl_match.Vocabulary,
          uniis: dict[str, str]) -> tuple[int, int, int]:
    """`(moiety occurrences, wordings naming one, distinct openFDA-arm pairs)`.

    The pair arm is `openfda_unii` ONLY, because that is the arm the
    measurement's 20,554 was computed over -- comparing against the whole ingest
    would put the DailyMed recovery on one side of the subtraction and not the
    other, which is the exact defect this slice's design round was corrected for.
    """
    occurrences = with_moiety = 0
    by_wording: dict[str, set[str]] = {}
    for text_key, text in corpus.wordings.items():
        found = spl_match.moiety_occurrences(spl_match.find_matches(text, vocab))
        if found:
            with_moiety += 1
        occurrences += len(found)
        by_wording[text_key] = {o.moiety_uuid for o in found}

    pairs: set[tuple[str, str]] = set()
    for label in corpus.labels:
        subjects = {uniis[u] for u in label.uniis if u in uniis}
        for subject in subjects:
            for other in by_wording.get(label.text_key, ()):
                if subject == other:
                    continue
                pairs.add((subject, other) if subject < other else (other, subject))
    return occurrences, with_moiety, len(pairs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openfda", required=True, type=pathlib.Path)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args(argv)

    import psycopg

    partitions = sorted(args.openfda.glob("drug-label-*.json.zip"))
    print(f"reading {len(partitions)} partition(s) ...", flush=True)
    corpus = spl.read_corpus(partitions)
    with psycopg.connect(args.dsn) as conn:
        registry = registry_read.load_registry(conn)
    names, uniis = registry.by_name, registry.by_unii
    print(f"  {len(corpus.labels):,} labels, {len(corpus.wordings):,} wordings, "
          f"{len(names):,} moiety names")

    shipped = spl_run.build_vocabulary(names)
    class_entries = load_class_entries(args.dsn)
    with_classes = spl_match.build_vocabulary([
        *(entry for entries in shipped.by_key.values() for entry in entries),
        *class_entries])
    print(f"  {len(class_entries):,} class entries added for the second arm")

    print("\nmatching, drug x drug only (SHIPPED) ...", flush=True)
    a = count(corpus, shipped, uniis)
    print("matching, drugs AND classes (the measurement round) ...", flush=True)
    b = count(corpus, with_classes, uniis)

    print(f"\n{'':<34}{'shipped':>12}{'+classes':>12}{'delta':>10}")
    for label, x, y in zip(
            ("moiety occurrences", "wordings naming a moiety",
             "openFDA-arm distinct pairs"), a, b):
        print(f"{label:<34}{x:>12,}{y:>12,}{x - y:>+10,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

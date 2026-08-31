"""Build the SPL ingest's test fixture from the real releases, WITHOUT the prose.

**Run once, output committed.** `tests/fixtures/spl/` is what it writes, and
`tests/test_spl_run.py` builds the zip archives around it at test time.

⇒ **WHY THIS EXISTS RATHER THAN A HAND-WRITTEN FIXTURE.** PROJECT-NOTES records
the rule: *every fixture is extracted from a real release -- the last hand-written
one invented an INN_ID, a CAS and a UNII*. The shapes this ingest reads are
genuinely surprising (SPL nests `<activeMoiety>` inside itself; the classCode
spelling of an active ingredient dominates the element-name spelling by an order
of magnitude), and a fixture written from the documentation would test the
documentation.

⇒ **AND WHY THE PROSE IS NOT IN IT.** CLAUDE.md rule 6, and the owner's
determination on issue 154: drugref stores a BOUNDED QUOTED WINDOW of a section
and never the section in full. A test fixture committed to this repository is
bundling by any reading, and a 200-character section quoted whole is 100% of it,
not 25%. So this extractor takes:

  * from openFDA -- the IDENTITY only: `set_id`, `version`, `effective_time`,
    `openfda.unii`, `openfda.product_type`. Facts, not expression;
  * from DailyMed -- the INGREDIENT STRUCTURE only: `<setId>`, `<versionNumber>`
    and the ingredient subtrees, copied verbatim so the real nesting and
    classCodes survive. Substance names and UNIIs are facts too.

The section text the test feeds through the matcher is SYNTHESISED by the test,
naming moieties the test itself registers. That is the one part of the corpus
this repository may not carry, and it is also the part a fixture teaches least
about -- the matcher's own tests cover the rules over text directly.

Usage::

    uv run python -m tools.spl_make_fixture \\
        --openfda downloads/OPENFDA \\
        --dailymed downloads/DAILYMED/dm_spl_release_human_rx_part6.zip \\
        --out tests/fixtures/spl --labels 8
"""
from __future__ import annotations

import argparse
import json
import pathlib
import xml.etree.ElementTree as ET

from drugref.ingest import spl, spl_dailymed, spl_release

_SPL_NS = "urn:hl7-org:v3"
_NS = f"{{{_SPL_NS}}}"


def structural_skeleton(xml_bytes: bytes) -> bytes | None:
    """A prose-free SPL document carrying only what the subject reader looks at.

    `<setId>`, `<versionNumber>` and every ingredient subtree, copied VERBATIM so
    the real nesting survives -- that is the whole point of extracting rather than
    writing one. Everything else, which is where the prose lives, is dropped.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    set_id = root.find(f"{_NS}setId")
    if set_id is None or not set_id.get("root"):
        return None

    ET.register_namespace("", _SPL_NS)
    document = ET.Element(f"{_NS}document")
    document.append(set_id)
    version = root.find(f"{_NS}versionNumber")
    if version is not None:
        document.append(version)

    kept = 0
    for tag in (f"{_NS}ingredient", f"{_NS}activeIngredient"):
        for element in root.iter(tag):
            document.append(element)
            kept += 1
    if not kept:
        return None
    return ET.tostring(document, encoding="utf-8", xml_declaration=True)


def read_dailymed(part: pathlib.Path, wanted: int) -> dict[str, bytes]:
    """The first `wanted` labels of one release part that declare an ingredient."""
    skeletons: dict[str, bytes] = {}
    for _document_id, xml_bytes in spl_release.iter_release_labels(str(part)):
        set_id = spl_dailymed.set_id_in_bytes(xml_bytes)
        if set_id is None or set_id in skeletons:
            continue
        found = spl_dailymed.extract_subject_uniis(xml_bytes)
        if found is None or not found.has_any_unii:
            continue
        skeleton = structural_skeleton(xml_bytes)
        if skeleton is None:
            continue
        skeletons[set_id] = skeleton
        if len(skeletons) >= wanted:
            break
    return skeletons


def read_openfda(openfda_dir: pathlib.Path, set_ids: set[str]) -> list[dict]:
    """The IDENTITY of every section-carrying openFDA label in `set_ids`.

    No prose leaves this function -- `drug_interactions` is deliberately not read
    into the result, only used to decide whether the label carries the section at
    all.
    """
    found: list[dict] = []
    for partition in sorted(openfda_dir.glob("drug-label-*.json.zip")):
        for record in spl.iter_partition_records(partition):
            section = spl.extract_section(record)
            if section is None or section.set_id not in set_ids:
                continue
            found.append({
                "set_id": section.set_id,
                "version": section.version,
                "effective_time": section.effective_time,
                "product_type": section.product_type,
                "uniis": list(section.uniis),
            })
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openfda", required=True, type=pathlib.Path)
    parser.add_argument("--dailymed", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--labels", type=int, default=8)
    parser.add_argument("--scan", type=int, default=400,
                        help="how many DailyMed labels to consider")
    args = parser.parse_args(argv)

    skeletons = read_dailymed(args.dailymed, args.scan)
    print(f"read {len(skeletons)} DailyMed skeletons")
    labels = read_openfda(args.openfda, set(skeletons))
    print(f"{len(labels)} of them carry section 34073-7 in openFDA")

    # Keep a MIX: labels openFDA already keys, and labels it does not -- the
    # second population is the whole reason the DailyMed pass exists, and a
    # fixture of only the first would exercise one route of five.
    keyed = [label for label in labels if label["uniis"]]
    unkeyed = [label for label in labels if not label["uniis"]]
    half = max(1, args.labels // 2)
    chosen = keyed[:half] + unkeyed[:args.labels - half]
    if not chosen:
        raise SystemExit("no overlapping labels found; raise --scan")
    print(f"chose {len(chosen)}: {len(keyed[:half])} keyed, "
          f"{len(unkeyed[:args.labels - half])} unkeyed")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "openfda_labels.json").write_text(
        json.dumps(chosen, indent=2, sort_keys=True) + "\n")
    dailymed_dir = args.out / "dailymed"
    dailymed_dir.mkdir(exist_ok=True)
    for existing in dailymed_dir.glob("*.xml"):
        existing.unlink()
    for label in chosen:
        (dailymed_dir / f"{label['set_id']}.xml").write_bytes(
            skeletons[label["set_id"]])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

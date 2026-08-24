"""Cross-check openFDA's ``drug_interactions`` against DailyMed's source XML.

**Throwaway spike code for the slice 5c.3 measurement round.**

openFDA's section field is FDA's own derivation from the SPL XML. Taking it on
trust would repeat the failure this project has recorded seven times -- *a
plausible value from a parser nobody verified, written down as a measurement*
(PROJECT-NOTES § "Slice 5c.2g"). So the same labels are read from DailyMed's
full Human Rx release and compared.

Two questions, answered by two different passes because they need different
rigour:

**Coverage** -- which labels carry section 34073-7 at all, and do they appear in
openFDA? A whole-corpus pass over ~50,000 nested zips. It only needs the set id
and whether the section code is present, so it scans the XML bytes rather than
building a tree: cheap, and the predicate (``code="34073-7"`` appearing at all)
over-matches rather than under-matches, which is the safe direction for a
coverage claim.

**Fidelity** -- does openFDA's text actually reproduce the section? A SAMPLE,
parsed properly with an XML tree, because here a sloppy read would produce
exactly the false reassurance the check exists to prevent. Compared by token
overlap rather than equality: openFDA prepends the section title ("7 DRUG
INTERACTIONS ...") and flattens tables, so the two are never byte-identical and
an equality test would report a fidelity problem that is really a formatting
difference.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass

#: The HL7 v3 namespace every SPL document uses.
_SPL_NS = "{urn:hl7-org:v3}"

#: LOINC code for the DRUG INTERACTIONS section, and the two document-type codes
#: that classify a label. Keyed on the CODE, never on displayName -- the source
#: evaluation found case variants of the display name in a single 50-label draw.
INTERACTIONS_CODE = "34073-7"
RX_DOC_CODE = "34391-3"
OTC_DOC_CODE = "34390-5"

_HAS_INTERACTIONS = re.compile(rb'code="34073-7"')
_SET_ID = re.compile(rb'<setId[^>]*\sroot="([^"]+)"')
_DOC_CODE = re.compile(rb'<code[^>]*\scode="(34391-3|34390-5|[0-9]{4,6}-[0-9])"')


@dataclass(frozen=True)
class LabelScan:
    """The cheap pass's result for one label."""

    document_id: str
    set_id: str | None
    doc_code: str | None
    has_interactions: bool


def scan_label(xml_bytes: bytes, document_id: str) -> LabelScan:
    """Read only what the coverage question needs, without building a tree."""
    set_match = _SET_ID.search(xml_bytes)
    doc_match = _DOC_CODE.search(xml_bytes)
    return LabelScan(
        document_id=document_id,
        set_id=set_match.group(1).decode() if set_match else None,
        doc_code=doc_match.group(1).decode() if doc_match else None,
        has_interactions=bool(_HAS_INTERACTIONS.search(xml_bytes)),
    )


def iter_release_labels(
    part_path: str, *, limit: int | None = None
) -> Iterator[tuple[str, bytes]]:
    """Yield ``(document_id, xml_bytes)`` for every label in one release part.

    Each outer member is itself a zip holding the XML plus the label's images;
    only the ``.xml`` member is read, so the images never leave the archive.
    """
    seen = 0
    with zipfile.ZipFile(part_path) as outer:
        for name in outer.namelist():
            if not name.endswith(".zip"):
                continue
            with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                xml_names = [n for n in inner.namelist() if n.endswith(".xml")]
                if not xml_names:
                    continue
                document_id = xml_names[0].rsplit("/", 1)[-1][: -len(".xml")]
                yield document_id, inner.read(xml_names[0])
            seen += 1
            if limit is not None and seen >= limit:
                return


def extract_interactions_text(xml_bytes: bytes) -> str | None:
    """The full text of section 34073-7, or ``None`` if the label has none.

    Walks every ``<section>`` and keeps those whose own ``<code>`` carries the
    interactions LOINC code, then flattens all descendant text. Nested
    subsections (7.1, 7.2 ...) come along because they are descendants of the
    matched section -- and they must, since the tizanidine label puts its whole
    strong-versus-moderate distinction in them.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    chunks: list[str] = []
    for section in root.iter(f"{_SPL_NS}section"):
        code = section.find(f"{_SPL_NS}code")
        if code is None or code.get("code") != INTERACTIONS_CODE:
            continue
        chunks.append(" ".join(t.strip() for t in section.itertext() if t.strip()))
    if not chunks:
        return None
    return " ".join(chunks)


_WORD = re.compile(r"[a-z0-9]+")


def token_overlap(left: str, right: str) -> float:
    """Jaccard overlap of the two texts' token sets.

    Reported alongside containment as a symmetric sanity figure: a low Jaccard
    with a high containment means openFDA carries substantially MORE than the
    section (the prepended title, a flattened table), which is a formatting
    difference. Both being low is the real failure.

    Returns 0.0 when either side is empty rather than dividing by zero.
    """
    a = set(_WORD.findall(left.lower()))
    b = set(_WORD.findall(right.lower()))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def token_containment(source: str, candidate: str) -> float:
    """What fraction of ``source``'s tokens appear in ``candidate``.

    **This is the fidelity metric, and Jaccard is not.** The question the
    cross-check asks is "did openFDA DROP any of the section", which is
    asymmetric: extra text on openFDA's side (its prepended '7 DRUG
    INTERACTIONS' title) is harmless, while missing text is the defect. Jaccard
    punishes both equally and would report a fidelity problem where there is
    only a formatting one -- on a short section it scores a perfect
    reproduction at 0.5.
    """
    a = set(_WORD.findall(source.lower()))
    b = set(_WORD.findall(candidate.lower()))
    if not a:
        return 0.0
    return len(a & b) / len(a)


def _load_openfda(cache: str) -> tuple[dict[str, str], dict[str, str]]:
    """``set_id -> text`` and ``set_id -> text_key`` from the spike's caches."""
    import json
    import pathlib

    root = pathlib.Path(cache)
    texts: dict[str, str] = {}
    with (root / "texts.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            texts[row["text_key"]] = row["text"]
    by_set: dict[str, str] = {}
    key_by_set: dict[str, str] = {}
    with (root / "sections.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            by_set[row["set_id"]] = texts.get(row["text_key"], "")
            key_by_set[row["set_id"]] = row["text_key"]
    return by_set, key_by_set


def _other_text(openfda_text: dict[str, str], set_id: str) -> str:
    """Some OTHER label's section, for the negative control."""
    for other_id, text in openfda_text.items():
        if other_id != set_id and text:
            return text
    return ""


def main(argv: list[str] | None = None) -> int:
    """Run the coverage pass, the fidelity sample, or both."""
    import argparse
    import statistics

    parser = argparse.ArgumentParser(description="DailyMed cross-check")
    parser.add_argument("--parts", nargs="+", required=True)
    parser.add_argument("--cache", required=True, help="the spike's openFDA cache")
    parser.add_argument("--fidelity-sample", type=int, default=200)
    parser.add_argument(
        "--negative-control", action="store_true",
        help="compare each label against a DIFFERENT label's openFDA text. "
             "A fidelity check that scores 1.0 proves nothing until it has been "
             "shown it can score low -- db/050's lesson, that every guard in a "
             "slice passed vacuously, cost a whole review round.",
    )
    args = parser.parse_args(argv)

    openfda_text, _ = _load_openfda(args.cache)
    print(f"openFDA cache: {len(openfda_text):,} labels with section 34073-7")

    total = with_section = rx = rx_with_section = 0
    in_openfda = missing_from_openfda = 0
    containments: list[float] = []
    jaccards: list[float] = []
    sampled = 0

    for part in args.parts:
        print(f"  scanning {part} ...", flush=True)
        for document_id, xml_bytes in iter_release_labels(part):
            total += 1
            scan = scan_label(xml_bytes, document_id)
            if scan.doc_code == RX_DOC_CODE:
                rx += 1
            if not scan.has_interactions:
                continue
            with_section += 1
            if scan.doc_code == RX_DOC_CODE:
                rx_with_section += 1
            if scan.set_id in openfda_text:
                in_openfda += 1
                if sampled < args.fidelity_sample:
                    source = extract_interactions_text(xml_bytes)
                    if source:
                        candidate = openfda_text[scan.set_id]
                        if args.negative_control:
                            candidate = _other_text(openfda_text, scan.set_id)
                        containments.append(token_containment(source, candidate))
                        jaccards.append(token_overlap(source, candidate))
                        sampled += 1
            else:
                missing_from_openfda += 1

    print("\n=== DAILYMED COVERAGE ===")
    print(f"  labels scanned                   {total:>9,}")
    print(f"  HUMAN PRESCRIPTION (34391-3)     {rx:>9,}")
    print(f"  carry section 34073-7            {with_section:>9,}")
    print(f"    of which prescription          {rx_with_section:>9,}")
    print(f"  set_id present in openFDA        {in_openfda:>9,}")
    print(f"  set_id MISSING from openFDA      {missing_from_openfda:>9,}")

    print("\n=== FIDELITY OF openFDA's drug_interactions FIELD ===")
    if not containments:
        print("  no comparable labels sampled")
        return 0
    perfect = sum(1 for c in containments if c >= 0.999)
    good = sum(1 for c in containments if c >= 0.95)
    poor = sum(1 for c in containments if c < 0.80)
    print(f"  labels compared                  {len(containments):>9,}")
    print(f"  containment == 1.00 (nothing lost){perfect:>8,}")
    print(f"  containment >= 0.95              {good:>9,}")
    print(f"  containment <  0.80 (content lost){poor:>8,}")
    print(f"  mean containment                 {statistics.mean(containments):>9.4f}")
    print(f"  min  containment                 {min(containments):>9.4f}")
    print(f"  mean jaccard (symmetric)         {statistics.mean(jaccards):>9.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

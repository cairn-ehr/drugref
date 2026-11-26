"""Measure how US labels state drug effects on laboratory values (issue #294).

**Measurement code for a source evaluation, not an ingest.** It answers two
questions the evaluation must settle before a model is designed:

1. **Can labels reproduce ReportTriage's starting test set?** For each test-set
   drug and analyte, which is the STRONGEST label section that names the effect?
2. **How badly do labels over-include?** Across every single-substance
   prescription label, how many substances name each effect at all, and how many
   name it in a boxed warning or the warnings section? The owner ruled
   (2026-11-27) that drugref records label statements WITH a strength grade and
   lets the consumer filter, so the grade's power to separate is the point.

**The unit is a substance, not a label.** openFDA carries one record per label
and a common generic has dozens; a substance's grade is the strongest section
across all its labels. A label with more than one substance is skipped: a
combination product's warning cannot be attributed to one ingredient.

**Most labels carry no `openfda` block at all** (measured: 175,458 of 262,032,
of which 36,375 carry a prescription-format section,
`unkeyed_with_prescription_sections`), so this survey cannot tell
whether they are prescription or single-substance and skips them. Each skip
reason is COUNTED in the output (`skipped`) so the population is never
mistaken for "every US prescription label"; resolving those labels through
DailyMed (as `drugref.ingest.spl_subject` does for the ingest) is issue #301.

**A MENTION IS NOT A STATEMENT.** "Do not use in patients with hyperkalemia"
names hyperkalaemia without saying the drug causes it. Every figure here is a
mention-level UPPER bound on what a statement-level reader would find.

Usage: ``uv run python -m tools.lab_effect_survey downloads/OPENFDA/*.json.zip``
"""
from __future__ import annotations

import collections
import json
import re
import sys
from collections.abc import Mapping

from drugref import server_messages
from drugref.ingest.spl import iter_partition_records

#: openFDA section fields, STRONGEST FIRST. The interference section
#: (`drug_and_or_laboratory_test_interactions`) is deliberately absent: a false
#: reading is a different role (ReportTriage#36), never a physiological grade.
#: `general_precautions` and the other `*_table` fields are not ranked either:
#: measured in partition 1, `general_precautions` added 2 mentions found in no
#: ranked section, too few to change any figure.
SECTION_RANK = (
    "boxed_warning",
    "warnings_and_cautions",
    "warnings",
    "precautions",
    "laboratory_tests",
    "drug_interactions",
    "adverse_reactions",
    "adverse_reactions_table",
)
INTERFERENCE_SECTION = "drug_and_or_laboratory_test_interactions"
#: Sections only the prescription label format has; used to size the unkeyed gap.
RX_FORMAT_SECTIONS = ("boxed_warning", "warnings_and_cautions")

#: Potassium as a MEASURED analyte. A bare "potassium" would match the salt in
#: a product name ("losartan potassium tablets may increase ...") and count every
#: potassium salt under both potassium effects.
_SERUM_K = r"((serum|plasma|blood)\W+potassium|potassium\W+(level|concentration)s?)"
#: "creatinine clearance" is dosing text on nearly every renally cleared drug and
#: "creatinine kinase" a misspelt muscle marker; neither is serum creatinine.
_CREATININE = r"creatinine(?!\W+(clearance|kinase))"

#: One pattern per test-set effect: "bleeding" is a pathway, not an analyte,
#: and "INR high" reads no direction (any INR mention; with warfarin the
#: interaction is almost always a rise). Deliberately broad (it over-matches; see
#: the module docstring), case-insensitive; each is pinned by a true and a known
#: false positive in the tests.
EFFECTS = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in {
        "potassium high": rf"hyperkal"
                          rf"|({_SERUM_K}\W+(\w+\W+){{0,4}}(increas|elevat|rais))"
                          rf"|((increas|elevat|rais)\w*\W+(\w+\W+){{0,2}}{_SERUM_K})",
        "potassium low": rf"hypokal"
                         rf"|({_SERUM_K}\W+(\w+\W+){{0,4}}(decreas|low|deplet))"
                         rf"|((decreas|low|deplet)\w*\W+(\w+\W+){{0,2}}{_SERUM_K})",
        "sodium low": r"hyponatr|SIADH|inappropriate antidiuretic",
        "creatinine high": rf"({_CREATININE}\W+(\w+\W+){{0,6}}(increas|elevat|rise))"
                           rf"|((increas|elevat|rise)\w*\W+(\w+\W+){{0,6}}{_CREATININE})"
                           r"|acute kidney injury|acute renal failure",
        "bleeding": r"bleed|hemorrhag|haemorrhag",
        "INR high": r"\bINR\b|international normali[sz]ed ratio|prothrombin time",
        "neutrophils low": r"neutropeni|agranulocyt",
        # "platelet aggregation" is the antiplatelet mechanism, not a low count.
        "platelets low": r"thrombocytopeni"
                         r"|(platelets?(?!\W+aggregat)\W+(\w+\W+){0,3}(decreas|low))",
    }.items()
}

#: ReportTriage's starting test set (issue #294), as US substance-name
#: prefixes. Classes are represented by common members; carbimazole is not
#: US-marketed, so methimazole (its active metabolite) stands in for it.
TEST_SET = (
    ("potassium high", "causes", ("lisinopril", "ramipril", "enalapril", "losartan",
     "candesartan", "valsartan", "spironolactone", "eplerenone", "potassium chloride",
     "trimethoprim", "ibuprofen", "naproxen", "diclofenac")),
    ("potassium low", "causes", ("furosemide", "bumetanide", "hydrochlorothiazide",
     "chlorthalidone", "indapamide")),
    ("potassium low", "more dangerous", ("digoxin",)),
    ("sodium low", "causes", ("hydrochlorothiazide", "chlorthalidone", "indapamide",
     "sertraline", "fluoxetine", "citalopram", "escitalopram", "venlafaxine",
     "duloxetine", "carbamazepine", "desmopressin")),
    ("creatinine high", "causes", ("ibuprofen", "naproxen", "diclofenac", "lisinopril",
     "ramipril", "losartan", "valsartan", "furosemide", "trimethoprim")),
    ("bleeding", "causes", ("warfarin", "apixaban", "rivaroxaban", "dabigatran",
     "heparin", "enoxaparin", "aspirin", "clopidogrel", "ticagrelor", "ibuprofen",
     "naproxen")),
    ("INR high", "causes (with warfarin)", ("amiodarone", "fluconazole", "voriconazole",
     "miconazole", "metronidazole", "trimethoprim")),
    ("neutrophils low", "causes", ("methimazole", "methotrexate")),
    ("neutrophils low", "more dangerous / monitoring", ("clozapine", "cyclophosphamide",
     "doxorubicin", "carboplatin")),
    ("platelets low", "causes", ("heparin", "enoxaparin", "dalteparin", "methotrexate",
     "cyclophosphamide", "carboplatin")),
)


def _text(record: Mapping, field: str) -> str:
    """One section's text: openFDA stores each section as a list of strings."""
    return " ".join(record.get(field) or [])


def strongest_section(record: Mapping, effect: re.Pattern) -> str | None:
    """The highest-ranked section of one label that mentions `effect`, or None."""
    return next((f for f in SECTION_RANK if effect.search(_text(record, f))), None)


def single_substance(record: Mapping) -> str | None:
    """The label's one substance name, lower-cased; None unless exactly one."""
    names = (record.get("openfda") or {}).get("substance_name") or []
    return names[0].lower() if len(names) == 1 else None


def _is_rx(record: Mapping) -> bool:
    """Whether openFDA tags the label as a human prescription drug."""
    types = (record.get("openfda") or {}).get("product_type") or []
    return "HUMAN PRESCRIPTION DRUG" in types


def skip_reason(record: Mapping) -> str | None:
    """Why the survey cannot use this label, or None when it can.

    The reasons are kept apart because they mean different things: a missing
    `openfda` block is openFDA not having keyed the label (most labels), while
    "combination" is a real product the survey chooses not to attribute.
    """
    openfda = record.get("openfda") or {}
    if not openfda:
        return "no_openfda_block"
    if not _is_rx(record):
        return "not_prescription"
    count = len(openfda.get("substance_name") or [])
    if count == 0:
        return "no_substance_name"
    return "combination" if count > 1 else None


def survey(partitions) -> dict:
    """Stream every partition once; return the published figures."""
    rank = {f: i for i, f in enumerate(SECTION_RANK)}
    # (effect, substance) -> strongest section seen across that substance's labels
    best: dict[tuple[str, str], str] = {}
    labels_per_substance = collections.Counter()
    interference: dict[str, str] = {}
    skipped = collections.Counter()
    labels_with_section = collections.Counter()
    records = rx = unkeyed_rx_format = 0
    for partition in partitions:
        for record in iter_partition_records(partition):
            records += 1
            reason = skip_reason(record)
            if reason:
                skipped[reason] += 1
                # the size of the gap #301 closes: unkeyed, yet prescription-format
                if reason == "no_openfda_block" and any(
                        record.get(f) for f in RX_FORMAT_SECTIONS):
                    unkeyed_rx_format += 1
                continue
            substance = single_substance(record)
            rx += 1
            labels_per_substance[substance] += 1
            labels_with_section.update(f for f in SECTION_RANK if record.get(f))
            # The population is "the section is present"; 600 characters is
            # enough to classify the reading by hand, and a later label's
            # non-blank text replaces a blank one so a blank first label hides
            # nothing.
            if record.get(INTERFERENCE_SECTION):
                text = _text(record, INTERFERENCE_SECTION)[:600]
                if not interference.get(substance, "").strip():
                    interference[substance] = text
            for name, effect in EFFECTS.items():
                section = strongest_section(record, effect)
                key = (name, substance)
                if section and (key not in best or rank[section] < rank[best[key]]):
                    best[key] = section

    def grade_of(name: str, prefix: str) -> dict:
        # `substances == []` means the prefix matched no kept substance (no
        # single-substance keyed label, or a misspelt prefix); a non-empty list
        # with `strongest: None` means the labels exist but never mention it.
        hits = {s: best.get((name, s))
                for s in labels_per_substance if s.startswith(prefix)}
        strongest = min((g for g in hits.values() if g), key=rank.get, default=None)
        return {"substances": sorted(hits), "strongest": strongest}

    by_effect = {}
    for name in EFFECTS:
        grades = collections.Counter(g for (e, _), g in best.items() if e == name)
        by_effect[name] = {"any_mention": sum(grades.values()),
                           "by_strongest_section": {f: grades[f] for f in SECTION_RANK}}
    return {
        "records_read": records,
        "single_substance_rx_labels": rx,
        "single_substance_rx_substances": len(labels_per_substance),
        "skipped": {r: skipped[r] for r in
                    ("no_openfda_block", "not_prescription", "no_substance_name",
                     "combination")},
        "unkeyed_with_prescription_sections": unkeyed_rx_format,
        "labels_with_section": {f: labels_with_section[f] for f in SECTION_RANK},
        "by_effect": by_effect,
        "test_set": [
            {"effect": e, "role": role, "drugs": {d: grade_of(e, d) for d in drugs}}
            for e, role, drugs in TEST_SET],
        "interference_substances": len(interference),
        # about 40 evenly spaced examples for reading by hand (all of them
        # when there are fewer than 80)
        "interference_sample": dict(
            sorted(interference.items())[:: max(1, len(interference) // 40)]),
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.split("Usage: ")[1], file=sys.stderr)
        return 2
    if len(set(argv)) != len(argv):
        print("refused: a partition was given twice (overlapping globs?)",
              file=sys.stderr)
        return 2
    result = survey(argv)
    # A run that kept nothing, or a ranked section no kept label carries (a
    # misspelt field name), would otherwise print plausible-looking zeros.
    unseen = [f for f, n in result["labels_with_section"].items() if n == 0]
    if result["single_substance_rx_labels"] == 0 or unseen:
        print(f"refused: no usable labels, or sections never seen: {unseen}",
              file=sys.stderr)
        return 1
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    server_messages.configure_logging()
    sys.exit(main(sys.argv[1:]))

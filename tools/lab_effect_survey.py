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

#: One pattern per (analyte, direction) in the test set. Deliberately broad
#: (it over-matches; see the module docstring), case-insensitive.
EFFECTS = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in {
        "potassium high": r"hyperkal|(potassium\W+(\w+\W+){0,4}(increas|elevat|rais))",
        "potassium low": r"hypokal|(potassium\W+(\w+\W+){0,4}(decreas|low|deplet))",
        "sodium low": r"hyponatr|SIADH|inappropriate antidiuretic",
        "creatinine high": r"(creatinine\W+(\w+\W+){0,6}(increas|elevat|rise))"
                           r"|((increas|elevat|rise)\w*\W+(\w+\W+){0,6}creatinine)"
                           r"|acute kidney injury|acute renal failure",
        "bleeding": r"bleed|hemorrhag|haemorrhag",
        "INR high": r"\bINR\b|international normali[sz]ed ratio|prothrombin time",
        "neutrophils low": r"neutropeni|agranulocyt",
        "platelets low": r"thrombocytopeni|(platelet\W+(\w+\W+){0,3}(decreas|low))",
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
    return " ".join(record.get(field) or [])


def strongest_section(record: Mapping, effect: re.Pattern) -> str | None:
    """The highest-ranked section of one label that mentions `effect`, or None."""
    return next((f for f in SECTION_RANK if effect.search(_text(record, f))), None)


def single_substance(record: Mapping) -> str | None:
    """The label's one substance name, lower-cased; None for a combination."""
    names = (record.get("openfda") or {}).get("substance_name") or []
    return names[0].lower() if len(names) == 1 else None


def _is_rx(record: Mapping) -> bool:
    types = (record.get("openfda") or {}).get("product_type") or []
    return "HUMAN PRESCRIPTION DRUG" in types


def survey(partitions) -> dict:
    """Stream every partition once; return the published figures."""
    rank = {f: i for i, f in enumerate(SECTION_RANK)}
    # (effect, substance) -> strongest section seen across that substance's labels
    best: dict[tuple[str, str], str] = {}
    labels_per_substance = collections.Counter()
    interference: dict[str, str] = {}
    records = rx = 0
    for partition in partitions:
        for record in iter_partition_records(partition):
            records += 1
            substance = single_substance(record)
            if not (_is_rx(record) and substance):
                continue
            rx += 1
            labels_per_substance[substance] += 1
            if record.get(INTERFERENCE_SECTION):
                text = _text(record, INTERFERENCE_SECTION)[:600]
                interference.setdefault(substance, text)
            for name, effect in EFFECTS.items():
                section = strongest_section(record, effect)
                key = (name, substance)
                if section and (key not in best or rank[section] < rank[best[key]]):
                    best[key] = section

    def grade_of(name: str, prefix: str) -> dict:
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
        "by_effect": by_effect,
        "test_set": [
            {"effect": e, "role": role, "drugs": {d: grade_of(e, d) for d in drugs}}
            for e, role, drugs in TEST_SET],
        "interference_substances": len(interference),
        # ~40 evenly spaced examples, for reading by hand
        "interference_sample": dict(
            sorted(interference.items())[:: max(1, len(interference) // 40)]),
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.split("Usage: ")[1], file=sys.stderr)
        return 2
    print(json.dumps(survey(argv), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    server_messages.configure_logging()
    sys.exit(main(sys.argv[1:]))

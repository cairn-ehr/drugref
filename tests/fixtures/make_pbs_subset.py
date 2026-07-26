"""Extract a small, committable items.csv from a real PBS release.

Run:
    python tests/fixtures/make_pbs_subset.py \\
        downloads/tables_as_csv/items.csv > tests/fixtures/pbs_items_subset.csv

WHY THIS EXISTS rather than a hand-written fixture: a hand-written one encodes
what we BELIEVE the upstream shape is, and slice 8a's whole lesson was that the
belief was wrong three times over (spec 5.3). Extracting from the real file means
the fixture can never re-encode an assumption -- the same discipline as
make_medrt_subset.py and make_mesh_subset.py.

LICENCE (spec section 1): PBS data is NOT confirmed redistributable by drugref
(issue #25), so this extract is deliberately TINY -- roughly a dozen rows chosen
to exercise the parser, which is fair-dealing scale, not a dataset. Never commit
the full file, and never add columns beyond the allow-list below.

THE FIXTURE IS THE ONE PLACE REAL PBS DATA ENTERS THE REPOSITORY, and saying so
plainly is the point (fix round, finding 3): elsewhere the project states it
ships code and not data, and that is true of the ingest path but not of this
file. If #25 comes back negative, THIS is what has to go -- so it is kept small,
regenerable from the script below, and never treated as anything but a test
input. Reviewers checking the licence posture should start here.

The two planted columns (atc_code, amt_code) are NOT upstream. They are added
here on purpose so the quarantine test has something to prove drugref discards:
absence upstream is exactly what the test must not depend on.
"""
import csv
import sys

# The allow-list, plus the two planted encumbrance canaries.
COLUMNS = ["li_item_id", "pbs_code", "brand_name", "li_drug_name", "drug_name",
           "li_form", "schedule_form", "program_code", "benefit_type_code",
           "atc_code", "amt_code"]

# Names chosen to cover every branch of the resolver. Keep this list SHORT.
WANTED = [
    "Rifaximin",                          # plain single ingredient
    "Abacavir with lamivudine",           # ' with ' combination
    "Abiraterone and methylprednisolone",  # ' and ' combination
    "Alfuzosin hydrochloride",            # salt-stripped match
    "Dimethyl fumarate",                  # INN that LOOKS salt-suffixed (regression)
    "Alendronic acid",                    # the 'acid' trap
    "Folic acid",                         # the 'acid' trap
    "Paracetamol",                        # high-frequency, should match
    "Amoxicillin with clavulanic acid",   # combination WHERE a part ends in 'acid'
    "Allantoin with sulfur, phenol, coal tar solution and menthol",  # multi-component
]


def main(source_path: str) -> None:
    writer = csv.DictWriter(sys.stdout, fieldnames=COLUMNS)
    writer.writeheader()
    wanted = {name.lower() for name in WANTED}
    seen: set[str] = set()
    with open(source_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("li_drug_name") or "").strip().lower()
            if name not in wanted or name in seen:
                continue
            seen.add(name)
            out = {c: row.get(c, "") for c in COLUMNS}
            # Plant the canaries the quarantine test looks for (see docstring).
            out["atc_code"] = "ZZZ_ATC_CANARY"
            out["amt_code"] = "ZZZ_AMT_CANARY"
            writer.writerow(out)
    # One extra row exercising the 'null' sentinel fallback path.
    writer.writerow({
        "li_item_id": "NULLCASE_1", "pbs_code": "NULLC", "brand_name": "null",
        "li_drug_name": "null", "drug_name": "Aspirin", "li_form": "null",
        "schedule_form": "null", "program_code": "GE", "benefit_type_code": "U",
        "atc_code": "ZZZ_ATC_CANARY", "amt_code": "ZZZ_AMT_CANARY"})

    # Self-report to stderr (review round, finding 11): make_medrt_subset.py and
    # make_mesh_subset.py both report what they found, and this script silently
    # omitted it. Without this, a WANTED name that no longer appears upstream (a
    # future release renaming or dropping a product) would silently shrink the
    # fixture by one case and nobody regenerating it would notice -- the exact
    # same silent-drop failure class as finding 1's column-drift guard, just at
    # fixture-generation time instead of ingest time.
    missing = sorted(wanted - seen)
    print(f"make_pbs_subset: found {len(seen)}/{len(wanted)} WANTED names",
          file=sys.stderr)
    if missing:
        print(f"make_pbs_subset: MISSING from {source_path}: {missing}",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1])

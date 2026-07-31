"""Extract a small, committable subset of a real UNII release.

Run:
    python tests/fixtures/make_unii_subset.py \\
        downloads/UNII_Records_26Feb2026.txt > tests/fixtures/unii_subset.tsv

WHY THIS EXISTS (issue #27). The fixture this replaces was hand-written, 284
bytes, and had a `PT` column. The real release has no such column -- its
preferred term lives in `Display Name`, and `PT` is a *value* of the TYPE column
in the separate UNII_Names_*.txt file, not a header anywhere. Because the parser
read `row.get("PT")` and the hand-written fixture obligingly supplied one, the
whole suite passed while a production run would have registered every moiety with
an EMPTY display_name, an empty INN claim, and a legacy allow-list that matched
nothing. Nothing raised.

The same discipline as make_medrt_subset.py / make_mesh_subset.py /
make_pbs_subset.py, and for the same reason: a hand-written fixture encodes what
we BELIEVE upstream looks like, and this project has now been wrong about that
four times. An extracted fixture cannot re-encode the belief.

THE HEADER IS COPIED VERBATIM from the source file rather than listed here. That
is deliberate: naming the columns in this script would just move the hand-written
assumption one file to the left. If FDA adds, drops or renames a column, the
regenerated fixture carries the change and the parser's header check (unii.py)
is what decides whether that matters.

LICENCE: UNII is a US FDA work in the public domain -- it is already drugref's
bundled identity backbone (slice 1), so unlike the PBS fixture there is no
redistribution question here. The subset is small only because a test fixture
should be readable, not because the data is encumbered.

ROWS ARE SELECTED BY UNII, NEVER BY NAME. The display name is precisely the
field that drifted; selecting on it would reintroduce the bug this script exists
to prevent (magnesium sulfate is upstream as "MAGNESIUM SULFATE, UNSPECIFIED
FORM", so a name-keyed selector would silently drop it).
"""
import csv
import sys

# Each entry: UNII -> why the row is in the fixture. Keep this list SHORT; every
# row must earn its place by exercising a branch no other row does.
WANTED = {
    # has_inn True, every cross-ref populated, and the one USAN<->INN crosswalk
    # divergence the suite asserts on (acetaminophen -> paracetamol).
    "362O9ITL9D": "ACETAMINOPHEN -- INN + full cross-refs + crosswalk override",
    # has_inn True, a second fully-populated row so no assertion can pass by
    # accident on a single-row coincidence.
    "1J444QC288": "AMLODIPINE -- INN + full cross-refs",
    # has_inn False and on the legacy allow-list. ALSO the row whose upstream
    # Display Name ("MAGNESIUM SULFATE, UNSPECIFIED FORM") does not equal the
    # allow-list's "magnesium sulfate" -- the evidence for re-keying that list on
    # UNII (issue #17). ALSO the only row with a genuinely EMPTY RN upstream, so
    # it is what proves an empty cross-ref cell is omitted rather than stored "".
    "DE08037SAB": "MAGNESIUM SULFATE, UNSPECIFIED FORM -- allow-list + empty RN",
    # has_inn False and NOT allow-listed: an excipient the gate must exclude.
    "OP1R32D61U": "MICROCRYSTALLINE CELLULOSE -- correctly gated out",
    # has_inn False, allow-listed, and its Display Name DOES match the list
    # verbatim -- the contrast case that keeps the magnesium-sulfate mismatch
    # from reading as "the allow-list never works".
    "2P3VWU3H10": "ACTIVATED CHARCOAL -- allow-list entry that matches by name",

    # ---- the #26 gate redesign: one row per branch of the new rule ----------
    # No INN_ID at all, admitted on RXCUI + a drug-like type. THE headline case:
    # the old gate excluded the world's most-prescribed antibiotic and the
    # reference opioid, and nothing said so.
    "804826J2HU": "AMOXICILLIN -- RXCUI-only admission (no INN_ID upstream)",
    "76I7G6D29C": "MORPHINE -- RXCUI-only admission (no INN_ID upstream)",
    # INN_ID with a NON-drug-like type. Pins the asymmetry: a strong signal must
    # admit outright, or the type filter deletes heparin from a drug-interaction
    # service (and enoxaparin, protamine, and 346 gene/cell therapies with it).
    "ZZ45AB24CA": "HEPARIN SODIUM -- INN + `polymer` type",
    # USAN_ID only, also a `polymer`: the same asymmetry via the other strong
    # signal, so neither branch can be dropped without a test failing.
    "FZ7NYF5N8L": "IRON SUCROSE -- USAN + `polymer` type",
    # RXCUI on a non-drug-like type -> rejected. The two classes of noise the
    # type constraint exists to exclude: homeopathic botanicals and excipients.
    "0T0DQN8786": "THUJA OCCIDENTALIS LEAF -- RXCUI + `structurallyDiverse`, rejected",
    "6OZP39ZG8H": "POLYSORBATE 80 -- RXCUI + `polymer` excipient, rejected",

    # ---- slice 5b: the MOIETY ARM of CI_ChemClass ---------------------------
    # These two are a PAIR, and neither earns its place alone. MED-RT's real
    # 2026.07.06 release states `CI_ChemClass` from escitalopram (RxCUI 321988) to
    # MeSH M0016871, which is descriptor D010868 Pimozide -- a SPECIFIC DRUG, not a
    # chemical class. That assertion only becomes an exact drug-drug row if BOTH
    # ends are registered moieties: the subject through its RXNORM_IN claim (321988)
    # and the object through the UNII the MeSH record itself carries (1HIZ4DL86F).
    #
    # Before they were added, every CI_ChemClass object in medrt_subset.xml was a
    # genuine class (Alkalies, Organic Chemicals), so only the WITHHELD arm of that
    # predicate could be tested and the ingested arm was asserted by nobody. Both
    # rows, the association and the UNII on the MeSH record are read from the real
    # releases; nothing about the pairing is constructed here.
    "4O4S742ANY": "ESCITALOPRAM -- CI_ChemClass SUBJECT (RxCUI 321988), INN admission",
    "1HIZ4DL86F": "PIMOZIDE -- CI_ChemClass OBJECT; MeSH D010868 carries this UNII",

    # ---- slice 5b.2: the only ingredient carrying `induces` / `may_diagnose` ------
    # HALOTHANE (RxCUI 5095) is admitted on INN_ID 697, the strong-signal branch, so
    # it bridges the moiety gate outright and does not depend on the RXCUI branch.
    # Without this row make_medrt_subset.py's halothane associations would parse as
    # UNMATCHED SUBJECTS -- correct for ibuprofen, wrong here, since this is the
    # fixture's only ingredient exercising moiety_induced_condition and the
    # `may_diagnose` predicate at all.
    "UQT9G45D1P": "HALOTHANE -- INN admission; subject for induces / may_diagnose",
}


def main(source_path: str) -> None:
    seen: set[str] = set()
    with open(source_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        # Verbatim upstream header (see docstring): whatever FDA ships is what
        # the fixture ships.
        writer = csv.DictWriter(sys.stdout, fieldnames=reader.fieldnames,
                                delimiter="\t", quoting=csv.QUOTE_NONE,
                                lineterminator="\n")
        writer.writeheader()
        for row in reader:
            unii = (row.get("UNII") or "").strip()
            if unii not in WANTED or unii in seen:
                continue
            seen.add(unii)
            writer.writerow(row)

    # Self-report, and FAIL rather than quietly shrink the fixture: a UNII that
    # vanished upstream would otherwise remove a test case with nobody noticing
    # -- the same silent-drop class this script was written to close.
    missing = sorted(set(WANTED) - seen)
    print(f"make_unii_subset: found {len(seen)}/{len(WANTED)} WANTED UNIIs",
          file=sys.stderr)
    if missing:
        print(f"make_unii_subset: MISSING from {source_path}: "
              f"{[f'{u} ({WANTED[u]})' for u in missing]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1])

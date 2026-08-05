#!/usr/bin/env python3
"""Cut tests/fixtures/gsrs_subset.gsrs from the real GSRS public dump.

Usage:
    python tests/fixtures/make_gsrs_subset.py \
        downloads/GSRS/dump-public-2026-02-26.gsrs tests/fixtures/gsrs_subset.gsrs

The OUTPUT IS GZIPPED, because gsrs.iter_records opens with gzip.open -- the fixture
has to be the same shape as the real release or it tests a format that never ships.

COMMITTED AND RE-RUNNABLE because every fixture in this repo is extracted from a
real release, never hand-written: slice 5b found five spec errors that only real
bytes surfaced, and the last hand-written fixture invented three identifiers that
do not exist.

WHAT IS KEPT, and why each one is load-bearing (slice-3 spec 7.3):

  1. a single-parent salt                      -- the ordinary case
  2. ZINC GLYCINATE CITRATE (H3472PJ7YA)       -- THREE components; a single-FK
                                                  schema truncates it silently
  3. a solvate/anhydrous pair                  -- the second axis
  4. an active-vs-counterion discrimination    -- so a mutation defaulting NULL is caught
  5. BOTH mirror encodings of one edge         -- the direction test on real bytes
  6. a composite with components but NO active moiety -- so the gap view has a row
  7. the magnesium family                      -- the case slice 3 does NOT resolve

The magnesium family is kept precisely BECAUSE it fails. Issue 33 predicted that
GSRS gives ML30MJ2U7I -> DE08037SAB; it does not, and DE08037SAB has ZERO inbound
references across all 173,080 records. A future change that "fixes" that by joining
on shared ACTIVE MOIETY must fail a test, not pass one.
"""
import gzip
import json
import sys

# Every UNII the fixture must carry, with the role it plays. Kept as an explicit
# allow-list rather than a sampling rule so the fixture is reproducible byte-for-byte.
WANTED = {
    # (2) multi-component salt and its three components
    "H3472PJ7YA": "ZINC GLYCINATE CITRATE -- three components",
    "13S1S8SF37": "ZINC CATION -- its ACTIVE component",
    "TE7660XO1C": "Glycine -- a counterion",
    "XF417D3PSL": "Anhydrous citric acid -- a counterion, and 117 other salts' parent",
    # (1) + (5) a single-parent salt whose edge is stored from BOTH ends
    "1D06KZ672I": "CHLORTETRACYCLINE BISULFATE -- single parent, mirror-encoded",
    "WCK1KIQ23Q": "Chlortetracycline -- its parent and ACTIVE MOIETY",
    # (3) + (7) the magnesium family: the solvate axis, and the refuted case
    "SK47B8698T": "Magnesium sulfate heptahydrate -- solvate",
    "ML30MJ2U7I": "Magnesium sulfate anhydrous -- its anhydrous form",
    "DE08037SAB": "MAGNESIUM SULFATE, UNSPECIFIED FORM -- drugref's moiety, 0 inbound refs",
    "T6V3LHY838": "MAGNESIUM CATION -- the active moiety GSRS names, NOT a drugref moiety",
    "02F3473H9O": "MAGNESIUM CHLORIDE -- shares that cation; the merge to refuse",
    "1VZZ62R081": "LEVOMEFOLATE MAGNESIUM -- shares it too; the merge that is absurd",
}


def main(dump_path: str, out_path: str) -> None:
    kept = set()
    with gzip.open(dump_path, "rt", encoding="utf-8", errors="replace") as handle, \
            gzip.open(out_path, "wt", encoding="utf-8") as out:
        for line in handle:
            brace = line.find("{")
            if brace < 0:
                continue
            # Cheap pre-filter before paying for json.loads on 173,080 records:
            # the UNII appears verbatim in the line if the record is one we want.
            if not any(unii in line for unii in WANTED if unii not in kept):
                continue
            record = json.loads(line[brace:])
            unii = record.get("approvalID")
            if unii in WANTED and unii not in kept:
                kept.add(unii)
                # Copied VERBATIM, two-tab prefix and all, so the fixture is real
                # bytes rather than a re-serialisation of our own parse.
                out.write(line if line.endswith("\n") else line + "\n")
    missing = sorted(set(WANTED) - kept)
    if missing:
        raise SystemExit(f"FIXTURE INCOMPLETE -- not found in the dump: {missing}")
    print(f"wrote {len(kept)} records to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])

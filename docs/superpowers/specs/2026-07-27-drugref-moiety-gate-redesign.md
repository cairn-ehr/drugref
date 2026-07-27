# Moiety membership gate — redesign (issue #26)

**Date:** 2026-07-27 · **Status:** accepted · **Slice:** identity spine (slice 1, corrective)
**Issue:** [#26](https://github.com/cairn-ehr/drugref/issues/26) · **Depends on:**
[#27](https://github.com/cairn-ehr/drugref/issues/27) (the `Display Name` fix, same branch)

## 1. The problem

Slice 1 defined moiety membership as **`has_inn = bool(INN_ID)`** (design §6.1), on the assumption
that UNII's `INN_ID` column means "this substance has a WHO INN".

Measured against the real `UNII_Records_26Feb2026.txt` (168,046 rows), that assumption is false.
`INN_ID` is populated for 12,588 records (7.49%) and is **empty for amoxicillin, morphine, codeine,
doxycycline, tacrolimus, dasatinib and aspirin**. It is a sparse cross-reference, not a has-INN flag.

A drug registry that excludes amoxicillin and morphine is not fit for the purpose drugref exists for.
The gate is also the **binding constraint behind every coverage number the project has published** —
MED-RT classification yield, the MeSH PA bridge, and slice 8a's PBS bridge (84.6% against the gated
registry vs a 92.4% ceiling against all UNII names).

## 2. What was measured

All figures from `UNII_Records_26Feb2026.txt`, 168,046 rows.

### Signal availability

| signal | rows | share |
|---|---:|---:|
| `INCHIKEY` / `MF` | 125,803 | 74.9% |
| `NCIT` | 23,951 | 14.3% |
| `DAILYMED` | 14,901 | 8.9% |
| `RXCUI` | 13,707 | 8.2% |
| `INN_ID` | 12,588 | 7.5% |
| `USAN_ID` | 5,404 | 3.2% |

### Candidate gates

| gate | admitted | share |
|---|---:|---:|
| `INN_ID` (today) | 12,588 | 7.49% |
| `INN \| USAN` | 14,208 | 8.45% |
| `INN \| USAN \| RXCUI` | 24,284 | 14.45% |
| `(INN \| USAN \| RXCUI) & drug-like type` | 18,766 | 11.17% |
| **`INN \| USAN \| (RXCUI & drug-like type)`** | **19,436** | **11.57%** |

### Why the asymmetry, and not a uniform type filter

Applying the substance-type filter uniformly (row 4 above) is **unsafe**. 571 records carry an
`INN_ID` but a non-drug-like `SUBSTANCE_TYPE`, and they include:

| substance | type | why it must not be excluded |
|---|---|---|
| heparin sodium | `polymer` | among the highest-DDI-risk drugs in existence |
| enoxaparin sodium | `polymer` | as above |
| protamine sulfate | `structurallyDiverse` | heparin's reversal agent |
| ferric carboxymaltose, iron sucrose | `polymer` | routinely prescribed IV iron |
| ciltacabtagene autoleucel, and 345 more | `structurallyDiverse` | CAR-T / gene therapies |

So the design **trusts a strong identifier over a type judgement**: an INN or a USAN is an act of
naming by WHO or USAN Council, which is a positive assertion that the substance is a drug. UNII's
`SUBSTANCE_TYPE` is a chemistry classification and was never intended to answer "is this a
medicine".

The type filter is applied **only** to the weak signal. `RXCUI` alone is a weak signal because
RxNorm covers US-marketed content broadly, including excipients (microcrystalline cellulose carries
`RXCUI 1000577`), homeopathic botanicals and allergen extracts.

## 3. The design

```
is_moiety(cand, allowlist) =
       cand.has_inn                                          # strong: WHO INN
    or cand.has_usan                                         # strong: USAN Council
    or (cand.has_rxcui and cand.substance_type in DRUG_LIKE) # weak, type-constrained
    or cand.unii in allowlist                                # curated exceptions
```

with `DRUG_LIKE = {"chemical", "protein", "nucleicAcid"}`.

### Monotonicity is a design property, not an accident

Every substance the old gate admitted, the new gate admits: `INN_ID` implies `has_inn`, and the
allow-list only gained members (#27 re-keyed it on UNII, which *fixed* magnesium sulfate rather than
dropping anything). **The gate may widen; it must never silently narrow.** This is pinned by a test,
because a narrowing would remove drugs from a registry whose UUIDs are immortal and whose consumers
have already cited them.

### The allow-list keeps its job

The new gate still rejects magnesium sulfate (`mixture`), activated charcoal (`polymer`) and
pancrelipase (`mixture`) — all real drugs with no INN and no USAN. The curated allow-list is
therefore not a legacy wart but the **designed escape hatch for drugs the identifier signals cannot
express**. It stays keyed on UNII (#17/#27).

Sodium bicarbonate and sodium chloride become redundant allow-list entries (the new gate admits them
on their own). They are kept deliberately: the entry records curator intent, and upstream's decision
to populate an `INN_ID` or an `RXCUI` should not be what keeps a first-line drug in the registry.

### Header contract widens

`USAN_ID`, `RXCUI` and `SUBSTANCE_TYPE` join `UNII`, `Display Name` and `INN_ID` as **required**
columns of `ingest/unii.py`. `RXCUI` was previously an optional cross-reference; once it is
gate-critical its absence must raise, not quietly shrink the registry. This is #27's lesson applied
forward rather than relearned.

## 4. Admission evidence — `db/011`

```sql
CREATE TABLE drugref.moiety_admission (
    moiety_uuid uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    signal      text   NOT NULL,
    ingest_run  bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (moiety_uuid, signal),
    CONSTRAINT moiety_admission_signal
        CHECK (signal IN ('INN_ID', 'USAN_ID', 'RXCUI', 'LEGACY_ALLOWLIST'))
);
```

**Set-valued.** A moiety admitted by both an INN and an RxCUI gets two rows. That is what makes
"which moieties rest on the weakest evidence?" a one-line query — the ordering
[#19](https://github.com/cairn-ehr/drugref/issues/19)'s curation worklist wants:

```sql
SELECT moiety_uuid FROM drugref.moiety_admission
GROUP BY moiety_uuid HAVING array_agg(signal ORDER BY signal) = ARRAY['RXCUI'];
```

**A rebuildable projection, deliberately outside slice 1's append-only floor.** The moiety is
immortal; the *evidence* is a per-release observation. If a future release stops populating a
substance's `INN_ID`, the moiety must stay (its UUID is cited) while that evidence row must be able
to disappear — otherwise the table would accumulate claims the current release does not support.
Same posture as `class_membership` and `local_product`, and the reason it is a separate table rather
than a column on `substance_moiety`: that table is floor-protected and holds only immortal facts.

The UNII orchestrator rebuilds it (delete-all, re-insert) inside the run's transaction, like every
other projection.

## 5. Consequences to re-measure

The registry grows 12,588 → 19,436 (+54%), so every published coverage figure moves:

- **PBS bridge (#26's own claim).** Slice 8a measured 84.6% against the gated registry vs a 92.4%
  ceiling and concluded the gate, not the bridge, was binding. Re-running it is what proves or
  refutes that.
- **MED-RT classification** and the **MeSH PA bridge** both join through the moiety registry, so
  their yields rise without any parser change.

These numbers are part of the deliverable, not a follow-up.

### 5.1 Measured outcome (added after implementation)

**A second constraint was hiding behind the first.** The gate change alone moved *no* downstream
number, because `run.py` writes an `INN` identity_claim only `if cand.has_inn` and the PBS bridge
indexed those claims — so the newly-admitted moieties were in the registry but invisible to it.
Both arms measured with the `Display Name` fix already in place:

| | INN-claim index | display-name index |
|---|---:|---:|
| old gate (`INN_ID` only) | 12,685 (85.5%) | 12,689 (85.5%) |
| **new gate** | 12,685 (85.5%) | **13,719 (92.4%)** |

Indexing `substance_moiety.display_name` instead is lossless (all 12,588 INN claims equal their
moiety's display_name; zero mismatches) and is right on the merits — the local tier performs a *name*
bridge and `display_name` is drugref's name for a moiety. Writing an INN claim for the new moieties
would have been the wrong fix: drugref must not assert a WHO INN it has no source for.

**So #26's diagnosis holds** — the gate, not the bridge, was binding — but only both fixes together
show it. The bridge reaches **exactly the 92.4% ceiling** slice 8a measured against all UNII
substance names. Unmatched components fall 3,140 → 347 (−89%); the salt-strip heuristic falls from
149 bridge rows to 5 (0.03%), reconfirming that slice 3 is its replacement rather than tuning.

**MED-RT, no parser change** (it joins on `RXNORM_IN` claims, which every RxCUI-carrying moiety now
records):

| metric | old gate | new gate |
|---|---:|---:|
| classified moieties | 2,066 | 3,875 (+88%) |
| membership rows | 10,562 | 18,639 (+76%) |
| populated CI rules | 331 | 635 (+92%) |
| unmatched RxCUIs | 3,946 | 2,137 (−46%) |
| `ddi_candidate_pair` rows | 6,402 | 21,664 (+238%) |

The old-gate arm's 6,402 pairs reproduces Plan B's recorded 6,395 to within the two moieties the
allow-list re-key added, which is the consistency check on the measurement itself.

Registry: **12,591 → 19,438** moieties, admitted by `INN_ID` 12,588 · `RXCUI` 8,694 · `USAN_ID`
5,404 · `LEGACY_ALLOWLIST` 4; **5,227 rest on `RXCUI` alone**, the weakest evidence and the natural
head of a curation worklist.

## 6. Known residual — stated, not hidden

4,453 records carry **both** `RXCUI` and `DAILYMED` (i.e. are marketed in the US) yet are rejected,
because they have no INN/USAN and a non-drug-like type. Composition:

| type | rows | assessment |
|---|---:|---|
| `structurallyDiverse` | 3,015 | botanicals, homeopathics, allergen extracts, venoms — correctly excluded |
| `polymer` | 821 | PEG, polysorbate, hypromellose, dimethicone — excipients, correctly excluded |
| `mixture` | 600 | mostly excipients and inorganic salts |
| `specifiedSubstanceG1` | 17 | — |

Genuine misses do exist in the tail: **pancrelipase**, **sodium polystyrene sulfonate**, **monobasic
sodium phosphate**. They are **not a new loss** — today's `INN_ID` gate excludes them too. They are
allow-list candidates, and curating them is deliberately left to a separate, measured pass rather
than added anecdotally here.

## 7. Licence (CLAUDE.md rule 6)

**No new source and no new dependency.** `USAN_ID`, `RXCUI` and `SUBSTANCE_TYPE` are columns of the
FDA UNII data file, a US federal work in the public domain, which drugref already ingests as its
identity backbone. Reading an RxCUI *from that file* is not an RxNorm ingest; RxNorm remains demoted
to a claim, exactly as slice 1 decided. `NOTICE` is unchanged.

## 8. Testing

- **Gate unit tests** — one per admitting signal; one per rejection class (excipient by type,
  botanical by type, no-signal); the heparin/enoxaparin/protamine cases that the uniform type filter
  would have broken.
- **Monotonicity test** — every `INN_ID` holder is still admitted (§3).
- **Header-contract tests** — each newly required column raises when absent.
- **Fixture** — regenerated by `tests/fixtures/make_unii_subset.py`, extended with the exemplars
  that motivated the change (amoxicillin: RXCUI-only; heparin: INN + `polymer`; a botanical; the
  existing excipient).
- **DB tests** — `moiety_admission` is set-valued, is rebuilt rather than appended across two
  ingests, and every admitted moiety has at least one evidence row (a conservation check in the same
  spirit as the MeSH no-silent-drop test).

## 9. Open questions this does not close

- **[#5](https://github.com/cairn-ehr/drugref/issues/5)** — the INN *display name* is still derived
  from UNII's `Display Name` plus a hand-curated crosswalk. Measured during this work: the
  `UNII_Names_*.txt` file carries `TYPE='of'` (official name) rows covering 24,127 UNIIs, and for
  acetaminophen it lists **both** `ACETAMINOPHEN` and `PARACETAMOL` — so an authoritative INN source
  may be derivable from a file drugref already downloads, and the crosswalk may be replaceable by
  data rather than curation. Not attempted here; recorded because the measurement was made.
- **[#3](https://github.com/cairn-ehr/drugref/issues/3)** — moiety immortality across a UNII change.
- The residual of §6.

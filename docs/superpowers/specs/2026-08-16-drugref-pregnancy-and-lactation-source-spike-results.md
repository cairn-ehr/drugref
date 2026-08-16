# Pregnancy and lactation source spike — measured results

**Run:** 2026-08-16T10:38:32+00:00 · **Database:** `drugref_db038` ·
**Status:** computational source spike complete; clinician review pending.

Command:

```bash
uv run python -m tools.pregnancy_lactation_spike \
  --dsn 'host=localhost port=5532 dbname=drugref_db038 user=postgres' \
  --work-dir /private/tmp/drugref-population-spike \
  --report docs/superpowers/specs/2026-08-16-drugref-pregnancy-and-lactation-source-spike-results.md
```

Full source payloads and sampled narrative remain outside the repository.
This report contains aggregate measurements, identifiers and links only.
No clinical assertion, schema row or alert was created.

## Decision

| source | decision | measured reason |
| --- | --- | --- |
| MED-RT | **Keep as candidate floor** | Existing direct categorical patient-state assertions |
| LactMed | **Design next** | 1,950 XML members parsed; 1,940 evidence records and 1,702 moieties resolved |
| AEMPS CIMA | **Design next** | 20,422 authorised products; segmented SmPCs and change API usable |
| ANSM BDPM | **Design next (non-firing)** | Official bulk plus RCP; 4.6 in 35/80 sampled products |

All new sources remain **non-firing evidence candidates**. `Design next`
does not approve normalization or writing to `curated_condition`.

## MED-RT floor

Release `2026.07.06`, checksum `d346e58d40791dcfaa3ab58eb6c7b2fdb532f85287e81c9e5243c174c41d2461`.

| condition | MeSH | direct rules | moieties |
| --- | --- | --- | --- |
| Pregnancy | D011247 | 549 | 549 |
| Lactation | D007774 | 66 | 66 |
| Pregnancy Trimester, Third | D011263 | 29 | 29 |
| Pregnancy, Abdominal | D011269 | 15 | 15 |
| Pregnancy Trimester, First | D011261 | 9 | 9 |
| Pregnancy Trimester, Second | D011262 | 5 | 5 |
| Pregnancy, Ectopic | D011271 | 1 | 1 |

## LactMed

- Archive: 208,950,187 bytes; SHA-256 `3551cce1794ccd3d7523f895cd5d2062d01780c1b29e791e049d086a5e5bde10`.
- XML members: 1,950; evidence records: **1,940**; auxiliary/support members: 10.
- Current HTML table-of-contents items: 1929; the upstream archive/HTML discrepancy is retained.
- Evidence-record duplicate IDs 0; missing revision 0.
- Rights variants: 1; disclaimer variants: 1.
- Records with UNII: 1,582; with CAS: 1,802.
- Identity buckets: `{"ambiguous_identity": 56, "candidate_unique_name": 37, "resolved_exact_claim": 1591, "unresolved": 256}`.
- Section coverage: `{"alternative_medicine": 1002, "drug_levels_context": 301, "infant_effect": 1938, "infant_level": 1849, "lactation_effect": 1937, "lactmed_summary": 1940, "maternal_level": 1844}`.
- Discovery signals: `{"conditional_or_conflicting": 938, "explicit_no_information": 1820, "postpartum_timing": 801, "prematurity": 537, "relative_infant_dose": 109}`.
- MED-RT overlap — pregnancy 234; lactation 23.
- Resolved moieties outside the direct MED-RT lactation floor: **1,679**.

Only `.nxml` members were parsed. Linked works were not downloaded.

## AEMPS CIMA

- API total 20,422; reconciled unique products **20,422** over 103 pages.
- Segmented SmPC metadata: 18,337 products.
- Distinct VTM IDs 2,159; combinations 3,144.
- Listing SHA-256: `b6ef894b0d8303d570b64c82315dca744b59d5fd61a4de5503563bb59218c4fa`.
- Stratified product/section sample: 120.
- Section coverage: `{"4.3": 112, "4.6": 112}`.
- No-sections API responses: 16.
- Ingredient name-resolution buckets: `{"candidate_unique_name": 26, "unresolved": 115}`.
- Discovery signals: `{"dose_or_route": 86, "lactation": 106, "pregnancy": 110, "trimester": 35, "uncertainty": 66}`.
- Duplicate normalized sampled sections: 0.
- Changes since 17/07/2026: 46,358 reported; 200 returned on the cached first page.

CIMA names remain identity candidates. ATC and SNOMED were not read.

## ANSM BDPM

- Specialties 15,857; composition rows 32,420; with composition 15,855.
- Distinct ingredient names: 4,477.
- Ingredient name-resolution buckets: `{"ambiguous_name": 1, "candidate_unique_name": 1047, "unresolved": 3429}`.
- Bulk SHA-256: specialties `d847c03abbfd42f9994e68025e317dca6424f12b498f61ad807daaad79735087`; composition `f7efa70919c35fad1952478d9f9ee3b811c2ad47f19ef5399661a70844bb0f96`.
- RCP sample 80; fetch failures 0.
- Section coverage: `{"4.3": 35, "4.6": 35}`.
- Revision coverage: `{"present": 39}`.
- Discovery signals: `{"dose_or_route": 17, "lactation": 35, "pregnancy": 35, "trimester": 8, "uncertainty": 7}`.
- Duplicate normalized sampled 4.6 sections: 0.

Official RCP responses are server-rendered with stable 4.3/4.6 anchors.
Production retrieval still needs rate limits and change tests.

## Clinician-readable review worklist

The deterministic sample was chosen before inspecting clinical meaning.
Cached inputs contain exact sections; this table contains identifiers only.

| source | record | product | ingredients | sections |
| --- | --- | --- | --- | --- |
| CIMA | 42991 | A.A.S. 100 mg COMPRIMIDOS | 1 | 4.3, 4.6 |
| CIMA | 84068 | ATAZANAVIR STADA 300 MG CAPSULAS DURAS EFG | 1 | 4.3, 4.6 |
| CIMA | 66605 | CETIRIZINA CINFA 10 mg COMPRIMIDOS RECUBIERTOS CON PELICULA EFG | 1 | 4.3, 4.6 |
| CIMA | 69240 | DOXAZOSINA NEO SANDOZ 4 mg COMPRIMIDOS DE LIBERACION PROLONGADA EFG | 1 | 4.3, 4.6, 4.6.1, 4.6.2 |
| CIMA | 86161 | FIXAPROST 50 MICROGRAMOS/ML + 5 MG/ML COLIRIO EN SOLUCION | 2 | 4.3, 4.6 |
| CIMA | 09550001 | JAVLOR 25 mg/ml CONCENTRADO PARA SOLUCION PARA PERFUSION | 1 | 4.3, 4.6 |
| CIMA | 49319 | MERCROMINA FILM 20 MG/ML SOLUCIÓN CUTÁNEA | 1 | 4.3, 4.6 |
| CIMA | 1151014001IP | OPDIVO 10 MG/ML CONCENTRADO PARA SOLUCION PARA PERFUSION | 1 | — |
| CIMA | 91298 | QUETIAPINA NORMON 400 MG COMPRIMIDOS RECUBIERTOS CON PELICULA | 1 | 4.3, 4.6 |
| CIMA | 76926 | SILDENAFILO QUALIGEN 50 mg COMPRIMIDOS MASTICABLES EFG | 1 | 4.3, 4.6 |
| CIMA | 89885 | TRICUAL 20 MG/5 MG/5 MG COMPRIMIDOS | 3 | 4.3, 4.6 |
| CIMA | 67445 | ÓXIDO NITROSO MEDICINAL LÍQUIDO LINDE, 98%, GAS CRIOGÉNICO MEDICINAL EN RECIPIENTE CRIOGÉNICO MÓVIL | 1 | 4.3, 4.6, 4.6.1, 4.6.2, 4.6.3 |
| BDPM | 61266250 | A 313 200 000 UI POUR CENT, pommade | 1 | — |
| BDPM | 67568483 | ASPAVELI 1080 mg, solution pour perfusion | 1 | — |
| BDPM | 66143084 | CASTANEA VESCA BOIRON, degré de dilution compris entre 2CH et 30CH ou entre 4DH et 60DH | 6 | — |
| BDPM | 64285722 | DOGMATIL 0,5 g/100 ml SANS SUCRE, solution buvable édulcorée au cyclamate de sodium | 1 | 4.3, 4.6 |
| BDPM | 63052576 | FESOTERODINE BIOGARAN LP 8 mg, comprimé à libération prolongée | 2 | — |
| BDPM | 69867826 | INDAPAMIDE CRISTERS LP 1,5 mg, comprimé pelliculé à libération prolongée | 1 | 4.3, 4.6 |
| BDPM | 69980852 | LORAMYC 50 mg, comprimé buccogingival muco-adhésif | 1 | — |
| BDPM | 63576333 | NOVONORM 0,5 mg, comprimé | 1 | — |
| BDPM | 61715282 | PONVORY 2 mg + 3 mg + 4 mg + 5 mg + 6 mg + 7 mg + 8 mg + 9 mg + 10 mg, comprimé pelliculé | 9 | — |
| BDPM | 61190991 | SEVELAMER CARBONATE VIATRIS 800 mg, comprimé pelliculé | 1 | 4.3, 4.6 |
| BDPM | 64878644 | TRAMADOL SANDOZ L.P. 150 mg, comprimé pelliculé à libération prolongée | 1 | — |
| BDPM | 68311278 | ZYVOXID 600 mg, comprimé pelliculé | 1 | — |

**Pending human step:** a clinician must verify extraction boundaries,
product scope, and whether normalization would be unsafe.

## Licence evidence snapshots

| evidence | bytes | SHA-256 | live URL |
| --- | --- | --- | --- |
| lactmed_collection | 298531 | `15bd47cc196483297bffd6b9f67f01e87d7ca188414a7d9560cdd727db3c96c2` | https://www.ncbi.nlm.nih.gov/books/NBK501922/ |
| ncbi_policy | 38936 | `8ad8f6f186ca51ec73a5fb8935ecfa17b8cbaad300b7025b381898ab72621869` | https://www.ncbi.nlm.nih.gov/home/about/policies/ |
| aemps_open_data | 221164 | `cb83b015b842fdb4dfad0b4093e21d6b4a0b796dfc3ab1ae0cdb431a37ddccfe` | https://sede.aemps.gob.es/datos-abiertos/ |
| cima_api | 479759 | `eefb623d33dbd19f0e6aa74252d5de14633201c03a057b1825048c65b51f46f6` | https://www.aemps.gob.es/apps/cima/docs/CIMA_REST_API.pdf |
| bdpm_download | 48732 | `1a0ae1bfb223e8bdedad2c206e0baab9aae8221e82454cc4c37413244d2a9732` | https://base-donnees-publique.medicaments.gouv.fr/telechargement |
| bdpm_licence | 540249 | `44ff23ae2b5cce0d729d1b53b20d17aae625189b131c2dd9cb98d1fe0d0a2a90` | https://base-donnees-publique.medicaments.gouv.fr/docs/telechargement/licence_bdpm.pdf |

Hashes prove what this spike reviewed, not permanent licence approval.
Production still requires signed rule-6 deeds and `NOTICE` entries.

## Implementation boundary confirmed

The sources have unlike grains: active-substance review, Spanish product
section, and French product section. A production design must not collapse
them into one moiety recommendation. The next round should design a
rebuildable non-firing projection plus an open-question bridge.
Clinician-signed promotion remains separate.

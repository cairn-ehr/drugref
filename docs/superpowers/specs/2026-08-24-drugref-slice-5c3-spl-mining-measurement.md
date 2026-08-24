# Slice 5c.3 — SPL/DailyMed mining: source measurement

**2026-08-24. A measurement round, not a design.** It produces figures and a
recommendation; it commits no schema, no migration and no ingest. The design
round follows and should start from these numbers rather than re-deriving them.

Its instruction came from HANDOVER: *"brainstorm before designing and measure
before both — that is now twice in a row that measuring first changed the shape
of the slice."* **It has now happened a third time, and more sharply than in
either previous round: the corpus this slice was going to read is the wrong one,
and the prerequisite built to unblock it does not fit the material.**

---

## 1. What was decided before measuring

Three choices were made in the brainstorm, and they scope everything below.

| Question | Choice |
|---|---|
| What does the slice produce from a label? | **Both** a drug × class rule and the individually-named drug × drug exemplars, kept as **separate assertion kinds with shared provenance** — never collapsed |
| How is it extracted? | **Deterministic entity recognition, no relation extraction.** Recognise which known entities a section names and where; assert nothing about what the sentence means |
| Corpus | The **full** release, both corpora, cross-checked — no sampling |

The extraction choice follows the standing invariant *ingest preserves evidence;
curation creates clinical judgement*. Deciding that a sentence means
"contraindicated" rather than "monitor" is a clinical reading of prose, so this
slice does not do it.

---

## 2. Reproduction manifest

**The checksums are recorded HERE, in the committed document**, because
`downloads/` is gitignored — a manifest that lives only beside the bytes it
describes disappears with them, and this round's whole claim to reproducibility
is that a later reader can re-fetch and verify.

**Primary corpus — openFDA bulk drug labels.** `export_date` **2026-08-22**,
**262,032** records, 14 partitions, 1.73 GB, fetched 2026-08-24 from
`https://download.open.fda.gov/drug/label/`.

| openFDA partition | bytes | SHA-256 |
|---|---|---|
| `drug-label-0001-of-0014.json.zip` | 136,839,223 | `b80efc0292a53506268cfe78fde095d03fae4b64cddf61f52511aca5dd27ada6` |
| `drug-label-0002-of-0014.json.zip` | 149,686,738 | `3ededea2121cdcbc0dd02f13f0f5b3a51458fbacff3ad87529ddb1b54d5f9f7c` |
| `drug-label-0003-of-0014.json.zip` | 144,160,121 | `dae71255a3dd6f2f7e998c7f72109e5222d8f0ec2a4f8c1a4e1a7e17d82bf613` |
| `drug-label-0004-of-0014.json.zip` | 144,466,316 | `f076ecc3237536e35be5553411e278c0404b258ce6552d96a19e19b5588cfdc9` |
| `drug-label-0005-of-0014.json.zip` | 131,252,197 | `0a7764f4b79a9197e356a3ac1d0de6832b754c08edd1a8a6272d095707f4bce4` |
| `drug-label-0006-of-0014.json.zip` | 140,826,682 | `589809d514c8cf46536b4a5027a6d5c9206f31620c85e95088ed778a67aaf7cc` |
| `drug-label-0007-of-0014.json.zip` | 150,476,971 | `9ac25d364ad732792b3e3bec32cb9485f9c96c767c3faf379d07406b3d6f3309` |
| `drug-label-0008-of-0014.json.zip` | 143,000,859 | `31df397c30cbd786027aa6a2c02d3db3b5f51bed3b067a1cb4ea3bd35ef7bc66` |
| `drug-label-0009-of-0014.json.zip` | 131,904,304 | `4903f8a0db1651c10eb8505d185bfc24c74076e401ff4e8d31970f74e8f52eb9` |
| `drug-label-0010-of-0014.json.zip` | 137,316,991 | `80a638d8d46898407bc5059ee3af62175b658be93db2d9b8f43a9ce239709a8f` |
| `drug-label-0011-of-0014.json.zip` | 151,547,066 | `c4766464281f394a575567f8f22735130353fdf3089060366f2929a102c7e1ca` |
| `drug-label-0012-of-0014.json.zip` | 138,748,320 | `02cdd166fc6f74e4d6b494dd0c900a6222ebf4ae3d09702b540a31a9c5069fea` |
| `drug-label-0013-of-0014.json.zip` | 140,050,163 | `2c9cba4c6120a7552432afc91f7c9a1fbb2b076ae7bc145c9c24c1d7864ee94e` |
| `drug-label-0014-of-0014.json.zip` | 14,935,012 | `c1085c0c1032a14f321cdd37f74d4727c99d466a9c0f384807a4a67c6fe875f5` |

**Cross-check corpus — DailyMed full Human Rx release**, `last-modified`
**2026-08-21**, 6 parts, 17.6 GB, from
`https://dailymed-data.nlm.nih.gov/public-release-files/`.

| DailyMed part | bytes | SHA-256 |
|---|---|---|
| `dm_spl_release_human_rx_part1.zip` | 3,220,967,233 | `86f2fd7f50595fb692170e0cf9299021964622be83e22d2268be7b36438508d2` |
| `dm_spl_release_human_rx_part2.zip` | 3,216,908,129 | `dd5fe50ef91b20b4a68e9d4d7cec6e98ceb15011539d53c9c5774f166bef68e2` |
| `dm_spl_release_human_rx_part3.zip` | 3,220,921,272 | `1f195888eff7b866fb8ad35f984499685c0c0dfdbb303ec42dc214220991e710` |
| `dm_spl_release_human_rx_part4.zip` | 3,221,117,168 | `5dae5cc643a330afa28f9157d10ae59f6dc6bb8759e34eae8a98784b95a4fc56` |
| `dm_spl_release_human_rx_part5.zip` | 3,221,064,059 | `5e069b5f8267f7f1c35cefb6cf9cf3d307a76307796ea751cbbc08a0f5857a6e` |
| `dm_spl_release_human_rx_part6.zip` | 1,750,936,557 | `6896e0c46f6ffa1319b870f990f7a4a6e616b388f6b944a2d73d753c576c1484` |

**Database** — `drugref_spl`, built to hold every vocabulary the measurement
compares against, because **no existing database held them all**: `drugref_5c2g`
has FDA-CYP but no DrugCentral, `drugref_dc049` has DrugCentral but neither
FDA-CYP nor the ONC floor.

```sh
createdb -T drugref_dc049 drugref_spl          # DrugCentral + MED-RT + MeSH
export DRUGREF_DSN="host=localhost port=5532 dbname=drugref_spl user=postgres"
uv run drugref migrate                          # -> db/050
uv run drugref ingest fda-cyp --page downloads/FDA/fda_cyp_2026-05-29.html
uv run drugref ingest onchigh --release 2026-08-12
```

Baselines in that database: **19,438** moieties · **4,267** classes (MED-RT
3,634 · MeSH 568 · FDA-CYP 65) · **41,166** memberships · `exact_ddi_pair`
**8,943** (DrugCentral 7,501 + MED-RT `moiety_contraindication` 1,442) ·
`ddi_candidate_pair` **21,877**.

```sh
uv run python -m tools.spl_ddi_spike extract --downloads downloads/OPENFDA --out CACHE
uv run python -m tools.spl_ddi_spike measure  --cache CACHE --dsn "$DRUGREF_DSN"
uv run python -m tools.spl_ddi_spike measure  --cache CACHE --dsn "$DRUGREF_DSN" \
    --exclude-common-words /usr/share/dict/words
uv run python -m tools.spl_dailymed_crosscheck --parts downloads/DAILYMED/*.zip \
    --cache CACHE [--negative-control]
```

The probe code is **throwaway** and says so in every module docstring:
`tools/spl_label_extract.py`, `tools/spl_entity_match.py`,
`tools/spl_ddi_measure.py`, `tools/spl_ddi_spike.py`,
`tools/spl_dailymed_crosscheck.py`. Nothing under `src/drugref/` imports any of
it. It ships with **62 tests**, because the figures below are only worth as much
as the parser that produced them and this project has recorded seven wrong
figures from partially-working probes. (The last three were added when a review
catch forced §9 to re-measure a claim it had asserted without checking — see
that section.)

---

## 3. ⇒ RULE 6: THE TWO PUBLISHERS OF THIS CORPUS TAKE OPPOSITE POSITIONS

This was checked first, before the download, because it is a blocker and because
the extraction design stores section prose.

**NLM/DailyMed asserts nothing and disclaims explicitly.** Its web policy says
US-government works are not copyrighted, then:

> *"…documents, illustrations, photographs, or other content contributed by or
> licensed from private individuals, companies, or organizations that may be
> protected by U.S. and international copyright laws."*
> *"It is your responsibility to determine and satisfy copyright or other use
> restrictions when using materials that are not in the public domain."*
> **"NLM cannot guarantee the copyright status for any item."**

And DailyMed describes its own content as *"the most recent labeling submitted
to the Food and Drug Administration (FDA) **by companies**"*. So the prose is
third-party-authored material that a federal body republishes — **the exact
shape of the DIRIL determination**, where the FDA spike ruled that *"a
public-domain FDA publication does not turn copied third-party material into
federal work"* and cleared only the FDA-authored columns.

**openFDA — FDA's own service — dedicates the same content to the public
domain:**

> *"the content, data, documentation, code, and related materials on openFDA is
> public domain and made available with a Creative Commons CC0 1.0 Universal
> dedication."*

Its only carve-out is GMDN device terminology. Drug labeling is **not** carved
out, and `drug/label`'s `drug_interactions` field is section 34073-7.

**The determination, split by what is actually stored — because the unit of
clearance is the field, not the file:**

- **Derived facts are clear under either reading.** Which known entities a
  section names, their character offsets, the label's `set_id`, `version` and
  `effective_time`. Facts are not copyrightable, and a citation is not a copy.
  drugref's reviewer tier **already treats SPL this way** — `db/045` admits
  "citation-only DOI/PMID/PMCID/NCT/**SPL**/URL references".
- **Verbatim section prose is the contested part.** CC0 from the agency that
  receives and approves the labeling is a strong argument for it. The caution is
  that a CC0 dedication waives the dedicator's **own** rights and cannot
  extinguish a third party's, and NLM — publishing the same bytes — declines to
  make any assertion at all.

**⇒ RECOMMENDATION: design so the prose is REFERENCED, not COPIED.** Store the
entity occurrences, the offsets, and a `set_id`+`version` citation; keep the
text itself node-local and re-fetchable rather than bundled. This is not a
concession — it satisfies both readings at once, it costs the slice nothing that
matters, and it matches how the reviewer tier already handles SPL. **This is a
licensing-posture call for the project owner, and it needs an explicit decision
before the schema is set** — filed as an issue rather than assumed here.

---

## 4. ⇒ THE CORPUS DECISION REVERSED ITSELF ON MEASUREMENT

The round opened committed to DailyMed's 18 GB Human Rx release. openFDA carries
the same section and is better on every axis that matters:

| | DailyMed Rx release | openFDA bulk labels |
|---|---|---|
| Licence | *"cannot guarantee the copyright status"* | **CC0 1.0 dedication** |
| Size | ~18 GB, 6 parts | **1.73 GB, 14 partitions** |
| Section 34073-7 | nested zips → XML → split by LOINC | **pre-split `drug_interactions` field** |
| Identity | resolve ingredient names by string | **`openfda.unii`, and `moiety_uuid` is UUIDv5-on-UNII** |
| Rx/OTC | by document-type code | `openfda.product_type` |

**The decision was still to take both**, and that was right: the cross-check in
§9 is what turns "openFDA's field looks correct" into a measured claim.

---

## 5. Corpus census (openFDA, all 14 partitions)

| | labels |
|---|---|
| records read | **262,032** |
| carry section 34073-7 | **68,550** |
| do not carry it | 193,482 |
| — `HUMAN PRESCRIPTION DRUG` | 28,101 |
| — no `openfda` block at all | **40,413** |
| — `HUMAN OTC DRUG` | **23** |
| — `CELLULAR THERAPY` | 13 |
| carry ≥ 1 UNII | 27,694 |
| **DISTINCT WORDINGS** | **27,406** |

**The de-duplication factor is 2.50 labels per wording** — and it is much lower
than expected. One UNII appears on up to **498** separate labels, which invited
the assumption that generic labels copy one another; measured, they do not.
Different manufacturers write their own section 7. **Every rate below is
therefore quoted against 27,406 wordings, never against 68,550 labels.**

**23 OTC labels of 68,550 independently confirm the earlier 0-of-30 finding**:
section 34073-7 is a prescription-label section. A corpus that does not filter
by product type is not wrong so much as pointless.

**The 40,413 labels with no `openfda` block are the cost of this corpus** — 59%
of it. They carry the section but no UNII and no product type, so their subject
drug cannot be keyed from the record alone.

---

## 6. Entity yield — the material is dense

Denominator: **27,406 distinct wordings**.

| | count | share |
|---|---|---|
| name ≥ 1 known entity | 27,021 | **98.6%** |
| name ≥ 1 known **moiety** | 26,754 | **97.6%** |
| name ≥ 1 known **class** | 25,530 | 93.2% |
| moiety occurrences | 1,319,099 | |
| class occurrences | 463,792 | |
| distinct moieties named | **2,151** | |
| distinct classes named | 527 | |

Exact, case-insensitive matching against `substance_moiety.display_name` — the
same rule FDA-CYP's shipped resolver uses, deliberately, so this is not a more
generous variant that would flatter the result.

---

## 7. ⇒ THE CLASS VOCABULARY DOES NOT FIT THE MATERIAL, AND THAT IS THE FINDING

93.2% of wordings "name a known class" — and that number is close to worthless
until it is split by whether the class **has any members**. A class with none
cannot be one end of an interaction rule, however often a label names it:
expanding it reaches nobody.

| publishing axis | class occurrences | of which the class is EMPTY |
|---|---|---|
| MED-RT (non-PK) | 265,955 | 71,944 |
| MeSH | 115,583 | **112** |
| **MED-RT PK axis** | 80,042 | **77,795 (97.2%)** |
| **FDA-CYP** | **2,212** | **0** |
| **total** | **463,792** | **149,851 (32.3%)** |

**MED-RT's PK axis is not a drug-class vocabulary at all.** Its 59 concepts are
pharmacokinetic *properties* — `Absorption`, `Clearance`, `Distribution`,
`Elimination`, `First Order`, `Half-Life`, `Cytochromes`, `Compartments`, `Hair
Excretion` — and **only 6 of the 59 have a single member**. Matching them
against label prose recognises ordinary pharmacokinetic English: `Clearance
[PK]` scores 22,277 "mentions", `Metabolism [PK]` 17,513, `Absorption [PK]`
11,634. **These are false positives with a class UUID attached**, and any design
that treats the PK axis as an endpoint vocabulary will manufacture them.

MeSH is the opposite and is the quiet good news: 115,583 occurrences, 112 of
them empty.

**A second effect, working as designed but worth naming:** `Diuretics` (MeSH)
and `Diuretic [APC]` (MED-RT) both score 17,118 because they fold to one string
and the matcher returns **both** entries rather than silently picking one. That
is deliberate — FDA-CYP's rule is *ambiguity is unresolved, never "pick the
first"* — but it means class occurrences are not a count of distinct clinical
concepts, and cross-source class identity is an open problem this slice inherits
rather than creates.

---

## 8. ⇒ THE HEADLINE: THE POTENCY BAND IS 7× MORE COMMON THAN drugref CAN SEE

This is issue [#102](https://github.com/cairn-ehr/drugref/issues/102), and the
measurement inverts the premise everyone has been working from.

**8.1 The class drugref built for this is empty.** `CYP1A2 strong inhibitor
[FDA-CYP]` exists with **0 members**. Chasing the two drugs the tizanidine label
names as strong CYP1A2 inhibitors:

- **fluvoxamine** — FDA does band it `1A2; 2C19 strong inhibitor`, but the row
  carries footnote 8, so `db/039`'s withhold-on-any-footnote rule filed it
  `withheld_qualified`. Footnote 8 concerns CYP3A substrates and does not negate
  the 1A2 claim: a conservative withhold, working exactly as designed.
- **ciprofloxacin** — FDA files it under `CYP Mod INH`, **moderate**, and its
  footnote 20 is the whole story:

> *"Ciprofloxacin is generally classified a moderate CYP 1A2 inhibitor based on
> totality of evidence; however, it can sometimes behave like a strong inhibitor
> (i.e., increase AUC more than 5-fold) when it interacts with certain CYP 1A2
> substrates that are considered highly sensitive (**e.g., tizanidine**)."*

**FDA's own footnote names tizanidine.** The label and the table do not
disagree: **FDA is saying the band is not a property of the inhibitor, it is a
property of the (inhibitor, substrate) PAIR.**

**⇒ That retires two of issue #102's four options.** Option 1 (a `potency_band`
column on the rule) and option 2 (drugref-minted `Strong CYP1A2 Inhibitors`
subclasses) both hang the band on the **class**. If the band is pair-scoped,
a per-class band is not merely coarser than the source — **it is wrong for the
case it was introduced to fix**, and it would assert `strong` for ciprofloxacin
against every CYP1A2 substrate when FDA says that holds for the highly sensitive
ones only.

**8.2 The band looked rare, and that was an artefact of our own vocabulary.**
Measured through drugref's stored class names, only 0.8% of class occurrences
carry a band word (1.2% on the PK axes). Measured directly against the prose:

| phrasing | occurrences | wordings | share of wordings |
|---|---|---|---|
| `band + CYP<n> + role` — *"Strong cytochrome P450 3A4 inhibitors"* | **15,708** | 4,236 | **15.5%** |
| `band + role`, pathway elsewhere — *"moderate or weak inhibitors"* | 7,444 | 4,416 | 16.1% |
| any band word within 30 chars of a role word | **24,750** | **6,973** | **25.4%** |

against the **2,212** occurrences the stored FDA-CYP vocabulary actually
matched — **roughly a 7× gap**.

**The cause is word order.** Labels write *"strong CYP1A2 inhibitors"*; drugref
stores *"CYP1A2 strong inhibitor"*. The matcher is contiguous by design (a
matcher that skips words produces spans it cannot quote back), so it sees
neither. The tizanidine label's actual phrasing — *"strong cytochrome P450 1A2
**(CYP1A2)** inhibitors"* — additionally interrupts the phrase with a
parenthetical, which is pinned as a deliberately-failing test case rather than
papered over.

**⇒ So the band question is not an edge case affecting a handful of labels. It
is present in a quarter of all distinct wordings, and the design round cannot
treat it as a corner to be swept into a gap view.**

---

## 9. Candidate drug–drug pairs — the figure that decides the slice

Pairs are orientation-normalised and de-duplicated, so they are directly
comparable with DrugCentral's. Self-pairs are excluded: a label routinely names
its own drug, and a drug does not interact with itself.

### ⇒ The first pass asserted causes it had not measured, and got one backwards

This section originally reported a **range** between "all names" and "all names
minus the 477 that appear in `/usr/share/dict/words`", justified by the claim
that exact matching admits ordinary English — *"`prothrombin` is a lab test,
`lead` is a verb"*. **Both halves of that were unverified, and the framing was
wrong.** The correction is recorded rather than quietly applied, because it is
the standing rule at work: *a disposition records what was OBSERVED, never what
the round suspects it MEANS* — the [#122](https://github.com/cairn-ehr/drugref/issues/122)
manufactured-cause defect, reached again.

Measured over the 27,406 wordings:

| name | occurrences | what it actually is |
|---|---|---|
| `lead` | 9,160 | **the verb** — 9,157 (100.0%) followed by `to`, preceded by *may* (6,855) / *can* (1,190) / *could* (403). **False positive.** |
| `prothrombin` | 9,363 | **a lab test** — 81.6% followed by `time`, 10.0% `times`, 1.9% `activity`. **False positive.** |
| `serotonin` | 19,804 | **a syndrome and a drug class** — 50.2% `syndrome`, 23.6% `reuptake`, 5.9% `norepinephrine`. **Mostly false positive.** |
| `alcohol` | 13,530 | **ethanol, a genuine interactant** — preceded by *excessive*, *opioids and*, *including*; only **0.2%** excipient-qualified (`benzyl`/`cetyl`/…). **TRUE positive, wrongly listed as suspect.** |

**And the dictionary endpoint was wrong in both directions.** `lead` and
`prothrombin` are dictionary words and were dropped correctly; but `serotonin`
is **not** in the dictionary and survived, while `alcohol`, `iron` and — worst —
**`lithium`, the single most-matched moiety in the whole corpus at 28,368
occurrences and a clinically critical interactant** — are dictionary words and
were deleted. **So that endpoint is not a lower bound on the truth. It is a
differently-wrong number**, and calling it the bottom of a range implied a
guarantee it does not carry.

**The real mechanism is not "ordinary English".** Three of the four are the
**head of a longer term that names something else** — and the matcher's own
longest-match-wins rule would already suppress them *if drugref held the longer
term*. It does not hold `prothrombin time` or `serotonin syndrome`, so the short
name wins by default.

⇒ **The fix is a negative vocabulary, not a stop-list**, and it was tested rather
than argued: nine measured non-entity terms registered as `suppress` entries
(`tools/spl_suppress_terms.txt`, every line carrying the distribution that
justifies it). A stop-list deletes a name everywhere, **including where it is
genuinely the drug** — and lead-the-element (Pb) is a real moiety and a real
interaction participant, through chelation therapy. Suppression removes it only
inside the phrase that misleads.

**A folding cost worth naming, bounded at 0.28%:** the registry spells stereoisomers
with a punctuation suffix (`carvone, (+)-`, `epinephrine,(+/-)-`, `.beta.-pinene`),
and the matcher's fold strips punctuation — so **24 folded keys carry more than one
registry name, covering 55 of 19,438**. The matcher returns every colliding entry and
refuses to pick one. The direction matters for DDI specifically, since S- and
R-warfarin take different CYP pathways; it is
[#128](https://github.com/cairn-ehr/drugref/issues/128) reached from the other side.
It is also why the dictionary endpoint counts 477 rather than the 463 a plain
`lower()` finds — the extra 14 are stereo-suffixed names folding onto a common
word.

### The three variants, and which one to quote

| | all names | dictionary-excluded | **suppression (measured)** |
|---|---|---|---|
| labels with a resolved subject | 27,494 | 27,494 | 27,494 |
| labels with **no** resolvable subject | 41,056 | 41,056 | 41,056 |
| moiety occurrences | 1,319,099 | — | 1,286,775 |
| **distinct candidate pairs** | 21,201 | 17,279 | **20,554** |
| already held (exact **or** class) | 2,447 | 2,272 | 2,447 |
| **NOVEL** | 18,754 (88.5%) | 15,007 (86.9%) | **18,107 (88.1%)** |
| novel vs `exact_ddi_pair` alone | 19,339 (91.2%) | 15,558 (90.0%) | 18,692 (90.9%) |

**⇒ Quote the suppression column: 20,554 distinct candidate pairs, 18,107
(88.1%) novel.** It is the only one of the three whose exclusions were each
measured. The naive column over-counts by the 32,324 occurrences of the three
confirmed false positives; the dictionary column under-counts by deleting
lithium, alcohol and iron.

**For scale: DrugCentral's whole slice was justified on 7,501 pairs at 91% new.
SPL yields nearly three times that, at the same novelty rate.** On this figure
alone the slice is worth building — and note the conclusion is robust to which
column you take, which is the one virtue the original range framing did have.

**The 41,056 labels with no resolvable subject are the counterweight** — 60% of
the section-carrying corpus, discarded before a pair can form, because openFDA's
`openfda` block is absent. Recovering them is a design question with a known
route (the `set_id` resolves against DailyMed's own XML, which carries the
ingredient list), and it is not free.

---

## 10. Cross-check against DailyMed's source XML

openFDA's `drug_interactions` is FDA's own derivation from the SPL XML. Trusting
it would repeat this project's most-recorded failure — *a plausible value from a
parser nobody verified, written down as a measurement.* So the same labels were
read from DailyMed's release and compared, by **token containment** (what
fraction of the source section's tokens survive into openFDA's field), not by
equality: openFDA prepends the section title and flattens tables, so a faithful
reproduction is never byte-identical.

**Containment, not Jaccard, and the difference is not cosmetic.** The question
is whether openFDA DROPPED anything, which is asymmetric — extra text on
openFDA's side is formatting, missing text is the defect. Jaccard punishes both
equally and scores a perfect short-section reproduction at 0.50; that is pinned
as a test so the metric cannot be "simplified" back.

### Coverage — all six parts

| | labels |
|---|---|
| scanned across the 6 parts | **54,813** |
| `HUMAN PRESCRIPTION` (`34391-3`) | 54,793 |
| carry section 34073-7 | **39,743** (72.5%) |
| — of which prescription | 39,724 |
| `set_id` **present** in openFDA | **39,678** |
| `set_id` **missing** from openFDA | **65** (0.16%) |

**openFDA loses essentially nothing from this release** — 65 labels of 39,743.

**And it is a superset, not a subset**: openFDA carries **68,550** labels with
the section against DailyMed's 39,743, i.e. **28,807 more**. DailyMed's release
is current in-use Human Rx only; openFDA's export is not restricted that way,
which is also where most of its 40,413 identity-less records live. **So the two
corpora are not two views of one population, and a figure from one may not be
quoted against the other's denominator.**

*(The DailyMed download page states 50,813 files for this release; the six parts
actually contain 54,813 labels. Counted, not quoted — the scan is the authority
here, and the page's figure is recorded only so the difference is not
rediscovered as a defect later.)*

### Fidelity — and the negative control that makes it mean something

2,000 labels, each read from DailyMed's XML and compared with openFDA's field
for the same `set_id`:

| | value |
|---|---|
| labels compared | 2,000 |
| containment **== 1.00** (nothing lost) | **2,000** |
| containment < 0.80 (content lost) | **0** |
| mean containment | **1.0000** |
| mean Jaccard (symmetric) | 1.0000 |

**openFDA's `drug_interactions` reproduces section 34073-7 exactly**, including
the nested 7.1/7.2 subsections where the tizanidine label puts its whole
strong-versus-moderate distinction.

**A perfect score proves nothing until the check has been shown it can fail** —
that is `db/050`'s lesson, where *"every reconciliation in the slice proved the
orchestrator self-consistent and none proved it published anything."* So the
same 2,000 comparisons were re-run with each label deliberately paired against a
**different** label's openFDA text:

| | real pairing | negative control |
|---|---|---|
| mean containment | **1.0000** | **0.4276** |
| containment < 0.80 | 0 | **1,937** |
| mean Jaccard | 1.0000 | 0.1535 |

The check discriminates, so the perfect score is evidence rather than an
artefact.

**One honest limit, visible in that control:** 55 of the 2,000 mismatched pairs
still score 1.00, because containment is a **set** measure — a very short
section's tokens can be wholly contained in an unrelated longer one. It does not
affect the result here (the real pairing scores 1.00 on all 2,000, not on 1,945)
but a future round using this metric on short texts alone should know it.

---

## 11. What the design round should carry forward

1. **Take openFDA as the corpus**, pinned by `export_date` + per-partition
   SHA-256, with DailyMed as the cross-check and as the recovery route for
   identity-less labels. Its section field is **verified, not assumed** —
   containment 1.0000 on 2,000 labels, against a negative control that collapses
   to 0.4276. But **the two corpora are not two views of one population**
   (openFDA 68,550 section-bearing labels against DailyMed's 39,743), so no
   figure from one may be quoted against the other's denominator.
2. **Settle rule 6 explicitly** (§3). The recommendation is to reference the
   prose rather than bundle it.
3. **The band is pair-scoped, not class-scoped** (§8.1), and it affects a
   quarter of wordings (§8.2). Issue #102's options 1 and 2 should be retired
   and the question re-opened in those terms.
4. **Do not use MED-RT's PK axis as an endpoint vocabulary** (§7). 97.2% of its
   matched occurrences are empty classes and the concepts are properties, not
   drug classes.
5. **The class-name gap is a real work item, not a normalisation detail** — word
   order, abbreviation (`CYP3A4` vs `Cytochrome P450 3A4`), and parentheticals
   all defeat exact matching, and together they are the difference between 2,212
   and ~15,708 banded matches.
6. **Subject resolution is 40% of the corpus** and the rest needs a route.
7. **The moiety grain is ready now.** 97.5% of wordings name a known moiety, and
   the pair yield is nearly 3× DrugCentral's at the same novelty. If the slice
   needs to be cut down, the drug × drug half stands on its own and the class
   half is where every unsolved problem lives.
8. **Use a negative vocabulary, not a stop-list, for false positives** (§9).
   Three of the four suspect names are the head of a longer term naming
   something else, and longest-match-wins already handles them once the longer
   term is known. A stop-list would delete `lead` everywhere — including where a
   label means the element, which is a real moiety with a real interaction
   (chelation) — and would still miss `serotonin`, which is not a dictionary
   word. The nine terms used here are a starting point, not the finished list;
   deriving it systematically from next-word distributions is design-round work.

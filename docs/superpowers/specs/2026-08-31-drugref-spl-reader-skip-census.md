# drugref — the SPL reader skip census (2026-08-31)

**What it settles:** every branch of the DailyMed reader that declines a document,
counted over the whole Human Rx release of 2026-08-21 — the two counters PR #161's
review shipped **unmeasured into a guard that aborts the ingest**, and the three
[issue #162](https://github.com/cairn-ehr/drugref/issues/162) left uncounted entirely.

**Why one pass answers both:** each is a document skipped for a reason nobody counts,
reappearing three stages later as `absent_from_dailymed` — a fact about the READER sold as
a fact about the RELEASE, on the route whose population the design spec turns into a
commitment. They differ only in which line of the reader does the skipping.

---

## 1. How to reproduce it

```bash
uv run python -m tools.spl_skip_census \
    downloads/DAILYMED/dm_spl_release_human_rx_part*.zip
```

**163.6 s**, no database and no target set — which is the point. The census reads the
member-level counters from `spl_release.iter_release_labels` itself, and re-parses each
document's tree only to split the three situations `extract_subject_uniis` folds into one
`None`. `tests/test_spl_skip_census.py::test_the_census_NEVER_disagrees_with_the_shipped
_reader` pins that second parse as a **refinement** of the shipped one and never a rival:
this project has published seven wrong figures from partially-working probes.

The corpus is the one every 5c.3 round has read: DailyMed Human Rx, 6 parts,
`last-modified` 2026-08-21, 17.6 GB. Per-file SHA-256s are in the mining measurement
record §2 (`downloads/` is gitignored).

---

## 2. What it found

**54,813 documents** — equal, to the document, to the figure the shipped ingest published
on 2026-08-27, and equal to the total number of outer members across the six parts.

| branch | count | verdict |
| --- | --- | --- |
| `not_a_member_zip` | **0** | reported, not a drop (unchanged) |
| `no_xml_member` | **0** | drop — *was shipped unmeasured* |
| `several_xml_members` | **0** | drop — *was shipped unmeasured* |
| #162 case 1 — pre-filter setId ≠ the document's own | **0** | now a drop |
| #162 case 2 — unparseable `<versionNumber>` | **0** | now a drop |
| #162 case 3 — classCode outside the vocabulary | **`COLR` × 10** | **reported, NOT a drop** |
| `no_set_id_in_bytes` · `doctype_refused` · `parse_error` · `no_set_id_in_tree` | **0** each | unchanged |
| labels carrying no `<versionNumber>` at all | **0** | — |

Every `<ingredient classCode>` in the release:

| code | count | |
| --- | --- | --- |
| `IACT` | 635,954 | inactive — ruled on |
| `ACTIB` | 79,207 | active |
| `ACTIM` | 21,075 | active |
| `ACTIR` | 2,849 | active |
| `INGR` | 1,827 | ruled on |
| `CNTM` | 556 | ruled on |
| `COLR` | **10** | **nobody had ruled on it** |

---

## 3. ⇒ THE STANDING RISK IS RETIRED, AND IT WAS ALREADY ANSWERABLE

HANDOVER carried *"the two new counters are still unmeasured on a real release, and the
next real run may refuse where the last succeeded."* They are zero. `main` does not refuse.

**The answer was already sitting in two published numbers and nobody had put them side by
side.** The results record of 2026-08-27 states 54,813 documents read; the six parts'
central directories hold exactly 54,813 outer members, every one a `.zip`. A member skipped
for *any* of the three reasons yields no document, so those two numbers being equal
already implied all three counters were zero — derivable in seconds, without reading
17.6 GB. ⇒ *Before measuring, check whether the measurement has already been published in
two halves.*

---

## 4. ⇒ THE FIX ISSUE #162 PROPOSED WOULD HAVE ABORTED THE INGEST

#162's suggested shape was *"count all three; fold 2 and 3 into `total_dropped`"*. Applied
literally, **case 3 refuses this release**: `COLR` appears ten times, `total_dropped` would
have been 10, and `check_scan_dropped_nothing` aborts before the run row exists. The slice
would have lost its ingest to a guard added to protect it — which is exactly the risk #162
named when it said this needed a measurement rather than an edit, and exactly why it was
not fixed in #161.

**What `COLR` actually is, from the release rather than from an HL7 table.** All ten
occurrences sit on three labels in part 3, name a colour — `WHITE`, `RED`, `BLUE`,
`YELLOW` — and **none carries a `<code>` element at all**. So no `COLR` ingredient could
have contributed a subject even if the code were admitted as active: `_unii_of` requires a
`<code>` whose `codeSystem` is FDA SRS, and there is no `<code>`.

That is what keys the shipped guard on **the condition that harms** rather than on the
cause imagined (PROJECT-NOTES has the same lesson from `db/038`):

* an unknown classCode carrying **a UNII** is a drop — a future ACTIVE code looks exactly
  like this, and with only a 2.3% margin over the pair floor a small silent degradation
  passes every downstream check;
* an unknown classCode carrying **no UNII** is reported and not refused — `COLR`'s real
  shape, measured.

`COLR` itself is now in `_DOCUMENTED_INACTIVE_CLASS_CODES`, so it is not merely tolerated
but ruled on.

---

## 5. Case 1 is closed at its CAUSE, not only at its outcome

`set_id_in_bytes` takes the **first** `setId` in the bytes. SPL's `<relatedDocument>` names
the label this one replaces and carries a `setId` of its own, so a document that put its
`<relatedDocument>` first would be pre-filtered under the name of the label being replaced —
and `scan_release` compared pre-filter against tree **only for documents already in
`targets`**. A document mis-named out of `targets` was skipped before any comparison.

Two measurements, not one — and the review of this PR found the tool instrumented only
the first, so the command in §1 could not reproduce the pair it reports. It counts both now
(`untrustworthy_prefilter`, read through the shipped `prefilter_is_trustworthy`):

* the **outcome** — pre-filter setId ≠ tree setId, over every document rather than only
  targeted ones: **0 of 54,813**;
* the **cause** — `<relatedDocument` appearing before the first `<setId` in the bytes:
  **0 of 54,813**.

⇒ They are not independent, and saying so is the honest form: the cause *produces* the
outcome, so outcome-zero already implies cause-zero. Counting both is worth it because the
cause is what `prefilter_is_trustworthy` actually tests, so the census now exercises the
shipped predicate rather than a paraphrase of it — but this is one measurement confirmed
twice, not two. §3's lesson applied to §5's own evidence.

`spl_dailymed.prefilter_is_trustworthy` now tests the cause in the bytes already in memory
(no tree is built), and a non-target whose pre-filter is untrustworthy is a **drop**. It is
a drop rather than a recovery deliberately: recovery would be a policy invented against
zero observations, whereas refusing surfaces the condition to a human who can then decide
it with real data in hand.

---

## 6. The SHIPPED code, run against the real release

A census is a probe. The counters that refuse a run are new code, and this project has
shipped a guard over an unmeasured condition once already — that is what this round exists
to clean up. So the ingest was re-run end to end on `drugref_spl162`
(`TEMPLATE drugref_spl` → `migrate` → `ingest spl`, the command in the results record §1):

```
read 54,813 documents, found 10,670 of the labels looked for
spl: 68,550 labels of 262,032 records carrying 27,406 wordings -> 29,952 pairs (26,598 novel)
```

**10 min 43 s** wall clock, against the ~12.5 min the ingest round published — so the
per-document trustworthiness check costs nothing measurable, which is what bounding it to
the bytes before the selected `setId` (`endpos`) buys.

* **It did not abort.** Every new drop counter is zero on the real release, now measured by
  the code that ships rather than by the probe.
* **No reported-skip line printed**, so `skipped_unknown_class_code` and
  `skipped_not_a_member_zip` are both zero *for this run*.
* **Nothing that had no licence to move, moved**: `spl_ddi_pair` **29,952** (26,598 novel),
  `spl_label_subject` **73,867**, `spl_wording_quote` **138,187**, `spl_entity_occurrence`
  **1,297,944**, and the five subject routes — `openfda_unii` 27,494,
  `dailymed_active_moiety` 10,555, `dailymed_active_substance` 23, `absent_from_dailymed`
  30,386, `unresolved` 92 — all reproduce the 2026-08-27 record exactly.

⇒ **AND THE POPULATION DIFFERENCE IS NOW A CONCRETE NUMBER, NOT A CAVEAT.**
`skipped_unknown_class_code` is **0** while the census counts `COLR` **10 times**. Both are
right: the shipped counter is scoped to the documents the scan reads a subject from — the
10,670 figure is the DE-DUPLICATED label count, and the document count behind it is higher
by the number of labels shipping several versions — and `COLR`'s three labels are not among
the 41,056 targeted. Anyone comparing the two as if
they were one number will find a discrepancy that is not there — which is exactly the
mistake the design round made when it filed 14,455 never-read labels into a bucket meaning
*"read"*.

---

## 6a. ⇒ WHAT THE REVIEW OF THIS PR FOUND, AND WHY IT BELONGS IN THE RECORD

The measurement above stood. The **new code shipped alongside it** did not, and the pattern
is worth naming: every finding was in the half the census could not check, because the
census had already been written when that code was added.

* **The vocabulary went into two homes and drifted within one commit.** `COLR` was added to
  `spl_dailymed._DOCUMENTED_INACTIVE_CLASS_CODES` and not to the census's retyped copy —
  three lines under a comment explaining that a vocabulary with two homes is the defect this
  slice keeps finding. Re-running the census would have reported `COLR` as unruled: the
  instrument contradicting the verdict it produced. Both sets are read at call time now.
* **The census disagreed with the shipped reader on `<versionNumber/>`.** The reader keys on
  the ELEMENT (`int(get("value") or "")`), the census keyed on the ATTRIBUTE, so a
  valueless element was a run-aborting drop to one and a benign context line to the other.
  `test_the_census_NEVER_disagrees_with_the_shipped_reader` compared `version` — `None` on
  both sides — and never the junk verdict, which is the half that decides anything.
* **`total_dropped` could exceed `documents_read`.** The three document-level counters fell
  through instead of `continue`-ing, so one document tripping two was two drops *and* was
  kept in `found`.
* **Three ways a label was lost with every counter clean**: an unknown `encoding=` raises
  `LookupError`, not `ET.ParseError`, and aborted the whole scan; a member whose bytes are a
  corrupt zip raised `BadZipFile` out of the generator naming nothing; and membership was
  decided by a `.zip` suffix, so `M.ZIP` was filed under the one member bucket that does not
  refuse — the round's own headline defect, reproduced inside the fix.
* **The counters could be mis-bound undetectably.** Every counter in the fixture was seeded
  with exactly 1, so swapping two at the construction site passed all 2402 tests. The counts
  are 1/2/3/4 now.

⇒ **The lesson is narrower than "test more".** A census answers what the release contains.
It cannot answer whether the code written *in response to it* is right, and four of the six
findings are conditions the 2026-08-21 release simply does not contain. Measurement retires
a risk about the corpus; only a test retires one about the reader.

---

## 7. What this does NOT settle

* The shipped counters are scoped to **targeted** documents — the population whose subject
  the scan actually reads. The census figures above are **release-wide**. They are not the
  same population and will not be the same number. `COLR`'s three labels are **not**
  targeted — §6 establishes it: the shipped counter reads 0 where the census reads 10, and a
  targeted label carrying `COLR` would have moved both. Comparing them as though they were one number is the mistake the
  design round made when it filed 14,455 never-read labels into a bucket meaning *"read"*.
* Nothing here touches the three DailyMed reader skips' *downstream* effect on the recovery
  register, which remains 99.7% a RELEASE gap.
* [#160](https://github.com/cairn-ehr/drugref/issues/160) (the `spl_label_subject` `COPY`)
  and [#159](https://github.com/cairn-ehr/drugref/issues/159) are untouched.

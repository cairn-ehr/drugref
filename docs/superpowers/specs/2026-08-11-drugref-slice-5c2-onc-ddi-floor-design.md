# drugref — slice 5c.2: the ONC high-priority DDI floor

**Date:** 2026-08-11 · **Status:** design, approved · **Sequencing:** ROADMAP § 5c —
**5c.1 ✅ → 5c.4 ✅ (signing) → 5c.2 (this slice)**, with 5c.3 still free to follow in either order.

This is the first slice that writes **clinical content**. 5c.1 built the assertion shape and shipped it empty;
5c.4 built the signing subsystem and shipped it with nothing to sign. The ONC high-priority drug–drug
interaction list (Phansalkar 2012, and the Ayvaz/Boyce 2015 update) is the content both were built for: a
small, consensus-derived, publicly-funded set of interactions that a prescribing system should never miss.

## 1. Scope

| | |
|---|---|
| **In scope** | the encoded ONC list as a second **candidate source** · `db/031` (three changes, no new table) · a pure parser and an orchestrator · salt-form resolution · one new gap kind · drugref's own graded judgements over the list · the signing round-trip · the rule-6 determination in writing |
| Not in scope | curator-**originated** rules with no upstream backing — §2.3 records why this slice does not need them |
| Not in scope | SPL/DailyMed mining (5c.3) |
| Not in scope | `curated_condition` content — this slice touches the **interaction** half only; the 168 contradicted pairs (issue 51) stay queued |
| Not in scope | surfacing a `spurious` ruling to a consumer — §12 explains why 5c.1's deferral survives this slice |
| Not in scope | issue 67 (salt↔base *strength* equivalence). §6 resolves salt-form **coverage**, which is a different question |

## 2. The shape, and the shape that was rejected

### 2.1 The measurement that forced the question

Taken against `drugref_5c4` on 2026-08-11:

- `warfarin`, `sildenafil`, `methotrexate`, `spironolactone` and `nitroglycerin` all exist as gated-in
  moieties, each with salt twins — and **not one of them is the subject of any `class_contraindication`
  rule.** The ONC pairs built on them contribute nothing to the 593-rule worklist.
- The MAOI half of the list *is* present, per-moiety: `tranylcypromine` and `tranylcypromine sulfate` against
  `Serotonin Uptake Inhibitors [MoA]` (73 members) and `Norepinephrine Uptake Inhibitors [MoA]`, and
  `linezolid` against `Monoamine Oxidase Inhibitors [MoA]` (31 members).
- Every **class** endpoint the list needs already exists in `substance_class`.

So the ONC floor is mostly content MED-RT never asserted. A grading pass over the existing worklist would
deliver a fraction of the list and could not honestly be called a floor.

### 2.2 What the candidate tier already is

`class_contraindication` is keyed `(subject_moiety_uuid, object_class_uuid, relationship, source)` — **`source`
is in the primary key** — and `curated_interaction`'s natural key deliberately omits it, its own table comment
saying: *"one clinical fact, one live drugref judgement, however many upstream authorities asserted it."*
`curated_ddi_pair` already returns `candidate_source` and `upstream_release`.

**5c.1 designed the candidate tier for multiple upstream authorities. MED-RT is merely the only one so far.**
The ONC list is an upstream publication, so it enters as `source = 'ONCHIGH'` and drugref grades it through the
db/029 overlay **unchanged**.

### 2.3 The rejected alternative, recorded

The first shape considered was curator-**originated** rules: a declared `basis` column on `curated_interaction`
(`grades_candidate` | `drugref_asserted`) and a widened `curated_ddi_pair` that no longer requires a projected
candidate. It was rejected once §2.2 was checked, and the reasons are worth keeping because a later round with
genuinely source-less content will face them again:

- It would have widened the **read path** for content that does not need it — the ONC list has an upstream
  authority, and `candidate_source` already distinguishes authorities for free.
- `curated_target_unresolved` would have had to learn `basis` to keep meaning "the projection dropped my
  candidate" rather than "I asserted this myself".
- Salt-form coverage would have been frozen into **immortal** curated rows (§6), rather than re-derived by a
  rebuild.
- It buys nothing this slice needs: an asserted rule is only necessary when *no* authority has said it, and
  that is not the ONC list.

**When curator-originated rules do become necessary, `basis` is the shape to reach for**, and this slice
deliberately leaves the door open by not spending the column on content that has a source.

## 3. `db/031` — three changes, no new table

1. **Widen `class_contraindication_source`** from `CHECK (source = 'MED-RT')` to
   `CHECK (source IN ('MED-RT', 'ONCHIGH'))`.
2. **Widen `ingest_run_source`** to admit `'ONCHIGH'` and **`ingest_run_writer`** to admit `'onchigh_run'`.
   Verified: the current vocabularies are `UNII, CHEBI, MED-RT, MeSH, PBS, DRUGREF, GSRS` and
   `unii_run, chebi, medrt_run, mesh_run, mesh_rel_run, pbs_run, curation, unattributed, gsrs_run`.
3. **One `ci_axis` INSERT: `CI_EPC → has_EPC`, `expands_descendants = true`**, plus gap kind
   `unresolved_onc_endpoint` appended to `open_question_gap_kind` (§7) by the same idempotent `DO $$` pattern
   db/029 used.

**Why a third axis rather than forcing every endpoint onto MoA/PE.** The ONC endpoints split across both
vocabularies and neither subsumes the other: `Cyclooxygenase Inhibitors [MoA]` carries **56** members against
`Nonsteroidal Anti-inflammatory Drug [EPC]`'s **21**, but `Potassium-sparing Diuretic [EPC]` (2 members) has no
usable MoA twin. `has_EPC` already holds **1,525** memberships and 65 of the 811 EPC classes have children, so
descendant expansion is meaningful rather than decorative. db/006 built `ci_axis` precisely so that adding a
predicate is **one INSERT in one place** instead of a CHECK and a view's CASE drifting apart.

**What `db/031` does NOT touch.** db/003's `substance_class.source` CHECK stays `('MED-RT', 'MeSH')`: the ONC
list defines **no classes**, it only references MED-RT ones, so db/003's licence-scoping argument — every edge
endpoint must be an ingested class — holds unchanged and no new class authority lands. `class_membership`'s
relationship CHECK already admits `has_EPC`. Neither db/029 nor db/030 is edited; both are frozen.

## 4. The encoded list — one file, two lifetimes per entry

`src/drugref/data/onc_high_priority.yaml`, committed to the repository. Each entry carries two blocks, because
one interaction is the unit a curator thinks in, but the two halves have **different lifetimes**:

```yaml
- entry_id: warfarin-nsaid          # stable within the file; the human's handle
  candidate:                        # WHAT THE PAPERS SAY -- rebuildable projection
    subject:
      unii: 5Q7ZVV76EI              # warfarin. The KEY.
      name: warfarin                # review aid ONLY -- see below
    object:
      medrt_code: N0000175722       # Nonsteroidal Anti-inflammatory Drug [EPC]
      name: Nonsteroidal Anti-inflammatory Drug [EPC]
    axis: CI_EPC
    citation: "Phansalkar 2012, Table 2"
  judgement:                        # WHAT DRUGREF SAYS -- append-only overlay
    applies: true
    severity: major
    evidence_grade: established
    mechanism: "…drugref's own prose, never the paper's…"
    management: "…drugref's own prose, never the paper's…"
```

The two identifiers above are **real and verified** against `drugref_5c4` (warfarin `5Q7ZVV76EI`; NSAID [EPC]
`N0000175722`); only the two prose fields are illustrative, and they are the fields §10 requires drugref to
author rather than quote.

**Endpoints are keyed by stable identifier, never by name** (principle 2): UNII for a drug subject, MED-RT
concept code for a class object. The `name` field exists **only so a human reviewing the diff can see what the
identifier means**, and the parser **fails** when a name disagrees with what its identifier resolves to. A test
plants a mismatch and asserts the failure — otherwise the field is decoration that rots, and a reviewer would
be reading a name while the database read a different substance.

**Why one file rather than two.** The candidate half and the judgement half must be read together to be
reviewed at all: "is `major` the right grade for *this* pair?" is unanswerable with the pair on another page.
The two lifetimes are enforced by the **loader**, not by the filesystem (§5).

## 5. Parser and orchestrator

Per the architecture invariant — parsers pure and streaming, orchestrators the only writers:

- **`ingest/onchigh.py`** — pure. Reads the file, validates structure, returns frozen dataclasses. **No
  database access**, so every structural rule is testable without a DSN.
- **`ingest/onchigh_run.py`** — the orchestrator, owning one transaction: resolve identifiers to UUIDs, expand
  salt forms (§6), **delete-and-rebuild `class_contraindication WHERE source = 'ONCHIGH'`**, record the
  `ingest_run`, rebuild `open_question`.

**Two commands, not one, because they write different tiers:**

| command | writes | tier |
|---|---|---|
| `drugref ingest onchigh` | `class_contraindication` rows, `source = 'ONCHIGH'` | rebuildable projection |
| `drugref curate onchigh` | `curated_interaction` rows via `curation.record_interaction_judgement` | append-only overlay |

Folding both into one step would let a routine chain re-run write to the append-only tier, which is the one
place in this schema where a mistake is permanent. The ingest step joins `cli.STEPS` **after** `gsrs` (it needs
moieties, MED-RT classes, and — for §6 — the composition tree); the curate step is a deliberate operator act
and is never part of the chain.

**`curate` is a new top-level command, and it lands in its own `cli_curate.py`.** `cli.py` is at 379 lines
after 5c.4 split it twice; adding a command group there walks straight back into the rule-4 breach that slice
had to unwind, and issue 89 is still open on two files that crossed the line.

**`drugref curate onchigh` is idempotent by comparison, not by luck:** for each resolved rule it reads the live
curated row, writes nothing when every graded field is identical, and supersedes when any differs. A second run
against an unedited file must write **zero** rows, and a test asserts exactly that.

## 6. Salt forms, resolved on the projection side

`curated_ddi_pair` keys on `subject_moiety` **exactly**, so a judgement written against `warfarin` reaches
nothing for a consumer holding `warfarin sodium` — which is a real product. MED-RT itself dodges this by
asserting per-form: it carries rules for both `tranylcypromine` and `tranylcypromine sulfate`.

The orchestrator therefore resolves a subject to the base moiety **plus every gated-in moiety the composition
tree marks as carrying it as an active component** — measured examples: `4V2UBU7H8W`, `I47IU4FOCO` and
`6153CWM0CL` → `warfarin`; `7H4CZX4FYH` → `tranylcypromine`; `3IG1E710ZN` → `methotrexate` — writing one
candidate row per resolved form.

**Why on the projection side rather than at read time or in the curated rows.** Three reasons, in order of
weight:

1. Issue 68 measured that **3,631 moieties (~19%)** carry a questionable GSRS `ACTIVE MOIETY` edge. Inheriting
   clinical *advice* along that population at read time would spread advice across a suspect edge set, and it
   is the wrong first use of the composition tree.
2. A rebuildable projection **re-derives**. A salt form arriving in a later release becomes a **visible
   ungraded candidate** in `gap_uncurated_interaction_rule` on the next rebuild. Baked into append-only curated
   rows, the same event would be a silent hole — and curated rows are immortal.
3. It costs the ~1.4 ms hot path nothing, where a read-time join would land next to issues 37 and 75.

**Stated cost:** each salt form needs its own judgement row, so one clinical fact becomes several curated rows.
They cannot disagree, because all of them are written from one file entry by one orchestrator — but the count
is real and the measurement in §11 must report it.

## 7. Coverage is data, not a log line

Issue 71's finding — 8,163 dropped edges counted only into a transient integer — governs here. **Two failures,
two treatments:**

- A **malformed** entry (unknown axis, missing block, name↔identifier mismatch) **raises and aborts the
  ingest.** The file is hand-authored; a typo is a bug, not a gap.
- An entry whose identifier is well-formed but names a substance or class **drugref does not hold** is a
  genuine coverage gap in the identity spine, and registers as gap kind **`unresolved_onc_endpoint`** (the
  fifteenth kind — the CHECK currently lists fourteen). Its `gap_key` is **`ONCHIGH:<entry_id>:<identifier>`**
  — the entry's handle plus the endpoint identifier that failed to resolve, so two unresolved endpoints in one
  entry are two questions rather than one that flickers. **The format is frozen at mint time** like every other
  kind, because `question_uuid` is `uuid5(gap_kind, gap_key)` and a later reformat would orphan curator work.

**Ungraded ONC rules need no new view**: `gap_uncurated_interaction_rule` reads `class_contraindication` joined
to `ddi_candidate_pair` with no source filter, so ONC rules appear on the worklist the moment they are
projected, and leave it when graded. That the worklist works unchanged for a second authority is itself
evidence the §2.2 reading of the candidate tier is right — and a test asserts it rather than assuming it.

## 8. The read path — deliberately unchanged

No view is created or replaced. A consumer asking `curated_ddi_pair` for a pair receives graded advice from
both authorities in one result set, distinguished by the columns that already exist:

| | `candidate_source` | `upstream_release` |
|---|---|---|
| MED-RT-grounded | `MED-RT` | `2026.07.06` |
| ONC floor | `ONCHIGH` | the encoded list's release tag |

`reviewed_against` on an ONC judgement names **the ONC list version**, not the MED-RT release, because that is
the release the judgement was formed against and the thing that makes "is this ruling stale?" answerable.

`curated_target_unresolved` keeps its exact meaning: a live curated row whose candidate is no longer projected
is still an operator fault, and now correctly reports an ONC judgement left behind by an entry deleted from the
file — which is a fault worth seeing.

## 9. Signing — the first content with provenance worth attesting

Candidates are projection rows and are **not** signed; rebuilding them is routine and a signature over a
rebuildable row would attest nothing. **Judgements are signed**, with a curator-held Ed25519 key, through
5c.4's existing CLI. The slice ships:

- the operator procedure — `drugref keys register` → `drugref sign` → release manifest → `drugref verify` —
  documented as the way ONC content is published;
- one test driving an ONC judgement end to end with a **throwaway** test key: signed → `valid`, payload
  tampered → `altered`, entry removed from the manifest → `dropped`.

No production key is committed. The test follows 5c.4's non-committing pattern rather than widening the
carried test-isolation debt (issue 2's shape), and says so in a comment.

## 10. Rule 6, discharged in writing

The pairs are **facts**, and facts are not copyrightable. This slice therefore:

- re-encodes the *interactions* — no verbatim text from either paper, in any field;
- has **drugref author every `mechanism` and `management` string**, which is also why they are drugref's
  judgement rather than a quotation;
- cites the source paper per entry, so the provenance of a claim is inspectable;
- records the determination where the repository can find it: a `NOTICE` entry for ONCHigh and a **published
  decision record** under `docs-site/docs/decisions/`.

The determination is **the first task of the slice, not the last**, and it is written down this round precisely
because it currently exists only in a session memory file — which the next round cannot read.

## 11. What must be measured, and what must not move

`ddi_candidate_pair`'s **21,664** stops being one number and becomes per-source. Every count in the docs is
restated that way, and the tests assert the halves independently:

- **MED-RT's 21,664 has not moved**, and neither has `substance_moiety` 19,438. A per-source rebuild that
  disturbed another source's rows would break the invariant this slice leans on hardest.
- ONCHIGH's own candidate and pair counts stand alone, reported per entry and per resolved salt form.
- `gap_uncurated_interaction_rule` grows by exactly the ungraded ONC rules and shrinks to its MED-RT baseline
  as they are graded.
- `open_question` moves by exactly the new gap kind's rows.
- The `curated_ddi_pair` hot path is re-measured against 5c.4's ~1.4 ms with a populated ONC overlay.

## 12. What this slice deliberately does not do

- **It does not resolve the `spurious` deferral.** 5c.1 handed 5c.2 the question of how "drugref believes this
  upstream row is wrong" reaches a consumer, on the grounds that answering it needs content to say it about.
  This slice's content is `curated_interaction`, and `spurious` is a `curated_condition` ruling — so the
  deferral moves to the first slice that curates the 168 contradicted pairs, and ROADMAP must say so rather
  than leaving it attached to a slice that cannot discharge it.
- **It does not close issue 73's shape for the interaction views.** `ddi_candidate_pair` reading every source
  at once is *wanted* here — that is the point of §8 — but this slice is the first to make the behaviour
  observable, so the issue's text should be re-read against it.
- It does not add a second write path for ad-hoc curation (`drugref curate` for arbitrary rows), does not close
  issue 2, and does not gate any read on a signature.

## 13. Risks

| risk | treatment |
|---|---|
| The encoded content is clinically wrong | No row is committed before the file is reviewed and signed off; `reviewed_by` names the reviewing clinician, never the drafting agent |
| The papers' entries cannot be retrieved | Both are open-access on PMC and retrievable through this session's PubMed tooling; if an entry cannot be sourced it is **omitted**, never guessed |
| A MED-RT release later asserts the same rule | Intended and harmless: `source` is in the candidate PK so both coexist, and `curated_interaction`'s key omits `source` so one judgement still covers both |
| `CI_EPC` expansion is wider than expected | Measured before content lands: per-entry pair counts are reported by the orchestrator and reviewed against the paper's intent |
| Per-form expansion multiplies rows | Counted and reported in §11; all forms derive from one file entry, so they cannot disagree |

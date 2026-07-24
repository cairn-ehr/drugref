# Slice 5a — MED-RT mechanism/effect contraindications — IMPLEMENTATION PLAN

> **Status: forward plan, not yet built.** The **canonical design is the
> [slice-5a design spec](../specs/2026-07-25-drugref-slice-5a-medrt-contraindication-design.md)**; this
> file only orders the build into TDD-sized, independently-reviewable tasks. If the two disagree, the spec
> wins. As slice 2a proved, reading the real release can overturn assumptions — if a measurement here
> contradicts the spec, **stop and update the spec first**, then continue.

**Goal:** land drugref's first drug–drug interaction data — MED-RT `CI_MoA`/`CI_PE` ("contraindicated
mechanism/physiological-effect of a co-administered ingredient") — as a **rebuildable projection** in one
new table, populated from the MED-RT file slice 2a already parses. **No new external source, no new join,
no new UUID minting.** ≈739 class-level rules (462 `CI_MoA` + 277 `CI_PE`) measured in the 2026.07.06
release.

**Definition of done:** full suite green; a real-release ingest writes `class_contraindication` rows with
correct subject/object direction and provenance; `ddi_candidate_pair` expands them over the existing class
DAG; re-ingest is idempotent and leaves `class_membership`/`class_parent` intact; `unmatched_ci_rxcuis` is
reported, never silently dropped; `NOTICE` unchanged (no new source).

## Ground rules (from the cross-cutting ROADMAP section — do not relax)

- **TDD, failing-test-first.** Every task below is *write the test, watch it fail, implement, watch it
  pass*. Do not write implementation before its red test exists.
- **Integrity in the DB.** The table is a rebuildable projection (like `class_membership`) — CHECK/FK/PK in
  Postgres, no append-only floor on it (a floor would break re-ingest).
- **No silent drops.** A subject RxCUI not on a gated moiety is a counted worklist number, never discarded.
- **Licence scoping is structural, and here it is free:** both endpoints (`RxNorm` subject, `MED-RT`
  MoA/PE-class object) are already ingested drugref content, so no edge can introduce a new namespace.
- **Baseline first:** confirm the existing suite is green *before* touching anything (`pytest -q`), so a new
  red is unambiguously yours.

## Task graph (each task = one red→green→review cycle)

### Task 1 — Schema: `db/004_schema_interactions.sql`

**Red — `tests/test_schema_interactions.py`:**
- `drugref.class_contraindication` exists with columns `subject_moiety_uuid`, `object_class_uuid`,
  `relationship`, `source`, `ingest_run`, the composite PK, and FKs to `substance_moiety`,
  `substance_class`, `ingest_run`.
- CHECK `relationship IN ('CI_MoA','CI_PE')` rejects a third value; CHECK `source IN ('MED-RT')` rejects a
  third value.
- Index `class_contraindication_by_object` exists.
- View `drugref.ddi_candidate_pair` exists and selects the expected columns.
- **Idempotent replay:** running `apply_migrations` twice neither errors nor duplicates (mirror
  `test_class_registry_source_neutral.py`'s replay assertion).

**Green:** write `db/004_schema_interactions.sql` exactly as the spec §5.1/§5.3 (guarded `CREATE TABLE IF
NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `CREATE OR REPLACE VIEW`). No `ALTER`s to `db/002`/`db/003`.

**Review checkpoint:** schema shape only. Confirm no append-only trigger was attached (projection, not
floor) and the CHECK vocab matches the spec.

### Task 2 — Parser: emit `ContraindicationAssertion` from `medrt.py`

**Red — extend `tests/test_medrt_parser.py`:**
- A `CI_MoA` association (`from_ns=RxNorm`, `to_ns=MED-RT`, object an ingested MoA class) yields one
  `ContraindicationAssertion(rxcui=<from_code>, class_nui=<resolved nui>, relationship='CI_MoA')`.
- A `CI_PE` association likewise yields `relationship='CI_PE'`.
- **Direction is pinned:** subject is `from_code` (the drug), object is `to_code` (the co-administered
  drug's class). Assert against a *named real example* from the fixture so a silent inversion is caught (an
  inverted edge would read as a clinically opposite statement and pass a hand fixture).
- A `CI_MoA` whose object class is **not** ingested yields **no** assertion (endpoint scoping holds).
- `may_treat` / `CI_with` / `Parent Of` / `has_MoA` associations yield **no** `ContraindicationAssertion`
  (they stay out of this table).

**Green:** in `medrt.py` add `CI_RELATIONSHIPS = frozenset({"CI_MoA", "CI_PE"})`, a frozen
`ContraindicationAssertion(rxcui, class_nui, relationship)` dataclass, a `contraindications` field on
`ParsedMedrt`, and a branch in the association loop (guard `from_ns==RxNorm`, `to_ns==MED-RT`,
`to_code in nui_by_code`). This is the change [`medrt.py`:226](../../../src/drugref/ingest/medrt.py) already
anticipates — update that comment to say `CI_MoA`/`CI_PE` are now emitted (5a), the rest still deferred.

**Review checkpoint:** parser purity preserved (no DB/network); direction test is against real data.

### Task 3 — Writer: `src/drugref/interactions.py` (the only module writing the interaction table)

**Red — `tests/test_interactions.py` (DB-gated):**
- `add_contraindication(conn, subject_moiety_uuid, object_class_uuid, relationship, source, run_id)` inserts
  one row and returns a wrote/duplicate signal (mirror `classes.add_membership`); a second identical call
  does **not** duplicate (PK).
- `clear_source_contraindications(conn, source)` deletes only that source's rows.

**Green:** create `src/drugref/interactions.py` (sibling to `classes.py`, same single-writer role) with
those two helpers. Keep it the *only* module that writes `class_contraindication`.

**Review checkpoint:** single-writer discipline; delete scoped by source (rebuild correctness).

### Task 4 — Orchestrator: step 5 in `medrt_run.py` + `MedrtSummary` extension

**Red — extend `tests/test_medrt_run.py`:**
- After a run over the fixture, `class_contraindication` holds the expected rows; `MedrtSummary.contraindications` equals rows written.
- A `CI_MoA` subject RxCUI **not** in the registry writes no row and increments
  `MedrtSummary.unmatched_ci_rxcuis` (distinct RxCUI), never dropped silently.
- **Idempotent re-ingest:** second run rebuilds `class_contraindication` to the same state (not doubled),
  and `class_membership`/`class_parent` are **unchanged** by the contraindication clear (scope isolation).
- Shared provenance: the contraindication rows carry the **same `ingest_run`** as the run's classes/edges.

**Green:** in `ingest_medrt`, after membership (step 4) add **step 5**: reuse the already-built
`moieties_by_rxcui` (subject) and `uuid_by_nui` (object) maps; `clear_source_contraindications(conn,
SOURCE)` at the same rebuild point as the edge clear; insert via `interactions.add_contraindication`; tally
`contraindications` and `unmatched_ci_rxcuis`. Extend the `MedrtSummary` dataclass + its docstring with the
two new fields (document what each means, per the existing convention).

**Review checkpoint:** one file read / one `ingest_run` (no second parse); clear happens before any write;
summary fields documented.

### Task 5 — Pair expansion (integration test over the view)

**Red — in `tests/test_medrt_run.py` or a new `tests/test_ddi_pairs.py`:**
- Given a stored `X CI_MoA C` and a moiety `Y` with `has_MoA C`, `ddi_candidate_pair` returns `(X, Y, 'CI_MoA', C)`.
- The **self-pair is excluded** when the subject `X` is itself a member of `C`.
- A `CI_PE` row joins only `has_PE` members (the `CASE` mapping is correct, not cross-wired to `has_MoA`).

**Green:** already delivered by the view in `db/004` (Task 1) — this task is the *proof* it expands
correctly. If it fails, the bug is the view definition; fix it in `db/004`.

**Review checkpoint:** the relationship→membership mapping is exact; no accidental cartesian blow-up.

### Task 6 — Fixture: extend `tests/fixtures/make_medrt_subset.py` + regenerate

**Red — extend `tests/test_medrt_run.py`/fixture-shape test:** the fixture contains at least one `CI_MoA`
edge to a selected MoA class, one `CI_PE` edge to a selected PE class, and one `CI_MoA` edge to a
**deliberately unselected** class (proves scoping drops it). A shape test pins this CI edge set so a hand
regeneration cannot drift.

**Green:** extend the generator's selection to pull those edges from the real release; **regenerate the
committed fixture with the script** (never hand-edit it); the endpoints are RxNorm/MED-RT (already
licensed), so no redaction beyond slice 2a's existing rules. Verify counts against the real file
(462 `CI_MoA` / 277 `CI_PE`) so the subset is a faithful sample.

**Review checkpoint:** fixture regenerated by script, not by hand; shape test present.

### Task 7 — Integrate & finish

- Full suite green (`pytest -q`); ruff clean.
- `NOTICE` **unchanged** — confirm and state it (no new source; MED-RT already attributed).
- Update `docs/HANDOVER.md` (and tick ROADMAP Slice 5a → done once merged).
- **Final review** (`superpowers:requesting-code-review`) before PR: direction correctness, rebuild
  isolation, no-silent-drop, and the §7 clinical-safety posture (candidate tier — nothing here auto-alerts).

## Watch-outs carried from the design (spec §4, §7, §10)

- **Direction is the highest-risk bug.** subject = the drug the statement is about; object = the
  co-administered drug's class. An inversion is clinically opposite and silently plausible — Task 2 pins it
  against real data for exactly this reason.
- **This is a candidate tier, never an alert.** MED-RT does not track label updates (spec §4.3). Rows carry
  provenance and feed review/cross-check; they must not be rendered as high-intrusiveness alerts. Don't add
  a severity column (the source has none — spec tension C).
- **Don't overload `class_membership`.** Separate table by design (spec tension A); the shared shape is not
  shared meaning.
- **Rebuild, don't append.** `clear_source_contraindications` before writing; no append-only trigger on the
  table (spec §3).
- **`CI_ChemClass`/`CI_with`/`may_treat`/… are Slice 5b, not this.** They need MeSH descriptor ingest;
  admitting them here would reach an un-ingested namespace (spec tension E, §11).
```

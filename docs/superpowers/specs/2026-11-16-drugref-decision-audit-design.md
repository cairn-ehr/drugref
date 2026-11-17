# drugref decision audit: release stamps, upstream archive, ruling reach (#258)

**Status:** design, awaiting owner approval. **Issue:** #258 (also touches #256). **Date:** 2026-11-16.

## 1. Intent

drugref is decision support. The owner ruled (2026-11-16, on #258) that drugref must be able to show **what
information was available when a decision was made**. Class memberships change as upstream sources update, and
today `class_membership` / `class_parent` are delete-and-rebuild projections per source
(`classes.clear_source_edges`), so every re-ingest erases the state a curator or clinician acted on.

Two kinds of decision need reconstructing:

1. **A clinician's decision on a Cairn node**, made against whatever drugref data that node had loaded.
2. **A curator's ruling** on a class × class rule, made against the membership of that moment. #258's concrete
   defect lives here: drug X was in class A when A × B was graded; a re-ingest files X out of A and drops the
   candidate, orphaning the ruling; `drugref interactions X` no longer banners the orphan for X (exit 0).

## 2. Goals, non-goals, anti-goals

**Goals**
- G1. Any drugref answer a consumer may act on can be tied to a **release tag** that names the full data state.
- G2. For every release tag, drugref keeps the inputs needed to **rebuild that data state**: the upstream files of
  **every** ingest run in the database's history (by checksum), the curated rows and expansion policies at
  publication, and the drugref code version.
- G3. Every `curated_class_interaction` ruling records **which drugs it reached when it was written**, and the
  orphan banner uses that record, closing #258's under-warning. The `shortcut:` in `class_grain_read.py` goes.
  (Class-subject `curated_property_ruling` rows are audited through G1/G2 only: they have no orphan banner, so
  nothing reads a live snapshot of them. *Builder-check call.*)

**Non-goals**
- In-database membership history (diff/interval tables). Chosen against on resource grounds (§4). The owner noted
  that diff-based tracking is the fallback **if archive disk use becomes a problem**; not built now.
- An automated "rebuild release X into a fresh database" command. This round makes the rebuild *possible and
  checked*; the replay tool is a follow-up issue.
- Cairn-side stamping. drugref exposes the tag; cairn-ehr's own slice stores it. Do not edit that repo from here.
- Nodes that build their drugref data by running their own ingests, and a step to "adopt" a published tag by
  proving a match. Out of scope by the owner's distribution ruling (§5); a later round if ever needed.
- A backfill tool for runs that predate the archive (§6.2).
- #256 (a grade drops silently when a member leaves the class): the reach record makes it detectable later, but
  reporting it is not this round.

**Anti-goals (would make this a failure even if it works)**
- Any measurable cost on ordinary reads or ingests. The owner asked for the least resource- and
  performance-intensive option.
- A release tag that claims a data state drugref can no longer reconstruct (archive missing a file) without
  saying so.
- A tag reported for a database that has **drifted** since that release (re-ingested or re-curated after
  publication) as if it still were that release.

## 3. Constraints

- AGPL-3.0; no new dependencies expected (stdlib `hashlib`, `shutil`, `pathlib`).
- Migrations are ledger-immutable: new numbered file `db/080_*`; never edit `db/079` or earlier.
- Curated tables are append-only (supersession, never UPDATE/DELETE). The reach record is insert-only.
- Ingest parsers stay pure; only orchestrators write.
- `downloads/` is gitignored. The archive is **not** in the repo either.
- Files under ~500 lines: `curation.py` is at 483, so the reach write must not land there (see §6.3).

## 4. Approaches considered

| | Runtime / DB cost | Storage | Rebuilds |
|---|---|---|---|
| **A. Release tag + archived upstream files** | none on reads or ingest | heaviest: one copy per distinct upstream file, deduplicated by SHA-256 | the whole data state |
| B. In-DB membership + DAG history tables | ~1 s diff per class re-ingest; none on reads | small (changes only) | class membership/DAG only |
| C. Per-ruling reach snapshot | one insert per class ruling | tiny (0 class rulings today) | what each ruling reached |
| (rejected) keep every run's full `class_membership` copy | rename + repoint ~30 migrations' views | ~45k rows per load | membership only |
| (rejected) delete/insert audit triggers | ~90k events per re-ingest | large, noisy | by log replay |

**Chosen: A + C** (owner, 2026-11-16). A serves clinicians at zero runtime cost; C serves the one case that must
work *live* — the orphan banner cannot rebuild an old release on demand. B is dropped: it only speeds up a rebuild,
and audits are rare enough that a slow rebuild is acceptable. Owner's fallback if disk becomes an issue: diff-based
tracking (B-like), later.

## 5. What the owner decided

- The audit anchor for clinical decisions is a **release tag** stamped by the consumer on any record that may rest
  on drugref decision support. (The owner first said "migration ID"; a migration names only the schema, so two
  nodes at `db/079` can hold different data. `release_manifest.release_tag` (db/030) names the data state.)
- Reconstruction is by **archived upstream files**, not in-DB history.
- Class-grain rulings carry a **reach snapshot** for the banner.
- **Distribution:** a Cairn node gets its drugref data by **restoring a database dump that drugref publishes**,
  so `release_manifest` arrives with it and the node holds exactly the published bytes. "Drifted" then means the
  node changed something after the restore. A node's own local feeds (e.g. the PBS tier) do not count as drift.

## 6. Design

### 6.1 Which release is this database? (G1)

A read, `releases.current_release(conn) -> CurrentRelease | None`, returning:
- `release_tag`, `published_at` of the latest `release_manifest` row;
- `drifted: bool` plus the list of reasons, true when any of these happened after `published_at`:
  - an `ingest_run` finished for a **source the release carried** (the release's own run list, §6.2). Sources the
    release never carried, such as a node's PBS tier, are not drift. This is derived from the record, not a
    hard-coded source list. *Builder-check call.*
  - a row in a release-covered curated kind was reviewed;
  - a `class_expansion_policy` row was recorded (it changes `class_rule_member` and the answers);
  - the migration ledger head differs from the one the release was built at (a newer migration can rewrite a
    view).
  Cheap: max-timestamp and one ledger probe, no manifest verification.
  **Not drift** (*builder-check call*): signing-key status changes. They change a row's `signature_status`, which
  is already rendered per row, not the clinical content. The **running code's** version is also not checked: the
  database cannot see it.
- `None` when no release was ever published (dev/test databases).

**Where the build facts live in the database.** `db/080` adds an insert-only `drugref.release_build`
(`release_tag` FK → `release_manifest`, `git_commit`, `ledger_head`, `run_ids bigint[]`), written by publish beside
the manifest. `release_manifest` itself is immutable and stays unchanged. The drift probe reads `ledger_head` and
`run_ids` from here, because a restored node has the database but not the archive.
**Timestamps:** compare in the database's clock (`now()` at insert), not Python's, so the probe compares like
with like (*builder-check call*).

`drugref interactions` (and `drugref status`) print one header line: `drugref release: <tag>` /
`drugref release: <tag> (DRIFTED since publication: answers may differ from that release)` /
`drugref release: none published (answers are not tied to a reconstructable release)`. A drifted or absent release
does not change the exit code (the answer is still the database's answer); the line is the signal.

For a machine consumer reading the database directly, the same facts come from a view `drugref.current_release`
(one row or none) so Cairn can stamp without parsing CLI text.

### 6.2 The upstream archive (G2)

**Layout** (content-addressed, under a configured root, env `DRUGREF_ARCHIVE`):
```
<root>/blobs/sha256/<aa>/<full-hex>          one file per distinct upstream file
<root>/runs/<ingest_run.source_checksum>.json  the run's ordered file list
<root>/releases/<release_tag>.json             the release's runs + code version
```
- A **run record** lists, in `checksum()` order, each file's original name, size and SHA-256. Its own name is the
  run's `source_checksum`, so `ingest_run` already points at it.
- A **release record** lists **every finished `ingest_run` in the database, in order**: source, writer,
  ingest_run_id, upstream_release, source_checksum, finished_at. Not only `loaded_release`: the registry
  (`substance_moiety`, `identity_claim`, `moiety_admission`) accumulates across all runs, so replaying only the
  latest run per source would rebuild a different registry (*builder-check finding*). It also records the drugref
  **git commit** the release was built with and the migration ledger head. Code version matters: rebuilding with
  today's code can produce different projections.
- A **curated export** (`<root>/releases/<release_tag>.curated.json`) holds the full content of every live
  curated row the manifest enumerates, plus every `class_expansion_policy` row. The manifest stores only digests,
  and these are hand-made inputs no upstream file can supply. They are small. The export lets the archive survive
  loss of the publishing database. *Builder-check call.*

**Writing it.**
- Each ingest orchestrator already computes `checksum(*paths)` over its input tuple. After a run finishes
  successfully, an `archive.store_run(paths, source_checksum)` copies any blob not already present and writes the
  run record. No-op when `DRUGREF_ARCHIVE` is unset (dev, tests), so ingest cost is unchanged by default.
- `drugref release publish` writes the release record and curated export, and **refuses** to publish when the
  archive is configured and **any** run's record or blob is missing (exit non-zero, names what is missing). When
  the archive is not configured, publish proceeds but prints that the release is **not reconstructable**.
- **Runs from before the archive existed:** no backfill tool (*builder-check call*). The database keeps one
  combined checksum per run, with no file list, so a run record cannot be rebuilt from it. Production is built from
  nothing on current releases before go-live (HANDOVER's "Before production"), so its archive starts at run 1.
  Today's dev and measurement databases either publish unarchived, which warns, or are refused.
- **An archive write that fails after the ingest committed** leaves the run without a record. Publish then
  refuses, naming that run, so the gap is loud rather than silent. The fix is to re-ingest that source with the
  archive set. The ingest itself is not rolled back for an archive failure.
- Files are never deleted or overwritten by drugref (a blob is immutable by its name).

**Checking it.** `drugref archive verify [--release TAG]` re-hashes the blobs a release needs and reports missing
or corrupt files. This is the "possible and checked" part of the non-goal above.

### 6.3 Reach snapshot for class-grain rulings (G3)

**Schema** (`db/080`): `drugref.curated_class_interaction_reach`
(`curated_class_interaction_id` FK, `side` in ('subject','partner'), `moiety_uuid` FK), PK on all three,
insert-only trigger (reuse `forbid_any_rewrite`).

**Write.** In the same transaction as the ruling insert, copy `class_rule_member`'s rows for the new id into the
reach table (one `INSERT ... SELECT`). Called from `record_class_interaction_judgement`; the helper lives in a new
small module (curation.py is at 483 lines), e.g. `class_ruling_reach.py`. Rows of a superseded ruling are kept:
they are the audit record of what that ruling reached.

**Backfill.** `db/080` snapshots current `class_rule_member` for existing live rulings, marked as backfilled
(column `captured` = 'at_ruling' | 'backfill_db080'), because a backfilled row is membership at migration time, not
at ruling time, and the audit read must say so. (0 such rulings on every known database today.)

**Banner.** `class_grain_read.class_rules_reaching` returns a rule when an asked drug is in its reach **now**
(`class_rule_member`) **or was when ruled** (any reach row of this rule's natural key, including superseded
versions), plus the existing "either side reaches nobody" fallback. The `shortcut:` comment is removed.

**Audit read.** `drugref audit class-ruling <id>` (or a function the CLI wraps) prints, per side, the drugs reached
at ruling time vs now (added / removed), with `reviewed_at`, `reviewed_against`, and the ingest runs that were
loaded at `reviewed_at` (derived from `ingest_run`: latest finished run per (source, writer) before `reviewed_at`).
This also gives #256 its data.

## 7. Left to the builder

- Exact module split, function names and CLI wording; whether `audit` is a new command group or under `status`.
- Wiring `store_run` into each orchestrator (all writers with a `source_checksum`, not only the class feeds).
- How the git commit is obtained (`git rev-parse` at publish, or a build-time constant) and what happens in a
  non-git checkout (record `unknown`, publish warns).
- Test strategy (TDD): fixtures with a temp archive root; DB-gated tests for the reach table, banner and drift.
- Docs: PROJECT-NOTES (state, schema map), ROADMAP, a docs-site *Design decisions* record for "release tag as
  the audit anchor".

## Amendments (2026-11-16, review)

Recorded after the round's final review; the shipped code follows these, not the text above where they differ.

1. **Blob repair (§6.2).** A blob whose size is wrong is replaced on re-ingest by hash-verified bytes of its own
   name. "Files are never deleted or overwritten" applies to release files and to correct blobs. A same-size blob
   with wrong content is not repaired automatically; `drugref audit archive` reports it.
2. **No build record (§6.1).** A release in `release_manifest` without a `release_build` row (published before
   `db/081`) reports DRIFTED with the reason `no build record (published before db/081)`, not "unknown". "Unknown"
   is kept for a database that predates `db/081` and has no `current_release` view.
3. **Command name (§6.2).** `drugref archive verify [--release TAG]` shipped as `drugref audit archive [--release
   TAG]`, beside `drugref audit class-ruling <id>`. It also checks the curated export.
4. **Code-version warning (§6.2, §7).** Publish warns, besides an unset archive, when the git commit it recorded
   is `unknown` or ends in `-dirty`: the release is then not fully reconstructable.
5. **Migration numbers (§6.1).** `release_build` and `current_release` landed in `db/081`; `db/080` holds only
   `curated_class_interaction_reach`.

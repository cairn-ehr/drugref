# Release tag as the decision-audit anchor

**Status:** Active
**Last reviewed:** 2026-11-16
**Applies to:** the decision audit -- `db/080`, `db/081`, `drugref.current_release`, `DRUGREF_ARCHIVE`, `drugref audit`
**Full derivation:** the [decision-audit design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-11-16-drugref-decision-audit-design.md) (with its dated review amendments)

## Context

drugref is decision support, so it must be able to show **what information was available when a decision was
made** (owner ruling, 2026-11-16, issue #258). Two kinds of decision need reconstructing: a clinician's, made on
a Cairn node against whatever drugref data that node held, and a curator's ruling on a class x class rule, made
against the class membership of that moment.

Neither survives on its own. Class membership is a delete-and-rebuild projection per source, so every re-ingest
erases the state someone acted on. The concrete defect: drug X was in class A when A x B was graded; a re-ingest
filed X out of A and orphaned the ruling, and `drugref interactions X` no longer warned about it.

The owner also asked for the **least resource- and performance-intensive** option: no measurable cost on
ordinary reads or ingests.

## Decision

**1. The anchor is a release tag, not a migration id.** A consumer stamps the `release_manifest.release_tag` on
any record that may rest on drugref decision support. A migration id names only the schema: two nodes at the same
migration can hold different data. A release tag names the full data state. `drugref.current_release` (one row or
none) gives a machine consumer the tag and the reasons the database may have **drifted** from it (re-ingested,
re-curated, an expansion-policy change, a newer migration); `drugref interactions` and `drugref status` print the
same fact as their first line. Drift never changes an exit code: the answer is still the database's answer, and the
line is the signal.

**2. Reconstruction is by archived upstream files (A), plus a reach snapshot per class ruling (C).** With
`DRUGREF_ARCHIVE` set, every ingest keeps its input files, content-addressed by SHA-256 so a file shared by two
runs is stored once, and `drugref publish` writes a release record (every finished ingest run, the git
commit, the migration ledger head) and a curated export. Publish refuses when any run's files are missing, and
warns when the archive is unset or the code version is unknown or has uncommitted changes (`-dirty`). This costs nothing on reads or ingests; its price
is disk. Separately, each class ruling records the drugs it reached when written, because the orphan warning has to
work *live* and cannot rebuild an old release on demand.

In-database membership history (diff or interval tables) was **set aside**: it only speeds up a rebuild, audits
are rare enough that a slow rebuild is acceptable, and it would add work to every class re-ingest.

**3. Nodes restore a published dump.** A Cairn node gets its drugref data by restoring a database dump drugref
publishes, so the release record arrives with it and the node holds exactly the published bytes. "Drifted" then
means the node changed something after the restore. Drift counts only sources the release itself carried, so a
node's own local feeds (such as the PBS tier) are not drift *when the publishing database never ingested them*;
where it did, a node's own re-ingest of that source reads as `re-ingested` (#265).

**4. The fallback is diff-based tracking, if disk becomes the problem.** The owner's stated fallback, should the
archive's disk use grow too large, is to record membership changes per re-ingest instead of keeping every old
file (issue #260). Not built now.

## Consequences

- A release is reconstructable only if it was built with the archive set from its first ingest. A release record
  lists every finished run, so publish refuses on any run that predates the archive. Production must be built from
  nothing with `DRUGREF_ARCHIVE` set (an absolute path).
- Rebuilding is possible and checked (`drugref audit archive` re-hashes every file a release needs), but not
  automated: the replay command is a follow-up (issue #259).
- A class ruling recorded before `db/080` has only a snapshot taken when the migration ran, not when the curator
  ruled, and `drugref audit class-ruling` says so. No such rulings exist today.
- `drugref audit class-ruling <id>` shows which drugs a ruling reached then versus now, which is also the data for
  reporting a grade that silently dropped (issue #256).
- On a node that ingested PBS, the archive holds the encumbered PBS file. That is fine while the archive stays on
  the node; it must be decided before archives are shared (issue #263, under #25).
- Cairn stores the tag on its own records; drugref only exposes it.

## Related

- [Signing the curated overlay](signing-the-curated-overlay.md) -- `release_manifest`, the immutable record of
  what was published, which `release_build` sits beside
- [The hybrid store](hybrid-store.md) -- why projections are rebuildable and the curated tier is append-only
- Code: `src/drugref/archive.py`, `release_archive.py`, `release_state.py`, `class_ruling_reach.py`,
  `cli_audit.py`

# A reviewed grade never vanishes silently

**Status:** Active
**Last reviewed:** 2026-11-26 (GUI acknowledgement, #283)
**Applies to:** `drugref interactions`, `drugref audit departures`, `drugref curate acknowledge-departure`, the reviewer GUI's *Departed grades* view, `db/083`, `db/084`, `db/085`, `db/086`, `db/087`
**Full derivation:** the [ever-graded design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-11-24-drugref-ever-graded-design.md)

## Context

drugref is decision support. When a clinician has seen a reviewed grade for a drug pair (contraindicated, avoid),
a later answer must not drop that grade without saying so. A class ruling grades every pair its two classes expand
to, and class membership is rebuilt on every re-ingest, so a drug can leave a class, or an expansion-policy deny
can cut a subclass, and the grade quietly stops reaching the pair. The first fix (issue #256) compared against a
snapshot of the drugs a class ruling reached when it was written. That left three silent cases: a drug that left
before a correction (the correction takes a fresh snapshot), a drug that joined after the ruling and later left
(never in the snapshot), and the whole moiety grain (no snapshot at all; issue #279).

## Decision

**1. Record every pair a ruling ever graded, not a snapshot taken at one moment (owner ruling, issue #281).**
`db/083` keeps an append-only ledger with one row per *episode* in which a ruling graded a pair, for both the
class and the moiety grain. It is keyed on the ruling's natural key, not on its version, so a correction inherits
the history of the rule it corrects. A pair is a **departure** when the ledger holds an open episode the grading
view no longer produces while the ruling still stands, applies and has its candidate. The check is **per ruling**,
not per pair: if a class rule stops reaching a pair that a moiety rule still grades, the clinician who saw the
class rule's grade may now see only a weaker one, and is told.

**2. The ledger is refreshed by deferred database triggers, not by calls from Python.** A refresh runs at commit
after any insert of a ruling or an expansion-policy decision, and after any ingest run finishes. Rulings are also
written by the reviewer GUI's Rust service, and the release replay inserts them directly, so a rule that depended
on callers remembering to refresh would miss exactly the writers nobody thought of. A ruling changes grades only
for its own rule, so a ruling's commit refreshes just that rule (`db/084`): milliseconds, where a full read of the
grading view takes about 5 seconds on a full registry. An expansion-policy decision or a finished ingest can change
any rule, so those still pay the full read, once per commit (issue #285). Every writer of the ledger (both
refreshes and an acknowledgement) first takes one database lock, held until it commits (`db/086`, issue #287).
Without it, two commits at the same moment could each miss the other's work: a ruling saved during an ingest would
not see the ingest's new class members, and an acknowledgement racing an ingest that grades the pair again would
close the only episode. Each case left a pair graded with nothing to warn when it next departed. The lock makes
such commits take turns: a ruling saved while an ingest is committing can wait for that ingest's 5-second refresh.
Commits that change no ruling, expansion policy, ingest run or acknowledgement never wait. The lock refuses to
run outside the database's default isolation level (READ COMMITTED), where it could not work.

**3. A departure ends only when a curator acknowledges it.** The warning (`CHANGED SINCE REVIEW`, exit 3) repeats
on every answer until `drugref curate acknowledge-departure`, or the reviewer GUI's *Departed grades* view, records
who accepted the departure, when, against what and why. A warning a person cannot end teaches clinicians to ignore it. The acknowledgement covers that
*episode*: if the pair is graded again and departs again, the warning returns. The warning's hint names the drug
that actually left and says how many pairs acknowledging it closes, so a curator cannot silence departures they
were not shown. `drugref audit departures` lists every open departure for the operator, counts orphaned rulings
(whose candidate is gone, so their grade reaches nobody and they are not departures), and exits 3 when there are
any of either, so it can gate a post-ingest checklist. An orphan's warning stays the orphan banner, which also
names a drug the ruling was only ever observed grading.

**4. The rules live in the database, once, for every caller (owner ruling, issue #283).** What departed, which drug
left, and what an acknowledgement closes are SQL functions (`db/087`) that the command line and the reviewer GUI
both call, so the two cannot disagree. An acknowledgement waits at most 30 seconds for the lock in decision 2 and
then names who holds it, instead of hanging. The GUI does not ask the reviewer to type what they checked against: it
records the ingest release(s) that hold the ruling's classes' memberships at that moment (a class with no members
left names its source's latest finished release). The command line still takes that value from the curator.

**5. History before the migration is best effort, and says so.** Pairs the view graded at `db/083`, plus the pairs
in each class ruling's earlier reach snapshot, are backfilled and marked as recorded at the migration, not when
graded. The moiety grain has no earlier evidence; its history starts at `db/083`.

## Consequences

- The release export carries the ledger and the acknowledgements. A replay regenerates the observations and
  compares them as sets, both every (ruling, pair) observed and every one left with an unacknowledged episode;
  it loads the acknowledgements. A release that holds backfilled rows cannot reproduce them, so the replay skips
  the comparison and warns, which keeps it short of "reproduced". Production is built from nothing with the
  archive set, so a real release never has them.
- Acknowledgements are not signed yet (issue #284).
- The GUI's list is the unfiltered departure list, which reads the whole grading view (about 3 seconds on a full
  registry, the cost in decision 2).
- The earlier snapshot stays as the audit of what the curator saw when ruling (`drugref audit class-ruling`); the
  warning no longer reads it.
- A grade that *changed* (severity, mechanism) is not a departure; only a grade that stopped reaching a pair is.
- `drugref status` does not count departures: the warning reaches the person at risk, and `drugref audit
  departures` serves the operator.

## Related

- [Release tag as the decision-audit anchor](release-tag-as-the-audit-anchor.md) -- the audit this extends, and
  the reach snapshot it supersedes for the warning
- [Append-only claims](append-only-claims.md) -- why the ledger and its acknowledgements are never rewritten
- Code: `src/drugref/graded_pairs.py`, `cli_departures.py`, `db/083_graded_pair.sql`,
  `db/084_graded_pair_per_key.sql`, `db/086_graded_pair_lock.sql`, `db/087_graded_pair_sql_surface.sql`,
  `reviewer-service/src/departures.rs`, `reviewer-app/src/Departures.svelte`

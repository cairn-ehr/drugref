# HANDOVER — drugref

> **The volatile half: where we are right now.** Regenerated at the end of every working session
> (nextsession rule 9) and kept **under 130 lines**, so a rewrite costs nothing.
>
> **THIS LINE IS THE ONLY HOME FOR THAT NUMBER.** It was also written in `CLAUDE.md` (twice) and in the
> `nextsession` skill, all three said `~120` while this header said `~130`, and the file was 136 — a bound
> is a vocabulary like any other, and this repo has lost four rounds to one rule kept in two places. Both
> now say "the number its own header states" and point here. Change it here, alone.
>
> **The stable half is [`PROJECT-NOTES.md`](PROJECT-NOTES.md)** — traps, state by layer, how to run and test,
> the schema and code map, upstream errata, repo facts. It is edited in place and under no bound. **Put
> anything whose history is worth reading there, not here** (#63): this file's history is deliberately
> disposable, and content that lands here gets compressed away.
>
> Slice sequencing is [`ROADMAP.md`](ROADMAP.md); the canonical what/why is the specs under
> [`superpowers/specs/`](superpowers/specs/).

## ⇒ NEXT

**Merged to `main`** (ROADMAP orders them, full list there): everything through **slice 5c.1 — the curated overlay's assertion shape** (PR
[#77](https://github.com/cairn-ehr/drugref/pull/77), `db/029`, EMPTY as planned), plus the gates-that-do-not-fire round and the review of PR
#80. **`db/029` is MERGED and FROZEN** — corrections need a new `db/NNN`; `db/030` freezes the same way once this branch merges. Figures and
traps: PROJECT-NOTES § "Slice 5c.1".

**⇒ SLICE `5c.4`, SIGNING (`db/030`) IS MERGED** — PR [#84](https://github.com/cairn-ehr/drugref/pull/84), 2026-08-10; `main` is clean and
**`db/030` is now FROZEN** like `db/029`. Suite **969 → 1297, re-verified green on 2026-08-11** (`ruff check .` clean too). The overlay is signed at **two
layers**: curator-held Ed25519 keys over one row's canonical payload, an institutional key over a per-release **content manifest**
enumerating every live assertion — so verification is bidirectional and catches **omission** (`dropped`) as well as `added`/`altered`.
Signatures are **detached rows, not a column**, so any row can be signed later and counter-signing works. Revocation is **data, not
branches**: `rotated`/`retired` time-scoped, `compromised` blanket **and now permanent**. `cli.py` 508 → 379, split into `cli.py` +
`cli_chain.py`, then `cli_signing*.py`. Record: [signing the curated overlay](https://docs.drugref.org/decisions/signing-the-curated-overlay/).

**⇒ A FIVE-REVIEWER ROUND (PR [#84](https://github.com/cairn-ehr/drugref/pull/84)) FOUND FOUR THINGS FOUR EARLIER ROUNDS DID NOT — two
MEASURED, not argued. 1260 → 1297; `db/030`'s payload format and every committed vector UNCHANGED.** (1) **Deleting the release layer's
Ed25519 check outright left the suite GREEN** — that layer had no negative test at all. (2) **`drugref keys revoke --status active` undid a
`compromised` revocation**, returning the thief's signatures to `valid`: both readers used the LIVE row, while db/030 §3's own comment
justified the whole design on the status history being read — which nothing did. (3) **One planted `payload_context` denied verification of
a curated row FOREVER** (insert-only table, uncaught `KeyError`); it is a verdict now, and honest signatures beside it still report `valid`.
(4) **`release_manifest_entry` had no floor test** — the `dropped` finding's guard. Also: `verify` exited **0** on `unknown_key` (the
*cheaper* forgery); `generate_keypair`'s tuple let a transposed unpack write the PRIVATE key into `signing_key.public_key`, permanently;
`ManifestVerdict`'s findings were mutable inside a frozen dataclass. **Full account and every trap: PROJECT-NOTES § "Slice 5c.4"**, which
now leads with this round. Deferred: [#86](https://github.com/cairn-ehr/drugref/issues/86) (label vocabulary),
[#87](https://github.com/cairn-ehr/drugref/issues/87), [#88](https://github.com/cairn-ehr/drugref/issues/88).

**⇒ MEASURED on a fresh `drugref_5c4`** from the real releases (2026-08-10). **All five must-not-move counts held exactly** —
`ddi_candidate_pair` **21,664** · `substance_moiety` **19,438** · `open_question` **21,842** · `gap_uncurated_interaction_rule` **595** ·
`gap_uncurated_condition_contradiction` **168** — **taken BEFORE the end-to-end exercise, which left two curated rows, so that DB reads 593
today.** Hot path **~1.4 ms** populated, **re-measured after the revocation fix added a `NOT EXISTS` to the read view**: old view 1.337–1.371
ms vs new 1.309–1.455 ms on a `TEMPLATE drugref_5c4` clone — no regression. **Chain 132.96 s WITH the per-leg breakdown
[#81](https://github.com/cairn-ehr/drugref/issues/81) asked for** — `mesh-relations` 58.9 + `mesh` 41.8 = **75.7%**; `unii` 7.2 · `medrt`
9.8 · `gsrs` 15.0. **#81 answered on the breakdown, open on the variance** (127.5 → 144 → 133 s).

**⇒ THE MEASUREMENT DB IS `drugref_5c4`, NOT `drugref_5c1m`** — the latter is EMPTY despite two rounds of PROJECT-NOTES saying otherwise
(verified twice). `drugref_5c4` REPRODUCED the counts; it no longer holds them all. § "Repo facts" says which.

**⇒ NEXT SLICE: `5c.2` — the ONC high-priority DDI floor** (Phansalkar 2012 / Ayvaz 2015), the first curated content, signable as written.
**ROADMAP § 5c's execution-order callout has been CORRECTED and a reordering round must read it**: 5c.1 recorded the order as *hard and
irreversible* assuming a signature **column**; 5c.4 built detached signatures, so the irreversibility is gone — **good order, not a trap**.
Payload waiting: **168** contradicted pairs, **595** ungraded rules, on a CLEAN chain (`drugref_5c4` reads **593**) — re-measure, never
quote.

**⇒ Issue-tracker hygiene — sweep-closed-but-unfixed has happened FOUR times** (#31, #35, #40, #61). **Standing rule:** near
`close`/`fix`/`resolve` in any inflection, write the number WITHOUT a `#` ("issue 65"). Full account: PROJECT-NOTES.

## Open follow-ups (all filed as GitHub issues)

**Filed by slice 5c.4 and its final review** (detail in PROJECT-NOTES § "Slice 5c.4") — **issue 85** `signing_key_status_kind` has **no
append-only floor**, so one `UPDATE` disarms every compromise verdict (additive later). **Its parked note said "the two seeded vocabulary
tables" and named the wrong remedy — floor that one ALONE**: `signature_target_kind` is *designed* to move to a `/v2`, the migration the
read-back machinery exists for. Still carried, unfiled: `tests/test_cli_signing*.py` **cannot commit for real** — other modules assert
blanket unfiltered counts on shared tables, a test-isolation problem shaped like [#2](https://github.com/cairn-ehr/drugref/issues/2) ·
`db/030` is 568 lines vs `db/029`'s 579: precedent, not debt. **[#89](https://github.com/cairn-ehr/drugref/issues/89)** — `signing.py`
(582) and `release_verification.py` (532) breach rule 4; the seam is named in the issue, and it was deliberately not split inside a
security-fix diff.

**Filed by the last three rounds** — [#79](https://github.com/cairn-ehr/drugref/issues/79) **`tests/` is exempt from E501** (its title's 324
has drifted — re-measure, never quote; **debt, not policy** — delete the `pyproject.toml` block when 79 closes) ·
[#81](https://github.com/cairn-ehr/drugref/issues/81) per-leg breakdown now taken (above), variance remains ·
[#82](https://github.com/cairn-ehr/drugref/issues/82) **`drugref status` reports orphans to humans only** — it exits 0, so the rebuild
script that CAUSED them cannot see it; a CLI-contract call, not a cleanup · [#75](https://github.com/cairn-ehr/drugref/issues/75)
**`gap_uncurated_interaction_rule` costs ~2.7s**, inherited whole from `ddi_candidate_pair`'s unfiltered scan, not a new defect; no consumer
yet. (**74 and 76 closed by the gates round**, whose fix also covered the fifth Plan C index the parametrized test had never named.)

**Filed by the slice-3 design, its measurement, and the whole-branch review** — [#67](https://github.com/cairn-ehr/drugref/issues/67)
**salt↔base strength equivalence has no source** (409 *assay* specs, not conversion factors; MW covers 5.4%), routed to 5c ·
[#68](https://github.com/cairn-ehr/drugref/issues/68) **3,631 moieties carry a GSRS `ACTIVE MOIETY` edge to something else** (~19%;
unrepairable — immortal `moiety_uuid`, monotone gate; why issue 33 stays open) · [#69](https://github.com/cairn-ehr/drugref/issues/69) the
27-edge scope question · [#70](https://github.com/cairn-ehr/drugref/issues/70) **354 all-false composites reachable and queued by nothing**
· [#71](https://github.com/cairn-ehr/drugref/issues/71) **8,163 of 16,834 unregistered-component edges dropped, counted only transiently** ·
[#73](https://github.com/cairn-ehr/drugref/issues/73) **both views read every source at once** (`db/028` is immutable, so the next migration
there carries it).

**Filed by the policy-surface round** — [#65](https://github.com/cairn-ehr/drugref/issues/65) **no index serves a HISTORY query** on
`class_expansion_policy`; unfixed at 14 rows, revisit at curation. (**66 is closed by the gates round**, which found the gap wider than the
issue said: ruff was not a dependency and CI never linted.) **Owned by 5c** (5c.1's design round routed all three here, unanswered) —
[#51](https://github.com/cairn-ehr/drugref/issues/51) **the 168 contradicted pairs**: 5c.1 gives them a queue and a home for the ruling
(`curated_condition`); answering them is 5c.2+ · [#52](https://github.com/cairn-ehr/drugref/issues/52) **the 422 broadened assertions**: no
`concept_ui` on the row · [#55](https://github.com/cairn-ehr/drugref/issues/55) **`indications_for_condition` generalises through a
boolean**.

**Filed by the interaction debt round** — [#48](https://github.com/cairn-ehr/drugref/issues/48) **a non-expanding predicate with no direct
member is equally dead and is deliberately not reported**; unreachable until a *class-side* predicate stops expanding. **Retired by the five
debt rounds** (#50, #39, #31, #45 · #40, #17, #42, #41, #43 · #16, #47 · #35 · #59, #60, #61, #63), each verified against the code first;
their **standing rules** are in [`PROJECT-NOTES.md`](PROJECT-NOTES.md).

**Floor, identity and ingest correctness** — [#2](https://github.com/cairn-ehr/drugref/issues/2) **floor hardening** (the `TRUNCATE` +
owner-role bypass; blocked on test isolation — **13** `TRUNCATE`-ing modules depend on it, re-grep before quoting); **5c.4 does NOT close it
and makes it more visible** — a superuser dropping a trigger is now the way to remove a signature ·
[#3](https://github.com/cairn-ehr/drugref/issues/3) **UNII-change immortality** · [#33](https://github.com/cairn-ehr/drugref/issues/33)
**MeSH CAS keys name specific forms** — slice 3 does **not** settle it, its own proposed fix is refuted, blocked behind **#68** ·
[#30](https://github.com/cairn-ehr/drugref/issues/30) (`strip_salt`) **unmeasured** for slice 3 ·
[#5](https://github.com/cairn-ehr/drugref/issues/5) INN from UNII's `Display Name` ·
[#7](https://github.com/cairn-ehr/drugref/issues/7)/[#29](https://github.com/cairn-ehr/drugref/issues/29) **row-at-a-time ingest** (MED-RT
~31k, PBS ~28k round trips) — **now quantified: the two MeSH legs are 75.7% of a 133 s chain.**

**Interaction model** — [#19](https://github.com/cairn-ehr/drugref/issues/19) **CI rules whose object class is unpopulated**, filed as 41 of
739 but `gap_unpopulated_contraindication` returns **13**, so **re-measure before acting on the issue text** ·
[#20](https://github.com/cairn-ehr/drugref/issues/20) **n-ary interactions**, Plan C's `interaction_group` is the shape ·
[#8](https://github.com/cairn-ehr/drugref/issues/8) **class-level `has_*` assertions unused** ·
[#36](https://github.com/cairn-ehr/drugref/issues/36) **discovery counts descendant classes, not reachable members** ·
[#37](https://github.com/cairn-ehr/drugref/issues/37) **the DAG is expanded unprunably on every query** — restricting the *root set* is
safe, restricting the *walk* deletes the coagulation case. **Before the first production load**: every parser re-run against a current
release, the `add_claim` canonicalisation check from #17, and **three** rule-6 deeds (#6, #25, GSRS's *"unless otherwise noted"*) —
PROJECT-NOTES § "Verify".

## Current DSN

- **The one home for this value.** Dev DSN (Postgres.app, PG18): `host=localhost port=5532 dbname=drugref_test user=postgres`. Set it
  as `DRUGREF_TEST_DSN` for the DB-gated tests. **Which verification database to read, what it holds and what it does not, is stated
  once in `PROJECT-NOTES.md` § Repo facts** — read **`drugref_5c4`**. Not restated here: this file is compressed every session, so a
  second copy would outlive the first and disagree with it.

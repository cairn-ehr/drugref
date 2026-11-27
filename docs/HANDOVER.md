# HANDOVER — drugref

> **The volatile half: where we are right now.** Regenerated at the end of every working session
> (nextsession rule 9) and kept **under 130 lines**, so a rewrite costs nothing.
>
> **THIS LINE IS THE ONLY HOME FOR THAT NUMBER.** Do not restate it in `CLAUDE.md`, a skill, or elsewhere.
> A bound is a vocabulary like any other, and this repo has repeatedly lost rounds to one rule kept in two
> places.
>
> **The stable half is [`PROJECT-NOTES.md`](PROJECT-NOTES.md)** — traps, state by layer, commands, schema and
> code map. Edited in place, under no bound. Slice sequencing is [`ROADMAP.md`](ROADMAP.md); canonical what/why
> is in the immutable specs under [`superpowers/specs/`](superpowers/specs/).

## ⇒ NEXT

**Branch `claude/lab-effects-model-294`: #294 step 2 of 4 (the model); schema ends at `db/092`.** The suite total
lives in PROJECT-NOTES § "How to run / test" **and nowhere else**; `uv run pytest` fails if they disagree.

**⇒ NEXT: #294 step 3, the first openFDA label ingest** (PROJECT-NOTES § "Lab effects: the model"; the
[design](superpowers/specs/2026-11-27-drugref-lab-effects-design.md)). Fill the empty `SPL_LAB` tables (`db/091`):
a pure term + role reader beside `spl_advice.py` (pin its terms to `lab_effect_term`), orchestrator `spl_lab_run`
(+ its replay adapter and source-clear order: both exempted in tests until then), the test-set comparison, and a
precision sample scoring **role and direction separately**. First check `spl_match` / `spl_advice_scope` on
sections other than 34073-7. Also owed: the subject `resolution` vocabulary, quotes with their own 25% budget
trigger (never `drug_interactions`), a combination-label rule. **Step 4:** Rust must snapshot class reach and refuse
a class reaching no drug (Python-only today). **Owner ruled 2026-11-28 (spec §13)** on which readings a ruling hides;
**open:** only when it cites the pair's question (§12)? **Next:** #302, #301, #299, #293, #295/#298, #297, #292.

**⇒ OWNER, from #270:** profiles put reviewers' names, qualifications and bios into the archive. Fine while
archives are node-local; decide before they are shared (with #263). **#275**: the question bookkeeping and
accumulation tables are still not exported. No writer outside their own modules yet; they name `ingest_run` ids
the replay renumbers, and the coverage guard will NOT fire for their first writer. Decide with that writer.

**⇒ STANDING (#258):** `DRUGREF_ARCHIVE` (absolute path; unset = no archive I/O, and publish warns the release is NOT
reconstructable). **Publish refuses on any run ingested before the archive was set**, so production must be built
from nothing with it set. **No named database carries `db/079`–`db/092` yet** (only `drugref_test`). No `ruff
format` here (hand style). **Never VALIDATE `db/078`'s `NOT VALID` CHECK.** **No `-p no:cacheprovider` on a full run.**

**⇒ NEW, owner:** **#263** (under #25): an archive on a PBS-ingesting database holds the encumbered PBS file and its
release record lists the PBS run; fine node-local, decide before archives are shared. **#244** (brand → composition:
*Augmentin* finds nothing, so its amoxicillin is never allergy-checked): owner design pass; SPL (public domain, on
disk) has brand names + ingredient UNIIs, not Australian-only brands. **Still owed (#231/#232):** the exclusion
list applies to search (*glucose* does not find dextrose); from #227: glance at the exclusions and the 12 shadowed
names.

**⇒ CARRY TO cairn-ehr's allergy slice (do not edit that repo from here):** `no-match` is NOT "safe" for
cross-reactivity: case (c), cefalexin for a penicillin allergy, answers `no-match` (no cross-reactivity rule yet,
for any beta-lactam). `unplaced` means "not checked" (30% of prescribable moieties). The group is
`structural-seed`, not clinically reviewed, and the read says so.

## ⇒ DO THIS NEXT

0. **Allergy groups (owner):** (a) who reviews the allow-list so `structural-seed` can come off; (b) when does
   case (c) come, and at what grain (R1 side-chain groups vs blanket); (c) **#223**: the registry's `RXNORM_IN`
   lacks MED-RT's RxCUIs for ampicillin / amoxicillin / nafcillin sodium / Penicillin, which blocks any MED-RT
   `has_SC` join.
1. **Owner's clinical read of the class-mentions round's precision sample** (results record, "Precision sample" and "Gates").
   Trade-offs to decide:
   - Sentence-scope avoid / monitor are withheld by EXACT agreement. Most disagreements were the machine reading
     STRONGER than the label (the safe direction). Should the gate count safe-direction disagreement, and
     re-admit those levels?
   - Contraindicated reads stronger than the label in 22 of 100 (safe direction; zero read lower). Acceptable?
   - **#220**: withheld readings are stored as `not_read`, so no curator ever sees them. Store the level, and
     put withheld strength readings on the worklist? (Also decide **#221** and **#222**.)
   - The reader's two conventions: "Reserve concomitant prescribing ..." reads `avoid` (4,638 spans);
     "separate administration" reads `adjust_dose`. Keep, or change?
   - Wilson lower bounds are below 90% at n = 50 (results record gate table): is a larger sample wanted?
2. The still-owed **clinical read of the class-profiles results §5** (beta blockers, ACE inhibitors, SSRIs);
   **then** write its docs-site *Design decisions* record (living decisions only).
3. **Owner design call on #204**: interaction rulings never reach the prescribing-alert path. **Curators must
   not work `interacts_with` questions until it is decided.**
4. **Owner design calls on #203** (MED-RT "Unknown ..." catch-all classes; near-universal properties such as
   `CI_with Drug Hypersensitivity`). Whatever is chosen, "not confirmed" stays "not confirmed".
5. **Owner questions**: should an ASSERTED property passing down to a subclass honour a `class_expansion_policy`
   deny (today not: db/060, db/065)? May a `curated_interaction` cite the gap-19 `interacts_with class` question
   about the same pair (today the guard says no; from #207)?
6. **Owner design call on #239** (625 `INN` claims are not the INN): rename to UNII's `[INN]` name, and what rule
   for the 74 that collide with another moiety's display name and the 99 hydrate forms? A rename ripples into
   onchigh's name check, the FDA-CYP / SPL name keys, the `moiety_official_name` snapshot and Cairn's search.
   Likely also a PBS-bridge recall gap (Australian names follow the INN; not measured).
   **Owner design calls from #214:** **#245** (turn the dropped "non-X" spans into statements about the complement
   group: which parent class to subtract from) and **#246** (a class word continuing into a differently spelled
   longer class, e.g. "catecholamine-depleting drugs" on `Catecholamine [EPC]`: no mechanical rule found).
   Both likely need hand evaluation. **#214 stays open** for "drugs with X effects" (dropped by R19's head rule; a
   with/having exemption) and "consider alternative therapy" as an avoid phrasing: both move figures, owner call.
7. **#155** (PK-axis classes): this round answers it by exclusion (the MED-RT PK and APC axes are never matched).
   Owner to close it or say what remains.
8. Still open on their own triggers: #164, #169 (SPL); `5c.5` pregnancy/lactation is gated on a clinician review
   that has not happened. From #258: **#265** (drift scope: needs a node-local source list, owner input); **#275**
   waits for a writer.

## Open follow-ups

Full ledger: [PROJECT-NOTES § "The standing open-issue ledger"](PROJECT-NOTES.md). **Open: #285, #292, #293, #295, #297–#299,
#301, #302;** **open from #258: #260, #263, #265** (diff fallback, PBS in the archive, drift scope); #224 partly done.
**#244.** **#214 open; filed #245, #246.** **Open from allergy groups: #223, #224.** **Open from class profiles: #203,
#204. From class mentions: #220–#222.** FDA-CYP names: **#239**. Open: **#164**, **#169**. Standing: **#155**,
**#102**; **#67** (salt↔base) is wanted by four sources now (lab effects too) and blocks a grain; **#158** untouched.
Also #148, #152, #128/#129, #124, #94. Before production: re-run every parser on current releases **with
`DRUGREF_ARCHIVE` set**, resolve #17, the three rule-6 deeds (#6, #25, GSRS).

## Current DSN

- Test-only DSN: `host=localhost port=5532 dbname=drugref_test user=postgres` (`DRUGREF_TEST_DSN`; never for reviewer
  accounts or GUI data). Pytest uses a per-session copy (#153): concurrent sessions are safe; the role needs CREATEDB.
- **`drugref_excl248`** (#248/#249): `createdb -T drugref_hygiene214` at `db/078`, no ingest; never patch it.
- **`drugref_hygiene214` is #214's SPL database**: `createdb -T drugref_ambig132`, `db/077`, **SPL run 19
  is the shipped measurement** (run 18: refused by the old class floor, unfinished, nothing written). Never patch
  it. **`drugref_names243`** (#243) had its official names truncated on purpose: never use it as a template.
- **The FDA-CYP / names chain** (each `createdb -T` the previous; never patch any): `drugref_usnames` (#213, SPL
  run 12 shipped) → `drugref_names` (#232) → `drugref_fdacyp226` → `drugref_role134` → `drugref_ambig132` (#132/#135,
  `db/075`, run 19's baseline);
  `drugref_synonyms` (#231) branches from `drugref_names`. Details: PROJECT-NOTES § "How to run / test". Create a
  test DB with `createdb` first (pytest connects there and creates its session copy beside it). Other named
  databases lack db/072–077 until migrated; their pre-072 census rows then read NULL (not measured).
- **`drugref_allergy` is the allergy round's** — `createdb -T drugref_profiles`, migrated to `db/069`, allergy groups ingested
  (MeSH 2026); never patch it. Its test DB: `drugref_test_allergy`. `drugref_profiles` untouched at `db/065`.
- **`drugref_classmentions` is the previous round's** — built from nothing at `db/066` (chain + `onchigh` + `fda-cyp` +
  `drugcentral` + `ingest spl`). **SPL run 17 is the shipped measurement**; it also carries an aborted run 9 and
  superseded runs 10-16. **Never patch it**; census: `uv run python -m tools.spl_class_census --dsn ... --openfda downloads/OPENFDA`.
- **`drugref_profiles` is the class-profiles round's** — built from nothing at `db/061` (chain + `onchigh` +
  `fda-cyp` + `drugcentral`, no SPL), migrated to `db/065`, `curated_property_ruling` empty. **Its builds and figures are in the
  [results record](superpowers/specs/2026-11-07-drugref-class-profiles-results.md)**; re-measure #203 there (#202's figures: its §6 `db/065` addendum).
- **The older verification databases** (`drugref176`, `drugref_notice174`, `drugref_dur159*`, `drugref_spl*`,
  `drugref_dc049`/`drugref_dc101`) and their caveats moved verbatim to PROJECT-NOTES § "How to run / test". All
  predate `db/054`; `drugref176` REFUSES to migrate. **Never patch one — rebuild under a new name.**
- Corpora: `downloads/OPENFDA/` (14 partitions, 2026-08-22), `downloads/DAILYMED/` (6 Human Rx parts,
  2026-08-21, 17.6 GB). **`downloads/` is gitignored, so every SHA-256 is in the mining record §2** — verify
  against that table, not a manifest that vanishes with the bytes.

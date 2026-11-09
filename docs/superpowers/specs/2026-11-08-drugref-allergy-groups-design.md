# drugref — allergy groups: "is this prescribed drug in the same group as the recorded allergy?"

**Design spec, 2026-11-08.** Unblocks the allergy slice in cairn-ehr. Marks: **(owner)** = decided by the
owner in the 2026-11-08 brainstorm; **(designer's call)** = settled by the designer, open to the owner's
correction; everything else is left to the builder (§8).

## 1. Intent

cairn-ehr is about to build "record an allergy" and warn a prescriber against it. The owner's three
acceptance cases (owner):

| recorded allergy | prescribed | expected |
|---|---|---|
| penicillin | perindopril | no warning |
| penicillin | amoxicillin | warning |
| penicillin | cefalexin | cross-allergy warning |

cairn records an allergy **preferably as a drugref code, but must allow free text** (owner). When the
allergy is a **single drug** ("rash to amoxicillin") and the prescription is a different drug in the same
group (flucloxacillin), cairn **still warns, stating explicitly that the recorded allergy was to amoxicillin
and the reaction was a rash** (owner). So drugref has to answer: *does the prescribed drug fall under the
recorded allergen, and by what path?* cairn owns the reaction, the wording and the free-text case.

**Why drugref can't do this today** (measured 2026-11-08 on `drugref_profiles`):

- The only penicillin group drugref holds is MED-RT's `Penicillin-class Antibacterial [EPC]` (N0000175497):
  **8 direct members**. Ampicillin, flucloxacillin, cloxacillin and the moiety drugref calls `amoxicillin`
  (UNII `804826J2HU`, the trihydrate) are **not** members; only `amoxicillin anhydrous` (`9EM05410Q9`) is.
- MED-RT's `has_SC` (drug → MeSH chemical family, deferred since slice 2b) points at the right family
  (`Penicillins`) but covers only US-marketed drugs: **no flucloxacillin, no cloxacillin**.
- The **MeSH descriptor tree** has them all. Under `Penicillins` (D010406) sit 29 descriptors, including
  Floxacillin D005436, Cloxacillin D003023, Amoxicillin D000658 and Ampicillin D000667, and each descriptor's
  `<RegistryNumber>` carries the UNII drugref's moiety holds (Floxacillin `43B2M34G2V` = drugref's
  `flucloxacillin`; Amoxicillin lists **both** `804826J2HU` and `9EM05410Q9`). drugref already parses
  `desc2026` and already bridges descriptors to moieties by UNII/CAS (`ingest/mesh.py`, `registry_keys`,
  `tree_parent_edges`), but only for Pharmacological Action classes. No MeSH *chemical* class is registered.

## 2. Goals, non-goals, anti-goals

**Goals (this round)**

1. **A complete penicillin allergy group**, seeded from the MeSH descriptor tree under `Penicillins`
   (owner: "completion of the penicillin group via MeSH would probably already suffice for testing").
2. **A read that answers the question in §1**, for an allergen given as either a drugref **moiety** or a
   drugref **allergy-group class**, and returns the **path** (recorded allergen → group → prescribed drug).
   This is the path cairn needs for "the recorded allergy was to amoxicillin".
3. **Salt, ester and hydrate forms fold to all their component moieties** before matching, through the
   composition tree drugref already holds (`substance_composition`, slice 3; every component, not just the
   "active" ones; owner, §4.3). Prescribing `ampicillin sodium` matches the way `ampicillin` does.
4. **Measured before it ships**, as every slice here is (§6).

**Non-goals (this round)**

- **Case (c), the cefalexin cross-allergy warning.** No open source states "penicillin-allergic → caution with
  cephalosporins"; it needs a curated, cited cross-reactivity rule. The clinical subtlety: most of the
  cross-reactivity risk tracks a **shared R1 side chain** (cefalexin and cefaclor share amoxicillin's and
  ampicillin's; cefazolin shares none). That means a later round may want side-chain groups, not a blanket
  penicillin↔cephalosporin rule. (designer's call: the owner said the MeSH completion suffices for testing.)
- Any allergy group beyond penicillins. The mechanism takes more roots (§4.1), but this round lists one.
- MED-RT `has_SC` ingestion. It is used here only as a **recall cross-check** (§6), not as a membership source
  (designer's call: the tree route is a strict superset for penicillins and needs no MeSH concept→descriptor
  resolution).
- Free-text allergies, reaction type, severity, and the warning's wording. These are cairn's job. drugref
  offers no free-text→code matcher this round.
- Products and combinations (amoxicillin/clavulanate as a product). cairn codes medications at the moiety
  (cairn ADR-0059), and a combination is cairn's job to split into its moieties before asking.
- Any change to interaction/contraindication expansion, class profiles, or the 96 withheld `CI_ChemClass`
  class-arm rules.

**Anti-goals (would make this a failure even if it worked)**

- **The sulfa trap.** Using MeSH's *structural* tree as an allergy population, unreviewed, is what makes
  "sulfonamide allergy" reach thiazides and bosentan. drugref already withheld 96 rules over exactly this
  (`docs-site/docs/decisions/withheld-chemical-class-contraindications.md`). Therefore **nothing is an allergy
  group unless it is named in a reviewed allow-list** (§4.1), and the group's ancestors (β-Lactams, Lactams,
  Amides…) are **never** groups by inheritance.
- **Waking the latent source-blind walk.** `class_parent` carries no `source`, and existing transitive walks
  (`ci_class_subtree`, class profiles, gap views) would start crossing into whatever MeSH chemical classes are
  registered. This round must not change any existing reader's output. The test in §6 pins that.
- **A silent "no".** A "not in the group" answer for a drug drugref could not place must not read the same as
  a checked negative (§4.3).
- **Presenting the group as reviewed clinical content.** It is a structural seed for testing, and the read
  says so (§4.3).

## 3. Constraints

- AGPL-3.0 compatibility: MeSH is already verified and attributed in `NOTICE`. No new source.
- Architecture invariants (CLAUDE.md): parsers pure/streaming; the orchestrator owns the transaction and is the
  only writer; per-source rebuilds are delete-and-rebuild. **One source with two writers has already caused
  clobbering (#39); `class_parent`/`class_membership` are cleared per `source` (`classes.clear_source_edges`),
  and `mesh_run` clears source `'MeSH'`.** Whatever writes these groups must not be wiped by, or wipe, the PA
  rebuild.
- Migrations: new numbered file only; never edit an applied one.
- `class_uuid` for a group must be as stable as any other registered class (cairn will store it in an allergy
  record, permanently).

## 4. The design

### 4.1 The allow-list (designer's call)

A reviewed data file in `src/drugref/data/` (like `onc_high_priority.toml`) names each allergy group by its
**MeSH DescriptorUI root** and lists **excluded descendants** with a reason. This round's content:

- root `D010406` Penicillins
- excluded: `D013407` Sulbactam, `D000078142` Tazobactam (β-lactamase inhibitors, filed under Penicillins
  structurally; not penicillin-allergy triggers on their own), `D010397` Penicillanic Acid (not a drug).
  Combination descriptors (`D019980` Amoxicillin-Potassium Clavulanate, `D000077725` Piperacillin/Tazobactam)
  carry no UNII of their own that maps to a single moiety. The builder confirms what they bridge to, and
  excludes them if they bridge to the inhibitor.

The file records who reviewed it and against what, and is marked **"structural seed — not clinically
reviewed"** until it is.

### 4.2 Membership

A moiety is a **direct member** of a group if a MeSH descriptor **at or below the root** in the tree (any of
the root's tree numbers), not excluded, carries a `<RegistryNumber>` UNII (CAS as the existing fallback)
that bridges to that moiety by the existing two-key rule. Membership is materialised at ingest, with the
group registered as a class (new `concept_type`, e.g. `'CS'`, chemical-structure group) so that it has a
stable `class_uuid` cairn can store.

**Where it is stored is the builder's call, under one hard rule: no edge this round writes may be visible to
an existing class walk** (§2 anti-goal). The two shapes considered:

- register in `substance_class` + `class_membership` with **no `class_parent` edges at all** (a group is flat:
  its membership is already the whole subtree, computed from tree numbers at ingest), and confirm no
  existing reader picks up a `'CS'` class; or
- a dedicated membership table read only by the allergy read.

### 4.3 The read

One pure-ish read in a small new module (e.g. `src/drugref/allergy_read.py`), plus a CLI command, plus (for
cairn, which co-resides in the same Postgres) a SQL surface it can call. The builder picks view vs function.

Input: `prescribed` (a `moiety_uuid`) and `allergen` (a `moiety_uuid` **or** a group `class_uuid`).

1. **Fold** each moiety to **every** component moiety it has in `substance_composition`, whatever
   `is_active_component` says (TRUE, FALSE or NULL), and keep the moiety itself in the set too (owner,
   2026-11-09). GSRS's "active" means *pharmacologically* active, which is not what triggers an allergy:
   `clemizole penicillin` marks penicillin G FALSE, and `benethamine penicillin` leaves it NULL. An
   active-only fold would answer a penicillin allergen with a silent `no-match`. The trade is accepted on
   purpose: the partner of a salt or ester also matches (penicillin G procaine warns a procaine-allergic
   patient, which is clinically right; a benzathine-recorded allergy warns on penicillin G benzathine). The
   returned path **names the component that matched**, so cairn can say "via its procaine component". The
   existing `moiety_active_in_composite` view (db/028, active-only) is therefore **not** the fold. This read
   has its own predicate, and the module's docstring says why.
2. Allergen is a **group**: match if any folded prescribed moiety is a member.
3. Allergen is a **moiety**: match if (a) folded allergen and folded prescribed share a moiety (**same drug**),
   or (b) they share a group (**same group**). Return each matching group.
4. Return, per match: the kind (`same-drug` | `same-group`), the allergen as recorded, the folded moieties on
   both sides, the group (name, MeSH UI, `class_uuid`), and the group's review status from §4.1.
5. **Three outcomes, never two**: `match`; `no-match`; `unplaced`. The last must be distinguishable so cairn can
   show "not checked". **"Placed" means the folded moiety bridges to at least one MeSH descriptor anywhere in
   the tree** (designer's call). A drug with a MeSH descriptor outside the group's subtree is genuinely not
   in the group. A drug with no descriptor bridge at all could be a penicillin MeSH never filed, so it answers
   `unplaced`, never `no-match`. That needs a placement set covering every bridgeable moiety in `desc2026`,
   not just the subtree. It's cheap, because `desc2026` is already read whole. So:
   - `no-match`: both sides placed, nothing shared.
   - `unplaced`: an input UUID drugref does not hold; a prescribed side with no MeSH bridge; or an allergen
     moiety that is in no group (only `same-drug` could be checked; a `same-drug` hit still answers `match`).
   Perindopril bridges to MeSH `Perindopril` (the builder confirms), so case (a) answers `no-match`.

Perindopril against a penicillin allergen → `no-match`. Amoxicillin (either moiety) or flucloxacillin against
a penicillin allergen, or against an amoxicillin allergen → `match`, `same-group` via Penicillins.

## 5. What cairn gets

A stable `class_uuid` for "Penicillins (allergy group)" to store as a coded allergy, and the read in §4.3,
with paths to word its warning. Case (c) answers `no-match` this round; cairn must not treat that as
"cephalosporins are safe". This goes in the read's documentation and in cairn's slice notes.

## 6. Measurement and tests (before it ships)

- The owner's three cases and the amoxicillin-allergen → flucloxacillin case as tests (case c pinned as
  `no-match`, documented as a known gap). Plus fold cases: `clemizole penicillin` and `benethamine
  penicillin` against a penicillin allergen → `match` (via penicillin G); `ampicillin sodium` → `match`; a
  procaine allergen against `penicillin G procaine` → `match`, `same-drug` via the procaine component.
- **Recall cross-check against MED-RT `has_SC`**: every RxNorm ingredient MED-RT files under `Penicillins`
  (≈30 in the 2026.07.06 release) should reach the group via the tree route after folding. **This is a
  measurement, not a gate** (designer's call: the owner set the bar at "suffices for testing"). Each miss is
  reported by name and fixed by an explicit `include` entry in the allow-list (moiety UNII + reason, reviewed
  like the exclusions), or documented as a known miss in the read's documentation if it can't be bridged.
- Count of members, and every `-cillin` moiety (60 on `drugref_profiles`) not in the group, listed with the
  reason (no MeSH descriptor / no bridge key / excluded).
- **No existing reader's output changes**: class profiles, `ci_class_subtree`, gap views, class-grain counts
  are byte-identical before and after on a rebuilt database.
- A rebuild of MeSH PA and of the allergy groups, in either order, leaves both intact (#39's lesson).

## 7. Open questions for the owner (none blocking this round)

- When does the cross-allergy rule (case c) come, and at what grain: blanket penicillin↔cephalosporin, or R1
  side-chain groups?
- Who reviews the allow-list so the "structural seed" label can come off?

## 8. Left to the builder

Storage shape within §4.2's rule; a new source label vs extending `mesh_run` (within §3's constraint);
`concept_type` naming and its CHECK migration; view vs function for cairn; CLI spelling; the allow-list file
format; how combination descriptors bridge (§4.1); where docs-site's **Design decisions** records the
allow-list rule.

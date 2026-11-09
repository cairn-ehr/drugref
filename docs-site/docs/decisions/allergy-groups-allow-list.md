# An allergy group is an allow-listed root, never a structural class

**Status:** Active
**Last reviewed:** 2026-11-09
**Applies to:** the allergy-groups slice -- `db/067`, `db/068`, `db/069`, `drugref.allergy_check`
**Full derivation:** the [allergy-groups design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-11-08-drugref-allergy-groups-design.md) and its [results record](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-11-09-drugref-allergy-groups-results.md)

## Context

cairn-ehr needs to warn a prescriber when a prescribed drug falls under a recorded allergy ("allergic to
penicillin", or "rash to amoxicillin" and flucloxacillin is prescribed). MeSH's descriptor tree plus its
supplementary records place almost every penicillin, but MeSH's chemical tree is *structural*, and the
[same trap that withheld the `CI_ChemClass` class arm](withheld-chemical-class-contraindications.md) applies
here: a rule that walks a structural tree reaches drugs that merely share a scaffold.

## Decision

**1. Only an allow-listed root is an allergy group.** A group exists only if the reviewed file
`src/drugref/data/allergy_groups.toml` names its MeSH descriptor root. The root's ancestors (beta-Lactams,
Lactams, Amides...) are never groups by inheritance, and a class that is not listed -- Sulfonamides above all,
the sulfa trap -- is not an allergy group however well MeSH places it. This round lists one root, Penicillins
(`D010406`), marked `structural-seed`: not clinically reviewed, and the read says so on every same-group match (a same-drug row carries a NULL `review_status`).

**2. Exclusions and includes need a reason.** `exclude` removes a descriptor's whole subtree (sulbactam,
tazobactam, penicillanic acid -- whose subtree also holds tazobactam and the piperacillin/tazobactam combination
`D000077725`, while piperacillin itself stays in via `D010878` -- and the amoxicillin/clavulanate combination `D019980`, so a mixture key can never pull clavulanate into the group; amoxicillin itself is reached via `D000658`); `include` adds a moiety MeSH carries no bridge key for (`piperacillin anhydrous`,
`carbenicillin indanyl sodium`). Each entry carries a `reason`; the loader refuses an entry without one, any unknown key, a file with no group,
and an exclude that is not strictly below its root. An
edit to this file is a clinical decision, so it is reviewed as one.

**3. The fold covers every component.** Before matching, a prescribed or recorded moiety is folded to every
component in `substance_composition`, whatever `is_active_component` says. GSRS's "active" means
pharmacologically active, which is not what triggers an allergy: `clemizole penicillin` marks penicillin G
FALSE, and an active-only fold would answer a penicillin allergy with a silent `no-match`. The trade is
accepted on purpose: the partner of a salt also matches (penicillin G procaine warns a procaine-allergic
patient). The returned path names the component that matched. This is the owner's call.

**4. `no-match` is not "safe" for cross-reactivity.** The read answers `match`, `no-match` or `unplaced`.
`unplaced` means drugref could not place the drug in MeSH at all: *not checked*, never *safe*. And a
`no-match` against a penicillin group says only that the drug is not a penicillin. It does **not** say
penicillin-allergic patients may take cephalosporins: **case (c), cefalexin, answers `no-match`**, because no
open source states the cross-reactivity rule and it tracks a shared R1 side chain rather than a blanket
penicillin-cephalosporin relation. A consumer must not render that `no-match` as "safe".

## Consequences

- cairn gets a stable `class_uuid` for "Penicillins (allergy group)" and a path
  (allergen -> group -> prescribed drug) to word its warning. Membership is 83 moieties on MeSH 2026.
- **One standing correction to the design spec.** The spec says an allergy group touches no class walk. In
  fact `class_subtree` (db/021) gains the group class's own self-row (every class is its own subtree root).
  It is inert: every view that depends on `class_subtree` joins `class_membership`, and the group has no
  `class_membership` or `class_parent` rows, so no walk reaches a member (checked for
  `additive_effect_contributor` and `interaction_group_member_moiety` too).
- Roughly 30% of prescribable moieties answer `unplaced`; cairn shows them as "not checked".
- Cross-reactivity (case c) needs a later, curated side-chain round.
- Adding a group means adding an allow-list entry and having it reviewed; there is no automatic path from a
  MeSH class to an allergy group.

## Related

- [A structural chemical tree is not a clinical class](withheld-chemical-class-contraindications.md)
- Code: `src/drugref/ingest/allergy_groups.py` (parser, loader), `src/drugref/allergy_read.py`

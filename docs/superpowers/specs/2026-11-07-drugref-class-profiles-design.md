# drugref — class profiles: what a class shares, what a member adds (design)

**Status:** design, 2026-11-07. Owner decisions are marked **(owner)**; calls the designer made are marked
**(designer's call)** and may be overridden. Everything unmarked follows from those two.

## 1. Intent — why this exists

A drug class lets a clinician generalise: learn what the members share **once**, then learn each member as a
short list of differences rather than as something wholly unique. Without class-level information every drug
has to be learnt property by property, and generalisation and recall suffer. **(owner)**

The model is the *structure* of the Australian Medicines Handbook — a class entry stating what the class
shares, then per member only what is peculiar to it — **not its content and not its book format**. AMH text is
copyrighted and commercial; nothing of it is bundled or consulted. drugref is not constrained to a single tree:
classes are **tags**, and a drug carries as many as its sources give it. **(owner)**

The roadmap's data model already says the curated overlay "inherits along the edges … up through a moiety's
classes". This slice makes that visible and adds a second, observed route by which a class earns a property.

## 2. What the reader sees

Two views, served as a data layer (§6):

- **A class profile.** The class's **direct members**; the **class properties** (§3), each with the route that
  put it there; for each member, its **peculiarities** relative to this class; and, separately, the drugs that
  belong **only to subclasses** of it (§4).
- **A drug profile through chosen lenses.** The drug's class tags. **The drug's properties are its own
  member-level properties (§3)**; a property it inherits from class A appears as *shared* under lens A only, and
  is not counted as *peculiar* under any other lens **(designer's call** — otherwise every asserted property of
  one class floods every other lens's peculiarities). The reader picks one or more of them; for
  each chosen class the drug's properties split into **shared with this class** and **peculiar to this drug
  relative to this class**. The same drug splits differently through different lenses and every split is
  correct. **(owner)**

## 3. When a property belongs to a class

**A property** is one exact fact about a drug, identified by *(property kind, relationship, object)*:

| kind | relationship | object | member-level source |
|---|---|---|---|
| indication | `may_treat` / `may_prevent` / `may_diagnose` | condition | `moiety_condition_indication` |
| contraindication | `CI_with` | condition | `moiety_condition_contraindication` |
| induced condition | `induces` | condition | `moiety_induced_condition` |
| interaction | — | partner moiety | `exact_ddi_pair` (`moiety_contraindication`, DrugCentral) |
| interaction | — | partner class | `class_contraindication` (subject side) |

**A member's properties are what a source STATES for that member, never what a view GENERATES** **(designer's
call**, following the owner's exact-property and no-roll-up rules). `ddi_candidate_pair` and the class-rule half
of `curated_ddi_pair` turn one class-level rule into one pair per member of a class *and its subclasses*;
counting those would make the observed route re-derive the asserted one and smuggle in the very subclass
expansion §4 forbids. They are not read. A curator's live **holds** answer (§5) adds the property to the member.

A property belongs to a class by either route; it may qualify by both, and then both are shown. **(owner)**

1. **Asserted** — a source or a curator states it for the class. Today that is `class_pair_contraindication`
   (MED-RT, ONCHIGH) and `curated_class_interaction` with `applies = true`, both class × class interactions.
   **No source asserts a condition property for a class today**, so the asserted route for indications,
   contraindications and induced conditions is curator-only (§5).
2. **Observed** — **at least 75%** of the class's direct members carry **exactly that property**: the same
   kind, relationship and object. **(owner)** Nothing rolls up a hierarchy: a member recorded against *essential
   hypertension* does not count toward *hypertension*, and a member recorded against *fluvoxamine* does not
   count toward *strong CYP1A2 inhibitors*. A member's other properties, and the class's, are irrelevant to the
   count. **(owner)**

Each class property states its route and, if observed, its count (*13 of 15 members*). Sources are provenance,
not part of the property: two sources recording the same fact for one member count once. **(designer's call)**

**Members of an asserted class property inherit it** without needing a member-level record. Members of an
observed one that do not carry it are its **gaps** (§5). **(designer's call:** an asserted property is
authoritative for the class, so a member lacking its own record is not a gap.)

**Curated judgement overrides candidate evidence (designer's call**, the precedence the overlay already
uses). A class × class assertion that `curated_class_interaction` rules `applies = false` is not asserted. A
member whose property a curator has ruled *does not hold* is excluded from the observed count's numerator, does
not inherit an asserted property, and is shown as a **confirmed exception** — even where a candidate record says
otherwise.

### Interactions specifically

- **Both sides (designer's call).** Interactions are clinically symmetric. A moiety × moiety record gives
  **both** drugs the property *interacts with the other*. A class × class assertion appears on both classes'
  profiles, direction preserved as the source states it. A `class_contraindication` row *drug X → class C* is a
  **member property of X** (*interacts with class C*) and an **asserted class property of C** (*interacts with
  X*) — it is a source stating a fact about C as a whole, so it needs no expansion to reach C.
- **SPL co-mentions are NOT interactions.** `spl_ddi_pair` means *a label's interactions section names both
  drugs*, which this repo deliberately keeps out of `exact_ddi_pair`. It is not a member property here either.
- **Self-pairs are excluded**, as everywhere else on the read path.

## 4. Membership and nesting

- **Membership is direct only.** A drug belongs to the classes `class_membership` tags it with. Being a member
  of *Beta-1 selective blockers* does not make a drug a member of *Beta blockers*; a drug may sit in one or more
  subclasses without being in the parent. **(owner)**
- **The observed count's denominator is the direct members.** Subclass-only drugs neither help nor hurt it.
  **(owner)**
- **Subclass-only drugs are listed on the parent's profile as their own group** — *"in subclasses only:
  atenolol (Beta-1 selective blockers), …"* — visibly not members, not counted, never a gap and never on the
  worklist. **(owner)** "Subclass" means any descendant through `class_parent`, via `class_subtree`.
- **ASSERTED properties pass down to subclass-only drugs; OBSERVED ones do not. (owner, 2026-11-07)** Atenolol,
  tagged only *Beta-1 selective blockers*, inherits an interaction MED-RT asserts for *Beta blockers*, shown as
  inherited *via Beta blockers* in its drug profile and beside its entry in the parent's "in subclasses only"
  group. It is still not a parent member and still not counted. The reason is consistency: the prescribing-alert
  path (`curated_ddi_pair`, `ddi_candidate_pair`) already fans class rules out to subclass members, and the help
  section must not omit an interaction the alert fires on. An observed property is a statistic about the
  parent's direct members, so it says nothing about a subclass's.
- **Class hygiene (designer's call).** Classes with no direct members show asserted properties only.
  **MED-RT's PK axis is excluded from class profiles**: #155 measured 97.2% of its occurrences to name an empty
  class, and its concepts (*Clearance*, *Half-Life*) are pharmacokinetic properties rather than drug classes.
  FDA-CYP classes, which reuse the PK concept type but are 100% populated, are kept. The observed route needs at
  least **2** direct members — a one-member class would promote every property of its member. (With *at least
75%*, a two-member class therefore needs both; 3 of 4 qualifies.)

## 5. Gaps, confirmed exceptions, and the worklist

For an **observed** class property, each direct member that does not carry it is one of: **(owner)**

- **Not confirmed** — the default. Rendered as *"not confirmed for X, Y"*. **Never** as "exception", "does not
  apply", or anything a clinician could read as *safe*: in drugref today absence almost always means *no source
  has said*, not *a source said no*.
- **A confirmed exception** — only when a source or curator affirmatively states the property does **not** hold
  for that member: a live **does not hold** answer (below), or `curated_interaction` /
  `curated_class_interaction` with `applies = false` for exactly that pair. **`curated_condition` does NOT
  count (designer's call):** it is keyed by *(moiety, condition)* with no relationship and exists to rule on
  MED-RT's indicated-and-contraindicated contradictions, so a `spurious` ruling cannot be mapped to one property
  without guessing — and every wrong guess is the §9 failure. It errs toward *not confirmed*.

**The worklist.** A *not confirmed* member that no source settles either way becomes a question in the existing
registry: a **new gap kind**, one question per *(property, member)* — **a fact about the member, not about
the class (designer's call)**: whether metoprolol is contraindicated in asthma does not depend on which lens it
is viewed through, so a drug two classes both raise is asked once, and one answer applies in every lens. The
question text names the class(es) that raised it. Registered the way every other gap
kind is (a gap view, a `_GAP_SOURCES` entry, and the widened `open_question_gap_kind` CHECK — the three
`tests/test_drugcentral_gap.py` holds in step). The curator answers **holds**, **does not hold**, or
**cannot be established**. **(designer's call:** the third answer closes the question without asserting
either way, so the member stays *not confirmed* and drops off the worklist.)

**What this needs that does not exist (designer's call on shape, builder's on detail):**

- a curated record of a **class-level property assertion** for the condition kinds, the way
  `curated_class_interaction` already is for interactions;
- a curated record answering a worklist question about **one property of one member** (holds / does not
  hold / cannot be established), keyed *(member, property)*. A live answer outlives the class property that
  raised it: if the property stops being observed, the answer stays and still applies.

Both are curated overlay records — **built as ONE table, `curated_property_ruling`, whose subject is a class or a drug** (builder's call, 2026-11-07: one signing context, one release kind, one question guard instead of two) — append-only, supersession by `superseded_by`, one live row per key, and
**signature targets** under 5c.4's signing — a curated row the signed release omits is #98's defect again.

## 6. How it is made

- **The deliverable is the data layer** — SQL views in new numbered migrations (the schema ends at `db/057`),
  the two curated tables, the gap kind, a Python read module for the CLI, and a `drugref` CLI command that
  prints a class profile and a drug-through-a-lens profile so the result can be inspected. **(designer's call)**
- **The first consumer is the "drug information" help section of a prescribing front end** (Cairn's); the
  reviewer app follows later. **(owner)** Neither is built here: drugref has no HTTP API yet (Slice 6 is
  deferred) and co-located Cairn reads the schema directly, so **the views are the contract** — a consumer must
  be able to render both profiles from the schema alone, without the Python module.
- No new source and no new dependency; nothing to licence-check. Every property comes from tables already
  ingested from public-domain or open sources.
- The usual rules hold: TDD, pure functions where logic lives in Python, files under ~500 lines, ingest
  projections stay delete-and-rebuild per source, applied migrations are immutable.

## 7. Success

- On a real database built from the full chain, both profiles render for any class and any drug, and every
  class property shows its route and, if observed, its count.
- **Measured before it ships**, as every slice here is: how many classes gain observed properties, per axis and
  per property kind; how many *not confirmed* gaps the worklist would receive (the count decides whether the
  worklist is usable as built, and may force a design follow-up); and spot checks on familiar classes
  (beta blockers, ACE inhibitors, SSRIs) read by a clinician.

## 8. Non-goals

- **Mechanism, dosing, counselling.** drugref holds none of them; each needs a source and is its own slice.
- **New class-level statements from SPL labels** — *"avoid strong CYP1A2 inhibitors"* — the original
  "class half" of 5c.3, with its word-order gap and its pair-scoped potency band (#102). It is the **next**
  project and feeds this one; it is not part of it.
- **Hierarchy roll-up** of conditions or partners, for either the count or the display. (Asserted class
  properties descending to subclass-only drugs, §4, is inheritance down the class tree, not roll-up.)
- **UI**, in Cairn or in the reviewer app.
- Salt-grain resolution (#67).

## 9. Anti-goals — failure even if it technically works

- A *not confirmed* member a clinician could read as *safe*.
- Class properties manufactured from empty classes or MED-RT's PK axis — false positives carrying a real
  immortal UUID.
- SPL co-mentions presented as interactions.
- A count that silently rolls up the condition or class hierarchy.
- A curated assertion the signed release omits.

## 10. Left to the builder

Table and view names and column shapes; whether the 75% split is computed in SQL or in a pure Python function
fed by a view (the views must remain the consumer contract either way); the gap key's frozen scheme; the CLI's
argument shape; how the profiles are ordered.

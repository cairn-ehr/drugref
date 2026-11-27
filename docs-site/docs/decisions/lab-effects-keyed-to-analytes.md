# Lab effects are keyed to drugref's own analytes

**Status:** Active
**Last reviewed:** 2026-11-27 (the model, #294 step 2)
**Applies to:** `lab_analyte`, `lab_effect_term`, `curated_lab_effect`, `lab_effect`, `lab_effect_coverage`, `db/089`–`db/092`
**Full derivation:** the [lab-effects design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-11-27-drugref-lab-effects-design.md)

## Context

A laboratory-report triage tool asks drugref: for this analyte and direction (potassium, high), which of a
patient's medicines matter, and how? A medicine can **cause** the abnormality, make it **more dangerous**, define a
**monitoring** protocol, or **interfere** with the result itself. The main open source is US prescription labels,
which name clinical conditions ("hyperkalemia") as often as test results ("serum potassium increased"). No openly
licensed dataset of drug effects on laboratory values exists, and the sources that come closest are coded in
MedDRA, which cannot be redistributed.

## Decision

**1. Statements key to an analyte and a direction, and the analytes are drugref's own, derived from MeSH.** Each
analyte (potassium, INR, creatinine, ...) is built from a MeSH descriptor. Keying to MeSH *conditions* was the first
choice and was dropped: an INR rise has no MeSH condition (INR exists only as a test), and the only condition for a
creatinine rise is acute kidney injury, which would record that trimethoprim injures the kidney when it raises
creatinine by blocking its secretion. The condition a label named is still kept, as what the label said.

**2. No LOINC code appears in drugref's tables, and no analyte is derived from LOINC.** LOINC's licence forbids
changing its content and using it to develop "a different standard for identifying patient observations"; those
terms cannot pass through the AGPL. The consumer maps drugref's analytes to its own LOINC-coded results, a use
LOINC's licence permits.

**3. Label statements are recorded with a grade, and the consumer filters.** The grade is the label section the
statement sits in (boxed warning strongest). The section on laboratory-test interference ranks just after
precautions, so a consumer's filter does not hide the fourth role.

**4. The role is machine-read and unconfirmed, and never defaulted.** When no role rule fires, the role is
`not_read`, never "causes". Reading the role lets testing start before curators have reviewed anything; defaulting
it would turn "makes it more dangerous" (low potassium on digoxin) into "causes", which a consumer treats
differently.

**5. A ruling replaces only the label readings it actually reviewed** (owner rulings, 2026-11-28). A curator's
"holds" on, say, digoxin and low potassium replaces the label readings for low potassium in any role, so a
misread role is corrected by ruling the right one. A "does not hold" or "cannot be established" replaces only
the readings in its own role: "does not interfere with the assay" leaves "causes hypokalaemia" standing. A
ruling also has to match the reading's co-medication ("not more dangerous with drug E" leaves the drug's own
statement alone) and its direction: a ruling on low potassium leaves the readings for high potassium alone, and
a reading whose direction was not read stays listed in whichever direction no ruling has answered yet. A ruling
on a class reaches every member; a member's own "does not hold" carves it out. **A class ruling never hides a
member's label readings**: one "does not hold" on a class must not silence every member's labels, because fewer
rows is the direction that harms.

**6. "No statement" is never "no effect".** The coverage view gives every drug, analyte and direction one state.
Only a curator's "does not hold" for all four roles, with no co-medication, reads as no effect, and a drug's own
"cannot be established" outranks a class-level "does not hold"; "a label was read and said nothing" and "no label
was read" are both unavailable context, never reassurance.

## Consequences

- The read view is usable before any curator works, at the cost of over-inclusion: the source evaluation found
  245 substances mentioning low platelets in a boxed warning or warnings section. Curated salience is the later
  refinement.
- Every class ruling records the drugs it reached when made, so a decision audit can say who it covered then.
- A LOINC crosswalk, if ever wanted, ships beside drugref under LOINC's own licence, never inside its tables.

# A bounded quoted window is bundled; the section is not

**Status:** Active
**Last reviewed:** 2026-08-27
**Applies to:** `drugref.spl_wording`, `drugref.spl_wording_quote` and the deferred constraint trigger
`spl_wording_quote_within_budget` (`db/051`); every future source whose material is prose
**Full derivation:** the [SPL drug × drug ingest design
spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-24-drugref-slice-5c3-spl-ddi-ingest-design.md)
§2 and §4.5, resting on the [subject-recovery
measurement](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-24-drugref-slice-5c3-subject-recovery-measurement.md)
§6

## Context

drugref's rule on licensing is that it is a blocker, not a cleanup item: every bundled source must be
AGPL-3.0-compatible, checked *before* it is added. SPL section 34073-7 is the first source where that question
could not be answered for the source as a whole, because **its two publishers take opposite positions on the
same bytes**:

- **openFDA** publishes the section in its bulk `drug/label` export under an explicit **CC0 1.0** dedication.
- **NLM/DailyMed** publishes the same labels with an explicit disclaimer — *"cannot guarantee the copyright
  status for any item"* — over labeling *"submitted to the FDA by companies"*.

So the unit of clearance is the **column**, decided one at a time, exactly as it was for the DrugCentral
reference question. Three of the four columns are easy: entity occurrences and character offsets are facts and
are not copyrightable; a `set_id`/`version` citation is a citation, not a copy; and the section text in full
is not stored under either reading.

The fourth is the interesting one. Evidence that two drugs are named together is much more useful when a
reader can see the words — and quoting *some* of a document is ordinary. The question is how much, and the
answer could not be chosen by taste, because of what the corpus looks like: the average section is **3,809
characters** and names **48.2 moiety occurrences**. A window around every occurrence does not quote a section.
It reassembles one.

Measured over the whole corpus, per-occurrence rules store:

| rule | mean share of the section stored | ≥ 90% of the section |
| --- | --- | --- |
| the containing sentence | **82.7%** | 41.4% |
| ±120 characters | **89.0%** | 64.4% |
| ±60 characters | 74.9% | 15.6% |

## Decision

**A bounded quoted window is bundled, and the bound is a schema constraint rather than a convention.**

The rule, decided by drugref's owner on 2026-08-24 and measured before it was chosen: **±60 characters around
the FIRST occurrence of each distinct moiety, kept in document order, until 25% of the section's characters
are spent.** Overlapping windows are merged; a window that would exceed the budget is skipped whole rather
than truncated, because truncating cuts a quote mid-word.

Two details carry more weight than they look:

- **Document order, never "pair priority".** Ordering windows by which pairs the registry happens to resolve
  would make the stored bytes move with the vocabulary — and a licensing constraint whose result changes when
  an unrelated ingest runs is not a constraint.
- **The budget is enforced by the database.** `db/051` carries a deferred constraint trigger that re-computes
  the stored characters per wording at commit and refuses the transaction if they exceed `ceil(0.25 ×
  char_length)`, if any two windows overlap, or if any window names a character the wording does not have.
  The failure mode of a merely-intended rule here is silent, additive and visible only in aggregate — which is
  exactly the shape that survives a test suite.

`spl_wording` — the table that identifies each distinct section by the SHA-256 of its normalised text — has
**no prose column in any form**, and its absence is pinned by a test. A `text` column there would make the
budget unenforceable in a single edit.

## Consequences

**Measured on the real releases (2026-08-27):** 138,187 windows over 26,760 wordings — **20.5% of a section
stored on average**, 5.2 merged windows per wording, covering **74.5%** of the distinct moieties named.
Across the corpus, 22,954,172 characters of prose are stored out of 104,384,065.

**The moieties that lose a window lose only the window.** Their occurrence, offsets and citation are stored
regardless, because those columns are clear under either publisher's reading. A consumer who needs the words
for one of them has the `set_id` and can fetch the label from either publisher.

**It is re-decidable without re-ingesting.** The share is one constant in one pure module and one expression
in one trigger, and a test reads the trigger's own definition back out of the database catalog and compares it
against the constant — so a future determination that says 15%, or 0%, is a migration and a re-run rather than
an argument about what the code actually does.

That test used to run the expression with the share *retyped inside the test*, which made it a third home for
the number rather than a check on the other two: changing the trigger alone left it, and every other test over
the budget, passing. It was corrected in the review of the ingest round. The point is worth keeping in a record
of decisions, because the decision here is not merely *25%* — it is *25% enforced where it cannot be quietly
disagreed with*, and a guard nothing has watched refuse is not yet enforcement.

**The cost is that the quoted context is not always the sentence.** ±60 characters can begin mid-clause. That
was accepted deliberately: the alternative that reads best is the one that stores 82.7% of the document.

## Related

- [Licensing is a blocker](licensing-is-a-blocker.md) — the rule this decision applies.
- [A label naming two drugs is evidence, not an assertion](evidence-is-not-an-assertion.md) — what the windows
  sit beside, and why there is no severity column for them to explain.
- [The ONC high-priority floor is facts, not text](the-onc-high-priority-floor.md) — the earlier case where
  the facts-not-expression line was drawn without needing a budget.

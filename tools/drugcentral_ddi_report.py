"""Render the DrugCentral `ddi` measurement as Markdown.

Split out of ``tools/drugcentral_ddi_spike.py`` under CLAUDE.md rule 1 -- pure
functions in small reusable modules -- and for a second reason specific to a
measurement: rendering is **pure**. Every figure arrives in `ReportContext`, so
this module cannot consult the database or the dump and quietly introduce a number
the measurement phase never produced.

The precedent is ``tools/pregnancy_lactation_report.py``, which does the same for
that spike.

**Two things this module must not do again.** It held a second, independent copy of
the rule-6 verdict (``ref_id == "2"``) while `BUNDLEABLE_REF_IDS` decided which rows
were counted -- one home for the licensing rule now, passed in. And it interpolated
live figures into fixed conclusions, so a run that measured nothing still printed a
bold bundling recommendation over an empty evidence table; the conclusions are now
gated on the figures that support them, and `render_report` refuses a context with
no evidence in it at all.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tools.drugcentral_ddi_measure import (
    ClassCoverage,
    Measurement,
    NameProvenance,
)


@dataclass(frozen=True)
class RegistryTotals:
    """The drugref side of the join, sized so the reader can audit the overlap.

    `duplicate_*` count keys claimed by more than one moiety. `identity_claim` is
    unique on ``(moiety_uuid, scheme, value)`` and deliberately NOT across
    moieties, so two moieties may legitimately share a CAS number; a non-zero
    count means some resolutions picked one of several right answers.
    """

    moieties: int
    classes: int
    migration: str
    display_names: int
    inchikeys: int
    cas: int
    duplicate_display_names: int
    duplicate_inchikeys: int
    duplicate_cas: int


@dataclass(frozen=True)
class ReportContext:
    """Everything the report prints, named and typed at the module boundary.

    Was a bare ``dict[str, object]`` filled in one function and read by string
    subscript in another, which is how two measured figures -- ``self_pair_rows``
    and the ``ddi_risk`` vocabulary -- came to be computed, carried across the
    boundary, and rendered nowhere. An unused field is visible; an unused dict key
    is not.
    """

    generated: str
    dump: str
    dump_bytes: int
    dump_sha256: str
    release: str
    dump_lines: int
    decompressed_bytes: int
    table_counts: Mapping[str, int]
    references: Mapping[str, Mapping[str, str]]
    ref_distribution: Mapping[str, int]
    bundleable_ref_ids: frozenset[str]
    risk_vocabulary: Sequence[Mapping[str, str]]
    risk_whole: Mapping[str, int]
    risk_bundleable: Mapping[str, int]
    registry_totals: RegistryTotals
    candidate_rows: int
    candidate_pairs: int
    whole_name_only: Measurement
    whole_cascade: Measurement
    bundleable_name_only: Measurement
    bundleable_cascade: Measurement
    whole_class_coverage: ClassCoverage
    whole_class_coverage_name_only: ClassCoverage
    name_provenance: NameProvenance
    qt_rows: Sequence[Mapping[str, str]]
    pharma_class_rows: int
    pharma_class_named: int
    pharma_class_qt: int


def _resolution_table(name_only: Measurement, cascade: Measurement) -> str:
    """The comparison that IS the finding: name matching vs. the structural cascade.

    Six measures, each as ``before | after | delta``. ``delta`` is computed here
    rather than carried in, so it cannot disagree with the two columns beside it.
    """
    rows = [
        ("distinct endpoint spellings", name_only.raw_names, cascade.raw_names),
        ("distinct endpoint names (folded)", name_only.names, cascade.names),
        ("names resolved to a moiety", name_only.names_resolved,
         cascade.names_resolved),
        ("rows with an unresolvable endpoint", name_only.unresolvable_rows,
         cascade.unresolvable_rows),
        ("rows resolving to a self-pair", name_only.self_pair_rows,
         cascade.self_pair_rows),
        ("rows yielding a pair", name_only.pair_rows, cascade.pair_rows),
        ("distinct unordered moiety pairs", name_only.pairs, cascade.pairs),
        ("pairs drugref already holds", name_only.held, cascade.held),
        ("pairs that are NEW", name_only.new, cascade.new),
    ]
    out = ["| measure | name matching (issue #101) | + InChIKey/CAS cascade | delta |",
           "|---|---:|---:|---:|"]
    for label, before, after in rows:
        out.append(f"| {label} | {before:,} | {after:,} | {after - before:+,} |")
    return "\n".join(out)


def _accounting(measurement: Measurement) -> str:
    """State the row arithmetic in full, because it used not to be statable.

    The published table gave rows and distinct pairs and left the difference
    unattributable -- self-pairs were counted and never printed.
    """
    return (
        f"{measurement.rows:,} rows = {measurement.unresolvable_rows:,} unresolvable "
        f"+ {measurement.self_pair_rows:,} self-pair "
        f"+ {measurement.pair_rows:,} pair-yielding, and those "
        f"{measurement.pair_rows:,} rows collapse to "
        f"{measurement.pairs:,} distinct unordered pairs.")


def _routes(cascade: Measurement) -> str:
    """Every route, resolved and unresolved alike, so the residue is attributable."""
    out = ["| route | endpoint names |", "|---|---:|"]
    for route, count in sorted(cascade.routes.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"| `{route}` | {count:,} |")
    return "\n".join(out)


def _coverage_table(name_only: ClassCoverage, cascade: ClassCoverage) -> str:
    """Moiety / class / neither, under both resolvers.

    Both are shown because issue #101's *"7,000 of 7,621 (91.9%) keyable"* was
    measured with name matching alone: quoting it beside a cascade figure would
    compare two different questions.
    """
    rows = [
        ("endpoint names resolved to a moiety",
         name_only.names_resolved, cascade.names_resolved),
        ("unresolved names that ARE a `substance_class`",
         name_only.names_matching_a_class, cascade.names_matching_a_class),
        ("names matching neither",
         name_only.names_matching_nothing, cascade.names_matching_nothing),
        ("rows keyable (moiety **or** class at both ends)",
         name_only.keyable_rows, cascade.keyable_rows),
        ("rows moiety x moiety",
         name_only.moiety_by_moiety_rows, cascade.moiety_by_moiety_rows),
    ]
    out = ["| measure | name matching (issue #101) | + cascade |", "|---|---:|---:|"]
    for label, before, after in rows:
        out.append(f"| {label} | {before:,} | {after:,} |")
    return "\n".join(out)


def _reference_detail(ref: Mapping[str, str] | None) -> str:
    """Describe one cited reference, distinguishing 'absent' from 'blank title'."""
    if ref is None:
        return "**not cited in the `reference` table**"
    title = ref.get("title") or "_(the reference row carries no title)_"
    who = ref.get("authors") or ""
    extra = []
    if ref.get("isbn10"):
        extra.append(f"ISBN {ref['isbn10']}")
    if ref.get("dp_year"):
        extra.append(str(ref["dp_year"]))
    detail = title
    if who:
        detail += f" — {who}"
    if extra:
        detail += f" ({', '.join(extra)})"
    return detail


def _references(context: ReportContext) -> str:
    """Rule 6, printed from the `reference` table and the set that did the filtering."""
    out = ["| `ddi_ref_id` | rows | what the dump says it is | rule 6 |",
           "|---|---:|---|---|"]
    distribution = sorted(context.ref_distribution.items(), key=lambda kv: -kv[1])
    for ref_id, rows in distribution:
        detail = _reference_detail(context.references.get(ref_id))
        verdict = ("**clean — bundle**" if ref_id in context.bundleable_ref_ids
                   else "**out**")
        out.append(f"| `{ref_id}` | {rows:,} | {detail} | {verdict} |")
    return "\n".join(out)


def _risk_vocabulary(context: ReportContext) -> str:
    """Print the whole `ddi_risk` lookup, including labels no row uses.

    A label at 0/0 is the evidence for "scoped per reference"; omitting it left
    the claim asserted and unsupported.
    """
    labels = sorted(
        {row.get("name", "") for row in context.risk_vocabulary if row.get("name")}
        | set(context.risk_whole))
    out = ["| risk label | whole table | bundleable subset |", "|---|---:|---:|"]
    for label in labels:
        out.append(f"| `{label}` | {context.risk_whole.get(label, 0):,} "
                   f"| {context.risk_bundleable.get(label, 0):,} |")
    return "\n".join(out)


def _qt(context: ReportContext) -> str:
    """The QT rows, verbatim -- except the prose of any row rule 6 excludes.

    CLAUDE.md rule 6 calls licensing a blocker. All three QT rows in the
    2023-11-01 dump cite Lexicomp, which this same document rules out, so
    reproducing their sentences into a committed AGPL repo on every run should not
    be a side effect of this section having no reference filter. The endpoint
    strings are what issue 93 actually needs; the descriptions are withheld.
    """
    lines = []
    for row in context.qt_rows:
        ref_id = row["ddi_ref_id"]
        if ref_id in context.bundleable_ref_ids:
            prose = row["description"]
        else:
            prose = ("_withheld — this row cites a reference rule 6 excludes; "
                     "read it in the dump_")
        lines.append(
            f"- `ddi_ref_id={ref_id}` · risk `{row['ddi_risk']}`\n"
            f"  - `drug_class1` = `{row['drug_class1']}`\n"
            f"  - `drug_class2` = `{row['drug_class2']}`\n"
            f"  - description: {prose}"
        )
    return "\n".join(lines) if lines else "_none_"


def _check(context: ReportContext) -> None:
    """Refuse to render a report whose evidence is missing.

    A run that measured nothing still produced clean Markdown, a full comparison
    table of zeros and `+0` deltas, and a bold bundling recommendation over an
    empty reference table -- exit code 0. No corruption is required to get there:
    a release that renumbers its references empties the bundleable subset.
    """
    if not context.ref_distribution:
        raise ValueError("no `ddi` row cited any reference: nothing was measured")
    if context.bundleable_cascade.rows == 0:
        raise ValueError(
            "the bundleable subset is empty -- `BUNDLEABLE_REF_IDS` "
            f"{sorted(context.bundleable_ref_ids)} matched no row, so there is no "
            "licensing determination to publish")
    uncited = sorted(context.bundleable_ref_ids - set(context.references))
    if uncited:
        raise ValueError(
            f"reference(s) {uncited} are marked bundleable but the dump's "
            "`reference` table does not describe them: a bundling right cannot "
            "rest on a citation that is not there")


def render_report(context: ReportContext) -> str:
    """Return the whole Markdown report for *context*."""
    _check(context)

    whole_n, whole_c = context.whole_name_only, context.whole_cascade
    bund_n, bund_c = context.bundleable_name_only, context.bundleable_cascade
    totals = context.registry_totals

    coverage = context.whole_class_coverage
    name_only_coverage = context.whole_class_coverage_name_only
    prov = context.name_provenance
    def authorities(cov: ClassCoverage) -> str:
        return ", ".join(f"`{source}` {count:,}"
                         for source, count in sorted(cov.by_source.items())) or "_none_"

    class_authorities = authorities(coverage)
    name_only_authorities = authorities(name_only_coverage)
    decompressed = (f"{context.decompressed_bytes:,} characters over "
                    f"{context.dump_lines:,} lines, counted during the extract")
    bundleable = ", ".join(sorted(context.bundleable_ref_ids))
    unresolved_list = "\n".join(
        f"- `{name}` — `{route}`"
        for name, route in bund_c.unresolved_names) or "_none_"
    table_sizes = " · ".join(
        f"`{t}` {c:,}" for t, c in sorted(context.table_counts.items()))

    duplicates = (
        f"{totals.duplicate_display_names:,} display names, "
        f"{totals.duplicate_inchikeys:,} InChIKeys and {totals.duplicate_cas:,} CAS "
        "numbers are claimed by more than one moiety")

    qt_conclusion = (
        "so the dump names those populations and defines them nowhere"
        if context.pharma_class_qt == 0 else
        f"and {context.pharma_class_qt} of them do name a QT population")

    return f"""# DrugCentral `ddi` — re-measurement for issue #101

> Generated by `tools/drugcentral_ddi_spike.py` on **{context.generated}**.
> Re-run it rather than quoting these numbers from memory.

## What was measured, and against what

| | |
|---|---|
| dump | `{context.dump}` |
| published release | **{context.release}** |
| size | {context.dump_bytes:,} bytes |
| decompresses to | {decompressed} |
| SHA-256 | `{context.dump_sha256}` |
| drugref schema | `{totals.migration}` |
| registry | {totals.moieties:,} moieties · {totals.classes:,} classes |
| registry lookups | {totals.display_names:,} display names · \
{totals.inchikeys:,} InChIKeys · {totals.cas:,} CAS |
| `ddi_candidate_pair` | {context.candidate_rows:,} rows · \
{context.candidate_pairs:,} distinct unordered pairs |

Extracted table sizes: {table_sizes}.

Collision check on the join: {duplicates}. Every lookup is loaded under a
deterministic `ORDER BY`, so a colliding key resolves the same way on every run.

## Rule 6 — read from the `reference` table, never inferred

DrugCentral publishes the compilation under CC BY-SA 4.0, **which is not evidence of
a right to relicense a third-party compendium inside it.**
Every `ddi` row cites one of {len(context.ref_distribution)} references:

{_references(context)}

**Bundle `ddi_ref_id` {bundleable} only.**

## Severity vocabulary

`ddi_risk` is a lookup table scoped **per reference**, so the vocabulary available to
the bundleable subset is narrower than the table's overall vocabulary. The whole
lookup is printed, including labels no row uses — a label at 0/0 is what makes the
scoping visible:

{_risk_vocabulary(context)}

## ⇒ The finding: resolution should key on STRUCTURE, not on spelling

Issue #101 resolved endpoints by matching `substance_moiety.display_name` and
concluded the residual INN spellings *"need a synonym bridge"*. DrugCentral
resolves its own endpoint text to a `struct_id`, and `structures` carries an
InChIKey and a CAS number that drugref already holds as `identity_claim` rows.

**Bundleable subset ({bund_c.rows:,} rows):**

{_resolution_table(bund_n, bund_c)}

Row accounting, cascade run: {_accounting(bund_c)}

Routes that answered:

{_routes(bund_c)}

**Whole table ({whole_c.rows:,} rows), for comparison with the original evaluation:**

{_resolution_table(whole_n, whole_c)}

Row accounting, cascade run: {_accounting(whole_c)}

### Where the endpoint names come from, before drugref is consulted at all

Bundleable subset, over its {prov.names:,} folded endpoint names — the
denominator the synonym-bridge claim is about. This is the evidence that a
hand-maintained bridge is unnecessary: if DrugCentral can name the structure
itself, drugref never has to learn the spelling.

| DrugCentral's own tables | endpoint names |
|---|---:|
| a `structures.name` | {prov.in_structures:,} |
| a `synonyms.name` and not a primary one | {prov.in_synonyms_only:,} |
| in neither | {prov.in_neither:,} |
| **total (folded)** | **{prov.names:,}** |

### The residue drugref holds as a CLASS rather than a moiety

Whole table. *Keyable* and *moiety x moiety* are DIFFERENT denominators -- their
difference is the rows with exactly one class endpoint, which is why
`rows - keyable` is not the unresolvable count:

Both resolvers are shown, because issue #101's *"7,000 of 7,621 (91.9%) keyable"*
was measured with name matching alone and is not comparable to a cascade figure:

{_coverage_table(name_only_coverage, coverage)}

Class matches by the authority that defines them — name matching:
{name_only_authorities}; cascade: {class_authorities}.

### The {len(bund_c.unresolved_names)} endpoint names the cascade does not resolve

Bundleable subset, each with the route that gave up. The four are not the same
fact: `not_a_substance` means DrugCentral itself holds no structure for the text
(a class-named endpoint, and a correct miss); `no_structural_key` is a biologic or
mixture with neither key; `unresolved` means the keys exist and drugref does not
hold them; and `missing_keys_row` would mean the extract is inconsistent.

{unresolved_list}

## Issue 93 (QT) — restated, and narrower than recorded

`pharma_class` holds {context.pharma_class_rows:,} rows
({context.pharma_class_named:,} of {context.pharma_class_rows:,} carry a name) and
the token `QT` appears in **{context.pharma_class_qt}** of those names,
{qt_conclusion}. The rows that mention QT at all:

{_qt(context)}
"""

# src/drugref/ingest/fda_cyp_run.py
"""Orchestrate one FDA-CYP ingest: parse -> resolve -> clear -> write -> rebuild.

The ONLY writer of drugref's FDA-CYP rows, per the architecture invariant:
parsers are pure, orchestrators own the transaction.

ORDER MATTERS, as for every other feed here:
  1. parse and checksum BEFORE opening the run, so a crash during the parse
     leaves no half-written run row;
  2. clear this source's old rows, so a re-ingest REPLACES rather than accumulates;
  3. write classes, then memberships, then the assertion projection;
  4. rebuild the question register, finish, commit.

WHAT THIS MODULE REFUSES TO DO, and it is most of the design:

* It writes NO class_contraindication and NO DDI pair. FDA calls its table an
  optional, non-exhaustive interpretive guide; joining the inhibitor and
  substrate columns would manufacture ~800 pairs no source asserts.
* It promotes NO footnoted cell to membership. Two of FDA's footnotes NEGATE the
  row they sit on, and deciding which do is a clinical reading of prose.
* It bridges NO name. Six recognisable categories sit in the resolution residue
  and every one of them is a different job -- see the standing rule in
  PROJECT-NOTES, and issue 128 for the enantiomers specifically.
"""
import dataclasses
import logging
import pathlib
import re
import uuid

import psycopg

from drugref import classes, db, provenance, questions
from drugref.ingest import fda_cyp
from drugref.ingest.checksum import checksum

SOURCE = "FDA-CYP"
# WHICH orchestrator this is, as distinct from SOURCE, the authority it reads
# (db/025). Declared in provenance.WRITERS and db/039's CHECK -- a pair.
WRITER = "fda_cyp_run"

# The axis FDA's roles sit on. Both values predate this slice (db/003), which is
# the whole argument for projecting the roles as PK classes rather than inventing
# a mechanism for them.
CONCEPT_TYPE = "PK"
RELATIONSHIP = "has_PK"

# The projection this source owns, cleared per-source on every re-ingest. Named
# as a tuple (rather than a bare string) to match db.clear_source_tables's
# signature and the sibling constants in classes.py (CLASS_EDGE_TABLES,
# UNMATCHED_INGREDIENT_TABLES) and onchigh_run.py (UNRESOLVED_ENDPOINT_TABLES)
# -- the latter being the orchestrator-level precedent db/039's own header
# already cites as this projection's shape. gsrs_run.py, named here before, has
# no such constant and never calls clear_source_tables at all.
FDA_CYP_TABLES = ("fda_cyp_assertion",)

# FDA'S OWN FIVE, quoted from the page's prose rather than inferred:
#   "Table 1 also includes five other substances that interact with CYP enzymes
#    and transporter systems (i.e., St. John's wort (a dietary supplement),
#    curcumin (a supplement), diosmin (a supplement), tobacco (smoking) and
#    grapefruit juice (a food))."
#
# READ THAT LIST CAREFULLY BEFORE CHANGING IT: curcumin and diosmin RESOLVE as
# ordinary drugref moieties. Non-drug and unresolvable are INDEPENDENT properties,
# so this list can never be derived from a resolution failure -- it can only be
# FDA's own statement. Matched through _is_non_drug_entity below, which folds
# case AND apostrophe spelling: FDA prints U+2019, but this set stores the ASCII
# form and the comparison normalises to it, so the entry no longer depends on
# which apostrophe the CMS happens to emit.
NON_DRUG_ENTITIES = frozenset({
    "st. john's wort", "curcumin", "diosmin", "tobacco (smoking)", "grapefruit juice",
})

# Apostrophe spellings one name can arrive under. FDA prints U+2019; a CMS
# change to &#39;, to a plain ASCII quote, or to U+02BC would silently stop
# matching -- and the row would then fall through to a DIFFERENT VALID
# disposition (withheld_qualified, since marker 18 sits on St. John's wort's
# name), so nothing would fail. A one-codepoint change must not flip a clinical
# classification, and this is the whole distance between "FDA says it is not a
# drug" and "drugref asserts a CYP3A induction membership for a supplement".
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "´": "'", "`": "'"})


def _is_non_drug_entity(substance: str) -> bool:
    """Is this one of FDA's own five declared non-drugs?

    Case- and apostrophe-insensitive, matched against the footnote-stripped
    name. Deliberately NOT ids.normalise_name: that folds far more than
    punctuation, and this comparison must stay narrow enough that a reader can
    tell exactly which spellings it accepts.
    """
    return substance.lower().translate(_APOSTROPHES) in NON_DRUG_ENTITIES


# A COMBINATION REGIMEN is FDA reporting a role for several substances taken
# together ("atazanavir and ritonavir"), never for one. The word "and" as a
# whole word -- \b on both sides -- is the signal: measured against every one of
# the 244 distinct substance names on the real page, it is true for exactly the
# nine combination rows and false for every single-substance name, including
# ones that CONTAIN the letters "and" as a substring ("vandetanib" has no word
# boundary before or after "and", so \band\b correctly leaves it alone). A
# plain substring test would not have that property.
_COMBINATION_WORD = re.compile(r"\band\b", re.I)

# Markers FDA prints in a cell but never defines in its own Footnotes list.
# Exactly one today: the lettered 'b' on cenobamate's CYP3A-inducer cell. Design
# section 2.3 calls the letters "a second namespace"; the live page's Footnotes
# block has no entry for a letter at all. NAMED rather than absorbed by a
# general "skip anything unknown" rule, so a marker going undefined for any
# OTHER reason -- the far likelier one being that drugref stopped reading the
# definitions correctly -- is loud instead of looking like this same oddity.
UNDEFINED_MARKERS = frozenset({"b"})

log = logging.getLogger(__name__)


class FdaCypShrinkError(RuntimeError):
    """This page would replace the stored projection with a fraction of itself.

    Distinct from fda_cyp.FdaCypParseError because NOTHING IS WRONG WITH THE
    PARSE: every surviving row is well-formed, the vocabulary is closed, the
    cross-check passes. What is wrong is the COUNT, and only the writer -- which
    knows what is already stored -- can see it.

    A RuntimeError SUBCLASS, deliberately, and that is what routes it: cli.main
    already catches RuntimeError to print one line and exit 2, which is the
    right shape here because this is OPERATOR-FACING -- the page they fetched is
    short, drugref is not broken, and the message (which names --allow-shrink)
    is the whole useful answer. Contrast FdaCypDispositionError below, which is
    a plain Exception precisely so it does NOT get that treatment: that one
    means drugref produced a disposition it cannot count, and its traceback
    naming the writer is the most useful thing the process can print. cli.py's
    own comment about CheckViolation draws the same distinction.
    """


class FdaCypFootnoteError(Exception):
    """A qualified cell's markers have no text on a page whose Footnotes read fine.

    A plain Exception, like FdaCypDispositionError and unlike FdaCypShrinkError:
    the Footnotes section parsed, so this is drugref and FDA disagreeing about
    what a marker IS, and the traceback is the useful output.
    """


class FdaCypDispositionError(Exception):
    """_classify returned a disposition this orchestrator cannot count.

    Raised rather than absorbed for db/041's reason, applied to the Python side:
    a disposition nothing counts is a row that vanishes from the summary while
    still being written, and the summary is what an operator reads.
    """


# How much of the stored projection a re-ingest is allowed to drop before it
# must be authorised deliberately. Half is a wide margin on purpose: FDA
# revising the table is ordinary, and this guard is aimed at the catastrophic
# case (a truncated fetch leaving a handful of rows), not at policing normal
# release-to-release drift.
MIN_RETAINED_FRACTION = 0.5

# The closed disposition vocabulary, in ONE place on the Python side. It must
# stay equal to db/039's fda_cyp_assertion_disposition CHECK -- pinned as an
# equality (not a subset) by tests/test_fda_cyp_schema.py, so widening one
# without the other fails rather than drifts.
DISPOSITIONS = frozenset({
    "member", "withheld_qualified", "unresolved_substance",
    "combination_regimen", "non_drug_entity",
})


def _footnote_text(markers: str | None,
                   footnote_by_marker: dict[str, str]) -> str | None:
    """FDA's own prose for a tuple's footnote_markers ('14, 15, 16' -> joined text).

    `footnote_by_marker` is fda_cyp.parse_footnotes's return value for THIS
    page -- read structurally on every ingest, never hardcoded here. An
    earlier round of this module kept a hand-copied dict of FDA's footnote
    text instead, quoted verbatim from a checksum-verified fetch; review
    caught what that copy could not detect: checksum() and parse_release()
    exist specifically to make a SOURCE CHANGE loud, and a copy pasted into
    Python escapes both of them. If FDA reworded footnote 2 tomorrow, the
    checksum would change and the ingest would still run green, silently
    writing the OLD wording into footnote_text -- the one column whose entire
    job is to carry FDA's current words. Reading it fresh from the page every
    time is what keeps that column and the checksum answering the same
    question.

    Returns None only when `markers` itself is None -- an unqualified cell has
    nothing to explain. For a qualified cell this looks up EVERY marker in the
    comma-separated list and joins whatever text is on file, silently skipping
    a marker with no known definition (see parse_footnotes's own docstring on
    the lettered marker 'b') rather than raising: an undefined marker is a
    page oddity to record evidence about, not a reason to abort an otherwise-
    good row. It can only return None for a qualified cell if NONE of its
    markers are on file, which has not happened on any release measured so far.
    """
    if markers is None:
        return None
    wanted = [part.strip() for part in markers.split(",") if part.strip()]
    found = [footnote_by_marker[marker] for marker in wanted
             if marker in footnote_by_marker]
    if not found:
        # EVERY marker on a qualified cell being undefined is a different event
        # from ONE being undefined, and only the second is the documented page
        # oddity. The old code returned None for both, so a parse that silently
        # stopped matching FDA's footnote paragraphs (see
        # fda_cyp.parse_footnotes, which now raises on an empty section) would
        # have surfaced here as "no prose on file" for every withheld row --
        # which reads as a fact about FDA's page rather than a defect in the
        # read. UNDEFINED_MARKERS names the ONE spelling that is genuinely
        # undefined upstream, so anything else going missing is loud.
        unexpected = [marker for marker in wanted if marker not in UNDEFINED_MARKERS]
        if unexpected:
            raise FdaCypFootnoteError(
                f"none of the footnote markers {wanted!r} has any text on this "
                f"page, and {unexpected!r} are not the known-undefined ones "
                f"({sorted(UNDEFINED_MARKERS)}). FDA's Footnotes section was "
                "read, so this is not a missing section -- the markers and the "
                "definitions have stopped agreeing.")
        return None
    return " / ".join(found)


@dataclasses.dataclass(frozen=True)
class FdaCypSummary:
    """What one FDA-CYP run did -- returned so a caller or test can assert on it.

    EVERY FIELD IS NAMED FOR WHAT IT ACTUALLY COUNTS. `assertions_written` is
    every tuple parsed; `memberships_written` is the subset promoted, and the two
    differ by exactly the withheld and unresolved populations. A summary line the
    CLI prints is the number a reader quotes later, so a name that overstates its
    scope is a wrong number with a plausible source.

    `classes_in_release` and `classes_added` are TWO NUMBERS BECAUSE THEY ARE
    TWO FACTS, exactly as MedrtSummary splits them: the first is every class
    this release names, the second is how many of them this run actually
    minted. A single field called `classes_minted` carrying the first was a
    wrong number with a plausible source -- it printed 65 on every re-ingest
    while nothing was minted at all, and "did this release change anything?" is
    the question an operator asks it.

    `memberships_written` is MEASURED, never derived from the others. NOT
    because the disposition categories overlap -- they cannot, since _classify
    returns exactly one disposition per tuple, so the five counters are
    disjoint by construction. The real reason is classes.add_membership's
    ON CONFLICT DO NOTHING: two tuples resolving to the same (moiety, class)
    pair write one edge and increment this once, so no arithmetic over the
    other counts reconstructs it.

    `questions_registered` reads the 'fda_cyp_unadjudicated' bucket of
    questions.register_from_gaps's return value -- wired into questions.py's
    _GAP_SOURCES in the same round as this module, so it is the live count of
    currently-open fda_cyp_unadjudicated questions on every run, not a
    placeholder. Measured on the pinned page: 55 gap rows (33
    withheld_qualified + 9 combination_regimen + 8 unresolved_substance + 5
    non_drug_entity). THOSE ARE GAP-ROW COUNTS, NOT THE SIBLING FIELDS ABOVE:
    the subject-grain dispositions collapse many tuples onto one row, so
    `combination_regimens` (17) and `unresolved_substances` (16) are larger
    than the 9 and 8 here. A figure of THIS run, not a property of the code.
    """
    upstream_release: str
    classes_in_release: int
    classes_added: int
    memberships_written: int
    assertions_written: int
    withheld_qualified: int
    unresolved_substances: int
    combination_regimens: int
    non_drug_entities: int
    questions_registered: int


def source_code(system: str, pathway: str, role: str, potency: str | None) -> str:
    """The deterministic key a class_uuid is minted from.

    EXPLICITLY A DRUGREF NORMALISATION KEY, NOT AN FDA IDENTIFIER -- FDA publishes
    no code for these classes. The live URL, dateModified, checksum and raw column
    heading carry the provenance, which is why substance_class.published_code is
    left NULL rather than filled with something invented.

    Punctuation is folded out of the pathway ('P-gp' -> 'pgp', 'MATE2-K' ->
    'mate2k') so the key is stable against a spelling change upstream that does
    not change the concept.
    """
    prefix = "cyp" if system == "CYP" else "transporter"
    token = pathway.lower().replace("-", "").replace(" ", "")
    parts = [prefix, token, role]
    if potency:
        parts.append(potency.replace(" ", "-"))
    return ":".join(parts)


def class_name(system: str, pathway: str, role: str, potency: str | None) -> str:
    """The cached display name, SOURCE-TAGGED.

    '[FDA-CYP]' rather than MED-RT's '[MoA]' shape, so no consumer or UI can
    mistake one for the other. MED-RT's bracketed suffix is published BY MED-RT;
    this one is drugref's own label, and saying so is the difference between a
    label and a claim about what FDA published.
    """
    stem = f"CYP{pathway}" if system == "CYP" else pathway
    band = f"{potency} " if potency else ""
    return f"{stem} {band}{role} [FDA-CYP]"


def _fold_by_lower(
        by_display_name: dict[str, list[uuid.UUID]]) -> dict[str, list[uuid.UUID]]:
    """Re-key classes.moieties_by_display_name's index onto lower(display_name).

    A SEPARATE fold rather than lower-casing at the call site, so two distinct
    display names that happen to fold onto one lower-case string are correctly
    MERGED here -- both sets of claimants kept, never one dropped -- and a
    lookup against the merged bucket then naturally lands on unresolved_substance
    if it holds more than one moiety: resolution is exact and case-insensitive,
    and AMBIGUITY IS UNRESOLVED, never "pick the first", because a name
    resolving to several moieties is real information (the registry grew and
    now genuinely disagrees with itself about this name) that silently picking
    one would erase. Nothing collides under lower() on the registry measured
    for this design, but the registry grows, and this is what keeps that true
    without anyone having to re-derive it by hand later.
    """
    folded: dict[str, list[uuid.UUID]] = {}
    for name, moieties in by_display_name.items():
        folded.setdefault(name.lower(), []).extend(moieties)
    return folded


def _classify(substance: str, single: uuid.UUID | None,
             footnote_markers: str | None) -> tuple[str, uuid.UUID | None]:
    """One tuple's disposition and resolved moiety, in this FIXED order:
    non_drug_entity -> combination_regimen -> unresolved_substance ->
    withheld_qualified -> member.

    The order exists BECAUSE the categories overlap, not despite it:
    grapefruit juice is both one of FDA's own pinned five non-drugs (the
    sentence quoted verbatim on NON_DRUG_ENTITIES above; it sits at the END of
    the design spec's section 7.2, in the "trap worth stating" paragraph --
    that section is mostly about the three enantiomer names and their own
    unrelated deferral, but FDA's non-drug sentence is quoted there too) AND
    footnoted (marker
    9), so a disposition function that checked footnote status first would
    misfile it as withheld_qualified -- a real category, but the wrong one,
    and silently so, since both are valid CHECK values. Checking
    non_drug_entity first is what keeps grapefruit juice non_drug_entity
    regardless of what else is true of the row (pinned directly by
    test_grapefruit_juice_is_non_drug_entity_even_though_it_is_footnoted).

    `single` is the ALREADY-RESOLVED moiety (or None), computed once by the
    caller from the case-folded index -- so this function only decides WHERE
    that resolution result lands, never how it was computed. The resolution
    itself and the non_drug_entity/combination_regimen check are independent:
    curcumin resolves to a real moiety AND is still non_drug_entity (the
    design spec's own "trap worth stating because it inverts the obvious
    assumption" -- non-drug and unresolvable are independent properties), so
    `single` is threaded through unchanged for that branch. combination_regimen
    forces None regardless of what `single` says -- FDA reports the role for
    the REGIMEN, and assigning it to any one component (even an accidental
    exact-name match) is an inference FDA did not make.
    """
    if _is_non_drug_entity(substance):
        return "non_drug_entity", single
    if _COMBINATION_WORD.search(substance):
        return "combination_regimen", None
    if single is None:
        return "unresolved_substance", None
    if footnote_markers:
        return "withheld_qualified", single
    return "member", single


def ingest_fda_cyp(conn: psycopg.Connection, *, page_path: str | pathlib.Path,
                   upstream_release: str | None = None,
                   allow_shrink: bool = False) -> FdaCypSummary:
    """Ingest one FDA-CYP page: parse, resolve, clear, write, rebuild questions.

    `upstream_release`, if given, OVERRIDES fda_cyp.parse_release -- the escape
    hatch every test in this module uses, since the parser test fixture carries
    only table 1 and no dateModified stamp to parse. A real ingest omits it and
    lets the page's own stamp govern (design section 13): fetch time is never a
    substitute, because it records when drugref looked rather than when FDA
    changed the content.

    `allow_shrink` authorises a run that would drop more than
    MIN_RETAINED_FRACTION of the stored projection. It defaults to False
    because THE DEFAULT IS THE WHOLE POINT: a truncated page parses green (its
    surviving rows are individually perfect), and this projection is
    delete-and-rebuild, so an unguarded run replaces 419 tuples with 5 and
    reports success. FDA genuinely shrinking its table is a real event -- it
    just has to be a decision someone made, not one nobody saw.
    """
    clock = provenance.start_clock()  # FIRST: see provenance.start_clock (#159)
    # 1. PARSE FIRST, before any run row exists, so a crash here -- an unknown
    #    pathway token, a ragged row, a missing dateModified, a missing
    #    Footnotes section -- leaves no trace to explain (gsrs_run's ordering,
    #    restated here for the same reason). parse_footnotes reads the page's
    #    OWN current wording every call, never a cached copy -- see its
    #    docstring and _footnote_text's for why that is the whole point.
    page = pathlib.Path(page_path).read_text(encoding="utf-8")
    tuples = fda_cyp.parse_table(page)
    footnote_by_marker = fda_cyp.parse_footnotes(page)
    if upstream_release is None:
        upstream_release = fda_cyp.parse_release(page)
    source_checksum = checksum(page_path)

    # 1a. REFUSE A PAGE THAT WOULD GUT THE PROJECTION -- here, with the rest of
    #     the parse-time refusals, so a refusal leaves NO run row to explain,
    #     exactly as step 1's own comment requires. Compared against what is
    #     already STORED rather than against a pinned 245, for two reasons: no
    #     constant has to be bumped when FDA grows its table, and a FIRST
    #     ingest (stored == 0) is never blocked -- correctly, since it destroys
    #     nothing.
    #
    #     This is the guard fda_cyp.py's module docstring used to CLAIM ("the
    #     row and cell COUNTS are asserted") while only the cell count existed.
    #     It cannot live in the parser: 245 is a property of one release, not
    #     of the table's shape, and the harm is done by REPLACING a stored
    #     projection, which only the writer can see.
    stored = conn.execute(
        "SELECT count(*) FROM drugref.fda_cyp_assertion a "
        "JOIN drugref.ingest_run r ON r.ingest_run_id = a.ingest_run "
        "WHERE r.source = %s", (SOURCE,)).fetchone()[0]
    if stored and not allow_shrink and len(tuples) < stored * MIN_RETAINED_FRACTION:
        raise FdaCypShrinkError(
            f"refusing to replace {stored} stored FDA-CYP assertion rows with "
            f"{len(tuples)} from {page_path}: that drops more than "
            f"{1 - MIN_RETAINED_FRACTION:.0%} of the projection. A truncated "
            "fetch parses green -- every surviving row is well-formed -- so "
            "this count is the only signal. Re-fetch the page, or pass "
            "--allow-shrink if FDA really did shrink the table.")

    # 2. Open the run. COMMITS in its own transaction (provenance.open_run) --
    #    everything from here is the work, and it rolls back together on failure.
    run_id = provenance.open_run(conn, source=SOURCE, upstream_release=upstream_release,
                                 source_checksum=source_checksum, writer=WRITER,
                                 clock=clock)

    try:
        # 3. The resolution index. Read ONCE, whole -- moieties_by_display_name is
        #    bounded by the registry, not by this feed, exactly as classes.py's own
        #    docstring argues for MED-RT and MeSH.
        fold = _fold_by_lower(classes.moieties_by_display_name(conn))

        # 4. Clear this source's previous rows before writing this run's. TWO
        #    clears, because FDA-CYP owns two kinds of table: the edges
        #    (class_membership; class_parent, though this slice writes none) via
        #    classes.clear_source_edges, and the assertion projection via
        #    db.clear_source_tables directly (fda_cyp_assertion has no dedicated
        #    wrapper of its own, unlike class_membership's). Both are scoped through
        #    ingest_run.source, so an unrelated source's rows are untouched, and
        #    both run BEFORE any row is written under the run just opened -- which
        #    is what stops this run's own (still-empty) rows from being swept up.
        classes.clear_source_edges(conn, SOURCE)
        db.clear_source_tables(conn, FDA_CYP_TABLES, SOURCE)

        # Every (system, pathway, role, potency) seen this run mints or refreshes
        # its class exactly once; class_uuid is a pure function of the key, so this
        # cache only saves a round trip, never changes the identity that comes back.
        class_cache: dict[tuple[str, str, str, str | None], uuid.UUID] = {}

        classes_added = 0
        memberships_written = 0
        withheld_qualified = 0
        unresolved_substances = 0
        combination_regimens = 0
        non_drug_entities = 0

        for t in tuples:
            key = (t.system, t.pathway, t.role, t.potency)
            class_uuid = class_cache.get(key)
            if class_uuid is None:
                concept = classes.ClassConcept(
                    nui=source_code(t.system, t.pathway, t.role, t.potency),
                    # published_code is deliberately None, not source_code(...) again:
                    # FDA publishes no code for these classes (design section 4.2),
                    # and writing the same string into "the code as published" would
                    # be a manufactured fact in a provenance field. ClassConcept.code
                    # is typed `str` for its other callers (MED-RT, MeSH both publish
                    # a real code); substance_class.published_code itself is
                    # nullable, and this is the first source for which None is the
                    # honest value.
                    code=None,
                    name=class_name(t.system, t.pathway, t.role, t.potency),
                    concept_type=CONCEPT_TYPE)
                class_uuid, is_new = classes.upsert_class(conn, concept, run_id, SOURCE)
                class_cache[key] = class_uuid
                if is_new:
                    classes_added += 1

            # Resolution is exact and case-insensitive. `single` is None
            # for zero matches AND for more than one -- ambiguity is unresolved,
            # never "pick the first", because a name resolving to several moieties
            # is real information (the registry grew and now genuinely disagrees
            # with itself about this name) that silently picking one would erase.
            candidates = fold.get(t.substance.lower(), [])
            single = candidates[0] if len(candidates) == 1 else None

            disposition, resolved_moiety = _classify(
                t.substance, single, t.footnote_markers)
            # CHECKED BEFORE THE INSERT, not after: this module's contract is that
            # it refuses to write rather than writing something it cannot account
            # for. Today db/039's CHECK would also catch an unknown value, but it
            # catches it AFTER the row is built and only while the vocabulary
            # stays five wide -- and db/041's header calls widening it "a real,
            # foreseeable event". The moment the CHECK is widened, the counting
            # chain below would bank an uncounted row: no counter sums to
            # assertions_written anywhere, so the summary would silently stop
            # adding up. This is db/041's negative-predicate lesson applied to the
            # Python beside it.
            if disposition not in DISPOSITIONS:
                raise FdaCypDispositionError(
                    f"row {t.row_ordinal} ({t.raw_substance!r}) has disposition "
                    f"{disposition!r}, which this orchestrator counts into no "
                    f"summary field. The closed set is {sorted(DISPOSITIONS)}. "
                    "Widening it means widening the counts, db/039's CHECK and "
                    "questions.py's CASE together.")

            # registry_near_name is left NULL throughout this slice. Section 7.1
            # describes it as curator evidence from "a stated, mechanical prefix
            # rule", but no such rule is part of this task's interface, and
            # inventing one here risks exactly the DrugCentral defect the design
            # warns about (a prefix match that "found" glycerol 1,3-dimethacrylate
            # for glycerol -- a different substance). Leaving it NULL is the safe
            # default until a rule is specified and reviewed on its own; the column
            # exists so a future round can populate it without a migration.
            conn.execute(
                "INSERT INTO drugref.fda_cyp_assertion "
                "(ingest_run, source, row_ordinal, raw_substance, "
                " resolved_moiety_uuid, column_heading, raw_cell, system, "
                " pathway, role, potency, class_uuid, footnote_markers, "
                " footnote_text, registry_near_name, disposition, "
                " substance, row_footnote_markers, cell_footnote_markers) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "        %s, %s, %s, %s, %s, %s)",
                (run_id, SOURCE, t.row_ordinal, t.raw_substance, resolved_moiety,
                 t.column_heading, t.raw_cell, t.system, t.pathway, t.role, t.potency,
                 class_uuid, t.footnote_markers,
                 _footnote_text(t.footnote_markers, footnote_by_marker),
                 None, disposition,
                 # db/042: the clean name and the row-vs-cell footnote split
                 # fda_cyp.CypTuple already computed, which db/039's INSERT never
                 # stored -- see db/042's header for why this column ships
                 # nullable rather than backfilled by a second, SQL-side
                 # reimplementation of split_footnotes.
                 t.substance, t.row_footnote_markers, t.cell_footnote_markers))

            if disposition == "member":
                # EVERY path on which `single` is None returns a non-member
                # disposition before this one is reached, so a "member" without a
                # moiety is unreachable. Checked rather than assumed: a future edit
                # that breaks it should fail here, legibly, instead of sending a
                # NULL moiety_uuid into add_membership's SQL to fail there with a
                # less obvious error. `raise`, not `assert` -- python -O strips
                # asserts, and a guard that disappears under a flag is exactly the
                # less-obvious failure this one exists to prevent.
                if resolved_moiety is None:
                    raise FdaCypDispositionError(
                        f"row {t.row_ordinal} ({t.raw_substance!r}) classified "
                        "'member' with no resolved moiety -- _classify's ordering "
                        "invariant is broken.")
                if classes.add_membership(conn, resolved_moiety, class_uuid,
                                          RELATIONSHIP, run_id):
                    memberships_written += 1
            elif disposition == "withheld_qualified":
                withheld_qualified += 1
            elif disposition == "unresolved_substance":
                unresolved_substances += 1
            elif disposition == "combination_regimen":
                combination_regimens += 1
            else:
                # Reachable only for 'non_drug_entity': the DISPOSITIONS guard
                # above has already rejected anything outside the closed set, so
                # this arm needs no condition and cannot silently swallow a sixth
                # value the way a bare `elif` chain with no else did.
                non_drug_entities += 1

        # 5. Rebuild the question register. Called unconditionally, like every other
        #    orchestrator here: register_from_gaps refreshes EVERY currently-open
        #    gap kind's last_derived_ingest, not only the ones this ingest touches
        #    (gsrs_run's own test_gsrs_run.py docstring records the same point for
        #    gap_unclassified_moiety). 'fda_cyp_unadjudicated' IS in
        #    questions._GAP_SOURCES as of this slice, so this call derives THIS
        #    source's questions too -- and it would still have to run if it did
        #    not, to keep every other source's open questions current.
        register_counts = questions.register_from_gaps(conn, run_id)

        # 6. Finish and commit. finish_run does NOT commit on its own (see its
        #    docstring on why symmetry with open_run would be a bug), so this run's
        #    "finished" stamp and everything it wrote land in one atomic commit.
        provenance.finish_run(conn, run_id)
        conn.commit()
    except Exception:
        # THE ONLY ORCHESTRATOR HERE THAT LACKED THIS, and the omission had two
        # costs. A programmatic caller was left holding a connection in an
        # aborted transaction (the rollback happened only incidentally, at
        # db.connect's context manager in the CLI), and nothing recorded WHICH
        # source or release failed -- so a CheckViolation surfaced as a bare
        # psycopg traceback naming neither FDA-CYP nor the page it came from.
        # onchigh_run's tail is the pattern this now matches.
        conn.rollback()
        log.exception("FDA-CYP ingest failed for release %s (%s); rolled back",
                      upstream_release, page_path)
        raise


    summary = FdaCypSummary(
        upstream_release=upstream_release,
        classes_in_release=len(class_cache),
        classes_added=classes_added,
        memberships_written=memberships_written,
        assertions_written=len(tuples),
        withheld_qualified=withheld_qualified,
        unresolved_substances=unresolved_substances,
        combination_regimens=combination_regimens,
        non_drug_entities=non_drug_entities,
        # Subscript, not .get(..., 0): register_from_gaps always sets this key,
        # so a default could only ever mask a wiring regression -- and it would
        # mask it as `questions_registered=0`, which is indistinguishable from
        # the honest "no open gaps".
        questions_registered=register_counts["fda_cyp_unadjudicated"])
    log.info("FDA-CYP ingest: %s", summary)
    return summary

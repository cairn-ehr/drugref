# src/drugref/ingest/fda_cyp_disposition.py
"""What each parsed FDA-CYP tuple BECOMES: its disposition, prose and class key.

Split out of fda_cyp_run.py (issue 177) so the writer holds only the run: the
transaction, the clears, the INSERTs and the counts. Everything here is a PURE
decision about one tuple -- no database, no I/O -- which is why it can live
outside the orchestrator without breaking the "orchestrators are the only
writers" invariant:

* _classify and the vocabulary it reads (NON_DRUG_ENTITIES, the combination
  word, DISPOSITIONS): which of the five dispositions a tuple lands in;
* _footnote_text and UNDEFINED_MARKERS: FDA's own footnote prose for a
  qualified cell, read from THIS page's parse_footnotes result;
* source_code and class_name: the key a class_uuid is minted from, and the
  display name cached beside it;
* _fold_by_lower: the case-folded resolution index _classify's `single` comes
  from.

fda_cyp_run imports these by name. Tests that monkeypatch `_classify` patch it
on fda_cyp_run, because that is the name the writer's loop actually calls.
"""
import re
import uuid

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

class FdaCypFootnoteError(Exception):
    """A qualified cell's markers have no text on a page whose Footnotes read fine.

    A plain Exception, like fda_cyp_run.FdaCypDispositionError and unlike
    fda_cyp_run.FdaCypShrinkError: the Footnotes section parsed, so this is
    drugref and FDA disagreeing about what a marker IS, and the traceback is
    the useful output.
    """


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
    earlier round of fda_cyp_run kept a hand-copied dict of FDA's footnote
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

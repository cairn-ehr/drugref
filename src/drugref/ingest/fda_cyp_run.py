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
# signature and the sibling constants in classes.py and gsrs_run.py.
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
# FDA's own statement. Matched case-insensitively against the footnote-stripped
# name; the apostrophe in "St. John's wort" is U+2019 as FDA prints it.
NON_DRUG_ENTITIES = frozenset({
    "st. john’s wort", "curcumin", "diosmin", "tobacco (smoking)", "grapefruit juice",
})

# A COMBINATION REGIMEN is FDA reporting a role for several substances taken
# together ("atazanavir and ritonavir"), never for one. The word "and" as a
# whole word -- \b on both sides -- is the signal: measured against every one of
# the 244 distinct substance names on the real page, it is true for exactly the
# nine combination rows and false for every single-substance name, including
# ones that CONTAIN the letters "and" as a substring ("vandetanib" has no word
# boundary before or after "and", so \band\b correctly leaves it alone). A
# plain substring test would not have that property.
_COMBINATION_WORD = re.compile(r"\band\b", re.I)

log = logging.getLogger(__name__)


# FDA'S OWN FOOTNOTE PROSE, quoted verbatim from the same live page whose
# SHA-256 the design spec verified (design section 2). This is hardcoded for the
# same reason NON_DRUG_ENTITIES above is: the footnotes live in a page section
# ("<h2>Footnotes</h2>", a flat <p><sup>N</sup>text</p> list) that sits OUTSIDE
# table 1, so fda_cyp.py's parser -- which the fixture policy (spec section 10)
# deliberately keeps to "the data table, verbatim" -- never sees it and has no
# reason to. Quoting it here, once, keyed by FDA's own marker, is the same
# treatment as the non-drug sentence: a fact this slice needs that can only come
# from FDA's prose, not from the matrix.
#
# Twenty-one markers, 1-21, matching every marker the real page's 419 tuples
# carry (measured directly against the shipped parser). Marker 'b' -- cenobamate's
# lone lettered marker (design section 2.3, "a second namespace") -- has NO
# entry here on purpose: the live page's own Footnotes list runs 1-21 and never
# defines a letter. That is not a gap this ingest can fill without inventing
# text FDA never wrote, so `_footnote_text` below simply skips an unknown
# marker rather than raising -- and it is safe to skip, because 'b' never
# appears alone: it is always paired with cenobamate's numbered marker '4' on
# the same row (verified against the real page), so the row's footnote_text is
# never empty.
FOOTNOTE_TEXT: dict[str, str] = {
    "1": "These drugs are active moieties of their corresponding pro-drugs, "
         "adefovir dipivoxil, oseltamivir, tenofovir alafenamide fumarate (TAF), "
         "and tenofovir disoproxil fumarate (TDF). Those pro-drugs are "
         "substrates of P-gp.",
    "2": "Bupropion itself is not a sensitive substrate. It is metabolized by "
         "multiple enzymes including CYP2B6 that is only responsible for the "
         "formation of hydroxybupropion, an active metabolite. Thus, the "
         "considerations of drug interactions with CYP2B6 modulators should "
         "take into account plasma concentration changes of both buproprion "
         "and hydroxybupropion.",
    "3": "Listed based on pharmacogenetic studies.",
    "4": "The classification is based on 200 mg daily dose. The effect "
         "potentially could be stronger at 400 mg/day.",
    "5": "The classification is based on studies conducted with intravenously "
         "administered conivaptan.",
    "6": "Usually administered to patients in combination with ritonavir, a "
         "strong CYP3A inhibitor.",
    "7": "Diltiazem increased AUC of certain sensitive CYP3A substrates (e.g., "
         "buspirone) more than 5-fold.",
    "8": "Fluvoxamine increased the AUC of certain sensitive CYP3A substrates "
         "more than 2-fold (e.g., increased the AUC of buspirone 2.35-fold)",
    "9": "The effect of grapefruit juice varies widely among brands and is "
         "concentration-, dose-, and preparation-dependent. Studies have shown "
         "that it can be classified as a “strong CYP3A inhibitor” "
         "when a certain preparation was used (e.g., high dose, double "
         "strength) or more commonly as a “moderate CYP3A inhibitor” "
         "when another preparation was used (e.g., low dose, single strength).",
    "10": "Based on PBPK simulation",
    "11": "S-lansoprazole is a sensitive substrate in CYP2C19 extensive "
          "metabolizer subjects.",
    "12": "Based on effect of 200 mg/day modafinil. A higher dosage (400 "
          "mg/day) modafinil had larger induction effect on CYP3A.",
    "13": "Single dose",
    "14": "Ritonavir is approved for use in combination with other anti-HIV or "
          "anti-HCV drugs. Caution should be used when extrapolating the "
          "observed effect of ritonavir alone to the effect of anti-HIV or "
          "anti-HCV combination regimens on CYP3A activities.",
    "15": "Moderate inducer of CYP1A2 with dosage of 800 mg/day ritonavir (not "
          "with other anti-HIV drugs). Effect on CYP1A2 at lower dosages of "
          "ritonavir is unknown.",
    "16": "Weak inducer of CYP2B6, CYP2C9, and CYP2C19. Classification is "
          "based on studies conducted with ritonavir itself (not with other "
          "anti-HIV drugs) at dosages of 100-200 mg/day, although larger "
          "effects have been reported in literature for high dosages of "
          "ritonavir.",
    "17": "Intravenously administered rolapitant does not inhibit BCRP and "
          "P-gp.",
    "18": "The effect of St. John’s wort varies widely and is preparation "
          "dependent.",
    "19": "S-warfarin",
    "20": "Ciprofloxacin is generally classified a moderate CYP 1A2 inhibitor "
          "based on totality of evidence; however, it can sometimes behave "
          "like a strong inhibitor (i.e., increase AUC more than 5-fold) when "
          "it interacts with certain CYP 1A2 substrates that are considered "
          "highly sensitive (e.g., tizanidine).",
    "21": "Selexipag is a prodrug. it is the selexipag active metabolite "
          "ACT-333679 that is a sensitive substrate of CYP2C8. Selexipag and "
          "ACT-333679 are also substrates of OATP1B transporter",
}


def _footnote_text(markers: str | None) -> str | None:
    """FDA's own prose for a tuple's footnote_markers ('14, 15, 16' -> joined text).

    Returns None only when `markers` itself is None -- an unqualified cell has
    nothing to explain. For a qualified cell this looks up EVERY marker in the
    comma-separated list and joins whatever text is on file, silently skipping a
    marker with no known definition (see FOOTNOTE_TEXT's docstring on marker
    'b') rather than raising: an undefined marker is a page oddity to record
    evidence about, not a reason to abort an otherwise-good row. It can only
    return None for a qualified cell if NONE of its markers are on file, which
    has not happened on any release measured so far.
    """
    if markers is None:
        return None
    found = [FOOTNOTE_TEXT[marker] for marker in
             (part.strip() for part in markers.split(","))
             if marker in FOOTNOTE_TEXT]
    return " / ".join(found) if found else None


@dataclasses.dataclass(frozen=True)
class FdaCypSummary:
    """What one FDA-CYP run did -- returned so a caller or test can assert on it.

    EVERY FIELD IS NAMED FOR WHAT IT ACTUALLY COUNTS. `assertions_written` is
    every tuple parsed; `memberships_written` is the subset promoted, and the two
    differ by exactly the withheld and unresolved populations. A summary line the
    CLI prints is the number a reader quotes later, so a name that overstates its
    scope is a wrong number with a plausible source.

    `memberships_written` is MEASURED, never derived from the others: the
    withheld and unresolved (and non-drug, and combination) exclusions overlap
    -- grapefruit juice is both non-drug and footnoted -- so there is no safe
    arithmetic that reconstructs it from the other counts (design section 11).

    `questions_registered` reads the 'fda_cyp_unadjudicated' bucket of
    questions.register_from_gaps's return value. Task 7 is what wires that gap
    kind into questions.py's _GAP_SOURCES; until it does, this reads 0 on every
    run -- an honest consequence of running before that wiring exists, not a bug
    in this module.
    """
    upstream_release: str
    classes_minted: int
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
    if it holds more than one moiety (ruling 3: ambiguity is unresolved, not
    "pick the first"). Nothing collides under lower() on the registry measured
    for this design, but the registry grows, and this is what keeps that true
    without anyone having to re-derive it by hand later.
    """
    folded: dict[str, list[uuid.UUID]] = {}
    for name, moieties in by_display_name.items():
        folded.setdefault(name.lower(), []).extend(moieties)
    return folded


def _classify(substance: str, single: uuid.UUID | None,
             footnote_markers: str | None) -> tuple[str, uuid.UUID | None]:
    """One tuple's disposition and resolved moiety, in the FIXED order ruling 2
    sets: non_drug_entity -> combination_regimen -> unresolved_substance ->
    withheld_qualified -> member.

    The order exists BECAUSE the categories overlap, not despite it:
    grapefruit juice is both one of FDA's own pinned five non-drugs (section 7.2)
    AND footnoted (marker 9), so a disposition function that checked footnote
    status first would misfile it as withheld_qualified -- a real category, but
    the wrong one, and silently so, since both are valid CHECK values. Checking
    non_drug_entity first is what keeps grapefruit juice non_drug_entity
    regardless of what else is true of the row (pinned directly by
    test_grapefruit_juice_is_non_drug_entity_even_though_it_is_footnoted).

    `single` is the ALREADY-RESOLVED moiety (or None), computed once by the
    caller from the case-folded index -- so this function only decides WHERE
    that resolution result lands, never how it was computed. The resolution
    itself and the non_drug_entity/combination_regimen check are independent
    (design section 7's inversion): curcumin resolves to a real moiety AND is
    still non_drug_entity, so `single` is threaded through unchanged for that
    branch. combination_regimen forces None regardless of what `single` says --
    FDA reports the role for the REGIMEN, and assigning it to any one
    component (even an accidental exact-name match) is an inference FDA did not
    make.
    """
    if substance.lower() in NON_DRUG_ENTITIES:
        return "non_drug_entity", single
    if _COMBINATION_WORD.search(substance):
        return "combination_regimen", None
    if single is None:
        return "unresolved_substance", None
    if footnote_markers:
        return "withheld_qualified", single
    return "member", single


def ingest_fda_cyp(conn: psycopg.Connection, *, page_path: str | pathlib.Path,
                   upstream_release: str | None = None) -> FdaCypSummary:
    """Ingest one FDA-CYP page: parse, resolve, clear, write, rebuild questions.

    `upstream_release`, if given, OVERRIDES fda_cyp.parse_release -- the escape
    hatch every test in this module uses, since the parser test fixture carries
    only table 1 and no dateModified stamp to parse. A real ingest omits it and
    lets the page's own stamp govern (design section 13): fetch time is never a
    substitute, because it records when drugref looked rather than when FDA
    changed the content.
    """
    # 1. PARSE FIRST, before any run row exists, so a crash here -- an unknown
    #    pathway token, a ragged row, a missing dateModified -- leaves no trace
    #    to explain (gsrs_run's ordering, restated here for the same reason).
    page = pathlib.Path(page_path).read_text(encoding="utf-8")
    tuples = fda_cyp.parse_table(page)
    if upstream_release is None:
        upstream_release = fda_cyp.parse_release(page)
    source_checksum = checksum(page_path)

    # 2. Open the run. COMMITS in its own transaction (provenance.open_run) --
    #    everything from here is the work, and it rolls back together on failure.
    run_id = provenance.open_run(conn, source=SOURCE, upstream_release=upstream_release,
                                 source_checksum=source_checksum, writer=WRITER)

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
            class_uuid, _is_new = classes.upsert_class(conn, concept, run_id, SOURCE)
            class_cache[key] = class_uuid

        # Resolution is exact and case-insensitive (ruling 3). `single` is None
        # for zero matches AND for more than one -- ambiguity is unresolved,
        # never "pick the first", because a name resolving to several moieties
        # is real information (the registry grew and now genuinely disagrees
        # with itself about this name) that silently picking one would erase.
        candidates = fold.get(t.substance.lower(), [])
        single = candidates[0] if len(candidates) == 1 else None

        disposition, resolved_moiety = _classify(
            t.substance, single, t.footnote_markers)

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
            "(ingest_run, source, row_ordinal, raw_substance, resolved_moiety_uuid, "
            " column_heading, raw_cell, system, pathway, role, potency, class_uuid, "
            " footnote_markers, footnote_text, registry_near_name, disposition) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, SOURCE, t.row_ordinal, t.raw_substance, resolved_moiety,
             t.column_heading, t.raw_cell, t.system, t.pathway, t.role, t.potency,
             class_uuid, t.footnote_markers, _footnote_text(t.footnote_markers),
             None, disposition))

        if disposition == "member":
            # _classify only ever returns "member" alongside a real moiety --
            # the "unresolved_substance" branch it falls through from is the
            # only place `single` can be None, and that branch returns before
            # this one is reached. Asserted rather than left implicit: if that
            # invariant is ever broken by a future edit, this turns it into an
            # immediate, legible failure instead of a NULL moiety_uuid reaching
            # add_membership's SQL and failing there with a less obvious error.
            assert resolved_moiety is not None
            if classes.add_membership(conn, resolved_moiety, class_uuid,
                                      RELATIONSHIP, run_id):
                memberships_written += 1
        elif disposition == "withheld_qualified":
            withheld_qualified += 1
        elif disposition == "unresolved_substance":
            unresolved_substances += 1
        elif disposition == "combination_regimen":
            combination_regimens += 1
        elif disposition == "non_drug_entity":
            non_drug_entities += 1

    # 5. Rebuild the question register. Called unconditionally, like every other
    #    orchestrator here: register_from_gaps refreshes EVERY currently-open
    #    gap kind's last_derived_ingest, not only the ones this ingest touches
    #    (gsrs_run's own test_gsrs_run.py docstring records the same point for
    #    gap_unclassified_moiety). Until Task 7 adds 'fda_cyp_unadjudicated' to
    #    questions._GAP_SOURCES, this derives nothing new for THIS source, but
    #    still must run so every other source's open questions stay current.
    register_counts = questions.register_from_gaps(conn, run_id)

    # 6. Finish and commit. finish_run does NOT commit on its own (see its
    #    docstring on why symmetry with open_run would be a bug), so this run's
    #    "finished" stamp and everything it wrote land in one atomic commit.
    provenance.finish_run(conn, run_id)
    conn.commit()

    summary = FdaCypSummary(
        upstream_release=upstream_release,
        classes_minted=len(class_cache),
        memberships_written=memberships_written,
        assertions_written=len(tuples),
        withheld_qualified=withheld_qualified,
        unresolved_substances=unresolved_substances,
        combination_regimens=combination_regimens,
        non_drug_entities=non_drug_entities,
        questions_registered=register_counts.get("fda_cyp_unadjudicated", 0))
    log.info("FDA-CYP ingest: %s", summary)
    return summary

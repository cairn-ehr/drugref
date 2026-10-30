# src/drugref/gap_sources_interactions.py
"""Gap kinds from slice 5c on: curated pairs and the interaction sources' endpoints.

The second half of `questions._GAP_SOURCES` -- see `gap_sources_registry.py`, the
first half, for what these dicts are and why they live outside questions.py
(issue 177). EVERY gap_key FORMAT HERE IS FROZEN.
"""

INTERACTION_GAP_SOURCES = {
    # Slice 5c.1. The two kinds whose answer is a CURATED ROW rather than a lookup --
    # like unreviewed_expansion_root, drugref answers these itself.
    #
    # THE gap_key FORMATS BELOW ARE FROZEN. question_uuid is uuid5(gap_kind, gap_key),
    # immortal and externally citable, so changing either re-mints every question and
    # breaks every reference an external tool holds.
    "uncurated_condition_contradiction": {
        "view": "gap_uncurated_condition_contradiction",
        # Compound, on Plan C's CLASS:a/CLASS:b precedent: the question is about the
        # PAIR, and folding it onto either half would hand two independent questions
        # one immortal UUID.
        "key_sql": "'MOIETY:' || subject_moiety || '/CONDITION:' || object_condition",
        "text_sql": (
            "'Is ' || display_name || ' indicated or contraindicated in ' || "
            "condition_name || '? The release asserts BOTH, with no qualifier "
            "distinguishing them -- often because one MeSH descriptor covers "
            "clinical states in which the answers differ.'"),
    },
    "uncurated_interaction_rule": {
        "view": "gap_uncurated_interaction_rule",
        "key_sql": ("'MOIETY:' || subject_moiety || '/CLASS:' || object_class || "
                    "'/CI_AXIS:' || relationship"),
        "text_sql": (
            "'How severe is co-administering ' || display_name || ' with a drug of ' "
            "|| class_name || ', by what mechanism, and what should a prescriber do? "
            "The release asserts the contraindication and grades nothing. ' || "
            "pair_count || ' drug pair(s) inherit the answer.'"),
    },
    # Slice 5c.2, db/031. The ONC high-priority list's own worklist: a pair
    # endpoint (subject OR object) naming a well-formed identifier drugref does
    # not hold at all -- one pipeline stage EARLIER than the two kinds above,
    # which ask about a rule drugref already holds but has not graded. This
    # one asks "should drugref hold this identity at all?", a coverage
    # question in unmatched_ingredient's / unresolved_ci_object's family, not
    # a curation one.
    #
    # THE gap_key FORMAT BELOW IS FROZEN -- question_uuid is uuid5(gap_kind,
    # gap_key), immortal and externally citable, and onchigh_run.OBJECT_SCHEME
    # already lost one round to this exact key (spelling MED-RT's scheme
    # 'MEDRT' would have baked the wrong spelling in forever). A later
    # reformat orphans every question already registered under the old key.
    #
    # THE KEY CARRIES endpoint_role BECAUSE THE VIEW'S GRAIN DOES. db/031's own
    # COMMENT on gap_unresolved_onc_endpoint states the rule: the view is
    # grouped on (source, entry_id, endpoint_role) and that is "the grain a
    # gap_key built from this view must also use (db/017's lesson: a coarser
    # grouping folds two independently-failing endpoints into one question)".
    # Omitting the role was invisible while every entry had a moiety subject --
    # the two roles then carry different schemes ('UNII' vs 'MED-RT') and so
    # differ anyway -- but a CLASS subject records OBJECT_SCHEME on BOTH roles
    # (onchigh_resolve.OBJECT_SCHEME's comment), so a class SELF-PAIR, which
    # db/032's DECISION 2 deliberately permits for the ONC list's real
    # QT-prolonging x QT-prolonging entry, made both roles collide onto one
    # question_uuid. register_from_gaps' upsert then silently overwrote one role's text
    # with the other's, and closing either role retired the question for both.
    "unresolved_onc_endpoint": {
        "view": "gap_unresolved_onc_endpoint",
        "key_sql": ("'ONCHIGH:' || entry_id || ':' || endpoint_role || ':' || "
                    "identifier_scheme || ':' || identifier_value"),
        "text_sql": (
            "'Does drugref hold ' || coalesce(endpoint_name, identifier_value) || "
            "' (' || identifier_scheme || ' ' || identifier_value || ')? The ONC "
            "high-priority list names it as the ' || endpoint_role || ' of entry ' || "
            "entry_id || ', and no drugref identity resolves it, so that "
            "interaction cannot be projected at all.'"),
    },
    # db/035, gap kind 16. THE CLASS GRAIN'S PRIMARY QUESTION, and the grain shipped
    # without it: db/032-db/034 built the class x class write path and db/031 gave it a
    # kind for the LESSER failure (an endpoint resolving to nothing), while "these
    # class x class rules are ungraded" reached nobody. An operator could see
    # `class_rules_written=N`, never run the deliberately-separate `drugref curate`,
    # and leave every ONC high-priority class rule permanently uncurated with
    # question_worklist showing nothing to do.
    #
    # THE FIGURE IS SEVEN, AND THIS COMMENT SAID NINE until the review of PR #119.
    # `class_rules_written=9` was issue 96's failure-scenario PROSE, restated here and
    # in db/035's COMMENT as though it were measured. It never was: issue 94 withheld
    # the class x class ONC entries pending literature research -- there are SEVEN
    # (onc_high_priority.toml, an eleven-entry draft = 4 moiety + 7 class). db/038 § 3
    # corrected the catalog; this is the Python side of the same figure. Spelled `N`
    # rather than `7` on purpose -- the count is a property of the seed file, and
    # nothing in this argument depends on which number it is, which is precisely the
    # dependency that made a quoted figure outlive the issue it came from.
    #
    # THE gap_key FORMAT IS FROZEN, like every other here, and carries the rule's WHOLE
    # natural key -- both classes AND the axis. Omitting the axis would fold two rules
    # over one class pair onto ONE immortal question_uuid, which is precisely the
    # defect the 5c.2 review found in unresolved_onc_endpoint's own key (it omitted
    # endpoint_role) and the one db/017 was re-issued for. `CI_AXIS:` rather than
    # `AXIS:` because uncurated_interaction_rule above already spells it that way: one
    # convention, not two that differ by one word.
    #
    # The view is GROUPED WITHOUT `source` so this key's grain matches it -- see
    # db/035 section 3, which explains why a per-source grain would upsert two rows
    # onto one question_uuid and silently overwrite one text with the other.
    "uncurated_class_interaction_rule": {
        "view": "gap_uncurated_class_interaction_rule",
        "key_sql": ("'CLASS:' || subject_class || '/CLASS:' || object_class || "
                    "'/CI_AXIS:' || relationship"),
        # Names the fan-out, as its moiety-grain sibling does, because the answer is a
        # judgement whose cost is the number of pairs that inherit it -- and says "up
        # to", since max_pair_count is an upper bound (the read path excludes a drug
        # pairing with itself, which the product cannot see).
        "text_sql": (
            "'How severe is co-administering a drug of ' || subject_class_name || "
            "' with a drug of ' || object_class_name || ', by what mechanism, and "
            "what should a prescriber do? The rule is ingested and graded by nobody, "
            "and up to ' || max_pair_count || ' drug pair(s) inherit the answer. "
            "Grading it `applies = false` is a real answer and retires this "
            "question.'"),
    },
    # Slice 5c.2g. Four dispositions reach this view TODAY and they are four
    # different questions -- since db/041 made the subject half a NEGATIVE
    # predicate, every non-member disposition reaches it, which is the point:
    # a future sixth arrives here and trips the CASE's missing ELSE loudly
    # rather than being silently filtered out. So the text branches on
    # `disposition` with a CASE rather than
    # asserting one reason for all of them -- issue 122's lesson: a message may not
    # state a cause it has not confirmed.
    #
    # TWO GRAINS, ONE gap_kind, because db/040 corrected db/039's view into a UNION
    # ALL of two correctly-grained halves (db/040's own header has the full measured
    # argument: 71 gap rows minted for 55 facts under db/039's single grouping,
    # issue 41's rule in its FINER direction -- "grouping finer mints two questions
    # for one fact"):
    #   * withheld_qualified is grained per CELL (source, raw_substance,
    #     column_heading, pathway) -- each footnoted cell is its own adjudication,
    #     so column_heading/pathway are part of the fact and belong in the key.
    #   * EVERY OTHER NON-MEMBER disposition (unresolved_substance,
    #     combination_regimen, non_drug_entity today, and whatever else the CHECK
    #     admits later) is grained per SUBJECT (source, raw_substance,
    #     disposition) -- the question is about the NAME, not the cell that
    #     happened to mention it, so the view projects column_heading and pathway
    #     as NULL for this half.
    #
    # db/040 first shipped the subject half as a POSITIVE enumeration of the three
    # known values (`IN ('unresolved_substance', 'combination_regimen',
    # 'non_drug_entity')`), which its own review caught: a sixth disposition
    # would match neither that list nor the cell half's `= 'withheld_qualified'`,
    # so it produced ZERO gap-view rows -- reaching this CASE, and the loud
    # NOT-NULL failure the next paragraph describes, NEVER. db/041 restated the
    # predicate NEGATIVELY (`NOT IN ('member', 'withheld_qualified')`) so an
    # unanticipated disposition still reaches the view, and so still reaches the
    # CASE below, instead of vanishing before either can see it.
    #
    # ⇒ db/042: `substance`, NOT `raw_substance`, IS WHAT gap_key AND question_text
    # QUOTE NOW. db/039's view (and this entry, until db/042) built both from
    # raw_substance -- FDA's PRINTED form, footnote markers and all -- so a
    # question read "Which drugref moiety, if any, is FDA's oseltamivir
    # carboxylate 1?" and its gap_key was
    # 'FDACYP:oseltamivir carboxylate 1||': the trailing '1' is a FOOTNOTE
    # MARKER, not part of the name, reproducing the exact defect that gave this
    # slice its headline case ('ritonavir 14, 15,') in the human-readable
    # output. Worse than cosmetic: question_uuid = uuid5(gap_kind, gap_key) is
    # IMMORTAL and externally citable, so keying on FDA's own footnote
    # NUMBERING means FDA renumbering a footnote changes the identity of every
    # open question about that substance. `substance` is fda_cyp.CypTuple's
    # already-clean name (db/042 adds the column db/039 never stored);
    # `COALESCE(substance, raw_substance)` guards the one honest reason it can
    # still be NULL -- a database that has applied db/042 but not yet re-run
    # `drugref ingest fda-cyp` -- the same NULL-propagation hazard db/040's own
    # header already explains for column_heading/pathway (SQL's `||` returns
    # NULL if ANY operand is NULL). raw_substance is UNCHANGED in the view and
    # kept available for evidence; it is simply no longer what identifies or
    # narrates a question.
    #
    # key_sql below ALSO COALESCEs column_heading/pathway to '' before
    # concatenating, unchanged from db/040/041: for withheld_qualified those two
    # are never NULL, so every one of its gap_keys -- and therefore its
    # question_uuids -- stays BYTE-IDENTICAL to what db/041 minted; only the
    # subject-grain dispositions' keys change (and, within them, only the ones
    # whose raw_substance actually carried a footnote marker or differed from
    # its clean form).
    #
    # key_sql DELIBERATELY OMITS `disposition` even though the subject half now
    # groups by it. That is safe only because `fda_cyp_disposition._classify` decides
    # each of the subject dispositions from `raw_substance` (and, for
    # combination_regimen, its own regex over that same string) ALONE -- never
    # from per-cell data like footnote_markers -- so one raw_substance can never
    # carry two different subject dispositions at once, and omitting disposition
    # from the key cannot fold two distinct facts onto one question_uuid. That
    # invariant is real (checked directly: zero substances straddle two subject
    # dispositions on the real page) but lives in _classify's code, not in any
    # schema constraint, so it is recorded here rather than assumed silently.
    #
    # DO NOT "FIX" THE unresolved_substance / combination_regimen TEXT TO NAME
    # column_heading OR pathway. It would look like an omission next to
    # withheld_qualified's, which does interpolate them -- it is not one. Those
    # two branches never named a cell (they ask about the SUBSTANCE), and now that
    # the view's grain agrees, naming a cell in the text would misstate what the
    # question is actually about: one substance, not one occurrence of it.
    #
    # ⇒ db/042: THE withheld_qualified BRANCH NO LONGER ASSERTS A CELL
    # ATTACHMENT FDA DID NOT MAKE. FDA's footnote markers live in TWO positions
    # -- glued to the substance NAME (a claim about the substance) or attached
    # INSIDE the cell (a claim about that one role/pathway) -- and db/039's
    # single `footnote_markers` merged both, so EVERY withheld cell got the same
    # "Does FDA's footnote on X (column, pathway) narrow or NEGATE the
    # membership its row states?" wording, whether or not the footnote was ever
    # attached to that cell at all. Measured: 30 of the 33 withheld gap rows
    # carry a NAME-level marker ONLY, and 3 carry a cell-level one
    # (ciprofloxacin, conivaptan, and cenobamate -- cenobamate carries BOTH at
    # once, which is exactly why "31 rows carry a row-level marker" and "30
    # rows are name-level only" are different figures, and only the second
    # sizes the two arms of the CASE below) -- bupropion's own withheld
    # question is the
    # example that named this defect (footnote 2 is about CYP2B6 substrate
    # status generally, glued to the name, and says nothing about the specific
    # 2D6-inhibitor cell the old text named). Withholding the membership is
    # still correct for a name-level marker (any footnote on the row is grounds
    # to withhold -- db/039 section 3); asserting the footnote is ABOUT that one
    # cell is not, when it is not. The nested CASE below branches on
    # `cell_footnote_markers` (db/042): NOT NULL means a marker genuinely
    # attaches to THIS cell, and the text says so; NULL means only
    # `row_footnote_markers` is present, and the text asks about "this cell's
    # membership" without claiming the footnote is specifically about it.
    #
    # THE ELSE ARM COALESCEs row_footnote_markers, AND MUST. `_classify` only
    # reaches withheld_qualified when footnote_markers is truthy, so for rows
    # THIS code writes at least one scope column is always populated -- but
    # that says nothing about rows ALREADY ON DISK. db/042 added all three
    # columns nullable with NO backfill (its header argues the case at length),
    # so on a database that has applied db/042 and not yet re-run
    # `drugref ingest fda-cyp`, every pre-existing withheld row has BOTH scope
    # columns NULL. SQL's `||` returns NULL if any operand is NULL, and
    # open_question.question_text is NOT NULL -- so an unguarded concatenation
    # here aborts `register_from_gaps`, which runs at the END OF EVERY INGEST
    # OF EVERY SOURCE, with an error naming neither FDA-CYP nor db/042. That is
    # the same migration window the sibling COALESCE on `substance` three lines
    # up exists for; these two columns shipped under identical terms in the
    # same migration and were missed. footnote_markers is the fallback because
    # db/039 has always populated it for a withheld row; '(unrecorded)' is the
    # last resort rather than a NULL that takes an unrelated feed down.
    #
    # 'FDACYP:' rather than a source-derived prefix because the source is already
    # a fixed literal here ('FDA-CYP' is the only source this view reads), unlike
    # unresolved_ci_object's namespace, which genuinely varies row to row.
    #
    # THE CASE HAS NO ELSE, on unresolved_ci_object's precedent in gap_sources_registry:
    # open_question.question_text is NOT NULL, so a fifth live disposition value
    # (a `member` leak, or a future sixth CHECK value) aborts the ingest loudly
    # instead of a curator reading "one of five substances that are not drugs"
    # about a row that is nothing of the kind -- the exact false-premise failure
    # the CASE branching exists to avoid. An earlier draft of this entry used
    # ELSE for non_drug_entity, which is precisely the defect gap_sources_registry's
    # unresolved_ci_object comment warns against, restated for a second gap kind.
    "fda_cyp_unadjudicated": {
        "view": "gap_fda_cyp_unadjudicated",
        "key_sql": ("'FDACYP:' || COALESCE(substance, raw_substance) || '|' || "
                    "COALESCE(column_heading, '') || '|' || COALESCE(pathway, '')"),
        "text_sql": (
            "CASE disposition "
            "WHEN 'withheld_qualified' THEN "
            "  CASE WHEN cell_footnote_markers IS NOT NULL THEN "
            "    'Does FDA''s footnote (marker(s) ' || cell_footnote_markers || "
            "    ') on the ' || column_heading || '/' || pathway || ' cell for ' || "
            "    COALESCE(substance, raw_substance) || ' narrow or NEGATE the "
            "membership that cell states? Drugref withheld the membership rather "
            "than assert either way. FDA''s note: ' || "
            "    COALESCE(footnote_text, '(not captured)') "
            "  ELSE "
            "    'FDA''s row for ' || COALESCE(substance, raw_substance) || "
            "    ' carries footnote(s) ' || "
            "    COALESCE(row_footnote_markers, footnote_markers, '(unrecorded)') || "
            "    '. Does it "
            "narrow or negate this cell''s membership (' || column_heading || ', ' "
            "|| pathway || ')? Drugref withheld the membership rather than assert "
            "either way. FDA''s note: ' || "
            "    COALESCE(footnote_text, '(not captured)') "
            "  END "
            "WHEN 'unresolved_substance' THEN "
            "  'Which drugref moiety, if any, is FDA''s ' || "
            "  COALESCE(substance, raw_substance) || '? "
            "No moiety''s display name matches it.' || "
            "  COALESCE(' A near name in the registry is ' || registry_near_name || "
            "  ', which is EVIDENCE for a curator, not a resolution.', '') "
            "WHEN 'combination_regimen' THEN "
            "  'FDA reports this role for the REGIMEN ' || "
            "  COALESCE(substance, raw_substance) || '. "
            "Which component, if any, carries it? Drugref does not assign a "
            "regimen''s role to a component.' "
            "WHEN 'non_drug_entity' THEN "
            "  'FDA lists ' || COALESCE(substance, raw_substance) || "
            "  ' as one of five substances that are "
            "not drugs. Should drugref carry it at all, and under what identity?' "
            "END"),
    },
    # Issue 101 (db/049). The endpoint names DrugCentral keys to a structure and
    # drugref does not. NOT a synonym-bridge worklist: the re-measurement showed a
    # display_name -> inchikey -> cas cascade takes resolution from 857 of 924
    # names to 914, so what is left is registry COVERAGE -- a moiety or an
    # identity_claim drugref does not yet hold. The question retires by itself
    # when the claim lands.
    "unresolved_ddi_endpoint": {
        "view": "gap_unresolved_ddi_endpoint",
        # FOLDED in the view, so the key is folded here too. question_uuid is
        # immortal, so 'Phytomenadione' and 'phytomenadione' must be one question.
        "key_sql": "'DRUGCENTRAL:ENDPOINT:' || endpoint_name",
        # ROUTE-AWARE, because the view is deliberately route-AGNOSTIC. It filters
        # on a NULL uuid and never on the route vocabulary (db/049's own comment
        # says why), so it admits `not_a_substance` and `no_structural_key` too --
        # and the old single sentence asserted the `unresolved` story about all of
        # them: "DrugCentral resolves it to a structure with an InChIKey or a CAS
        # number, and no live identity_claim in drugref carries either". For a
        # class name DrugCentral has no struct_id at all, so the first half is
        # false; for a keyless structure the second half is. question_uuid is
        # IMMORTAL and externally cited, so a question minted under the wrong story
        # cannot be quietly reworded later -- the text has to be right the first
        # time. db/050 publishes `route` for exactly this CASE to read.
        "text_sql": (
            "'Which moiety does the interaction endpoint ' || endpoint_name || "
            "' denote? ' || CASE route "
            "  WHEN 'unresolved' THEN "
            "    'DrugCentral resolves it to a structure with an InChIKey or a CAS "
            "number, and no live identity_claim in drugref carries either' "
            "  WHEN 'not_a_substance' THEN "
            "    'DrugCentral itself holds no structure under this text, so it is "
            "most likely a drug CLASS rather than a substance -- the question is "
            "whether drugref should carry it, and under what identity' "
            "  WHEN 'no_structural_key' THEN "
            "    'DrugCentral holds a structure for it but records neither an "
            "InChIKey nor a CAS number, which is what it does for biologics and "
            "mixtures, so there is no structural key to join on' "
            "  WHEN 'missing_keys_row' THEN "
            "    'DrugCentral names a structure that is ABSENT from the structures "
            "extract -- a broken join rather than a hard endpoint, and the extract "
            "should be re-read before this is curated' "
            "  ELSE 'drugref cannot key it (route ' || route || ')' "
            "END || ', so ' || row_count || ' interaction row(s) cannot yield a "
            "pair.'"),
    },
}

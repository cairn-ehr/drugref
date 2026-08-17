"""The ONLY module that writes the open-question registry.

It mirrors classes.py's and interactions.py's single-writer role, and enforces the
split db/007 is built around:

  * `open_question` is DERIVED. register_from_gaps() re-derives it from the gap
    views at the end of every ingest, upserting on the deterministic question_uuid,
    and retires rows whose gap has closed. Nothing a curator owns lives on it.
  * `question_state` / `question_source_check` / `question_evidence` are CURATED.
    They are append-only, keyed off that same immortal UUID, and no rebuild touches
    them -- which is the whole reason state is not a column on open_question.

"Retires" rather than "deletes" because the two halves meet at a cascade: a closed
gap with no curator work is deleted, one that has any is kept with `is_current`
false. See register_from_gaps.

The registry is auto-registering by design (a known gap IS a question; requiring a
promotion step means real gaps sit unregistered because nobody did the paperwork),
so the noise control is `withdrawn`, not a manual allow-list.
"""
import uuid

import psycopg

from drugref import ids, overlay

# Each gap_kind, the view that derives it, and how a row of that view becomes a
# question. Keeping the three together is what stops a view being added without a
# gap_key format -- and gap_key is an INPUT to question_uuid, so an ad-hoc format
# chosen at the call site would mint questions nothing can reconcile later.
#
# `key_sql` must produce the frozen SCHEME:value form; `text_sql` produces the
# literature-searchable statement, which names its subject rather than referring to
# it by UUID so the text is usable as a search expression on its own.
_GAP_SOURCES = {
    "unclassified_moiety": {
        "view": "gap_unclassified_moiety",
        "key_sql": "'MOIETY:' || moiety_uuid",
        "text_sql": (
            "'Which physiologic effects does ' || display_name || "
            "' produce? No has_PE membership is recorded, so it cannot participate "
            "in any effect-accumulation model.'"),
    },
    "unpopulated_contraindication": {
        "view": "gap_unpopulated_contraindication",
        "key_sql": "'CLASS:' || class_uuid",
        "text_sql": (
            "'Which drugs belong to ' || class_name || '? It carries ' || "
            "ci_rule_count || ' contraindication rule(s) but no drug is filed under "
            "it anywhere in its subtree, so those rules can never yield a pair.'"),
    },
    "unmatched_ingredient": {
        "view": "gap_unmatched_ingredient",
        "key_sql": "'RXNORM_IN:' || rxcui",
        # "NAMED upstream", NOT "classified upstream", and the widening is a correction
        # rather than a hedge. Since db/019 the bucket has THREE reasons, and 13 of the
        # 2,150 RxCUIs on this worklist are never classified at all -- 10 reach it only
        # as the subject of an indication rule and 3 only as the subject of a
        # contraindication and an indication. The old text asserted a classification for
        # all 2,150, so those 13 became EXTERNALLY CITABLE questions (question_uuid is
        # immortal) carrying a false premise about the release.
        #
        # DELIBERATELY NOT REASON-SPECIFIC: gap_unmatched_ingredient is DISTINCT ON
        # (rxcui) and does not project `reason`, precisely so its grain matches the
        # question's -- one RxCUI, one question, however many reasons named it. Naming
        # the reason here would mean either widening the view (splitting one question
        # into three, which db/008's DISTINCT ON exists to prevent) or picking one
        # reason arbitrarily. The disjunction is what is actually true of every row.
        "text_sql": (
            "'Does RxCUI ' || rxcui || COALESCE(' (' || name || ')', '') || "
            "' have an active moiety drugref should carry? An upstream release names "
            "it -- as a classified ingredient, or as the subject of a contraindication "
            "or indication rule -- but no moiety in the registry claims it.'"),
    },
    # Plan B. The one kind here that drugref can answer ITSELF -- by recording a
    # decision in class_expansion_policy -- rather than by consulting a source. It
    # shares the CLASS:{uuid} gap_key format with unpopulated_contraindication, and
    # only gap_kind separates the two: a sprawling class nothing is filed under
    # legitimately raises both questions, independently answerable.
    "unreviewed_expansion_root": {
        "view": "gap_unreviewed_expansion_root",
        "key_sql": "'CLASS:' || class_uuid",
        "text_sql": (
            "'Should a contraindication naming ' || class_name || ' expand over its ' "
            "|| descendant_class_count || ' descendant classes, or is the class too "
            "abstract to pair on? ' || ci_rule_count || ' rule(s) ride on the answer. "
            "It expands by default until a decision is recorded in "
            "class_expansion_policy.'"),
    },
    # #31 (db/018). The rules Plan B's deny-list leaves reaching nobody: the object
    # class is denied expansion AND carries no direct member on the rule's axis, while
    # drugs DO sit below it. The third kind drugref can answer ITSELF, by recording a
    # decision -- and it shares the CLASS:{uuid} gap_key with the two other class-level
    # kinds, which only gap_kind separates. That is deliberate and already the
    # established shape: one class can legitimately raise several independently
    # answerable questions, and question_uuid takes gap_kind as an input precisely so
    # they do not collide.
    #
    # The text names BOTH numbers a curator needs to answer it -- how many rules ride
    # on the decision, and how many drugs the deny is holding back -- because the
    # answer is a judgement between them (299 partners for Endocrine Activity
    # Alteration is fan-out, so `allow` is probably wrong; a class holding back three
    # is a different conversation). PARTNERS, not members: the count excludes each
    # rule's own subject (ci_rule_partner_reach), so the number the curator weighs is
    # what allowing expansion would actually reach, never one drug more.
    "dead_by_expansion_policy": {
        "view": "gap_dead_by_expansion_policy",
        "key_sql": "'CLASS:' || class_uuid",
        "text_sql": (
            "'Which drugs belong DIRECTLY to ' || class_name || '? ' || ci_rule_count "
            "|| ' contraindication rule(s) name it, expansion over the ' || "
            "subtree_partner_count || ' drug(s) below it is DENIED in "
            "class_expansion_policy, and no drug it could pair with is filed directly "
            "under it -- so those rule(s) reach nobody. Answer by filing a drug "
            "directly, by revisiting the deny, or by recording that the rule is "
            "unactionable.'"),
    },
    # Slice 5b. CI_ChemClass objects that reached no moiety. The gap_key scheme is
    # {NAMESPACE}:{code} because the subject is an upstream RECORD drugref never
    # registered: it has no drugref UUID to cite, which is exactly why it is a gap.
    #
    # THE NAMESPACE COMES FROM THE DATA (issue #41, with db/017). It was hardcoded
    # 'MESH:' here while the view collapsed its grouping onto object_code alone, so
    # the same one-namespace assumption lived in TWO places and fixing either alone
    # left the other live -- and this is the half no migration can reach. An object
    # code is not namespace-unique in general, and question_uuid is a pure function
    # of (gap_kind, gap_key): a collision here does not merely miscount, it hands two
    # objects ONE immortal question that append-only curator rows then attach to.
    #
    # upper(), not object_source verbatim, and the choice is deliberate: it keeps the
    # frozen SCHEME:value convention every other gap kind uses (MOIETY:, CLASS:,
    # RXNORM_IN:) AND leaves every existing MeSH question_uuid bit-for-bit unchanged
    # (object_source is stored 'MeSH'), so a fix to an externally-citable identifier
    # scheme needed no migration of the identifiers themselves. Pinned by test.
    #
    # db/017 ALSO groups on upper(object_source), and the repetition is deliberate --
    # it is not #41's defect returning. That defect was the namespace VALUE ('MESH')
    # written in two places, where correcting one left the other wrong. This is the
    # same canonicalisation RULE stated twice, where the two cannot disagree: applying
    # upper() to an already-upper string changes nothing. Kept here because gap_key
    # defines a FROZEN, externally-citable identifier scheme and must not depend on a
    # future re-issue of the view remembering to canonicalise -- exactly the way db/016
    # was re-issued as db/017. Both halves are pinned independently, in test_gap_views.
    #
    # TWO KINDS, TWO QUESTIONS, one gap_kind (db/014). Both are objects drugref did
    # not ingest, so both belong on this worklist -- but the remedies are opposites
    # and so the text must be too:
    #   * CHEMICAL_CLASS         -- a policy question drugref answers ITSELF, like
    #                               unreviewed_expansion_root: may this class expand
    #                               over MeSH's structural tree?
    #   * UNREGISTERED_SUBSTANCE -- a COVERAGE question, like unmatched_ingredient:
    #                               the object names a real substance the registry
    #                               does not carry. Asking whether a leaf drug
    #                               descriptor should "expand over the tree" is a
    #                               category error, and asking it was the defect
    #                               db/014's object_kind closed.
    # The CASE has NO ELSE deliberately. open_question.question_text is NOT NULL, so
    # a third object_kind added without its own question aborts the ingest loudly
    # instead of shipping a curator the wrong sentence -- the same force-a-declaration
    # discipline db/014 gave condition_ci_axis.expands_descendants.
    "unresolved_ci_object": {
        "view": "gap_unresolved_ci_object",
        "key_sql": "upper(object_source) || ':' || object_code",
        "text_sql": (
            "CASE object_kind "
            "WHEN 'CHEMICAL_CLASS' THEN "
            "  'Should contraindications naming ' "
            "  || COALESCE(object_name, object_code) "
            "  || ' be expanded to the drugs beneath it in MeSH''s structural tree? ' "
            "  || ci_rule_count || ' upstream rule(s) ride on the answer, and they "
            "are withheld until it is decided -- MeSH structural classes do not map "
            "cleanly onto clinical ones.' "
            "WHEN 'UNREGISTERED_SUBSTANCE' THEN "
            "  'MED-RT contraindicates ' || ci_rule_count || ' drug(s) with ' "
            "  || COALESCE(object_name, object_code) "
            "  || ', a substance drugref registers no moiety for, so those rule(s) "
            "were not ingested. Should it be registered? This is a registry-coverage "
            "gap -- do NOT answer it by expanding anything over MeSH''s tree.' "
            "END"),
    },
    # Slice 5b.2. Diseases nothing in the registry treats, prevents or diagnoses --
    # directly or from above. The gap_key is the REGISTERED-OBJECT form (MOIETY:,
    # CLASS:) rather than unresolved_ci_object's {NAMESPACE}:{code}, and the difference
    # is real: this subject IS registered and has a drugref UUID to cite, whereas that
    # one is an upstream record drugref never registered, which is exactly why it is a
    # gap. The text names the disease AND its MeSH code so the row is usable as a
    # literature search on its own.
    "condition_without_indication": {
        "view": "gap_condition_without_indication",
        "key_sql": "'CONDITION:' || condition_uuid",
        "text_sql": (
            "'Which drugs treat, prevent or diagnose ' || name || ' (MeSH ' || "
            "source_code || ')? No may_treat, may_prevent or may_diagnose assertion "
            "names it or any condition above it in the MeSH tree, so drugref can offer "
            "nothing for a patient coded with it.'"),
    },
    # ---- Plan C: the four curation-dependent kinds --------------------------
    #
    # ALL FOUR ARE QUESTIONS drugref ANSWERS ITSELF, like unreviewed_expansion_root
    # and dead_by_expansion_policy and unlike unmatched_ingredient -- the remedy is a
    # curator decision recorded in additive_effect or effect_contribution, never a
    # source to go and consult. The cost ladder (source_tier) therefore does not order
    # them: there is no cheaper tier to check first.
    "uncurated_additive_effect": {
        "view": "gap_uncurated_additive_effect",
        "key_sql": "'CLASS:' || class_uuid",
        # Names BOTH numbers a curator weighs -- how much upstream attention the class
        # already has, and how many drugs a threshold would range over -- because the
        # answer is a judgement between them, exactly as dead_by_expansion_policy's is.
        "text_sql": (
            "'Does the effect ' || class_name || ' ACCUMULATE across a regimen, and at "
            "what threshold? ' || ci_rule_count || ' contraindication rule(s) name it "
            "and ' || subtree_member_count || ' drug(s) sit at or below it. Answer by "
            "recording an additive_effect row -- including one with accumulates = "
            "false, which is a real answer and retires this question.'"),
    },
    "uncurated_threshold": {
        "view": "gap_uncurated_threshold",
        "key_sql": "'CLASS:' || effect_class_uuid",
        # Shares the CLASS:{uuid} format with uncurated_additive_effect and with the
        # three class-level kinds above; only gap_kind separates them. That is the
        # established shape (one class legitimately raises several independently
        # answerable questions) and question_uuid takes gap_kind as an input precisely
        # so they cannot collide.
        # Quotes the UNGRADED count, which since db/023 is the number the gap gates on
        # -- a curator told "only N graded" could satisfy that by grading classes the
        # effect never reaches, which is exactly the hole db/023 closed. "How many of
        # the drugs this would fire on has nobody looked at" is the actual question.
        "text_sql": (
            "'Which drugs are MAJOR contributors to ' || class_name || '? It fires on "
            "any ' || threshold_total || ' contributor(s) with no major required, and "
            "' || ungraded_member_count || ' of its drug(s) have no grade at all "
            "(across ' || graded_contributor_count || ' promotion(s) that reach it) "
            "-- so it currently fires on members nobody has reviewed. Answer by "
            "grading contributors, or by raising threshold_major.'"),
    },
    "ineffective_contribution": {
        "view": "gap_ineffective_contribution",
        # A COMPOUND KEY, joined with '/' per mint_question_uuid's frozen convention.
        # The gap is about the PAIR: the same contributor class may be a fine promotion
        # for one effect and a no-op for another, and folding them onto one question
        # would hand two unrelated gaps a single immortal UUID.
        "key_sql": ("'CLASS:' || effect_class_uuid || "
                    "'/CLASS:' || contributor_class_uuid"),
        "text_sql": (
            "'Promoting ' || contributor_class_name || ' to ' || magnitude || ' for ' "
            "|| effect_class_name || ' changes nothing: the two classes share no drug. "
            "Was the wrong class named, or did an upstream release move the drugs out "
            "from under one of them?'"),
    },
    "ungraded_contribution": {
        "view": "gap_ungraded_contribution",
        "key_sql": ("'CLASS:' || effect_class_uuid || "
                    "'/CLASS:' || contributor_class_uuid"),
        "text_sql": (
            "'Is ' || contributor_class_name || ' a MAJOR or a minor contributor to ' "
            "|| effect_class_name || '? Its ' || member_count || ' member(s) count as "
            "minor by default because nobody has graded the class. Recording minor "
            "EXPLICITLY is a real answer and retires this question -- it records that "
            "a curator looked.'"),
    },
    # Slice 3, gap kind 12. The read path propagates ONLY the active component, so
    # an unruled composite is reached by nothing -- and for a contraindication,
    # fewer rows is the harm direction. That trade is defensible only because the
    # shortfall is on a worklist rather than hidden, which is this entry.
    #
    # Keyed on the COMPOSITE, which is also the view's grain (#41): grouping more
    # coarsely would fold two gaps onto one immortal question_uuid.
    "unruled_composition_activity": {
        "view": "gap_unruled_composition_activity",
        "key_sql": "'SUBSTANCE:' || substance_unii",
        "text_sql": (
            "'Which component of UNII ' || substance_unii || ' makes it "
            "pharmacologically active? It has ' || component_count || ' registered "
            "component(s) and the release marks none of them active, so no "
            "contraindication or interaction on a component reaches it.'"),
    },
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
    # question_uuid. The upsert below then silently overwrote one role's text
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
    # Slice 5c.2g. FOUR dispositions reach this view and they are four different
    # questions, so the text branches on `disposition` with a CASE rather than
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
    # groups by it. That is safe only because `fda_cyp_run._classify` decides
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
    # attached to that cell at all. Measured: 31 of the 33 withheld gap rows
    # carry a NAME-level marker only -- bupropion's own withheld question is the
    # example that named this defect (footnote 2 is about CYP2B6 substrate
    # status generally, glued to the name, and says nothing about the specific
    # 2D6-inhibitor cell the old text named). Withholding the membership is
    # still correct for a name-level marker (any footnote on the row is grounds
    # to withhold -- db/039 section 3); asserting the footnote is ABOUT that one
    # cell is not, when it is not. The nested CASE below branches on
    # `cell_footnote_markers` (db/042): NOT NULL means a marker genuinely
    # attaches to THIS cell, and the text says so; NULL means only
    # `row_footnote_markers` (never both NULL for a withheld_qualified row,
    # since `_classify` only reaches this disposition when footnote_markers --
    # their merge -- is truthy) is present, and the text asks about "this
    # cell's membership" without claiming the footnote is specifically about
    # it.
    #
    # 'FDACYP:' rather than a source-derived prefix because the source is already
    # a fixed literal here ('FDA-CYP' is the only source this view reads), unlike
    # unresolved_ci_object's namespace, which genuinely varies row to row.
    #
    # THE CASE HAS NO ELSE, on unresolved_ci_object's own precedent above:
    # open_question.question_text is NOT NULL, so a fifth live disposition value
    # (a `member` leak, or a future sixth CHECK value) aborts the ingest loudly
    # instead of a curator reading "one of five substances that are not drugs"
    # about a row that is nothing of the kind -- the exact false-premise failure
    # the CASE branching exists to avoid. An earlier draft of this entry used
    # ELSE for non_drug_entity, which is precisely the defect this file's own
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
            "    ' carries footnote(s) ' || row_footnote_markers || '. Does it "
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
}


def register_from_gaps(conn: psycopg.Connection, ingest_run_id: int) -> dict[str, int]:
    """Re-derive `open_question` from the gap views. Returns rows live per gap_kind.

    Call this at the END of an ingest, after every projection the gap views read has
    been rebuilt and before the commit. Called earlier it reads a half-demolished
    registry -- the orchestrators clear this source's edges, memberships and
    contraindications before re-inserting them -- and would close, then reopen, every
    question those tables feed.

    Idempotent by construction: question_uuid is a pure function of (gap_kind,
    gap_key), so re-running mints the same UUIDs and the upsert refreshes the text
    and `last_derived_ingest` rather than inserting duplicates. `first_derived_ingest`
    is never overwritten -- it is write-once provenance answering "when did drugref
    first notice this".

    A CLOSED GAP LEAVES, BUT NEVER TAKES CURATOR WORK WITH IT. The register tracks
    reality, so a question whose gap has closed is deleted -- one that only ever
    grows is the stale generated document these views exist to replace. But every
    curated table cascades from open_question, and those tables are APPEND-ONLY with
    a trigger that refuses DELETE. So an unconditional delete here does not quietly
    lose a curator's work: the cascade hits forbid_question_state_rewrite (or the
    evidence one), the trigger RAISES, and the whole ingest transaction aborts. The
    first design shipped that, and it was unreachable only while no question had
    ever been withdrawn or cited -- it would have failed on the first ingest after a
    curator touched a gap that later closed.

    So a question carrying any curated row is RETAINED with `is_current` false instead:
    invisible on the worklist, still citable by the external tool that already holds the
    UUID, and restored to current under that same UUID if the gap reopens. Only
    untouched questions are deleted, and those have nothing to cascade to. The guard now
    covers SIX tables, not three: db/029 (slice 5c.1) added curated_interaction and
    curated_condition to question_state's, question_source_check's and
    question_evidence's original three, because curating a pair is exactly what CLOSES
    its gap -- the very row that answers a question is what would otherwise make the
    next ingest try to delete it. db/032 (slice 5c.2) added curated_class_interaction,
    the class grain's own overlay, for exactly the same reason.
    """
    counts: dict[str, int] = {}
    for gap_kind, spec in _GAP_SOURCES.items():
        # The view computes the gap_key and the question text; Python mints the UUID.
        # Deliberately NOT minted in SQL: uuid5 in Postgres would mean a second
        # implementation of a derivation that is frozen forever and that external
        # tools hold references to, and two implementations of one frozen rule is
        # the "two lists in two places" footgun db/006 was written to remove. One
        # implementation, in ids.mint_question_uuid, is the whole point.
        gaps = conn.execute(
            f"SELECT {spec['key_sql']}, {spec['text_sql']} FROM drugref.{spec['view']}"
        ).fetchall()

        live_keys = [gap_key for gap_key, _ in gaps]
        if gaps:
            # executemany, not a Python loop of execute(): gap_unclassified_moiety
            # returns one row per moiety carrying no has_PE membership, which on a
            # full registry is thousands. A per-row round trip there costs more than
            # the rest of the ingest.
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO drugref.open_question "
                    "(question_uuid, gap_kind, gap_key, "
                    "question_text, first_derived_ingest, last_derived_ingest) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (question_uuid) DO UPDATE "
                    "   SET question_text       = EXCLUDED.question_text, "
                    "       last_derived_ingest = EXCLUDED.last_derived_ingest, "
                    # A reopened gap becomes current again under the same UUID.
                    "       is_current          = true",
                    [(ids.mint_question_uuid(gap_kind, gap_key), gap_kind, gap_key,
                      question_text, ingest_run_id, ingest_run_id)
                     for gap_key, question_text in gaps])

        # Whatever this kind derived last time and does not derive now has closed.
        # Drop the ones nobody has touched; keep -- and mark stale -- the rest.
        conn.execute(
            "DELETE FROM drugref.open_question q "
            "WHERE q.gap_kind = %s AND NOT (q.gap_key = ANY(%s)) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.question_state x "
            "                WHERE x.question_uuid = q.question_uuid) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.question_source_check x "
            "                WHERE x.question_uuid = q.question_uuid) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.question_evidence x "
            "                WHERE x.question_uuid = q.question_uuid) "
            # db/029. Curating a pair is exactly what CLOSES its gap, so without these
            # two the very first curated row would make the next ingest delete its
            # question, cascade into an append-only table, RAISE, and abort the whole
            # transaction. The guard -- not the cascade -- is what keeps curator work.
            "AND NOT EXISTS (SELECT 1 FROM drugref.curated_interaction x "
            "                WHERE x.question_uuid = q.question_uuid) "
            "AND NOT EXISTS (SELECT 1 FROM drugref.curated_condition x "
            "                WHERE x.question_uuid = q.question_uuid) "
            # db/032 (slice 5c.2), the class grain. Added with the same hazard shape
            # as the two above -- ON DELETE CASCADE plus an append-only trigger that
            # refuses DELETE -- and so needs the same guard. WHENEVER A TABLE GAINS A
            # question_uuid FK, IT BELONGS IN THIS LIST: the cascade is what makes the
            # omission an aborted ingest rather than a silent data loss, which means
            # it is discovered by every source's ingest failing at once, long after
            # the curated row that caused it was written.
            "AND NOT EXISTS (SELECT 1 FROM drugref.curated_class_interaction x "
            "                WHERE x.question_uuid = q.question_uuid)",
            (gap_kind, live_keys))
        conn.execute(
            "UPDATE drugref.open_question SET is_current = false "
            "WHERE gap_kind = %s AND NOT (gap_key = ANY(%s)) AND is_current",
            (gap_kind, live_keys))
        counts[gap_kind] = len(live_keys)

    return counts


def current_state(conn: psycopg.Connection, question_uuid: uuid.UUID) -> str:
    """The question's live state, defaulting to 'open' when no row has been written.

    Absence meaning `open` is what makes auto-registration affordable: thousands of
    questions can be registered without a single state row, and only a deliberate
    curator action ever writes one.
    """
    row = conn.execute(
        "SELECT state FROM drugref.question_state "
        "WHERE question_uuid = %s AND superseded_by IS NULL",
        (question_uuid,)).fetchone()
    return row[0] if row else "open"


def set_state(conn: psycopg.Connection, question_uuid: uuid.UUID, state: str,
              rationale: str, ingest_run_id: int, source: str = "DRUGREF") -> int:
    """Move a question to `state`, superseding whatever it said before.

    Insert-then-point, in that order, via overlay.supersede -- see overlay.py for why
    the order is forced and why single-live is a DEFERRED trigger rather than a unique
    index. db/007 met that problem here first; db/020 generalised the answer.
    """
    new_id = conn.execute(
        "INSERT INTO drugref.question_state "
        "(question_uuid, state, rationale, source, ingest_run) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING question_state_id",
        (question_uuid, state, rationale, source, ingest_run_id)).fetchone()[0]
    overlay.supersede(conn, "question_state", "question_state_id", new_id,
                      ("question_uuid",), (question_uuid,))
    return new_id


def record_source_check(
        conn: psycopg.Connection, question_uuid: uuid.UUID, source: str,
        source_version: str, outcome: str, note: str | None = None) -> bool:
    """Record that `source` was consulted at `source_version`, with `outcome`.

    Never an overwrite: a re-check against a NEWER version is a new row, which is
    what makes "has this been looked at since the January labels?" answerable. A
    re-check at the same version is a no-op rather than an error, so a re-run of a
    sweep is harmless.

    Recording `not_covered` does NOT close the question -- it is a watermark, and the
    only terminal state is `withdrawn`.
    """
    cur = conn.execute(
        "INSERT INTO drugref.question_source_check "
        "(question_uuid, source, source_version, outcome, note) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (question_uuid, source, source_version, outcome, note))
    return cur.rowcount == 1


def add_evidence(conn: psycopg.Connection, question_uuid: uuid.UUID,
                 reference_scheme: str, reference_value: str, verdict: str,
                 ingest_run_id: int, confidence: str | None = None,
                 source: str = "DRUGREF") -> int:
    """Attach a finding to a question. Append-only; supersede rather than edit.

    Whether the reference actually supports the verdict is a judgement this schema
    RECORDS and does not make.
    """
    return conn.execute(
        "INSERT INTO drugref.question_evidence "
        "(question_uuid, reference_scheme, reference_value, verdict, confidence, "
        " source, ingest_run) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "RETURNING question_evidence_id",
        (question_uuid, reference_scheme, reference_value, verdict, confidence,
         source, ingest_run_id)).fetchone()[0]

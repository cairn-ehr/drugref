-- db/036_class_grain_comment_corrections.sql -- two false claims in db/035's catalog.
--
-- NO SCHEMA CHANGE AT ALL: three COMMENT ON statements and nothing else. db/035 is
-- applied and therefore immutable (the ledger RAISEs on a changed file), so a wrong
-- sentence that shipped INSIDE the catalog can only be corrected by a new migration.
-- That is the whole content of this one.
--
-- WHY CATALOG COMMENTS EARN A MIGRATION OF THEIR OWN, when a wrong comment in a .sql
-- file could wait for the next round. These two ship in `pg_description`: they are what
-- `\d+` prints, which makes them the documentation a DBA reads on a running node with
-- no repository checked out. A wrong sentence there is not a stale note, it is the
-- authoritative answer to the question being asked.

-- ============================================================================
-- 1. the frozen gap_key was documented with the wrong spelling (#96)
-- ============================================================================
-- The view comment said the key is `CLASS:{subject}/CLASS:{object}/AXIS:{relationship}`.
-- It is `CI_AXIS:` -- `questions.py`'s `_GAP_SOURCES` emits `'/CI_AXIS:' || relationship`
-- and tests/test_class_grain_detectors.py pins it literally. The Python comment beside
-- the key even argues the point ("`CI_AXIS:` rather than `AXIS:` because
-- uncurated_interaction_rule above already spells it that way"), so the divergence was
-- in the documentation only, never in the value.
--
-- WHY THIS IS NOT A TYPO. `question_uuid = uuid5(gap_kind, gap_key)` and that key is
-- FROZEN FOREVER and externally citable. Anyone reconstructing a question_uuid from
-- what `\d+` told them computes a DIFFERENT uuid than the one the database holds, and
-- gets no error saying so -- they get a uuid that simply matches nothing, which is the
-- hardest kind of wrong answer to notice. The whole reason this project freezes the key
-- is so an outside citation stays resolvable; a mis-documented key defeats that as
-- thoroughly as a changed one.
COMMENT ON VIEW drugref.gap_uncurated_class_interaction_rule IS
    'CLASS x CLASS contraindication rules carrying no live drugref grade, ranked by '
    'max_pair_count -- the drug pairs at stake in the answer. The class grain''s '
    'PRIMARY question, and it had none until db/035: db/031 added a gap kind for the '
    'lesser one (an endpoint that resolved to nothing) while the grain''s own "these '
    'rules are ungraded" reached nobody, so nine ingested rules could sit permanently '
    'uncurated with question_worklist showing nothing to do. GROUPED WITHOUT `source` '
    'so one rule asserted by two authorities raises ONE question -- its gap_key is '
    'CLASS:{subject}/CLASS:{object}/CI_AXIS:{relationship} and question_uuid is a pure '
    'function of it, so a per-source grain would mint one immortal question and '
    'overwrite its own text. (db/035''s comment spelled that key `AXIS:`; the value was '
    'always `CI_AXIS:`, matching uncurated_interaction_rule one grain over, and db/036 '
    'corrected the sentence rather than the key -- the key is frozen.) A rule reaching '
    'NO pair is omitted (#36: a review gate must only ask what an answer could change) '
    'and is reported to the OPERATOR through class_pair_rule_reach instead, since a '
    'rule reaching nobody is a data fault rather than a clinical question.';

-- ============================================================================
-- 2. `max_pair_count` is NOT exact about zero (#96, #99)
-- ============================================================================
-- db/035 claimed `max_pair_count` "is exact about ZERO, which is the threshold that
-- matters", and defended it by arguing that the read path's self-pair exclusion "cannot
-- change 0 into non-zero". That direction is true and harmless. THE DIRECTION THAT
-- MATTERS IS THE OTHER ONE: the exclusion can change NON-ZERO into zero, and it does.
--
-- `max_pair_count` = subject_effective x object_effective, while `curated_ddi_pair`
-- additionally requires `sm.subject_moiety <> pm.partner_moiety`. db/032 DECISION 2
-- deliberately permits a class to pair with ITSELF (QT-prolonging x QT-prolonging is a
-- real ONC entry), so for a self-pair rule over a class with N effective members the
-- true reach is N x (N-1), not N x N. AT N = 1 THAT IS 1 VERSUS 0: the product says the
-- rule reaches one pair, the read path yields none.
--
-- BOTH DETECTORS THEN INVERT, which is why this is worth a migration rather than a
-- note. `gap_uncurated_class_interaction_rule`'s `HAVING max(max_pair_count) > 0`
-- ADMITS that rule to the curator worklist and mints it an immortal question_uuid
-- asking about "up to 1 drug pair(s)" -- #36's measured mistake, a review gate asking
-- what no answer can change. And `drugref status`'s `WHERE max_pair_count = 0` OMITS
-- it, so the rule stays "ingested, graded, committed and reported successful while
-- reaching zero patients" -- the exact failure db/035 is named for, reproduced by the
-- detector built to catch it.
--
-- THE COMMENT IS CORRECTED HERE; THE ARITHMETIC IS NOT. Making the bound exact is a
-- change to what these two detectors DO -- the honest fix subtracts the two sides'
-- shared effective membership, of which the self-pair is the reachable special case --
-- and that is a schema decision with its own tests, not a sentence. Filed as issue #108
-- so the gap is a decision rather than an omission, and named in the catalog below so
-- nobody re-derives the guarantee from the old wording in the meantime.
COMMENT ON VIEW drugref.class_pair_rule_reach IS
    'Per CLASS x CLASS rule: how many drugs each side could pair with, counted over '
    'the class subtree, over direct members only, and EFFECTIVELY (subtree or direct '
    'according to today''s class_expansion_policy, using db/034''s own predicate). '
    'ci_rule_partner_reach''s class-grain sibling -- and a PRODUCT rather than a '
    'single count, because a class x class rule expands on BOTH sides. THE ONE PLACE '
    'the class grain STATES A RULE''S REACH: gap_uncurated_class_interaction_rule is '
    'a filter over it, so the two agree by construction. `max_pair_count` is an UPPER '
    'BOUND, and NOT exact about zero -- db/035 claimed it was. The read path excludes '
    'a drug pairing with itself, which this product cannot see, so where the two '
    'sides share effective membership the true reach is lower: for a SELF-PAIR rule '
    '(db/032 DECISION 2 permits one) over a class with N members it is N*(N-1), which '
    'at N=1 is ZERO while this column reads 1. A rule reading 0 here therefore reaches '
    'nobody, but a rule reading non-zero may ALSO reach nobody -- see issue #108, '
    'which owns making the bound exact. A 0 on either side is issue #92''s mixed-kind '
    'shape ([MoA] x [EPC], where one axis cannot select both memberships) made '
    'visible, and an unpopulated class besides. Walks ci_class_pair_subtree, NEVER '
    'ci_class_subtree -- db/034 separated them after a merged walk was measured to tax '
    'every moiety-grain query ~3.6x.';
COMMENT ON COLUMN drugref.class_pair_rule_reach.max_pair_count IS
    'subject_effective_member_count * object_effective_member_count -- an UPPER BOUND '
    'on the drug pairs this rule reaches. NOT exact about zero (db/035 said it was): '
    'the read path excludes self-pairs, so a self-pair rule over a one-member class '
    'reads 1 here and reaches 0. Issue #108. See the view comment.';

-- ============================================================================
-- 3. curated_grain_disagreement names ONE deliberate omission and has TWO (#97)
-- ============================================================================
-- Not a false claim like the two above -- an incomplete one, which in a comment that
-- goes out of its way to enumerate what it does NOT cover reads the same way. db/035
-- states the different-AXIS omission (issue #106) and stops, so a reader reasonably
-- concludes it is the only one.
--
-- THE SECOND IS ORIENTATION. The view joins `curated_ddi_pair` to itself on
-- (subject_moiety, partner_moiety, relationship), and db/006's own comment on
-- `ddi_candidate_pair` makes the convention explicit: rows are 'DIRECTIONAL, not
-- symmetric... A consumer asking "do X and Y interact" MUST query both directions.'
-- A moiety rule emitting (a, b) and a class rule stated the other way round emitting
-- (b, a) are ONE clinical pair on ONE axis with two grades, and this join returns
-- nothing for them. Nothing normalises orientation between the two candidate tiers,
-- and they come from different upstreams, so agreement is coincidence not invariant.
--
-- The READ path is unaffected -- a consumer unioning both directions still gets
-- most-severe-first. What under-reports is the worklist db/035 calls 'what keeps
-- most-severe-wins from becoming permanent over-warning'. Filed as issue #109.
COMMENT ON VIEW drugref.curated_grain_disagreement IS
    'Rule PAIRS -- one moiety-grain rule and one class-grain rule -- that grade at '
    'least one drug pair on the same axis with a different severity or evidence '
    'grade. EXPECTED EMPTY, and it is what makes curated_ddi_pair''s '
    'most-severe-wins precedence safe rather than merely loud: without it, a class '
    'rule over-warning where a curator specifically graded one drug milder would '
    'stand forever, and permanent irrelevant warnings are how prescribers learn to '
    'click through them. THE GRAIN IS THE RULE PAIR, not the drug pair: two rules can '
    'overlap on thousands of pairs (SSRIs x MAOIs alone is ~2,263) and one curator '
    'decision must not be reported thousands of times -- overlapping_pair_count '
    'carries the size instead. TWO SHAPES IT DOES NOT COVER, and db/035 named only the '
    'first: two MOIETY-grain rules on different axes (issue #106, two statements about '
    'two mechanisms, which is why the join matches on relationship), and rules stated '
    'in OPPOSITE ORIENTATIONS (issue #109 -- these rows are directional, so a moiety '
    'rule on (a,b) and a class rule on (b,a) are one clinical pair this join does not '
    'bring together). An OPERATOR view rather than a gap kind FOR NOW: it is '
    'a question drugref answers itself and so a fair candidate, but a gap_key is '
    'frozen forever and no class-grain content ships yet, so the key''s grain would '
    'be chosen against no real instance. Answer a row by superseding one of the two '
    'rulings, or by recording why both stand.';

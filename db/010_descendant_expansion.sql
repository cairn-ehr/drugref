-- db/010_descendant_expansion.sql
-- drugref global tier, Plan B: DAG-DESCENDANT EXPANSION for contraindications.
--
-- THE DEFECT (issue #15, measured against the real 2026.07.06 release). Until now
-- ddi_candidate_pair joined DIRECT class_membership only, so a contraindication
-- naming a broad class returned nothing for a drug classified solely under a
-- DESCENDANT of it. db/004 called direct-only "the conservative default". For a
-- contraindication that reads backwards: it is conservative about over-alerting,
-- never about patient safety, and FEWER ROWS IS THE HARM DIRECTION.
--
-- How much was missing, over 739 CI_MoA/CI_PE rules:
--
--     CI_MoA (462 rules)   14,350 pairs direct   18,363 expanded    21.9% hidden
--     CI_PE  (277 rules)    5,829 pairs direct   39,354 expanded    85.2% hidden
--
-- MED-RT tends to file membership at the most SPECIFIC node while writing rules
-- against the parent, so direct-only is not a neutral default -- it is
-- systematically mismatched with how the source is authored. The case that makes it
-- concrete: `Decreased Coagulation Activity [PE]` carries 8 CI rules and reaches
-- 4 drugs; 105 more sit one level down -- warfarin, apixaban, rivaroxaban, edoxaban,
-- aspirin, ticagrelor, every heparin, every thrombolytic. Additive-toxicity
-- interactions are invisible to the MoA axis by construction (warfarin, apixaban,
-- dabigatran and the heparins share essentially no mechanism and converge on one
-- physiologic effect), so PE is where this matters most.
--
-- WHY A DENY-LIST AND NOT A SIZE THRESHOLD. Unbounded expansion would be dominated
-- by abstract PE organ-system buckets: `Hematologic Activity Alteration [PE]` alone
-- accounts for 48.7% of the gap. But size is only how those were DISCOVERED. The
-- criterion is qualitative -- "would a contraindication naming this class alone tell
-- a prescriber what to avoid?" -- because `Decreased Coagulation Activity` has 109
-- drugs in its subtree and must expand, while `Hematologic Activity Alteration` has
-- 1,233 and must not. A size rule captured the coagulation and CNS-depression cases
-- by luck of topology and would not survive MED-RT reshaping its tree.
--
-- THE DENY-LIST FILTERS THE RULE'S OBJECT CLASS. It is NOT a barrier met during the
-- walk, and the wrong reading is implementable: `Decreased Coagulation Activity` is
-- a DESCENDANT of the denied `Hematologic Activity Alteration`, so a traversal
-- barrier would leave the coagulation rules unexpanded -- deleting the single most
-- important case this migration exists to fix. Pinned by
-- test_a_descendant_of_a_denied_root_still_expands.

-- ---- 1. the policy, as data --------------------------------------------------
--
-- WHICH ROOTS ARE TOO ABSTRACT TO PAIR ON IS A CLINICAL JUDGEMENT, so it is a table
-- a pharmacist can read, diff and revise -- not a constant inside a view. Three
-- storage properties, each deliberate:
--
--   * NOT a rebuildable projection. Every other MED-RT-keyed table in drugref is
--     dropped and rebuilt per release; nothing clears this one. An ingest that wiped
--     curator judgement would re-open every bucket silently on the next release.
--   * NOT the append-only signed overlay either (that tier arrives with Plan C).
--     This is small, low-cardinality policy data in the same class as ci_axis and
--     source_tier: edited in place, reviewed by diff.
--   * KEYED ON (source, source_code), NOT on class_uuid. A migration runs before any
--     class exists, so a foreign key to substance_class could not be satisfied; and
--     storing a derived class_uuid would put the ids.mint_class_uuid derivation in a
--     second place, which is exactly the two-lists-in-two-places footgun db/006 was
--     written to remove. The NUI and the class name sit in the row where a reviewer
--     can read them; expansion_policy_unresolved (below) reports any row that
--     resolves to no class, which is the integrity check the missing FK would give.
CREATE TABLE IF NOT EXISTS drugref.class_expansion_policy (
    -- The authority that defines the class, spelled as substance_class.source is.
    source          text        NOT NULL,
    -- Its stable identity key -- a MED-RT NUI. substance_class.source_code.
    source_code     text        NOT NULL,
    decision        text        NOT NULL,
    -- Denormalised from the release ON PURPOSE: a bare NUI is unreviewable, and this
    -- table's entire justification is that a human can judge a row on sight. Never
    -- read by the view -- the join is on source_code -- so a stale name here is a
    -- documentation defect, not a behaviour change.
    class_name      text        NOT NULL,
    rationale       text        NOT NULL,
    -- Who decided, and against which release they looked. Staleness is the failure
    -- mode of a curated list, so "reviewed against 2026.07.06" has to be legible
    -- without archaeology through git.
    reviewed_by     text        NOT NULL,
    reviewed_against text       NOT NULL,
    reviewed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, source_code),
    -- The view branches on this literal. A row spelled 'denied' or 'no' would read
    -- as neither deny nor allow and silently expand a bucket somebody meant to stop.
    CONSTRAINT class_expansion_policy_decision
        CHECK (decision IN ('deny', 'allow'))
);

COMMENT ON TABLE drugref.class_expansion_policy IS
    'Per-class descendant-expansion policy for contraindications: `deny` means a CI '
    'rule naming this class expands to its DIRECT members only, `allow` means it '
    'expands over the full subtree. NO ROW MEANS UNREVIEWED, which expands (the safe '
    'default) and is reported by gap_unreviewed_expansion_root -- so `allow` and '
    'absent differ for the worklist and not for the pair set. CURATOR POLICY, not a '
    'projection: no ingest clears it. THE DECISION APPLIES TO THE CLASS THE RULE '
    'NAMES, never to classes met while walking down -- a denied root does not stop a '
    'rule stated against one of its descendants.';
COMMENT ON COLUMN drugref.class_expansion_policy.class_name IS
    'Cached from the release so the row is reviewable on sight. NOT a join key; a '
    'stale name here changes no behaviour.';
COMMENT ON COLUMN drugref.class_expansion_policy.reviewed_against IS
    'The upstream release the judgement was made against. A list like this fails by '
    'going stale, so its age must be readable from the row.';

-- The seed: the fourteen CI object classes with more than 20 descendant classes in
-- the 2026.07.06 release. ALL FOURTEEN ARE PE; not one is a MoA class -- the finding
-- that made a named list, rather than a size threshold, the right mechanism.
--
-- Eleven are denied. Ten of those are "<system> Activity Alteration" buckets: they
-- name a system that is affected, never an effect that accumulates. The eleventh,
-- Increased Immunologic Activity, is denied on the evidence of its SUBTREE rather
-- than its name.
--
-- Three are large AND legitimate, so they carry an explicit `allow`: absence would
-- leave them on the review worklist forever.
--
-- ON CONFLICT DO NOTHING, because migrations are replayed whole and a node operator
-- may have revised a decision locally. The seed is drugref's opinion at first
-- install, not a value re-imposed on every startup.
INSERT INTO drugref.class_expansion_policy
    (source, source_code, decision, class_name, rationale, reviewed_by, reviewed_against)
VALUES
    ('MED-RT', 'N0000009065', 'deny', 'Hematologic Activity Alteration [PE]',
     'Abstract organ-system bucket: names the system affected, not an effect that '
     'accumulates. 114 descendant classes, 6 direct members against 1,233 in the '
     'subtree; alone it accounts for 48.7% of the whole expansion gap. "Not with '
     'anything that alters hematologic activity" is not advice a prescriber can act on.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000009069', 'deny', 'Hemic/Lymphatic Activity Alteration [PE]',
     'Abstract organ-system bucket (115 descendant classes, 2 direct members against '
     '1,235 in the subtree). Names the system, not the effect.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000009036', 'deny', 'Endocrine Activity Alteration [PE]',
     'Abstract organ-system bucket (128 descendant classes, 0 direct members against '
     '336 in the subtree). Names the system, not the effect.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000009832', 'deny', 'Renal/Urological Activity Alteration [PE]',
     'Abstract organ-system bucket (123 descendant classes, 6 direct members against '
     '209 in the subtree). Names the system, not the effect.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000008331', 'deny', 'Cardiovascular Activity Alteration [PE]',
     'Abstract organ-system bucket (110 descendant classes, 7 direct members against '
     '613 in the subtree). Names the system, not the effect.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000009027', 'deny', 'Electrical Activity Alteration [PE]',
     'Abstract organ-system bucket (70 descendant classes, 7 direct members against '
     '265 in the subtree). Names the system, not the effect.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000009839', 'deny', 'Respiratory/Pulmonary Activity Alteration [PE]',
     'Abstract organ-system bucket (39 descendant classes, 5 direct members against '
     '226 in the subtree). Names the system, not the effect.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000009070', 'deny', 'Hemostasis Alteration [PE]',
     'Abstract bucket: "Alteration" of a function, with no direction. 34 descendant '
     'classes, 8 direct members against 195 in the subtree. Contrast '
     'Decreased Coagulation Activity, which names a direction and is expanded.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000009739', 'deny', 'Lipid Metabolism Alteration [PE]',
     'Abstract bucket: names a metabolic system with no direction (26 descendant '
     'classes, 54 direct members against 153 in the subtree).',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000009020', 'deny', 'Dermatologic Activity Alteration [PE]',
     'Abstract organ-system bucket (21 descendant classes, 5 direct members against '
     '121 in the subtree). Names the system, not the effect.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000175551', 'deny', 'Increased Immunologic Activity [PE]',
     'DENIED ON ITS SUBTREE, NOT ITS NAME -- the one seed row the qualitative test '
     'alone would have got wrong. It does name a direction and a function, but its '
     'children are heterogeneous: Acquired Immunity [PE] holds 1,109 drugs (in effect '
     'every vaccine), which is not "increased immunologic activity" in the '
     'additive-harm sense. 33 direct members fan out to 1,313.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000009908', 'allow', 'Vasoconstriction [PE]',
     'REVIEWED AND EXPANDED despite clearing the discovery threshold: it names a '
     'direction and a function, and only Arterial (65) and Venous (2) '
     'Vasoconstriction sit beneath it. 54 direct members, 119 in the subtree.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000008663', 'allow', 'Decreased Immunologically Active Molecule Activity [PE]',
     'REVIEWED AND EXPANDED: names a direction and a molecule class, and its children '
     'are specific effects (cytokine, complement, kinin, adhesion factor, antibody). '
     '35 direct members, 327 in the subtree.',
     'DRUGREF', '2026.07.06'),
    ('MED-RT', 'N0000175651', 'allow', 'Increased Sympathetic Activity [PE]',
     'REVIEWED AND EXPANDED: names a direction and a function. Expansion is a no-op '
     'today in any case -- all 21 of its children are empty, so 16 direct members are '
     'also the whole subtree.',
     'DRUGREF', '2026.07.06')
ON CONFLICT (source, source_code) DO NOTHING;

-- A deny that matches nothing looks exactly like a deny that is working. This is the
-- integrity check the deliberately-absent foreign key would otherwise give, and the
-- other half of the rot problem: gap_unreviewed_expansion_root catches a NEW abstract
-- root, this catches a policy row whose class upstream re-keyed or withdrew.
CREATE OR REPLACE VIEW drugref.expansion_policy_unresolved AS
SELECT p.source, p.source_code, p.decision, p.class_name, p.reviewed_against
FROM   drugref.class_expansion_policy p
WHERE  NOT EXISTS (SELECT 1 FROM drugref.substance_class sc
                   WHERE  sc.source      = p.source
                   AND    sc.source_code = p.source_code);

COMMENT ON VIEW drugref.expansion_policy_unresolved IS
    'Expansion-policy rows naming a class the registry does not hold -- upstream '
    're-keyed or withdrew it, so the decision silently stops applying. Expected to be '
    'EMPTY after a full ingest; on a partial test fixture every unmatched row is '
    'listed, which is correct rather than alarming.';

-- ---- 2. whether a predicate expands at all, declared beside what it expands over --
--
-- db/006 made "which membership axis does this predicate expand over" a declaration
-- on ci_axis rather than a CASE in the view, so a new predicate cannot be admitted
-- without saying. WHETHER it expands over descendants is the same class of decision,
-- and slice 5b lands predicates (CI_with, CI_ChemClass) whose object vocabulary is
-- MeSH -- a differently shaped tree, over which the answer may well be different.
ALTER TABLE drugref.ci_axis
    ADD COLUMN IF NOT EXISTS expands_descendants boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN drugref.ci_axis.expands_descendants IS
    'Does a rule with this predicate reach members of the object class''s DESCENDANTS, '
    'or only its direct members? True for both MED-RT predicates: MED-RT files '
    'membership at the specific node and writes rules against the parent, so '
    'direct-only loses 65% of the pairs. A predicate over a differently shaped object '
    'vocabulary (slice 5b, MeSH) may want false -- decided here, per predicate, rather '
    'than assumed by the view.';

-- ---- 3. the read path ---------------------------------------------------------
--
-- Dropped rather than replaced: CREATE OR REPLACE VIEW cannot add columns in the
-- middle, and member_class/is_direct belong beside via_class.
DROP VIEW IF EXISTS drugref.ddi_candidate_pair;

CREATE VIEW drugref.ddi_candidate_pair AS
-- Every class at or below each contraindicated class. UNION over (root, class) --
-- NOT paths, and NOT carrying a depth -- for two reasons: db/002 forbids only
-- self-parenting, so A-is-a-B-is-a-A is representable and one bad release could
-- introduce it; and a multi-parent DAG (440 such classes in the real release) has
-- exponentially many paths but few nodes. Deduping on the node makes the recursion
-- terminate under a cycle and stay linear in the DAG. This is the same idiom
-- db/008's gap_unpopulated_contraindication uses, deliberately -- one recursion
-- pattern in the codebase, not two.
WITH RECURSIVE subtree(root_uuid, class_uuid) AS (
    SELECT DISTINCT ci.object_class_uuid, ci.object_class_uuid
    FROM   drugref.class_contraindication ci
  UNION
    SELECT s.root_uuid, cp.child_class_uuid
    FROM   subtree s
    JOIN   drugref.class_parent cp ON cp.parent_class_uuid = s.class_uuid
)
-- One row per (rule, partner). Without DISTINCT ON, a partner filed under two
-- branches of one root -- ordinary in a multi-parent DAG -- would be counted twice
-- by any consumer tallying candidate partners.
SELECT DISTINCT ON (ci.subject_moiety_uuid, ci.object_class_uuid, ci.relationship,
                    ci.source, m.moiety_uuid)
       ci.subject_moiety_uuid AS subject_moiety,   -- the drug the CI is ABOUT
       m.moiety_uuid          AS partner_moiety,   -- the co-administered drug
       ci.relationship,
       ci.object_class_uuid   AS via_class,        -- the class the RULE names
       m.class_uuid           AS member_class,     -- where the PARTNER is filed
       (m.class_uuid = ci.object_class_uuid) AS is_direct,
       ci.source,
       ci.ingest_run,
       r.upstream_release,                         -- WHICH release said so
       r.finished_at          AS ingested_at       -- and when drugref took it in
FROM   drugref.class_contraindication ci
       -- The axis mapping, a join rather than a CASE since db/006: a predicate with
       -- no ci_axis row cannot be in the table at all (foreign key), so there is no
       -- way for a stored contraindication to expand to nothing.
JOIN   drugref.ci_axis a
       ON a.relationship = ci.relationship
JOIN   subtree s
       ON s.root_uuid = ci.object_class_uuid
JOIN   drugref.class_membership m
       ON m.class_uuid   = s.class_uuid
      AND m.relationship = a.membership_relationship
JOIN   drugref.ingest_run r
       ON r.ingest_run_id = ci.ingest_run
       -- Inner join: object_class_uuid is a foreign key into substance_class, so
       -- this drops nothing. It exists to reach (source, source_code), the key the
       -- policy is stated on.
JOIN   drugref.substance_class oc
       ON oc.class_uuid = ci.object_class_uuid
LEFT   JOIN drugref.class_expansion_policy p
       ON p.source = oc.source AND p.source_code = oc.source_code
WHERE  m.moiety_uuid <> ci.subject_moiety_uuid
       -- Direct membership always pairs. Beyond that the predicate must expand AND
       -- the class the RULE NAMES must not be denied. COALESCE makes "no policy row"
       -- expand: unreviewed is the safe default, and the review gate reports it.
AND    (m.class_uuid = ci.object_class_uuid
        OR (a.expands_descendants AND COALESCE(p.decision, 'allow') <> 'deny'))
       -- DISTINCT ON keeps the FIRST row per group, so a partner filed both directly
       -- and under a descendant is reported as the direct hit it is -- which is also
       -- what makes `WHERE is_direct` reproduce the pre-expansion row set exactly.
       -- m.class_uuid last: a deterministic tiebreak among equally-indirect classes.
ORDER  BY ci.subject_moiety_uuid, ci.object_class_uuid, ci.relationship, ci.source,
          m.moiety_uuid, (m.class_uuid = ci.object_class_uuid) DESC, m.class_uuid;

COMMENT ON VIEW drugref.ddi_candidate_pair IS
    'DIRECTIONAL, not symmetric: one row means "subject_moiety is contraindicated '
    'with partner_moiety", derived from the subject''s own contraindication against '
    'a class the partner belongs to. The mirror row (partner, subject) appears ONLY '
    'if the partner independently carries its own contraindication -- a distinct '
    'assertion. A consumer asking "do X and Y interact" MUST query both directions; '
    'querying one and finding nothing is not evidence of no interaction. '
    'EXPANSION DESCENDS THE CLASS DAG (Plan B, #15): the partner may be filed under a '
    'DESCENDANT of the class the rule names -- member_class says which, is_direct '
    'says whether it was the class itself. Filter WHERE is_direct for the '
    'direct-membership-only semantics this view had before. Two things bound the '
    'walk: ci_axis.expands_descendants per predicate, and class_expansion_policy, '
    'which denies expansion for abstract organ-system roots -- so this view still has '
    'recall gaps under a DENIED class. '
    'CANDIDATE TIER -- see class_contraindication; nothing here is an alert.';
COMMENT ON COLUMN drugref.ddi_candidate_pair.subject_moiety IS
    'The drug the contraindication is ABOUT (the subject of the upstream assertion).';
COMMENT ON COLUMN drugref.ddi_candidate_pair.partner_moiety IS
    'The co-administered drug, reached through its membership of member_class.';
COMMENT ON COLUMN drugref.ddi_candidate_pair.via_class IS
    'The class the CONTRAINDICATION RULE names. Equal to member_class for a direct '
    'hit; an ancestor of it for an expanded one.';
COMMENT ON COLUMN drugref.ddi_candidate_pair.member_class IS
    'The class the PARTNER is actually filed under -- what to show a reviewer asking '
    'why this pair was proposed ("warfarin, via Decreased Coagulation Factor Activity").';
COMMENT ON COLUMN drugref.ddi_candidate_pair.is_direct IS
    'member_class = via_class. True reproduces exactly the row set this view returned '
    'before descendant expansion landed. A partner reachable both ways is reported '
    'once, as direct.';
COMMENT ON COLUMN drugref.ddi_candidate_pair.upstream_release IS
    'The upstream release this advice came from -- how stale it is.';

-- ---- 4. the review gate -------------------------------------------------------
--
-- A curated list rots the first time upstream reshapes its tree, and the failure is
-- silent: a new abstract root nobody has judged expands over hundreds of drugs and
-- nothing says so. §7's own rule supplies the mechanism -- A GAP IS A QUERY, NEVER A
-- REPORT -- so an unjudged large root arrives as an open question for a pharmacist
-- instead of as fan-out in the pair view.
--
-- THE >20 THRESHOLD HERE IS A DISCOVERY HEURISTIC FOR THE WORKLIST, and nothing
-- else. It is emphatically NOT the criterion for denying expansion: that judgement
-- is qualitative ("does this class name an effect a prescriber can act on, or only
-- the organ system?") and lives in class_expansion_policy. Size is merely how the
-- original fourteen were found -- and it found them well: every single class over
-- the threshold in the 2026.07.06 release is a PE "Activity Alteration" bucket, not
-- one is a MoA class. Retuning it is a CREATE OR REPLACE VIEW in a later migration
-- and wants a reason from a curator, not from this file.
--
-- Deliberately scoped to classes a CONTRAINDICATION NAMES. The question is about
-- expansion policy, and a class no rule names expands nothing; asking about every
-- large class in a 3,634-class DAG would bury the handful that matter.
CREATE OR REPLACE VIEW drugref.gap_unreviewed_expansion_root AS
WITH RECURSIVE subtree(root_uuid, class_uuid) AS (
    SELECT DISTINCT ci.object_class_uuid, ci.object_class_uuid
    FROM   drugref.class_contraindication ci
  UNION
    SELECT s.root_uuid, cp.child_class_uuid
    FROM   subtree s
    JOIN   drugref.class_parent cp ON cp.parent_class_uuid = s.class_uuid
),
-- Minus one for the root itself, which the base case always contributes exactly
-- once -- the UNION dedupes on (root, class), so this stays right under a cycle too.
sized AS (
    SELECT root_uuid, count(*) - 1 AS descendant_class_count
    FROM   subtree
    GROUP  BY root_uuid
)
SELECT sc.class_uuid,
       sc.class_name,
       sc.concept_type,
       z.descendant_class_count,
       -- How many contraindications ride on the decision: the priority signal for a
       -- reviewer, not an ordering this view imposes.
       count(*)                AS ci_rule_count,
       max(r.upstream_release) AS upstream_release
FROM   drugref.class_contraindication ci
JOIN   drugref.substance_class sc ON sc.class_uuid   = ci.object_class_uuid
JOIN   sized                   z  ON z.root_uuid    = ci.object_class_uuid
JOIN   drugref.ingest_run      r  ON r.ingest_run_id = ci.ingest_run
WHERE  z.descendant_class_count > 20
       -- Either decision counts as reviewed. `allow` and `deny` differ for the pair
       -- set and agree here, because this view asks only whether a human has looked.
AND    NOT EXISTS (SELECT 1 FROM drugref.class_expansion_policy p
                   WHERE  p.source      = sc.source
                   AND    p.source_code = sc.source_code)
GROUP  BY sc.class_uuid, sc.class_name, sc.concept_type, z.descendant_class_count;

COMMENT ON VIEW drugref.gap_unreviewed_expansion_root IS
    'Contraindicated classes with more than 20 descendant classes that nobody has '
    'ruled on in class_expansion_policy -- so they expand over their whole subtree by '
    'default, which for an abstract organ-system bucket is fan-out rather than '
    'recall. The threshold is a DISCOVERY HEURISTIC for the worklist, never the '
    'criterion for denying expansion: that judgement is qualitative and belongs in '
    'the policy table. EITHER decision retires the question. ABSENCE OF A ROW IS NOT '
    'A GUARANTEE OF SENSIBLE EXPANSION: a badly-shaped root with 20 descendants is '
    'invisible here.';

-- ---- 5. admit the new question kind -------------------------------------------
--
-- db/007 constrained gap_kind to the three kinds Plan A shipped, and said to widen it
-- deliberately in a new migration as further gap views land. This is that widening.
-- It is not cosmetic: register_from_gaps INSERTs the kind its view derives, so a kind
-- the CHECK does not admit fails at the very LAST step of an ingest, after every
-- projection has been rebuilt -- aborting the whole transaction.
--
-- Guarded on the constraint's own text rather than merely on its name, so a replay
-- against an already-widened database skips the drop/add entirely instead of
-- rescanning the table. Same idiom as db/003's vocabulary widenings.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE  conname  = 'open_question_gap_kind'
                   AND    conrelid = 'drugref.open_question'::regclass
                   AND    pg_get_constraintdef(oid) LIKE '%unreviewed_expansion_root%') THEN
        ALTER TABLE drugref.open_question
            DROP CONSTRAINT IF EXISTS open_question_gap_kind;
        ALTER TABLE drugref.open_question
            ADD CONSTRAINT open_question_gap_kind CHECK (gap_kind IN (
                'unpopulated_contraindication', 'unclassified_moiety',
                'unmatched_ingredient', 'unreviewed_expansion_root'));
    END IF;
END $$;

-- ---- 6. the contract db/008 stated as current fact ----------------------------
--
-- db/008's COMMENT carried two caveats on gap_unpopulated_contraindication, the first
-- of which was written while descendant expansion was still outstanding: it warned
-- that the view UNDERSTATES what returns nothing, because population was tested over
-- the whole subtree while the pair view expanded over direct membership only.
--
-- That is no longer true in general -- but it is STILL TRUE FOR A DENIED ROOT, whose
-- rules expand to direct members only by design. So the caveat is NARROWED, not
-- deleted. Migrations are immutable once applied, hence a re-issue here rather than
-- an edit there; the view definition itself is unchanged and deliberately so, since
-- "is this concern answerable anywhere below" is a different question from "what does
-- the read path currently return".
COMMENT ON VIEW drugref.gap_unpopulated_contraindication IS
    'Contraindications whose object class has no drug filed under it ON THE AXIS THE '
    'RULE EXPANDS OVER (ci_axis), anywhere in the class subtree -- upstream asserts '
    'the concern and never populates it. ci_rule_count counts only the DEAD rules on '
    'that class and is the priority signal for this view; question_worklist does not '
    'order by it. TWO CAVEATS. (1) Population is tested over the whole SUBTREE. Since '
    'descendant expansion landed (#15) the pair view agrees -- EXCEPT where expansion '
    'is switched off: under a class denied in class_expansion_policy, or a predicate '
    'with ci_axis.expands_descendants false, a rule populated only via a descendant '
    'still yields no pair yet is deliberately absent here. For those, this view '
    'UNDERSTATES what returns nothing. (2) ABSENCE OF A ROW IS NOT COVERAGE: a hazard '
    'MED-RT never modelled at all appears nowhere here.';

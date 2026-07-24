-- db/006_interaction_axis.sql
-- Make the contraindication model's two comment-enforced invariants structural,
-- and make its clinical contract visible to whoever reads the database.
--
-- 1. THE CHECK<->CASE COUPLING. db/004 admitted CI predicates with a CHECK on the
--    table, and mapped each to the membership axis it expands over with a CASE
--    inside ddi_candidate_pair -- two lists in two places kept in step by a comment
--    saying "COUPLED ... keep the two in lockstep". Widening only the CHECK (which
--    is exactly what slice 5b has to do) inserted rows that expanded to ZERO pairs
--    with no error: an unmapped CASE arm yields NULL, and `m.relationship = NULL`
--    joins nothing. A contraindication present in the table and absent from the
--    read path is the worst failure this projection has, so the mapping becomes a
--    TABLE that the vocabulary itself is a foreign key into. A predicate now cannot
--    be admitted without declaring what it expands over, and once declared the view
--    picks it up with no second edit.
--
-- 2. SOURCE BELONGS IN THE KEY. The primary key was (subject, object, relationship),
--    so a second authority asserting a contraindication MED-RT had already recorded
--    was silently swallowed by ON CONFLICT DO NOTHING -- and the next routine MED-RT
--    rebuild, which deletes by ingest_run, took the shared row away with it,
--    destroying the other source's independent assertion. Slice 5c plans exactly
--    that second source. Fixed now, while the table holds one authority's data and
--    the change costs nothing.
--
-- 3. THE CONTRACT WAS INVISIBLE. Everything a consumer must know -- that the pair
--    view is DIRECTIONAL, that it does not inherit down the class DAG, that it is
--    candidate-tier and must not auto-alert -- lived in `--` comments here, which
--    Postgres discards. `\d+ drugref.ddi_candidate_pair` showed columns named
--    moiety_a/moiety_b (reading exactly like an unordered pair) with empty
--    descriptions. COMMENT ON puts the contract in the catalog, and the columns are
--    renamed to their roles so the directionality is legible without reading it.

-- ---- 1. the axis vocabulary -------------------------------------------------

CREATE TABLE IF NOT EXISTS drugref.ci_axis (
    -- The contraindication predicate, as MED-RT names it.
    relationship            text PRIMARY KEY,
    -- The class_membership axis its object class is expanded over. CI_MoA pairs
    -- with has_MoA members, CI_PE with has_PE -- never cross-wired, or the
    -- clinical meaning inverts.
    membership_relationship text NOT NULL
);

INSERT INTO drugref.ci_axis (relationship, membership_relationship)
VALUES ('CI_MoA', 'has_MoA'), ('CI_PE', 'has_PE')
ON CONFLICT (relationship) DO NOTHING;

COMMENT ON TABLE drugref.ci_axis IS
    'The admissible contraindication predicates and the class_membership axis each '
    'one expands over. class_contraindication.relationship is a foreign key into '
    'this table, and ddi_candidate_pair joins it, so adding a predicate is ONE '
    'insert here and cannot leave the read path silently returning nothing.';

-- ---- 2. the projection table ------------------------------------------------

-- Replace the relationship CHECK with the foreign key. Both name the same
-- vocabulary; the FK keeps it in one place.
ALTER TABLE drugref.class_contraindication
    DROP CONSTRAINT IF EXISTS class_contraindication_relationship;
ALTER TABLE drugref.class_contraindication
    ADD CONSTRAINT class_contraindication_relationship
    FOREIGN KEY (relationship) REFERENCES drugref.ci_axis(relationship);

-- Source into the primary key. Safe to do unconditionally: only MED-RT rows can
-- exist today, so no duplicate can be created by widening the key.
ALTER TABLE drugref.class_contraindication
    DROP CONSTRAINT IF EXISTS class_contraindication_pkey;
ALTER TABLE drugref.class_contraindication
    ADD PRIMARY KEY (subject_moiety_uuid, object_class_uuid, relationship, source);

COMMENT ON TABLE drugref.class_contraindication IS
    'Class-level drug-drug contraindications: the subject moiety is contraindicated '
    'with any CO-ADMINISTERED drug belonging to the object class. A REBUILDABLE '
    'PROJECTION of an upstream authority (re-ingest deletes this source''s rows and '
    're-inserts), not the append-only curated overlay. CANDIDATE TIER: MED-RT does '
    'not track label updates, so rows here feed review and must not auto-alert.';
COMMENT ON COLUMN drugref.class_contraindication.subject_moiety_uuid IS
    'The drug the contraindication is ABOUT. Not interchangeable with the object side.';
COMMENT ON COLUMN drugref.class_contraindication.object_class_uuid IS
    'The class of the CO-ADMINISTERED drug the subject must not be combined with.';

-- ---- 3. the read path -------------------------------------------------------

-- Dropped rather than replaced: CREATE OR REPLACE VIEW cannot rename columns.
DROP VIEW IF EXISTS drugref.ddi_candidate_pair;

CREATE VIEW drugref.ddi_candidate_pair AS
SELECT ci.subject_moiety_uuid AS subject_moiety,   -- the drug the CI is ABOUT
       m.moiety_uuid          AS partner_moiety,   -- the co-administered drug
       ci.relationship,
       ci.object_class_uuid   AS via_class,
       ci.source,
       ci.ingest_run,
       r.upstream_release,                         -- WHICH release said so
       r.finished_at          AS ingested_at       -- and when drugref took it in
FROM   drugref.class_contraindication ci
       -- The axis mapping, now a join rather than a CASE. A predicate with no
       -- ci_axis row cannot be in the table at all (foreign key), so there is no
       -- longer a way for a stored contraindication to expand to nothing.
JOIN   drugref.ci_axis a
       ON a.relationship = ci.relationship
JOIN   drugref.class_membership m
       ON m.class_uuid = ci.object_class_uuid
      AND m.relationship = a.membership_relationship
JOIN   drugref.ingest_run r
       ON r.ingest_run_id = ci.ingest_run
WHERE  m.moiety_uuid <> ci.subject_moiety_uuid;

COMMENT ON VIEW drugref.ddi_candidate_pair IS
    'DIRECTIONAL, not symmetric: one row means "subject_moiety is contraindicated '
    'with partner_moiety", derived from the subject''s own contraindication against '
    'a class the partner belongs to. The mirror row (partner, subject) appears ONLY '
    'if the partner independently carries its own contraindication -- a distinct '
    'assertion. A consumer asking "do X and Y interact" MUST query both directions; '
    'querying one and finding nothing is not evidence of no interaction. '
    'Expansion uses DIRECT class membership only: a contraindication naming a broad '
    'class does NOT reach drugs classified solely under a descendant of it, so this '
    'view has known recall gaps wherever the object class has children. '
    'CANDIDATE TIER -- see class_contraindication; nothing here is an alert.';
COMMENT ON COLUMN drugref.ddi_candidate_pair.subject_moiety IS
    'The drug the contraindication is ABOUT (the subject of the upstream assertion).';
COMMENT ON COLUMN drugref.ddi_candidate_pair.partner_moiety IS
    'The co-administered drug, reached through its membership of via_class.';
COMMENT ON COLUMN drugref.ddi_candidate_pair.upstream_release IS
    'The upstream release this advice came from -- how stale it is.';

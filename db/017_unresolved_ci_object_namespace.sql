-- db/017_unresolved_ci_object_namespace.sql
-- Give gap_unresolved_ci_object the object's NAMESPACE, and stop picking one
-- predicate arbitrarily (issue #41).
--
-- THE DEFECT. drugref.ingest_unresolved_ci_object is keyed
-- (ingest_run, source, relationship, object_source, object_code) -- deliberately,
-- because a future authority can name a contraindication object outside MeSH.
-- db/016's view grouped on object_code ALONE, so the moment a second object_source
-- lands, two DIFFERENT objects sharing a code (a bare "D013449"-shaped identifier
-- is not namespace-unique in general) fold into one row: sum(assertion_count)
-- attributes one authority's rules to the other's object, and max(object_name)
-- reports whichever sorted last.
--
-- WHAT MAKES IT WORSE THAN A WRONG COUNT. questions.py derives gap_key from this
-- view, and question_uuid is a pure function of (gap_kind, gap_key). Two collided
-- objects become ONE curator question -- and because question_state /
-- question_evidence are append-only and keyed off that UUID, a decision recorded
-- against one object stays permanently attached to the other's. A rebuild cannot
-- repair that half.
--
-- THIS MIGRATION IS HALF THE FIX. The other half is in questions.py, whose gap_key
-- hardcoded the 'MESH:' prefix a SECOND time; a migration-only fix looks complete
-- and is not. The two landed together, each with its own test.
--
-- WHY relationship IS NOT IN THE GROUPING KEY, though it was thrown away too.
-- The grain is per OBJECT because the DECISION is per object: "may a rule naming
-- Sulfonamides expand over MeSH's structural tree?" is one question whatever
-- predicate asserted it, and ci_rule_count is how much rides on that one answer,
-- so it must keep summing across predicates. Grouping by relationship WITHOUT also
-- putting relationship in the gap_key would be worse than lossy -- two view rows
-- would mint the same question_uuid and questions.py's executemany upsert would
-- silently keep whichever text was written last. So the predicates are AGGREGATED
-- instead of arbitrarily picked: string_agg returns exactly what max() did while
-- one predicate populates this table, and shows both instead of hiding one when a
-- second lands (slice 5b.2's indications are the next candidate).
--
-- WHY THE NAMESPACE IS CANONICALISED (upper()) IN THE GROUPING KEY, and not merely
-- carried through. questions.py builds gap_key as upper(object_source) || ':' ||
-- object_code, because the frozen SCHEME:value convention every other gap kind uses
-- is upper-case (MOIETY:, CLASS:, RXNORM_IN:). If this view grouped on the VERBATIM
-- spelling, 'MeSH' and 'MESH' would be two view rows folding to ONE gap_key -- and
-- that is the very failure this migration exists to remove, one case narrower:
-- two rows minting one question_uuid, with the executemany upsert silently keeping
-- whichever text landed last. A view row that does not survive to its own gap_key is
-- a collision however it arose. So the grouping key IS the gap_key's namespace: one
-- namespace, one row, one question, whatever spelling a writer used.
--
-- Two case-variant spellings therefore MERGE here rather than collide -- which is
-- correct, because they name the same namespace: their rule counts sum, exactly as
-- two spellings of one authority should. It is only a genuinely DIFFERENT namespace
-- that must stay a different row, and upper() never merges two of those.
--
-- ROW SET UNCHANGED TODAY: MeSH is the only object_source and CI_ChemClass the only
-- relationship, so this returns the same 103 rows summing to the same 405 rules.
-- The gap_key is unchanged too -- 'MeSH' upper()s to the frozen 'MESH', so every
-- existing question_uuid is preserved bit-for-bit; see questions.py.

-- Dropped rather than replaced, the same reason db/010 gave: CREATE OR REPLACE VIEW
-- can only append columns, and object_source belongs BESIDE object_code -- the two
-- are one composite key and reading them apart is what this migration exists to stop.
-- Plain DROP, not CASCADE: this view has no dependent objects (questions.py reads it
-- at runtime), so an unexpected dependent should fail loudly rather than vanish.
DROP VIEW IF EXISTS drugref.gap_unresolved_ci_object;

CREATE VIEW drugref.gap_unresolved_ci_object AS
       -- Grouped on the CANONICAL namespace, not the stored spelling, so that one
       -- view row is exactly one gap_key -- see the header. Emitted canonical too:
       -- a consumer reading object_source off this view is reading the same string
       -- questions.py keys on, which is what stops the two drifting.
SELECT upper(u.object_source)   AS object_source,
       u.object_code,
       max(u.object_name)       AS object_name,
       -- Every predicate asserting this object, not one of them. DISTINCT because a
       -- predicate repeats across ingest runs; ORDER BY so the text is reproducible
       -- (it becomes question_text, which is upserted on every ingest -- an unstable
       -- ordering would rewrite the row on each run for no reason).
       string_agg(DISTINCT u.relationship, ', ' ORDER BY u.relationship)
                                AS relationship,
       max(u.object_kind)       AS object_kind,
       sum(u.assertion_count)   AS ci_rule_count,
       max(r.upstream_release)  AS upstream_release
FROM   drugref.ingest_unresolved_ci_object u
JOIN   drugref.ingest_run r ON r.ingest_run_id = u.ingest_run
GROUP  BY upper(u.object_source), u.object_code;

COMMENT ON VIEW drugref.gap_unresolved_ci_object IS
    'Contraindication objects drugref did not ingest, with how many upstream rules '
    'ride on each and WHY each was not ingested (object_kind, which decides whether '
    'the curator is asked a structural-tree-expansion question or a '
    'register-this-moiety one). ONE ROW PER (CANONICAL object_source, object_code), '
    'because the decision is per object and an object code is only unique within its '
    'namespace; relationship is aggregated, not picked, since one object may be '
    'asserted by several predicates and the decision is still one decision. '
    'ABSENCE OF A ROW IS NOT COVERAGE -- an object no release ever asserted appears '
    'nowhere here.';

COMMENT ON COLUMN drugref.gap_unresolved_ci_object.object_source IS
    'The object''s namespace, UPPER-CASED -- both emitted and GROUPED ON in that '
    'form. This is the namespace half of questions.py''s gap_key, and the grain of '
    'this view has to be the grain of that key: were this grouped on the stored '
    'spelling, two spellings of one namespace would be two rows folding to one '
    'question_uuid, which is the collision this view was re-issued to remove. '
    'Case-variant spellings therefore MERGE (same namespace, counts summed); '
    'genuinely different namespaces never do.';

COMMENT ON COLUMN drugref.gap_unresolved_ci_object.object_kind IS
    'Still folded by max(): object_kind is derived from the OBJECT RECORD, so every '
    'row of one (canonical object_source, object_code) carries the same value and '
    'there is nothing for max() to choose between. Should that ever stop being true, '
    'the derivation -- not this view -- is what changed.';

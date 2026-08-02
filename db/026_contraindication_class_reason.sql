-- db/026_contraindication_class_reason.sql
-- #47: persist the CI subjects medrt_run counts and discards, and re-cut the gap
-- view's tie-break so it states its own reason instead of coinciding with it.
--
-- WHY A FOURTH VALUE AND NOT A SHARED ONE. db/018's invariant is EXACTLY ONE WRITER
-- PER (source, reason): the value scopes a DELETE, so two writers sharing a bucket
-- makes the worklist depend on which ran last -- #39, exactly, with nothing to notice
-- it. medrt_run's CI subjects are its own bucket; `contraindication` belongs to
-- mesh_rel_run's MeSH-keyed rules and stays there.
--
-- THE NAME IS NOT THE ONE THE ISSUE PROPOSED, and the difference is load-bearing.
-- #47 suggests `class_contraindication`. Measured against the live database, that
-- string sorts BEFORE `classification` (`_` precedes `i` under C collation, and the
-- punctuation-insensitive pass of en_US.UTF-8 compares `classc...` against
-- `classi...`) -- so the one value the issue proposed is the one value that inverts
-- the tie-break db/018 wrote to protect it. `contraindication_class` sorts after.
ALTER TABLE drugref.ingest_unmatched_ingredient
    DROP CONSTRAINT IF EXISTS ingest_unmatched_ingredient_reason;
ALTER TABLE drugref.ingest_unmatched_ingredient
    ADD CONSTRAINT ingest_unmatched_ingredient_reason
    CHECK (reason IN ('classification', 'contraindication', 'indication',
                      'contraindication_class'));

COMMENT ON COLUMN drugref.ingest_unmatched_ingredient.reason IS
    'WHY this RxCUI is on the worklist, and -- because the clear is scoped on it -- '
    'WHICH writer owns the row. FOUR values, TWO writers, each now owning TWO buckets: '
    'medrt_run owns `classification` (an ingredient the release classifies) and, since '
    'db/026, `contraindication_class` (the subject of a CI_MoA/CI_PE rule); mesh_rel_run '
    'owns `contraindication` and `indication`. The value count and the writer count '
    'move independently -- db/026 is the second time a new value landed on an existing '
    'writer''s second bucket rather than minting a third writer, so a reader must not '
    'infer one count from the other. NO DEFAULT, DELIBERATELY: a writer that does not '
    'declare its reason must fail, not inherit somebody else''s bucket. EXACTLY ONE '
    'WRITER PER (source, reason) -- add a value here rather than sharing one, or the '
    'clears collide again exactly as medrt_run''s and the MeSH-keyed run''s did.';

-- ============================================================================
-- The tie-break, re-cut to say what it means
-- ============================================================================
-- db/018 widened this ORDER BY to (rxcui, ingest_run DESC, reason) EXPLICITLY
-- anticipating #47, and justified it twice. Both justifications failed on measurement:
--
--   1. "`classification` wins alphabetically" -- true only for the three values that
--      existed, and #47's own proposed name would have inverted it.
--   2. "and by being the bucket with a `name`" -- measured on the real releases,
--      0 of 4,389 rows carry a name in ANY bucket. medrt_run passes no names mapping,
--      so the intended discriminator has never once had a value.
--
-- 1,430 RxCUIs already sit in more than one bucket, so this tie-break is live on real
-- data today; it is simply unobservable, because every candidate row is identical in
-- every column the view projects. That is precisely the kind of latent choice that
-- becomes a bug the moment a source starts supplying names.
--
-- So state the intent: prefer a row that HAS a name. `(u.name IS NULL)` sorts false
-- before true, so a named row wins; `reason` remains as the final, now-decorative
-- settler so the ORDER BY still names a unique row for DISTINCT ON.
CREATE OR REPLACE VIEW drugref.gap_unmatched_ingredient AS
SELECT DISTINCT ON (u.rxcui)
       u.rxcui,
       u.name,
       r.upstream_release
FROM   drugref.ingest_unmatched_ingredient u
JOIN   drugref.ingest_run r ON r.ingest_run_id = u.ingest_run
WHERE  NOT EXISTS (SELECT 1 FROM drugref.identity_claim ic
                   WHERE  ic.scheme = 'RXNORM_IN'
                   AND    ic.value  = u.rxcui
                   AND    ic.superseded_by IS NULL)
ORDER  BY u.rxcui, u.ingest_run DESC, (u.name IS NULL), u.reason;

COMMENT ON VIEW drugref.gap_unmatched_ingredient IS
    'Ingredients an upstream release names that no moiety in the registry carries -- '
    'every one is a drug drugref can say nothing about. Closes by itself when a moiety '
    'claims the RxCUI. Superseded identity claims do not count as carrying it. ONE ROW '
    'PER RxCUI, from the most recent run that reported it and, within that run, from a '
    'row that CARRIES A NAME (db/026, replacing db/018''s alphabetical accident): '
    'gap_key is an input to question_uuid, so two rows here would mint one question '
    'and register_from_gaps would over-report its own live count.';

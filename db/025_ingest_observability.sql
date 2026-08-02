-- db/025_ingest_observability.sql
-- #16, part 1: make a run record say who wrote it, and publish what is loaded.
--
-- WHY A `writer` COLUMN AT ALL. `ingest_run.source` names the AUTHORITY, and one
-- authority can have two writers: medrt_run ingests MED-RT's classification and
-- class-keyed contraindications, mesh_rel_run ingests MED-RT's MeSH-keyed halves, and
-- BOTH open their runs under source 'MED-RT'. Their source_checksums legitimately
-- differ (ingest/checksum.py hashes one file for the first and three for the second),
-- so "which MED-RT release is live" has two answers. A view keyed on source alone
-- reports whichever finished last and hides that the other half is a release behind.
-- That is exactly #39 -- two writers sharing one scope -- one layer up, on the table
-- #39's own fix (a `reason` discriminator) could not reach.
--
-- NOT NULL, NO DEFAULT: db/018's posture for `reason`, and for the same reason. A
-- writer that does not declare itself must fail loudly rather than inherit somebody
-- else's identity, because the value is what a consumer reads to decide whether a
-- projection is current.
ALTER TABLE drugref.ingest_run ADD COLUMN IF NOT EXISTS writer text;

-- HISTORICAL ROWS ARE NOT GUESSED. Nothing in an existing row distinguishes the two
-- MED-RT writers, so attributing them would be inventing provenance -- the one thing
-- this table exists to prevent. `unattributed` is a real value with a stated meaning:
-- written before this migration, when two orchestrators shared a source and nothing
-- told them apart. ingest_run is HISTORY, not a rebuildable projection, so it cannot
-- heal itself the way db/018's table did; these rows age out of loaded_release
-- naturally, by being older than the next real run.
UPDATE drugref.ingest_run SET writer = 'unattributed' WHERE writer IS NULL;

ALTER TABLE drugref.ingest_run ALTER COLUMN writer SET NOT NULL;

ALTER TABLE drugref.ingest_run DROP CONSTRAINT IF EXISTS ingest_run_writer;
ALTER TABLE drugref.ingest_run ADD CONSTRAINT ingest_run_writer
    CHECK (writer IN ('unii_run', 'chebi', 'medrt_run', 'mesh_run', 'mesh_rel_run',
                      'pbs_run', 'curation', 'unattributed'));

COMMENT ON COLUMN drugref.ingest_run.writer IS
    'WHICH orchestrator opened this run, as distinct from `source`, which names the '
    'AUTHORITY. They are not the same: source ''MED-RT'' has two writers (medrt_run '
    'and mesh_rel_run), so a release is only unambiguous per (source, writer). '
    '''curation'' covers a DRUGREF-sourced run written by a curator rather than by an '
    'orchestrator (Plan C''s overlay tier). ''unattributed'' means the row predates '
    'db/025 and cannot be attributed. Extend this CHECK together with '
    'provenance.WRITERS -- they are a pair, and a value admitted to one but not the '
    'other is either refused at write time or invisible to the contract test.';

-- ============================================================================
-- The two views: complementary filters on ONE column
-- ============================================================================
-- Stated as a partition rather than as two independent questions, because "one
-- quantity stated twice is a quantity that will disagree" (db/006, and the defect the
-- interaction debt round found in its own first draft). finished_at IS NULL and
-- finished_at IS NOT NULL exhaust the table between them.

-- BEFORE THIS ROUND THIS VIEW COULD ONLY EVER BE EMPTY. Every orchestrator wrote its
-- ingest_run row inside the transaction that did the work, so a crash rolled the
-- provenance away with it: `finished_at` is nullable, which ASSERTS that "started,
-- never finished" is an observable state, and it never was. provenance.open_run
-- commits the row in its own transaction, which is what makes this view able to hold
-- anything at all.
CREATE OR REPLACE VIEW drugref.ingest_run_incomplete AS
SELECT ingest_run_id, source, writer, upstream_release, source_checksum, started_at
FROM   drugref.ingest_run
WHERE  finished_at IS NULL
ORDER  BY started_at DESC, ingest_run_id DESC;

COMMENT ON VIEW drugref.ingest_run_incomplete IS
    'Runs that started and never finished -- a crash, a kill, or a run still in '
    'flight. EMPTY BY CONSTRUCTION BEFORE db/025: the run row used to roll back with '
    'the work it described, so a crashed ingest was indistinguishable from one that '
    'never started. A row here is not itself an error: check it against '
    'loaded_release, which reports the last run that DID finish. '
    'WHAT AN EMPTY VIEW DOES NOT PROVE: the window starts at open_run, not at the '
    'command. THREE of the six orchestrators (medrt_run, mesh_run, mesh_rel_run) '
    'parse their release BEFORE opening the run -- the parsers are pure and take no '
    'connection -- so a crash during MeSH''s ~750 MB parse still leaves no row here, '
    'exactly as before db/025. What this view makes observable is a crash during the '
    'WRITES, which is where the projection can be left half-rebuilt and where the '
    'question "did this ingest land?" actually bites. Reordering the parse is a real '
    'design question, not a wording one, and is deliberately not settled here.';

-- One row per (source, writer): the release that writer last landed.
--
-- The ingest_run_id tie-break is not decoration. finished_at is a timestamp, two runs
-- can share one, and a DISTINCT ON whose ORDER BY does not name a unique row keeps
-- whichever the plan happened to emit first -- the same latent non-determinism db/018
-- found in gap_unmatched_ingredient.
CREATE OR REPLACE VIEW drugref.loaded_release AS
SELECT DISTINCT ON (source, writer)
       source, writer, upstream_release, source_checksum,
       ingest_run_id, started_at, finished_at
FROM   drugref.ingest_run
WHERE  finished_at IS NOT NULL
ORDER  BY source, writer, finished_at DESC, ingest_run_id DESC;

COMMENT ON VIEW drugref.loaded_release IS
    'Which upstream release each writer last landed, from which bytes, and when. '
    'PER (source, writer), NOT per source: MED-RT has two writers and re-ingesting one '
    'without the other is a real and otherwise invisible staleness. WHAT IT DOES NOT '
    'MEAN: this is the release a per-source rebuild last replaced its PROJECTION from, '
    'not a claim that every row attributed to that source carries this run''s id -- '
    'substance_moiety and identity_claim ACCUMULATE and hold rows from many runs by '
    'design. A run still in flight, or one that died, is in ingest_run_incomplete '
    'instead.';

-- db/052_spl_route_census_comments.sql
--
-- COMMENTS ONLY. No table, column, view, constraint or function changes shape
-- here; every statement below is a `COMMENT ON`. db/051 is applied and therefore
-- immutable, and a catalog comment is not a schema edit, so the correction lands
-- as its own numbered file rather than as a rewrite of the file it corrects.
--
-- ⇒ WHY: db/051 was written against the DESIGN round's route census and shipped
-- those figures into the database catalog, where `\d+` and every consumer reads
-- them. The ingest was then measured on the real releases and contradicted them,
-- and only `docs/` was updated. One of the numbers is wrong by two orders of
-- magnitude, and it is the one the design's own reader would plan against:
--
--   route                       db/051 said   measured (2026-08-27)
--   dailymed_active_moiety            6,498   10,555
--   dailymed_active_substance            16   23
--   absent_from_dailymed             19,862   30,386
--   unresolved                       14,680   92
--   gap view rows                    34,542   30,478
--
-- The `unresolved` figure is the important one. Its bucket is DEFINED as
-- "present, read, and still unkeyable", and the design's route table filed
-- 14,455 labels the probe had never READ into it. Scanned for real, 30,386 of
-- the 41,056 targets are simply absent from the current DailyMed release. The
-- recovery register is therefore **99.7% a RELEASE gap and 0.3% a REGISTRY
-- gap** -- the opposite of what a reader of the old comment would plan for, and
-- the opposite conclusion about whether drugref's identifier coverage is the
-- thing to fix. Full account: the 2026-08-27 results record, section 3.
--
-- ⇒ THE "INCLUDING 200 CARRYING A UNII DRUGREF DOES NOT HOLD" CLAUSE IS DROPPED,
-- not just re-numbered. db/051's version read "14,680 ... including 200", which
-- was at least arithmetically possible. Rewriting 14,680 as 92 while keeping the
-- clause would have asserted that 200 is a subset of 92 -- and the correction is
-- not the arithmetic anyway: those 200 labels offer a UNII that resolves to
-- nothing, so `resolve_subject` step 1 DECLINES them and they fall through to
-- DailyMed like any other unkeyed label. Many of them get a subject that way.
-- They belong to the 41,056 TARGETS, not to the unresolved bucket, and the
-- probe classifier that put them there branched on presence rather than on
-- resolution -- which is the same mistake that produced the 14,680.
--
-- The quote figures move too, and for a reason worth stating in the catalog
-- rather than only in a spec: 20.4% / 5.1 windows / 71.6% coverage were measured
-- with a matcher that ALSO held 8,534 class entries, whose names consumed 11,169
-- moiety spans. The shipped vocabulary is moiety-only and stores 20.5% over 5.2
-- windows covering 74.5%. Both are real measurements of different vocabularies.

COMMENT ON TABLE drugref.spl_label_subject IS
    'Which moiety a label is ABOUT, and by which route it was determined. '
    'Measured on the 2026-08-22 openFDA + 2026-08-21 DailyMed releases: 27,494 '
    'labels through openfda_unii, 10,555 + 23 through DailyMed, 30,386 absent '
    'from that release and 92 present, read and still unkeyable. The design '
    'round predicted 14,680 unresolved; that figure counted 14,455 labels its '
    'probe had never scanned, and the corrected census makes the recovery gap '
    '99.7% a release gap rather than a registry gap.';

COMMENT ON COLUMN drugref.spl_label_subject.route IS
    'How the subject was determined. SECOND HOME of '
    'drugref.ingest.spl_subject.SUBJECT_ROUTES -- the module the tuple actually '
    'lives in; this comment previously named drugref.ingest.spl_run, which '
    'exports no such attribute, so the pointer whose whole job is naming the '
    'vocabulary''s other home did not resolve. Pinned by a test comparing this '
    'CHECK''s definition against that tuple in both directions.';

COMMENT ON VIEW drugref.gap_unresolved_spl_subject IS
    'Labels carrying an interactions section whose subject drug is not '
    'determined, with their route and wording key. Measured on the 2026-08-27 '
    'run: 30,478 rows, of which 30,386 are absent_from_dailymed and 92 were '
    'read and are still unkeyable. A VIEW rather than minted questions: 30,478 immortal, externally '
    'citable question_uuids for a population that shrinks every time DailyMed '
    'publishes would be a promise the next release breaks. The 0.3%/99.7% split '
    'between registry gap and release gap is the reason this stays a view.';

COMMENT ON FUNCTION drugref.spl_wording_quote_budget() IS
    'Enforces the issue-154 quoted-window determination: per wording, the '
    'summed window length may not exceed ceil(0.25 * char_length), no two '
    'windows may overlap (so the sum IS the count of distinct characters), and '
    'no window may end past the wording. DEFERRED, so it is checked once at '
    'COMMIT over the whole wording rather than per row. The 0.25 has exactly '
    'two homes -- this function and drugref.ingest.spl_quote.QUOTE_SHARE -- and '
    'a test reads THIS definition out of pg_proc.prosrc and compares. It '
    'previously retyped the literal instead, which made the test a third home: '
    'changing this function to ceil(0.35 * ...) left the entire suite green.';

COMMENT ON TABLE drugref.spl_wording_quote IS
    'The bounded quoted window: +/-60 characters around the FIRST occurrence of '
    'each distinct moiety, in DOCUMENT order, to a hard budget of 25% of the '
    'section''s characters. Measured on the shipped moiety-only vocabulary over '
    '26,760 wordings: 20.5% of a section stored on average, 5.2 merged windows '
    'per wording, covering 74.5% of the moieties named, using 88.1% of the '
    'budget. (db/051 quoted 20.4% / 5.1 / 71.6% over 26,721 wordings; those were '
    'measured with a vocabulary that also held 8,534 class entries, whose names '
    'consumed 11,169 moiety spans.) The section text in full is NOT stored under '
    'either publisher''s reading -- see NOTICE for the per-column determination.';

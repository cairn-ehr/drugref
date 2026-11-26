-- db/087 -- the departure read and the acknowledgement as SQL functions, so the
-- Python CLI and the Rust reviewer GUI share one copy of each rule (#283).
--
-- WHY. db/083 put the ledger in SQL but left three rules in Python
-- (`graded_pairs.py`): which open episodes have DEPARTED, which drug LEFT in each
-- departed pair, and what an ACKNOWLEDGEMENT closes (with #289's bounded wait on
-- db/086's lock). The reviewer GUI is Rust and never runs Python, so acknowledging
-- from it needed those rules too. A Rust copy would be a second home for each, and
-- this repo has repeatedly lost rounds to one rule kept in two places. The owner
-- chose SQL functions (#283): `graded_pairs.departed`, `leavers` and `acknowledge`
-- are now thin callers of the functions below, and so is the reviewer service.
--
-- The bodies are the Python ones, ported. Nothing about what departs, who left, or
-- what an acknowledgement closes changes; the existing Python tests check the port.
-- One thing does change: `acknowledge`'s read and its INSERT now run in one
-- function call under the ledger lock, so the defensive "INSERT wrote nothing"
-- branch (a concurrent ack between read and write, #286 review) is one check: no
-- row written is NothingDeparted.
--
-- SQLSTATEs (class LG, "ledger"; I-Z classes are implementation-defined) so callers
-- can tell the refusals apart without parsing messages:
--   LG001  no ruling (any version) has that id in that grain     -> UnknownRuling
--   LG002  the drug is in no open departed episode of that key   -> NothingDeparted
--   LG003  the ledger lock was not free within the wait          -> LedgerBusy
--   LG004  no ingest release to record as reviewed_against


-- ============================================================================
-- 1. What departed (was `graded_pairs._DEPARTED_SQL`)
-- ============================================================================
-- Live, applying, non-orphaned rulings (both grains) with open episodes the view no
-- longer returns under the same ruling key. One row per ruling; the pairs come as
-- two parallel arrays (subject moiety, partner moiety), in observation order.
-- `now` is `curated_ddi_pair` reduced to (ruling key, pair), read ONCE per call
-- (MATERIALIZED) and filtered like the outer query, so the one-drug banner reduces
-- only that drug's rows. Unordered: callers ORDER BY reviewed_at DESC, rule_grain,
-- ruling_id DESC (most recent ruling first), as the Python read did.
CREATE OR REPLACE FUNCTION drugref.departed_graded_pairs(
    subject_filter uuid DEFAULT NULL, partner_filter uuid DEFAULT NULL)
RETURNS TABLE (
    rule_grain text, ruling_id bigint, subject_key uuid, object_class_uuid uuid,
    relationship text, severity text, reviewed_by text, reviewed_against text,
    reviewed_at timestamptz, backfilled boolean, first_seen_at timestamptz,
    subject_moieties uuid[], partner_moieties uuid[]) AS $$
WITH now AS MATERIALIZED (
    SELECT DISTINCT p.rule_grain,
           CASE p.rule_grain WHEN 'class_rule' THEN p.via_subject_class
                             ELSE p.subject_moiety END AS subject_key,
           p.via_class, p.relationship, p.subject_moiety, p.partner_moiety
    FROM   drugref.curated_ddi_pair p
    WHERE  (subject_filter IS NULL OR p.subject_moiety = subject_filter)
    AND    (partner_filter IS NULL OR p.partner_moiety = partner_filter)),
live AS (
    SELECT 'moiety_rule' AS rule_grain, 'curated_interaction' AS target_table,
           curated_interaction_id AS ruling_id, subject_moiety_uuid AS subject_key,
           object_class_uuid, relationship, applies, severity, reviewed_by,
           reviewed_against, reviewed_at
    FROM   drugref.curated_interaction WHERE superseded_by IS NULL
    UNION ALL
    SELECT 'class_rule', 'curated_class_interaction',
           curated_class_interaction_id, subject_class_uuid,
           object_class_uuid, relationship, applies, severity, reviewed_by,
           reviewed_against, reviewed_at
    FROM   drugref.curated_class_interaction WHERE superseded_by IS NULL)
SELECT l.rule_grain, l.ruling_id, l.subject_key, l.object_class_uuid,
       l.relationship, l.severity, l.reviewed_by, l.reviewed_against, l.reviewed_at,
       bool_or(o.captured = 'backfill_db083'), min(o.first_seen_at),
       array_agg(o.subject_moiety ORDER BY o.observation_id),
       array_agg(o.partner_moiety ORDER BY o.observation_id)
FROM   drugref.graded_pair_observation o
JOIN   live l
       ON  l.rule_grain = o.rule_grain AND l.subject_key = o.subject_key
       AND l.object_class_uuid = o.object_class_uuid
       AND l.relationship = o.relationship
WHERE  (subject_filter IS NULL OR o.subject_moiety = subject_filter)
AND    (partner_filter IS NULL OR o.partner_moiety = partner_filter)
-- open: no acknowledgement closes this episode
AND    NOT EXISTS (SELECT 1 FROM drugref.graded_pair_departure_ack a
                   WHERE a.observation_id = o.observation_id)
-- departed: the view no longer grades this pair under this key
AND    NOT EXISTS (SELECT 1 FROM now n
                   WHERE n.rule_grain = o.rule_grain
                   AND   n.subject_key = o.subject_key
                   AND   n.via_class = o.object_class_uuid
                   AND   n.relationship = o.relationship
                   AND   n.subject_moiety = o.subject_moiety
                   AND   n.partner_moiety = o.partner_moiety)
-- withdrawn: an applies = false ruling grades nothing, so nothing fell out of it
AND    l.applies
-- orphaned: the candidate is gone, which #121's banner already reports
AND    NOT EXISTS (SELECT 1 FROM drugref.curated_target_unresolved u
                   WHERE u.target_table = l.target_table
                   AND   u.object_uuid = l.object_class_uuid
                   AND   u.relationship = l.relationship
                   AND   CASE l.target_table WHEN 'curated_interaction'
                              THEN u.subject_moiety ELSE u.subject_class END
                         = l.subject_key)
GROUP  BY l.rule_grain, l.ruling_id, l.subject_key, l.object_class_uuid,
          l.relationship, l.severity, l.reviewed_by, l.reviewed_against,
          l.reviewed_at
$$ LANGUAGE sql STABLE;

COMMENT ON FUNCTION drugref.departed_graded_pairs(uuid, uuid) IS
    'Live, applying, non-orphaned rulings with open graded_pair_observation episodes '
    'the view no longer grades under the same key; subject_filter / partner_filter '
    'narrow by the pair''s subject / partner moiety (db/087, #283; read by '
    'graded_pairs.departed and the reviewer service).';


-- ============================================================================
-- 2. Who left (was `graded_pairs.leavers`)
-- ============================================================================
-- For a departure's pairs, the drugs that actually LEFT. Class grain: a pair's
-- subject left if it is no longer a subject-side member of the live ruling in
-- `class_rule_member`, likewise the partner on its side (both can be gone). Moiety
-- grain: the subject is the ruling's own drug, so the partner left. A pair with no
-- side gone (cause unknown) names its partner, so the result is never empty for a
-- non-empty pair list.
CREATE OR REPLACE FUNCTION drugref.departure_leavers(
    leaver_grain text, live_ruling_id bigint, subjects uuid[], partners uuid[])
RETURNS SETOF uuid AS $$
SELECT DISTINCT out.moiety
FROM   unnest(subjects, partners) AS t(s, p)
CROSS  JOIN LATERAL (
    SELECT NOT EXISTS (SELECT 1 FROM drugref.class_rule_member m
                       WHERE m.curated_class_interaction_id = live_ruling_id
                       AND m.side = 'subject' AND m.moiety_uuid = t.s) AS s_gone,
           NOT EXISTS (SELECT 1 FROM drugref.class_rule_member m
                       WHERE m.curated_class_interaction_id = live_ruling_id
                       AND m.side = 'partner' AND m.moiety_uuid = t.p) AS p_gone) g
CROSS  JOIN LATERAL (
    SELECT t.s WHERE leaver_grain = 'class_rule' AND g.s_gone
    UNION ALL
    -- the partner: always at the moiety grain; at the class grain when it left,
    -- or when neither side did (cause unknown)
    SELECT t.p WHERE leaver_grain <> 'class_rule' OR g.p_gone OR NOT g.s_gone
) AS out(moiety)
$$ LANGUAGE sql STABLE;

COMMENT ON FUNCTION drugref.departure_leavers(text, bigint, uuid[], uuid[]) IS
    'The drugs that left in one departure''s (subject, partner) pairs; never empty '
    'for a non-empty pair list (db/087, #283; read by graded_pairs.leavers and the '
    'reviewer service).';


-- ============================================================================
-- 3. The release a GUI acknowledgement is recorded against (new, #283)
-- ============================================================================
-- The CLI makes the curator type --reviewed-against. The GUI does not (owner,
-- #283): it records the ingest run(s) whose CURRENT memberships of the ruling's
-- classes the reviewer accepted the departure against, as 'SOURCE release' joined
-- by ' / ' (the decision path's separator). Moiety grain: the object class. Class
-- grain: the subject and object classes. A class with no members left names the
-- latest finished run of its own source instead. NULL for an unknown ruling.
CREATE OR REPLACE FUNCTION drugref.departure_reviewed_against(
    release_grain text, release_ruling_id bigint)
RETURNS text AS $$
WITH classes AS (
    SELECT object_class_uuid AS class_uuid FROM drugref.curated_interaction
    WHERE  release_grain = 'moiety_rule' AND curated_interaction_id = release_ruling_id
    UNION
    SELECT unnest(ARRAY[subject_class_uuid, object_class_uuid])
    FROM   drugref.curated_class_interaction
    WHERE  release_grain = 'class_rule'
    AND    curated_class_interaction_id = release_ruling_id),
runs AS (
    SELECT m.ingest_run FROM drugref.class_membership m
    WHERE  m.class_uuid IN (SELECT class_uuid FROM classes)
    UNION
    SELECT (SELECT max(r.ingest_run_id) FROM drugref.ingest_run r
            WHERE r.source = c.source AND r.finished_at IS NOT NULL)
    FROM   drugref.substance_class c
    WHERE  c.class_uuid IN (SELECT class_uuid FROM classes)
    AND    NOT EXISTS (SELECT 1 FROM drugref.class_membership m
                       WHERE m.class_uuid = c.class_uuid))
SELECT string_agg(DISTINCT r.source || ' ' || r.upstream_release, ' / ')
FROM   runs JOIN drugref.ingest_run r ON r.ingest_run_id = runs.ingest_run
$$ LANGUAGE sql STABLE;

COMMENT ON FUNCTION drugref.departure_reviewed_against(text, bigint) IS
    'The ''SOURCE release'' list a GUI departure acknowledgement is recorded against: '
    'the runs holding the ruling''s classes'' current memberships, or a memberless '
    'class''s source''s latest finished run (db/087, #283).';


-- ============================================================================
-- 4. The acknowledgement (was `graded_pairs.acknowledge` + #289's bounded lock)
-- ============================================================================
-- Acknowledges every open, currently-departed episode of the key of
-- `ack_ruling_id` (any version's id names its key: a curator may paste a superseded
-- id from an old banner) in which `drug` is the subject or partner moiety; returns
-- the distinct pairs closed, each with the reviewed_against recorded (the same on
-- every row). Every open episode of those pairs is closed,
-- duplicates included.
--
-- THE LOCK FIRST (#287): db/086's ledger lock is taken before any read, so an
-- ingest re-grading the pair either committed first (the read sees it graded) or
-- waits for this transaction and opens a new episode after the ack. The lock is
-- held until the CALLER commits or rolls back.
--
-- BOUNDED (#289): the wait gives up after `lock_wait` (NULL = 30 s; an ingest's
-- commit-time refresh holds the lock ~5 s, #285, so longer means a transaction was
-- left open) and raises LG003 naming each holder as 'backend PID (ROLE, STATE)'.
-- PostgreSQL hides another role's state without pg_read_all_stats, so the role is
-- always shown. The wait runs in an exception block, i.e. a subtransaction: a
-- timeout rolls only that back, and the caller's lock_timeout is put back on
-- success too. TRAP: the holder query repeats db/086's lock key text; if one
-- changes, change both (test_graded_pairs_races fails if they differ).
--
-- `ack_reviewed_against` NULL means "derive it" (section 3), AFTER the lock, so the
-- release recorded is the one the departure was read against. A blank rationale
-- trips the table's CHECK.
CREATE OR REPLACE FUNCTION drugref.acknowledge_departure(
    ack_grain text, ack_ruling_id bigint, drug uuid, ack_rationale text,
    ack_reviewed_by text, ack_reviewed_against text, lock_wait text DEFAULT NULL)
RETURNS TABLE (acked_subject uuid, acked_partner uuid, acked_against text) AS $$
DECLARE
    previous_timeout text := current_setting('lock_timeout');
    wait text := coalesce(lock_wait, '30s');
    key_subject uuid;
    key_object uuid;
    key_relationship text;
    against text := ack_reviewed_against;
    holders text;
BEGIN
    BEGIN
        PERFORM set_config('lock_timeout', wait, true);
        PERFORM drugref.lock_graded_pair_ledger();
    EXCEPTION WHEN lock_not_available THEN
        SELECT string_agg(format('backend %s (%s, %s)', l.pid, a.usename,
                   coalesce(a.state, 'state hidden: needs pg_read_all_stats')),
                   ', ' ORDER BY l.pid)
        INTO   holders
        FROM   pg_locks l JOIN pg_stat_activity a USING (pid)
        WHERE  l.locktype = 'advisory' AND l.granted
        AND    l.database = (SELECT oid FROM pg_database
                             WHERE datname = current_database())
        -- a bigint advisory key shows in pg_locks as two 32-bit halves
        AND    ((l.classid::bigint << 32) | l.objid::bigint)
               = hashtextextended('drugref.graded_pair_observation', 0);
        RAISE EXCEPTION USING ERRCODE = 'LG003', MESSAGE = format(
            'the graded-pair ledger lock is held by %s; waited %s, longer than any '
            'ingest commit takes, so a transaction was probably left open. End it '
            '(pg_terminate_backend(pid) as a last resort) and retry (db/086, #289)',
            coalesce(holders, 'a transaction that has since ended'), wait);
    END;
    PERFORM set_config('lock_timeout', previous_timeout, true);

    IF ack_grain = 'moiety_rule' THEN
        SELECT subject_moiety_uuid, object_class_uuid, relationship
        INTO   key_subject, key_object, key_relationship
        FROM   drugref.curated_interaction
        WHERE  curated_interaction_id = ack_ruling_id;
    ELSIF ack_grain = 'class_rule' THEN
        SELECT subject_class_uuid, object_class_uuid, relationship
        INTO   key_subject, key_object, key_relationship
        FROM   drugref.curated_class_interaction
        WHERE  curated_class_interaction_id = ack_ruling_id;
    END IF;
    IF key_subject IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'LG001',
            MESSAGE = format('no %s with id %s', ack_grain, ack_ruling_id);
    END IF;

    IF against IS NULL THEN
        against := drugref.departure_reviewed_against(ack_grain, ack_ruling_id);
        IF against IS NULL THEN
            RAISE EXCEPTION USING ERRCODE = 'LG004', MESSAGE = format(
                'no finished ingest run to record %s %s''s acknowledgement against',
                ack_grain, ack_ruling_id);
        END IF;
    END IF;

    RETURN QUERY
    WITH departed AS (
        SELECT d.rule_grain, d.subject_key, d.object_class_uuid, d.relationship,
               unnest(d.subject_moieties) AS s, unnest(d.partner_moieties) AS p
        FROM   drugref.departed_graded_pairs(drug, NULL) d
        UNION
        SELECT d.rule_grain, d.subject_key, d.object_class_uuid, d.relationship,
               unnest(d.subject_moieties), unnest(d.partner_moieties)
        FROM   drugref.departed_graded_pairs(NULL, drug) d),
    written AS (
        INSERT INTO drugref.graded_pair_departure_ack
               (observation_id, rationale, reviewed_by, reviewed_against)
        SELECT o.observation_id, ack_rationale, ack_reviewed_by, against
        FROM   drugref.graded_pair_observation o
        JOIN   departed dp
               ON  dp.rule_grain = o.rule_grain AND dp.subject_key = o.subject_key
               AND dp.object_class_uuid = o.object_class_uuid
               AND dp.relationship = o.relationship
               AND dp.s = o.subject_moiety AND dp.p = o.partner_moiety
        WHERE  o.rule_grain = ack_grain AND o.subject_key = key_subject
        AND    o.object_class_uuid = key_object AND o.relationship = key_relationship
        AND    NOT EXISTS (SELECT 1 FROM drugref.graded_pair_departure_ack a
                           WHERE a.observation_id = o.observation_id)
        RETURNING observation_id)
    SELECT DISTINCT o.subject_moiety, o.partner_moiety, against
    FROM   written w JOIN drugref.graded_pair_observation o USING (observation_id);
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'LG002', MESSAGE = format(
            '%s is in no open departed episode of %s %s', drug, ack_grain,
            ack_ruling_id);
    END IF;
END;
$$ LANGUAGE plpgsql VOLATILE;

COMMENT ON FUNCTION drugref.acknowledge_departure(text, bigint, uuid, text, text,
                                                  text, text) IS
    'Closes every open departed episode of a ruling key involving one drug, under '
    'db/086''s ledger lock with a bounded wait; NULL reviewed_against is derived. '
    'Raises LG001 unknown ruling, LG002 nothing departed, LG003 lock busy, LG004 no '
    'release (db/087, #283; called by graded_pairs.acknowledge and the reviewer '
    'service). Does not commit.';

COMMENT ON FUNCTION drugref.lock_graded_pair_ledger() IS
    'Takes the ledger''s transaction-scoped advisory lock; refuses outside READ '
    'COMMITTED. Called by refresh_graded_pairs(), refresh_graded_pairs_for_key() '
    'and acknowledge_departure() before they read (db/086, #287; db/087 moved the '
    'acknowledgement into SQL); any new writer of graded_pair_observation or '
    'graded_pair_departure_ack must call it too.';

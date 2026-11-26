-- db/088 -- departure acknowledgements are signable (#284).
--
-- THE DEFECT. A graded_pair_departure_ack row (db/083) silences a CHANGED SINCE
-- REVIEW banner. It carries reviewed_by / reviewed_against but no signature, so an
-- inserted ack (silencing a real departure) or an altered rationale is not
-- tamper-evident, unlike the curated rulings db/030 lets curators sign.
--
-- THE FIX. A fifth curator signature target kind. assertion_signature, the key
-- registry and curated_signature_status (db/030, db/048) are already generic over
-- target_kind, so `drugref sign` / `drugref verify` and the reviewer GUI work
-- through the one catalog row below.
--
-- OWNER RULING (2026-11-26): acks are signed one by one by curators and are NOT
-- part of a signed release manifest (`releases._CURATED_KINDS`). A release attests
-- to the rulings consumers read; an ack changes only what reviewers are shown.
-- Acks are in the release archive either way (db/083).

-- ============================================================================
-- 1. The signing target: one row per ack, its episode's CONTENT, not its id
-- ============================================================================
-- WHY A VIEW. The ack names its episode by observation_id, a database-local id the
-- release replay renumbers (it loads each ack onto the replayed episode of the same
-- ruling key and pair). Signing the id would make every signature fail after a
-- replay and would not say which pair the curator accepted. So the signed fields are
-- the episode's ruling key and pair (graded_pair_observation's own key columns) plus
-- the curator's fields. Two observation columns are deliberately NOT signed:
-- `captured` and `first_seen_at` are written by whichever database ran the refresh,
-- so a replay records its own values for them.
--
-- ack_id is the target id (assertion_signature.target_id), as every kind's surrogate
-- key is; the replay keeps ack ids verbatim, so signatures still point at their ack.
--
-- COLUMN ORDER IS NOT THE SIGNED ORDER: signing_contexts.GRADED_PAIR_DEPARTURE_ACK_V1
-- is, and tests/test_signing_payload_coverage.py fails when this view gains a column
-- that list does not name.
CREATE OR REPLACE VIEW drugref.graded_pair_departure_ack_payload AS
SELECT a.ack_id,
       o.rule_grain, o.subject_key, o.object_class_uuid, o.relationship,
       o.subject_moiety, o.partner_moiety,
       a.rationale, a.reviewed_by, a.reviewed_against, a.acknowledged_at
FROM   drugref.graded_pair_departure_ack a
JOIN   drugref.graded_pair_observation o ON o.observation_id = a.observation_id;

COMMENT ON VIEW drugref.graded_pair_departure_ack_payload IS
    'The signing target of kind graded_pair_departure_ack (db/088, #284): one row per '
    'acknowledgement, naming its episode by content (ruling key and pair), never by '
    'the database-local observation_id a replay renumbers.';

INSERT INTO drugref.signature_target_kind
    (target_kind, target_table, pk_column, payload_context)
VALUES
    ('graded_pair_departure_ack', 'graded_pair_departure_ack_payload', 'ack_id',
     'graded_pair_departure_ack/v1')
ON CONFLICT (target_kind) DO NOTHING;

-- ============================================================================
-- 2. The read both callers share: every ack with its registry signature status
-- ============================================================================
-- `drugref audit departures` and the reviewer service's unsigned list read this, so
-- "unsigned" means one thing in both. `signature_status` is
-- curated_signature_status' vocabulary plus `unsigned` for an ack with no signature
-- row. Like that view it is REGISTRY policy, not cryptography: `drugref verify`
-- checks the mathematics. An ack needs a signature exactly when this reads anything
-- but `signed` (no signature by a key the registry does not object to), the rule the
-- GUI's pending-signature list uses for rulings.
CREATE OR REPLACE VIEW drugref.departure_ack_signature AS
SELECT p.*,
       COALESCE(s.signature_status, 'unsigned') AS signature_status,
       COALESCE(s.signature_count, 0) AS signature_count
FROM   drugref.graded_pair_departure_ack_payload p
LEFT   JOIN drugref.curated_signature_status s
       ON s.target_kind = 'graded_pair_departure_ack' AND s.target_id = p.ack_id;

COMMENT ON VIEW drugref.departure_ack_signature IS
    'Every departure acknowledgement with its registry signature status (db/088): '
    'curated_signature_status'' vocabulary plus unsigned. Registry policy, not '
    'cryptographic verification (run drugref verify).';

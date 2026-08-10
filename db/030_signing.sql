-- db/030_signing.sql -- slice 5c.4: signing the curated overlay.
--
-- TWO LAYERS OVER ONE MECHANISM. A per-row curator attestation and a per-release
-- institutional manifest are both `assertion_signature` rows over a canonical payload,
-- verified by one code path. That is the payoff of detaching the signature from the row
-- rather than adding a column to db/029 (spec 3): a column would have had to exist at
-- INSERT time, would have permitted exactly one signature per row, and would have done
-- nothing for the release layer.
--
-- WHAT SIGNING DOES NOT DO, stated here because the word invites over-reading: an
-- attacker with database write access can still INSERT unsigned curated rows (which
-- read `unsigned` -- the honest label), and a SUPERUSER can drop the triggers below
-- outright. That is issue 2's TRUNCATE + owner-role bypass and this file does not close
-- it. Signing converts "trust the database" into "trust the key holders", which is a
-- real reduction and not the same as making the database tamper-proof.

-- ============================================================================
-- 1. signing_key_status_kind -- the revocation rule, as DATA
-- ============================================================================
-- TWO BOOLEANS, AND THE SECOND IS NOT REDUNDANT. `invalidates_all_signatures` says
-- whether a revocation destroys evidence retrospectively; `is_revocation` says whether
-- `status_from` is an END boundary at all. Without the second, an active key's
-- status_from -- its registration time -- would expire every signature it ever made,
-- because every signature is necessarily later than the registration.
--
-- HELD AS DATA rather than as a CHECK plus a Python if-statement, for db/006's reason,
-- now applied a fifth time: the rule a verifier branches on is exactly the thing that
-- drifts when it is written down twice. ci_axis.expands_descendants and
-- class_expansion_policy are the precedents -- a rule a pharmacist can read.
CREATE TABLE IF NOT EXISTS drugref.signing_key_status_kind (
    status                     text    PRIMARY KEY,
    is_revocation              boolean NOT NULL,
    invalidates_all_signatures boolean NOT NULL,
    note                       text    NOT NULL
);

INSERT INTO drugref.signing_key_status_kind
    (status, is_revocation, invalidates_all_signatures, note)
VALUES
    ('active', false, false,
     'In use. status_from is the registration time, not an expiry.'),
    ('rotated', true, false,
     'Replaced by a new key -- a new laptop, a scheduled rotation. TIME-SCOPED: '
     'signatures made before status_from still verify, because the holder''s prior '
     'work is unaffected by their changing keys.'),
    ('retired', true, false,
     'The holder is no longer curating. Time-scoped for the same reason as rotated: '
     'a curator leaving does not make their past clinical judgements unsound.'),
    ('compromised', true, true,
     'The private key may be in other hands. BLANKET: every signature this key ever '
     'made is suspect regardless of signed_at, because after a compromise there is no '
     'way to tell the holder''s signatures from the attacker''s. The consequence is a '
     're-review queue, not a silent mass invalidation -- the read views keep serving '
     'these rows, labelled.')
ON CONFLICT (status) DO NOTHING;

COMMENT ON TABLE drugref.signing_key_status_kind IS
    'The revocation rule as data. is_revocation says whether status_from is an END '
    'boundary (an active key''s is its registration time, so without this every '
    'signature ever made would expire); invalidates_all_signatures says whether the '
    'revocation destroys evidence retrospectively. NEITHER HAS A DEFAULT -- a fifth '
    'status must not inherit a guess about whether it invalidates a curator''s work.';

-- ============================================================================
-- 2. signature_target_kind -- what a signature may point at
-- ============================================================================
-- ONE HOME for the mapping from a target kind to its table, key column and canonical
-- context, so a fourth kind is one INSERT here rather than an edit in Python, in SQL
-- and in a CHECK.
CREATE TABLE IF NOT EXISTS drugref.signature_target_kind (
    target_kind     text PRIMARY KEY,
    target_table    text NOT NULL,
    pk_column       text NOT NULL,
    payload_context text NOT NULL
);

INSERT INTO drugref.signature_target_kind
    (target_kind, target_table, pk_column, payload_context)
VALUES
    ('curated_interaction', 'curated_interaction', 'curated_interaction_id',
     'curated_interaction/v1'),
    ('curated_condition', 'curated_condition', 'curated_condition_id',
     'curated_condition/v1'),
    ('release_manifest', 'release_manifest', 'manifest_id', 'release_manifest/v1')
ON CONFLICT (target_kind) DO NOTHING;

-- ============================================================================
-- 3. signing_key -- the registry, on db/020's overlay floor (its EIGHTH table)
-- ============================================================================
-- REVOCATION IS A CORRECTION, not a column edit: INSERT the new status, then point the
-- live row at it via overlay.supersede. The full status history of a key is therefore
-- readable, which is the only thing that makes "was this key already revoked when that
-- signature was made?" answerable.
--
-- The natural key is `key_fingerprint` and it is deliberately NOT UNIQUE -- a correction
-- keeps the same key by definition, so both rows are briefly live. The partial index
-- plus the deferred trigger enforce single-live; adding UNIQUE (key_fingerprint) "for
-- safety" would forbid every revocation. db/027's note, one table on.
CREATE TABLE IF NOT EXISTS drugref.signing_key (
    signing_key_id  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key_fingerprint text        NOT NULL,
    public_key      bytea       NOT NULL,
    algorithm       text        NOT NULL,
    -- FREE TEXT, and deliberately not constrained to match curated_*.reviewed_by.
    -- Enforcing the match would put one string in two places under a constraint a
    -- legitimate name change breaks; a verifier reports both, and a mismatch is a fact
    -- a consumer can act on rather than an error the schema should refuse.
    holder          text        NOT NULL,
    status          text        NOT NULL
                                REFERENCES drugref.signing_key_status_kind(status),
    status_from     timestamptz NOT NULL,
    registered_by   text        NOT NULL,
    registered_at   timestamptz NOT NULL DEFAULT now(),
    superseded_by   bigint      REFERENCES drugref.signing_key(signing_key_id),
    CONSTRAINT signing_key_algorithm CHECK (algorithm IN ('Ed25519')),
    -- THE FINGERPRINT IS THE IDENTITY A SIGNATURE NAMES, in a text column. A truncated
    -- or upper-case value is a row that silently matches no signature -- which is
    -- indistinguishable from a key nobody registered, so it reports UNKNOWN_KEY forever
    -- rather than failing loudly.
    CONSTRAINT signing_key_fingerprint_shape
        CHECK (key_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT signing_key_public_key_length CHECK (octet_length(public_key) = 32)
);

DROP TRIGGER IF EXISTS signing_key_append_only ON drugref.signing_key;
CREATE TRIGGER signing_key_append_only
    BEFORE UPDATE OR DELETE ON drugref.signing_key
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'signing_key_id', 'key_fingerprint');

-- DEFERRED, because a correction is momentarily TWO live rows -- between the INSERT and
-- the UPDATE that supersedes -- and an immediate check would reject the only sequence
-- that can express one.
DROP TRIGGER IF EXISTS signing_key_single_live ON drugref.signing_key;
CREATE CONSTRAINT TRIGGER signing_key_single_live
    AFTER INSERT OR UPDATE ON drugref.signing_key
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'key_fingerprint');

-- PARTIAL and NOT UNIQUE, matching the trigger's predicate exactly. Nothing but the
-- trigger reads it, so a test asserts it by name -- and since the gates round the
-- covered set is DERIVED from pg_trigger.tgargs, so this table is picked up
-- automatically rather than by editing three literal lists.
CREATE INDEX IF NOT EXISTS signing_key_live_key
    ON drugref.signing_key (key_fingerprint)
    WHERE superseded_by IS NULL;

COMMENT ON TABLE drugref.signing_key IS
    'CURATED, APPEND-ONLY: the public keys drugref trusts, and their status history. '
    'The private half NEVER enters this database or any drugref infrastructure -- that '
    'is the whole point of the row layer, and it is what an insider with full write '
    'access cannot forge. Revocation is a correction (insert, then supersede), never an '
    'UPDATE, so the history that DATES a revocation survives. THE TRUST ROOT IS AN '
    'OPERATOR: a key is trusted because someone with database access registered it. '
    'There is no enrolment protocol and no certificate chain.';

-- ============================================================================
-- 4. forbid_any_rewrite -- strictly insert-only
-- ============================================================================
-- STRICTER THAN forbid_overlay_rewrite, which exists to permit exactly one column
-- (superseded_by) to change. The three tables below have no superseded_by and need
-- none -- and the question was ASKED rather than assumed, because this project's
-- standing finding is that supersession alone withdraws nothing, and four tables have
-- needed a ruling column for it (additive_effect.accumulates,
-- interaction_group_member.satisfies_role, interaction_group_assertion.applies,
-- class_expansion_policy.decision = 'withdrawn').
--
-- THE ANSWER FOR A SIGNATURE IS THAT RETRACTION HAPPENS IN THE LAYERS EITHER SIDE OF
-- IT, never here. A curator who signed a judgement they now disagree with corrects the
-- JUDGEMENT -- a new curated row, the predecessor superseded and out of the read path
-- -- and the old signature remains a true statement about what they attested on that
-- date, which is exactly what a row that fired alerts for six months needs. A key whose
-- signatures must all be repudiated is handled at the key layer by `compromised`. A
-- signature is a historical fact about a moment, not an assertion that can be revised.
CREATE OR REPLACE FUNCTION drugref.forbid_any_rewrite() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'drugref.% is insert-only: % forbidden', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION drugref.forbid_any_rewrite() IS
    'The insert-only floor: no UPDATE of any column, no DELETE, ever. Distinct from '
    'forbid_overlay_rewrite, which permits superseded_by to change. For tables whose '
    'rows are historical FACTS rather than revisable assertions.';

-- ============================================================================
-- 5. assertion_signature
-- ============================================================================
-- target_id IS A POINTER, NOT CONTENT, and is deliberately absent from the signed
-- payload: GENERATED ALWAYS AS IDENTITY values are local to one database, so signing
-- one would break a signature carried into another. Verification re-derives the payload
-- from the row's CONTENT and checks the signature over that.
--
-- signed_at IS INSIDE THE SIGNED PAYLOAD, so it cannot be edited to walk a signature
-- across a revocation boundary. recorded_at is this database's own clock and is NOT
-- signed -- the gap between the two is a backdating signal, reported by
-- signature_backdated.
CREATE TABLE IF NOT EXISTS drugref.assertion_signature (
    signature_id    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_kind     text        NOT NULL
                                REFERENCES drugref.signature_target_kind(target_kind),
    target_id       bigint      NOT NULL,
    payload_context text        NOT NULL,
    payload_digest  bytea       NOT NULL,
    -- NO FOREIGN KEY into signing_key, and that is deliberate. A signature naming a key
    -- nobody has registered is an ORDINARY finding -- it is the UNKNOWN_KEY verdict --
    -- and an FK would make recording it impossible, which would mean a node could not
    -- even store the evidence that an unknown key signed something.
    key_fingerprint text        NOT NULL,
    algorithm       text        NOT NULL,
    signature       bytea       NOT NULL,
    signed_at       timestamptz NOT NULL,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT assertion_signature_algorithm CHECK (algorithm IN ('Ed25519')),
    CONSTRAINT assertion_signature_digest_length
        CHECK (octet_length(payload_digest) = 32),
    CONSTRAINT assertion_signature_length CHECK (octet_length(signature) = 64),
    CONSTRAINT assertion_signature_fingerprint_shape
        CHECK (key_fingerprint ~ '^[0-9a-f]{64}$'),
    -- A DEDUPE GUARD, not an identity: re-signing with a later signed_at yields a
    -- different payload and therefore a different digest, so a second row is legitimate
    -- and both are true. This refuses only recording the SAME attestation twice.
    CONSTRAINT assertion_signature_unique
        UNIQUE (target_kind, target_id, key_fingerprint, payload_digest)
);

DROP TRIGGER IF EXISTS assertion_signature_insert_only ON drugref.assertion_signature;
CREATE TRIGGER assertion_signature_insert_only
    BEFORE UPDATE OR DELETE ON drugref.assertion_signature
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

-- The lookup every verification does, and the one the read views join on. Read only by
-- the planner, so a test asserts it by name -- as with the live-key indexes.
CREATE INDEX IF NOT EXISTS assertion_signature_by_target
    ON drugref.assertion_signature (target_kind, target_id);
-- The lookup a key revocation does: "what did this key sign?" is the re-review queue a
-- compromise produces, and without this it is a sequential scan.
CREATE INDEX IF NOT EXISTS assertion_signature_by_key
    ON drugref.assertion_signature (key_fingerprint);

COMMENT ON TABLE drugref.assertion_signature IS
    'INSERT-ONLY: no UPDATE of any column, no DELETE. A signature is a historical fact '
    'about a moment, not a revisable assertion -- a mis-signed judgement is corrected '
    'at the curated row, and a compromised key is repudiated at signing_key. target_id '
    'is a POINTER and is NOT in the signed payload: identity values are local to a '
    'database and the signature must survive being carried into another. signed_at IS '
    'signed; recorded_at is not, and the gap between them is what signature_backdated '
    'reports. NO FK to signing_key: a signature by an unregistered key is the '
    'UNKNOWN_KEY verdict, and must be storable.';

-- ============================================================================
-- 6. release_manifest + release_manifest_entry
-- ============================================================================
-- A CONTENT MANIFEST, not a signature over shipped bytes. A transport signature dies at
-- load time -- once the data is in a database it can never be re-checked against those
-- bytes -- and "is this table still what drugref published?" is the question that
-- matters for the following several years. Because the manifest ENUMERATES,
-- verification is bidirectional and catches OMISSION as well as alteration.
CREATE TABLE IF NOT EXISTS drugref.release_manifest (
    manifest_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- drugref's OWN version string, STATED by the operator and never derived, exactly
    -- as ingest_run's release tags are (PROJECT-NOTES: "stated, never parsed from a
    -- filename"). UNIQUE, so one tag cannot name two manifests.
    release_tag       text        NOT NULL UNIQUE,
    manifest_digest   bytea       NOT NULL,
    -- REDUNDANT WITH THE ENTRIES ON PURPOSE: a group truncated at its END is otherwise
    -- detectable only by recomputing the whole digest, and a scalar count makes that
    -- specific failure nameable.
    row_count         integer     NOT NULL,
    upstream_releases jsonb       NOT NULL,
    published_by      text        NOT NULL,
    published_at      timestamptz NOT NULL,
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT release_manifest_row_count CHECK (row_count >= 0),
    CONSTRAINT release_manifest_digest_length
        CHECK (octet_length(manifest_digest) = 32)
);

-- KEYED ON THE NATURAL KEY, NEVER ON target_id. target_id is a database-local
-- GENERATED ALWAYS AS IDENTITY value (section 5 says why it stays out of a signed
-- payload), so keying a manifest on it would break the release layer in exactly the
-- situation it exists for: a node that REBUILT rather than restored assigns different
-- identity values, and every entry would fail to match. natural_key is stable across
-- databases because moiety_uuid is immortal and class_uuid/condition_uuid are
-- deterministic UUIDv5 mints -- and it is what makes `altered` nameable at all, since
-- pairing on the digest alone could only ever report one drop plus one addition and
-- leave a consumer to guess whether they were the same row.
--
-- target_id survives as an UNSIGNED convenience column so an operator can join an entry
-- back to the local row it describes. Nothing verifies against it.
CREATE TABLE IF NOT EXISTS drugref.release_manifest_entry (
    manifest_id     bigint NOT NULL REFERENCES drugref.release_manifest(manifest_id),
    target_kind     text   NOT NULL
                           REFERENCES drugref.signature_target_kind(target_kind),
    natural_key     text   NOT NULL,
    target_id       bigint NOT NULL,
    payload_context text   NOT NULL,
    payload_digest  bytea  NOT NULL,
    PRIMARY KEY (manifest_id, target_kind, natural_key),
    CONSTRAINT release_manifest_entry_digest_length
        CHECK (octet_length(payload_digest) = 32)
);

DROP TRIGGER IF EXISTS release_manifest_insert_only ON drugref.release_manifest;
CREATE TRIGGER release_manifest_insert_only
    BEFORE UPDATE OR DELETE ON drugref.release_manifest
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

DROP TRIGGER IF EXISTS release_manifest_entry_insert_only
    ON drugref.release_manifest_entry;
CREATE TRIGGER release_manifest_entry_insert_only
    BEFORE UPDATE OR DELETE ON drugref.release_manifest_entry
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.release_manifest IS
    'INSERT-ONLY. One published drugref release: an enumeration of every live curated '
    'assertion at publication with its content digest, plus a snapshot of which '
    'upstream releases were loaded. Signed by the institutional key as an '
    'assertion_signature row with target_kind = ''release_manifest'' -- ONE mechanism '
    'carries both the row layer and this one. Verification is BIDIRECTIONAL: a row the '
    'manifest lists and the database lacks is a DROP, a live row the manifest omits is '
    'an ADDITION, and a digest mismatch is an ALTERATION.';

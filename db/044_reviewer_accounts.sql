-- db/044_reviewer_accounts.sql -- authenticated reviewer identities and sessions.
--
-- This migration deliberately ships with NO account seed. An empty administrator
-- set is the first-run state detected by the review service; the bootstrap endpoint
-- creates the first administrator in one advisory-locked transaction. Passwords are
-- Argon2id PHC strings, session secrets are represented only by SHA-256 digests, and
-- every durable identity/profile/credential fact keeps its history.

-- Roles are data so authorisation queries and the database admit the same vocabulary.
CREATE TABLE drugref.reviewer_role_kind (
    role text PRIMARY KEY,
    note text NOT NULL
);

INSERT INTO drugref.reviewer_role_kind (role, note) VALUES
    ('reviewer', 'May use reviewer workflows but may not administer accounts.'),
    ('administrator', 'May create and administer reviewer accounts.')
ON CONFLICT (role) DO NOTHING;

CREATE TRIGGER reviewer_role_kind_insert_only
    BEFORE UPDATE OR DELETE ON drugref.reviewer_role_kind
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

-- The stable account: username is identity, not editable profile text.
CREATE TABLE drugref.reviewer_account (
    reviewer_uuid uuid PRIMARY KEY,
    username text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid REFERENCES drugref.reviewer_account(reviewer_uuid),
    CONSTRAINT reviewer_account_username_shape
        CHECK (username ~ '^[a-z][a-z0-9._-]{2,63}$')
);

CREATE TRIGGER reviewer_account_insert_only
    BEFORE UPDATE OR DELETE ON drugref.reviewer_account
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

-- Names, biography, role and enabled/disabled status are revisioned together. This
-- avoids a role UPDATE erasing who had administrator authority at an earlier time.
CREATE TABLE drugref.reviewer_profile (
    reviewer_profile_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reviewer_uuid uuid NOT NULL REFERENCES drugref.reviewer_account(reviewer_uuid),
    full_name text NOT NULL,
    qualifications text NOT NULL DEFAULT '',
    bio_markdown text NOT NULL DEFAULT '',
    role text NOT NULL REFERENCES drugref.reviewer_role_kind(role),
    active boolean NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    recorded_by uuid NOT NULL REFERENCES drugref.reviewer_account(reviewer_uuid),
    superseded_by bigint REFERENCES drugref.reviewer_profile(reviewer_profile_id),
    CONSTRAINT reviewer_profile_full_name_present CHECK (btrim(full_name) <> ''),
    CONSTRAINT reviewer_profile_full_name_length CHECK (char_length(full_name) <= 200),
    CONSTRAINT reviewer_profile_qualifications_length
        CHECK (char_length(qualifications) <= 500),
    CONSTRAINT reviewer_profile_bio_length CHECK (char_length(bio_markdown) <= 10000)
);

CREATE TRIGGER reviewer_profile_append_only
    BEFORE UPDATE OR DELETE ON drugref.reviewer_profile
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'reviewer_profile_id', 'reviewer_uuid');

CREATE CONSTRAINT TRIGGER reviewer_profile_single_live
    AFTER INSERT OR UPDATE ON drugref.reviewer_profile
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'reviewer_uuid');

CREATE INDEX reviewer_profile_live_key
    ON drugref.reviewer_profile (reviewer_uuid)
    WHERE superseded_by IS NULL;

CREATE INDEX reviewer_profile_live_administrator
    ON drugref.reviewer_profile (role, active)
    WHERE superseded_by IS NULL;

-- Rotation uses the same append-then-supersede protocol as a profile correction.
-- The restrictive prefix check is not a password validator; it prevents a writer
-- from storing plaintext or a hash from a weaker/different algorithm by mistake.
CREATE TABLE drugref.reviewer_password_credential (
    credential_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reviewer_uuid uuid NOT NULL REFERENCES drugref.reviewer_account(reviewer_uuid),
    password_hash text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    recorded_by uuid NOT NULL REFERENCES drugref.reviewer_account(reviewer_uuid),
    superseded_by bigint
        REFERENCES drugref.reviewer_password_credential(credential_id),
    CONSTRAINT reviewer_password_credential_argon2id
        CHECK (password_hash LIKE '$argon2id$%')
);

CREATE TRIGGER reviewer_password_credential_append_only
    BEFORE UPDATE OR DELETE ON drugref.reviewer_password_credential
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'credential_id', 'reviewer_uuid');

CREATE CONSTRAINT TRIGGER reviewer_password_credential_single_live
    AFTER INSERT OR UPDATE ON drugref.reviewer_password_credential
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'reviewer_uuid');

CREATE INDEX reviewer_password_credential_live_key
    ON drugref.reviewer_password_credential (reviewer_uuid)
    WHERE superseded_by IS NULL;

-- A reviewer may enrol several device keys. The FK deliberately names the existing
-- signing registry instead of copying public-key bytes into the account model.
CREATE TABLE drugref.reviewer_key_enrolment (
    reviewer_key_enrolment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reviewer_uuid uuid NOT NULL REFERENCES drugref.reviewer_account(reviewer_uuid),
    signing_key_id bigint NOT NULL REFERENCES drugref.signing_key(signing_key_id),
    enrolled boolean NOT NULL,
    enrolled_at timestamptz NOT NULL DEFAULT now(),
    enrolled_by uuid NOT NULL REFERENCES drugref.reviewer_account(reviewer_uuid),
    superseded_by bigint
        REFERENCES drugref.reviewer_key_enrolment(reviewer_key_enrolment_id)
);

CREATE TRIGGER reviewer_key_enrolment_append_only
    BEFORE UPDATE OR DELETE ON drugref.reviewer_key_enrolment
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'reviewer_key_enrolment_id', 'signing_key_id');

CREATE CONSTRAINT TRIGGER reviewer_key_enrolment_single_live
    AFTER INSERT OR UPDATE ON drugref.reviewer_key_enrolment
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'signing_key_id');

CREATE INDEX reviewer_key_enrolment_live_key
    ON drugref.reviewer_key_enrolment (signing_key_id)
    WHERE superseded_by IS NULL;

CREATE INDEX reviewer_key_enrolment_by_reviewer
    ON drugref.reviewer_key_enrolment (reviewer_uuid)
    WHERE superseded_by IS NULL AND enrolled;

-- The bearer secret returned to the desktop client never enters PostgreSQL. The
-- service hashes its 32 random bytes and stores this fixed-size digest only.
CREATE TABLE drugref.auth_session (
    session_uuid uuid PRIMARY KEY,
    reviewer_uuid uuid NOT NULL REFERENCES drugref.reviewer_account(reviewer_uuid),
    token_digest bytea NOT NULL UNIQUE,
    issued_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CONSTRAINT auth_session_token_digest_length
        CHECK (octet_length(token_digest) = 32),
    CONSTRAINT auth_session_expiry_order CHECK (expires_at > issued_at)
);

CREATE INDEX auth_session_by_reviewer
    ON drugref.auth_session (reviewer_uuid, expires_at);

CREATE TRIGGER auth_session_insert_only
    BEFORE UPDATE OR DELETE ON drugref.auth_session
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

-- Revocation is an insert-only fact rather than a mutable nullable column. It records
-- who invalidated a session and makes both logout and administrator revocation auditable.
CREATE TABLE drugref.auth_session_revocation (
    session_uuid uuid PRIMARY KEY REFERENCES drugref.auth_session(session_uuid),
    revoked_at timestamptz NOT NULL DEFAULT now(),
    revoked_by uuid REFERENCES drugref.reviewer_account(reviewer_uuid),
    reason text NOT NULL,
    CONSTRAINT auth_session_revocation_reason
        CHECK (reason IN ('logout', 'administrative', 'credential_rotation'))
);

CREATE TRIGGER auth_session_revocation_insert_only
    BEFORE UPDATE OR DELETE ON drugref.auth_session_revocation
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.reviewer_account IS
    'Stable reviewer identity. Username and UUID are immutable; mutable presentation, '
    'role and account status live in append-only reviewer_profile revisions.';
COMMENT ON TABLE drugref.reviewer_profile IS
    'Append-only reviewer profile and authorisation history. Exactly one live revision '
    'per reviewer; corrections insert a new row and supersede the predecessor.';
COMMENT ON TABLE drugref.reviewer_password_credential IS
    'Append-only Argon2id password history. Hashes are PHC strings; plaintext passwords '
    'must never enter this database.';
COMMENT ON TABLE drugref.auth_session IS
    'Insert-only authenticated sessions containing SHA-256 token digests only. The raw '
    'bearer token remains in the native desktop core and is never returned to the WebView.';
COMMENT ON TABLE drugref.auth_session_revocation IS
    'Insert-only session invalidation facts. A row means the referenced session is no '
    'longer authorised regardless of its expiry time.';

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
    payload_context text NOT NULL,
    -- THE SAME SHAPE ITS TWO SIBLINGS CARRY. `assertion_signature.payload_context` and
    -- `release_manifest_entry.payload_context` are both CHECKed against this pattern;
    -- this column held the same kind of value with no constraint at all, and it is the
    -- one an operator is MEANT to update (pointing a kind at a /v2 is exactly the
    -- migration the read-back machinery exists to support). A malformed value here
    -- reaches `signing.canonical_payload`'s own ValueError by way of every writer, so
    -- the table an operator edits by hand was the only one not telling them at once.
    CONSTRAINT signature_target_kind_context_shape
        CHECK (payload_context ~ '^[a-z_]+/v[0-9]+$')
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
    -- SAME SHAPE signing._CONTEXT validates in Python, given a SQL counterpart. This
    -- table is insert-only, so a malformed context -- unlike a malformed fingerprint,
    -- which this file already CHECKs twice over -- would otherwise be a permanently
    -- uncorrectable row.
    --
    -- DELIBERATELY NOT A FOREIGN KEY into signature_target_kind(target_kind,
    -- payload_context), tempting as that looks: that catalog holds the CURRENT
    -- context for a target kind, and a signature is a historical fact about the
    -- context it was actually signed under. A future `curated_interaction/v2` must
    -- not retroactively invalidate every `curated_interaction/v1` signature on file --
    -- which is exactly what an FK tracking the catalog's current value would do the
    -- day the catalog moves on.
    CONSTRAINT assertion_signature_context_shape
        CHECK (payload_context ~ '^[a-z_]+/v[0-9]+$'),
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

-- NO SEPARATE (target_kind, target_id) INDEX: assertion_signature_unique above is a
-- UNIQUE index on (target_kind, target_id, key_fingerprint, payload_digest), and its
-- leading two columns already serve the "what signed this row?" lookup every
-- verification does. A second index on the same leading prefix would be a permanent
-- duplicate write cost on the table that grows fastest, for a query the first index
-- already answers.
--
-- The lookup a key revocation does: "what did this key sign?" is the re-review queue a
-- compromise produces, and key_fingerprint is only THIRD in the unique index -- not a
-- usable prefix -- so this one earns its place. Read only by the planner, so a test
-- asserts it by name -- as with the live-key indexes.
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
    -- specific failure nameable. WRITER-ASSERTED, not schema-enforced: nothing here
    -- checks row_count against the entries actually inserted (that would need new
    -- PL/pgSQL, out of scope for a floor migration, and genuinely belongs to the
    -- writer). The release verifier (releases.py, task 7's `drugref verify --release`)
    -- is what actually checks it, by recomputing the count from
    -- release_manifest_entry and comparing.
    row_count         integer     NOT NULL,
    -- AN ARRAY, ENFORCED: jsonb's NOT NULL admits the JSON scalar `null` (a value
    -- present in the column, distinct from SQL NULL) -- so NOT NULL alone would let a
    -- manifest with no recorded provenance read identically to one that honestly
    -- recorded an empty list, this file's own is_active_component lesson turned on
    -- itself. jsonb_typeof rules out `null`, scalars and objects; only an array
    -- (possibly empty, `[]`) passes.
    upstream_releases jsonb       NOT NULL,
    published_by      text        NOT NULL,
    published_at      timestamptz NOT NULL,
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT release_manifest_row_count CHECK (row_count >= 0),
    CONSTRAINT release_manifest_digest_length
        CHECK (octet_length(manifest_digest) = 32),
    CONSTRAINT release_manifest_upstream_releases_array
        CHECK (jsonb_typeof(upstream_releases) = 'array')
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
    -- SAME SHAPE CHECK as assertion_signature.payload_context, and the same reason NOT
    -- to promote it to an FK into signature_target_kind: this table is insert-only, so
    -- a malformed context would be permanently uncorrectable, but the catalog holds
    -- the CURRENT context for a target kind while an entry is a historical fact about
    -- the context an ENTRY was actually built under -- a future `.../v2` must not
    -- retroactively break a manifest entry recorded under `.../v1`.
    payload_context text   NOT NULL,
    payload_digest  bytea  NOT NULL,
    PRIMARY KEY (manifest_id, target_kind, natural_key),
    CONSTRAINT release_manifest_entry_digest_length
        CHECK (octet_length(payload_digest) = 32),
    CONSTRAINT release_manifest_entry_context_shape
        CHECK (payload_context ~ '^[a-z_]+/v[0-9]+$')
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

-- ============================================================================
-- 7. Read path
-- ============================================================================
-- REGISTRY-LEVEL ONLY. Postgres cannot verify an Ed25519 signature, so this view
-- reports what SQL can know -- is a signature present, is its key registered, has that
-- key been revoked -- and NOT whether the mathematics checks out. `drugref verify` is
-- the only thing that does that.
--
-- NO VERIFICATION RESULT IS EVER CACHED IN A COLUMN. A stored "verified" flag is a
-- claim nothing re-checks, which is the exact failure mode this slice exists to remove.
--
-- `signed` MEANS "NOTHING IN THE REGISTRY OBJECTS", not that the mathematics was
-- checked: the live key naming a signature must be registered, its live status must
-- not carry `invalidates_all_signatures`, and -- when that status IS a revocation --
-- the signature must predate the boundary it draws. `is_revocation` is what makes
-- `status_from` an END boundary at all: an active key's `status_from` is its
-- REGISTRATION time, so without that guard every signature ever made would read as
-- expired, including one made a second after the key was registered.
--
-- A BLANKET REVOCATION IS PERMANENT, AND THAT IS WHY THE LIVE ROW IS NOT THE WHOLE
-- ANSWER. `keys.revoke` writes whatever status it is handed, so `drugref keys revoke
-- --status active` on a compromised key -- one supported command, no raw SQL and no
-- superuser -- moved the LIVE row back to `active` and returned every one of that
-- key's rows to `signed` here, including every signature the thief made with the
-- stolen private half. The `NOT EXISTS` below asks the question section 3's own
-- comment promised the status history would answer, and which nothing asked until
-- now: has this fingerprint EVER carried a blanket revocation? `rotated` and
-- `retired` stay reversible -- a mistaken revocation must be correctable on an
-- append-only floor -- because permanence keys off `invalidates_all_signatures`
-- alone, never off a status name spelled again here.
--
-- `keys.key_status` runs the SAME rule for `drugref verify`, and
-- tests/test_signature_read_path.py pins the two against each other case by case: a
-- consumer reading this column and an operator running the verifier must never be
-- told different things about one key, or the cheaper answer is the believed one.
CREATE OR REPLACE VIEW drugref.curated_signature_status AS
WITH per_target AS (
    SELECT s.target_kind,
           s.target_id,
           count(*) AS signature_count,
           -- UNOBJECTED, not "valid": Postgres has not checked a single signature's
           -- mathematics here, only whether the registry has anything to say against
           -- it. A key the registry has never heard of (k.key_fingerprint IS NULL,
           -- because assertion_signature carries no FK into signing_key -- section 5's
           -- UNKNOWN_KEY case) counts as OBJECTED, on the same reasoning as
           -- signing.verdict's own precedence: without the public key there is nothing
           -- to vouch for the signature, so silence from the registry is not evidence
           -- of anything.
           count(*) FILTER (
               WHERE k.key_fingerprint IS NOT NULL
                 AND NOT t.invalidates_all_signatures
                 AND NOT (t.is_revocation AND s.signed_at >= k.status_from)
                 -- ...AND THE KEY HAS NEVER BEEN COMPROMISED IN ITS WHOLE HISTORY, not
                 -- merely in its live row. See this section's header: without this a
                 -- single `keys revoke --status active` undid a blanket revocation.
                 -- Written as NOT EXISTS over every row for the fingerprint rather
                 -- than as a join, so a key with several superseded statuses still
                 -- contributes at most one objection and the FILTER's count stays a
                 -- count of SIGNATURES.
                 AND NOT EXISTS (
                     SELECT 1
                     FROM   drugref.signing_key bk
                     JOIN   drugref.signing_key_status_kind bt ON bt.status = bk.status
                     WHERE  bk.key_fingerprint = s.key_fingerprint
                       AND  bt.invalidates_all_signatures
                 )
           ) AS unobjected_count
    FROM   drugref.assertion_signature s
           -- LEFT, twice over: an unregistered key must still produce a per_target row
           -- (OBJECTED, via the FILTER above) rather than vanish from the aggregate,
           -- and a key whose signing_key_status_kind lookup somehow failed must not
           -- silently drop the row either. superseded_by IS NULL picks out the key's
           -- LIVE status -- the only one that can answer "does the registry object
           -- TODAY".
    LEFT   JOIN drugref.signing_key k
           ON  k.key_fingerprint = s.key_fingerprint
           AND k.superseded_by IS NULL
    LEFT   JOIN drugref.signing_key_status_kind t ON t.status = k.status
    GROUP  BY s.target_kind, s.target_id
)
SELECT target_kind,
       target_id,
       signature_count,
       unobjected_count,
       -- ONLY TWO LABELS, deliberately coarser than signing.verdict's six: SQL cannot
       -- tell BAD_SIGNATURE from VALID (no cryptography here), so both collapse into
       -- whatever the registry says about the key that made them. One unobjected
       -- signature is enough -- see the file-level comment on why "signed" does not
       -- mean "every signature is unobjected".
       -- NOTE WHAT THE `ELSE` LABEL ACTUALLY COVERS: not only a revoked key, but a
       -- key the registry has NEVER HEARD OF (the `k.key_fingerprint IS NULL` case
       -- above, correctly counted as objected). `signed_by_revoked_key` therefore
       -- reads "the registry objects", NOT "the key was revoked" -- and an unknown
       -- key is the MORE suspicious of the two, not the less: `signing.verdict`
       -- ranks UNKNOWN_KEY above KEY_REVOKED_COMPROMISED. A consumer needing them
       -- apart runs `drugref verify`. A third value here is additive later (this is
       -- CREATE OR REPLACE) but changes a PUBLISHED vocabulary, so it is issue 86
       -- rather than a quiet widening.
       CASE WHEN unobjected_count > 0 THEN 'signed'
            ELSE 'signed_by_revoked_key' END AS signature_status
FROM   per_target;

COMMENT ON VIEW drugref.curated_signature_status IS
    'REGISTRY-LEVEL SIGNATURE STATUS -- NOT CRYPTOGRAPHIC VERIFICATION. Postgres cannot '
    'check an Ed25519 signature; this reports only whether a signature exists, whether '
    'its key is registered, and whether that key has been revoked. `signed` means '
    'NOTHING IN THE REGISTRY OBJECTS, not that the mathematics was checked -- run '
    '`drugref verify` for that. A target with no row here is UNSIGNED, which is an '
    'ordinary state: signing is optional per row. `signed_by_revoked_key` means THE '
    'REGISTRY OBJECTS -- the key was revoked, OR it was never registered at all; '
    'run `drugref verify` to tell those apart.';

-- A row whose signature claims a date long before this database learned of it. An
-- OPERATOR SIGNAL, deliberately not a gap kind -- a curator with an air-gapped signing
-- flow legitimately submits late -- on curated_target_unresolved's precedent. One day
-- is the threshold because `drugref sign` writes within seconds of signing.
CREATE OR REPLACE VIEW drugref.signature_backdated AS
SELECT signature_id, target_kind, target_id, key_fingerprint,
       signed_at, recorded_at, recorded_at - signed_at AS lag
FROM   drugref.assertion_signature
WHERE  signed_at < recorded_at - interval '1 day';

COMMENT ON VIEW drugref.signature_backdated IS
    'Signatures claiming a signed_at more than a day before this database recorded '
    'them. signed_at is INSIDE the signed payload and so cannot be forged by an '
    'attacker without the key -- but a compromised key CAN backdate, which is one '
    'reason a compromise is blanket rather than time-scoped. An operator signal, not a '
    'gap kind: a legitimate air-gapped flow also lands here.';

-- ---- re-issue db/029's two read views: APPEND signature_status, nothing else moves --
--
-- `CREATE OR REPLACE VIEW` can only ADD a trailing column; it cannot reorder or rename
-- an existing one, so both views below repeat db/029's SELECT list VERBATIM and add
-- exactly one column at the end. tests/test_signature_read_path.py pins the full
-- column list, in order, for both views -- the property a row-count comparison cannot
-- see.
--
-- THE JOIN IS LEFT, AND IT MUST STAY LEFT. db/029 section 3 made curated_ddi_pair and
-- curated_condition_ruling INNER joins against their curated tables for exactly the
-- opposite reason this join is LEFT: there, an ungraded candidate reaching the view
-- with a NULL severity would read as "reviewed and harmless". HERE, a signature is
-- OPTIONAL per row and the overlay ships empty of them -- an INNER join against
-- curated_signature_status would drop every unsigned curated row from the read path,
-- which is nearly all of them today, and -- far more seriously -- would let a single
-- key revocation silently withdraw a live contraindication ruling from every
-- downstream consumer the moment its only signature stopped being unobjected. FEWER
-- ROWS IS THE HARM DIRECTION FOR A CONTRAINDICATION (Plan B's central finding), and a
-- key-management event must never be able to trigger it. `COALESCE(..., 'unsigned')`
-- is what an ordinary, never-signed row reads as through the LEFT join.
CREATE OR REPLACE VIEW drugref.curated_ddi_pair AS
SELECT p.subject_moiety,
       p.partner_moiety,
       p.relationship,
       p.via_class,
       p.member_class,
       p.is_direct,
       c.severity,
       c.mechanism,
       c.management,
       c.evidence_grade,
       c.question_uuid,
       c.source           AS curated_source,
       c.reviewed_by,
       c.reviewed_against,
       c.reviewed_at,
       p.upstream_release,          -- which release raised the candidate
       p.source           AS candidate_source,
       COALESCE(ss.signature_status, 'unsigned') AS signature_status
FROM   drugref.ddi_candidate_pair p
       -- INNER: an ungraded rule reaches this view NEVER, not with NULL columns.
JOIN   drugref.curated_interaction c
       ON  c.subject_moiety_uuid = p.subject_moiety
       AND c.object_class_uuid   = p.via_class
       AND c.relationship        = p.relationship
       -- LEFT: see the block comment above this view.
LEFT   JOIN drugref.curated_signature_status ss
       ON  ss.target_kind = 'curated_interaction'
       AND ss.target_id   = c.curated_interaction_id
WHERE  c.superseded_by IS NULL
AND    c.applies;

COMMENT ON VIEW drugref.curated_ddi_pair IS
    'Drug pairs carrying a live drugref grade, expanded from the class-level rule the '
    'grade was written against -- so ONE curated row reaches every pair its rule '
    'expands to. INNER JOIN by design: an ungraded candidate does not appear here at '
    'all, because a NULL severity beside a real pair reads as "reviewed and harmless". '
    'ddi_candidate_pair remains the place to ask what the release said. '
    'signature_status is REGISTRY-LEVEL ONLY (see curated_signature_status) and its '
    'join is LEFT: an unsigned row still appears, labelled ''unsigned'', and a key '
    'revocation relabels a row rather than removing it -- fewer rows is the harm '
    'direction for a contraindication.';

CREATE OR REPLACE VIEW drugref.curated_condition_ruling AS
SELECT c.subject_moiety_uuid  AS subject_moiety,
       c.object_condition_uuid AS object_condition,
       c.ruling,
       c.severity,
       c.mechanism,
       c.management,
       c.evidence_grade,
       c.question_uuid,
       c.source               AS curated_source,
       c.reviewed_by,
       c.reviewed_against,
       c.reviewed_at,
       cand.candidate_kind,
       cand.relationship,
       cand.source            AS candidate_source,
       COALESCE(ss.signature_status, 'unsigned') AS signature_status
FROM   drugref.curated_condition c
       -- ONE ROW PER (ruling, candidate assertion), NOT one per ruling. The
       -- beta-blocker case returns two rows carrying the same `context_dependent`
       -- ruling, one naming may_treat and one naming CI_with -- which is exactly what
       -- a consumer needs in order to render "both, in different states". Aggregating
       -- the candidates into an array would hide which relationships the ruling
       -- reconciles, and #41's finding was that folding a key component under an
       -- aggregate breaks a view's grain.
JOIN   (SELECT subject_moiety_uuid, object_condition_uuid, relationship, source,
               'contraindication'::text AS candidate_kind
          FROM drugref.moiety_condition_contraindication
        UNION ALL
        SELECT subject_moiety_uuid, object_condition_uuid, relationship, source,
               'indication'
          FROM drugref.moiety_condition_indication) cand
       ON  cand.subject_moiety_uuid   = c.subject_moiety_uuid
       AND cand.object_condition_uuid = c.object_condition_uuid
       -- LEFT: the same refusal as curated_ddi_pair's, over curated_condition_id
       -- instead. Both rows a single ruling produces share ONE curated_condition_id,
       -- so both carry the SAME signature_status -- a ruling is signed once, not once
       -- per candidate it reconciles.
LEFT   JOIN drugref.curated_signature_status ss
       ON  ss.target_kind = 'curated_condition'
       AND ss.target_id   = c.curated_condition_id
WHERE  c.superseded_by IS NULL
       -- `spurious` is live and binds nothing: it records a disagreement without
       -- acting on it. Nothing renders it as advice.
AND    c.ruling <> 'spurious';

COMMENT ON VIEW drugref.curated_condition_ruling IS
    'Live drugref rulings on (drug, condition) pairs, joined to the upstream '
    'assertions they rule on -- ONE ROW PER CANDIDATE, so a `context_dependent` ruling '
    'over a pair asserted as both may_treat and CI_with returns both, and a consumer '
    'can see exactly which claims the ruling reconciles. A `spurious` ruling appears '
    'here never; the candidate it disagrees with stays in its projection. '
    'signature_status is REGISTRY-LEVEL ONLY and LEFT-joined -- see curated_ddi_pair''s '
    'comment, which states the refusal this view shares verbatim.';

-- db/048_unknown_signature_status.sql -- distinguish unknown from revoked keys.
--
-- PostgreSQL still reports registry policy only: it cannot verify Ed25519. This
-- additive published-vocabulary widening makes the registry fact precise without
-- removing clinical rows or changing `drugref verify`'s six cryptographic verdicts.

CREATE OR REPLACE VIEW drugref.curated_signature_status AS
WITH per_target AS (
    SELECT s.target_kind,
           s.target_id,
           count(*) AS signature_count,
           count(*) FILTER (
               WHERE k.key_fingerprint IS NULL
           ) AS unknown_key_count,
           count(*) FILTER (
               WHERE k.key_fingerprint IS NOT NULL
                 AND NOT t.invalidates_all_signatures
                 AND NOT (t.is_revocation AND s.signed_at >= k.status_from)
                 AND NOT EXISTS (
                     SELECT 1
                     FROM   drugref.signing_key blanket_key
                     JOIN   drugref.signing_key_status_kind blanket_status
                       ON   blanket_status.status = blanket_key.status
                     WHERE  blanket_key.key_fingerprint = s.key_fingerprint
                       AND  blanket_status.invalidates_all_signatures
                 )
           ) AS unobjected_count
    FROM   drugref.assertion_signature s
    LEFT   JOIN drugref.signing_key k
      ON   k.key_fingerprint = s.key_fingerprint
     AND   k.superseded_by IS NULL
    LEFT   JOIN drugref.signing_key_status_kind t ON t.status = k.status
    GROUP  BY s.target_kind, s.target_id
)
SELECT target_kind,
       target_id,
       signature_count,
       unobjected_count,
       CASE
           -- One independently unobjected signature is sufficient for registry policy.
           WHEN unobjected_count > 0 THEN 'signed'
           -- Among fully objected sets, an unregistered key is the stronger warning.
           WHEN unknown_key_count > 0 THEN 'signed_by_unknown_key'
           ELSE 'signed_by_revoked_key'
       END AS signature_status
FROM   per_target;

COMMENT ON VIEW drugref.curated_signature_status IS
    'REGISTRY-LEVEL SIGNATURE STATUS -- NOT CRYPTOGRAPHIC VERIFICATION. Postgres cannot '
    'check an Ed25519 signature; run `drugref verify` for its six mathematical and '
    'registry verdicts. A target with no row is `unsigned`. `signed` means at least '
    'one signature has a registered key to which the registry does not object. When '
    'every signature is objected, `signed_by_unknown_key` means at least one fingerprint '
    'was never registered and takes precedence over `signed_by_revoked_key`. Clinical '
    'rows remain served in every state.';

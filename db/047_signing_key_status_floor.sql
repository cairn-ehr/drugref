-- db/047_signing_key_status_floor.sql -- protect the key-revocation rule as data.
--
-- `signing_key_status_kind` determines whether a revocation is time-scoped or
-- blanket. One UPDATE could otherwise make every historical compromise appear safe.
-- INSERT remains available for a future fifth status. `signature_target_kind` is
-- deliberately untouched because its current payload context is designed to advance.

DROP TRIGGER IF EXISTS signing_key_status_kind_insert_only
    ON drugref.signing_key_status_kind;
CREATE TRIGGER signing_key_status_kind_insert_only
    BEFORE UPDATE OR DELETE ON drugref.signing_key_status_kind
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.signing_key_status_kind IS
    'INSERT-ONLY signing-key revocation rules. is_revocation says whether status_from '
    'is an END boundary; invalidates_all_signatures says whether the revocation '
    'destroys evidence retrospectively. Neither has a default, and existing rows may '
    'not be updated or deleted. A future status remains an explicit INSERT.';

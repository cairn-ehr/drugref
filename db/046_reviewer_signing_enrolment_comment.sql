-- db/046_reviewer_signing_enrolment_comment.sql -- document the new key trust root.
--
-- NO TABLE SHAPE CHANGES. db/030's catalog comment correctly stated that no key
-- enrolment protocol existed when the signing floor shipped. The authenticated
-- reviewer application now supplies that protocol, so leaving the old sentence in
-- pg_description would give operators a false answer through \d+.

COMMENT ON TABLE drugref.signing_key IS
    'CURATED, APPEND-ONLY: the public keys Drugref trusts, and their status history. '
    'The private half NEVER enters this database or Drugref infrastructure. Initial '
    'active registration may now come through the reviewer service: an authenticated, '
    'active reviewer enrols a device-generated public key to their stable account, so '
    'account-session possession and local-private-key possession remain separate '
    'requirements. The service derives and verifies the SHA-256 fingerprint; it never '
    'accepts or stores private bytes. A later status change is still a correction '
    '(insert, then supersede), never an UPDATE, preserving the history that dates '
    'rotation or revocation. Key-status administration and approval policy are '
    'separate from initial device enrolment.';

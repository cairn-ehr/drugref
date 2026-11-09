-- db/069_allergy_check_comment.sql
-- Wording fix to db/068's function comment (final review of the allergy-groups round).
-- A NEW file rather than an edit of 068: the ledger refuses a changed checksum on an
-- applied migration, and the measurement database already holds 068.
COMMENT ON FUNCTION drugref.allergy_check(uuid, uuid) IS
    'Allergy check for cairn: prescribed moiety vs recorded allergen (a moiety or '
    'an allergy group class_uuid). outcome is match | no-match | unplaced, never '
    'two: unplaced (with a reason: unknown-prescribed, unknown-allergen, '
    'allergen-not-a-group, allergen-in-no-group, prescribed-not-in-mesh) means '
    '"not checked" and must never be shown as safe. match rows (one per path) give '
    'match_kind same-drug | same-group, the group (class_uuid, name, MeSH root UI) '
    'and its review_status, and the '
    'fold components that matched on each side. Both sides are folded through '
    'moiety_allergy_fold (every composition component). KNOWN GAP: no '
    'penicillin-cephalosporin cross-allergy rule yet, so cephalexin vs penicillins '
    'answers no-match; that is not a statement that cephalosporins, or any other '
    'beta-lactam (e.g. carbapenems), are safe.';

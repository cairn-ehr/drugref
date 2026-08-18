/** Behavioural and validation constants shared by reviewer GUI components. */

/** The first valid page in the one-based review queue API. */
export const FIRST_QUEUE_PAGE = 1;

/** Number of review records requested for each GUI page. */
export const REVIEW_QUEUE_PAGE_SIZE = 25;

/** Delay after typing before the GUI sends a queue search request. */
export const SEARCH_DEBOUNCE_MILLISECONDS = 350;

/** Relative page movement requested by the Previous control. */
export const PREVIOUS_PAGE_DELTA = -1;

/** Relative page movement requested by the Next control. */
export const NEXT_PAGE_DELTA = 1;

/** Minimum username length shared with the reviewer domain validation. */
export const USERNAME_MIN_LENGTH = 3;

/** Maximum username length shared with the reviewer domain validation. */
export const USERNAME_MAX_LENGTH = 64;

/** Browser validation pattern shared with the reviewer domain validation. */
export const USERNAME_PATTERN = "[a-z][a-z0-9._-]+";

/** Maximum reviewer full-name length shared with the database constraint. */
export const FULL_NAME_MAX_LENGTH = 200;

/** Maximum qualifications length shared with the database constraint. */
export const QUALIFICATIONS_MAX_LENGTH = 500;

/** Maximum biography length shared with the database constraint. */
export const BIOGRAPHY_MAX_LENGTH = 10_000;

/** Maximum working-note length shared with db/045 and the reviewer domain. */
export const ANNOTATION_MAX_LENGTH = 20_000;

/** Maximum citation identifier or URL length shared with db/045. */
export const EVIDENCE_REFERENCE_MAX_LENGTH = 2_000;

/** Maximum citation context length shared with db/045. */
export const EVIDENCE_NOTE_MAX_LENGTH = 10_000;

/** Maximum characters accepted for mechanism or management prose. */
export const CLINICAL_PROSE_MAX_LENGTH = 20_000;

/** Minimum password length enforced before Argon2id hashing. */
export const PASSWORD_MIN_LENGTH = 12;

/** Maximum password length accepted before Argon2id hashing. */
export const PASSWORD_MAX_LENGTH = 256;

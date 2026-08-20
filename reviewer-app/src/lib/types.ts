/** Typed WebView representation of the reviewer service's queue API contract. */

/** Stable kinds of unresolved clinical records exposed by the review service. */
export type ReviewKind = "interaction_rule" | "condition_contradiction";

/** Identifier schemes admitted for citation-only working references. */
export type EvidenceReferenceScheme = "DOI" | "PMID" | "PMCID" | "NCT" | "SPL" | "URL";

/** Clinical decisions admitted across interaction and condition targets. */
export type ReviewDecision =
  | "applies"
  | "does_not_apply"
  | "contraindicated"
  | "indicated"
  | "context_dependent"
  | "spurious";

/** Ordered clinical severities accepted by the curated overlay. */
export type Severity = "contraindicated" | "major" | "moderate" | "minor";

/** Evidence-attestation grades accepted by the curated overlay. */
export type EvidenceGrade = "established" | "probable" | "suspected" | "theoretical";

/** Registry-level detached signature status published for a curated revision. */
export type SignatureStatus =
  | "unsigned"
  | "signed"
  | "signed_by_revoked_key"
  | "signed_by_unknown_key";

/** Optional paging and filter parameters accepted by the review queue endpoint. */
export interface ReviewQueueQuery {
  /** One-based page number. */
  page?: number;
  /** Maximum number of records returned on one page. */
  pageSize?: number;
  /** Exact review-kind filter. */
  kind?: ReviewKind;
  /** Exact source filter. */
  source?: string;
  /** Exact relationship filter. */
  relationship?: string;
  /** Literal case-insensitive subject, object, or relationship search. */
  search?: string;
}

/** Counts that describe the complete current review queue snapshot. */
export interface ReviewQueueSummary {
  /** Number of uncurated interaction rules. */
  interactionRules: number;
  /** Number of drug-condition pairs with contradictory projections. */
  conditionContradictions: number;
  /** Number of concrete DDI pairs expanded from curated rules. */
  reviewedPairs: number;
}

/** Filter values derived from the complete current queue. */
export interface ReviewQueueFilters {
  /** Review kinds currently present. */
  kinds: ReviewKind[];
  /** Candidate sources currently present. */
  sources: string[];
  /** Clinical relationships currently present. */
  relationships: string[];
}

/** One stable, unresolved clinical target displayed in the reviewer queue. */
export interface ReviewQueueItem {
  /** UI identity derived from the target's stable natural key. */
  id: string;
  /** Source-neutral natural key used by target-scoped review writes. */
  targetKey: string;
  /** Kind of clinical question represented by this target. */
  kind: ReviewKind;
  /** Stable Drugref UUID for the subject moiety. */
  subjectUuid: string;
  /** Stable Drugref UUID for the object class or condition. */
  objectUuid: string;
  /** Human-readable subject name. */
  subjectName: string;
  /** Human-readable class or condition name. */
  objectName: string;
  /** Source relationships that produced the target. */
  relationships: string[];
  /** Sources asserting the candidate target. */
  candidateSources: string[];
  /** Upstream releases supporting the candidate target. */
  upstreamReleases: string[];
  /** Number of concrete pairs affected by this target. */
  impactCount: number;
  /** Human-readable clinical question for the reviewer. */
  question: string;
  /** Explanation of why the target entered the queue. */
  provenance: string;
}

/** Stable selector used to load immutable working history for one target. */
export interface ReviewRecordQuery {
  /** Kind of clinical question named by the target key. */
  kind: ReviewKind;
  /** Frozen canonical open-question gap key. */
  targetKey: string;
}

/** Request to append one Markdown working note. */
export interface CreateAnnotationInput extends ReviewRecordQuery {
  /** Immutable Markdown working note. */
  annotationMarkdown: string;
}

/** Request to append one citation-only working reference. */
export interface CreateEvidenceReferenceInput extends ReviewRecordQuery {
  /** Structured identifier scheme for the cited source. */
  referenceScheme: EvidenceReferenceScheme;
  /** Identifier or URL in the selected scheme. */
  referenceValue: string;
  /** Optional Markdown context for the reference. */
  noteMarkdown: string;
}

/** Immutable reviewer-authored working note. */
export interface ReviewAnnotation {
  /** Stable annotation ledger identifier. */
  annotationId: number;
  /** Stable reviewer identity that authored the note. */
  reviewerUuid: string;
  /** Current compact reviewer username. */
  username: string;
  /** Current reviewer display name. */
  reviewerName: string;
  /** Immutable Markdown source. */
  annotationMarkdown: string;
  /** RFC 3339 time at which the note was recorded. */
  recordedAt: string;
}

/** Immutable citation-only working reference. */
export interface EvidenceReference {
  /** Stable evidence-reference ledger identifier. */
  evidenceReferenceId: number;
  /** Stable reviewer identity that attached the reference. */
  reviewerUuid: string;
  /** Current compact reviewer username. */
  username: string;
  /** Current reviewer display name. */
  reviewerName: string;
  /** Structured identifier scheme for the cited source. */
  referenceScheme: EvidenceReferenceScheme;
  /** Identifier or URL in the selected scheme. */
  referenceValue: string;
  /** Optional Markdown context supplied with the reference. */
  noteMarkdown: string;
  /** RFC 3339 time at which the reference was recorded. */
  recordedAt: string;
}

/** Complete immutable working history attached to one target. */
export interface ReviewRecord {
  /** Frozen canonical open-question gap key. */
  targetKey: string;
  /** Reviewer notes in insertion order. */
  annotations: ReviewAnnotation[];
  /** Citation-only references in insertion order. */
  evidenceReferences: EvidenceReference[];
}

/** Request to create one immutable clinical decision revision. */
export interface CreateReviewDecisionInput extends ReviewRecordQuery {
  /** Target-specific clinical decision. */
  decision: ReviewDecision;
  /** Required severity for asserting decisions. */
  severity?: Severity;
  /** Optional clinical mechanism. */
  mechanism?: string;
  /** Optional practical management guidance. */
  management?: string;
  /** Required evidence grade for asserting decisions. */
  evidenceGrade?: EvidenceGrade;
  /** Live revision observed when the form was loaded. */
  expectedRevisionId?: number | null;
}

/** One immutable curated interaction or condition revision. */
export interface ReviewDecisionRevision {
  /** Stable curated row identifier within this target kind. */
  revisionId: number;
  /** Target-specific clinical decision. */
  decision: ReviewDecision;
  /** Stored severity, absent for retiring decisions. */
  severity: Severity | null;
  /** Optional clinical mechanism. */
  mechanism: string | null;
  /** Optional practical management guidance. */
  management: string | null;
  /** Stored evidence grade, absent for retiring decisions. */
  evidenceGrade: EvidenceGrade | null;
  /** Immortal question UUID, absent on legacy CLI rows. */
  questionUuid: string | null;
  /** Authenticated reviewer-name snapshot. */
  reviewedBy: string;
  /** Candidate releases against which the decision was formed. */
  reviewedAgainst: string;
  /** RFC 3339 recording time. */
  reviewedAt: string;
  /** Later immutable revision, absent while this row is live. */
  supersededBy: number | null;
  /** Database-derived registry-level detached signature status. */
  signatureStatus: SignatureStatus;
}

/** Complete append-only decision history for one canonical target. */
export interface ReviewDecisionRecord {
  /** Frozen canonical open-question gap key. */
  targetKey: string;
  /** Current live revision identifier, absent before the first decision. */
  currentRevisionId: number | null;
  /** Immutable revisions in insertion order. */
  history: ReviewDecisionRevision[];
}

/** One current public signing key enrolled to the authenticated reviewer. */
export interface SigningKeySummary {
  /** SHA-256 fingerprint of the raw Ed25519 public key. */
  keyFingerprint: string;
  /** Registry algorithm name. */
  algorithm: string;
  /** Human-readable registry holder. */
  holder: string;
  /** Current database-owned key status. */
  status: string;
  /** RFC 3339 enrolment timestamp. */
  enrolledAt: string;
  /** Detached signatures already recorded with this fingerprint. */
  signatureCount: number;
}

/** Result of retiring a public enrolment and replacing its local private key. */
export interface SigningKeyReplacement {
  /** Fingerprint withdrawn from this reviewer and device. */
  keyFingerprint: string;
  /** Existing signatures preserved by time-scoped rotation. */
  preservedSignatureCount: number;
  /** Current public registry status retained after local cleanup. */
  registryStatus: string;
}

/** Native device-vault state merged with service enrolments. */
export interface DeviceSigningStatus {
  /** Whether this reviewer has an encrypted local vault on this device. */
  localVaultExists: boolean;
  /** Public fingerprint recorded beside the vault after local generation. */
  localKeyFingerprint: string | null;
  /** Current service enrolments. */
  keys: SigningKeySummary[];
}

/** Administrator-selectable append-only signing-key trust action. */
export type AdministrativeSigningKeyStatus = "retired" | "compromised";

/** One current public registry key with reviewer ownership and review impact. */
export interface SigningKeyTrustSummary {
  /** SHA-256 fingerprint of the raw Ed25519 public key. */
  keyFingerprint: string;
  /** Registry algorithm name. */
  algorithm: string;
  /** Human-readable holder recorded with the key. */
  holder: string;
  /** Current database-owned status. */
  status: string;
  /** RFC 3339 instant at which the current status began. */
  statusFrom: string;
  /** RFC 3339 registry timestamp for the current correction. */
  registeredAt: string;
  /** Stable reviewer identity owning the enrolment, when one exists. */
  reviewerUuid: string | null;
  /** Stable reviewer username, when one exists. */
  username: string | null;
  /** Current reviewer display name, when one exists. */
  reviewerFullName: string | null;
  /** Whether the current reviewer enrolment still permits this key. */
  enrolled: boolean;
  /** Every detached signature recorded with this fingerprint. */
  signatureCount: number;
  /** Current curated revisions carrying a signature from this key. */
  currentRevisionCount: number;
  /** Current revisions with no registry-unobjected signature remaining. */
  affectedCurrentRevisionCount: number;
}

/** Complete current public registry projection for administrators. */
export interface SigningKeyTrustStatus {
  /** Current keys ordered by holder and fingerprint. */
  keys: SigningKeyTrustSummary[];
}

/** Result of one append-only administrative key status correction. */
export interface SigningKeyAdministrationResult {
  /** Fresh database projection after the correction. */
  key: SigningKeyTrustSummary;
  /** Whether a live reviewer enrolment was withdrawn. */
  withdrawnEnrolment: boolean;
  /** Current revisions now awaiting an unobjected counter-signature. */
  revisionsAwaitingCounterSignature: number;
}

/** One current curated revision awaiting an unobjected detached signature. */
export interface PendingReviewSignature extends ReviewRecordQuery {
  /** Current immutable curated row identifier. */
  revisionId: number;
  /** Human-readable subject name. */
  subjectName: string;
  /** Human-readable class or condition name. */
  objectName: string;
  /** Stored target-specific clinical decision. */
  decision: string;
  /** Authenticated reviewer-name snapshot stored on the curated row. */
  reviewedBy: string;
  /** RFC 3339 recording timestamp. */
  reviewedAt: string;
  /** Whether this is first sign-off or counter-signing after registry objection. */
  pendingReason: "unsigned" | "needs_counter_signature";
  /** Existing signature rows currently objected to by the registry. */
  objectedSignatureCount: number;
}

/** Selector for one current curated row and one enrolled signing key. */
export interface ReviewSignatureQuery extends ReviewRecordQuery {
  /** Current immutable curated revision identifier. */
  revisionId: number;
  /** Enrolled key fingerprint bound into the payload. */
  keyFingerprint: string;
}

/** One named, canonically rendered value covered by a detached signature. */
export interface CanonicalField {
  /** Frozen field name and ordering identity. */
  name: string;
  /** Exact rendered value, or null when the database value is SQL NULL. */
  value: string | null;
}

/** Exact payload metadata shown before native signing can proceed. */
export interface ReviewSignaturePreview {
  /** Current curated revision that will be signed. */
  revisionId: number;
  /** Domain-separated canonical payload context. */
  payloadContext: string;
  /** SHA-256 digest of the exact canonical payload. */
  payloadDigest: string;
  /** Enrolled key fingerprint bound into the payload. */
  keyFingerprint: string;
  /** Server-issued signing instant bound into the payload. */
  signedAt: string;
  /** Number of frozen fields covered by the signature. */
  fieldCount: number;
  /** Every exact field in the canonical order covered by the digest. */
  fields: CanonicalField[];
}

/** Page metadata returned with every queue snapshot. */
export interface Pagination {
  /** One-based current page number. */
  page: number;
  /** Maximum records requested for the page. */
  pageSize: number;
  /** Number of records matching the current filters. */
  totalItems: number;
  /** Number of pages matching the current filters. */
  totalPages: number;
}

/** Complete review queue response for one filtered page. */
export interface ReviewQueuePage {
  /** RFC 3339 timestamp identifying the database snapshot time. */
  generatedAt: string;
  /** Counts for the unfiltered queue. */
  summary: ReviewQueueSummary;
  /** Available filters derived from the unfiltered queue. */
  filters: ReviewQueueFilters;
  /** Paging metadata for the filtered result. */
  pagination: Pagination;
  /** Stable review targets on the requested page. */
  items: ReviewQueueItem[];
}

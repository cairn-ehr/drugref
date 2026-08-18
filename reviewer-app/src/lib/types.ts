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
  /** Database-derived detached signature verdict. */
  signatureStatus: string;
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

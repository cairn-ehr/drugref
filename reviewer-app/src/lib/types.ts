/** Typed WebView representation of the reviewer service's queue API contract. */

/** Stable kinds of unresolved clinical records exposed by the review service. */
export type ReviewKind = "interaction_rule" | "condition_contradiction";

/** Identifier schemes admitted for citation-only working references. */
export type EvidenceReferenceScheme = "DOI" | "PMID" | "PMCID" | "NCT" | "SPL" | "URL";

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
  /** Source-neutral natural key used by the future write path. */
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

export type ReviewKind = "interaction_rule" | "condition_contradiction";

export interface ReviewQueueQuery {
  page?: number;
  pageSize?: number;
  kind?: ReviewKind;
  source?: string;
  relationship?: string;
  search?: string;
}

export interface ReviewQueueSummary {
  interactionRules: number;
  conditionContradictions: number;
  reviewedPairs: number;
}

export interface ReviewQueueFilters {
  kinds: ReviewKind[];
  sources: string[];
  relationships: string[];
}

export interface ReviewQueueItem {
  id: string;
  targetKey: string;
  kind: ReviewKind;
  subjectUuid: string;
  objectUuid: string;
  subjectName: string;
  objectName: string;
  relationships: string[];
  candidateSources: string[];
  upstreamReleases: string[];
  impactCount: number;
  question: string;
  provenance: string;
}

export interface Pagination {
  page: number;
  pageSize: number;
  totalItems: number;
  totalPages: number;
}

export interface ReviewQueuePage {
  generatedAt: string;
  summary: ReviewQueueSummary;
  filters: ReviewQueueFilters;
  pagination: Pagination;
  items: ReviewQueueItem[];
}

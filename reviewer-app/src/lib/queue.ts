/** Pure construction of typed review queue requests from component filter state. */

import {
  FIRST_QUEUE_PAGE,
  REVIEW_QUEUE_PAGE_SIZE,
} from "./constants";
import type { ReviewKind, ReviewQueueQuery } from "./types";

/** Sentinel used by filter controls to mean that no filter is applied. */
export const ALL_FILTERS = "all" as const;

/** Values selected by the review queue's search and filter controls. */
export interface QueueFilterState {
  /** Optional free-text search entered by the reviewer. */
  search: string;
  /** Selected review kind, or the all-kinds sentinel. */
  kind: ReviewKind | typeof ALL_FILTERS;
  /** Selected source, or the all-sources sentinel. */
  source: string;
  /** Selected relationship, or the all-relationships sentinel. */
  relationship: string;
}

/** Build a service query without sending empty or all-filter sentinel values. */
export function buildReviewQueueQuery(
  page: number = FIRST_QUEUE_PAGE,
  filters: QueueFilterState,
): ReviewQueueQuery {
  return {
    page,
    pageSize: REVIEW_QUEUE_PAGE_SIZE,
    kind: filters.kind === ALL_FILTERS ? undefined : filters.kind,
    source: filters.source === ALL_FILTERS ? undefined : filters.source,
    relationship: filters.relationship === ALL_FILTERS ? undefined : filters.relationship,
    search: filters.search.trim() || undefined,
  };
}

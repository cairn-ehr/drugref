/** Live native queue adapter plus explicitly isolated browser-preview projection. */

import { invoke } from "@tauri-apps/api/core";
import demoWorkspace from "./demo-workspace.json";
import { FIRST_QUEUE_PAGE, REVIEW_QUEUE_PAGE_SIZE } from "./constants";
import type {
  ReviewKind,
  ReviewQueueFilters,
  ReviewQueueItem,
  ReviewQueuePage,
  ReviewQueueQuery,
} from "./types";

/** Number of records represented by an empty filtered result. */
const EMPTY_RESULT_COUNT = 0;

/** Whether queue calls should cross the protected native trust boundary. */
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/**
 * The installed app always asks the authenticated Rust core for live data. The
 * browser-only development surface keeps a small, explicitly preview-only data set
 * so responsive layout can be inspected without exposing the service to a WebView.
 */
export async function loadReviewQueue(query: ReviewQueueQuery): Promise<ReviewQueuePage> {
  if (isTauri) return invoke<ReviewQueuePage>("load_review_queue", { query });

  const items = previewItems();
  const filtered = items.filter((item: ReviewQueueItem): boolean => matchesQuery(item, query));
  const page = query.page ?? FIRST_QUEUE_PAGE;
  const pageSize = query.pageSize ?? REVIEW_QUEUE_PAGE_SIZE;
  const start = (page - FIRST_QUEUE_PAGE) * pageSize;

  return {
    generatedAt: demoWorkspace.generatedAt,
    summary: demoWorkspace.summary,
    filters: previewFilters(items),
    pagination: {
      page,
      pageSize,
      totalItems: filtered.length,
      totalPages:
        filtered.length === EMPTY_RESULT_COUNT
          ? EMPTY_RESULT_COUNT
          : Math.ceil(filtered.length / pageSize),
    },
    items: filtered.slice(start, start + pageSize),
  };
}

/** Return whether a browser-preview item satisfies every active queue filter. */
function matchesQuery(item: ReviewQueueItem, query: ReviewQueueQuery): boolean {
  const needle = query.search?.trim().toLocaleLowerCase();
  return (
    (!query.kind || item.kind === query.kind) &&
    (!query.source || item.candidateSources.includes(query.source)) &&
    (!query.relationship || item.relationships.includes(query.relationship)) &&
    (!needle ||
      `${item.subjectName} ${item.objectName} ${item.relationships.join(" ")}`
        .toLocaleLowerCase()
        .includes(needle))
  );
}

/** Derive sorted browser-preview filter choices from the complete preview queue. */
function previewFilters(items: ReviewQueueItem[]): ReviewQueueFilters {
  return {
    kinds: unique(items.map((item: ReviewQueueItem): ReviewKind => item.kind)),
    sources: unique(items.flatMap((item: ReviewQueueItem): string[] => item.candidateSources)),
    relationships: unique(items.flatMap((item: ReviewQueueItem): string[] => item.relationships)),
  };
}

/** Convert the compact JSON fixture into the same contract returned by the service. */
function previewItems(): ReviewQueueItem[] {
  return demoWorkspace.items.map((item) => ({
    id: item.id,
    targetKey: item.targetKey,
    kind: item.kind as ReviewKind,
    subjectUuid: item.subjectUuid,
    objectUuid: item.objectUuid,
    subjectName: item.subjectName,
    objectName: item.objectName,
    relationships:
      item.kind === "condition_contradiction" ? ["CI_with", "may_treat"] : [item.relationship],
    candidateSources: ["MED-RT"],
    upstreamReleases: ["2026.07.06"],
    impactCount: item.impactCount,
    question: item.question,
    provenance: item.provenance,
  }));
}

/** Return sorted unique values without mutating the caller's array. */
function unique<T extends string>(values: T[]): T[] {
  return [...new Set(values)].sort();
}

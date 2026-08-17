import { invoke } from "@tauri-apps/api/core";
import demoWorkspace from "./demo-workspace.json";
import type {
  ReviewKind,
  ReviewQueueItem,
  ReviewQueuePage,
  ReviewQueueQuery,
} from "./types";

const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/**
 * The installed app always asks the authenticated Rust core for live data. The
 * browser-only development surface keeps a small, explicitly preview-only data set
 * so responsive layout can be inspected without exposing the service to a WebView.
 */
export async function loadReviewQueue(query: ReviewQueueQuery): Promise<ReviewQueuePage> {
  if (isTauri) return invoke<ReviewQueuePage>("load_review_queue", { query });

  const items = previewItems();
  const filtered = items.filter((item) => {
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
  });
  const page = query.page ?? 1;
  const pageSize = query.pageSize ?? 25;
  const start = (page - 1) * pageSize;

  return {
    generatedAt: demoWorkspace.generatedAt,
    summary: demoWorkspace.summary,
    filters: {
      kinds: unique(items.map((item) => item.kind)),
      sources: unique(items.flatMap((item) => item.candidateSources)),
      relationships: unique(items.flatMap((item) => item.relationships)),
    },
    pagination: {
      page,
      pageSize,
      totalItems: filtered.length,
      totalPages: filtered.length === 0 ? 0 : Math.ceil(filtered.length / pageSize),
    },
    items: filtered.slice(start, start + pageSize),
  };
}

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

function unique<T extends string>(values: T[]): T[] {
  return [...new Set(values)].sort();
}

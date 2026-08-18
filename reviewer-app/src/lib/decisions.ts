/** Native clinical-decision adapter plus isolated browser-preview history. */

import { invoke } from "@tauri-apps/api/core";
import type {
  CreateReviewDecisionInput,
  ReviewDecisionRecord,
  ReviewDecisionRevision,
  ReviewRecordQuery,
} from "./types";

/** Whether clinical decision calls should cross the protected native boundary. */
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Browser-only decision histories used for interaction and layout preview. */
const previewDecisions = new Map<string, ReviewDecisionRecord>();

/** Next browser-preview curated revision identifier. */
let nextPreviewRevisionId = 1;

/** Load immutable decision history without exposing the bearer token to the WebView. */
export async function loadReviewDecision(
  query: ReviewRecordQuery,
): Promise<ReviewDecisionRecord> {
  if (isTauri) return invoke<ReviewDecisionRecord>("load_review_decision", { query });
  return cloneRecord(previewRecord(query.targetKey));
}

/** Record one optimistic clinical revision through native IPC or preview memory. */
export async function createReviewDecision(
  input: CreateReviewDecisionInput,
): Promise<ReviewDecisionRecord> {
  if (isTauri) return invoke<ReviewDecisionRecord>("create_review_decision", { input });
  const record = previewRecord(input.targetKey);
  if (record.currentRevisionId !== (input.expectedRevisionId ?? null)) {
    throw new Error("decision changed since this form was loaded; reload its history");
  }
  const revisionId = nextPreviewRevisionId++;
  const revisedHistory = record.history.map(
    (revision: ReviewDecisionRevision): ReviewDecisionRevision =>
      revision.revisionId === record.currentRevisionId
        ? { ...revision, supersededBy: revisionId }
        : { ...revision },
  );
  revisedHistory.push({
    revisionId,
    decision: input.decision,
    severity: input.severity ?? null,
    mechanism: input.mechanism?.trim() || null,
    management: input.management?.trim() || null,
    evidenceGrade: input.evidenceGrade ?? null,
    questionUuid: "00000000-0000-0000-0000-000000000099",
    reviewedBy: "Maya Chen",
    reviewedAgainst: "browser preview",
    reviewedAt: new Date().toISOString(),
    supersededBy: null,
    signatureStatus: "unsigned",
  });
  const updated: ReviewDecisionRecord = {
    targetKey: input.targetKey,
    currentRevisionId: revisionId,
    history: revisedHistory,
  };
  previewDecisions.set(input.targetKey, updated);
  return cloneRecord(updated);
}

/** Return or initialise one browser-preview target decision history. */
function previewRecord(targetKey: string): ReviewDecisionRecord {
  const existing = previewDecisions.get(targetKey);
  if (existing) return existing;
  const created: ReviewDecisionRecord = { targetKey, currentRevisionId: null, history: [] };
  previewDecisions.set(targetKey, created);
  return created;
}

/** Copy nested preview revisions so callers cannot mutate the in-memory authority. */
function cloneRecord(record: ReviewDecisionRecord): ReviewDecisionRecord {
  return {
    targetKey: record.targetKey,
    currentRevisionId: record.currentRevisionId,
    history: record.history.map((revision: ReviewDecisionRevision) => ({ ...revision })),
  };
}

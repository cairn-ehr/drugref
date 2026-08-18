/** Native clinical-decision adapter plus isolated browser-preview history. */

import { invoke } from "@tauri-apps/api/core";
import type {
  CreateReviewDecisionInput,
  PendingReviewSignature,
  ReviewDecisionRecord,
  ReviewDecisionRevision,
  ReviewQueueItem,
  ReviewRecordQuery,
} from "./types";

/** Whether clinical decision calls should cross the protected native boundary. */
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Browser-only decision histories used for interaction and layout preview. */
const previewDecisions = new Map<string, ReviewDecisionRecord>();

/** Browser-only target kinds retained so recorded rows can reappear in resume view. */
const previewKinds = new Map<string, ReviewRecordQuery["kind"]>();

/** Browser-only human labels retained for the resumable signing list. */
const previewLabels = new Map<
  string,
  Pick<ReviewQueueItem, "subjectName" | "objectName">
>();

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
  labels?: Pick<ReviewQueueItem, "subjectName" | "objectName">,
): Promise<ReviewDecisionRecord> {
  if (isTauri) return invoke<ReviewDecisionRecord>("create_review_decision", { input });
  const record = previewRecord(input.targetKey);
  previewKinds.set(input.targetKey, input.kind);
  if (labels) previewLabels.set(input.targetKey, labels);
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

/** Mark the current browser-preview revision signed after simulated verification. */
export function markPreviewDecisionSigned(query: ReviewRecordQuery): ReviewDecisionRecord {
  const record = previewRecord(query.targetKey);
  const updated: ReviewDecisionRecord = {
    targetKey: record.targetKey,
    currentRevisionId: record.currentRevisionId,
    history: record.history.map(
      (revision: ReviewDecisionRevision): ReviewDecisionRevision =>
        revision.revisionId === record.currentRevisionId
          ? { ...revision, signatureStatus: "signed" }
          : { ...revision },
    ),
  };
  previewDecisions.set(query.targetKey, updated);
  return cloneRecord(updated);
}

/** Project unsigned browser-preview records into the same resume contract as the service. */
export function previewPendingSignatures(): PendingReviewSignature[] {
  return [...previewDecisions.values()].flatMap(
    (record: ReviewDecisionRecord): PendingReviewSignature[] => {
      const current = record.history.find(
        (revision: ReviewDecisionRevision): boolean =>
          revision.revisionId === record.currentRevisionId && revision.signatureStatus === "unsigned",
      );
      const kind = previewKinds.get(record.targetKey);
      const labels = previewLabels.get(record.targetKey);
      if (!current || !kind) return [];
      return [
        {
          kind,
          targetKey: record.targetKey,
          revisionId: current.revisionId,
          subjectName: labels?.subjectName ?? "Recently recorded review",
          objectName: labels?.objectName ?? record.targetKey,
          decision: current.decision,
          reviewedBy: current.reviewedBy,
          reviewedAt: current.reviewedAt,
        },
      ];
    },
  );
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

/** Native working-record adapter plus isolated browser-preview history. */

import { invoke } from "@tauri-apps/api/core";
import type {
  CreateAnnotationInput,
  CreateEvidenceReferenceInput,
  EvidenceReference,
  ReviewAnnotation,
  ReviewRecord,
  ReviewRecordQuery,
} from "./types";

/** Whether working-record calls should cross the protected native boundary. */
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Browser-only in-memory history used for layout and interaction preview. */
const previewRecords = new Map<string, ReviewRecord>();

/** Next browser-preview annotation ledger identifier. */
let nextPreviewAnnotationId = 1;

/** Next browser-preview evidence-reference ledger identifier. */
let nextPreviewEvidenceId = 1;

/** Load immutable working history without exposing the bearer token to the WebView. */
export async function loadReviewRecord(query: ReviewRecordQuery): Promise<ReviewRecord> {
  if (isTauri) return invoke<ReviewRecord>("load_review_record", { query });
  return cloneRecord(previewRecord(query.targetKey));
}

/** Append one working note through native IPC or browser-preview memory. */
export async function createReviewAnnotation(
  input: CreateAnnotationInput,
): Promise<ReviewAnnotation> {
  if (isTauri) return invoke<ReviewAnnotation>("create_review_annotation", { input });
  const annotation: ReviewAnnotation = {
    annotationId: nextPreviewAnnotationId++,
    reviewerUuid: "00000000-0000-0000-0000-000000000001",
    username: "maya.chen",
    reviewerName: "Maya Chen",
    annotationMarkdown: input.annotationMarkdown.trim(),
    recordedAt: new Date().toISOString(),
  };
  previewRecord(input.targetKey).annotations.push(annotation);
  return { ...annotation };
}

/** Append one citation-only reference through native IPC or browser-preview memory. */
export async function createEvidenceReference(
  input: CreateEvidenceReferenceInput,
): Promise<EvidenceReference> {
  if (isTauri) return invoke<EvidenceReference>("create_evidence_reference", { input });
  const reference: EvidenceReference = {
    evidenceReferenceId: nextPreviewEvidenceId++,
    reviewerUuid: "00000000-0000-0000-0000-000000000001",
    username: "maya.chen",
    reviewerName: "Maya Chen",
    referenceScheme: input.referenceScheme,
    referenceValue: input.referenceValue.trim(),
    noteMarkdown: input.noteMarkdown.trim(),
    recordedAt: new Date().toISOString(),
  };
  previewRecord(input.targetKey).evidenceReferences.push(reference);
  return { ...reference };
}

/** Return or initialise one browser-preview target history. */
function previewRecord(targetKey: string): ReviewRecord {
  const existing = previewRecords.get(targetKey);
  if (existing) return existing;
  const created: ReviewRecord = { targetKey, annotations: [], evidenceReferences: [] };
  previewRecords.set(targetKey, created);
  return created;
}

/** Copy nested preview arrays so callers cannot mutate the in-memory authority. */
function cloneRecord(record: ReviewRecord): ReviewRecord {
  return {
    targetKey: record.targetKey,
    annotations: record.annotations.map((annotation: ReviewAnnotation) => ({ ...annotation })),
    evidenceReferences: record.evidenceReferences.map((reference: EvidenceReference) => ({
      ...reference,
    })),
  };
}

/** Narrow native signing adapter plus explicitly simulated browser-preview state. */

import { invoke } from "@tauri-apps/api/core";
import { markPreviewDecisionSigned, previewPendingSignatures } from "./decisions";
import type {
  CanonicalField,
  DeviceSigningStatus,
  PendingReviewSignature,
  ReviewDecisionRecord,
  ReviewSignaturePreview,
  ReviewSignatureQuery,
  SigningKeyReplacement,
  SigningKeySummary,
} from "./types";

/** Whether signing calls may cross the protected native trust boundary. */
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Fixed browser-preview fingerprint; no private key exists in browser preview. */
const PREVIEW_FINGERPRINT = "a".repeat(64);

/** Mutable browser-only enrolment state used for responsive workflow inspection. */
let previewEnrolled = true;

/** Pending browser-only preview retained across the explicit confirmation step. */
let previewPending: ReviewSignaturePreview | null = null;

/** Load current enrolments and whether an encrypted vault exists on this device. */
export async function loadSigningStatus(): Promise<DeviceSigningStatus> {
  if (isTauri) return invoke<DeviceSigningStatus>("signing_status");
  return {
    localVaultExists: previewEnrolled,
    localKeyFingerprint: previewEnrolled ? PREVIEW_FINGERPRINT : null,
    keys: previewEnrolled ? [previewKey()] : [],
  };
}

/** Load current unsigned revisions that remain available after queue refresh or restart. */
export async function loadPendingReviewSignatures(): Promise<PendingReviewSignature[]> {
  if (isTauri) return invoke<PendingReviewSignature[]>("load_pending_signatures");
  return previewPendingSignatures();
}

/** Generate or reopen the native vault and enrol only its public key. */
export async function enrolLocalSigningKey(passphrase: string): Promise<SigningKeySummary> {
  if (isTauri) return invoke<SigningKeySummary>("enrol_local_signing_key", { passphrase });
  previewEnrolled = true;
  return previewKey();
}

/** Retire the current enrolment before deleting its fixed native vault files. */
export async function replaceLocalSigningKey(): Promise<SigningKeyReplacement> {
  if (isTauri) return invoke<SigningKeyReplacement>("replace_local_signing_key");
  const replacement: SigningKeyReplacement = {
    keyFingerprint: PREVIEW_FINGERPRINT,
    preservedSignatureCount: 0,
  };
  previewEnrolled = false;
  previewPending = null;
  return replacement;
}

/** Prepare and retain exact canonical metadata before asking for a passphrase. */
export async function prepareReviewSignature(
  query: ReviewSignatureQuery,
): Promise<ReviewSignaturePreview> {
  if (isTauri) return invoke<ReviewSignaturePreview>("prepare_review_signature", { query });
  const signedAt = new Date().toISOString();
  const fields = previewSignatureFields(query, signedAt);
  const preview: ReviewSignaturePreview = {
    revisionId: query.revisionId,
    payloadContext:
      query.kind === "interaction_rule" ? "curated_interaction/v1" : "curated_condition/v1",
    payloadDigest: "b".repeat(64),
    keyFingerprint: query.keyFingerprint,
    signedAt,
    fieldCount: fields.length,
    fields,
  };
  previewPending = preview;
  return preview;
}

/** Representative complete canonical fields used only for visual workflow testing. */
function previewSignatureFields(
  query: ReviewSignatureQuery,
  signedAt: string,
): CanonicalField[] {
  const sharedTail: CanonicalField[] = [
    { name: "severity", value: "major" },
    {
      name: "mechanism",
      value: "Additive pharmacodynamic effects may increase clinically significant toxicity.",
    },
    {
      name: "management",
      value: "Review the indication and patient-specific risk. Monitor closely and use a safer alternative when the combined risk outweighs benefit.",
    },
    { name: "evidence_grade", value: "established" },
    { name: "question_uuid", value: "33333333-3333-4333-8333-333333333333" },
    { name: "source", value: "MED-RT" },
    { name: "reviewed_by", value: "Dr Maya Chen" },
    { name: "reviewed_against", value: "MED-RT 2026-08-01; DrugCentral 2026-07-15" },
    { name: "reviewed_at", value: "2026-08-18T00:00:00.000000Z" },
    { name: "signer_key_fingerprint", value: query.keyFingerprint },
    { name: "signed_at", value: signedAt },
  ];
  if (query.kind === "interaction_rule") {
    return [
      { name: "subject_moiety_uuid", value: "11111111-1111-4111-8111-111111111111" },
      { name: "object_class_uuid", value: "22222222-2222-4222-8222-222222222222" },
      { name: "relationship", value: "CI_with" },
      { name: "applies", value: "true" },
      ...sharedTail,
    ];
  }
  return [
    { name: "subject_moiety_uuid", value: "11111111-1111-4111-8111-111111111111" },
    { name: "object_condition_uuid", value: "44444444-4444-4444-8444-444444444444" },
    { name: "ruling", value: "contraindicated" },
    ...sharedTail,
  ];
}

/** Sign the already-confirmed native payload and return refreshed database history. */
export async function completeReviewSignature(
  query: ReviewSignatureQuery,
  preview: ReviewSignaturePreview,
  passphrase: string,
): Promise<ReviewDecisionRecord> {
  if (isTauri) {
    return invoke<ReviewDecisionRecord>("complete_review_signature", {
      query,
      payloadDigest: preview.payloadDigest,
      passphrase,
    });
  }
  if (!previewPending || previewPending.payloadDigest !== preview.payloadDigest) {
    throw new Error("prepare this exact signature again before confirming it");
  }
  previewPending = null;
  return markPreviewDecisionSigned(query);
}

/** Representative public key used only by browser layout preview. */
function previewKey(): SigningKeySummary {
  return {
    keyFingerprint: PREVIEW_FINGERPRINT,
    algorithm: "Ed25519",
    holder: "Dr Maya Chen",
    status: "active",
    enrolledAt: "2026-08-18T00:00:00Z",
    signatureCount: 0,
  };
}

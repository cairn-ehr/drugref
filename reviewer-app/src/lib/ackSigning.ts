/** Signing departure acknowledgements (db/088, #284): native adapter plus an isolated
 * browser preview. The native core retains the exact batch at preview time and signs
 * only that batch; the WebView never handles key material. */

import { invoke } from "@tauri-apps/api/core";
import type {
  AckSignaturePreview,
  AckSignatureQuery,
  AckSignatureResult,
  CanonicalField,
  UnsignedDepartureAck,
} from "./types";

/** Whether calls may cross the protected native trust boundary. */
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Browser-only unsigned acknowledgements for layout inspection. */
let previewAcks: UnsignedDepartureAck[] = [41, 42].map((ackId, index) => ({
  ackId,
  ruleGrain: "class_rule",
  subjectName: "Monoamine Oxidase Inhibitors [MoA]",
  objectClassName: "Serotonin Reuptake Inhibitors [MoA]",
  relationship: "CI_MoA",
  subjectMoietyName: index === 0 ? "phenelzine" : "tranylcypromine",
  partnerMoietyName: "vortioxetine",
  rationale: "Reclassified upstream: vortioxetine is now a multimodal agent.",
  reviewedBy: "Dr Maya Chen",
  reviewedAgainst: "MED-RT 2026.07.06",
  acknowledgedAt: "2026-11-26T03:14:15Z",
  signatureStatus: "unsigned",
}));

/** The browser preview's retained batch, mirroring the native store. */
let previewPending: AckSignaturePreview | null = null;

/** Every acknowledgement still needing a trusted signature. */
export async function loadUnsignedDepartureAcks(): Promise<UnsignedDepartureAck[]> {
  if (isTauri) return invoke<UnsignedDepartureAck[]>("load_unsigned_departure_acks");
  return previewAcks.map((ack) => ({ ...ack }));
}

/** Prepare and retain one batch's exact payloads before asking for a passphrase. */
export async function prepareAckSignatures(query: AckSignatureQuery): Promise<AckSignaturePreview> {
  if (isTauri) return invoke<AckSignaturePreview>("prepare_ack_signatures", { query });
  const signedAt = new Date().toISOString();
  previewPending = {
    payloadContext: "graded_pair_departure_ack/v1",
    keyFingerprint: query.keyFingerprint,
    signedAt,
    items: query.ackIds.map((ackId) => ({
      ackId,
      payloadDigest: String(ackId % 10).repeat(64),
      fields: previewFields(ackId, query.keyFingerprint, signedAt),
    })),
  };
  return previewPending;
}

/** Sign the confirmed batch natively and record it; returns how many were written. */
export async function completeAckSignatures(
  query: AckSignatureQuery,
  preview: AckSignaturePreview,
  passphrase: string,
): Promise<AckSignatureResult> {
  const payloadDigests = preview.items.map((item) => item.payloadDigest);
  if (isTauri) {
    return invoke<AckSignatureResult>("complete_ack_signatures", { query, payloadDigests, passphrase });
  }
  // By digest, not identity: the component holds a Svelte state proxy of `preview`.
  const pendingDigests = previewPending?.items.map((item) => item.payloadDigest).join();
  if (pendingDigests !== payloadDigests.join()) {
    throw new Error("prepare these exact signatures again before confirming");
  }
  previewPending = null;
  previewAcks = previewAcks.filter((ack) => !query.ackIds.includes(ack.ackId));
  return { recorded: query.ackIds.length };
}

/** Representative payload fields, used only by the browser preview. */
function previewFields(ackId: number, keyFingerprint: string, signedAt: string): CanonicalField[] {
  const ack = previewAcks.find((item) => item.ackId === ackId);
  return [
    { name: "rule_grain", value: "class_rule" },
    { name: "subject_key", value: "00000000-0000-4000-8000-000000000041" },
    { name: "object_class_uuid", value: "00000000-0000-4000-8000-000000000042" },
    { name: "relationship", value: "CI_MoA" },
    { name: "subject_moiety", value: `00000000-0000-4000-8000-0000000000${ackId}` },
    { name: "partner_moiety", value: "00000000-0000-4000-8000-000000000043" },
    { name: "rationale", value: ack?.rationale ?? "" },
    { name: "reviewed_by", value: ack?.reviewedBy ?? "" },
    { name: "reviewed_against", value: ack?.reviewedAgainst ?? "" },
    { name: "acknowledged_at", value: "2026-11-26T03:14:15.000000Z" },
    { name: "signer_key_fingerprint", value: keyFingerprint },
    { name: "signed_at", value: signedAt },
  ];
}

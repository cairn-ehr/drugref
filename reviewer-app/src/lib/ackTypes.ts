/** Wire types of departure-acknowledgement signing (db/088, #284), mirroring
 * `reviewer-domain/src/ack_signing.rs` under camelCase. Kept apart from `types.ts`
 * to hold both under the ~500-line cap. */

import type { CanonicalField, RuleGrain, SignatureStatus } from "./types";

/** One acknowledgement with no signature the registry trusts (db/088, #284). */
export interface UnsignedDepartureAck {
  /** Acknowledgement id, the signature's target. */
  ackId: number;
  /** Grain of the ruling whose departure was acknowledged. */
  ruleGrain: RuleGrain;
  /** Ruling subject: a drug or a class. */
  subjectName: string;
  /** The ruling's object class. */
  objectClassName: string;
  /** The ruling's relationship (axis). */
  relationship: string;
  /** The pair's subject drug. */
  subjectMoietyName: string;
  /** The pair's partner drug. */
  partnerMoietyName: string;
  /** The curator's stated reason. */
  rationale: string;
  /** Reviewer-name snapshot recorded with the acknowledgement. */
  reviewedBy: string;
  /** Release the acknowledgement was reviewed against. */
  reviewedAgainst: string;
  /** RFC 3339 recording time. */
  acknowledgedAt: string;
  /** `unsigned`, or a registry status naming only objected signatures. */
  signatureStatus: SignatureStatus;
}

/** A batch of acknowledgements to sign with one enrolled key. */
export interface AckSignatureQuery {
  /** Distinct acknowledgement ids, in display order. */
  ackIds: number[];
  /** Enrolled device-key fingerprint. */
  keyFingerprint: string;
}

/** One acknowledgement's exact payload, shown before signing. */
export interface AckSignaturePreviewItem {
  /** Acknowledgement that will be signed. */
  ackId: number;
  /** SHA-256 digest of the exact canonical payload. */
  payloadDigest: string;
  /** Every rendered field, in the order covered by the digest. */
  fields: CanonicalField[];
}

/** Everything a batch will sign. */
export interface AckSignaturePreview {
  /** Payload context of every item. */
  payloadContext: string;
  /** Enrolled key fingerprint bound inside every payload. */
  keyFingerprint: string;
  /** Server-issued signing instant bound inside every payload. */
  signedAt: string;
  /** One entry per acknowledgement, in query order. */
  items: AckSignaturePreviewItem[];
}

/** How many signatures a batch recorded. */
export interface AckSignatureResult {
  /** Detached signatures written. */
  recorded: number;
}

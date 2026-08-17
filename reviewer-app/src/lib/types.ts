export type ReviewKind = "interaction_rule" | "condition_contradiction";
export type Priority = "high" | "routine";
export type ReviewState = "unreviewed" | "in_review" | "reviewed";
export type SignatureStatus = "unsigned" | "valid" | "invalid";

export interface ReviewerProfile {
  username: string;
  fullName: string;
  qualifications: string;
  bioMarkdown: string;
  keyFingerprint: string;
}

export interface QueueSummary {
  interactionRules: number;
  conditionContradictions: number;
  reviewedPairs: number;
}

export interface ReviewItem {
  id: string;
  targetKey: string;
  kind: ReviewKind;
  subjectUuid: string;
  objectUuid: string;
  subjectName: string;
  objectName: string;
  relationship: string;
  candidateSource: string;
  upstreamRelease: string;
  impactCount: number;
  priority: Priority;
  reviewState: ReviewState;
  signatureStatus: SignatureStatus;
  question: string;
  provenance: string;
}

export interface ReviewWorkspace {
  mode: "preview";
  generatedAt: string;
  reviewer: ReviewerProfile;
  summary: QueueSummary;
  items: ReviewItem[];
}

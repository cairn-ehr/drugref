/** Account-service boundary and isolated browser-preview account state. */
import { invoke } from "@tauri-apps/api/core";

/** Roles accepted by the reviewer account service. */
export type ReviewerRole = "reviewer" | "administrator";

/** Fields required to create an initial or subsequent reviewer account. */
export interface CreateAccountInput {
  /** Stable lowercase sign-in name. */
  username: string;
  /** Reviewer's human-readable name. */
  fullName: string;
  /** Professional qualifications displayed in the workspace. */
  qualifications: string;
  /** Markdown biography source stored in the append-only profile. */
  bioMarkdown: string;
  /** Access-control role assigned to the account. */
  role: ReviewerRole;
  /** Raw password sent only to the service for hashing. */
  password: string;
}

/** Complete append-only profile replacement submitted by an administrator. */
export interface UpdateReviewerProfileInput {
  /** Corrected human-readable name. */
  fullName: string;
  /** Corrected professional qualifications. */
  qualifications: string;
  /** Corrected Markdown biography source. */
  bioMarkdown: string;
  /** Corrected access-control role. */
  role: ReviewerRole;
  /** Whether the replacement profile permits authentication. */
  active: boolean;
  /** Profile revision observed when the form was opened. */
  expectedProfileRevisionId: number;
}

/** Current reviewer account projection returned by the service. */
export interface ReviewerAccount {
  /** Stable identity of the reviewer. */
  reviewerUuid: string;
  /** Stable lowercase sign-in name. */
  username: string;
  /** Current human-readable name. */
  fullName: string;
  /** Current professional qualifications. */
  qualifications: string;
  /** Current Markdown biography source. */
  bioMarkdown: string;
  /** Current access-control role. */
  role: ReviewerRole;
  /** Whether the current profile permits sign-in. */
  active: boolean;
  /** Current append-only profile revision identifier. */
  profileRevisionId: number;
  /** RFC 3339 account creation timestamp. */
  createdAt: string;
  /** Number of live signing-key enrolments. */
  keyCount: number;
  /** Number of unexpired, unrevoked sessions. */
  liveSessionCount: number;
}

/** Database-derived result returned by every account-administration mutation. */
export interface AccountAdministrationResult {
  /** Current reviewer projection after the mutation. */
  reviewer: ReviewerAccount;
  /** Number of session revocation facts appended by the mutation. */
  revokedSessionCount: number;
}

/** First-run state returned before authentication. */
export interface BootstrapStatus {
  /** Whether the database still needs its first administrator. */
  bootstrapRequired: boolean;
}

/** Number of enrolled keys shown on the browser preview administrator. */
const PREVIEW_ADMIN_KEY_COUNT = 1;

/** Number of enrolled keys assigned to a newly previewed account. */
const NEW_PREVIEW_ACCOUNT_KEY_COUNT = 0;

/** Whether this module is executing inside the protected Tauri WebView. */
const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Representative administrator used only by the browser layout preview. */
const previewAdmin: ReviewerAccount = {
  reviewerUuid: "c53e72a6-fda0-492a-a1c7-3b4bf309e1c4",
  username: "maya.chen",
  fullName: "Dr Maya Chen",
  qualifications: "MBBS, FRACP",
  bioMarkdown: "Clinical pharmacologist and Drugref reviewer.",
  role: "administrator",
  active: true,
  profileRevisionId: 1,
  createdAt: "2026-08-17T00:00:00Z",
  keyCount: PREVIEW_ADMIN_KEY_COUNT,
  liveSessionCount: 1,
};

/** Mutable browser-preview account list; native builds never read it. */
let previewUsers: ReviewerAccount[] = [previewAdmin];

/** Browser-preview credentials keyed by username; native builds never read them. */
let previewPasswords = new Map([[previewAdmin.username, "preview"]]);

/** Browser-preview authentication state; native sessions remain in Rust memory. */
let previewSignedIn = false;

/** Browser-preview identity corresponding to the simulated current session. */
let previewCurrentReviewerUuid = "";

/** Monotonic browser-preview profile revision identifier. */
let previewProfileRevision = previewAdmin.profileRevisionId;

/** Return whether the browser URL explicitly requests the first-run preview. */
function previewBootstrapRequested(): boolean {
  return typeof window !== "undefined" && new URLSearchParams(window.location.search).has("bootstrap");
}

/** Read first-run state from the native service or the explicit browser preview. */
export async function startupState(): Promise<BootstrapStatus> {
  if (isTauri) return invoke<BootstrapStatus>("startup_state");
  return { bootstrapRequired: previewBootstrapRequested() && !previewSignedIn };
}

/** Create the first administrator and establish its authenticated session. */
export async function bootstrapAdmin(input: CreateAccountInput): Promise<ReviewerAccount> {
  if (isTauri) return invoke<ReviewerAccount>("bootstrap_admin", { input });
  const reviewer = previewAccount(input, "administrator");
  previewUsers = [reviewer];
  previewPasswords = new Map([[reviewer.username, input.password]]);
  previewSignedIn = true;
  previewCurrentReviewerUuid = reviewer.reviewerUuid;
  return reviewer;
}

/** Authenticate a reviewer without exposing the native session token to the WebView. */
export async function login(username: string, password: string): Promise<ReviewerAccount> {
  if (isTauri) return invoke<ReviewerAccount>("login", { input: { username, password } });
  const reviewer = previewUsers.find((user) => user.username === username);
  if (!reviewer || !reviewer.active || previewPasswords.get(username) !== password) {
    throw new Error("invalid username or password");
  }
  previewSignedIn = true;
  previewCurrentReviewerUuid = reviewer.reviewerUuid;
  const signedIn = { ...reviewer, liveSessionCount: Math.max(1, reviewer.liveSessionCount) };
  replacePreviewUser(signedIn);
  return signedIn;
}

/** List current reviewer profiles for the administrator surface. */
export async function listUsers(): Promise<ReviewerAccount[]> {
  if (isTauri) return invoke<ReviewerAccount[]>("list_users");
  if (!previewSignedIn) throw new Error("sign in before using account administration");
  return [...previewUsers];
}

/** Create a reviewer account through the authenticated administrator surface. */
export async function createUser(input: CreateAccountInput): Promise<ReviewerAccount> {
  if (isTauri) return invoke<ReviewerAccount>("create_user", { input });
  if (previewUsers.some((user) => user.username === input.username)) {
    throw new Error("username already exists");
  }
  const reviewer = previewAccount(input, input.role);
  previewUsers = [...previewUsers, reviewer];
  previewPasswords.set(reviewer.username, input.password);
  return reviewer;
}

/** Append a profile correction through native IPC or isolated preview memory. */
export async function updateUserProfile(
  reviewerUuid: string,
  input: UpdateReviewerProfileInput,
): Promise<AccountAdministrationResult> {
  if (isTauri) {
    return invoke<AccountAdministrationResult>("update_user_profile", { reviewerUuid, input });
  }
  const current = previewUser(reviewerUuid);
  if (current.profileRevisionId !== input.expectedProfileRevisionId) {
    throw new Error("reviewer profile changed; reload before recording this correction");
  }
  if (
    current.role === "administrator" &&
    current.active &&
    (input.role !== "administrator" || !input.active) &&
    !previewUsers.some(
      (user) => user.reviewerUuid !== reviewerUuid && user.role === "administrator" && user.active,
    )
  ) {
    throw new Error("the last active administrator cannot be disabled or demoted");
  }
  if (
    current.fullName === input.fullName &&
    current.qualifications === input.qualifications &&
    current.bioMarkdown === input.bioMarkdown &&
    current.role === input.role &&
    current.active === input.active
  ) {
    throw new Error("reviewer profile has no changes");
  }
  previewProfileRevision += 1;
  const revokedSessionCount = current.active && !input.active ? current.liveSessionCount : 0;
  const reviewer: ReviewerAccount = {
    ...current,
    fullName: input.fullName,
    qualifications: input.qualifications,
    bioMarkdown: input.bioMarkdown,
    role: input.role,
    active: input.active,
    profileRevisionId: previewProfileRevision,
    liveSessionCount: input.active ? current.liveSessionCount : 0,
  };
  replacePreviewUser(reviewer);
  if (reviewerUuid === previewCurrentReviewerUuid && !reviewer.active) previewSignedIn = false;
  return { reviewer, revokedSessionCount };
}

/** Rotate one password and invalidate every existing session. */
export async function rotateUserPassword(
  reviewerUuid: string,
  password: string,
): Promise<AccountAdministrationResult> {
  if (isTauri) {
    return invoke<AccountAdministrationResult>("rotate_user_password", {
      reviewerUuid,
      input: { password },
    });
  }
  const current = previewUser(reviewerUuid);
  const revokedSessionCount = current.liveSessionCount;
  const reviewer = { ...current, liveSessionCount: 0 };
  previewPasswords.set(current.username, password);
  replacePreviewUser(reviewer);
  if (reviewerUuid === previewCurrentReviewerUuid) previewSignedIn = false;
  return { reviewer, revokedSessionCount };
}

/** Revoke every current session without changing profile or credentials. */
export async function revokeUserSessions(
  reviewerUuid: string,
): Promise<AccountAdministrationResult> {
  if (isTauri) {
    return invoke<AccountAdministrationResult>("revoke_user_sessions", { reviewerUuid });
  }
  const current = previewUser(reviewerUuid);
  const revokedSessionCount = current.liveSessionCount;
  const reviewer = { ...current, liveSessionCount: 0 };
  replacePreviewUser(reviewer);
  if (reviewerUuid === previewCurrentReviewerUuid) previewSignedIn = false;
  return { reviewer, revokedSessionCount };
}

/** Revoke the current native session and reset browser-preview authentication. */
export async function logout(): Promise<void> {
  if (isTauri) await invoke("logout");
  previewSignedIn = false;
  previewCurrentReviewerUuid = "";
}

/** Identify whether account calls use the native service or browser-only preview. */
export function accountMode(): "service" | "browser-preview" {
  return isTauri ? "service" : "browser-preview";
}

/** Convert account input into a representative browser-preview account projection. */
function previewAccount(input: CreateAccountInput, role: ReviewerRole): ReviewerAccount {
  return {
    reviewerUuid: crypto.randomUUID(),
    username: input.username,
    fullName: input.fullName,
    qualifications: input.qualifications,
    bioMarkdown: input.bioMarkdown,
    role,
    active: true,
    profileRevisionId: ++previewProfileRevision,
    createdAt: new Date().toISOString(),
    keyCount: NEW_PREVIEW_ACCOUNT_KEY_COUNT,
    liveSessionCount: 0,
  };
}

/** Return one browser-preview account or fail with the service's public shape. */
function previewUser(reviewerUuid: string): ReviewerAccount {
  const reviewer = previewUsers.find((user) => user.reviewerUuid === reviewerUuid);
  if (!reviewer) throw new Error("reviewer account was not found");
  return reviewer;
}

/** Replace one preview projection without changing stable list order. */
function replacePreviewUser(reviewer: ReviewerAccount): void {
  previewUsers = previewUsers.map((user) =>
    user.reviewerUuid === reviewer.reviewerUuid ? reviewer : user,
  );
}

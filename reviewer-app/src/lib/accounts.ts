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
  /** RFC 3339 account creation timestamp. */
  createdAt: string;
  /** Number of live signing-key enrolments. */
  keyCount: number;
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
  createdAt: "2026-08-17T00:00:00Z",
  keyCount: PREVIEW_ADMIN_KEY_COUNT,
};

/** Mutable browser-preview account list; native builds never read it. */
let previewUsers: ReviewerAccount[] = [previewAdmin];

/** Browser-preview credentials keyed by username; native builds never read them. */
let previewPasswords = new Map([[previewAdmin.username, "preview"]]);

/** Browser-preview authentication state; native sessions remain in Rust memory. */
let previewSignedIn = false;

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
  return reviewer;
}

/** Authenticate a reviewer without exposing the native session token to the WebView. */
export async function login(username: string, password: string): Promise<ReviewerAccount> {
  if (isTauri) return invoke<ReviewerAccount>("login", { input: { username, password } });
  const reviewer = previewUsers.find((user) => user.username === username);
  if (!reviewer || previewPasswords.get(username) !== password) {
    throw new Error("invalid username or password");
  }
  previewSignedIn = true;
  return reviewer;
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

/** Revoke the current native session and reset browser-preview authentication. */
export async function logout(): Promise<void> {
  if (isTauri) await invoke("logout");
  previewSignedIn = false;
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
    createdAt: new Date().toISOString(),
    keyCount: NEW_PREVIEW_ACCOUNT_KEY_COUNT,
  };
}

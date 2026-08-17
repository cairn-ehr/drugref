import { invoke } from "@tauri-apps/api/core";

export type ReviewerRole = "reviewer" | "administrator";

export interface CreateAccountInput {
  username: string;
  fullName: string;
  qualifications: string;
  bioMarkdown: string;
  role: ReviewerRole;
  password: string;
}

export interface ReviewerAccount {
  reviewerUuid: string;
  username: string;
  fullName: string;
  qualifications: string;
  bioMarkdown: string;
  role: ReviewerRole;
  active: boolean;
  createdAt: string;
  keyCount: number;
}

export interface BootstrapStatus {
  bootstrapRequired: boolean;
}

const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
const previewAdmin: ReviewerAccount = {
  reviewerUuid: "c53e72a6-fda0-492a-a1c7-3b4bf309e1c4",
  username: "maya.chen",
  fullName: "Dr Maya Chen",
  qualifications: "MBBS, FRACP",
  bioMarkdown: "Clinical pharmacologist and Drugref reviewer.",
  role: "administrator",
  active: true,
  createdAt: "2026-08-17T00:00:00Z",
  keyCount: 1,
};
let previewUsers: ReviewerAccount[] = [previewAdmin];
let previewPasswords = new Map([[previewAdmin.username, "preview"]]);
let previewSignedIn = false;

function previewBootstrapRequested() {
  return typeof window !== "undefined" && new URLSearchParams(window.location.search).has("bootstrap");
}

export async function startupState(): Promise<BootstrapStatus> {
  if (isTauri) return invoke<BootstrapStatus>("startup_state");
  return { bootstrapRequired: previewBootstrapRequested() && !previewSignedIn };
}

export async function bootstrapAdmin(input: CreateAccountInput): Promise<ReviewerAccount> {
  if (isTauri) return invoke<ReviewerAccount>("bootstrap_admin", { input });
  const reviewer = previewAccount(input, "administrator");
  previewUsers = [reviewer];
  previewPasswords = new Map([[reviewer.username, input.password]]);
  previewSignedIn = true;
  return reviewer;
}

export async function login(username: string, password: string): Promise<ReviewerAccount> {
  if (isTauri) return invoke<ReviewerAccount>("login", { input: { username, password } });
  const reviewer = previewUsers.find((user) => user.username === username);
  if (!reviewer || previewPasswords.get(username) !== password) {
    throw new Error("invalid username or password");
  }
  previewSignedIn = true;
  return reviewer;
}

export async function listUsers(): Promise<ReviewerAccount[]> {
  if (isTauri) return invoke<ReviewerAccount[]>("list_users");
  if (!previewSignedIn) throw new Error("sign in before using account administration");
  return [...previewUsers];
}

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

export async function logout(): Promise<void> {
  if (isTauri) await invoke("logout");
  previewSignedIn = false;
}

export function accountMode(): "service" | "browser-preview" {
  return isTauri ? "service" : "browser-preview";
}

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
    keyCount: 0,
  };
}

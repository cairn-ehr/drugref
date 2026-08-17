import { invoke } from "@tauri-apps/api/core";
import demoWorkspace from "./demo-workspace.json";
import type { ReviewWorkspace } from "./types";

/**
 * Use Tauri IPC in the installed application and the same bundled fixture when Vite
 * renders in a browser. The fallback is preview-only: it authenticates nobody and
 * exposes no clinical write path.
 */
export async function loadReviewWorkspace(): Promise<ReviewWorkspace> {
  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
    return invoke<ReviewWorkspace>("load_review_workspace");
  }

  return demoWorkspace as ReviewWorkspace;
}

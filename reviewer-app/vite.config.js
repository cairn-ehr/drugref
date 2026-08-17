import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

/** Fixed Vite port expected by the Tauri development shell. */
const TAURI_DEV_SERVER_PORT = 1420;

/** Fixed hot-module-reload port expected by remote Tauri development clients. */
const TAURI_HMR_PORT = 1421;

/**
 * Optional host supplied by Tauri when development is served to another device.
 * @type {string | undefined}
 */
// @ts-expect-error process is a Node.js global unavailable to the browser TS library.
const host = process.env.TAURI_DEV_HOST;

/** Vite configuration shared by browser previews and the Tauri build pipeline. */
const config = defineConfig({
  plugins: [svelte()],

  // Preserve Rust diagnostics printed alongside the Vite development server.
  clearScreen: false,
  // Tauri requires fixed development ports and should fail on accidental conflicts.
  server: {
    port: TAURI_DEV_SERVER_PORT,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: TAURI_HMR_PORT,
        }
      : undefined,
    watch: {
      // Rust's own build loop owns changes beneath the native source directory.
      ignored: ["**/src-tauri/**"],
    },
  },
});

export default config;

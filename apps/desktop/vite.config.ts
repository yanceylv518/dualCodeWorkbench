import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// Node types are intentionally not part of the browser application type surface.
// @ts-expect-error Vite executes this configuration in Node.
import { readFileSync } from "node:fs";
// @ts-expect-error Vite executes this configuration in Node.
import { homedir } from "node:os";
// @ts-expect-error Vite executes this configuration in Node.
import { join } from "node:path";

function developmentToken(): string {
  try {
    return readFileSync(
      join(homedir(), ".dualcode-workbench", "sidecar.token"),
      "utf8",
    ).trim();
  } catch {
    return "";
  }
}

export default defineConfig({
  plugins: [react()],
  define: { __DUALCODE_DEV_TOKEN__: JSON.stringify(developmentToken()) },
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/target/**"] },
  },
  clearScreen: false,
});

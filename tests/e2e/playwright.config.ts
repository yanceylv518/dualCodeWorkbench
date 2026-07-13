import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  webServer: {
    command: "corepack pnpm --dir ../../apps/desktop exec vite --host 127.0.0.1 --port 1421",
    url: "http://127.0.0.1:1421",
    reuseExistingServer: true,
  },
  use: {
    baseURL: "http://127.0.0.1:1421",
    channel: "chrome",
    headless: true,
  },
  reporter: "list",
});

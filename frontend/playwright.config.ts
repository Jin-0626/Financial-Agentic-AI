import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  use: {
    channel: process.env.PLAYWRIGHT_CHANNEL || "chromium",
    baseURL: "http://127.0.0.1:5179",
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command: "npm run dev -- --port 5179",
    url: "http://127.0.0.1:5179",
    reuseExistingServer: !process.env.CI,
  },
  workers: 1,
});

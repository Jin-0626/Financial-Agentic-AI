import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  use: {
    channel: process.env.PLAYWRIGHT_CHANNEL || "chromium",
    baseURL: "http://127.0.0.1:5180",
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command: "npm run preview -- --port 5180",
    url: "http://127.0.0.1:5180",
    reuseExistingServer: false,
  },
});

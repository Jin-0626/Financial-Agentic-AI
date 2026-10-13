import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const python = resolve(
  "../.venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const fixture = resolve("../tests/production_server.py");

export default defineConfig({
  testDir: "./production-tests",
  workers: 1,
  use: {
    channel: process.env.PLAYWRIGHT_CHANNEL || "chromium",
    baseURL: "http://127.0.0.1:8019",
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command:
      process.env.PRODUCTION_TEST_SERVER || `"${python}" -X utf8 "${fixture}"`,
    url: "http://127.0.0.1:8019/api/auth/config",
    reuseExistingServer: false,
    timeout: 120000,
  },
});

import { test, expect } from "@playwright/test";

const summary = {
  id: "trace-1",
  name: "Research 99SMART",
  thread_id: "default-org__local-user__t",
  status: "error",
  started_at: "2026-10-12T10:00:00Z",
  duration_ms: 1500,
  call_count: 2,
  limited: false,
};
const detail = {
  ...summary,
  calls: [
    {
      id: "parent",
      parent_id: null,
      name: "Research agent",
      kind: "chain",
      status: "success",
      started_at: summary.started_at,
      duration_ms: 1500,
      input: null,
      output: null,
    },
    {
      id: "quote",
      parent_id: "parent",
      name: "market_data",
      kind: "tool",
      status: "error",
      started_at: summary.started_at,
      duration_ms: 1250,
      input: { symbol: "5326.KL" },
      output: { error: "Provider unavailable" },
      error: "Provider unavailable",
    },
  ],
};

test("traces inspect tool input output duration and failure", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const data =
      path === "/api/auth/config"
        ? { required: false, configured: false }
        : path === "/api/traces"
          ? { traces: [summary] }
          : detail;
    await route.fulfill({ json: data });
  });
  await page.goto("/traces");
  await expect(
    page.getByRole("heading", { name: "Traces", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Call details")).toContainText("market_data");
  await expect(page.getByLabel("Call details")).toContainText("5326.KL");
  await expect(page.getByLabel("Call details")).toContainText(
    "Provider unavailable",
  );
  await expect(page.getByLabel("Call details")).toContainText("1.25 s");
  await page.getByLabel("Filter calls").fill("nothing");
  await expect(page.getByText("No matching calls.")).toBeVisible();
  await page.getByLabel("Filter calls").fill("");
  await page.getByLabel("Call type").selectOption("all");
  await expect(page.getByLabel("Call tree").getByRole("button")).toHaveCount(2);
});

test("traces show empty and unavailable states", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    if (route.request().url().includes("/auth/config"))
      return route.fulfill({ json: { required: false, configured: false } });
    return route.fulfill({ json: { traces: [] } });
  });
  await page.goto("/traces");
  await expect(
    page.getByText("No traces yet.", { exact: false }),
  ).toBeVisible();
  await page.route("**/api/traces?**", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Trace service unavailable" },
    }),
  );
  await page.getByRole("button", { name: "Refresh traces" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Trace service unavailable",
  );
});

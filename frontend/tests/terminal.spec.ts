import { test, expect, Page } from "@playwright/test";
const doc = {
  id: "saved",
  revision: 1,
  portfolio: {
    name: "Long term",
    currency: "USD",
    benchmark: "SPY",
    period: "1y",
    positions: [{ symbol: "AAPL", quantity: "2", average_cost: "100" }],
  },
  transactions: [
    {
      id: "buy",
      symbol: "AAPL",
      side: "BUY",
      quantity: "2",
      price: "100",
      date: "2025-01-01",
    },
  ],
};
const summary = {
  sectors: [{ name: "Technology", market_value: "240", weight_percent: "100" }],
  revision: 2,
  positions: [
    {
      ...doc.portfolio.positions[0],
      price: "120",
      market_value: "240",
      cost_basis: "200",
      unrealized_gain: "40",
      weight_percent: "100",
      sector: "Technology",
      name: "Apple",
      market_state: "CLOSED",
      as_of: 1760000000,
      change_percent: 2,
    },
  ],
  market_value: "240",
  cost_basis: "200",
  unrealized_gain: "40",
  realized_gain: "0",
  dividends: "0",
  currency: "USD",
  fetched_at: "2026-10-08T10:00:00Z",
  errors: [],
  history: [{ date: "2025-01-01", market_value: "200", cost_basis: "200" }],
  projection: {
    dates: ["2025-01-01", "2025-01-02"],
    portfolio: [100, 120],
    benchmark: [100, 105],
    method: "projection",
    limitations:
      "Fixed-holdings back-projection, not actual trading performance.",
  },
};
async function mock(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = [];
    if (path === "/api/auth/config")
      data = { required: false, configured: false };
    if (path === "/api/portfolio/list")
      data = {
        portfolios: [{ id: "saved", name: "Long term", currency: "USD" }],
      };
    if (path === "/api/portfolio") data = doc;
    if (path === "/api/portfolio/summary") data = summary;
    if (path === "/api/portfolio/symbols")
      data = {
        symbols: [{ symbol: "AAPL", name: "Apple", exchange: "NASDAQ" }],
      };
    if (
      path === "/api/portfolio/create" ||
      path === "/api/portfolio/transactions"
    )
      data = doc;
    if (path === "/api/portfolio/import/preview")
      data = {
        preview_hash: "hash",
        expected_revision: 1,
        transaction_count: 1,
        document: doc,
      };
    if (path === "/api/portfolio/import/commit") data = doc;
    if (path === "/api/portfolio/export")
      data = {
        format_version: "1.0",
        portfolio_name: "Long term",
        currency: "USD",
        transactions: [],
      };
    await route.fulfill({ json: data });
  });
}
test("saved portfolio loads automatically and views work", async ({ page }) => {
  await mock(page);
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/portfolio/);
  await expect(page.getByLabel("Selected portfolio")).toHaveValue("saved");
  await expect(page.getByText("Apple", { exact: true })).toBeVisible();
  await expect(
    page.getByText("$240.00", { exact: true }).first(),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/portfolio-overview.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Heatmap", exact: true }).click();
  await expect(page.locator(".heatmap")).toContainText("AAPL");
  await page.getByRole("tab", { name: "Performance", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Fixed-holdings projection" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Transactions", exact: true }).click();
  await expect(page.getByText("BUY", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/portfolio.png", fullPage: true });
  await page.reload();
  await expect(page.getByLabel("Selected portfolio")).toHaveValue("saved");
});
test("trade uses canonical symbol and decimal strings", async ({ page }) => {
  await mock(page);
  await page.goto("/portfolio");
  await expect(
    page.getByRole("button", { name: "Record trade", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Record trade", exact: true }).click();
  await page.getByPlaceholder("e.g. Apple or 1155.KL").fill("Apple");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByLabel("Trade symbol").selectOption("AAPL");
  await page.getByLabel("Quantity", { exact: true }).fill("0.3");
  await page.getByLabel("Price (USD)", { exact: true }).fill("0.1");
  const request = page.waitForRequest((r) => r.url().includes("/transactions"));
  await page.getByRole("button", { name: "Record transaction" }).click();
  expect((await request).postDataJSON()).toMatchObject({
    symbol: "AAPL",
    quantity: "0.3",
    price: "0.1",
  });
});
test("import requires preview and offers New/Merge", async ({ page }) => {
  await mock(page);
  await page.goto("/portfolio");
  await expect(page.getByLabel("Selected portfolio")).toHaveValue("saved");
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await page.getByLabel("JSON", { exact: true }).fill(
    JSON.stringify({
      portfolio_name: "Long term",
      currency: "USD",
      transactions: [
        {
          date: "2025-01-01",
          symbol: "AAPL",
          type: "BUY",
          quantity: 2,
          price: 100,
        },
      ],
    }),
  );
  await expect(page.getByRole("button", { name: "Commit import" })).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(
    page.getByRole("button", { name: "Commit import" }),
  ).toBeVisible();
  await expect(page.getByLabel("Import mode").locator("option")).toHaveText([
    "New",
    "Merge",
  ]);
  const request = page.waitForRequest((r) =>
    r.url().includes("/import/commit"),
  );
  await page.getByRole("button", { name: "Commit import" }).click();
  expect((await request).postDataJSON()).toMatchObject({
    preview_hash: "hash",
    expected_revision: 1,
  });
});
test("five-minute refresh stops while hidden", async ({ page }) => {
  await page.clock.install();
  await mock(page);
  let calls = 0;
  page.on("request", (r) => {
    if (r.url().includes("/summary")) calls++;
  });
  await page.goto("/portfolio");
  await expect(page.getByText("Apple", { exact: true })).toBeVisible();
  const before = calls;
  await page.clock.fastForward(300000);
  await expect.poll(() => calls).toBeGreaterThan(before);
  await page.evaluate(() =>
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    }),
  );
  const hidden = calls;
  await page.clock.fastForward(300000);
  expect(calls).toBe(hidden);
});
test("readable research, activity and financial statement values", async ({
  page,
}) => {
  await mock(page);
  const company = {
    symbol: "AAPL",
    currency: "USD",
    source: "Yahoo Finance",
    frequency: "Annual",
    income_statement: { "2025-12-31": { Revenue: 100, "Net Income": 0 } },
    balance_sheet: {},
    cash_flow: {},
  };
  await page.route("**/api/chat/stream", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: [
        { type: "tool_call", id: "task", name: "task", agent: "research" },
        {
          type: "tool_result",
          id: "task",
          name: "task",
          status: "success",
          output: "Verified provider evidence",
        },
        { type: "token", content: "RAW_INTERNAL_JSON" },
        { type: "financial_statements", companies: [company] },
        {
          type: "done",
          success: true,
          result: "## Executive Summary\n\nA readable report.",
          todos: [{ task: "Research", status: "completed" }],
        },
      ]
        .map((e) => "data: " + JSON.stringify(e) + "\n\n")
        .join(""),
    }),
  );
  await page.goto("/research");
  await page.getByLabel("Research question").fill("Research Apple");
  await page.getByRole("button", { name: "Research →" }).click();
  await expect(
    page.getByRole("heading", { name: "Executive Summary" }),
  ).toBeVisible();
  await expect(page.getByText("RAW_INTERNAL_JSON")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Financial statements · AAPL" }),
  ).toBeVisible();
  await expect(page.getByText("Net Income", { exact: true })).toBeVisible();
  await expect(page.locator(".activity-item")).toContainText("research");
  await page.screenshot({ path: "test-results/research.png", fullPage: true });
});
test("truncated stream never presents completed report", async ({ page }) => {
  await mock(page);
  await page.route("**/api/chat/stream", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body:
        "data: " +
        JSON.stringify({ type: "token", content: "Incomplete" }) +
        "\n\n",
    }),
  );
  await page.goto("/research");
  await page.getByLabel("Research question").fill("Research");
  await page.getByRole("button", { name: "Research →" }).click();
  await expect(page.getByRole("alert")).toContainText("stream ended");
  await expect(page.locator(".message.assistant")).toHaveCount(0);
});

test("research accepts CRLF multiline SSE and ignores comments", async ({
  page,
}) => {
  await mock(page);
  await page.route("**/api/chat/stream", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: ': keepalive\r\n\r\nevent: message\r\ndata:{"type":"done",\r\ndata: "success":true,"result":"## Framed report"}\r\n\r\n',
    }),
  );
  await page.goto("/research");
  await page.getByLabel("Research question").fill("Research");
  await page.getByRole("button", { name: /^Research/ }).click();
  await expect(
    page.getByRole("heading", { name: "Framed report" }),
  ).toBeVisible();
});

test("proxy errors remain readable and hide HTML", async ({ page }) => {
  await mock(page);
  await page.route("**/api/portfolio/list?**", (route) =>
    route.fulfill({
      status: 502,
      contentType: "text/html",
      body: "<html>INTERNAL_PROXY_DETAIL</html>",
    }),
  );
  await page.goto("/portfolio");
  await expect(page.getByRole("alert")).toContainText("Request failed (502)");
  await expect(page.getByText("INTERNAL_PROXY_DETAIL")).toHaveCount(0);
});

test("malformed research event fails without a completed report", async ({
  page,
}) => {
  await mock(page);
  await page.route("**/api/chat/stream", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: "data: {broken\n\n",
    }),
  );
  await page.goto("/research");
  await page.getByLabel("Research question").fill("Research");
  await page.getByRole("button", { name: /^Research/ }).click();
  await expect(page.getByRole("alert")).toContainText("invalid data");
  await expect(page.locator(".message.assistant")).toHaveCount(0);
});

test("unimplemented sample modules stay unavailable", async ({ page }) => {
  await mock(page);
  await page.goto("/modules");
  await expect(
    page.getByRole("heading", { name: "Terminal modules" }),
  ).toBeVisible();
  const equity = page.locator("article").filter({
    has: page.getByRole("heading", { name: "Equity Trading", exact: true }),
  });
  await expect(
    equity.getByRole("button", { name: "Unavailable" }),
  ).toBeDisabled();
  await expect(equity.getByRole("link")).toHaveCount(0);
});

test("production configuration errors do not expose local workspace", async ({
  page,
}) => {
  await mock(page);
  await page.route("**/api/auth/config", (route) =>
    route.fulfill({ json: { required: true, configured: false } }),
  );
  await page.goto("/portfolio");
  await expect(page.getByRole("alert")).toContainText(
    "login is not configured",
  );
  await expect(page.getByLabel("Organization")).toHaveCount(0);
  await expect(page.getByLabel("Selected portfolio")).toHaveCount(0);
});

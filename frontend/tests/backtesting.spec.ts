import { test, expect, Page } from "@playwright/test";
const csv = "date,close\n2025-01-02,100\n2025-01-03,110\n2025-01-06,90";
const reply = {
  status: "success",
  provenance: { currency: "USD", engine: "rust" },
  result: {
    strategy: "buy_and_hold",
    initial_cash: "1000.00000000",
    final_equity: "818.18181818",
    total_return: -0.18181818,
    equity: [
      { date: "2025-01-02", equity: "1000" },
      { date: "2025-01-03", equity: "1000" },
      { date: "2025-01-06", equity: "818.18181818" },
    ],
    trades: [
      {
        date: "2025-01-03",
        side: "BUY",
        quantity: "9.09090909",
        price: "110.00000000",
        cost: "0.00000000",
      },
    ],
    assumptions: [
      "Signals from prior close execute at next close",
      "Final holdings marked to market; no forced liquidation",
    ],
    metrics: {
      maximum_drawdown: -0.18181818,
      trade_count: 1,
      total_costs: "0.00000000",
      open_position: true,
    },
  },
};
async function setup(page: Page) {
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = {};
    if (path === "/api/auth/config")
      data = { required: false, configured: false };
    if (path === "/api/backtesting/catalog")
      data = {
        execution: "Prior-close signals execute at the next close",
        strategies: [
          {
            id: "buy_and_hold",
            name: "Buy And Hold",
            description: "Buy at next close",
            parameters: {},
          },
          {
            id: "moving_average_crossover",
            name: "Moving Average Crossover",
            description: "Two averages",
            parameters: {
              fast_window: {
                title: "Fast Window",
                default: 5,
                minimum: 2,
                maximum: 2520,
              },
              slow_window: {
                title: "Slow Window",
                default: 20,
                minimum: 3,
                maximum: 2520,
              },
            },
          },
          {
            id: "rsi",
            name: "Rsi",
            description: "RSI signals",
            parameters: {
              rsi_window: {
                title: "Rsi Window",
                default: 14,
                minimum: 2,
                maximum: 2520,
              },
              lower: { title: "Lower", default: 30, minimum: 0, maximum: 100 },
              upper: { title: "Upper", default: 70, minimum: 0, maximum: 100 },
            },
          },
        ],
      };
    if (path === "/api/backtesting/run") data = reply;
    if (path === "/api/backtesting/history")
      data = {
        status: "ok",
        currency: "MYR",
        symbol: "1155.KL",
        coverage: "Adjusted daily closing prices",
        data: [
          { date: "2025-01-02", close: 100 },
          { date: "2025-01-03", close: 110 },
          { date: "2025-01-06", close: 90 },
        ],
      };
    return route.fulfill({ json: data });
  });
  await page.goto("/backtesting");
  await expect(
    page.getByRole("combobox", { name: "Strategy", exact: true }),
  ).toBeEnabled();
}
test("CSV to results, charts, trades and exports preserves the completed run", async ({
  page,
}) => {
  await setup(page);
  await expect(
    page.getByRole("button", { name: "Run backtest", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Price CSV", { exact: true }).fill(csv);
  await page.getByRole("button", { name: "Validate CSV" }).click();
  await page.getByLabel("Initial capital", { exact: true }).fill("1000");
  await page.getByLabel("Cost per trade (basis points)").fill("0");
  const request = page.waitForRequest("**/api/backtesting/run**");
  await page.getByRole("button", { name: "Run backtest", exact: true }).click();
  expect((await request).postDataJSON()).toMatchObject({
    prices: [100, 110, 90],
    initial_cash: "1000",
    cost_bps: 0,
    strategy: "buy_and_hold",
  });
  await expect(page.getByRole("status")).toHaveText("COMPLETED");
  await expect(page.getByRole("tabpanel")).toContainText("$818.18");
  await page.getByRole("tab", { name: "Equity Curve" }).click();
  await expect(
    page.getByRole("heading", { name: "Equity (USD)" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Drawdown (%)" }),
  ).toBeVisible();
  await expect(page.locator(".bt-chart svg")).toHaveCount(2);
  await page.screenshot({
    path: "../.review-tmp/backtesting-desktop.png",
    fullPage: true,
  });
  await page.getByRole("tab", { name: "Trades", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "BUY", exact: true }),
  ).toBeVisible();
  const csvDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export trades CSV" }).click();
  expect((await csvDownload).suggestedFilename()).toBe("backtest-trades.csv");
  await page.getByLabel("Currency", { exact: true }).fill("MYR");
  await page.getByRole("tab", { name: "Raw JSON" }).click();
  await expect(page.locator(".bt-json")).toContainText('"currency": "USD"');
  const jsonDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export JSON" }).click();
  expect((await jsonDownload).suggestedFilename()).toBe("backtest-result.json");
});
test("validates CSV, switches strategy parameters and invalidates edited data", async ({
  page,
}) => {
  await setup(page);
  await page
    .getByLabel("Price CSV", { exact: true })
    .fill(csv.replace("2025-01-06", "2025-01-03"));
  await page.getByRole("button", { name: "Validate CSV" }).click();
  await expect(page.getByRole("alert")).toContainText("unique and increasing");
  await expect(
    page.getByRole("button", { name: "Run backtest", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Price CSV", { exact: true }).fill(csv);
  await page.getByRole("button", { name: "Validate CSV" }).click();
  await page
    .getByRole("combobox", { name: "Strategy", exact: true })
    .selectOption("moving_average_crossover");
  await expect(page.getByLabel("Fast Window", { exact: true })).toHaveValue(
    "5",
  );
  await page
    .getByRole("combobox", { name: "Strategy", exact: true })
    .selectOption("rsi");
  await expect(page.getByLabel("Fast Window", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Rsi Window", { exact: true })).toHaveValue(
    "14",
  );
  await page
    .getByLabel("Price CSV", { exact: true })
    .fill(csv + "\n2025-01-07,95");
  await expect(
    page.getByRole("button", { name: "Run backtest", exact: true }),
  ).toBeDisabled();
});
test("loads daily history with provider currency and displays failures", async ({
  page,
}) => {
  await setup(page);
  await page
    .getByLabel("Data source", { exact: true })
    .selectOption("Yahoo Finance");
  await page.getByLabel("Symbol", { exact: true }).fill("1155.KL");
  await page.getByLabel("Start date", { exact: true }).fill("2025-01-01");
  await page.getByLabel("End date", { exact: true }).fill("2025-01-06");
  await page.getByRole("button", { name: "Load daily history" }).click();
  await expect(page.getByLabel("Currency", { exact: true })).toHaveValue("MYR");
  await expect(
    page.getByRole("button", { name: "Run backtest", exact: true }),
  ).toBeEnabled();
  await page.route("**/api/backtesting/run**", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Analytics runtime unavailable" },
    }),
  );
  await page.getByRole("button", { name: "Run backtest", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Analytics runtime unavailable",
  );
  await expect(
    page.getByRole("button", { name: "Export JSON" }),
  ).toBeDisabled();
});

test("CSV upload and mobile layout", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  await page.getByLabel("Import price CSV", { exact: true }).setInputFiles({
    name: "prices.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv),
  });
  await expect(
    page.getByRole("button", { name: "Run backtest", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".bt-data-ready")).toContainText("prices.csv");
  await page.getByRole("button", { name: "Run backtest", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("COMPLETED");
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "../.review-tmp/backtesting-mobile.png",
    fullPage: true,
  });
});

test("cancel request returns the screen to ready without a stale result", async ({
  page,
}) => {
  await setup(page);
  await page.getByLabel("Price CSV", { exact: true }).fill(csv);
  await page.getByRole("button", { name: "Validate CSV" }).click();
  await page.route("**/api/backtesting/run**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    await route.fulfill({ json: reply }).catch(() => {});
  });
  await page.getByRole("button", { name: "Run backtest", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("RUNNING");
  await page.getByRole("button", { name: "Cancel request" }).click();
  await expect(page.getByRole("alert")).toContainText("Request cancelled");
  await expect(page.getByRole("status")).toHaveText("READY");
  await expect(
    page.getByRole("button", { name: "Run backtest", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Export JSON" }),
  ).toBeDisabled();
});

import { test, expect, Page } from "@playwright/test";
import { restore, defaults } from "../src/terminal/state";
const key = 'financial-terminal:v1:["default-org","local-user"]';
const asset = {
  id: "yahoo:5326.KL",
  symbol: "5326.KL",
  name: "Fixture stock",
  asset_class: "equity",
  exchange: "Bursa",
};
const bitcoin = {
  id: "coingecko:bitcoin",
  symbol: "BTC",
  name: "Bitcoin",
  asset_class: "crypto",
  exchange: "CoinGecko",
};
const report =
  "## Executive Summary\n\nCompleted fixture research.\n\n## Analysis\n\nVerified fixture only.\n\n## Key Risks\n\nLimited sample.\n\n## Recommendations\n\nVerify current evidence.\n\n## Sources\n\nFixture provider.";
async function mock(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let data: unknown = [];
    if (url.pathname === "/api/auth/config")
      data = { required: false, configured: false };
    if (url.pathname === "/api/traces") data = { traces: [] };
    if (url.pathname.startsWith("/api/market/")) {
      let payload: unknown = [];
      if (url.pathname.endsWith("/search")) payload = [bitcoin];
      if (url.pathname.endsWith("/quote")) {
        const selected =
          url.searchParams.get("instrument") === bitcoin.id ? bitcoin : asset;
        payload = {
          instrument: selected,
          price: "3.2",
          currency: selected.asset_class === "crypto" ? "USD" : "MYR",
          change_percent: 1.2,
        };
      }
      if (url.pathname.endsWith("/ohlcv"))
        payload = Array.from({ length: 60 }, (_, i) => ({
          time: 1700000000 + i * 86400,
          open: 3 + i * 0.01,
          high: 3.1 + i * 0.01,
          low: 2.9 + i * 0.01,
          close: 3.05 + i * 0.01,
          volume: 1000,
          session: "2026-10-01",
        }));
      data = {
        status: "ok",
        data: payload,
        source: "Fixture provider",
        coverage: "Synthetic test fixture",
        observed_at: "2026-10-12",
        currency: "MYR",
      };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(data),
    });
  });
}
async function state(page: Page) {
  return page.evaluate((k) => JSON.parse(localStorage.getItem(k)!), key);
}

test("corrupt and incompatible stored layouts recover safely", () => {
  expect(restore("{bad").widgets).toHaveLength(5);
  expect(restore(JSON.stringify({ ...defaults(), version: 999 })).version).toBe(
    1,
  );
  const invalid = defaults();
  invalid.layout[0].w = 100;
  expect(restore(JSON.stringify(invalid)).layout[0].w).toBe(8);
});

test("remove add and chart settings persist across reload", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/terminal");
  await expect(
    page.getByRole("button", { name: "Remove quote widget" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Remove quote widget" }).click();
  await expect.poll(async () => (await state(page)).widgets.length).toBe(4);
  await page
    .getByRole("button", { name: "Hide Research", exact: true })
    .click();
  await page.getByRole("button", { name: "1Y", exact: true }).click();
  await page.getByRole("checkbox", { name: "RSI", exact: true }).check();
  await page.keyboard.press("Alt+7");
  await expect
    .poll(async () =>
      (await state(page)).widgets.some(
        (w: { type: string }) => w.type === "options",
      ),
    )
    .toBe(true);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Remove quote widget" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("checkbox", { name: "RSI", exact: true }),
  ).toBeChecked();
  await expect
    .poll(
      async () =>
        (await state(page)).widgets.find(
          (w: { type: string }) => w.type === "chart",
        ).settings.range,
    )
    .toBe(JSON.stringify("1Y"));
});

test("drag and resize update persisted grid positions", async ({ page }) => {
  await mock(page);
  await page.goto("/terminal");
  await page
    .getByRole("button", { name: "Hide Research", exact: true })
    .click();
  const before = await state(page);
  const handle = page.locator(".widget-drag").first();
  const box = await handle.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + 20, box!.y + 10);
  await page.mouse.down();
  await page.mouse.move(box!.x + 320, box!.y + 10, { steps: 8 });
  await page.mouse.up();
  await expect
    .poll(async () => JSON.stringify((await state(page)).layout))
    .not.toBe(JSON.stringify(before.layout));
  const resize = page.locator(".react-resizable-handle").first();
  await resize.scrollIntoViewIfNeeded();
  const r = await resize.boundingBox();
  const old = await state(page);
  await page.mouse.move(r!.x + r!.width / 2, r!.y + r!.height / 2);
  await page.mouse.down();
  await page.mouse.move(r!.x + r!.width / 2 - 60, r!.y + r!.height / 2 + 70, {
    steps: 8,
  });
  await page.mouse.up();
  await expect
    .poll(async () => JSON.stringify((await state(page)).layout))
    .not.toBe(JSON.stringify(old.layout));
});

test("global crypto selection keeps a pinned chart unchanged", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/terminal");
  await page.getByRole("button", { name: "Pin chart", exact: true }).click();
  await page.getByRole("button", { name: "Search symbol / workspace" }).click();
  await page
    .getByRole("textbox", { name: "Search instruments or commands" })
    .fill("bitcoin");
  await expect(
    page.getByRole("button", { name: /BTC.*Bitcoin/ }),
  ).toBeVisible();
  await page.keyboard.press("Enter");
  await expect.poll(async () => (await state(page)).active.id).toBe(bitcoin.id);
  expect(
    (await state(page)).widgets.find(
      (w: { type: string }) => w.type === "chart",
    ).instrument.id,
  ).toBe(asset.id);
});

test("closing research does not cancel and submits only public context", async ({
  page,
}) => {
  await mock(page);
  let release: () => void = () => {};
  const wait = new Promise<void>((resolve) => (release = resolve));
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/chat/stream", async (route) => {
    body = route.request().postDataJSON();
    await wait;
    await route.fulfill({
      contentType: "text/event-stream",
      body: `data: ${JSON.stringify({ type: "done", result: report, todos: [] })}\n\n`,
    });
  });
  await page.goto("/terminal");
  await page
    .getByPlaceholder("Research a company or market…")
    .fill("Review this company");
  await page.getByRole("button", { name: "Research →", exact: true }).click();
  await expect.poll(() => body).not.toBeNull();
  expect(
    (body!.terminal_context as Record<string, unknown>).portfolio,
  ).toBeUndefined();
  await page
    .getByRole("button", { name: "Close research sidebar", exact: true })
    .click();
  release();
  await page
    .getByRole("button", { name: "Open Research", exact: true })
    .click();
  await expect(page.getByText("Completed fixture research.")).toBeVisible();
});

test("identity switch isolates layouts and watchlists", async ({ page }) => {
  await mock(page);
  await page.goto("/terminal");
  await page.getByRole("button", { name: "Remove quote widget" }).click();
  await expect.poll(async () => (await state(page)).widgets.length).toBe(4);
  await page
    .getByRole("textbox", { name: "Organization", exact: true })
    .fill("another-org");
  await page
    .getByRole("button", { name: "Switch workspace", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Remove quote widget" }),
  ).toBeVisible();
  expect((await state(page)).widgets.length).toBe(4);
});

test("desktop and mobile terminal fit their viewport", async ({ page }) => {
  await mock(page);
  await page.goto("/terminal");
  await expect(page.locator(".market-chart")).toBeVisible();
  await page.screenshot({
    path: "../.review-tmp/terminal-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Close research sidebar" }).click();
  await expect(page.locator(".market-chart")).toBeVisible();
  await expect
    .poll(async () => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390);
  await expect
    .poll(async () =>
      page.locator(".react-grid-item").evaluateAll((items) =>
        items.every((item) => {
          const box = item.getBoundingClientRect();
          return box.x >= 10 && box.right <= 390 && box.width > 350;
        }),
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: "../.review-tmp/terminal-mobile.png",
    fullPage: true,
  });
});

test("expanded research can close and removing expanded chart restores workspace", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/terminal");
  await page.getByRole("button", { name: "Expand research sidebar" }).click();
  await page
    .getByRole("button", { name: "Hide Research", exact: true })
    .click();
  await expect(
    page.getByRole("complementary", { name: "Research sidebar" }),
  ).toBeHidden();
  await page.getByRole("button", { name: "Expand chart", exact: true }).click();
  await page.getByRole("button", { name: "Remove chart widget" }).click();
  await expect(
    page.getByRole("button", { name: "Remove quote widget" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Restore workspace" }),
  ).toHaveCount(0);
});

test("portfolio context requires explicit inclusion and is not stored in layout", async ({
  page,
}) => {
  await mock(page);
  await page.addInitScript(() =>
    localStorage.setItem("portfolio:default-org:local-user", "saved-fixture"),
  );
  await page.route("**/api/portfolio?**", (route) =>
    route.fulfill({
      json: {
        portfolio: {
          name: "Private fixture",
          currency: "MYR",
          positions: [
            { symbol: "5326.KL", quantity: "2", average_cost: "3.0" },
          ],
        },
      },
    }),
  );
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/chat/stream", (route) => {
    body = route.request().postDataJSON();
    return route.fulfill({
      contentType: "text/event-stream",
      body: `data: ${JSON.stringify({ type: "done", result: report, todos: [] })}\n\n`,
    });
  });
  await page.goto("/terminal");
  await page
    .getByRole("button", { name: "Include selected portfolio" })
    .click();
  await expect(
    page.getByText("Includes Private fixture holdings in this request."),
  ).toBeVisible();
  await page
    .getByPlaceholder("Research a company or market…")
    .fill("Review this company");
  await page.getByRole("button", { name: "Research →", exact: true }).click();
  await expect.poll(() => body).not.toBeNull();
  expect((body!.terminal_context as any).portfolio.positions[0].quantity).toBe(
    "2",
  );
  expect(JSON.stringify(await state(page))).not.toContain("Private fixture");
});

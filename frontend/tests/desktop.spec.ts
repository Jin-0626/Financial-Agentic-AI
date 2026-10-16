import { defaultBrowser } from "../src/browser-model";
import { expect, test, Page } from "@playwright/test";
import { defaultWorkspace, type Snapshot } from "../src/bridge";
import { compareDecimal } from "../src/watchlist";
import { alignContracts } from "../src/options-screen";
import type { Contract } from "../src/resources";

test("option rows preserve exact strikes and duplicate contracts", () => {
  const contract = (id: string, strike: string | null) =>
    ({
      contract: id,
      strike,
      bid: null,
      ask: null,
      last: null,
      volume: null,
      open_interest: null,
      implied_volatility: null,
      itm: false,
    }) satisfies Contract;
  const rows = alignContracts({
    calls: [
      contract("a", "9007199254740993.01"),
      contract("b", "100.0"),
      contract("c", "100"),
    ],
    puts: [contract("d", "100.00"), contract("e", null)],
    expiry: "2026-10-16",
    expiries: ["2026-10-16"],
  });
  expect(rows).toHaveLength(3);
  expect(rows[0].calls.map((c) => c.contract)).toEqual(["b", "c"]);
  expect(rows[0].puts[0].contract).toBe("d");
  expect(rows[2].strike).toBeNull();
});

test("desktop chrome, navigation history, index ticker and saved views work", async ({
  page,
}) => {
  await nativeFixture(page, false, true);
  await page.goto("/");
  await expect(page.locator(".title-bar")).toHaveCSS("height", "32px");
  await expect(page.getByLabel("Command prompt", { exact: true })).toBeHidden();
  await page.getByLabel("Collapse navigation").click();
  await expect(page.locator(".navigation-rail")).toHaveCSS("width", "48px");
  await page.getByLabel("Ticker mode").selectOption("indices");
  await expect(page.locator(".ticker-strip")).toContainText("Bursa KLCI");
  await page
    .getByRole("navigation", { name: "Workspace views" })
    .getByRole("button", { name: "markets", exact: true })
    .click();
  await expect(page.locator(".index-cards")).toBeVisible();
  await page.getByLabel("Back", { exact: true }).click();
  await expect(page.locator(".workspace")).toHaveAttribute(
    "data-view",
    "dashboard",
  );
  await page.getByLabel("Forward", { exact: true }).click();
  await expect(page.locator(".workspace")).toHaveAttribute(
    "data-view",
    "markets",
  );
  await page.getByLabel("Minimize window").click();
  await page.getByLabel("Maximize window").click();
  await expect(page.getByLabel("Restore window")).toBeVisible();
  await page.getByLabel("Restore window").click();
  expect(
    await page.evaluate(
      () =>
        (window as unknown as { testWindowCalls: string[] }).testWindowCalls,
    ),
  ).toContain("plugin:window|minimize");
  await page.getByLabel("Expand navigation").click();
  await page.getByLabel("Open quote inspector").click();
  await expect(
    page.getByRole("complementary", { name: "Instrument inspector" }),
  ).toBeVisible();
  await page
    .getByRole("navigation", { name: "Workspace views" })
    .getByRole("button", { name: "settings", exact: true })
    .click();
  await page
    .getByRole("navigation", { name: "Workspace views" })
    .getByRole("button", { name: "markets", exact: true })
    .click();
  await expect(
    page.getByRole("complementary", { name: "Instrument inspector" }),
  ).toBeVisible();
  await page.getByLabel("Close inspector").click();
  await page.screenshot({
    path: "test-results/markets-overview.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 960, height: 640 });
  await page.screenshot({
    path: "test-results/markets-overview-small.png",
    fullPage: true,
  });
});

test("options and calendar show supported data with explicit coverage", async ({
  page,
}) => {
  await nativeFixture(page, false, true);
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Workspace views" });
  await nav.getByRole("button", { name: "options", exact: true }).click();
  await expect(page.locator(".options-table tbody tr")).toHaveCount(2);
  await expect(page.locator('.options-table [title="CALL-A"]')).toHaveCount(6);
  await expect(page.locator('.options-table [title="CALL-B"]')).toHaveCount(6);
  await expect(page.getByLabel("Option expiry", { exact: true })).toHaveValue(
    "2026-10-16",
  );
  await page.screenshot({ path: "test-results/options.png", fullPage: true });
  await page.setViewportSize({ width: 960, height: 640 });
  await page.screenshot({
    path: "test-results/options-small.png",
    fullPage: true,
  });
  await nav.getByRole("button", { name: "calendar", exact: true }).click();
  await expect(page.locator(".calendar-week")).toBeVisible();
  await expect(
    page.getByText("Undated fixture event", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Table", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "Verified fixture event", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Calendar country").selectOption("USD");
  await expect(
    page.getByRole("cell", { name: "Undated fixture event", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Calendar country").selectOption("");
  await page.getByRole("button", { name: "Week", exact: true }).click();
  await page.screenshot({
    path: "test-results/calendar-small.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "test-results/calendar.png", fullPage: true });
  await nav.getByRole("button", { name: "dashboard", exact: true }).click();
  await nav.getByRole("button", { name: "calendar", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Week", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
});

test("decimal quote sorting preserves precision and sign", () => {
  expect(compareDecimal("9007199254740993.01", "9007199254740993.02")).toBe(-1);
  expect(compareDecimal("-0.002", "-0.01")).toBe(1);
  expect(compareDecimal("1.000", "1")).toBe(0);
});

test("watchlist panels bind independently and release hidden quote owners", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  await page.evaluate(async () => {
    const native = (
      window as unknown as {
        __TAURI_INTERNALS__: {
          invoke: (name: string, args: unknown) => Promise<unknown>;
        };
      }
    ).__TAURI_INTERNALS__;
    await native.invoke("terminal_watchlist", {
      mutation: {
        type: "create",
        id: "second",
        name: "Second",
        duplicate_from: null,
      },
    });
    await native.invoke("terminal_watchlist", {
      mutation: { type: "add", id: "second", symbols: ["yahoo:MSFT"] },
    });
    const spec = (id: string) => ({
      kind: "watchlist",
      binding: { type: "linked" },
      range: null,
      watchlist_id: id,
      table: {
        columns: ["instrument", "price", "change"],
        sort: null,
        descending: false,
      },
    });
    await native.invoke("terminal_action", {
      action: {
        type: "workspace",
        workspace: {
          desktop: {
            rail_collapsed: false,
            inspector_open: false,
            inspector_width: 290,
            inspector_tab: "quote",
            ticker_mode: "watchlist",
            market_tab: "overview",
            portfolio_tab: "holdings",
            calendar_tab: "economic",
            calendar_mode: "week",
          },
          screen: "dashboard",
          compact: false,
          active_layout: "Dual lists",
          layouts: [
            {
              name: "Dual lists",
              panels: { left: spec("main"), right: spec("second") },
              root: {
                type: "split",
                id: "root",
                axis: "horizontal",
                ratio: 0.5,
                first: {
                  type: "tabs",
                  id: "left-group",
                  panels: ["left"],
                  active: "left",
                },
                second: {
                  type: "tabs",
                  id: "right-group",
                  panels: ["right"],
                  active: "right",
                },
              },
            },
          ],
        },
      },
    });
  });
  const left = page.locator('[data-panel-id="left"] .watchlist'),
    right = page.locator('[data-panel-id="right"] .watchlist');
  await expect(left.locator("tbody tr")).toHaveCount(2);
  await expect(right.locator("tbody tr")).toHaveCount(1);
  await left.focus();
  await expect(page.locator(".ticker-strip")).toContainText("5326.KL");
  await right.focus();
  await expect(page.locator(".ticker-strip")).toContainText("MSFT");
  await expect(page.locator(".market-heading h2")).toContainText("5326.KL");
  const owners = () =>
    page.evaluate(
      () =>
        Object.keys(
          (
            window as unknown as { testSubscriptions: () => object }
          ).testSubscriptions(),
        ).length,
    );
  await expect.poll(owners).toBeGreaterThan(0);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(owners).toBe(0);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(owners).toBeGreaterThan(0);
});

test("named lists retain drafts after failed bulk writes and persist table settings", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  const watch = page.locator(".watchlist");
  await watch.getByText("Manage", { exact: true }).click();
  await watch.getByLabel("Watchlist name").fill("US desk");
  await watch.getByRole("button", { name: "Create list", exact: true }).click();
  await expect(
    watch.getByLabel("Named watchlist").locator("option:checked"),
  ).toHaveText("US desk");
  await expect(watch.locator("tbody tr")).toHaveCount(0);
  await expect(page.locator(".market-heading h2")).toContainText("5326.KL");
  await watch.getByText("Manage", { exact: true }).click();
  await watch.getByRole("button", { name: "Bulk add", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Bulk symbols")
    .fill("msft;MSFT\ncoingecko:bitcoin,bad/symbol");
  await dialog.getByRole("button", { name: "Preview symbols" }).click();
  await expect(dialog.getByLabel("Bulk preview")).toContainText(
    "Duplicates skipped: yahoo:MSFT",
  );
  await expect(
    dialog.getByRole("button", { name: "Commit symbols" }),
  ).toBeDisabled();
  await dialog.getByLabel("Bulk symbols").fill("msft;MSFT\ncoingecko:bitcoin");
  await dialog.getByRole("button", { name: "Preview symbols" }).click();
  await page.evaluate(() => {
    (window as unknown as { testFailWrite: boolean }).testFailWrite = true;
  });
  await dialog.getByRole("button", { name: "Commit symbols" }).click();
  await expect(dialog.getByRole("alert")).toContainText("SQLite write denied");
  await expect(dialog.getByLabel("Bulk symbols")).toHaveValue(
    "msft;MSFT\ncoingecko:bitcoin",
  );
  await page.evaluate(() => {
    (window as unknown as { testFailWrite: boolean }).testFailWrite = false;
  });
  await dialog.getByRole("button", { name: "Commit symbols" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(watch.locator("tbody tr")).toHaveCount(2);
  await watch.getByText("Columns", { exact: true }).click();
  await watch.getByRole("checkbox", { name: "Volume", exact: true }).check();
  await watch.getByText("Columns", { exact: true }).click();
  await expect(
    watch.getByRole("columnheader", { name: "Volume", exact: true }),
  ).toBeVisible();
  await watch
    .getByRole("button", { name: "MSFT Synthetic", exact: true })
    .click();
  await watch.getByRole("button", { name: "Move down", exact: true }).click();
  await expect(watch.locator("tbody tr").first()).toContainText("bitcoin");
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "settings", exact: true })
    .click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "dashboard", exact: true })
    .click();
  await expect(
    watch.getByRole("columnheader", { name: "Volume", exact: true }),
  ).toBeVisible();
  await expect(
    watch.getByLabel("Named watchlist").locator("option:checked"),
  ).toHaveText("US desk");
});

test("multi-chart presets retain independent ranges and saved layouts", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  await workspaceTools(page);
  await page.getByLabel("Dashboard template").selectOption("Two Chart Desk");
  await expect(page.locator(".history")).toHaveCount(2);
  await expect(
    page.locator(".history .ranges button[aria-pressed=true]"),
  ).toHaveText(["1M", "6M"]);
  await workspaceTools(page);
  await page.getByLabel("Dashboard template").selectOption("Four Chart Desk");
  await expect(page.locator(".history")).toHaveCount(4);
  await expect(
    page.locator(".history .ranges button[aria-pressed=true]"),
  ).toHaveText(["1D", "1M", "6M", "1Y"]);
  await page
    .locator(".ticker-strip")
    .getByRole("button", { name: /^AAPL/ })
    .click();
  for (const binding of await page.locator(".panel-binding").all())
    await expect(binding).toContainText("Linked · yahoo:AAPL");
  await workspaceTools(page);
  await page.getByLabel("Saved layout").selectOption("Moomoo Market Desk");
  await expect(page.locator(".history")).toHaveCount(1);
  await workspaceTools(page);
  await page.getByLabel("Saved layout").selectOption("Four Chart Desk");
  await expect(page.locator(".history")).toHaveCount(4);
  await page.screenshot({
    path: "test-results/four-chart-desk.png",
    fullPage: true,
  });
});

test("market browser opens instruments without adding membership", async ({
  page,
}) => {
  await nativeFixture(page, false, true);
  await page.goto("/");
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "markets", exact: true })
    .click();
  await page.getByRole("tab", { name: "Crypto", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Open BTC", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Open BTC", exact: true }).click();
  await expect(page.locator(".market-heading h2")).toContainText("bitcoin");
  await expect(page.locator(".ticker-strip")).not.toContainText("bitcoin");
  await page
    .getByRole("button", { name: "Add BTC to watchlist", exact: true })
    .click();
  await expect(page.locator(".ticker-strip")).toContainText("bitcoin");
  await page.getByRole("tab", { name: "US Equities", exact: true }).click();
  await expect(page.getByLabel("Market sector")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Next market page" }),
  ).toBeDisabled();
});

test("market desk supports chart interaction, sorting and a responsive inspector", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  const chart = page.getByRole("img", { name: "OHLC candlestick chart" });
  await expect(chart).toBeVisible();
  await expect(page.locator(".navigation-rail")).toHaveCSS("width", "132px");
  await page.getByLabel("Open quote inspector").click();
  const divider = page.getByRole("separator", {
    name: "Resize instrument inspector",
  });
  await divider.focus();
  await divider.press("ArrowLeft");
  await expect(divider).toHaveAttribute("aria-valuenow", "300");
  await expect(page.locator(".inspector-slot")).toHaveCSS(
    "flex-basis",
    "300px",
  );
  await divider.press("End");
  await expect(divider).toHaveAttribute("aria-valuenow", "420");
  await divider.dblclick();
  await expect(divider).toHaveAttribute("aria-valuenow", "290");
  await expect
    .poll(() =>
      chart.evaluate((element) =>
        Math.abs(
          element.getBoundingClientRect().width -
            (element as SVGSVGElement).viewBox.baseVal.width,
        ),
      ),
    )
    .toBeLessThan(2);
  await page.getByLabel("Sort watchlist").selectOption("instrument");
  await expect(page.locator(".watchlist tbody tr").first()).toContainText(
    "5326.KL",
  );
  await page.getByLabel("Reverse watchlist sort").click();
  await expect(page.locator(".watchlist tbody tr").first()).toContainText(
    "AAPL",
  );
  await chart.focus();
  await chart.press("ArrowLeft");
  await expect(page.locator(".crosshair")).toBeVisible();
  const box = await chart.boundingBox();
  if (!box) throw new Error("chart bounds");
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  for (let i = 0; i < 5; i++) {
    await page.mouse.wheel(0, -100);
    await page.waitForTimeout(50);
  }
  await expect(page.locator(".chart-controls")).not.toContainText("60 candles");
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.65, box.y + box.height * 0.5, {
    steps: 5,
  });
  await page.mouse.up();
  await expect(page.locator(".chart-controls")).toContainText(
    "Historical view",
  );
  await page
    .getByRole("button", { name: "Follow latest", exact: true })
    .click();
  await expect(page.locator(".chart-controls")).toContainText(
    "Following latest",
  );
  await page.getByRole("button", { name: "Reset view", exact: true }).click();
  await expect(page.locator(".chart-controls")).toContainText("60 candles");
  await page.getByLabel("Close inspector").click();
  await page.setViewportSize({ width: 960, height: 640 });
  await expect(
    page.getByRole("complementary", { name: "Instrument inspector" }),
  ).toBeHidden();
  await page
    .getByRole("button", { name: "Instrument details", exact: true })
    .click();
  await expect(
    page.getByRole("complementary", { name: "Instrument inspector" }),
  ).toBeVisible();
  await expect(page.getByLabel("Close inspector")).toBeFocused();
  await page.getByLabel("Close inspector").press("Shift+Tab");
  await expect(
    page
      .locator(".instrument-inspector")
      .getByRole("button", { name: "Calendar", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Instrument details", exact: true }),
  ).toBeFocused();
  await page.getByLabel("Open command prompt").click();
  await expect(
    page.getByLabel("Command prompt", { exact: true }),
  ).toBeInViewport();
  await page.screenshot({
    path: "test-results/desktop-small.png",
    fullPage: true,
  });
});

test("duplicate chart panels retain pinned symbols and independent ranges", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  await workspaceTools(page);
  await page.getByLabel("Add dashboard panel").selectOption("chart");
  const input = page.locator(
    '.panel-instance:not([data-panel-id="chart"]) .panel-binding input:not([type="checkbox"])',
  );
  await expect(input).toHaveCount(1);
  const instance = page.locator(
    '.panel-instance:not([data-panel-id="chart"]):has(.panel-binding)',
  );
  await instance.locator(".panel-binding summary").click();
  await input.fill("AAPL");
  await instance
    .getByRole("button", { name: "Pin symbol", exact: true })
    .click();
  await expect(instance.locator(".panel-binding")).toContainText(
    "Pinned · yahoo:AAPL",
  );
  await instance.getByRole("button", { name: "1D", exact: true }).click();
  await expect(
    instance.getByRole("button", { name: "1D", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page
    .locator(".ticker-strip")
    .getByRole("button", { name: /^5326.KL/ })
    .click();
  await expect(instance.locator(".panel-binding")).toContainText(
    "Pinned · yahoo:AAPL",
  );
  await expect(
    page.locator('[data-panel-id="chart"] .panel-binding'),
  ).toContainText("Linked · yahoo:5326.KL");
  await instance.locator(".panel-binding summary").click();
  await instance
    .getByRole("button", { name: "Follow active instrument", exact: true })
    .click();
  await expect(instance.locator(".panel-binding")).toContainText(
    "Linked · yahoo:5326.KL",
  );
});

test("portfolio revision conflicts keep the draft visible without publishing an edit", async ({
  page,
}) => {
  await nativeFixture(page, false, true, true);
  await page.goto("/");
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "portfolio", exact: true })
    .click();
  await page
    .getByRole("button", { name: "New portfolio", exact: true })
    .click();
  await page.getByLabel("Portfolio name").fill("Conflict fund");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Conflict fund · revision 1" }),
  ).toBeVisible();
  await page.evaluate(() => {
    const native = (
      window as unknown as {
        __TAURI_INTERNALS__: {
          invoke: (
            name: string,
            args: Record<string, unknown>,
          ) => Promise<unknown>;
        };
      }
    ).__TAURI_INTERNALS__;
    const invoke = native.invoke;
    native.invoke = (name, args) =>
      name === "terminal_service" &&
      (args.request as { type: string }).type === "portfolio_save"
        ? Promise.reject("HTTP status 409")
        : invoke(name, args);
  });
  await page
    .getByRole("button", { name: "Edit portfolio", exact: true })
    .click();
  await page.getByLabel("Portfolio name").fill("Unsaved change");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("form", { name: "Portfolio editor" }).getByRole("alert"),
  ).toContainText("HTTP status 409");
  await expect(page.getByLabel("Portfolio name")).toHaveValue("Unsaved change");
  await expect(
    page.getByRole("heading", { name: "Conflict fund · revision 1" }),
  ).toBeVisible();
});

test("portfolio editing, transactions, reviewed imports and explicit deletion use native IPC", async ({
  page,
}) => {
  await nativeFixture(page, false, true, true);
  await page.goto("/");
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "portfolio", exact: true })
    .click();
  await page
    .getByRole("button", { name: "New portfolio", exact: true })
    .click();
  await page.getByLabel("Portfolio name").fill("My fund");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "My fund · revision 1" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Edit portfolio", exact: true })
    .click();
  await page.getByLabel("Portfolio name").fill("Edited fund");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Edited fund · revision 2" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Record transaction", exact: true })
    .click();
  await page.getByLabel("Transaction symbol").fill("AAPL");
  await page.getByLabel("Transaction quantity").fill("2.5000");
  await page.getByLabel("Transaction price").fill("123.4500");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Edited fund · revision 3" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Transactions", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "2.5000", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Import portfolio", exact: true })
    .click();
  const data = JSON.stringify({
    portfolio: {
      name: "Imported fund",
      owner: "",
      currency: "USD",
      benchmark: "SPY",
      period: "1y",
      positions: [],
    },
    transactions: [],
  });
  await page.getByLabel("Import portfolio JSON").fill(data);
  await page
    .getByRole("button", { name: "Preview import", exact: true })
    .click();
  await expect(page.getByLabel("Import preview")).toContainText(
    "Imported fund",
  );
  await page.getByLabel("Import portfolio JSON").fill(data + " ");
  await expect(
    page.getByRole("button", { name: "Commit reviewed import" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Preview import", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Commit reviewed import", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Imported fund · revision 1" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Delete portfolio", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Delete confirmed portfolio" }),
  ).toBeDisabled();
  await page.getByLabel("Confirm portfolio deletion").fill("Imported fund");
  await page
    .getByRole("button", { name: "Delete confirmed portfolio" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Edited fund · revision 3" }),
  ).toBeVisible();
});

async function workspaceTools(page: Page) {
  const menu = page.locator(".workspace-tools");
  if (!(await menu.evaluate((element) => (element as HTMLDetailsElement).open)))
    await menu.locator(":scope > summary").click();
}

async function nativeFixture(
  page: Page,
  offline = false,
  live = false,
  full = false,
  rich = false,
) {
  await page.addInitScript(
    ({ initialOffline, live, full, rich, workspace }) => {
      let offline = initialOffline;
      type Portfolio = {
        name: string;
        owner: string;
        currency: string;
        benchmark: string;
        period: string;
        positions: { symbol: string; quantity: string; average_cost: string }[];
      };
      type Doc = {
        id: string;
        revision: number;
        portfolio: Portfolio;
        transactions: {
          id: string;
          symbol: string;
          side: string;
          quantity: string;
          price: string;
          date: string;
          notes: string;
        }[];
      };
      const documents = new Map<string, Doc>();
      let documentCounter = 0;
      const callbacks = new Map<number, (event: unknown) => void>();
      const listeners = new Map<number, number>();
      let next = 1;
      let resourceGeneration = 0;
      const subscriptions: Record<
        string,
        {
          owner: string;
          generation: number;
          revision: number;
          request: unknown;
          payload: unknown;
          loading: boolean;
          error: null;
        }
      > = {};
      const instrument = (id: string) => ({
        id,
        symbol: id.split(":")[1],
        name: id.split(":")[1],
        exchange: "Synthetic",
        asset_class: "equity",
      });
      const market = (data: unknown) => ({
        status: "ok",
        data,
        source: "Synthetic Brownian motion",
        fetched_at: "2026-10-14T00:00:00Z",
        observed_at: "2026-10-14T00:00:00Z",
        coverage: "Synthetic data; not market prices",
        stale: false,
        error: null,
        limitations: [],
        interval: "Day",
      });
      const quote = (id: string) =>
        market({
          instrument: instrument(id),
          price: "123.45",
          change_percent: "1.25",
          volume: "42000",
          currency: "MOCK",
          bid: null,
          ask: null,
        });
      const initial = "yahoo:5326.KL";
      const snapshot = {
        revision: 1,
        watchlist: { symbols: [initial, "yahoo:AAPL"] },
        preferences: {
          selected: initial,
          range: "6M",
          history: [],
          keymap: [
            { key: "Up", operation: "Up" },
            { key: "Down", operation: "Down" },
            { key: "Enter", operation: "Select" },
            { key: { Char: ":" }, operation: "Command" },
            { key: { Char: "t" }, operation: "NextRange" },
            { key: { Ctrl: "c" }, operation: "Quit" },
          ],
        },
        quotes: {
          [initial]: quote(initial),
          "yahoo:AAPL": quote("yahoo:AAPL"),
        },
        candles: market(
          Array.from({ length: 60 }, (_, index) => ({
            time: 1700000000 + index * 86400,
            open: 120 + index / 10,
            high: 122 + index / 10,
            low: 119 + index / 10,
            close: 121 + index / 10,
            volume: 3000,
          })),
        ),
        mode: live ? "HTTP" : "MOCK",
        message: "Ready",
        latency_ms: 2,
        quitting: false,
      } as unknown as Snapshot;
      snapshot.preferences.workspace = workspace;
      snapshot.watchlists = [
        {
          id: "main",
          name: "My Watchlist",
          symbols: [...snapshot.watchlist.symbols],
        },
      ];
      snapshot.preferences.active_watchlist_id = "main";
      const platform = window as unknown as {
        isTauri: boolean;
        __TAURI_INTERNALS__: unknown;
        __TAURI_EVENT_PLUGIN_INTERNALS__: unknown;
        testPublish: (snapshot: Snapshot) => void;
        testRecover: () => void;
        testFailWrite?: boolean;
        testOptionsState?: string;
        testNoQuote?: boolean;
        testDelayOptions?: number;
        testFinishedOldOptions?: boolean;
        testSubscriptions: () => unknown;
        testWindowCalls: string[];
      };
      platform.testWindowCalls = [];
      let maximized = false;
      platform.isTauri = true;
      platform.testSubscriptions = () => structuredClone(subscriptions);
      platform.testRecover = () => {
        offline = false;
      };
      const publish = (data: Snapshot) => {
        for (const callback of listeners.values())
          callbacks.get(callback)?.({
            event: "terminal:snapshot",
            id: callback,
            payload: structuredClone(data),
          });
      };
      platform.testPublish = publish;
      platform.__TAURI_EVENT_PLUGIN_INTERNALS__ = {
        unregisterListener: (_event: string, id: number) =>
          listeners.delete(id),
      };
      platform.__TAURI_INTERNALS__ = {
        metadata: { currentWindow: { label: "main" } },
        transformCallback: (callback: (event: unknown) => void) => {
          const id = next++;
          callbacks.set(id, callback);
          return id;
        },
        invoke: async (name: string, args: Record<string, unknown>) => {
          if (name.startsWith("plugin:window|")) {
            platform.testWindowCalls.push(name);
            if (name.endsWith("toggle_maximize")) maximized = !maximized;
            return name.endsWith("is_maximized") ? maximized : undefined;
          }

          if (name === "plugin:event|listen") {
            const id = next++;
            listeners.set(id, args.handler as number);
            return id;
          }
          if (name === "plugin:event|unlisten") {
            listeners.delete(args.eventId as number);
            return;
          }
          if (
            (name === "terminal_start" || name === "terminal_snapshot") &&
            offline
          )
            throw "Market gateway unavailable";
          if (name === "terminal_start" || name === "terminal_snapshot")
            return structuredClone(snapshot);
          if (name === "terminal_browser") {
            if (platform.testFailWrite) throw "SQLite write failed";
            const mutation =
              args.mutation as import("../src/browser-model").BrowserMutation;
            const browser = structuredClone(
              snapshot.preferences.workspace!.browser!,
            );
            const key =
              mutation.type === "market" || mutation.type === "market_preset"
                ? mutation.market === "MY"
                  ? "my"
                  : mutation.market === "US"
                    ? "us"
                    : "crypto"
                : "options";
            if (mutation.type === "market")
              browser[key as "my" | "us" | "crypto"] = mutation.settings;
            else if (mutation.type === "options")
              browser.options = mutation.settings;
            else {
              const action = mutation.action,
                presets = (
                  key === "options"
                    ? browser.options_presets
                    : browser.market_presets
                ) as { name: string; settings: any }[];
              if (action.type === "save") {
                if (
                  presets.length >= 20 ||
                  presets.some((p) => p.name === action.name)
                )
                  throw "Invalid preset";
                presets.push({
                  name: action.name,
                  settings: structuredClone(
                    action.duplicate_from
                      ? presets.find((p) => p.name === action.duplicate_from)!
                          .settings
                      : browser[key],
                  ),
                });
              }
              if (action.type === "rename")
                presets.find((p) => p.name === action.name)!.name =
                  action.new_name;
              if (action.type === "delete")
                presets.splice(
                  presets.findIndex((p) => p.name === action.name),
                  1,
                );
              if (action.type === "apply")
                Object.assign(
                  browser[key],
                  structuredClone(
                    presets.find((p) => p.name === action.name)!.settings,
                  ),
                );
              if (action.type === "reset")
                Object.assign(
                  browser[key],
                  key === "options"
                    ? {
                        columns: [
                          "bid",
                          "ask",
                          "last",
                          "implied_volatility",
                          "open_interest",
                          "volume",
                        ],
                        side: "both",
                        moneyness: "all",
                        min_volume: null,
                        min_open_interest: null,
                        search: "",
                        strike_window: "all",
                      }
                    : {
                        columns: [
                          "instrument",
                          "price",
                          "change_percent",
                          "market_cap",
                          "volume",
                          "sector",
                        ],
                        sort: "market_cap",
                        descending: true,
                        sector: null,
                        min_cap: null,
                        min_change: null,
                        min_volume: null,
                        filter: "",
                      },
                );
            }
            snapshot.preferences.workspace!.browser = browser;
            snapshot.preferences.version = 7;
            snapshot.revision++;
            snapshot.message = "Browser settings saved";
            publish(snapshot);
            return structuredClone(browser);
          }
          if (name === "terminal_chart") {
            if (platform.testFailWrite) throw "SQLite write failed";
            const mutation = args.mutation as {
              type: string;
              layout: string;
              panel: string;
              instrument: string;
              settings: import("../src/chart-model").ChartSettings;
              drawings: import("../src/chart-model").Drawing[];
            };
            const panel = snapshot.preferences.workspace?.layouts.find(
              (l) => l.name === mutation.layout,
            )?.panels[mutation.panel];
            if (!panel) throw "Chart panel no longer exists";
            if (mutation.type === "settings") panel.chart = mutation.settings;
            else
              panel.drawings = {
                ...panel.drawings,
                [mutation.instrument]: mutation.drawings,
              };
            snapshot.revision++;
            snapshot.message = "Chart saved";
            publish(snapshot);
            return;
          }
          if (name === "terminal_watchlist_preview") {
            const list = snapshot.watchlists?.find((l) => l.id === args.id);
            if (!list) throw "Missing list";
            const symbols: string[] = [],
              duplicates: string[] = [],
              invalid: string[] = [];
            for (const token of String(args.text)
              .split(/[\n,;]/)
              .map((s) => s.trim())
              .filter(Boolean)) {
              const valid =
                /^(?:coingecko:[a-z0-9-]+|(?:yahoo:)?[A-Za-z0-9.^=_-]+)$/.test(
                  token,
                );
              if (!valid) {
                invalid.push(token);
                continue;
              }
              const id = token.startsWith("coingecko:")
                ? token
                : token.startsWith("yahoo:")
                  ? token
                  : `yahoo:${token.toUpperCase()}`;
              if (list.symbols.includes(id) || symbols.includes(id))
                duplicates.push(id);
              else symbols.push(id);
            }
            return { symbols, duplicates, invalid, error: null };
          }
          if (name === "terminal_watchlist") {
            if (platform.testFailWrite) throw "SQLite write denied";
            const m =
              args.mutation as import("../src/bridge").WatchlistMutation;
            let lists = structuredClone(snapshot.watchlists ?? []),
              active = snapshot.preferences.active_watchlist_id ?? "main";
            const list =
              "id" in m ? lists.find((l) => l.id === m.id) : undefined;
            switch (m.type) {
              case "create":
                if (
                  lists.some(
                    (l) => l.name.toLowerCase() === m.name.trim().toLowerCase(),
                  )
                )
                  throw "Duplicate name";
                lists.push({
                  id: m.id,
                  name: m.name.trim(),
                  symbols:
                    lists.find((l) => l.id === m.duplicate_from)?.symbols ?? [],
                });
                active = m.id;
                break;
              case "rename":
                if (list) list.name = m.name;
                break;
              case "delete":
                if (lists.length === 1) throw "Keep one list";
                lists = lists.filter((l) => l.id !== m.id);
                if (active === m.id) active = lists[0].id;
                break;
              case "order":
                lists = m.ids.map((id) => lists.find((l) => l.id === id)!);
                break;
              case "activate":
                active = m.id;
                break;
              case "add":
                if (list)
                  for (const id of m.symbols) {
                    if (!list.symbols.includes(id)) list.symbols.push(id);
                    snapshot.quotes[id] = quote(
                      id,
                    ) as Snapshot["quotes"][string];
                  }
                break;
              case "remove":
                if (list)
                  list.symbols = list.symbols.filter((id) => id !== m.symbol);
                break;
              case "reorder":
                if (list) list.symbols = m.symbols;
                break;
            }
            snapshot.watchlists = lists;
            snapshot.preferences.active_watchlist_id = active;
            snapshot.watchlist = {
              symbols: lists.find((l) => l.id === active)?.symbols ?? [],
            };
            snapshot.message = "Watchlist saved";
            snapshot.revision++;
            publish(snapshot);
            return;
          }
          if (name === "terminal_subscribe") {
            const resource = await (
              platform.__TAURI_INTERNALS__ as {
                invoke: (
                  name: string,
                  args: Record<string, unknown>,
                ) => Promise<unknown>;
              }
            ).invoke("terminal_resource", { request: args.request });
            const owner = args.owner as string;
            const update = {
              owner,
              generation: ++resourceGeneration,
              revision: resourceGeneration,
              request: args.request,
              payload: resource,
              loading: false,
              error: null,
            };
            subscriptions[owner] = update;
            return structuredClone(update);
          }
          if (name === "terminal_unsubscribe") {
            const owner = args.owner as string;
            if (subscriptions[owner]?.generation === args.generation)
              delete subscriptions[owner];
            return;
          }
          if (name === "terminal_resource_recover")
            return structuredClone(subscriptions);
          if (name === "terminal_resource") {
            const request = args.request as { type: string };
            if (request.type === "history")
              return { type: "history", market: snapshot.candles };
            if (request.type === "quote") {
              const result = quote(
                (args.request as { instrument: string }).instrument,
              );
              if (platform.testNoQuote) result.data!.price = null;
              return { type: "quote", market: result };
            }
            if (request.type === "capabilities")
              return {
                type: "capabilities",
                market: {
                  service: "market-data",
                  features: {
                    portfolio: full,
                    research: false,
                    traces: false,
                    backtesting: false,
                  },
                  widgets: ["crypto", "news"],
                  coverage: "Fixture coverage",
                },
              };
            if (request.type === "crypto")
              return {
                type: "crypto",
                market: market([
                  {
                    instrument: {
                      ...instrument("coingecko:bitcoin"),
                      symbol: "BTC",
                      asset_class: "crypto",
                    },
                    price: "61000.1234",
                    change_percent: 2.1,
                    market_cap: 1200000000000,
                    sector: null,
                    volume: null,
                    sparkline: [1, 2, 3],
                  },
                ]),
              };
            if (request.type === "screener" && rich) {
              const query = args.request as { market: string; offset: number };
              return {
                type: "screener",
                market: market(
                  Array.from(
                    { length: query.offset === 0 ? 100 : 2 },
                    (_, i) => ({
                      instrument: {
                        ...instrument(
                          `yahoo:${query.market}${query.offset + i}`,
                        ),
                        name: `Fixture stock ${query.offset + i}`,
                      },
                      price: "12.34",
                      change_percent: 2,
                      market_cap: 1000000,
                      sector: "Technology",
                      volume: 5000,
                      currency: query.market === "MY" ? "MYR" : "USD",
                      sparkline: [],
                    }),
                  ),
                ),
              };
            }
            if (request.type === "macro" && rich)
              return {
                type: "macro",
                market: market([
                  {
                    name: "Gold proxy",
                    kind: "ETF proxy",
                    price: "250.25",
                    unit: null,
                    source: "Fixture quotes",
                    observed_at: "2026-10-15T12:00:00Z",
                    instrument: instrument("yahoo:GLD"),
                    change_percent: "-0.65",
                    currency: "USD",
                  },
                  {
                    name: "Oil proxy",
                    kind: "ETF proxy",
                    price: "80.12",
                    unit: null,
                    source: "Fixture quotes",
                    observed_at: "2026-10-15T12:00:00Z",
                    instrument: instrument("yahoo:USO"),
                    change_percent: "1.25",
                    currency: "USD",
                  },
                  {
                    name: "Treasury 2Y",
                    kind: "yield",
                    price: "4.25",
                    unit: "percent",
                    source: "Fixture official rates",
                    observed_at: "2026-10-14",
                    instrument: null,
                    change_percent: null,
                    currency: null,
                  },
                  {
                    name: "Malaysia OPR",
                    kind: "policy rate",
                    price: "3.0",
                    unit: "percent",
                    source: "Fixture policy rate",
                    observed_at: "2026-10-01",
                    instrument: null,
                    change_percent: null,
                    currency: null,
                  },
                ]),
              };
            if (request.type === "options") {
              const optionRequest = args.request as {
                instrument: string;
                expiry: string | null;
              };
              const prefix =
                optionRequest.instrument === "yahoo:5326.KL" ? "" : "AAPL-";
              if (
                platform.testDelayOptions &&
                optionRequest.expiry === "2026-11-20"
              ) {
                await new Promise((resolve) =>
                  setTimeout(resolve, platform.testDelayOptions),
                );
                platform.testFinishedOldOptions = true;
              }
              if (platform.testOptionsState === "unavailable")
                return {
                  type: "options",
                  market: {
                    ...market(null),
                    status: "unavailable",
                    error: "No listed options",
                  },
                };
              const contract = (name: string, strike: string | null) => ({
                contract: prefix + name,
                strike,
                bid: "1.00",
                ask: "1.20",
                last: "1.10",
                volume: "20",
                open_interest: "100",
                implied_volatility: "0.25",
                itm: name === "CALL-A",
                observed_at: "2026-10-14T16:00:00Z",
              });
              return {
                type: "options",
                market: market({
                  calls:
                    platform.testOptionsState === "empty"
                      ? []
                      : [
                          contract("CALL-A", "100.00"),
                          contract("CALL-B", "100"),
                        ],
                  puts:
                    platform.testOptionsState === "empty"
                      ? []
                      : [
                          contract("PUT-A", "100.000"),
                          contract("PUT-MISSING", null),
                        ],
                  expiries:
                    platform.testOptionsState === "no_expiries"
                      ? []
                      : ["2026-10-16", "2026-11-20"],
                  expiry:
                    (args.request as { expiry?: string }).expiry ||
                    "2026-10-16",
                }),
              };
            }
            if (request.type === "calendar")
              return {
                type: "calendar",
                market: market([
                  {
                    title: "Verified fixture event",
                    country: "USD",
                    date: new Date().toISOString(),
                    impact: "High",
                    forecast: "2.0%",
                    previous: "1.9%",
                    actual: null,
                    actual_status: "Official evidence unavailable",
                  },
                  {
                    title: "Undated fixture event",
                    country: "MY",
                    date: null,
                    impact: null,
                    forecast: null,
                    previous: null,
                    actual: null,
                    actual_status: "Unavailable",
                  },
                ]),
              };
            if (request.type === "news")
              return {
                type: "news",
                market: market([
                  {
                    title: "Verified fixture headline",
                    url: "https://example.com/article",
                    publisher: "Fixture publisher",
                    published_at: "2026-10-14",
                  },
                ]),
              };
            return { type: request.type, market: market([]) };
          }
          if (name === "terminal_service") {
            const request = args.request as {
              type: string;
              id: string;
              revision: number;
              portfolio: Portfolio;
              trade: Doc["transactions"][number];
              data: string;
              mode: string;
              preview_hash: string;
              expected_revision: number;
            };
            if (request.type === "portfolio_list")
              return {
                portfolios: [...documents.values()].map((doc) => ({
                  id: doc.id,
                  name: doc.portfolio.name,
                  currency: doc.portfolio.currency,
                })),
              };
            if (request.type === "portfolio_create") {
              const doc: Doc = {
                id: `portfolio-${++documentCounter}`,
                revision: 1,
                portfolio: request.portfolio,
                transactions: [],
              };
              documents.set(doc.id, doc);
              return structuredClone(doc);
            }
            const doc = documents.get(request.id);
            if (request.type === "import_preview")
              return {
                preview_hash: "a".repeat(64),
                expected_revision: doc?.revision ?? 0,
                transaction_count: 0,
                document: {
                  id: "preview",
                  revision: 0,
                  portfolio: JSON.parse(request.data).portfolio,
                  transactions: [],
                },
              };
            if (request.type === "import_commit") {
              if (
                request.preview_hash !== "a".repeat(64) ||
                request.expected_revision !== (doc?.revision ?? 0)
              )
                throw "HTTP status 409";
              const imported: Doc = {
                id: `portfolio-${++documentCounter}`,
                revision: 1,
                portfolio: JSON.parse(request.data).portfolio,
                transactions: [],
              };
              documents.set(imported.id, imported);
              return structuredClone(imported);
            }
            if (!doc) throw "Portfolio not found";
            if (request.type === "portfolio_load") return structuredClone(doc);
            if (request.type === "portfolio_summary")
              return {
                revision: doc.revision,
                market_value: "0",
                cost_basis: "0",
                unrealized_gain: "0",
                currency: doc.portfolio.currency,
                positions: doc.portfolio.positions,
                errors: [],
              };
            if (request.revision !== doc.revision) throw "HTTP status 409";
            if (request.type === "portfolio_save")
              doc.portfolio = request.portfolio;
            if (
              request.type === "trade" &&
              !doc.transactions.some((trade) => trade.id === request.trade.id)
            )
              doc.transactions.push(request.trade);
            if (request.type === "portfolio_delete") {
              documents.delete(request.id);
              return { deleted: request.id };
            }
            doc.revision++;
            return structuredClone(doc);
          }
          if (name !== "terminal_action")
            throw new Error(`Unexpected IPC ${name}`);
          const action = args.action as {
            type: string;
            symbol?: string;
            range?: Snapshot["preferences"]["range"];
            text?: string;
            workspace?: Snapshot["preferences"]["workspace"];
            view?: import("../src/bridge").DesktopViewSettings;
          };
          if (action.type === "view" && snapshot.preferences.workspace) {
            snapshot.preferences.workspace.desktop = action.view;
            snapshot.message = "View settings saved";
          }
          if (action.type === "select")
            snapshot.preferences.selected = action.symbol ?? null;
          if (action.type === "set_range" && action.range)
            snapshot.preferences.range = action.range;
          if (action.type === "save") snapshot.message = "UI state saved";
          if (action.type === "workspace") {
            if (action.workspace)
              action.workspace.browser =
                snapshot.preferences.workspace?.browser;
            snapshot.preferences.workspace = action.workspace;
            snapshot.message = "Workspace saved";
          }
          if (action.type === "command") {
            const text = action.text ?? "";
            if (text.includes("invalid")) throw "Invalid instrument";
            snapshot.preferences.history.push(text);
            const [command, raw] = text.trim().split(/\s+/);
            const id = raw?.includes(":") ? raw : `yahoo:${raw?.toUpperCase()}`;
            if (
              command === ":add" &&
              !snapshot.watchlist.symbols.includes(id)
            ) {
              snapshot.watchlist.symbols.push(id);
              snapshot.quotes[id] = quote(id) as Snapshot["quotes"][string];
            }
            if (command === ":del")
              snapshot.watchlist.symbols = snapshot.watchlist.symbols.filter(
                (item) => item !== id,
              );
            snapshot.message =
              command === ":w" ? "UI state saved" : "Watchlist saved";
          }
          const activeList = snapshot.watchlists?.find(
            (l) =>
              l.id === (snapshot.preferences.active_watchlist_id ?? "main"),
          );
          if (activeList) activeList.symbols = [...snapshot.watchlist.symbols];
          snapshot.revision++;
          publish(snapshot);
        },
      };
    },
    {
      initialOffline: offline,
      live,
      full,
      rich,
      workspace: {
        ...defaultWorkspace(),
        browser: defaultBrowser(),
        compact: false,
        desktop: {
          ...defaultWorkspace().desktop!,
          ticker_mode: "watchlist" as const,
        },
      },
    },
  );
}
test("native snapshots render the desktop, chart and provenance", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  await expect(page.locator(".app-title")).toBeVisible();
  await expect(
    page.getByRole("img", { name: "OHLC candlestick chart" }),
  ).toBeVisible();
  await page.locator(".history .data-info > summary").click();
  await expect(
    page.getByText("Synthetic data; not market prices"),
  ).toBeVisible();
  await page.getByLabel("Open quote inspector").click();
  await expect(page.locator(".inspector-price")).toContainText("123.45");
  await page.getByLabel("Open command prompt").click();
  await expect(
    page.getByLabel("Command prompt", { exact: true }),
  ).toBeInViewport();
  await page.screenshot({ path: "test-results/desktop.png", fullPage: true });
});
test("selection, range, watchlist edits, command history and save use typed IPC", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "AAPL Synthetic" }).click();
  await expect(page.locator(".market-heading h2")).toContainText("AAPL");
  await page.getByRole("button", { name: "1D", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "1D", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Add symbol").fill("MSFT");
  await page.getByLabel("Add to watchlist").click();
  await expect(
    page.getByRole("button", { name: "MSFT Synthetic" }),
  ).toBeVisible();
  await page.getByLabel("Remove MSFT").click();
  await expect(
    page.getByRole("button", { name: "MSFT Synthetic" }),
  ).toHaveCount(0);
  await page.getByLabel("Open command prompt").click();
  const prompt = page.getByLabel("Command prompt", { exact: true });
  await prompt.fill(":w");
  await prompt.press("Enter");
  await expect(page.getByRole("status")).toHaveText("UI state saved");
  await page.getByLabel("Open command prompt").click();
  await prompt.press("ArrowUp");
  await expect(prompt).toHaveValue(":w");
  await prompt.press("Escape");
  await expect(prompt).toHaveValue("");
  await page.getByRole("button", { name: "Save workspace" }).click();
  await expect(page.getByRole("status")).toHaveText("UI state saved");
});
test("keyboard cursor remains separate from selection and IPC errors are visible", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  await expect(page.locator(".market-heading h2")).toContainText("5326.KL");
  await page.keyboard.press("ArrowDown");
  await expect(page.locator(".market-heading h2")).toContainText("5326.KL");
  await page.keyboard.press("Enter");
  await expect(page.locator(".market-heading h2")).toContainText("AAPL");
  await page.keyboard.press(":");
  const prompt = page.getByLabel("Command prompt", { exact: true });
  await expect(prompt).toBeFocused();
  await prompt.fill(":add invalid");
  await prompt.press("Enter");
  await expect(page.getByRole("alert")).toContainText("Invalid instrument");
});
test("plain browser reports that the desktop runtime is required", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "The engine could not start" }),
  ).toBeVisible();
  await expect(page.locator(".startup")).toContainText("npm run desktop:dev");
});
test("failed live startup can reconnect without restarting the desktop", async ({
  page,
}) => {
  await nativeFixture(page, true);
  await page.goto("/");
  await expect(page.locator(".startup")).toContainText(
    "Market gateway unavailable",
  );
  await expect(page.locator(".connection-state")).toContainText("CONNECTING");
  await page.evaluate(() =>
    (window as unknown as { testRecover: () => void }).testRecover(),
  );
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(
    page.getByRole("img", { name: "OHLC candlestick chart" }),
  ).toBeVisible();
  await expect(page.locator(".startup")).toHaveCount(0);
});
test("empty watchlists render and obsolete revisions cannot overwrite current state", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  await expect(page.locator(".market-heading h2")).toContainText("5326.KL");
  await page.evaluate(async () => {
    const native = window as unknown as {
      testPublish: (s: Snapshot) => void;
      __TAURI_INTERNALS__: {
        invoke: (name: string, args: unknown) => Promise<Snapshot>;
      };
    };
    const old = await native.__TAURI_INTERNALS__.invoke(
      "terminal_snapshot",
      {},
    );
    const current = structuredClone(old);
    current.revision = 100;
    current.watchlist.symbols = [];
    current.watchlists = [{ id: "main", name: "My Watchlist", symbols: [] }];
    current.preferences.selected = null;
    current.candles.data = null;
    native.testPublish(current);
    native.testPublish(old);
  });
  await expect(page.locator(".market-heading h2")).toContainText(
    "No instrument selected",
  );
  await expect(page.locator(".watchlist .empty")).toContainText(
    "Your watchlist is empty",
  );
});
test("workspace tabs, compact mode, ticker selection and add-symbol toolbar work", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  const navigation = page.getByRole("navigation", { name: "Workspace views" });
  await navigation
    .getByRole("button", { name: "markets", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Market browser" }),
  ).toBeVisible();
  await expect(page.locator(".watchlist")).toBeHidden();
  await navigation
    .getByRole("button", { name: "watchlist", exact: true })
    .click();
  await expect(page.locator(".history")).toBeHidden();
  await page.getByLabel("Open quote inspector").click();
  await expect(
    page.getByRole("region", { name: "Instrument details" }),
  ).toContainText("Synthetic");
  await workspaceTools(page);
  const compact = page.getByRole("button", { name: "COMPACT", exact: true });
  await compact.focus();
  await compact.press("Enter");
  await expect(compact).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "+ SYMBOL", exact: true }).click();
  await expect(page.getByLabel("Add symbol")).toBeFocused();
  await expect(
    navigation.getByRole("button", { name: "dashboard", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page
    .locator(".ticker-strip")
    .getByRole("button", { name: /^AAPL/ })
    .click();
  await expect(page.locator(".market-heading h2")).toContainText("AAPL");
});

test("docking supports keyboard resizing, grouping, maximize, named saves and recovery", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  const divider = page.getByRole("separator", { name: "Resize panels" });
  await expect(divider).toHaveAttribute("aria-valuenow", "19");
  await divider.focus();
  await divider.press("ArrowLeft");
  await expect(divider).toHaveAttribute("aria-valuenow", "15");
  await page.getByLabel("Maximize chart").click();
  await expect(page.locator(".watchlist")).toBeHidden();
  await page.getByLabel("Maximize chart").click();
  await expect(page.locator(".watchlist")).toBeVisible();
  await workspaceTools(page);
  await page.locator(".layout-menu summary").click();
  await page.getByLabel("Layout name").fill("My desk");
  await page.getByRole("button", { name: "Save as", exact: true }).click();
  await expect(page.getByLabel("Saved layout")).toHaveValue("My desk");
  await page.getByLabel("Move watchlist", { exact: true }).click();
  await page
    .locator(".dock-menu[open]")
    .getByLabel("Destination group")
    .selectOption("chart-group");
  await page
    .locator(".dock-menu[open]")
    .getByRole("button", { name: "Apply", exact: true })
    .click();
  await expect(
    page.getByRole("separator", { name: "Resize panels" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("tab", { name: "WATCHLIST", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "CHART", exact: true }).click();
  await expect(
    page.getByRole("img", { name: "OHLC candlestick chart" }),
  ).toBeVisible();
  await page.keyboard.press("Control+k");
  await page.getByLabel("Search commands").fill("settings");
  await page
    .getByRole("button", { name: "Open settings", exact: true })
    .click();
  await expect(page.getByLabel("Keymap JSON")).toBeVisible();
});

test("live screens use native resources, preserve crypto IDs and disclose missing backend services", async ({
  page,
}) => {
  await nativeFixture(page, false, true);
  await page.route("**/api/**", (route) => route.abort());
  await page.goto("/");
  const navigation = page.getByRole("navigation", { name: "Workspace views" });
  await navigation.getByText("More", { exact: true }).click();
  await navigation.getByRole("button", { name: "crypto", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "BTC", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "BTC", exact: true }).click();
  await expect(page.locator(".market-heading h2")).toContainText("bitcoin");
  await navigation.getByText("More", { exact: true }).click();
  await navigation.getByRole("button", { name: "news", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "Read original article ↗" }),
  ).toHaveAttribute("href", "https://example.com/article");
  await navigation
    .getByRole("button", { name: "portfolio", exact: true })
    .click();
  await expect(page.locator(".feature-screen")).toContainText(
    "does not provide portfolio",
  );
  await page.setViewportSize({ width: 960, height: 640 });
  await page.getByLabel("Open command prompt").click();
  await expect(
    page.getByLabel("Command prompt", { exact: true }),
  ).toBeInViewport();
});

test("indicator calculations have deterministic warm-up and missing values", async () => {
  const { sma, ema, rsi, calculate, defaultChart, anchorToPoint } =
    await import("../src/chart-model");
  expect(sma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  expect(ema([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  expect(ema([null, 2, 4, 6, 8], 3)).toEqual([null, null, null, 4, 6]);
  expect(rsi([5, 5, 5, 5], 2)).toEqual([null, null, 50, 50]);
  expect(rsi([1, 2, 3, 4], 2)).toEqual([null, null, 100, 100]);
  expect(rsi([4, 3, 2, 1], 2)).toEqual([null, null, 0, 0]);
  const settings = defaultChart();
  settings.bollinger.periods = [2];
  settings.macd.periods = [2, 3, 2];
  const candles = [1, 2, 3, 4, 5].map((close, time) => ({
    time,
    open: close,
    high: close,
    low: close,
    close,
    volume: null,
  }));
  const values = calculate(candles, settings);
  expect(values.bollinger[0]).toEqual([null, 0.5, 1.5, 2.5, 3.5]);
  expect(values.bollinger[2]).toEqual([null, 2.5, 3.5, 4.5, 5.5]);
  expect(values.macd).toEqual([null, null, 0.5, 0.5, 0.5]);
  expect(values.signal).toEqual([null, null, null, 0.5, 0.5]);
  expect(values.histogram).toEqual([null, null, null, 0, 0]);
  expect(calculate([], settings).rsi).toEqual([]);
  expect(
    anchorToPoint({ time: 150, price: 25 }, [100, 200], 10, 20, (p) => 100 - p),
  ).toEqual([30, 75]);
  expect(candles.every((c) => c.volume === null)).toBe(true);
  expect(ema([1, 3, 2, 6, 5], 3)).toEqual([null, null, 2, 4, 4.5]);
  const wilder = rsi(
    [
      44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.1, 45.42, 45.84, 46.08,
      45.89, 46.03, 45.61, 46.28, 46.28, 46.0,
    ],
    14,
  );
  expect(wilder[14]).toBeCloseTo(70.464135, 5);
  expect(wilder[15]).toBeCloseTo(66.249619, 5);
});

test("chart settings, indicator panes, drawing gestures and failed drafts", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  await page.getByLabel("Chart style").selectOption("line");
  await expect(
    page.getByRole("img", { name: "Closing-price line chart" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Indicators", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Indicator settings" });
  for (const name of ["SMA", "RSI", "MACD"])
    await dialog
      .locator("fieldset")
      .filter({
        has: page
          .locator("legend")
          .filter({ hasText: new RegExp("^" + name + "$") }),
      })
      .getByRole("checkbox")
      .check();
  await dialog.getByLabel("MACD fast", { exact: true }).fill("26");
  await expect(
    dialog.getByRole("button", { name: "Apply indicators" }),
  ).toBeDisabled();
  await dialog.getByLabel("MACD fast", { exact: true }).fill("12");
  await dialog.getByRole("button", { name: "Apply indicators" }).click();
  await expect(page.getByLabel("RSI indicator pane")).toHaveCount(1);
  await expect(page.getByLabel("MACD indicator pane")).toHaveCount(1);
  const chart = page.getByRole("img", { name: "Closing-price line chart" });
  await page.locator(".drawing-menu summary").click();
  await page.getByRole("button", { name: "Trend line", exact: true }).click();
  let box = await chart.boundingBox();
  if (!box) throw Error("No chart bounds");
  await page.mouse.move(box.x + 80, box.y + 50);
  await page.mouse.down();
  await page.mouse.move(box.x + 220, box.y + 90);
  await page.mouse.up();
  await expect(page.locator("[data-drawing-id]")).toHaveCount(1);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.locator("[data-drawing-id]")).toHaveCount(0);
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(page.locator("[data-drawing-id]")).toHaveCount(1);
  await page
    .getByRole("button", { name: "Clear drawings", exact: true })
    .click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.locator("[data-drawing-id]")).toHaveCount(1);
  await page.screenshot({
    path: "test-results/chart-analysis.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 960, height: 640 });
  await page.screenshot({
    path: "test-results/chart-analysis-small.png",
    fullPage: true,
  });
  await page.evaluate(() => {
    (window as unknown as { testFailWrite: boolean }).testFailWrite = true;
  });
  await page.getByLabel("Chart style").selectOption("candles");
  await expect(page.getByRole("alert")).toContainText("Unsaved chart draft");
  await page.evaluate(() => {
    (window as unknown as { testFailWrite: boolean }).testFailWrite = false;
  });
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByRole("img", { name: "OHLC candlestick chart" }),
  ).toBeVisible();
  await page.locator(".drawing-menu summary").click();
  await page.getByRole("button", { name: "Rectangle", exact: true }).click();
  box = await page
    .getByRole("img", { name: "OHLC candlestick chart" })
    .boundingBox();
  if (!box) throw Error("No chart bounds");
  await page.mouse.move(box.x + 65, box.y + 45);
  await page.mouse.down();
  await page.mouse.move(box.x + 130, box.y + 65);
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await expect(page.locator("[data-drawing-id]")).toHaveCount(1);
});

test("watchlist filtering and row actions preserve membership and selection", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  await page.getByLabel("Filter watchlist").fill("not-a-symbol");
  await expect(
    page.getByText("No instruments match this filter."),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Active instrument is outside these results; chart selection is unchanged.",
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear filter", exact: true }).click();
  await expect(page.locator(".watchlist tbody tr")).toHaveCount(2);
  await page.getByLabel("Actions for 5326.KL", { exact: true }).click();
  await expect(
    page.getByRole("group", { name: "Actions for 5326.KL" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Open Instrument", exact: true })
    .click();
  await expect(
    page.getByRole("group", { name: "Actions for 5326.KL" }),
  ).toHaveCount(0);
  await expect(page.locator(".watchlist tbody tr")).toHaveCount(2);
});

test("all drawing tools edit, delete, survive range changes and reopen", async ({
  page,
}) => {
  await nativeFixture(page);
  await page.goto("/");
  const chart = page.getByRole("img", { name: "OHLC candlestick chart" });
  for (const name of [
    "Horizontal level",
    "Rectangle",
    "Fibonacci retracement",
  ]) {
    await page.locator(".drawing-menu summary").click();
    await page.getByRole("button", { name, exact: true }).click();
    const box = await chart.boundingBox();
    if (!box) throw Error("No chart bounds");
    await page.mouse.move(box.x + 90, box.y + 55);
    await page.mouse.down();
    await page.mouse.move(box.x + 200, box.y + 100);
    await page.mouse.up();
  }
  await expect(page.locator("[data-drawing-id]")).toHaveCount(3);
  const handle = page.getByLabel("Edit fibonacci anchor 2");
  const handleBox = await handle.boundingBox();
  if (!handleBox) throw Error("No drawing handle");
  const before = await handle.getAttribute("cy");
  await page.mouse.move(handleBox.x + 5, handleBox.y + 5);
  await page.mouse.down();
  await page.mouse.move(handleBox.x + 5, handleBox.y + 30);
  await page.mouse.up();
  await expect(handle).not.toHaveAttribute("cy", before ?? "");
  await chart.press("Delete");
  await expect(page.locator("[data-drawing-id]")).toHaveCount(2);
  await page.getByRole("button", { name: "1M", exact: true }).click();
  await expect(page.locator("[data-drawing-id]")).toHaveCount(2);
  const nav = page.getByRole("navigation", { name: "Workspace views" });
  await nav.getByRole("button", { name: "markets", exact: true }).click();
  await nav.getByRole("button", { name: "dashboard", exact: true }).click();
  await expect(page.locator("[data-drawing-id]")).toHaveCount(2);
  await page.locator(".workspace-tools>summary").click();
  await page.getByLabel("Dashboard template").selectOption("Two Chart Desk");
  await expect(page.locator(".chart-editor")).toHaveCount(2);
  await expect(page.locator("[data-drawing-id]")).toHaveCount(0);
  if ((await page.locator(".workspace-tools").getAttribute("open")) === null)
    await page.locator(".workspace-tools>summary").click();
  await page.locator(".layout-menu>summary").click();
  await page.getByRole("button", { name: "Reset panels", exact: true }).click();
  await expect(page.locator(".chart-editor")).toHaveCount(1);
  await expect(page.locator(".watchlist")).toHaveCount(1);
});

async function browserSnapshot(page: Page) {
  return page.evaluate(async () => {
    const api = (
      window as unknown as {
        __TAURI_INTERNALS__: {
          invoke: (name: string, args?: unknown) => Promise<Snapshot>;
        };
      }
    ).__TAURI_INTERNALS__;
    return api.invoke("terminal_snapshot");
  });
}
async function navigateScreen(page: Page, name: string) {
  await page
    .getByRole("navigation", { name: "Workspace views" })
    .getByRole("button", { name, exact: true })
    .click();
}

test("reference geometry uses prepared API cards and releases overview subscriptions", async ({
  page,
}) => {
  await nativeFixture(page, false, true, false, true);
  await page.clock.setFixedTime(new Date("2026-10-16T06:00:00Z"));
  await page.goto("/");
  await expect(
    page.locator(".title-bar").getByLabel("Save workspace", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".dashboard-toolbar")).toHaveCount(0);
  await navigateScreen(page, "watchlist");
  await expect(page.locator(".desk-heading")).toBeHidden();
  await expect
    .poll(async () =>
      Math.round((await page.locator(".watchlist-list-tabs").boundingBox())!.y),
    )
    .toBe(32);
  await page.locator(".watchlist-sort .watchlist-menu > summary").click();
  await page.getByRole("checkbox", { name: "Name", exact: true }).check();
  await page.getByRole("checkbox", { name: "Volume", exact: true }).check();
  await page.locator(".watchlist-sort .watchlist-menu > summary").click();
  await expect(
    page.locator('.watchlist-full th[data-column="name"]'),
  ).toHaveCSS("width", "180px");
  await page.screenshot({
    path: "test-results/precise-watchlist.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 960, height: 640 });
  await page.screenshot({
    path: "test-results/precise-watchlist-small.png",
    fullPage: true,
  });
  const original = (await browserSnapshot(page)).watchlist;
  await navigateScreen(page, "markets");
  await expect
    .poll(async () =>
      Math.round(
        (await page
          .getByRole("tablist", { name: "Market universe" })
          .boundingBox())!.y,
      ),
    )
    .toBe(32);
  await expect(
    page.getByRole("region", { name: "Macro overview" }),
  ).toContainText("ETF proxies, not spot");
  await expect(
    page.getByRole("region", { name: "Macro overview" }),
  ).toContainText("4.25%");
  await page
    .getByRole("button", { name: "Open Gold proxy from overview", exact: true })
    .click();
  await expect
    .poll(async () => (await browserSnapshot(page)).preferences.selected)
    .toBe("yahoo:GLD");
  expect((await browserSnapshot(page)).watchlist).toEqual(original);
  await page
    .getByRole("button", { name: "Open BTC from overview", exact: true })
    .click();
  await expect
    .poll(async () => (await browserSnapshot(page)).preferences.selected)
    .toBe("coingecko:bitcoin");
  expect((await browserSnapshot(page)).watchlist).toEqual(original);
  await page.screenshot({
    path: "test-results/precise-overview-small.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({
    path: "test-results/precise-overview.png",
    fullPage: true,
  });
  await navigateScreen(page, "settings");
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          Object.keys(
            (
              window as unknown as {
                testSubscriptions: () => Record<string, unknown>;
              }
            ).testSubscriptions(),
          ).filter((id) => id.startsWith("overview:")).length,
      ),
    )
    .toBe(0);
});

test("saved screeners share native market settings without changing membership", async ({
  page,
}) => {
  await nativeFixture(page, false, true, false, true);
  await page.clock.setFixedTime(new Date("2026-10-15T14:00:00Z"));
  await page.goto("/");
  await page.locator(".workspace-tools > summary").click();
  await page.getByRole("button", { name: "COMPACT", exact: true }).click();
  await page.locator(".workspace-tools > summary").click();
  const before = await browserSnapshot(page);
  const nav = page.getByRole("navigation", { name: "Workspace views" });
  await nav.getByText("More", { exact: true }).click();
  await navigateScreen(page, "screener");
  await expect(
    page.getByRole("heading", { name: "Screeners", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".screener-presets")).toHaveCSS("width", "220px");
  await expect(page.locator(".market-custom-table tbody tr")).toHaveCount(100);
  await page.getByLabel("Market sector", { exact: true }).fill("Technology");
  await page.getByRole("button", { name: "Apply sector", exact: true }).click();
  await page.locator(".browser-presets summary").click();
  await page
    .getByLabel("Preset name", { exact: true })
    .fill("Technology screen");
  await page
    .getByRole("button", { name: "Save new preset", exact: true })
    .click();
  await expect(
    page
      .getByRole("complementary", { name: "Saved screeners" })
      .getByRole("button", { name: "Technology screen", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Preset name", { exact: true }).press("Escape");
  await expect(page.locator(".browser-presets")).not.toHaveAttribute(
    "open",
    "",
  );
  await expect(page.locator(".browser-presets summary")).toBeFocused();
  await page.getByRole("tab", { name: "US Equities", exact: true }).click();
  await expect(page.getByLabel("Market sector", { exact: true })).toHaveValue(
    "",
  );
  await page
    .getByRole("complementary", { name: "Saved screeners" })
    .getByRole("button", { name: "Technology screen", exact: true })
    .click();
  await expect(page.getByLabel("Market sector", { exact: true })).toHaveValue(
    "Technology",
  );
  await expect
    .poll(
      async () =>
        (await browserSnapshot(page)).preferences.workspace?.browser?.us.sector,
    )
    .toBe("Technology");
  expect((await browserSnapshot(page)).watchlist).toEqual(before.watchlist);
  await page.screenshot({
    path: "test-results/screener-migration.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 960, height: 640 });
  await expect(page.locator(".screener-workspace")).toHaveCSS(
    "grid-template-columns",
    /px$/,
  );
  await page.screenshot({
    path: "test-results/screener-migration-small.png",
    fullPage: true,
  });
  await navigateScreen(page, "settings");
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          Object.values(
            (
              window as unknown as {
                testSubscriptions: () => Record<
                  string,
                  { request: { type: string } }
                >;
              }
            ).testSubscriptions(),
          ).filter((entry) => entry.request.type === "screener").length,
      ),
    )
    .toBe(0);
  await navigateScreen(page, "markets");
  await page.getByRole("tab", { name: "US Equities", exact: true }).click();
  await expect(page.getByLabel("Market sector", { exact: true })).toHaveValue(
    "Technology",
  );
});

test("migration shell menus dismiss accessibly and contextual quotes preserve stale state", async ({
  page,
}) => {
  await nativeFixture(page, false, true);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Chart", exact: true, level: 1 }),
  ).toBeVisible();
  await page.locator(".workspace-tools > summary").click();
  await page.getByLabel("Saved layout", { exact: true }).focus();
  await page.keyboard.press("Escape");
  await expect(page.locator(".workspace-tools")).not.toHaveAttribute(
    "open",
    "",
  );
  await expect(page.locator(".workspace-tools > summary")).toBeFocused();
  await page.evaluate(async () => {
    const native = window as unknown as {
      testPublish: (s: Snapshot) => void;
      __TAURI_INTERNALS__: { invoke: (name: string) => Promise<Snapshot> };
    };
    const snapshot =
      await native.__TAURI_INTERNALS__.invoke("terminal_snapshot");
    snapshot.revision += 100;
    const id = snapshot.preferences.selected!;
    snapshot.quotes[id].stale = true;
    snapshot.quotes[id].status = "stale";
    native.testPublish(snapshot);
  });
  await expect(page.getByLabel("Active instrument quote")).toContainText(
    "STALE",
  );
  await expect(page.getByLabel("Active instrument quote")).toContainText(
    "123.45",
  );
  await page.screenshot({
    path: "test-results/migration-stale.png",
    fullPage: true,
  });
  await page.getByLabel("Collapse navigation").click();
  // The stale fixture was published ahead of the native fixture's revision.
  // Re-publish its latest committed view with the same stale quote evidence.
  await page.evaluate(async () => {
    const native = window as unknown as {
      testPublish: (s: Snapshot) => void;
      __TAURI_INTERNALS__: { invoke: (name: string) => Promise<Snapshot> };
    };
    const snapshot =
      await native.__TAURI_INTERNALS__.invoke("terminal_snapshot");
    snapshot.revision += 100;
    const id = snapshot.preferences.selected!;
    snapshot.quotes[id].stale = true;
    snapshot.quotes[id].status = "stale";
    native.testPublish(snapshot);
  });
  await expect(page.locator(".navigation-rail")).toHaveCSS("width", "48px");
  await page.setViewportSize({ width: 960, height: 640 });
  await page.screenshot({
    path: "test-results/migration-stale-small.png",
    fullPage: true,
  });
  await page.getByLabel("Close window", { exact: true }).click();
  expect(
    await page.evaluate(
      () =>
        (window as unknown as { testWindowCalls: string[] }).testWindowCalls,
    ),
  ).toContain("plugin:window|close");
});

test("remaining workflow presentation remains reachable at both desktop sizes", async ({
  page,
}) => {
  await nativeFixture(page, false, true, true, true);
  await page.clock.setFixedTime(new Date("2026-10-15T14:00:00Z"));
  await page.goto("/");
  await page.locator(".workspace-tools > summary").click();
  await page.getByRole("button", { name: "COMPACT", exact: true }).click();
  await page.locator(".workspace-tools > summary").click();
  const nav = page.getByRole("navigation", { name: "Workspace views" });
  for (const screen of [
    "watchlist",
    "portfolio",
    "news",
    "crypto",
    "macro",
    "research",
    "backtesting",
    "traces",
    "settings",
  ]) {
    const button = nav.getByRole("button", { name: screen, exact: true });
    if (!(await button.isVisible()))
      await nav.getByText("More", { exact: true }).click();
    await button.click();
    await expect(page.locator(".workspace")).toHaveAttribute(
      "data-view",
      screen,
    );
    await expect(page.locator(".workspace-content")).toBeVisible();
    if (screen === "settings") {
      await expect(
        page.getByRole("navigation", { name: "Settings sections" }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: "Workspace", exact: true })
        .click();
      await expect(page.getByLabel("Settings saved layout")).toBeInViewport();
    }
    for (const [width, height] of [
      [1440, 1000],
      [960, 640],
    ]) {
      await page.setViewportSize({ width, height });
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= window.innerWidth,
          ),
        )
        .toBe(true);
      await page.screenshot({
        path: `test-results/migration-${screen}-${width}.png`,
        fullPage: true,
      });
    }
  }
});

test("option filters use exact signed decimals, preserve duplicates and bound nearest strikes", async () => {
  const { defaultOptions } = await import("../src/browser-model");
  const { filterChain, alignContracts } = await import("../src/options-model");
  const { normalizeDecimal, percent } = await import("../src/decimal");
  const contract = (id: string, strike: string | null): Contract => ({
    contract: id,
    strike,
    bid: null,
    ask: null,
    last: null,
    volume: "20",
    open_interest: "100",
    implied_volatility: ".25",
    itm: false,
  });
  expect(normalizeDecimal("-0.5000")).toBe("-0.5");
  expect(normalizeDecimal("+000.50")).toBe("0.5");
  expect(normalizeDecimal("-0.0")).toBe("0");
  expect(normalizeDecimal("bad")).toBeNull();
  expect(percent(".25")).toBe("25.00");
  const chain = {
    expiry: "2026-10-16",
    expiries: ["2026-10-16"],
    calls: [
      contract("negative", "-0.5"),
      contract("positive", ".5"),
      contract("duplicate", "0.5000"),
      contract("missing", null),
    ],
    puts: [contract("put", ".50")],
  };
  expect(alignContracts(chain)).toHaveLength(3);
  expect(alignContracts(chain)[0].strike).toBe("-0.5");
  const settings = defaultOptions();
  settings.side = "calls";
  expect(filterChain(chain, settings, null)[1].calls).toHaveLength(2);
  settings.min_volume = "21";
  expect(filterChain(chain, settings, null)).toHaveLength(0);
  settings.min_volume = null;
  settings.strike_window = "nearest21";
  const many = {
    ...chain,
    calls: Array.from({ length: 40 }, (_, i) =>
      contract(String(i), `9007199254740993.${String(i).padStart(2, "0")}`),
    ),
    puts: [],
  };
  const nearest = filterChain(many, settings, "9007199254740993.20");
  expect(nearest).toHaveLength(21);
  expect(nearest[0].strike).toBe("9007199254740993.1");
  expect(nearest[20].strike).toBe("9007199254740993.3");
  expect(filterChain(many, settings, null)).toHaveLength(40);
});

test("crypto sorting keeps precise prices and unavailable values last", async () => {
  const { sortAssets } = await import("../src/markets-browser");
  const rows = [
    { instrument: { id: "a", symbol: "A" }, price: "9007199254740993.02" },
    { instrument: { id: "b", symbol: "B" }, price: null },
    { instrument: { id: "c", symbol: "C" }, price: "9007199254740993.01" },
  ] as import("../src/resources").Asset[];
  expect(sortAssets(rows, "price", false).map((r) => r.instrument.id)).toEqual([
    "c",
    "a",
    "b",
  ]);
  expect(sortAssets(rows, "price", true).map((r) => r.instrument.id)).toEqual([
    "a",
    "c",
    "b",
  ]);
});

test("market filters, pagination, columns and identity-local presets persist independently", async ({
  page,
}) => {
  await nativeFixture(page, false, true, false, true);
  await page.goto("/");
  await navigateScreen(page, "markets");
  await page.getByRole("tab", { name: "MY Equities", exact: true }).click();
  await expect(page.locator(".market-custom-table tbody tr")).toHaveCount(100);
  await page
    .getByRole("button", { name: "Next market page", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await browserSnapshot(page)).preferences.workspace?.browser?.my.sort,
    )
    .toBe("market_cap");
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          Object.values(
            (
              window as unknown as {
                testSubscriptions: () => Record<
                  string,
                  { request: { type: string; offset?: number } }
                >;
              }
            ).testSubscriptions(),
          ).find((v) => v.request.type === "screener")?.request.offset,
      ),
    )
    .toBe(100);
  await page.getByRole("button", { name: "Losers", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Losers", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Market sector", { exact: true }).fill("Technology");
  await page
    .getByLabel("Minimum market cap", { exact: true })
    .fill("1000000.01");
  await page.getByLabel("Minimum change %", { exact: true }).fill("-2.5");
  await page.getByLabel("Minimum volume", { exact: true }).fill("500");
  await page.getByRole("button", { name: "Apply sector", exact: true }).click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          Object.values(
            (
              window as unknown as {
                testSubscriptions: () => Record<
                  string,
                  { request: { type: string } }
                >;
              }
            ).testSubscriptions(),
          ).find((v) => v.request.type === "screener")?.request,
      ),
    )
    .toMatchObject({
      offset: 0,
      descending: false,
      min_cap: "1000000.01",
      min_change: "-2.5",
      min_volume: "500",
      sector: "Technology",
    });
  await page.getByLabel("Filter loaded market results").fill("Fixture stock 7");
  await page.getByLabel("Filter loaded market results").press("Enter");
  await expect(page.locator(".market-custom-table tbody tr")).toHaveCount(11);
  await page
    .getByRole("button", { name: "Market columns", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Market columns" });
  await expect(
    dialog.getByRole("checkbox", { name: "Instrument", exact: true }),
  ).toBeDisabled();
  await dialog.getByRole("checkbox", { name: "Volume", exact: true }).uncheck();
  await dialog.getByRole("checkbox", { name: "Currency", exact: true }).check();
  await dialog
    .getByRole("button", { name: "Apply columns", exact: true })
    .click();
  await expect(page.locator(".market-custom-table thead")).not.toContainText(
    "Volume",
  );
  await page.locator(".browser-presets summary").click();
  await page.getByLabel("Preset name", { exact: true }).fill("Technology MY");
  await page
    .getByRole("button", { name: "Save new preset", exact: true })
    .click();
  await page
    .getByLabel("Market preset", { exact: true })
    .selectOption("Technology MY");
  await page.getByLabel("Preset name", { exact: true }).fill("Duplicate");
  await page
    .getByRole("button", { name: "Duplicate preset", exact: true })
    .click();
  await page
    .getByLabel("Market preset", { exact: true })
    .selectOption("Duplicate");
  await page.getByLabel("Preset name", { exact: true }).fill("Renamed");
  await page
    .getByRole("button", { name: "Rename preset", exact: true })
    .click();
  await page
    .getByLabel("Market preset", { exact: true })
    .selectOption("Renamed");
  await page
    .getByRole("button", { name: "Delete preset", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Confirm preset deletion", exact: true })
    .click();
  await expect(
    page.getByLabel("Market preset", { exact: true }).locator("option"),
  ).toHaveCount(2);
  await page.locator(".browser-presets summary").click();
  await page.screenshot({
    path: "test-results/markets-custom.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 960, height: 640 });
  await page.screenshot({
    path: "test-results/markets-custom-small.png",
    fullPage: true,
  });
  await page.getByRole("tab", { name: "US Equities", exact: true }).click();
  await expect(page.getByLabel("Market sector", { exact: true })).toHaveValue(
    "",
  );
  await page.getByRole("tab", { name: "MY Equities", exact: true }).click();
  await expect(page.getByLabel("Market sector", { exact: true })).toHaveValue(
    "Technology",
  );
  await expect(page.getByLabel("Filter loaded market results")).toHaveValue(
    "Fixture stock 7",
  );
});

test("failed display writes retain column drafts and recover through retry", async ({
  page,
}) => {
  await nativeFixture(page, false, true);
  await page.goto("/");
  await navigateScreen(page, "options");
  await page.evaluate(() => {
    (window as unknown as { testFailWrite: boolean }).testFailWrite = true;
  });
  await page
    .getByRole("button", { name: "Option columns", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Option columns" });
  await dialog.getByRole("checkbox", { name: "Volume", exact: true }).uncheck();
  await dialog
    .getByRole("button", { name: "Apply columns", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Unsaved display draft");
  expect(
    (await browserSnapshot(page)).preferences.workspace?.browser?.options
      .columns,
  ).toContain("volume");
  await page.evaluate(() => {
    (window as unknown as { testFailWrite: boolean }).testFailWrite = false;
  });
  await page
    .getByRole("button", { name: "Retry display save", exact: true })
    .click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(
    (await browserSnapshot(page)).preferences.workspace?.browser?.options
      .columns,
  ).not.toContain("volume");
});

test("option expiry navigation, filters, contract inspection and narrow drawer work", async ({
  page,
}) => {
  await nativeFixture(page, false, true);
  await page.goto("/");
  await navigateScreen(page, "options");
  await expect(
    page.getByRole("button", { name: "Call CALL-A IV % 1", exact: true }),
  ).toHaveText("25.00%");
  await page.getByLabel("Next option expiry", { exact: true }).click();
  await expect(page.getByLabel("Option expiry", { exact: true })).toHaveValue(
    "2026-11-20",
  );
  await page.getByLabel("Previous option expiry", { exact: true }).click();
  await expect(page.getByLabel("Option expiry", { exact: true })).toHaveValue(
    "2026-10-16",
  );
  await page
    .getByRole("button", { name: "Call CALL-A Bid 1", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("region", { name: "Selected option contract" }),
  ).toContainText("2026-10-14T16:00:00Z");
  await expect
    .poll(() =>
      page.evaluate(() =>
        Object.keys(
          (
            window as unknown as { testSubscriptions: () => object }
          ).testSubscriptions(),
        ).includes("options:history"),
      ),
    )
    .toBe(false);
  await page.screenshot({
    path: "test-results/options-contract.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 960, height: 640 });
  const drawer = page.getByRole("dialog", {
    name: "Option contract details",
    exact: true,
  });
  await expect(drawer).toBeVisible();
  await page.keyboard.press("Tab");
  expect(
    await drawer.evaluate((el) => el.contains(document.activeElement)),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/options-contract-small.png",
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await page
    .getByLabel("Option moneyness", { exact: true })
    .selectOption("itm");
  await expect(page.locator(".options-table tbody tr")).toHaveCount(1);
  await page
    .getByLabel("Option moneyness", { exact: true })
    .selectOption("all");
  await page.getByLabel("Option side", { exact: true }).selectOption("calls");
  await expect(page.locator(".options-table thead")).not.toContainText("Puts");
  await page.getByLabel("Option minimum volume", { exact: true }).fill("21");
  await page
    .getByRole("button", { name: "Apply option filters", exact: true })
    .click();
  await expect(
    page.getByText("No contracts match these filters.", { exact: true }),
  ).toBeVisible();
  await page.locator(".browser-presets summary").click();
  await page
    .getByRole("button", { name: "Reset display defaults", exact: true })
    .click();
  await page.getByLabel("Preset name", { exact: true }).fill("Options default");
  await page
    .getByRole("button", { name: "Save new preset", exact: true })
    .click();
  expect(
    (await browserSnapshot(page)).preferences.workspace?.browser
      ?.options_presets[0].settings,
  ).not.toHaveProperty("expiry");
});

test("late option responses cannot restore the previous underlying contract", async ({
  page,
}) => {
  await nativeFixture(page, false, true);
  await page.goto("/");
  await navigateScreen(page, "options");
  await page.evaluate(() => {
    (window as unknown as { testDelayOptions: number }).testDelayOptions = 350;
  });
  await page.getByLabel("Next option expiry", { exact: true }).click();
  await page.evaluate(async () => {
    const api = (
      window as unknown as {
        __TAURI_INTERNALS__: {
          invoke: (name: string, args: unknown) => Promise<unknown>;
        };
      }
    ).__TAURI_INTERNALS__;
    await api.invoke("terminal_action", {
      action: { type: "select", symbol: "yahoo:AAPL" },
    });
  });
  await expect(
    page.getByRole("button", { name: "Call AAPL-CALL-A Bid 1", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Call CALL-A Bid 1", exact: true }),
  ).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { testFinishedOldOptions?: boolean })
            .testFinishedOldOptions,
      ),
    )
    .toBe(true);
  await expect(
    page.getByRole("button", { name: "Call CALL-A Bid 1", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Call AAPL-CALL-A Bid 1", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Selected option contract" }),
  ).toContainText("AAPL-CALL-A");
});

for (const state of ["empty", "unavailable", "no_expiries"] as const)
  test(`option ${state} state is explicit and absent quotes disable nearest strikes`, async ({
    page,
  }) => {
    await nativeFixture(page, false, true);
    await page.addInitScript(
      ({ state }) => {
        Object.assign(window, { testOptionsState: state, testNoQuote: true });
      },
      { state },
    );
    await page.goto("/");
    await navigateScreen(page, "options");
    if (state === "empty")
      await expect(
        page.getByText("No contracts returned for this expiry.", {
          exact: true,
        }),
      ).toBeVisible();
    if (state === "unavailable")
      await expect(
        page.getByText("No option chain available", { exact: true }),
      ).toBeVisible();
    if (state === "no_expiries") {
      await expect(
        page.getByLabel("Option expiry", { exact: true }),
      ).toBeDisabled();
      await expect(
        page.getByRole("option", { name: "Nearest 21 strikes", exact: true }),
      ).toHaveAttribute("disabled", "");
      await page
        .getByRole("button", { name: "Option columns", exact: true })
        .click();
      await page.setViewportSize({ width: 960, height: 640 });
      await page.screenshot({
        path: "test-results/options-columns-small.png",
        fullPage: true,
      });
    }
  });

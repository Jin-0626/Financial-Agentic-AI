import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        new URL(route.request().url()).pathname === "/api/auth/config"
          ? { required: false, configured: false }
          : new URL(route.request().url()).pathname === "/api/threads"
            ? []
            : { traces: [], portfolios: [] },
      ),
    }),
  );
});

test("workspace switcher supports keyboard search and navigation", async ({
  page,
}) => {
  await page.goto("/research");
  await expect(
    page.getByRole("button", { name: "Search symbol / workspace" }),
  ).toBeVisible();
  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "Search instruments" });
  await expect(dialog).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Search instruments or commands" }),
  ).toBeFocused();
  await page
    .getByRole("textbox", { name: "Search instruments or commands" })
    .fill("> traces");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/traces$/);
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "Search symbol / workspace" }).click();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Search symbol / workspace" }),
  ).toBeFocused();
});

test("mobile navigation stays labelled and fits the viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/research");
  for (const label of ["Portfolio", "Research", "Traces", "Terminal modules"]) {
    await expect(
      page.getByRole("link", { name: label, exact: true }),
    ).toBeVisible();
  }
  await expect(
    page.getByRole("heading", { name: "Research", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "../.review-tmp/financial-terminal-mobile.png",
    fullPage: true,
  });
});

test("terminal shell renders at desktop size", async ({ page }) => {
  await page.goto("/research");
  await expect(
    page.getByRole("heading", { name: "Research", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Search symbol / workspace" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../.review-tmp/financial-terminal-desktop.png",
    fullPage: true,
  });
});

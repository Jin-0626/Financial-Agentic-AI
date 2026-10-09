import { test, expect } from "@playwright/test";

test("production build persists portfolio through real APIs and Rust", async ({
  page,
  request,
}) => {
  const session = await (await request.get("/fixture/session")).json();
  const headers = { Authorization: `Bearer ${session.access_token}` };
  const created = await request.post(
    "/api/portfolio/create?org_id=spoof&user_id=victim",
    { headers, data: { name: "Production saved" } },
  );
  expect(created.ok()).toBeTruthy();
  const doc = await created.json();
  const trade = await request.post(
    `/api/portfolio/transactions?portfolio_id=${doc.id}&revision=${doc.revision}`,
    {
      headers,
      data: {
        id: "real-buy",
        symbol: "AAPL",
        side: "BUY",
        quantity: "2",
        price: "100",
        date: "2025-01-01",
      },
    },
  );
  expect(trade.ok()).toBeTruthy();
  await page.addInitScript((session) => {
    sessionStorage.setItem(
      "oidc.user:https://fixture.invalid:terminal-e2e",
      JSON.stringify(session),
    );
  }, session);
  await page.goto("/portfolio");
  await expect(page.getByLabel("Selected portfolio")).toHaveValue(doc.id);
  await expect(
    page.getByText("$240.00", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByLabel("Organization")).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel("Selected portfolio")).toHaveValue(doc.id);
  await page.getByRole("button", { name: "Edit holding AAPL" }).click();
  await expect(page.getByLabel("Transaction to correct")).toHaveValue(
    "real-buy",
  );
  await page.getByLabel("Quantity", { exact: true }).fill("3");
  await page.getByLabel("Price (USD)", { exact: true }).fill("80");
  await page.getByRole("button", { name: "Save correction" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByText("$360.00", { exact: true }).first(),
  ).toBeVisible();
  const corrected = await (
    await request.get(`/api/portfolio?portfolio_id=${doc.id}`, { headers })
  ).json();
  expect(corrected.portfolio.positions[0]).toMatchObject({
    quantity: "3",
    average_cost: "80",
  });
  expect(corrected.transaction_corrections[0].previous).toMatchObject({
    id: "real-buy",
    quantity: "2",
    price: "100",
  });
  await page.reload();
  await expect(
    page.getByText("$360.00", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Recent transaction corrections" }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "2 @ 100", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "3 @ 80", exact: true }),
  ).toBeVisible();
  const other = await (await request.get("/fixture/session?org=other")).json();
  const foreign = await request.get(
    `/api/portfolio?portfolio_id=${doc.id}&org_id=production-test&user_id=alice`,
    { headers: { Authorization: `Bearer ${other.access_token}` } },
  );
  expect((await foreign.json()).portfolio).toBeNull();
});

test("production research streams and survives history reload", async ({
  page,
  request,
}) => {
  const session = await (await request.get("/fixture/session")).json();
  await page.addInitScript((session) => {
    sessionStorage.setItem(
      "oidc.user:https://fixture.invalid:terminal-e2e",
      JSON.stringify(session),
    );
  }, session);
  await page.goto("/research");
  await page.getByLabel("Research question").fill("Research fixture");
  await page.getByRole("button", { name: /^Research/ }).click();
  await expect(
    page.getByRole("heading", { name: "Production fixture report" }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: /Research fixture/ }).click();
  await expect(
    page.getByRole("heading", { name: "Production fixture report" }),
  ).toBeVisible();
});

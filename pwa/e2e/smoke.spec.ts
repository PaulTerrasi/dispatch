import { test, expect } from "@playwright/test";

test("page loads with correct title", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });

  const response = await page.goto("/");
  expect(response?.status()).toBe(200);
  await expect(page).toHaveTitle(/Dispatch/i);

  const fatal = errors.filter(
    (e) => e.includes("TypeError") || e.includes("SyntaxError"),
  );
  expect(fatal, `Fatal JS errors on load: ${fatal.join("; ")}`).toHaveLength(0);
});

test("SPA routing — /archive does not 404", async ({ page }) => {
  const response = await page.goto("/archive");
  // CloudFront maps 403/404 → index.html, so we get 200
  expect(response?.status()).toBe(200);
  await expect(page).toHaveTitle(/Dispatch/i);
});

test("API health endpoint returns ok", async ({ request }) => {
  const res = await request.get("/api/_health");
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.status).toBe("ok");
});

test("static assets load — no 404 on main JS bundle", async ({ page }) => {
  const failed: string[] = [];
  page.on("response", (res) => {
    if (res.status() === 404 && res.url().match(/\.(js|css)$/)) {
      failed.push(res.url());
    }
  });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  expect(
    failed,
    `Missing static assets: ${failed.join(", ")}`,
  ).toHaveLength(0);
});

test("feedback flow — first thumbs-up moves item out of the inbox feed", async ({ page }) => {
  await page.goto("/");
  await page.waitForLoadState("networkidle");

  // The feed view renders one .item per unreacted item. If the inbox is empty
  // there's nothing to test — skip rather than fail (single-user app, the live
  // CloudFront site can be inbox-zero on any given day).
  const items = page.locator(".item");
  const count = await items.count();
  test.skip(count === 0, "No feed items present to react to");

  const first = items.first();
  const itemId = await first.getAttribute("data-item-id");
  expect(itemId).toBeTruthy();

  // Click thumbs-up on the first item; it should fade out and disappear from the feed.
  await first.locator("button.thumb.up").click();
  await expect(page.locator(`[data-item-id="${itemId}"]`)).toHaveCount(0, {
    timeout: 5000,
  });
});

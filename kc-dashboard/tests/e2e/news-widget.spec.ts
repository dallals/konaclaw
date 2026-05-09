import { test, expect } from "@playwright/test";

test("NewsWidget renders on Chat view and shows the toggle", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.getByText("News", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Topic$/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Source$/ })).toBeVisible();
  await expect(page.getByPlaceholder(/topic/i)).toBeVisible();
});

test("NewsWidget collapses and re-expands", async ({ page }) => {
  await page.goto("/chat");
  await page.getByRole("button", { name: "Collapse News" }).click();
  await expect(page.getByRole("button", { name: "Expand News" })).toBeVisible();
  await page.getByRole("button", { name: "Expand News" }).click();
  await expect(page.getByText("News", { exact: true })).toBeVisible();
});

test("NewsWidget shows not_configured banner when supervisor has no key", async ({ page, request }) => {
  // Skip if supervisor not running
  try {
    await request.get("http://127.0.0.1:8765/health", { timeout: 1000 });
  } catch {
    test.skip(true, "supervisor not reachable");
    return;
  }
  // Skip if newsapi IS configured — this test only validates the not_configured path
  const r = await request.get("http://127.0.0.1:8765/api/news?mode=topic&q=ping");
  if (r.status() !== 503) {
    test.skip(true, "newsapi appears configured — skipping not_configured assertion");
    return;
  }

  await page.goto("/chat");
  await page.getByPlaceholder(/topic/i).fill("ping");
  await page.getByRole("button", { name: /^Go$/ }).click();
  await expect(page.getByText(/News not configured/i)).toBeVisible();
});

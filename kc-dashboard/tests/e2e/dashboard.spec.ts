import { test, expect } from "@playwright/test";

test("dashboard shell loads and shows tabs", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("KonaClaw")).toBeVisible();
  for (const tab of ["Chat", "Agents", "Shares", "Permissions", "Monitor", "Audit"]) {
    await expect(page.getByRole("link", { name: tab })).toBeVisible();
  }
});

test("Audit view loads (skips if supervisor not running)", async ({ page, request }) => {
  try {
    await request.get("http://127.0.0.1:8765/health", { timeout: 1000 });
  } catch {
    test.skip(true, "supervisor not reachable");
    return;
  }

  await page.goto("/audit");
  await expect(page.getByText(/Audit log/)).toBeVisible();
});

import { expect, test } from "@playwright/test";

// The Community Edition shows paid features as locked, with a clear way to upgrade.

test("paid features are locked with an upgrade path in the Community Edition", async ({ page, request }) => {
  const info = (await (await request.get("/api/v1/system/info")).json()) as { edition: string };
  test.skip(info.edition !== "community", "stack runs a licensed edition");

  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  for (const label of ["Users", "Secrets", "Audit log", "Notifications"]) {
    await expect(nav.getByRole("link", { name: new RegExp(`^${label}`) })).toBeVisible();
  }
  await nav.getByRole("link", { name: /^Secrets/ }).click();
  await expect(page.getByText("Secrets is a Team & Enterprise feature")).toBeVisible();
  await expect(page.getByRole("link", { name: "Contact us" })).toHaveAttribute("href", /^mailto:anshulshrm12@gmail\.com/);

  // The API agrees: paid endpoints don't exist, and secrets in jobs are refused with a hint.
  const r = await request.post("/api/v1/jobs", {
    data: { name: `pw-secret-${Date.now()}`, spec: { runtime: "shell", script: "x", secrets: { A: "a" } } },
  });
  expect(r.status()).toBe(402);

  await page.goto("/jobs/new");
  await expect(page.getByText("are part of")).toBeVisible();
});

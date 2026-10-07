import { expect, test } from "@playwright/test";

// M4: create a schedule with live preview, see it listed, pause/resume, delete.

test("schedule a job with a cron preset and manage it", async ({ page, request }) => {
  const name = `pw-sched-${Date.now().toString(36)}`;
  const job = await (await request.post("/api/v1/jobs", { data: { name, spec: { runtime: "shell", script: "true" } } })).json();

  await page.goto(`/jobs/${job.id}`);
  await page.getByRole("tab", { name: /Schedules/ }).click();
  await page.getByRole("button", { name: "Add schedule" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill("nightly");
  await dialog.getByRole("button", { name: "Weekdays 09:00" }).click();
  await dialog.getByLabel("Time zone").selectOption("Asia/Kolkata");
  await expect(dialog.getByText("Weekdays at 09:00")).toBeVisible();
  await expect(dialog.getByRole("list", { name: "Next runs" }).getByRole("listitem")).toHaveCount(5);

  await dialog.getByLabel("Cron expression").fill("61 * * * *");
  await expect(dialog.getByText(/minute value 61 out of range/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Create schedule" })).toBeDisabled();
  await dialog.getByLabel("Cron expression").fill("30 9 * * 1-5");
  await dialog.getByRole("button", { name: "Create schedule" }).click();

  const row = page.getByRole("row").filter({ hasText: "nightly" });
  await expect(row).toContainText("Weekdays at 09:30");
  await expect(row).toContainText("Asia/Kolkata");
  await row.getByRole("button", { name: "Pause nightly" }).click();
  await expect(row.getByText("Paused")).toBeVisible();
  await row.getByRole("button", { name: "Resume nightly" }).click();
  await expect(row.getByText("Paused")).toHaveCount(0);

  await page.goto("/schedules");
  await expect(page.getByRole("row").filter({ hasText: name })).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("row").filter({ hasText: name }).getByRole("button", { name: "Delete nightly" }).click();
  await expect(page.getByRole("row").filter({ hasText: name })).toHaveCount(0);
});

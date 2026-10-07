import { expect, test } from "@playwright/test";

// M5: author a workflow with live validation, run it, watch the graph, see task states.

test("create a workflow with live validation and run it", async ({ page, request }) => {
  const id = Date.now().toString(36);
  for (const n of ["fetch", "build", "notify"]) {
    await request.post("/api/v1/jobs", { data: { name: `pw-${n}-${id}`, spec: { runtime: "shell", script: `echo ${n}` } } });
  }
  await page.goto("/workflows/new");
  await page.getByLabel("Name").fill(`pw-flow-${id}`);
  const editor = page.getByLabel("Workflow definition (YAML)");

  await editor.fill(`tasks:\n  a: {job: pw-fetch-${id}, depends_on: [b]}\n  b: {job: pw-build-${id}, depends_on: [a]}\n`);
  await expect(page.getByRole("alert")).toContainText("cycle");
  await expect(page.getByRole("button", { name: "Create workflow" })).toBeDisabled();

  await editor.fill(
    `tasks:\n  fetch: {job: pw-fetch-${id}}\n  build: {job: pw-build-${id}, depends_on: [fetch]}\n  notify: {job: pw-notify-${id}, depends_on: [build], trigger_rule: all_done}\n`,
  );
  await expect(page.getByText("Valid · 3 tasks in 3 stages")).toBeVisible();
  await page.getByRole("button", { name: "Create workflow" }).click();

  await expect(page.getByRole("heading", { name: `pw-flow-${id}` })).toBeVisible();
  await page.getByRole("button", { name: "Run workflow" }).first().click();
  await expect(page).toHaveURL(/\/workflow-runs\//);
  await expect(page.getByText("Succeeded").first()).toBeVisible({ timeout: 30_000 });
  for (const task of ["fetch", "build", "notify"]) {
    await expect(page.locator(`[data-task="${task}"]`)).toHaveAttribute("data-state", "SUCCEEDED");
  }
  // Each node links to its run.
  await page.locator('[data-task="build"] a').click();
  await expect(page.getByRole("log", { name: "Run output" })).toContainText("build");
});

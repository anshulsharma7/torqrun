import { expect, test, type APIRequestContext } from "@playwright/test";

// M2 through the real UI: cancel, retry with attempts, reliability settings, queues.

const unique = () => `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

async function createJob(request: APIRequestContext, name: string, spec: Record<string, unknown>) {
  const r = await request.post("/api/v1/jobs", { data: { name, spec: { runtime: "shell", ...spec } } });
  expect(r.ok()).toBeTruthy();
  return (await r.json()) as { id: string };
}

test("cancel a running job from the run page", async ({ page, request }) => {
  const job = await createJob(request, `pw-cancel-${unique()}`, { script: "echo started; sleep 300" });
  await page.goto(`/jobs/${job.id}`);
  await page.getByRole("button", { name: "Run now" }).first().click();
  await expect(page.getByRole("log", { name: "Run output" })).toContainText("started");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByText("Cancelled").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
});

test("retry a failed run and switch between attempt logs", async ({ page, request }) => {
  const job = await createJob(request, `pw-retry-${unique()}`, {
    script: 'echo "attempt $TORQRUN_ATTEMPT"; [ "$TORQRUN_ATTEMPT" -ge 2 ] || exit 3',
  });
  await page.goto(`/jobs/${job.id}`);
  await page.getByRole("button", { name: "Run now" }).first().click();
  await expect(page.getByRole("alert")).toContainText("exited with code 3");
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText("Succeeded").first()).toBeVisible({ timeout: 15_000 });
  const log = page.getByRole("log", { name: "Run output" });
  await expect(log).toContainText("attempt 2");
  await page.getByRole("tab", { name: /Attempt 1/ }).click();
  await expect(log).toContainText("attempt 1");
  await expect(log).not.toContainText("attempt 2");
});

test("automatic retries show the waiting countdown", async ({ page, request }) => {
  const job = await createJob(request, `pw-backoff-${unique()}`, {
    script: '[ "$TORQRUN_ATTEMPT" -ge 2 ] || exit 1; echo fine',
    retry: { max_attempts: 2, backoff_seconds: 6, backoff_factor: 1 },
  });
  await page.goto(`/jobs/${job.id}`);
  await page.getByRole("button", { name: "Run now" }).first().click();
  await expect(page.getByText(/Attempt 2 of 2 starts in/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Succeeded").first()).toBeVisible({ timeout: 20_000 });
});

test("reliability settings round-trip through the job form", async ({ page }) => {
  const name = `pw-settings-${unique()}`;
  await page.goto("/jobs/new");
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Attempts").fill("4");
  await page.getByLabel("First retry after (s)").fill("30");
  await page.getByLabel("Also retry after a timeout").check();
  await page.getByLabel("If the agent dies mid-run").selectOption("retry");
  await page.getByLabel("Max concurrent runs").fill("2");
  await page.getByRole("button", { name: "Create job" }).click();
  await page.getByRole("tab", { name: "Configuration" }).click();
  await expect(page.getByText("4 (first retry after 30s, doubling; also on timeout)")).toBeVisible();
  await expect(page.getByText("retry", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await expect(page.getByLabel("Max concurrent runs")).toHaveValue("2");
});

test("pause and resume a queue", async ({ page, request }) => {
  const queue = `pw-q-${unique()}`;
  await createJob(request, `pw-queue-${unique()}`, { script: "true", queue });
  await page.goto("/queues");
  const row = page.getByRole("row").filter({ hasText: queue });
  await row.getByRole("button", { name: "Pause" }).click();
  await expect(row.getByText("Paused")).toBeVisible();
  await row.getByLabel(`Concurrency limit for ${queue}`).fill("3");
  await row.getByRole("button", { name: "Save" }).click();
  await expect(row.getByLabel(`Concurrency limit for ${queue}`)).toHaveValue("3");
  await row.getByRole("button", { name: "Resume" }).click();
  await expect(row.getByText("Paused")).toHaveCount(0);
});

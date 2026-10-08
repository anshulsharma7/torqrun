import { expect, test } from "@playwright/test";

// M7 through the real UI: notification channels, artifacts, container job settings.

const unique = () => `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test("files a job writes to TORQRUN_ARTIFACTS_DIR can be downloaded from the run", async ({ page, request }) => {
  const r = await request.post("/api/v1/jobs", {
    data: { name: `pw-artifacts-${unique()}`, spec: { runtime: "shell", script: 'echo "hello artifact" > "$TORQRUN_ARTIFACTS_DIR/report.txt"' } },
  });
  const job = (await r.json()) as { id: string };
  await page.goto(`/jobs/${job.id}`);
  await page.getByRole("button", { name: "Run now" }).first().click();
  await expect(page.getByText("Succeeded").first()).toBeVisible();
  const link = page.getByRole("link", { name: "Download report.txt" });
  await expect(link).toBeVisible();
  const download = await Promise.all([page.waitForEvent("download"), link.click()]).then(([d]) => d);
  expect(download.suggestedFilename()).toBe("report.txt");
});

test("container settings round-trip through the job form", async ({ page }) => {
  const name = `pw-container-${unique()}`;
  await page.goto("/jobs/new");
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Executor").selectOption("docker");
  await page.getByLabel("Image").fill("python:3.12-slim");
  await page.getByLabel("Network").selectOption("none");
  await page.getByLabel("Memory (MB)").fill("256");
  await page.getByRole("button", { name: "Create job" }).click();
  await page.getByRole("tab", { name: "Configuration" }).click();
  await expect(page.getByText("container python:3.12-slim · no network · 256 MB")).toBeVisible();
});

test("plans page shows the edition and how to upgrade", async ({ page, request }) => {
  const info = (await (await request.get("/api/v1/system/info")).json()) as { edition: string };
  await page.goto("/");
  if (info.edition === "community") {
    await page.getByRole("link", { name: /Community Edition/ }).click();
  } else {
    await page.goto("/plans");
  }
  await expect(page.getByRole("heading", { name: "Plans", exact: true })).toBeVisible();
  await expect(page.getByText("Current plan")).toBeVisible();
  const sales = page.getByRole("link", { name: "Contact sales" });
  await expect(sales).toHaveAttribute("href", /^mailto:anshulshrm12@gmail\.com\?subject=Torqrun%20Team%20plan/);
  await expect(page.getByRole("link", { name: "anshulshrm12@gmail.com" })).toBeVisible();
  const inPlace = page.getByText("make upgrade LICENSE=<license key> TOKEN=<registry token>");
  if (info.edition === "community") await expect(inPlace).toBeVisible();
  else await expect(inPlace).toHaveCount(0);
});

import { expect, test } from "@playwright/test";

// M1 acceptance through the real UI: create a job, run it, watch live logs, see the result.
// Requires a running stack with at least one connected agent.

const unique = () => `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test("fleet shows online agents with live host stats", async ({ page }) => {
  await page.goto("/agents"); // old URL redirects
  await expect(page).toHaveURL(/\/fleet$/);
  await expect(page.getByRole("heading", { name: "Fleet" })).toBeVisible();
  await expect(page.getByText("Online").first()).toBeVisible();
  await expect(page.getByRole("meter", { name: "Memory" }).first()).toBeVisible();
});

test("connect-agent dialog creates a token and shows install commands", async ({ page }) => {
  await page.goto("/fleet");
  await page.getByRole("button", { name: "Connect agent" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Connect an agent" });
  const label = `pw-server-${Date.now().toString(36)}`;
  await dialog.getByPlaceholder("e.g. build-server-01").fill(label);
  await dialog.getByRole("button", { name: "Create token" }).click();
  await expect(dialog).toContainText("/agent/install.sh | sudo bash -s -- --token tqe_");
  await dialog.getByRole("tab", { name: "Docker" }).click();
  await expect(dialog).toContainText("TORQRUN_AGENT_ENROLLMENT_TOKEN=tqe_");
  await dialog.getByRole("tab", { name: "From source" }).click();
  await expect(dialog).toContainText("uv run torq-agent start");
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText(label)).toBeVisible(); // listed under active tokens
});

test("command palette jumps to a page", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible(); // signed-in shell is up
  await page.keyboard.press("Control+k");
  await page.getByRole("textbox", { name: "Search" }).fill("fleet");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/fleet/);
});

test("create a Python job, run it and see live output and success", async ({ page }) => {
  const name = `pw-hello-${unique()}`;
  await page.goto("/jobs/new");
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Script", { exact: true }).fill(
    'import sys, time\nprint("hello from playwright")\nfor i in range(3):\n    print(f"tick {i}", flush=True)\n    time.sleep(0.7)\nprint("a warning", file=sys.stderr)\n',
  );
  await page.getByRole("button", { name: "Create job" }).click();

  await expect(page.getByRole("heading", { name })).toBeVisible();
  await page.getByRole("button", { name: "Run now" }).first().click();

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/);
  const log = page.getByRole("log", { name: "Run output" });
  // Lines appear while the job is still running (live streaming), before it succeeds.
  await expect(log).toContainText("hello from playwright");
  await expect(log).toContainText("tick 2");
  await expect(log).toContainText("a warning");
  await expect(page.getByText("Succeeded").first()).toBeVisible();
  await expect(page.locator("dt", { hasText: "Exit code" }).locator("+ dd")).toHaveText("0");
  await expect(page.getByRole("list", { name: "Run progress" })).toContainText("Succeeded");

  // The run shows up in the job's history after navigating back.
  await page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name }).click();
  await expect(page.getByRole("cell").getByText("Succeeded")).toBeVisible();
});

test("a failing shell job shows its exit code and error", async ({ page }) => {
  const name = `pw-fail-${unique()}`;
  await page.goto("/jobs/new");
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByRole("radio", { name: "Bash" }).click();
  await page.getByLabel("Script", { exact: true }).fill("echo 'connection refused' >&2\nexit 4\n");
  await page.getByRole("button", { name: "Create job" }).click();
  await page.getByRole("button", { name: "Run now" }).first().click();

  await expect(page.getByText("Failed").first()).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("exited with code 4");
  await expect(page.getByRole("alert")).toContainText("connection refused");
});

test("invalid input is explained, not swallowed", async ({ page }) => {
  await page.goto("/jobs/new");
  await page.getByLabel("Name", { exact: true }).fill(`pw-env-${unique()}`);
  await page.getByLabel("Environment variables").fill("TORQRUN_TOKEN=x");
  await page.getByRole("button", { name: "Create job" }).click();
  await expect(page.getByRole("alert")).toContainText("reserved");
});

test("pages fit a phone screen without sideways scrolling", async ({ browser }) => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  for (const path of ["/", "/jobs", "/runs", "/fleet", "/jobs/new"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0);
  }
  await page.close();
});

test("side-by-side boxes share their edges", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const card = (title: string) => page.locator(`section:has(> header h2:text-is("${title}"))`).first();
  const edges = async (title: string) => {
    // Cards slide in on load; measure only once every animation has settled.
    // (Ignore infinite ones such as pulsing status dots.)
    await page.waitForFunction(() =>
      document
        .getAnimations()
        .filter((a) => a.effect?.getTiming().iterations !== Infinity)
        .every((a) => a.playState !== "running"),
    );
    const b = (await card(title).boundingBox())!;
    return { top: Math.round(b.y), bottom: Math.round(b.y + b.height) };
  };
  await page.goto("/");
  await expect(card("Control plane")).toBeVisible();
  expect(await edges("Activity")).toEqual(await edges("Fleet"));
  expect(await edges("Recent runs")).toEqual(await edges("Control plane"));
  await page.goto("/jobs/new");
  await expect(card("Settings")).toBeVisible();
  expect((await edges("Script")).top).toBe((await edges("Settings")).top);
});

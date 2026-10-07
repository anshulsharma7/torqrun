import { expect, test } from "@playwright/test";

// M3: drain / resume / revoke from the agent page, on an agent enrolled with a real token.

test("drain, resume and revoke an agent", async ({ page, request }) => {
  const name = `pw-agent-${Date.now().toString(36)}`;
  const token = await (await request.post("/api/v1/enrollment-tokens", { data: { description: name } })).json();
  const enrolled = await request.post("/api/v1/agent/enroll", {
    data: { enrollment_token: token.token, name, hostname: "h", os: "linux", arch: "x86_64", agent_version: "t", max_slots: 1 },
  });
  expect(enrolled.ok()).toBeTruthy();
  const { agent_id: id, credential } = await enrolled.json();
  await request.post("/api/v1/agent/heartbeat", {
    data: { running: [], free_slots: 1 },
    headers: { Authorization: `Bearer ${credential}` },
  });

  await page.goto(`/fleet/${id}`);
  await page.getByRole("button", { name: "Drain" }).click();
  await expect(page.getByText(/Draining: no new jobs/)).toBeVisible();
  await page.getByRole("button", { name: "Resume" }).click();
  await expect(page.getByRole("button", { name: "Drain" })).toBeVisible();

  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Revoke" }).click();
  await expect(page.getByText("Revoked", { exact: true }).first()).toBeVisible();
  const hb = await request.post("/api/v1/agent/heartbeat", {
    data: { running: [], free_slots: 1 },
    headers: { Authorization: `Bearer ${credential}` },
  });
  expect(hb.status()).toBe(401);
});

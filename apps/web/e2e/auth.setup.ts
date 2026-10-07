import { expect, test as setup } from "@playwright/test";

// Signs in once (creating the first admin on a fresh stack) and saves the session for every
// spec. Override the account with TORQRUN_E2E_EMAIL / TORQRUN_E2E_PASSWORD.
export const STORAGE_STATE = "e2e/.auth/admin.json";
export const E2E_EMAIL = process.env.TORQRUN_E2E_EMAIL ?? "e2e-admin@example.com";
export const E2E_PASSWORD = process.env.TORQRUN_E2E_PASSWORD ?? "e2e-admin-password";

setup("sign in", async ({ page }) => {
  await page.goto("/");
  const setupHeading = page.getByRole("heading", { name: "Welcome to Torqrun" });
  const signInHeading = page.getByRole("heading", { name: "Sign in to Torqrun" });
  await expect(setupHeading.or(signInHeading)).toBeVisible();

  if (await setupHeading.isVisible()) {
    await page.getByLabel("Your name").fill("E2E Admin");
    await page.getByLabel("Email").fill(E2E_EMAIL);
    await page.getByLabel("Password", { exact: true }).fill(E2E_PASSWORD);
    await page.getByLabel("Confirm password").fill(E2E_PASSWORD);
    await page.getByRole("button", { name: "Create admin account" }).click();
  } else {
    await page.getByLabel("Email").fill(E2E_EMAIL);
    await page.getByLabel("Password").fill(E2E_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await page.context().storageState({ path: STORAGE_STATE });
});

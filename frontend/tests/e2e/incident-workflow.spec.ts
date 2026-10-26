import { expect, test, type Page } from "@playwright/test";

// The critical workflow, end to end through the real stack (UI → Next proxy → API → Redis → worker → PostgreSQL):
// login → declare incident → AI investigation runs in the worker → engineer reviews evidence and rates it →
// engineer resolves the incident → the audit trail records it.
async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel(/e-?mail/i).fill(email);
  await page.getByLabel(/password/i).fill("opspilot-demo");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
}

test("engineer investigates and resolves an incident", async ({ page }) => {
  const title = `E2E checkout failures ${Date.now()}`;

  await signIn(page, "maria@opspilot.dev");

  // Declare
  await page.goto("/incidents");
  await page.getByRole("button", { name: "Declare incident" }).click();
  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Severity").selectOption("SEV1");
  await page.getByLabel("Affected service").selectOption("payment-service");
  await page.getByLabel("Summary").fill("Customers report checkout freezing after clicking Pay");
  await page.getByLabel("Started (minutes ago)").fill("30");
  await page.getByRole("button", { name: "Declare incident" }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  const number = (await page.locator("div.font-mono").first().textContent())?.trim();
  expect(number).toMatch(/^INC-\d+$/);

  // Investigate: the API answers 202 at once, the worker reports each stage, the result replaces the progress.
  await page.getByRole("button", { name: "Run investigation" }).click();
  await expect(page.getByText("Probable root cause")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Grounding check · \d+ citations verified/)).toBeVisible();
  await expect(page.getByText("Evidence", { exact: true })).toBeVisible();

  // Review and rate
  await page.getByRole("button", { name: "👍 Useful" }).click();
  await expect(page.getByRole("button", { name: "👍 Useful" })).toHaveAttribute("aria-pressed", "true");

  // Resolve (the root cause is pre-filled from the AI finding; a human confirms it)
  await page.getByRole("button", { name: "Resolve" }).first().click();
  const rootCause = page.getByLabel("Root cause (required)");
  await expect(rootCause).not.toHaveValue("");
  await page.getByRole("button", { name: "Resolve", exact: true }).last().click();
  await expect(page.getByText("Confirmed root cause")).toBeVisible();
  await expect(page.locator("span", { hasText: /^RESOLVED$/ }).first()).toBeVisible();

  // Audit trail
  const activity = page.locator("section", { hasText: "Activity · audit trail" });
  await expect(activity.getByText(`Incident ${number} declared by Maria Papadopoulou (SEV1)`)).toBeVisible();
  await expect(activity.getByText("AI investigation started")).toBeVisible();
  await expect(activity.getByText(/^AI investigation generated:/)).toBeVisible();
  await expect(activity.getByText(/rated investigation #\d+ useful/)).toBeVisible();
  await expect(activity.getByText("Incident resolved by Maria Papadopoulou")).toBeVisible();
});

test("viewers can read incidents but not act on them", async ({ page }) => {
  await signIn(page, "viewer@opspilot.dev");
  await page.goto("/incidents");
  await expect(page.getByRole("heading", { name: "Incidents" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Declare incident" })).toHaveCount(0);
  await page.locator("a[href^='/incidents/INC-']").first().click();
  await expect(page.getByText("Was this investigation useful?").or(page.getByText("No investigation has been run"))).toBeVisible();
  await expect(page.getByRole("button", { name: /Run investigation|Re-run investigation/ })).toHaveCount(0);
});

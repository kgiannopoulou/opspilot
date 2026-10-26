import { expect, test, type Browser, type Locator, type Page } from "@playwright/test";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

// Records the README media against a freshly seeded stack (docs/media/README.md):
//   - docs/media/opspilot-demo.webm   the 90-second walkthrough as video
//   - docs/media/frames/*.png + frames.json → docs/media/build_gif.py → docs/media/opspilot-demo.gif
//   - docs/screenshots/hero-incident-brief.png   the recruiter screenshot (2× pixel density)
// Run: npx playwright test -c playwright.media.config.ts

const OUT = path.resolve(__dirname, "../../../docs/media");
const FRAMES = path.join(OUT, "frames");
const VIEWPORT = { width: 1280, height: 800 };
const frames: { file: string; ms: number }[] = [];

async function session(browser: Browser): Promise<string> {
  const ctx = await browser.newContext({ viewport: VIEWPORT });
  const page = await ctx.newPage();
  await page.goto("/login");
  await page.getByLabel(/e-?mail/i).fill("maria@opspilot.dev");
  await page.getByLabel(/password/i).fill("opspilot-demo");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
  const state = path.join(OUT, ".state.json");
  await ctx.storageState({ path: state }); // the HttpOnly refresh cookie restores the session in the recording
  await ctx.close();
  return state;
}

/** Bottom caption bar, drawn into the page so it is part of both the video and the GIF frames. */
async function caption(page: Page, step: string, text: string) {
  await page.evaluate(([s, t]) => {
    let bar = document.getElementById("demo-caption");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "demo-caption";
      bar.style.cssText = "position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:9999;max-width:88%;" +
        "padding:10px 18px;border-radius:999px;background:rgba(2,6,23,.92);border:1px solid rgba(56,189,248,.45);" +
        "box-shadow:0 10px 30px rgba(0,0,0,.5);color:#e2e8f0;font:500 15px/1.35 Inter,system-ui,sans-serif;white-space:nowrap";
      document.body.appendChild(bar);
    }
    bar.innerHTML = `<span style="color:#38bdf8;font-weight:700;margin-right:10px">${s}</span>${t}`;
  }, [step, text]);
}

async function frame(page: Page, ms: number) {
  const file = `${String(frames.length).padStart(3, "0")}.png`;
  await page.screenshot({ path: path.join(FRAMES, file) });
  frames.push({ file, ms });
  await page.waitForTimeout(Math.min(ms, 1_200)); // the video gets real time too, compressed a little
}

/** Instant scroll (no smooth-scroll frames half painted), with `el` placed `offset` px below the viewport top. */
async function scrollTo(page: Page, target: Locator | number, offset = 90) {
  if (typeof target === "number") {
    await page.evaluate((y) => window.scrollTo({ top: y, behavior: "instant" }), target);
  } else {
    await target.first().evaluate((el, off) => {
      window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - off, behavior: "instant" });
    }, offset);
  }
  await page.waitForTimeout(400);
}

// Runs first, on the fresh seed, so the incident is still open. A new sign-in: the demo below rotates the refresh
// token, and reusing a rotated one revokes the session (by design).
test("hero screenshot: the incident brief at 2× density", async ({ browser }) => {
  mkdirSync(OUT, { recursive: true });
  const state = await session(browser);
  const hero = await browser.newContext({ viewport: { width: 1100, height: 1000 }, deviceScaleFactor: 2, storageState: state });
  const page = await hero.newPage();
  await page.goto("/incidents");
  await page.getByText("Payment checkout timeout").first().click();
  await expect(page.getByRole("heading", { name: "Payment checkout timeout" })).toBeVisible();
  if (!(await page.getByText("Probable root cause").isVisible())) {
    await page.getByRole("button", { name: /Run investigation/ }).click();
    await expect(page.getByText("Probable root cause")).toBeVisible({ timeout: 60_000 });
  }
  await page.goto(`${page.url()}/brief`);
  await expect(page.locator("#incident-brief")).toBeVisible();
  await page.waitForTimeout(800);
  await page.locator("#incident-brief").screenshot({ path: path.resolve(OUT, "../screenshots/hero-incident-brief.png") });
  await hero.close();
  rmSync(state, { force: true });
});

test("record the 90-second demo", async ({ browser }) => {
  test.setTimeout(300_000);
  rmSync(FRAMES, { recursive: true, force: true });
  mkdirSync(FRAMES, { recursive: true });
  const state = await session(browser);

  const ctx = await browser.newContext({
    viewport: VIEWPORT, storageState: state, recordVideo: { dir: path.join(OUT, ".video"), size: VIEWPORT },
  });
  const page = await ctx.newPage();

  // 0–10 s · dashboard
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
  await page.waitForTimeout(1_500);
  await caption(page, "OpsPilot", "AI-powered application support — live operations dashboard");
  await frame(page, 6_000);
  await scrollTo(page, 380);
  await caption(page, "OpsPilot", "Active incidents, service health and ticket volume at a glance");
  await frame(page, 6_000);

  // 10–25 s · a customer ticket
  await page.goto("/tickets");
  await page.getByRole("button", { name: "New ticket" }).click();
  await caption(page, "1 · Ticket", "A customer reports a problem");
  const title = "Payment screen throws timeout after login";
  await page.getByPlaceholder("Title", { exact: true }).click();
  await page.keyboard.type(title.slice(0, 20), { delay: 35 });
  await frame(page, 1_500);
  await page.keyboard.type(title.slice(20), { delay: 35 });
  await page.getByPlaceholder("What is the customer seeing?").fill(
    "Customers on mobile log in fine, then the payment screen spins and fails with a timeout. Several reports in the last 10 minutes.",
  );
  await page.locator("form select").first().selectOption("P2");
  await frame(page, 3_500);
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.getByText(title).first().click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  await caption(page, "1 · Ticket", "Saved in ~60 ms — AI triage runs in a background worker");
  await frame(page, 3_000);
  await expect(page.getByText(/^Matches active incident INC-/)).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1_500); // let the triage suggestion land
  await caption(page, "2 · Correlation", "Logs and metrics ±15 min around the ticket point to an active incident");
  await page.getByText(/^Matches active incident INC-/).scrollIntoViewIfNeeded();
  await frame(page, 6_000);
  await page.getByRole("button", { name: "Link ticket" }).click();
  await expect(page.getByText("✓ linked")).toBeVisible();
  await frame(page, 1_500);

  // 25–40 s · incident and AI investigation
  await page.getByText(/^Matches active incident INC-/).getByRole("link").click();
  await expect(page.getByText("AI investigation").first()).toBeVisible();
  await caption(page, "3 · Investigation", "Re-run the AI investigation — progress streams live over SSE");
  await frame(page, 2_500);
  await page.getByRole("button", { name: /Re-run investigation|Run investigation/ }).click();
  for (let n = 0; n < 3; n++) await frame(page, 900);
  await expect(page.getByText("Probable root cause")).toBeVisible({ timeout: 60_000 });

  // 40–60 s · root cause, confidence, evidence
  await caption(page, "4 · Root cause", "Likely cause + confidence, scored only from evidence");
  await scrollTo(page, 0);
  await frame(page, 6_000);
  await scrollTo(page, page.getByText("Evidence", { exact: true }), 110);
  await caption(page, "4 · Evidence", "Timeout logs, latency +400 %, database healthy, a similar past incident — each one cited");
  await frame(page, 9_000);

  // 60–75 s · runbook + generated query
  await scrollTo(page, page.getByText(/relevant documentation/i), 260);
  await caption(page, "5 · RAG", "Runbook passages retrieved with pgvector, cited by section");
  await frame(page, 5_000);
  const run = page.getByRole("button", { name: "Run query" }).first();
  await scrollTo(page, page.getByText(/investigation queries/i), 90);
  await caption(page, "5 · SQL", "Generated investigation query — validated, read-only, run by a human");
  await frame(page, 3_500);
  await run.click();
  await page.waitForTimeout(1_500);
  await frame(page, 5_000);

  // Incident brief (also the hero screenshot)
  const incidentUrl = page.url();
  await page.goto(`${incidentUrl}/brief`);
  await expect(page.locator("#incident-brief")).toBeVisible();
  await caption(page, "6 · Brief", "One screen for the incident channel — copy as a status update");
  await frame(page, 7_000);

  // 75–90 s · resolve → audit trail
  await page.goto(incidentUrl);
  await expect(page.getByText("Probable root cause")).toBeVisible();
  await page.getByRole("button", { name: "Resolve" }).first().click();
  await caption(page, "7 · Resolve", "The AI root cause is pre-filled — a human confirms it");
  await frame(page, 3_500);
  await page.getByRole("button", { name: "Resolve", exact: true }).last().click();
  await expect(page.getByText("Confirmed root cause")).toBeVisible();
  await scrollTo(page, 0);
  await caption(page, "7 · Resolved", "Resolved with a confirmed root cause");
  await frame(page, 3_500);
  const activity = page.locator("section", { hasText: "Activity · audit trail" });
  const resolved = activity.getByText(/^Incident resolved by/);
  await expect(resolved).toBeVisible({ timeout: 20_000 });
  await scrollTo(page, resolved, 520);
  await caption(page, "7 · Audit", "Every human and AI action lands in the tamper-evident audit trail");
  await frame(page, 8_000);
  await caption(page, "OpsPilot", "github.com/kgiannopoulou/opspilot");
  await frame(page, 5_000);

  const video = page.video();
  await ctx.close();
  if (video) await video.saveAs(path.join(OUT, "opspilot-demo.webm"));
  writeFileSync(path.join(FRAMES, "frames.json"), JSON.stringify(frames, null, 1));

  rmSync(state, { force: true });
});

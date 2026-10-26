import { defineConfig, devices } from "@playwright/test";

// README media (demo video, GIF frames, hero screenshot) — not part of the test suite. See docs/media/README.md.
export default defineConfig({
  testDir: "./tests/media",
  timeout: 300_000,
  expect: { timeout: 20_000 },
  reporter: "list",
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3010", colorScheme: "dark" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});

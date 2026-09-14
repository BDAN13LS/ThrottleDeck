import { devices, defineConfig } from "@playwright/test";

const PORT = 4173;
const BASE_URL = `http://127.0.0.1:${PORT}`;

// Browser interactions run against the real production build, served the same
// way Governor serves it. Every API call is intercepted per test, so the suite
// needs no running Governor service and never reaches a venue.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: true,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "off",
    video: "off",
    screenshot: "off",
  },
  projects: [
    {
      name: "window-940x700",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 940, height: 700 },
      },
    },
    {
      name: "compact-720x480",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 720, height: 480 },
      },
    },
  ],
  webServer: {
    command: "npm run build && npm run preview",
    url: BASE_URL,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});

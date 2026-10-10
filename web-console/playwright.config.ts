import { defineConfig } from "@playwright/test";

// Runs against an already running console + demo server (see README): `make demo` serves both
// on :8080, or `make console-dev` + `make demo-server` for :5173.
export default defineConfig({
  testDir: "./e2e",
  timeout: 300_000,
  workers: 1,
  expect: { timeout: 30_000 },
  use: { baseURL: process.env.CONSOLE_URL ?? "http://localhost:5173", headless: true },
  reporter: "list",
});

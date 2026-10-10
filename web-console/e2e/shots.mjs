// Captures docs screenshots against a running console + demo server:
//   node e2e/shots.mjs  (CONSOLE_URL defaults to http://localhost:5173, OUT to ../docs/screenshots)
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = process.env.CONSOLE_URL ?? "http://localhost:5173";
const OUT = process.env.OUT ?? new URL("../../docs/screenshots/", import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });
const VP = { d: { width: 1440, height: 900 }, t: { width: 1024, height: 768 }, m: { width: 390, height: 844 } };

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: VP.d, deviceScaleFactor: 1 });
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => (m.type() === "error" || m.type() === "warning") && errors.push(`${m.type()}: ${m.text()}`));
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));

const api = (path, body) =>
  page.evaluate(
    async ([p, b]) => {
      const tok = JSON.parse(sessionStorage.getItem("ssi.session")).token;
      const r = await fetch("/api" + p, { method: "POST", headers: { Authorization: "Bearer " + tok, "content-type": "application/json" }, body: JSON.stringify(b) });
      return r.status;
    },
    [path, body],
  );
const shot = async (name) => {
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}${name}.png` });
  console.log("saved", name);
};
const go = async (path) => {
  await page.evaluate((p) => { history.pushState({}, "", p); dispatchEvent(new PopStateEvent("popstate")); }, path);
  await page.waitForTimeout(900);
};

await page.goto(BASE + "/");
await page.getByTestId("role-officer").click();
await page.waitForTimeout(1500);

// Paused mid-run at t=1250 (after bank B's hold, antibody applied).
await api("/demo/start", { seed: 1, speed: 20, mode: "hero" });
await api("/demo/control", { action: "pause" });
await api("/demo/control", { action: "seek", t: 1250 });
await page.waitForTimeout(2500);
await page.evaluate(() => window.scrollTo(0, 0));
await shot("player-live-1440x900");
await go("/command");
await shot("command-1440x900");
await page.evaluate(() => document.querySelector('[aria-label="Ring graph: victims to mules to cash-out"]')?.scrollIntoView({ block: "center" }));
await shot("command-ring-1440x900");

await page.setViewportSize(VP.t);
await go("/");
await page.evaluate(() => window.scrollTo(0, 0));
await shot("player-live-1024x768");
await go("/command");
await shot("command-1024x768");

await page.setViewportSize(VP.m);
await go("/");
await shot("player-live-390x844");
await go("/command");
await shot("command-390x844");

// Let it finish for the finale card.
await page.setViewportSize(VP.d);
await go("/");
await api("/demo/control", { action: "resume" });
await page.getByRole("heading", { name: "Victim B protected" }).waitFor({ timeout: 240000 });
await page.getByTestId("verify-offline").click();
await page.getByTestId("verify-status").waitFor();
await shot("player-finale-1440x900");

console.log("console errors/warnings:", JSON.stringify(errors, null, 1));
await browser.close();

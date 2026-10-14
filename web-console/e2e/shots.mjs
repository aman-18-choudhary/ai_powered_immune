// Captures docs screenshots against a running console + demo server:
//   node e2e/shots.mjs   (CONSOLE_URL defaults to http://localhost:5173, OUT to ../docs/screenshots)
// Output names: <page>-<viewport>-<theme>.png. Set ONLY=analyst,citizen to limit pages and
// THEMES=dark to limit themes. Images are palette-optimised afterwards by e2e/optimise-shots.py.
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = process.env.CONSOLE_URL ?? "http://localhost:5173";
const OUT = process.env.OUT ?? new URL("../../docs/screenshots/", import.meta.url).pathname;
const THEMES = (process.env.THEMES ?? "dark,light").split(",");
const ONLY = process.env.ONLY ? new Set(process.env.ONLY.split(",")) : null;
mkdirSync(OUT, { recursive: true });
const VP = { "1440x900": { width: 1440, height: 900 }, "1024x768": { width: 1024, height: 768 }, "390x844": { width: 390, height: 844 } };
const want = (k) => !ONLY || ONLY.has(k);

const browser = await chromium.launch();
const problems = [];

for (const theme of THEMES) {
  const ctx = await browser.newContext({ viewport: VP["1440x900"], deviceScaleFactor: 1 });
  await ctx.addInitScript((t) => {
    try {
      localStorage.setItem("ssi.theme", t);
    } catch {
      /* ignore */
    }
  }, theme);
  const page = await ctx.newPage();
  page.on("console", (m) => (m.type() === "error" || m.type() === "warning") && problems.push(`${theme} ${m.type()}: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`${theme} pageerror: ${e.message}`));

  const api = (method, path, body) =>
    page.evaluate(
      async ([m, p, b]) => {
        const tok = JSON.parse(sessionStorage.getItem("ssi.session")).token;
        const r = await fetch("/api" + p, { method: m, headers: { Authorization: "Bearer " + tok, "content-type": "application/json" }, body: b ? JSON.stringify(b) : undefined });
        let json = null;
        try { json = await r.json(); } catch { /* none */ }
        return { status: r.status, json };
      },
      [method, path, body ?? null],
    );
  const waitState = async (want, ms = 90000) => {
    const t0 = Date.now();
    for (;;) {
      const s = (await api("GET", "/demo/state")).json;
      if (want.includes(s.status)) return s;
      if (Date.now() - t0 > ms) throw new Error("state timeout " + s.status);
      await page.waitForTimeout(300);
    }
  };
  const shot = async (name, vp, opts = {}) => {
    await page.setViewportSize(VP[vp]);
    await page.waitForTimeout(opts.settle ?? 1300);
    await page.screenshot({ path: `${OUT}${name}-${vp}-${theme}.png`, fullPage: !!opts.full });
    console.log("saved", `${name}-${vp}-${theme}`);
  };
  const go = async (path) => {
    await page.goto(BASE + path);
    await page.getByRole("navigation", { name: "Primary" }).waitFor();
  };

  await page.goto(BASE + "/");
  await page.getByTestId("role-admin").click();
  await page.waitForTimeout(1500);

  // ---- mid-run state: paused after bank B's hold, antibody applied ---------------------------
  await api("POST", "/demo/start", { seed: 1, speed: 20, mode: "hero", pacing: "cinematic" });
  await api("POST", "/demo/control", { action: "pause" });
  await api("POST", "/demo/control", { action: "seek", t: 1250 });
  await waitState(["paused"]);
  for (const vp of Object.keys(VP)) {
    if (want("player")) { await go("/?mode=simulation"); await shot("player-live", vp); }
    if (want("command")) { await go("/command"); await shot("command", vp, { settle: 2000 }); }
    if (want("analyst")) { await go("/analyst"); await shot("analyst", vp); }
    if (want("evidence")) { await go("/evidence"); await shot("evidence", vp, { settle: 1800 }); }
  }

  // ---- finished run: finale, evidence verified, remaining pages ------------------------------
  await api("POST", "/demo/control", { action: "resume", speed: 20 });
  await waitState(["finished"]);
  if (want("player")) {
    await go("/?mode=simulation");
    await page.getByTestId("verify-offline").click({ timeout: 20000 }).catch(() => undefined);
    await page.getByTestId("verify-status").waitFor({ timeout: 20000 }).catch(() => undefined);
    await shot("player-finale", "1440x900", { settle: 1500 });
    await shot("player-finale", "390x844", { settle: 1200 });
  }
  if (want("evidence")) {
    await go("/evidence");
    await page.getByTestId("build-case").click();
    await page.getByTestId("case-built").waitFor();
    await page.getByTestId("export-package").click();
    await page.getByTestId("package-meta").waitFor();
    await page.getByTestId("verify-run").click();
    await page.getByTestId("verify-status").waitFor();
    await page.getByTestId("verify-result").scrollIntoViewIfNeeded();
    await shot("evidence-verified", "1440x900", { settle: 1500 });
    await shot("evidence-verified", "390x844", { settle: 1000 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByRole("button", { name: /Inspect ledger entry 3,/ }).click();
    await page.getByTestId("inspector").waitFor();
    await shot("evidence-inspector", "1440x900", { settle: 900 });
  }
  if (want("analyst")) {
    await go("/analyst");
    await page.getByTestId("act-confirm").click().catch(() => undefined);
    await page.getByTestId("resolve-note").fill("Payee matches the mule reported on the scam call.").catch(() => undefined);
    await shot("analyst-resolve", "1440x900", { settle: 800 });
  }
  if (want("citizen")) {
    for (const vp of Object.keys(VP)) {
      await go("/citizen");
      await shot("citizen", vp, { settle: 1000 });
      await page.getByTestId("sample-sample1").click();
      await page.getByTestId("check").click();
      await page.getByTestId("verdict").waitFor();
      await shot("citizen-verdict", vp, { settle: 1600, full: vp === "390x844" });
    }
    await go("/citizen");
    await page.getByRole("radio", { name: "தமிழ்" }).click();
    await page.getByTestId("sample-sample1").click();
    await page.getByTestId("check").click();
    await page.getByTestId("verdict").waitFor();
    await shot("citizen-verdict-ta", "390x844", { settle: 1600, full: true });
  }
  if (want("insights")) {
    for (const vp of Object.keys(VP)) {
      await go("/insights");
      await shot("insights", vp, { settle: 1800 });
    }
    await go("/insights");
    await shot("insights-full", "1440x900", { settle: 1800, full: true });
  }
  if (want("about")) {
    for (const vp of Object.keys(VP)) {
      await go("/about");
      await shot("about", vp, { settle: 1200 });
    }
  }
  await ctx.close();
}

await browser.close();
console.log("console errors/warnings:", JSON.stringify(problems, null, 1));

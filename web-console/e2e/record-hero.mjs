// Records the cinematic hero run + page tour as a webm (Playwright video, chromium, 1440x900).
//   CONSOLE_URL=http://127.0.0.1:18095 OUT_DIR=../docs/demo node e2e/record-hero.mjs
// Needs the demo server running with the STATIC console build (see docs/demo/README.md).
// Writes OUT_DIR/hero-run.webm and OUT_DIR/hero-run.marks.json (segment start times in seconds,
// used to build the caption track). No audio is recorded.
import { chromium } from "@playwright/test";
import { mkdirSync, renameSync, writeFileSync, rmSync, readdirSync } from "node:fs";
import { join } from "node:path";

const BASE = process.env.CONSOLE_URL ?? "http://127.0.0.1:18095";
const OUT = process.env.OUT_DIR ?? new URL("../../docs/demo/", import.meta.url).pathname;
const TMP = join(OUT, ".rec-tmp");
rmSync(TMP, { recursive: true, force: true });
mkdirSync(TMP, { recursive: true });

const CARD_CSS = `
html,body{margin:0;height:100%;background:#0B0F17;color:#E8ECF4;font-family:Inter,-apple-system,"Helvetica Neue",Arial,sans-serif}
.w{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:18px;
background:radial-gradient(900px 500px at 50% 30%,rgba(16,185,129,.14),transparent 70%),#0B0F17}
h1{font-size:72px;margin:0;letter-spacing:-1px}h2{font-size:30px;font-weight:500;margin:0;color:#9FB0C8}
.pill{font-size:18px;letter-spacing:3px;text-transform:uppercase;border:1px solid #F59E0B;color:#F59E0B;border-radius:999px;padding:8px 20px}
code{font-family:"SF Mono",Menlo,monospace;font-size:34px;background:#141A26;border:1px solid #2A3446;border-radius:12px;padding:10px 22px;color:#34D399}
p{margin:0;font-size:22px;color:#9FB0C8}`;
const intro = `<style>${CARD_CSS}</style><div class="w"><h1>ScamShield Immune</h1><h2>One bank's detection protects every bank</h2><span class="pill">Simulated data</span><p>Hero run, seed 1, cinematic pacing at 1x. No audio.</p></div>`;
const outro = `<style>${CARD_CSS}</style><div class="w"><h1>Run it yourself</h1><code>make demo</code><p>Repository: &lt;repo URL placeholder&gt;</p><h2>Open http://localhost:8080, choose Officer, press Play</h2><span class="pill">Simulated data</span></div>`;

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  recordVideo: { dir: TMP, size: { width: 1440, height: 900 } },
});
await ctx.addInitScript(() => {
  try {
    localStorage.setItem("ssi.theme", "dark");
  } catch {
    /* ignore */
  }
});
const page = await ctx.newPage();
const t0 = Date.now();
const marks = {};
const mark = (k) => {
  marks[k] = +((Date.now() - t0) / 1000).toFixed(2);
  console.log(k, marks[k]);
};
const problems = [];
page.on("console", (m) => (m.type() === "error" || m.type() === "warning") && problems.push(`${m.type()}: ${m.text()}`));
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));

// A visible cursor dot, since headless video has no OS cursor.
const cursorJs = () => {
  if (!document.documentElement || document.getElementById("__cur")) return;
  const d = document.createElement("div");
  d.id = "__cur";
  d.style.cssText = "position:fixed;z-index:2147483647;width:18px;height:18px;border-radius:50%;background:rgba(52,211,153,.85);border:2px solid #fff;pointer-events:none;left:-40px;top:-40px;transform:translate(-50%,-50%);box-shadow:0 0 12px rgba(52,211,153,.7)";
  document.documentElement.appendChild(d);
  addEventListener("mousemove", (e) => {
    d.style.left = e.clientX + "px";
    d.style.top = e.clientY + "px";
  }, true);
};
await ctx.addInitScript(cursorJs);
let mx = 720, my = 450;
const move = async (x, y, steps = 45) => {
  await page.mouse.move(x, y, { steps });
  mx = x;
  my = y;
};
const moveToEl = async (loc, steps = 45) => {
  const b = await loc.boundingBox();
  if (b) await move(b.x + b.width / 2, b.y + b.height / 2, steps);
};
const clickEl = async (loc) => {
  await moveToEl(loc);
  await page.waitForTimeout(250);
  await loc.click();
};
const wait = (ms) => page.waitForTimeout(ms);
const nav = async (name) => {
  const link = page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name });
  await clickEl(link);
  await page.getByRole("navigation", { name: "Primary" }).waitFor();
};
const wander = async (pts, dwell = 700) => {
  for (const [x, y] of pts) {
    await move(x, y, 50);
    await wait(dwell);
  }
};

// intro card, 3 s
await page.setContent(intro);
mark("intro");
await wait(3200);

// splash -> officer
await page.goto(BASE + "/");
await page.getByTestId("role-officer").waitFor();
mark("splash");
await wait(800);
await clickEl(page.getByTestId("role-officer"));
await page.getByRole("navigation", { name: "Primary" }).waitFor();
await page.waitForLoadState("networkidle");
mark("live_idle");
await wander([[1000, 300], [420, 520]], 700);

// the cinematic player is the SIMULATION tab of Live Interception
await clickEl(page.getByTestId("mode-simulation"));
// reset any previous run, then Play at 1x
const start = page.getByTestId("hero-play").or(page.getByTestId("restart"));
await clickEl(start);
await page.getByRole("region", { name: "Playback" }).getByText("Live", { exact: true }).waitFor();
mark("play");
await move(1180, 700, 60);
await page.getByRole("heading", { name: "Victim B protected" }).waitFor({ timeout: 150_000 });
mark("finale");
await wait(2500);
await clickEl(page.getByTestId("verify-offline"));
await page.getByTestId("verify-status").waitFor({ timeout: 60_000 });
await page.getByTestId("verify-status").getByText("PASS_PINNED").waitFor({ timeout: 60_000 }).catch(() => undefined);
mark("verify");
await wait(4500);

// /command
await nav("Command");
await page.getByTestId("kpi-ledger").waitFor();
mark("command");
await wander([[400, 420], [1000, 450]], 2500);

// /analyst
await nav("Analyst");
mark("analyst");
await wander([[500, 330], [1000, 420]], 2000);

// /evidence: build, export, verify
await nav("Evidence");
mark("evidence");
await wait(600);
const bc = page.getByTestId("build-case");
if (await bc.isVisible().catch(() => false)) {
  await clickEl(bc);
  await page.getByTestId("case-built").waitFor({ timeout: 20_000 }).catch(() => undefined);
  const ex = page.getByTestId("export-package");
  await clickEl(ex);
  await page.getByTestId("package-meta").waitFor({ timeout: 20_000 }).catch(() => undefined);
  const vr = page.getByTestId("verify-run");
  await clickEl(vr);
  await page.getByTestId("verify-status").waitFor({ timeout: 30_000 }).catch(() => undefined);
  await page.getByTestId("verify-result").scrollIntoViewIfNeeded().catch(() => undefined);
}
await wait(2500);

// /citizen
await nav("Citizen");
mark("citizen");
await wait(800);
const SAMPLE =
  "This is Inspector Rao from the CBI cyber cell. A parcel in your name has been seized with illegal items and a digital arrest warrant is issued. Do not disconnect this video call and do not tell anyone. Transfer the amount to the RBI safe account immediately to verify your funds.";
const msg = page.getByTestId("message");
await clickEl(msg);
await msg.fill(SAMPLE); // stands in for a paste
await wait(600);
await clickEl(page.getByTestId("check"));
await page.getByTestId("verdict").waitFor({ timeout: 20_000 });
mark("citizen_en");
await wait(2500);
const ta = page.getByRole("radio", { name: "தமிழ்" });
if (await ta.count()) {
  await ta.scrollIntoViewIfNeeded().catch(() => undefined);
  await clickEl(ta);
  await wait(600);
  const chk = page.getByTestId("check");
  if (await chk.isVisible().catch(() => false)) {
    await clickEl(chk);
    await page.getByTestId("verdict").waitFor({ timeout: 20_000 }).catch(() => undefined);
  }
}
mark("citizen_ta");
await wait(2500);

// /insights
await nav("Insights");
mark("insights");
await wait(1500);
await page.mouse.move(720, 500, { steps: 30 });
for (let i = 0; i < 4; i++) {
  await page.mouse.wheel(0, 380);
  await wait(1000);
}

// outro card
await page.setContent(outro);
mark("outro");
await wait(3500);
mark("end");

const vid = page.video();
await ctx.close();
const src = await vid.path();
renameSync(src, join(OUT, "hero-run.webm"));
writeFileSync(join(OUT, "hero-run.marks.json"), JSON.stringify(marks, null, 2));
rmSync(TMP, { recursive: true, force: true });
await browser.close();
console.log("console errors/warnings:", JSON.stringify(problems, null, 1));
console.log("files:", readdirSync(OUT));

import { existsSync, readFileSync } from "node:fs";
import { AxeBuilder } from "@axe-core/playwright";
import { test as base, expect, type Page } from "@playwright/test";

/**
 * Account features against a REAL demo server (complaints service mounted at /api/complaints) in
 * local sign-in mode. The sign-in code is read from the TEST-ONLY outbox file the server writes
 * when COMPLAINTS_LOCAL_AUTH_OUTBOX is set (refused by the service outside local/demo mode):
 *
 *   COMPLAINTS_AUTH_MODE=local COMPLAINTS_LOCAL_AUTH_OUTBOX=/tmp/x/outbox.jsonl \
 *   INVESTIGATOR_ALLOWLIST=investigator.e2e@example.org ADMIN_BOOTSTRAP_EMAILS=admin.e2e@example.org \
 *   COMPLAINTS_DATA_DIR=/tmp/x/data \
 *   DEMO_STATIC_DIR=web-console/dist PORT=18091 python -m demo_server
 *   COMPLAINTS_OUTBOX=/tmp/x/outbox.jsonl E2E_ADMIN_EMAIL=admin.e2e@example.org \
 *   CONSOLE_URL=http://127.0.0.1:18091 npx playwright test e2e/complaints.spec.ts
 */
const OUTBOX = process.env.COMPLAINTS_OUTBOX ?? "";
const INVESTIGATOR = process.env.E2E_INVESTIGATOR_EMAIL ?? "investigator.e2e@example.org";
// ADMIN_BOOTSTRAP_EMAILS on the server; the admin test is skipped when unset here
const ADMIN = process.env.E2E_ADMIN_EMAIL ?? "";
// a valid 1x1 PNG
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");

/** Fails a test on any console error/warning except the ones it declares as expected. */
const test = base.extend<{ allow: RegExp[] }>({
  allow: [
    async ({ page }, use) => {
      const allowed: RegExp[] = [];
      const msgs: string[] = [];
      page.on("console", (m) => {
        if (m.type() === "error" || m.type() === "warning") msgs.push(m.text());
      });
      page.on("pageerror", (e) => msgs.push(`pageerror: ${e.message}`));
      await use(allowed);
      expect(msgs.filter((m) => !allowed.some((r) => r.test(m)))).toEqual([]);
    },
    { auto: true },
  ],
});

function outboxLines(): { email: string; code: string }[] {
  if (!existsSync(OUTBOX)) return [];
  return readFileSync(OUTBOX, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l) as { email: string; code: string });
}

async function signIn(page: Page, email: string, entry: "citizen" | "investigator") {
  await page.goto("/");
  await page.getByTestId(`entry-${entry}`).click();
  await expect(page.getByTestId("local-dev-note")).toContainText("the code is printed in the server terminal, not emailed");
  const before = outboxLines().length;
  await page.getByTestId("local-email").fill(email);
  await page.getByTestId("local-send").click();
  await expect(page.getByTestId("local-code")).toBeVisible();
  let code = "";
  await expect
    .poll(() => {
      const fresh = outboxLines().slice(before).filter((l) => l.email === email.toLowerCase());
      code = fresh.at(-1)?.code ?? "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await page.getByTestId("local-code").fill(code);
  await page.getByTestId("local-verify").click();
}

const run = Date.now().toString(36);
const citizenEmail = `citizen.${run}@example.org`;
const title = `E2E refund call ${run}`;
let complaintId = "";

test.describe.configure({ mode: "serial" });
test.skip(!OUTBOX, "set COMPLAINTS_OUTBOX to the server's COMPLAINTS_LOCAL_AUTH_OUTBOX file");

test("citizen: local sign-in, mandatory-only complaint with an image, visible in history", async ({ page }) => {
  await signIn(page, citizenEmail, "citizen");
  await expect(page).toHaveURL(/\/me$/);
  await expect(page.getByRole("heading", { name: "My complaints" })).toBeVisible();
  await expect(page.getByTestId("empty-history")).toContainText("Register your first complaint");
  await expect(page.getByTestId("stat-total")).toContainText("0");

  await page.getByTestId("new-complaint").click();
  await expect(page).toHaveURL(/\/me\/complaints\/new$/);
  await page.getByTestId("f-category").selectOption("UPI_FRAUD");
  await page.getByTestId("f-title").fill(title);
  await page.getByTestId("f-description").fill("A caller pretending to be from my bank asked me to approve a UPI collect request.");
  await page.getByTestId("f-evidence").setInputFiles({ name: "screenshot.png", mimeType: "image/png", buffer: PNG });
  const created = page.waitForResponse((r) => r.url().endsWith("/api/complaints/complaints") && r.request().method() === "POST");
  await page.getByTestId("submit-complaint").click();
  const res = await created;
  expect(res.status()).toBe(201);
  const body = (await res.json()) as { id: string; upi_id: unknown; bank_account: unknown };
  expect(body.upi_id).toBeNull();
  expect(body.bank_account).toBeNull();
  complaintId = body.id;

  await expect(page).toHaveURL(new RegExp(`/me/complaints/${complaintId}$`));
  await expect(page.getByTestId("created-banner")).toContainText(complaintId);
  await expect(page.getByTestId("created-banner").getByTestId("status-chip")).toHaveText("Submitted");
  await expect(page.getByTestId("upload-item")).toHaveAttribute("data-status", "stored");
  await expect(page.getByTestId("evidence-item")).toHaveCount(1);
  await expect(page.getByTestId("evidence-item")).toContainText("screenshot.png");
  await expect(page.locator('[data-field="IFSC"]')).toHaveText("Not provided");

  const dl = page.waitForEvent("download");
  await page.getByTestId("evidence-download").click();
  expect((await dl).suggestedFilename()).toBe("screenshot.png");

  await page.getByRole("link", { name: "My complaints" }).first().click();
  const row = page.getByTestId("complaint-row").filter({ hasText: title });
  await expect(row).toBeVisible();
  await expect(row.getByTestId("status-chip")).toHaveText("Submitted");
  await expect(row).toContainText("1 file");
  await expect(page.getByTestId("stat-total")).toContainText("1");
});

// REAL speech-to-text on the server (Phase 6): needs a server with a cached faster-whisper model
// and a speech WAV (the same 16 kHz scam recording the live-audio spec uses).
const AUDIO_WAV = process.env.E2E_AUDIO_WAV ?? process.env.LIVE_AUDIO_WAV ?? "";

test("citizen: an attached call recording is transcribed locally; transcript and timed indicators are shown", async ({ page }) => {
  test.skip(!AUDIO_WAV, "set E2E_AUDIO_WAV (or LIVE_AUDIO_WAV) to a speech WAV and run the server with faster-whisper");
  // its own citizen, so the other tests' complaint counts are unchanged
  await signIn(page, `citizen.audio.${run}@example.org`, "citizen");
  await expect(page).toHaveURL(/\/me$/);
  await page.getByTestId("new-complaint").click();
  await page.getByTestId("f-category").selectOption("DIGITAL_ARREST");
  await page.getByTestId("f-title").fill(`E2E recorded call ${run}`);
  await page.getByTestId("f-description").fill("Someone called me today and I recorded part of the call on my phone.");
  await page.getByTestId("f-evidence").setInputFiles(AUDIO_WAV);
  await page.getByTestId("submit-complaint").click();
  await expect(page.getByTestId("upload-item")).toHaveAttribute("data-status", "stored");
  const status = page.getByTestId("transcription-status");
  await expect(status).toHaveAttribute("data-status", "not_attempted");
  await expect(status).toContainText("transcribed when the complaint analysis runs");
  const ran = page.waitForResponse((r) => /\/analysis$/.test(r.url()) && r.request().method() === "POST");
  await page.getByTestId("run-analysis").click();
  const res = await ran;
  expect(res.status()).toBe(201);
  const a = (await res.json()) as { audio_transcription: { status: string }[]; indicators: { stage: string; evidence: { start_s: number | null } }[] };
  expect(a.audio_transcription[0]!.status).toBe("transcribed");
  expect(a.indicators.some((i) => i.stage === "audio_transcript" && typeof i.evidence.start_s === "number")).toBe(true);
  await expect(status).toHaveAttribute("data-status", "transcribed");
  await page.getByTestId("transcript-toggle").click();
  await expect(page.getByTestId("transcript-segment").first()).toBeVisible();
  await expect(page.getByTestId("transcript")).toContainText(/digital arrest/i);
  await expect(page.getByTestId("audio-offset").first()).toHaveText(/^\d+:\d\d–\d+:\d\d$/);
  await expect(page.getByTestId("audio-file").first()).toHaveAttribute("data-status", "transcribed");
});

test("citizen: check a message (nothing stored), register it pre-filled, run analysis twice with version history", async ({ page }) => {
  expect(complaintId).not.toBe("");
  await signIn(page, citizenEmail, "citizen");
  await expect(page).toHaveURL(/\/me$/);
  await expect(page.getByRole("heading", { name: "My complaints" })).toBeVisible();
  await expect(page.getByTestId("complaint-row").first()).toBeVisible();
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Check" }).click();
  await expect(page.getByRole("heading", { name: "Check a message or link" })).toBeVisible();
  await expect(page).toHaveURL(/\/check$/);
  await page.getByTestId("chk-text").fill("This is officer Sharma from the CBI. Do not disconnect this video call. Transfer your money to the RBI safe account for verification or you will be arrested.");
  await page.getByTestId("chk-url").fill("https://bit.ly/rbi-verify");
  const analyzed = page.waitForResponse((r) => r.url().endsWith("/api/complaints/analyze") && r.request().method() === "POST");
  await page.getByTestId("chk-submit").click();
  const res = await analyzed;
  expect(res.status()).toBe(200);
  const body = (await res.json()) as { persisted: boolean; provenance: { ran: string[] }; indicators: { source: string; method: string }[] };
  expect(body.persisted).toBe(false);
  expect(body.provenance.ran).toContain("text_rules");
  expect(body.indicators.every((i) => ["rule", "ml"].includes(i.source) && i.method)).toBe(true);
  const result = page.getByTestId("check-result");
  await expect(result.getByTestId("nothing-stored")).toContainText("Nothing was stored");
  await expect(result.getByTestId("score-semantics")).toContainText("uncalibrated heuristic score, not a probability");
  await expect(result.getByTestId("indicator-group-rule")).toContainText("call_guard.rules");
  await expect(result.getByTestId("indicator-group-rule")).toContainText("url_heuristics");
  // audio is analysed only as complaint evidence; screenshots: no OCR engine (Phase 6 wording)
  await expect(result.getByTestId("input-capabilities")).toContainText("Audio: not accepted here");
  await expect(result.getByTestId("input-capabilities")).toContainText("attached to a complaint");
  await expect(result.getByTestId("input-capabilities")).toContainText("no OCR engine is installed");
  await expect(result.getByTestId("model-versions")).toContainText("category-v1");
  // nothing was stored: the history still has one complaint
  const stats = await page.evaluate(async () => {
    const t = JSON.parse(sessionStorage.getItem("ssi.account") ?? "{}").token as string;
    const r = await fetch("/api/complaints/me/stats", { headers: { Authorization: `Bearer ${t}` } });
    return (await r.json()) as { total: number };
  });
  expect(stats.total).toBe(1);

  await page.getByTestId("chk-register").click();
  await expect(page).toHaveURL(/\/me\/complaints\/new$/);
  await expect(page.getByTestId("prefill-note")).toBeVisible();
  await expect(page.getByTestId("f-description")).toHaveValue(/RBI safe account/);
  await expect(page.getByTestId("f-category")).toHaveValue("");

  // run the analysis on the stored complaint, twice
  await page.goto(`/me/complaints/${complaintId}`);
  await page.getByTestId("run-analysis").click();
  const panel = page.getByTestId("analysis-panel");
  await expect(panel.getByTestId("analysis-status")).toBeVisible();
  await expect(panel.getByTestId("score-semantics")).toContainText("not a probability");
  await expect(panel.getByTestId("provenance")).toBeAttached();
  await expect(panel.getByTestId("analysis-version")).toHaveCount(1);
  await panel.getByTestId("run-analysis").click();
  await expect(panel.getByTestId("analysis-version")).toHaveCount(2);
  await expect(panel.getByTestId("analysis-version").first()).toContainText("v2");

  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Models" }).click();
  await expect(page.getByTestId("synthetic-warning")).toContainText("synthetic data only");
  await expect(page.getByTestId("model-category-v1").getByTestId("leaky-split")).toContainText("Not the headline");
  await expect(page.getByTestId("model-llm").getByTestId("runtime-status")).toBeVisible();
});

test("allowlisted investigator: queue, open the complaint, change status with a note", async ({ page }) => {
  expect(complaintId).not.toBe("");
  await signIn(page, INVESTIGATOR, "investigator");
  await expect(page).toHaveURL(/\/investigator$/);
  await expect(page.getByRole("heading", { name: "Investigator queue" })).toBeVisible();
  await page.getByTestId("queue-search").fill(title);
  await page.getByTestId("queue-apply").click();
  const row = page.getByTestId("complaint-row").filter({ hasText: title });
  await expect(row).toHaveCount(1);
  await row.click();
  await expect(page).toHaveURL(new RegExp(`/investigator/complaints/${complaintId}$`));
  await expect(page.getByTestId("complaint-title")).toHaveText(title);
  await expect(page.getByTestId("evidence-item")).toHaveCount(1);
  await expect(page.getByTestId("evidence-delete")).toHaveCount(0);

  await page.getByTestId("status-select").selectOption("UNDER_REVIEW");
  await page.getByTestId("status-note").fill("Picked up for review by the e2e investigator.");
  await page.getByTestId("status-submit").click();
  await expect(page.getByTestId("status-done")).toContainText("Under review");
  await expect(page.getByTestId("status-chip").first()).toHaveText("Under review");
  await expect(page.getByTestId("history-row")).toHaveCount(2);
  await expect(page.getByTestId("history-row").first()).toContainText("Picked up for review");
});

test("citizen sees the status alert and marks it read", async ({ page }) => {
  await signIn(page, citizenEmail, "citizen");
  await expect(page).toHaveURL(/\/me$/);
  const alert = page.getByTestId("alert-item").filter({ hasText: /UNDER_REVIEW|under review/i }).first();
  await expect(alert).toHaveAttribute("data-read", "false");
  await alert.getByTestId("alert-read").click();
  await expect(alert).toHaveAttribute("data-read", "true");
  await page.getByTestId("complaint-row").filter({ hasText: title }).click();
  // review started: the owner can no longer remove evidence
  await expect(page.getByTestId("evidence-item")).toHaveCount(1);
  await expect(page.getByTestId("evidence-delete")).toHaveCount(0);
});

test("non-allowlisted investigator entry shows the denial and continues as a citizen", async ({ page, allow }) => {
  // the 403 from POST /auth/entry is expected; Chromium logs every failed response
  allow.push(/Failed to load resource: the server responded with a status of 403/);
  await signIn(page, `not.approved.${run}@example.org`, "investigator");
  await expect(page).toHaveURL(/\/me$/);
  await expect(page.getByTestId("investigator-denied")).toContainText("Investigator access is limited to approved accounts. You can continue as a citizen.");
  await expect(page.getByRole("heading", { name: "My complaints" })).toBeVisible();
  await page.getByRole("button", { name: "Preferences and account" }).click();
  await expect(page.getByTestId("account-role")).toHaveText("Citizen");
  await page.getByTestId("account-sign-out").click();
  await expect(page.getByTestId("account-sign-in")).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("ssi.account"))).toBeNull();
});

test("simulator demo roles stay available and labelled as simulation", async ({ page }) => {
  await page.goto("/");
  const sim = page.getByTestId("simulation-demo");
  await expect(sim).toContainText("Simulation demo");
  await expect(sim).toContainText("No password, no real data.");
  await page.getByTestId("role-officer").click();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Simulation\. About these numbers/ })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Sign in" })).toBeVisible();
});

test("bootstrap admin: change a role with confirmation, last-admin refusal, audit log and honest health", async ({ page, allow }) => {
  test.skip(!ADMIN, "set E2E_ADMIN_EMAIL to an address in the server's ADMIN_BOOTSTRAP_EMAILS");
  allow.push(/Failed to load resource: the server responded with a status of 409/);
  await signIn(page, ADMIN, "citizen");
  await expect(page).toHaveURL(/\/admin$/);
  const row = page.getByTestId("admin-user").filter({ hasText: citizenEmail });
  await expect(row).toHaveCount(1);
  await row.getByTestId("admin-role-select").selectOption("INVESTIGATOR");
  await row.getByTestId("admin-role-change").click();
  await expect(page.getByRole("dialog")).toContainText("will change from Citizen to Investigator");
  await page.getByTestId("admin-confirm").click();
  await expect(row.getByTestId("admin-user-role")).toHaveText("Investigator · via admin");
  await row.getByTestId("admin-role-select").selectOption("CITIZEN");
  await row.getByTestId("admin-role-change").click();
  await page.getByTestId("admin-confirm").click();
  await expect(row.getByTestId("admin-user-role")).toHaveText(/^Citizen/);

  const me = page.getByTestId("admin-user").filter({ hasText: ADMIN });
  await me.getByTestId("admin-role-select").selectOption("CITIZEN");
  await me.getByTestId("admin-role-change").click();
  await page.getByTestId("admin-confirm").click();
  await expect(me.getByTestId("admin-error")).toHaveText("At least one active administrator must remain.");
  await expect(me.getByTestId("admin-user-role")).toHaveText(/^Administrator/);

  await page.getByRole("tab", { name: "Audit log" }).click();
  await expect(page.getByTestId("audit-row").first()).toBeVisible();
  await expect(page.getByTestId("admin-audit")).toContainText("Role change");
  await page.getByRole("tab", { name: "Health" }).click();
  await expect(page.getByTestId("health-row").filter({ hasText: "Db" })).toContainText("Working (ok)");
  // Qdrant runs embedded for Fraud DNA (Phase 8); Neo4j is still not used
  await expect(page.getByTestId("health-row").filter({ hasText: "Qdrant" })).toContainText("Working (local)");
  await expect(page.getByTestId("health-row").filter({ hasText: /^Neo4j/ })).toContainText("Not configured");
});

for (const theme of ["dark", "light"] as const) {
  test(`account pages: axe has no serious/critical violations and nothing overflows at 360 px (${theme})`, async ({ page }) => {
    await page.addInitScript((t) => {
      try {
        localStorage.setItem("ssi.theme", t);
      } catch {
        /* ignore */
      }
    }, theme);
    await page.setViewportSize({ width: 360, height: 780 });
    // sign-in screen first (signed out)
    await page.goto("/");
    await expect(page.getByTestId("account-sign-in")).toBeVisible();
    const found: unknown[] = [];
    const scan = async (where: string) => {
      await page.waitForTimeout(700);
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      for (const v of r.violations.filter((x) => x.impact === "serious" || x.impact === "critical"))
        found.push({ where, id: v.id, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(" ")) });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `horizontal overflow on ${where}`).toBeLessThanOrEqual(0);
    };
    await scan("sign-in");
    await signIn(page, citizenEmail, "citizen");
    await expect(page).toHaveURL(/\/me$/);
    await scan("/me");
    for (const path of ["/me/complaints/new", `/me/complaints/${complaintId}`, "/me/profile", "/check", "/models"]) {
      await page.goto(path);
      await expect(page.locator("main h1").first()).toBeVisible();
      await scan(path);
    }
    // staff pages
    await page.getByRole("button", { name: "Preferences and account" }).click();
    await page.getByTestId("account-sign-out").click();
    await signIn(page, INVESTIGATOR, "investigator");
    await expect(page).toHaveURL(/\/investigator$/);
    await expect(page.getByTestId("complaint-row").first()).toBeVisible();
    await scan("/investigator");
    await page.goto(`/investigator/complaints/${complaintId}`);
    await expect(page.getByTestId("status-update")).toBeVisible();
    await scan("/investigator/complaints/:id");
    if (ADMIN) {
      await page.getByRole("button", { name: "Preferences and account" }).click();
      await page.getByTestId("account-sign-out").click();
      await signIn(page, ADMIN, "citizen");
      await expect(page.getByTestId("admin-user").first()).toBeVisible();
      await scan("/admin");
    }
    expect(found, `${theme} mode violations`).toEqual([]);
  });
}

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
  await expect(page.getByTestId("health-row").filter({ hasText: "Qdrant" })).toContainText("Not configured");
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
    for (const path of ["/me/complaints/new", `/me/complaints/${complaintId}`, "/me/profile"]) {
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

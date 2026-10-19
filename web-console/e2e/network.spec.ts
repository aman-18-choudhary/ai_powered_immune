import { existsSync, readFileSync } from "node:fs";
import { AxeBuilder } from "@axe-core/playwright";
import { test as base, expect, type Page } from "@playwright/test";

/**
 * Phase 9 complaint network against a REAL demo server in local sign-in mode (same setup as
 * complaints.spec.ts; see there for the server command). Every record here is SYNTHETIC and made
 * by this run: two citizens report the same UPI ID, one an unrelated UPI ID, and one reports the
 * same UPI ID after turning case matching off.
 */
const OUTBOX = process.env.COMPLAINTS_OUTBOX ?? "";
const INVESTIGATOR = process.env.E2E_INVESTIGATOR_EMAIL ?? "investigator.e2e@example.org";
const ADMIN = process.env.E2E_ADMIN_EMAIL ?? "";

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
  const before = outboxLines().length;
  await page.getByTestId("local-email").fill(email);
  await page.getByTestId("local-send").click();
  await expect(page.getByTestId("local-code")).toBeVisible();
  let code = "";
  await expect
    .poll(() => {
      code = outboxLines().slice(before).filter((l) => l.email === email.toLowerCase()).at(-1)?.code ?? "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await page.getByTestId("local-code").fill(code);
  await page.getByTestId("local-verify").click();
}

async function report(page: Page, title: string, upi: string): Promise<string> {
  await page.goto("/me/complaints/new");
  await page.getByTestId("f-category").selectOption("UPI_FRAUD");
  await page.getByTestId("f-title").fill(title);
  await page.getByTestId("f-description").fill("SYNTHETIC test complaint: a caller asked me to approve a UPI collect request to receive a refund.");
  await page.getByTestId("f-upi_id").fill(upi);
  const created = page.waitForResponse((r) => r.url().endsWith("/api/complaints/complaints") && r.request().method() === "POST");
  await page.getByTestId("submit-complaint").click();
  const res = await created;
  expect(res.status()).toBe(201);
  return ((await res.json()) as { id: string }).id;
}

const run = Date.now().toString(36);
const SHARED = `synthetic.${run}@okaxis`;
const OTHER = `unrelated.${run}@okicici`;
const ids = { a: "", b: "", c: "", d: "" };
const titles = { a: `E2E net A ${run}`, b: `E2E net B ${run}`, c: `E2E net C ${run}`, d: `E2E net D ${run}` };

/** A citizen sees no Network entry, is refused the page, and gets 403 from the network API. */
async function citizenHasNoNetwork(page: Page) {
  await expect(page.getByRole("link", { name: "Network" })).toHaveCount(0);
  await page.goto("/investigator/network");
  await expect(page.getByTestId("role-mismatch")).toBeVisible();
  const status = await page.evaluate(async (id) => {
    const raw = sessionStorage.getItem("ssi.account");
    const token = raw ? (JSON.parse(raw) as { token: string }).token : "";
    const h = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    const r1 = await fetch(`/api/complaints/complaints/${id}/related`, { headers: h });
    const r2 = await fetch(`/api/complaints/entities/search`, { method: "POST", headers: h, body: JSON.stringify({ type: "upi", value: "x@okaxis" }) });
    const r3 = await fetch(`/api/complaints/graph/clusters`, { headers: h });
    return [r1.status, r2.status, r3.status];
  }, ids.a);
  expect(status).toEqual([403, 403, 403]);
}

test.describe.configure({ mode: "serial" });
test.skip(!OUTBOX, "set COMPLAINTS_OUTBOX to the server's COMPLAINTS_LOCAL_AUTH_OUTBOX file");

// Local sign-in allows 20 code requests per IP per 15 minutes; with the other specs in one run,
// this file keeps to 6 sign-ins (each session is reused for several checks).
test("citizens report: two share a UPI ID, one unrelated, one opted out; citizens get no network", async ({ browser }) => {
  for (const k of ["a", "b", "c", "d"] as const) {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await signIn(page, `net.${k}.${run}@example.org`, "citizen");
    await expect(page).toHaveURL(/\/me$/);
    if (k === "d") {
      await page.goto("/me/profile");
      const sw = page.getByTestId("allow-matching");
      await expect(sw).toHaveAttribute("aria-checked", "true");
      await sw.click();
      await expect(page.getByTestId("profile-status")).toContainText("Case matching turned off.");
      await expect(sw).toHaveAttribute("aria-checked", "false");
    }
    // B types the same UPI ID differently: normalisation must still match it
    ids[k] = await report(page, titles[k], k === "c" ? OTHER : k === "b" ? `  ${SHARED.toUpperCase()} ` : SHARED);
    if (k === "a") await citizenHasNoNetwork(page);
    await ctx.close();
  }
});

test("investigator: linked complaints with evidence, opted-out and unrelated absent, confirm, accessibility", async ({ page }) => {
  await signIn(page, INVESTIGATOR, "investigator");
  await expect(page).toHaveURL(/\/investigator$/);
  await page.goto(`/investigator/complaints/${ids.a}`);
  const panel = page.getByTestId("related-panel");
  await expect(panel.getByTestId("related-disclaimer")).toContainText("leads, not proof");
  const items = panel.getByTestId("related-item");
  await expect(items).toHaveCount(1);
  await expect(items.first()).toContainText(titles.b);
  await expect(items.first().getByTestId("link-badge")).toHaveAttribute("data-link", "observed");
  await expect(items.first().getByTestId("shared-identifier")).toContainText("UPI ID here, UPI ID there");
  await expect(panel).not.toContainText(titles.c);
  await expect(panel).not.toContainText(titles.d);
  // the identifier is masked, never shown in full
  await expect(panel).not.toContainText(SHARED);

  await items.first().getByTestId("link-confirm").click();
  await expect(page.getByRole("dialog")).toContainText("not a finding that anyone committed a crime");
  await page.getByTestId("link-note").fill("SYNTHETIC e2e review");
  const reviewed = page.waitForResponse((r) => r.url().endsWith("/complaint-links/review"));
  await page.getByTestId("link-submit").click();
  expect((await reviewed).status()).toBe(201);
  await expect(items.first().getByTestId("link-badge")).toHaveAttribute("data-link", "confirmed");
  await expect(items.first().getByTestId("related-review")).toContainText("SYNTHETIC e2e review");

  // the unrelated complaint has no related complaints; the opted-out one says linking is off
  await page.goto(`/investigator/complaints/${ids.c}`);
  await expect(page.getByTestId("related-panel")).toContainText("No related complaints");
  await page.goto(`/investigator/complaints/${ids.d}`);
  await expect(page.getByTestId("related-disabled")).toBeVisible();

  // network page: A and B with the confirmed review edge; C and D absent; every edge has evidence
  await page.goto(`/investigator/network?complaint=${ids.a}`);
  await expect(page.getByTestId("network-graph")).toBeVisible();
  const shown = await page.getByTestId("graph-complaint").evaluateAll((els) => els.map((e) => e.getAttribute("data-id")));
  expect(shown.sort()).toEqual([ids.a, ids.b].sort());
  const rows = page.getByTestId("graph-edge-row");
  await expect(rows).toHaveCount(3); // A-UPI, B-UPI, review A-B
  for (const r of await rows.all()) await expect(r.getByTestId("edge-evidence")).not.toBeEmpty();
  await expect(page.locator('[data-testid="graph-edge-row"][data-link="confirmed"]')).toContainText("not a finding");
  await page.getByTestId("depth-2").click();
  await expect(page.getByTestId("depth-2")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("graph-complaint")).toHaveCount(2);

  // identifier search: POST body only; the opted-out complaint with the same UPI ID is not counted
  await page.getByTestId("search-value").fill(SHARED);
  const searched = page.waitForRequest((r) => r.url().endsWith("/entities/search"));
  await page.getByTestId("search-submit").click();
  const req = await searched;
  expect(req.method()).toBe("POST");
  expect(req.url()).not.toContain(run);
  await expect(page.getByTestId("search-result")).toContainText("2 complaints");
  await expect(page.getByTestId("search-item")).toHaveCount(2);
  await expect(page.getByTestId("search-result")).not.toContainText(titles.d);
  expect(page.url()).not.toContain("synthetic");

  // accessibility: axe (no serious/critical) and no horizontal overflow at 360 px, both themes
  await page.setViewportSize({ width: 360, height: 780 });
  const found: unknown[] = [];
  for (const theme of ["dark", "light"] as const) {
    const scan = async (where: string) => {
      await page.waitForTimeout(700);
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      for (const v of r.violations.filter((x) => x.impact === "serious" || x.impact === "critical"))
        found.push({ theme, where, id: v.id, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(" ")) });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `horizontal overflow on ${where} (${theme})`).toBeLessThanOrEqual(0);
    };
    await page.evaluate((t) => localStorage.setItem("ssi.theme", t), theme);
    await page.goto(`/investigator/complaints/${ids.a}`);
    await expect(page.locator("html")).toHaveClass(new RegExp(theme === "dark" ? "dark" : "^(?!.*dark)"));
    await expect(page.getByTestId("related-item")).toHaveCount(1);
    await scan("/investigator/complaints/:id (related panel)");
    await page.goto("/investigator/network");
    await expect(page.getByTestId("cluster").first()).toBeVisible();
    await scan("/investigator/network (no complaint)");
    await page.goto(`/investigator/network?complaint=${ids.a}`);
    await expect(page.getByTestId("network-graph")).toBeVisible();
    await page.getByTestId("graph-entity").first().focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("graph-detail").getByTestId("edge-evidence").first()).toBeVisible();
    await scan("/investigator/network?complaint (selected)");
  }
  expect(found).toEqual([]);
});

test("admin: the link review is in the audit log", async ({ page }) => {
  test.skip(!ADMIN, "set E2E_ADMIN_EMAIL to an ADMIN_BOOTSTRAP_EMAILS address");
  await signIn(page, ADMIN, "citizen");
  await expect(page).toHaveURL(/\/admin$/);
  await page.getByRole("tab", { name: "Audit log" }).click();
  const row = page.getByTestId("audit-row").filter({ hasText: "Complaint link confirmed" }).filter({ hasText: ids.a < ids.b ? ids.a : ids.b });
  await expect(row.first()).toContainText("not a finding of crime");
  await expect(row.first()).not.toContainText(SHARED);
});

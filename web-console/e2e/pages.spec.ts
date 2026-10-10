import { AxeBuilder } from "@axe-core/playwright";
import { api, expect, gotoPath, login, runToFinish, test, token, waitForStatus } from "./helpers";
import { STRINGS } from "../src/lib/i18n/citizen";

test.describe.configure({ mode: "serial" });

const fmt = (v: number | null, d = 1) => (v === null ? "—" : `${(v * 100).toFixed(d)}%`);

test("analyst resolves a hold manually and sees the antibody effect at bank B", async ({ page }) => {
  await login(page, "admin");
  // Start fast, pause and seek to just after bank A's hold: the automatic confirmation (60 simulated s later) is not yet due.
  await api(page, "POST", "/demo/start", { seed: 1, speed: 20, mode: "hero", pacing: "cinematic" });
  await api(page, "POST", "/demo/control", { action: "pause" });
  await api(page, "POST", "/demo/control", { action: "seek", t: 840 });
  await waitForStatus(page, ["paused"]);
  await gotoPath(page, "/analyst");

  const rows = page.getByTestId("hold-row");
  await expect(rows).toHaveCount(1);
  await expect(page.getByTestId("detail-amount")).toHaveText("₹16,000.00");
  await page.getByTestId("act-confirm").click();
  await page.getByTestId("resolve-note").fill("Payee matches the mule reported on the scam call.");
  await page.getByTestId("resolve-submit").click();
  await expect(page.getByText("Mule confirmed", { exact: true }).first()).toBeVisible();

  // The antibody is published at once; resume and let bank B meet the same mule.
  await api(page, "POST", "/demo/control", { action: "resume", speed: 20 });
  await waitForStatus(page, ["finished"]);
  const holds = (await api<{ holds: { bank: string; state: string; reasons: { code: string }[]; resolved_by: string | null }[] }>(page, "GET", "/holds?state=all")).json.holds;
  const a = holds.find((h) => h.bank === "bank_a" && h.state === "blocked");
  expect(a?.resolved_by).toMatch(/analyst|admin/);
  expect(holds.some((h) => h.bank === "bank_b" && h.reasons.some((r) => r.code === "ANTIBODY_MATCH"))).toBe(true);

  // The queue shows bank B's antibody-driven hold with its meaning.
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page.getByTestId("hold-row").first()).toBeVisible();
  const bankB = page.getByTestId("hold-row").filter({ hasText: "Antibody match" }).first();
  await bankB.click();
  await expect(page.getByTestId("reason-row").filter({ hasText: "Another bank confirmed this receiver as a mule" })).toBeVisible();
});

test("evidence: build case, export package, verify PASS_PINNED", async ({ page }) => {
  await login(page, "officer");
  await runToFinish(page);
  await gotoPath(page, "/evidence");
  await expect(page.getByTestId("chain-block").first()).toBeVisible();
  expect(await page.getByTestId("chain-block").count()).toBeGreaterThanOrEqual(12);
  await page.getByTestId("build-case").click();
  await expect(page.getByTestId("case-built")).toBeVisible();
  await page.getByTestId("export-package").click();
  await expect(page.getByTestId("package-meta")).toContainText("ETag");
  await page.getByTestId("verify-run").click();
  await expect(page.getByTestId("verify-status")).toHaveText("PASS_PINNED");
  await expect(page.getByTestId("exit-meaning")).toContainText("Exit 0");
  await expect(page.getByLabel("Verifier output")).toContainText("PASS");
  // redacted entries are shown as sealed blocks
  const sealed = await page.getByRole("button", { name: /payload withheld/ }).count();
  expect(sealed).toBeGreaterThan(0);
  // inspector: payload hash recomputed in the browser matches
  await page.getByRole("button", { name: /Inspect ledger entry 1,/ }).click();
  await expect(page.getByTestId("hash-note")).toContainText("matches payload_hash");
});

test("citizen: three samples in two languages", async ({ page }) => {
  await login(page, "citizen");
  await gotoPath(page, "/citizen");
  const expectLabel: Record<string, string> = { sample1: "likely_scam", sample2: "suspicious", sample3: "safe" };
  for (const lang of ["en", "ta"] as const) {
    await page.getByRole("radio", { name: lang === "en" ? "English" : "தமிழ்" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(STRINGS[lang].title);
    for (const key of ["sample1", "sample2", "sample3"] as const) {
      await page.getByTestId(`sample-${key}`).click();
      await expect(page.getByTestId("message")).not.toHaveValue("");
      await page.getByTestId("check").click();
      const verdict = page.getByTestId("verdict");
      await expect(verdict).toBeVisible();
      await expect(verdict).toHaveAttribute("data-label", expectLabel[key]!);
      await expect(page.getByTestId("verdict-label")).toHaveText(
        STRINGS[lang][expectLabel[key] === "likely_scam" ? "verdictScam" : expectLabel[key] === "safe" ? "verdictSafe" : "verdictSuspicious"],
      );
      if (lang === "ta" && key === "sample1") {
        // reasons and advice are localised by the API: Tamil script, not English
        await expect(verdict).toContainText(/[஀-௿]/);
      }
      expect(page.url()).not.toContain("parcel");
      await page.getByTestId("again").click();
    }
  }
  // nothing from the message is persisted
  const dump = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  expect(dump).not.toContain("Inspector Rao");
});

test("insights: displayed numbers match the API JSON", async ({ page }) => {
  await login(page, "officer");
  const tok = await token(page);
  const res = await page.request.get("/api/benchmark/summary", { headers: { Authorization: `Bearer ${tok}` } });
  expect(res.status()).toBe(200);
  const b = (await res.json()) as {
    headline: Record<string, { value: number | null; ci95: [number, number] | null }> & { n_scam: number; n_benign: number };
    per_rail: Record<string, { fpr: { value: number }; flagged_fpr: { value: number }; n_benign: number }>;
    meta: { git_commit: string; seed: number };
    ablations: Record<string, { victim_transfer_recall: { value: number } }>;
    hero: { model_only: { allow: number }; with_antibody: { hold_verify: number } };
    limitations: string[];
  };
  await gotoPath(page, "/insights");
  await expect(page.getByTestId("gauge-precision")).toContainText(fmt(b.headline.precision!.value, 1));
  await expect(page.getByTestId("gauge-recall")).toContainText(fmt(b.headline.recall!.value, 1));
  await expect(page.getByTestId("gauge-held-benign-rate")).toContainText(fmt(b.headline.fpr!.value, 2));
  await expect(page.getByTestId("gauge-flagged-benign-rate")).toContainText(fmt(b.headline.flagged_fpr!.value, 2));
  const neft = b.per_rail.NEFT!;
  await expect(page.getByTestId("neft-callout")).toContainText(fmt(neft.flagged_fpr.value, 2));
  await expect(page.getByTestId("rail-UPI")).toContainText(b.per_rail.UPI!.n_benign.toLocaleString("en-IN"));
  await expect(page.getByTestId("ablation-table")).toContainText(fmt(b.ablations.no_antibody!.victim_transfer_recall.value, 1));
  await expect(page.getByTestId("hero-panel")).toContainText(`${b.hero.model_only.allow} allowed`);
  await expect(page.getByTestId("meta")).toContainText(b.meta.git_commit);
  await expect(page.getByTestId("limitations")).toContainText(b.limitations[0]!.slice(0, 40));
});

test("command palette jumps to a page", async ({ page }) => {
  await login(page, "officer");
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("insights");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/insights$/);
  await expect(page.getByRole("heading", { name: "Insights", level: 1 })).toBeVisible();
});

test("unknown route shows the 404 page", async ({ page }) => {
  await login(page, "officer");
  await gotoPath(page, "/no-such-page");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
});

const PAGES = ["/", "/command", "/analyst", "/evidence", "/citizen", "/insights", "/about"];
for (const theme of ["dark", "light"] as const) {
  test(`axe: no serious or critical violations on every page (${theme})`, async ({ page }) => {
    await page.addInitScript((t) => {
      try {
        localStorage.setItem("ssi.theme", t);
      } catch {
        /* ignore */
      }
    }, theme);
    await login(page, "admin");
    await runToFinish(page);
    const found: unknown[] = [];
    for (const path of PAGES) {
      await gotoPath(page, path);
      await page.waitForTimeout(900);
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      for (const v of r.violations.filter((x) => x.impact === "serious" || x.impact === "critical"))
        found.push({
          path, id: v.id, impact: v.impact,
          nodes: v.nodes.slice(0, 3).map((n) => n.target.join(" ") + " :: " + (n.any[0]?.message ?? n.failureSummary ?? "").slice(0, 110)),
        });
    }
    expect(found, `${theme} mode violations`).toEqual([]);
  });
}

test("evidence page states that the ledger clock is simulated", async ({ page }) => {
  await login(page, "officer");
  const r = await api<{ simulated_clock: boolean }>(page, "GET", "/ledger/head");
  expect(r.json.simulated_clock).toBe(true);
  await gotoPath(page, "/evidence");
  await expect(page.getByTestId("clock-note")).toContainText("Simulated clock");
});

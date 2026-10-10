import { test as base, expect, type Page } from "@playwright/test";

export type RoleName = "officer" | "analyst" | "citizen" | "admin";

/** Every test fails on any console error or warning, and on uncaught page errors. */
export const test = base.extend<{ guard: string[] }>({
  guard: [
    async ({ page }, use) => {
      const msgs: string[] = [];
      page.on("console", (m) => {
        if (m.type() === "error" || m.type() === "warning") msgs.push(`${m.type()}: ${m.text()}`);
      });
      page.on("pageerror", (e) => msgs.push(`pageerror: ${e.message}`));
      const failed: string[] = [];
      page.on("response", (r) => {
        if (r.status() >= 400) failed.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`);
      });
      await use(msgs);
      expect(msgs, `console errors or warnings (failed requests: ${failed.join("; ") || "none"})`).toEqual([]);
    },
    { auto: true },
  ],
});
export { expect };

export async function login(page: Page, role: RoleName) {
  await page.goto("/");
  await page.getByTestId(`role-${role}`).click();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  // let the finale of an already finished run prepare its package before a test restarts the run
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(600);
}

export async function token(page: Page): Promise<string> {
  return page.evaluate(() => {
    const raw = sessionStorage.getItem("ssi.session");
    return raw ? (JSON.parse(raw) as { token: string }).token : "";
  });
}

export async function api<T = unknown>(page: Page, method: "GET" | "POST", path: string, body?: unknown): Promise<{ status: number; json: T }> {
  const tok = await token(page);
  return page.evaluate(
    async ([m, p, b, t]) => {
      const r = await fetch("/api" + p, {
        method: m as string,
        headers: { Authorization: "Bearer " + t, "content-type": "application/json" },
        body: b === null ? undefined : JSON.stringify(b),
      });
      let json: unknown = null;
      try {
        json = await r.json();
      } catch {
        /* no body */
      }
      return { status: r.status, json };
    },
    [method, path, body === undefined ? null : body, tok] as const,
  ) as Promise<{ status: number; json: T }>;
}

interface State {
  status: string;
  t: number;
  step: string | null;
}

export async function waitForStatus(page: Page, want: string[], timeoutMs = 90_000) {
  const t0 = Date.now();
  for (;;) {
    const s = (await api<State>(page, "GET", "/demo/state")).json;
    if (want.includes(s.status)) return s;
    if (Date.now() - t0 > timeoutMs) throw new Error(`run did not reach ${want.join("/")}: ${s.status}`);
    await page.waitForTimeout(400);
  }
}

/** Run the hero scenario to completion quickly (cinematic pacing at 20x is about 4 s). */
export async function runToFinish(page: Page, speed = 20) {
  const r = await api(page, "POST", "/demo/start", { seed: 1, speed, mode: "hero", pacing: "cinematic" });
  expect(r.status).toBe(200);
  await waitForStatus(page, ["finished"]);
}

export async function gotoPath(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
}

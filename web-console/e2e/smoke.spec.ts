import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

test("golden path: officer -> play -> call alert -> hold -> antibody applied -> blocked -> verify PASS_PINNED", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto("/");
  await page.getByTestId("role-officer").click();
  // The page opens in the Live microphone mode; the cinematic player is the SIMULATION tab.
  await page.getByTestId("mode-simulation").click();
  await expect(page).toHaveURL(/\?mode=simulation$/);
  // A previous run may already exist on the server: the player then shows Restart instead of the hero.
  const start = page.getByTestId("hero-play").or(page.getByTestId("restart"));
  await expect(start).toBeEnabled();
  await start.click();
  // The server may replay a finished run's snapshot first: wait for the fresh run to be live.
  await expect(page.getByRole("region", { name: "Playback" }).getByText("Live", { exact: true })).toBeVisible();

  await expect(page.getByText("Likely scam call")).toBeVisible();
  await expect(page.getByText("Held for verification").first()).toBeVisible();
  await expect(page.getByText("Antibody applied").first()).toBeVisible();
  await expect(page.getByText("Blocked by shared antibody")).toBeVisible();

  await expect(page.getByRole("heading", { name: "Victim B protected" })).toBeVisible({ timeout: 150_000 });
  await page.getByTestId("verify-offline").click();
  await expect(page.getByTestId("verify-status")).toHaveText("PASS_PINNED", { timeout: 60_000 });
  await expect(page.getByLabel("Verifier output")).toContainText("PASS");
  expect(errors).toEqual([]);
});

test("command centre renders KPIs and the ring takedown simulator", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("role-officer").click();
  await page.getByRole("link", { name: "Command" }).click();
  await expect(page.getByRole("heading", { name: "Command Centre" })).toBeVisible();
  await expect(page.getByTestId("kpi-ledger")).toBeVisible();
  await expect(page.getByRole("group", { name: /Ring graph/ })).toBeVisible();
});

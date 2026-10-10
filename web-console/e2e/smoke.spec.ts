import { expect, test } from "@playwright/test";

test("golden path: officer -> play -> call alert -> hold -> antibody applied -> blocked -> verify PASS_PINNED", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto("/");
  await page.getByTestId("role-officer").click();
  await expect(page.getByTestId("hero-play")).toBeEnabled();
  await page.getByTestId("hero-play").click();

  await expect(page.getByText("Likely scam call")).toBeVisible();
  await expect(page.getByText("Held for verification").first()).toBeVisible();
  await expect(page.getByText("Antibody applied").first()).toBeVisible();
  await expect(page.getByText("Blocked by shared antibody")).toBeVisible();

  await expect(page.getByRole("heading", { name: "Victim B protected" })).toBeVisible();
  await page.getByTestId("verify-offline").click();
  await expect(page.getByTestId("verify-status")).toHaveText("PASS_PINNED");
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

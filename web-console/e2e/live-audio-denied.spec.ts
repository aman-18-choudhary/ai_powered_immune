import { expect, login, test } from "./helpers";

// No fake-UI flag: Chromium is told to deny the permission prompt (as a user clicking Block).
// full Chromium: the headless shell reports NotSupportedError for getUserMedia instead
test.use({ channel: "chromium", launchOptions: { args: ["--use-fake-device-for-media-stream", "--deny-permission-prompts"] } });

test.describe("live microphone with permission denied", () => {

  test("denied permission shows how to re-enable and starts no session", async ({ page }) => {
    let sessions = 0;
    page.on("request", (r) => {
      if (r.url().endsWith("/api/live-audio/sessions") && r.method() === "POST") sessions++;
    });
    await login(page, "citizen");
    await page.getByTestId("consent-check").check();
    await page.getByTestId("live-start").click();
    await expect(page.getByTestId("mic-error")).toContainText("permission was denied");
    await expect(page.getByTestId("mic-error")).toContainText("address bar");
    await expect(page.getByTestId("mic-indicator")).toContainText("Microphone off");
    expect(sessions).toBe(0);
  });
});

import { readFileSync } from "node:fs";
import { expect, login, test } from "./helpers";

/**
 * Live microphone mode against a real demo server with real local Whisper (faster-whisper).
 * Chromium's fake microphone plays a synthetic speech file (macOS `say` + ffmpeg, see
 * services/live_audio/README.md):
 *
 *   LIVE_AUDIO_WAV=/abs/path/scam-16k.wav CONSOLE_URL=http://127.0.0.1:19401 npx playwright test e2e/live-audio.spec.ts
 *
 * The server must run with LIVE_STT_ENGINE=faster_whisper and a cached model.
 *
 * LIVE_E2E_DEVICE_SHIM=1 replaces ONLY the capture device: getUserMedia returns a MediaStream
 * that plays the same WAV through a MediaStreamAudioDestinationNode. Use it where Chromium's
 * fake capture device is unusable (in the sandboxed environment this was built in, getUserMedia
 * never resolved, even for the plain fake beep device). Everything after the device is the real
 * path: the console's AudioContext + AudioWorklet capture, resampling, WAV chunks, uploads, the
 * server's Whisper transcription and call-guard analysis.
 */
const WAV = process.env.LIVE_AUDIO_WAV;
const SHIM = process.env.LIVE_E2E_DEVICE_SHIM === "1";

test.use({
  channel: "chromium",
  launchOptions: {
    args: SHIM
      ? ["--autoplay-policy=no-user-gesture-required"]
      : ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${WAV ?? ""}`],
  },
});

test.beforeEach(async ({ page }) => {
  if (!SHIM || !WAV) return;
  const b64 = readFileSync(WAV).toString("base64");
  await page.addInitScript((data: string) => {
    const md = navigator.mediaDevices;
    md.getUserMedia = async (c?: MediaStreamConstraints) => {
      if (!c?.audio) throw new DOMException("audio only", "NotSupportedError");
      const bytes = Uint8Array.from(atob(data), (ch) => ch.charCodeAt(0));
      const ctx = new AudioContext();
      const buf = await ctx.decodeAudioData(bytes.buffer);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.loop = true;
      const dest = ctx.createMediaStreamDestination();
      src.connect(dest);
      src.start();
      return dest.stream;
    };
  }, b64);
});

test.describe("live microphone with a fake audio device", () => {
  test.skip(!WAV, "set LIVE_AUDIO_WAV to a 16 kHz mono speech WAV");

  test("consent -> start -> real transcript and rising indicators -> stop", async ({ page }) => {
    await login(page, "citizen");
    await expect(page.getByTestId("mode-live")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("services")).toContainText("faster_whisper");
    await expect(page.getByTestId("live-start")).toBeDisabled();
    await page.getByTestId("consent-check").check();
    const created = page.waitForResponse((r) => r.url().endsWith("/api/live-audio/sessions") && r.request().method() === "POST");
    await page.getByTestId("live-start").click();
    expect((await created).status()).toBe(201);
    await expect(page.getByTestId("mic-indicator")).toContainText("Microphone on");
    await expect(page.getByTestId("transcript")).toContainText(/digital arrest/i, { timeout: 90_000 });
    await expect(page.getByTestId("indicator-DIGITAL_ARREST_PHRASE")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("indicator-DIGITAL_ARREST_PHRASE")).toContainText("detected, not confirmed");
    await expect(page.getByTestId("live-level")).toHaveText(/High|Critical/, { timeout: 30_000 });
    await expect(page.getByTestId("warnings")).toBeVisible();
    await expect(page.getByTestId("actions")).toContainText("digital arrest");
    await expect(page.getByTestId("live-latency")).toBeVisible();
    // no simulated banking content in this mode
    await expect(page.getByText(/Bank A|mule|Blocked by shared antibody/)).toHaveCount(0);

    await page.getByTestId("live-pause").click();
    await expect(page.getByTestId("mic-indicator")).toContainText("Paused");
    await page.getByTestId("live-resume").click();
    await expect(page.getByTestId("mic-indicator")).toContainText("Microphone on");

    const ended = page.waitForResponse((r) => /\/api\/live-audio\/sessions\/[^/]+\/end$/.test(r.url()));
    await page.getByTestId("live-stop").click();
    expect((await ended).status()).toBe(200);
    await expect(page.getByTestId("mic-indicator")).toContainText("Microphone off");
    // the transcript stays only on this page; the server session is gone
    await expect(page.getByTestId("transcript")).toContainText(/digital arrest/i);
  });
});

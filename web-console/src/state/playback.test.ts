import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/endpoints", () => ({
  endpoints: { demoStart: vi.fn(), demoState: vi.fn(async () => ({ status: "running", t: 0, speed: 1, duration: 10, run_id: "r", step: null })), demoControl: vi.fn() },
}));
import { endpoints } from "@/lib/api/endpoints";
import { ApiError } from "@/lib/api/client";
import { playback, uiToServerSpeed, serverToUiSpeed, isCinematic } from "./playback";

beforeEach(() => vi.clearAllMocks());

describe("playback start", () => {
  it("requests cinematic pacing first, with 1x = server 1x", async () => {
    vi.mocked(endpoints.demoStart).mockResolvedValue({ run_id: "r" });
    await playback.start();
    expect(endpoints.demoStart).toHaveBeenCalledWith({ seed: 1, speed: 1, mode: "hero", pacing: "cinematic" });
    expect(isCinematic()).toBe(true);
    expect(uiToServerSpeed(2)).toBe(2);
    expect(serverToUiSpeed(5)).toBe(5);
  });

  it("falls back to real pacing when the server rejects the field", async () => {
    vi.mocked(endpoints.demoStart)
      .mockRejectedValueOnce(new ApiError(422, "invalid_request", "x"))
      .mockResolvedValueOnce({ run_id: "r" });
    await playback.start();
    expect(endpoints.demoStart).toHaveBeenLastCalledWith({ seed: 1, speed: 20, mode: "hero" });
    expect(isCinematic()).toBe(false);
    expect(uiToServerSpeed(1)).toBe(4);
    expect(uiToServerSpeed(50)).toBe(20);
  });
});

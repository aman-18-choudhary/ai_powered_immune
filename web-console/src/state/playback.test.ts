import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/endpoints", () => ({
  endpoints: { demoStart: vi.fn(), demoState: vi.fn(async () => ({ status: "running", t: 0, speed: 1, duration: 10, run_id: "r", step: null })), demoControl: vi.fn() },
}));
import { endpoints } from "@/lib/api/endpoints";
import { ApiError } from "@/lib/api/client";
import { playback, uiToServerSpeed, serverToUiSpeed, isCinematic } from "./playback";

import { useRunStore } from "./runStore";

beforeEach(() => {
  vi.clearAllMocks();
  useRunStore.getState().reset();
});

describe("playback seek", () => {
  it("seeks by wall seconds on the pacing timeline when the run has one", async () => {
    vi.mocked(endpoints.demoControl).mockResolvedValue({ status: "paused", t: 0, speed: 1, duration: 10, run_id: "r", step: null } as never);
    useRunStore.setState((s) => ({ run: { ...s.run, wallDuration: 71.1 } }));
    await playback.seek(30);
    expect(endpoints.demoControl).toHaveBeenCalledWith({ action: "seek", wall: 30 });
  });
  it("falls back to simulated t without a pacing timeline", async () => {
    vi.mocked(endpoints.demoControl).mockResolvedValue({ status: "paused", t: 0, speed: 1, duration: 10, run_id: "r", step: null } as never);
    await playback.seek(900);
    expect(endpoints.demoControl).toHaveBeenCalledWith({ action: "seek", t: 900 });
  });
});

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

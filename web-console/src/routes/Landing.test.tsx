import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import { renderApp } from "@/test-utils";
import { heroEvents } from "@/mocks/fixture";

vi.mock("@/lib/api/endpoints", () => ({
  endpoints: {
    createCase: vi.fn(async () => ({})),
    packageBlob: vi.fn(async () => new Blob([new Uint8Array(100)])),
    verify: vi.fn(),
    demoStart: vi.fn(async () => ({ run_id: "x" })),
    demoState: vi.fn(),
    demoControl: vi.fn(),
    resolveHold: vi.fn(),
  },
}));

vi.mock("@/lib/liveAudio/api", () => ({
  liveAudioApi: {
    capabilities: vi.fn(async () => {
      throw new Error("not used");
    }),
    createSession: vi.fn(),
    sendChunk: vi.fn(),
    endSession: vi.fn(),
  },
}));

import Landing from "./Landing";

const SIM = "/?mode=simulation";
import { useRunStore } from "@/state/runStore";
import { useSession } from "@/state/session";
import { endpoints } from "@/lib/api/endpoints";

beforeEach(() => {
  useRunStore.getState().reset();
  useRunStore.getState().setConn("open");
  useSession.setState({ token: "t", role: "officer", sub: "s" });
  vi.clearAllMocks();
});

describe("Landing / mode switch", () => {
  it("defaults to the live microphone mode; the simulation is a labelled tab", () => {
    renderApp(<Landing />);
    expect(screen.getByTestId("mode-live")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("mode-simulation")).toHaveTextContent("Simulated scenario (demo)");
    expect(screen.getByTestId("mode-simulation")).toHaveTextContent("SIMULATION");
    expect(screen.getByTestId("consent-card")).toBeInTheDocument();
    expect(screen.queryByTestId("hero-play")).not.toBeInTheDocument();
    // no simulated bank / mule content in live mode
    expect(screen.queryByText(/Bank A/)).not.toBeInTheDocument();
    expect(screen.queryByText(/mule/i)).not.toBeInTheDocument();
  });
});

describe("Landing / simulated interception (SIMULATION tab)", () => {
  it("idle: hero with Play, headline and honest label", () => {
    renderApp(<Landing />, SIM);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("protects every bank");
    expect(screen.getByTestId("hero-play")).toBeEnabled();
    expect(screen.getByText(/only the shared threat memory saves victim B/)).toBeInTheDocument();
  });

  it("starts a run through the API on Play", async () => {
    renderApp(<Landing />, SIM);
    await act(async () => {
      screen.getByTestId("hero-play").click();
    });
    expect(endpoints.demoStart).toHaveBeenCalledWith(expect.objectContaining({ seed: 1, mode: "hero", pacing: "cinematic" }));
  });

  it("read-only roles cannot start and are told why", () => {
    useSession.setState({ role: "citizen" });
    renderApp(<Landing />, SIM);
    expect(screen.getByTestId("hero-play")).toBeDisabled();
    expect(screen.getByText(/Sign in as Officer or Admin/)).toBeInTheDocument();
  });

  it("renders the recorded finished run: panes, finale card and verify controls", async () => {
    renderApp(<Landing />, SIM);
    act(() => {
      useRunStore.getState().apply(heroEvents());
    });
    expect(await screen.findByRole("heading", { name: "Victim B protected" })).toBeInTheDocument();
    expect(screen.getByText("Bank A · HDFC console")).toBeInTheDocument();
    expect(screen.getByText("Bank B · AXIS console")).toBeInTheDocument();
    expect(screen.getAllByText(/ANTIBODY_MATCH/).length).toBeGreaterThan(0);
    const fin = heroEvents().find((e) => e.kind === "scenario.finished");
    const ms = fin && fin.kind === "scenario.finished" ? fin.latency_ms : null;
    expect(ms).not.toBeNull();
    expect(screen.getByTestId("finale-region")).toHaveTextContent(`Victim B protected in ${(ms as number).toFixed(1)} milliseconds`);
    // the scrubber works in the compressed timeline: its label is in time at 1x, with the sim clock beside it
    expect(screen.getByTestId("sim-clock")).toHaveTextContent("43:29");
    expect(screen.getByTestId("honest-strip")).toBeInTheDocument();
    expect(screen.getByTestId("verify-offline")).toBeEnabled();
  });
});

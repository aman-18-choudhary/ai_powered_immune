import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderApp } from "@/test-utils";
import type { CaptureHandlers } from "@/lib/liveAudio/capture";
import type { ChunkResult } from "@/lib/liveAudio/api";

const fake = {
  handlers: null as CaptureHandlers | null,
  paused: false,
  tracks: 0,
  pause: vi.fn(() => {
    fake.paused = true;
  }),
  resume: vi.fn(() => {
    fake.paused = false;
  }),
  stop: vi.fn(async () => {
    fake.tracks = 0;
  }),
};

vi.mock("@/lib/liveAudio/capture", async (orig) => {
  const real = await orig<typeof import("@/lib/liveAudio/capture")>();
  return {
    ...real,
    openCapture: vi.fn(async (h: CaptureHandlers) => {
      fake.handlers = h;
      fake.tracks = 1;
      fake.paused = false;
      return {
        sampleRate: 48000,
        mode: "worklet" as const,
        pause: fake.pause,
        resume: fake.resume,
        stop: fake.stop,
        activeTracks: () => fake.tracks,
      };
    }),
  };
});

const CAPS = {
  mode: "incremental_chunked",
  mode_note: "",
  chunk_ms: 1000,
  max_chunk_ms: 15000,
  max_chunk_bytes: 524288,
  stt: { engine: "faster_whisper", model: "base", status: "ready", reason: null, languages: ["auto", "hi", "en"] },
  scorer: { status: "ready", model_version: "clf-v1", fallback_mode: false },
  llm: { status: "not_configured", model: null },
  limits: { sessions_per_user: 2, chunks_per_minute: 40, session_idle_ttl_s: 600 },
};

function result(seq: number, over: Partial<ChunkResult> = {}): ChunkResult {
  return {
    seq,
    duplicate: false,
    late: false,
    segments: [],
    indicators: [],
    risk: {
      score: null, level: null, status: "not_assessed", stale: false, assessed_chunks: 0, history_tail: [],
      score_semantics: "uncalibrated_heuristic", statement: "Indicators detected - not a confirmation of fraud.", model_version: null,
    },
    warnings: [],
    actions: [],
    explanation: { rules: "", llm: null, llm_status: "not_configured", llm_at_s: null },
    status: { stt: "ok", analysis: "no_speech", llm: "not_configured" },
    errors: [],
    gaps: [],
    timing: { total_ms: 900 },
    ...over,
  };
}

const SCAM: ChunkResult = result(0, {
  segments: [{ id: 1, seq: 0, start_s: 0.2, end_s: 0.9, text: "You are under digital arrest.", language: "en", indicator_codes: ["DIGITAL_ARREST_PHRASE"], confidence: -0.2 }],
  indicators: [
    {
      code: "DIGITAL_ARREST_PHRASE", label: "Says you are under 'digital arrest'", source: "call_guard", weight: 0.69,
      segment_ids: [1], evidence_excerpts: ["under digital arrest"], first_at_s: 0.2, last_at_s: 0.9, status: "detected_not_confirmed",
    },
  ],
  risk: {
    score: 69, level: "HIGH", status: "assessed", stale: false, assessed_chunks: 1, history_tail: [{ seq: 0, at_s: 1, score: 69, level: "HIGH" }],
    score_semantics: "uncalibrated_heuristic", statement: "Indicators detected - not a confirmation of fraud.", model_version: "clf-v1",
  },
  warnings: [{ id: 1, seq: 0, at_s: 1, wall: "2026-10-14T10:00:00+00:00", level: "HIGH", previous_level: null, score: 69, codes: ["DIGITAL_ARREST_PHRASE"], message: "Strong scam indicators were heard." }],
  actions: [{ id: "no_digital_arrest", text: "There is no such thing as a 'digital arrest'.", source: "static_guidance", priority: 0 }],
  explanation: { rules: "Risk HIGH (score 69/100).", llm: null, llm_status: "not_configured", llm_at_s: null },
  status: { stt: "ok", analysis: "ok", llm: "not_configured" },
});

vi.mock("@/lib/liveAudio/api", () => ({
  liveAudioApi: {
    capabilities: vi.fn(),
    createSession: vi.fn(),
    sendChunk: vi.fn(),
    endSession: vi.fn(async () => ({ ended: true })),
  },
}));

import { LiveMonitor } from "./LiveMonitor";
import { liveAudioApi } from "@/lib/liveAudio/api";
import { openCapture, MicError } from "@/lib/liveAudio/capture";
import { useLiveAudio, __setSleep } from "@/state/liveAudio";
import { ApiError } from "@/lib/api/client";

const api = vi.mocked(liveAudioApi);

beforeEach(() => {
  vi.clearAllMocks();
  __setSleep(async () => undefined);
  useLiveAudio.setState({ phase: "idle" });
  useLiveAudio.getState().clear();
  useLiveAudio.setState({ language: "auto", caps: null, capsError: null });
  api.capabilities.mockResolvedValue(CAPS as never);
  api.createSession.mockResolvedValue({ session_id: "s".repeat(24), language: "auto", chunk_ms: 1000, max_chunk_bytes: 524288 });
  let n = 0;
  api.sendChunk.mockImplementation(async () => result(n++));
});

async function startMonitoring() {
  renderApp(<LiveMonitor />);
  fireEvent.click(screen.getByTestId("consent-check"));
  await act(async () => {
    fireEvent.click(screen.getByTestId("live-start"));
  });
}

function speak(seconds: number) {
  act(() => {
    // +1 frame: the 48 -> 16 kHz resampler keeps one input sample back for the next call
    if (!fake.paused) fake.handlers?.onFrames(new Float32Array(Math.round(48000 * seconds) + 3).fill(0.1), 48000);
  });
}

describe("LiveMonitor", () => {
  it("needs explicit consent before Start; no microphone access before the click", async () => {
    renderApp(<LiveMonitor />);
    expect(screen.getByTestId("live-start")).toBeDisabled();
    expect(openCapture).not.toHaveBeenCalled();
    expect(screen.getByTestId("consent-card")).toHaveTextContent("transcribed locally by Whisper");
    expect(screen.getByTestId("consent-card")).toHaveTextContent("speakerphone");
    expect(screen.getByTestId("consent-card")).toHaveTextContent("not proof of fraud");
  });

  it("permission denied: friendly error and no session started", async () => {
    vi.mocked(openCapture).mockRejectedValueOnce(new MicError("denied"));
    await startMonitoring();
    expect(screen.getByTestId("mic-error")).toHaveTextContent(/permission was denied.*address bar/);
    expect(api.createSession).not.toHaveBeenCalled();
    expect(useLiveAudio.getState().phase).toBe("idle");
  });

  it.each([
    ["no_device", /No microphone was found/],
    ["in_use", /in use by another application/],
    ["insecure", /https or localhost/],
  ] as const)("mic error %s is explained", async (kind, msg) => {
    vi.mocked(openCapture).mockRejectedValueOnce(new MicError(kind));
    await startMonitoring();
    expect(screen.getByTestId("mic-error")).toHaveTextContent(msg);
    expect(api.createSession).not.toHaveBeenCalled();
  });

  it("granted: starts a session, uploads ordered WAV chunks with seq numbers, renders results", async () => {
    api.sendChunk.mockResolvedValueOnce(SCAM).mockResolvedValue({ ...SCAM, seq: 1, segments: [], warnings: [] });
    await startMonitoring();
    expect(api.createSession).toHaveBeenCalledWith("auto");
    expect(screen.getByTestId("mic-indicator")).toHaveTextContent("Microphone on");
    speak(2.1);
    await waitFor(() => expect(api.sendChunk).toHaveBeenCalledTimes(2));
    const [sid, seq0, start0, wav0] = api.sendChunk.mock.calls[0]!;
    expect(sid).toBe("s".repeat(24));
    expect([seq0, start0]).toEqual([0, 0]);
    expect(String.fromCharCode(...wav0.slice(0, 4))).toBe("RIFF");
    expect(wav0.length).toBe(44 + 16000 * 2);
    expect(api.sendChunk.mock.calls[1]!.slice(1, 3)).toEqual([1, 1000]);
    expect(await screen.findByText("You are under digital arrest.")).toBeInTheDocument();
    expect(screen.getByTestId("live-score")).toHaveTextContent("69");
    expect(screen.getByTestId("live-level")).toHaveTextContent("High");
    const ind = screen.getByTestId("indicator-DIGITAL_ARREST_PHRASE");
    expect(ind).toHaveTextContent("detected, not confirmed");
    expect(ind).toHaveTextContent("under digital arrest");
    expect(ind.querySelector('a[href="#seg-1"]')).not.toBeNull();
    expect(screen.getByTestId("warnings")).toHaveTextContent("Strong scam indicators");
    expect(screen.getByTestId("live-warning-region")).toHaveTextContent("High");
    expect(screen.getByTestId("actions")).toHaveTextContent("digital arrest");
    expect(screen.getByText("Indicators detected - not a confirmation of fraud.")).toBeInTheDocument();
  });

  it("pause stops sending; resume restarts it", async () => {
    await startMonitoring();
    speak(1.0);
    await waitFor(() => expect(api.sendChunk).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId("live-pause"));
    expect(fake.pause).toHaveBeenCalled();
    expect(screen.getByTestId("mic-indicator")).toHaveTextContent("Paused");
    speak(3.0);
    expect(api.sendChunk).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("live-resume"));
    expect(fake.resume).toHaveBeenCalled();
    speak(1.0);
    await waitFor(() => expect(api.sendChunk).toHaveBeenCalledTimes(2));
  });

  it("stop stops all tracks, sends the final partial chunk and ends the session", async () => {
    await startMonitoring();
    speak(1.5);
    await waitFor(() => expect(api.sendChunk).toHaveBeenCalledTimes(1));
    await act(async () => {
      fireEvent.click(screen.getByTestId("live-stop"));
    });
    await waitFor(() => expect(useLiveAudio.getState().phase).toBe("ended"));
    expect(fake.stop).toHaveBeenCalled();
    expect(fake.tracks).toBe(0);
    expect(api.sendChunk).toHaveBeenCalledTimes(2); // 1.0 s + the 0.5 s remainder
    expect(api.endSession).toHaveBeenCalledWith("s".repeat(24));
    expect(screen.getByTestId("mic-indicator")).toHaveTextContent("Microphone off");
  });

  it("retries transient upload failures in order", async () => {
    api.sendChunk
      .mockRejectedValueOnce(new ApiError(0, "network", "x"))
      .mockRejectedValueOnce(new ApiError(503, "server", "x"))
      .mockResolvedValue(result(0));
    await startMonitoring();
    speak(1.0);
    await waitFor(() => expect(api.sendChunk).toHaveBeenCalledTimes(3));
    expect(api.sendChunk.mock.calls.map((c) => c[1])).toEqual([0, 0, 0]);
    expect(useLiveAudio.getState().errors).toEqual([]);
  });

  it("session creation failure releases the microphone", async () => {
    api.createSession.mockRejectedValueOnce(new ApiError(409, "conflict", "x", "too_many_sessions"));
    await startMonitoring();
    expect(screen.getByTestId("server-error")).toHaveTextContent("too_many_sessions");
    expect(fake.stop).toHaveBeenCalled();
  });

  it("shows STT unavailable and a stale (not low) risk", async () => {
    api.capabilities.mockResolvedValue({ ...CAPS, stt: { ...CAPS.stt, status: "unavailable", reason: "model_not_available" } } as never);
    api.sendChunk.mockResolvedValue(
      result(0, {
        status: { stt: "unavailable", analysis: "skipped", llm: "not_configured" },
        risk: { ...result(0).risk, status: "stale", stale: true },
        errors: [{ seq: 0, stage: "stt", code: "model_not_available", message: "x", wall: "2026-10-14T10:00:00+00:00" }],
      }),
    );
    await startMonitoring();
    expect(await screen.findByTestId("stt-down")).toHaveTextContent("NOT a low-risk result");
    expect(screen.getByTestId("services")).toHaveTextContent("model_not_available");
    speak(1.0);
    expect(await screen.findByTestId("risk-stale")).toBeInTheDocument();
    expect(screen.getByTestId("live-level")).toHaveTextContent("Not assessed yet");
    expect(screen.getByTestId("errors")).toHaveTextContent("model_not_available");
  });

  it("hindi interface strings", async () => {
    renderApp(<LiveMonitor />);
    fireEvent.change(screen.getByTestId("live-ui-lang"), { target: { value: "hi" } });
    expect(screen.getByText("लाइव माइक्रोफ़ोन निगरानी")).toBeInTheDocument();
    expect(screen.getByTestId("live-start")).toHaveTextContent("निगरानी शुरू करें");
  });
});

describe("i18n tables", () => {
  it("hi has exactly the en keys", async () => {
    const { LIVE_STRINGS } = await import("@/lib/i18n/liveAudio");
    expect(Object.keys(LIVE_STRINGS.hi).sort()).toEqual(Object.keys(LIVE_STRINGS.en).sort());
  });
});

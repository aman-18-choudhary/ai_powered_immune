/**
 * Live microphone monitoring controller (zustand store).
 *
 * Start: microphone permission first (only from the Start click), then a server session.
 * Capture -> 16 kHz Int16 -> fixed chunks (server `chunk_ms`, default 4 s) -> WAV -> an ordered
 * upload queue (one request at a time, seq numbers, retry with backoff on network / 5xx / 429).
 * Pause stops capture and sending (track disabled); Resume continues; Stop ends capture (all
 * tracks stopped, AudioContext closed), sends the last partial chunk, ends the server session
 * and clears audio buffers. Results on screen come only from server responses.
 */
import { create } from "zustand";
import { ApiError } from "@/lib/api/client";
import { openCapture, MicError, type Capture, type MicErrorKind } from "@/lib/liveAudio/capture";
import {
  liveAudioApi,
  type Capabilities,
  type ChunkStatus,
  type Explanation,
  type Indicator,
  type Risk,
  type SafetyAction,
  type SpeechLang,
  type TranscriptSegment,
  type Warning,
} from "@/lib/liveAudio/api";
import { Chunker, Resampler, encodeWav, floatToInt16, rms } from "@/lib/liveAudio/wav";

export type Phase = "idle" | "requesting" | "starting" | "recording" | "paused" | "stopping" | "ended";

export interface ClientError {
  id: number;
  at: string;
  stage: "microphone" | "upload" | "session" | "stt" | "analysis" | "server";
  code: string;
  seq?: number;
}

interface Queued {
  seq: number;
  startMs: number;
  wav: Uint8Array;
}

export interface LiveState {
  phase: Phase;
  language: SpeechLang;
  micError: MicErrorKind | null;
  serverError: string | null;
  caps: Capabilities | null;
  capsError: string | null;
  sessionId: string | null;
  chunkMs: number;
  /** ms of recording before the current running stretch (pauses excluded) */
  recordedMs: number;
  runningSince: number | null;
  level: number;
  pending: number;
  lastLatencyMs: number | null;
  transcript: TranscriptSegment[];
  indicators: Indicator[];
  risk: Risk | null;
  warnings: Warning[];
  actions: SafetyAction[];
  explanation: Explanation | null;
  status: ChunkStatus | null;
  errors: ClientError[];
  gaps: number[];
  setLanguage: (l: SpeechLang) => void;
  loadCapabilities: () => Promise<void>;
  start: () => Promise<void>;
  pause: () => void;
  resume: () => void;
  stop: () => Promise<void>;
  clear: () => void;
}

const MAX_ATTEMPTS = 4;
const BACKOFF_MS = [500, 1000, 2000, 4000];
let capture: Capture | null = null;
let resampler: Resampler | null = null;
let chunker: Chunker | null = null;
let queue: Queued[] = [];
let draining: Promise<void> | null = null;
let seq = 0;
let errId = 0;
let sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Test hook: make retries instant. */
export function __setSleep(fn: (ms: number) => Promise<void>) {
  sleep = fn;
}

const EMPTY = {
  sessionId: null,
  recordedMs: 0,
  runningSince: null,
  level: 0,
  pending: 0,
  lastLatencyMs: null,
  transcript: [] as TranscriptSegment[],
  indicators: [] as Indicator[],
  risk: null,
  warnings: [] as Warning[],
  actions: [] as SafetyAction[],
  explanation: null,
  status: null,
  errors: [] as ClientError[],
  gaps: [] as number[],
  serverError: null,
  micError: null,
};

function now() {
  return new Date().toISOString();
}

function retryable(e: unknown): boolean {
  return e instanceof ApiError && (e.code === "network" || e.code === "server" || e.code === "rate_limited");
}

export const useLiveAudio = create<LiveState>((set, get) => {
  const addError = (stage: ClientError["stage"], code: string, s?: number) =>
    set((st) => ({ errors: [...st.errors, { id: ++errId, at: now(), stage, code, seq: s }].slice(-50) }));

  const onFrames = (f: Float32Array, rate: number) => {
    if (!resampler || resampler.inRate !== rate) resampler = new Resampler(rate);
    const level = rms(f);
    if (Math.abs(level - get().level) > 0.005) set({ level });
    const chunks = chunker?.push(floatToInt16(resampler.process(f))) ?? [];
    for (const c of chunks) enqueue(encodeWav(c.samples), c.startMs);
  };

  const enqueue = (wav: Uint8Array, startMs: number) => {
    queue.push({ seq: seq++, startMs, wav });
    set({ pending: queue.length });
    if (!draining) draining = drain().finally(() => (draining = null));
  };

  const drain = async () => {
    while (queue.length) {
      const item = queue[0];
      const sid = get().sessionId;
      if (!item || !sid) break;
      let done = false;
      for (let attempt = 0; attempt < MAX_ATTEMPTS && !done; attempt++) {
        const t0 = performance.now();
        try {
          const res = await liveAudioApi.sendChunk(sid, item.seq, item.startMs, item.wav);
          set((st) => {
            const transcript = [...st.transcript];
            // indicator links of earlier segments do not change; new segments carry their codes
            for (const s of res.segments) if (!transcript.some((x) => x.id === s.id)) transcript.push(s);
            transcript.sort((a, b) => a.start_s - b.start_s || a.id - b.id);
            const errors = [...st.errors];
            for (const e of res.errors) errors.push({ id: ++errId, at: e.wall, stage: e.stage, code: e.code, seq: e.seq });
            return {
              transcript: transcript.slice(-2000),
              indicators: res.indicators,
              risk: res.risk,
              warnings: [...st.warnings, ...res.warnings].slice(-100),
              actions: res.actions,
              explanation: res.explanation,
              status: res.status,
              gaps: res.gaps,
              errors: errors.slice(-50),
              lastLatencyMs: Math.round(performance.now() - t0),
            };
          });
          done = true;
        } catch (e) {
          if (e instanceof ApiError && e.code === "not_found") {
            addError("session", "session_lost", item.seq);
            queue = [];
            set({ pending: 0 });
            void get().stop(); // not awaited: stop() waits for this drain loop to finish
            return;
          }
          if (retryable(e) && attempt < MAX_ATTEMPTS - 1) {
            await sleep(BACKOFF_MS[attempt] ?? 4000);
            continue;
          }
          addError("upload", e instanceof ApiError ? (e.serverCode ?? e.code) : "unknown", item.seq);
          done = true; // dropped: the server reports the missing seq as a gap
        }
      }
      item.wav = new Uint8Array(0);
      queue.shift();
      set({ pending: queue.length });
    }
  };

  const teardownAudio = async () => {
    const c = capture;
    capture = null;
    if (c) await c.stop();
    resampler = null;
  };

  return {
    phase: "idle",
    language: "auto",
    caps: null,
    capsError: null,
    chunkMs: 4000,
    ...EMPTY,
    setLanguage: (language) => set({ language }),

    loadCapabilities: async () => {
      try {
        const caps = await liveAudioApi.capabilities();
        set({ caps, capsError: null, chunkMs: caps.chunk_ms });
      } catch (e) {
        set({ capsError: e instanceof ApiError ? e.code : "unknown" });
      }
    },

    start: async () => {
      const ph = get().phase;
      if (ph !== "idle" && ph !== "ended") return;
      set({ ...EMPTY, phase: "requesting" });
      seq = 0;
      queue = [];
      try {
        capture = await openCapture({
          onFrames,
          onEnded: () => {
            if (get().phase === "recording" || get().phase === "paused") {
              addError("microphone", "track_ended");
              void get().stop();
            }
          },
        });
      } catch (e) {
        capture = null;
        set({ phase: "idle", micError: e instanceof MicError ? e.kind : "unknown" });
        return;
      }
      set({ phase: "starting" });
      try {
        const s = await liveAudioApi.createSession(get().language);
        chunker = new Chunker(s.chunk_ms);
        set({ sessionId: s.session_id, chunkMs: s.chunk_ms, phase: "recording", runningSince: Date.now() });
      } catch (e) {
        await teardownAudio();
        set({ phase: "idle", serverError: e instanceof ApiError ? (e.serverCode ?? e.code) : "unknown" });
      }
    },

    pause: () => {
      const st = get();
      if (st.phase !== "recording") return;
      capture?.pause();
      set({
        phase: "paused",
        level: 0,
        recordedMs: st.recordedMs + (st.runningSince ? Date.now() - st.runningSince : 0),
        runningSince: null,
      });
    },

    resume: () => {
      if (get().phase !== "paused") return;
      capture?.resume();
      set({ phase: "recording", runningSince: Date.now() });
    },

    stop: async () => {
      const st = get();
      if (st.phase !== "recording" && st.phase !== "paused") return;
      set({
        phase: "stopping",
        level: 0,
        recordedMs: st.recordedMs + (st.runningSince ? Date.now() - st.runningSince : 0),
        runningSince: null,
      });
      await teardownAudio();
      const last = chunker?.flush();
      if (last) enqueue(encodeWav(last.samples), last.startMs);
      chunker = null;
      if (draining) await Promise.race([draining, sleep(30_000)]);
      queue = [];
      const sid = get().sessionId;
      if (sid) {
        try {
          await liveAudioApi.endSession(sid);
        } catch {
          /* the server expires idle sessions after 10 minutes anyway */
        }
      }
      set({ phase: "ended", sessionId: null, pending: 0 });
    },

    clear: () => {
      if (get().phase === "ended" || get().phase === "idle") set({ ...EMPTY, phase: "idle" });
    },
  };
});

/** Abandon everything (route change / unmount): stop the microphone and end the session. */
export async function teardownLiveAudio() {
  const st = useLiveAudio.getState();
  if (st.phase === "recording" || st.phase === "paused") await st.stop();
}

/**
 * Microphone capture. `openCapture` is called ONLY from the Start button handler, after the user
 * has read the consent card; it asks the browser for the microphone (the browser shows its own
 * permission prompt and recording indicator) and delivers mono float PCM frames.
 *
 * AudioWorklet is used when available (processor code loaded from a Blob URL); ScriptProcessor
 * is the fallback for browsers without AudioWorklet. Nothing is recorded to disk or storage.
 */

export type MicErrorKind = "denied" | "no_device" | "in_use" | "insecure" | "unsupported" | "unknown";

export class MicError extends Error {
  constructor(readonly kind: MicErrorKind) {
    super(kind);
    this.name = "MicError";
  }
}

export function classifyMicError(e: unknown): MicErrorKind {
  const name = e instanceof Error || (typeof DOMException !== "undefined" && e instanceof DOMException) ? (e as Error).name : "";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") return "denied";
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError") return "no_device";
  if (name === "NotReadableError" || name === "AbortError" || name === "TrackStartError") return "in_use";
  return "unknown";
}

export interface CaptureHandlers {
  onFrames: (frames: Float32Array, sampleRate: number) => void;
  /** Called when the browser or OS ends the microphone track (device unplugged, permission revoked). */
  onEnded?: () => void;
}

export interface Capture {
  readonly sampleRate: number;
  readonly mode: "worklet" | "script-processor";
  pause(): void;
  resume(): void;
  stop(): Promise<void>;
  /** Live tracks still open (for tests and the UI's "Microphone on" indicator). */
  activeTracks(): number;
}

const WORKLET = `
class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
`;

export const AUDIO_CONSTRAINTS: MediaStreamConstraints = {
  audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  video: false,
};

export async function openCapture(h: CaptureHandlers): Promise<Capture> {
  if (typeof window !== "undefined" && window.isSecureContext === false) throw new MicError("insecure");
  const md = typeof navigator !== "undefined" ? navigator.mediaDevices : undefined;
  if (!md?.getUserMedia) throw new MicError(typeof window !== "undefined" && !window.isSecureContext ? "insecure" : "unsupported");
  let stream: MediaStream;
  try {
    stream = await md.getUserMedia(AUDIO_CONSTRAINTS);
  } catch (e) {
    throw new MicError(classifyMicError(e));
  }
  const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctx) {
    stream.getTracks().forEach((t) => t.stop());
    throw new MicError("unsupported");
  }
  const ctx = new Ctx();
  const source = ctx.createMediaStreamSource(stream);
  const mute = ctx.createGain();
  mute.gain.value = 0;
  mute.connect(ctx.destination);
  let paused = false;
  let node: AudioNode;
  let mode: Capture["mode"];
  let blobUrl: string | null = null;
  const deliver = (f: Float32Array) => {
    if (!paused) h.onFrames(f, ctx.sampleRate);
  };
  try {
    if (!ctx.audioWorklet) throw new Error("no worklet");
    blobUrl = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await ctx.audioWorklet.addModule(blobUrl);
    const w = new AudioWorkletNode(ctx, "pcm-capture", { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
    w.port.onmessage = (ev: MessageEvent<Float32Array>) => deliver(ev.data);
    node = w;
    mode = "worklet";
  } catch {
    const sp = ctx.createScriptProcessor(4096, 1, 1);
    sp.onaudioprocess = (ev) => deliver(ev.inputBuffer.getChannelData(0).slice(0));
    node = sp;
    mode = "script-processor";
  }
  source.connect(node);
  node.connect(mute);
  for (const t of stream.getAudioTracks()) t.addEventListener("ended", () => h.onEnded?.());
  let stopped = false;
  return {
    sampleRate: ctx.sampleRate,
    mode,
    pause() {
      paused = true;
      stream.getAudioTracks().forEach((t) => (t.enabled = false));
      void ctx.suspend().catch(() => undefined);
    },
    resume() {
      paused = false;
      stream.getAudioTracks().forEach((t) => (t.enabled = true));
      void ctx.resume().catch(() => undefined);
    },
    async stop() {
      if (stopped) return;
      stopped = true;
      paused = true;
      stream.getTracks().forEach((t) => t.stop());
      try {
        source.disconnect();
        node.disconnect();
        mute.disconnect();
      } catch {
        /* already disconnected */
      }
      if (node instanceof AudioWorkletNode) node.port.onmessage = null;
      await ctx.close().catch(() => undefined);
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    },
    activeTracks: () => stream.getTracks().filter((t) => t.readyState === "live").length,
  };
}

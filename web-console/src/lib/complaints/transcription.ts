import type { TranscriptionStatus } from "./types";

/** Short status names for audio evidence transcription (the service sends the full reason). */
export const TRANSCRIPTION_LABEL: Record<string, string> = {
  transcribed: "Transcribed",
  truncated: "Transcribed (first part only)",
  engine_not_configured: "Not transcribed: no speech-to-text engine configured",
  model_not_available_offline: "Not transcribed: model not available offline",
  timeout: "Not transcribed: timed out",
  decode_failed: "Not transcribed: file could not be decoded",
  decode_unavailable: "Not transcribed: no decoder for this format",
  unsupported_format: "Not transcribed: format not supported",
  budget_exhausted: "Not transcribed in the last run: time budget used up",
  not_attempted_limit: "Not transcribed in the last run: per-run file limit",
  engine_error: "Not transcribed: speech-to-text failed",
  not_attempted: "Not transcribed yet",
  not_implemented_at_analysis_time: "Not transcribed: not available when that analysis ran",
};

export function transcriptionLabel(status: TranscriptionStatus): string {
  return TRANSCRIPTION_LABEL[status] ?? `Transcription: ${status.replace(/_/g, " ")}`;
}

export function isTranscribed(status: TranscriptionStatus | undefined | null): boolean {
  return status === "transcribed" || status === "truncated";
}

/** Seconds as m:ss (transcript offsets). */
export function clock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "?";
  const t = Math.max(0, Math.floor(seconds));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

/** "0:03–0:06" */
export function span(start: number | null | undefined, end: number | null | undefined): string {
  return `${clock(start)}–${clock(end ?? start)}`;
}

import { api } from "@/lib/api/endpoints";

/** Shapes returned by /api/live-audio (services/live_audio). Real audio, not simulated. */
export type SpeechLang = "auto" | "hi" | "en";
export type Level = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface Capabilities {
  mode: "incremental_chunked";
  mode_note: string;
  chunk_ms: number;
  max_chunk_ms: number;
  max_chunk_bytes: number;
  stt: { engine: string; model: string; status: string; reason: string | null; languages: string[] };
  scorer: { status: string; model_version: string | null; fallback_mode: boolean };
  llm: { status: string; model: string | null };
  limits: { sessions_per_user: number; chunks_per_minute: number; session_idle_ttl_s: number };
}

export interface SessionInfo {
  session_id: string;
  language: SpeechLang;
  chunk_ms: number;
  max_chunk_bytes: number;
}

export interface TranscriptSegment {
  id: number;
  seq: number;
  start_s: number;
  end_s: number;
  text: string;
  language: string;
  indicator_codes: string[];
  confidence: number | null;
}

export interface Indicator {
  code: string;
  label: string;
  source: "call_guard" | "live_rules";
  weight: number;
  segment_ids: number[];
  evidence_excerpts: string[];
  first_at_s: number;
  last_at_s: number;
  status: "detected_not_confirmed";
}

export interface RiskPoint {
  seq: number;
  at_s: number;
  score: number;
  level: Level;
}

export interface Risk {
  score: number | null;
  level: Level | null;
  status: "not_assessed" | "assessed" | "stale";
  stale: boolean;
  assessed_chunks: number;
  history_tail: RiskPoint[];
  score_semantics: string;
  statement: string;
  model_version: string | null;
}

export interface Warning {
  id: number;
  seq: number;
  at_s: number;
  wall: string;
  level: Level;
  previous_level: Level | null;
  score: number;
  codes: string[];
  message: string;
}

export interface SafetyAction {
  id: string;
  text: string;
  source: "static_guidance";
  priority: number;
}

export interface Explanation {
  rules: string;
  llm: string | null;
  /** available | rejected | timeout | error | unavailable | skipped | pending | idle | not_configured */
  llm_status: string;
  llm_at_s: number | null;
  /** Rejection or failure reason (e.g. role_inversion); added with the grounding checks. */
  llm_reason?: string | null;
  llm_model?: string | null;
  /** "LLM-generated (model), may be imprecise" when llm text is present. */
  llm_label?: string | null;
}

export interface ServerError {
  seq: number;
  stage: "stt" | "analysis";
  code: string;
  message: string;
  wall: string;
}

export interface ChunkStatus {
  stt: string;
  analysis: string;
  llm: string;
}

export interface ChunkResult {
  seq: number;
  duplicate: boolean;
  late: boolean;
  segments: TranscriptSegment[];
  indicators: Indicator[];
  risk: Risk;
  warnings: Warning[];
  actions: SafetyAction[];
  explanation: Explanation;
  status: ChunkStatus;
  errors: ServerError[];
  gaps: number[];
  timing: { stt_ms?: number; analysis_ms?: number; total_ms?: number };
}

export interface EndSummary {
  ended: true;
  chunks_processed: number;
  risk: Risk;
}

export const liveAudioApi = {
  capabilities: (signal?: AbortSignal) => api.get<Capabilities>("/live-audio/capabilities", signal),
  createSession: (language: SpeechLang) => api.post<SessionInfo>("/live-audio/sessions", { language }),
  /** The audio goes only in the request body, never in a URL; the server does not store it. */
  sendChunk: (sessionId: string, seq: number, startMs: number, wav: Uint8Array, signal?: AbortSignal) =>
    api.request<ChunkResult>("POST", `/live-audio/sessions/${encodeURIComponent(sessionId)}/chunks?seq=${seq}&start_ms=${startMs}`, {
      raw: { body: wav, contentType: "audio/wav" },
      signal,
    }),
  endSession: (sessionId: string) => api.post<EndSummary>(`/live-audio/sessions/${encodeURIComponent(sessionId)}/end`),
};

import { ApiClient } from "./client";
import type {
  AntibodiesResponse,
  BenchmarkSummary,
  BestTakedown,
  CaseView,
  DemoState,
  GeoResponse,
  HoldsResponse,
  HoldView,
  LedgerHead,
  LoginResponse,
  RingGraph,
  Role,
  TakedownResult,
  VerifyResult,
} from "@/types/api";
import { useSession } from "@/state/session";

export const api = new ApiClient({
  getToken: () => useSession.getState().token,
  onUnauthorized: () => useSession.getState().logout("expired"),
});

export const endpoints = {
  login: (role: Role) => api.request<LoginResponse>("POST", "/auth/demo-login", { body: { role }, auth: false }),
  demoState: (signal?: AbortSignal) => api.get<DemoState>("/demo/state", signal),
  demoStart: (b: { seed?: number; speed?: number; mode?: "hero" | "hero+noise"; pacing?: "real" | "cinematic" }) =>
    api.post<{ run_id: string }>("/demo/start", b),
  demoControl: (b: { action: "pause" | "resume" | "stop" | "seek"; t?: number; wall?: number; speed?: number }) =>
    api.post<DemoState>("/demo/control", b),
  holds: (state: "open" | "all" = "all", signal?: AbortSignal) =>
    api.get<HoldsResponse>(`/holds?state=${state}`, signal),
  resolveHold: (txnRef: string, action: "release" | "confirm_block", decisionSeq?: number) =>
    api.post<{ hold: HoldView }>(`/holds/${encodeURIComponent(txnRef)}/resolve`, { action, decision_seq: decisionSeq }),
  antibodies: (state: "active" | "all" = "active", signal?: AbortSignal) =>
    api.get<AntibodiesResponse>(`/antibodies?state=${state}`, signal),
  geo: (signal?: AbortSignal) => api.get<GeoResponse>("/geo/hotspots", signal),
  ringGraph: (caseId = "current", signal?: AbortSignal) =>
    api.get<RingGraph>(`/ring/${encodeURIComponent(caseId)}/graph`, signal),
  ringSimulate: (caseId: string, body: { k: number } | { remove: string[] }, signal?: AbortSignal) =>
    api.post<TakedownResult>(`/ring/${encodeURIComponent(caseId)}/simulate`, body, signal),
  bestTakedown: (caseId: string, k: number, signal?: AbortSignal) =>
    api.get<BestTakedown>(`/ring/${encodeURIComponent(caseId)}/best-takedown?k=${k}`, signal),
  benchmark: (signal?: AbortSignal) => api.get<BenchmarkSummary>("/benchmark/summary", signal),
  createCase: (body: Record<string, unknown> = {}) => api.post<CaseView>("/cases", body),
  ledgerHead: (signal?: AbortSignal) => api.get<LedgerHead>("/ledger/head", signal),
  /** Raw text, so integer-valued floats keep their source form for hash recomputation. */
  ledgerEntriesText: (fromSeq = 1, limit = 200, signal?: AbortSignal) =>
    api.request<string>("GET", `/ledger/entries?from_seq=${fromSeq}&limit=${limit}`, { text: true, signal }),
  checkpoints: (signal?: AbortSignal) => api.get<unknown>("/ledger/checkpoints", signal),
  packageWithEtag: (caseId: string, signal?: AbortSignal) =>
    api.request<{ data: Blob; headers: { etag: string | null; contentType: string | null } }>(
      "GET", `/packages/${encodeURIComponent(caseId)}`, { blob: true, withHeaders: true, signal },
    ),
  packageBlob: (caseId: string, signal?: AbortSignal) =>
    api.request<Blob>("GET", `/packages/${encodeURIComponent(caseId)}`, { blob: true, signal }),
  /** The message goes only in the POST body: never in a URL, never logged or stored client-side. */
  assess: (message: string, lang: string, signal?: AbortSignal) =>
    api.post<unknown>("/citizen/assess", { message, lang }, signal),
  verify: (caseId: string) => api.post<VerifyResult>("/verify", { case_id: caseId }),
};

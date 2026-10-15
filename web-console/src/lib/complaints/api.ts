import { ApiError, mapStatus } from "@/lib/api/client";
import { isRecord } from "@/types/guards";
import type {
  AdminHealth,
  AdminUser,
  AccountRole,
  Alert,
  AlertsPage,
  Analysis,
  AnalysisVersion,
  AuditRow,
  CheckRequest,
  CheckResult,
  AuthConfig,
  ComplaintCreate,
  ComplaintDetail,
  ComplaintListItem,
  ComplaintStatus,
  Evidence,
  LocalStartResponse,
  LocalVerifyResponse,
  Me,
  ModelsInfo,
  MyStats,
  Page,
  SessionPayload,
} from "./types";

/** The complaints service as mounted by the demo server. */
export const COMPLAINTS_BASE = "/api/complaints";

const FALLBACK: Record<number | "network" | "default", string> = {
  network: "Can't reach the complaints service. Check that the server is running.",
  400: "The request was not accepted.",
  401: "Your sign-in has expired. Please sign in again.",
  403: "Your account cannot do this.",
  404: "Not found.",
  409: "That conflicts with the current state. Reload and try again.",
  413: "The file or request is too large.",
  415: "That file type is not accepted.",
  422: "Some details need attention.",
  429: "Too many requests. Try again shortly.",
  503: "The complaints service is temporarily unavailable. Try again shortly.",
  default: "Something went wrong. Try again.",
};

/**
 * An error from the complaints service. `message` is the service's own friendly message when it
 * sent one (`{error, message}`; those never echo submitted values), otherwise a generic text.
 */
export class ComplaintsApiError extends ApiError {
  /** Field name -> message, from a 422 `{error: "validation_error", detail: [{loc, msg}]}`. */
  readonly fieldErrors: Record<string, string>;
  /** Validation messages that are not tied to one field. */
  readonly formErrors: string[];
  readonly retryAfter: number | null;
  constructor(status: number, message: string, serverCode: string | undefined, fieldErrors: Record<string, string> = {}, formErrors: string[] = [], retryAfter: number | null = null) {
    super(status, status === 0 ? "network" : mapStatus(status), message, serverCode);
    this.name = "ComplaintsApiError";
    this.fieldErrors = fieldErrors;
    this.formErrors = formErrors;
    this.retryAfter = retryAfter;
  }
}

/** Turns `[{loc: ["body", "upi_id"], msg}]` into `{upi_id: msg}` (first message per field). */
export function mapValidation(detail: unknown): { fields: Record<string, string>; form: string[] } {
  const fields: Record<string, string> = {};
  const form: string[] = [];
  if (!Array.isArray(detail)) return { fields, form };
  for (const d of detail) {
    if (!isRecord(d)) continue;
    const msg = typeof d.msg === "string" ? d.msg.replace(/^Value error, /, "") : "Invalid value";
    const loc = Array.isArray(d.loc) ? d.loc.map(String) : [];
    const where = loc[0] === "body" || loc[0] === "query" ? loc.slice(1) : loc;
    const field = where[0];
    if (field && field !== "privacy") {
      if (!(field in fields)) fields[field] = msg;
    } else if (where[0] === "privacy" && where[1]) {
      fields[`privacy.${where[1]}`] = msg;
    } else form.push(msg);
  }
  return { fields, form };
}

export function messageFor(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return FALLBACK.default;
}

type TokenProvider = () => Promise<string | null>;
let tokenProvider: TokenProvider = async () => null;
let onUnauthorized: () => void = () => {};

/** Wired by the account session store (local token or Clerk `getToken`). */
export function configureComplaintsAuth(p: { getToken: TokenProvider; onUnauthorized: () => void }) {
  tokenProvider = p.getToken;
  onUnauthorized = p.onUnauthorized;
}

interface Opts {
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  blob?: boolean;
  auth?: boolean;
  headers?: Record<string, string>;
  /** Token to use instead of the provider (right after local sign-in). */
  token?: string;
}

type Method = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export async function request<T>(method: Method, path: string, o: Opts = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: o.blob ? "*/*" : "application/json", ...o.headers };
  const token = o.auth === false ? null : (o.token ?? (await tokenProvider()));
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (o.form) body = o.form;
  else if (o.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(o.body);
  }
  let res: Response;
  try {
    res = await fetch(COMPLAINTS_BASE + path, { method, headers, body, signal: o.signal, cache: "no-store" });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw new ApiError(0, "aborted", "Request cancelled.");
    throw new ComplaintsApiError(0, FALLBACK.network, undefined);
  }
  if (!res.ok) {
    let j: unknown = null;
    try {
      j = await res.json();
    } catch {
      /* non-JSON error body (e.g. a proxy page) */
    }
    const serverCode = isRecord(j) && typeof j.error === "string" ? j.error : undefined;
    const serverMsg = isRecord(j) && typeof j.message === "string" && j.message.length < 400 ? j.message : undefined;
    const { fields, form } = isRecord(j) ? mapValidation(j.detail) : { fields: {}, form: [] };
    const ra = Number(res.headers.get("Retry-After"));
    const retryAfter = Number.isFinite(ra) && ra > 0 ? ra : null;
    let msg = serverMsg ?? FALLBACK[res.status] ?? (res.status >= 500 ? FALLBACK[503]! : FALLBACK.default);
    if (res.status === 429 && retryAfter) msg = `${msg} (about ${retryAfter} s)`;
    if (res.status === 401 && token && o.auth !== false) onUnauthorized();
    throw new ComplaintsApiError(res.status, msg, serverCode, fields, form, retryAfter);
  }
  if (o.blob) return (await res.blob()) as T;
  if (res.status === 204) return undefined as T;
  try {
    return (await res.json()) as T;
  } catch {
    throw new ComplaintsApiError(res.status, "Unexpected response from the complaints service.", "bad_response");
  }
}

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

export interface ComplaintFilters {
  status?: ComplaintStatus | "";
  category?: string;
  risk_level?: string;
  city?: string;
  state?: string;
  q?: string;
  created_from?: string;
  created_to?: string;
  sort?: string;
  limit?: number;
  offset?: number;
}

const enc = encodeURIComponent;

export const complaintsApi = {
  authConfig: (signal?: AbortSignal) => request<AuthConfig>("GET", "/auth/config", { auth: false, signal }),
  localStart: (email: string) => request<LocalStartResponse>("POST", "/local-auth/start", { auth: false, body: { email } }),
  localVerify: (email: string, code: string, name?: string) =>
    request<LocalVerifyResponse>("POST", "/local-auth/verify", { auth: false, body: name ? { email, code, name } : { email, code } }),
  session: (token?: string, signal?: AbortSignal) => request<SessionPayload>("GET", "/auth/session", { token, signal }),
  entry: (entry: "citizen" | "investigator", token?: string) => request<SessionPayload>("POST", "/auth/entry", { body: { entry }, token }),

  me: (signal?: AbortSignal) => request<Me>("GET", "/me", { signal }),
  updateMe: (patch: { display_name?: string | null; privacy?: { allow_case_matching: boolean } }) => request<Me>("PATCH", "/me", { body: patch }),
  stats: (signal?: AbortSignal) => request<MyStats>("GET", "/me/stats", { signal }),
  alerts: (p: { unread?: boolean; limit?: number; offset?: number }, signal?: AbortSignal) =>
    request<AlertsPage>("GET", `/alerts${qs({ unread: p.unread ? true : undefined, limit: p.limit, offset: p.offset })}`, { signal }),
  readAlert: (id: string) => request<Alert>("POST", `/alerts/${enc(id)}/read`, { body: {} }),

  list: (f: ComplaintFilters, signal?: AbortSignal) => request<Page<ComplaintListItem>>("GET", `/complaints${qs({ ...f })}`, { signal }),
  get: (id: string, signal?: AbortSignal) => request<ComplaintDetail>("GET", `/complaints/${enc(id)}`, { signal }),
  create: (body: ComplaintCreate, idempotencyKey: string) =>
    request<ComplaintDetail>("POST", "/complaints", { body, headers: { "Idempotency-Key": idempotencyKey } }),
  setStatus: (id: string, status: ComplaintStatus, note: string | null) =>
    request<ComplaintDetail>("PATCH", `/complaints/${enc(id)}/status`, { body: note ? { status, note } : { status } }),
  uploadEvidence: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<Evidence>("POST", `/complaints/${enc(id)}/evidence`, { form });
  },
  deleteEvidence: (id: string, eid: string) =>
    request<{ id: string; status: "deleted"; file_removed: boolean }>("DELETE", `/complaints/${enc(id)}/evidence/${enc(eid)}`),
  downloadEvidence: (id: string, eid: string) => request<Blob>("GET", `/complaints/${enc(id)}/evidence/${enc(eid)}/download`, { blob: true }),
  analysis: (id: string, signal?: AbortSignal) => request<Analysis>("GET", `/complaints/${enc(id)}/analysis`, { signal }),
  runAnalysis: (id: string) => request<Analysis>("POST", `/complaints/${enc(id)}/analysis`, { body: {} }),
  analysisVersions: (id: string, p: { limit: number; offset: number }, signal?: AbortSignal) =>
    request<Page<AnalysisVersion>>("GET", `/complaints/${enc(id)}/analyses${qs(p)}`, { signal }),
  /** Stateless check: nothing is stored by the service. */
  check: (body: CheckRequest, signal?: AbortSignal) => request<CheckResult>("POST", "/analyze", { body, signal }),
  models: (signal?: AbortSignal) => request<ModelsInfo>("GET", "/models", { signal }),

  adminUsers: (p: { limit: number; offset: number; role?: string }, signal?: AbortSignal) =>
    request<Page<AdminUser>>("GET", `/admin/users${qs(p)}`, { signal }),
  adminSetRole: (id: string, role: AccountRole, expected: AccountRole) =>
    request<AdminUser>("PUT", `/admin/users/${enc(id)}/role`, { body: { role, expected_role: expected } }),
  adminSetActive: (id: string, isActive: boolean) => request<AdminUser>("PUT", `/admin/users/${enc(id)}/active`, { body: { is_active: isActive } }),
  adminAudit: (p: { limit: number; offset: number }, signal?: AbortSignal) => request<Page<AuditRow>>("GET", `/admin/audit${qs(p)}`, { signal }),
  adminHealth: (signal?: AbortSignal) => request<AdminHealth>("GET", "/admin/health", { signal }),
};

/** Save a Blob as a file through a temporary object URL (the download itself was authorised). */
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "evidence";
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

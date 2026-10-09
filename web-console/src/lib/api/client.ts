import { isRecord } from "@/types/guards";

export type ApiErrorCode =
  | "network"
  | "aborted"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "payload_too_large"
  | "invalid_request"
  | "rate_limited"
  | "server"
  | "bad_response"
  | "unknown";

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly serverCode?: string;
  constructor(status: number, code: ApiErrorCode, message: string, serverCode?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.serverCode = serverCode;
  }
}

export function mapStatus(status: number): ApiErrorCode {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 413) return "payload_too_large";
  if (status === 422 || status === 400) return "invalid_request";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "server";
  return "unknown";
}

const FRIENDLY: Record<ApiErrorCode, string> = {
  network: "Can't reach the demo server. Check that it is running.",
  aborted: "Request cancelled.",
  unauthorized: "Your demo session expired. Please sign in again.",
  forbidden: "This role isn't allowed to do that.",
  not_found: "Not found.",
  conflict: "That action conflicts with the current state.",
  payload_too_large: "Request too large.",
  invalid_request: "The server rejected that request.",
  rate_limited: "Slow down a little: rate limit reached.",
  server: "The server hit an error. Try again.",
  bad_response: "Unexpected response from the server.",
  unknown: "Something went wrong.",
};

export interface ApiOptions {
  baseUrl?: string;
  getToken: () => string | null;
  onUnauthorized?: () => void;
  fetchImpl?: typeof fetch;
}

export interface RequestOptions {
  signal?: AbortSignal;
  body?: unknown;
  /** When true the response is returned as a Blob (package download). */
  blob?: boolean;
  auth?: boolean;
}

export class ApiClient {
  private readonly base: string;
  private readonly opts: ApiOptions;
  constructor(opts: ApiOptions) {
    this.opts = opts;
    this.base = opts.baseUrl ?? "/api";
  }

  async request<T>(method: "GET" | "POST", path: string, o: RequestOptions = {}): Promise<T> {
    const f = this.opts.fetchImpl ?? fetch;
    const headers: Record<string, string> = { Accept: o.blob ? "*/*" : "application/json" };
    const token = this.opts.getToken();
    if (token && o.auth !== false) headers.Authorization = `Bearer ${token}`;
    let body: string | undefined;
    if (o.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(o.body);
    }
    let res: Response;
    try {
      res = await f(this.base + path, { method, headers, body, signal: o.signal, cache: "no-store" });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") throw new ApiError(0, "aborted", FRIENDLY.aborted);
      throw new ApiError(0, "network", FRIENDLY.network);
    }
    if (!res.ok) {
      let serverCode: string | undefined;
      try {
        const j: unknown = await res.json();
        if (isRecord(j) && typeof j.error === "string") serverCode = j.error;
      } catch {
        /* non-JSON error body */
      }
      const code = mapStatus(res.status);
      if (code === "unauthorized" && token) this.opts.onUnauthorized?.();
      throw new ApiError(res.status, code, FRIENDLY[code], serverCode);
    }
    if (o.blob) return (await res.blob()) as T;
    try {
      return (await res.json()) as T;
    } catch {
      throw new ApiError(res.status, "bad_response", FRIENDLY.bad_response);
    }
  }

  get<T>(path: string, signal?: AbortSignal) {
    return this.request<T>("GET", path, { signal });
  }
  post<T>(path: string, body?: unknown, signal?: AbortSignal) {
    return this.request<T>("POST", path, { body: body ?? {}, signal });
  }
}

export function friendlyError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return FRIENDLY.unknown;
}

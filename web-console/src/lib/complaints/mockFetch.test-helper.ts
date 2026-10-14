import { vi } from "vitest";

/**
 * MOCKED fetch for unit tests only: routes `METHOD /path` (relative to /api/complaints) to canned
 * responses and records every call. Never used by the application.
 */
export interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
}

type Handler = (call: Call) => Response | Promise<Response>;

export function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

export function mockFetch(routes: Record<string, Handler | Response | (() => never)>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const u = new URL(url, "http://localhost");
    const path = u.pathname.replace(/^\/api\/complaints/, "") + u.search;
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown = init?.body;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body);
      } catch {
        /* keep raw */
      }
    }
    const call: Call = { method, path, headers: (init?.headers as Record<string, string>) ?? {}, body };
    calls.push(call);
    const key = Object.keys(routes).find((k) => {
      const [m, p] = k.split(" ") as [string, string];
      if (m !== method) return false;
      if (p.endsWith("*")) return path.startsWith(p.slice(0, -1));
      return path === p || path.split("?")[0] === p;
    });
    if (!key) return json(404, { error: "not_found", message: `mock: no route for ${method} ${path}` });
    const h = routes[key]!;
    if (h instanceof Response) return h.clone();
    return (h as Handler)(call);
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn };
}

export const SESSION = (role: "CITIZEN" | "INVESTIGATOR" | "ADMIN", extra: Record<string, unknown> = {}) => ({
  user: { id: `u-${role.toLowerCase()}`, email: `${role.toLowerCase()}@example.org`, email_verified: true, role, is_active: true },
  capabilities: [],
  ...extra,
});

export const LOCAL_CONFIG = {
  auth_mode: "local",
  clerk: { enabled: false, publishable_key: null },
  local: { enabled: true, dev_only: true, delivery: "server_terminal", code_ttl_s: 600 },
};

export function complaint(over: Record<string, unknown> = {}) {
  return {
    id: "c0ffee00-0000-4000-8000-000000000001",
    category: "UPI_FRAUD",
    title: "Fake refund call",
    description: "A caller asked me to approve a UPI collect request.",
    status: "SUBMITTED",
    incident_at: null,
    reported_at: "2026-10-14T09:00:00+00:00",
    amount: null,
    currency: null,
    payment_method: null,
    upi_id: null,
    utr_reference: null,
    suspect_phone: null,
    suspect_email: null,
    suspicious_url: null,
    location_text: null,
    city: null,
    state: null,
    latitude: null,
    longitude: null,
    social_platform: null,
    social_account: null,
    bank_name: null,
    bank_account: null,
    ifsc: null,
    created_at: "2026-10-14T09:00:00+00:00",
    updated_at: "2026-10-14T09:00:00+00:00",
    status_history: [{ from_status: null, to_status: "SUBMITTED", actor_user_id: "u-citizen", note: null, created_at: "2026-10-14T09:00:00+00:00" }],
    latest_analysis: null,
    evidence: [],
    ...over,
  };
}

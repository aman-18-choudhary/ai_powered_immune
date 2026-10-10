import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, friendlyError, mapStatus } from "./client";

function res(status: number, body: unknown, raw = false): Response {
  return new Response(raw ? (body as string) : JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function client(fetchImpl: typeof fetch, token: string | null = "tok", onUnauthorized = vi.fn()) {
  return { c: new ApiClient({ getToken: () => token, fetchImpl, onUnauthorized }), onUnauthorized };
}

describe("ApiClient", () => {
  it("sends the bearer token and JSON body, parses JSON", async () => {
    const f = vi.fn(async () => res(200, { ok: 1 }));
    const { c } = client(f as unknown as typeof fetch);
    const out = await c.post<{ ok: number }>("/demo/start", { seed: 1 });
    expect(out.ok).toBe(1);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/demo/start");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    expect(init.body).toBe('{"seed":1}');
  });

  it("maps statuses to codes and surfaces the server error code", async () => {
    const cases: [number, string][] = [[403, "forbidden"], [404, "not_found"], [409, "conflict"], [413, "payload_too_large"], [422, "invalid_request"], [429, "rate_limited"], [500, "server"], [503, "server"]];
    for (const [status, code] of cases) {
      const { c } = client((async () => res(status, { error: "boom" })) as unknown as typeof fetch);
      await expect(c.get("/x")).rejects.toMatchObject({ status, code, serverCode: "boom" });
    }
    expect(mapStatus(418)).toBe("unknown");
  });

  it("401 calls onUnauthorized once and throws unauthorized", async () => {
    const { c, onUnauthorized } = client((async () => res(401, { error: "unauthorized" })) as unknown as typeof fetch);
    await expect(c.get("/holds")).rejects.toMatchObject({ code: "unauthorized" });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("does not trigger sign-out for a 401 on a request that sent no token (login)", async () => {
    const { c, onUnauthorized } = client((async () => res(401, {})) as unknown as typeof fetch, null);
    await expect(c.request("POST", "/auth/demo-login", { body: {}, auth: false })).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("maps network failures and aborts", async () => {
    const { c } = client((async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch);
    await expect(c.get("/x")).rejects.toMatchObject({ code: "network", status: 0 });
    const ab = client((async () => {
      throw new DOMException("aborted", "AbortError");
    }) as unknown as typeof fetch);
    await expect(ab.c.get("/x")).rejects.toMatchObject({ code: "aborted" });
  });

  it("flags non-JSON success bodies and tolerates non-JSON error bodies", async () => {
    const { c } = client((async () => new Response("<html>", { status: 200 })) as unknown as typeof fetch);
    await expect(c.get("/x")).rejects.toMatchObject({ code: "bad_response" });
    const e = client((async () => new Response("oops", { status: 502 })) as unknown as typeof fetch);
    await expect(e.c.get("/x")).rejects.toMatchObject({ code: "server", serverCode: undefined });
  });

  it("returns blobs for package downloads and friendly messages never echo server text", async () => {
    const { c } = client((async () => new Response("zip", { status: 200 })) as unknown as typeof fetch);
    const b = await c.request<Blob>("GET", "/packages/x", { blob: true });
    expect(b.size).toBe(3);
    expect(friendlyError(new ApiError(500, "server", "The server hit an error. Try again.", "secret detail"))).not.toContain("secret");
    expect(friendlyError(new Error("x"))).toBe("Something went wrong.");
  });
});

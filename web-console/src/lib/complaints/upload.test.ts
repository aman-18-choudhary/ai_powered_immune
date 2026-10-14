// MOCKED fetch: per-file upload status is honest (stored only when the service returned a stored record).
import { afterEach, describe, expect, it, vi } from "vitest";
import { json, mockFetch } from "./mockFetch.test-helper";
import { uploadSequential } from "./upload";
import { mapValidation, request, ComplaintsApiError, configureComplaintsAuth } from "./api";

afterEach(() => vi.unstubAllGlobals());

const stored = (name: string) => ({ id: `ev-${name}`, complaint_id: "c1", original_filename: name, mime_type: "image/png", size_bytes: 4, sha256: "ab".repeat(32), status: "stored", created_at: "2026-10-14T09:00:00+00:00" });

describe("uploadSequential", () => {
  it("stored / rejected (service reason) / failed (network) / not sent (pre-check) / unconfirmed", async () => {
    let n = 0;
    const { calls } = mockFetch({
      "POST /complaints/c1/evidence": () => {
        n += 1;
        if (n === 1) return json(201, stored("a.png"));
        if (n === 2) return json(415, { error: "unsupported_file_type", message: "Only PNG, JPEG, WebP, PDF, MP3, WAV, OGG and M4A files are accepted." });
        if (n === 3) throw new TypeError("Failed to fetch");
        return json(201, { id: "x", status: "rejected" });
      },
    });
    const files = [new File(["png1"], "a.png"), new File(["fake"], "b.png"), new File(["png3"], "c.png"), new File(["x"], "d.exe"), new File(["png5"], "e.png")];
    const seen: string[][] = [];
    const out = await uploadSequential("c1", files, (items) => seen.push(items.map((i) => i.status)));
    expect(out.map((i) => i.status)).toEqual(["stored", "rejected", "failed", "rejected", "failed"]);
    expect(out[0]!.evidence?.id).toBe("ev-a.png");
    expect(out[1]!.reason).toContain("Only PNG");
    expect(out[3]!.reason).toMatch(/Not sent/);
    expect(out[4]!.reason).toMatch(/did not confirm/);
    expect(out.filter((i) => i.status !== "stored").every((i) => !i.evidence)).toBe(true);
    // one file per request, in order, multipart field "file"; d.exe never sent
    expect(calls).toHaveLength(4);
    expect(calls[0]!.body).toBeInstanceOf(FormData);
    expect((calls[0]!.body as FormData).get("file")).toBeInstanceOf(File);
    // a file is "uploading" before its result
    expect(seen.some((s) => s[0] === "uploading")).toBe(true);
  });
});

describe("complaints request()", () => {
  it("maps 422 detail to fields and keeps the service message for other errors", async () => {
    expect(mapValidation([{ loc: ["body", "upi_id"], msg: "Value error, not a valid UPI id", type: "value_error" }, { loc: ["body"], msg: "latitude and longitude must be given together", type: "value_error" }])).toEqual({
      fields: { upi_id: "not a valid UPI id" },
      form: ["latitude and longitude must be given together"],
    });
    mockFetch({ "GET /x": json(409, { error: "last_admin", message: "At least one active administrator must remain." }) });
    await expect(request("GET", "/x")).rejects.toMatchObject({ status: 409, serverCode: "last_admin", message: "At least one active administrator must remain." });
  });

  it("401 with a token signs the account out; 503 without a message gets a plain explanation", async () => {
    const onUnauthorized = vi.fn();
    configureComplaintsAuth({ getToken: async () => "tok", onUnauthorized });
    mockFetch({ "GET /a": json(401, { error: "invalid_token", message: "The access token is not valid." }), "GET /b": json(503, { error: "service_unavailable" }) });
    await expect(request("GET", "/a")).rejects.toBeInstanceOf(ComplaintsApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    await expect(request("GET", "/b")).rejects.toMatchObject({ message: expect.stringContaining("temporarily unavailable") });
  });
});

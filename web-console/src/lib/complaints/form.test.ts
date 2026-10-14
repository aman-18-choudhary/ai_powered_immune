import { describe, expect, it } from "vitest";
import { EMPTY_FORM, buildPayload, incidentIso, phoneOk, precheckFile, validate, type FormValues } from "./form";

const base: FormValues = { ...EMPTY_FORM, category: "UPI_FRAUD", title: "Refund call", description: "Caller asked me to approve a collect request." };

describe("complaint payload", () => {
  it("mandatory-only: exactly category, title, description (no empty optional keys)", () => {
    expect(buildPayload({ ...base, title: "  Refund call  " })).toEqual({ category: "UPI_FRAUD", title: "Refund call", description: "Caller asked me to approve a collect request." });
  });

  it("optional fields are sent trimmed; blanks stay out", () => {
    const p = buildPayload({
      ...base,
      amount: "25,000.50",
      payment_method: "UPI",
      upi_id: " scam@okaxis ",
      ifsc: "sbin0001234",
      bank_account: "1234 5678 90",
      city: "  ",
      latitude: "12.97",
      longitude: "77.59",
      incident_date: "2026-10-01",
      incident_time: "14:30",
    });
    expect(p).toMatchObject({ amount: "25000.50", payment_method: "UPI", upi_id: "scam@okaxis", ifsc: "sbin0001234", bank_account: "1234 5678 90", latitude: 12.97, longitude: 77.59 });
    expect(p).not.toHaveProperty("city");
    expect(p.incident_at).toMatch(/^2026-10-01T14:30:00[+-]\d\d:\d\d$/);
  });
});

describe("client-side validation mirrors the service", () => {
  it("requires category, title (>=3) and description (>=10)", () => {
    expect(Object.keys(validate(EMPTY_FORM)).sort()).toEqual(["category", "description", "title"]);
    expect(validate(base)).toEqual({});
  });

  it("checks formats of optional fields only when filled", () => {
    const e = validate({ ...base, upi_id: "nope", ifsc: "SBIN1234567", bank_account: "12ab", utr_reference: "12", suspicious_url: "javascript:alert(1)", suspect_phone: "12", suspect_email: "x@", amount: "1.234" });
    expect(Object.keys(e).sort()).toEqual(["amount", "bank_account", "ifsc", "suspect_email", "suspect_phone", "suspicious_url", "upi_id", "utr_reference"]);
  });

  it("coordinates go together and stay in range; incident time not in the future; time needs a date", () => {
    expect(validate({ ...base, latitude: "12" })).toHaveProperty("longitude");
    expect(validate({ ...base, latitude: "91", longitude: "0" })).toHaveProperty("latitude");
    expect(validate({ ...base, incident_date: "2999-01-01" }, new Date("2026-10-14T00:00:00Z"))).toHaveProperty("incident_at");
    expect(validate({ ...base, incident_time: "10:00" })).toHaveProperty("incident_at");
  });

  it("phone numbers like the service accepts", () => {
    for (const ok of ["9876543210", "+44 20 7946 0958", "09876543210", "919876543210"]) expect(phoneOk(ok)).toBe(true);
    for (const bad of ["12345", "+0123456789", "abc"]) expect(phoneOk(bad)).toBe(false);
  });

  it("incidentIso keeps the local wall-clock time with an offset", () => {
    expect(incidentIso("", "")).toBeNull();
    expect(incidentIso("2026-01-02", "")).toMatch(/^2026-01-02T00:00:00[+-]\d\d:\d\d$/);
  });

  it("file pre-check: type and size", () => {
    expect(precheckFile(new File(["x"], "a.exe"))).toMatch(/Only PNG/);
    expect(precheckFile(new File([], "a.png"))).toMatch(/empty/);
    expect(precheckFile(new File(["x"], "a.png"))).toBeNull();
  });
});

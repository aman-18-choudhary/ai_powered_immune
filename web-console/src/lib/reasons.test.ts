import { describe, expect, it } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { REASONS, humanise, isKnownReason, reasonInfo } from "./reasons";
import { rawHeroRun, fixtures } from "@/mocks/fixture";

const ROOT = resolve(__dirname, "../../../");
const read = (p: string) => (existsSync(resolve(ROOT, p)) ? readFileSync(resolve(ROOT, p), "utf8") : "");
const CODE = /^[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+$/;

/** Reason codes the services emit, harvested from the places they are declared. */
function serviceCodes(): Set<string> {
  const out = new Set<string>();
  const add = (re: RegExp, src: string) => {
    for (const m of src.matchAll(re)) if (m[1] && CODE.test(m[1])) out.add(m[1]);
  };
  const policy = read("services/txn_guard/txn_guard/policy.py");
  const service = read("services/txn_guard/txn_guard/service.py");
  const reasons = read("services/txn_guard/txn_guard/reasons.py");
  const rules = read("services/txn_guard/txn_guard/rules.py");
  const callRules = read("services/call_guard/call_guard/rules.py");
  const callModel = read("services/call_guard/call_guard/model.py");
  // Trigger("CODE", ...) and BASELINES keys in txn-guard feature reasons
  add(/Trigger\(\s*"([A-Z_]+)"/g, reasons);
  add(/^\s+"([A-Z_]+)": \{/gm, reasons);
  // OVERLAY_CODES frozenset members and module-level *_CODE / NO_RISK constants
  const ov = policy.match(/OVERLAY_CODES = frozenset\(\s*\{([\s\S]*?)\}\s*\)/);
  if (ov?.[1]) add(/"([A-Z_]+)"/g, ov[1]);
  add(/^(?:LATE_CODE|LATE_ANTIBODY_CODE|NO_RISK|DAMPER_CODE)\s*=\s*"([A-Z_]+)"/gm, service + reasons + policy);
  add(/Overlay\(\s*"([A-Z_]+)"/g, policy);
  // call-guard: the weight table and the classifier/no-risk codes
  const w = callRules.match(/_WEIGHT[^=]*=\s*\{([\s\S]*?)\}/);
  if (w?.[1]) add(/"([A-Z_]+)"\s*:/g, w[1]);
  add(/code="([A-Z_]+)"/g, callModel);
  add(/return Reason\(code="([A-Z_]+)"/g, callModel);
  add(/"(ANTIBODY_MATCH(?:_KNOWN_PAYEE)?)"/g, rules + policy);
  return out;
}

describe("reason dictionary", () => {
  it("covers every reason code in the recorded run and holds fixture", () => {
    const seen = new Set<string>();
    const walk = (o: unknown) => {
      if (Array.isArray(o)) o.forEach(walk);
      else if (o && typeof o === "object") {
        const r = o as Record<string, unknown>;
        if (Array.isArray(r.reasons))
          for (const x of r.reasons) if (x && typeof x === "object" && typeof (x as { code?: unknown }).code === "string") seen.add((x as { code: string }).code);
        Object.values(r).forEach(walk);
      }
    };
    walk(rawHeroRun);
    walk(fixtures.holds);
    expect(seen.size).toBeGreaterThan(5);
    const missing = [...seen].filter((c) => !isKnownReason(c));
    expect(missing).toEqual([]);
  });

  it("covers the codes the services declare (greppable enums)", () => {
    const codes = serviceCodes();
    if (codes.size === 0) return; // sources not available (running outside the repo)
    expect(codes.size).toBeGreaterThanOrEqual(15);
    for (const c of ["ACTIVE_SCAM_CALL", "ANTIBODY_MATCH", "LATE_ANTIBODY_POST_SETTLEMENT", "SCRIPT_CLASSIFIER_MATCH", "RAIL_TYPICAL_AMOUNT_DAMPER", "CREDENTIAL_ASK"])
      expect(codes.has(c)).toBe(true);
    const missing = [...codes].filter((c) => !isKnownReason(c));
    expect(missing).toEqual([]);
  });

  it("every entry has a label and a one-line meaning", () => {
    for (const [code, info] of Object.entries(REASONS)) {
      expect(CODE.test(code) || /^[A-Z]+$/.test(code)).toBe(true);
      expect(info.label.length).toBeGreaterThan(2);
      expect(info.meaning).toMatch(/\.$/);
      expect(info.meaning.includes("\n")).toBe(false);
    }
  });

  it("unknown codes fall back gracefully", () => {
    const i = reasonInfo("BRAND_NEW_SIGNAL_V2");
    expect(i.known).toBe(false);
    expect(i.label).toBe("Brand new signal v2");
    expect(humanise("")).toBe("Unknown");
  });
});

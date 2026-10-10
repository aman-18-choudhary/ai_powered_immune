import { describe, expect, it } from "vitest";
import { LANGS, STRINGS, t, type CitizenKey } from "@/lib/i18n/citizen";
import { MAX_CHARS, parseVerdict, prepareMessage, SAMPLES } from "./verdict";

describe("i18n table", () => {
  const enKeys = Object.keys(STRINGS.en).sort();
  it("has exactly the same keys in all four languages", () => {
    for (const l of LANGS) expect(Object.keys(STRINGS[l]).sort(), l).toEqual(enKeys);
  });
  it("has no empty strings and keeps placeholders consistent across languages", () => {
    for (const l of LANGS)
      for (const k of enKeys as CitizenKey[]) {
        const s = STRINGS[l][k];
        expect(s.trim().length, `${l}.${k}`).toBeGreaterThan(0);
        const ph = (x: string) => (x.match(/\{[a-z]+\}/g) ?? []).sort().join(",");
        expect(ph(s), `${l}.${k}`).toBe(ph(STRINGS.en[k]));
      }
  });
  it("non-English tables are really translated (not copies of English)", () => {
    for (const l of LANGS.filter((x) => x !== "en")) {
      const same = (enKeys as CitizenKey[]).filter((k) => STRINGS[l][k] === STRINGS.en[k]);
      expect(same, l).toEqual([]);
    }
  });
  it("interpolates variables", () => {
    expect(t("en", "counter", { n: 12, max: MAX_CHARS })).toBe("12 of 4000 characters");
    expect(t("hi", "overLimit", { max: 4000 })).toContain("4000");
  });
});

describe("verdict parsing and message preparation", () => {
  it("narrows a real response and clamps the score", () => {
    const v = parseVerdict({
      label: "likely_scam", score: 1.4, lang: "hi", uncertain: false,
      reasons: [{ code: "URGENCY", text: "t", weight: 0.15 }, { nope: 1 }],
      advice: { key: "likely_scam", steps: ["a", "b", 3] },
      thresholds: { suspicious: 0.35, likely_scam: 0.7 },
    });
    expect(v).toMatchObject({ label: "likely_scam", score: 1, uncertain: false });
    expect(v?.reasons).toHaveLength(1);
    expect(v?.steps).toEqual(["a", "b"]);
  });
  it("unknown labels fail toward suspicious and uncertain; garbage is rejected", () => {
    const v = parseVerdict({ label: "mystery", score: 0.2 });
    expect(v).toMatchObject({ label: "suspicious", uncertain: true });
    expect(parseVerdict({ label: "safe" })).toBeNull();
    expect(parseVerdict(null)).toBeNull();
  });
  it("truncates beyond 4000 characters and reports it", () => {
    const p = prepareMessage("x".repeat(MAX_CHARS + 1));
    expect(p.truncated).toBe(true);
    expect(p.text).toHaveLength(MAX_CHARS);
    expect(p.length).toBe(MAX_CHARS + 1);
    expect(prepareMessage("  hi  ")).toEqual({ text: "hi", truncated: false, length: 2 });
  });
  it("ships three samples", () => {
    expect(SAMPLES.map((s) => s.key)).toEqual(["sample1", "sample2", "sample3"]);
  });
});

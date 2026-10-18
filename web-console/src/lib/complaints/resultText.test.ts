import { describe, expect, it } from "vitest";
import { resultText } from "./format";

// Evidence wording for analysis results (accuracy audit 2026-10-17/18). Pure function, no mocks.
describe("resultText", () => {
  it("never says safe or low risk when nothing was found", () => {
    const t = resultText("LOW", 0, "no_indicators_found");
    expect(t).toBe("No scam indicators found");
    expect(t.toLowerCase()).not.toContain("safe");
    expect(t.toLowerCase()).not.toContain("low risk");
  });
  it("names insufficient evidence and not-assessed results", () => {
    expect(resultText(null, null, "insufficient_evidence")).toBe("Insufficient evidence");
    expect(resultText(null, null, "not_assessed")).toBe("Not assessed");
    expect(resultText(null, null, undefined)).toBe("Not assessed");
  });
  it("describes indicator strength, not a verdict", () => {
    expect(resultText("CRITICAL", 93, "indicators_found")).toBe("Very strong scam indicators · 93/100");
    expect(resultText("HIGH", 80, "indicators_found")).toBe("Strong scam indicators · 80/100");
    expect(resultText("LOW", 15, "indicators_found")).toBe("Weak scam indicators · 15/100");
  });
  it("marks model-only scores as a prediction", () => {
    expect(resultText("MEDIUM", 60, "model_prediction_only")).toBe("Moderate · model prediction only · 60/100");
  });
  it("older analyses without an assessment never read a low score as clean", () => {
    expect(resultText("LOW", 0, undefined)).toBe("Weak or no indicators · 0/100");
  });
});

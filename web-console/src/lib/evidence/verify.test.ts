import { describe, expect, it } from "vitest";
import { fixtures } from "@/mocks/fixture";
import type { VerifyResult } from "@/types/api";
import { exitMeaning, verifyView } from "./verify";

const base = fixtures.verify;

describe("verify result mapping", () => {
  it("PASS_PINNED with exit 0 is a pass and explains the exit code", () => {
    const v = verifyView({ ...base, status: "PASS_PINNED", exit_code: 0 });
    expect(v.tone).toBe("safe");
    expect(v.exit.meaning).toMatch(/Exit 0/);
    expect(v.lines.some((l) => l.kind === "pass")).toBe(true);
  });
  it("INTEGRITY_OK_UNPINNED (exit 2) is a warning, not a pass", () => {
    const v = verifyView({ ...base, status: "INTEGRITY_OK_UNPINNED", exit_code: 2 });
    expect(v.tone).toBe("warn");
    expect(v.summary).toMatch(/does not prove who/);
  });
  it("FAIL is danger; incoherent status/exit pairs and unknown statuses fail closed", () => {
    expect(verifyView({ ...base, status: "FAIL", exit_code: 1 }).tone).toBe("danger");
    expect(verifyView({ ...base, status: "PASS_PINNED", exit_code: 1 }).status).toBe("FAIL");
    expect(verifyView({ ...base, status: "WHATEVER" as VerifyResult["status"], exit_code: 0 }).status).toBe("FAIL");
  });
  it("exit code table", () => {
    expect(exitMeaning(1)).toMatch(/failed/);
    expect(exitMeaning(7)).toMatch(/unexpected/);
  });
});

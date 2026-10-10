import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import type { HoldView } from "@/types/api";
import {
  deadlineInfo, explainResolveError, filterQueue, historyChain, mergeView, overdueBy, sortQueue, validateNote, withOptimistic,
} from "./queue";
import { fixtures } from "@/mocks/fixture";

const base = (o: Partial<HoldView>): HoldView => ({
  txn_ref: "txn_a", bank: "bank_a", state: "open", decision: "hold_verify", decision_seq: 1, score: 0.9, reasons: [],
  rail: "UPI", amount_bucket: "10k-100k", amount_inr: "16000.00", created_t: 100, deadline_t: 220, ...o,
});

describe("queue ordering", () => {
  it("sorts by soonest deadline, then creation time, then ref", () => {
    const hs = [
      base({ txn_ref: "txn_c", deadline_t: 500 }),
      base({ txn_ref: "txn_b", deadline_t: 200, created_t: 90 }),
      base({ txn_ref: "txn_a", deadline_t: 200, created_t: 80 }),
    ];
    expect(sortQueue(hs).map((h) => h.txn_ref)).toEqual(["txn_a", "txn_b", "txn_c"]);
  });
  it("orders the recorded holds by deadline and filters open/resolved", () => {
    const list = sortQueue(fixtures.holds.holds);
    for (let i = 1; i < list.length; i++) expect(list[i]!.deadline_t).toBeGreaterThanOrEqual(list[i - 1]!.deadline_t);
    expect(filterQueue(list, "open").every((h) => h.state === "open")).toBe(true);
    expect(filterQueue(list, "open").length + filterQueue(list, "resolved").length).toBe(list.length);
  });
});

describe("countdown", () => {
  it("counts down against the simulated clock and flags overdue without resolving", () => {
    const h = base({ created_t: 100, deadline_t: 220 });
    expect(deadlineInfo(h, 130)).toMatchObject({ remaining: 90, total: 120, overdue: false });
    const o = deadlineInfo(h, 250);
    expect(o.overdue).toBe(true);
    expect(o.remaining).toBe(-30);
    expect(overdueBy(o.remaining)).toBe("30s overdue");
    expect(overdueBy(-125)).toBe("2m 05s overdue");
  });
  it("a resolved hold is never overdue", () => {
    expect(deadlineInfo(base({ state: "blocked" }), 9999).overdue).toBe(false);
  });
});

describe("optimistic resolve", () => {
  it("applies the pending action without mutating the server list and rolls back by dropping it", () => {
    const server = [base({ txn_ref: "txn_a" }), base({ txn_ref: "txn_b" })];
    const frozen = JSON.stringify(server);
    const p = { txn_a: { action: "confirm_block" as const, before: server[0]! } };
    const view = withOptimistic(server, p);
    expect(view[0]!.state).toBe("blocked");
    expect(view[1]!.state).toBe("open");
    expect(JSON.stringify(server)).toBe(frozen);
    expect(withOptimistic(server, {})[0]!.state).toBe("open"); // rollback
  });
  it("merges the authoritative response over the list", () => {
    const server = [base({ txn_ref: "txn_a" })];
    const next = mergeView(server, base({ txn_ref: "txn_a", state: "released", decision_seq: 2 }));
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({ state: "released", decision_seq: 2 });
  });
});

describe("error explanations name the cause", () => {
  it("409 -> stale decision_seq", () => {
    const f = explainResolveError(new ApiError(409, "conflict", "x", "hold_conflict"), "confirm_block");
    expect(f.title).toMatch(/Another change arrived/);
    expect(f.detail).toMatch(/stale decision_seq/);
    expect(f.refresh).toBe(true);
  });
  it("403 -> role", () => {
    const f = explainResolveError(new ApiError(403, "forbidden", "x"), "release");
    expect(f.title).toMatch(/role/);
    expect(f.detail).toMatch(/release the hold/);
  });
  it("network and unknown", () => {
    expect(explainResolveError(new ApiError(0, "network", "x"), "release").title).toMatch(/unreachable/);
    expect(explainResolveError(new Error("boom"), "release").title).toMatch(/Could not resolve/);
  });
});

describe("audit note guard", () => {
  it("requires a note of sensible length", () => {
    expect(validateNote("   ")).toMatchObject({ ok: false, problem: "empty" });
    expect(validateNote("too short")).toMatchObject({ ok: false, problem: "short" });
    expect(validateNote("x".repeat(300))).toMatchObject({ ok: false, problem: "long" });
  });
  it("rejects digit runs and email addresses (PII)", () => {
    expect(validateNote("Called payer, account 123456789012 is a mule")).toMatchObject({ ok: false, problem: "digits" });
    expect(validateNote("payee phone 98765 43210 confirmed")).toMatchObject({ ok: false, problem: "digits" });
    expect(validateNote("mail ravi@example.com about it ok")).toMatchObject({ ok: false, problem: "email" });
  });
  it("accepts a clean note and normalises whitespace", () => {
    expect(validateNote("  Payee   matches the known   mule pattern.  ")).toEqual({ ok: true, note: "Payee matches the known mule pattern." });
    expect(validateNote("Amount 16000 is 34x typical")).toMatchObject({ ok: true });
  });
});

describe("history chain", () => {
  it("renders allow -> step_up -> hold_verify", () => {
    const steps = [
      { seq: 1, decision: "step_up", from: "allow", t: 1, trigger: "initial" as const },
      { seq: 2, decision: "hold_verify", from: "step_up", t: 2, trigger: "antibody" as const },
    ];
    expect(historyChain(steps, "hold_verify")).toEqual(["allow", "step_up", "hold_verify"]);
  });
  it("falls back to the current decision", () => {
    expect(historyChain([], "hold_verify")).toEqual(["hold_verify"]);
    expect(historyChain([], "hold_verify", "step_up")).toEqual(["step_up", "hold_verify"]);
  });
});

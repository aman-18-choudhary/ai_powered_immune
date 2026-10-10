import { describe, expect, it } from "vitest";
import { heroEvents } from "@/mocks/fixture";
import { applyEvents, initialRun, selectKpis, selectTimeToBlock, victimCall, holdsList } from "./reducer";
import type { ScenarioSnapshot, StreamEvent } from "@/types/api";
import { ZERO_COUNTERS } from "@/types/guards";

const all = heroEvents();
const snapshotIdx = all.findIndex((e) => e.kind === "scenario.state");
const live = all.filter((e) => e.kind !== "scenario.state");

describe("event reducer over the recorded hero run", () => {
  it("parses every recorded event (no unknown kinds in the fixture)", () => {
    expect(all.length).toBe(158);
    expect(snapshotIdx).toBe(0);
  });

  it("replays to the expected end state", () => {
    const s = applyEvents(initialRun(), live);
    expect(s.status).toBe("finished");
    expect(s.steps.map((x) => x.key)).toEqual([
      "call_begins", "call_flagged", "transfer_held", "analyst_confirms", "antibody_shared", "bank_b_blocks", "cash_out", "evidence_sealed",
    ]);
    expect(s.ledger).toHaveLength(12);
    expect(s.antibodies).toHaveLength(1);
    expect(s.antibodies[0]?.applied[0]?.bank).toBe("bank_b");
    expect(s.antibodies[0]?.applied[0]?.latency_ms).toBeGreaterThan(0);
    expect(s.finished?.victimBProtected).toBe(true);
    expect(s.lastEventId).toBe(157);
    expect(Object.keys(s.rings)).toHaveLength(1);
  });

  it("keeps ledger sequence numbers and event ids strictly increasing", () => {
    const s = applyEvents(initialRun(), live);
    const seqs = s.ledger.map((l) => l.seq);
    expect([...seqs].sort((a, b) => a - b)).toEqual(seqs);
    const ids = s.incidents.map((i) => i.id);
    expect([...ids].sort((a, b) => a - b)).toEqual(ids);
  });

  it("joins hold records with their scored transaction (victim, hero)", () => {
    const s = applyEvents(initialRun(), live);
    const holds = holdsList(s);
    expect(holds.length).toBe(5);
    const first = holds[0];
    expect(first?.victim).toBe("A");
    expect(first?.hero).toBe(true);
    expect(first?.state).toBe("blocked");
    const bankB = holds.filter((h) => h.bank === "bank_b");
    expect(bankB.length).toBe(3);
    expect(bankB.every((h) => h.victim === "B")).toBe(true);
    expect(bankB[0]?.reasons.some((r) => r.code === "ANTIBODY_MATCH")).toBe(true);
  });

  it("derives call views: victim A alerts, victim B stays below threshold", () => {
    const s = applyEvents(initialRun(), live);
    expect(victimCall(s, "A")?.alert).not.toBeNull();
    const b = victimCall(s, "B");
    expect(b?.alert).toBeNull();
    expect(Math.max(...(b?.chunks.map((c) => c.score) ?? [0]))).toBeLessThan(0.7);
  });

  it("selectors: KPIs and time to block", () => {
    const s = applyEvents(initialRun(), live);
    const k = selectKpis(s);
    expect(k.callsFlagged).toBe(2);
    expect(k.transfersHeld).toBe(5);
    expect(k.antibodiesPublished).toBe(1);
    expect(k.ledgerEntries).toBe(12);
    expect(k.medianLatencyMs).toBeCloseTo(s.antibodies[0]?.applied[0]?.latency_ms ?? 0, 5);
    expect(selectTimeToBlock(s)).toBeCloseTo(60, 3);
  });

  it("batching is equivalent to one-by-one application", () => {
    const one = live.reduce((st, e) => applyEvents(st, [e]), initialRun());
    const batch = applyEvents(initialRun(), live);
    expect(JSON.stringify(batch)).toEqual(JSON.stringify(one));
  });

  it("does not mutate the previous state (new identities per batch)", () => {
    const a = applyEvents(initialRun(), live.slice(0, 30));
    const frozenLedger = a.ledger.length;
    const frozenCalls = Object.keys(a.calls).length;
    const b = applyEvents(a, live.slice(30, 100));
    expect(a.ledger.length).toBe(frozenLedger);
    expect(Object.keys(a.calls).length).toBe(frozenCalls);
    expect(b).not.toBe(a);
    expect(b.ledger).not.toBe(a.ledger);
  });
});

describe("snapshot then live", () => {
  const cut = 60;
  const snapshot: ScenarioSnapshot = {
    ...(all[0] as ScenarioSnapshot),
    status: "running",
    step: "analyst_confirms",
    counters: { ...ZERO_COUNTERS, call_alerts: 2 },
    last_event_id: live[cut - 1]?.id ?? 0,
    events: live.slice(0, cut) as StreamEvent[],
  };

  it("rebuilds the same state as full replay once the remaining live events arrive", () => {
    const viaSnapshot = applyEvents(applyEvents(initialRun(), [snapshot]), live.slice(cut));
    const viaReplay = applyEvents(initialRun(), live);
    expect(viaSnapshot.ledger).toEqual(viaReplay.ledger);
    expect(viaSnapshot.antibodies).toEqual(viaReplay.antibodies);
    expect(viaSnapshot.holds).toEqual(viaReplay.holds);
    expect(viaSnapshot.status).toBe("finished");
    expect(viaSnapshot.lastEventId).toBe(157);
  });

  it("takes status, counters and step from the snapshot, not from the replay", () => {
    const s = applyEvents(initialRun(), [snapshot]);
    expect(s.status).toBe("running");
    expect(s.step).toBe("analyst_confirms");
    expect(s.counters.call_alerts).toBe(2);
    expect(s.lastEventId).toBeGreaterThanOrEqual(live[cut - 1]?.id ?? 0);
  });

  it("a second snapshot (seek / resync) replaces prior state instead of appending", () => {
    const first = applyEvents(initialRun(), live);
    const again = applyEvents(first, [snapshot]);
    expect(again.ledger.length).toBeLessThan(first.ledger.length);
    expect(again.finished).toBeNull();
  });

  it("scenario.started for a new run resets accumulated state", () => {
    const first = applyEvents(initialRun(), live);
    const restart = live.find((e) => e.kind === "scenario.started") as StreamEvent;
    const next = applyEvents(first, [{ ...restart, run_id: "newrun" }]);
    expect(next.runId).toBe("newrun");
    expect(next.ledger).toHaveLength(0);
    expect(next.finished).toBeNull();
    expect(next.status).toBe("running");
  });
});

describe("caps", () => {
  it("bounds the transaction list", () => {
    const evs: StreamEvent[] = [];
    for (let i = 1; i <= 600; i++) {
      evs.push({
        id: i, t: i, wall: "", kind: "txn.scored", txn_ref: `txn_${i}`, bank: "bank_a", rail: "UPI", amount_bucket: "<1k",
        decision: "allow", score: 0, reasons: [], seq: 1, victim: null, hero: false,
      });
    }
    const s = applyEvents(initialRun(), evs);
    expect(s.txns.length).toBe(400);
    expect(s.txns[0]?.txn_ref).toBe("txn_201");
  });
});

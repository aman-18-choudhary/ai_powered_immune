import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StreamClient, backoffDelay, streamUrl, type ConnState, type WsLike } from "./stream";
import { GapTracker } from "./gap";
import type { StreamEvent } from "@/types/api";
import { rawHeroRun } from "@/mocks/fixture";

class FakeWs implements WsLike {
  static all: FakeWs[] = [];
  onopen: WsLike["onopen"] = null;
  onmessage: WsLike["onmessage"] = null;
  onclose: WsLike["onclose"] = null;
  onerror: WsLike["onerror"] = null;
  closed = false;
  constructor(public url: string) {
    FakeWs.all.push(this);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.({});
  }
  send(o: unknown) {
    this.onmessage?.({ data: JSON.stringify(o) });
  }
  drop(code = 1006) {
    this.onclose?.({ code });
  }
}

const raw = rawHeroRun as Record<string, unknown>[];
const snapshot = raw[0] as Record<string, unknown>;
const live = raw.slice(1);

function make() {
  const batches: StreamEvent[][] = [];
  const states: ConnState[] = [];
  const onResync = vi.fn();
  const client = new StreamClient({
    getUrl: () => "ws://test/api/stream?token=T",
    onBatch: (b) => batches.push(b),
    onState: (s) => states.push(s),
    onResync,
    factory: (u) => new FakeWs(u),
    schedule: (cb) => {
      setTimeout(cb, 16);
    },
    random: () => 0.5,
  });
  return { client, batches, states, onResync };
}

beforeEach(() => {
  FakeWs.all = [];
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

describe("StreamClient", () => {
  it("connects, applies snapshot then live events in order, batched per frame", () => {
    const { client, batches, states } = make();
    client.start();
    const ws = FakeWs.all[0] as FakeWs;
    ws.open();
    expect(states).toContain("open");
    ws.send(snapshot);
    for (const e of live.slice(0, 5)) ws.send(e);
    expect(batches).toHaveLength(0); // nothing delivered before the frame
    vi.advanceTimersByTime(20);
    expect(batches).toHaveLength(1);
    const ids = (batches[0] as StreamEvent[]).map((e) => e.id);
    expect(ids).toEqual([0, 1, 2, 3, 4, 5]);
    expect((batches[0] as StreamEvent[])[0]?.kind).toBe("scenario.state");
  });

  it("drops duplicates", () => {
    const { client, batches } = make();
    client.start();
    const ws = FakeWs.all[0] as FakeWs;
    ws.send(snapshot);
    ws.send(live[0]);
    ws.send(live[0]);
    ws.send(live[1]);
    vi.advanceTimersByTime(20);
    expect((batches[0] as StreamEvent[]).map((e) => e.id)).toEqual([0, 1, 2]);
  });

  it("detects an id gap, reports a resync and reconnects for a fresh snapshot", () => {
    const { client, batches, onResync } = make();
    client.start();
    const ws = FakeWs.all[0] as FakeWs;
    ws.open();
    ws.send(snapshot);
    ws.send(live[0]); // id 1
    ws.send(live[2]); // id 3: id 2 was missed
    expect(onResync).toHaveBeenCalledTimes(1);
    expect(ws.closed).toBe(true);
    vi.advanceTimersByTime(1000);
    expect(FakeWs.all).toHaveLength(2);
    // the gap event itself must not have been delivered
    vi.advanceTimersByTime(20);
    const delivered = batches.flat().map((e) => e.id);
    expect(delivered).not.toContain(3);
  });

  it("reconnects with exponential backoff after an unclean close and resets after a snapshot", () => {
    const { client } = make();
    client.start();
    (FakeWs.all[0] as FakeWs).drop(1006);
    vi.advanceTimersByTime(backoffDelay(0, 0.5) - 1);
    expect(FakeWs.all).toHaveLength(1);
    vi.advanceTimersByTime(2);
    expect(FakeWs.all).toHaveLength(2);
    (FakeWs.all[1] as FakeWs).drop(1006);
    vi.advanceTimersByTime(backoffDelay(1, 0.5) + 1);
    expect(FakeWs.all).toHaveLength(3);
    (FakeWs.all[2] as FakeWs).send(snapshot); // healthy: attempt counter resets
    (FakeWs.all[2] as FakeWs).drop(1006);
    vi.advanceTimersByTime(backoffDelay(0, 0.5) + 1);
    expect(FakeWs.all).toHaveLength(4);
  });

  it("caps the backoff", () => {
    expect(backoffDelay(0)).toBeLessThan(backoffDelay(3));
    expect(backoffDelay(20, 1)).toBeLessThanOrEqual(8000 * 1.15 + 1);
  });

  it("does not reconnect on 4401 (unauthorized) or 4403 (origin refused)", () => {
    const a = make();
    a.client.start();
    (FakeWs.all[0] as FakeWs).drop(4401);
    vi.advanceTimersByTime(60_000);
    expect(FakeWs.all).toHaveLength(1);
    expect(a.states.at(-1)).toBe("unauthorized");
    FakeWs.all = [];
    const b = make();
    b.client.start();
    (FakeWs.all[0] as FakeWs).drop(4403);
    vi.advanceTimersByTime(60_000);
    expect(FakeWs.all).toHaveLength(1);
    expect(b.states.at(-1)).toBe("refused");
  });

  it("reconnects immediately-ish after 4429 (too slow) for a fresh snapshot", () => {
    const { client } = make();
    client.start();
    (FakeWs.all[0] as FakeWs).drop(4429);
    vi.advanceTimersByTime(700);
    expect(FakeWs.all).toHaveLength(2);
  });

  it("ignores junk and oversized messages, and stops cleanly", () => {
    const { client, batches } = make();
    client.start();
    const ws = FakeWs.all[0] as FakeWs;
    ws.onmessage?.({ data: "not json" });
    ws.onmessage?.({ data: JSON.stringify({ id: 1, t: 0, kind: "unknown.kind" }) });
    ws.onmessage?.({ data: new ArrayBuffer(4) });
    vi.advanceTimersByTime(20);
    expect(batches).toHaveLength(0);
    client.stop();
    expect(ws.closed).toBe(true);
    vi.advanceTimersByTime(60_000);
    expect(FakeWs.all).toHaveLength(1);
  });

  it("signed out (no url) reports unauthorized without connecting", () => {
    const states: ConnState[] = [];
    const c = new StreamClient({ getUrl: () => null, onBatch: () => {}, onState: (s) => states.push(s), factory: (u) => new FakeWs(u) });
    c.start();
    expect(FakeWs.all).toHaveLength(0);
    expect(states).toEqual(["unauthorized"]);
  });
});

describe("GapTracker", () => {
  const ev = (id: number, kind = "metric.tick") => ({ id, t: 0, wall: "", kind, counters: {} }) as unknown as StreamEvent;
  it("accepts contiguous ids, flags gaps and duplicates, re-bases on started/snapshot", () => {
    const g = new GapTracker();
    expect(g.accept(ev(1, "scenario.started"))).toBe("ok");
    expect(g.accept(ev(2))).toBe("ok");
    expect(g.accept(ev(2))).toBe("duplicate");
    expect(g.accept(ev(4))).toBe("gap");
    expect(g.accept(ev(1, "scenario.started"))).toBe("ok");
    expect(g.last).toBe(1);
    const snap = { id: 0, t: 0, wall: "", kind: "scenario.state", last_event_id: 40, events: [], counters: {} } as unknown as StreamEvent;
    expect(g.accept(snap)).toBe("ok");
    expect(g.accept(ev(41))).toBe("ok");
  });
});

describe("streamUrl", () => {
  it("builds ws/wss urls with the encoded token", () => {
    expect(streamUrl("a b", { protocol: "http:", host: "h:5173" })).toBe("ws://h:5173/api/stream?token=a%20b");
    expect(streamUrl("t", { protocol: "https:", host: "x.y" })).toBe("wss://x.y/api/stream?token=t");
  });
});

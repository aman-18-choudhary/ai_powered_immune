import heroRun from "./hero-run.json";
import verify from "./verify.json";
import ring from "./ring.json";
import simulate from "./simulate.json";
import holds from "./holds.json";
import benchmark from "./benchmark.json";
import geo from "./geo.json";
import { parseEvent } from "@/types/guards";
import type { BenchmarkSummary, GeoResponse, HoldsResponse, RingGraph, StreamEvent, TakedownResult, VerifyResult } from "@/types/api";

/** Recorded from a real demo-server run (seed 1, hero, speed 20): the server's own bytes. */
export const rawHeroRun: unknown[] = heroRun as unknown[];

export function heroEvents(): StreamEvent[] {
  const out: StreamEvent[] = [];
  for (const r of rawHeroRun) {
    const e = parseEvent(r);
    if (e) out.push(e);
  }
  return out;
}

export const fixtures = {
  verify: verify as unknown as VerifyResult,
  ring: ring as unknown as RingGraph,
  simulate: simulate as unknown as TakedownResult,
  holds: holds as unknown as HoldsResponse,
  benchmark: benchmark as unknown as BenchmarkSummary,
  geo: geo as unknown as GeoResponse,
};

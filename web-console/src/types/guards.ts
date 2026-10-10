import type { Counters, RunStatus, StreamEvent } from "./api";

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const str = (v: unknown): v is string => typeof v === "string";

export const ZERO_COUNTERS: Counters = {
  chunks: 0,
  call_alerts: 0,
  txns_scored: 0,
  holds_created: 0,
  holds_open: 0,
  holds_resolved: 0,
  antibodies_published: 0,
  antibodies_applied: 0,
  antibody_matches: 0,
  ledger_entries: 0,
  geo_points: 0,
  ring_members: 0,
};

export function parseCounters(v: unknown): Counters {
  const out: Counters = { ...ZERO_COUNTERS };
  if (!isRecord(v)) return out;
  for (const k of Object.keys(out) as (keyof Counters)[]) {
    const x = v[k];
    if (num(x)) out[k] = x;
  }
  return out;
}

function reasons(v: unknown): { code: string; weight: number; detail: string }[] {
  if (!Array.isArray(v)) return [];
  const out: { code: string; weight: number; detail: string }[] = [];
  for (const r of v) {
    if (isRecord(r) && str(r.code)) {
      out.push({ code: r.code, weight: num(r.weight) ? r.weight : 0, detail: str(r.detail) ? r.detail : "" });
    }
  }
  return out;
}

/**
 * Runtime narrowing for the WS boundary. Returns null for anything that is not a well-formed
 * known event; unknown kinds are ignored safely (forward compatible).
 */
export function parseEvent(raw: unknown, depth = 0): StreamEvent | null {
  if (!isRecord(raw)) return null;
  const { id, t, kind } = raw;
  if (!num(id) || !num(t) || !str(kind)) return null;
  const base = {
    id,
    t,
    wall: str(raw.wall) ? raw.wall : "",
    wall_offset: num(raw.wall_offset) ? raw.wall_offset : undefined,
    run_id: str(raw.run_id) ? raw.run_id : undefined,
  };
  const s = (k: string) => (str(raw[k]) ? (raw[k] as string) : "");
  const n = (k: string) => (num(raw[k]) ? (raw[k] as number) : 0);
  const victim = raw.victim === "A" || raw.victim === "B" ? raw.victim : null;
  switch (kind) {
    case "scenario.started":
      return { ...base, kind, seed: n("seed"), mode: s("mode"), duration: n("duration"), banks: parseBanks(raw.banks), scenario_kind: s("scenario_kind") || undefined, scenario_note: s("scenario_note") || undefined };
    case "scenario.step":
      if (!str(raw.key)) return null;
      return { ...base, kind, key: raw.key, title: s("title"), caption: s("caption") };
    case "call.chunk":
      if (!str(raw.call_ref)) return null;
      return {
        ...base, kind, call_ref: raw.call_ref, victim, speaker: s("speaker"), text: s("text"),
        score: n("score"), reasons: reasons(raw.reasons), lang: s("lang") || undefined, channel: s("channel") || undefined,
      };
    case "call.alert":
      if (!str(raw.call_ref)) return null;
      return { ...base, kind, call_ref: raw.call_ref, victim, score: n("score"), reasons: reasons(raw.reasons), threshold: n("threshold") };
    case "txn.scored":
      if (!str(raw.txn_ref)) return null;
      return {
        ...base, kind, txn_ref: raw.txn_ref, bank: s("bank"), bank_name: s("bank_name") || undefined, rail: s("rail"),
        amount_bucket: s("amount_bucket"), decision: s("decision"), score: n("score"), reasons: reasons(raw.reasons),
        seq: n("seq"), victim, hero: raw.hero === true,
      };
    case "hold.created":
    case "hold.upgraded":
    case "hold.resolved": {
      if (!str(raw.txn_ref)) return null;
      const h = {
        ...base, txn_ref: raw.txn_ref, bank: s("bank"), decision: s("decision"), decision_seq: n("decision_seq"),
        score: n("score"), reasons: reasons(raw.reasons), rail: s("rail"), amount_bucket: s("amount_bucket"),
        deadline_t: n("deadline_t"), state: s("state"),
      };
      if (kind === "hold.created") return { ...h, kind };
      if (kind === "hold.upgraded") return { ...h, kind, from_decision: s("from_decision") || undefined };
      return { ...h, kind, action: s("action"), resolved_by: s("resolved_by"), source: s("source") };
    }
    case "antibody.published":
      if (!str(raw.antibody_ref)) return null;
      return { ...base, kind, antibody_ref: raw.antibody_ref, antibody_kind: s("antibody_kind"), bank: s("bank") };
    case "antibody.applied":
      if (!str(raw.antibody_ref)) return null;
      return { ...base, kind, antibody_ref: raw.antibody_ref, bank: s("bank"), latency_ms: n("latency_ms") };
    case "ledger.entry":
      return {
        ...base, kind, seq: n("seq"), event_type: s("event_type"), service: s("service"),
        actor_ref: s("actor_ref"), entry_hash_prefix: s("entry_hash_prefix"),
      };
    case "ring.updated":
      return { ...base, kind, ring_id: s("ring_id"), members: n("members"), victims: n("victims"), inflow_bucket: s("inflow_bucket") };
    case "geo.point": {
      const pk = raw.point_kind;
      if (!num(raw.lat) || !num(raw.lon)) return null;
      if (pk !== "scam_call" && pk !== "held_transfer" && pk !== "protected" && pk !== "cash_out") return null;
      return { ...base, kind, lat: raw.lat, lon: raw.lon, point_kind: pk, city: s("city"), state: s("state") };
    }
    case "metric.tick":
      return { ...base, kind, counters: parseCounters(raw.counters) };
    case "scenario.finished": {
      const lm = raw.latency_ms;
      return {
        ...base, kind, reason: s("reason"),
        ledger_entries: num(raw.ledger_entries) ? raw.ledger_entries : undefined,
        antibody_published: typeof raw.antibody_published === "boolean" ? raw.antibody_published : undefined,
        victim_b_protected: typeof raw.victim_b_protected === "boolean" ? raw.victim_b_protected : undefined,
        latency_ms: num(lm) ? lm : null,
      };
    }
    case "scenario.state": {
      if (depth > 0) return null; // snapshots never nest
      const events: StreamEvent[] = [];
      if (Array.isArray(raw.events)) {
        for (const e of raw.events) {
          const p = parseEvent(e, depth + 1);
          if (p) events.push(p);
        }
      }
      const st = s("status");
      return {
        ...base, kind, status: (st || "idle") as RunStatus,
        speed: n("speed"), step: str(raw.step) ? raw.step : null, duration: n("duration"),
        counters: parseCounters(raw.counters), last_event_id: num(raw.last_event_id) ? raw.last_event_id : undefined,
        banks: parseBanks(raw.banks), mode: s("mode") || undefined, seed: num(raw.seed) ? raw.seed : undefined,
        scenario_kind: s("scenario_kind") || undefined, scenario_note: s("scenario_note") || undefined,
        pacing: s("pacing") || undefined, wall_duration: num(raw.wall_duration) ? raw.wall_duration : undefined, events,
      };
    }
    default:
      return null;
  }
}

function parseBanks(v: unknown): { bank_a: string; bank_b: string } | undefined {
  if (isRecord(v) && str(v.bank_a) && str(v.bank_b)) return { bank_a: v.bank_a, bank_b: v.bank_b };
  return undefined;
}

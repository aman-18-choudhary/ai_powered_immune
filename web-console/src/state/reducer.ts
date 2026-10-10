import type {
  Banks,
  Counters,
  Decision,
  PointKind,
  Reason,
  RunStatus,
  ScenarioSnapshot,
  StreamEvent,
} from "@/types/api";
import { ZERO_COUNTERS } from "@/types/guards";

export interface ChunkView {
  id: number;
  t: number;
  text: string;
  score: number;
  speaker: string;
  reasons: Reason[];
}
export interface CallView {
  ref: string;
  victim: "A" | "B" | null;
  lang?: string;
  chunks: ChunkView[];
  score: number;
  alert: { id: number; t: number; score: number; threshold: number; reasons: Reason[] } | null;
}
export interface TxnView {
  id: number;
  t: number;
  txn_ref: string;
  bank: string;
  bank_name?: string;
  rail: string;
  bucket: string;
  decision: Decision;
  score: number;
  reasons: Reason[];
  victim: "A" | "B" | null;
  hero: boolean;
}
export interface HoldRecord {
  txn_ref: string;
  bank: string;
  decision: Decision;
  decision_seq: number;
  score: number;
  reasons: Reason[];
  rail: string;
  bucket: string;
  createdT: number;
  deadlineT: number;
  state: string;
  resolvedT?: number;
  action?: string;
  source?: string;
  resolvedBy?: string;
  upgradedFrom?: string;
  /** Every decision this hold has had, oldest first (decision_seq diff for the analyst view). */
  history: HoldStep[];
  victim: "A" | "B" | null;
  hero: boolean;
}
export interface HoldStep {
  seq: number;
  decision: string;
  from?: string;
  t: number;
  /** Which signal produced this step, inferred from the reason codes. */
  trigger: HoldTrigger;
}
export type HoldTrigger = "initial" | "call_risk" | "antibody" | "late_call_risk" | "late_antibody" | "resolved";

/** Derive what caused a decision from the reason codes (the services' own codes). */
export function inferTrigger(codes: string[], kind: "created" | "upgraded" | "resolved"): HoldTrigger {
  if (kind === "resolved") return "resolved";
  const has = (c: string) => codes.includes(c);
  if (has("LATE_ANTIBODY_POST_SETTLEMENT")) return "late_antibody";
  if (has("LATE_CALL_RISK_POST_SETTLEMENT")) return "late_call_risk";
  if (kind === "upgraded") {
    if (has("ANTIBODY_MATCH") || has("ANTIBODY_MATCH_KNOWN_PAYEE")) return "antibody";
    if (has("ACTIVE_SCAM_CALL") || has("CALL_RISK_AMOUNT_GUARD")) return "call_risk";
  }
  return "initial";
}
export interface AntibodyRecord {
  ref: string;
  kind: string;
  bank: string;
  publishedT: number;
  applied: { bank: string; latency_ms: number; t: number }[];
}
export interface LedgerRecord {
  id: number;
  seq: number;
  t: number;
  event_type: string;
  service: string;
  actor_ref: string;
  hash: string;
}
export interface RingRecord {
  ring_id: string;
  members: number;
  victims: number;
  inflow_bucket: string;
  t: number;
}
export interface GeoRecord {
  id: number;
  t: number;
  lat: number;
  lon: number;
  kind: PointKind;
  city: string;
  state: string;
}
export interface StepRecord {
  id: number;
  key: string;
  title: string;
  caption: string;
  t: number;
  /** Position on the pacing timeline (seconds at 1x). */
  w: number;
}
export type Severity = "critical" | "warn" | "safe" | "intel" | "info";
export interface IncidentRecord {
  id: number;
  t: number;
  type: "call_alert" | "hold" | "upgrade" | "blocked" | "released" | "antibody" | "applied" | "cash_out" | "protected" | "ring";
  severity: Severity;
  title: string;
  detail: string;
  bank?: string;
  ref?: string;
}
export interface FinishedRecord {
  t: number;
  reason: string;
  ledgerEntries?: number;
  antibodyPublished?: boolean;
  victimBProtected?: boolean;
  latencyMs?: number | null;
}

export interface RunState {
  runId: string | null;
  status: RunStatus;
  t: number;
  duration: number;
  speed: number;
  step: string | null;
  seed: number | null;
  mode: string | null;
  banks: Banks | null;
  scenarioNote: string | null;
  /** "cinematic" (compressed gaps) or "real"; null until the server says. */
  pacing: string | null;
  /** Length and current position of the pacing timeline, seconds at 1x. */
  wallDuration: number;
  wallOffset: number;
  counters: Counters;
  lastEventId: number;
  steps: StepRecord[];
  calls: Record<string, CallView>;
  callOrder: string[];
  txns: TxnView[];
  holds: Record<string, HoldRecord>;
  holdOrder: string[];
  antibodies: AntibodyRecord[];
  ledger: LedgerRecord[];
  rings: Record<string, RingRecord>;
  geo: GeoRecord[];
  incidents: IncidentRecord[];
  finished: FinishedRecord | null;
  /** True once any event was applied for the current run (distinguishes "no data" from "idle"). */
  hasData: boolean;
}

export const CAPS = { txns: 400, ledger: 300, geo: 400, incidents: 600, chunksPerCall: 200 } as const;

export function initialRun(): RunState {
  return {
    runId: null,
    status: "idle",
    t: 0,
    duration: 0,
    speed: 1,
    step: null,
    seed: null,
    mode: null,
    banks: null,
    scenarioNote: null,
    pacing: null,
    wallDuration: 0,
    wallOffset: 0,
    counters: { ...ZERO_COUNTERS },
    lastEventId: 0,
    steps: [],
    calls: {},
    callOrder: [],
    txns: [],
    holds: {},
    holdOrder: [],
    antibodies: [],
    ledger: [],
    rings: {},
    geo: [],
    incidents: [],
    finished: null,
    hasData: false,
  };
}

/** Copy the containers a batch may touch, so React/zustand see new identities. */
function draftOf(s: RunState): RunState {
  return {
    ...s,
    steps: s.steps.slice(),
    calls: { ...s.calls },
    callOrder: s.callOrder.slice(),
    txns: s.txns.slice(),
    holds: { ...s.holds },
    holdOrder: s.holdOrder.slice(),
    antibodies: s.antibodies.map((a) => ({ ...a, applied: a.applied.slice() })),
    ledger: s.ledger.slice(),
    rings: { ...s.rings },
    geo: s.geo.slice(),
    incidents: s.incidents.slice(),
  };
}

function cap<T>(arr: T[], n: number) {
  if (arr.length > n) arr.splice(0, arr.length - n);
}

function addIncident(d: RunState, i: IncidentRecord) {
  d.incidents.push(i);
  cap(d.incidents, CAPS.incidents);
}

function applyOne(d: RunState, e: StreamEvent): void {
  if (e.kind !== "scenario.state") {
    d.hasData = true;
    if (e.id > d.lastEventId) d.lastEventId = e.id;
    if (e.t > d.t) d.t = e.t;
    if (e.wall_offset !== undefined && e.wall_offset > d.wallOffset) d.wallOffset = e.wall_offset;
  }
  switch (e.kind) {
    case "scenario.started": {
      const fresh = initialRun();
      Object.assign(d, fresh, {
        runId: e.run_id ?? null,
        status: "running" as RunStatus,
        duration: e.duration,
        seed: e.seed,
        mode: e.mode,
        banks: e.banks ?? null,
        scenarioNote: e.scenario_note ?? null,
        wallOffset: e.wall_offset ?? 0,
        lastEventId: e.id,
        hasData: true,
        speed: d.speed,
      });
      return;
    }
    case "scenario.step": {
      d.step = e.key;
      d.steps.push({ id: e.id, key: e.key, title: e.title, caption: e.caption, t: e.t, w: e.wall_offset ?? e.t });
      return;
    }
    case "call.chunk": {
      let c = d.calls[e.call_ref];
      if (!c) {
        c = { ref: e.call_ref, victim: e.victim, lang: e.lang, chunks: [], score: 0, alert: null };
        d.callOrder.push(e.call_ref);
      } else {
        c = { ...c, chunks: c.chunks.slice() };
      }
      c.chunks.push({ id: e.id, t: e.t, text: e.text, score: e.score, speaker: e.speaker, reasons: e.reasons });
      cap(c.chunks, CAPS.chunksPerCall);
      c.score = e.score;
      d.calls[e.call_ref] = c;
      return;
    }
    case "call.alert": {
      const prev = d.calls[e.call_ref];
      const base: CallView = prev ?? { ref: e.call_ref, victim: e.victim, chunks: [], score: e.score, alert: null };
      if (!prev) d.callOrder.push(e.call_ref);
      d.calls[e.call_ref] = {
        ...base,
        alert: { id: e.id, t: e.t, score: e.score, threshold: e.threshold, reasons: e.reasons },
      };
      addIncident(d, {
        id: e.id,
        t: e.t,
        type: "call_alert",
        severity: "critical",
        title: "Scam call flagged",
        detail: `Call-guard score ${e.score.toFixed(2)}${e.victim ? ` · victim ${e.victim}` : ""}`,
        ref: e.call_ref,
      });
      return;
    }
    case "txn.scored": {
      d.txns.push({
        id: e.id, t: e.t, txn_ref: e.txn_ref, bank: e.bank, bank_name: e.bank_name, rail: e.rail,
        bucket: e.amount_bucket, decision: e.decision, score: e.score, reasons: e.reasons, victim: e.victim, hero: e.hero,
      });
      cap(d.txns, CAPS.txns);
      return;
    }
    case "hold.created":
    case "hold.upgraded": {
      const txn = d.txns.find((x) => x.txn_ref === e.txn_ref);
      const prev = d.holds[e.txn_ref];
      const rec: HoldRecord = {
        txn_ref: e.txn_ref, bank: e.bank, decision: e.decision, decision_seq: e.decision_seq, score: e.score,
        reasons: e.reasons, rail: e.rail, bucket: e.amount_bucket, createdT: prev?.createdT ?? e.t,
        deadlineT: e.deadline_t, state: e.state || "open", victim: txn?.victim ?? prev?.victim ?? null,
        hero: txn?.hero ?? prev?.hero ?? false,
        upgradedFrom: e.kind === "hold.upgraded" ? e.from_decision : prev?.upgradedFrom,
        history: [
          ...(prev?.history ?? []),
          ...(prev?.history.some((h) => h.seq === e.decision_seq && h.decision === e.decision)
            ? []
            : [
                {
                  seq: e.decision_seq,
                  decision: e.decision,
                  from: e.kind === "hold.upgraded" ? e.from_decision : undefined,
                  t: e.t,
                  trigger: inferTrigger(e.reasons.map((r) => r.code), e.kind === "hold.created" ? "created" : "upgraded"),
                } satisfies HoldStep,
              ]),
        ],
      };
      if (!prev) d.holdOrder.push(e.txn_ref);
      d.holds[e.txn_ref] = rec;
      addIncident(d, {
        id: e.id, t: e.t, type: e.kind === "hold.created" ? "hold" : "upgrade",
        severity: "warn",
        title: e.kind === "hold.created" ? "Transfer held" : "Hold upgraded",
        detail: `${e.rail} · ${e.amount_bucket} · score ${e.score.toFixed(2)}`,
        bank: e.bank, ref: e.txn_ref,
      });
      return;
    }
    case "hold.resolved": {
      const prev = d.holds[e.txn_ref];
      const txn = d.txns.find((x) => x.txn_ref === e.txn_ref);
      const rec: HoldRecord = {
        txn_ref: e.txn_ref, bank: e.bank, decision: e.decision, decision_seq: e.decision_seq, score: e.score,
        reasons: e.reasons, rail: e.rail, bucket: e.amount_bucket, createdT: prev?.createdT ?? e.t,
        deadlineT: e.deadline_t, state: e.state, resolvedT: e.t, action: e.action, source: e.source,
        resolvedBy: e.resolved_by, victim: txn?.victim ?? prev?.victim ?? null, hero: txn?.hero ?? prev?.hero ?? false,
        upgradedFrom: prev?.upgradedFrom,
        history: prev?.history ?? [],
      };
      if (!prev) d.holdOrder.push(e.txn_ref);
      d.holds[e.txn_ref] = rec;
      const blocked = e.state === "blocked" || e.action === "confirm_block";
      addIncident(d, {
        id: e.id, t: e.t, type: blocked ? "blocked" : "released", severity: blocked ? "safe" : "info",
        title: blocked ? "Mule confirmed, transfer blocked" : "Hold released",
        detail: `${e.source === "manual" ? "Manual" : "Automatic"} · ${e.resolved_by}`,
        bank: e.bank, ref: e.txn_ref,
      });
      return;
    }
    case "antibody.published": {
      d.antibodies.push({ ref: e.antibody_ref, kind: e.antibody_kind, bank: e.bank, publishedT: e.t, applied: [] });
      addIncident(d, {
        id: e.id, t: e.t, type: "antibody", severity: "intel", title: "Antibody published",
        detail: `${e.antibody_kind.replace(/_/g, " ")} shared from ${e.bank.replace("bank_", "bank ")}`,
        bank: e.bank, ref: e.antibody_ref,
      });
      return;
    }
    case "antibody.applied": {
      const ab = d.antibodies.find((a) => a.ref === e.antibody_ref);
      const a = { bank: e.bank, latency_ms: e.latency_ms, t: e.t };
      if (ab) ab.applied.push(a);
      else d.antibodies.push({ ref: e.antibody_ref, kind: "unknown", bank: "", publishedT: e.t, applied: [a] });
      addIncident(d, {
        id: e.id, t: e.t, type: "applied", severity: "intel", title: "Antibody applied",
        detail: `${e.bank.replace("bank_", "bank ")} · ${e.latency_ms.toFixed(1)} ms`, bank: e.bank, ref: e.antibody_ref,
      });
      return;
    }
    case "ledger.entry": {
      d.ledger.push({
        id: e.id, seq: e.seq, t: e.t, event_type: e.event_type, service: e.service, actor_ref: e.actor_ref,
        hash: e.entry_hash_prefix,
      });
      cap(d.ledger, CAPS.ledger);
      return;
    }
    case "ring.updated": {
      d.rings[e.ring_id] = {
        ring_id: e.ring_id, members: e.members, victims: e.victims, inflow_bucket: e.inflow_bucket, t: e.t,
      };
      return;
    }
    case "geo.point": {
      d.geo.push({ id: e.id, t: e.t, lat: e.lat, lon: e.lon, kind: e.point_kind, city: e.city, state: e.state });
      cap(d.geo, CAPS.geo);
      if (e.point_kind === "cash_out" || e.point_kind === "protected") {
        addIncident(d, {
          id: e.id, t: e.t, type: e.point_kind, severity: e.point_kind === "protected" ? "safe" : "critical",
          title: e.point_kind === "protected" ? "Victim protected" : "Cash-out point reached",
          detail: `${e.city}, ${e.state}`,
        });
      }
      return;
    }
    case "metric.tick": {
      d.counters = e.counters;
      return;
    }
    case "scenario.finished": {
      d.status = e.reason === "stopped" ? "stopped" : e.reason === "error" ? "error" : "finished";
      d.finished = {
        t: e.t, reason: e.reason, ledgerEntries: e.ledger_entries, antibodyPublished: e.antibody_published,
        victimBProtected: e.victim_b_protected, latencyMs: e.latency_ms,
      };
      return;
    }
    case "scenario.state":
      return; // handled by applySnapshot
  }
}

export function applyEvents(s: RunState, events: StreamEvent[]): RunState {
  if (events.length === 0) return s;
  let d = draftOf(s);
  for (const e of events) {
    if (e.kind === "scenario.state") {
      d = applySnapshotMut(d, e);
    } else {
      applyOne(d, e);
    }
  }
  return d;
}

function applySnapshotMut(d: RunState, snap: ScenarioSnapshot): RunState {
  const keepSpeed = snap.speed || d.speed;
  const n = initialRun();
  n.runId = snap.events.find((x) => x.run_id)?.run_id ?? null;
  n.status = snap.status;
  n.duration = snap.duration;
  n.speed = keepSpeed;
  n.banks = snap.banks ?? null;
  n.pacing = snap.pacing ?? null;
  n.wallDuration = snap.wall_duration ?? 0;
  n.wallOffset = snap.wall_offset ?? 0;
  n.scenarioNote = snap.scenario_note ?? snap.events.find((x) => x.kind === "scenario.started" && x.scenario_note)?.["scenario_note" as never] ?? null;
  n.seed = snap.seed ?? null;
  n.mode = snap.mode ?? null;
  const out = draftOf(n);
  for (const ev of snap.events) {
    if (ev.kind !== "scenario.state") applyOne(out, ev);
  }
  // Authoritative values from the snapshot override whatever replay derived.
  out.status = snap.status;
  out.speed = keepSpeed;
  out.step = snap.step ?? out.step;
  out.counters = snap.counters;
  out.duration = snap.duration || out.duration;
  if (snap.last_event_id !== undefined) out.lastEventId = Math.max(out.lastEventId, snap.last_event_id);
  out.t = Math.max(out.t, snap.t);
  out.wallDuration = snap.wall_duration ?? out.wallDuration;
  out.wallOffset = Math.max(out.wallOffset, snap.wall_offset ?? 0);
  out.pacing = snap.pacing ?? out.pacing;
  if (snap.events.length > 0 || snap.status !== "idle") out.hasData = true;
  return out;
}

// ---- selectors --------------------------------------------------------------------------------

export interface Kpis {
  callsFlagged: number;
  transfersHeld: number;
  holdsOpen: number;
  holdsBlocked: number;
  antibodiesPublished: number;
  antibodiesApplied: number;
  ledgerEntries: number;
  medianLatencyMs: number | null;
}

export function selectKpis(s: RunState): Kpis {
  const holds = s.holdOrder.map((k) => s.holds[k]).filter((h): h is HoldRecord => !!h);
  const latencies = s.antibodies.flatMap((a) => a.applied.map((x) => x.latency_ms)).sort((a, b) => a - b);
  const mid = Math.floor(latencies.length / 2);
  const med =
    latencies.length === 0
      ? null
      : latencies.length % 2
        ? (latencies[mid] ?? null)
        : (((latencies[mid - 1] ?? 0) + (latencies[mid] ?? 0)) / 2);
  const lastLedger = s.ledger.length ? Math.max(...s.ledger.map((l) => l.seq)) : 0;
  return {
    callsFlagged: Math.max(s.counters.call_alerts, Object.values(s.calls).filter((c) => c.alert).length),
    transfersHeld: Math.max(s.counters.holds_created, holds.length),
    holdsOpen: holds.filter((h) => h.state === "open").length,
    holdsBlocked: holds.filter((h) => h.state === "blocked").length,
    antibodiesPublished: Math.max(s.counters.antibodies_published, s.antibodies.length),
    antibodiesApplied: Math.max(
      s.counters.antibodies_applied,
      s.antibodies.reduce((n, a) => n + a.applied.length, 0),
    ),
    ledgerEntries: Math.max(s.counters.ledger_entries, lastLedger),
    medianLatencyMs: med,
  };
}

/** Median seconds (simulated) from hold creation to block. */
export function selectTimeToBlock(s: RunState): number | null {
  const xs: number[] = [];
  for (const k of s.holdOrder) {
    const h = s.holds[k];
    if (h && h.state === "blocked" && h.resolvedT !== undefined) xs.push(h.resolvedT - h.createdT);
  }
  if (xs.length === 0) return null;
  xs.sort((a, b) => a - b);
  const m = Math.floor(xs.length / 2);
  return xs.length % 2 ? (xs[m] ?? null) : (((xs[m - 1] ?? 0) + (xs[m] ?? 0)) / 2);
}

export function victimCall(s: RunState, victim: "A" | "B"): CallView | null {
  for (let i = s.callOrder.length - 1; i >= 0; i--) {
    const c = s.calls[s.callOrder[i] as string];
    if (c && c.victim === victim) return c;
  }
  return null;
}
export function victimCalls(s: RunState, victim: "A" | "B"): CallView[] {
  return s.callOrder.map((r) => s.calls[r]).filter((c): c is CallView => !!c && c.victim === victim);
}
export function holdsList(s: RunState): HoldRecord[] {
  return s.holdOrder.map((k) => s.holds[k]).filter((h): h is HoldRecord => !!h);
}

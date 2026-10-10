/** Types for what demo-server really sends (see services/demo_server/README.md). */

export type Role = "officer" | "analyst" | "citizen" | "admin";
export const ROLES: readonly Role[] = ["officer", "analyst", "citizen", "admin"] as const;

export interface LoginResponse {
  token: string;
  role: Role;
  sub: string;
}

export type RunStatus = "idle" | "starting" | "running" | "paused" | "seeking" | "finished" | "stopped" | "error";

export interface Counters {
  chunks: number;
  call_alerts: number;
  txns_scored: number;
  holds_created: number;
  holds_open: number;
  holds_resolved: number;
  antibodies_published: number;
  antibodies_applied: number;
  antibody_matches: number;
  ledger_entries: number;
  geo_points: number;
  ring_members: number;
}

export interface DemoState {
  run_id: string | null;
  status: RunStatus;
  t: number;
  duration: number;
  speed: number;
  step: string | null;
  counters: Counters;
  simulated: boolean;
  mode?: string;
  seed?: number;
  epoch?: number;
  last_event_id?: number;
  banks?: Banks;
  scenario_kind?: string;
  scenario_note?: string;
}

export interface Banks {
  bank_a: string;
  bank_b: string;
}

export interface Reason {
  code: string;
  weight: number;
  detail: string;
}

export type Decision = "allow" | "step_up" | "hold_verify" | "hold" | "block" | string;
export type PointKind = "scam_call" | "held_transfer" | "protected" | "cash_out";

export interface EventBase {
  id: number;
  t: number;
  wall: string;
  run_id?: string;
}

export interface ScenarioStarted extends EventBase {
  kind: "scenario.started";
  seed: number;
  mode: string;
  duration: number;
  banks?: Banks;
  scenario_kind?: string;
  scenario_note?: string;
}
export interface ScenarioStep extends EventBase {
  kind: "scenario.step";
  key: string;
  title: string;
  caption: string;
}
export interface CallChunk extends EventBase {
  kind: "call.chunk";
  call_ref: string;
  victim: "A" | "B" | null;
  speaker: string;
  text: string;
  score: number;
  reasons: Reason[];
  lang?: string;
  channel?: string;
}
export interface CallAlert extends EventBase {
  kind: "call.alert";
  call_ref: string;
  victim: "A" | "B" | null;
  score: number;
  reasons: Reason[];
  threshold: number;
}
export interface TxnScored extends EventBase {
  kind: "txn.scored";
  txn_ref: string;
  bank: string;
  bank_name?: string;
  rail: string;
  amount_bucket: string;
  decision: Decision;
  score: number;
  reasons: Reason[];
  seq: number;
  victim: "A" | "B" | null;
  hero: boolean;
}
export interface HoldBase extends EventBase {
  txn_ref: string;
  bank: string;
  decision: Decision;
  decision_seq: number;
  score: number;
  reasons: Reason[];
  rail: string;
  amount_bucket: string;
  deadline_t: number;
  state: string;
}
export interface HoldCreated extends HoldBase {
  kind: "hold.created";
}
export interface HoldUpgraded extends HoldBase {
  kind: "hold.upgraded";
  from_decision?: string;
}
export interface HoldResolved extends HoldBase {
  kind: "hold.resolved";
  action: string;
  resolved_by: string;
  source: "auto" | "manual" | string;
}
export interface AntibodyPublished extends EventBase {
  kind: "antibody.published";
  antibody_ref: string;
  /** NB: the envelope's `kind` is the event kind, so the antibody kind is `antibody_kind`. */
  antibody_kind: string;
  bank: string;
}
export interface AntibodyApplied extends EventBase {
  kind: "antibody.applied";
  antibody_ref: string;
  bank: string;
  latency_ms: number;
}
export interface LedgerEntry extends EventBase {
  kind: "ledger.entry";
  seq: number;
  event_type: string;
  service: string;
  actor_ref: string;
  entry_hash_prefix: string;
}
export interface RingUpdated extends EventBase {
  kind: "ring.updated";
  ring_id: string;
  members: number;
  victims: number;
  inflow_bucket: string;
}
export interface GeoPoint extends EventBase {
  kind: "geo.point";
  lat: number;
  lon: number;
  /** NB: `point_kind`, not `kind` (envelope collision). */
  point_kind: PointKind;
  city: string;
  state: string;
}
export interface MetricTick extends EventBase {
  kind: "metric.tick";
  counters: Counters;
}
export interface ScenarioFinished extends EventBase {
  kind: "scenario.finished";
  reason: "completed" | "stopped" | "error" | string;
  ledger_entries?: number;
  antibody_published?: boolean;
  victim_b_protected?: boolean;
  latency_ms?: number | null;
}
export interface ScenarioSnapshot extends EventBase {
  kind: "scenario.state";
  status: RunStatus;
  speed: number;
  step: string | null;
  duration: number;
  counters: Counters;
  last_event_id?: number;
  banks?: Banks;
  mode?: string;
  seed?: number;
  scenario_kind?: string;
  scenario_note?: string;
  events: StreamEvent[];
}

export type StreamEvent =
  | ScenarioStarted
  | ScenarioStep
  | CallChunk
  | CallAlert
  | TxnScored
  | HoldCreated
  | HoldUpgraded
  | HoldResolved
  | AntibodyPublished
  | AntibodyApplied
  | LedgerEntry
  | RingUpdated
  | GeoPoint
  | MetricTick
  | ScenarioFinished
  | ScenarioSnapshot;

// ---- REST views -------------------------------------------------------------------------

export interface HoldView {
  txn_ref: string;
  bank: string;
  bank_name?: string;
  state: string;
  decision: string;
  decision_seq: number;
  score: number;
  reasons: Reason[];
  rail: string;
  amount_bucket: string;
  amount_inr: string;
  payee_ref?: string;
  hero?: boolean;
  created_t: number;
  deadline_t: number;
  overdue?: boolean;
  resolved_by?: string | null;
  call_ref?: string | null;
}
export interface HoldsResponse {
  holds: HoldView[];
}
export interface AntibodyView {
  antibody_ref: string;
  kind: string;
  source_bank: string;
  state: string;
  key_hash_prefix: string;
  created_at: string;
  expires_at: string;
}
export interface AntibodiesResponse {
  antibodies: AntibodyView[];
}
export interface Hotspot {
  city: string;
  state: string;
  lat: number;
  lon: number;
  count: number;
  prev_count: number;
  total: number;
  kinds: Record<string, number>;
  trend: "up" | "down" | "flat" | string;
}
export interface CashOutPoint {
  city: string;
  state: string;
  lat: number;
  lon: number;
  count: number;
}
export interface GeoResponse {
  hotspots: Hotspot[];
  cash_out_points: CashOutPoint[];
  window_s: number;
  t: number;
}
export interface RingNode {
  id: string;
  role: "victim" | "mule" | "cash_out" | string;
  tier: number;
  bank: string | null;
  flags: string[];
  in_inr: string;
  out_inr: string;
}
export interface RingEdge {
  src: string;
  dst: string;
  count: number;
  total_inr: string;
  rails: string[];
  flagged: number;
}
export interface RingGraph {
  ring_id: string;
  computed_from: string;
  horizon_days: number;
  members: string[];
  victims: number;
  inflow_inr: string;
  inflow_bucket: string;
  harm_inr: string;
  nodes: RingNode[];
  edges: RingEdge[];
  truncated?: boolean;
  version?: number;
}
export interface TakedownResult {
  harm_before: string;
  harm_after: string;
  reduction_pct: number;
  removed: string[];
  cut_edges: number;
  cut_edge_list: { src: string; dst: string }[];
}
export interface BestTakedown {
  greedy: TakedownResult;
  optimal?: TakedownResult | null;
  gap_pct?: number | null;
}
export interface Metric {
  value: number | null;
  ci95: [number, number] | null;
}
export interface BenchHeadline {
  precision: Metric;
  recall: Metric;
  fpr: Metric;
  flagged_fpr: Metric;
  flagged_recall: Metric;
  f1: Metric;
  n_benign: number;
  n_scam: number;
}
export interface BenchmarkSummary {
  headline: BenchHeadline;
  per_rail: Record<string, BenchHeadline>;
  per_role?: Record<string, unknown>;
  ablations: Record<string, BenchHeadline & Record<string, unknown>>;
  sensitivity: { label: string; delay_s: number | null; victim_transfer_recall: Metric; mean_share_post_detection_held: Metric }[];
  limitations: string[];
  hero?: { counts: Record<string, number>; seeds: number };
  latency_ms?: { n: number; p50: number; p99: number };
  meta: { seed: number; git_commit: string; runtime_s?: number; model_versions?: Record<string, string> };
  config?: Record<string, number>;
}
export type VerifyStatus = "PASS_PINNED" | "INTEGRITY_OK_UNPINNED" | "FAIL";
export interface VerifyResult {
  exit_code: number;
  status: VerifyStatus;
  lines: string[];
  redacted_seqs: number[];
  entries: number;
  checkpoints: number | null;
  case_id: string;
  pinned_key_id: string;
  public_key_hex: string;
}
export interface CaseView {
  case_id: string;
  title: string;
  case_refs: string[];
  created_at: string;
}
export interface LedgerHead {
  seq: number;
  entry_hash: string;
  count: number;
  public_key_hex: string;
  key_id: string;
  algorithm: string;
}

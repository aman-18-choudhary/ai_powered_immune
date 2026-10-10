import type { HoldView, Reason } from "@/types/api";
import { ApiError } from "@/lib/api/client";
import type { HoldStep, HoldTrigger } from "@/state/reducer";

export type ResolveAction = "confirm_block" | "release";
export type QueueFilter = "open" | "resolved" | "all";

export const isOpen = (h: Pick<HoldView, "state">) => h.state === "open";

/** Soonest deadline first; ties broken by creation time, then ref for a stable order. */
export function sortQueue<T extends Pick<HoldView, "deadline_t" | "created_t" | "txn_ref">>(holds: readonly T[]): T[] {
  return [...holds].sort(
    (a, b) => a.deadline_t - b.deadline_t || a.created_t - b.created_t || a.txn_ref.localeCompare(b.txn_ref),
  );
}

export function filterQueue<T extends Pick<HoldView, "state">>(holds: readonly T[], f: QueueFilter): T[] {
  if (f === "all") return [...holds];
  return holds.filter((h) => (f === "open" ? isOpen(h) : !isOpen(h)));
}

export interface DeadlineInfo {
  /** Simulated seconds left; negative once overdue. */
  remaining: number;
  total: number;
  overdue: boolean;
}

/** Countdown relative to the simulated clock. Overdue holds stay open: nothing auto-resolves. */
export function deadlineInfo(h: Pick<HoldView, "deadline_t" | "created_t" | "state">, simT: number): DeadlineInfo {
  const total = Math.max(1, h.deadline_t - h.created_t);
  const remaining = h.deadline_t - simT;
  return { remaining, total, overdue: isOpen(h) && remaining <= 0 };
}

export function overdueBy(remaining: number): string {
  const s = Math.max(0, Math.ceil(-remaining));
  if (s < 60) return `${s}s overdue`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s overdue`;
}

// ---- optimistic resolve ------------------------------------------------------------------

export interface Pending {
  action: ResolveAction;
  /** The view as it was before the optimistic change, for rollback. */
  before: HoldView;
}

export function resolvedState(a: ResolveAction): "blocked" | "released" {
  return a === "confirm_block" ? "blocked" : "released";
}

/** Apply pending actions on top of server views. Pure: the server list is never mutated. */
export function withOptimistic(views: readonly HoldView[], pending: Record<string, Pending>): HoldView[] {
  return views.map((v) => {
    const p = pending[v.txn_ref];
    if (!p || !isOpen(v)) return v;
    return { ...v, state: resolvedState(p.action), resolved_by: "you (pending)" };
  });
}

/** Merge a fresh server view over the list (resolve response), keeping order. */
export function mergeView(views: readonly HoldView[], next: HoldView): HoldView[] {
  return views.some((v) => v.txn_ref === next.txn_ref)
    ? views.map((v) => (v.txn_ref === next.txn_ref ? next : v))
    : [...views, next];
}

export interface ResolveFailure {
  title: string;
  detail: string;
  /** True when the UI should reload the queue so the analyst sees what changed. */
  refresh: boolean;
}

/** Name the cause of a failed resolve in words an analyst can act on. */
export function explainResolveError(e: unknown, action: ResolveAction): ResolveFailure {
  const verb = action === "confirm_block" ? "confirm the mule" : "release the hold";
  if (e instanceof ApiError) {
    if (e.code === "conflict")
      return {
        title: "Another change arrived",
        detail: "This hold was resolved differently or its decision changed (stale decision_seq). Refresh to see the latest.",
        refresh: true,
      };
    if (e.code === "forbidden")
      return { title: "Not allowed for this role", detail: `Only an analyst or admin can ${verb}.`, refresh: false };
    if (e.code === "not_found")
      return { title: "Hold no longer exists", detail: "The run was restarted or the hold was removed.", refresh: true };
    if (e.code === "network")
      return { title: "Server unreachable", detail: "The action was not applied. Check the connection and try again.", refresh: false };
    if (e.code === "rate_limited")
      return { title: "Rate limit reached", detail: "Wait a moment and try again.", refresh: false };
    if (e.code === "unauthorized") return { title: "Session expired", detail: "Sign in again to continue.", refresh: false };
  }
  return { title: "Could not resolve the hold", detail: `The server could not ${verb}. Try again.`, refresh: true };
}

// ---- audit note --------------------------------------------------------------------------

export const NOTE_MIN = 10;
export const NOTE_MAX = 280;

export type NoteProblem = "empty" | "short" | "long" | "digits" | "email";

/**
 * Client-side guard for the reason note. Mirrors the PII guard idea used server side: no long
 * digit runs (phone, account, card or Aadhaar-like numbers) and no email addresses. The note is
 * never sent to the API (the resolve body has no note field) and is kept only in memory.
 */
export function validateNote(raw: string): { ok: true; note: string } | { ok: false; problem: NoteProblem; message: string } {
  const note = raw.trim().replace(/\s+/g, " ");
  if (note.length === 0) return { ok: false, problem: "empty", message: "A reason note is required." };
  if (note.length < NOTE_MIN) return { ok: false, problem: "short", message: `Add a little more detail (at least ${NOTE_MIN} characters).` };
  if (note.length > NOTE_MAX) return { ok: false, problem: "long", message: `Keep it under ${NOTE_MAX} characters.` };
  if (/\d[\d\s\-.]{4,}\d/.test(note) || /\d{6,}/.test(note))
    return { ok: false, problem: "digits", message: "Do not include long numbers (phone, account or card numbers)." };
  if (/\S+@\S+/.test(note)) return { ok: false, problem: "email", message: "Do not include email addresses." };
  return { ok: true, note };
}

// ---- upgrade history ---------------------------------------------------------------------

export const TRIGGER_LABEL: Record<HoldTrigger, string> = {
  initial: "initial decision",
  call_risk: "call risk",
  antibody: "antibody",
  late_call_risk: "late settlement (call risk)",
  late_antibody: "late settlement (antibody)",
  resolved: "analyst",
};

/** "allow -> step_up -> hold_verify" from the recorded steps; one entry when there was no upgrade. */
export function historyChain(steps: readonly HoldStep[], fallbackDecision: string, upgradedFrom?: string): string[] {
  if (steps.length === 0) return upgradedFrom ? [upgradedFrom, fallbackDecision] : [fallbackDecision];
  const out: string[] = [];
  const first = steps[0];
  if (first?.from) out.push(first.from);
  for (const s of steps) if (out[out.length - 1] !== s.decision) out.push(s.decision);
  return out;
}

export function reasonsOf(rs: readonly Reason[]): string[] {
  return rs.map((r) => r.code);
}

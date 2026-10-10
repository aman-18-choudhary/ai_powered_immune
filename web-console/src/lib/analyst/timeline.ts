import type { HoldView } from "@/types/api";
import type { RunState } from "@/state/reducer";
import { decisionLabel } from "@/components/common/Decision";
import { TRIGGER_LABEL } from "./queue";

export interface TimelineItem {
  key: string;
  t: number;
  kind: "call" | "scored" | "decision" | "antibody" | "ledger" | "resolved";
  title: string;
  detail?: string;
}

const HOLD_LEDGER = /^(hold\.|antibody\.)/;

/**
 * One hold's story from the event store: the call alert, the scoring, every decision, any
 * antibody and the ledger entries sealed while it was open. The stream carries no per-entry txn
 * ref, so ledger rows are matched by simulated time window and hold/antibody event types.
 */
export function buildHoldTimeline(run: RunState, view: HoldView): TimelineItem[] {
  const out: TimelineItem[] = [];
  const rec = run.holds[view.txn_ref];
  const call = view.call_ref ? run.calls[view.call_ref] : undefined;
  if (call?.alert) {
    out.push({
      key: `call-${call.ref}`,
      t: call.alert.t,
      kind: "call",
      title: "Call-guard alert",
      detail: `Session risk ${call.alert.score.toFixed(2)} crossed the ${call.alert.threshold.toFixed(2)} threshold`,
    });
  }
  const scored = run.txns.find((x) => x.txn_ref === view.txn_ref);
  if (scored) {
    out.push({ key: `scored-${scored.id}`, t: scored.t, kind: "scored", title: "Transfer scored", detail: `${scored.rail} · score ${scored.score.toFixed(2)}` });
  }
  const steps = rec?.history ?? [];
  if (steps.length === 0) {
    out.push({
      key: `d-${view.decision_seq}`,
      t: view.created_t,
      kind: "decision",
      title: `Decision: ${decisionLabel(view.decision)}`,
      detail: `decision_seq ${view.decision_seq}`,
    });
  }
  for (const s of steps) {
    out.push({
      key: `d-${s.seq}-${s.decision}`,
      t: s.t,
      kind: "decision",
      title: s.from ? `Upgraded ${decisionLabel(s.from)} to ${decisionLabel(s.decision)}` : `Decision: ${decisionLabel(s.decision)}`,
      detail: `decision_seq ${s.seq} · trigger: ${TRIGGER_LABEL[s.trigger]}`,
    });
  }
  const endT = rec?.resolvedT ?? Number.POSITIVE_INFINITY;
  for (const a of run.antibodies) {
    if (a.publishedT >= view.created_t && a.publishedT <= endT + 1) {
      out.push({ key: `ab-${a.ref}`, t: a.publishedT, kind: "antibody", title: "Antibody published", detail: `${a.kind.replace(/_/g, " ")} shared from ${a.bank.replace("bank_", "bank ")}` });
      for (const ap of a.applied)
        out.push({ key: `ap-${a.ref}-${ap.bank}`, t: ap.t, kind: "antibody", title: `Antibody applied at ${ap.bank.replace("bank_", "bank ")}`, detail: `${ap.latency_ms.toFixed(1)} ms after publication` });
    }
  }
  for (const l of run.ledger) {
    if (l.t >= view.created_t && l.t <= endT && HOLD_LEDGER.test(l.event_type)) {
      out.push({ key: `l-${l.seq}`, t: l.t, kind: "ledger", title: `Ledger #${l.seq}: ${l.event_type}`, detail: `${l.service} · ${l.hash}` });
    }
  }
  if (rec?.resolvedT !== undefined) {
    out.push({
      key: "resolved",
      t: rec.resolvedT,
      kind: "resolved",
      title: rec.state === "blocked" ? "Mule confirmed, transfer blocked" : "Hold released",
      detail: `${rec.source === "manual" ? "Manual" : "Automatic"} · ${rec.resolvedBy ?? "unknown"}`,
    });
  }
  return out.sort((a, b) => a.t - b.t);
}

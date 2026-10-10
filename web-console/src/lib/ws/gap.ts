import type { StreamEvent } from "@/types/api";

export type GapResult = "ok" | "duplicate" | "gap";

/**
 * Tracks the last seen event id. Live ids are contiguous within a run; a snapshot (or a
 * scenario.started) re-bases the tracker.
 */
export class GapTracker {
  last = 0;
  private based = false;
  private runId: string | undefined;

  reset() {
    this.last = 0;
    this.based = false;
    this.runId = undefined;
  }

  /** Classify an event and advance the tracker when it is accepted. */
  accept(e: StreamEvent): GapResult {
    if (e.kind === "scenario.state") {
      const max = e.events.reduce((m, x) => Math.max(m, x.id), 0);
      this.last = Math.max(e.last_event_id ?? 0, max);
      this.based = true;
      this.runId = e.events.find((x) => x.run_id)?.run_id ?? this.runId;
      return "ok";
    }
    if (e.kind === "scenario.started") {
      // The same run's start seen twice is a duplicate; a different run re-bases the ids.
      if (this.based && e.run_id !== undefined && e.run_id === this.runId && e.id <= this.last) return "duplicate";
      this.last = e.id;
      this.based = true;
      this.runId = e.run_id ?? this.runId;
      return "ok";
    }
    if (!this.based) {
      this.last = e.id;
      this.based = true;
      return "ok";
    }
    if (e.id <= this.last) return "duplicate";
    if (e.id !== this.last + 1) return "gap";
    this.last = e.id;
    return "ok";
  }
}

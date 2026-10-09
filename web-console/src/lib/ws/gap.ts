import type { StreamEvent } from "@/types/api";

export type GapResult = "ok" | "duplicate" | "gap";

/**
 * Tracks the last seen event id. Live ids are contiguous within a run; a snapshot (or a
 * scenario.started) re-bases the tracker.
 */
export class GapTracker {
  last = 0;
  private based = false;

  reset() {
    this.last = 0;
    this.based = false;
  }

  /** Classify an event and advance the tracker when it is accepted. */
  accept(e: StreamEvent): GapResult {
    if (e.kind === "scenario.state") {
      const max = e.events.reduce((m, x) => Math.max(m, x.id), 0);
      this.last = Math.max(e.last_event_id ?? 0, max);
      this.based = true;
      return "ok";
    }
    if (e.kind === "scenario.started") {
      this.last = e.id;
      this.based = true;
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

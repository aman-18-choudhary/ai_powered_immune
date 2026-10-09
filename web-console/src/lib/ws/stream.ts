import type { StreamEvent } from "@/types/api";
import { parseEvent } from "@/types/guards";
import { GapTracker } from "./gap";

export type ConnState = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "unauthorized" | "refused";

export interface WsLike {
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code: number }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  close(code?: number): void;
}
export type WsFactory = (url: string) => WsLike;

export interface StreamOptions {
  /** Returns the full ws(s) URL including `?token=`, or null when signed out. Never logged. */
  getUrl: () => string | null;
  onBatch: (events: StreamEvent[]) => void;
  onState: (s: ConnState) => void;
  onResync?: () => void;
  factory?: WsFactory;
  /** Frame scheduler (rAF in the browser, a timer in tests). */
  schedule?: (cb: () => void) => void;
  setTimer?: (cb: () => void, ms: number) => unknown;
  clearTimer?: (h: unknown) => void;
  random?: () => number;
  maxMessageBytes?: number;
}

export const BACKOFF_BASE_MS = 500;
export const BACKOFF_MAX_MS = 8000;
const FORCE_FLUSH_AT = 1500;

export function backoffDelay(attempt: number, rnd = 0): number {
  const raw = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attempt);
  return Math.round(raw * (0.85 + 0.3 * rnd));
}

export class StreamClient {
  private ws: WsLike | null = null;
  private queue: StreamEvent[] = [];
  private flushScheduled = false;
  private attempt = 0;
  private timer: unknown = null;
  private stopped = true;
  private gap = new GapTracker();
  private readonly o: StreamOptions;
  state: ConnState = "idle";
  resyncs = 0;

  constructor(o: StreamOptions) {
    this.o = o;
  }

  private set(s: ConnState) {
    if (this.state !== s) {
      this.state = s;
      this.o.onState(s);
    }
  }

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    this.clearTimer();
    this.detach();
    this.queue = [];
    this.set("closed");
  }

  private clearTimer() {
    if (this.timer !== null) {
      (this.o.clearTimer ?? ((h) => clearTimeout(h as number)))(this.timer);
      this.timer = null;
    }
  }

  private detach() {
    const ws = this.ws;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      try {
        ws.close(1000);
      } catch {
        /* already closed */
      }
    }
    this.ws = null;
  }

  private connect() {
    if (this.stopped) return;
    const url = this.o.getUrl();
    if (!url) {
      this.set("unauthorized");
      return;
    }
    this.set(this.attempt === 0 && this.state !== "reconnecting" ? "connecting" : "reconnecting");
    this.gap.reset();
    const factory = this.o.factory ?? ((u: string) => new WebSocket(u) as unknown as WsLike);
    let ws: WsLike;
    try {
      ws = factory(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.set("open");
    };
    ws.onmessage = (ev) => this.onMessage(ev.data);
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.stopped) return;
      if (ev.code === 4401) return this.set("unauthorized");
      if (ev.code === 4403) return this.set("refused");
      this.scheduleReconnect(ev.code === 4429 ? 0 : undefined);
    };
    ws.onerror = () => {
      /* onclose follows */
    };
  }

  private scheduleReconnect(forceAttempt?: number) {
    if (this.stopped) return;
    this.set("reconnecting");
    const attempt = forceAttempt ?? this.attempt;
    const delay = backoffDelay(attempt, (this.o.random ?? Math.random)());
    this.attempt = attempt + 1;
    this.clearTimer();
    this.timer = (this.o.setTimer ?? ((cb, ms) => setTimeout(cb, ms)))(() => {
      this.timer = null;
      this.connect();
    }, delay);
  }

  private onMessage(data: unknown) {
    const max = this.o.maxMessageBytes ?? 512 * 1024;
    if (typeof data !== "string" || data.length > max) return;
    let raw: unknown;
    try {
      raw = JSON.parse(data);
    } catch {
      return;
    }
    const ev = parseEvent(raw);
    if (!ev) return;
    const verdict = this.gap.accept(ev);
    if (verdict === "duplicate") return;
    if (verdict === "gap") {
      // Missed events: drop what is queued after the gap and take a fresh snapshot.
      this.resyncs += 1;
      this.o.onResync?.();
      this.detach();
      this.attempt = 0;
      this.scheduleReconnect(0);
      return;
    }
    if (ev.kind === "scenario.state") this.attempt = 0;
    this.queue.push(ev);
    if (this.queue.length >= FORCE_FLUSH_AT) {
      this.flush();
    } else if (!this.flushScheduled) {
      this.flushScheduled = true;
      (this.o.schedule ?? ((cb) => requestAnimationFrame(() => cb())))(() => {
        this.flushScheduled = false;
        this.flush();
      });
    }
  }

  flush() {
    if (this.queue.length === 0) return;
    const batch = this.queue;
    this.queue = [];
    this.o.onBatch(batch);
  }

  /** Test hook. */
  get pending() {
    return this.queue.length;
  }
}

export function streamUrl(token: string, loc: Pick<Location, "protocol" | "host"> = window.location): string {
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${loc.host}/api/stream?token=${encodeURIComponent(token)}`;
}

import { reasonInfo } from "@/lib/reasons";

/** Number, money and label formatters. Money is handled as integer paise: never as floats. */

const DEC = /^-?\d+(\.\d{1,2})?$/;

/** "16000.00" -> 1600000n (paise). Returns null for malformed input. */
export function inrToPaise(v: string | number | null | undefined): bigint | null {
  if (v === null || v === undefined) return null;
  const s = typeof v === "number" ? v.toFixed(2) : v.trim();
  if (!DEC.test(s)) return null;
  const neg = s.startsWith("-");
  const [whole = "0", frac = ""] = s.replace("-", "").split(".");
  const paise = BigInt(whole) * 100n + BigInt((frac + "00").slice(0, 2));
  return neg ? -paise : paise;
}

/** Indian digit grouping (3,2,2,...) of an integer string. */
export function groupIndian(intStr: string): string {
  const neg = intStr.startsWith("-");
  const d = neg ? intStr.slice(1) : intStr;
  if (d.length <= 3) return (neg ? "-" : "") + d;
  const last3 = d.slice(-3);
  let rest = d.slice(0, -3);
  const parts: string[] = [];
  while (rest.length > 2) {
    parts.unshift(rest.slice(-2));
    rest = rest.slice(0, -2);
  }
  if (rest) parts.unshift(rest);
  return (neg ? "-" : "") + parts.join(",") + "," + last3;
}

export function formatPaise(p: bigint, opts: { paise?: "always" | "auto" | "never" } = {}): string {
  const mode = opts.paise ?? "always";
  const neg = p < 0n;
  const abs = neg ? -p : p;
  const whole = abs / 100n;
  const frac = abs % 100n;
  let out = "₹" + groupIndian(whole.toString());
  if (mode === "always" || (mode === "auto" && frac !== 0n)) out += "." + frac.toString().padStart(2, "0");
  return neg ? "-" + out : out;
}

/** "569394.00" -> "₹5,69,394.00". Malformed -> "—". */
export function formatInr(v: string | number | null | undefined, opts: { paise?: "always" | "auto" | "never" } = {}): string {
  const p = inrToPaise(v);
  return p === null ? "—" : formatPaise(p, opts);
}

export function sumInr(values: (string | null | undefined)[]): bigint {
  let t = 0n;
  for (const v of values) {
    const p = inrToPaise(v);
    if (p !== null) t += p;
  }
  return t;
}

/** Whole-rupee approximation as a JS number for animated counters (safe up to 2^53 rupees). */
export function paiseToRupeesNumber(p: bigint): number {
  return Number(p / 100n);
}

export function formatCount(n: number): string {
  return groupIndian(Math.round(n).toString());
}

/** Amount bucket labels from the stream: "10k-100k" -> "₹10k – ₹100k". */
export function formatBucket(b: string | null | undefined): string {
  if (!b) return "—";
  const m = b.match(/^(<|>)?\s*([0-9.]+[a-zA-Z]*)\s*(?:[-–]\s*([0-9.]+[a-zA-Z]*))?(\+)?$/);
  if (!m) return b;
  const [, cmp, lo, hi, plus] = m;
  if (hi) return `₹${lo} – ₹${hi}`;
  if (cmp) return `${cmp === "<" ? "under" : "over"} ₹${lo}`;
  return plus ? `₹${lo}+` : `₹${lo}`;
}

export function formatLatencyMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  if (ms >= 100) return `${Math.round(ms)} ms`;
  return `${ms.toFixed(1)} ms`;
}

/** Simulated seconds -> "14:32" or "1:02:03". */
export function formatSimClock(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? h + ":" : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

export function formatSeconds(s: number): string {
  if (!Number.isFinite(s)) return "—";
  if (s >= 120) return `${(s / 60).toFixed(1)} min`;
  return `${Math.round(s)} s`;
}

export function formatPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function formatCi(ci: [number, number] | null | undefined, digits = 1): string {
  if (!ci) return "";
  return `${(ci[0] * 100).toFixed(digits)}–${(ci[1] * 100).toFixed(digits)}%`;
}

export function shortRef(ref: string, n = 8): string {
  const i = ref.indexOf(":");
  const prefix = ref.startsWith("txn_") ? "txn_" : i > 0 ? ref.slice(0, i + 1) : "";
  const body = ref.slice(prefix.length);
  return body.length <= n ? ref : `${prefix}${body.slice(0, n)}…`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function median(xs: number[]): number | null {
  if (xs.length === 0) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  const hi = s[m];
  const lo = s[m - 1];
  if (hi === undefined) return null;
  return s.length % 2 ? hi : ((lo ?? hi) + hi) / 2;
}

export function reasonLabel(code: string): string {
  return reasonInfo(code).label;
}

export function bankLabel(bank: string, names?: { bank_a: string; bank_b: string } | null): string {
  if (bank === "bank_a") return names?.bank_a ? names.bank_a.toUpperCase() : "Bank A";
  if (bank === "bank_b") return names?.bank_b ? names.bank_b.toUpperCase() : "Bank B";
  return bank.replace(/^bank_/, "").toUpperCase();
}

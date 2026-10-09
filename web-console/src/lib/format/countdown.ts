export function clamp01(x: number): number {
  return x < 0 ? 0 : x > 1 ? 1 : x;
}

/** Remaining simulated seconds until `deadlineT` at sim time `now`, never negative. */
export function remainingSeconds(deadlineT: number, now: number): number {
  return Math.max(0, deadlineT - now);
}

/** Compact label: 95 -> "1:35", 42 -> "42s", 0 -> "0s". */
export function countdown(s: number): string {
  const v = Math.max(0, Math.ceil(s));
  if (v >= 60) return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, "0")}`;
  return `${v}s`;
}

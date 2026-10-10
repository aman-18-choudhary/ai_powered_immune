/** Step times are deterministic per (seed, mode); remember them so markers exist before they play. */
const key = (seed: number | null, mode: string | null) => `ssi.steps2.${seed ?? "x"}.${mode ?? "x"}`;

export function loadStepTimes(seed: number | null, mode: string | null): Record<string, number> {
  try {
    const raw = sessionStorage.getItem(key(seed, mode));
    if (!raw) return {};
    const p: unknown = JSON.parse(raw);
    if (typeof p !== "object" || p === null) return {};
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(p)) if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
    return out;
  } catch {
    return {};
  }
}

export function saveStepTimes(seed: number | null, mode: string | null, times: Record<string, number>): void {
  try {
    sessionStorage.setItem(key(seed, mode), JSON.stringify(times));
  } catch {
    /* ignore */
  }
}

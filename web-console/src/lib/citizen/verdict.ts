import { isRecord } from "@/types/guards";
import type { Lang } from "@/lib/i18n/citizen";

export const MAX_CHARS = 4000;
export const INPUT_CAP = 20000;

/** "safe" was renamed (accuracy audit 2026-10-18): finding no cue is not a finding of safety. */
export type Label = "no_indicators_found" | "weak_indicators" | "insufficient_input" | "suspicious" | "likely_scam";

export interface Verdict {
  label: Label;
  score: number;
  reasons: { code: string; text: string; weight: number }[];
  steps: string[];
  lang: string;
  uncertain: boolean;
  thresholds: { suspicious: number; likely_scam: number };
}

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const str = (v: unknown): v is string => typeof v === "string";

/** Runtime-narrow the assess response. Unknown labels fail toward "suspicious" and uncertain. */
export function parseVerdict(raw: unknown): Verdict | null {
  if (!isRecord(raw) || !num(raw.score)) return null;
  let label: Label;
  let uncertain = raw.uncertain === true;
  if (raw.label === "no_indicators_found" || raw.label === "weak_indicators" || raw.label === "insufficient_input" || raw.label === "suspicious" || raw.label === "likely_scam") label = raw.label;
  else {
    label = "suspicious";
    uncertain = true;
  }
  const reasons = Array.isArray(raw.reasons)
    ? raw.reasons.flatMap((r) =>
        isRecord(r) && str(r.code) && str(r.text) ? [{ code: r.code, text: r.text, weight: num(r.weight) ? r.weight : 0 }] : [],
      )
    : [];
  const advice = isRecord(raw.advice) && Array.isArray(raw.advice.steps) ? raw.advice.steps.filter(str) : [];
  const th = isRecord(raw.thresholds) ? raw.thresholds : {};
  return {
    label,
    score: Math.min(1, Math.max(0, raw.score)),
    reasons,
    steps: advice,
    lang: str(raw.lang) ? raw.lang : "en",
    uncertain,
    thresholds: {
      suspicious: num(th.suspicious) ? th.suspicious : 0.35,
      likely_scam: num(th.likely_scam) ? th.likely_scam : 0.7,
    },
  };
}

/** What gets sent: trimmed to the scored window so a huge paste never hits the body cap. */
export function prepareMessage(raw: string): { text: string; truncated: boolean; length: number } {
  const text = raw.trim();
  return { text: text.slice(0, MAX_CHARS), truncated: text.length > MAX_CHARS, length: text.length };
}

export type SampleKey = "sample1" | "sample2" | "sample3";
export const SAMPLES: { key: SampleKey; text: string }[] = [
  {
    key: "sample1",
    text: "This is Inspector Rao from the CBI cyber cell. A parcel in your name has been seized with illegal items and a digital arrest warrant is issued. Do not disconnect this video call and do not tell anyone. Transfer the amount to the RBI safe account immediately to verify your funds.",
  },
  {
    key: "sample2",
    text: "Dear customer, your bank account will be blocked today. Share the OTP you just received and your card PIN with our officer to verify your KYC immediately.",
  },
  {
    key: "sample3",
    text: "Your parcel is on hold because the address is incomplete. Pay a Rs 25 redelivery fee at the link within 2 hours or the package will be returned.",
  },
];

export function langParam(l: Lang): string {
  return l;
}

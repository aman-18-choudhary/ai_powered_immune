import type { Category, ComplaintCreate, PaymentMethod } from "./types";

/**
 * The complaint form. Client checks mirror services/complaints/complaints/validation.py so most
 * mistakes are caught before sending; the service stays authoritative (its 422 field errors are
 * shown on the same fields).
 */
export interface FormValues {
  category: Category | "";
  title: string;
  description: string;
  incident_date: string; // yyyy-mm-dd
  incident_time: string; // hh:mm
  amount: string;
  payment_method: PaymentMethod | "";
  upi_id: string;
  utr_reference: string;
  bank_name: string;
  bank_account: string;
  ifsc: string;
  suspect_phone: string;
  suspect_email: string;
  suspicious_url: string;
  social_platform: string;
  social_account: string;
  location_text: string;
  city: string;
  state: string;
  latitude: string;
  longitude: string;
}

export const EMPTY_FORM: FormValues = {
  category: "",
  title: "",
  description: "",
  incident_date: "",
  incident_time: "",
  amount: "",
  payment_method: "",
  upi_id: "",
  utr_reference: "",
  bank_name: "",
  bank_account: "",
  ifsc: "",
  suspect_phone: "",
  suspect_email: "",
  suspicious_url: "",
  social_platform: "",
  social_account: "",
  location_text: "",
  city: "",
  state: "",
  latitude: "",
  longitude: "",
};

const UPI_RE = /^[a-z0-9][a-z0-9._-]{1,255}@[a-z][a-z0-9]{1,63}$/;
const UTR_RE = /^[A-Z0-9]{6,30}$/;
const ACCOUNT_RE = /^[0-9]{6,18}$/;
const IFSC_RE = /^[A-Z]{4}0[A-Z0-9]{6}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const AMOUNT_RE = /^\d{1,12}(\.\d{1,2})?$/;

export function phoneOk(raw: string): boolean {
  let s = raw.replace(/[\s\-().]/g, "");
  if (s.startsWith("00")) s = "+" + s.slice(2);
  if (s.startsWith("+")) return /^\+[1-9]\d{7,14}$/.test(s);
  if (!/^\d+$/.test(s)) return false;
  if (s.length === 10 && /^[6-9]/.test(s)) return true;
  if (s.length === 12 && /^91[6-9]/.test(s)) return true;
  return s.length >= 10 && s.length <= 12 && s[0] === "0" && s[1] !== "0";
}

export function urlOk(raw: string): boolean {
  if (raw.length > 2048 || /\s/.test(raw)) return false;
  try {
    const u = new URL(raw);
    return (u.protocol === "http:" || u.protocol === "https:") && !!u.hostname && !u.username && !u.password;
  } catch {
    return false;
  }
}

/** Local date + optional time as an ISO timestamp with the browser's UTC offset. */
export function incidentIso(date: string, time: string): string | null {
  if (!date) return null;
  const d = new Date(`${date}T${time || "00:00"}`);
  if (Number.isNaN(d.getTime())) return null;
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  const pad = (n: number) => String(Math.trunc(Math.abs(n))).padStart(2, "0");
  return `${date}T${time || "00:00"}:00${sign}${pad(off / 60)}:${pad(off % 60)}`;
}

export function validate(v: FormValues, now: Date = new Date()): Record<string, string> {
  const e: Record<string, string> = {};
  const t = (s: string) => s.trim();
  if (!v.category) e.category = "Choose the category that fits best.";
  if (t(v.title).length < 3) e.title = "Enter a short title (at least 3 characters).";
  else if (t(v.title).length > 200) e.title = "Keep the title under 200 characters.";
  if (t(v.description).length < 10) e.description = "Describe what happened (at least 10 characters).";
  else if (t(v.description).length > 5000) e.description = "Keep the description under 5,000 characters.";
  if (v.incident_time && !v.incident_date) e.incident_at = "Add the date as well as the time.";
  else if (v.incident_date) {
    const iso = incidentIso(v.incident_date, v.incident_time);
    if (!iso) e.incident_at = "Enter a valid date.";
    else {
      const d = new Date(iso);
      if (d.getTime() > now.getTime() + 60_000) e.incident_at = "The incident time cannot be in the future.";
      else if (d.getFullYear() < 2000) e.incident_at = "The date is too far in the past.";
    }
  }
  if (t(v.amount) && !AMOUNT_RE.test(t(v.amount).replace(/,/g, ""))) e.amount = "Enter an amount in rupees, with at most two decimals.";
  if (t(v.upi_id) && !UPI_RE.test(t(v.upi_id).toLowerCase())) e.upi_id = "A UPI ID looks like name@bank.";
  if (t(v.utr_reference) && !UTR_RE.test(t(v.utr_reference).replace(/[\s-]/g, "").toUpperCase()))
    e.utr_reference = "A UTR / reference is 6 to 30 letters or digits.";
  if (t(v.bank_name).length > 100) e.bank_name = "Keep the bank name under 100 characters.";
  if (t(v.bank_account) && !ACCOUNT_RE.test(t(v.bank_account).replace(/ /g, ""))) e.bank_account = "An account number is 6 to 18 digits.";
  if (t(v.ifsc) && !IFSC_RE.test(t(v.ifsc).toUpperCase())) e.ifsc = "An IFSC is 4 letters, a zero, then 6 letters or digits (e.g. SBIN0001234).";
  if (t(v.suspect_phone) && !phoneOk(t(v.suspect_phone))) e.suspect_phone = "Enter a phone number, e.g. 98xxxxxxxx or +44…";
  if (t(v.suspect_email) && !EMAIL_RE.test(t(v.suspect_email))) e.suspect_email = "Enter a valid email address.";
  if (t(v.suspicious_url) && !urlOk(t(v.suspicious_url))) e.suspicious_url = "Enter the full link starting with http:// or https://.";
  if (t(v.social_platform).length > 50) e.social_platform = "Keep this under 50 characters.";
  if (t(v.social_account).length > 100) e.social_account = "Keep this under 100 characters.";
  for (const k of ["city", "state"] as const) if (t(v[k]).length > 100) e[k] = "Keep this under 100 characters.";
  if (t(v.location_text).length > 200) e.location_text = "Keep this under 200 characters.";
  const lat = t(v.latitude);
  const lon = t(v.longitude);
  if (lat || lon) {
    if (!lat || !lon) e[lat ? "longitude" : "latitude"] = "Give both latitude and longitude, or neither.";
    if (lat && !(Number.isFinite(Number(lat)) && Math.abs(Number(lat)) <= 90)) e.latitude = "Latitude is a number from -90 to 90.";
    if (lon && !(Number.isFinite(Number(lon)) && Math.abs(Number(lon)) <= 180)) e.longitude = "Longitude is a number from -180 to 180.";
  }
  return e;
}

const OPTIONAL_TEXT = [
  "upi_id",
  "utr_reference",
  "bank_name",
  "bank_account",
  "ifsc",
  "suspect_phone",
  "suspect_email",
  "suspicious_url",
  "social_platform",
  "social_account",
  "location_text",
  "city",
  "state",
] as const;

/**
 * The POST /complaints body. Optional fields that are empty are left out entirely (the service
 * stores NULL for an absent key); values are sent trimmed and otherwise as typed, so the service
 * does the normalisation (upper-casing IFSC, +91 for phones, ...).
 */
export function buildPayload(v: FormValues): ComplaintCreate {
  if (!v.category) throw new Error("category is required");
  const out: ComplaintCreate = { category: v.category, title: v.title.trim(), description: v.description.trim() };
  const iso = incidentIso(v.incident_date, v.incident_time);
  if (iso) out.incident_at = iso;
  const amount = v.amount.trim().replace(/,/g, "");
  if (amount) out.amount = amount;
  if (v.payment_method) out.payment_method = v.payment_method;
  for (const k of OPTIONAL_TEXT) {
    const val = v[k].trim();
    if (val) out[k] = val;
  }
  if (v.latitude.trim() && v.longitude.trim()) {
    out.latitude = Number(v.latitude.trim());
    out.longitude = Number(v.longitude.trim());
  }
  return out;
}

export const ACCEPT = ".png,.jpg,.jpeg,.webp,.pdf,.mp3,.wav,.ogg,.m4a,image/png,image/jpeg,image/webp,application/pdf,audio/*";
const IMAGE_EXT = ["png", "jpg", "jpeg", "webp", "pdf"];
const AUDIO_EXT = ["mp3", "wav", "ogg", "m4a"];
export const MAX_FILES = 10;
const MIB = 1024 * 1024;

/** A quick local check before sending (the service still sniffs the content and enforces caps). */
export function precheckFile(f: File): string | null {
  const ext = f.name.includes(".") ? f.name.split(".").pop()!.toLowerCase() : "";
  if (!IMAGE_EXT.includes(ext) && !AUDIO_EXT.includes(ext)) return "Only PNG, JPEG, WebP, PDF, MP3, WAV, OGG and M4A files are accepted.";
  const cap = AUDIO_EXT.includes(ext) ? 25 * MIB : 10 * MIB;
  if (f.size > cap) return `The file is larger than ${cap / MIB} MB.`;
  if (f.size === 0) return "The file is empty.";
  return null;
}

export function newIdempotencyKey(): string {
  const c = globalThis.crypto;
  if (c && "randomUUID" in c) return `web-${c.randomUUID()}`;
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

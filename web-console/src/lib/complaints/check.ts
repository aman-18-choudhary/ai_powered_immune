import type { CheckRequest, PaymentMethod } from "./types";

/** The "Check a message or link" form (POST /analyze). Kept in component memory only. */
export interface CheckValues {
  text: string;
  url: string;
  upi_id: string;
  phone: string;
  email: string;
  amount: string;
  payment_method: PaymentMethod | "";
}
export const EMPTY_CHECK: CheckValues = { text: "", url: "", upi_id: "", phone: "", email: "", amount: "", payment_method: "" };
export const MIN_TEXT = 10;
export const MAX_TEXT = 5000;

/** Client checks mirror POST /analyze (services/complaints/complaints/validation.py AnalyzeIn); the service decides. */
export function validateCheck(v: CheckValues): Record<string, string> {
  const e: Record<string, string> = {};
  const t = v.text.trim();
  if (t.length < MIN_TEXT) e.text = `Paste at least ${MIN_TEXT} characters of the message or conversation.`;
  else if (t.length > MAX_TEXT) e.text = `At most ${MAX_TEXT} characters.`;
  if (v.url.trim().length > 2048) e.url = "At most 2048 characters.";
  if (v.upi_id.trim().length > 320) e.upi_id = "At most 320 characters.";
  if (v.phone.trim().length > 40) e.phone = "At most 40 characters.";
  if (v.email.trim().length > 254) e.email = "At most 254 characters.";
  const a = v.amount.trim();
  if (a && !/^\d{1,12}(\.\d{1,2})?$/.test(a)) e.amount = "A positive amount with at most two decimals, e.g. 4999.50";
  return e;
}

/** Only filled optional fields are sent. */
export function buildCheck(v: CheckValues): CheckRequest {
  const body: CheckRequest = { text: v.text.trim() };
  for (const k of ["url", "upi_id", "phone", "email", "amount"] as const) {
    const x = v[k].trim();
    if (x) body[k] = x;
  }
  if (v.payment_method) body.payment_method = v.payment_method;
  return body;
}

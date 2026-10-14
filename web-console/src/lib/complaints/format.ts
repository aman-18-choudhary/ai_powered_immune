import type { AccountRole, Category, ComplaintStatus, PaymentMethod, RiskLevel } from "./types";

export const CATEGORY_LABEL: Record<Category, string> = {
  DIGITAL_ARREST: "Digital arrest",
  UPI_FRAUD: "UPI fraud",
  PHISHING: "Phishing",
  OTP_SCAM: "OTP scam",
  BANK_FRAUD: "Bank fraud",
  INVESTMENT_SCAM: "Investment scam",
  JOB_SCAM: "Job scam",
  SOCIAL_MEDIA_FRAUD: "Social media fraud",
  OTHER: "Other",
};

export const STATUS_LABEL: Record<ComplaintStatus, string> = {
  SUBMITTED: "Submitted",
  UNDER_REVIEW: "Under review",
  INVESTIGATING: "Investigating",
  RESOLVED: "Resolved",
  REJECTED: "Rejected",
};

export const PAYMENT_LABEL: Record<PaymentMethod, string> = {
  UPI: "UPI",
  CARD: "Card",
  NETBANKING: "Net banking",
  WALLET: "Wallet",
  CASH: "Cash",
  BANK_TRANSFER: "Bank transfer",
  OTHER: "Other",
};

export const ROLE_LABEL: Record<AccountRole, string> = { CITIZEN: "Citizen", INVESTIGATOR: "Investigator", ADMIN: "Administrator" };

export const RISK_LABEL: Record<RiskLevel, string> = { LOW: "Low", MEDIUM: "Medium", HIGH: "High", CRITICAL: "Critical" };

/**
 * Status changes the service accepts (services/complaints/complaints/store.py TRANSITIONS). Used to
 * offer only valid choices; the service still decides and answers 409 otherwise.
 */
export const TRANSITIONS: Record<ComplaintStatus, ComplaintStatus[]> = {
  SUBMITTED: ["UNDER_REVIEW", "REJECTED"],
  UNDER_REVIEW: ["INVESTIGATING", "RESOLVED", "REJECTED"],
  INVESTIGATING: ["UNDER_REVIEW", "RESOLVED", "REJECTED"],
  RESOLVED: ["INVESTIGATING"],
  REJECTED: ["UNDER_REVIEW"],
};

export const NOT_PROVIDED = "Not provided";

const dtf = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return NOT_PROVIDED;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dtf.format(d);
}

export function formatAmount(amount: string | null, currency: string | null): string {
  if (amount === null || amount === "") return NOT_PROVIDED;
  const n = Number(amount);
  if (!Number.isFinite(n)) return `${amount} ${currency ?? ""}`.trim();
  try {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency: currency || "INR" }).format(n);
  } catch {
    return `${amount} ${currency ?? ""}`.trim();
  }
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function humanise(code: string): string {
  const s = code.replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** A short, copyable form of a UUID for lists (the full id is shown on the detail page). */
export const shortId = (id: string) => id.slice(0, 8);

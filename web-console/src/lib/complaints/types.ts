/**
 * Shapes returned by the complaints service (services/complaints/complaints/api.py), mounted by the
 * demo server at /api/complaints. Real persisted data, not simulated.
 */

export const CATEGORIES = [
  "DIGITAL_ARREST",
  "UPI_FRAUD",
  "PHISHING",
  "OTP_SCAM",
  "BANK_FRAUD",
  "INVESTMENT_SCAM",
  "JOB_SCAM",
  "SOCIAL_MEDIA_FRAUD",
  "OTHER",
] as const;
export type Category = (typeof CATEGORIES)[number];

export const STATUSES = ["SUBMITTED", "UNDER_REVIEW", "INVESTIGATING", "RESOLVED", "REJECTED"] as const;
export type ComplaintStatus = (typeof STATUSES)[number];

export const PAYMENT_METHODS = ["UPI", "CARD", "NETBANKING", "WALLET", "CASH", "BANK_TRANSFER", "OTHER"] as const;
export type PaymentMethod = (typeof PAYMENT_METHODS)[number];

export const ACCOUNT_ROLES = ["CITIZEN", "INVESTIGATOR", "ADMIN"] as const;
export type AccountRole = (typeof ACCOUNT_ROLES)[number];

/** Filter values accepted by GET /complaints?risk_level= (CRITICAL is stored as HIGH). */
export const RISK_FILTERS = ["LOW", "MEDIUM", "HIGH"] as const;
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface AuthConfig {
  auth_mode: "clerk" | "demo" | "local";
  clerk: { enabled: boolean; publishable_key: string | null };
  local: { enabled: boolean; dev_only: boolean; delivery: string; code_ttl_s: number | null };
}

export interface SessionUser {
  id: string;
  email: string | null;
  email_verified: boolean;
  role: AccountRole;
  is_active: boolean;
}

export interface SessionPayload {
  user: SessionUser;
  capabilities: string[];
  investigator_access?: string;
  admin_access?: string;
  message?: string;
  entry?: "citizen" | "investigator";
}

export interface LocalStartResponse {
  status: string;
  delivery: string;
  expires_in: number;
  message: string;
}

export interface LocalVerifyResponse {
  token: string;
  token_type: string;
  expires_in: number;
}

export interface Me {
  id: string;
  email: string | null;
  email_verified: boolean;
  display_name: string | null;
  role: AccountRole;
  created_at: string | null;
  privacy: { allow_case_matching: boolean };
  message?: string;
}

export interface MyStats {
  total: number;
  by_status: Record<ComplaintStatus, number>;
  by_category: Record<Category, number>;
  last_complaint_at: string | null;
  unread_alerts: number;
}

export interface AnalysisSummary {
  version: number;
  created_at: string | null;
  status: "COMPLETED" | "PARTIAL" | "UNAVAILABLE";
  risk_score: number | null;
  risk_level: RiskLevel | null;
  score_semantics: string;
}

export interface Evidence {
  id: string;
  complaint_id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  status: "stored" | "rejected" | "deleted";
  created_at: string | null;
}

export interface StatusHistoryRow {
  from_status: ComplaintStatus | null;
  to_status: ComplaintStatus;
  actor_user_id: string | null;
  note: string | null;
  created_at: string | null;
}

export interface ComplaintBase {
  id: string;
  category: Category;
  title: string;
  description: string;
  status: ComplaintStatus;
  incident_at: string | null;
  reported_at: string | null;
  amount: string | null;
  currency: string | null;
  payment_method: PaymentMethod | null;
  upi_id: string | null;
  utr_reference: string | null;
  suspect_phone: string | null;
  suspect_email: string | null;
  suspicious_url: string | null;
  location_text: string | null;
  city: string | null;
  state: string | null;
  latitude: number | null;
  longitude: number | null;
  social_platform: string | null;
  social_account: string | null;
  bank_name: string | null;
  bank_account: string | null;
  ifsc: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** Present for INVESTIGATOR / ADMIN only. */
  owner_user_id?: string;
}

export interface ComplaintListItem extends ComplaintBase {
  evidence_count: number;
  latest_analysis: AnalysisSummary | null;
}

export interface ComplaintDetail extends ComplaintBase {
  status_history: StatusHistoryRow[];
  latest_analysis: AnalysisSummary | null;
  evidence: Evidence[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Indicator {
  code: string;
  weight: number;
  text: string;
  source: string;
}

export interface Analysis {
  id: string;
  complaint_id: string;
  version: number;
  created_at: string | null;
  pipeline_version: string;
  status: "COMPLETED" | "PARTIAL" | "UNAVAILABLE";
  capabilities: Record<string, string>;
  risk_score: number | null;
  risk_level: RiskLevel | null;
  score_semantics: string;
  classification: string | null;
  confidence: number | null;
  indicators: Indicator[];
  explanation: string | null;
  recommended_actions: { text: string; source: string }[];
  limitations: string[];
  explanation_source: string | null;
  llm_explanation: string | null;
  confidence_semantics: string | null;
  capability_reasons: Record<string, string>;
  unavailable_capabilities: string[];
  unanalysed_attachments: unknown[];
  category_model: { label?: string; abstained?: boolean; model_version?: string; trained_on?: string } | null;
  history?: AnalysisSummary[];
}

export interface Alert {
  id: string;
  complaint_id: string | null;
  kind: string;
  message: string;
  created_at: string | null;
  read_at: string | null;
}

export interface AlertsPage extends Page<Alert> {
  unread: number;
}

export interface AdminUser {
  id: string;
  email: string | null;
  email_verified: boolean;
  display_name: string | null;
  role: AccountRole;
  role_source: string | null;
  is_active: boolean;
  created_at: string | null;
  last_login_at: string | null;
}

export interface AuditRow {
  id: string;
  actor_user_id: string | null;
  action: string;
  target_user_id: string | null;
  before: unknown;
  after: unknown;
  created_at: string | null;
}

export type AdminHealth = Record<string, { status: string; [k: string]: unknown }>;

/** Body for POST /complaints. Optional keys are omitted when empty (the service stores NULL). */
export interface ComplaintCreate {
  category: Category;
  title: string;
  description: string;
  incident_at?: string;
  amount?: string;
  payment_method?: PaymentMethod;
  upi_id?: string;
  utr_reference?: string;
  bank_name?: string;
  bank_account?: string;
  ifsc?: string;
  suspect_phone?: string;
  suspect_email?: string;
  suspicious_url?: string;
  social_platform?: string;
  social_account?: string;
  location_text?: string;
  city?: string;
  state?: string;
  latitude?: number;
  longitude?: number;
}

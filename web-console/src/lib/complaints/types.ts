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

/** Filter values accepted by GET /complaints?risk_level= (exact level of the latest analysis). */
export const RISK_FILTERS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

/** What a result can say (since 2026-10-18; absent on older analyses). */
export type Assessment =
  | "indicators_found"
  | "model_prediction_only"
  | "no_indicators_found"
  | "insufficient_evidence"
  | "not_assessed";

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
  /** Level of the latest analysis per complaint (added with the CRITICAL level). */
  by_risk_level?: Record<string, number>;
}

export type AnalysisStatus = "COMPLETED" | "PARTIAL" | "UNAVAILABLE" | "INSUFFICIENT_INPUT";

export interface AnalysisSummary {
  version: number;
  created_at: string | null;
  status: AnalysisStatus;
  risk_score: number | null;
  risk_level: RiskLevel | null;
  score_semantics: string;
  assessment?: Assessment | null;
}

/**
 * Per-file transcription outcome (services/complaints/complaints/transcribe.py). Stored:
 * transcribed | truncated | decode_failed | unsupported_format. Reported per analysis run:
 * engine_not_configured | model_not_available_offline | timeout | decode_unavailable |
 * budget_exhausted | not_attempted_limit | engine_error. Before any run: not_attempted.
 */
export type TranscriptionStatus =
  | "transcribed"
  | "truncated"
  | "engine_not_configured"
  | "model_not_available_offline"
  | "timeout"
  | "decode_failed"
  | "decode_unavailable"
  | "unsupported_format"
  | "budget_exhausted"
  | "not_attempted_limit"
  | "engine_error"
  | "not_attempted"
  | (string & {});

export interface EvidenceTranscription {
  status: TranscriptionStatus;
  reason: string;
  /** Plain-language reason from the service. */
  message: string;
  /** stored = a transcript row; last_analysis = outcome of the latest run; none = not yet run. */
  source: "stored" | "last_analysis" | "none";
  transcript_available: boolean;
  engine: string | null;
  model: string | null;
  language: string | null;
  duration_s: number | null;
  transcribed_s: number | null;
  truncated: boolean;
  segment_count: number;
  checked_at: string | null;
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
  /** Added with audio transcription (Phase 6); absent from older servers. */
  kind?: "audio" | "image" | "document" | "other";
  /** Audio files only; null for other kinds. */
  transcription?: EvidenceTranscription | null;
}

export interface TranscriptSegment {
  start_s: number;
  end_s: number;
  text: string;
}

/** GET /complaints/{id}/evidence/{eid}/transcript */
export interface Transcript {
  evidence_id: string;
  complaint_id: string;
  status: "transcribed" | "truncated";
  engine: string | null;
  model: string | null;
  language: string | null;
  duration_s: number | null;
  transcribed_s: number | null;
  truncated: boolean;
  segments: TranscriptSegment[];
  created_at: string | null;
  note: string;
}

/** One audio file in an analysis (`audio_transcription`). */
export interface AudioTranscriptionSummary {
  evidence_id: string;
  mime_type: string;
  status: TranscriptionStatus;
  reason: string;
  message: string;
  engine: string | null;
  model: string | null;
  language: string | null;
  duration_s: number | null;
  transcribed_s: number | null;
  truncated: boolean;
  segment_count: number;
  reused: boolean;
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

/** Provenance vocabulary (services/complaints/complaints/provenance.py); "legacy" = not derivable. */
export type ProvenanceSource = "rule" | "ml" | "llm" | "static_guidance" | "legacy";

export interface ProvenanceEvidence {
  type: "text_span" | "field" | "whole_text" | "keyed_by" | "not_recorded" | "generated" | "evidence_transcript" | "text_link" | string;
  /** evidence_transcript: the audio evidence file and the segment's time range (seconds). */
  evidence_id?: string | null;
  start_s?: number | null;
  end_s?: number | null;
  field?: string;
  start?: number;
  end?: number;
  snippet?: string;
  granularity?: string;
  host?: string | null;
  note?: string;
  [k: string]: unknown;
}

export interface Indicator {
  code: string;
  weight: number;
  text: string;
  source: ProvenanceSource | string;
  method?: string;
  stage?: string;
  evidence?: ProvenanceEvidence;
  derived_from_legacy?: boolean;
}

export interface ExplanationPart {
  text: string;
  /** e.g. "LLM-generated (qwen2.5:0.5b), may be imprecise" on the llm part */
  label?: string;
  source: ProvenanceSource | string;
  method: string;
  evidence?: ProvenanceEvidence;
  derived_from_legacy?: boolean;
}

export interface RecommendedAction {
  text: string;
  source: string;
  method?: string;
  evidence?: ProvenanceEvidence;
}

export interface ProvenanceStage {
  stage: string;
  source: ProvenanceSource | string;
  method: string;
  status: "ran" | "unavailable" | "skipped" | "not_configured" | string;
  reason: string | null;
  version: string | null;
  /** audio_transcription stages: one per audio file. */
  evidence_id?: string;
  reused?: boolean;
}

export interface Provenance {
  schema?: number;
  derived_from_legacy: boolean;
  legacy?: boolean;
  note?: string;
  pipeline?: string;
  stages?: ProvenanceStage[];
  ran?: string[];
  unavailable?: { stage: string; reason: string | null }[];
  skipped?: { stage: string; reason: string | null }[];
  model_versions?: Record<string, string | null>;
}

export interface CategoryModelOut {
  label?: string;
  abstained?: boolean;
  model_version?: string;
  trained_on?: string;
  top?: { category: string; score: number }[];
  top_categories?: { category: string; score: number }[];
  abstention_threshold?: number;
  min_confidence?: number;
}

/** Fields shared by a stored analysis and a stateless check (POST /analyze). */
export interface AnalysisResult {
  created_at: string | null;
  pipeline_version: string;
  status: AnalysisStatus;
  capabilities: Record<string, string>;
  risk_score: number | null;
  risk_level: RiskLevel | null;
  assessment?: Assessment | null;
  score_semantics: string;
  classification: string | null;
  confidence: number | null;
  confidence_interpretation?: string | null;
  indicators: Indicator[];
  explanation: string | null;
  explanation_parts?: ExplanationPart[];
  recommended_actions: RecommendedAction[];
  limitations: string[];
  explanation_source: string | null;
  llm_explanation: string | null;
  llm_model?: string | null;
  capability_reasons: Record<string, string>;
  unavailable_capabilities: string[];
  unanalysed_attachments: unknown[];
  /** Per audio evidence file (complaint analyses since Phase 6). */
  audio_transcription?: AudioTranscriptionSummary[];
  category_model: CategoryModelOut | null;
  provenance?: Provenance | null;
}

export interface InputCapability {
  status: string;
  reason?: string;
  note?: string;
  method?: string;
  methods?: string[];
}

/** POST /analyze: nothing is stored. */
export interface CheckResult extends AnalysisResult {
  persisted: false;
  input_capabilities?: Record<string, InputCapability>;
}

export interface CheckRequest {
  text: string;
  url?: string;
  upi_id?: string;
  phone?: string;
  email?: string;
  amount?: string;
  payment_method?: PaymentMethod;
}

export interface AnalysisVersion extends AnalysisSummary {
  id: string;
  pipeline_version: string;
  classification: string | null;
  confidence: number | null;
  model_versions?: Record<string, string | null> | null;
  provenance_derived_from_legacy?: boolean;
}

export interface ModelEntry {
  id: string;
  kind: string;
  source: string;
  version: string;
  algorithm?: string | null;
  artifact: { file?: string | null; bytes?: number | null; sha256: string | null; sha256_recorded_at_training?: string | null; verified_at_load?: string };
  training_data: { synthetic: boolean; description: string; rows?: number | null; split?: string };
  metrics: {
    basis: string;
    n: number | null;
    macro_f1: number | null;
    accuracy: number | null;
    abstention_rate: number | null;
    accuracy_on_answered?: number | null;
    abstention_threshold: number | null;
    metric_basis?: string;
    per_class?: Record<string, { precision: number; recall: number; f1: number; support: number }>;
    /** Binary classifiers (call-guard clf-v1): metrics at one probability threshold. */
    binary?: { threshold: number; precision: number | null; recall: number | null; f1: number | null; roc_auc: number | null; false_positive_rate: number | null };
    held_out_benign?: string;
  } | null;
  metrics_unavailable_reason?: string;
  leaky_random_split: { is_headline: false; macro_f1: number | null; accuracy?: number | null; note: string; metric_label?: string } | null;
  disclaimer?: string | null;
  limitations: string[];
  confidence_meaning?: string;
  runtime: { status: string; reason: string | null };
}

export interface ModelsInfo {
  synthetic_data_warning: string;
  score_semantics: string;
  models: ModelEntry[];
  llm: { provider: string; status: "not_configured" | "unavailable" | "ready" | string; model: string | null; reason: string | null; used_for: string };
  /** Speech-to-text for audio evidence (Phase 6). */
  stt?: {
    status: "ready" | "available_offline" | "model_not_available_offline" | "engine_not_configured" | "configured" | "not_loaded" | string;
    engine: string;
    model: string | null;
    reason: string | null;
    local: boolean;
    evidence_transcription: "enabled" | "disabled";
    used_for: string;
    limits?: { max_seconds_per_file: number; max_files_per_run: number; run_budget_s: number };
    decoders?: { wav: string; mp3_ogg_m4a: string };
  };
  /** Fraud DNA embeddings (Phase 8). */
  dna?: {
    status: "ready" | "loading" | "unavailable" | string;
    reason: string | null;
    message: string | null;
    model: string | null;
    revision?: string | null;
    dimensions?: number | null;
    thresholds?: { similar: number; strong: number };
    index?: { backend: string; mode: string; collection: string | null };
    used_for: string;
  };
}

export interface Analysis {
  id: string;
  complaint_id: string;
  version: number;
  created_at: string | null;
  pipeline_version: string;
  status: AnalysisStatus;
  capabilities: Record<string, string>;
  risk_score: number | null;
  risk_level: RiskLevel | null;
  assessment?: Assessment | null;
  score_semantics: string;
  classification: string | null;
  confidence: number | null;
  indicators: Indicator[];
  explanation: string | null;
  recommended_actions: RecommendedAction[];
  limitations: string[];
  explanation_source: string | null;
  explanation_parts?: ExplanationPart[];
  llm_explanation: string | null;
  llm_model?: string | null;
  confidence_semantics: string | null;
  confidence_interpretation?: string | null;
  capability_reasons: Record<string, string>;
  unavailable_capabilities: string[];
  unanalysed_attachments: unknown[];
  audio_transcription?: AudioTranscriptionSummary[];
  category_model: CategoryModelOut | null;
  provenance?: Provenance | null;
  history?: AnalysisSummary[];
}

export interface Alert {
  id: string;
  complaint_id: string | null;
  kind: string;
  message: string;
  created_at: string | null;
  read_at: string | null;
  risk_level?: RiskLevel | null;
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

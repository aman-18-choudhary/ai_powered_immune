import { request } from "./api";
import type { NetworkComplaint } from "./network";

/**
 * Fraud DNA (Phase 8), staff only: complaints whose descriptions are worded alike, from a local
 * sentence-embedding model. Similar wording is a lead about the scam METHOD, never proof that the
 * same person or crime is involved, and it is a different thing from a shared identifier.
 */
export type DnaStatus = "ok" | "unavailable" | "loading" | "insufficient_text" | "not_loaded";
export type DnaBand = "strong" | "moderate";

export const DNA_DISCLAIMER = "Similar descriptions suggest a similar scam method. They are not proof that the same person, group or crime is involved.";

export interface SimilarItem {
  complaint: NetworkComplaint;
  similarity: number;
  band: DnaBand;
  shared_indicators: string[];
  same_category: boolean;
  identifier_link: "observed" | "inferred" | null;
  explanation: string;
}

export interface SimilarResponse {
  complaint_id: string;
  status: DnaStatus | string;
  reason?: string | null;
  message?: string | null;
  matching: "enabled" | "disabled";
  matching_message: string | null;
  disclaimer: string;
  items: SimilarItem[];
  total: number;
  limit: number;
  truncated?: boolean;
  thresholds: { similar: number; strong: number };
  model?: { id: string; revision: string | null };
}

const enc = encodeURIComponent;

export const dnaApi = {
  similar: (id: string, limit: number, signal?: AbortSignal) => request<SimilarResponse>("GET", `/complaints/${enc(id)}/similar?limit=${limit}`, { signal }),
};

export const BAND_LABEL: Record<DnaBand, string> = { strong: "Strong similarity", moderate: "Moderate similarity" };

import { request } from "./api";
import type { Category, ComplaintStatus, RiskLevel } from "./types";

/**
 * Complaint network (Phase 9), staff only. Identifiers come from the structured complaint fields
 * only; the API returns masked display values. Identifier search is a POST body: an identifier is
 * never put in a URL.
 */
export const ENTITY_TYPES = ["phone", "upi", "email", "url", "domain", "bank_account", "social_account"] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];
export type LinkType = "observed" | "inferred" | "confirmed" | "rejected";
export type LinkBasis = "observed" | "inferred";
export type Decision = "confirmed" | "rejected";

export const DISCLAIMER = "Shared identifiers are leads, not proof that the same person is involved.";
/** Shown wherever a confirmed link appears: a link review is about the complaints, never about guilt. */
export const CONFIRMED_NOT_GUILT = "A confirmed link means an investigator judged the complaints to be part of the same case. It is not a finding that the identifier's owner, or anyone else, committed a crime.";

export interface NetworkComplaint {
  id: string;
  title: string;
  category: Category;
  status: ComplaintStatus;
  risk_level: RiskLevel | null;
  created_at: string | null;
}

export interface SharedIdentifier {
  type: EntityType;
  display: string;
  link: LinkBasis;
  this_field: string;
  other_field: string;
  /** bank_account only: whether the IFSC given on both complaints matches. */
  ifsc?: "same" | "different" | "not_given";
}

export interface LinkReview {
  id: string;
  decision: Decision;
  basis: LinkBasis;
  reviewer_user_id: string;
  note: string | null;
  created_at: string | null;
  review_count?: number;
}

export interface RelatedItem {
  complaint: NetworkComplaint;
  link_type: LinkType;
  basis: LinkBasis;
  shared: SharedIdentifier[];
  review: LinkReview | null;
}

export interface RelatedResponse {
  complaint_id: string;
  linking: "enabled" | "disabled";
  linking_message: string | null;
  disclaimer: string;
  items: RelatedItem[];
  total: number;
  limit: number;
  offset: number;
  truncated: boolean;
}

export interface GraphComplaintNode extends Omit<NetworkComplaint, "id"> {
  id: string;
  kind: "complaint";
  complaint_id: string;
  center: boolean;
  distance: number;
}

export interface GraphEntityNode {
  id: string;
  kind: "entity";
  entity_type: EntityType;
  display: string;
  complaint_count: number;
}

export type GraphNode = GraphComplaintNode | GraphEntityNode;

export interface GraphIdentifierEdge {
  id: string;
  kind: "identifier";
  source: string;
  target: string;
  entity_type: EntityType;
  link: LinkBasis;
  source_field: string;
  evidence: string;
}

export interface GraphReviewEdge {
  id: string;
  kind: "review";
  source: string;
  target: string;
  decision: Decision;
  basis: LinkBasis;
  reviewer_user_id: string;
  note: string | null;
  created_at: string | null;
  review_count: number;
}

export type GraphEdge = GraphIdentifierEdge | GraphReviewEdge;

export interface GraphResponse {
  center: string;
  depth: number;
  linking: "enabled" | "disabled";
  linking_message: string | null;
  disclaimer: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  counts: { complaints: number; entities: number; edges: number };
  limits: { depth: number; complaints: number; entities: number; edges: number };
}

export interface Cluster {
  id: string;
  size: number;
  member_ids: string[];
  member_ids_truncated: boolean;
  members: NetworkComplaint[];
  members_truncated: boolean;
  connecting_types: Partial<Record<EntityType, number>>;
  latest_created_at: string | null;
  earliest_created_at: string | null;
}

export interface ClustersResponse {
  items: Cluster[];
  total: number;
  limit: number;
  offset: number;
  truncated: boolean;
  disclaimer: string;
  basis: string;
}

export interface SearchRequest {
  type: EntityType;
  value: string;
  platform?: string;
  limit?: number;
  offset?: number;
}

export interface SearchResponse {
  entity: { type: EntityType; display: string };
  items: (NetworkComplaint & { source_field: string })[];
  total: number;
  limit: number;
  offset: number;
  disclaimer: string;
}

export interface ReviewResult {
  review: LinkReview;
  link_type: Decision;
  previous_decision: Decision | null;
  complaint_low: string;
  complaint_high: string;
}

const enc = encodeURIComponent;

export const networkApi = {
  related: (id: string, p: { limit: number; offset: number }, signal?: AbortSignal) =>
    request<RelatedResponse>("GET", `/complaints/${enc(id)}/related?limit=${p.limit}&offset=${p.offset}`, { signal }),
  graph: (id: string, depth: 1 | 2, signal?: AbortSignal) => request<GraphResponse>("GET", `/complaints/${enc(id)}/graph?depth=${depth}`, { signal }),
  clusters: (p: { limit: number; offset: number }, signal?: AbortSignal) => request<ClustersResponse>("GET", `/graph/clusters?limit=${p.limit}&offset=${p.offset}`, { signal }),
  /** The identifier travels in the POST body only. */
  search: (body: SearchRequest, signal?: AbortSignal) => request<SearchResponse>("POST", "/entities/search", { body, signal }),
  review: (body: { complaint_a: string; complaint_b: string; decision: Decision; note?: string }) =>
    request<ReviewResult>("POST", "/complaint-links/review", { body }),
};

export const ENTITY_LABEL: Record<EntityType, string> = {
  phone: "Phone",
  upi: "UPI ID",
  email: "Email",
  url: "Link (URL)",
  domain: "Domain",
  bank_account: "Bank account",
  social_account: "Social account",
};

export const FIELD_LABEL: Record<string, string> = {
  suspect_phone: "Phone",
  upi_id: "UPI ID",
  suspect_email: "Email",
  suspicious_url: "Suspicious link",
  bank_account: "Bank account",
  social_account: "Social account",
};

export const LINK_LABEL: Record<LinkType, string> = {
  observed: "Observed",
  inferred: "Inferred",
  confirmed: "Confirmed",
  rejected: "Rejected",
};

export const LINK_HINT: Record<LinkType, string> = {
  observed: "Both complaints give exactly the same identifier.",
  inferred: "Only the website domain matches (different links). A weak lead.",
  confirmed: "An investigator judged that these complaints belong to the same case. This is not a finding that anyone committed a crime.",
  rejected: "An investigator reviewed this pair and rejected the link.",
};

export const IFSC_LABEL: Record<NonNullable<SharedIdentifier["ifsc"]>, string> = {
  same: "same IFSC",
  different: "different IFSC",
  not_given: "IFSC not given on both",
};

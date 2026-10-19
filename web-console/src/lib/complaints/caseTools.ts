import { request } from "./api";

/** Investigator case tools (Phase 12): staff notes, the case timeline, the report download. */
export interface InvestigationNote {
  id: string;
  complaint_id: string;
  author_user_id: string;
  body: string;
  created_at: string | null;
}

export type TimelineKind = "complaint_created" | "status_changed" | "evidence_added" | "evidence_removed" | "transcript" | "analysis" | "link_review" | "investigation_note";

export interface TimelineItem {
  at: string | null;
  kind: TimelineKind | string;
  text: string;
  note?: string | null;
  actor_user_id?: string | null;
  related_complaint_id?: string;
}

const enc = encodeURIComponent;

export const caseApi = {
  notes: (id: string, p: { limit: number; offset: number }, signal?: AbortSignal) =>
    request<{ items: InvestigationNote[]; total: number; limit: number; offset: number }>("GET", `/complaints/${enc(id)}/notes?limit=${p.limit}&offset=${p.offset}`, { signal }),
  addNote: (id: string, body: string) => request<InvestigationNote>("POST", `/complaints/${enc(id)}/notes`, { body: { body } }),
  timeline: (id: string, signal?: AbortSignal) => request<{ complaint_id: string; items: TimelineItem[] }>("GET", `/complaints/${enc(id)}/timeline`, { signal }),
  report: (id: string) => request<Blob>("GET", `/complaints/${enc(id)}/report`, { blob: true }),
};

export const TIMELINE_LABEL: Record<string, string> = {
  complaint_created: "Registered",
  status_changed: "Status",
  evidence_added: "Evidence",
  evidence_removed: "Evidence removed",
  transcript: "Transcript",
  analysis: "Analysis",
  link_review: "Link review",
  investigation_note: "Note",
};

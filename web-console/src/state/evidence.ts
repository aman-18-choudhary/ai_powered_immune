import { create } from "zustand";
import { endpoints } from "@/lib/api/endpoints";
import { ApiError, friendlyError } from "@/lib/api/client";
import type { CaseView, VerifyResult } from "@/types/api";

export const HERO_CASE = "hero-case";

interface EvidenceState {
  runId: string | null;
  /** The case the evidence page works on (the player's finale always uses HERO_CASE). */
  caseId: string;
  caseView: CaseView | null;
  etag: string | null;
  building: boolean;
  caseError: string | null;
  prep: "idle" | "working" | "ready" | "error";
  prepError: string | null;
  blob: Blob | null;
  size: number | null;
  verifying: boolean;
  verify: VerifyResult | null;
  verifyError: string | null;
  prepare: (runId: string) => Promise<void>;
  buildCase: (body: { case_id: string; title?: string; case_refs?: string[] }) => Promise<boolean>;
  exportPackage: () => Promise<void>;
  runVerify: () => Promise<void>;
  download: () => void;
  reset: () => void;
}

export const useEvidence = create<EvidenceState>((set, get) => ({
  runId: null,
  caseId: HERO_CASE,
  caseView: null,
  etag: null,
  building: false,
  caseError: null,
  prep: "idle",
  prepError: null,
  blob: null,
  size: null,
  verifying: false,
  verify: null,
  verifyError: null,
  async prepare(runId) {
    const s = get();
    if (s.runId === runId && (s.prep === "working" || s.prep === "ready")) return;
    set({ runId, caseId: HERO_CASE, prep: "working", prepError: null, blob: null, size: null, etag: null, verify: null, verifyError: null });
    try {
      await endpoints.createCase({});
      const blob = await endpoints.packageBlob(HERO_CASE);
      if (get().runId !== runId) return;
      set({ blob, size: blob.size, prep: "ready" });
    } catch (e) {
      if (get().runId === runId) set({ prep: "error", prepError: friendlyError(e) });
    }
  },
  async buildCase(body) {
    set({ building: true, caseError: null });
    try {
      const view = await endpoints.createCase(body);
      set({ building: false, caseView: view, caseId: view.case_id ?? body.case_id, blob: null, size: null, etag: null, verify: null, prep: "idle" });
      return true;
    } catch (e) {
      const msg =
        e instanceof ApiError && e.code === "conflict"
          ? "A different case with this id already exists in this run. Pick another id."
          : friendlyError(e);
      set({ building: false, caseError: msg });
      return false;
    }
  },
  async exportPackage() {
    const { caseId } = get();
    set({ prep: "working", prepError: null });
    try {
      const r = await endpoints.packageWithEtag(caseId);
      set({ blob: r.data, size: r.data.size, etag: r.headers.etag, prep: "ready" });
    } catch (e) {
      set({ prep: "error", prepError: friendlyError(e) });
    }
  },
  async runVerify() {
    set({ verifying: true, verifyError: null });
    try {
      const r = await endpoints.verify(get().caseId);
      set({ verify: r, verifying: false });
    } catch (e) {
      set({ verifying: false, verifyError: friendlyError(e) });
    }
  },
  download() {
    const { blob } = get();
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${get().caseId}-evidence-package.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  },
  reset: () =>
    set({
      runId: null, caseId: HERO_CASE, caseView: null, etag: null, building: false, caseError: null, prep: "idle",
      prepError: null, blob: null, size: null, verify: null, verifyError: null, verifying: false,
    }),
}));

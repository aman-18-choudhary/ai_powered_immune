import { create } from "zustand";
import { endpoints } from "@/lib/api/endpoints";
import { friendlyError } from "@/lib/api/client";
import type { VerifyResult } from "@/types/api";

export const HERO_CASE = "hero-case";

interface EvidenceState {
  runId: string | null;
  prep: "idle" | "working" | "ready" | "error";
  prepError: string | null;
  blob: Blob | null;
  size: number | null;
  verifying: boolean;
  verify: VerifyResult | null;
  verifyError: string | null;
  prepare: (runId: string) => Promise<void>;
  runVerify: () => Promise<void>;
  download: () => void;
  reset: () => void;
}

export const useEvidence = create<EvidenceState>((set, get) => ({
  runId: null,
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
    set({ runId, prep: "working", prepError: null, blob: null, size: null, verify: null, verifyError: null });
    try {
      await endpoints.createCase({});
      const blob = await endpoints.packageBlob(HERO_CASE);
      if (get().runId !== runId) return;
      set({ blob, size: blob.size, prep: "ready" });
    } catch (e) {
      if (get().runId === runId) set({ prep: "error", prepError: friendlyError(e) });
    }
  },
  async runVerify() {
    set({ verifying: true, verifyError: null });
    try {
      const r = await endpoints.verify(HERO_CASE);
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
    a.download = `${HERO_CASE}-evidence-package.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  },
  reset: () =>
    set({ runId: null, prep: "idle", prepError: null, blob: null, size: null, verify: null, verifyError: null, verifying: false }),
}));

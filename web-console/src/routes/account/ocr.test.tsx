// MOCKED fetch (no server): Phase 6 OCR of screenshot evidence. SYNTHETIC text.
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { complaint, json, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";
import ComplaintPage from "./ComplaintPage";

const A = "c0ffee00-0000-4000-8000-000000000001";
afterEach(() => vi.unstubAllGlobals());

const IMG = {
  id: "e1", complaint_id: A, original_filename: "shot.png", mime_type: "image/png", size_bytes: 10, sha256: "ab".repeat(32), status: "stored", created_at: null, kind: "image",
  transcription: null,
  ocr: { status: "extracted", source: "stored", message: "Text was read from the image by OCR (machine-read; it may contain errors).", text_available: true, chars: 120, mean_confidence: 91.5, languages: ["eng", "hin"], engine: "tesseract 5.5.3" },
};

describe("screenshot text (OCR)", () => {
  it("shows the OCR state and the machine-read text on demand", async () => {
    useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION("CITIZEN") as never });
    mockFetch({
      [`GET /complaints/${A}`]: json(200, complaint({ evidence: [IMG] })),
      [`GET /complaints/${A}/analysis`]: json(404, { error: "analysis_not_found", message: "No analysis has been run for this complaint." }),
      [`GET /complaints/${A}/evidence/e1/ocr`]: json(200, { evidence_id: "e1", status: "extracted", engine: "tesseract 5.5.3", languages: ["eng", "hin"], mean_confidence: 91.5, text: "SYNTHETIC: your account will be blocked, update KYC", note: "Machine-read text (OCR); it may contain recognition errors." }),
    });
    renderApp(
      <Routes>
        <Route path="/me/complaints/:id" element={<ComplaintPage />} />
      </Routes>,
      `/me/complaints/${A}`,
    );
    const line = await screen.findByTestId("ocr-status");
    expect(line).toHaveAttribute("data-status", "extracted");
    expect(line).toHaveTextContent("OCR confidence 92/100");
    await userEvent.click(screen.getByTestId("ocr-toggle"));
    expect(await screen.findByTestId("ocr-text")).toHaveTextContent("update KYC");
    expect(screen.getByTestId("ocr-viewer")).toHaveTextContent("may contain recognition errors");
  });

  it("an image that could not be read says why", async () => {
    useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION("CITIZEN") as never });
    mockFetch({
      [`GET /complaints/${A}`]: json(200, complaint({ evidence: [{ ...IMG, ocr: { ...IMG.ocr, status: "engine_unavailable", text_available: false, mean_confidence: null, message: "No OCR engine is installed on this server." } }] })),
      [`GET /complaints/${A}/analysis`]: json(404, { error: "analysis_not_found", message: "none" }),
    });
    renderApp(
      <Routes>
        <Route path="/me/complaints/:id" element={<ComplaintPage />} />
      </Routes>,
      `/me/complaints/${A}`,
    );
    expect(await screen.findByTestId("ocr-status")).toHaveTextContent("No OCR engine is installed");
    expect(screen.queryByTestId("ocr-toggle")).not.toBeInTheDocument();
  });
});

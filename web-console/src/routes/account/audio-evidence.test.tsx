// MOCKED fetch (no server): audio evidence transcription status, the transcript viewer, audio
// indicators with time offsets in the analysis panel, the Models page STT panel and the Check
// page wording. Shapes follow services/complaints (Phase 6).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { complaint, json, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";
import { clock, span, transcriptionLabel } from "@/lib/complaints/transcription";
import ComplaintPage from "./ComplaintPage";
import CheckMessage from "./CheckMessage";
import Models from "./Models";

const ID = "c0ffee00-0000-4000-8000-000000000001";
const AUDIO = "a0d10000-0000-4000-8000-000000000001";
const AUDIO2 = "a0d10000-0000-4000-8000-000000000002";

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION("CITIZEN") as never }));

function audio(id: string, transcription: Record<string, unknown>, name = "call.wav") {
  return {
    id, complaint_id: ID, original_filename: name, mime_type: "audio/wav", size_bytes: 320044, sha256: "ab".repeat(32),
    status: "stored", created_at: "2026-10-16T09:00:00+00:00", kind: "audio",
    transcription: {
      status: "not_attempted", reason: "not_attempted", message: "not transcribed yet: audio evidence is transcribed when the complaint analysis runs",
      source: "none", transcript_available: false, engine: null, model: null, language: null, duration_s: null, transcribed_s: null,
      truncated: false, segment_count: 0, checked_at: null, ...transcription,
    },
  };
}

const TRANSCRIBED = {
  status: "transcribed", reason: "transcribed", message: "transcribed locally", source: "stored", transcript_available: true,
  engine: "faster_whisper", model: "base", language: "en", duration_s: 13.4, transcribed_s: 13.4, segment_count: 3,
};

const TRANSCRIPT = {
  evidence_id: AUDIO, complaint_id: ID, status: "transcribed", engine: "faster_whisper", model: "base", language: "en",
  duration_s: 13.4, transcribed_s: 13.4, truncated: false, created_at: "2026-10-16T09:01:00+00:00",
  note: "Machine transcription made locally; words can be misheard and time offsets are approximate.",
  segments: [
    { start_s: 0, end_s: 5.16, text: "Hello, this is Officer Sharma calling from the CBI." },
    { start_s: 6.16, end_s: 7.76, text: "You are under digital arrest." },
    { start_s: 72.5, end_s: 75, text: "Transfer the money to the safe account now." },
  ],
};

function renderDetail() {
  return renderApp(
    <Routes>
      <Route path="/me/complaints/:id" element={<ComplaintPage />} />
    </Routes>,
    `/me/complaints/${ID}`,
  );
}

const NO_ANALYSIS = json(404, { error: "analysis_not_found", message: "No analysis has been run for this complaint." });

describe("time helpers", () => {
  it("formats offsets as m:ss and statuses as words", () => {
    expect(clock(0)).toBe("0:00");
    expect(clock(72.5)).toBe("1:12");
    expect(clock(null)).toBe("?");
    expect(span(6.16, 7.76)).toBe("0:06–0:07");
    expect(transcriptionLabel("engine_not_configured")).toMatch(/no speech-to-text engine configured/);
    expect(transcriptionLabel("something_new")).toBe("Transcription: something new");
  });
});

describe("audio evidence on the complaint page", () => {
  it("shows the transcription status and opens the transcript with timestamps", async () => {
    const { calls } = mockFetch({
      [`GET /complaints/${ID}`]: json(200, complaint({ evidence: [audio(AUDIO, TRANSCRIBED)] })),
      [`GET /complaints/${ID}/analysis`]: NO_ANALYSIS,
      [`GET /complaints/${ID}/evidence/${AUDIO}/transcript`]: json(200, TRANSCRIPT),
    });
    const user = userEvent.setup();
    renderDetail();
    const st = await screen.findByTestId("transcription-status");
    expect(st).toHaveTextContent("Transcribed · 3 segments · language en");
    expect(st).toHaveAttribute("data-status", "transcribed");
    const toggle = screen.getByTestId("transcript-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const tr = await screen.findByTestId("transcript");
    expect(tr).toHaveTextContent("faster_whisper:base, language en");
    expect(tr).toHaveTextContent("time offsets are approximate");
    const segs = within(tr).getAllByTestId("transcript-segment");
    expect(segs.map((s) => s.textContent)).toEqual([
      "0:00–0:05Hello, this is Officer Sharma calling from the CBI.",
      "0:06–0:07You are under digital arrest.",
      "1:12–1:15Transfer the money to the safe account now.",
    ]);
    expect(calls.filter((c) => c.path.endsWith("/transcript"))).toHaveLength(1);
    await user.click(toggle);
    expect(screen.queryByTestId("transcript")).not.toBeInTheDocument();
  });

  it.each([
    ["engine_not_configured", "no speech-to-text engine is configured for evidence on this server", "Not transcribed: no speech-to-text engine configured"],
    ["model_not_available_offline", "the speech-to-text model is not available offline on this server (models are never downloaded at runtime)", "model not available offline"],
    ["not_attempted_limit", "not transcribed in this run: the per-run file limit was reached; run the analysis again to continue", "per-run file limit"],
    ["decode_unavailable", "no decoder for this audio format is installed (PyAV is needed for MP3, OGG and M4A)", "no decoder for this format"],
  ])("a file that was not transcribed shows the precise reason (%s) and no transcript button", async (status, message, label) => {
    mockFetch({
      [`GET /complaints/${ID}`]: json(200, complaint({ evidence: [audio(AUDIO, { status, reason: status, message, source: "last_analysis" })] })),
      [`GET /complaints/${ID}/analysis`]: NO_ANALYSIS,
    });
    renderDetail();
    const st = await screen.findByTestId("transcription-status");
    expect(st).toHaveTextContent(label);
    expect(st).toHaveTextContent(message);
    expect(st).toHaveTextContent("(latest analysis run)");
    expect(screen.queryByTestId("transcript-toggle")).not.toBeInTheDocument();
  });

  it("before any analysis an audio file reads 'not transcribed yet'; images carry no transcription line", async () => {
    const img = { id: "e-img", complaint_id: ID, original_filename: "shot.png", mime_type: "image/png", size_bytes: 10, sha256: "cd".repeat(32), status: "stored", created_at: null, kind: "image", transcription: null };
    mockFetch({
      [`GET /complaints/${ID}`]: json(200, complaint({ evidence: [audio(AUDIO, {}), img] })),
      [`GET /complaints/${ID}/analysis`]: NO_ANALYSIS,
    });
    renderDetail();
    const lines = await screen.findAllByTestId("transcription-status");
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveTextContent("Not transcribed yet · not transcribed yet: audio evidence is transcribed when the complaint analysis runs");
  });

  it("a transcript that cannot be loaded is reported, not faked", async () => {
    mockFetch({
      [`GET /complaints/${ID}`]: json(200, complaint({ evidence: [audio(AUDIO, TRANSCRIBED)] })),
      [`GET /complaints/${ID}/analysis`]: NO_ANALYSIS,
      [`GET /complaints/${ID}/evidence/${AUDIO}/transcript`]: json(404, { error: "transcript_not_available", message: "No transcript is available for this file: the audio file could not be decoded." }),
    });
    const user = userEvent.setup();
    renderDetail();
    await user.click(await screen.findByTestId("transcript-toggle"));
    expect(await screen.findByTestId("transcript-error")).toHaveTextContent("No transcript is available for this file");
    expect(screen.queryByTestId("transcript-segment")).not.toBeInTheDocument();
  });

  it("the analysis panel lists audio indicators with their time offsets and each file's status", async () => {
    const analysis = {
      id: "a1", complaint_id: ID, version: 1, created_at: "2026-10-16T09:01:00+00:00",
      pipeline_version: "complaints-analysis-v2+clf-v1+category-v1", status: "PARTIAL",
      capabilities: { text_risk_scorer: "available", category_classifier: "available", audio_transcription: "unavailable", audio_transcript_scoring: "available", llm_explanation: "unavailable" },
      risk_score: 100, risk_level: "CRITICAL", score_semantics: "uncalibrated_heuristic", classification: "OTHER", confidence: 0.4,
      indicators: [
        {
          code: "DIGITAL_ARREST_PHRASE", weight: 0.69, text: "caller says the victim is under 'digital arrest'", stage: "audio_transcript", source: "rule", method: "call_guard.rules",
          evidence: { type: "evidence_transcript", field: "evidence_transcript", evidence_id: AUDIO, start_s: 6.16, end_s: 7.76, snippet: "You are under digital arrest.", granularity: "segment" },
        },
        {
          code: "SCRIPT_CLASSIFIER_MATCH", weight: 0.9, text: "text matches known digital-arrest call scripts", stage: "audio_transcript", source: "ml", method: "call_guard.clf-v1",
          evidence: { type: "evidence_transcript", field: "evidence_transcript", evidence_id: AUDIO, start_s: null, end_s: null, note: "the classifier scores the whole transcript; it does not point to a segment" },
        },
      ],
      explanation: "x",
      explanation_parts: [{ text: "The audio recording matched: caller says the victim is under 'digital arrest' (at 0:06-0:07).", source: "rule", method: "call_guard.rules" }],
      recommended_actions: [], limitations: [], explanation_source: "rules", llm_explanation: null, llm_model: null,
      confidence_semantics: null, confidence_interpretation: null,
      capability_reasons: { audio_transcription: "not_attempted_limit", llm_explanation: "not_configured" },
      unavailable_capabilities: ["audio_transcription"], unanalysed_attachments: [], category_model: null,
      audio_transcription: [
        { evidence_id: AUDIO, mime_type: "audio/wav", status: "transcribed", reason: "transcribed", message: "transcribed locally", engine: "faster_whisper", model: "base", language: "en", duration_s: 13.4, transcribed_s: 13.4, truncated: false, segment_count: 3, reused: true },
        { evidence_id: AUDIO2, mime_type: "audio/mpeg", status: "not_attempted_limit", reason: "not_attempted_limit", message: "not transcribed in this run: the per-run file limit was reached; run the analysis again to continue", engine: null, model: null, language: null, duration_s: null, transcribed_s: null, truncated: false, segment_count: 0, reused: false },
      ],
      provenance: {
        derived_from_legacy: false, legacy: false,
        stages: [
          { stage: "audio_transcription", source: "ml", method: "faster_whisper:base", status: "ran", reason: null, version: "base", evidence_id: AUDIO, reused: true },
          { stage: "audio_transcription", source: "ml", method: "speech_to_text", status: "skipped", reason: "not_attempted_limit", version: null, evidence_id: AUDIO2, reused: false },
        ],
        model_versions: {},
      },
    };
    mockFetch({
      [`GET /complaints/${ID}`]: json(200, complaint({ latest_analysis: { version: 1, created_at: null, status: "PARTIAL", risk_score: 100, risk_level: "CRITICAL", score_semantics: "x" }, evidence: [audio(AUDIO, TRANSCRIBED)] })),
      [`GET /complaints/${ID}/analysis`]: json(200, analysis),
      [`GET /complaints/${ID}/analyses*`]: json(200, { items: [], total: 0, limit: 10, offset: 0 }),
    });
    renderDetail();
    const panel = await screen.findByTestId("analysis");
    const rule = within(panel).getByTestId("indicator-group-rule");
    expect(rule).toHaveTextContent("in the audio recording a0d10000 at 0:06–0:07: You are under digital arrest.");
    expect(within(rule).getByTestId("audio-offset")).toHaveTextContent("0:06–0:07");
    expect(within(panel).getByTestId("indicator-group-ml")).toHaveTextContent("in the audio transcript (the classifier scores the whole transcript");
    const files = within(panel).getAllByTestId("audio-file");
    expect(files[0]).toHaveTextContent("Transcribed · 3 segments · faster_whisper:base · stored transcript reused");
    expect(files[1]).toHaveTextContent("Not transcribed in the last run: per-run file limit · not transcribed in this run: the per-run file limit was reached");
    expect(within(panel).getByTestId("provenance")).toHaveTextContent("file a0d10000, stored transcript reused");
  });
});

describe("speech-to-text on the Models page", () => {
  it("reports the engine state truthfully", async () => {
    mockFetch({
      "GET /models": json(200, {
        synthetic_data_warning: "synthetic", score_semantics: "uncalibrated_heuristic", models: [],
        llm: { provider: "ollama", status: "not_configured", model: null, reason: null, used_for: "explanation wording only" },
        stt: {
          status: "model_not_available_offline", engine: "faster_whisper", model: "base", reason: "model_not_in_local_cache", local: true,
          evidence_transcription: "enabled", used_for: "transcribing uploaded audio evidence when a complaint analysis runs",
          limits: { max_seconds_per_file: 180, max_files_per_run: 2, run_budget_s: 120 }, decoders: { wav: "stdlib", mp3_ogg_m4a: "unavailable" },
        },
      }),
    });
    renderApp(
      <Routes>
        <Route path="/models" element={<Models />} />
      </Routes>,
      "/models",
    );
    const p = await screen.findByTestId("model-stt");
    expect(within(p).getByTestId("runtime-status")).toHaveTextContent("Model not available offline (model not in local cache)");
    expect(within(p).getByTestId("stt-state")).toHaveTextContent("never downloaded at runtime");
    expect(within(p).getByTestId("stt-state")).toHaveTextContent("2 new file(s), 180 s per file, 120 s in total");
    expect(within(p).getByTestId("stt-state")).toHaveTextContent("PyAV is not installed");
  });
});

describe("check page wording", () => {
  it("says where audio is analysed and why screenshots are not", async () => {
    mockFetch({});
    renderApp(
      <Routes>
        <Route path="/check" element={<CheckMessage />} />
      </Routes>,
      "/check",
    );
    const note = await screen.findByTestId("chk-attachments-note");
    expect(note).toHaveTextContent("Call recordings are analysed when you attach them to a complaint");
    expect(note).toHaveTextContent("no OCR (text-recognition) engine is installed");
    expect(screen.queryByText(/cannot be checked here yet/)).not.toBeInTheDocument();
  });
});

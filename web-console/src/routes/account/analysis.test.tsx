// MOCKED fetch (no server): the message check page, the analysis panel grouped by source with
// provenance and version history, the Models page, and the CRITICAL risk filter.
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { complaint, json, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";
import { buildCheck, EMPTY_CHECK, validateCheck } from "@/lib/complaints/check";
import CheckMessage from "./CheckMessage";
import NewComplaint from "./NewComplaint";
import Models from "./Models";
import ComplaintPage from "./ComplaintPage";
import InvestigatorQueue from "./InvestigatorQueue";

const ID = "c0ffee00-0000-4000-8000-000000000001";

function as(role: "CITIZEN" | "INVESTIGATOR" | "ADMIN") {
  useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION(role) as never });
}

afterEach(() => vi.unstubAllGlobals());

/** A mocked v2 result with provenance (shape of services/complaints POST /analyze). */
function result(over: Record<string, unknown> = {}) {
  return {
    pipeline_version: "complaints-analysis-v2+clf-v1+category-v1",
    status: "COMPLETED",
    capabilities: { text_risk_scorer: "available", category_classifier: "available", llm_explanation: "unavailable", audio_transcription: "skipped_no_input" },
    risk_score: 93,
    risk_level: "CRITICAL",
    score_semantics: "uncalibrated_heuristic",
    classification: "DIGITAL_ARREST",
    confidence: 0.71,
    confidence_interpretation: "0.71 is the top-class score (0-1) of category-v1, a model trained on 100% synthetic data. It is uncalibrated.",
    indicators: [
      {
        code: "SAFE_ACCOUNT_TRANSFER",
        weight: 0.69,
        text: "asks to move money to a safe account",
        source: "rule",
        method: "call_guard.rules",
        stage: "call_guard",
        evidence: { type: "text_span", field: "text", start: 11, end: 70, snippet: "Transfer your money to the RBI safe account", granularity: "sentence" },
      },
      { code: "SCRIPT_CLASSIFIER_MATCH", weight: 0.9, text: "text matches known digital-arrest call scripts", source: "ml", method: "call_guard.clf-v1", stage: "call_guard", evidence: { type: "whole_text", field: "text" } },
      { code: "URL_SHORTENER", weight: 0.3, text: "the link is shortened", source: "rule", method: "url_heuristics", stage: "url_heuristics", evidence: { type: "field", field: "url", host: "bit.ly" } },
    ],
    explanation: "Risk score 93/100 (CRITICAL). The text matched: asks to move money to a safe account.",
    explanation_parts: [
      { text: "Risk score 93/100 (CRITICAL).", source: "rule", method: "risk_formula" },
      { text: "The script classifier found the text similar to known digital-arrest call scripts.", source: "ml", method: "call_guard.clf-v1" },
    ],
    recommended_actions: [{ text: "Call 1930.", source: "static_guidance", method: "static_guidance_table" }],
    limitations: ["Synthetic data only."],
    explanation_source: "rules",
    llm_explanation: null,
    llm_model: null,
    capability_reasons: { llm_explanation: "not_configured", audio_transcription: "no_audio_evidence" },
    unavailable_capabilities: [],
    unanalysed_attachments: [],
    category_model: {
      label: "DIGITAL_ARREST",
      abstained: false,
      top_categories: [
        { category: "DIGITAL_ARREST", score: 0.71 },
        { category: "OTP_SCAM", score: 0.12 },
        { category: "BANK_FRAUD", score: 0.05 },
      ],
      abstention_threshold: 0.25,
    },
    provenance: {
      derived_from_legacy: false,
      legacy: false,
      stages: [
        { stage: "text_rules", source: "rule", method: "call_guard.rules", status: "ran", reason: null, version: "rules-v1" },
        { stage: "llm_explanation", source: "llm", method: "ollama:not_configured", status: "not_configured", reason: "not_configured", version: null },
      ],
      model_versions: { call_guard_rules: "rules-v1", call_guard_classifier: "clf-v1", category: "category-v1", llm: null },
    },
    created_at: "2026-10-15T09:00:00+00:00",
    persisted: false,
    input_capabilities: {
      text: { status: "analysed" },
      url: { status: "analysed", note: "offline lexical checks" },
      upi_id: { status: "not_provided" },
      audio: { status: "not_accepted_here", reason: "Audio recordings are analysed only when they are attached to a complaint: they are transcribed locally when the complaint analysis runs. This check takes text and the optional fields only.", where: "complaint_evidence" },
      screenshot: { status: "unavailable", reason: "Screenshots are not analysed: no OCR engine is installed on this server. This check takes text and the optional fields only.", cause: "no_ocr_engine_installed" },
    },
    ...over,
  };
}

function renderCheck() {
  return renderApp(
    <Routes>
      <Route path="/check" element={<CheckMessage />} />
      <Route path="/me/complaints/new" element={<NewComplaint />} />
    </Routes>,
    "/check",
  );
}

describe("check form helpers", () => {
  it("validates and sends only filled optional fields", () => {
    expect(validateCheck({ ...EMPTY_CHECK, text: "short" }).text).toMatch(/at least 10/);
    expect(validateCheck({ ...EMPTY_CHECK, text: "a long enough text", amount: "12.345" }).amount).toBeTruthy();
    expect(buildCheck({ ...EMPTY_CHECK, text: "  hello world text  ", url: " https://bit.ly/x ", payment_method: "UPI" })).toEqual({ text: "hello world text", url: "https://bit.ly/x", payment_method: "UPI" });
  });
});

describe("check a message or link", () => {
  it("submits, shows the result grouped by source, the score semantics and that nothing was stored", async () => {
    as("CITIZEN");
    const { calls } = mockFetch({ "POST /analyze": json(200, result()) });
    const user = userEvent.setup();
    renderCheck();
    await user.type(screen.getByTestId("chk-text"), "Sir, Transfer your money to the RBI safe account now");
    await user.type(screen.getByTestId("chk-url"), "https://bit.ly/x");
    await user.click(screen.getByTestId("chk-submit"));
    const res = await screen.findByTestId("check-result");
    const post = calls.find((c) => c.method === "POST" && c.path === "/analyze")!;
    expect(post.body).toEqual({ text: "Sir, Transfer your money to the RBI safe account now", url: "https://bit.ly/x" });
    expect(within(res).getByTestId("nothing-stored")).toHaveTextContent("Nothing was stored");
    expect(within(res).getByTestId("score-semantics")).toHaveTextContent("uncalibrated heuristic score, not a probability");
    expect(within(res).getAllByTestId("risk-badge")[0]).toHaveTextContent("Very strong scam indicators · 93/100");
    const rules = within(res).getByTestId("indicator-group-rule");
    expect(rules).toHaveTextContent("call_guard.rules");
    expect(rules).toHaveTextContent("Transfer your money to the RBI safe account");
    expect(rules).toHaveTextContent("url_heuristics");
    expect(rules).toHaveTextContent("host bit.ly");
    const ml = within(res).getByTestId("indicator-group-ml");
    expect(ml).toHaveTextContent("call_guard.clf-v1");
    expect(ml).toHaveTextContent("scores the whole text");
    expect(within(res).getByTestId("indicator-group-llm")).toHaveTextContent("no local language model is configured");
    expect(within(res).getByTestId("confidence-interpretation")).toHaveTextContent("uncalibrated");
    expect(within(res).getByTestId("top-categories")).toHaveTextContent("abstains below 0.25");
    expect(within(within(res).getByTestId("top-categories")).getAllByRole("listitem")).toHaveLength(4);
    expect(within(res).getByTestId("input-capabilities")).toHaveTextContent("Audio: not accepted here · Audio recordings are analysed only when they are attached to a complaint");
    expect(within(res).getByTestId("input-capabilities")).toHaveTextContent("Screenshot: unavailable · Screenshots are not analysed: no OCR engine is installed");
    expect(within(res).getByTestId("model-versions")).toHaveTextContent("category-v1");
    const badges = within(within(res).getByTestId("explanation-parts")).getAllByTestId("source-badge").map((b) => b.getAttribute("data-source"));
    expect(badges).toEqual(["rule", "ml"]);
  });

  it("no indicators found: neutral wording, never 'safe' or 'low risk', category marked as not evidence (mocked API)", async () => {
    as("CITIZEN");
    mockFetch({
      "POST /analyze": json(200, result({
        risk_score: 0, risk_level: "LOW", assessment: "no_indicators_found", classification: "JOB_SCAM", confidence: 0.42,
        indicators: [], explanation_parts: [],
        limitations: ["No scam indicators were found by the rules and models that ran. This is not a finding that the message or event is safe: these checks cover a limited set of scam patterns."],
      })),
    });
    const user = userEvent.setup();
    renderCheck();
    await user.type(screen.getByTestId("chk-text"), "We are pleased to offer you the position of analyst.");
    await user.click(screen.getByTestId("chk-submit"));
    const res = await screen.findByTestId("check-result");
    const badge = within(res).getAllByTestId("risk-badge")[0]!;
    expect(badge).toHaveTextContent("No scam indicators found");
    expect(badge.textContent!.toLowerCase()).not.toMatch(/safe|low risk/);
    expect(within(res).getByTestId("category-not-evidence")).toHaveTextContent("not used in the score");
    expect(res).toHaveTextContent("This is not a finding that the message or event is safe");
  });

  it("shows the evidence of a link written in the message as extracted, not verified (mocked API)", async () => {
    as("CITIZEN");
    mockFetch({
      "POST /analyze": json(200, result({
        indicators: [{
          code: "SUSPICIOUS_TLD", source: "rule", method: "url_heuristics", stage: "url_heuristics", weight: 0.25,
          text: "the top-level domain '.top' is on a static list", evidence: { type: "text_link", field: "description", start: 50, end: 81, snippet: "http://sbi-kyc-update.top/login", host: "sbi-kyc-update.top" },
        }],
      })),
    });
    const user = userEvent.setup();
    renderCheck();
    await user.type(screen.getByTestId("chk-text"), "Your account will be blocked. Update KYC at http://sbi-kyc-update.top/login");
    await user.click(screen.getByTestId("chk-submit"));
    const res = await screen.findByTestId("check-result");
    const ev = within(res).getAllByTestId("evidence").find((e) => e.textContent?.includes("link written"));
    expect(ev).toBeDefined();
    expect(ev!).toHaveTextContent("http://sbi-kyc-update.top/login");
    expect(ev!).toHaveTextContent("not opened or verified");
  });

  it("Register a complaint pre-fills the form without submitting it", async () => {
    as("CITIZEN");
    const { calls } = mockFetch({ "POST /analyze": json(200, result()) });
    const user = userEvent.setup();
    renderCheck();
    await user.type(screen.getByTestId("chk-text"), "Sir, Transfer your money to the RBI safe account now");
    await user.type(screen.getByTestId("chk-upi_id"), "refund@ybl");
    await user.click(screen.getByTestId("chk-submit"));
    await user.click(await screen.findByTestId("chk-register"));
    expect(await screen.findByTestId("prefill-note")).toBeInTheDocument();
    expect(screen.getByTestId("f-description")).toHaveValue("Sir, Transfer your money to the RBI safe account now");
    expect(screen.getByTestId("f-category")).toHaveValue("");
    expect(calls.some((c) => c.path === "/complaints")).toBe(false);
  });

  it("investigators get the check but no register action; errors are shown", async () => {
    as("INVESTIGATOR");
    let n = 0;
    mockFetch({
      "POST /analyze": () => (++n === 1 ? json(429, { error: "rate_limited", message: "Too many requests. Try again shortly." }, { "Retry-After": "20" }) : json(200, result({ risk_level: null, risk_score: null, status: "INSUFFICIENT_INPUT" }))),
    });
    const user = userEvent.setup();
    renderCheck();
    await user.type(screen.getByTestId("chk-text"), "hello there friend");
    await user.click(screen.getByTestId("chk-submit"));
    expect(await screen.findByTestId("check-error")).toHaveTextContent("Too many requests");
    await user.click(screen.getByTestId("chk-submit"));
    const res = await screen.findByTestId("check-result");
    expect(within(res).getByTestId("analysis-status")).toHaveTextContent("Insufficient input");
    expect(within(res).getByText("Risk not scored")).toBeInTheDocument();
    expect(screen.queryByTestId("chk-register")).not.toBeInTheDocument();
  });

  it("client validation blocks a too-short text; an unreachable service is reported", async () => {
    as("CITIZEN");
    const { calls } = mockFetch({ "POST /analyze": () => Promise.reject(new TypeError("Failed to fetch")) });
    const user = userEvent.setup();
    renderCheck();
    await user.type(screen.getByTestId("chk-text"), "short");
    await user.click(screen.getByTestId("chk-submit"));
    expect(screen.getByTestId("chk-text")).toHaveAttribute("aria-invalid", "true");
    expect(calls).toHaveLength(0);
    await user.type(screen.getByTestId("chk-text"), " but now long enough");
    await user.click(screen.getByTestId("chk-submit"));
    expect(await screen.findByTestId("error-block")).toHaveTextContent("Service unavailable");
  });
});

describe("complaint analysis panel", () => {
  it("shows CRITICAL, source groups, provenance and the version history", async () => {
    as("CITIZEN");
    const stored = { ...result(), id: "a2", complaint_id: ID, version: 2, persisted: undefined };
    mockFetch({
      [`GET /complaints/${ID}`]: json(200, complaint({ latest_analysis: { version: 2, created_at: null, status: "COMPLETED", risk_score: 93, risk_level: "CRITICAL", score_semantics: "uncalibrated_heuristic" } })),
      [`GET /complaints/${ID}/analysis`]: json(200, stored),
      [`GET /complaints/${ID}/analyses*`]: json(200, {
        items: [
          { id: "a2", version: 2, created_at: "2026-10-15T09:00:00+00:00", status: "COMPLETED", risk_score: 93, risk_level: "CRITICAL", score_semantics: "x", pipeline_version: "p2", classification: null, confidence: null },
          { id: "a1", version: 1, created_at: "2026-10-14T09:00:00+00:00", status: "COMPLETED", risk_score: 75, risk_level: "HIGH", score_semantics: "x", pipeline_version: "p1", classification: null, confidence: null, provenance_derived_from_legacy: true },
        ],
        total: 2,
        limit: 10,
        offset: 0,
      }),
    });
    renderApp(
      <Routes>
        <Route path="/me/complaints/:id" element={<ComplaintPage />} />
      </Routes>,
      `/me/complaints/${ID}`,
    );
    const panel = await screen.findByTestId("analysis");
    expect(within(panel).getAllByTestId("risk-badge")[0]).toHaveTextContent("Very strong scam indicators");
    expect(within(panel).getByTestId("indicator-group-rule")).toBeInTheDocument();
    expect(within(panel).getByTestId("provenance")).toHaveTextContent("call_guard.rules");
    const versions = await screen.findAllByTestId("analysis-version");
    expect(versions).toHaveLength(2);
    expect(versions[0]).toHaveTextContent("v2");
    expect(versions[0]).toHaveTextContent("Very strong scam indicators · 93/100");
    expect(versions[1]).toHaveTextContent("(older format)");
  });
});

describe("models page", () => {
  const MODELS = {
    synthetic_data_warning: "Both models were trained and evaluated on 100% synthetic data.",
    score_semantics: "uncalibrated_heuristic",
    models: [
      {
        id: "category-v1",
        kind: "complaint_category_classifier",
        source: "ml",
        version: "category-v1",
        algorithm: "tfidf+logreg",
        artifact: { sha256: "5103aa08".padEnd(64, "0"), sha256_recorded_at_training: "x", verified_at_load: "pin" },
        training_data: { synthetic: true, description: "100% synthetic: template-generated complaints", rows: 3680 },
        metrics: {
          basis: "held-out test split grouped by scenario family (synthetic)",
          n: 727,
          macro_f1: 0.7153,
          accuracy: 0.7139,
          abstention_rate: 0.391,
          abstention_threshold: 0.25,
          per_class: { PHISHING: { precision: 0.8, recall: 0.7, f1: 0.75, support: 80 } },
        },
        leaky_random_split: { is_headline: false, macro_f1: 0.985, note: "Random row split: NOT the headline." },
        limitations: ["Not validated on any real complaint."],
        runtime: { status: "loaded", reason: null },
      },
      {
        id: "call_guard.clf-v1",
        kind: "scam_script_classifier",
        source: "ml",
        version: "clf-v1",
        artifact: { sha256: "f2877267".padEnd(64, "0") },
        training_data: { synthetic: true, description: "100% synthetic: simulator transcripts" },
        metrics: null,
        metrics_unavailable_reason: "no held-out metrics file ships with clf-v1",
        leaky_random_split: null,
        limitations: ["Built for call scripts."],
        runtime: { status: "unavailable", reason: "ImportError" },
      },
    ],
    llm: { provider: "ollama", status: "not_configured", model: null, reason: null, used_for: "explanation wording only" },
  };

  it("renders the synthetic warning first, headline metrics, the leaky split as not the headline and honest gaps", async () => {
    as("CITIZEN");
    mockFetch({ "GET /models": json(200, MODELS) });
    renderApp(<Models />, "/models");
    const warn = await screen.findByTestId("synthetic-warning");
    expect(warn).toHaveTextContent("synthetic data only");
    const cat = screen.getByTestId("model-category-v1");
    expect(within(cat).getByTestId("headline-metrics")).toHaveTextContent("0.715");
    expect(within(cat).getByTestId("headline-metrics")).toHaveTextContent("39.1%");
    expect(within(cat).getByTestId("leaky-split")).toHaveTextContent("Not the headline");
    expect(within(cat).getByTestId("runtime-status")).toHaveTextContent("Loaded");
    const cg = screen.getByTestId("model-call_guard.clf-v1");
    expect(within(cg).getByTestId("no-metrics")).toHaveTextContent("no held-out metrics file");
    expect(within(cg).getByTestId("runtime-status")).toHaveTextContent("Unavailable (importerror)");
    expect(screen.getByTestId("model-llm")).toHaveTextContent("Not configured");
    expect(warn.compareDocumentPosition(cat) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows an unavailable state with retry", async () => {
    as("INVESTIGATOR");
    mockFetch({ "GET /models": json(503, { error: "service_unavailable" }) });
    renderApp(<Models />, "/models");
    expect(await screen.findByTestId("error-block")).toHaveTextContent("Service unavailable");
  });
});

describe("investigator queue", () => {
  it("offers CRITICAL in the risk filter and sends it", async () => {
    as("INVESTIGATOR");
    const { calls } = mockFetch({ "GET /complaints*": json(200, { items: [], total: 0, limit: 20, offset: 0 }) });
    const user = userEvent.setup();
    renderApp(<InvestigatorQueue />, "/investigator");
    const select = await screen.findByRole("combobox", { name: /risk/i });
    expect(within(select).getByRole("option", { name: "Critical" })).toBeInTheDocument();
    await user.selectOptions(select, "CRITICAL");
    const apply = screen.queryByRole("button", { name: /apply|search/i });
    if (apply) await user.click(apply);
    await waitFor(() => expect(calls.some((c) => c.path.includes("risk_level=CRITICAL"))).toBe(true));
  });
});

describe("LLM text (mocked data)", () => {
  it("is shown apart from the rule explanation with its label; a rejection is explained", async () => {
    const { AnalysisResultView } = await import("@/components/complaints/AnalysisResultView");
    const withLlm = result({
      llm_explanation: "The caller demands moving funds to a safe account.",
      llm_model: "qwen2.5:0.5b",
      capabilities: { llm_explanation: "available" },
      explanation_parts: [
        { text: "Risk score 93/100 (CRITICAL).", source: "rule", method: "risk_formula" },
        { text: "The caller demands moving funds to a safe account.", source: "llm", method: "ollama:qwen2.5:0.5b", label: "LLM-generated (qwen2.5:0.5b), may be imprecise" },
      ],
    });
    const { unmount } = renderApp(<AnalysisResultView a={withLlm as never} />);
    expect(screen.getByTestId("llm-text")).toHaveTextContent("LLM-generated (qwen2.5:0.5b), may be imprecise");
    expect(screen.getByTestId("explanation-parts")).not.toHaveTextContent("safe account");
    unmount();
    const rejected = result({ capabilities: { llm_explanation: "rejected" }, capability_reasons: { llm_explanation: "role_inversion" } });
    renderApp(<AnalysisResultView a={rejected as never} />);
    expect(screen.getByTestId("indicator-group-llm")).toHaveTextContent("rejected by the grounding checks (role inversion)");
  });
});

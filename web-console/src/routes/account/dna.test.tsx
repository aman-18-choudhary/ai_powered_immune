// MOCKED fetch (no server): Phase 8 Fraud DNA panel and the health/models entries. SYNTHETIC records.
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { complaint, json, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";
import ComplaintPage from "./ComplaintPage";
import Admin from "./Admin";
import Models from "./Models";

const A = "c0ffee00-0000-4000-8000-000000000001";
const B = "c0ffee00-0000-4000-8000-00000000000b";
const NO_ANALYSIS = json(404, { error: "analysis_not_found", message: "No analysis has been run for this complaint." });
const DISCLAIMER = "Similar descriptions suggest a similar scam method. They are not proof that the same person, group or crime is involved.";

function as(role: "CITIZEN" | "INVESTIGATOR" | "ADMIN") {
  useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION(role) as never });
}

afterEach(() => vi.unstubAllGlobals());

const base = { complaint_id: A, matching: "enabled", matching_message: null, disclaimer: DISCLAIMER, total: 0, limit: 10, items: [], thresholds: { similar: 0.58, strong: 0.62 } };
const OK = {
  ...base,
  status: "ok",
  total: 1,
  model: { id: "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", revision: "e8f8c211" },
  items: [
    {
      complaint: { id: B, title: "Synthetic digital arrest B", category: "DIGITAL_ARREST", status: "SUBMITTED", risk_level: null, created_at: null },
      similarity: 0.71,
      band: "strong",
      shared_indicators: ["AUTHORITY_IMPERSONATION"],
      same_category: true,
      identifier_link: "observed",
      explanation: "The descriptions are worded alike (similarity 0.71, strong). Both latest analyses found: AUTHORITY_IMPERSONATION.",
    },
  ],
};

function renderStaff(similar: unknown) {
  as("INVESTIGATOR");
  const m = mockFetch({
    [`GET /complaints/${A}`]: json(200, complaint()),
    [`GET /complaints/${A}/analysis`]: NO_ANALYSIS,
    [`GET /complaints/${A}/related`]: json(200, { complaint_id: A, linking: "enabled", linking_message: null, disclaimer: "x", items: [], total: 0, limit: 10, offset: 0, truncated: false }),
    [`GET /complaints/${A}/similar`]: typeof similar === "function" ? (similar as () => Response) : json(200, similar),
  });
  renderApp(
    <Routes>
      <Route path="/investigator/complaints/:id" element={<ComplaintPage />} />
    </Routes>,
    `/investigator/complaints/${A}`,
  );
  return m;
}

describe("similar complaints panel", () => {
  it("shows the band, score, explanation and the separate identifier link; similarity is not proof", async () => {
    renderStaff(OK);
    const item = await screen.findByTestId("similar-item");
    expect(item).toHaveAttribute("data-band", "strong");
    expect(within(item).getByTestId("similar-score")).toHaveTextContent("Strong similarity 0.71");
    expect(within(item).getByTestId("similar-explanation")).toHaveTextContent("AUTHORITY_IMPERSONATION");
    expect(within(item).getByTestId("similar-identifier-link")).toHaveTextContent("Also shares an identifier (observed)");
    const disc = screen.getByTestId("similar-disclaimer");
    expect(disc).toHaveTextContent("not proof that the same person, group or crime is involved");
    expect(disc).toHaveTextContent("Similar wording is not a shared identifier");
    expect(disc).toHaveTextContent("not probabilities");
  });

  it("opted-out, unavailable and empty states are honest", async () => {
    renderStaff({ ...base, status: "ok", matching: "disabled", matching_message: "Case matching is off for this complaint at the complainant's request." });
    expect(await screen.findByTestId("similar-disabled")).toHaveTextContent("complainant's request");
  });

  it("an unavailable engine says why and lists nothing", async () => {
    renderStaff({ ...base, status: "unavailable", reason: "engine_not_installed", message: "The embedding library is not installed (optional 'dna' extra)." });
    const st = await screen.findByTestId("similar-status");
    expect(st).toHaveAttribute("data-status", "unavailable");
    expect(st).toHaveTextContent("not installed");
    expect(screen.queryByTestId("similar-item")).not.toBeInTheDocument();
  });

  it("no matches above the threshold is an empty state, not a guess", async () => {
    renderStaff({ ...base, status: "ok" });
    expect(await screen.findByText("No similar complaints")).toBeInTheDocument();
    expect(screen.getByText(/similarity 0.58 or more/)).toBeInTheDocument();
  });

  it("while the model loads it offers to check again", async () => {
    let n = 0;
    const { calls } = renderStaff(() => json(200, n++ === 0 ? { ...base, status: "loading", reason: "loading", message: "The embedding model is loading. Try again in a moment." } : OK));
    expect(await screen.findByTestId("similar-status")).toHaveTextContent("loading");
    await userEvent.click(screen.getByRole("button", { name: /check again/i }));
    expect(await screen.findByTestId("similar-item")).toBeInTheDocument();
    expect(calls.filter((c) => c.path.startsWith(`/complaints/${A}/similar`)).length).toBeGreaterThanOrEqual(2);
  });

  it("citizens never request similar complaints", async () => {
    as("CITIZEN");
    const { calls } = mockFetch({ [`GET /complaints/${A}`]: json(200, complaint()), [`GET /complaints/${A}/analysis`]: NO_ANALYSIS });
    renderApp(
      <Routes>
        <Route path="/me/complaints/:id" element={<ComplaintPage />} />
      </Routes>,
      `/me/complaints/${A}`,
    );
    await screen.findByTestId("complaint-id");
    expect(screen.queryByTestId("similar-panel")).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.includes("/similar"))).toBe(false);
  });
});

describe("health and models", () => {
  it("SQL graph, embedded Qdrant and a ready model read as working", async () => {
    as("ADMIN");
    mockFetch({
      "GET /admin/users*": json(200, { items: [], total: 0, limit: 20, offset: 0 }),
      "GET /admin/health": json(200, { graph: { status: "sql" }, qdrant: { status: "local", mode: "embedded" }, dna: { status: "ready" }, neo4j: { status: "not_configured" } }),
    });
    const user = userEvent.setup();
    renderApp(<Admin />);
    await user.click(screen.getByRole("tab", { name: "Health" }));
    const rows = await screen.findAllByTestId("health-row");
    const text = (name: string) => rows.find((r) => r.textContent?.startsWith(name))?.textContent ?? "";
    expect(text("Graph")).toContain("Working (sql)");
    expect(text("Qdrant")).toContain("Working (local)");
    expect(text("Dna")).toContain("Working (ready)");
    expect(text("Neo4j")).toContain("Not configured");
  });

  it("the models page lists the Fraud DNA model and its synthetic thresholds", async () => {
    as("INVESTIGATOR");
    mockFetch({
      "GET /models": json(200, {
        synthetic_data_warning: "w",
        score_semantics: "s",
        models: [],
        llm: { provider: "ollama", status: "not_configured", model: null, reason: null, used_for: "x" },
        dna: { status: "ready", reason: null, message: null, model: "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", revision: "e8f8c211226b894f", dimensions: 384, thresholds: { similar: 0.58, strong: 0.62 }, index: { backend: "qdrant", mode: "local", collection: "c" }, used_for: "finding complaints described in similar words" },
      }),
    });
    renderApp(<Models />);
    const card = await screen.findByTestId("model-dna");
    expect(card).toHaveTextContent("paraphrase-multilingual-MiniLM-L12-v2");
    expect(card).toHaveTextContent("384 dimensions");
    expect(within(card).getByTestId("dna-state")).toHaveTextContent("chosen on synthetic data only");
    await waitFor(() => expect(within(card).getByTestId("runtime-status")).toHaveTextContent("Ready"));
  });

  it("a binary classifier shows precision, recall, ROC-AUC and the not-the-headline split", async () => {
    as("INVESTIGATOR");
    mockFetch({
      "GET /models": json(200, {
        synthetic_data_warning: "w",
        score_semantics: "s",
        llm: { provider: "ollama", status: "not_configured", model: null, reason: null, used_for: "x" },
        models: [
          {
            id: "call_guard.clf-v1", kind: "scam_script_classifier", source: "ml", version: "clf-v1",
            artifact: { file: "clf-v1.joblib", bytes: 1, sha256: "a".repeat(64) },
            training_data: { synthetic: true, description: "synthetic" },
            metrics: { basis: "Independent hand-written SYNTHETIC set", n: 64, macro_f1: null, accuracy: 0.52, abstention_rate: null, abstention_threshold: null, binary: { threshold: 0.5, precision: 0.8571, recall: 0.1667, f1: 0.2791, roc_auc: 0.5923, false_positive_rate: 0.0357 }, held_out_benign: "0 of 29 held-out hand-written benign lines and hard negatives flagged" },
            leaky_random_split: { is_headline: false, metric_label: "F1", macro_f1: 0.999, accuracy: 0.999, note: "Held-out seeds from the SAME simulator and authored templates." },
            limitations: [], runtime: { status: "loaded", reason: null },
          },
        ],
      }),
    });
    renderApp(<Models />);
    const head = await screen.findByTestId("headline-metrics");
    expect(head).toHaveTextContent("Recall0.167");
    expect(head).toHaveTextContent("ROC-AUC0.592");
    expect(screen.getByTestId("binary-note")).toHaveTextContent("0 of 29 held-out");
    expect(screen.getByText(/a split from the training generators gives F1/)).toBeInTheDocument();
  });
});

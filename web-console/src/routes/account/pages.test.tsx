// MOCKED fetch: complaint detail, alerts, admin errors and empty states (no server).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { complaint, json, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";
import ComplaintPage from "./ComplaintPage";
import CitizenDashboard from "./CitizenDashboard";
import Admin from "./Admin";
import { AlertsPanel } from "@/components/complaints/AlertsPanel";

const ID = "c0ffee00-0000-4000-8000-000000000001";
const STATS = (total: number) => ({
  total,
  by_status: { SUBMITTED: total, UNDER_REVIEW: 0, INVESTIGATING: 0, RESOLVED: 0, REJECTED: 0 },
  by_category: {},
  last_complaint_at: null,
  unread_alerts: 0,
});

function as(role: "CITIZEN" | "INVESTIGATOR" | "ADMIN") {
  useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION(role) as never });
}

afterEach(() => vi.unstubAllGlobals());

function renderDetail(base = "/me/complaints") {
  return renderApp(
    <Routes>
      <Route path={`${base}/:id`} element={<ComplaintPage />} />
    </Routes>,
    `${base}/${ID}`,
  );
}

describe("complaint detail", () => {
  beforeEach(() => as("CITIZEN"));

  it("shows every field; empty optional ones read 'Not provided'", async () => {
    mockFetch({ [`GET /complaints/${ID}`]: json(200, complaint({ upi_id: "scam@okaxis" })), [`GET /complaints/${ID}/analysis`]: json(404, { error: "analysis_not_found", message: "No analysis has been run for this complaint." }) });
    renderDetail();
    expect(await screen.findByTestId("complaint-id")).toHaveTextContent(ID);
    const fields = screen.getByTestId("complaint-fields");
    expect(within(fields).getByText("scam@okaxis")).toBeInTheDocument();
    for (const k of ["Bank account", "IFSC", "Phone", "Suspicious link", "City", "Coordinates", "Incident time", "Amount"]) {
      expect(fields.querySelector(`[data-field="${k}"]`)).toHaveTextContent("Not provided");
    }
    expect(await screen.findByTestId("analysis-none")).toBeInTheDocument();
    expect(screen.getAllByTestId("history-row")).toHaveLength(1);
    // citizens get no status controls
    expect(screen.queryByTestId("status-update")).not.toBeInTheDocument();
  });

  it("evidence removal is offered to the owner only while SUBMITTED, with a confirmation", async () => {
    const ev = { id: "e1", complaint_id: ID, original_filename: "shot.png", mime_type: "image/png", size_bytes: 10, sha256: "ab".repeat(32), status: "stored", created_at: null };
    let deleted = false;
    mockFetch({
      [`GET /complaints/${ID}`]: () => json(200, complaint({ evidence: deleted ? [] : [ev] })),
      [`GET /complaints/${ID}/analysis`]: json(404, { error: "analysis_not_found", message: "x" }),
      [`DELETE /complaints/${ID}/evidence/e1`]: () => {
        deleted = true;
        return json(200, { id: "e1", status: "deleted", file_removed: true });
      },
    });
    const user = userEvent.setup();
    renderDetail();
    await user.click(await screen.findByTestId("evidence-delete"));
    await user.click(await screen.findByTestId("evidence-delete-confirm"));
    await waitFor(() => expect(screen.queryByTestId("evidence-item")).not.toBeInTheDocument());
  });

  it("no removal once the complaint is under review", async () => {
    const ev = { id: "e1", complaint_id: ID, original_filename: "shot.png", mime_type: "image/png", size_bytes: 10, sha256: "ab".repeat(32), status: "stored", created_at: null };
    mockFetch({ [`GET /complaints/${ID}`]: json(200, complaint({ status: "UNDER_REVIEW", evidence: [ev] })), [`GET /complaints/${ID}/analysis`]: json(404, { error: "analysis_not_found", message: "x" }) });
    renderDetail();
    expect(await screen.findByTestId("evidence-item")).toBeInTheDocument();
    expect(screen.queryByTestId("evidence-delete")).not.toBeInTheDocument();
  });

  it("run analysis: shows the score as an uncalibrated heuristic, and a failure honestly", async () => {
    let fail = true;
    mockFetch({
      [`GET /complaints/${ID}`]: json(200, complaint()),
      [`GET /complaints/${ID}/analysis`]: json(404, { error: "analysis_not_found", message: "x" }),
      [`POST /complaints/${ID}/analysis`]: () =>
        fail
          ? json(429, { error: "rate_limited", message: "Too many requests. Try again shortly." }, { "Retry-After": "30" })
          : json(201, {
              id: "a1", complaint_id: ID, version: 1, created_at: "2026-10-14T09:00:00+00:00", pipeline_version: "complaints-analysis-v2+clf-v1+category-v1", status: "PARTIAL",
              capabilities: { text_risk_scorer: "available", screenshot_ocr: "unavailable" }, risk_score: 80, risk_level: "HIGH", score_semantics: "uncalibrated_heuristic",
              classification: "UPI_FRAUD", confidence: 0.6, indicators: [{ code: "X", weight: 0.5, text: "asks to approve a collect request", source: "call_guard" }],
              explanation: "Risk score 80/100 (HIGH).", recommended_actions: [{ text: "Call 1930.", source: "static_guidance" }], limitations: [], explanation_source: "rules",
              llm_explanation: null, confidence_semantics: "model_confidence_uncalibrated", capability_reasons: { screenshot_ocr: "no_ocr_engine_installed" }, unavailable_capabilities: ["screenshot_ocr"],
              unanalysed_attachments: [], category_model: null,
            }),
    });
    const user = userEvent.setup();
    renderDetail();
    await user.click(await screen.findByTestId("run-analysis"));
    expect(await screen.findByTestId("analysis-error")).toHaveTextContent("The analysis could not be run. Too many requests");
    fail = false;
    await user.click(screen.getByTestId("run-analysis"));
    expect(await screen.findByTestId("score-semantics")).toHaveTextContent("uncalibrated heuristic score, not a probability");
    const panel = screen.getByTestId("analysis");
    expect(within(panel).getAllByTestId("risk-badge")[0]).toHaveTextContent("Strong scam indicators · 80/100");
    // an older-format result: indicator source derived on the server; here the client still groups it
    expect(within(screen.getByTestId("explanation-parts")).getAllByTestId("source-badge")[0]).toHaveTextContent("Rule");
    expect(screen.getByTestId("unavailable-capabilities")).toHaveTextContent("Screenshot ocr: unavailable");
  });

  it("staff: status options follow the allowed transitions; a 409 is shown", async () => {
    as("INVESTIGATOR");
    const { calls } = mockFetch({
      [`GET /complaints/${ID}`]: json(200, complaint({ owner_user_id: "u-owner" })),
      [`GET /complaints/${ID}/analysis`]: json(404, { error: "analysis_not_found", message: "x" }),
      [`PATCH /complaints/${ID}/status`]: json(409, { error: "status_conflict", message: "The complaint was changed by someone else. Reload and try again." }),
    });
    const user = userEvent.setup();
    renderDetail("/investigator/complaints");
    const select = await screen.findByTestId("status-select");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose…", "Under review", "Rejected"]);
    await user.selectOptions(select, "UNDER_REVIEW");
    await user.type(screen.getByTestId("status-note"), "Picked up");
    await user.click(screen.getByTestId("status-submit"));
    expect(await screen.findByTestId("status-error")).toHaveTextContent("changed by someone else");
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "UNDER_REVIEW", note: "Picked up" });
    // investigators cannot remove or add evidence
    expect(screen.queryByTestId("add-evidence")).not.toBeInTheDocument();
  });

  it("a foreign or unknown id reads as not available", async () => {
    mockFetch({ [`GET /complaints/${ID}`]: json(404, { error: "not_found", message: "Not found." }), [`GET /complaints/${ID}/analysis`]: json(404, { error: "not_found", message: "Not found." }) });
    renderDetail();
    expect(await screen.findByTestId("error-block")).toHaveTextContent("not available to your account");
  });
});

describe("alerts", () => {
  beforeEach(() => as("CITIZEN"));
  it("mark as read calls the API and lowers the unread count", async () => {
    const alert = { id: "a1", complaint_id: ID, kind: "STATUS_CHANGED", message: "Your complaint is under review.", created_at: "2026-10-14T09:00:00+00:00", read_at: null };
    const { calls } = mockFetch({
      "GET /alerts*": json(200, { items: [alert], total: 1, unread: 1, limit: 8, offset: 0 }),
      "POST /alerts/a1/read": json(200, { ...alert, read_at: "2026-10-14T10:00:00+00:00" }),
    });
    const user = userEvent.setup();
    renderApp(<AlertsPanel />);
    expect(await screen.findByTestId("unread-count")).toHaveTextContent("1 unread");
    await user.click(screen.getByTestId("alert-read"));
    await waitFor(() => expect(screen.getByTestId("unread-count")).toHaveTextContent("0 unread"));
    expect(screen.getByTestId("alert-item")).toHaveAttribute("data-read", "true");
    expect(calls.some((c) => c.method === "POST" && c.path === "/alerts/a1/read")).toBe(true);
  });
});

describe("citizen dashboard", () => {
  beforeEach(() => as("CITIZEN"));
  it("empty history offers 'Register your first complaint'; stats come from /me/stats", async () => {
    mockFetch({
      "GET /me/stats": json(200, STATS(0)),
      "GET /complaints*": json(200, { items: [], total: 0, limit: 10, offset: 0 }),
      "GET /alerts*": json(200, { items: [], total: 0, unread: 0, limit: 8, offset: 0 }),
    });
    renderApp(<CitizenDashboard />);
    expect(await screen.findByTestId("empty-history")).toHaveTextContent("Register your first complaint");
    expect(await screen.findByTestId("stat-total")).toHaveTextContent("0");
    expect(screen.getByText("No alerts yet")).toBeInTheDocument();
    expect(screen.getByTestId("resources")).toHaveTextContent("1930");
    expect(screen.getByRole("link", { name: "cybercrime.gov.in" })).toHaveAttribute("href", "https://cybercrime.gov.in");
  });

  it("lists complaints with status chips and filters by status through the API", async () => {
    const { calls } = mockFetch({
      "GET /me/stats": json(200, STATS(1)),
      "GET /complaints*": json(200, { items: [{ ...complaint(), evidence_count: 1, latest_analysis: null }], total: 1, limit: 10, offset: 0 }),
      "GET /alerts*": json(200, { items: [], total: 0, unread: 0, limit: 8, offset: 0 }),
    });
    const user = userEvent.setup();
    renderApp(<CitizenDashboard />);
    expect(await screen.findByTestId("complaint-row")).toHaveTextContent("Fake refund call");
    expect(screen.getByTestId("status-chip")).toHaveTextContent("Submitted");
    await user.selectOptions(screen.getByLabelText("Filter by status"), "RESOLVED");
    await waitFor(() => expect(calls.some((c) => c.path.startsWith("/complaints?") && c.path.includes("status=RESOLVED"))).toBe(true));
  });

  it("an unreachable service shows an unavailable state, not zeros", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    renderApp(<CitizenDashboard />);
    const errs = await screen.findAllByTestId("error-block");
    expect(errs[0]).toHaveTextContent("Service unavailable");
    expect(screen.queryByTestId("stats")).not.toBeInTheDocument();
  });
});

describe("admin", () => {
  beforeEach(() => as("ADMIN"));
  const user0 = { id: "u-a", email: "admin@example.org", email_verified: true, display_name: null, role: "ADMIN", role_source: "bootstrap", is_active: true, created_at: null, last_login_at: null };
  const user1 = { id: "u-b", email: "new@example.org", email_verified: false, display_name: null, role: "CITIZEN", role_source: null, is_active: true, created_at: null, last_login_at: null };

  it("role change asks for confirmation, sends expected_role and shows the service's refusal", async () => {
    const { calls } = mockFetch({
      "GET /admin/users*": json(200, { items: [user0, user1], total: 2, limit: 20, offset: 0 }),
      "PUT /admin/users/u-b/role": json(409, { error: "target_email_not_verified", message: "The user has no verified email address." }),
      "PUT /admin/users/u-a/role": json(409, { error: "last_admin", message: "At least one active administrator must remain." }),
    });
    const user = userEvent.setup();
    renderApp(<Admin />);
    const rows = await screen.findAllByTestId("admin-user");
    await user.selectOptions(within(rows[1]!).getByTestId("admin-role-select"), "INVESTIGATOR");
    await user.click(within(rows[1]!).getByTestId("admin-role-change"));
    expect(await screen.findByText(/will change from Citizen to Investigator/)).toBeInTheDocument();
    await user.click(screen.getByTestId("admin-confirm"));
    expect(await within(rows[1]!).findByTestId("admin-error")).toHaveTextContent("The user has no verified email address.");
    expect(calls.find((c) => c.path === "/admin/users/u-b/role")?.body).toEqual({ role: "INVESTIGATOR", expected_role: "CITIZEN" });

    await user.selectOptions(within(rows[0]!).getByTestId("admin-role-select"), "CITIZEN");
    await user.click(within(rows[0]!).getByTestId("admin-role-change"));
    await user.click(await screen.findByTestId("admin-confirm"));
    expect(await within(rows[0]!).findByTestId("admin-error")).toHaveTextContent("At least one active administrator must remain.");
    expect(within(rows[0]!).getByTestId("admin-user-role")).toHaveTextContent("Administrator");
  });

  it("health renders configured, not configured and unavailable honestly", async () => {
    mockFetch({
      "GET /admin/users*": json(200, { items: [], total: 0, limit: 20, offset: 0 }),
      "GET /admin/health": json(200, { db: { status: "ok" }, qdrant: { status: "not_configured" }, ollama: { status: "unreachable", model: "qwen2.5:3b" } }),
    });
    const user = userEvent.setup();
    renderApp(<Admin />);
    await user.click(screen.getByRole("tab", { name: "Health" }));
    const rows = await screen.findAllByTestId("health-row");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("Working (ok)"),
      expect.stringContaining("Not configured (not_configured)"),
      expect.stringContaining("Unavailable (unreachable)"),
    ]);
  });

  it("audit log names link reviews plainly; a confirmed link is not a finding of crime", async () => {
    const row = (id: string, action: string, decision: string) => ({ id, actor_user_id: "u-investigator", action, target_user_id: null, before: { previous_decision: null }, after: { complaint_low: "c-1", complaint_high: "c-2", decision, basis: "observed", note_given: false }, created_at: null });
    mockFetch({
      "GET /admin/users*": json(200, { items: [], total: 0, limit: 20, offset: 0 }),
      "GET /admin/audit*": json(200, { items: [row("a1", "link_confirm", "confirmed"), row("a2", "link_reject", "rejected")], total: 2, limit: 20, offset: 0 }),
    });
    const user = userEvent.setup();
    renderApp(<Admin />);
    await user.click(screen.getByRole("tab", { name: "Audit log" }));
    const rows = await screen.findAllByTestId("audit-row");
    expect(rows[0]).toHaveTextContent("Complaint link confirmed (same case; not a finding of crime)");
    expect(rows[0]).toHaveTextContent('"complaint_low":"c-1"');
    expect(rows[1]).toHaveTextContent("Complaint link rejected");
  });
});

describe("admin configuration", () => {
  it("lists the effective settings read-only", async () => {
    useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION("ADMIN") as never });
    mockFetch({
      "GET /admin/users*": json(200, { items: [], total: 0, limit: 20, offset: 0 }),
      "GET /admin/config": json(200, { environment: "production", auth: { mode: "clerk", local_sign_in: false, investigator_allowlist_size: 2 }, cors_origins: [], note: "Read-only. Secrets are never shown." }),
    });
    const user = userEvent.setup();
    renderApp(<Admin />);
    await user.click(screen.getByRole("tab", { name: "Configuration" }));
    const rows = await screen.findAllByTestId("config-row");
    const text = rows.map((r) => r.textContent).join("|");
    expect(text).toContain("Environmentproduction");
    expect(text).toContain("Auth · Local sign inno");
    expect(text).toContain("Auth · Investigator allowlist size2");
    expect(text).toContain("Cors originsnone");
    expect(screen.getByTestId("admin-config")).toHaveTextContent("Secrets are never shown.");
  });
});

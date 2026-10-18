// MOCKED fetch (no server): Phase 9 related complaints, network graph, clusters and identifier search.
// All records are SYNTHETIC.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { complaint, json, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";
import { layoutNetwork } from "@/lib/complaints/networkLayout";
import type { GraphResponse } from "@/lib/complaints/network";
import ComplaintPage from "./ComplaintPage";
import Network from "./Network";

const A = "c0ffee00-0000-4000-8000-000000000001";
const B = "c0ffee00-0000-4000-8000-00000000000b";
const C = "c0ffee00-0000-4000-8000-00000000000c";
const DISCLAIMER = "Shared identifiers are leads, not proof that the same person is involved.";
const NO_ANALYSIS = json(404, { error: "analysis_not_found", message: "No analysis has been run for this complaint." });

function as(role: "CITIZEN" | "INVESTIGATOR" | "ADMIN") {
  useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION(role) as never });
}

afterEach(() => vi.unstubAllGlobals());

const summary = (id: string, title: string) => ({ id, title, category: "UPI_FRAUD", status: "SUBMITTED", risk_level: null, created_at: "2026-10-14T09:00:00+00:00" });

const RELATED = {
  complaint_id: A,
  linking: "enabled",
  linking_message: null,
  disclaimer: DISCLAIMER,
  items: [
    {
      complaint: summary(B, "Synthetic refund call B"),
      link_type: "observed",
      basis: "observed",
      shared: [{ type: "upi", display: "sy•••@okaxis", link: "observed", this_field: "upi_id", other_field: "upi_id" }],
      review: null,
    },
    {
      complaint: summary(C, "Synthetic phishing link C"),
      link_type: "inferred",
      basis: "inferred",
      shared: [{ type: "domain", display: "example-pay.top", link: "inferred", this_field: "suspicious_url", other_field: "suspicious_url" }],
      review: null,
    },
  ],
  total: 2,
  limit: 10,
  offset: 0,
  truncated: false,
};

function renderStaffDetail() {
  return renderApp(
    <Routes>
      <Route path="/investigator/complaints/:id" element={<ComplaintPage />} />
    </Routes>,
    `/investigator/complaints/${A}`,
  );
}

describe("related complaints panel", () => {
  it("staff see linked complaints with their evidence; inferred is labelled; confirmed is not guilt", async () => {
    as("INVESTIGATOR");
    mockFetch({ [`GET /complaints/${A}`]: json(200, complaint()), [`GET /complaints/${A}/analysis`]: NO_ANALYSIS, [`GET /complaints/${A}/related`]: json(200, RELATED) });
    renderStaffDetail();
    const items = await screen.findAllByTestId("related-item");
    expect(items).toHaveLength(2);
    expect(within(items[0]!).getByTestId("link-badge")).toHaveAttribute("data-link", "observed");
    expect(within(items[0]!).getByTestId("shared-identifier")).toHaveTextContent("UPI ID sy•••@okaxis · UPI ID here, UPI ID there");
    expect(within(items[1]!).getByTestId("link-badge")).toHaveAttribute("data-link", "inferred");
    expect(within(items[1]!).getByTestId("shared-identifier")).toHaveTextContent("inferred (domain only)");
    const disc = screen.getByTestId("related-disclaimer");
    expect(disc).toHaveTextContent(DISCLAIMER);
    expect(disc).toHaveTextContent("not a finding that the identifier's owner, or anyone else, committed a crime");
  });

  it("confirming records a review with an optional note, and the dialog says it is not a finding of crime", async () => {
    as("INVESTIGATOR");
    const { calls } = mockFetch({
      [`GET /complaints/${A}`]: json(200, complaint()),
      [`GET /complaints/${A}/analysis`]: NO_ANALYSIS,
      [`GET /complaints/${A}/related`]: json(200, RELATED),
      "POST /complaint-links/review": json(201, { review: { id: "r1", decision: "confirmed", basis: "observed", reviewer_user_id: "u-investigator", note: "same UPI", created_at: null }, link_type: "confirmed", previous_decision: null, complaint_low: A, complaint_high: B }),
    });
    renderStaffDetail();
    const [first] = await screen.findAllByTestId("related-item");
    await userEvent.click(within(first!).getByTestId("link-confirm"));
    expect(await screen.findByRole("dialog")).toHaveTextContent("not a finding that anyone committed a crime");
    await userEvent.type(screen.getByTestId("link-note"), "same UPI");
    await userEvent.click(screen.getByTestId("link-submit"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/complaint-links/review")).toBe(true));
    const post = calls.find((c) => c.path === "/complaint-links/review")!;
    expect(post.body).toEqual({ complaint_a: A, complaint_b: B, decision: "confirmed", note: "same UPI" });
    expect(await screen.findByTestId("related-announce")).toHaveTextContent("Your review is recorded");
  });

  it("an opted-out complaint shows that linking is off and lists nothing", async () => {
    as("INVESTIGATOR");
    mockFetch({
      [`GET /complaints/${A}`]: json(200, complaint()),
      [`GET /complaints/${A}/analysis`]: NO_ANALYSIS,
      [`GET /complaints/${A}/related`]: json(200, { ...RELATED, linking: "disabled", linking_message: "The complainant turned off case matching.", items: [], total: 0 }),
    });
    renderStaffDetail();
    expect(await screen.findByTestId("related-disabled")).toHaveTextContent("turned off case matching");
    expect(screen.queryByTestId("related-item")).not.toBeInTheDocument();
  });

  it("citizens never request related complaints", async () => {
    as("CITIZEN");
    const { calls } = mockFetch({ [`GET /complaints/${A}`]: json(200, complaint()), [`GET /complaints/${A}/analysis`]: NO_ANALYSIS });
    renderApp(
      <Routes>
        <Route path="/me/complaints/:id" element={<ComplaintPage />} />
      </Routes>,
      `/me/complaints/${A}`,
    );
    await screen.findByTestId("complaint-id");
    expect(screen.queryByTestId("related-panel")).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.includes("/related") || c.path.includes("/graph"))).toBe(false);
  });
});

const GRAPH: GraphResponse = {
  center: A,
  depth: 1,
  linking: "enabled",
  linking_message: null,
  disclaimer: DISCLAIMER,
  nodes: [
    { id: `c:${A}`, kind: "complaint", complaint_id: A, title: "Synthetic A", category: "UPI_FRAUD", status: "SUBMITTED", risk_level: null, created_at: null, center: true, distance: 0 },
    { id: `c:${B}`, kind: "complaint", complaint_id: B, title: "Synthetic B", category: "UPI_FRAUD", status: "SUBMITTED", risk_level: null, created_at: null, center: false, distance: 1 },
    { id: `c:${C}`, kind: "complaint", complaint_id: C, title: "Synthetic C", category: "PHISHING", status: "SUBMITTED", risk_level: null, created_at: null, center: false, distance: 1 },
    { id: "e:upi:aaaa", kind: "entity", entity_type: "upi", display: "sy•••@okaxis", complaint_count: 2 },
    { id: "e:domain:bbbb", kind: "entity", entity_type: "domain", display: "example-pay.top", complaint_count: 2 },
  ],
  edges: [
    { id: "e1", kind: "identifier", source: `c:${A}`, target: "e:upi:aaaa", entity_type: "upi", link: "observed", source_field: "upi_id", evidence: "Same normalised value in this complaint's UPI ID field (upi_id)." },
    { id: "e2", kind: "identifier", source: `c:${B}`, target: "e:upi:aaaa", entity_type: "upi", link: "observed", source_field: "upi_id", evidence: "Same normalised value in this complaint's UPI ID field (upi_id)." },
    { id: "e3", kind: "identifier", source: `c:${A}`, target: "e:domain:bbbb", entity_type: "domain", link: "inferred", source_field: "suspicious_url", evidence: "Registrable domain of this complaint's Suspicious link (suspicious_url). A shared domain with a different link is inferred, not proof." },
    { id: "e4", kind: "identifier", source: `c:${C}`, target: "e:domain:bbbb", entity_type: "domain", link: "inferred", source_field: "suspicious_url", evidence: "Registrable domain of this complaint's Suspicious link (suspicious_url). A shared domain with a different link is inferred, not proof." },
    { id: "r1", kind: "review", source: `c:${A}`, target: `c:${B}`, decision: "confirmed", basis: "observed", reviewer_user_id: "u-investigator", note: null, created_at: null, review_count: 1 },
  ],
  truncated: false,
  counts: { complaints: 3, entities: 2, edges: 5 },
  limits: { depth: 1, complaints: 40, entities: 60, edges: 250 },
};

const CLUSTERS = {
  items: [{ id: "cl-1", size: 2, member_ids: [A, B], member_ids_truncated: false, members: [summary(A, "Synthetic A"), summary(B, "Synthetic B")], members_truncated: false, connecting_types: { upi: 1 }, latest_created_at: null, earliest_created_at: null }],
  total: 1,
  limit: 10,
  offset: 0,
  truncated: false,
  disclaimer: DISCLAIMER,
  basis: "Exactly shared identifiers only.",
};

function renderNetwork(route = `/investigator/network?complaint=${A}`) {
  return renderApp(
    <Routes>
      <Route path="/investigator/network" element={<Network />} />
    </Routes>,
    route,
  );
}

describe("network page", () => {
  beforeEach(() => as("INVESTIGATOR"));

  it("draws every connection with its evidence; inferred and confirmed are distinguishable in text", async () => {
    mockFetch({ [`GET /complaints/${A}/graph`]: json(200, GRAPH), "GET /graph/clusters": json(200, CLUSTERS) });
    renderNetwork();
    await screen.findByTestId("network-graph");
    expect(screen.getAllByTestId("graph-complaint")).toHaveLength(3);
    expect(screen.getAllByTestId("graph-entity")).toHaveLength(2);
    const rows = screen.getAllByTestId("graph-edge-row");
    expect(rows).toHaveLength(GRAPH.edges.length);
    for (const r of rows) expect(within(r).getByTestId("edge-evidence").textContent!.length).toBeGreaterThan(10);
    const inferred = rows.filter((r) => r.dataset.link === "inferred");
    expect(inferred).toHaveLength(2);
    for (const r of inferred) expect(r).toHaveTextContent("inferred, not proof");
    const confirmed = rows.find((r) => r.dataset.link === "confirmed")!;
    expect(confirmed).toHaveTextContent("Investigator review: confirmed");
    expect(confirmed).toHaveTextContent("It is not a finding that the identifier's owner, or anyone else, committed a crime.");
    expect(screen.getByTestId("network-disclaimer")).toHaveTextContent(DISCLAIMER);
    expect(screen.getByTestId("graph-legend")).toHaveTextContent("Inferred");
  });

  it("selecting a node by keyboard shows its connections; depth 2 is requested on demand", async () => {
    const { calls } = mockFetch({ [`GET /complaints/${A}/graph`]: json(200, GRAPH), "GET /graph/clusters": json(200, CLUSTERS) });
    renderNetwork();
    const nodes = await screen.findAllByTestId("graph-entity");
    nodes[0]!.focus();
    await userEvent.keyboard("{Enter}");
    const detail = screen.getByTestId("graph-detail");
    expect(detail).toHaveTextContent("UPI ID sy•••@okaxis (masked)");
    expect(within(detail).getAllByTestId("edge-evidence")).toHaveLength(2);
    await userEvent.click(screen.getByTestId("depth-2"));
    await waitFor(() => expect(calls.some((c) => c.path === `/complaints/${A}/graph?depth=2`)).toBe(true));
  });

  it("shows the opt-out message and the truncation notice from the server", async () => {
    mockFetch({ [`GET /complaints/${A}/graph`]: json(200, { ...GRAPH, linking: "disabled", linking_message: "The complainant turned off case matching.", nodes: [GRAPH.nodes[0]], edges: [] }), "GET /graph/clusters": json(200, CLUSTERS) });
    renderNetwork();
    expect(await screen.findByTestId("graph-disabled")).toHaveTextContent("turned off case matching");
    expect(screen.queryByTestId("network-graph")).not.toBeInTheDocument();
  });

  it("truncated graphs say so", async () => {
    mockFetch({ [`GET /complaints/${A}/graph`]: json(200, { ...GRAPH, truncated: true }), "GET /graph/clusters": json(200, CLUSTERS) });
    renderNetwork();
    expect(await screen.findByTestId("graph-truncated")).toHaveTextContent("only part of it is shown");
  });

  it("lists clusters; picking a member opens its network", async () => {
    const { calls } = mockFetch({ "GET /complaints/*": json(200, GRAPH), "GET /graph/clusters": json(200, CLUSTERS) });
    renderNetwork("/investigator/network");
    const members = await screen.findAllByTestId("cluster-member");
    expect(members).toHaveLength(2);
    expect(calls.some((c) => c.path.includes("/graph?"))).toBe(false);
    await userEvent.click(members[1]!);
    await waitFor(() => expect(calls.some((c) => c.path === `/complaints/${B}/graph?depth=1`)).toBe(true));
  });

  it("identifier search sends the value in a POST body only, never in a URL", async () => {
    const { calls } = mockFetch({
      "GET /graph/clusters": json(200, CLUSTERS),
      "POST /entities/search": json(200, { entity: { type: "upi", display: "sy•••@okaxis" }, items: [{ ...summary(B, "Synthetic B"), source_field: "upi_id" }], total: 1, limit: 20, offset: 0, disclaimer: DISCLAIMER }),
    });
    renderNetwork("/investigator/network");
    await userEvent.type(screen.getByTestId("search-value"), "synthetic@okaxis");
    await userEvent.click(screen.getByTestId("search-submit"));
    expect(await screen.findAllByTestId("search-item")).toHaveLength(1);
    expect(screen.getByTestId("search-result")).toHaveTextContent(DISCLAIMER);
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toMatchObject({ type: "upi", value: "synthetic@okaxis" });
    expect(calls.every((c) => !c.path.includes("synthetic"))).toBe(true);
  });

  it("a social account search asks for the platform", async () => {
    const { calls } = mockFetch({ "GET /graph/clusters": json(200, CLUSTERS), "POST /entities/search": json(200, { entity: { type: "social_account", display: "instagram: sy•••" }, items: [], total: 0, limit: 20, offset: 0, disclaimer: DISCLAIMER }) });
    renderNetwork("/investigator/network");
    await userEvent.selectOptions(screen.getByTestId("search-type"), "social_account");
    await userEvent.type(screen.getByTestId("search-platform"), "Instagram");
    await userEvent.type(screen.getByTestId("search-value"), "@synthetic_user");
    await userEvent.click(screen.getByTestId("search-submit"));
    await screen.findByTestId("search-result");
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ type: "social_account", platform: "Instagram", value: "@synthetic_user" });
  });
});

describe("network layout", () => {
  it("is deterministic, puts the centre in the middle and keeps every node inside the canvas", () => {
    const a = layoutNetwork(GRAPH.nodes, GRAPH.edges, 720, 520);
    const b = layoutNetwork(GRAPH.nodes, GRAPH.edges, 720, 520);
    expect([...a.entries()]).toEqual([...b.entries()]);
    expect(a.get(`c:${A}`)).toEqual({ x: 360, y: 260 });
    expect(a.size).toBe(GRAPH.nodes.length);
    for (const p of a.values()) {
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.x).toBeLessThanOrEqual(720);
      expect(p.y).toBeGreaterThanOrEqual(0);
      expect(p.y).toBeLessThanOrEqual(520);
    }
  });
});

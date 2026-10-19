// MOCKED fetch (no server): Phase 12 investigation notes, case timeline and report download.
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { complaint, json, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";
import ComplaintPage from "./ComplaintPage";

const A = "c0ffee00-0000-4000-8000-000000000001";
const NO_ANALYSIS = json(404, { error: "analysis_not_found", message: "No analysis has been run for this complaint." });

afterEach(() => vi.unstubAllGlobals());

function as(role: "CITIZEN" | "INVESTIGATOR") {
  useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION(role) as never });
}

function renderAt(base: string) {
  return renderApp(
    <Routes>
      <Route path={`${base}/:id`} element={<ComplaintPage />} />
    </Routes>,
    `${base}/${A}`,
  );
}

const TIMELINE = {
  complaint_id: A,
  items: [
    { at: "2026-10-14T09:00:00+00:00", kind: "complaint_created", text: "Complaint registered by the complainant.", actor_user_id: "u-citizen" },
    { at: "2026-10-14T10:00:00+00:00", kind: "status_changed", text: "Status SUBMITTED -> UNDER_REVIEW.", note: "Picked up", actor_user_id: "u-investigator" },
    { at: "2026-10-14T11:00:00+00:00", kind: "link_review", text: "Link with complaint c0ffee00 confirmed (observed basis). A link is not a finding of crime.", actor_user_id: "u-investigator" },
  ],
};

describe("case tools", () => {
  it("staff add a note; the list, the timeline and the privacy notice are shown", async () => {
    as("INVESTIGATOR");
    let notes = [{ id: "n1", complaint_id: A, author_user_id: "u-investigator", body: "Old note", created_at: null }];
    const { calls } = mockFetch({
      [`GET /complaints/${A}`]: json(200, complaint()),
      [`GET /complaints/${A}/analysis`]: NO_ANALYSIS,
      [`GET /complaints/${A}/notes`]: () => json(200, { items: notes, total: notes.length, limit: 20, offset: 0 }),
      [`POST /complaints/${A}/notes`]: (c) => {
        notes = [{ id: "n2", complaint_id: A, author_user_id: "u-investigator", body: (c.body as { body: string }).body, created_at: null }, ...notes];
        return json(201, notes[0]);
      },
      [`GET /complaints/${A}/timeline`]: json(200, TIMELINE),
    });
    renderAt("/investigator/complaints");
    const panel = await screen.findByTestId("notes-panel");
    expect(panel).toHaveTextContent("never to the complainant");
    expect(await within(panel).findByText("Old note")).toBeInTheDocument();
    await userEvent.type(screen.getByTestId("note-text"), "Called the bank");
    await userEvent.click(screen.getByTestId("note-save"));
    await waitFor(() => expect(within(panel).getAllByTestId("note-item")).toHaveLength(2));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ body: "Called the bank" });
    const items = await screen.findAllByTestId("timeline-item");
    expect(items.map((i) => i.dataset.kind)).toEqual(["complaint_created", "status_changed", "link_review"]);
    expect(items[1]).toHaveTextContent("Picked up");
    expect(items[2]).toHaveTextContent("not a finding of crime");
  });

  it("an empty note is refused before sending", async () => {
    as("INVESTIGATOR");
    const { calls } = mockFetch({
      [`GET /complaints/${A}`]: json(200, complaint()),
      [`GET /complaints/${A}/analysis`]: NO_ANALYSIS,
      [`GET /complaints/${A}/notes`]: json(200, { items: [], total: 0, limit: 20, offset: 0 }),
      [`GET /complaints/${A}/timeline`]: json(200, TIMELINE),
    });
    renderAt("/investigator/complaints");
    await screen.findByText("No notes yet");
    await userEvent.click(screen.getByTestId("note-save"));
    expect(screen.getByTestId("note-error")).toHaveTextContent("Write a note first.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("the owner can download the report but sees no staff panels", async () => {
    as("CITIZEN");
    const saved: string[] = [];
    vi.spyOn(URL, "createObjectURL").mockImplementation(() => {
      saved.push("blob");
      return "blob:x";
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const { calls } = mockFetch({
      [`GET /complaints/${A}`]: json(200, complaint()),
      [`GET /complaints/${A}/analysis`]: NO_ANALYSIS,
      [`GET /complaints/${A}/report`]: () => new Response("# Complaint report", { status: 200, headers: { "content-type": "text/markdown" } }),
    });
    renderAt("/me/complaints");
    await userEvent.click(await screen.findByTestId("report-download"));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(calls.some((c) => c.path === `/complaints/${A}/report`)).toBe(true);
    expect(screen.queryByTestId("notes-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("timeline-panel")).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.includes("/notes") || c.path.includes("/timeline"))).toBe(false);
  });
});

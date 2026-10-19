// MOCKED fetch (no server): Phase 11 "Add or correct details" for the complainant.
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { complaint, json, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";
import ComplaintPage from "./ComplaintPage";

const A = "c0ffee00-0000-4000-8000-000000000001";
const NO_ANALYSIS = json(404, { error: "analysis_not_found", message: "No analysis has been run for this complaint." });

afterEach(() => vi.unstubAllGlobals());

function render(role: "CITIZEN" | "INVESTIGATOR", c: Record<string, unknown>, patch?: (b: unknown) => Response) {
  useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION(role) as never });
  const m = mockFetch({
    [`GET /complaints/${A}`]: json(200, complaint(c)),
    [`GET /complaints/${A}/analysis`]: NO_ANALYSIS,
    [`PATCH /complaints/${A}`]: (call) => (patch ? patch(call.body) : json(200, complaint(c))),
  });
  const base = role === "CITIZEN" ? "/me/complaints" : "/investigator/complaints";
  renderApp(
    <Routes>
      <Route path={`${base}/:id`} element={<ComplaintPage />} />
    </Routes>,
    `${base}/${A}`,
  );
  return m;
}

describe("edit details", () => {
  it("sends only what changed; an emptied box clears the field; the page shows the result", async () => {
    const { calls } = render("CITIZEN", { city: "Pune", upi_id: "old@ybl" }, (body) => json(200, complaint({ city: "Pune", upi_id: null, bank_name: "Example Bank", ...(body as object) })));
    await userEvent.click(await screen.findByTestId("edit-details"));
    await userEvent.clear(screen.getByTestId("e-upi_id"));
    await userEvent.type(screen.getByTestId("e-bank_name"), "Example Bank");
    await userEvent.click(screen.getByTestId("edit-save"));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ upi_id: null, bank_name: "Example Bank" });
    await waitFor(() => expect(screen.queryByTestId("edit-form")).not.toBeInTheDocument());
    expect(screen.getByTestId("complaint-fields")).toHaveTextContent("Example Bank");
  });

  it("coordinates are sent together; a server field error is shown on its box", async () => {
    const { calls } = render("CITIZEN", {}, () => json(422, { error: "validation_error", detail: [{ loc: ["body", "latitude"], msg: "Input should be less than or equal to 90", type: "less_than_equal" }] }));
    await userEvent.click(await screen.findByTestId("edit-details"));
    await userEvent.type(screen.getByTestId("e-latitude"), "95");
    await userEvent.type(screen.getByTestId("e-longitude"), "72.8");
    await userEvent.click(screen.getByTestId("edit-save"));
    expect(await screen.findByTestId("edit-error")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ latitude: "95", longitude: "72.8" });
    expect(screen.getAllByTestId("field-error").some((e) => /less than or equal to 90/.test(e.textContent ?? ""))).toBe(true);
  });

  it("nothing changed: nothing is sent", async () => {
    const { calls } = render("CITIZEN", {});
    await userEvent.click(await screen.findByTestId("edit-details"));
    await userEvent.click(screen.getByTestId("edit-save"));
    expect(screen.getByTestId("edit-error")).toHaveTextContent("Nothing was changed.");
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("not offered once review started, nor to staff", async () => {
    render("CITIZEN", { status: "UNDER_REVIEW" });
    await screen.findByTestId("complaint-id");
    expect(screen.queryByTestId("edit-details")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
    render("INVESTIGATOR", {});
    await screen.findAllByTestId("complaint-id");
    expect(screen.queryByTestId("edit-details")).not.toBeInTheDocument();
  });
});

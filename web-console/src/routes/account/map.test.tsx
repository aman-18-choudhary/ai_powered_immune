// MOCKED fetch (no server): Phase 10 complaint map. SYNTHETIC counts.
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { json, mockFetch, SESSION } from "@/lib/complaints/mockFetch.test-helper";
import { useAccount } from "@/state/account";
import { hotspotsQs } from "@/lib/complaints/geo";
import ComplaintMap from "./ComplaintMap";

afterEach(() => vi.unstubAllGlobals());

const NOTE = "Counts of complaints, not of offences or offenders.";
const RES = {
  cell_degrees: 0.25,
  min_count: 1,
  note: NOTE,
  filters: {},
  totals: { complaints: 4, with_coordinates: 3, without_coordinates: 1, cells: 2 },
  cells: [
    { id: "0.25:76:291", lat: 19.125, lon: 72.875, bounds: { south: 19, west: 72.75, north: 19.25, east: 73 }, count: 2, by_category: { UPI_FRAUD: 2 }, by_risk: { not_scored: 2 }, first_at: null, last_at: null },
    { id: "0.25:114:308", lat: 28.625, lon: 77.125, bounds: { south: 28.5, west: 77, north: 28.75, east: 77.25 }, count: 1, by_category: { PHISHING: 1 }, by_risk: { HIGH: 1 }, first_at: null, last_at: null },
  ],
  cells_truncated: false,
  places: [{ state: "Maharashtra", city: "Pune", count: 1 }],
  places_truncated: false,
  comparison: null,
  truncated: false,
};

function renderMap() {
  useAccount.setState({ method: "local", token: "tok", status: "ready", session: SESSION("INVESTIGATOR") as never });
  return renderApp(
    <Routes>
      <Route path="/investigator/map" element={<ComplaintMap />} />
    </Routes>,
    "/investigator/map",
  );
}

describe("complaint map", () => {
  it("draws cells on the bundled map, lists places without coordinates and explains what counts", async () => {
    mockFetch({ "GET /geo/complaints/hotspots*": json(200, RES) });
    renderMap();
    const dots = await screen.findAllByTestId("map-cell-dot");
    expect(dots.map((d) => d.getAttribute("data-count"))).toEqual(["2", "1"]);
    expect(screen.getByTestId("map-totals")).toHaveTextContent("4 complaints: 3 with coordinates in 2 cells, 1 without coordinates.");
    expect(screen.getByTestId("place-row")).toHaveTextContent("Pune, Maharashtra1");
    expect(screen.getByTestId("map-note")).toHaveTextContent(NOTE);
    expect(screen.getByTestId("map-note")).toHaveTextContent("no map or tile service is contacted");
  });

  it("a cell is selectable by keyboard and shows its breakdown", async () => {
    mockFetch({ "GET /geo/complaints/hotspots*": json(200, RES) });
    renderMap();
    const dots = await screen.findAllByTestId("map-cell-dot");
    dots[1]!.focus();
    await userEvent.keyboard("{Enter}");
    const detail = screen.getByTestId("cell-detail");
    expect(detail).toHaveTextContent("1 complaint in the cell 28.50–28.75° N, 77.00–77.25° E");
    expect(detail).toHaveTextContent("Phishing");
    expect(detail).toHaveTextContent("Risk: High 1");
  });

  it("filters go to the server; a comparison needs a 'from' date", async () => {
    const { calls } = mockFetch({ "GET /geo/complaints/hotspots*": json(200, { ...RES, comparison: { previous: { from: null, to: null } }, cells: [{ ...RES.cells[0], previous_count: 1, change: 1 }] }) });
    renderMap();
    await screen.findAllByTestId("map-cell-dot");
    await userEvent.click(screen.getByTestId("map-compare"));
    await userEvent.click(screen.getByTestId("map-apply"));
    expect(screen.getByTestId("map-error")).toHaveTextContent("Choose a 'from' date");
    await userEvent.selectOptions(screen.getByTestId("map-category"), "PHISHING");
    await userEvent.selectOptions(screen.getByTestId("map-cell"), "1");
    await userEvent.type(screen.getByTestId("map-from"), "2026-10-01");
    await userEvent.click(screen.getByTestId("map-apply"));
    await waitFor(() => expect(calls.some((c) => c.path.includes("category=PHISHING") && c.path.includes("cell=1") && c.path.includes("compare=true"))).toBe(true));
    expect(await screen.findByTestId("map-legend")).toHaveTextContent("Red: more than in the period before");
    expect(within(screen.getAllByTestId("cell-row")[0]!).getByText(/\+1 vs 1 before/)).toBeInTheDocument();
  });

  it("builds the query string without empty filters", () => {
    expect(hotspotsQs({ cell: 0.25, min_count: 1, compare: false })).toBe("cell=0.25&min_count=1");
  });

  it("no coordinates at all is an empty state, not an empty map", async () => {
    mockFetch({ "GET /geo/complaints/hotspots*": json(200, { ...RES, cells: [], totals: { complaints: 1, with_coordinates: 0, without_coordinates: 1, cells: 0 } }) });
    renderMap();
    expect(await screen.findByText("No complaints with coordinates")).toBeInTheDocument();
  });
});

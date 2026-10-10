import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test-utils";
import { fixtures } from "@/mocks/fixture";

vi.mock("@/lib/api/endpoints", () => ({
  endpoints: {
    ringGraph: vi.fn(),
    ringSimulate: vi.fn(),
    bestTakedown: vi.fn(),
  },
}));
import { endpoints } from "@/lib/api/endpoints";
import { RingPanel } from "./RingPanel";

beforeEach(() => {
  vi.mocked(endpoints.ringGraph).mockResolvedValue(fixtures.ring);
  vi.mocked(endpoints.ringSimulate).mockResolvedValue(fixtures.simulate);
  vi.mocked(endpoints.bestTakedown).mockResolvedValue({
    greedy: fixtures.simulate,
    optimal: fixtures.simulate,
    gap_pct: 0,
  });
});

describe("RingPanel takedown simulator", () => {
  it("renders the graph and simulates freezing k accounts through the API", async () => {
    renderApp(<RingPanel refreshKey={1} />);
    expect(await screen.findByRole("group", { name: /Ring graph/ })).toBeInTheDocument();
    await waitFor(() => expect(endpoints.ringSimulate).toHaveBeenCalledWith("current", { k: 1 }, expect.anything()));
    expect(await screen.findByTestId("takedown-gauge")).toHaveTextContent("100%");
    expect(screen.getByText(/not a prediction/)).toBeInTheDocument();
    expect(screen.getByText(/₹5,69,394/)).toBeInTheDocument();
  });

  it("slider changes k and re-simulates", async () => {
    renderApp(<RingPanel refreshKey={1} />);
    await screen.findByTestId("takedown-gauge");
    const slider = screen.getByLabelText(/Freeze k accounts/);
    await act(async () => {
      fireEvent.change(slider, { target: { value: "2" } });
    });
    await waitFor(() => expect(endpoints.ringSimulate).toHaveBeenCalledWith("current", { k: 2 }, expect.anything()));
  });

  it("Best takedown selects the greedy accounts and simulates them by id", async () => {
    renderApp(<RingPanel refreshKey={1} />);
    await screen.findByTestId("takedown-gauge");
    await userEvent.click(screen.getByTestId("best-takedown"));
    await waitFor(() =>
      expect(endpoints.ringSimulate).toHaveBeenCalledWith("current", { remove: fixtures.simulate.removed }, expect.anything()),
    );
  });

  it("clicking a mule node toggles it into the selection; victims are not selectable", async () => {
    renderApp(<RingPanel refreshKey={1} />);
    await screen.findByTestId("takedown-gauge");
    const mule = fixtures.ring.nodes.find((n) => n.role === "mule");
    const node = screen.getByRole("button", { name: new RegExp(`Mule ${mule?.id}`) });
    await userEvent.click(node);
    await waitFor(() =>
      expect(endpoints.ringSimulate).toHaveBeenLastCalledWith("current", { remove: [mule?.id] }, expect.anything()),
    );
    const victim = fixtures.ring.nodes.find((n) => n.role === "victim");
    expect(screen.getByRole("img", { name: new RegExp(`Victim ${victim?.id}`) })).toBeInTheDocument();
  });

  it("shows a role message on 403", async () => {
    const { ApiError } = await import("@/lib/api/client");
    vi.mocked(endpoints.ringGraph).mockRejectedValue(new ApiError(403, "forbidden", "no"));
    renderApp(<RingPanel refreshKey={2} />);
    expect(await screen.findByText("Not available for this role")).toBeInTheDocument();
  });
});

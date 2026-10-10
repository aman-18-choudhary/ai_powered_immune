import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test-utils";
import { fixtures, heroEvents } from "@/mocks/fixture";

vi.mock("@/lib/api/endpoints", () => ({
  endpoints: { holds: vi.fn(), resolveHold: vi.fn(), demoState: vi.fn() },
}));
import { endpoints } from "@/lib/api/endpoints";
import { ApiError } from "@/lib/api/client";
import Analyst from "./Analyst";
import { useRunStore } from "@/state/runStore";
import { useSession } from "@/state/session";
import { useToasts } from "@/state/toasts";

const openHolds = () => ({ holds: fixtures.holds.holds.map((h) => ({ ...h, state: "open", resolved_by: null })) });
const sorted = () => [...fixtures.holds.holds].sort((a, b) => a.deadline_t - b.deadline_t || a.created_t - b.created_t);

beforeEach(() => {
  vi.clearAllMocks();
  useToasts.setState({ items: [] });
  useRunStore.getState().reset();
  useRunStore.getState().apply(heroEvents());
  useSession.setState({ token: "t", role: "analyst", sub: "analyst-1" });
  vi.mocked(endpoints.holds).mockResolvedValue(openHolds() as never);
});

describe("Analyst console", () => {
  it("lists holds soonest-deadline first with exact INR amounts", async () => {
    renderApp(<Analyst />);
    const rows = await screen.findAllByTestId("hold-row");
    expect(rows.map((r) => r.getAttribute("data-ref"))).toEqual(sorted().map((h) => h.txn_ref));
    expect(within(rows[0] as HTMLElement).getByText("₹16,000.00")).toBeInTheDocument();
  });

  it("officer is read-only: banner shown, no resolve buttons", async () => {
    useSession.setState({ role: "officer" });
    renderApp(<Analyst />);
    expect(await screen.findByTestId("readonly-banner")).toBeInTheDocument();
    expect(screen.queryByTestId("act-confirm")).not.toBeInTheDocument();
  });

  it("confirm needs a valid note, then sends confirm_block with the decision_seq", async () => {
    vi.mocked(endpoints.resolveHold).mockResolvedValue({ hold: { ...fixtures.holds.holds[0]!, state: "blocked" } } as never);
    const user = userEvent.setup();
    renderApp(<Analyst />);
    await screen.findAllByTestId("hold-row");
    await user.click(screen.getByTestId("act-confirm"));
    await user.type(screen.getByTestId("resolve-note"), "call 9876543210 now");
    await user.click(screen.getByTestId("resolve-submit"));
    expect(endpoints.resolveHold).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/long numbers/);
    await user.clear(screen.getByTestId("resolve-note"));
    await user.type(screen.getByTestId("resolve-note"), "Payee is the known mule from the call.");
    await user.click(screen.getByTestId("resolve-submit"));
    const first = sorted()[0]!;
    await waitFor(() => expect(endpoints.resolveHold).toHaveBeenCalledWith(first.txn_ref, "confirm_block", first.decision_seq));
    // the server now reports it blocked: it leaves the open queue and shows under Resolved with its local note
    vi.mocked(endpoints.holds).mockResolvedValue({
      holds: openHolds().holds.map((h) => (h.txn_ref === first.txn_ref ? { ...h, state: "blocked", resolved_by: "analyst-1" } : h)),
    } as never);
    await user.click(screen.getByRole("button", { name: "Refresh holds" }));
    await user.click(screen.getByRole("button", { name: "Resolved" }));
    expect(await screen.findByTestId("local-note")).toHaveTextContent("Payee is the known mule");
  });

  it("rolls back and names the cause on a 409", async () => {
    vi.mocked(endpoints.resolveHold).mockRejectedValue(new ApiError(409, "conflict", "x", "hold_conflict"));
    const user = userEvent.setup();
    renderApp(<Analyst />);
    await screen.findAllByTestId("hold-row");
    await user.keyboard("r");
    await user.type(await screen.findByTestId("resolve-note"), "Payer verified by phone call.");
    await user.click(screen.getByTestId("resolve-submit"));
    await waitFor(() => expect(useToasts.getState().items.some((t) => /Another change arrived/.test(t.title))).toBe(true));
    await waitFor(() => expect(screen.getByTestId("act-release")).toBeEnabled());
  });

  it("j/k move the selection and ? opens help", async () => {
    const user = userEvent.setup();
    renderApp(<Analyst />);
    const rows = await screen.findAllByTestId("hold-row");
    expect(rows[0]).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("j");
    expect(screen.getAllByTestId("hold-row")[1]).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("k");
    expect(screen.getAllByTestId("hold-row")[0]).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("?");
    expect(await screen.findByText("Keyboard shortcuts")).toBeInTheDocument();
  });

  it("empty state when there are no open holds", async () => {
    vi.mocked(endpoints.holds).mockResolvedValue({ holds: [] } as never);
    renderApp(<Analyst />);
    expect(await screen.findAllByText("No open holds")).not.toHaveLength(0);
  });

  it("shows the decision chain and reasons with meanings", async () => {
    renderApp(<Analyst />);
    await screen.findAllByTestId("hold-row");
    expect(screen.getByTestId("history-chain")).toHaveTextContent("hold_verify");
    expect(screen.getAllByTestId("reason-row").length).toBeGreaterThan(0);
  });
});

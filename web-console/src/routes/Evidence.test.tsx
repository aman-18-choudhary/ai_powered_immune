import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test-utils";
import ledgerText from "@/mocks/ledger.json?raw";
import checkpoints from "@/mocks/checkpoints.json";
import head from "@/mocks/head.json";
import { fixtures } from "@/mocks/fixture";

vi.mock("@/lib/api/endpoints", () => ({
  endpoints: {
    ledgerEntriesText: vi.fn(),
    ledgerHead: vi.fn(),
    checkpoints: vi.fn(),
    createCase: vi.fn(),
    packageWithEtag: vi.fn(),
    verify: vi.fn(),
  },
}));
import { endpoints } from "@/lib/api/endpoints";
import Evidence from "./Evidence";
import { useSession } from "@/state/session";
import { useEvidence } from "@/state/evidence";
import { useRunStore } from "@/state/runStore";

beforeEach(() => {
  vi.clearAllMocks();
  useEvidence.getState().reset();
  useRunStore.getState().reset();
  useSession.setState({ token: "t", role: "officer", sub: "o" });
  vi.mocked(endpoints.ledgerEntriesText).mockResolvedValue(ledgerText);
  vi.mocked(endpoints.ledgerHead).mockResolvedValue(head as never);
  vi.mocked(endpoints.checkpoints).mockResolvedValue(checkpoints as never);
});

describe("Evidence page", () => {
  it("draws every ledger entry as a linked block with a signed checkpoint flag", async () => {
    renderApp(<Evidence />);
    const blocks = await screen.findAllByTestId("chain-block");
    expect(blocks.length).toBe(JSON.parse(ledgerText).items.length);
    expect(screen.getByTestId("checkpoints")).toHaveTextContent("cp-0000000012");
    expect(screen.getByTestId("clock-note")).toHaveTextContent(/Simulated clock/);
    expect(screen.getByTestId("chain-head")).toHaveTextContent(`${head.count} entries`);
  });

  it("opens the inspector with envelope, pretty payload and a recomputed hash match", async () => {
    const user = userEvent.setup();
    renderApp(<Evidence />);
    await user.click(await screen.findByRole("button", { name: /Inspect ledger entry 1,/ }));
    const insp = await screen.findByTestId("inspector");
    expect(within(insp).getByText(/callrisk.alert/)).toBeInTheDocument();
    expect(within(insp).getByTestId("payload-json")).toHaveTextContent('"threshold": 0.7');
    await waitFor(() => expect(within(insp).getByTestId("hash-note")).toHaveTextContent(/matches payload_hash/));
  });

  it("verify shows PASS_PINNED with the verifier lines and the exit-code explanation, and marks redacted blocks", async () => {
    vi.mocked(endpoints.createCase).mockResolvedValue({ case_id: "hero-case", title: "t", case_refs: ["txn_x"], created_at: "x" } as never);
    vi.mocked(endpoints.verify).mockResolvedValue(fixtures.verify as never);
    const user = userEvent.setup();
    renderApp(<Evidence />);
    await screen.findAllByTestId("chain-block");
    await user.click(screen.getByTestId("build-case"));
    await screen.findByTestId("case-built");
    await user.click(screen.getByTestId("verify-run"));
    expect(await screen.findByTestId("verify-status")).toHaveTextContent("PASS_PINNED");
    expect(screen.getByTestId("exit-meaning")).toHaveTextContent(/Exit 0/);
    expect(screen.getByLabelText("Verifier output")).toHaveTextContent(/PASS/);
    const redacted = fixtures.verify.redacted_seqs;
    const sealed = screen.getAllByRole("button", { name: /payload withheld/ });
    expect(sealed.length).toBe(redacted.length);
  });

  it("package export shows size and ETag", async () => {
    vi.mocked(endpoints.createCase).mockResolvedValue({ case_id: "hero-case", title: "t", case_refs: [], created_at: "x" } as never);
    vi.mocked(endpoints.packageWithEtag).mockResolvedValue({ data: new Blob([new Uint8Array(2048)]), headers: { etag: '"abc123"', contentType: "application/zip" } });
    const user = userEvent.setup();
    renderApp(<Evidence />);
    await screen.findAllByTestId("chain-block");
    await user.click(screen.getByTestId("build-case"));
    await user.click(await screen.findByTestId("export-package").then((b) => (waitFor(() => expect(b).toBeEnabled()), b)));
    expect(await screen.findByTestId("package-meta")).toHaveTextContent('"abc123"');
    expect(screen.getByTestId("package-meta")).toHaveTextContent("2.0 KB");
  });

  it("citizen role cannot build cases; empty ledger shows an empty state", async () => {
    useSession.setState({ role: "citizen" });
    vi.mocked(endpoints.ledgerEntriesText).mockResolvedValue(JSON.stringify({ items: [], next_from_seq: null }));
    renderApp(<Evidence />);
    expect(await screen.findByText("The chain is empty")).toBeInTheDocument();
    expect(screen.getByTestId("case-readonly")).toBeInTheDocument();
  });
});

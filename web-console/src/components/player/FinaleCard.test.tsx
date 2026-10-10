import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test-utils";
import { fixtures } from "@/mocks/fixture";
import type { VerifyResult } from "@/types/api";

vi.mock("@/lib/api/endpoints", () => ({
  endpoints: {
    createCase: vi.fn(async () => ({ case_id: "hero-case" })),
    packageBlob: vi.fn(async () => new Blob([new Uint8Array(49300)])),
    verify: vi.fn(),
  },
}));

import { endpoints } from "@/lib/api/endpoints";
import { FinaleCard } from "./FinaleCard";
import { useEvidence } from "@/state/evidence";

const finished = { t: 2609, reason: "completed", ledgerEntries: 12, antibodyPublished: true, victimBProtected: true, latencyMs: 7.4 };

beforeEach(() => {
  useEvidence.getState().reset();
  vi.mocked(endpoints.verify).mockReset();
});

describe("FinaleCard", () => {
  it("announces victim B protected with the measured latency and package size", async () => {
    renderApp(<FinaleCard runId="r1" finished={finished} canExport onClose={() => {}} />);
    expect(screen.getByRole("heading", { name: "Victim B protected" })).toBeInTheDocument();
    expect(screen.getAllByText("7.4 ms").length).toBeGreaterThan(0);
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(await screen.findByText("48.1 KB")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open in Evidence/ })).toHaveAttribute("href", "/evidence");
    expect(screen.getByTestId("download-package")).toBeEnabled();
  });

  it("shows the real verifier result and its own lines", async () => {
    vi.mocked(endpoints.verify).mockResolvedValue(fixtures.verify);
    renderApp(<FinaleCard runId="r1" finished={finished} canExport onClose={() => {}} />);
    await userEvent.click(screen.getByTestId("verify-offline"));
    expect(await screen.findByTestId("verify-status")).toHaveTextContent("PASS_PINNED");
    const out = screen.getByLabelText("Verifier output");
    expect(out.textContent).toContain("PASS: package is internally consistent and signed by the pinned key");
    expect(out.textContent).toContain("redacted (payload withheld)");
  });

  it.each([
    ["INTEGRITY_OK_UNPINNED", "UNPINNED", 2],
    ["FAIL", "FAIL", 1],
  ] as const)("labels %s as %s", async (status, label, code) => {
    const v: VerifyResult = { ...fixtures.verify, status, exit_code: code, lines: ["line one"] };
    vi.mocked(endpoints.verify).mockResolvedValue(v);
    renderApp(<FinaleCard runId="r2" finished={finished} canExport onClose={() => {}} />);
    await userEvent.click(screen.getByTestId("verify-offline"));
    await waitFor(() => expect(screen.getByTestId("verify-status")).toHaveTextContent(label));
    expect(screen.getByText("line one")).toBeInTheDocument();
  });

  it("explains instead of exporting for roles that cannot", () => {
    renderApp(<FinaleCard runId="r3" finished={finished} canExport={false} onClose={() => {}} />);
    expect(screen.getByText(/need the officer or admin role/)).toBeInTheDocument();
    expect(screen.queryByTestId("verify-offline")).toBeNull();
  });

  it("reports a failed verify request without crashing", async () => {
    vi.mocked(endpoints.verify).mockRejectedValue(new Error("x"));
    renderApp(<FinaleCard runId="r4" finished={finished} canExport onClose={() => {}} />);
    await userEvent.click(screen.getByTestId("verify-offline"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Verification request failed/);
  });

  it("states honestly when no antibody was shared", () => {
    renderApp(<FinaleCard runId="r5" finished={{ ...finished, victimBProtected: false, antibodyPublished: false }} canExport onClose={() => {}} />);
    expect(screen.getByRole("heading", { name: /no antibody was shared/ })).toBeInTheDocument();
  });
});

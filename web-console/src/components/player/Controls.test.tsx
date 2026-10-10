import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test-utils";
import { Controls } from "./Controls";

const base = { speed: 5, stepKey: "transfer_held", onToggle: vi.fn(), onRestart: vi.fn(), onSpeed: vi.fn(), onFullscreen: vi.fn() };

describe("player Controls", () => {
  it("shows Pause while running and toggles", async () => {
    const onToggle = vi.fn();
    renderApp(<Controls {...base} status="running" onToggle={onToggle} />);
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("shows Resume while paused", () => {
    renderApp(<Controls {...base} status="paused" />);
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("offers Play again when finished and restarts", async () => {
    const onRestart = vi.fn();
    renderApp(<Controls {...base} status="finished" onRestart={onRestart} />);
    await userEvent.click(screen.getByRole("button", { name: "Play again" }));
    expect(onRestart).toHaveBeenCalled();
  });

  it("speed radio group reports the chosen multiplier and marks the active one", async () => {
    const onSpeed = vi.fn();
    renderApp(<Controls {...base} status="running" speed={1} onSpeed={onSpeed} />);
    expect(screen.getByRole("radio", { name: "1x" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: "2x" }));
    expect(onSpeed).toHaveBeenCalledWith(2);
  });

  it("disables everything for read-only roles", () => {
    renderApp(<Controls {...base} status="running" disabled />);
    expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "5x" })).toBeDisabled();
  });

  it("Explain this step shows static copy for the current step", async () => {
    renderApp(<Controls {...base} status="running" stepKey="bank_b_blocks" />);
    await userEvent.click(screen.getByRole("button", { name: "Explain this step" }));
    expect(await screen.findByText(/only the shared threat memory saves victim B/)).toBeInTheDocument();
  });
});

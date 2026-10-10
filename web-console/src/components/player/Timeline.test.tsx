import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test-utils";
import { Timeline } from "./Timeline";

const markers = [
  { key: "call_begins", t: 0, title: "A scam call begins" },
  { key: "transfer_held", t: 832, title: "Bank A holds the transfer" },
];

describe("Timeline", () => {
  it("jumps to a marker", async () => {
    const onSeek = vi.fn();
    renderApp(
      <Timeline t={100} duration={2609} markers={markers} reached={new Set(["call_begins"])} currentKey="call_begins" onSeek={onSeek} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Jump to Bank A holds the transfer" }));
    expect(onSeek).toHaveBeenCalledWith(832);
  });

  it("commits a scrub on pointer release, not on every change", () => {
    const onSeek = vi.fn();
    renderApp(<Timeline t={0} duration={2609} markers={[]} reached={new Set()} currentKey={null} onSeek={onSeek} />);
    const slider = screen.getByRole("slider");
    fireEvent.change(slider, { target: { value: "1500" } });
    expect(onSeek).not.toHaveBeenCalled();
    fireEvent.pointerUp(slider);
    expect(onSeek).toHaveBeenCalledWith(1500);
  });

  it("is inert when disabled and shows step chips as progress", () => {
    renderApp(<Timeline t={0} duration={100} markers={markers} reached={new Set(["call_begins"])} currentKey="call_begins" disabled onSeek={() => {}} />);
    expect(screen.getByRole("slider")).toBeDisabled();
    expect(screen.getByRole("button", { name: /Call/ })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("list", { name: "Scenario steps" }).children).toHaveLength(8);
  });
});

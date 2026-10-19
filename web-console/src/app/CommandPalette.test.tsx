import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { renderApp } from "@/test-utils";
import { ThemeProvider } from "@/lib/theme";
import { filterPalette, nextIndex } from "@/lib/palette";

vi.mock("@/state/playback", () => ({ playback: { start: vi.fn(), pause: vi.fn(), resume: vi.fn() } }));
import { CommandPalette } from "./CommandPalette";
import { useSession } from "@/state/session";
import { useRunStore } from "@/state/runStore";
import { playback } from "@/state/playback";

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}
function Harness() {
  return (
    <ThemeProvider>
      <CommandPalette />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </ThemeProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useRunStore.getState().reset();
  useSession.setState({ token: "t", role: "officer", sub: "o" });
});

describe("palette logic", () => {
  it("filters by every word and wraps the active index", () => {
    const items = [
      { id: "a", label: "Evidence Ledger", group: "Go to" as const },
      { id: "b", label: "Insights", group: "Go to" as const, keywords: "benchmark" },
    ];
    expect(filterPalette(items, "evid").map((i) => i.id)).toEqual(["a"]);
    expect(filterPalette(items, "bench ins").map((i) => i.id)).toEqual(["b"]);
    expect(filterPalette(items, "")).toHaveLength(2);
    expect(nextIndex(0, -1, 3)).toBe(2);
    expect(nextIndex(2, 1, 3)).toBe(0);
    expect(nextIndex(0, 1, 0)).toBe(0);
  });
});

describe("CommandPalette", () => {
  it("opens with Ctrl+K, jumps to a page with Enter", async () => {
    const user = userEvent.setup();
    renderApp(<Harness />);
    expect(screen.queryByTestId("palette")).not.toBeInTheDocument();
    await user.keyboard("{Control>}k{/Control}");
    const input = await screen.findByTestId("palette-input");
    await user.type(input, "evid");
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("where")).toHaveTextContent("/evidence");
    expect(screen.queryByTestId("palette")).not.toBeInTheDocument();
  });

  it("offers Start the demo only to officer/admin and runs it", async () => {
    const user = userEvent.setup();
    renderApp(<Harness />);
    await user.keyboard("{Control>}k{/Control}");
    await user.type(await screen.findByTestId("palette-input"), "start");
    await user.keyboard("{Enter}");
    expect(playback.start).toHaveBeenCalled();
    useSession.setState({ role: "citizen" });
    await user.keyboard("{Control>}k{/Control}");
    await user.type(await screen.findByTestId("palette-input"), "start");
    expect(screen.getByText(/Nothing matches/)).toBeInTheDocument();
  });

  it("toggles the theme", async () => {
    localStorage.setItem("ssi.theme", "dark"); // a stored choice wins over the system default
    const user = userEvent.setup();
    renderApp(<Harness />);
    await user.keyboard("{Control>}k{/Control}");
    await user.type(await screen.findByTestId("palette-input"), "light theme");
    await user.keyboard("{Enter}");
    expect(document.documentElement.dataset.theme).toBe("light");
    localStorage.removeItem("ssi.theme");
  });

  it("without a stored choice the theme follows the system and defaults to light (finding 10)", () => {
    localStorage.removeItem("ssi.theme");
    renderApp(<Harness />);
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});

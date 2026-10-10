import { describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderApp } from "@/test-utils";
import About, { declaredLibraries } from "./About";
import { ARCH_EDGES, ARCH_NODES, edgesOf, REAL_VS_SIMULATED } from "@/lib/about/architecture";
import pkg from "../../package.json";

describe("architecture data", () => {
  it("every edge connects existing nodes and every node has an edge", () => {
    const ids = new Set(ARCH_NODES.map((n) => n.id));
    for (const e of ARCH_EDGES) {
      expect(ids.has(e.from), e.id).toBe(true);
      expect(ids.has(e.to), e.id).toBe(true);
    }
    for (const n of ARCH_NODES) expect(edgesOf(n.id).length, n.id).toBeGreaterThan(0);
  });
  it("blocks stay inside the viewBox and do not overlap", () => {
    for (const n of ARCH_NODES) {
      expect(n.x).toBeGreaterThanOrEqual(0);
      expect(n.x + n.w).toBeLessThanOrEqual(900);
      expect(n.y + n.h).toBeLessThanOrEqual(450);
    }
    for (const a of ARCH_NODES)
      for (const b of ARCH_NODES) {
        if (a.id >= b.id) continue;
        const overlap = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
        expect(overlap, `${a.id} vs ${b.id}`).toBe(false);
      }
  });
});

describe("About page", () => {
  it("hover highlights a block's flows and describes it", () => {
    renderApp(<About />);
    const node = document.querySelector('[data-node="hub"]') as SVGGElement;
    fireEvent.pointerEnter(node);
    expect(screen.getByTestId("arch-info")).toHaveTextContent(/privacy-preserving antibody/);
    const hot = [...document.querySelectorAll('[data-hot="1"]')].map((e) => e.getAttribute("data-edge"));
    expect(hot.sort()).toEqual(edgesOf("hub").map((e) => e.id).sort());
  });
  it("lists the real-vs-simulated table, limits, run command and credits from package.json", () => {
    renderApp(<About />);
    expect(screen.getByTestId("real-table").querySelectorAll("tbody tr")).toHaveLength(REAL_VS_SIMULATED.length);
    expect(screen.getByTestId("run-block")).toHaveTextContent("make demo");
    expect(screen.getByTestId("limits-list")).toHaveTextContent(/constructed hard case/);
    const libs = declaredLibraries();
    expect(libs.length).toBe(Object.keys(pkg.dependencies).length);
    expect(screen.getByTestId("credits")).toHaveTextContent("framer-motion");
  });
});

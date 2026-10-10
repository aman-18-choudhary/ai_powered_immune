import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

const hasDom = typeof window !== "undefined";
afterEach(() => {
  if (hasDom) cleanup();
});

// jsdom lacks these; components that observe size or media queries rely on them.
if (hasDom && !("ResizeObserver" in globalThis)) {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
}
if (hasDom && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
if (hasDom && !Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

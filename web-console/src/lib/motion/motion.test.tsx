import { describe, expect, it } from "vitest";
import { act, render, renderHook } from "@testing-library/react";
import { MotionPrefsProvider, useMotionPrefs } from "./prefs";
import { AnimatedNumber } from "./AnimatedNumber";
import { useTypedStream } from "./useTypedStream";

describe("reduced motion", () => {
  it("in-app toggle sets html[data-motion] and persists", () => {
    const { result } = renderHook(() => useMotionPrefs(), { wrapper: MotionPrefsProvider });
    expect(result.current.reduced).toBe(false);
    act(() => result.current.setReduced(true));
    expect(result.current.reduced).toBe(true);
    expect(document.documentElement.dataset.motion).toBe("reduced");
    expect(localStorage.getItem("ssi.motion")).toBe("reduced");
    act(() => result.current.setReduced(false));
    expect(document.documentElement.dataset.motion).toBe("full");
  });

  it("AnimatedNumber renders the value immediately under reduced motion", () => {
    localStorage.setItem("ssi.motion", "reduced");
    const { container, rerender } = render(
      <MotionPrefsProvider>
        <AnimatedNumber value={5} />
      </MotionPrefsProvider>,
    );
    expect(container.textContent).toBe("5");
    rerender(
      <MotionPrefsProvider>
        <AnimatedNumber value={42} />
      </MotionPrefsProvider>,
    );
    expect(container.textContent).toBe("42");
    localStorage.removeItem("ssi.motion");
  });
});

describe("useTypedStream", () => {
  it("reveals everything at once when instant", () => {
    const { result } = renderHook(() => useTypedStream([{ id: 1, text: "hello" }], true));
    expect(result.current[1]).toBe(5);
  });
});

import { useCallback, useState } from "react";

/** Minimal fixed-row-height windowing: returns the slice to render and spacer heights. */
export function useVirtualList(count: number, rowHeight: number, viewportHeight: number, overscan = 4) {
  const [scrollTop, setScrollTop] = useState(0);
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const end = Math.min(count, Math.ceil((scrollTop + viewportHeight) / rowHeight) + overscan);
  const onScroll = useCallback((e: { currentTarget: { scrollTop: number } }) => setScrollTop(e.currentTarget.scrollTop), []);
  return { start, end, onScroll, padTop: start * rowHeight, padBottom: Math.max(0, (count - end) * rowHeight) };
}

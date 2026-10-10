import { useEffect, useRef, useState } from "react";

export interface TypedItem {
  id: number;
  text: string;
}

/**
 * Reveals items in order, a few characters per tick (typing effect). `instant` shows everything
 * at once (reduced motion). Returns the number of characters revealed per item id.
 */
export function useTypedStream(items: TypedItem[], instant: boolean, charsPerTick = 3, tickMs = 28): Record<number, number> {
  const [revealed, setRevealed] = useState<Record<number, number>>({});
  const itemsRef = useRef(items);
  itemsRef.current = items;

  useEffect(() => {
    if (instant) return;
    const h = setInterval(() => {
      setRevealed((prev) => {
        const its = itemsRef.current;
        let backlog = 0;
        for (const it of its) backlog += Math.max(0, it.text.length - (prev[it.id] ?? 0));
        if (backlog === 0) return prev;
        // Adaptive: a large backlog (late joiner, seek) is cleared in about a second.
        let budget = backlog > 320 ? Math.max(charsPerTick, Math.ceil(backlog / 30)) : charsPerTick;
        const next = { ...prev };
        for (const it of its) {
          const have = next[it.id] ?? 0;
          if (have >= it.text.length) continue;
          const give = Math.min(budget, it.text.length - have);
          next[it.id] = have + give;
          budget -= give;
          if (budget <= 0) break;
        }
        return next;
      });
    }, tickMs);
    return () => clearInterval(h);
  }, [instant, charsPerTick, tickMs]);

  if (instant) {
    const all: Record<number, number> = {};
    for (const it of items) all[it.id] = it.text.length;
    return all;
  }
  return revealed;
}

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
        for (const it of itemsRef.current) {
          const have = prev[it.id] ?? 0;
          if (have < it.text.length) {
            return { ...prev, [it.id]: Math.min(it.text.length, have + charsPerTick) };
          }
        }
        return prev;
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

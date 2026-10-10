export interface PaletteItem {
  id: string;
  label: string;
  hint?: string;
  group: "Go to" | "Demo" | "Preferences";
  keywords?: string;
}

/** Case-insensitive match over label, hint and keywords: every query word must appear. */
export function filterPalette(items: readonly PaletteItem[], query: string): PaletteItem[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...items];
  return items.filter((i) => {
    const hay = `${i.label} ${i.hint ?? ""} ${i.keywords ?? ""}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

export function nextIndex(cur: number, dir: 1 | -1, len: number): number {
  if (len === 0) return 0;
  return (cur + dir + len) % len;
}

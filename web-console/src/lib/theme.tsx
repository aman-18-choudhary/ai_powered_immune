import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Theme = "dark" | "light";
const Ctx = createContext<{ theme: Theme; toggle: () => void }>({ theme: "dark", toggle: () => {} });

/** A stored choice wins; otherwise the system preference, light when there is none. */
function initial(): Theme {
  try {
    const saved = localStorage.getItem("ssi.theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* storage unavailable: fall through to the system preference */
  }
  try {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(initial);
  useEffect(() => {
    const r = document.documentElement;
    r.classList.toggle("dark", theme === "dark");
    r.classList.toggle("light", theme === "light");
    r.dataset.theme = theme;
  }, [theme]);
  const toggle = useCallback(() => {
    setTheme((t) => {
      const n: Theme = t === "dark" ? "light" : "dark";
      try {
        localStorage.setItem("ssi.theme", n);
      } catch {
        /* ignore */
      }
      return n;
    });
  }, []);
  const v = useMemo(() => ({ theme, toggle }), [theme, toggle]);
  return <Ctx.Provider value={v}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);

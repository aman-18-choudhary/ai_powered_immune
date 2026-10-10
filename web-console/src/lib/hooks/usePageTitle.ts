import { useEffect } from "react";

/** Sets the document title for a route ("Page · ScamShield Immune"). */
export function usePageTitle(title: string): void {
  useEffect(() => {
    const prev = document.title;
    document.title = `${title} · ScamShield Immune`;
    return () => {
      document.title = prev;
    };
  }, [title]);
}

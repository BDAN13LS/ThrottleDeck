import { useEffect, useState } from "react";

/** Below this width the layout switches to the compact composition. */
export const COMPACT_MAX_WIDTH = 900;

/**
 * True on a compact window. Resize-driven rather than CSS-only because the
 * compact composition changes the table structure, not just the type scale.
 */
export function useCompact(maxWidth = COMPACT_MAX_WIDTH): boolean {
  const [compact, setCompact] = useState<boolean>(() => readCompact(maxWidth));

  useEffect(() => {
    const update = (): void => {
      setCompact(readCompact(maxWidth));
    };
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("resize", update);
    };
  }, [maxWidth]);

  return compact;
}

function readCompact(maxWidth: number): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return window.innerWidth < maxWidth;
}

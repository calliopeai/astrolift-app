"use client";

import { useCallback, useEffect, useState } from "react";

export type ViewMode = "card" | "list";

/**
 * Persists a card/list view preference per screen key in localStorage.
 *
 * @param key     localStorage key, e.g. "astrolift_view_apps"
 * @param defaultMode  The view mode to use when no preference is saved
 */
export function useViewToggle(
  key: string,
  defaultMode: ViewMode = "card"
): [ViewMode, (mode: ViewMode) => void] {
  const [mode, setModeState] = useState<ViewMode>(defaultMode);

  // Hydrate from localStorage on mount (client-only).
  useEffect(() => {
    try {
      const saved = localStorage.getItem(key);
      if (saved === "card" || saved === "list") {
        setModeState(saved);
      }
    } catch {
      // localStorage unavailable (SSR, private mode, etc.) — use default.
    }
  }, [key]);

  const setMode = useCallback(
    (next: ViewMode) => {
      setModeState(next);
      try {
        localStorage.setItem(key, next);
      } catch {
        // ignore write failures
      }
    },
    [key]
  );

  return [mode, setMode];
}

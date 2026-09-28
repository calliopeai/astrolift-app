"use client";

import * as React from "react";

/**
 * One rail's collapsed state, remembered per person in this browser
 * (spec 44 §4.2). `defaultCollapsed` applies until they choose; the projects
 * rail passes "collapsed below 1280px".
 */
export function useRailState(storageKey: string, defaultCollapsed: () => boolean) {
  const [collapsed, setCollapsed] = React.useState<boolean>(() => {
    try {
      const saved = window.localStorage.getItem(storageKey);
      if (saved === "1") return true;
      if (saved === "0") return false;
    } catch {
      // Storage unavailable: fall back to the default.
    }
    return defaultCollapsed();
  });

  const set = React.useCallback(
    (next: boolean) => {
      setCollapsed(next);
      try {
        window.localStorage.setItem(storageKey, next ? "1" : "0");
      } catch {
        // Not remembered this time; the rail still collapses.
      }
    },
    [storageKey]
  );

  return [collapsed, set] as const;
}

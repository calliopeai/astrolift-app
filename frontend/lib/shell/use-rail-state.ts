"use client";

import * as React from "react";

/**
 * One rail's collapsed state, remembered per person in this browser
 * (spec 44 §4.2). `defaultCollapsed` applies until they choose; the projects
 * rail passes "narrower than 1280px".
 *
 * An external store over localStorage rather than state set in an effect: the
 * server renders the rails expanded, the client applies the saved choice (or
 * the default) without a hydration mismatch, and a change in one tab reaches
 * the others through the storage event.
 */
const CHANGED = "astrolift:rail-state";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

export function useRailState(storageKey: string, defaultCollapsed: () => boolean) {
  const collapsed = React.useSyncExternalStore(
    subscribe,
    () => {
      const saved = read(storageKey);
      return saved === "1" ? true : saved === "0" ? false : defaultCollapsed();
    },
    () => false
  );

  const set = React.useCallback(
    (next: boolean) => {
      try {
        window.localStorage.setItem(storageKey, next ? "1" : "0");
      } catch {
        // Not remembered; the change below still applies for this page.
      }
      window.dispatchEvent(new Event(CHANGED));
    },
    [storageKey]
  );

  return [collapsed, set] as const;
}

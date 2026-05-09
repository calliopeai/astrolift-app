"use client";

import * as React from "react";

type Priority = "polite" | "assertive";

interface LiveRegionContextValue {
  announce: (message: string, priority?: Priority) => void;
}

const LiveRegionContext = React.createContext<LiveRegionContextValue | null>(null);

/**
 * Mounts two ARIA live regions (polite + assertive) and exposes an
 * `announce(message, priority?)` function via context.
 *
 * Mount once near the top of the (app) layout. Sonner toasts already
 * announce themselves, so this is for state changes that don't surface
 * a toast — filter applied, sort changed, async data ready, etc.
 */
export function LiveRegionProvider({ children }: { children: React.ReactNode }) {
  const [polite, setPolite] = React.useState("");
  const [assertive, setAssertive] = React.useState("");

  // Clear-then-set with a 50ms gap so screen readers re-announce when
  // the same message fires twice in a row.
  const announce = React.useCallback((message: string, priority: Priority = "polite") => {
    if (priority === "assertive") {
      setAssertive("");
      window.setTimeout(() => setAssertive(message), 50);
    } else {
      setPolite("");
      window.setTimeout(() => setPolite(message), 50);
    }
  }, []);

  return (
    <LiveRegionContext.Provider value={{ announce }}>
      {children}
      <div aria-live="polite" aria-atomic="true" role="status" className="sr-only">
        {polite}
      </div>
      <div aria-live="assertive" aria-atomic="true" role="alert" className="sr-only">
        {assertive}
      </div>
    </LiveRegionContext.Provider>
  );
}

/**
 * Returns `announce(message, priority?)`. Defaults to polite.
 *
 *   const { announce } = useAnnounce();
 *   announce("Filter applied: status = active");
 *   announce("Failed to load deployments", "assertive");
 */
export function useAnnounce() {
  const ctx = React.useContext(LiveRegionContext);
  if (!ctx) {
    throw new Error("useAnnounce must be used inside <LiveRegionProvider>");
  }
  return ctx;
}

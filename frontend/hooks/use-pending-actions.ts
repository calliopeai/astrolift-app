"use client";

import * as React from "react";

/** Admit each action once, including repeated calls before React renders. */
export function usePendingActions() {
  const active = React.useRef(new Set<string>());
  const [pending, setPending] = React.useState<ReadonlySet<string>>(new Set());
  function begin(key: string) {
    if (active.current.has(key)) return false;
    active.current.add(key);
    setPending(new Set(active.current));
    return true;
  }
  function finish(key: string) {
    active.current.delete(key);
    setPending(new Set(active.current));
  }
  return { pending, begin, finish };
}

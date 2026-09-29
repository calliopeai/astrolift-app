"use client";

/**
 * What a RunPage's hook owes it (components/run/RunPage.tsx): a clock that
 * ticks while the run is live, and the log download. Shared by the
 * deployment, job run and command run hooks.
 */
import * as React from "react";

import type { LogLine } from "@/components/run/LogView";

/** `Date.now()`, re-read every `intervalMs` while `live`; frozen otherwise. */
export function useNow(live: boolean, intervalMs = 1000): number {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [live, intervalMs]);
  return now;
}

/** Milliseconds between two instants, or null when either is missing. */
export function spanMs(
  from: string | null | undefined,
  to: string | number | null | undefined
): number | null {
  if (!from || to == null) return null;
  const a = Date.parse(from);
  const b = typeof to === "number" ? to : Date.parse(to);
  const d = b - a;
  return Number.isFinite(d) && d >= 0 ? d : null;
}

/** The log as the plain text a download holds: one `ISO LEVEL message` per line. */
export function logText(lines: LogLine[]): string {
  return lines
    .map((l) => {
      const ts = new Date(l.ts);
      const at = Number.isNaN(ts.getTime()) ? String(l.ts) : ts.toISOString();
      return l.level ? `${at} ${l.level.toUpperCase()} ${l.message}` : `${at} ${l.message}`;
    })
    .join("\n");
}

/** Save `text` as `filename` in the browser. */
export function downloadText(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

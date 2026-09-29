/**
 * App log lines for the shared LogView (spec 44 §5.5): the subscription's
 * structured lines mapped onto `LogLine`, the level and text filters the
 * Logs and Metrics sections put over them, and the download file.
 */

import {
  classifyLogLevel,
  type LogLevel,
  type LogLevelFilter,
} from "@/components/observability/LogViewer";
import type { LogLine } from "@/components/run/LogView";
import type { AstroliftAppLogLine } from "@/graphql/lifecycle/lifecycle.types";

export type { LogLevelFilter };

export const LOG_LEVEL_FILTERS: readonly LogLevelFilter[] = [
  "all",
  "error",
  "warn",
  "info",
  "debug",
  "other",
];

// One mapped object per source line, so LogView's memoised chunks see the
// same identity across renders and an append re-renders only the tail.
const plain = new WeakMap<AstroliftAppLogLine, LogLine>();
const withPod = new WeakMap<AstroliftAppLogLine, LogLine>();

/**
 * `withPodName` prefixes the pod (and container) to each message, for the
 * all-replicas and historical views where lines come from many pods.
 */
export function toLogLines(
  lines: AstroliftAppLogLine[],
  { withPodName = false }: { withPodName?: boolean } = {}
): LogLine[] {
  const cache = withPodName ? withPod : plain;
  return lines.map((line) => {
    const hit = cache.get(line);
    if (hit) return hit;
    const source = line.container ? `${line.podName}/${line.container}` : line.podName;
    const mapped: LogLine = {
      ts: line.timestamp,
      message: withPodName ? `${source} ${line.message}` : line.message,
      level: classifyLogLevel(line.message),
    };
    cache.set(line, mapped);
    return mapped;
  });
}

/** Lines at `level` whose message contains `query`, case-insensitive. */
export function filterLogLines(lines: LogLine[], level: LogLevelFilter, query: string): LogLine[] {
  const q = query.trim().toLowerCase();
  if (level === "all" && !q) return lines;
  return lines.filter(
    (l) =>
      (level === "all" || (l.level ?? classifyLogLevel(l.message)) === level) &&
      (!q || l.message.toLowerCase().includes(q))
  );
}

/** Per-level counts for the level picker ("Error · 4"). */
export function countLevels(lines: LogLine[]): Record<LogLevel, number> {
  const counts: Record<LogLevel, number> = { error: 0, warn: 0, info: 0, debug: 0, other: 0 };
  for (const l of lines) counts[l.level ?? classifyLogLevel(l.message)] += 1;
  return counts;
}

/** `<slug>-<env>-<pod>-<iso>.log`, each part reduced to filename-safe characters. */
export function logFilename(
  parts: { value: string | null | undefined; fallback: string }[],
  now: Date = new Date()
): string {
  const safe = ({ value, fallback }: { value: string | null | undefined; fallback: string }) =>
    (value ?? "").replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || fallback;
  const iso = now.toISOString().replace(/[:.]/g, "-");
  return [...parts.map(safe), iso].join("-") + ".log";
}

/** The file body: one `<ISO time> <message>` per line. */
export function formatLogFile(lines: LogLine[]): string {
  return (
    lines
      .map((l) => {
        const d = new Date(l.ts);
        const ts = Number.isNaN(d.getTime()) ? String(l.ts) : d.toISOString();
        return `${ts} ${l.message}`;
      })
      .join("\n") + "\n"
  );
}

/** Hand the browser a text file. Call from a hook, never at render. */
export function downloadTextFile(filename: string, text: string): void {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Deferred so the browser has taken the navigation before the URL goes.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

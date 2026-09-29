/**
 * An agent run's log tail on the shared LogView (spec 44 §5.5).
 * `agentTaskLogs` returns plain strings; the pod writes each as
 * `<RFC 3339 time> <LEVEL> <message>`, so the time and level are read off
 * the front and the rest is the message. A line without a leading time
 * keeps its whole text and no time. Pure.
 */
import type { LogLevel, LogLine } from "@/components/run/LogView";

const LINE = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\s+(.*)$/;
const LEVEL = /^(ERROR|ERR|WARN(?:ING)?|WRN|INFO|INF|DEBUG|DBG|TRACE)\b\s*(.*)$/i;

const LEVELS: Record<string, LogLevel> = {
  error: "error",
  err: "error",
  warn: "warn",
  warning: "warn",
  wrn: "warn",
  info: "info",
  inf: "info",
  debug: "debug",
  dbg: "debug",
  trace: "debug",
};

export function agentLogLine(raw: string): LogLine {
  const timed = LINE.exec(raw);
  if (!timed) return { ts: "", message: raw };
  const rest = timed[2] ?? "";
  const levelled = LEVEL.exec(rest);
  return levelled
    ? {
        ts: timed[1]!,
        message: levelled[2] ?? "",
        level: LEVELS[levelled[1]!.toLowerCase()],
      }
    : { ts: timed[1]!, message: rest };
}

export function agentLogLines(lines: string[]): LogLine[] {
  return lines.map(agentLogLine);
}

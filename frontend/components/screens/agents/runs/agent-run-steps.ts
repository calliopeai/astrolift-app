/**
 * An agent run on the run archetype (spec 44 §5.5), pure: its lifecycle
 * and the tool calls, gates and signals it made as Timeline steps, and its
 * log tail as LogView lines.
 */
import type { LogLine } from "@/components/run/LogView";
import type { StepState, TimelineStep } from "@/components/run/Timeline";
import type { AstroliftAgentInteraction } from "@/graphql/agents/agents.types";

/** Canonical task statuses and terminal spellings accepted by the run surfaces. */
const TERMINAL_STATUSES = new Set([
  "completed",
  "succeeded",
  "failed",
  "timed_out",
  "cancelled",
  "canceled",
]);

export const isTerminalAgentTask = (status: string) => TERMINAL_STATUSES.has(status.toLowerCase());

/** Past this many interaction steps the Timeline keeps the newest; the map has them all. */
export const MAX_INTERACTION_STEPS = 40;

const KIND_LABEL: Record<string, string> = {
  tool_call: "tool call",
  gate: "gate",
  signal: "signal",
};

const ERROR = new Set(["error", "errored", "failed", "failure", "timed_out"]);
const WAITING = new Set(["pending", "waiting", "running", "in_progress"]);

export interface AgentRunTimes {
  status: string;
  createdAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
}

function span(
  from: string | null | undefined,
  to: string | number | null | undefined
): number | null {
  if (!from || to == null) return null;
  const d = (typeof to === "number" ? to : Date.parse(to)) - Date.parse(from);
  return Number.isFinite(d) && d >= 0 ? d : null;
}

function interactionState(status: string, terminal: boolean): StepState {
  const s = status.toLowerCase();
  if (ERROR.has(s)) return "failed";
  if (WAITING.has(s)) return terminal ? "skipped" : "running";
  return "ok";
}

/**
 * The run's steps, oldest first: waiting for a pod, the run itself, then
 * each tool call, gate and signal. Control API calls (heartbeats) stay on
 * the interaction map; a burst of the same call in a row is one step with a
 * count, and past MAX_INTERACTION_STEPS the oldest fold into one line.
 */
export function agentRunSteps(
  task: AgentRunTimes,
  interactions: AstroliftAgentInteraction[],
  now: number
): TimelineStep[] {
  const s = task.status.toLowerCase();
  const terminal = isTerminalAgentTask(s);
  const started = Boolean(task.startedAt);
  const failed = s === "failed" || s === "timed_out";

  const steps: TimelineStep[] = [
    {
      id: "queued",
      name: "queued",
      state: started ? "ok" : terminal ? (failed ? "failed" : "skipped") : "running",
      durationMs: span(
        task.createdAt,
        task.startedAt ?? (terminal ? (task.finishedAt ?? null) : now)
      ),
    },
    {
      id: "run",
      name: terminal ? s.replace(/_/g, " ") : "running",
      state: !started
        ? terminal
          ? "skipped"
          : "pending"
        : !terminal
          ? "running"
          : failed
            ? "failed"
            : s === "cancelled" || s === "canceled"
              ? "skipped"
              : "ok",
      durationMs: started ? span(task.startedAt, task.finishedAt ?? (terminal ? null : now)) : null,
    },
  ];

  const calls = interactions
    .filter((i) => i.kind !== "control_api")
    .sort((a, b) => Date.parse(a.occurredAt) - Date.parse(b.occurredAt));

  const grouped: { first: AstroliftAgentInteraction; count: number }[] = [];
  for (const i of calls) {
    const last = grouped.at(-1);
    if (
      last &&
      last.first.kind === i.kind &&
      last.first.name === i.name &&
      last.first.status === i.status
    ) {
      last.count += 1;
    } else {
      grouped.push({ first: i, count: 1 });
    }
  }

  const hidden = Math.max(0, grouped.length - MAX_INTERACTION_STEPS);
  if (hidden > 0) {
    const n = grouped.slice(0, hidden).reduce((sum, g) => sum + g.count, 0);
    steps.push({
      id: "earlier",
      name: `${n} earlier ${n === 1 ? "call" : "calls"}`,
      state: "ok",
      detail: "The interaction map has every one.",
    });
  }
  for (const { first, count } of grouped.slice(hidden)) {
    const kind = KIND_LABEL[first.kind] ?? first.kind.replace(/_/g, " ");
    steps.push({
      id: first.id,
      name: first.name,
      state: interactionState(first.status, terminal),
      detail: count > 1 ? `${kind} · ×${count}` : kind,
    });
  }
  return steps;
}

const STAMP = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(.*)$/;

/**
 * The log tail as lines. A line that starts with an ISO instant keeps it;
 * any other takes `fallback` (the run's start), as a run's captured output
 * does elsewhere.
 */
export function agentLogLines(logs: string[], fallback: string): LogLine[] {
  return logs.map((raw) => {
    const m = STAMP.exec(raw);
    return m ? { ts: m[1], message: m[2] } : { ts: fallback, message: raw };
  });
}

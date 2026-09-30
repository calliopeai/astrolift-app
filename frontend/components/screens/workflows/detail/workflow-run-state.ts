import type { TieredWorkflowRun } from "@/graphql/workflows/tiered.types";

// triggerKind is a free String! on the schema — label the known kinds and
// title-case anything else so unknown values degrade gracefully.
const TRIGGER_LABELS: Record<string, string> = {
  manual: "Manual",
  schedule: "Schedule",
  webhook: "Webhook",
};

export function formatTriggerKind(triggerKind: string): string {
  const key = triggerKind.toLowerCase();
  if (TRIGGER_LABELS[key]) return TRIGGER_LABELS[key];
  if (!triggerKind) return "—";
  return triggerKind.charAt(0).toUpperCase() + triggerKind.slice(1).toLowerCase();
}

/** Newest run by startedAt (the detail query doesn't guarantee order). */
export function latestRun(runs: TieredWorkflowRun[]): TieredWorkflowRun | null {
  if (runs.length === 0) return null;
  return runs.reduce((newest, run) => (run.startedAt > newest.startedAt ? run : newest));
}

/**
 * Is a run finished? `isCompleted` is authoritative; the currentState string
 * match is a backstop for terminal states (failed/cancelled/terminated) that
 * may report before the flag flips. Drives live-DAG poll gating (#1090) — a
 * null run (nothing selected) counts as terminal so nothing polls.
 */
export function isRunTerminal(run: TieredWorkflowRun | null): boolean {
  if (!run) return true;
  if (run.isCompleted) return true;
  const s = run.currentState.toLowerCase();
  return (
    s === "expired" ||
    s.includes("fail") ||
    s.includes("error") ||
    s.includes("terminat") ||
    s.includes("cancel") ||
    s.includes("complet") ||
    s.includes("succe") ||
    s.includes("done")
  );
}

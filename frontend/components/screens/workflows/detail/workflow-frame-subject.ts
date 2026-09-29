import type {
  ConfiguredWorkflowWithRuns,
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";

import { isRunTerminal, latestRun } from "./workflow-run-state";

/**
 * What the workflow frame's header shows, the same shape for both kinds a
 * `/workflows/[slug]` can be: a configured workflow (tier 2, wrapping a
 * definition), or a definition opened directly (a repository workflow, a
 * template, or an org-authored pipeline). Pure.
 */
export interface WorkflowFrameSubject {
  kind: "configured" | "definition";
  /** The guid: Copy ID. */
  id: string;
  slug: string;
  name: string;
  isEnabled: boolean;
  patternKind: string;
  /** Null until the definition behind a configured workflow has loaded. */
  stageCount: number | null;
  projectSlug: string | null;
  lastRun: WorkflowFrameRun | null;
}

export interface WorkflowFrameRun {
  guid: string;
  state: string;
  startedAt: string | null;
  /** Still running (not in a terminal state). */
  live: boolean;
  /** The engine identity its stage executions key on; null before it reached the engine. */
  temporalWorkflowId: string | null;
  temporalRunId: string | null;
}

type Dot = "ok" | "warn" | "error" | "muted" | "pending";

const DEFINITION_TERMINAL = new Set([
  "completed",
  "failed",
  "cancelled",
  "terminated",
  "timed_out",
]);

/** A run state that means it failed (the engine's and the tier-2 row's spellings). */
export function isFailedState(state: string): boolean {
  const s = state.toLowerCase();
  return s.includes("fail") || s.includes("error") || s.includes("timed_out") || s === "timeout";
}

/** The header's one status: running, disabled, never run, or how the last run ended. */
export function workflowStatus(subject: WorkflowFrameSubject): { dot: Dot; label: string } {
  const run = subject.lastRun;
  if (run?.live) return { dot: "pending", label: "Running" };
  if (!subject.isEnabled) return { dot: "muted", label: "Disabled" };
  if (!run) return { dot: "muted", label: "Never run" };
  if (isFailedState(run.state)) return { dot: "error", label: "Last run failed" };
  const s = run.state.toLowerCase();
  if (s.includes("cancel") || s.includes("terminat"))
    return { dot: "muted", label: "Last run cancelled" };
  return { dot: "ok", label: "Last run succeeded" };
}

/** `fan_out_aggregate` reads `Fan out aggregate`. */
export function patternLabel(patternKind: string): string {
  const words = patternKind.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1).toLowerCase() : "Pipeline";
}

/** A configured workflow, with the definition behind it once that loads. */
export function configuredSubject(
  workflow: ConfiguredWorkflowWithRuns,
  definition: WorkflowDefinitionSummary | null
): WorkflowFrameSubject {
  const latest = latestRun(workflow.runs);
  return {
    kind: "configured",
    id: workflow.guid,
    slug: workflow.slug,
    name: workflow.name,
    isEnabled: workflow.isEnabled,
    patternKind: workflow.patternKind || definition?.patternKind || "",
    stageCount: definition?.stageCount ?? null,
    projectSlug: definition?.projectSlug || null,
    lastRun: latest
      ? {
          guid: latest.guid,
          state: latest.currentState,
          startedAt: latest.startedAt,
          live: !isRunTerminal(latest),
          temporalWorkflowId: latest.temporalWorkflowId,
          temporalRunId: latest.temporalRunId,
        }
      : null,
  };
}

/** A definition opened directly, with its runs (any order; the newest is picked). */
export function definitionSubject(
  definition: WorkflowDefinitionSummary,
  runs: WorkflowDefinitionRun[]
): WorkflowFrameSubject {
  const latest =
    runs
      .filter((r) => r.definitionGuid === definition.guid)
      .sort((a, b) => ((a.startedAt ?? "") < (b.startedAt ?? "") ? 1 : -1))[0] ?? null;
  return {
    kind: "definition",
    id: definition.guid,
    slug: definition.slug,
    name: definition.name,
    isEnabled: definition.isEnabled,
    patternKind: definition.patternKind,
    stageCount: definition.stageCount,
    projectSlug: definition.projectSlug || null,
    lastRun: latest
      ? {
          guid: latest.guid,
          state: latest.status,
          startedAt: latest.startedAt,
          live: !DEFINITION_TERMINAL.has(latest.status.toLowerCase()),
          temporalWorkflowId: latest.temporalWorkflowId || null,
          temporalRunId: latest.temporalRunId,
        }
      : null,
  };
}

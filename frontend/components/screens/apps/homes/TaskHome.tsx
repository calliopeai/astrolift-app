"use client";

import { HistoryIcon, ListChecksIcon } from "lucide-react";

import { Panel, PanelGrid } from "@/components/panel/Panel";
import { Timeline, type StepState, type TimelineStep } from "@/components/run/Timeline";
import { StatusDot } from "@/components/StatusDot";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";
import { formatRelativeAge } from "@/lib/format";

import { formatDuration, runStatusDot, titleCaseStatus } from "./run-status";
import type { useTaskRuns } from "./use-task-runs";

export type TaskHomeScreenProps = ReturnType<typeof useTaskRuns> & {
  /** The app's name; the frame above shows it. */
  name: string;
};

// The one-off execution lifecycle. `stageIndex` maps a run status onto how
// far it got, so the timeline reflects the latest run.
const STAGES = ["Queued", "Provisioning", "Running", "Completed"] as const;
function stageIndex(status: string): number {
  switch (status.toLowerCase()) {
    case "pending":
    case "queued":
      return 0;
    case "provisioning":
      return 1;
    case "running":
    case "active":
      return 2;
    case "succeeded":
    case "completed":
    case "failed":
    case "error":
    case "timed_out":
      return 3;
    default:
      return -1;
  }
}
function isFailed(status: string): boolean {
  return ["failed", "error", "timed_out"].includes(status.toLowerCase());
}

/** The latest run's lifecycle as the shared run Timeline's steps. */
export function taskStages(latest: AstroliftTaskRun | null): TimelineStep[] {
  const reached = latest ? stageIndex(latest.status) : -1;
  const failed = latest ? isFailed(latest.status) : false;
  return STAGES.map((stage, i) => {
    let state: StepState = "pending";
    if (i === 3 && reached === 3) state = failed ? "failed" : "ok";
    else if (reached > i) state = "ok";
    else if (reached === i) state = "running";
    return {
      id: stage,
      name: failed && i === 3 ? "Failed" : stage,
      state,
      durationMs:
        i === 3 && reached === 3 && latest?.durationSeconds != null
          ? latest.durationSeconds * 1000
          : null,
    };
  });
}

/**
 * The Overview for a **task**: a one-off run. The latest run's lifecycle on
 * the shared run Timeline, a failure's exit code first, and the prior runs
 * below. Real data from TaskRun.
 */
export function TaskHomeScreen({ runs, loading }: TaskHomeScreenProps) {
  const latest = runs[0] ?? null;
  const failed = latest ? isFailed(latest.status) : false;

  return (
    <PanelGrid>
      <Panel
        title={latest ? "Latest run" : "Execution"}
        icon={<ListChecksIcon className="size-4" />}
        span={4}
        loading={loading && runs.length === 0}
        failure={
          latest && failed
            ? {
                title: `Run ${titleCaseStatus(latest.status).toLowerCase()}`,
                reason:
                  latest.exitCode != null
                    ? `Exited with code ${latest.exitCode}${latest.k8sJobName ? ` · ${latest.k8sJobName}` : ""}`
                    : latest.k8sJobName || "No reason recorded",
              }
            : null
        }
      >
        <Timeline steps={taskStages(latest)} label="Run stages" />
        {!latest && (
          <p className="text-muted-foreground mt-3 text-xs">
            No run recorded yet. A one-off task moves through these stages each time it runs.
          </p>
        )}
      </Panel>

      <Panel
        title="Run history"
        icon={<HistoryIcon className="size-4" />}
        span={8}
        loading={loading && runs.length === 0}
        flush
        empty={
          runs.length === 0
            ? {
                icon: <ListChecksIcon className="size-5" />,
                title: "No runs yet",
                description: "Each one-off execution of this task appears here.",
              }
            : null
        }
      >
        <ul className="divide-y">
          {runs.map((r) => (
            <li key={r.id} className="flex min-w-0 items-center gap-3 px-4 py-2.5 text-sm">
              <StatusDot status={runStatusDot(r.status)} />
              <span className="w-24 shrink-0">{titleCaseStatus(r.status)}</span>
              <span className="text-muted-foreground min-w-0 flex-1 truncate font-mono text-xs">
                {r.startedAt ? formatRelativeAge(r.startedAt) : "not started"}
              </span>
              <span className="text-muted-foreground shrink-0 font-mono text-xs tabular-nums">
                {formatDuration(r.durationSeconds)}
              </span>
            </li>
          ))}
        </ul>
      </Panel>
    </PanelGrid>
  );
}

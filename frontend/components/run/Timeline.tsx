"use client";

/**
 * Timeline: a run's steps in order (spec 44 §5.5, §7), for deployments,
 * agent runs, workflow runs, jobs, tasks and functions alike. Each row is a
 * state dot, the step's name, its duration and an optional detail line
 * (a failure reason, an attempt count). Selecting a step is optional: pass
 * `onSelect` and the rows become buttons (the run page filters its log by it).
 *
 *   <Timeline steps={[{ id: "fetch", name: "fetch", state: "ok", durationMs: 12_000 }]} />
 *
 * Adapted from the observability WorkflowTimeline's activity rows;
 * `activitiesToSteps` maps its `WorkflowActivity[]` onto steps unchanged.
 */

import * as React from "react";

import type { ActivityStatus, WorkflowActivity } from "@/components/observability/types";
import { StatusDot } from "@/components/StatusDot";
import { cn } from "@/lib/utils";

import { formatClock } from "./format";

export type StepState = "pending" | "running" | "ok" | "failed" | "skipped";

export interface TimelineStep {
  id: string;
  name: string;
  state: StepState;
  /** Elapsed so far while running; absent before it starts. */
  durationMs?: number | null;
  /** One line under the name: a failure reason, "2 retries". */
  detail?: React.ReactNode;
}

export interface TimelineProps {
  steps: TimelineStep[];
  /** Makes each step a button. */
  onSelect?: (id: string) => void;
  selectedId?: string | null;
  /** Accessible name of the list. Defaults to "Steps". */
  label?: string;
  className?: string;
}

const STATE_LABEL: Record<StepState, string> = {
  pending: "pending",
  running: "running",
  ok: "succeeded",
  failed: "failed",
  skipped: "skipped",
};

export function Timeline({
  steps,
  onSelect,
  selectedId,
  label = "Steps",
  className,
}: TimelineProps) {
  return (
    <ol aria-label={label} className={cn("flex min-w-0 flex-col", className)}>
      {steps.map((step, i) => {
        const body = (
          <>
            <span className="relative flex w-3 shrink-0 justify-center self-stretch" aria-hidden>
              {i < steps.length - 1 && <span className="bg-border absolute top-4 -bottom-2 w-px" />}
              <StepDot state={step.state} />
            </span>
            <span className="min-w-0 flex-1">
              <span
                className={cn(
                  "block font-mono text-sm [overflow-wrap:anywhere]",
                  (step.state === "skipped" || step.state === "pending") && "text-muted-foreground",
                  step.state === "skipped" && "line-through"
                )}
              >
                {step.name}
              </span>
              <span className="sr-only">, {STATE_LABEL[step.state]}</span>
              {step.detail && (
                <span
                  className={cn(
                    "mt-0.5 block text-xs [overflow-wrap:anywhere]",
                    step.state === "failed" ? "text-danger-fg font-mono" : "text-muted-foreground"
                  )}
                >
                  {step.detail}
                </span>
              )}
            </span>
            <span className="text-muted-foreground shrink-0 font-mono text-xs tabular-nums">
              {step.durationMs != null
                ? formatClock(step.durationMs)
                : step.state === "running"
                  ? "…"
                  : ""}
            </span>
          </>
        );
        const row = "flex w-full min-w-0 items-start gap-3 rounded-sm px-2 py-2 text-left";
        return (
          <li key={step.id} className="min-w-0">
            {onSelect ? (
              <button
                type="button"
                onClick={() => onSelect(step.id)}
                aria-pressed={selectedId === step.id}
                className={cn(
                  row,
                  "hover:bg-muted/50 focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                  selectedId === step.id && "bg-muted"
                )}
              >
                {body}
              </button>
            ) : (
              <div className={row}>{body}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function StepDot({ state }: { state: StepState }) {
  const place = "relative mt-1.5";
  switch (state) {
    case "pending":
      return <span className={cn(place, "border-muted-foreground size-2 rounded-full border")} />;
    case "running":
      return <StatusDot status="pending" className={place} />;
    case "ok":
      return <StatusDot status="ok" className={place} />;
    case "failed":
      return <StatusDot status="error" className={place} />;
    case "skipped":
      return <StatusDot status="muted" className={cn(place, "opacity-50")} />;
  }
}

const ACTIVITY_STATE: Record<ActivityStatus, StepState> = {
  pending: "pending",
  running: "running",
  succeeded: "ok",
  failed: "failed",
  cancelled: "skipped",
};

/** The observability WorkflowTimeline's activities as steps. */
export function activitiesToSteps(activities: WorkflowActivity[]): TimelineStep[] {
  return activities.map((a) => {
    const retries = Math.max(0, a.attempts.length - 1);
    const lastError = a.attempts.findLast((x) => x.errorMessage)?.errorMessage;
    return {
      id: a.id,
      name: a.name,
      state: ACTIVITY_STATE[a.status],
      durationMs: a.durationMs ?? null,
      detail:
        a.status === "failed" && lastError
          ? lastError
          : retries > 0
            ? `${retries} ${retries === 1 ? "retry" : "retries"}`
            : undefined,
    };
  });
}

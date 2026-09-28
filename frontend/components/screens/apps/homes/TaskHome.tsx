"use client";

import {
  CheckCircle2Icon,
  CircleIcon,
  ListChecksIcon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatRelativeAge } from "@/lib/format";
import { cn } from "@/lib/utils";

import { formatDuration, runStatusDot, titleCaseStatus } from "./run-status";
import type { useTaskRuns } from "./use-task-runs";

export type TaskHomeScreenProps = ReturnType<typeof useTaskRuns> & {
  name: string;
  /** The app's tab bar. */
  tabs?: React.ReactNode;
};

// The one-off execution lifecycle, shown as a checklist. `stageIndex` maps a
// run status onto how far it got so the checklist reflects the latest run.
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

/**
 * Primitive home for a **task** — a one-off run as a checklist. The latest run's
 * status drives the checklist; prior runs list below. Real data from TaskRun.
 */
export function TaskHomeScreen({ name, runs, loading, tabs }: TaskHomeScreenProps) {
  const latest = runs[0] ?? null;
  const reached = latest ? stageIndex(latest.status) : -1;
  const failed = latest ? isFailed(latest.status) : false;

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted flex size-9 items-center justify-center rounded-md">
            <ListChecksIcon className="text-muted-foreground size-5" />
          </span>
          <span>{name}</span>
          <Badge variant="outline" className="gap-1.5">
            <ListChecksIcon className="size-3" />
            Task
          </Badge>
          {latest && (
            <Badge variant={failed ? "destructive" : "secondary"} className="gap-1.5">
              <StatusDot status={runStatusDot(latest.status)} />
              {titleCaseStatus(latest.status)}
            </Badge>
          )}
        </span>
      }
      description={<span className="text-muted-foreground text-xs">Runs once, on demand.</span>}
    >
      {tabs}
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              {latest ? "Latest run" : "Execution checklist"}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="space-y-3">
              {STAGES.map((stage, i) => {
                const done = reached > i || (reached === 3 && i === 3 && !failed);
                const current = reached === i && !(i === 3);
                const failHere = failed && i === 3;
                return (
                  <li key={stage} className="flex items-center gap-3">
                    {failHere ? (
                      <XCircleIcon className="text-danger-fg size-5 shrink-0" />
                    ) : done ? (
                      <CheckCircle2Icon className="text-success-fg size-5 shrink-0" />
                    ) : current ? (
                      <Loader2Icon className="size-5 shrink-0 animate-spin text-[var(--brand-primary)]" />
                    ) : (
                      <CircleIcon className="text-muted-foreground/40 size-5 shrink-0" />
                    )}
                    <span
                      className={cn(
                        "text-sm",
                        done || current ? "text-foreground" : "text-muted-foreground"
                      )}
                    >
                      {failHere ? "Failed" : stage}
                    </span>
                  </li>
                );
              })}
            </ol>
            {!latest && !loading && (
              <p className="text-muted-foreground mt-4 text-xs">
                No run recorded yet — a one-off task moves through these stages each time it runs.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Run history</CardTitle>
          </CardHeader>
          <CardContent>
            {loading && runs.length === 0 ? (
              <div className="text-muted-foreground flex items-center gap-2 text-sm">
                <Loader2Icon className="size-4 animate-spin" /> Loading runs…
              </div>
            ) : runs.length === 0 ? (
              <EmptyState
                icon={<ListChecksIcon className="size-5" />}
                title="No runs yet"
                description="Each one-off execution of this task appears here."
              />
            ) : (
              <ul className="divide-y">
                {runs.map((r) => (
                  <li key={r.id} className="flex items-center gap-3 py-2 text-sm">
                    <StatusDot status={runStatusDot(r.status)} />
                    <Badge
                      variant={runStatusDot(r.status) === "error" ? "destructive" : "secondary"}
                      className="text-xs"
                    >
                      {titleCaseStatus(r.status)}
                    </Badge>
                    <span className="text-muted-foreground text-xs">
                      {r.startedAt ? formatRelativeAge(r.startedAt) : "—"}
                    </span>
                    <span className="text-muted-foreground ml-auto text-xs tabular-nums">
                      {formatDuration(r.durationSeconds)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}

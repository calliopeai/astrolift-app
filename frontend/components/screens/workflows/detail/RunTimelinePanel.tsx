"use client";

import { Loader2Icon, XIcon } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { TieredWorkflowRun } from "@/graphql/workflows/tiered.types";
import type { WorkflowInstanceDetail } from "@/graphql/workflows/workflows.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { RunStateBadge } from "./RunStateBadge";

export interface RunTimelinePanelViewProps {
  /** The focused run, or null when none is selected. */
  run: TieredWorkflowRun | null;
  /** The run's Temporal instance detail (engine history). */
  detail: WorkflowInstanceDetail | null;
  loading: boolean;
  error?: { message: string } | null;
  /** The live stage DAG for the run (`WorkflowRunDag`), a slot with its own queries. */
  dag?: React.ReactNode;
  onClose: () => void;
}

/** A run's drill-down: header, live stage DAG and engine history timeline. */
export function RunTimelinePanelView({
  run,
  detail,
  loading,
  error,
  dag,
  onClose,
}: RunTimelinePanelViewProps) {
  const fmt = useFormatters();

  if (!run) {
    return (
      <div className="text-muted-foreground hidden items-center justify-center rounded-md border border-dashed p-6 text-center text-sm md:flex">
        Select a run to view its engine timeline.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <RunStateBadge state={run.currentState} />
            <span className="truncate font-mono text-xs">{run.temporalWorkflowId ?? run.guid}</span>
          </div>
          <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 text-xs">
            <span>Started {fmt.formatDateTime(run.startedAt)}</span>
            {run.completedAt && <span>Completed {fmt.formatDateTime(run.completedAt)}</span>}
          </div>
        </div>
        <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close">
          <XIcon className="size-4" />
        </Button>
      </div>

      {dag}

      <p className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">
        Engine timeline
      </p>

      {!run.temporalWorkflowId && (
        <div className="text-muted-foreground rounded-md border border-dashed p-4 text-center text-xs">
          No engine instance recorded for this run.
        </div>
      )}

      {run.temporalWorkflowId && loading && !detail && (
        <div className="flex items-center justify-center p-6">
          <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
        </div>
      )}
      {run.temporalWorkflowId && error && (
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-3 text-sm">
          {error.message}
        </div>
      )}

      {detail && detail.history.length === 0 && (
        <div className="text-muted-foreground rounded-md border border-dashed p-4 text-center text-xs">
          No history events available for this run.
        </div>
      )}

      {detail && detail.history.length > 0 && (
        <div className="max-h-[60vh] overflow-y-auto pr-1">
          <ol className="flex flex-col gap-1">
            {detail.history.map((ev, idx) => (
              <li
                key={`${ev.timestamp}-${idx}`}
                className="flex items-start gap-2 border-l-2 border-dotted py-1 pl-3 text-xs"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono">{ev.eventType}</span>
                    {ev.decision && (
                      <Badge
                        variant={
                          ev.decision === "failed" || ev.decision === "timed_out"
                            ? "destructive"
                            : ev.decision === "cancelled"
                              ? "outline"
                              : "secondary"
                        }
                      >
                        {ev.decision}
                      </Badge>
                    )}
                    {ev.retryCount > 1 && <Badge variant="outline">retry {ev.retryCount}</Badge>}
                  </div>
                </div>
                {ev.timestamp && (
                  <span className="text-muted-foreground shrink-0 font-mono whitespace-nowrap">
                    {fmt.formatDateTime(ev.timestamp)}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

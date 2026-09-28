"use client";

import { ChevronRightIcon, ClockIcon, Loader2Icon, RefreshCwIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import type { TieredWorkflowRun } from "@/graphql/workflows/tiered.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { RunStateBadge } from "./RunStateBadge";

export interface WorkflowObserveViewProps {
  /** The workflow's runs, newest first. */
  runs: TieredWorkflowRun[];
  loading: boolean;
  error?: { message: string } | null;
  /** The run the drill-down shows (the newest run unless one was picked). */
  focusGuid: string | null;
  onSelect: (guid: string | null) => void;
  onRefresh: () => void;
  /**
   * The focused run's drill-down (`RunTimelinePanelView`). A slot so its
   * engine-history and stage-execution queries live in their own container.
   */
  timeline: React.ReactNode;
}

/**
 * Observe pillar — the workflow's run history, with a drill-down into the
 * focused run's Temporal instance timeline.
 */
export function WorkflowObserveView({
  runs,
  loading,
  error,
  focusGuid,
  onSelect,
  onRefresh,
  timeline,
}: WorkflowObserveViewProps) {
  const fmt = useFormatters();

  return (
    <Section
      title="Run history"
      description="Runs of this workflow, newest first. Select a run for its engine timeline."
      action={
        <Button variant="outline" size="icon" onClick={() => onRefresh()} aria-label="Refresh runs">
          <RefreshCwIcon className="size-4" />
        </Button>
      }
    >
      {loading && runs.length === 0 && (
        <div className="flex items-center justify-center p-8">
          <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
        </div>
      )}

      {error && (
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-3 text-sm">
          {error.message}
        </div>
      )}

      {!loading && !error && runs.length === 0 && (
        <div className="text-muted-foreground rounded-md border border-dashed p-12 text-center text-sm">
          <ClockIcon className="text-muted-foreground mx-auto mb-2 size-5" />
          <p className="text-foreground mb-1 font-medium">No runs yet</p>
          <p>Runs appear here once this workflow is dispatched.</p>
        </div>
      )}

      {runs.length > 0 && (
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
          <ul className="flex flex-col gap-1">
            {runs.map((run) => (
              <li key={run.guid}>
                <button
                  type="button"
                  onClick={() => onSelect(run.guid)}
                  className={`hover:bg-accent flex w-full items-center justify-between gap-2 rounded-md border p-3 text-left transition-colors ${
                    focusGuid === run.guid ? "bg-accent" : ""
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <RunStateBadge state={run.currentState} />
                      <span className="text-muted-foreground truncate font-mono text-xs">
                        {run.temporalWorkflowId ?? run.guid.slice(0, 8)}
                      </span>
                    </div>
                    <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                      <span>Started {fmt.formatDateTime(run.startedAt)}</span>
                      {run.completedAt && (
                        <span>Completed {fmt.formatDateTime(run.completedAt)}</span>
                      )}
                    </div>
                  </div>
                  <ChevronRightIcon className="text-muted-foreground size-4 shrink-0" />
                </button>
              </li>
            ))}
          </ul>

          {timeline}
        </div>
      )}
    </Section>
  );
}

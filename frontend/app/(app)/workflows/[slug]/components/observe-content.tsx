"use client";

import { useEffect, useState } from "react";
import { ChevronRightIcon, ClockIcon, Loader2Icon, RefreshCwIcon, XIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { useWorkflowRuns } from "@/graphql/workflows/tiered.hooks";
import type {
  ConfiguredWorkflowWithRuns,
  TieredWorkflowRun,
} from "@/graphql/workflows/tiered.types";
import { useWorkflowInstanceDetail } from "@/graphql/workflows/workflows.hooks";
import { useFormatters } from "@/lib/i18n/formatters";

import { isRunTerminal, RunStateBadge } from "./run-content";
import { WorkflowRunDag } from "./workflow-run-dag";

// Run-list poll cadence while a run is live — inside the 3–5s window (#1090).
const RUNS_POLL_MS = 4000;

/**
 * Observe pillar — the workflow's run history (tier-3 `workflowRuns`), with a
 * drill-down into the run's Temporal instance timeline. The timeline reuses
 * the instances-panel hook (`useWorkflowInstanceDetail`) so both surfaces
 * read the same engine history.
 */
export function ObserveContent({ workflow }: { workflow: ConfiguredWorkflowWithRuns }) {
  const fmt = useFormatters();
  const { runs, loading, error, refetch, startPolling, stopPolling } = useWorkflowRuns(
    workflow.guid,
    workflow.organizationGuid
  );
  const [selectedGuid, setSelectedGuid] = useState<string | null>(null);

  const sorted = [...runs].sort((a, b) => (a.startedAt < b.startedAt ? 1 : -1));
  // Default the drill-down to the newest run so the live flow shows on load.
  const focusGuid = selectedGuid ?? sorted[0]?.guid ?? null;
  const selected = sorted.find((r) => r.guid === focusGuid) ?? null;
  const focusTerminal = isRunTerminal(selected);

  // Poll the run list while the focused run is non-terminal — this keeps its
  // state live and produces the stop signal for the stage-execution poll below;
  // stop once the run settles.
  useEffect(() => {
    if (focusTerminal) {
      stopPolling();
      return;
    }
    startPolling(RUNS_POLL_MS);
    return () => stopPolling();
  }, [focusTerminal, startPolling, stopPolling]);

  return (
    <Section
      title="Run history"
      description="Runs of this workflow, newest first. Select a run for its engine timeline."
      action={
        <Button variant="outline" size="icon" onClick={() => refetch()} aria-label="Refresh runs">
          <RefreshCwIcon className="size-4" />
        </Button>
      }
    >
      {loading && sorted.length === 0 && (
        <div className="flex items-center justify-center p-8">
          <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
        </div>
      )}

      {error && (
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-3 text-sm">
          {error.message}
        </div>
      )}

      {!loading && !error && sorted.length === 0 && (
        <div className="text-muted-foreground rounded-md border border-dashed p-12 text-center text-sm">
          <ClockIcon className="text-muted-foreground mx-auto mb-2 size-5" />
          <p className="text-foreground mb-1 font-medium">No runs yet</p>
          <p>Runs appear here once this workflow is dispatched.</p>
        </div>
      )}

      {sorted.length > 0 && (
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
          <ul className="flex flex-col gap-1">
            {sorted.map((run) => (
              <li key={run.guid}>
                <button
                  type="button"
                  onClick={() => setSelectedGuid(run.guid)}
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

          <RunTimelinePanel
            run={selected}
            definitionSlug={workflow.definitionSlug}
            isTerminal={focusTerminal}
            onClose={() => setSelectedGuid(null)}
          />
        </div>
      )}
    </Section>
  );
}

function RunTimelinePanel({
  run,
  definitionSlug,
  isTerminal,
  onClose,
}: {
  run: TieredWorkflowRun | null;
  definitionSlug: string;
  isTerminal: boolean;
  onClose: () => void;
}) {
  const fmt = useFormatters();
  // The hook skips when the id is null (no run selected, or the run never
  // reached the engine), so it's safe to call unconditionally.
  const { detail, loading, error } = useWorkflowInstanceDetail(run?.temporalWorkflowId ?? null);

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

      <WorkflowRunDag
        key={run.guid}
        workflowId={run.temporalWorkflowId ?? detail?.instance.workflowId ?? null}
        runId={run.temporalRunId ?? detail?.instance.runId ?? null}
        definitionSlug={definitionSlug}
        isTerminal={isTerminal}
      />

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

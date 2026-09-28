"use client";

import {
  AlertTriangleIcon,
  ChevronRightIcon,
  CircleSlashIcon,
  ClockIcon,
  Loader2Icon,
  PlayIcon,
  RefreshCwIcon,
  WorkflowIcon,
} from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { WorkflowTopology } from "@/components/workflows/workflow-topology";
import type {
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { RunStateBadge } from "./RunStateBadge";
import type {
  useDefinitionObserve,
  useDefinitionRun,
  useDefinitionRunPanel,
} from "./use-definition-workflow";

export interface DefinitionWorkflowScreenProps {
  slug: string;
  definition: WorkflowDefinitionSummary | null;
  loading: boolean;
  error?: { message: string } | null;
  /** The workflow tab bar. */
  tabs?: React.ReactNode;
  /** The pillar (build, run, observe) under the tabs. */
  children?: React.ReactNode;
}

/** A repository workflow definition's page: header, tabs, and one pillar. */
export function DefinitionWorkflowScreen({
  slug,
  definition,
  loading,
  error,
  tabs,
  children,
}: DefinitionWorkflowScreenProps) {
  if (loading && !definition) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }
  if (error || !definition) {
    return (
      <PageShell title="Workflow not found">
        <EmptyState
          icon={
            error ? <AlertTriangleIcon className="size-5" /> : <WorkflowIcon className="size-5" />
          }
          title={error ? "Couldn't load this workflow" : `No workflow with slug ${slug}`}
          description={error?.message ?? "It may have been deleted or you may not have access."}
          actionHref="/workflows"
          actionLabel="Back to workflows"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted flex size-9 items-center justify-center rounded-md">
            <WorkflowIcon className="text-muted-foreground size-5" />
          </span>
          <span>{definition.name}</span>
          <Badge variant={definition.isEnabled ? "default" : "secondary"}>
            {definition.isEnabled ? "Enabled" : "Disabled"}
          </Badge>
          <Badge variant="outline" className="capitalize">
            {definition.patternKind.replace(/_/g, " ")}
          </Badge>
        </span>
      }
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground font-mono text-xs">{definition.slug}</span>
          <span className="text-muted-foreground text-xs">repository workflow</span>
          {definition.sourceRepo && (
            <span className="text-muted-foreground text-xs">{definition.sourceRepo}</span>
          )}
        </span>
      }
    >
      {tabs}
      {children}
    </PageShell>
  );
}

/** Build pillar: the declared topology and where it was declared. */
export function DefinitionBuildView({ definition }: { definition: WorkflowDefinitionSummary }) {
  return (
    <div className="flex flex-col gap-6">
      <Section
        title="Definition topology"
        description="Repository-declared stage order, agent binding, environment recipe, model, and output flow."
      >
        <WorkflowTopology stages={definition.stages} height={320} />
      </Section>
      <Section
        title="Declaration source"
        description="Repository workflows are read-only in Astrolift so repository reconciliation remains authoritative."
      >
        <dl className="grid gap-3 rounded-md border p-4 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-muted-foreground text-xs">Repository</dt>
            <dd className="mt-1 font-mono text-xs">
              {definition.sourceRepo || "Platform catalogue"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">Manifest</dt>
            <dd className="mt-1 font-mono text-xs">{definition.sourcePath || "Built in"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">Reconciled ref</dt>
            <dd className="mt-1 font-mono text-xs">{definition.sourceRef || "—"}</dd>
          </div>
        </dl>
      </Section>
    </div>
  );
}

export type DefinitionRunViewProps = ReturnType<typeof useDefinitionRun> & {
  definition: WorkflowDefinitionSummary;
};

/** Run pillar: dispatch now and the latest run. */
export function DefinitionRunView({
  definition,
  canRun,
  running,
  latest,
  run,
}: DefinitionRunViewProps) {
  const fmt = useFormatters();
  return (
    <div className="flex flex-col gap-8">
      <Section
        title="Run now"
        description="Dispatch this repository workflow directly; no configured wrapper is required."
        action={
          canRun ? (
            <Button onClick={() => void run()} disabled={running || !definition.isEnabled}>
              {running ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <PlayIcon className="size-4" />
              )}
              Run now
            </Button>
          ) : null
        }
      >
        <WorkflowTopology stages={definition.stages} height={280} />
      </Section>
      <Section title="Latest run">
        {latest ? (
          <div className="flex flex-wrap items-center gap-3 rounded-md border px-4 py-3 text-sm">
            <RunStateBadge state={latest.status} />
            {latest.startedAt && <span>Started {fmt.formatDateTime(latest.startedAt)}</span>}
            <span className="text-muted-foreground font-mono text-xs">
              {latest.temporalWorkflowId}
            </span>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">This workflow has never run.</p>
        )}
      </Section>
    </div>
  );
}

export type DefinitionObserveViewProps = ReturnType<typeof useDefinitionObserve> & {
  /** The selected run's panel (timeline, graph, cancel). */
  renderRunPanel: (run: WorkflowDefinitionRun | null) => React.ReactNode;
};

/** Observe pillar: the run list and the selected run. */
export function DefinitionObserveView({
  runs,
  loading,
  error,
  refetch,
  requestedRunGuid,
  renderRunPanel,
}: DefinitionObserveViewProps) {
  const [selectedGuid, setSelectedGuid] = React.useState<string | null>(null);
  const selected =
    runs.find((run) => run.guid === (selectedGuid ?? requestedRunGuid)) ?? runs[0] ?? null;

  return (
    <Section
      title="Run observability"
      description="Live stage state, agent execution flow, and engine history for this workflow."
      action={
        <Button variant="outline" size="icon" onClick={() => refetch()} aria-label="Refresh runs">
          <RefreshCwIcon className="size-4" />
        </Button>
      }
    >
      {loading && runs.length === 0 && (
        <div className="flex justify-center p-8">
          <Loader2Icon className="size-5 animate-spin" />
        </div>
      )}
      {error && (
        <div className="text-destructive rounded-md border p-3 text-sm">{error.message}</div>
      )}
      {!loading && !error && runs.length === 0 && (
        <div className="text-muted-foreground rounded-md border border-dashed p-10 text-center text-sm">
          <ClockIcon className="mx-auto mb-2 size-5" />
          No runs yet.
        </div>
      )}
      {runs.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[minmax(220px,0.7fr)_minmax(0,2fr)]">
          <ul className="flex max-h-[560px] flex-col gap-1 overflow-y-auto">
            {runs.map((run) => (
              <li key={run.guid}>
                <button
                  type="button"
                  onClick={() => setSelectedGuid(run.guid)}
                  className={`hover:bg-accent flex w-full items-center justify-between gap-2 rounded-md border p-3 text-left ${selected?.guid === run.guid ? "bg-accent" : ""}`}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <RunStateBadge state={run.status} />
                      {run.parentRunGuid && (
                        <Badge variant="outline" className="text-2xs">
                          Nested · level {run.nestingDepth}
                        </Badge>
                      )}
                    </div>
                    <div className="text-muted-foreground mt-1 truncate font-mono text-xs">
                      {run.temporalWorkflowId}
                    </div>
                  </div>
                  <ChevronRightIcon className="text-muted-foreground size-4 shrink-0" />
                </button>
              </li>
            ))}
          </ul>
          {renderRunPanel(selected)}
        </div>
      )}
    </Section>
  );
}

export type DefinitionRunPanelViewProps = ReturnType<typeof useDefinitionRunPanel> & {
  run: WorkflowDefinitionRun | null;
  /** The run's live stage graph. */
  runDag?: React.ReactNode;
};

/** One run: state, cancel, live stage graph, and the engine timeline. */
export function DefinitionRunPanelView({
  run,
  canRun,
  terminal,
  detail,
  detailLoading,
  detailError,
  cancelling,
  cancel,
  runDag,
}: DefinitionRunPanelViewProps) {
  const fmt = useFormatters();
  if (!run) return null;

  return (
    <div className="flex min-w-0 flex-col gap-4 rounded-md border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <RunStateBadge state={run.status} />
            <span className="font-mono text-xs">{run.temporalWorkflowId}</span>
          </div>
          <div className="text-muted-foreground mt-1 flex gap-3 text-xs">
            {run.startedAt && <span>Started {fmt.formatDateTime(run.startedAt)}</span>}
            {run.endedAt && <span>Ended {fmt.formatDateTime(run.endedAt)}</span>}
            {run.parentRunGuid && <span>Child of run {run.parentRunGuid}</span>}
            {run.childRunCount > 0 && <span>{run.childRunCount} child run(s)</span>}
          </div>
        </div>
        {!terminal && canRun && (
          <Button size="sm" variant="outline" onClick={() => void cancel()} disabled={cancelling}>
            {cancelling ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <CircleSlashIcon className="size-4" />
            )}
            Cancel
          </Button>
        )}
      </div>
      {runDag}
      <div>
        <p className="text-muted-foreground text-2xs mb-2 font-medium tracking-wide uppercase">
          Engine timeline
        </p>
        {detailLoading && !detail && (
          <div className="flex justify-center p-6">
            <Loader2Icon className="size-5 animate-spin" />
          </div>
        )}
        {detailError && <p className="text-destructive text-sm">{detailError.message}</p>}
        {detail && detail.history.length === 0 && (
          <p className="text-muted-foreground text-sm">No engine events recorded yet.</p>
        )}
        {detail && detail.history.length > 0 && (
          <ol className="max-h-64 space-y-1 overflow-y-auto">
            {detail.history.map((event, index) => (
              <li
                key={`${event.timestamp}-${index}`}
                className="flex justify-between gap-3 border-l-2 py-1 pl-3 text-xs"
              >
                <span className="font-mono">{event.eventType}</span>
                <span className="text-muted-foreground shrink-0">
                  {fmt.formatDateTime(event.timestamp)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

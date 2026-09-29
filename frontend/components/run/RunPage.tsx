"use client";

/**
 * RunPage: the run and log archetype (spec 44 §5.5). One layout for
 * deployments, agent runs, workflow runs, jobs, tasks and functions.
 *
 *   Workflows ▾ › nightly-sync › run 7f3c…
 *   run 7f3c…   ● running  4m 12s                         [ Stop ] ⋯
 *   ┌ Timeline ──────────────┐ ┌ Log ─────────── [ Follow ] [ ⤓ ] ┐
 *   │ ● fetch        0:12    │ │ 12:01:04 INF pulling image …     │
 *   │ ● transform    1:40    │ │ 12:01:09 INF started             │
 *   │ ◌ publish      …       │ │ …                                │
 *   └────────────────────────┘ └──────────────────────────────────┘
 *
 * The header is ShellHeader with no tabs (§4.4 rule 5: a run has none). The
 * Timeline sits left and the LogView right from `lg`, stacked below it. A
 * failed run's reason is the first thing in the Timeline panel (§5.2). The
 * same page reads a finished run: nothing here is only visible while live.
 *
 *   <RunPage
 *     crumbs={[{ label: "Workflows", switcher }, { label: "nightly-sync", href }, { label: "run 7f3c…" }]}
 *     title="run 7f3c…"
 *     status={<RunStatus state="running" />}
 *     durationMs={elapsed}                      // the hook ticks it while running
 *     primaryAction={<Button onClick={stop}>Stop</Button>}
 *     steps={steps}                             // TimelineStep[]
 *     failure={run.failed ? { reason: run.error } : null}
 *     log={{ lines, onDownload, loading, error, onRetry }}
 *   />
 *
 * Pure: the hook polls or subscribes, builds the download and ticks the clock.
 * Selecting a step (`onSelectStep`) is for pages whose log can be narrowed
 * to one step; the page passes the narrowed lines back in `log`.
 */

import { ListTreeIcon } from "lucide-react";
import * as React from "react";

import { Panel, type PanelFailure, PanelGrid } from "@/components/panel/Panel";
import { type Crumb, ShellHeader } from "@/components/shell/ShellHeader";

import { formatElapsed } from "./format";
import { LogView, type LogViewProps } from "./LogView";
import { Timeline, type TimelineStep } from "./Timeline";

export interface RunPageProps {
  crumbs: Crumb[];
  title: React.ReactNode;
  /** The run's status badge. */
  status?: React.ReactNode;
  /** Elapsed, or the total of a finished run. */
  durationMs?: number | null;
  /** More context after the duration: trigger, environment, commit. */
  context?: React.ReactNode;
  /** Stop while running; Retry or Redeploy once finished. */
  primaryAction?: React.ReactNode;
  menu?: React.ReactNode;
  steps: TimelineStep[];
  stepsLoading?: boolean;
  stepsError?: string | { message: string } | null;
  onRetrySteps?: () => void;
  selectedStepId?: string | null;
  onSelectStep?: (id: string) => void;
  /** Why the run failed, shown first. */
  failure?: PanelFailure | null;
  log: Omit<LogViewProps, "className">;
}

export function RunPage({
  crumbs,
  title,
  status,
  durationMs,
  context,
  primaryAction,
  menu,
  steps,
  stepsLoading = false,
  stepsError,
  onRetrySteps,
  selectedStepId,
  onSelectStep,
  failure,
  log,
}: RunPageProps) {
  const headerContext =
    durationMs != null || context ? (
      <>
        {durationMs != null && (
          <span className="text-foreground font-mono tabular-nums">
            {formatElapsed(durationMs)}
          </span>
        )}
        {durationMs != null && context && " · "}
        {context}
      </>
    ) : undefined;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ShellHeader
        crumbs={crumbs}
        title={title}
        status={status}
        context={headerContext}
        primaryAction={primaryAction}
        menu={menu}
      />
      <PanelGrid className="items-start">
        <Panel
          title="Timeline"
          icon={<ListTreeIcon className="size-4" />}
          className="lg:col-span-4"
          failure={failure}
          loading={stepsLoading && steps.length === 0}
          error={steps.length === 0 ? stepsError : null}
          onRetry={onRetrySteps}
          empty={
            !stepsLoading && !stepsError && steps.length === 0
              ? { icon: <ListTreeIcon className="size-5" />, title: "No steps recorded" }
              : null
          }
        >
          <Timeline steps={steps} selectedId={selectedStepId} onSelect={onSelectStep} />
        </Panel>
        <LogView
          {...log}
          paneClassName={log.paneClassName ?? "h-96 lg:h-[60vh]"}
          className="lg:col-span-8"
        />
      </PanelGrid>
    </div>
  );
}

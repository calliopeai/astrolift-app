"use client";

import {
  AlertCircleIcon,
  BoltIcon,
  BotIcon,
  BoxIcon,
  CalendarClockIcon,
  ClipboardListIcon,
  GlobeIcon,
  HardDriveIcon,
  RocketIcon,
  WorkflowIcon,
} from "lucide-react";
import type * as React from "react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import type { AstroliftWorkload, WorkloadKind } from "@/graphql/registry/registry.types";

import type { useWorkloadsList, WorkloadLiveStatus } from "./use-workloads-list";
import { KIND_LABEL } from "./workloads-list";

export type WorkloadsListScreenProps = ReturnType<typeof useWorkloadsList> & {
  slug: string;
  /** `/apps` normally, `/agents` inside the agent shell. */
  basePath: string;
  /**
   * The inline scale control for one row, rendered only for workloads the
   * HPA does not own and with a per-object primary-environment permission decision.
   */
  renderScale?: (workload: AstroliftWorkload, currentDesired: number) => React.ReactNode;
  /** A row's `⋯` items (`DropdownMenuItem`s): Run now for a CronJob. */
  renderRowActions?: (workload: AstroliftWorkload) => React.ReactNode;
};

function readinessTone(ready: number, desired: number): string {
  if (desired === 0) return "bg-muted text-muted-foreground";
  if (ready === desired) return "bg-success/15 text-success-fg";
  if (ready === 0) return "bg-danger/15 text-danger-fg";
  return "bg-warning/15 text-warning-fg";
}

const KIND_ICON: Record<WorkloadKind, React.ComponentType<{ className?: string }>> = {
  deployment: RocketIcon,
  statefulset: HardDriveIcon,
  job: WorkflowIcon,
  cronjob: CalendarClockIcon,
  task: ClipboardListIcon,
  agent: BotIcon,
  workflow: WorkflowIcon,
  function: BoltIcon,
};

/**
 * An app's Workloads tab (spec 44 §5.1, §10.2): its deployment,
 * statefulset, job and cronjob workloads on the embedded list, filtered by
 * kind (scheduled jobs are `kind: cronjob`), numbered pages. The whole row
 * opens the workload; replicas scale in place; a CronJob runs now from `⋯`.
 * Pure.
 */
export function WorkloadsListScreen({
  list,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
  liveStatus,
  slug,
  basePath,
  renderScale,
  renderRowActions,
}: WorkloadsListScreenProps) {
  const at = (...segments: string[]) => [`${basePath}/${slug}`, ...segments].join("/");

  const columns: Column<AstroliftWorkload>[] = [
    {
      id: "name",
      header: "Name",
      sortKey: "name",
      cellClassName: "max-w-72",
      cell: (w) => {
        const Icon = KIND_ICON[w.kind] ?? BoxIcon;
        return (
          <span className="flex min-w-0 items-center gap-2">
            <Icon className="text-muted-foreground size-4 shrink-0" />
            <span className="block min-w-0">
              <span className="block truncate font-medium" title={w.name}>
                {w.name}
              </span>
              <span
                className="text-muted-foreground block truncate font-mono text-xs"
                title={w.slug}
              >
                {w.slug}
              </span>
            </span>
          </span>
        );
      },
    },
    {
      id: "kind",
      header: "Kind",
      sortKey: "kind",
      cell: (w) => (
        <Badge variant="outline" className="capitalize">
          {KIND_LABEL[w.kind] ?? w.kind}
        </Badge>
      ),
    },
    {
      id: "replicas",
      header: "Ready / desired",
      // The row link is an ::after overlay stretched from the first
      // cell; the scale popover lives in this one and has to be lifted
      // back on top of it or the only thing a click here can do is
      // navigate.
      cellClassName: "relative z-10 font-mono text-xs",
      cell: (w) => {
        const live = liveStatus.get(w.slug) ?? {
          ready: 0,
          desired: w.replicas || 0,
          maxRestarts: 0,
          errorEvent: null as WorkloadLiveStatus["errorEvent"],
        };
        return (
          <>
            <div className="flex items-center gap-1.5">
              <Badge className={readinessTone(live.ready, live.desired)}>
                Ready {live.ready}/{live.desired}
              </Badge>
              {/* #668 — inline scale popover so operators can bump
                  replicas during an incident without navigating to the
                  detail page. HPA-bound workloads skip the affordance
                  (HPA owns the replica count). */}
              {!w.hpaMinReplicas && !w.hpaMaxReplicas ? renderScale?.(w, live.desired) : null}
            </div>
            {w.hpaMinReplicas && w.hpaMaxReplicas ? (
              <div className="text-muted-foreground mt-1">
                HPA {w.hpaMinReplicas}–{w.hpaMaxReplicas} @ {w.hpaTargetCpuPct}% CPU
              </div>
            ) : null}
            {/* #666 — inline error chip for ImagePullBackOff /
                CrashLoopBackOff / OOMKilled / FailedScheduling. Shows the
                K8s reason as the badge label; the tooltip carries the
                full event message. */}
            {live.errorEvent ? (
              <div className="mt-1 min-w-0" title={live.errorEvent.message}>
                <Badge
                  variant="outline"
                  className="border-danger-border text-danger-fg max-w-full gap-1 [overflow-wrap:anywhere] whitespace-normal"
                >
                  <AlertCircleIcon className="size-3" />
                  {live.errorEvent.reason}
                  {live.errorEvent.count > 1 ? ` × ${live.errorEvent.count}` : ""}
                </Badge>
              </div>
            ) : null}
          </>
        );
      },
    },
    {
      id: "restarts",
      header: "Restarts",
      cellClassName: "font-mono text-xs",
      cell: (w) => {
        const restarts = liveStatus.get(w.slug)?.maxRestarts ?? 0;
        return restarts > 0 ? (
          <Badge
            variant="outline"
            className={
              restarts >= 3
                ? "border-danger-border text-danger-fg"
                : "border-warning-border text-warning-fg"
            }
          >
            {restarts}
          </Badge>
        ) : (
          <span className="text-muted-foreground">0</span>
        );
      },
    },
    {
      id: "public",
      header: "Public",
      cell: (w) =>
        w.isPublic ? (
          <Badge className="bg-success/15 text-success-fg">
            <GlobeIcon className="size-3" /> public
          </Badge>
        ) : (
          <Badge variant="secondary">internal</Badge>
        ),
    },
    {
      id: "resources",
      header: "Resources",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (w) => `${w.cpuRequest || "—"} cpu · ${w.memoryRequest || "—"} mem`,
    },
    {
      id: "schedule",
      header: "Schedule",
      cellClassName: "font-mono text-xs",
      cell: (w) =>
        w.schedule ? (
          <span className="inline-flex items-center gap-1">
            <CalendarClockIcon className="size-3" />
            {w.schedule}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
  ];

  return (
    <ListPage<AstroliftWorkload>
      embedded
      list={list}
      label="Workloads"
      columns={columns}
      rows={rows}
      getRowId={(w) => w.id}
      rowHref={(w) => at("workloads", w.slug)}
      rowActions={renderRowActions}
      loading={loading}
      error={error}
      onRetry={onRetry}
      totalCount={totalCount}
      empty={{
        icon: <BoxIcon className="size-5" />,
        title: "No workloads declared",
        description:
          "Workloads are parsed from this app's manifest. Add a workload section to astrolift.yaml and push to repopulate this view.",
        actionHref: at("manifest"),
        actionLabel: "Open manifest",
      }}
    />
  );
}

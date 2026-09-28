"use client";

import {
  AlertCircleIcon,
  AlertTriangleIcon,
  BoltIcon,
  BotIcon,
  BoxIcon,
  CalendarClockIcon,
  ClipboardListIcon,
  GlobeIcon,
  HardDriveIcon,
  LayersIcon,
  RocketIcon,
  WorkflowIcon,
} from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { DataTable, type Column } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
  WorkloadKind,
} from "@/graphql/registry/registry.types";

import type { useWorkloadsList, WorkloadLiveStatus } from "./use-workloads-list";

export type WorkloadsListScreenProps = Omit<ReturnType<typeof useWorkloadsList>, "app"> & {
  app: Pick<AstroliftRegisteredApp, "name" | "slug" | "manifestPath"> | null;
  slug: string;
  /** `/apps` normally, `/agents` inside the agent shell. */
  basePath: string;
  /** The app's tab bar. */
  tabs?: React.ReactNode;
  /**
   * The inline scale control for one row, rendered only for workloads the
   * HPA does not own and only with app.deploy.
   */
  renderScale?: (workload: AstroliftWorkload, currentDesired: number) => React.ReactNode;
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

const KIND_LABEL: Record<WorkloadKind, string> = {
  deployment: "Deployment",
  statefulset: "StatefulSet",
  job: "Job",
  cronjob: "CronJob",
  task: "Task",
  agent: "Agent",
  workflow: "Workflow",
  function: "Function",
};

export function WorkloadsListScreen({
  app: a,
  appLoading,
  table,
  liveStatus,
  stats,
  slug,
  basePath,
  tabs,
  renderScale,
}: WorkloadsListScreenProps) {
  const at = (appSlug: string, ...segments: string[]) =>
    [`${basePath}/${appSlug}`, ...segments].join("/");

  if (appLoading && !a) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title="App not found">
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No app with slug ${slug}`}
          description="It may have been soft-deleted, or you may not have permission to read it."
          actionHref="/apps"
          actionLabel="Back to apps"
        />
      </PageShell>
    );
  }

  const columns: Column<AstroliftWorkload>[] = [
    {
      id: "name",
      header: "Name",
      cell: (w) => {
        const Icon = KIND_ICON[w.kind] ?? BoxIcon;
        return (
          <span className="flex items-center gap-2">
            <Icon className="text-muted-foreground size-4 shrink-0" />
            <span className="block">
              <span className="block font-medium">{w.name}</span>
              <span className="text-muted-foreground block font-mono text-xs">{w.slug}</span>
            </span>
          </span>
        );
      },
    },
    {
      id: "kind",
      header: "Kind",
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
              {!w.hpaMinReplicas && !w.hpaMaxReplicas ? (
                <Can permission="app.deploy">{renderScale?.(w, live.desired)}</Can>
              ) : null}
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
              <div className="mt-1" title={live.errorEvent.message}>
                <Badge variant="outline" className="border-danger-border text-danger-fg gap-1">
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
    <PageShell
      title={`${a.name} · Workloads`}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {a.slug} · workloads parsed from the manifest at {a.manifestPath}
        </span>
      }
    >
      {tabs}

      {/* ─── stats strip ───────────────────────────────────────────────── */}
      <div className="grid gap-3 text-sm sm:grid-cols-4">
        <SummaryTile icon={LayersIcon} label="Workloads" value={stats.workloads} />
        <SummaryTile icon={BoxIcon} label="Total replicas" value={stats.totalReplicas} />
        <SummaryTile icon={GlobeIcon} label="Public" value={stats.publicCount} />
        <SummaryTile icon={CalendarClockIcon} label="Scheduled" value={stats.scheduled} />
      </div>

      <DataTable
        label="Workloads"
        controller={table}
        columns={columns}
        getRowId={(w) => w.id}
        rowHref={(w) => at(a.slug, "workloads", w.slug)}
        searchPlaceholder="Filter workloads..."
        empty={{
          icon: <BoxIcon className="size-5" />,
          title: "No workloads declared",
          description:
            "Workloads are parsed from this app's manifest. Add a workload section to astrolift.yaml and push to repopulate this view.",
          actionHref: at(a.slug, "manifest"),
          actionLabel: "Open manifest",
        }}
        emptyFiltered={{
          title: "No matching workloads",
          description:
            "No workload matches that name, slug, or kind. Clear the search to see every workload in this app.",
        }}
      />
    </PageShell>
  );
}

function SummaryTile({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: number;
}) {
  return (
    <div className="border-border bg-card flex items-center gap-3 rounded-md border p-3">
      <div className="bg-primary/10 text-primary rounded-md p-1.5">
        <Icon className="size-4" />
      </div>
      <div>
        <div className="text-muted-foreground text-xs tracking-wide uppercase">{label}</div>
        <div className="text-lg font-bold tabular-nums">{value}</div>
      </div>
    </div>
  );
}

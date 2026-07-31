"use client";

import { useMutation, useQuery } from "@apollo/client/react";
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
  Loader2Icon,
  RocketIcon,
  ScalingIcon,
  WorkflowIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { DataTable, useCursorTable, type Column } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { SCALE_WORKLOAD } from "@/graphql/lifecycle/lifecycle.mutations";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { LIST_APP_PODS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP, LIST_WORKLOADS, LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
  WorkloadKind,
} from "@/graphql/registry/registry.types";

import { appPath, useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}
interface WorkloadsPageResp {
  astroliftWorkloadsPage: {
    items: AstroliftWorkload[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
}
interface AppPodsResp {
  astroliftAppPods: AstroliftAppPod[];
}

interface WorkloadLiveStatus {
  ready: number;
  desired: number;
  maxRestarts: number;
  // #666 — surfaces the most recent K8s warning event across the
  // workload's pods so operators see ImagePullBackOff /
  // CrashLoopBackOff / OOMKilled inline without drilling into the
  // pod's detail page.
  errorEvent: {
    reason: string;
    message: string;
    count: number;
    lastSeen: string;
  } | null;
}

/**
 * Aggregate per-workload runtime counts from the flat pod list.
 *
 * `ready` = pods in phase `Running` (case-insensitive — the cluster
 * resolver emits "Running" but we lowercase to defend against driver
 * drift). `desired` falls through to the manifest-declared replica
 * count when the pod list yields fewer rows than declared (e.g. a
 * fresh deploy still spinning up). `maxRestarts` is the **max**
 * restart_count across pods in the workload — that matches what an
 * operator wants to see ("which workload is flapping?"), not a sum.
 *
 * Runs over the workloads on the current page only; the pod list is
 * app-wide, so a page's rows always find their pods.
 */
function aggregatePodStatus(
  workloads: AstroliftWorkload[],
  pods: AstroliftAppPod[]
): Map<string, WorkloadLiveStatus> {
  const byWorkload = new Map<string, AstroliftAppPod[]>();
  for (const p of pods) {
    const arr = byWorkload.get(p.workload) ?? [];
    arr.push(p);
    byWorkload.set(p.workload, arr);
  }
  const result = new Map<string, WorkloadLiveStatus>();
  for (const w of workloads) {
    const pp = byWorkload.get(w.slug) ?? [];
    const ready = pp.filter((p) => (p.phase || "").toLowerCase() === "running").length;
    const maxRestarts = pp.reduce((acc, p) => Math.max(acc, p.restarts ?? 0), 0);
    // #666 — pick the most-recent error event across pods so the
    // chip surfaces what's actually breaking. `recentErrorEvent`
    // is null on healthy pods.
    let errorEvent: WorkloadLiveStatus["errorEvent"] = null;
    for (const p of pp) {
      const ev = (
        p as AstroliftAppPod & {
          recentErrorEvent?: {
            reason: string;
            message: string;
            count: number;
            lastSeen: string;
          } | null;
        }
      ).recentErrorEvent;
      if (!ev) continue;
      if (!errorEvent || Date.parse(ev.lastSeen) > Date.parse(errorEvent.lastSeen)) {
        errorEvent = ev;
      }
    }
    result.set(w.slug, {
      ready,
      desired: Math.max(w.replicas || 0, pp.length),
      maxRestarts,
      errorEvent,
    });
  }
  return result;
}

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

export function WorkloadsListClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });

  /**
   * The stats strip summarises the app's *whole* workload set, and
   * `astroliftWorkloadsPage` exposes no aggregate — summing the page in
   * hand would make "Total replicas" quietly mean "on this page". So the
   * strip stays on the flat field while the table below pages on the
   * server. Backend ask: a workload-summary field (or kind / public
   * filters with `totalCount`) would retire this second round trip.
   */
  const summary = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
    pollInterval: 60000,
  });
  // Live pod state for the readiness + restart-count cells. Pulled on a
  // 30s tick so an operator watching a rollout sees ready/desired
  // converge without page reloads.
  const pods = useQuery<AppPodsResp>(LIST_APP_PODS, {
    variables: { appSlug: slug },
    pollInterval: 30000,
    fetchPolicy: "cache-and-network",
  });

  // `astroliftWorkloadsPage` filters on `appSlug` and searches name,
  // slug, kind and app slug. It takes no sort argument, so no column
  // declares a `sortKey` — sorting the page in hand while the rest of
  // the set sits on the server is wrong at every page boundary.
  const table = useCursorTable<AstroliftWorkload>({
    query: LIST_WORKLOADS_PAGE,
    variables: { appSlug: slug },
    extract: (d) => (d as WorkloadsPageResp | undefined)?.astroliftWorkloadsPage,
    searchVariable: "search",
    urlKey: "wl",
    pollInterval: 60000,
  });

  const a = app.data?.astroliftApp;
  const summaryList = React.useMemo(
    () => summary.data?.astroliftWorkloads ?? [],
    [summary.data?.astroliftWorkloads]
  );
  const podList = React.useMemo(
    () => pods.data?.astroliftAppPods ?? [],
    [pods.data?.astroliftAppPods]
  );
  const liveStatus = React.useMemo(
    () => aggregatePodStatus(table.rows, podList),
    [table.rows, podList]
  );

  if (app.loading && !a) {
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

  // Surface stats up top so an operator immediately sees the shape of
  // the app's workload set before diving into rows.
  const totalReplicas = summaryList.reduce((acc, w) => acc + (w.replicas || 0), 0);
  const publicCount = summaryList.filter((w) => w.isPublic).length;
  const kindCounts = summaryList.reduce<Partial<Record<WorkloadKind, number>>>((acc, w) => {
    acc[w.kind] = (acc[w.kind] ?? 0) + 1;
    return acc;
  }, {});

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
                <Can permission="app.deploy">
                  <ScalePopover
                    workloadId={w.id}
                    workloadName={w.name}
                    currentDesired={live.desired}
                  />
                </Can>
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
      <AppTabs slug={a.slug} active="workloads" />

      {/* ─── stats strip ───────────────────────────────────────────────── */}
      <div className="grid gap-3 text-sm sm:grid-cols-4">
        <SummaryTile
          icon={LayersIcon}
          label="Workloads"
          value={table.totalCount ?? summaryList.length}
        />
        <SummaryTile icon={BoxIcon} label="Total replicas" value={totalReplicas} />
        <SummaryTile icon={GlobeIcon} label="Public" value={publicCount} />
        <SummaryTile icon={CalendarClockIcon} label="Scheduled" value={kindCounts.cronjob ?? 0} />
      </div>

      <DataTable
        label="Workloads"
        controller={table}
        columns={columns}
        getRowId={(w) => w.id}
        rowHref={(w) => appPath(chrome, a.slug, "workloads", w.slug)}
        searchPlaceholder="Filter workloads..."
        empty={{
          icon: <BoxIcon className="size-5" />,
          title: "No workloads declared",
          description:
            "Workloads are parsed from this app's manifest. Add a workload section to astrolift.yaml and push to repopulate this view.",
          actionHref: appPath(chrome, a.slug, "manifest"),
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

function ScalePopover({
  workloadId,
  workloadName,
  currentDesired,
}: {
  workloadId: string;
  workloadName: string;
  currentDesired: number;
}) {
  const [open, setOpen] = React.useState(false);
  const [value, setValue] = React.useState(String(currentDesired));
  React.useEffect(() => {
    if (open) setValue(String(currentDesired));
  }, [open, currentDesired]);
  const [scale, { loading }] = useMutation<{
    scaleAstroliftWorkload: MutationResult<unknown>;
  }>(SCALE_WORKLOAD);

  async function apply() {
    const next = Number.parseInt(value, 10);
    if (Number.isNaN(next) || next < 0 || next > 50) {
      toast.error("Replicas must be a number between 0 and 50");
      return;
    }
    if (next === currentDesired) {
      setOpen(false);
      return;
    }
    try {
      const { data } = await scale({
        variables: { input: { workloadId, replicas: next } },
      });
      if (data?.scaleAstroliftWorkload.ok) {
        toast.success(`${workloadName} → ${next} replicas`);
        setOpen(false);
      } else {
        toast.error(data?.scaleAstroliftWorkload.errors?.[0]?.message ?? "Scale failed");
      }
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="size-6" title={`Scale ${workloadName}`}>
          <ScalingIcon className="size-3" />
          <span className="sr-only">Scale</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-56 p-3" align="start">
        <p className="mb-2 text-xs font-medium">Scale {workloadName}</p>
        <div className="flex items-center gap-2">
          <Input
            type="number"
            min={0}
            max={50}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="h-8"
            onKeyDown={(e) => {
              if (e.key === "Enter") void apply();
              if (e.key === "Escape") setOpen(false);
            }}
            autoFocus
          />
          <Button size="sm" onClick={() => void apply()} disabled={loading}>
            {loading ? <Loader2Icon className="size-3 animate-spin" /> : "Apply"}
          </Button>
        </div>
        <p className="text-muted-foreground text-2xs mt-2">
          Current: {currentDesired}. Takes effect immediately.
        </p>
      </PopoverContent>
    </Popover>
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

"use client";

import { useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  BoxIcon,
  ChevronRightIcon,
  CopyIcon,
  GitBranchIcon,
  GlobeIcon,
  LayersIcon,
  PackageIcon,
  PuzzleIcon,
  RotateCwIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  GET_WORKLOAD_POD_STATUS_BREAKDOWN,
  LIST_APP_PODS,
  LIST_SCHEDULED_JOB_RUNS,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppPod,
  AstroliftContainerStatus,
  AstroliftScheduledJobRun,
  AstroliftWorkloadPodStatusBucket,
  ContainerKind,
} from "@/graphql/lifecycle/lifecycle.types";
import { GET_WORKLOAD, LIST_CONTAINERS } from "@/graphql/registry/registry.queries";
import type { AstroliftContainer, AstroliftWorkload } from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

import { ResourceUsageGauges } from "./resource-usage-gauges";

interface WorkloadResp {
  astroliftWorkload: AstroliftWorkload | null;
}

interface ContainersResp {
  astroliftContainers: AstroliftContainer[];
}

interface JobRunsResp {
  astroliftScheduledJobRuns: AstroliftScheduledJobRun[];
}

interface PodsResp {
  astroliftAppPods: AstroliftAppPod[];
}

interface BreakdownResp {
  astroliftWorkloadPodStatusBreakdown: AstroliftWorkloadPodStatusBucket[];
}

const HEALTHCHECK_LABEL: Record<string, string> = {
  none: "no probe",
  http: "HTTP probe",
  tcp: "TCP probe",
  exec: "exec probe",
};

// Refresh cadence for the live pod surface (status grid + pod table).
// Matches the observability page's pod poll so a user toggling between
// them sees the same data on the same cadence.
const POD_POLL_MS = 15_000;

// Bucket-color classes for the status grid. Anything outside the
// known set falls through to ``muted`` so a fresh kubelet reason
// doesn't crash the surface.
const STATUS_VARIANT: Record<string, string> = {
  Running: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  Succeeded: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  Pending: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
  ContainerCreating: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
  Terminating: "bg-slate-500/10 text-slate-700 dark:text-slate-300",
  Unknown: "bg-slate-500/10 text-slate-700 dark:text-slate-300",
  CrashLoopBackOff: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  ImagePullBackOff: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  ErrImagePull: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  CreateContainerConfigError: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  CreateContainerError: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  InvalidImageName: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  OOMKilled: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  Error: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
};

function statusClass(status: string): string {
  return STATUS_VARIANT[status] ?? "bg-muted text-muted-foreground";
}

// Heuristic for the "flapping" badge — operators care about pods
// that have restarted >5 times in the last hour because that's the
// signal that an OOMKiller / probe-failure loop is in progress (vs
// a single transient crash). Kept on the client so the badge stays
// in sync with the live pod-list polling cadence.
const FLAP_THRESHOLD = 5;
const FLAP_WINDOW_MS = 60 * 60 * 1000;

function podIsFlapping(pod: AstroliftAppPod): boolean {
  if (pod.restarts <= FLAP_THRESHOLD) return false;
  const recent = pod.containerStatuses
    .map((c) => c.lastRestartAt)
    .filter((iso): iso is string => Boolean(iso))
    .map((iso) => Date.parse(iso))
    .filter((ms) => Number.isFinite(ms));
  if (recent.length === 0) return false;
  const newest = Math.max(...recent);
  return Date.now() - newest <= FLAP_WINDOW_MS;
}

function formatAge(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return "—";
  const delta = Math.max(0, Date.now() - ms);
  const seconds = Math.floor(delta / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

function emptyOrValue(s: string | null | undefined): string {
  return s && s.trim() ? s : "—";
}

export function WorkloadDetailClient({
  appSlug,
  workloadSlug,
}: {
  appSlug: string;
  workloadSlug: string;
}) {
  const t = useTranslations("apps.workloadDetail");
  const { data: wlData, loading: wlLoading } = useQuery<WorkloadResp>(GET_WORKLOAD, {
    variables: { appSlug, slug: workloadSlug },
    fetchPolicy: "cache-and-network",
  });
  const { data: cData, loading: cLoading } = useQuery<ContainersResp>(LIST_CONTAINERS, {
    variables: { workloadSlug },
    fetchPolicy: "cache-and-network",
  });
  const w = wlData?.astroliftWorkload ?? null;

  // Live pod state — drives the restart column + sidecar resource
  // cards. Filtered to *this* workload's pods only so a noisy
  // sibling workload doesn't drown the table.
  const { data: pdData, loading: pdLoading } = useQuery<PodsResp>(LIST_APP_PODS, {
    variables: { appSlug },
    pollInterval: POD_POLL_MS,
    fetchPolicy: "cache-and-network",
  });

  const { data: brData, loading: brLoading } = useQuery<BreakdownResp>(
    GET_WORKLOAD_POD_STATUS_BREAKDOWN,
    {
      variables: { appSlug, workloadSlug },
      pollInterval: POD_POLL_MS,
      fetchPolicy: "cache-and-network",
    }
  );

  const isCronjob = w?.kind === "cronjob";
  const { data: rData, loading: rLoading } = useQuery<JobRunsResp>(LIST_SCHEDULED_JOB_RUNS, {
    variables: { appSlug, limit: 10 },
    skip: !isCronjob,
    pollInterval: isCronjob ? 15000 : 0,
    fetchPolicy: "cache-and-network",
  });

  const containers = cData?.astroliftContainers ?? [];
  const runs = (rData?.astroliftScheduledJobRuns ?? []).filter(
    (r) => r.workloadSlug === workloadSlug
  );

  const podRows: AstroliftAppPod[] = React.useMemo(
    () => (pdData?.astroliftAppPods ?? []).filter((p) => (p.workload || "") === workloadSlug),
    [pdData, workloadSlug]
  );
  const buckets = brData?.astroliftWorkloadPodStatusBreakdown ?? [];

  if (wlLoading && !w) {
    return (
      <PageShell title="Workload" description="Loading…">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
      </PageShell>
    );
  }

  if (!w) {
    return (
      <PageShell
        title="Workload not found"
        description="The workload doesn't exist or you don't have permission to view it."
      >
        <Card>
          <CardContent className="text-muted-foreground p-6 text-sm">
            Return to the{" "}
            <a href={`/apps/${appSlug}`} className="underline">
              app overview
            </a>
            .
          </CardContent>
        </Card>
      </PageShell>
    );
  }

  // Group containers by kind for the Init / Primary / Sidecar split.
  // Source of truth is the *live* pod data because the manifest-side
  // container row doesn't carry the sidecar/init classification (it
  // mirrors what the operator declared, not what kubernetes ended up
  // running with injected sidecars like istio-proxy).
  const liveContainers: AstroliftContainerStatus[] = podRows
    .flatMap((p) => p.containerStatuses)
    // Dedupe by name — multiple pods report the same containers.
    .reduce<AstroliftContainerStatus[]>((acc, cs) => {
      if (acc.some((c) => c.name === cs.name)) return acc;
      acc.push(cs);
      return acc;
    }, []);

  return (
    <PageShell
      title={`${appSlug} · ${w.slug}`}
      description={`${w.kind} workload from the manifest. ${w.replicas} replica${w.replicas === 1 ? "" : "s"}.`}
      actions={
        <Button variant="outline" asChild>
          <a href={`/apps/${appSlug}/manifest`}>
            <GitBranchIcon className="size-4" />
            Manifest preview
          </a>
        </Button>
      }
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            <BoxIcon className="size-4" />
            <span className="font-mono">{w.slug}</span>
            <Badge variant="secondary" className="capitalize">
              {w.kind}
            </Badge>
            {w.isPublic && (
              <Badge variant="outline" className="gap-1">
                <GlobeIcon className="size-3" /> public
              </Badge>
            )}
            {w.schedule && (
              <Badge variant="outline" className="font-mono">
                cron {w.schedule}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm sm:grid-cols-3">
          <Field label="Replicas" mono value={w.replicas} />
          <Field
            label="HPA"
            mono
            value={
              w.hpaMinReplicas && w.hpaMaxReplicas
                ? `${w.hpaMinReplicas} - ${w.hpaMaxReplicas} @ ${w.hpaTargetCpuPct}% cpu`
                : "off"
            }
          />
          <Field
            label="CPU req/limit"
            mono
            value={`${w.cpuRequest || "—"} / ${w.cpuLimit || "—"}`}
          />
          <Field
            label="Memory req/limit"
            mono
            value={`${w.memoryRequest || "—"} / ${w.memoryLimit || "—"}`}
          />
          <Field
            label="Storage"
            mono
            value={
              w.storageClass || w.storageSize
                ? `${w.storageClass || "default"} / ${w.storageSize || "—"}`
                : "—"
            }
          />
          <ServiceFqdnField
            fqdn={w.inClusterServiceFqdn}
            labels={{
              label: t("fqdn.label"),
              copyTitle: t("fqdn.copyTitle"),
              copyToast: t("fqdn.copyToast"),
              copyError: t("fqdn.copyError"),
              unavailable: t("fqdn.unavailable"),
            }}
          />
        </CardContent>
      </Card>

      <ResourceUsageGauges
        appSlug={appSlug}
        workloadSlug={workloadSlug}
        environmentName={null}
        labels={{
          title: t("resourceUsage.title"),
          cpuTitle: t("resourceUsage.cpuTitle"),
          memoryTitle: t("resourceUsage.memoryTitle"),
          ofRequest: t("resourceUsage.ofRequest"),
          ofLimit: t("resourceUsage.ofLimit"),
          currentLabel: t("resourceUsage.currentLabel"),
          requestLabel: t("resourceUsage.requestLabel"),
          limitLabel: t("resourceUsage.limitLabel"),
          empty: t("resourceUsage.empty"),
          sourcedAt: t("resourceUsage.sourcedAt"),
        }}
      />

      <PodStatusGridCard
        appSlug={appSlug}
        buckets={buckets}
        loading={brLoading && buckets.length === 0}
        labels={{
          title: t("podStatus.title"),
          headerStatus: t("podStatus.headerStatus"),
          headerCount: t("podStatus.headerCount"),
          headerPercent: t("podStatus.headerPercent"),
          empty: t("podStatus.empty"),
          podsCount: t("podStatus.podsCount", {
            count: buckets.reduce((a, b) => a + b.count, 0),
          }),
          bucketEmpty: t("podStatus.bucketEmpty"),
          notReady: t("podStatus.notReady"),
        }}
      />

      <PodHealthTableCard
        appSlug={appSlug}
        pods={podRows}
        loading={pdLoading && podRows.length === 0}
        labels={{
          title: t("pods.title"),
          empty: t("pods.empty"),
          columnName: t("pods.columnName"),
          columnStatus: t("pods.columnStatus"),
          columnRestarts: t("pods.columnRestarts"),
          columnNode: t("pods.columnNode"),
          columnAge: t("pods.columnAge"),
          flapping: t("pods.flapping"),
          restartReasonsHeading: t("pods.restartReasonsHeading"),
        }}
      />

      <ContainerSplitCard
        manifestContainers={containers}
        liveContainers={liveContainers}
        loading={cLoading && containers.length === 0}
        labels={{
          initTitle: t("containers.init.title"),
          initDescription: t("containers.init.description"),
          initEmpty: t("containers.init.empty"),
          primaryTitle: t("containers.primary.title"),
          primaryDescription: t("containers.primary.description"),
          primaryEmpty: t("containers.primary.empty"),
          sidecarsTitle: t("containers.sidecars.title"),
          sidecarsDescription: t("containers.sidecars.description"),
          sidecarsEmpty: t("containers.sidecars.empty"),
          image: t("containers.image"),
          cpuReqLimit: t("containers.cpuReqLimit"),
          memReqLimit: t("containers.memReqLimit"),
          restarts: t("containers.restarts"),
          empty: t("containers.empty"),
        }}
      />

      {isCronjob && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Recent runs
              <Badge variant="outline" className="ml-2">
                {runs.length}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {rLoading && runs.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : runs.length === 0 ? (
              <div className="text-muted-foreground p-6 text-sm">
                No scheduled runs recorded yet.
              </div>
            ) : (
              <ul className="divide-y">
                {runs.map((r) => (
                  <li
                    key={r.id}
                    className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <Badge variant="secondary" className="capitalize">
                          {r.status}
                        </Badge>
                        <span className="font-mono text-xs">
                          {r.k8sJobName || r.id.slice(0, 12)}
                        </span>
                      </div>
                      <div className="text-muted-foreground mt-0.5 text-xs">
                        env <span className="font-mono">{r.environmentName}</span>
                        {r.startedAt && (
                          <>
                            {" · "}started {new Date(r.startedAt).toLocaleString()}
                          </>
                        )}
                        {r.durationSeconds != null && (
                          <>
                            {" · "}
                            {r.durationSeconds < 60
                              ? `${r.durationSeconds}s`
                              : `${Math.floor(r.durationSeconds / 60)}m ${r.durationSeconds % 60}s`}
                          </>
                        )}
                      </div>
                    </div>
                    <code className="text-muted-foreground font-mono text-xs">
                      exit {r.exitCode ?? "—"}
                    </code>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </PageShell>
  );
}

// ---------------------------------------------------------------------------
// Scope A — Pod status grid
// ---------------------------------------------------------------------------

interface PodStatusGridLabels {
  title: string;
  headerStatus: string;
  headerCount: string;
  headerPercent: string;
  empty: string;
  podsCount: string;
  bucketEmpty: string;
  notReady: string;
}

function PodStatusGridCard({
  appSlug,
  buckets,
  loading,
  labels,
}: {
  appSlug: string;
  buckets: AstroliftWorkloadPodStatusBucket[];
  loading: boolean;
  labels: PodStatusGridLabels;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <LayersIcon className="size-4" />
          {labels.title}
          <Badge variant="outline" className="ml-2">
            {labels.podsCount}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {loading ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : buckets.length === 0 ? (
          <div className="text-muted-foreground p-6 text-sm">{labels.empty}</div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>{labels.headerStatus}</TableHead>
                <TableHead className="text-right">{labels.headerCount}</TableHead>
                <TableHead className="text-right">{labels.headerPercent}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {buckets.map((b) => (
                <PodStatusGridRow
                  key={b.status}
                  appSlug={appSlug}
                  bucket={b}
                  bucketEmptyLabel={labels.bucketEmpty}
                  notReadyLabel={labels.notReady}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function PodStatusGridRow({
  appSlug,
  bucket,
  bucketEmptyLabel,
  notReadyLabel,
}: {
  appSlug: string;
  bucket: AstroliftWorkloadPodStatusBucket;
  bucketEmptyLabel: string;
  notReadyLabel: string;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <TableRow
        className="hover:bg-accent/30 cursor-pointer"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <TableCell>
          <ChevronRightIcon
            className={cn("text-muted-foreground size-4 transition-transform", open && "rotate-90")}
          />
        </TableCell>
        <TableCell>
          <Badge className={cn("font-medium", statusClass(bucket.status))}>{bucket.status}</Badge>
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">{bucket.count}</TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {bucket.percent.toFixed(1)}%
        </TableCell>
      </TableRow>
      {open && (
        <TableRow className="bg-muted/30 hover:bg-muted/30">
          <TableCell />
          <TableCell colSpan={3} className="py-3">
            {bucket.pods.length === 0 ? (
              <span className="text-muted-foreground text-xs">{bucketEmptyLabel}</span>
            ) : (
              <ul className="space-y-1.5">
                {bucket.pods.map((p) => (
                  <li key={p.name} className="flex items-center justify-between gap-3 text-xs">
                    <Link
                      href={`/apps/${appSlug}/observability?pod=${encodeURIComponent(p.name)}`}
                      className="font-mono hover:underline"
                    >
                      {p.name}
                    </Link>
                    <span
                      className={cn(
                        "text-muted-foreground font-mono",
                        !p.ready && "text-rose-500 dark:text-rose-300"
                      )}
                    >
                      {formatAge(p.age)}
                      {!p.ready && ` · ${notReadyLabel}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Scope B — Pod table with restart column + reason tooltip
// ---------------------------------------------------------------------------

interface PodHealthLabels {
  title: string;
  empty: string;
  columnName: string;
  columnStatus: string;
  columnRestarts: string;
  columnNode: string;
  columnAge: string;
  flapping: string;
  restartReasonsHeading: string;
}

function PodHealthTableCard({
  appSlug,
  pods,
  loading,
  labels,
}: {
  appSlug: string;
  pods: AstroliftAppPod[];
  loading: boolean;
  labels: PodHealthLabels;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ActivityIcon className="size-4" />
          {labels.title}
          <Badge variant="outline" className="ml-2">
            {pods.length}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {loading ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : pods.length === 0 ? (
          <div className="text-muted-foreground p-6 text-sm">{labels.empty}</div>
        ) : (
          <TooltipProvider delayDuration={200}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{labels.columnName}</TableHead>
                  <TableHead>{labels.columnStatus}</TableHead>
                  <TableHead className="text-right">{labels.columnRestarts}</TableHead>
                  <TableHead>{labels.columnNode}</TableHead>
                  <TableHead className="text-right">{labels.columnAge}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pods.map((p) => {
                  const flapping = podIsFlapping(p);
                  const reasons = p.containerStatuses
                    .flatMap((c) => c.lastRestartReasons)
                    .slice(0, 3);
                  return (
                    <TableRow key={p.name}>
                      <TableCell>
                        <Link
                          href={`/apps/${appSlug}/observability?pod=${encodeURIComponent(p.name)}`}
                          className="font-mono text-xs hover:underline"
                        >
                          {p.name}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <Badge className={cn(statusClass(p.status))}>{p.status}</Badge>
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        <div className="flex items-center justify-end gap-2">
                          <RestartCell
                            restarts={p.restarts}
                            reasons={reasons}
                            heading={labels.restartReasonsHeading}
                          />
                          {flapping && (
                            <Badge className="bg-rose-500/15 text-rose-700 dark:text-rose-300">
                              <RotateCwIcon className="size-3" /> {labels.flapping}
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {p.node || "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {formatAge(p.age)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TooltipProvider>
        )}
      </CardContent>
    </Card>
  );
}

function RestartCell({
  restarts,
  reasons,
  heading,
}: {
  restarts: number;
  reasons: string[];
  heading: string;
}) {
  if (restarts === 0 || reasons.length === 0) {
    return <span>{restarts}</span>;
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help underline decoration-dotted underline-offset-4">
          {restarts}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="text-xs">
        <div className="mb-1 font-semibold">{heading}</div>
        <ul className="space-y-0.5">
          {reasons.map((r, i) => (
            <li key={`${r}-${i}`} className="font-mono">
              {i + 1}. {r}
            </li>
          ))}
        </ul>
      </TooltipContent>
    </Tooltip>
  );
}

// ---------------------------------------------------------------------------
// Scope C — Container split (Init / Primary / Sidecars)
// ---------------------------------------------------------------------------

interface ContainerSplitLabels {
  initTitle: string;
  initDescription: string;
  initEmpty: string;
  primaryTitle: string;
  primaryDescription: string;
  primaryEmpty: string;
  sidecarsTitle: string;
  sidecarsDescription: string;
  sidecarsEmpty: string;
  image: string;
  cpuReqLimit: string;
  memReqLimit: string;
  restarts: string;
  empty: string;
}

function ContainerSplitCard({
  manifestContainers,
  liveContainers,
  loading,
  labels,
}: {
  manifestContainers: AstroliftContainer[];
  liveContainers: AstroliftContainerStatus[];
  loading: boolean;
  labels: ContainerSplitLabels;
}) {
  // Manifest containers carry image / declared probe info, live
  // containers carry kind + per-container resources. Merge by name
  // so we render one card per actual container — falling back to
  // either side when the other doesn't have a row yet.
  const byName = new Map<
    string,
    {
      name: string;
      manifest?: AstroliftContainer;
      live?: AstroliftContainerStatus;
    }
  >();
  for (const m of manifestContainers) {
    byName.set(m.name, { name: m.name, manifest: m });
  }
  for (const l of liveContainers) {
    const existing = byName.get(l.name);
    if (existing) existing.live = l;
    else byName.set(l.name, { name: l.name, live: l });
  }

  const init: typeof byName extends Map<unknown, infer V> ? V[] : never = [];
  const primary: typeof init = [];
  const sidecars: typeof init = [];
  for (const entry of byName.values()) {
    const kind: ContainerKind =
      entry.live?.kind ?? (entry.manifest?.isPrimary ? "primary" : "sidecar");
    if (kind === "init") init.push(entry);
    else if (kind === "primary") primary.push(entry);
    else sidecars.push(entry);
  }

  if (loading && byName.size === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Containers</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (byName.size === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Containers</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">{labels.empty}</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-3 lg:grid-cols-3">
      <ContainerGroupCard
        title={labels.initTitle}
        description={labels.initDescription}
        icon={PackageIcon}
        entries={init}
        emptyHint={labels.initEmpty}
        fieldLabels={labels}
      />
      <ContainerGroupCard
        title={labels.primaryTitle}
        description={labels.primaryDescription}
        icon={BoxIcon}
        entries={primary}
        emptyHint={labels.primaryEmpty}
        fieldLabels={labels}
      />
      <ContainerGroupCard
        title={labels.sidecarsTitle}
        description={labels.sidecarsDescription}
        icon={PuzzleIcon}
        entries={sidecars}
        emptyHint={labels.sidecarsEmpty}
        fieldLabels={labels}
      />
    </div>
  );
}

function ContainerGroupCard({
  title,
  description,
  icon: Icon,
  entries,
  emptyHint,
  fieldLabels,
}: {
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  entries: Array<{
    name: string;
    manifest?: AstroliftContainer;
    live?: AstroliftContainerStatus;
  }>;
  emptyHint: string;
  fieldLabels: ContainerSplitLabels;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Icon className="size-4" />
          {title}
          <Badge variant="outline" className="ml-auto">
            {entries.length}
          </Badge>
        </CardTitle>
        <p className="text-muted-foreground mt-1 text-xs">{description}</p>
      </CardHeader>
      <CardContent className="space-y-3">
        {entries.length === 0 ? (
          <p className="text-muted-foreground text-xs">{emptyHint}</p>
        ) : (
          entries.map((e) => (
            <ContainerCard
              key={e.name}
              name={e.name}
              manifest={e.manifest}
              live={e.live}
              fieldLabels={fieldLabels}
            />
          ))
        )}
      </CardContent>
    </Card>
  );
}

function ContainerCard({
  name,
  manifest,
  live,
  fieldLabels,
}: {
  name: string;
  manifest?: AstroliftContainer;
  live?: AstroliftContainerStatus;
  fieldLabels: ContainerSplitLabels;
}) {
  const image = live?.image || manifest?.imageRef || "";
  const res = live?.resources;
  const cpuReq = res?.cpuRequest;
  const cpuLim = res?.cpuLimit;
  const memReq = res?.memoryRequest;
  const memLim = res?.memoryLimit;
  return (
    <div className="border-muted rounded-md border p-3 text-sm">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-mono">{name}</span>
        {manifest?.port && manifest.port > 0 && (
          <Badge variant="outline" className="font-mono text-xs">
            :{manifest.port}
          </Badge>
        )}
        {manifest && (
          <Badge variant="secondary" className="ml-auto gap-1 text-xs">
            <ActivityIcon className="size-3" />
            {HEALTHCHECK_LABEL[manifest.healthcheckKind] ?? manifest.healthcheckKind}
          </Badge>
        )}
      </div>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-2">
        <Field label={fieldLabels.image} mono value={emptyOrValue(image)} />
        <Field
          label={fieldLabels.cpuReqLimit}
          mono
          value={`${emptyOrValue(cpuReq)} / ${emptyOrValue(cpuLim)}`}
        />
        <Field
          label={fieldLabels.memReqLimit}
          mono
          value={`${emptyOrValue(memReq)} / ${emptyOrValue(memLim)}`}
        />
        {live?.restarts != null && (
          <Field label={fieldLabels.restarts} mono value={String(live.restarts)} />
        )}
      </dl>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scope D — In-cluster service FQDN copy badge
// ---------------------------------------------------------------------------

interface ServiceFqdnLabels {
  label: string;
  copyTitle: string;
  copyToast: string;
  copyError: string;
  unavailable: string;
}

function ServiceFqdnField({ fqdn, labels }: { fqdn: string; labels: ServiceFqdnLabels }) {
  const onCopy = React.useCallback(async () => {
    if (!fqdn) return;
    try {
      await navigator.clipboard.writeText(fqdn);
      toast.success(labels.copyToast);
    } catch {
      toast.error(labels.copyError);
    }
  }, [fqdn, labels.copyToast, labels.copyError]);

  return (
    <div className="sm:col-span-3">
      <dt className="text-muted-foreground text-xs tracking-wide uppercase">{labels.label}</dt>
      <dd className="mt-1">
        {fqdn ? (
          <button
            type="button"
            onClick={onCopy}
            className={cn(
              "bg-muted/60 hover:bg-muted inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-left",
              "font-mono text-xs transition-colors"
            )}
            title={labels.copyTitle}
            aria-label={`${labels.copyTitle}: ${fqdn}`}
          >
            <span>{fqdn}</span>
            <CopyIcon className="text-muted-foreground size-3" />
          </button>
        ) : (
          <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
            <AlertTriangleIcon className="size-3" />
            {labels.unavailable}
          </span>
        )}
      </dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small shared field renderer (kept in this file so unrelated workload
// pages don't pull it as a shared dep).
// ---------------------------------------------------------------------------

function Field({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs tracking-wide uppercase">{label}</dt>
      <dd className={mono ? "font-mono text-sm" : "text-sm"}>{value}</dd>
    </div>
  );
}

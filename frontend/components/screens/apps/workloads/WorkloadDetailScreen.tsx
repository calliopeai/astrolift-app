"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  BoxIcon,
  CalendarClockIcon,
  CopyIcon,
  DatabaseIcon,
  GitBranchIcon,
  GlobeIcon,
  HardDriveIcon,
  LayersIcon,
  PackageIcon,
  PuzzleIcon,
  RotateCwIcon,
  ShieldCheckIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { type SelectRowsSpec, selectRows } from "@/components/list/select-rows";
import {
  type ListDefinition,
  type ListStateController,
  standardViews,
  useLocalListState,
} from "@/components/list/use-list-state";
import { Panel, PanelGrid, SkeletonRows } from "@/components/panel/Panel";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type {
  AstroliftAppPod,
  AstroliftContainerStatus,
  AstroliftWorkloadPodStatusBucket,
  ContainerKind,
} from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftContainer } from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

import type { useWorkloadDetail } from "./use-workload-detail";

export type WorkloadDetailScreenProps = ReturnType<typeof useWorkloadDetail> & {
  appSlug: string;
  workloadSlug: string;
  /** `/apps` normally, `/agents` inside the agent shell. */
  basePath: string;
  /** Live resource usage card (fetches its own data). */
  resourceUsage?: React.ReactNode;
  /** Scaling card (fetches its own data); not shown for cronjobs. */
  scaling?: React.ReactNode;
  /** Rendered-manifest card (fetches its own data). */
  manifest?: React.ReactNode;
};

/** Slug-scoped path under the active base, as appPath builds it. */
function appHref(basePath: string, slug: string, ...segments: string[]): string {
  const base = `${basePath}/${slug}`;
  return segments.length === 0 ? base : `${base}/${segments.join("/")}`;
}

const HEALTHCHECK_LABEL: Record<string, string> = {
  none: "no probe",
  http: "HTTP probe",
  tcp: "TCP probe",
  exec: "exec probe",
};

// Bucket-color classes for the status grid. Anything outside the
// known set falls through to ``muted`` so a fresh kubelet reason
// doesn't crash the surface.
const STATUS_VARIANT: Record<string, string> = {
  Running: "bg-success/10 text-success-fg",
  Succeeded: "bg-success/10 text-success-fg",
  Pending: "bg-warning/10 text-warning-fg",
  ContainerCreating: "bg-warning/10 text-warning-fg",
  Terminating: "bg-foreground/5 text-muted-foreground",
  Unknown: "bg-foreground/5 text-muted-foreground",
  CrashLoopBackOff: "bg-danger/10 text-danger-fg",
  ImagePullBackOff: "bg-danger/10 text-danger-fg",
  ErrImagePull: "bg-danger/10 text-danger-fg",
  CreateContainerConfigError: "bg-danger/10 text-danger-fg",
  CreateContainerError: "bg-danger/10 text-danger-fg",
  InvalidImageName: "bg-danger/10 text-danger-fg",
  OOMKilled: "bg-danger/10 text-danger-fg",
  Error: "bg-danger/10 text-danger-fg",
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

/**
 * The detail page's sections, one at a time, so the page stays about two
 * screens and shows one list: the overview (the workload, its usage and
 * scaling, the pod-status breakdown), then pods, containers with their
 * probes, volumes and the rendered manifest.
 */
type WorkloadSection = "overview" | "pods" | "containers" | "volumes" | "manifest";

const NOT_PERSONAL = "Pods and volumes are the workload's, not a person's, so Mine is empty.";

/** The client-side step's input, from a list's own state. */
function pageOf(list: ListStateController) {
  return {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  };
}

function optionsOf(values: string[]) {
  return [...new Set(values.filter(Boolean))].sort().map((v) => ({ value: v, label: v }));
}

/**
 * Pods as an embedded list: the live pod list arrives whole, so search,
 * filters, sort and numbered pages run in the client.
 */
function podsList(pods: AstroliftAppPod[], flappingLabel: string): ListDefinition {
  return {
    id: "apps.workload.pods",
    fields: [
      { key: "status", label: "Status", options: optionsOf(pods.map((p) => p.status)) },
      { key: "node", label: "Node", options: optionsOf(pods.map((p) => p.node ?? "")) },
      { key: "flapping", label: "Flapping", options: [{ value: "yes", label: flappingLabel }] },
    ],
    searchPlaceholder: "Search pods, nodes…",
    defaultSort: [{ key: "order", dir: "asc" }],
    views: standardViews({ owner: "me" }, [], { mineNote: NOT_PERSONAL }),
    paging: "numbered",
    pageSizes: [25, 50, 100],
  };
}

function podsSelect(pods: AstroliftAppPod[]): SelectRowsSpec<AstroliftAppPod> {
  // The query's own order is the default.
  const order = new Map(pods.map((p, i) => [p.name, i]));
  return {
    filter: {
      owner: () => false,
      status: (p, value) => p.status === value,
      node: (p, value) => p.node === value,
      flapping: (p) => podIsFlapping(p),
    },
    text: (p) => [p.name, p.node, p.status],
    sort: {
      order: (p) => order.get(p.name) ?? 0,
      name: (p) => p.name,
      status: (p) => p.status,
      restarts: (p) => p.restarts,
      node: (p) => p.node ?? "",
      age: (p) => -(Date.parse(p.age ?? "") || 0),
    },
    id: (p) => p.name,
  };
}

export function WorkloadDetailScreen({
  workload: w,
  workloadLoading: wlLoading,
  containers,
  containersLoading: cLoading,
  pods: podRows,
  podsLoading: pdLoading,
  buckets,
  bucketsLoading: brLoading,
  liveContainers,
  isCronjob,
  runs,
  runsLoading: rLoading,
  appSlug,
  basePath,
  resourceUsage,
  scaling,
  manifest,
}: WorkloadDetailScreenProps) {
  const t = useTranslations("apps.workloadDetail");
  const [section, setSection] = React.useState<WorkloadSection>("overview");
  const pods = useLocalListState(podsList(podRows, t("pods.flapping")));

  if (wlLoading && !w) {
    return (
      <PanelGrid>
        <Panel title="Workload" icon={<BoxIcon className="size-4" />} loading />
        <Panel title="Pod status" span={6} loading />
        <Panel title="Pods" span={6} loading />
      </PanelGrid>
    );
  }

  if (!w) {
    return (
      <PanelGrid>
        <Panel
          title="Workload"
          icon={<BoxIcon className="size-4" />}
          empty={{
            icon: <AlertTriangleIcon className="size-5" />,
            title: "Workload not found",
            description: "The workload doesn't exist or you don't have permission to view it.",
            actionHref: appHref(basePath, appSlug, "workloads"),
            actionLabel: "Back to workloads",
          }}
        />
      </PanelGrid>
    );
  }

  const volumeRows: VolumeDeclDict[] = Array.isArray(w.volumes)
    ? (w.volumes as VolumeDeclDict[]).filter((v) => v && typeof v === "object")
    : [];
  const sections: { id: WorkloadSection; label: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "pods", label: t("pods.title") },
    { id: "containers", label: "Containers" },
    ...(volumeRows.length > 0 ? [{ id: "volumes" as const, label: t("volumes.title") }] : []),
    ...(manifest ? [{ id: "manifest" as const, label: "Manifest" }] : []),
  ];
  const active = sections.some((x) => x.id === section) ? section : "overview";
  const showPods = (status: string) => {
    pods.setFilter("status", status);
    setSection("pods");
  };

  return (
    <PanelGrid>
      <div
        role="tablist"
        aria-label="Workload sections"
        className="bg-muted/40 col-span-12 flex w-fit max-w-full flex-wrap gap-1 rounded-md border p-1"
      >
        {sections.map((x) => (
          <button
            key={x.id}
            type="button"
            role="tab"
            aria-selected={active === x.id}
            onClick={() => setSection(x.id)}
            className={cn(
              "rounded px-3 py-1 text-sm font-medium transition",
              active === x.id
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            {x.label}
          </button>
        ))}
      </div>

      {active === "overview" ? (
        <>
          <Panel
            title={w.slug}
            icon={<BoxIcon className="size-4" />}
            description={`${w.kind} workload from the manifest. ${w.replicas} replica${w.replicas === 1 ? "" : "s"}.`}
            actions={
              <>
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
                <Button variant="outline" size="sm" asChild>
                  <Link href={appHref(basePath, appSlug, "manifest")}>
                    <GitBranchIcon className="size-4" />
                    Manifest preview
                  </Link>
                </Button>
              </>
            }
          >
            <div className="grid min-w-0 grid-cols-2 gap-x-8 gap-y-3 text-sm sm:grid-cols-3">
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
            </div>
          </Panel>

          {resourceUsage}

          {w.kind !== "cronjob" && scaling}

          <PodStatusGridCard
            buckets={buckets}
            loading={brLoading && buckets.length === 0}
            onShowPods={showPods}
            labels={{
              title: t("podStatus.title"),
              headerCount: t("podStatus.headerCount"),
              headerPercent: t("podStatus.headerPercent"),
              empty: t("podStatus.empty"),
              podsCount: t("podStatus.podsCount", {
                count: buckets.reduce((a, b) => a + b.count, 0),
              }),
            }}
          />

          {isCronjob && (
            <Panel
              title="Recent runs"
              icon={<CalendarClockIcon className="size-4" />}
              actions={
                <Badge variant="outline" className="font-mono">
                  {runs.length}
                </Badge>
              }
              loading={rLoading && runs.length === 0}
              skeleton={<SkeletonRows count={2} />}
              flush={runs.length > 0}
            >
              {runs.length === 0 ? (
                <p className="text-muted-foreground text-sm">No scheduled runs recorded yet.</p>
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
            </Panel>
          )}
        </>
      ) : active === "pods" ? (
        <PodHealthTableCard
          appSlug={appSlug}
          basePath={basePath}
          list={pods}
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
      ) : active === "containers" ? (
        <>
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

          <ProbesCard
            containers={containers}
            labels={{
              title: t("probes.title"),
              description: t("probes.description"),
              startup: t("probes.startup"),
              readiness: t("probes.readiness"),
              liveness: t("probes.liveness"),
              notConfigured: t("probes.notConfigured"),
              container: t("probes.container"),
              empty: t("probes.empty"),
            }}
          />
        </>
      ) : active === "volumes" ? (
        <VolumesCard
          rows={volumeRows}
          labels={{
            title: t("volumes.title"),
            description: t("volumes.description"),
            columnName: t("volumes.columnName"),
            columnKind: t("volumes.columnKind"),
            columnMount: t("volumes.columnMount"),
            columnDetail: t("volumes.columnDetail"),
          }}
        />
      ) : (
        manifest
      )}
    </PanelGrid>
  );
}

// ---------------------------------------------------------------------------
// Scope A — Pod status grid
// ---------------------------------------------------------------------------

interface PodStatusGridLabels {
  title: string;
  headerCount: string;
  headerPercent: string;
  empty: string;
  podsCount: string;
}

/**
 * The pod-status breakdown: a count per status, not a list. Each status
 * opens the Pods section filtered to it.
 */
function PodStatusGridCard({
  buckets,
  loading,
  onShowPods,
  labels,
}: {
  buckets: AstroliftWorkloadPodStatusBucket[];
  loading: boolean;
  onShowPods: (status: string) => void;
  labels: PodStatusGridLabels;
}) {
  return (
    <Panel
      title={labels.title}
      icon={<LayersIcon className="size-4" />}
      span={6}
      actions={
        <Badge variant="outline" className="font-mono">
          {labels.podsCount}
        </Badge>
      }
      loading={loading}
      skeleton={<SkeletonRows count={2} />}
    >
      {buckets.length === 0 ? (
        <p className="text-muted-foreground text-sm">{labels.empty}</p>
      ) : (
        <ul className="flex min-w-0 flex-wrap gap-2">
          {buckets.map((b) => (
            <li key={b.status} className="min-w-0">
              <button
                type="button"
                onClick={() => onShowPods(b.status)}
                className="hover:bg-accent/30 flex min-w-0 items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm transition-colors"
              >
                <Badge className={cn("font-medium", statusClass(b.status))}>{b.status}</Badge>
                <span className="font-mono tabular-nums" title={labels.headerCount}>
                  {b.count}
                </span>
                <span
                  className="text-muted-foreground font-mono text-xs tabular-nums"
                  title={labels.headerPercent}
                >
                  {b.percent.toFixed(1)}%
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Scope B: pod list with restart column + reason tooltip
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
  basePath,
  list,
  pods,
  loading,
  labels,
}: {
  appSlug: string;
  basePath: string;
  list: ListStateController;
  pods: AstroliftAppPod[];
  loading: boolean;
  labels: PodHealthLabels;
}) {
  const page = selectRows(pods, pageOf(list), podsSelect(pods));
  const columns: Column<AstroliftAppPod>[] = [
    {
      id: "name",
      header: labels.columnName,
      sortKey: "name",
      cell: (p) => (
        <Link
          href={`${appHref(basePath, appSlug, "observability")}?pod=${encodeURIComponent(p.name)}`}
          className="font-mono text-xs [overflow-wrap:anywhere] hover:underline"
        >
          {p.name}
        </Link>
      ),
    },
    {
      id: "status",
      header: labels.columnStatus,
      sortKey: "status",
      cell: (p) => <Badge className={cn(statusClass(p.status))}>{p.status}</Badge>,
    },
    {
      id: "restarts",
      header: labels.columnRestarts,
      sortKey: "restarts",
      align: "right",
      cellClassName: "font-mono tabular-nums",
      cell: (p) => (
        <div className="flex items-center justify-end gap-2">
          <RestartCell
            restarts={p.restarts}
            reasons={p.containerStatuses.flatMap((c) => c.lastRestartReasons).slice(0, 3)}
            heading={labels.restartReasonsHeading}
          />
          {podIsFlapping(p) && (
            <Badge className="bg-danger/15 text-danger-fg">
              <RotateCwIcon className="size-3" /> {labels.flapping}
            </Badge>
          )}
        </div>
      ),
    },
    {
      id: "node",
      header: labels.columnNode,
      sortKey: "node",
      cellClassName: "text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]",
      cell: (p) => emptyOrValue(p.node),
    },
    {
      id: "age",
      header: labels.columnAge,
      sortKey: "age",
      align: "right",
      cellClassName: "font-mono text-xs",
      cell: (p) => formatAge(p.age),
    },
  ];
  return (
    <Panel
      title={labels.title}
      icon={<ActivityIcon className="size-4" />}
      actions={
        <Badge variant="outline" className="font-mono">
          {pods.length}
        </Badge>
      }
    >
      <TooltipProvider delayDuration={200}>
        <ListPage<AstroliftAppPod>
          embedded
          list={list}
          label={labels.title}
          columns={columns}
          rows={page.rows}
          getRowId={(p) => p.name}
          totalCount={page.totalCount}
          loading={loading}
          empty={{ icon: <ActivityIcon className="size-5" />, title: labels.empty }}
        />
      </TooltipProvider>
    </Panel>
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

  if (byName.size === 0) {
    return (
      <Panel
        title="Containers"
        icon={<BoxIcon className="size-4" />}
        loading={loading}
        skeleton={<Skeleton className="h-24 w-full" />}
      >
        <p className="text-muted-foreground text-sm">{labels.empty}</p>
      </Panel>
    );
  }

  return (
    <>
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
    </>
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
    <Panel
      title={title}
      icon={<Icon className="size-4" />}
      description={description}
      span={4}
      actions={
        <Badge variant="outline" className="font-mono">
          {entries.length}
        </Badge>
      }
    >
      <div className="min-w-0 space-y-3">
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
      </div>
    </Panel>
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
  // Init-container debuggability needs the live container state alongside
  // the image so an operator looking at the Init group can immediately tell
  // whether the init has Completed (good), is Waiting on an image pull, or
  // is Terminated with a non-zero exit. Reuses the same colour vocabulary
  // as the pod-status grid above so the surface reads coherently.
  const liveState = live?.state;
  const liveReason = live?.waitingReason || live?.terminatedReason || "";
  return (
    <div className="border-muted rounded-md border p-3 text-sm">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-mono">{name}</span>
        {manifest?.port && manifest.port > 0 && (
          <Badge variant="outline" className="font-mono text-xs">
            :{manifest.port}
          </Badge>
        )}
        {liveState && (
          <Badge className={cn("gap-1 text-xs", containerStateClass(liveState, live?.ready))}>
            <StatusDot status={containerStateDot(liveState, live?.ready)} />
            <span className="capitalize">{liveState}</span>
            {liveReason && (
              <span className="text-muted-foreground ml-1 font-mono">{liveReason}</span>
            )}
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

// Live container `state` → coloured badge. `running` + ready is the only
// "everything's fine" path; `running` without ready means the readiness
// probe is still failing (operator needs to know). `terminated` is success
// for init containers (Completed exit 0) and failure for primary containers
// — we surface the raw state and let the operator dig into the reason
// string for terminal failure modes (OOMKilled, Error, …).
function containerStateClass(state: string, ready?: boolean): string {
  if (state === "running" && ready) return STATUS_VARIANT.Running;
  if (state === "running") return STATUS_VARIANT.Pending;
  if (state === "terminated") return STATUS_VARIANT.Succeeded;
  if (state === "waiting") return STATUS_VARIANT.ContainerCreating;
  return "bg-muted text-muted-foreground";
}

function containerStateDot(
  state: string,
  ready?: boolean
): "ok" | "warn" | "error" | "muted" | "pending" {
  if (state === "running" && ready) return "ok";
  if (state === "running") return "warn";
  if (state === "terminated") return "ok";
  if (state === "waiting") return "pending";
  return "muted";
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

// ---------------------------------------------------------------------------
// Scope E — Volumes (manifest-declared) (#669)
//
// Reads ``astroliftWorkload.volumes`` — a JSON array carrying the parsed
// VolumeDecl dict shape from the manifest parser. Snake-case keys
// (``mount_path``, ``storage_class``, ``source_name``, …) because the
// backend persists them through ``persist_manifest`` as-is. Hidden when
// the workload declares no volumes.
// ---------------------------------------------------------------------------

interface VolumesLabels {
  title: string;
  description: string;
  columnName: string;
  columnKind: string;
  columnMount: string;
  columnDetail: string;
}

type VolumeDeclDict = {
  name?: string;
  kind?: string;
  mount_path?: string;
  size?: string;
  storage_class?: string;
  access_mode?: string;
  source_name?: string;
  size_limit?: string;
  [key: string]: unknown;
};

const VOLUME_KIND_LABEL: Record<string, string> = {
  pvc: "PVC",
  empty_dir: "emptyDir",
  config_map: "ConfigMap",
  secret: "Secret",
};

const VOLUME_KIND_VARIANT: Record<string, string> = {
  pvc: "bg-info/10 text-info-fg",
  empty_dir: "bg-foreground/5 text-muted-foreground",
  config_map: "bg-warning/10 text-warning-fg",
  secret: "bg-danger/10 text-danger-fg",
};

function volumeDetail(v: VolumeDeclDict): string {
  if (v.kind === "pvc") {
    const sc = v.storage_class || "default";
    const mode = v.access_mode || "ReadWriteOnce";
    return `${sc} · ${emptyOrValue(v.size)} · ${mode}`;
  }
  if (v.kind === "empty_dir") {
    return v.size_limit ? `sizeLimit ${v.size_limit}` : "ephemeral";
  }
  if (v.kind === "config_map" || v.kind === "secret") {
    return v.source_name ? `source ${v.source_name}` : "—";
  }
  return "—";
}

const volumeKey = (v: VolumeDeclDict, i: number) => `${v.name || "vol"}-${i}`;

const VOLUMES_LIST: ListDefinition = {
  id: "apps.workload.volumes",
  fields: [
    {
      key: "kind",
      label: "Kind",
      options: Object.entries(VOLUME_KIND_LABEL).map(([value, label]) => ({ value, label })),
    },
  ],
  searchPlaceholder: "Search volumes, mount paths…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ owner: "me" }, [], { mineNote: NOT_PERSONAL }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

type VolumeRow = VolumeDeclDict & { key: string };

const VOLUMES_SELECT: SelectRowsSpec<VolumeRow> = {
  filter: { owner: () => false, kind: (v, value) => (v.kind || "").toLowerCase() === value },
  text: (v) => [v.name, v.kind, v.mount_path, v.source_name, v.storage_class],
  sort: {
    name: (v) => (v.name || "").toLowerCase(),
    kind: (v) => (v.kind || "").toLowerCase(),
    mount: (v) => v.mount_path || "",
  },
  id: (v) => v.key,
};

/** The manifest's volumes (nested in the workload payload), as an embedded list. */
function VolumesCard({ rows, labels }: { rows: VolumeDeclDict[]; labels: VolumesLabels }) {
  const list = useLocalListState(VOLUMES_LIST);
  const keyed: VolumeRow[] = rows.map((v, i) => ({ ...v, key: volumeKey(v, i) }));
  const page = selectRows(keyed, pageOf(list), VOLUMES_SELECT);
  const columns: Column<VolumeRow>[] = [
    {
      id: "name",
      header: labels.columnName,
      sortKey: "name",
      cellClassName: "font-mono text-xs [overflow-wrap:anywhere]",
      cell: (v) => emptyOrValue(v.name),
    },
    {
      id: "kind",
      header: labels.columnKind,
      sortKey: "kind",
      cell: (v) => {
        const kindKey = (v.kind || "").toLowerCase();
        return (
          <Badge className={cn("font-mono text-xs", VOLUME_KIND_VARIANT[kindKey] ?? "")}>
            <DatabaseIcon className="size-3" />
            {VOLUME_KIND_LABEL[kindKey] ?? emptyOrValue(kindKey)}
          </Badge>
        );
      },
    },
    {
      id: "mount",
      header: labels.columnMount,
      sortKey: "mount",
      cellClassName: "font-mono text-xs [overflow-wrap:anywhere]",
      cell: (v) => emptyOrValue(v.mount_path),
    },
    {
      id: "detail",
      header: labels.columnDetail,
      cellClassName: "text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]",
      cell: (v) => volumeDetail(v),
    },
  ];
  return (
    <Panel
      title={labels.title}
      icon={<HardDriveIcon className="size-4" />}
      description={labels.description}
      actions={
        <Badge variant="outline" className="font-mono">
          {rows.length}
        </Badge>
      }
    >
      <ListPage<VolumeRow>
        embedded
        list={list}
        label={labels.title}
        columns={columns}
        rows={page.rows}
        getRowId={(v) => v.key}
        totalCount={page.totalCount}
        empty={{ icon: <HardDriveIcon className="size-5" />, title: labels.title }}
      />
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Scope F — Probe configs (read-only) (#669)
//
// One row per probe slot (startup / readiness / liveness) per container.
// Probe JSON mirrors the Kubernetes probe shape — ``httpGet`` / ``exec`` /
// ``tcpSocket`` is the action, the rest of the fields tune timings. We
// render the action inline + the three knobs an operator actually reads
// when a probe is flapping: initialDelaySeconds, periodSeconds,
// failureThreshold. Edits live in the manifest, not here — this is
// debuggability only.
// ---------------------------------------------------------------------------

interface ProbesLabels {
  title: string;
  description: string;
  startup: string;
  readiness: string;
  liveness: string;
  notConfigured: string;
  container: string;
  empty: string;
}

type KubeProbe = {
  httpGet?: { path?: string; port?: number | string; scheme?: string };
  exec?: { command?: string[] };
  tcpSocket?: { port?: number | string };
  initialDelaySeconds?: number;
  periodSeconds?: number;
  timeoutSeconds?: number;
  successThreshold?: number;
  failureThreshold?: number;
  [key: string]: unknown;
};

function describeProbe(probe: KubeProbe | null | undefined): string {
  if (!probe) return "";
  if (probe.httpGet) {
    const scheme = (probe.httpGet.scheme || "HTTP").toUpperCase();
    const path = probe.httpGet.path || "/";
    const port = probe.httpGet.port ?? "";
    return `${scheme} GET ${path}${port !== "" ? `:${port}` : ""}`;
  }
  if (probe.exec) {
    const cmd = Array.isArray(probe.exec.command) ? probe.exec.command.join(" ") : "";
    return cmd ? `Exec ${cmd}` : "Exec";
  }
  if (probe.tcpSocket) {
    return `TCP ${probe.tcpSocket.port ?? ""}`.trim();
  }
  return "—";
}

function probeTimings(probe: KubeProbe | null | undefined): string {
  if (!probe) return "";
  const parts: string[] = [];
  if (probe.initialDelaySeconds != null) parts.push(`initial ${probe.initialDelaySeconds}s`);
  if (probe.periodSeconds != null) parts.push(`every ${probe.periodSeconds}s`);
  if (probe.failureThreshold != null) parts.push(`fail × ${probe.failureThreshold}`);
  return parts.join(" · ");
}

function ProbeItem({
  label,
  probe,
  notConfigured,
}: {
  label: string;
  probe: KubeProbe | null | undefined;
  notConfigured: string;
}) {
  return (
    <div className="grid min-w-0 grid-cols-1 gap-x-4 gap-y-0.5 py-2 sm:grid-cols-[8rem_1fr]">
      <dt className="text-sm font-medium">{label}</dt>
      <dd className="min-w-0">
        {probe ? (
          <span className="flex min-w-0 flex-wrap gap-x-4 gap-y-0.5">
            <span className="font-mono text-xs [overflow-wrap:anywhere]">
              {describeProbe(probe)}
            </span>
            <span className="text-muted-foreground font-mono text-xs">
              {emptyOrValue(probeTimings(probe))}
            </span>
          </span>
        ) : (
          <Badge variant="outline" className="text-muted-foreground">
            {notConfigured}
          </Badge>
        )}
      </dd>
    </div>
  );
}

function ProbesCard({
  containers,
  labels,
}: {
  containers: AstroliftContainer[];
  labels: ProbesLabels;
}) {
  // Render probes for every manifest container that declares at least one.
  // Primary is the canonical case; init containers can also declare probes
  // (rare but legal in Kubernetes), so we don't hard-filter to primary.
  const rows = containers.filter((c) => c.startupProbe || c.readinessProbe || c.livenessProbe);
  if (rows.length === 0) {
    return (
      <Panel
        title={labels.title}
        icon={<ShieldCheckIcon className="size-4" />}
        description={labels.description}
        span={6}
      >
        <p className="text-muted-foreground text-xs">{labels.empty}</p>
      </Panel>
    );
  }
  return (
    <Panel
      title={labels.title}
      icon={<ShieldCheckIcon className="size-4" />}
      description={labels.description}
      span={6}
      actions={
        <Badge variant="outline" className="font-mono">
          {rows.length}
        </Badge>
      }
    >
      <div className="min-w-0 space-y-4">
        {rows.map((c) => (
          <div key={c.id} className="space-y-2">
            <div className="flex items-center gap-2 text-xs">
              <span className="text-muted-foreground tracking-wide uppercase">
                {labels.container}
              </span>
              <span className="font-mono">{c.name}</span>
              {c.isPrimary && (
                <Badge variant="secondary" className="text-xs">
                  primary
                </Badge>
              )}
            </div>
            <dl className="divide-y">
              <ProbeItem
                label={labels.startup}
                probe={c.startupProbe as KubeProbe | null}
                notConfigured={labels.notConfigured}
              />
              <ProbeItem
                label={labels.readiness}
                probe={c.readinessProbe as KubeProbe | null}
                notConfigured={labels.notConfigured}
              />
              <ProbeItem
                label={labels.liveness}
                probe={c.livenessProbe as KubeProbe | null}
                notConfigured={labels.notConfigured}
              />
            </dl>
          </div>
        ))}
      </div>
    </Panel>
  );
}

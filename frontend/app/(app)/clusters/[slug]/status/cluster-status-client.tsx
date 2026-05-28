"use client";

import { useQuery } from "@apollo/client/react";
import { AlertTriangleIcon, CheckIcon, RocketIcon, XIcon } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CLUSTER_HEALTH,
  CLUSTER_LIFECYCLE_AUDIT,
  CLUSTER_PROMETHEUS_METRICS,
  CLUSTER_WORKLOAD_HEALTH,
  LIST_CLUSTERS,
  RECENT_CLUSTER_WORKFLOWS,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

import { ClusterTabs } from "../components/cluster-tabs";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

/**
 * Cluster status tab (#68 / #362) — second tab on the cluster detail
 * page. This is PR 1 of the 4-PR breakdown filed on the ticket:
 *
 *   - PR 1 (this): tab routing skeleton + Overview / Status nav
 *   - PR 2: slice 2 cards (lifecycle audit timeline, apps-bound full
 *           list, recent workflows — wired against #393 / #394 / a
 *           new ``astroliftClusterLifecycleAudit`` resolver)
 *   - PR 3: slice 1 cards (control plane reachability, pod-phase
 *           rollup, recent K8s warning events — needs the
 *           ``ClusterDriver.list_events`` + ``list_pods`` Protocol
 *           extension)
 *   - PR 4: slice 3 (Prometheus metrics, capability-gated)
 *
 * For now the page shows the cluster's basic identity + a callout
 * pointing at the upcoming slices so the route is discoverable
 * before the cards land.
 */
export function ClusterStatusClient({ slug }: { slug: string }) {
  const { data, loading } = useQuery<Resp>(LIST_CLUSTERS);
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug);

  if (loading && !cluster) {
    return (
      <PageShell title="Cluster status" description="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!cluster) {
    return (
      <PageShell
        title="Cluster not found"
        description="The cluster doesn't exist or you don't have permission to view it."
      >
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No cluster with slug ${slug}`}
          actionHref="/clusters"
          actionLabel="Back to clusters"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={`${cluster.name} · Status`}
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{cluster.slug}</span>
          <Badge variant="secondary">{cluster.providerPluginSlug}</Badge>
        </span>
      }
    >
      <ClusterTabs slug={slug} active="status" />

      <LiveHealthCard clusterId={cluster.id} />

      <WorkloadHealthCard clusterId={cluster.id} />

      <RecentWorkflowsCard clusterId={cluster.id} />

      <LifecycleTimelineCard clusterId={cluster.id} />

      <ClusterMetricsCard clusterId={cluster.id} />
    </PageShell>
  );
}

// ─── Lifecycle timeline card (#68 slice 2) ───────────────────────────────
// Backed by ``astroliftClusterLifecycleAudit`` — filters MutationAuditLog
// for cluster-targeted operations referencing this cluster's guid/slug
// and renders a vertical timeline (operation, outcome, actor, time).

interface LifecycleAuditEntry {
  operation: string;
  variables: Record<string, unknown>;
  success: boolean;
  errors: string[];
  timestamp: string;
  actor: string | null;
}

interface LifecycleAuditResp {
  astroliftClusterLifecycleAudit: LifecycleAuditEntry[];
}

function LifecycleTimelineCard({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<LifecycleAuditResp>(CLUSTER_LIFECYCLE_AUDIT, {
    variables: { clusterId, limit: 50 },
    pollInterval: 30000,
  });
  const entries = data?.astroliftClusterLifecycleAudit ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Lifecycle + activity</CardTitle>
        <CardDescription>
          Every mutation that targeted this cluster — registered →
          managing → managed transitions, refreshes, prereq installs,
          decommission attempts. Sourced from the platform audit log.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && entries.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : entries.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No lifecycle events recorded yet. Operator mutations
            against this cluster will show up here.
          </p>
        ) : (
          <ol className="border-border relative space-y-3 border-l pl-6">
            {entries.map((e, i) => (
              <li key={`${e.timestamp}-${i}`} className="relative">
                <span
                  className={
                    "absolute -left-[27px] flex size-5 items-center justify-center rounded-full " +
                    (e.success
                      ? "bg-emerald-500/15 text-emerald-600"
                      : "bg-destructive/15 text-destructive")
                  }
                >
                  {e.success ? (
                    <CheckIcon className="size-3" />
                  ) : (
                    <XIcon className="size-3" />
                  )}
                </span>
                <div className="flex flex-wrap items-baseline gap-2 text-sm">
                  <code className="font-mono text-xs">{e.operation}</code>
                  {e.actor && (
                    <span className="text-muted-foreground text-xs">
                      by {e.actor}
                    </span>
                  )}
                  <span className="text-muted-foreground ml-auto text-xs">
                    {new Date(e.timestamp).toLocaleString()}
                  </span>
                </div>
                {!e.success && e.errors.length > 0 && (
                  <p className="text-destructive mt-1 line-clamp-2 text-xs">
                    {e.errors[0]}
                  </p>
                )}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Live health card (#68 slice 1) ──────────────────────────────────────
// Backed by ``astroliftClusterHealth`` — driver calls into the kube API
// for pod phases + recent Warning events. Empty payload = no data
// available (no creds / unreachable apiserver / bare-metal); UI surfaces
// that case rather than erroring out.

interface PodPhase {
  namespace: string;
  phase: string;
  count: number;
}

interface ClusterEvent {
  namespace: string;
  name: string;
  reason: string;
  message: string;
  type: string;
  count: number;
  firstSeen: string;
  lastSeen: string;
  involvedObject: string;
}

interface HealthResp {
  astroliftClusterHealth: {
    clusterId: string;
    pods: PodPhase[];
    events: ClusterEvent[];
  } | null;
}

const PHASE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  Running: "default",
  Succeeded: "secondary",
  Pending: "outline",
  Failed: "destructive",
  Unknown: "outline",
};

function LiveHealthCard({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<HealthResp>(CLUSTER_HEALTH, {
    variables: { clusterId, eventLimit: 25 },
    pollInterval: 30000,
  });
  const payload = data?.astroliftClusterHealth;
  const pods = payload?.pods ?? [];
  const events = payload?.events ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Live health</CardTitle>
        <CardDescription>
          Pod-phase rollup + recent Warning events across the
          platform&apos;s managed namespaces. Sourced directly from
          the cluster driver; polls every 30s.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading && pods.length === 0 && events.length === 0 ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <>
            <div>
              <h3 className="text-muted-foreground mb-2 text-xs uppercase tracking-wide">
                Pods
              </h3>
              {pods.length === 0 ? (
                <p className="text-muted-foreground text-sm">
                  No pod data available — the driver couldn&apos;t
                  reach the apiserver, or no astrolift-managed pods
                  are running.
                </p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {pods.map((p) => (
                    <Badge
                      key={`${p.namespace}-${p.phase}`}
                      variant={PHASE_VARIANT[p.phase] ?? "outline"}
                      className="gap-1.5"
                    >
                      <span className="font-mono text-[10px]">{p.namespace}</span>
                      <span className="opacity-60">·</span>
                      <span>{p.phase}</span>
                      <span className="opacity-60">·</span>
                      <span>{p.count}</span>
                    </Badge>
                  ))}
                </div>
              )}
            </div>
            <div>
              <h3 className="text-muted-foreground mb-2 text-xs uppercase tracking-wide">
                Recent warnings
              </h3>
              {events.length === 0 ? (
                <p className="text-muted-foreground text-sm">
                  No recent warning events. (A green Live health
                  state is the expected baseline.)
                </p>
              ) : (
                <ul className="space-y-2">
                  {events.map((e, i) => (
                    <li
                      key={`${e.namespace}-${e.name}-${i}`}
                      className="border-border rounded-md border p-2 text-sm"
                    >
                      <div className="flex flex-wrap items-baseline gap-2">
                        <code className="font-mono text-xs">{e.reason}</code>
                        <span className="text-muted-foreground text-xs">
                          {e.involvedObject}
                        </span>
                        {e.count > 1 && (
                          <Badge variant="outline" className="text-[10px]">
                            ×{e.count}
                          </Badge>
                        )}
                        <span className="text-muted-foreground ml-auto text-xs">
                          {e.lastSeen
                            ? new Date(e.lastSeen).toLocaleString()
                            : "—"}
                        </span>
                      </div>
                      <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                        {e.message}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Recent workflows card (#394) ────────────────────────────────────────
// Pulled live from Temporal's visibility API filtered by workflow ids
// that reference this cluster's guid. Empty when Temporal is disabled
// or the query fails — identical empty-state copy in both cases.

interface ClusterWorkflowRun {
  workflowId: string;
  workflowType: string;
  status: string;
  startedAt: string;
  closedAt: string;
  runId: string;
}

interface RecentWorkflowsResp {
  astroliftRecentClusterWorkflows: ClusterWorkflowRun[];
}

const STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  RUNNING: "secondary",
  COMPLETED: "default",
  FAILED: "destructive",
  CANCELED: "outline",
  TERMINATED: "destructive",
  TIMED_OUT: "destructive",
  CONTINUED_AS_NEW: "outline",
};

function RecentWorkflowsCard({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<RecentWorkflowsResp>(RECENT_CLUSTER_WORKFLOWS, {
    variables: { clusterId, limit: 10 },
    pollInterval: 15000,
  });
  const runs = data?.astroliftRecentClusterWorkflows ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Recent workflows</CardTitle>
        <CardDescription>
          Temporal runs targeting this cluster — BringClusterInto-
          Management, Refresh, Decommission, InstallClusterPrereqs,
          DriftDetection. Polls every 15s while a run is in flight.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && runs.length === 0 ? (
          <Skeleton className="h-24 w-full" />
        ) : runs.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No workflow runs recorded yet. Operator actions like Bring
            into management, Refresh, or Install prereqs will appear
            here.
          </p>
        ) : (
          <ul className="space-y-2">
            {runs.map((r) => {
              const duration =
                r.closedAt && r.startedAt
                  ? Math.max(
                      0,
                      Math.round(
                        (new Date(r.closedAt).getTime() -
                          new Date(r.startedAt).getTime()) /
                          1000,
                      ),
                    )
                  : null;
              return (
                <li
                  key={r.workflowId + r.runId}
                  className="border-border flex flex-wrap items-baseline gap-2 rounded-md border p-2 text-sm"
                >
                  <code className="font-mono text-xs">{r.workflowType}</code>
                  <Badge
                    variant={STATUS_VARIANT[r.status] ?? "outline"}
                    className="text-[10px]"
                  >
                    {r.status}
                  </Badge>
                  <span className="text-muted-foreground ml-auto text-xs">
                    {r.startedAt
                      ? new Date(r.startedAt).toLocaleString()
                      : "—"}
                    {duration !== null && (
                      <span className="opacity-60"> · {duration}s</span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Cluster metrics card (#771) ─────────────────────────────────────
// Prometheus instant queries: node count, pod running ratio, CPU/memory
// utilization, deployment ready ratio. Gracefully degrades when no
// prometheus_endpoint is configured in provider_config.

interface PrometheusMetricsResp {
  astroliftClusterPrometheusMetrics: {
    available: boolean;
    reason: string | null;
    nodeCount: number | null;
    podRunningRatio: number | null;
    cpuUtilization: number | null;
    memoryUtilization: number | null;
    deploymentReadyRatio: number | null;
  };
}

function pct(ratio: number | null): string {
  if (ratio === null) return "—";
  return `${(ratio * 100).toFixed(1)}%`;
}

function MetricTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "bad";
}) {
  const colour =
    tone === "ok"
      ? "text-emerald-600"
      : tone === "warn"
        ? "text-amber-600"
        : tone === "bad"
          ? "text-destructive"
          : "text-foreground";
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </span>
      <span className={`text-xl font-semibold tabular-nums ${colour}`}>
        {value}
      </span>
    </div>
  );
}

function utilizationTone(ratio: number | null): "ok" | "warn" | "bad" {
  if (ratio === null) return "ok";
  if (ratio < 0.7) return "ok";
  if (ratio < 0.9) return "warn";
  return "bad";
}

function ClusterMetricsCard({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<PrometheusMetricsResp>(
    CLUSTER_PROMETHEUS_METRICS,
    { variables: { clusterId }, pollInterval: 60000 },
  );
  const m = data?.astroliftClusterPrometheusMetrics;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Metrics</CardTitle>
        <CardDescription>
          Prometheus-sourced cluster saturation. Instant queries — node
          count, pod health, CPU / memory utilization, deployment
          readiness. Updates every 60s.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && !m ? (
          <Skeleton className="h-16 w-full" />
        ) : !m?.available ? (
          <p className="text-muted-foreground text-sm">
            {m?.reason === "no_endpoint"
              ? "No Prometheus endpoint configured. Set prometheus_endpoint in provider_config to unlock this card."
              : "Prometheus is unreachable. Check that the endpoint in provider_config is correct and accessible from the control plane."}
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-5">
            <MetricTile
              label="Nodes"
              value={m.nodeCount !== null ? String(m.nodeCount) : "—"}
            />
            <MetricTile
              label="Pods running"
              value={pct(m.podRunningRatio)}
              tone={
                m.podRunningRatio !== null && m.podRunningRatio >= 0.9
                  ? "ok"
                  : "warn"
              }
            />
            <MetricTile
              label="CPU utilization"
              value={pct(m.cpuUtilization)}
              tone={utilizationTone(m.cpuUtilization)}
            />
            <MetricTile
              label="Memory utilization"
              value={pct(m.memoryUtilization)}
              tone={utilizationTone(m.memoryUtilization)}
            />
            <MetricTile
              label="Deployments ready"
              value={pct(m.deploymentReadyRatio)}
              tone={
                m.deploymentReadyRatio !== null &&
                m.deploymentReadyRatio >= 0.9
                  ? "ok"
                  : "warn"
              }
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Workload health card (#362 slice 3) ─────────────────────────────
// Backed by ``astroliftClusterWorkloadHealth`` — per-Deployment view
// of desired vs ready replicas + 24h restart counts + last rollout.
// The pod-phase card above answers "is anything red"; this answers
// "which workload is red" and lets the operator triage without
// clicking through to ``kubectl`` / a dashboard.

interface WorkloadHealthRow {
  namespace: string;
  workloadName: string;
  desiredReplicas: number;
  readyReplicas: number;
  restartCount24h: number;
  lastImageDeployedAt: string;
}

interface WorkloadHealthResp {
  astroliftClusterWorkloadHealth: WorkloadHealthRow[];
}

function WorkloadHealthCard({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<WorkloadHealthResp>(
    CLUSTER_WORKLOAD_HEALTH,
    {
      variables: { clusterId },
      pollInterval: 30000,
    },
  );
  const rows = data?.astroliftClusterWorkloadHealth ?? [];

  // Sort by readiness deficit, descending — most-broken first. The
  // backend already sorts by (namespace, name), but the operator
  // cares about "what's not healthy" before "what's named first".
  const sorted = [...rows].sort((a, b) => {
    const deficitA = a.desiredReplicas - a.readyReplicas;
    const deficitB = b.desiredReplicas - b.readyReplicas;
    if (deficitA !== deficitB) return deficitB - deficitA;
    if (a.namespace !== b.namespace) {
      return a.namespace.localeCompare(b.namespace);
    }
    return a.workloadName.localeCompare(b.workloadName);
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Workload health</CardTitle>
        <CardDescription>
          Per-Deployment readiness + 24h restart counts across the
          platform&apos;s managed namespaces. Sorted most-broken
          first; polls every 30s.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && rows.length === 0 ? (
          <Skeleton className="h-24 w-full" />
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No workload data available — the driver couldn&apos;t
            reach the apiserver, or no Deployments are running in
            the platform&apos;s managed namespaces yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-muted-foreground text-xs uppercase tracking-wide">
                <tr className="border-border border-b">
                  <th className="py-2 pr-3 text-left font-medium">
                    Workload
                  </th>
                  <th className="py-2 pr-3 text-left font-medium">
                    Namespace
                  </th>
                  <th className="py-2 pr-3 text-right font-medium">
                    Ready / desired
                  </th>
                  <th className="py-2 pr-3 text-right font-medium">
                    Restarts 24h
                  </th>
                  <th className="py-2 pr-0 text-right font-medium">
                    Last deploy
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => {
                  const deficit =
                    row.desiredReplicas - row.readyReplicas;
                  const readyTone =
                    deficit === 0
                      ? "text-emerald-600"
                      : deficit === row.desiredReplicas
                        ? "text-destructive"
                        : "text-amber-600";
                  const restartsTone =
                    row.restartCount24h === 0
                      ? "text-muted-foreground"
                      : row.restartCount24h < 5
                        ? "text-amber-600"
                        : "text-destructive";
                  return (
                    <tr
                      key={`${row.namespace}-${row.workloadName}`}
                      className="border-border/50 border-b last:border-b-0"
                    >
                      <td className="py-2 pr-3">
                        <code className="font-mono text-xs">
                          {row.workloadName}
                        </code>
                      </td>
                      <td className="text-muted-foreground py-2 pr-3 font-mono text-xs">
                        {row.namespace}
                      </td>
                      <td
                        className={
                          "py-2 pr-3 text-right tabular-nums " + readyTone
                        }
                      >
                        {row.readyReplicas} / {row.desiredReplicas}
                      </td>
                      <td
                        className={
                          "py-2 pr-3 text-right tabular-nums " +
                          restartsTone
                        }
                      >
                        {row.restartCount24h}
                      </td>
                      <td className="text-muted-foreground py-2 pr-0 text-right text-xs">
                        {row.lastImageDeployedAt ? (
                          <span className="inline-flex items-center gap-1">
                            <RocketIcon className="size-3" />
                            {new Date(
                              row.lastImageDeployedAt,
                            ).toLocaleString()}
                          </span>
                        ) : (
                          <span className="opacity-60">never</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

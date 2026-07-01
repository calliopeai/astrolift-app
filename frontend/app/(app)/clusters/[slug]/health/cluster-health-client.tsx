"use client";

import { useQuery } from "@apollo/client/react";
import { AlertTriangleIcon, RocketIcon } from "lucide-react";

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
  CLUSTER_WORKLOAD_HEALTH,
  LIST_CLUSTERS,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { ClusterTabs } from "../components/cluster-tabs";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

/**
 * Cluster health tab — pod-phase rollup + recent warning events +
 * per-Deployment readiness. Sourced from the driver's kube API
 * client; polls every 30s. Empty payloads (no creds, unreachable
 * apiserver, bare-metal) surface as copy rather than errors.
 */
export function ClusterHealthClient({ slug }: { slug: string }) {
  const { data, loading } = useQuery<Resp>(LIST_CLUSTERS);
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug);

  if (loading && !cluster) {
    return (
      <PageShell title="Cluster health" description="Loading…">
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
      title={`${cluster.name} · Health`}
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{cluster.slug}</span>
          <Badge variant="secondary">{cluster.providerPluginSlug}</Badge>
        </span>
      }
    >
      <ClusterTabs slug={slug} active="health" />

      <LiveHealthCard clusterId={cluster.id} />

      <WorkloadHealthCard clusterId={cluster.id} />
    </PageShell>
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
  const fmt = useFormatters();
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
                      <span className="font-mono text-2xs">{p.namespace}</span>
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
                          <Badge variant="outline" className="text-2xs">
                            ×{e.count}
                          </Badge>
                        )}
                        <span className="text-muted-foreground ml-auto text-xs">
                          {e.lastSeen ? fmt.formatDateTime(e.lastSeen) : "—"}
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
  const fmt = useFormatters();
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
                            {fmt.formatDateTime(row.lastImageDeployedAt)}
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

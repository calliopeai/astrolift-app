"use client";

/**
 * Cluster > Health: pod-phase rollup + recent warning events + per-
 * Deployment readiness. Sourced from the driver's kube API client; polls
 * every 30s. Empty payloads (no creds, unreachable apiserver, bare-metal)
 * surface as copy rather than errors.
 */

import { RocketIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useClusterHealth } from "./use-cluster-health";
import type { useClusterWorkloadHealth } from "./use-cluster-workload-health";

export interface ClusterHealthBodyProps {
  health: ReturnType<typeof useClusterHealth>;
  workloads: ReturnType<typeof useClusterWorkloadHealth>;
}

/** The Health tab body: the live health card over the workload health card. */
export function ClusterHealthBody({ health, workloads }: ClusterHealthBodyProps) {
  return (
    <>
      <HealthLiveCard {...health} />
      <HealthWorkloadCard {...workloads} />
    </>
  );
}

// ─── Live health card (#68 slice 1) ──────────────────────────────────────
// Backed by ``astroliftClusterHealth`` — driver calls into the kube API
// for pod phases + recent Warning events. Empty payload = no data
// available (no creds / unreachable apiserver / bare-metal); UI surfaces
// that case rather than erroring out.

const PHASE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  Running: "default",
  Succeeded: "secondary",
  Pending: "outline",
  Failed: "destructive",
  Unknown: "outline",
};

function HealthLiveCard({ pods, events, loading }: ReturnType<typeof useClusterHealth>) {
  const fmt = useFormatters();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Live health</CardTitle>
        <CardDescription>
          Pod-phase rollup + recent Warning events across the platform&apos;s managed namespaces.
          Sourced directly from the cluster driver; polls every 30s.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading && pods.length === 0 && events.length === 0 ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <>
            <div>
              <h3 className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">Pods</h3>
              {pods.length === 0 ? (
                <p className="text-muted-foreground text-sm">
                  No pod data available — the driver couldn&apos;t reach the apiserver, or no
                  astrolift-managed pods are running.
                </p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {pods.map((p) => (
                    <Badge
                      key={`${p.namespace}-${p.phase}`}
                      variant={PHASE_VARIANT[p.phase] ?? "outline"}
                      className="gap-1.5"
                    >
                      <span className="text-2xs font-mono">{p.namespace}</span>
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
              <h3 className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">
                Recent warnings
              </h3>
              {events.length === 0 ? (
                <p className="text-muted-foreground text-sm">
                  No recent warning events. (A green Live health state is the expected baseline.)
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
                        <span className="text-muted-foreground text-xs">{e.involvedObject}</span>
                        {e.count > 1 && (
                          <Badge variant="outline" className="text-2xs">
                            ×{e.count}
                          </Badge>
                        )}
                        <span className="text-muted-foreground ml-auto text-xs">
                          {e.lastSeen ? fmt.formatDateTime(e.lastSeen) : "—"}
                        </span>
                      </div>
                      <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">{e.message}</p>
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

function HealthWorkloadCard({ rows, loading }: ReturnType<typeof useClusterWorkloadHealth>) {
  const fmt = useFormatters();

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
          Per-Deployment readiness + 24h restart counts across the platform&apos;s managed
          namespaces. Sorted most-broken first; polls every 30s.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && rows.length === 0 ? (
          <Skeleton className="h-24 w-full" />
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No workload data available — the driver couldn&apos;t reach the apiserver, or no
            Deployments are running in the platform&apos;s managed namespaces yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-muted-foreground text-xs tracking-wide uppercase">
                <tr className="border-border border-b">
                  <th className="py-2 pr-3 text-left font-medium">Workload</th>
                  <th className="py-2 pr-3 text-left font-medium">Namespace</th>
                  <th className="py-2 pr-3 text-right font-medium">Ready / desired</th>
                  <th className="py-2 pr-3 text-right font-medium">Restarts 24h</th>
                  <th className="py-2 pr-0 text-right font-medium">Last deploy</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => {
                  const deficit = row.desiredReplicas - row.readyReplicas;
                  const readyTone =
                    deficit === 0
                      ? "text-success-fg"
                      : deficit === row.desiredReplicas
                        ? "text-destructive"
                        : "text-warning-fg";
                  const restartsTone =
                    row.restartCount24h === 0
                      ? "text-muted-foreground"
                      : row.restartCount24h < 5
                        ? "text-warning-fg"
                        : "text-destructive";
                  return (
                    <tr
                      key={`${row.namespace}-${row.workloadName}`}
                      className="border-border/50 border-b last:border-b-0"
                    >
                      <td className="py-2 pr-3">
                        <code className="font-mono text-xs">{row.workloadName}</code>
                      </td>
                      <td className="text-muted-foreground py-2 pr-3 font-mono text-xs">
                        {row.namespace}
                      </td>
                      <td className={"py-2 pr-3 text-right tabular-nums " + readyTone}>
                        {row.readyReplicas} / {row.desiredReplicas}
                      </td>
                      <td className={"py-2 pr-3 text-right tabular-nums " + restartsTone}>
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

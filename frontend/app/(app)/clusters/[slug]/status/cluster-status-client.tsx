"use client";

import { useQuery } from "@apollo/client/react";
import { AlertTriangleIcon, CheckIcon, XIcon } from "lucide-react";
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

      <RecentWorkflowsCard clusterId={cluster.id} />

      <LifecycleTimelineCard clusterId={cluster.id} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Metrics</CardTitle>
          <CardDescription>
            Prometheus-sourced CPU / memory / pod-count / deploy-
            frequency trends, capability-gated on
            <code className="font-mono mx-1 text-xs">prometheus.installed</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-muted-foreground text-sm">
          Coming in PR 4 of this feature. Install the Prometheus stack
          via the Overview tab&apos;s bootstrap recipe card to unlock
          this surface.
        </CardContent>
      </Card>
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

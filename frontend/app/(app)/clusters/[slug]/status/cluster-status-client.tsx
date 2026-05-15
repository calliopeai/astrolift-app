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
  CLUSTER_LIFECYCLE_AUDIT,
  LIST_CLUSTERS,
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

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Live health</CardTitle>
          <CardDescription>
            Real-time signals from the cluster — pod phases, recent
            warning events, control-plane reachability — sourced
            directly from the cluster driver.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-muted-foreground text-sm">
          Coming soon. Wired in the next PR once the
          <code className="font-mono mx-1 text-xs">ClusterDriver.list_events</code>
          and
          <code className="font-mono mx-1 text-xs">list_pods</code>
          protocol methods land on each cloud driver.
        </CardContent>
      </Card>

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

"use client";

import { useQuery } from "@apollo/client/react";
import { AlertTriangleIcon } from "lucide-react";
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
import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
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

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Lifecycle + activity</CardTitle>
          <CardDescription>
            What the platform itself did to this cluster — registered →
            managing → managed transitions, recent workflow runs, and
            app bindings.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-muted-foreground text-sm">
          Apps bound view, recent workflows view, and lifecycle
          timeline land in the next PR. The bound-app count is already
          surfaced on the{" "}
          <Link
            href={`/clusters/${slug}`}
            className="text-primary underline-offset-4 hover:underline"
          >
            Overview tab
          </Link>
          .
        </CardContent>
      </Card>

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

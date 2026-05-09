"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  BoxIcon,
  CheckCircle2Icon,
  ExternalLinkIcon,
  FileCodeIcon,
  GaugeIcon,
  GitBranchIcon,
  RocketIcon,
  Trash2Icon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import {
  AppTopologyMap,
  type TopologyEdge,
  type TopologyNode,
  type TopologyNodeStatus,
} from "@/components/topology";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useFormatters } from "@/lib/i18n/formatters";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  LIST_DEPLOYMENTS,
} from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { SOFT_DELETE_APP } from "@/graphql/registry/registry.mutations";
import {
  GET_APP,
  LIST_APPS,
  LIST_WORKLOADS,
} from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
  ProvisioningStatus,
} from "@/graphql/registry/registry.types";

const statusDot: Record<ProvisioningStatus, "ok" | "warn" | "error" | "pending"> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

const DEPLOYMENT_TONE: Record<string, string> = {
  succeeded: "text-emerald-500",
  failed: "text-destructive",
  in_flight: "text-amber-500",
  rolled_back: "text-muted-foreground",
};

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}
interface DeploymentsResp {
  astroliftDeployments: Array<{
    id: string;
    registeredAppSlug: string;
    environmentName?: string | null;
    workloadSlug?: string | null;
    triggerKind?: string | null;
    status: string;
    imageTag?: string | null;
    startedAt?: string | null;
    endedAt?: string | null;
    durationSeconds?: number | null;
    createdAt: string;
  }>;
}
interface EventsResp {
  astroliftEvents: Array<{
    id: string;
    eventType: string;
    payload: Record<string, unknown>;
    registeredAppId?: string | null;
    occurredAt: string;
  }>;
}

// ─── topology synthesis ───────────────────────────────────────────────────────

function appTopology(
  app: AstroliftRegisteredApp,
  workloads: AstroliftWorkload[],
): { nodes: TopologyNode[]; edges: TopologyEdge[] } {
  const nodes: TopologyNode[] = [];
  const edges: TopologyEdge[] = [];

  const appStatus: TopologyNodeStatus =
    app.provisioningStatus === "ready"
      ? "running"
      : app.provisioningStatus === "failed"
        ? "failed"
        : "provisioning";

  const publicWorkloads = workloads.filter((w) => w.isPublic);

  if (publicWorkloads.length > 0) {
    nodes.push({
      id: "ingress",
      type: "ingress",
      label: "Public ingress",
      sublabel: app.subdomain,
      status: appStatus,
      hostnames: publicWorkloads.map((w) => `${w.slug}.${app.subdomain}`),
    });
  }

  for (const w of workloads) {
    const wlStatus: TopologyNodeStatus =
      w.replicas > 0 ? "running" : "provisioning";

    if (w.isPublic) {
      const svcId = `svc-${w.slug}`;
      nodes.push({
        id: svcId,
        type: "service",
        label: w.slug,
        sublabel: "ClusterIP",
        status: wlStatus,
      });
      edges.push({ id: `e-ingress-${svcId}`, source: "ingress", target: svcId });
      edges.push({ id: `e-${svcId}-wl-${w.slug}`, source: svcId, target: `wl-${w.slug}` });
    }

    nodes.push({
      id: `wl-${w.slug}`,
      type: "workload",
      label: w.name,
      sublabel: w.kind,
      status: wlStatus,
      replicas: { ready: w.replicas, desired: w.replicas },
      hostnames: w.isPublic ? [`${w.slug}.${app.subdomain}`] : undefined,
      href: `/apps/${app.slug}/workloads/${w.slug}`,
    });
  }

  return { nodes, edges };
}

// ─── component ────────────────────────────────────────────────────────────────

export function AppDetailClient({ slug }: { slug: string }) {
  const router = useRouter();
  const fmt = useFormatters();
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
  });
  const deployments = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug: slug, limit: 5 },
    fetchPolicy: "cache-and-network",
  });
  const events = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { limit: 30 },
    fetchPolicy: "cache-and-network",
  });

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteApp: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_APP, {
    refetchQueries: [{ query: LIST_APPS }],
    awaitRefetchQueries: true,
  });

  if (app.loading && !app.data) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  const a = app.data?.astroliftApp;
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

  async function handleDelete() {
    if (!a) return;
    if (
      !confirm(
        `Delete ${a.slug}? Soft delete only — its slug becomes reclaimable but workloads stay torn down.`,
      )
    ) {
      return;
    }
    const { data } = await softDelete({ variables: { input: { id: a.id } } });
    if (data?.softDeleteApp.ok) {
      toast.success(`Deleted ${a.slug}`);
      router.push("/apps");
    } else {
      toast.error(data?.softDeleteApp.errors?.[0]?.message ?? "Delete failed");
    }
  }

  const wlList = workloads.data?.astroliftWorkloads ?? [];
  const deployList = deployments.data?.astroliftDeployments ?? [];
  const eventsList =
    events.data?.astroliftEvents.filter((e) => e.registeredAppId === a.id) ?? [];
  const recentEvents = eventsList.slice(0, 8);

  const totalReplicas = wlList.reduce((acc, w) => acc + w.replicas, 0);
  const publicCount = wlList.filter((w) => w.isPublic).length;
  const primaryHost = publicCount > 0 ? `${wlList.find((w) => w.isPublic)?.slug}.${a.subdomain}` : null;

  const { nodes: topoNodes, edges: topoEdges } = appTopology(a, wlList);

  return (
    <PageShell
      title={a.name}
      description={a.description || `Registered app · ${a.slug}`}
      actions={
        <>
          {primaryHost && (
            <Button asChild variant="outline">
              <a href={`https://${primaryHost}`} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-4" />
                Open
              </a>
            </Button>
          )}
          <Button asChild>
            <a href={`/apps/${a.slug}/deployments`}>
              <RocketIcon className="size-4" />
              Deploy
            </a>
          </Button>
          <Button asChild variant="outline">
            <a href={`/apps/${a.slug}/manifest`}>
              <FileCodeIcon className="size-4" />
              Manifest
            </a>
          </Button>
          {a.sourceUrl && (
            <Button asChild variant="outline">
              <a href={a.sourceUrl} target="_blank" rel="noreferrer">
                <GitBranchIcon className="size-4" />
                Source
              </a>
            </Button>
          )}
          <Button variant="ghost" onClick={handleDelete} disabled={deleting}>
            <Trash2Icon className="size-4" />
            Delete
          </Button>
        </>
      }
    >
      {/* status / source / trigger row */}
      <div className="grid gap-4 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-muted-foreground text-sm">Status</CardTitle>
            <StatusDot status={statusDot[a.provisioningStatus]} />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold capitalize">{a.provisioningStatus}</p>
            {a.provisioningError && (
              <p className="text-destructive mt-1 truncate text-xs">{a.provisioningError}</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Workloads</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{wlList.length}</p>
            <p className="text-muted-foreground text-xs">
              {totalReplicas} replica{totalReplicas === 1 ? "" : "s"} ·{" "}
              {publicCount} public
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Source</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="truncate font-mono text-sm">{a.sourceRepo || "—"}</p>
            <p className="text-muted-foreground text-xs">
              <span className="font-mono">{a.deployBranch}</span> · {a.manifestPath}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Trigger</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm capitalize">{a.triggerMode.replace(/_/g, " ")}</p>
            <p className="text-muted-foreground text-xs">
              ns: <span className="font-mono">{a.k8sNamespace}</span>
            </p>
          </CardContent>
        </Card>
      </div>

      {/* topology */}
      <Card>
        <CardHeader>
          <CardTitle>Topology</CardTitle>
          <CardDescription>
            Live provisioned architecture for this app. Click a node to drill in.
            Managed services join this view once the platform exposes them in
            GraphQL.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {workloads.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-[420px] w-full" />
            </div>
          ) : topoNodes.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title="No topology yet"
                description="Topology fills in once workloads are registered for this app."
              />
            </div>
          ) : (
            <div className="p-4">
              <AppTopologyMap nodes={topoNodes} edges={topoEdges} height={440} />
            </div>
          )}
        </CardContent>
      </Card>

      {/* recent activity row */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent deployments</CardTitle>
            <CardDescription>Last 5 rollouts across environments.</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {deployments.loading && deployList.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : deployList.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<RocketIcon className="size-5" />}
                  title="No deployments yet"
                  description="Push to the deploy branch or trigger a deploy from the CLI to see rollouts here."
                />
              </div>
            ) : (
              <ul className="divide-y">
                {deployList.map((d) => (
                  <li key={d.id} className="flex items-center justify-between gap-3 px-4 py-3 text-sm">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className={DEPLOYMENT_TONE[d.status] ?? "text-muted-foreground"}>
                          ●
                        </span>
                        <span className="truncate font-mono text-xs">{d.imageTag || d.id.slice(0, 8)}</span>
                        {d.environmentName && (
                          <Badge variant="outline" className="text-[10px]">
                            {d.environmentName}
                          </Badge>
                        )}
                      </div>
                      <div className="text-muted-foreground mt-0.5 text-xs">
                        {fmt.formatRelativeTime(d.createdAt)} · <span className="capitalize">{d.status.replace(/_/g, " ")}</span>
                      </div>
                    </div>
                    <Button asChild variant="ghost" size="sm">
                      <a href={`/deployments/${d.id}`}>Open</a>
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent events</CardTitle>
            <CardDescription>Platform events for this app.</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {events.loading && recentEvents.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : recentEvents.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<ActivityIcon className="size-5" />}
                  title="No events yet"
                  description="Deployment, lifecycle, and reconcile events appear here as they happen."
                />
              </div>
            ) : (
              <ul className="divide-y">
                {recentEvents.map((e) => (
                  <li key={e.id} className="px-4 py-3 text-sm">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs">{e.eventType}</span>
                    </div>
                    <div className="text-muted-foreground mt-0.5 text-xs">
                      {fmt.formatRelativeTime(e.occurredAt)}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {/* workloads */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Workloads</CardTitle>
            <CardDescription>
              Deployments, statefulsets, jobs, and cronjobs declared in the manifest.
            </CardDescription>
          </div>
          <Badge variant="outline">{wlList.length}</Badge>
        </CardHeader>
        <CardContent className="p-0">
          {workloads.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
            </div>
          ) : wlList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title="No workloads yet"
                description="Workloads appear here after the manifest sync activity runs."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Workload</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Replicas</TableHead>
                  <TableHead>CPU</TableHead>
                  <TableHead>Memory</TableHead>
                  <TableHead>Public</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {wlList.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell>
                      <a
                        href={`/apps/${a.slug}/workloads/${w.slug}`}
                        className="hover:underline"
                      >
                        <div className="font-medium">{w.name}</div>
                        <div className="text-muted-foreground font-mono text-xs">
                          {w.slug}
                        </div>
                      </a>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{w.kind}</Badge>
                    </TableCell>
                    <TableCell>{w.replicas}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {w.cpuRequest || "—"} / {w.cpuLimit || "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {w.memoryRequest || "—"} / {w.memoryLimit || "—"}
                    </TableCell>
                    <TableCell>
                      {w.isPublic ? (
                        <CheckCircle2Icon className="text-emerald-600 size-4" />
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* future-tense stubs */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card className="border-dashed">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <GaugeIcon className="size-4 text-muted-foreground" />
              Runtime metrics
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-xs leading-relaxed">
              Request rate, error rate, and p95 latency. Wires when the
              Prometheus-fed metrics resolver lands in GraphQL.
            </p>
          </CardContent>
        </Card>
        <Card className="border-dashed">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Managed services</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-xs leading-relaxed">
              Databases, caches, queues attached to this app. Surfaces when the
              backend exposes the managed-services list field.
            </p>
          </CardContent>
        </Card>
        <Card className="border-dashed">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Custom domains</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-xs leading-relaxed">
              DNS validation + cert state per app-attached domain. Wires from
              #241.
            </p>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}

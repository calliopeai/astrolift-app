"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BoxIcon,
  ExternalLinkIcon,
  FileCodeIcon,
  GitBranchIcon,
  RocketIcon,
  Trash2Icon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { SOFT_DELETE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP, LIST_APPS, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
  ProvisioningStatus,
} from "@/graphql/registry/registry.types";

import { ActivityTimeline } from "./components/activity-timeline";
import { AppTabs } from "./components/app-tabs";
import { ControlsSection } from "./components/controls-section";
import { DeployActivityStrip } from "./components/deploy-activity-strip";
import { DeployStrategyCard } from "./components/deploy-strategy-card";
import { DeployTokenControl } from "./components/deploy-token-control";
import { DeploymentPanel } from "./components/deployment-panel";
import { GithubConnectCallout } from "./components/github-connect-callout";
import { ObservabilitySection } from "./components/observability-section";
import { PendingDeployments } from "./components/pending-deployments";
import { QuickLinksGrid } from "./components/quick-links-grid";
import { RepoBadge } from "./components/repo-badge";
import { UrlCard } from "./components/url-card";

// ─── status mappings ──────────────────────────────────────────────────────────

const statusDot: Record<ProvisioningStatus, "ok" | "warn" | "error" | "pending"> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

const SOURCE_KIND_LABEL: Record<string, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  bitbucket: "Bitbucket",
  gitea: "Gitea",
  git_url: "Git URL",
};

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}

// ─── topology synthesis ───────────────────────────────────────────────────────

function appTopology(
  app: AstroliftRegisteredApp,
  workloads: AstroliftWorkload[]
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
    const wlStatus: TopologyNodeStatus = w.replicas > 0 ? "running" : "provisioning";

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
      edges.push({
        id: `e-${svcId}-wl-${w.slug}`,
        source: svcId,
        target: `wl-${w.slug}`,
      });
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
  const tCommon = useTranslations("apps.common");
  const tDetail = useTranslations("apps.detail");
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
  });

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteApp: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_APP, {
    refetchQueries: [{ query: LIST_APPS }],
    awaitRefetchQueries: true,
  });

  if (app.loading && !app.data) {
    return (
      <PageShell title={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  const a = app.data?.astroliftApp;
  if (!a) {
    return (
      <PageShell title={tCommon("notFound")}>
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
          description={tCommon("notFoundDescription")}
          actionHref="/apps"
          actionLabel={tCommon("backToApps")}
        />
      </PageShell>
    );
  }

  async function handleDelete() {
    if (!a) return;
    const { data } = await softDelete({ variables: { input: { id: a.id } } });
    if (data?.softDeleteApp.ok) {
      toast.success(`Deleted ${a.slug}`);
      router.push("/apps");
    } else {
      throw new Error(data?.softDeleteApp.errors?.[0]?.message ?? "Delete failed");
    }
  }

  const wlList = workloads.data?.astroliftWorkloads ?? [];
  const publicCount = wlList.filter((w) => w.isPublic).length;
  const primaryHost =
    publicCount > 0 ? `${wlList.find((w) => w.isPublic)?.slug}.${a.subdomain}` : null;

  const { nodes: topoNodes, edges: topoEdges } = appTopology(a, wlList);
  const sourceLabel = SOURCE_KIND_LABEL[a.sourceKind] ?? a.sourceKind;

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          {a.previewScreenshotUrl ? (
            <img
              src={a.previewScreenshotUrl}
              alt={`${a.name} preview`}
              className="size-10 rounded border object-cover"
            />
          ) : null}
          <span>{a.name}</span>
          <Badge variant="outline" className="gap-1">
            <StatusDot status={statusDot[a.provisioningStatus]} />
            <span className="capitalize">{a.provisioningStatus}</span>
          </Badge>
        </span>
      }
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground font-mono text-xs">{a.slug}</span>
          <RepoBadge
            sourceKind={a.sourceKind}
            sourceUrl={a.sourceUrl}
            sourceRepo={a.sourceRepo}
            branch={a.deployBranch || a.defaultBranch}
          />
          <Badge variant="outline" className="text-[10px]">
            {sourceLabel}
          </Badge>
        </span>
      }
      actions={
        <>
          {primaryHost && (
            <Button asChild variant="outline">
              <a href={`https://${primaryHost}`} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-4" />
                {tDetail("actions.open")}
              </a>
            </Button>
          )}
          <Button asChild>
            <a href={`/apps/${a.slug}/environments`}>
              <RocketIcon className="size-4" />
              {tDetail("actions.deploy")}
            </a>
          </Button>
          <Button asChild variant="outline">
            <a href={`/apps/${a.slug}/config`}>
              <FileCodeIcon className="size-4" />
              {tDetail("actions.config")}
            </a>
          </Button>
          {a.sourceUrl && (
            <Button asChild variant="outline">
              <a href={a.sourceUrl} target="_blank" rel="noreferrer">
                <GitBranchIcon className="size-4" />
                {tDetail("actions.source")}
              </a>
            </Button>
          )}
          <Can permission="app.delete">
            <Button variant="ghost" onClick={() => setConfirmOpen(true)} disabled={deleting}>
              <Trash2Icon className="size-4" />
              {tDetail("actions.delete")}
            </Button>
          </Can>
        </>
      }
    >
      <AppTabs slug={a.slug} active="overview" />

      {a.provisioningStatus === "failed" && a.provisioningError && (
        <div className="border-destructive/40 bg-destructive/5 rounded-md border p-3">
          <p className="text-destructive text-xs font-semibold">
            {tDetail("provisioning.failed")}
          </p>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
            {a.provisioningError}
          </p>
        </div>
      )}

      <DeployActivityStrip appSlug={a.slug} limit={20} />

      <UrlCard
        appId={a.id}
        appSlug={a.slug}
        subdomain={a.subdomain}
        primaryWorkloadSlug={wlList.find((w) => w.isPublic)?.slug ?? null}
      />

      <DeploymentPanel appSlug={a.slug} />

      <Card>
        <CardHeader>
          <CardTitle>{tDetail("topology.title")}</CardTitle>
          <CardDescription>{tDetail("topology.description")}</CardDescription>
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
                title={tDetail("topology.emptyTitle")}
                description={tDetail("topology.emptyDescription")}
              />
            </div>
          ) : (
            <div className="p-4">
              <AppTopologyMap nodes={topoNodes} edges={topoEdges} height={440} />
            </div>
          )}
        </CardContent>
      </Card>

      <PendingDeployments appSlug={a.slug} />

      <ControlsSection appSlug={a.slug} deployBranch={a.deployBranch} />

      <DeployStrategyCard app={a} />

      <DeployTokenControl appSlug={a.slug} />

      <ObservabilitySection appSlug={a.slug} />

      <GithubConnectCallout sourceKind={a.sourceKind} />

      <ActivityTimeline appId={a.id} limit={20} />

      <QuickLinksGrid appSlug={a.slug} />

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={tDetail("delete.title", { slug: a.slug })}
        description={tDetail("delete.description")}
        confirmLabel={tDetail("delete.confirm")}
        destructive
        onConfirm={handleDelete}
      />
    </PageShell>
  );
}

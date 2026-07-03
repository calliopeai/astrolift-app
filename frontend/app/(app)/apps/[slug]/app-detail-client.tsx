"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Section } from "@/components/ui/section";
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
import { ConfigDriftBanner } from "./components/config-drift-banner";
import { ControlsSection } from "./components/controls-section";
import { DeployActivityStrip } from "./components/deploy-activity-strip";
import { DeployStrategyCard } from "./components/deploy-strategy-card";
import { DeployTokenControl } from "./components/deploy-token-control";
import { DeploymentPanel } from "./components/deployment-panel";
import { GithubConnectCallout } from "./components/github-connect-callout";
import { DeregisterPendingBanner } from "./components/deregister-pending-banner";
import { LatestDeploymentRow } from "./components/latest-deployment-row";
import { ObservabilitySection } from "./components/observability-section";
import { PendingDeployments } from "./components/pending-deployments";
import { ProvisioningProgressPanel } from "./components/provisioning-progress";
import { QuickLinksGrid } from "./components/quick-links-grid";
import { RepoBadge } from "./components/repo-badge";
import { ReprovisionCallout } from "./components/reprovision-callout";
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

// ─── component ────────────────────────────────────────────────────────────────

export function AppDetailClient({ slug }: { slug: string }) {
  const router = useRouter();
  const tCommon = useTranslations("apps.common");
  const tDetail = useTranslations("apps.detail");
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  // includeDrift opts the resolver into the config-drift rollup
  // (#407 C). The overview is the only caller that needs it; sibling
  // queries that hit GET_APP without the flag keep the cheap shape.
  const app = useQuery<AppResp>(GET_APP, {
    variables: { slug, includeDrift: true },
  });
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
  // Use the backend-computed managed hostname (e.g. my-app.astrolift.example.com).
  // Fall back to the short subdomain only when no managed domain is configured.
  const primaryHost =
    publicCount > 0
      ? (a.managedHostname || a.subdomain || null)
      : null;

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
            <span className="capitalize">
              {/* "Ready" = manifest valid + app record healthy. When the
                  managed domain is live, surface "Live" so operators
                  immediately know traffic is routing. */}
              {a.provisioningStatus === "ready" && a.managedHostname
                ? "Live"
                : a.provisioningStatus}
            </span>
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
          <Badge variant="outline" className="text-2xs">
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

      {/* Grace-period cancel banner (#436 B). Renders only when the
          operator just kicked off a deregister and the 5-min window
          is still open — clears itself once the countdown elapses or
          the cancel signal lands. */}
      <DeregisterPendingBanner appSlug={a.slug} />

      {a.provisioningStatus === "failed" && a.provisioningError && (
        <div className="border-destructive/40 bg-destructive/5 rounded-md border p-3">
          <p className="text-destructive text-xs font-semibold">{tDetail("provisioning.failed")}</p>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
            {a.provisioningError}
          </p>
        </div>
      )}

      <ProvisioningProgressPanel app={a} />

      <ReprovisionCallout appSlug={a.slug} reprovision={a.reprovision} />

      {a.configDrift?.hasDrift ? (
        <ConfigDriftBanner appSlug={a.slug} drift={a.configDrift} />
      ) : null}

      <GithubConnectCallout sourceKind={a.sourceKind} />

      {/* Primary object: the app's public URL / access surface. */}
      <UrlCard
        appId={a.id}
        appSlug={a.slug}
        subdomain={a.subdomain}
        managedHostname={a.managedHostname}
        primaryWorkloadSlug={wlList.find((w) => w.isPublic)?.slug ?? null}
        provisioningStatus={a.provisioningStatus}
      />

      <Section title={tDetail("groups.deployment")}>
        <DeployActivityStrip appSlug={a.slug} limit={20} />
        <DeploymentPanel appSlug={a.slug} />
        <PendingDeployments appSlug={a.slug} />
        <DeployStrategyCard app={a} />
        <LatestDeploymentRow appSlug={a.slug} />
      </Section>

      <Section title={tDetail("groups.controls")}>
        <ControlsSection appSlug={a.slug} deployBranch={a.deployBranch} />
        <DeployTokenControl appSlug={a.slug} />
      </Section>

      <Section title={tDetail("groups.insights")}>
        <ObservabilitySection appSlug={a.slug} />
        <ActivityTimeline appSlug={a.slug} limit={20} />
      </Section>

      <Section title={tDetail("groups.links")}>
        <QuickLinksGrid appSlug={a.slug} />
      </Section>

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

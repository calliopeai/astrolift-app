"use client";

import {
  AlertTriangleIcon,
  ExternalLinkIcon,
  FileCodeIcon,
  GitBranchIcon,
  RocketIcon,
  Trash2Icon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StaleManifestNotice } from "@/components/StaleManifestNotice";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import type { ProvisioningStatus } from "@/graphql/registry/registry.types";

import { RepoBadge } from "./RepoBadge";
import type { useAppDetail } from "./use-app-detail";

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

/**
 * Pieces of the overview with data of their own, rendered by the route's
 * containers so each query runs only where it is shown.
 */
export interface AppDetailSlots {
  /** Grace-period cancel banner (#436 B). */
  deregisterBanner?: React.ReactNode;
  provisioningProgress?: React.ReactNode;
  reprovisionCallout?: React.ReactNode;
  /** The config-drift banner; the route passes it only when there is drift. */
  configDrift?: React.ReactNode;
  githubConnect?: React.ReactNode;
  /** Autowire completeness (#1108). */
  autowire?: React.ReactNode;
  /** The cloud-side doctor (#1550). */
  doctor?: React.ReactNode;
  /** Primary object: the app's public URL / access surface. */
  urlCard?: React.ReactNode;
  /** Bodies of the four grouped sections. */
  deployment?: React.ReactNode;
  controls?: React.ReactNode;
  insights?: React.ReactNode;
  links?: React.ReactNode;
}

export type AppDetailScreenProps = ReturnType<typeof useAppDetail> & {
  slug: string;
  /** The app's own base path, e.g. `/apps/acme` (or `/agents/acme`). */
  appHref: string;
  /** The app detail tab bar. */
  tabs?: React.ReactNode;
  /**
   * A primitive-native home (bundle, cronjob, task, function). When set it
   * replaces the generic overview once the app has loaded.
   */
  home?: React.ReactNode;
  slots?: AppDetailSlots;
};

/** The app overview: header, status, banners and the grouped sections. */
export function AppDetailScreen({
  slug,
  appHref,
  tabs,
  home,
  slots = {},
  loading,
  app: a,
  workloads,
  latestDeploy,
  headerDeploying,
  onHeaderDeploy,
  deleting,
  onDelete,
}: AppDetailScreenProps) {
  const tCommon = useTranslations("apps.common");
  const tDetail = useTranslations("apps.detail");
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [confirmDeploy, setConfirmDeploy] = React.useState(false);

  if (loading) {
    return (
      <PageShell title={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

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

  if (home) return <>{home}</>;

  const publicCount = workloads.filter((w) => w.isPublic).length;
  // Use the backend-computed managed hostname (e.g. my-app.astrolift.example.com).
  // Fall back to the short subdomain only when no managed domain is configured.
  const primaryHost = publicCount > 0 ? a.managedHostname || a.subdomain || null : null;

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
          {latestDeploy ? (
            <Can permission="app.deploy">
              <Button onClick={() => setConfirmDeploy(true)} disabled={headerDeploying}>
                <RocketIcon className="size-4" />
                {tDetail("actions.deploy")}
              </Button>
            </Can>
          ) : (
            <Button asChild>
              <a href={`${appHref}/environments`}>
                <RocketIcon className="size-4" />
                {tDetail("actions.deploy")}
              </a>
            </Button>
          )}
          <Button asChild variant="outline">
            <a href={`${appHref}/config`}>
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
      {tabs}

      {/* Grace-period cancel banner (#436 B). Renders only when the
          operator just kicked off a deregister and the 5-min window
          is still open — clears itself once the countdown elapses or
          the cancel signal lands. */}
      {slots.deregisterBanner}

      {a.provisioningStatus === "failed" && a.provisioningError && (
        <div className="border-destructive/40 bg-destructive/5 rounded-md border p-3">
          <p className="text-destructive text-xs font-semibold">{tDetail("provisioning.failed")}</p>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
            {a.provisioningError}
          </p>
        </div>
      )}

      {slots.provisioningProgress}

      {slots.reprovisionCallout}

      {/* #1553: an app that registered without workloads says so here,
       * rather than letting the operator discover it at deploy time. Sits
       * outside the drift conditional — the two are unrelated. */}
      <StaleManifestNotice
        status={a.manifestBootstrapStatus}
        error={a.manifestBootstrapError}
        appSlug={a.slug}
        context="registration"
      />

      {slots.configDrift}

      {slots.githubConnect}

      {/* Autowire completeness (#1108): "connect for auto-deploy" when
          unwired, or an "autowire incomplete" repair banner when a step
          (CI workflow / webhook / deploy secret) didn't land. */}
      {slots.autowire}

      {/* The cloud half of the same question (#1550). Autowire above covers
          the repo wiring; this verifies the manifest, registry, push role,
          DNS and deployment state. On demand: each run does live probes. */}
      {slots.doctor}

      {/* Primary object: the app's public URL / access surface. */}
      {slots.urlCard}

      <Section title={tDetail("groups.deployment")}>{slots.deployment}</Section>

      <Section title={tDetail("groups.controls")}>{slots.controls}</Section>

      <Section title={tDetail("groups.insights")}>{slots.insights}</Section>

      <Section title={tDetail("groups.links")}>{slots.links}</Section>

      {latestDeploy && (
        <ConfirmDialog
          open={confirmDeploy}
          onOpenChange={setConfirmDeploy}
          title={tDetail("headerDeploy.title", {
            tag: latestDeploy.imageTag,
            env: latestDeploy.environmentName,
          })}
          description={tDetail("headerDeploy.description")}
          confirmLabel={tDetail("headerDeploy.confirm")}
          onConfirm={onHeaderDeploy}
        />
      )}

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={tDetail("delete.title", { slug: a.slug })}
        description={tDetail("delete.description")}
        confirmLabel={tDetail("delete.confirm")}
        destructive
        onConfirm={onDelete}
      />
    </PageShell>
  );
}

"use client";

import { ActivityIcon, AlertTriangleIcon, RocketIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { OverviewNotices } from "@/components/screens/apps/overview/OverviewNotices";
import { StaleManifestNotice } from "@/components/StaleManifestNotice";
import { Badge } from "@/components/ui/badge";

import { RepoBadge } from "./RepoBadge";
import type { useAppDetail } from "./use-app-detail";

const SOURCE_KIND_LABEL: Record<string, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  bitbucket: "Bitbucket",
  gitea: "Gitea",
  git_url: "Git URL",
};

/**
 * Pieces of the overview with data of their own, rendered by the route's
 * containers so each query runs only where it is shown. Every panel slot
 * renders its own `Panel` (with its span); the screen only orders them.
 */
export interface AppDetailSlots {
  // ── The notices slot, above the grid (each renders a Notice or nothing) ──
  /** Grace-period cancel (#436 B). */
  deregisterBanner?: React.ReactNode;
  provisioningProgress?: React.ReactNode;
  reprovisionCallout?: React.ReactNode;
  /** The config-drift notice; the route passes it only when there is drift. */
  configDrift?: React.ReactNode;
  githubConnect?: React.ReactNode;
  /** Autowire completeness (#1108). */
  autowire?: React.ReactNode;

  // ── The panels, in reading order ──
  /** First: the latest deploy, its failure reason first when it failed. */
  latestDeploy?: React.ReactNode;
  /** Uptime and probe latency. */
  health?: React.ReactNode;
  /** The public URL and its live health. */
  url?: React.ReactNode;
  /** Deploy throughput, failure rate, unresolved alerts. */
  observability?: React.ReactNode;
  /** AppView over the app's topology (the former Topology tab). */
  topology?: React.ReactNode;
  /** The deploy heatmap and the event feed. */
  activity?: React.ReactNode;
  /** Six places to go, as one list. */
  links?: React.ReactNode;
  managedServices?: React.ReactNode;
  /** Project, home team and granted teams. */
  ownership?: React.ReactNode;
  /** How a push reaches a deploy. */
  ci?: React.ReactNode;
  /** The cloud-side doctor (#1550). */
  doctor?: React.ReactNode;
}

export type AppDetailScreenProps = ReturnType<typeof useAppDetail> & {
  slug: string;
  /** The app detail tab bar; renders nothing inside the app frame. */
  tabs?: React.ReactNode;
  /**
   * The Overview for a primitive-native kind (bundle, cronjob, task,
   * function). When set it replaces the panel grid; the notices still lead.
   */
  home?: React.ReactNode;
  slots?: AppDetailSlots;
};

/**
 * The app's Overview tab (spec 44 §5.2), inside the app frame: one quiet
 * notices slot, then the panels on the 12-column grid. The frame owns the
 * name, status, Deploy, `⋯`, a failed app's reason and the tab row.
 */
export function AppDetailScreen({
  slug,
  tabs,
  home,
  slots = {},
  loading,
  app: a,
}: AppDetailScreenProps) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.overview");

  if (loading) {
    return (
      <PageShell title={tCommon("loading")}>
        <PanelGrid>
          <Panel
            title={t("latestDeploy.title")}
            icon={<RocketIcon className="size-4" />}
            span={6}
            loading
          />
          <Panel
            title={t("health.title")}
            icon={<ActivityIcon className="size-4" />}
            span={6}
            loading
          />
        </PanelGrid>
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

  const sourceLabel = SOURCE_KIND_LABEL[a.sourceKind] ?? a.sourceKind;

  const notices = (
    <OverviewNotices>
      {slots.deregisterBanner}
      {slots.provisioningProgress}
      {slots.reprovisionCallout}
      {/* #1553: an app that registered without workloads says so here,
          rather than letting the operator discover it at deploy time. */}
      <StaleManifestNotice
        status={a.manifestBootstrapStatus}
        error={a.manifestBootstrapError}
        appSlug={a.slug}
        context="registration"
        className="bg-card text-foreground rounded-none border-0 px-4 py-3"
      />
      {slots.configDrift}
      {slots.githubConnect}
      {slots.autowire}
    </OverviewNotices>
  );

  return (
    <PageShell
      title={a.name}
      description={
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]">
            {a.slug}
          </span>
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
    >
      {tabs}
      {notices}
      {home ?? (
        <PanelGrid>
          {slots.latestDeploy}
          {slots.health}
          {slots.url}
          {slots.observability}
          {slots.topology}
          {slots.activity}
          {slots.links}
          {slots.managedServices}
          {slots.ownership}
          {slots.ci}
          {slots.doctor}
        </PanelGrid>
      )}
    </PageShell>
  );
}

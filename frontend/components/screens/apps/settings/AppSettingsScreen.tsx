"use client";

import {
  AlertTriangleIcon,
  ChevronRightIcon,
  FileCodeIcon,
  GlobeIcon,
  KeyIcon,
  LineChartIcon,
  LockIcon,
  PlugIcon,
  UsersIcon,
  WebhookIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRelativeAge } from "@/lib/format";

import type { useAppSettings } from "./use-app-settings";

/** Field name on ``AstroliftAppSettingsLastModified`` whose timestamp
 *  drives this card's "Modified N ago" caption (#454). The wrapper is
 *  keyed by section because each card needs to know which sub-resource
 *  owns its freshness signal (deploy-tokens → DeployToken.updatedAt,
 *  secrets → AppSecretBundleRef.updatedAt, etc.). The backend exposes
 *  ``deployStrategy`` as the on-row proxy for the deploy-strategy card
 *  since that section has no separate resource. */
type SettingsSectionTimestampKey =
  | "deployStrategy"
  | "deployTokens"
  | "secrets"
  | "managedServices"
  | "domains"
  | "webhooks"
  | "members"
  | "observability";

interface LinkSection {
  key: string;
  i18nKey: string;
  /** Sub-page segment under `${basePath}/${slug}`. */
  segment: string;
  icon: typeof KeyIcon;
  lastModifiedKey: SettingsSectionTimestampKey;
}

/** Ordered list of link cards that compose the settings landing. Inline
 *  controls (deploys, ingress) live above; danger zone lives below.
 *
 *  ``lastModifiedKey`` ties each card to the matching field on
 *  ``app.settingsLastModified`` (#454) so the caption reflects the
 *  staleness of the section's primary resource rather than the whole
 *  app's ``updatedAt``. */
const LINK_SECTIONS: LinkSection[] = [
  // #400: Deploy strategy now renders as an inline DeployStrategyCard
  // (badge + edit affordance) above this list; the "Edit manifest TOML"
  // route is preserved as its own link card so operators can still drop
  // into the full TOML editor for advanced changes.
  {
    key: "manifest",
    i18nKey: "manifestEditor",
    segment: "config",
    icon: FileCodeIcon,
    lastModifiedKey: "deployStrategy",
  },
  {
    key: "deploy-tokens",
    i18nKey: "deployTokens",
    segment: "tokens",
    icon: KeyIcon,
    lastModifiedKey: "deployTokens",
  },
  {
    key: "secrets",
    i18nKey: "secrets",
    segment: "secrets",
    icon: LockIcon,
    lastModifiedKey: "secrets",
  },
  {
    key: "managed-services",
    i18nKey: "managedServices",
    segment: "managed-services",
    icon: PlugIcon,
    lastModifiedKey: "managedServices",
  },
  {
    key: "domains",
    i18nKey: "domains",
    segment: "domains",
    icon: GlobeIcon,
    lastModifiedKey: "domains",
  },
  {
    key: "webhooks",
    i18nKey: "webhooks",
    segment: "webhooks",
    icon: WebhookIcon,
    lastModifiedKey: "webhooks",
  },
  {
    key: "members",
    i18nKey: "members",
    segment: "members",
    icon: UsersIcon,
    lastModifiedKey: "members",
  },
  {
    key: "observability",
    i18nKey: "observability",
    segment: "observability",
    icon: LineChartIcon,
    lastModifiedKey: "observability",
  },
];

/**
 * Sections that carry data of their own, rendered by the route's
 * containers. The route leaves out the ones an agent does not get.
 */
export interface AppSettingsSections {
  /** Grace-period cancel banner (#436 B). */
  deregisterPending?: React.ReactNode;
  controls?: React.ReactNode;
  resync?: React.ReactNode;
  webhookDeploys?: React.ReactNode;
  ingress?: React.ReactNode;
  assignProject?: React.ReactNode;
  teams?: React.ReactNode;
  managedServicesSummary?: React.ReactNode;
  managedServicesAdmin?: React.ReactNode;
  retention?: React.ReactNode;
  environmentSettings?: React.ReactNode;
  archive?: React.ReactNode;
  deployStrategy?: React.ReactNode;
  /** CI setup, under the manifest link; shown only when the app has a repo. */
  ciSetup?: React.ReactNode;
  /** Force redeploy, under the manifest link (and CI setup when shown). */
  forceRedeploy?: React.ReactNode;
  runJob?: React.ReactNode;
  dangerZone?: React.ReactNode;
}

export type AppSettingsScreenProps = ReturnType<typeof useAppSettings> & {
  slug: string;
  /** An agent is a RegisteredApp; it gets a trimmed settings landing. */
  isAgent?: boolean;
  /** Compact density merges the link rows into one panel. */
  compactLinks?: boolean;
  /** `/apps` or `/agents`: the prefix the link cards navigate under. */
  basePath: string;
  /** The app detail tab row. */
  tabs?: React.ReactNode;
  sections?: AppSettingsSections;
};

/**
 * The app settings landing: inline controls above, link cards to each
 * settings sub-page, the danger zone below.
 */
export function AppSettingsScreen({
  app,
  loading,
  slug,
  isAgent = false,
  compactLinks = false,
  basePath,
  tabs,
  sections = {},
}: AppSettingsScreenProps) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.settings");

  if (loading) {
    return (
      <PageShell title={t("title")} description={t("loading")}>
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  const a = app;
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

  return (
    <PageShell
      title={t("title")}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t.rich("description", {
            slug: () => <span className="text-foreground">{a.slug}</span>,
          })}
        </span>
      }
    >
      {tabs}

      {/* Grace-period cancel banner (#436 B). Mounts above the
          inline controls so the operator sees the countdown
          immediately after kicking off a deregister, regardless of
          which subpage they navigate to next. */}
      {sections.deregisterPending}

      {sections.controls}

      {sections.resync}

      {sections.webhookDeploys}

      {sections.ingress}

      {sections.assignProject}

      {sections.teams}

      {sections.managedServicesSummary}

      {sections.managedServicesAdmin}

      {sections.retention}

      {sections.environmentSettings}

      {sections.archive}

      {/* Inline deploy-strategy badge + edit affordance (#400). Replaces
          the link-card to /config for the strategy itself — the manifest
          editor is still reachable via the "Edit manifest TOML" card in
          the LINK_SECTIONS list below for advanced edits. */}
      {sections.deployStrategy}

      {/* Density decides the shape of this list, not just its padding.
       * Compact merges the rows into one panel (hairline borders collapsed
       * with -mt-px); cards keeps boxes and flows them into columns, where
       * the interleaved CI block spans the full width. */}
      <div
        className={
          compactLinks
            ? "flex flex-col"
            : "grid gap-3 sm:grid-cols-2 xl:grid-cols-3 [&>*:not(a)]:sm:col-span-2 [&>*:not(a)]:xl:col-span-3"
        }
      >
        {LINK_SECTIONS.filter(
          (section) =>
            !isAgent || !["deploy-tokens", "managed-services", "domains"].includes(section.key)
        ).map((s) => {
          // Per-section "Modified N ago" (#454). Each card pulls its
          // own timestamp off the resolver-derived rollup so the
          // staleness cue is accurate per surface (deploy-tokens
          // freshness vs secrets freshness vs domains freshness) rather
          // than the whole-app proxy that #437 scope E shipped with.
          // Null means the section has no underlying rows yet — the
          // helper hides the caption rather than rendering a default.
          const sectionLastModified = a.settingsLastModified?.[s.lastModifiedKey] ?? null;
          const card = (
            <SettingsLinkCard
              key={s.key}
              section={s}
              slug={a.slug}
              lastModifiedAt={sectionLastModified}
              compact={compactLinks}
              basePath={basePath}
            />
          );
          // CI-setup section slots between the manifest-editor link
          // and Deploy tokens (#382). Hidden when the app has no source
          // repo — there's nothing to wire up to until registration
          // captures one. Sibling-friendly: appending a fragment under
          // the manifest row, no surrounding reformatting.
          // (#400 renamed the section key from "deploy-strategy" →
          // "manifest" when the inline DeployStrategyCard moved above
          // the link list; the CI block still anchors to the same
          // visual slot.)
          if (s.key === "manifest" && a.sourceRepo) {
            return (
              <React.Fragment key="manifest-with-ci">
                {card}
                {sections.ciSetup}
                {sections.forceRedeploy}
              </React.Fragment>
            );
          }
          // Apps without a source repo still benefit from the force-
          // redeploy card — the cancel + delete steps don't depend on
          // the CI pipeline. Slot it directly under the manifest row
          // when the CI block is hidden so the visual hierarchy
          // matches the with-repo path.
          if (s.key === "manifest" && !a.sourceRepo && sections.forceRedeploy) {
            return (
              <React.Fragment key="manifest-with-force-redeploy">
                {card}
                {sections.forceRedeploy}
              </React.Fragment>
            );
          }
          return card;
        })}
      </div>

      {sections.runJob}

      {sections.dangerZone}
    </PageShell>
  );
}

// ─── link card ────────────────────────────────────────────────────────────────

function SettingsLinkCard({
  section,
  slug,
  lastModifiedAt,
  compact = false,
  basePath,
}: {
  section: LinkSection;
  slug: string;
  basePath: string;
  lastModifiedAt?: string | null;
  /** Compact renders a table-style row; cards renders a boxed tile. */
  compact?: boolean;
}) {
  const t = useTranslations("apps.settings.links");
  const Icon = section.icon;
  const title = t(`${section.i18nKey}.title`);
  const description = t(`${section.i18nKey}.description`);
  const modified = lastModifiedAt ? `Modified ${formatRelativeAge(lastModifiedAt)}` : null;

  // These are destinations, not peer objects to compare, so the compact
  // default is a row: name and description on the left, freshness right-
  // aligned where it can be scanned down the column.
  if (compact) {
    return (
      <Link
        href={`${basePath}/${slug}/${section.segment}`}
        className="group border-border bg-card hover:bg-accent/40 -mt-px flex items-center gap-3 border px-3 py-2.5 transition-colors first:mt-0"
      >
        <Icon className="text-primary size-4 shrink-0" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold">{title}</span>
          <span className="text-muted-foreground block truncate text-xs">{description}</span>
        </span>
        {modified && (
          <span className="text-muted-foreground text-2xs hidden font-mono whitespace-nowrap sm:block">
            {modified}
          </span>
        )}
        <ChevronRightIcon className="text-muted-foreground group-hover:text-foreground size-4 shrink-0 transition-colors" />
      </Link>
    );
  }

  return (
    <Link href={`${basePath}/${slug}/${section.segment}`} className="group block">
      <Card className="hover:bg-accent/40 h-full transition-colors">
        <CardContent className="flex items-start gap-3 p-4">
          <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2">
            <Icon className="size-4" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">{title}</p>
            <p className="text-muted-foreground mt-0.5 text-xs">{description}</p>
            {modified && (
              <p className="text-muted-foreground text-2xs mt-1 font-mono">{modified}</p>
            )}
          </div>
          <ChevronRightIcon className="text-muted-foreground group-hover:text-foreground size-4 shrink-0 transition-colors" />
        </CardContent>
      </Card>
    </Link>
  );
}

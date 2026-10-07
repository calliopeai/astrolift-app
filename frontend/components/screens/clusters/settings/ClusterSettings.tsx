"use client";

import {
  AlertTriangleIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  HistoryIcon,
  MoreHorizontalIcon,
  PackageIcon,
  PlayIcon,
  RefreshCcwIcon,
  ShieldIcon,
  UserIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { useTranslations } from "next-intl";

import type { Column } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { ListPage } from "@/components/list/ListPage";
import { type SelectRowsSpec, selectRows } from "@/components/list/select-rows";
import {
  type ListDefinition,
  type ListStateController,
  standardViews,
  useLocalListState,
} from "@/components/list/use-list-state";
import { ClusterHeader } from "@/components/screens/clusters/list/ClusterHeader";
import { PermissionNote, Restricted, useRestrictedMode } from "@/components/settings/Restricted";
import { DangerAction, SettingsPage } from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

import { Field } from "./Field";
import { type BootstrapRun, bootstrapReleaseCount, type Lifecycle } from "./types";
import type { useBootstrapHistory } from "./use-bootstrap";
import type { useClusterSettings } from "./use-cluster-settings";

const CAPABILITY_KEYS = [
  "cert_manager",
  "ingress",
  "external_dns",
  "storage_classes",
  "metrics_server",
  "prometheus",
] as const;

/** Sections that carry data of their own, rendered by the route's containers. */
export interface ClusterSettingsCards {
  agent?: React.ReactNode;
  ingressClass?: React.ReactNode;
  centralAuth?: React.ReactNode;
  ingressAuth?: React.ReactNode;
  authUsers?: React.ReactNode;
  bootstrapPlan?: React.ReactNode;
}

export type ClusterSettingsScreenProps = Omit<
  ReturnType<typeof useClusterSettings>,
  "error" | "onRetry" | "readOnly"
> & {
  error?: string | null;
  onRetry?: () => void;
  readOnly?: boolean;
  slug: string;
  cards?: ClusterSettingsCards;
  /** The bootstrap history list, mounted only while its disclosure is open. */
  bootstrapHistory?: React.ReactNode;
  /** Overrides the person's "settings you can't change" preference (stories). */
  restrictedMode?: "show" | "hide";
  /**
   * Why deleting the cloud cluster is refused on this install (cluster
   * lifecycle withheld, calliope-installer#447). Retiring the row stays.
   */
  deleteWithheldReason?: string | null;
  /**
   * Which section is shown (`?section=`). Set by the route, so only the
   * active section's cards mount and fetch (list rules 1 and 2); without it
   * every section renders, as in the catalog's overview story.
   */
  section?: SectionSelection;
};

/**
 * Cluster settings tab on the settings archetype (spec 44 §5.3): header
 * `Admin ▾ › Clusters › <name>` with the Settings tab active, then one
 * section per concern, each saving on its own, and decommission in the
 * Danger zone behind a ConfirmDialog. The lifecycle actions (Bring into
 * management, Refresh, Re-run preflight, Force retrigger) are the header's
 * primary action and `⋯` menu. A part the viewer may not change shows the
 * same fields disabled and names the permission, or, when the person has
 * chosen to hide what they can't change, is left out (and a section left
 * empty leaves the nav).
 */
export function ClusterSettingsScreen({
  slug,
  cluster,
  loading,
  error,
  onRetry,
  readOnly = false,
  lifecycle,
  bringing,
  refreshing,
  decommissioning,
  onBring,
  onRefresh,
  onDecommission,
  access,
  cards = {},
  bootstrapHistory,
  restrictedMode,
  deleteWithheldReason,
  section,
}: ClusterSettingsScreenProps) {
  const sourceT = useTranslations("clusterSettings.source");
  const lifecycleT = useTranslations("clusterSettings.lifecycle");
  const presentationT = useTranslations("clusterSettings.presentation");
  const centralT = useTranslations("clusterSettings.centralAuth");
  const ingressClassT = useTranslations("clusterSettings.ingressClass");
  const ingressAuthT = useTranslations("clusterSettings.ingressAuth");
  const authUsersT = useTranslations("clusterSettings.authUsers");
  const fmt = useFormatters();
  const hideRestricted = useRestrictedMode(restrictedMode) === "hide";

  if (loading && !cluster) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6" aria-busy>
        <ClusterHeader slug={slug} cluster={null} loading active="settings" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  const sourceFailure = error ? (
    <div
      role="alert"
      className="border-danger-border bg-danger/5 flex min-w-0 flex-col gap-2 rounded-md border p-4"
    >
      <p className="text-danger-fg text-sm font-medium">{sourceT("readFailed")}</p>
      {cluster && <p className="text-muted-foreground text-sm">{sourceT("cached")}</p>}
      <pre className="text-danger-fg font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
        {error}
      </pre>
      {onRetry && (
        <Button
          variant="outline"
          size="sm"
          onClick={onRetry}
          className="self-start"
          disabled={loading}
        >
          {sourceT("retry")}
        </Button>
      )}
    </div>
  ) : null;

  if (!cluster && sourceFailure) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ClusterHeader
          slug={slug}
          cluster={null}
          active="settings"
          emptyTitle={sourceT("readFailed")}
        />
        {sourceFailure}
      </div>
    );
  }

  if (!cluster) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ClusterHeader
          slug={slug}
          cluster={null}
          active="settings"
          emptyTitle={sourceT("notFoundHeader")}
        />
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={sourceT("notFound", { slug })}
          description={sourceT("notFoundHelp")}
          actionHref="/clusters"
          actionLabel={sourceT("back")}
        />
      </div>
    );
  }

  const caps = reportObject(cluster.capabilities);
  const missingHeadlinePrereq =
    lifecycle === "managed" &&
    (reportObject(caps.cert_manager).installed !== true ||
      reportObject(caps.ingress).installed !== true);

  const actionsBlocked =
    readOnly || loading || !!error || bringing || refreshing || decommissioning;
  const reviewKey = JSON.stringify([slug, cluster, loading, error, readOnly, access]);
  const { primaryAction, menu } = access.manage
    ? lifecycleActions({ lifecycle, onBring, onRefresh, blocked: actionsBlocked, t: lifecycleT })
    : { primaryAction: undefined, menu: undefined };

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ClusterHeader
        slug={slug}
        cluster={cluster}
        active="settings"
        primaryAction={primaryAction}
        menu={menu}
      />

      {sourceFailure}

      {lifecycle === "error" && cluster.lastManagementError && (
        <div
          role="alert"
          className="border-danger-border bg-danger/5 flex min-w-0 flex-col gap-1 rounded-md border p-4"
        >
          <p className="text-danger flex items-center gap-2 text-sm font-medium">
            <AlertTriangleIcon className="size-4 shrink-0" />
            {lifecycleT("failureTitle")}
          </p>
          <p className="text-muted-foreground text-sm">{lifecycleT("failureHelp")}</p>
          <pre className="text-danger font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
            {cluster.lastManagementError}
          </pre>
        </div>
      )}

      <SettingsPage
        single={section}
        sections={[
          {
            id: "agent",
            title: presentationT("agent"),
            content: (
              <div className="flex min-w-0 flex-col gap-10">
                <Section
                  title={presentationT("connection")}
                  description={presentationT("connectionHelp")}
                  divided
                >
                  <dl className="grid min-w-0 grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
                    <Field label={presentationT("endpoint")} mono value={cluster.endpoint} />
                    <Field
                      label={presentationT("authMethod")}
                      value={
                        <span className="inline-flex min-w-0 items-center gap-1.5">
                          <ShieldIcon className="size-3.5 shrink-0" />
                          <span className="font-mono [overflow-wrap:anywhere]">
                            {cluster.authMethod}
                          </span>
                        </span>
                      }
                    />
                    <Field label={ingressClassT("title")} mono value={cluster.ingressClass} />
                    <Field
                      label={presentationT("registered")}
                      mono
                      value={fmt.formatDateTime(cluster.createdAt)}
                    />
                  </dl>
                </Section>
                <Restricted
                  mode={restrictedMode}
                  allowed={access.manage}
                  permission="cluster.manage"
                >
                  {cards.agent}
                </Restricted>
              </div>
            ),
          },
          {
            id: "ingress-class",
            title: ingressClassT("title"),
            content: (
              <Restricted mode={restrictedMode} allowed={access.update} permission="cluster.update">
                {cards.ingressClass}
              </Restricted>
            ),
          },
          {
            id: "central-auth",
            title: centralT("title"),
            content: (
              <Restricted mode={restrictedMode} allowed={access.update} permission="cluster.update">
                {cards.centralAuth}
              </Restricted>
            ),
          },
          {
            id: "ingress-auth",
            title: ingressAuthT("genericLabel"),
            content: (
              <Restricted mode={restrictedMode} allowed={access.update} permission="cluster.update">
                {cards.ingressAuth}
              </Restricted>
            ),
          },
          {
            id: "users",
            title: authUsersT("title"),
            content: access.users ? (
              cards.authUsers
            ) : (
              // The user list itself needs the permission, so there are no
              // fields to show disabled: only the sentence.
              <Section title={authUsersT("title")} divided>
                <PermissionNote permission="cluster.users" verb={presentationT("usersVerb")} />
              </Section>
            ),
          },
          {
            id: "bootstrap",
            title: presentationT("bootstrap"),
            content: (
              <div className="flex min-w-0 flex-col gap-10">
                <CapabilitiesSection
                  caps={caps}
                  probedAt={cluster.capabilitiesProbedAt}
                  lifecycle={lifecycle}
                />
                {missingHeadlinePrereq && <MissingPrereqs slug={cluster.slug} />}
                <LastBootstrapSection
                  slug={cluster.slug}
                  run={cluster.lastBootstrapRun ?? null}
                  history={bootstrapHistory}
                />
                <Restricted
                  mode={restrictedMode}
                  allowed={access.manage}
                  permission="cluster.manage"
                >
                  {cards.bootstrapPlan}
                </Restricted>
              </div>
            ),
          },
        ].filter(
          (section) =>
            !hideRestricted ||
            !(
              (["ingress-class", "central-auth", "ingress-auth"].includes(section.id) &&
                !access.update) ||
              (section.id === "users" && !access.users)
            )
        )}
        dangerZone={
          hideRestricted && !access.unregister ? undefined : (
            <Restricted
              mode={restrictedMode}
              allowed={access.unregister}
              permission="cluster.unregister"
            >
              <fieldset
                disabled={actionsBlocked || !["managed", "error"].includes(lifecycle)}
                className="flex min-w-0 flex-col divide-y"
              >
                <DangerAction
                  key={`${reviewKey}:keep`}
                  title={lifecycleT("retire")}
                  description={lifecycleT("retireHelp")}
                  actionLabel={lifecycleT("retire")}
                  confirmTitle={lifecycleT.rich("retireConfirm", {
                    cluster: () => <span className="font-mono">{cluster.slug}</span>,
                  })}
                  confirmDescription={lifecycleT("retireConfirmHelp")}
                  onConfirm={() => onDecommission(false)}
                />
                <DangerAction
                  key={`${reviewKey}:delete`}
                  title={lifecycleT("deleteCluster")}
                  description={lifecycleT.rich("deleteHelp", {
                    provider: () => (
                      <code className="font-mono text-xs">{cluster.providerPluginSlug}</code>
                    ),
                    command: () => <code className="font-mono text-xs">teardown_cluster</code>,
                  })}
                  actionLabel={lifecycleT("deleteAction")}
                  confirmTitle={lifecycleT.rich("deleteConfirm", {
                    cluster: () => <span className="font-mono">{cluster.slug}</span>,
                  })}
                  confirmDescription={lifecycleT("deleteConfirmHelp")}
                  disabledReason={deleteWithheldReason}
                  onConfirm={() => onDecommission(true)}
                />
              </fieldset>
            </Restricted>
          )
        }
      />
    </div>
  );
}

/**
 * The lifecycle actions by state: the one primary action, and the `⋯` menu
 * for the second one when there is one.
 */
function lifecycleActions({
  lifecycle,
  onBring,
  onRefresh,
  blocked,
  t,
}: Pick<ClusterSettingsScreenProps, "lifecycle" | "onBring" | "onRefresh"> & {
  blocked: boolean;
  t: ReturnType<typeof useTranslations<"clusterSettings.lifecycle">>;
}): { primaryAction: React.ReactNode; menu?: React.ReactNode } {
  if (lifecycle === "managing") {
    return {
      primaryAction: (
        <Button size="sm" variant="outline" onClick={() => onRefresh(true)} disabled={blocked}>
          <RefreshCcwIcon className="size-4" />
          {t("forceRetrigger")}
        </Button>
      ),
    };
  }
  if (lifecycle === "managed") {
    return {
      primaryAction: (
        <Button size="sm" variant="outline" onClick={() => onRefresh(false)} disabled={blocked}>
          <RefreshCcwIcon className="size-4" />
          {t("refresh")}
        </Button>
      ),
      menu: (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="icon" variant="ghost" aria-label={t("more")} disabled={blocked}>
              <MoreHorizontalIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem disabled={blocked} onSelect={() => onRefresh(true)}>
              <PlayIcon className="size-4" />
              {t("fullRefresh")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ),
    };
  }
  if (!["registered", "error"].includes(lifecycle)) return { primaryAction: undefined };
  return {
    primaryAction: (
      <Button size="sm" onClick={onBring} disabled={blocked}>
        <PlayIcon className="size-4" />
        {t(lifecycle === "error" ? "retryManage" : "manage")}
      </Button>
    ),
  };
}

function reportObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function reportText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function reportedInstalled(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function CapabilitiesSection({
  caps,
  probedAt,
  lifecycle,
}: {
  caps: Record<string, unknown>;
  probedAt?: string | null;
  lifecycle: Lifecycle;
}) {
  const t = useTranslations("clusterSettings.presentation");
  return (
    <Section title={t("capabilities")} description={t("capabilitiesHelp")} divided>
      {Object.keys(caps).length === 0 ? (
        lifecycle === "managing" && !probedAt ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <p className="text-muted-foreground text-sm">{t("noCapabilities")}</p>
        )
      ) : (
        <dl className="grid min-w-0 grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
          {CAPABILITY_KEYS.map((key) => (
            <CapabilityItem key={key} keyName={key} value={caps[key]} />
          ))}
        </dl>
      )}
    </Section>
  );
}

function MissingPrereqs({ slug }: { slug: string }) {
  const t = useTranslations("clusterSettings.presentation");
  return (
    <div className="border-muted-foreground/30 bg-muted/30 min-w-0 rounded-md border border-dashed p-4 text-sm">
      <p className="font-medium">{t("missingTitle")}</p>
      <p className="text-muted-foreground mt-1">{t("missingHelp")}</p>
      <ul className="text-muted-foreground mt-2 ml-4 list-disc space-y-1">
        <li>{t("recipeHelp")}</li>
        <li>
          {t.rich("cliHelp", {
            command: () => (
              <code className="font-mono text-xs [overflow-wrap:anywhere]">
                astro cluster bootstrap --cluster-slug {slug}
              </code>
            ),
            link: (children) => (
              <Link href="/downloads" className="text-primary underline-offset-4 hover:underline">
                {children}
              </Link>
            ),
          })}
        </li>
        <li>
          {t.rich("manualHelp", {
            link: (children) => (
              <Link
                href="/documentation/cluster-prerequisites"
                className="text-primary underline-offset-4 hover:underline"
              >
                {children}
              </Link>
            ),
          })}
        </li>
      </ul>
    </div>
  );
}

function CapabilityItem({ keyName, value }: { keyName: string; value: unknown }) {
  const t = useTranslations("clusterSettings.presentation");
  const historyT = useTranslations("clusterSettings.bootstrapHistory");
  const presentation = formatCapability(keyName, value, t);
  return (
    <div className="min-w-0">
      <dt className="font-mono text-xs">{presentation.label}</dt>
      <dd className="mt-1 flex min-w-0 flex-wrap items-center gap-2">
        <Badge variant={presentation.installed === true ? "default" : "outline"} className="gap-1">
          {presentation.installed === true && <CheckCircleIcon className="size-3" />}
          {presentation.installed === null
            ? historyT("unknown")
            : t(presentation.installed ? "installed" : "notDetected")}
        </Badge>
        <span className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
          {presentation.detail}
        </span>
      </dd>
    </div>
  );
}

function formatCapability(
  key: string,
  value: unknown,
  t: ReturnType<typeof useTranslations<"clusterSettings.presentation">>
): { label: string; installed: boolean | null; detail: string } {
  const v = reportObject(value);
  const detail = (key: "versionDetail" | "issuerDetail" | "classDetail", value: unknown) => {
    const text = reportText(value);
    return text ? t(key, { value: text }) : undefined;
  };
  if (key === "cert_manager")
    return {
      label: "cert-manager",
      installed: reportedInstalled(v.installed),
      detail:
        [detail("versionDetail", v.version), detail("issuerDetail", v.default_issuer)]
          .filter(Boolean)
          .join(" · ") || "—",
    };
  if (key === "ingress")
    return {
      label: t("ingressController"),
      installed: reportedInstalled(v.installed),
      detail:
        [detail("classDetail", v.class), detail("versionDetail", v.controller_version)]
          .filter(Boolean)
          .join(" · ") || "—",
    };
  if (key === "external_dns")
    return {
      label: "external-dns",
      installed: reportedInstalled(v.installed),
      detail: reportText(v.provider) || "—",
    };
  if (key === "storage_classes") {
    const known = Array.isArray(value) && value.every((item) => reportText(item) !== undefined);
    return {
      label: t("storageClasses"),
      installed: known ? value.length > 0 : null,
      detail: known && value.length ? value.join(", ") : "—",
    };
  }
  return {
    label: key === "metrics_server" ? "metrics-server" : "Prometheus",
    installed: reportedInstalled(value),
    detail: "—",
  };
}

// ─── Last bootstrap card (#319) ─────────────────────────────────────
// Surfaces the outcome of the most recent ``astro cluster bootstrap``
// run, fed by recordClusterBootstrapRun (called by the CLI). Status
// pill + relative timestamp + chart version + installed-release
// count + triggering operator. Inline 'View history' disclosure mounts
// the per-cluster history list the route passes in.

function LastBootstrapSection({
  slug,
  run,
  history,
}: {
  slug: string;
  run: BootstrapRun | null;
  history?: React.ReactNode;
}) {
  // One disclosure open at a time, so the section shows one list.
  const [open, setOpen] = React.useState<"releases" | "history" | null>(null);
  const historyOpen = open === "history";
  const releasesOpen = open === "releases";
  const fmt = useFormatters();

  const cliHint = `astro cluster bootstrap --cluster-slug ${slug}`;
  const t = useTranslations("clusterSettings.presentation");
  const historyT = useTranslations("clusterSettings.bootstrapHistory");

  if (!run) {
    return (
      <Section
        title={t("lastBootstrap")}
        description={t.rich("noBootstrap", {
          command: () => (
            <code className="font-mono text-xs [overflow-wrap:anywhere]">{cliHint}</code>
          ),
        })}
        divided
      >
        {null}
      </Section>
    );
  }

  const succeeded = run.status === "succeeded";
  const failed = run.status === "failed";
  const releaseCount = Array.isArray(run.installedReleases) ? run.installedReleases.length : null;

  return (
    <Section
      title={t("lastBootstrap")}
      description={t.rich("lastBootstrapHelp", {
        command: (children) => <code className="font-mono text-xs">{children}</code>,
      })}
      divided
    >
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {succeeded ? (
            <Badge variant="default" className="gap-1">
              <CheckCircleIcon className="size-3" />
              {historyT("succeeded")}
            </Badge>
          ) : failed ? (
            <Badge variant="destructive" className="gap-1">
              <XCircleIcon className="size-3" />
              {historyT("failed")}
            </Badge>
          ) : (
            <Badge variant="outline">{run.status || historyT("unknown")}</Badge>
          )}
          <span
            className="text-muted-foreground font-mono text-xs"
            title={fmt.formatDateTime(run.endedAt)}
          >
            {fmt.formatRelativeTime(run.endedAt)}
          </span>
          {run.cliVersion && (
            <Badge variant="outline" className="text-2xs font-mono">
              {run.cliVersion}
            </Badge>
          )}
        </div>

        <dl className="grid min-w-0 grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
          <Field label={historyT("chart")} mono value={run.chartVersion || "—"} />
          <Field
            label={historyT("releases")}
            value={
              <span>
                {releaseCount === null
                  ? historyT("unknown")
                  : t("releasesCount", { count: releaseCount })}
              </span>
            }
          />
          <Field
            label={historyT("operator")}
            value={
              <span className="inline-flex items-center gap-1.5">
                <UserIcon className="size-3.5" />
                <span className="font-mono [overflow-wrap:anywhere]">
                  {run.triggeredByUsername || historyT("unknown")}
                </span>
              </span>
            }
          />
          <Field label={t("started")} mono value={fmt.formatDateTime(run.startedAt)} />
        </dl>

        {!succeeded && run.errorMessage && (
          <div className="border-danger-border bg-danger/5 min-w-0 rounded-md border p-3">
            <p className="text-danger text-xs font-medium">{t("diagnostic")}</p>
            <pre className="text-danger mt-1 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
              {run.errorMessage}
            </pre>
          </div>
        )}

        {releaseCount !== null && releaseCount > 0 && Array.isArray(run.installedReleases) && (
          <>
            <button
              type="button"
              onClick={() => setOpen(releasesOpen ? null : "releases")}
              aria-expanded={releasesOpen}
              className="text-primary hover:text-primary inline-flex w-fit items-center gap-1 text-xs underline-offset-4 hover:underline"
            >
              <ChevronDownIcon
                className={cn("size-3 transition-transform", releasesOpen && "rotate-180")}
              />
              {t(releasesOpen ? "hideReleases" : "viewReleases")}
            </button>
            {releasesOpen && <InstalledReleases releases={run.installedReleases} />}
          </>
        )}

        <button
          type="button"
          onClick={() => setOpen(historyOpen ? null : "history")}
          aria-expanded={historyOpen}
          className="text-primary hover:text-primary inline-flex w-fit items-center gap-1 text-xs underline-offset-4 hover:underline"
        >
          <ChevronDownIcon
            className={cn("size-3 transition-transform", historyOpen && "rotate-180")}
          />
          {t(historyOpen ? "hideHistory" : "viewHistory")}
        </button>

        {historyOpen && history}
      </div>
    </Section>
  );
}

/** One release the bootstrap run installed, as the CLI reported it. */
interface ReleaseRow {
  key: string;
  name: string;
  version: string;
  status: string;
}

const releaseKey = (r: ReleaseRow) => r.key;

const RELEASES_SELECT: SelectRowsSpec<ReleaseRow> = {
  filter: { owner: () => false, status: (r, value) => r.status === value },
  text: (r) => [r.name, r.version, r.status],
  sort: {
    name: (r) => r.name.toLowerCase(),
    version: (r) => r.version,
    status: (r) => r.status,
  },
  id: releaseKey,
};

function releasesList(
  rows: ReleaseRow[],
  t: ReturnType<typeof useTranslations<"clusterSettings.presentation">>,
  historyT: ReturnType<typeof useTranslations<"clusterSettings.bootstrapHistory">>
): ListDefinition {
  const statuses = [...new Set(rows.map((r) => r.status).filter(Boolean))].sort();
  return {
    id: "clusters.settings.bootstrap-releases",
    fields: [
      {
        key: "status",
        label: historyT("status"),
        options: statuses.map((v) => ({ value: v, label: v })),
      },
    ],
    searchPlaceholder: t("searchReleases"),
    defaultSort: [{ key: "name", dir: "asc" }],
    views: standardViews({ owner: "me" }, [], { mineNote: historyT("mineNote") }).map((view) => ({
      ...view,
      label: historyT(view.key === "all" ? "all" : "mine"),
    })),
    paging: "numbered",
    pageSizes: [25, 50, 100],
  };
}

/** The last run's installed releases (nested in its payload), as an embedded list. */
function InstalledReleases({ releases }: { releases: unknown[] }) {
  const t = useTranslations("clusterSettings.presentation");
  const historyT = useTranslations("clusterSettings.bootstrapHistory");
  const rows: ReleaseRow[] = releases.map((value, i) => {
    const r = reportObject(value);
    return {
      key: `release-${i}`,
      name: reportText(r.name) || historyT("unknown"),
      version: reportText(r.version) || historyT("unknown"),
      status: reportText(r.status) || historyT("unknown"),
    };
  });
  const list = useLocalListState(releasesList(rows, t, historyT));
  const page = selectRows(rows, pageState(list), RELEASES_SELECT);
  const columns: Column<ReleaseRow>[] = [
    {
      id: "name",
      header: t("release"),
      sortKey: "name",
      cellClassName: "font-mono text-xs [overflow-wrap:anywhere]",
      cell: (r) => r.name,
    },
    {
      id: "version",
      header: t("version"),
      sortKey: "version",
      cellClassName: "font-mono text-xs break-all",
      cell: (r) => r.version,
    },
    {
      id: "status",
      header: historyT("status"),
      sortKey: "status",
      cellClassName: "text-muted-foreground text-xs",
      cell: (r) => r.status,
    },
  ];
  return (
    <ListPage<ReleaseRow>
      embedded
      list={list}
      label={historyT("releases")}
      columns={columns}
      rows={page.rows}
      getRowId={releaseKey}
      totalCount={page.totalCount}
      empty={{ icon: <PackageIcon className="size-5" />, title: t("noReleases") }}
    />
  );
}

function pageState(list: ListStateController) {
  return {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  };
}

function historyList(
  t: ReturnType<typeof useTranslations<"clusterSettings.bootstrapHistory">>
): ListDefinition {
  return {
    id: "clusters.settings.bootstrap-history",
    fields: [
      {
        key: "status",
        label: t("status"),
        options: [
          { value: "succeeded", label: t("succeeded") },
          { value: "failed", label: t("failed") },
        ],
      },
    ],
    searchPlaceholder: t("search"),
    defaultSort: [{ key: "when", dir: "desc" }],
    views: standardViews({ owner: "me" }, [], { mineNote: t("mineNote") }).map((view) => ({
      ...view,
      label: t(view.key === "all" ? "all" : "mine"),
    })),
    paging: "numbered",
    pageSizes: [25, 50, 100],
  };
}

const HISTORY_SELECT: SelectRowsSpec<BootstrapRun> = {
  filter: {
    owner: () => false,
    status: (r, value) => r.status === value,
  },
  text: (r) => [r.chartVersion, r.triggeredByUsername, r.cliVersion],
  sort: {
    when: (r) => r.endedAt ?? "",
    chart: (r) => r.chartVersion,
    releases: (r) => bootstrapReleaseCount(r.installedReleases),
    operator: (r) => (r.triggeredByUsername ?? "").toLowerCase(),
  },
  id: (r) => r.id,
};

export type BootstrapHistoryViewProps = Omit<
  ReturnType<typeof useBootstrapHistory>,
  "error" | "onRetry"
> & {
  error?: string | null;
  onRetry?: () => void;
};

/**
 * The per-cluster bootstrap history (#319), inside the Last bootstrap card:
 * the recent runs the query returns, as an embedded list.
 */
export function BootstrapHistoryView({ runs, loading, error, onRetry }: BootstrapHistoryViewProps) {
  const t = useTranslations("clusterSettings.bootstrapHistory");
  const fmt = useFormatters();
  const list = useLocalListState(historyList(t));
  const page = selectRows(runs, pageState(list), HISTORY_SELECT);

  const columns: Column<BootstrapRun>[] = [
    {
      id: "status",
      header: t("status"),
      cell: (r) =>
        r.status === "succeeded" ? (
          <Badge variant="default" className="gap-1">
            <CheckCircleIcon className="size-3" />
            {t("succeeded")}
          </Badge>
        ) : r.status === "failed" ? (
          <Badge variant="destructive" className="gap-1">
            <XCircleIcon className="size-3" />
            {t("failed")}
          </Badge>
        ) : (
          <Badge variant="outline">{r.status || t("unknown")}</Badge>
        ),
    },
    {
      id: "when",
      header: t("when"),
      sortKey: "when",
      cellClassName: "font-mono text-xs",
      cell: (r) => (
        <span title={fmt.formatDateTime(r.endedAt)}>{fmt.formatRelativeTime(r.endedAt)}</span>
      ),
    },
    {
      id: "chart",
      header: t("chart"),
      sortKey: "chart",
      cellClassName: "font-mono text-xs break-all",
      cell: (r) => r.chartVersion || t("unknown"),
    },
    {
      id: "releases",
      header: t("releases"),
      sortKey: "releases",
      cellClassName: "font-mono text-xs",
      cell: (r) => bootstrapReleaseCount(r.installedReleases),
    },
    {
      id: "operator",
      header: t("operator"),
      sortKey: "operator",
      cellClassName: "font-mono text-xs [overflow-wrap:anywhere]",
      cell: (r) => r.triggeredByUsername || t("unknown"),
    },
  ];

  return (
    <ListPage<BootstrapRun>
      embedded
      list={list}
      label={t("label")}
      columns={columns}
      rows={page.rows}
      getRowId={(r) => r.id}
      totalCount={page.totalCount}
      loading={loading}
      error={error ? { message: error } : null}
      onRetry={onRetry}
      empty={{ icon: <HistoryIcon className="size-5" />, title: t("empty") }}
    />
  );
}

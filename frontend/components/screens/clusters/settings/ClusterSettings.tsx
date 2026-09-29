"use client";

import {
  AlertTriangleIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  MoreHorizontalIcon,
  PlayIcon,
  RefreshCcwIcon,
  ShieldIcon,
  UserIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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

export type ClusterSettingsScreenProps = ReturnType<typeof useClusterSettings> & {
  slug: string;
  cards?: ClusterSettingsCards;
  /** The bootstrap history list, mounted only while its disclosure is open. */
  bootstrapHistory?: React.ReactNode;
  /** Overrides the person's "settings you can't change" preference (stories). */
  restrictedMode?: "show" | "hide";
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
  lifecycle,
  bringing,
  refreshing,
  onBring,
  onRefresh,
  onDecommission,
  access,
  cards = {},
  bootstrapHistory,
  restrictedMode,
  section,
}: ClusterSettingsScreenProps) {
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

  if (!cluster) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ClusterHeader slug={slug} cluster={null} active="settings" />
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No cluster with slug ${slug}`}
          description="The cluster doesn't exist or you don't have permission to view it."
          actionHref="/clusters"
          actionLabel="Back to clusters"
        />
      </div>
    );
  }

  const caps = (cluster.capabilities ?? {}) as Record<string, unknown>;
  const certManager = (caps.cert_manager ?? {}) as { installed?: boolean };
  const ingress = (caps.ingress ?? {}) as { installed?: boolean };
  const missingHeadlinePrereq =
    lifecycle === "managed" && (!certManager.installed || !ingress.installed);

  const { primaryAction, menu } = access.manage
    ? lifecycleActions({ lifecycle, bringing, refreshing, onBring, onRefresh })
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

      {lifecycle === "error" && cluster.lastManagementError && (
        <div
          role="alert"
          className="border-danger-border bg-danger/5 flex min-w-0 flex-col gap-1 rounded-md border p-4"
        >
          <p className="text-danger flex items-center gap-2 text-sm font-medium">
            <AlertTriangleIcon className="size-4 shrink-0" />
            Last management failure
          </p>
          <p className="text-muted-foreground text-sm">
            The workflow recorded this error on its most recent attempt. Fix the underlying issue
            and click Retry to re-run.
          </p>
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
            title: "Agent",
            content: (
              <div className="flex min-w-0 flex-col gap-10">
                <Section
                  title="Connection"
                  description="How the platform reaches the cluster's API server."
                  divided
                >
                  <dl className="grid min-w-0 grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
                    <Field label="API endpoint" mono value={cluster.endpoint} />
                    <Field
                      label="Auth method"
                      value={
                        <span className="inline-flex min-w-0 items-center gap-1.5">
                          <ShieldIcon className="size-3.5 shrink-0" />
                          <span className="font-mono [overflow-wrap:anywhere]">
                            {cluster.authMethod}
                          </span>
                        </span>
                      }
                    />
                    <Field label="Ingress class" mono value={cluster.ingressClass} />
                    <Field label="Registered" mono value={fmt.formatDateTime(cluster.createdAt)} />
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
            title: "Ingress class",
            content: (
              <Restricted mode={restrictedMode} allowed={access.update} permission="cluster.update">
                {cards.ingressClass}
              </Restricted>
            ),
          },
          {
            id: "central-auth",
            title: "Central auth",
            content: (
              <Restricted mode={restrictedMode} allowed={access.update} permission="cluster.update">
                {cards.centralAuth}
              </Restricted>
            ),
          },
          {
            id: "ingress-auth",
            title: "Ingress auth",
            content: (
              <Restricted mode={restrictedMode} allowed={access.update} permission="cluster.update">
                {cards.ingressAuth}
              </Restricted>
            ),
          },
          {
            id: "users",
            title: "Users",
            content: access.users ? (
              cards.authUsers
            ) : (
              // The user list itself needs the permission, so there are no
              // fields to show disabled: only the sentence.
              <Section title="Sign-in users" divided>
                <PermissionNote permission="cluster.users" verb="Seeing and changing them" />
              </Section>
            ),
          },
          {
            id: "bootstrap",
            title: "Bootstrap",
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
              <div className="flex min-w-0 flex-col divide-y">
                <DangerAction
                  title="Decommission"
                  description="The platform stops managing this cluster; the cluster keeps running. Apps already bound here must be migrated first: the workflow refuses while bindings are active."
                  actionLabel="Decommission"
                  confirmTitle={
                    <>
                      Decommission <span className="font-mono">{cluster.slug}</span>?
                    </>
                  }
                  confirmDescription="Its lifecycle flips to decommissioned and it leaves the active-cluster picker for new app deploys. The cluster and its cloud infrastructure keep running."
                  onConfirm={() => onDecommission(false)}
                />
                <DangerAction
                  title="Decommission and delete the cluster"
                  description={
                    <>
                      Also calls the {cluster.providerPluginSlug} driver&apos;s{" "}
                      <code className="font-mono text-xs">teardown_cluster</code>, which
                      irreversibly deletes the underlying managed cluster.
                    </>
                  }
                  actionLabel="Decommission + delete cluster"
                  confirmTitle={
                    <>
                      Decommission and delete <span className="font-mono">{cluster.slug}</span>?
                    </>
                  }
                  confirmDescription="Node groups and Fargate profiles cascade-delete with the managed cluster. The cloud-controlled VPC, IAM and DNS roots remain operator-owned. Apps already bound here must be migrated first."
                  onConfirm={() => onDecommission(true)}
                />
              </div>
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
  bringing,
  refreshing,
  onBring,
  onRefresh,
}: Pick<
  ClusterSettingsScreenProps,
  "lifecycle" | "bringing" | "refreshing" | "onBring" | "onRefresh"
>): { primaryAction: React.ReactNode; menu?: React.ReactNode } {
  if (lifecycle === "managing") {
    return {
      primaryAction: (
        <Button size="sm" variant="outline" onClick={() => onRefresh(true)} disabled={refreshing}>
          <RefreshCcwIcon className="size-4" />
          Force retrigger
        </Button>
      ),
    };
  }
  if (lifecycle === "managed") {
    return {
      primaryAction: (
        <Button size="sm" variant="outline" onClick={() => onRefresh(false)} disabled={refreshing}>
          <RefreshCcwIcon className="size-4" />
          Refresh setup
        </Button>
      ),
      menu: (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="icon" variant="ghost" aria-label="More actions">
              <MoreHorizontalIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem disabled={refreshing} onSelect={() => onRefresh(true)}>
              <PlayIcon className="size-4" />
              Re-run preflight
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ),
    };
  }
  return {
    primaryAction: (
      <Button size="sm" onClick={onBring} disabled={bringing}>
        <PlayIcon className="size-4" />
        {lifecycle === "error" ? "Retry bring into management" : "Bring into management"}
      </Button>
    ),
  };
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
  return (
    <Section
      title="Probed capabilities"
      description="Reported by the management workflow on the most recent run. Empty until the cluster is brought into management; Refresh setup re-probes."
      divided
    >
      {Object.keys(caps).length === 0 ? (
        !probedAt || lifecycle === "managing" ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <p className="text-muted-foreground text-sm">
            No capabilities reported yet. Click <strong>Bring into management</strong> to run the
            probe.
          </p>
        )
      ) : (
        <div className="min-w-0 overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Capability</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {CAPABILITY_KEYS.map((key) => (
                <CapabilityRow key={key} keyName={key} value={caps[key]} />
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </Section>
  );
}

function MissingPrereqs({ slug }: { slug: string }) {
  return (
    <div className="border-muted-foreground/30 bg-muted/30 min-w-0 rounded-md border border-dashed p-4 text-sm">
      <p className="font-medium">Missing platform prerequisites</p>
      <p className="text-muted-foreground mt-1">
        One or more headline prereqs (cert-manager, ingress controller) aren&apos;t detected. Tenant
        deploys may fail at TLS / ingress provisioning. Two paths to fix:
      </p>
      <ul className="text-muted-foreground mt-2 ml-4 list-disc space-y-1">
        <li>
          Use the bootstrap recipe below to apply driver-tuned prereqs via Flux (recommended for
          managed clusters).
        </li>
        <li>
          Run{" "}
          <code className="font-mono text-xs [overflow-wrap:anywhere]">
            astro cluster bootstrap --cluster-slug {slug}
          </code>{" "}
          from your terminal for the one-shot CLI path.{" "}
          <Link href="/downloads" className="text-primary underline-offset-4 hover:underline">
            Install the CLI
          </Link>
          .
        </li>
        <li>
          See{" "}
          <Link
            href="/documentation/cluster-prerequisites"
            className="text-primary underline-offset-4 hover:underline"
          >
            cluster prerequisites
          </Link>{" "}
          for manual install commands per controller.
        </li>
      </ul>
    </div>
  );
}

// Per-row capability renderer. Each key in the JSONField shape has
// a small adapter that pulls out the user-facing status string +
// detail line — keeping the table readable without forcing the
// operator to decode the JSON.
function CapabilityRow({ keyName, value }: { keyName: string; value: unknown }) {
  const presentation = formatCapability(keyName, value);
  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{presentation.label}</TableCell>
      <TableCell>
        {presentation.installed ? (
          <Badge variant="default" className="gap-1">
            <CheckCircleIcon className="size-3" />
            Installed
          </Badge>
        ) : (
          <Badge variant="outline" className="gap-1">
            Not detected
          </Badge>
        )}
      </TableCell>
      <TableCell className="text-muted-foreground text-xs">{presentation.detail}</TableCell>
    </TableRow>
  );
}

function formatCapability(
  key: string,
  value: unknown
): { label: string; installed: boolean; detail: string } {
  if (key === "cert_manager") {
    const v = (value ?? {}) as {
      installed?: boolean;
      version?: string | null;
      default_issuer?: string | null;
    };
    return {
      label: "cert-manager",
      installed: !!v.installed,
      detail:
        [v.version && `version ${v.version}`, v.default_issuer && `issuer ${v.default_issuer}`]
          .filter(Boolean)
          .join(" · ") || "—",
    };
  }
  if (key === "ingress") {
    const v = (value ?? {}) as {
      installed?: boolean;
      class?: string | null;
      controller_version?: string | null;
    };
    return {
      label: "ingress controller",
      installed: !!v.installed,
      detail:
        [v.class && `class ${v.class}`, v.controller_version && `version ${v.controller_version}`]
          .filter(Boolean)
          .join(" · ") || "—",
    };
  }
  if (key === "external_dns") {
    const v = (value ?? {}) as { installed?: boolean; provider?: string | null };
    return {
      label: "external-dns",
      installed: !!v.installed,
      detail: v.provider ?? "—",
    };
  }
  if (key === "storage_classes") {
    const arr = Array.isArray(value) ? (value as string[]) : [];
    return {
      label: "storage classes",
      installed: arr.length > 0,
      detail: arr.length > 0 ? arr.join(", ") : "—",
    };
  }
  if (key === "metrics_server") {
    return {
      label: "metrics-server",
      installed: !!value,
      detail: "—",
    };
  }
  if (key === "prometheus") {
    return {
      label: "Prometheus",
      installed: !!value,
      detail: "—",
    };
  }
  return {
    label: key,
    installed: false,
    detail: typeof value === "object" ? JSON.stringify(value) : String(value),
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
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const fmt = useFormatters();

  const cliHint = `astro cluster bootstrap --cluster-slug ${slug}`;

  if (!run) {
    return (
      <Section
        title="Last bootstrap"
        description={
          <>
            No bootstrap runs reported for this cluster yet. Run{" "}
            <code className="font-mono text-xs [overflow-wrap:anywhere]">{cliHint}</code> from the
            CLI to install platform prerequisites; the outcome will land here automatically.
          </>
        }
        divided
      >
        {null}
      </Section>
    );
  }

  const succeeded = run.status === "succeeded";
  const releaseCount = bootstrapReleaseCount(run.installedReleases);

  return (
    <Section
      title="Last bootstrap"
      description={
        <>
          Most recent <code className="font-mono text-xs">astro cluster bootstrap</code> run
          reported by the CLI. Re-runs append; the row never mutates after the CLI submits it.
        </>
      }
      divided
    >
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {succeeded ? (
            <Badge variant="default" className="gap-1">
              <CheckCircleIcon className="size-3" />
              Succeeded
            </Badge>
          ) : (
            <Badge variant="destructive" className="gap-1">
              <XCircleIcon className="size-3" />
              Failed
            </Badge>
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
          <Field label="Chart version" mono value={run.chartVersion || "—"} />
          <Field
            label="Installed releases"
            value={
              <span>
                <span className="font-mono font-semibold">{releaseCount}</span>{" "}
                <span className="text-muted-foreground text-xs">
                  release{releaseCount === 1 ? "" : "s"}
                </span>
              </span>
            }
          />
          <Field
            label="Triggered by"
            value={
              <span className="inline-flex items-center gap-1.5">
                <UserIcon className="size-3.5" />
                <span className="font-mono [overflow-wrap:anywhere]">
                  {run.triggeredByUsername || "unknown"}
                </span>
              </span>
            }
          />
          <Field label="Started" mono value={fmt.formatDateTime(run.startedAt)} />
        </dl>

        {!succeeded && run.errorMessage && (
          <div className="border-danger-border bg-danger/5 min-w-0 rounded-md border p-3">
            <p className="text-danger text-xs font-medium">Error reported by the CLI</p>
            <pre className="text-danger mt-1 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
              {run.errorMessage}
            </pre>
          </div>
        )}

        {releaseCount > 0 && Array.isArray(run.installedReleases) && (
          <details className="min-w-0 overflow-x-auto rounded-md border">
            <summary className="text-muted-foreground cursor-pointer px-3 py-2 text-xs">
              View installed releases
            </summary>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Release</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(run.installedReleases as Array<Record<string, unknown>>).map((r, i) => (
                  <TableRow key={`${r.name ?? "release"}-${i}`}>
                    <TableCell className="font-mono text-xs">{String(r.name ?? "—")}</TableCell>
                    <TableCell className="font-mono text-xs">{String(r.version ?? "—")}</TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {String(r.status ?? "")}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </details>
        )}

        <button
          type="button"
          onClick={() => setHistoryOpen((v) => !v)}
          aria-expanded={historyOpen}
          className="text-primary hover:text-primary inline-flex items-center gap-1 text-xs underline-offset-4 hover:underline"
        >
          <ChevronDownIcon
            className={cn("size-3 transition-transform", historyOpen && "rotate-180")}
          />
          {historyOpen ? "Hide history" : "View history"}
        </button>

        {historyOpen && history}
      </div>
    </Section>
  );
}

export type BootstrapHistoryViewProps = ReturnType<typeof useBootstrapHistory>;

/** The per-cluster bootstrap history (#319), inside the Last bootstrap card. */
export function BootstrapHistoryView({ runs, loading }: BootstrapHistoryViewProps) {
  const fmt = useFormatters();

  if (loading) {
    return <Skeleton className="h-16 w-full" />;
  }
  if (runs.length === 0) {
    return <p className="text-muted-foreground text-xs">No prior bootstrap runs.</p>;
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead>When</TableHead>
          <TableHead>Chart</TableHead>
          <TableHead>Releases</TableHead>
          <TableHead>Operator</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((r) => (
          <TableRow key={r.id}>
            <TableCell>
              {r.status === "succeeded" ? (
                <Badge variant="default" className="gap-1">
                  <CheckCircleIcon className="size-3" />
                  Succeeded
                </Badge>
              ) : (
                <Badge variant="destructive" className="gap-1">
                  <XCircleIcon className="size-3" />
                  Failed
                </Badge>
              )}
            </TableCell>
            <TableCell className="font-mono text-xs" title={fmt.formatDateTime(r.endedAt)}>
              {fmt.formatRelativeTime(r.endedAt)}
            </TableCell>
            <TableCell className="font-mono text-xs">{r.chartVersion || "—"}</TableCell>
            <TableCell className="font-mono text-xs">
              {bootstrapReleaseCount(r.installedReleases)}
            </TableCell>
            <TableCell className="font-mono text-xs">
              {r.triggeredByUsername || "unknown"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

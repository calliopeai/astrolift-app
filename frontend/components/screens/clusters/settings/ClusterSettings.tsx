"use client";

import {
  AlertTriangleIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  CloudIcon,
  GlobeIcon,
  Loader2Icon,
  PlayIcon,
  RefreshCcwIcon,
  RocketIcon,
  ShieldIcon,
  Trash2Icon,
  UserIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { type BootstrapRun, bootstrapReleaseCount } from "./types";
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

/** Cards that carry data of their own, rendered by the route's containers. */
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
  /** The cluster detail tab row. */
  tabs?: React.ReactNode;
  cards?: ClusterSettingsCards;
  /** The bootstrap history list, mounted only while its disclosure is open. */
  bootstrapHistory?: React.ReactNode;
};

/**
 * Cluster settings tab — owns every operator mutation that touches
 * cluster lifecycle (Bring into management, Refresh, Re-run preflight,
 * Force retrigger, Install prereqs, Decommission). Also surfaces the
 * Connection card, probed capabilities, last bootstrap, and the
 * driver-tuned bootstrap recipe. Danger-zone decommission lives at
 * the bottom in its own red-bordered section.
 */
export function ClusterSettingsScreen({
  slug,
  cluster,
  loading,
  lifecycle,
  bringing,
  refreshing,
  decommissioning,
  onBring,
  onRefresh,
  onDecommission,
  tabs,
  cards = {},
  bootstrapHistory,
}: ClusterSettingsScreenProps) {
  const fmt = useFormatters();
  // Decommission confirmation modal state. Two-step opt-in: open the
  // modal, then within the modal explicitly check "also delete cloud
  // infrastructure" if the operator wants the destructive path. Default
  // is the safe "just remove platform management" flow.
  const [decommissionOpen, setDecommissionOpen] = React.useState(false);
  const [decommissionDeleteInfra, setDecommissionDeleteInfra] = React.useState(false);

  async function handleDecommission() {
    if (await onDecommission(decommissionDeleteInfra)) {
      setDecommissionOpen(false);
      setDecommissionDeleteInfra(false);
    }
  }

  if (loading && !cluster) {
    return (
      <PageShell title="Cluster settings" description="Loading…">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
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

  const caps = (cluster.capabilities ?? {}) as Record<string, unknown>;
  const certManager = (caps.cert_manager ?? {}) as { installed?: boolean };
  const ingress = (caps.ingress ?? {}) as { installed?: boolean };
  const missingHeadlinePrereq =
    lifecycle === "managed" && (!certManager.installed || !ingress.installed);

  // Lifecycle-aware management actions block. Mirrors the old overview
  // header — bring/refresh/re-run preflight/force retrigger, with the
  // top-level Decommission still living up here for convenience plus a
  // dedicated Danger zone at the bottom.
  const managementActions: React.ReactNode = (() => {
    if (lifecycle === "managing") {
      return (
        <Can permission="cluster.manage">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" disabled>
              <Loader2Icon className="size-4 animate-spin" />
              Setup in progress…
            </Button>
            <Button variant="outline" onClick={() => onRefresh(true)} disabled={refreshing}>
              <RefreshCcwIcon className="size-4" />
              Force retrigger
            </Button>
          </div>
        </Can>
      );
    }
    if (lifecycle === "managed") {
      return (
        <Can permission="cluster.manage">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => onRefresh(false)} disabled={refreshing}>
              <RefreshCcwIcon className="size-4" />
              Refresh setup
            </Button>
            <Button variant="ghost" onClick={() => onRefresh(true)} disabled={refreshing}>
              <PlayIcon className="size-4" />
              Re-run preflight
            </Button>
          </div>
        </Can>
      );
    }
    if (lifecycle === "error") {
      return (
        <Can permission="cluster.manage">
          <Button onClick={onBring} disabled={bringing}>
            <PlayIcon className="size-4" />
            Retry bring into management
          </Button>
        </Can>
      );
    }
    return (
      <Can permission="cluster.manage">
        <Button onClick={onBring} disabled={bringing}>
          <PlayIcon className="size-4" />
          Bring into management
        </Button>
      </Can>
    );
  })();

  return (
    <PageShell
      title={`${cluster.name} · Settings`}
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{cluster.slug}</span>
          <Badge variant="secondary">{cluster.providerPluginSlug}</Badge>
        </span>
      }
    >
      {tabs}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Management</CardTitle>
          <CardDescription>
            Drive the cluster through its lifecycle — bring into management, refresh setup, or
            re-run the preflight probe.
          </CardDescription>
        </CardHeader>
        <CardContent>{managementActions}</CardContent>
      </Card>

      {lifecycle === "error" && cluster.lastManagementError && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2 text-base">
              <AlertTriangleIcon className="size-4" />
              Last management failure
            </CardTitle>
            <CardDescription>
              The workflow recorded this error on its most recent attempt. Fix the underlying issue
              and click Retry to re-run.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="text-destructive text-xs whitespace-pre-wrap">
              {cluster.lastManagementError}
            </pre>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <GlobeIcon className="size-4" />
            Connection
          </CardTitle>
          <CardDescription>How the platform reaches the cluster&apos;s API server.</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
          <Field label="API endpoint" mono value={cluster.endpoint} />
          <Field
            label="Auth method"
            value={
              <span className="inline-flex items-center gap-1.5">
                <ShieldIcon className="size-3.5" />
                <span className="font-mono">{cluster.authMethod}</span>
              </span>
            }
          />
          <Field label="Ingress class" mono value={cluster.ingressClass} />
          <Field label="Registered" value={fmt.formatDateTime(cluster.createdAt)} />
        </CardContent>
      </Card>

      <Can permission="cluster.manage">{cards.agent}</Can>

      <Can permission="cluster.update">
        {cards.ingressClass}
        {cards.centralAuth}
        {cards.ingressAuth}
      </Can>

      <Can permission="cluster.users">{cards.authUsers}</Can>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <CloudIcon className="size-4" />
            Probed capabilities
          </CardTitle>
          <CardDescription>
            Reported by the management workflow on the most recent run. Empty until the cluster is
            brought into management; click Refresh setup to re-probe.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {Object.keys(caps).length === 0 ? (
            !cluster.capabilitiesProbedAt || lifecycle === "managing" ? (
              <div className="p-6">
                <Skeleton className="h-32 w-full" />
              </div>
            ) : (
              <p className="text-muted-foreground p-6 text-sm">
                No capabilities reported yet. Click <strong>Bring into management</strong> to run
                the probe.
              </p>
            )
          ) : (
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
          )}
        </CardContent>
      </Card>

      {missingHeadlinePrereq && (
        <div className="border-muted-foreground/30 bg-muted/30 rounded-md border border-dashed p-4 text-sm">
          <p className="font-medium">Missing platform prerequisites</p>
          <p className="text-muted-foreground mt-1">
            One or more headline prereqs (cert-manager, ingress controller) aren&apos;t detected.
            Tenant deploys may fail at TLS / ingress provisioning. Two paths to fix:
          </p>
          <ul className="text-muted-foreground mt-2 ml-4 list-disc space-y-1">
            <li>
              Use the bootstrap recipe card below to apply driver-tuned prereqs via Flux
              (recommended for managed clusters).
            </li>
            <li>
              Run{" "}
              <code className="font-mono text-xs">
                astro cluster bootstrap --cluster-slug {cluster.slug}
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
      )}

      <LastBootstrapCard
        slug={cluster.slug}
        run={cluster.lastBootstrapRun ?? null}
        history={bootstrapHistory}
      />

      {cards.bootstrapPlan}

      <Can permission="cluster.unregister">
        <Card className="border-destructive/40">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2 text-base">
              <AlertTriangleIcon className="size-4" />
              Danger zone
            </CardTitle>
            <CardDescription>
              Decommissioning stops the platform from managing this cluster. Apps already bound here
              must be migrated first; the workflow refuses when bindings are active.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              variant="outline"
              onClick={() => setDecommissionOpen(true)}
              disabled={decommissioning}
              className="border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive"
            >
              <Trash2Icon className="size-4" />
              Decommission cluster
            </Button>
          </CardContent>
        </Card>
      </Can>

      <AlertDialog open={decommissionOpen} onOpenChange={setDecommissionOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Decommission {cluster.slug}?</AlertDialogTitle>
            <AlertDialogDescription>
              The platform will stop managing this cluster — its lifecycle flips to{" "}
              <strong>decommissioned</strong> and it&apos;s removed from the active-cluster picker
              for new app deploys. Existing apps already bound to this cluster must be migrated
              first; the workflow refuses when bindings are active.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="border-destructive/30 bg-destructive/5 my-4 rounded-md border p-3">
            <label className="flex cursor-pointer items-start gap-3 text-sm">
              <input
                type="checkbox"
                checked={decommissionDeleteInfra}
                onChange={(e) => setDecommissionDeleteInfra(e.target.checked)}
                className="border-destructive/50 accent-destructive mt-0.5 size-4 cursor-pointer rounded"
              />
              <span>
                <strong className="text-destructive">Also delete cloud infrastructure.</strong>{" "}
                <span className="text-muted-foreground">
                  This calls the {cluster.providerPluginSlug} driver&apos;s{" "}
                  <code className="font-mono text-xs">teardown_cluster</code> and irreversibly
                  deletes the underlying managed cluster (node groups / Fargate profiles
                  cascade-delete). The cloud-controlled VPC / IAM / DNS roots remain operator-owned.
                  Leave unchecked to keep the cluster running.
                </span>
              </span>
            </label>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDecommission}
              disabled={decommissioning}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {decommissionDeleteInfra ? "Decommission + delete cluster" : "Decommission"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageShell>
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

function LastBootstrapCard({
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
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <RocketIcon className="size-4" />
            Last bootstrap
          </CardTitle>
          <CardDescription>
            No bootstrap runs reported for this cluster yet. Run{" "}
            <code className="font-mono text-xs">{cliHint}</code> from the CLI to install platform
            prerequisites; the outcome will land here automatically.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const succeeded = run.status === "succeeded";
  const releaseCount = bootstrapReleaseCount(run.installedReleases);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <RocketIcon className="size-4" />
          Last bootstrap
        </CardTitle>
        <CardDescription>
          Most recent <code className="font-mono text-xs">astro cluster bootstrap</code> run
          reported by the CLI. Re-runs append; the row never mutates after the CLI submits it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
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
          <span className="text-muted-foreground text-xs" title={fmt.formatDateTime(run.endedAt)}>
            {fmt.formatRelativeTime(run.endedAt)}
          </span>
          {run.cliVersion && (
            <Badge variant="outline" className="text-2xs font-mono">
              {run.cliVersion}
            </Badge>
          )}
        </div>

        <div className="grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
          <Field label="Chart version" mono value={run.chartVersion || "—"} />
          <Field
            label="Installed releases"
            value={
              <span>
                <span className="font-semibold">{releaseCount}</span>{" "}
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
                <span>{run.triggeredByUsername || "unknown"}</span>
              </span>
            }
          />
          <Field label="Started" value={fmt.formatDateTime(run.startedAt)} />
        </div>

        {!succeeded && run.errorMessage && (
          <div className="border-destructive/30 bg-destructive/5 rounded-md border p-3">
            <p className="text-destructive text-xs font-medium">Error reported by the CLI</p>
            <pre className="text-destructive mt-1 text-xs whitespace-pre-wrap">
              {run.errorMessage}
            </pre>
          </div>
        )}

        {releaseCount > 0 && Array.isArray(run.installedReleases) && (
          <details className="rounded-md border">
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
      </CardContent>
    </Card>
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
            <TableCell className="text-xs" title={fmt.formatDateTime(r.endedAt)}>
              {fmt.formatRelativeTime(r.endedAt)}
            </TableCell>
            <TableCell className="font-mono text-xs">{r.chartVersion || "—"}</TableCell>
            <TableCell className="text-xs">{bootstrapReleaseCount(r.installedReleases)}</TableCell>
            <TableCell className="text-xs">{r.triggeredByUsername || "unknown"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
